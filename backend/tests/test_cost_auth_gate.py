"""Tests for authentication and role authorization on cost ledger endpoints.

Verifies that cost endpoints require admin session (HTTP 401 unauthenticated,
HTTP 403 non-admin, HTTP 200 admin), while refresh endpoints remain protected
by X-Cron-Secret alone.
"""
import os
import pytest
from fastapi.testclient import TestClient

os.environ["REQUIRE_POSTGRES"] = "0"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from backend.app.config import settings
from backend.app.database import Base, SessionLocal
from backend.app.main import app
from backend.app.models.auth import AuthUser
from backend.app.services.auth.seeds import seed_auth
from backend.app.services.auth.sessions import SESSION_COOKIE_NAME, create_session
from backend.app.services.costs.rate_card import seed_rate_card_v1_if_empty, invalidate_rate_card_cache
from backend.app.services.costs.aggregation import invalidate_costs_cache


@pytest.fixture(autouse=True)
def setup_db():
    invalidate_rate_card_cache()
    invalidate_costs_cache()
    db = SessionLocal()
    try:
        Base.metadata.create_all(bind=db.get_bind())
        seed_auth(db)
        seed_rate_card_v1_if_empty(db)
    finally:
        db.close()
    yield
    invalidate_rate_card_cache()
    invalidate_costs_cache()


def test_costs_unauthenticated_returns_401():
    """Accessing costs endpoints without any session cookie returns 401 Unauthorized."""
    client = TestClient(app)
    endpoints = [
        "/costs/simple",
        "/costs/summary",
        "/costs/timeline",
        "/costs/documents",
        "/costs/quizzes",
        "/costs/events",
        "/costs/rate-card",
    ]
    for ep in endpoints:
        res = client.get(ep)
        assert res.status_code == 401, f"{ep} did not return 401 for unauthenticated request"


def test_costs_non_admin_returns_403():
    """Member/consultant session returns 403 Forbidden on cost endpoints."""
    db = SessionLocal()
    try:
        member = db.query(AuthUser).filter(AuthUser.username == "user").first()
        assert member is not None
        assert member.role == "member"
        raw_token, _ = create_session(db, member)
    finally:
        db.close()

    client = TestClient(app, cookies={SESSION_COOKIE_NAME: raw_token})

    res_summary = client.get("/costs/summary")
    assert res_summary.status_code == 403
    assert "admin role required" in res_summary.json()["detail"].lower()

    res_simple = client.get("/costs/simple")
    assert res_simple.status_code == 403

    res_card = client.get("/costs/rate-card")
    assert res_card.status_code == 403


def test_costs_admin_returns_200():
    """Admin session returns 200 on cost endpoints."""
    db = SessionLocal()
    try:
        admin = db.query(AuthUser).filter(AuthUser.role == "admin", AuthUser.status == "active").first()
        assert admin is not None
        raw_token, _ = create_session(db, admin)
    finally:
        db.close()

    client = TestClient(app, cookies={SESSION_COOKIE_NAME: raw_token})

    res = client.get("/costs/summary")
    assert res.status_code == 200
    assert "periods" in res.json()


def test_refresh_endpoints_no_auth_session_needed():
    """POST /costs/refresh and GET /costs/refresh-status do not require an admin user session."""
    client = TestClient(app)

    # Status endpoint is open or secret-gated, never requires user session
    res_status = client.get("/costs/refresh-status")
    # Might be 200 or return latest run info
    assert res_status.status_code in (200, 404)

    # Refresh endpoint validates X-Cron-Secret, not auth user session
    secret = getattr(settings, "COST_CRON_SECRET", None) or "test-cron-secret"
    orig_secret = getattr(settings, "COST_CRON_SECRET", "")
    try:
        settings.COST_CRON_SECRET = secret
        # Without cron secret -> 401 (from _verify_cron_secret)
        r_fail = client.post("/costs/refresh")
        assert r_fail.status_code == 401
        assert "x-cron-secret" in r_fail.json()["detail"].lower()

        # With valid cron secret -> passes verify_cron_secret (dry_run=True to avoid DB work)
        r_ok = client.post("/costs/refresh?dry_run=true", headers={"X-Cron-Secret": secret})
        assert r_ok.status_code == 200
        assert r_ok.json()["status"] == "ok"
    finally:
        settings.COST_CRON_SECRET = orig_secret
