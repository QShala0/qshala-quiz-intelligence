"""Unit tests for Microsoft Entra ID OAuth with PKCE and ID token validation."""
import time
import uuid
from datetime import datetime, timezone
from typing import Dict
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.database import Base, SessionLocal, engine
from backend.app.main import app
from backend.app.models.auth import AuthUser
from backend.app.services.auth.microsoft import (
    OAUTH_STATE_COOKIE_NAME,
    generate_code_verifier,
    sign_oauth_state,
    validate_email_domain,
    validate_microsoft_id_token,
)
from backend.app.services.auth.seeds import seed_auth


@pytest.fixture(scope="session")
def rsa_key_pair():
    """Generate in-memory RSA key pair for unit tests."""
    priv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_priv = priv_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pem_pub = priv_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return pem_priv, pem_pub


@pytest.fixture
def test_client():
    """Fresh isolated test client per test."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def init_db():
    """Ensure database schema is created and seeded before each test."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_auth(db)
    finally:
        db.close()
    yield


# ---------------------------------------------------------------------- 1. Token Validation Tests

def _create_test_id_token(
    pem_priv: bytes,
    claims_override: Dict = None,
    headers: Dict = None,
) -> str:
    now = int(time.time())
    payload = {
        "iss": f"https://login.microsoftonline.com/{settings.AZURE_TENANT_ID or 'test-tenant'}/v2.0",
        "aud": settings.AUTH_MS_CLIENT_ID or "test-client-id",
        "tid": settings.AZURE_TENANT_ID or "test-tenant",
        "sub": "oid-user-12345",
        "oid": "oid-user-12345",
        "name": "Test User",
        "email": "testuser@walnuts.co.in",
        "nonce": "test-nonce-123",
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
    }
    if claims_override:
        payload.update(claims_override)
    return jwt.encode(payload, pem_priv, algorithm="RS256", headers=headers)


def test_validate_token_success(rsa_key_pair):
    pem_priv, pem_pub = rsa_key_pair
    token = _create_test_id_token(pem_priv)
    claims = validate_microsoft_id_token(
        token,
        expected_nonce="test-nonce-123",
        signing_key=pem_pub,
        client_id=settings.AUTH_MS_CLIENT_ID or "test-client-id",
        tenant_id=settings.AZURE_TENANT_ID or "test-tenant",
    )
    assert claims["email"] == "testuser@walnuts.co.in"
    assert claims["oid"] == "oid-user-12345"


def test_validate_token_wrong_aud(rsa_key_pair):
    pem_priv, pem_pub = rsa_key_pair
    token = _create_test_id_token(pem_priv, claims_override={"aud": "wrong-client-id"})
    with pytest.raises(Exception):
        validate_microsoft_id_token(
            token,
            expected_nonce="test-nonce-123",
            signing_key=pem_pub,
            client_id="correct-client-id",
            tenant_id=settings.AZURE_TENANT_ID or "test-tenant",
        )


def test_validate_token_wrong_tid(rsa_key_pair):
    pem_priv, pem_pub = rsa_key_pair
    token = _create_test_id_token(
        pem_priv,
        claims_override={
            "iss": "https://login.microsoftonline.com/correct-tenant-id/v2.0",
            "tid": "rogue-tenant-id",
        },
    )
    with pytest.raises(ValueError, match="does not match expected tenant"):
        validate_microsoft_id_token(
            token,
            expected_nonce="test-nonce-123",
            signing_key=pem_pub,
            client_id=settings.AUTH_MS_CLIENT_ID or "test-client-id",
            tenant_id="correct-tenant-id",
        )


def test_validate_token_expired(rsa_key_pair):
    pem_priv, pem_pub = rsa_key_pair
    now = int(time.time())
    token = _create_test_id_token(pem_priv, claims_override={"exp": now - 60, "iat": now - 3600})
    with pytest.raises(jwt.ExpiredSignatureError):
        validate_microsoft_id_token(
            token,
            expected_nonce="test-nonce-123",
            signing_key=pem_pub,
            client_id=settings.AUTH_MS_CLIENT_ID or "test-client-id",
            tenant_id=settings.AZURE_TENANT_ID or "test-tenant",
        )


