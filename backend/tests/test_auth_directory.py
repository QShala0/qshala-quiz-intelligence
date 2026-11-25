"""Unit tests for GET /api/v1/auth/directory endpoint.

Tests admin-only authorization, Microsoft Graph mock integration via httpx MockTransport,
filtering of disabled/guest/external/existing users, nextLink pagination, 403 permission error mapping,
search querying, 5-minute cache behavior, and ensuring no tokens or raw Graph bodies leak.
"""
from typing import Dict, List, Optional
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.app.config import settings
from backend.app.database import Base, SessionLocal, engine
from backend.app.main import app
from backend.app.models.auth import AuthUser
from backend.app.services.auth.directory import clear_directory_cache
from backend.app.services.auth.seeds import seed_auth
from backend.app.services.auth.sessions import SESSION_COOKIE_NAME, create_session

BASE_URL = "http://testserver"
DIRECTORY_ENDPOINT = "/api/v1/auth/directory"


@pytest.fixture(autouse=True)
def init_db(monkeypatch):
    """Ensure database schema is created and seeded before each test, and test credentials are mock-set."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_auth(db)
    finally:
        db.close()
    monkeypatch.setattr(settings, "AUTH_MS_CLIENT_ID", settings.AUTH_MS_CLIENT_ID or "mock-test-client-id")
    monkeypatch.setattr(settings, "AUTH_MS_CLIENT_SECRET", settings.AUTH_MS_CLIENT_SECRET or "mock-test-client-secret")
    monkeypatch.setattr(settings, "AZURE_TENANT_ID", settings.AZURE_TENANT_ID or "mock-test-tenant-id")
    clear_directory_cache()
    yield
    clear_directory_cache()


def _get_session_token(role: str = "admin") -> str:
    """Helper to generate a valid session token for a seeded or test user."""
    db = SessionLocal()
    try:
        user = db.query(AuthUser).filter(AuthUser.role == role, AuthUser.status == "active").first()
        assert user is not None, f"No active user found with role {role}"
        token, _ = create_session(db, user)
        return token
    finally:
        db.close()


def _mock_graph_client(handler):
    """Patch httpx.AsyncClient in directory service to use an httpx.MockTransport handler."""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    return patch("backend.app.services.auth.directory.httpx.AsyncClient", side_effect=_factory)


# ---------------------------------------------------------------------- 1. Authorization Tests

def test_directory_requires_authentication():
    """Anonymous requests must return 401 Unauthorized."""
    client = TestClient(app)
    resp = client.get(DIRECTORY_ENDPOINT)
    assert resp.status_code == 401
    assert "Not authenticated" in resp.json()["detail"]


def test_directory_requires_admin_role():
    """Non-admin members must receive 403 Forbidden."""
    member_token = _get_session_token("member")
    client = TestClient(app, cookies={SESSION_COOKIE_NAME: member_token})
    resp = client.get(DIRECTORY_ENDPOINT)
    assert resp.status_code == 403
    assert "Admin role required" in resp.json()["detail"]


# ---------------------------------------------------------------------- 2. Filtering Tests

def test_directory_filtering_disabled_guest_external_and_existing():
    """Filters out:

    - disabled accounts (accountEnabled=False)
    - guest accounts (#EXT# in UPN or userType=Guest)
    - non-allowed domains (not in AUTH_ALLOWED_EMAIL_DOMAINS)
    - already registered users in auth_users
    - includes active internal accounts with allowed domain.
    """
    admin_token = _get_session_token("admin")
    client = TestClient(app, cookies={SESSION_COOKIE_NAME: admin_token})

    # Find an existing admin email in db to test exclusion
    db = SessionLocal()
    try:
        existing_admin = db.query(AuthUser).filter(AuthUser.role == "admin").first()
        existing_email = existing_admin.email.lower()
    finally:
        db.close()

    mock_graph_users = [
        # 1. Disabled account -> drop
        {
            "id": "u1",
            "displayName": "Disabled Person",
            "mail": "disabled@walnuts.co.in",
            "userPrincipalName": "disabled@walnuts.co.in",
            "accountEnabled": False,
        },
        # 2. Guest account via #EXT# -> drop
        {
            "id": "u2",
            "displayName": "External Vendor",
            "mail": "vendor@external.com",
            "userPrincipalName": "vendor_external.com#EXT#@walnuts.co.in",
            "accountEnabled": True,
        },
        # 3. Guest account via userType -> drop
        {
            "id": "u3",
            "displayName": "Guest User",
            "mail": "guest@walnuts.co.in",
            "userPrincipalName": "guest@walnuts.co.in",
            "userType": "Guest",
            "accountEnabled": True,
        },
        # 4. Non-allowed domain -> drop
        {
            "id": "u4",
            "displayName": "Other Domain User",
            "mail": "user@otherdomain.com",
            "userPrincipalName": "user@otherdomain.com",
            "accountEnabled": True,
        },
        # 5. Already registered user -> drop
        {
            "id": "u5",
            "displayName": "Existing Admin",
            "mail": existing_email,
            "userPrincipalName": existing_email,
            "accountEnabled": True,
        },
        # 6. Valid eligible user with mail -> keep
        {
            "id": "u6",
            "displayName": "Test Bob",
            "mail": "bob@walnuts.co.in",
            "userPrincipalName": "bob@walnuts.co.in",
            "accountEnabled": True,
        },
        # 7. Valid eligible user falling back to UPN -> keep
        {
            "id": "u7",
            "displayName": "Test Alice",
            "mail": None,
            "userPrincipalName": "alice@walnuts.co.in",
            "accountEnabled": True,
        },
    ]

    def handler(request: httpx.Request):
        url = str(request.url)
        if "oauth2/v2.0/token" in url:
            return httpx.Response(200, json={"access_token": "mock_token_abc", "expires_in": 3600})
        if "graph.microsoft.com/v1.0/users" in url:
            return httpx.Response(200, json={"value": mock_graph_users})
        return httpx.Response(404)

    with _mock_graph_client(handler):
        resp = client.get(DIRECTORY_ENDPOINT)

    assert resp.status_code == 200
    data = resp.json()

    # Must contain only Alice and Bob, sorted alphabetically by name
    assert len(data) == 2
    assert data[0] == {"display_name": "Test Alice", "email": "alice@walnuts.co.in"}
    assert data[1] == {"display_name": "Test Bob", "email": "bob@walnuts.co.in"}


# ---------------------------------------------------------------------- 3. Pagination Tests

def test_directory_pagination_next_link():
    """Follows @odata.nextLink pages and aggregates users across multiple pages."""
    admin_token = _get_session_token("admin")
    client = TestClient(app, cookies={SESSION_COOKIE_NAME: admin_token})

    page1_users = [
        {
            "id": "p1",
            "displayName": "User Charlie",
            "mail": "charlie@walnuts.co.in",
            "userPrincipalName": "charlie@walnuts.co.in",
            "accountEnabled": True,
        },
    ]
    page2_users = [
        {
            "id": "p2",
            "displayName": "User David",
            "mail": "david@walnuts.co.in",
            "userPrincipalName": "david@walnuts.co.in",
            "accountEnabled": True,
        },
    ]

    def handler(request: httpx.Request):
        url = str(request.url)
        if "oauth2/v2.0/token" in url:
            return httpx.Response(200, json={"access_token": "mock_token_abc", "expires_in": 3600})
        if "graph.microsoft.com/v1.0/users" in url:
            if "skiptoken" in url:
                return httpx.Response(200, json={"value": page2_users})
            return httpx.Response(
                200,
                json={
                    "value": page1_users,
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/users?$skiptoken=nextpage2",
                },
            )
        return httpx.Response(404)

    with _mock_graph_client(handler):
        resp = client.get(DIRECTORY_ENDPOINT)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert [u["email"] for u in data] == ["charlie@walnuts.co.in", "david@walnuts.co.in"]


# ---------------------------------------------------------------------- 4. Error Handling & No Leakage

def test_directory_graph_403_returns_clear_message_without_leaks():
    """Graph 403 returns clear 503 message and never leaks raw tokens or Graph body."""
    admin_token = _get_session_token("admin")
    client = TestClient(app, cookies={SESSION_COOKIE_NAME: admin_token})

    raw_graph_error = {
        "error": {
            "code": "Authorization_RequestDenied",
            "message": "Insufficient privileges to complete the operation on directory service.",
            "innerError": {"request-id": "secret-req-id-777", "date": "2026-11-25T12:00:00"},
        }
    }

    def handler(request: httpx.Request):
        url = str(request.url)
        if "oauth2/v2.0/token" in url:
            return httpx.Response(200, json={"access_token": "secret_mock_token_xyz999", "expires_in": 3600})
        if "graph.microsoft.com/v1.0/users" in url:
            return httpx.Response(403, json=raw_graph_error, headers={"request-id": "mock-req-777"})
        return httpx.Response(404)

    with _mock_graph_client(handler):
        resp = client.get(DIRECTORY_ENDPOINT)

    assert resp.status_code == 503
    expected_msg = (
        "Directory access is not enabled. Ask a Microsoft 365 admin to grant User.Read.All to the login app."
    )
    assert resp.json()["detail"] == expected_msg

    # Ensure no token or raw Graph body leaks
    assert "secret_mock_token_xyz999" not in resp.text
    assert "Insufficient privileges" not in resp.text
    assert "Authorization_RequestDenied" not in resp.text


def test_directory_token_endpoint_failure_returns_clear_message():
    """If client credentials token endpoint fails, returns 503 without leaking credentials."""
    admin_token = _get_session_token("admin")
    client = TestClient(app, cookies={SESSION_COOKIE_NAME: admin_token})

    def handler(request: httpx.Request):
        url = str(request.url)
        if "oauth2/v2.0/token" in url:
            return httpx.Response(
                401,
                json={"error": "invalid_client", "error_description": "Secret is invalid"},
                headers={"client-request-id": "token-fail-123"},
            )
        return httpx.Response(404)

    with _mock_graph_client(handler):
        resp = client.get(DIRECTORY_ENDPOINT)

    assert resp.status_code == 503
    assert "Directory access is not enabled" in resp.json()["detail"]
    assert "invalid_client" not in resp.text
    assert "Secret is invalid" not in resp.text


def test_directory_credentials_missing_returns_clear_message(monkeypatch):
    """When AUTH_MS_CLIENT_ID or AUTH_MS_CLIENT_SECRET is missing, returns 503 without falling back to AZURE_CLIENT_*."""
    admin_token = _get_session_token("admin")
    client = TestClient(app, cookies={SESSION_COOKIE_NAME: admin_token})

    # Even if SharePoint credentials (AZURE_CLIENT_*) are present, lack of AUTH_MS_* must return 503
    monkeypatch.setattr(settings, "AUTH_MS_CLIENT_ID", None)
    monkeypatch.setattr(settings, "AUTH_MS_CLIENT_SECRET", None)
    monkeypatch.setattr(settings, "AZURE_CLIENT_ID", "sharepoint-app-client-id")
    monkeypatch.setattr(settings, "AZURE_CLIENT_SECRET", "sharepoint-app-client-secret")

    resp = client.get(DIRECTORY_ENDPOINT)
    assert resp.status_code == 503
    assert "Directory access is not enabled. Ask a Microsoft 365 admin to grant User.Read.All to the login app." in resp.json()["detail"]

    # When only one of the AUTH_MS_* is set, it also returns 503
    monkeypatch.setattr(settings, "AUTH_MS_CLIENT_ID", "login-app-id")
    monkeypatch.setattr(settings, "AUTH_MS_CLIENT_SECRET", None)
    resp2 = client.get(DIRECTORY_ENDPOINT)
    assert resp2.status_code == 503
    assert "Directory access is not enabled" in resp2.json()["detail"]


# ---------------------------------------------------------------------- 5. Search & Caching Tests

def test_directory_search_filter_q():
    """Server-side ?q= filters by substring in display name or email."""
    admin_token = _get_session_token("admin")
    client = TestClient(app, cookies={SESSION_COOKIE_NAME: admin_token})

    mock_users = [
        {"id": "1", "displayName": "Alpha Team", "mail": "alpha@walnuts.co.in", "accountEnabled": True},
        {"id": "2", "displayName": "Beta User", "mail": "beta@walnuts.co.in", "accountEnabled": True},
        {"id": "3", "displayName": "Charlie Alpha", "mail": "charlie@walnuts.co.in", "accountEnabled": True},
    ]

    def handler(request: httpx.Request):
        url = str(request.url)
        if "oauth2/v2.0/token" in url:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if "graph.microsoft.com/v1.0/users" in url:
            return httpx.Response(200, json={"value": mock_users})
        return httpx.Response(404)

    with _mock_graph_client(handler):
        # Match 'alpha' in displayName (Alpha Team, Charlie Alpha)
        resp1 = client.get(f"{DIRECTORY_ENDPOINT}?q=alpha")
        assert resp1.status_code == 200
        emails1 = [u["email"] for u in resp1.json()]
        assert emails1 == ["alpha@walnuts.co.in", "charlie@walnuts.co.in"]

        # Match 'beta@' in email
        resp2 = client.get(f"{DIRECTORY_ENDPOINT}?q=beta@")
        assert resp2.status_code == 200
        emails2 = [u["email"] for u in resp2.json()]
        assert emails2 == ["beta@walnuts.co.in"]


def test_directory_5_minute_cache():
    """Subsequent requests within 5 minutes return cached data without hitting Graph again."""
    admin_token = _get_session_token("admin")
    client = TestClient(app, cookies={SESSION_COOKIE_NAME: admin_token})

    call_counts = {"token": 0, "users": 0}

    mock_users = [
        {"id": "1", "displayName": "Cache Test", "mail": "cache@walnuts.co.in", "accountEnabled": True},
    ]

    def handler(request: httpx.Request):
        url = str(request.url)
        if "oauth2/v2.0/token" in url:
            call_counts["token"] += 1
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if "graph.microsoft.com/v1.0/users" in url:
            call_counts["users"] += 1
            return httpx.Response(200, json={"value": mock_users})
        return httpx.Response(404)

    with _mock_graph_client(handler):
        # First call fetches from Graph
        resp1 = client.get(DIRECTORY_ENDPOINT)
        assert resp1.status_code == 200
        assert call_counts["users"] == 1

        # Second call returns cached data
        resp2 = client.get(DIRECTORY_ENDPOINT)
        assert resp2.status_code == 200
        assert call_counts["users"] == 1  # No additional Graph call!

        # After clearing cache, next call refetches
        clear_directory_cache()
        resp3 = client.get(DIRECTORY_ENDPOINT)
        assert resp3.status_code == 200
        assert call_counts["users"] == 2
