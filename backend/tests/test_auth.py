"""Comprehensive tests for authentication, sessions, admin API, seeds, CSRF, and mode gates."""
import secrets
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import SessionLocal, get_db
from backend.app.main import app, _is_allowed_origin
from backend.app.models.auth import AuthAudit, AuthSession, AuthTeam, AuthUser
from backend.app.services.auth.audit import log_auth_audit
from backend.app.services.auth.dependencies import (
    get_current_user,
    get_current_user_optional,
    require_admin,
)
from backend.app.services.auth.passwords import (
    dummy_verify,
    hash_password,
    verify_password,
)
from backend.app.services.auth.seeds import seed_auth
from backend.app.services.auth.sessions import (
    SESSION_COOKIE_NAME,
    create_session,
    delete_session,
    delete_user_sessions,
    verify_and_slide_session,
)


@pytest.fixture
def client():
    """Fresh test client with clean cookie jar for each test."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def init_auth_db():
    """Ensure seeds and tables are ready before each test, and reset consultant user."""
    db = SessionLocal()
    try:
        seed_auth(db)
        consultant = db.query(AuthUser).filter(AuthUser.username == "user").first()
        if consultant:
            consultant.failed_logins = 0
            consultant.locked_until = None
            consultant.status = "active"
            consultant.password_hash = hash_password(settings.AUTH_SEED_CONSULTANT_PASSWORD)
            db.commit()
    finally:
        db.close()
    yield


def test_seed_idempotent():
    """Idempotent seed can run multiple times without duplicating or overwriting."""
    db = SessionLocal()
    try:
        # Seed already ran in fixture; modify consultant user display name
        consultant = db.query(AuthUser).filter(AuthUser.username == "user").first()
        assert consultant is not None
        consultant.display_name = "Custom Consultant Name"
        db.commit()

        # Run seed again
        seed_auth(db)

        # Check no duplicates
        users_named_user = (
            db.query(AuthUser).filter(AuthUser.username == "user").all()
        )
        assert len(users_named_user) == 1
        # Custom edit preserved
        assert users_named_user[0].display_name == "Custom Consultant Name"

        # Check seeded teams
        teams = db.query(AuthTeam).all()
        team_names = {t.name for t in teams}
        for expected in ("School", "Corporate", "QShala", "Consultant"):
            assert expected in team_names
    finally:
        db.close()


def test_login_success_with_seeded_user(client):
    """Login success with seeded user sets qs_session cookie and returns user dict."""
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "user", "password": settings.AUTH_SEED_CONSULTANT_PASSWORD},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "user" in data
    user_data = data["user"]
    assert user_data["username"] == "user"
    assert user_data["role"] == "member"
    assert user_data["status"] == "active"
    assert user_data["team_id"] == "Consultant"

    # Verify session cookie was set
    assert SESSION_COOKIE_NAME in resp.cookies
    cookie_header = resp.headers.get("set-cookie", "")
    assert "httponly" in cookie_header.lower()
    assert "samesite=lax" in cookie_header.lower()
    assert "path=/" in cookie_header.lower()


def test_login_failure_bad_password(client):
    """Wrong password returns uniform 401 error."""
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "user", "password": "definitely-wrong-password-123"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid username or password"


def test_login_failure_unknown_username(client):
    """Unknown username returns identical uniform 401 error."""
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "nonexistent_user_999", "password": "somepassword"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid username or password"


def test_login_lockout_after_5_attempts(client):
    """5 wrong attempts locks out for 15 minutes, with uniform message throughout."""
    db = SessionLocal()
    try:
        user = db.query(AuthUser).filter(AuthUser.username == "user").first()
        user.failed_logins = 0
        user.locked_until = None
        db.commit()
    finally:
        db.close()

    # Perform 4 failed logins
    for i in range(4):
        r = client.post("/api/v1/auth/login", json={"username": "user", "password": f"wrong-{i}"})
        assert r.status_code == 401
        assert r.json()["detail"] == "Invalid username or password"

    # 5th failed login triggers lockout
    r5 = client.post("/api/v1/auth/login", json={"username": "user", "password": "wrong-5"})
    assert r5.status_code == 401
    assert r5.json()["detail"] == "Invalid username or password"

    # Verify locked in DB
    db = SessionLocal()
    try:
        user = db.query(AuthUser).filter(AuthUser.username == "user").first()
        assert user.failed_logins == 5
        assert user.locked_until is not None
        locked_dt = (
            user.locked_until.replace(tzinfo=timezone.utc)
            if user.locked_until.tzinfo is None
            else user.locked_until
        )
        assert locked_dt > datetime.now(timezone.utc)
    finally:
        db.close()

    # Even right password fails with uniform message while locked
    r_locked = client.post(
        "/api/v1/auth/login",
        json={"username": "user", "password": settings.AUTH_SEED_CONSULTANT_PASSWORD},
    )
    assert r_locked.status_code == 401
    assert r_locked.json()["detail"] == "Invalid username or password"

    # Advance time / clear lock and login succeeds
    db = SessionLocal()
    try:
        user = db.query(AuthUser).filter(AuthUser.username == "user").first()
        user.locked_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    r_ok = client.post(
        "/api/v1/auth/login",
        json={"username": "user", "password": settings.AUTH_SEED_CONSULTANT_PASSWORD},
    )
    assert r_ok.status_code == 200


def test_sliding_session_lifecycle():
    """Sliding session updates expires_at up to 30 days absolute."""
    db = SessionLocal()
    try:
        user = db.query(AuthUser).filter(AuthUser.username == "user").first()
        raw_token, session_record = create_session(db, user)
        initial_expires = session_record.expires_at

        # Verify initial
        verified_user = verify_and_slide_session(db, raw_token)
        assert verified_user is not None
        assert verified_user.id == user.id

        # Fast forward last_seen_at past throttle (e.g. 10 minutes)
        session_record.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        db.commit()

        # Sliding should update expires_at
        verify_and_slide_session(db, raw_token)
        db.refresh(session_record)
        assert session_record.expires_at > initial_expires
    finally:
        db.close()


def test_session_older_than_30_days_rejected():
    """Session older than 30-day absolute ceiling is rejected and deleted."""
    db = SessionLocal()
    try:
        user = db.query(AuthUser).filter(AuthUser.username == "user").first()
        raw_token, session_record = create_session(db, user)

        # Set created_at to 31 days ago
        now = datetime.now(timezone.utc)
        session_record.created_at = now - timedelta(days=31)
        session_record.expires_at = now + timedelta(days=1)
        session_record.last_seen_at = now
        db.commit()

        # Verification must fail and purge session
        result = verify_and_slide_session(db, raw_token)
        assert result is None

        # Check purged from DB
        sess = db.query(AuthSession).filter(AuthSession.id == session_record.id).first()
        assert sess is None
    finally:
        db.close()


def test_logout_invalidates_session(client):
    """Logout deletes session in DB and clears cookie."""
    # Login
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"username": "user", "password": settings.AUTH_SEED_CONSULTANT_PASSWORD},
    )
    assert login_resp.status_code == 200
    token = login_resp.cookies.get(SESSION_COOKIE_NAME)
    assert token is not None

    # Verify session exists in DB
    db = SessionLocal()
    try:
        user = verify_and_slide_session(db, token)
        assert user is not None
    finally:
        db.close()

    # Logout
    logout_resp = client.post(
        "/api/v1/auth/logout",
        cookies={SESSION_COOKIE_NAME: token},
        headers={"Origin": "http://localhost:3000"},
    )
    assert logout_resp.status_code == 200

    # Cookie is cleared
    cookie_header = logout_resp.headers.get("set-cookie", "")
    assert f"{SESSION_COOKIE_NAME}=" in cookie_header

    # Session no longer valid in DB
    db = SessionLocal()
    try:
        user = verify_and_slide_session(db, token)
        assert user is None
    finally:
        db.close()


def test_blocking_user_invalidates_sessions_immediately(client):
    """Blocking a user deletes their sessions at once."""
    db = SessionLocal()
    try:
        admin = db.query(AuthUser).filter(AuthUser.role == "admin", AuthUser.status == "active").first()
        admin_token, _ = create_session(db, admin)

        user = db.query(AuthUser).filter(AuthUser.username == "user").first()
        user_id = user.id
        user.status = "active"
        db.commit()

        # Create session for user
        user_token, user_sess = create_session(db, user)
        assert verify_and_slide_session(db, user_token) is not None
    finally:
        db.close()

    # Admin blocks user
    patch_resp = client.patch(
        f"/api/v1/auth/users/{user_id}",
        json={"status": "blocked"},
        cookies={SESSION_COOKIE_NAME: admin_token},
        headers={"Origin": "http://localhost:3000"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["status"] == "blocked"

    # User's session is now purged immediately
    db = SessionLocal()
    try:
        assert verify_and_slide_session(db, user_token) is None
        remaining_sessions = db.query(AuthSession).filter(AuthSession.user_id == user_id).count()
        assert remaining_sessions == 0
    finally:
        db.close()


def test_admin_only_endpoints_reject_members_with_403(client):
    """Members get 403 Forbidden on admin endpoints; admins succeed."""
    db = SessionLocal()
    try:
        user = db.query(AuthUser).filter(AuthUser.username == "user").first()
        member_token, _ = create_session(db, user)

        admin = db.query(AuthUser).filter(AuthUser.role == "admin", AuthUser.status == "active").first()
        admin_token, _ = create_session(db, admin)
    finally:
        db.close()

    # Member tries to list users
    r_member = client.get("/api/v1/auth/users", cookies={SESSION_COOKIE_NAME: member_token})
    assert r_member.status_code == 403
    assert "Admin role required" in r_member.json()["detail"]

    # Member tries to view audit
    r_audit_member = client.get("/api/v1/auth/audit", cookies={SESSION_COOKIE_NAME: member_token})
    assert r_audit_member.status_code == 403

    # Admin lists users
    r_admin = client.get("/api/v1/auth/users", cookies={SESSION_COOKIE_NAME: admin_token})
    assert r_admin.status_code == 200
    assert "items" in r_admin.json()

    # Admin views audit
    r_audit_admin = client.get("/api/v1/auth/audit", cookies={SESSION_COOKIE_NAME: admin_token})
    assert r_audit_admin.status_code == 200
    assert "items" in r_audit_admin.json()


def test_last_admin_protection(client):
    """Guardrail refuses to demote or block the last active admin."""
    db = SessionLocal()
    try:
        admins = db.query(AuthUser).filter(AuthUser.role == "admin", AuthUser.status == "active").all()
        sole_admin = admins[0]
        sole_admin_id = sole_admin.id
        for extra in admins[1:]:
            extra.status = "pending"
        db.commit()

        admin_token, _ = create_session(db, sole_admin)
    finally:
        db.close()

    # Attempt to demote sole admin to member
    r_demote = client.patch(
        f"/api/v1/auth/users/{sole_admin_id}",
        json={"role": "member"},
        cookies={SESSION_COOKIE_NAME: admin_token},
        headers={"Origin": "http://localhost:3000"},
    )
    assert r_demote.status_code == 400
    assert "last active admin" in r_demote.json()["detail"].lower()

    # Attempt to block sole admin
    r_block = client.patch(
        f"/api/v1/auth/users/{sole_admin_id}",
        json={"status": "blocked"},
        cookies={SESSION_COOKIE_NAME: admin_token},
        headers={"Origin": "http://localhost:3000"},
    )
    assert r_block.status_code == 400
    assert "last active admin" in r_block.json()["detail"].lower()

    # Restore extra admins
    db = SessionLocal()
    try:
        for extra in db.query(AuthUser).filter(AuthUser.role == "admin").all():
            extra.status = "active"
        db.commit()
    finally:
        db.close()


def test_password_user_cannot_be_admin(client):
    """Guardrail: password user can never be created or promoted to admin role."""
    db = SessionLocal()
    try:
        admin = db.query(AuthUser).filter(AuthUser.role == "admin", AuthUser.status == "active").first()
        admin_token, _ = create_session(db, admin)
        consultant = db.query(AuthUser).filter(AuthUser.username == "user").first()
        consultant_id = consultant.id
    finally:
        db.close()

    # 1. Attempt to create a password user with admin role
    r_create = client.post(
        "/api/v1/auth/users",
        json={
            "username": f"pwdadmin_{secrets.token_hex(4)}",
            "display_name": "Test Pwd Admin",
            "provider": "password",
            "password": "Password123!",
            "role": "admin",
        },
        cookies={SESSION_COOKIE_NAME: admin_token},
        headers={"Origin": "http://localhost:3000"},
    )
    assert r_create.status_code == 400
    assert "password users cannot" in r_create.json()["detail"].lower()

    # 2. Attempt to promote existing consultant password user to admin
    r_promote = client.patch(
        f"/api/v1/auth/users/{consultant_id}",
        json={"role": "admin"},
        cookies={SESSION_COOKIE_NAME: admin_token},
        headers={"Origin": "http://localhost:3000"},
    )
    assert r_promote.status_code == 400
    assert "password users cannot" in r_promote.json()["detail"].lower()


def test_audit_logging_records_events(client):
    """Audit log records login, logout, user created, user updated, and password reset."""
    db = SessionLocal()
    try:
        admin = db.query(AuthUser).filter(AuthUser.role == "admin", AuthUser.status == "active").first()
        admin_token, _ = create_session(db, admin)
        admin_id = admin.id
    finally:
        db.close()

    # 1. User created
    uname = f"testaudit_{secrets.token_hex(4)}"
    create_resp = client.post(
        "/api/v1/auth/users",
        json={
            "username": uname,
            "display_name": "Audit Test",
            "provider": "password",
            "password": "InitialPass123!",
            "role": "member",
        },
        cookies={SESSION_COOKIE_NAME: admin_token},
        headers={"Origin": "http://localhost:3000"},
    )
    assert create_resp.status_code == 201
    new_id = create_resp.json()["id"]

    db = SessionLocal()
    try:
        audit_create = (
            db.query(AuthAudit)
            .filter(AuthAudit.action == "user_created", AuthAudit.target_user_id == new_id)
            .first()
        )
        assert audit_create is not None
        assert audit_create.actor_user_id == admin_id
    finally:
        db.close()

    # 2. User updated
    patch_resp = client.patch(
        f"/api/v1/auth/users/{new_id}",
        json={"display_name": "Audit Test Renamed"},
        cookies={SESSION_COOKIE_NAME: admin_token},
        headers={"Origin": "http://localhost:3000"},
    )
    assert patch_resp.status_code == 200

    db = SessionLocal()
    try:
        audit_update = (
            db.query(AuthAudit)
            .filter(AuthAudit.action == "user_updated", AuthAudit.target_user_id == new_id)
            .first()
        )
        assert audit_update is not None
    finally:
        db.close()

    # 3. Password reset by admin
    reset_resp = client.post(
        f"/api/v1/auth/users/{new_id}/reset-password",
        json={"new_password": "NewResetPassword123!"},
        cookies={SESSION_COOKIE_NAME: admin_token},
        headers={"Origin": "http://localhost:3000"},
    )
    assert reset_resp.status_code == 200

    db = SessionLocal()
    try:
        audit_reset = (
            db.query(AuthAudit)
            .filter(AuthAudit.action == "password_reset_by_admin", AuthAudit.target_user_id == new_id)
            .first()
        )
        assert audit_reset is not None
    finally:
        db.close()

    # 4. Login success
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"username": uname, "password": "NewResetPassword123!"},
    )
    assert login_resp.status_code == 200
    user_token = login_resp.cookies[SESSION_COOKIE_NAME]

    db = SessionLocal()
    try:
        audit_login = (
            db.query(AuthAudit)
            .filter(AuthAudit.action == "login_success", AuthAudit.target_user_id == new_id)
            .first()
        )
        assert audit_login is not None
    finally:
        db.close()

    # 5. Logout
    logout_resp = client.post(
        "/api/v1/auth/logout",
        cookies={SESSION_COOKIE_NAME: user_token},
        headers={"Origin": "http://localhost:3000"},
    )
    assert logout_resp.status_code == 200

    db = SessionLocal()
    try:
        audit_logout = (
            db.query(AuthAudit)
            .filter(AuthAudit.action == "logout", AuthAudit.target_user_id == new_id)
            .first()
        )
        assert audit_logout is not None
    finally:
        db.close()


def test_soft_mode_never_blocks(client, monkeypatch):
    """Soft mode allows unauthenticated requests through without 401."""
    monkeypatch.setattr(settings, "AUTH_ENFORCED", False)
    monkeypatch.setattr(settings, "API_KEY_SECRET", None)
    monkeypatch.delenv("REQUIRE_API_KEY", raising=False)

    # Calling protected endpoint without cookie or key
    r = client.get("/api/v1/stats/dashboard")
    assert r.status_code != 401
    assert r.status_code != 403

    # Calling /me without login in soft mode returns 200 with null
    r_me = client.get("/api/v1/auth/me")
    assert r_me.status_code == 200
    assert r_me.json() is None


def test_enforced_mode_blocks_unauthenticated_and_passes_public(client, monkeypatch):
    """Enforced mode blocks protected routes with 401, but allows public routes."""
    monkeypatch.setattr(settings, "AUTH_ENFORCED", True)
    monkeypatch.setattr(settings, "API_KEY_SECRET", None)

    # Protected routes blocked
    r_protected = client.get("/api/v1/stats/dashboard")
    assert r_protected.status_code == 401
    assert r_protected.json()["detail"] == "Authentication required"

    # GET /me blocked with 401 in enforced mode
    r_me = client.get("/api/v1/auth/me")
    assert r_me.status_code == 401

    # Public routes pass through (not blocked by auth gate)
    assert client.get("/health").status_code != 401
    assert client.get("/api/v1/health").status_code != 401
    assert client.get(f"{settings.API_V1_STR}/openapi.json").status_code == 200
    assert client.get(f"{settings.API_V1_STR}/docs").status_code == 200


def test_enforced_mode_passes_with_session_or_api_key(client, monkeypatch):
    """Enforced mode allows requests with valid session or valid X-API-Key."""
    monkeypatch.setattr(settings, "AUTH_ENFORCED", True)
    monkeypatch.setattr(settings, "API_KEY_SECRET", "super-secret-key")

    db = SessionLocal()
    try:
        user = db.query(AuthUser).filter(AuthUser.username == "user").first()
        token, _ = create_session(db, user)
    finally:
        db.close()

    # 1. Allowed with session cookie
    r_cookie = client.get("/api/v1/stats/dashboard", cookies={SESSION_COOKIE_NAME: token})
    assert r_cookie.status_code != 401

    # 2. Allowed with X-API-Key
    r_key = client.get("/api/v1/stats/dashboard", headers={"X-API-Key": "super-secret-key"})
    assert r_key.status_code != 401


def test_csrf_origin_check(client, monkeypatch):
    """CSRF: cookie-authenticated mutating requests require approved Origin; X-API-Key is exempt."""
    monkeypatch.setattr(settings, "AUTH_PUBLIC_BASE_URL", "https://qshala.example.com")
    monkeypatch.setattr(settings, "API_KEY_SECRET", "machine-key-123")

    db = SessionLocal()
    try:
        admin = db.query(AuthUser).filter(AuthUser.role == "admin", AuthUser.status == "active").first()
        admin_id = admin.id
        admin_token, _ = create_session(db, admin)
    finally:
        db.close()

    payload = {"display_name": "New Name"}

    # 1. Cookie request with missing Origin -> 403
    r_no_origin = client.patch(
        f"/api/v1/auth/users/{admin_id}",
        json=payload,
        cookies={SESSION_COOKIE_NAME: admin_token},
    )
    assert r_no_origin.status_code == 403
    assert r_no_origin.json()["detail"] == "CSRF origin check failed"

    # 2. Cookie request with malicious Origin -> 403
    r_bad_origin = client.patch(
        f"/api/v1/auth/users/{admin_id}",
        json=payload,
        cookies={SESSION_COOKIE_NAME: admin_token},
        headers={"Origin": "https://evil-attacker-site.com"},
    )
    assert r_bad_origin.status_code == 403

    # 3. Cookie request with allowed AUTH_PUBLIC_BASE_URL -> 200
    r_good_origin = client.patch(
        f"/api/v1/auth/users/{admin_id}",
        json=payload,
        cookies={SESSION_COOKIE_NAME: admin_token},
        headers={"Origin": "https://qshala.example.com"},
    )
    assert r_good_origin.status_code == 200

    # 4. Cookie request with allowed localhost Origin -> 200
    r_local_origin = client.patch(
        f"/api/v1/auth/users/{admin_id}",
        json=payload,
        cookies={SESSION_COOKIE_NAME: admin_token},
        headers={"Origin": "http://localhost:3000"},
    )
    assert r_local_origin.status_code == 200

    # 5. Machine call with X-API-Key is exempt from Origin check -> 200
    r_key_exempt = client.patch(
        f"/api/v1/auth/users/{admin_id}",
        json=payload,
        cookies={SESSION_COOKIE_NAME: admin_token},
        headers={"X-API-Key": "machine-key-123"},  # no Origin header
    )
    assert r_key_exempt.status_code == 200


def test_change_password_endpoint(client):
    """Logged in password user can change password (min 8 chars)."""
    # Login as consultant
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"username": "user", "password": settings.AUTH_SEED_CONSULTANT_PASSWORD},
    )
    assert login_resp.status_code == 200
    token = login_resp.cookies[SESSION_COOKIE_NAME]

    # Change password too short -> 400
    r_short = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": settings.AUTH_SEED_CONSULTANT_PASSWORD, "new_password": "short"},
        cookies={SESSION_COOKIE_NAME: token},
        headers={"Origin": "http://localhost:3000"},
    )
    assert r_short.status_code == 400

    # Wrong old password -> 400
    r_wrong_old = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "wrong-old-pwd", "new_password": "BrandNewPassword123!"},
        cookies={SESSION_COOKIE_NAME: token},
        headers={"Origin": "http://localhost:3000"},
    )
    assert r_wrong_old.status_code == 400

    # Correct change
    r_ok = client.post(
        "/api/v1/auth/change-password",
        json={
            "old_password": settings.AUTH_SEED_CONSULTANT_PASSWORD,
            "new_password": "BrandNewPassword123!",
        },
        cookies={SESSION_COOKIE_NAME: token},
        headers={"Origin": "http://localhost:3000"},
    )
    assert r_ok.status_code == 200
    assert r_ok.json() == {"ok": True}

    # Verify new password works for login
    r_relogin = client.post(
        "/api/v1/auth/login",
        json={"username": "user", "password": "BrandNewPassword123!"},
    )
    assert r_relogin.status_code == 200

    # Restore default password for other tests
    client.post(
        "/api/v1/auth/change-password",
        json={
            "old_password": "BrandNewPassword123!",
            "new_password": settings.AUTH_SEED_CONSULTANT_PASSWORD,
        },
        cookies={SESSION_COOKIE_NAME: r_relogin.cookies[SESSION_COOKIE_NAME]},
        headers={"Origin": "http://localhost:3000"},
    )