def test_validate_token_wrong_nonce(rsa_key_pair):
    pem_priv, pem_pub = rsa_key_pair
    token = _create_test_id_token(pem_priv, claims_override={"nonce": "nonce-A"})
    with pytest.raises(ValueError, match="Token nonce does not match"):
        validate_microsoft_id_token(
            token,
            expected_nonce="nonce-B",
            signing_key=pem_pub,
            client_id=settings.AUTH_MS_CLIENT_ID or "test-client-id",
            tenant_id=settings.AZURE_TENANT_ID or "test-tenant",
        )


def test_validate_token_tampered_signature(rsa_key_pair):
    pem_priv, pem_pub = rsa_key_pair
    token = _create_test_id_token(pem_priv)
    # Tamper with the payload part of the JWT
    parts = token.split(".")
    tampered_token = f"{parts[0]}.{parts[1]}xyz.{parts[2]}"
    with pytest.raises(jwt.InvalidTokenError):
        validate_microsoft_id_token(
            tampered_token,
            expected_nonce="test-nonce-123",
            signing_key=pem_pub,
            client_id=settings.AUTH_MS_CLIENT_ID or "test-client-id",
            tenant_id=settings.AZURE_TENANT_ID or "test-tenant",
        )


# ---------------------------------------------------------------------- 2. Domain Validation Tests

def test_domain_validation():
    assert validate_email_domain("akshath@walnuts.co.in") is True
    assert validate_email_domain("USER@WALNUTS.CO.IN") is True
    assert validate_email_domain("outsider@gmail.com") is False
    assert validate_email_domain("attacker@walnuts.co.in.fake.com") is False
    assert validate_email_domain("") is False
    assert validate_email_domain("invalid-email") is False


# ---------------------------------------------------------------------- 3. Login Endpoint Tests

def test_microsoft_login_redirect(test_client):
    res = test_client.get("/api/v1/auth/microsoft/login", follow_redirects=False)
    assert res.status_code == 303
    loc = res.headers["location"]
    assert "login.microsoftonline.com" in loc
    assert "response_type=code" in loc
    assert "code_challenge=" in loc
    assert "code_challenge_method=S256" in loc
    assert "scope=openid+profile+email" in loc or "scope=openid%20profile%20email" in loc
    assert OAUTH_STATE_COOKIE_NAME in res.cookies


# ---------------------------------------------------------------------- 4. Callback State Tests

def test_microsoft_callback_state_mismatch(test_client):
    signed_state = sign_oauth_state(state="real-state", nonce="nonce1", verifier="verif1")
    test_client.cookies.set(OAUTH_STATE_COOKIE_NAME, signed_state)

    # Call with mismatched state in query
    res = test_client.get(
        "/api/v1/auth/microsoft/callback?code=fake-code&state=wrong-state",
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert "/login?error=state_mismatch" in res.headers["location"]


def test_microsoft_callback_error_param(test_client):
    res = test_client.get(
        "/api/v1/auth/microsoft/callback?error=access_denied&error_description=User+cancelled",
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert "/login?error=oauth_error" in res.headers["location"]


# ---------------------------------------------------------------------- 5. Account Mapping Tests

def test_microsoft_callback_active_user_matched_by_email(rsa_key_pair, test_client):
    pem_priv, pem_pub = rsa_key_pair
    db = SessionLocal()
    try:
        # Initial admin akshath@walnuts.co.in was seeded in active state
        admin = db.query(AuthUser).filter(AuthUser.email == "akshath@walnuts.co.in").first()
        assert admin is not None
        assert admin.status == "active"
        assert admin.ms_oid is None
    finally:
        db.close()

    state = "state-active-test"
    nonce = "nonce-active-test"
    verifier = generate_code_verifier()
    signed_state = sign_oauth_state(state=state, nonce=nonce, verifier=verifier)
    test_client.cookies.set(OAUTH_STATE_COOKIE_NAME, signed_state)

    id_token = _create_test_id_token(
        pem_priv,
        claims_override={
            "email": "akshath@walnuts.co.in",
            "oid": "ms-oid-akshath-1234",
            "name": "Akshath Sharma",
            "nonce": nonce,
        },
    )

    with patch("backend.app.routers.auth.exchange_code_for_tokens", new_callable=AsyncMock) as mock_exchange, \
         patch("backend.app.routers.auth.validate_microsoft_id_token") as mock_validate:
        mock_exchange.return_value = {"id_token": id_token}
        mock_validate.return_value = {
            "email": "akshath@walnuts.co.in",
            "oid": "ms-oid-akshath-1234",
            "name": "Akshath Sharma",
            "nonce": nonce,
        }

        res = test_client.get(
            f"/api/v1/auth/microsoft/callback?code=mock-code&state={state}",
            follow_redirects=False,
        )

        assert res.status_code == 303
        assert res.headers["location"].endswith("/")
        assert "qs_session" in res.cookies

    db = SessionLocal()
    try:
        admin_refreshed = db.query(AuthUser).filter(AuthUser.email == "akshath@walnuts.co.in").first()
        assert admin_refreshed.ms_oid == "ms-oid-akshath-1234"
        assert admin_refreshed.last_login_at is not None
    finally:
        db.close()


def test_microsoft_callback_matched_by_ms_oid(rsa_key_pair, test_client):
    pem_priv, pem_pub = rsa_key_pair
    db = SessionLocal()
    try:
        user = AuthUser(
            id=str(uuid.uuid4()),
            email="existing@walnuts.co.in",
            display_name="Existing User",
            ms_oid="existing-oid-999",
            provider="microsoft",
            role="member",
            status="active",
            created_at=datetime.now(timezone.utc),
            created_by="test",
        )
        db.add(user)
        db.commit()
    finally:
        db.close()

    state = "state-oid-test"
    nonce = "nonce-oid-test"
    signed_state = sign_oauth_state(state=state, nonce=nonce, verifier="dummy-verifier")
    test_client.cookies.set(OAUTH_STATE_COOKIE_NAME, signed_state)

    with patch("backend.app.routers.auth.exchange_code_for_tokens", new_callable=AsyncMock) as mock_exchange, \
         patch("backend.app.routers.auth.validate_microsoft_id_token") as mock_validate:
        mock_exchange.return_value = {"id_token": "fake-jwt"}
        mock_validate.return_value = {
            "email": "different-alias@walnuts.co.in",
            "oid": "existing-oid-999",
            "name": "Existing User",
            "nonce": nonce,
        }

        res = test_client.get(
            f"/api/v1/auth/microsoft/callback?code=mock-code&state={state}",
            follow_redirects=False,
        )
        assert res.status_code == 303
        assert res.headers["location"].endswith("/")
        assert "qs_session" in res.cookies


def test_microsoft_callback_pending_user_no_session(test_client):
    db = SessionLocal()
    try:
        user = AuthUser(
            id=str(uuid.uuid4()),
            email="pending@walnuts.co.in",
            display_name="Pending User",
            ms_oid="oid-pending-111",
            provider="microsoft",
            role="member",
            status="pending",
            created_at=datetime.now(timezone.utc),
            created_by="test",
        )
        db.add(user)
        db.commit()
    finally:
        db.close()

    state = "state-pending-test"
    nonce = "nonce-pending-test"
    signed_state = sign_oauth_state(state=state, nonce=nonce, verifier="dummy-verifier")
    test_client.cookies.set(OAUTH_STATE_COOKIE_NAME, signed_state)

    with patch("backend.app.routers.auth.exchange_code_for_tokens", new_callable=AsyncMock) as mock_exchange, \
         patch("backend.app.routers.auth.validate_microsoft_id_token") as mock_validate:
        mock_exchange.return_value = {"id_token": "fake-jwt"}
        mock_validate.return_value = {
            "email": "pending@walnuts.co.in",
            "oid": "oid-pending-111",
            "name": "Pending User",
            "nonce": nonce,
        }

        res = test_client.get(
            f"/api/v1/auth/microsoft/callback?code=mock-code&state={state}",
            follow_redirects=False,
        )
        assert res.status_code == 303
        assert res.headers["location"].endswith("/pending")
        # NEVER create a session for a pending user
        assert "qs_session" not in res.cookies


def test_microsoft_callback_blocked_user_no_session(test_client):
    db = SessionLocal()
    try:
        user = AuthUser(
            id=str(uuid.uuid4()),
            email="blocked@walnuts.co.in",
            display_name="Blocked User",
            ms_oid="oid-blocked-222",
            provider="microsoft",
            role="member",
            status="blocked",
            created_at=datetime.now(timezone.utc),
            created_by="test",
        )
        db.add(user)
        db.commit()
    finally:
        db.close()

    state = "state-blocked-test"
    nonce = "nonce-blocked-test"
    signed_state = sign_oauth_state(state=state, nonce=nonce, verifier="dummy-verifier")
    test_client.cookies.set(OAUTH_STATE_COOKIE_NAME, signed_state)

    with patch("backend.app.routers.auth.exchange_code_for_tokens", new_callable=AsyncMock) as mock_exchange, \
         patch("backend.app.routers.auth.validate_microsoft_id_token") as mock_validate:
        mock_exchange.return_value = {"id_token": "fake-jwt"}
        mock_validate.return_value = {
            "email": "blocked@walnuts.co.in",
            "oid": "oid-blocked-222",
            "name": "Blocked User",
            "nonce": nonce,
        }

        res = test_client.get(
            f"/api/v1/auth/microsoft/callback?code=mock-code&state={state}",
            follow_redirects=False,
        )
        assert res.status_code == 303
        assert "/login?error=blocked" in res.headers["location"]
        # NEVER create a session for a blocked user
        assert "qs_session" not in res.cookies


def test_microsoft_callback_new_user_creates_pending_no_session(test_client):
    state = "state-newuser-test"
    nonce = "nonce-newuser-test"
    signed_state = sign_oauth_state(state=state, nonce=nonce, verifier="dummy-verifier")
    test_client.cookies.set(OAUTH_STATE_COOKIE_NAME, signed_state)

    new_email = f"newhire-{uuid.uuid4().hex[:6]}@walnuts.co.in"
    new_oid = f"oid-newhire-{uuid.uuid4().hex[:6]}"

    with patch("backend.app.routers.auth.exchange_code_for_tokens", new_callable=AsyncMock) as mock_exchange, \
         patch("backend.app.routers.auth.validate_microsoft_id_token") as mock_validate:
        mock_exchange.return_value = {"id_token": "fake-jwt"}
        mock_validate.return_value = {
            "email": new_email,
            "oid": new_oid,
            "name": "New Hire",
            "nonce": nonce,
        }

        res = test_client.get(
            f"/api/v1/auth/microsoft/callback?code=mock-code&state={state}",
            follow_redirects=False,
        )
        assert res.status_code == 303
        assert res.headers["location"].endswith("/pending")
        assert "qs_session" not in res.cookies

    # Verify newly created user in db has status='pending', team=None, role='member'
    db = SessionLocal()
    try:
        created = db.query(AuthUser).filter(AuthUser.email == new_email).first()
        assert created is not None
        assert created.status == "pending"
        assert created.team_id is None
        assert created.role == "member"
        assert created.ms_oid == new_oid
        assert created.provider == "microsoft"
    finally:
        db.close()


def test_microsoft_callback_domain_rejected(test_client):
    state = "state-domain-test"
    nonce = "nonce-domain-test"
    signed_state = sign_oauth_state(state=state, nonce=nonce, verifier="dummy-verifier")
    test_client.cookies.set(OAUTH_STATE_COOKIE_NAME, signed_state)

    outsider_email = "hacker@evilcorp.com"

    with patch("backend.app.routers.auth.exchange_code_for_tokens", new_callable=AsyncMock) as mock_exchange, \
         patch("backend.app.routers.auth.validate_microsoft_id_token") as mock_validate:
        mock_exchange.return_value = {"id_token": "fake-jwt"}
        mock_validate.return_value = {
            "email": outsider_email,
            "oid": "oid-evil-123",
            "name": "Evil Outsider",
            "nonce": nonce,
        }

        res = test_client.get(
            f"/api/v1/auth/microsoft/callback?code=mock-code&state={state}",
            follow_redirects=False,
        )
        assert res.status_code == 303
        assert "/login?error=unauthorized_domain" in res.headers["location"]
        assert "qs_session" not in res.cookies

    db = SessionLocal()
    try:
        user = db.query(AuthUser).filter(AuthUser.email == outsider_email).first()
        assert user is None
    finally:
        db.close()
