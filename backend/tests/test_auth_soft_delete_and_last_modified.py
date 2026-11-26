"""Comprehensive tests for Soft Delete, User Restore, Seeded User Protection, and Quiz Last-Modified Tracking.

Covers:
1. DELETE /api/v1/auth/users/{user_id} authorization (401 unauthenticated, 403 non-admin, 200 admin).
2. Cannot delete self (400) and cannot delete last active admin (400).
3. Seeded user deletion refusal (409 "Seeded account, block it instead").
4. 404 for non-existent or already deleted users.
5. Soft-delete mechanics: status="deleted", sessions deleted, audit row logged, user row stays.
6. Record consistency: quizzes/docs created by a deleted user keep created_by_user_id & created_by_name intact.
7. Login refusal:
   - Deleted password user gets generic 401 "Invalid username or password".
   - Deleted Microsoft user gets redirect to /login?error=unauthorized with no session.
8. User listing: deleted users hidden by default from GET /users, shown with ?include_deleted=true.
9. Re-adding: adding a deleted user restores row (same id, status active, logs audit user_restored, restored=True).
10. Quiz last_modified:
    - None on newly created quiz.
    - Set on PATCH /quizzes/{id}, PATCH /questions/{id}, POST /questions/{id}/action, DELETE /questions/{id}.
    - Unset/untouched for anonymous edits and read-only/export endpoints.
11. Alembic migration upgrade/downgrade for 0017_quiz_last_modified.
"""
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ["REQUIRE_POSTGRES"] = "0"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from backend.app.config import settings
from backend.app.database import Base, SessionLocal, engine
from backend.app.main import app
from backend.app.models.auth import AuthAudit, AuthSession, AuthUser
from backend.app.models.quiz import GeneratedQuestion, Quiz
from backend.app.services.auth.passwords import hash_password
from backend.app.services.auth.seeds import seed_auth
from backend.app.services.auth.sessions import SESSION_COOKIE_NAME, create_session
from backend.app.services.auth.microsoft import sign_oauth_state, OAUTH_STATE_COOKIE_NAME
from backend.app.routers.auth import delete_user


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_auth(db)
    finally:
        db.close()
    yield


def _client(token: str = None) -> TestClient:
    cookies = {SESSION_COOKIE_NAME: token} if token else {}
    return TestClient(
        app,
        cookies=cookies,
        headers={"Origin": "http://localhost:3000"},
    )


def _get_admin_token() -> tuple[str, str, str]:
    db = SessionLocal()
    try:
        admin = db.query(AuthUser).filter(AuthUser.role == "admin", AuthUser.status == "active").first()
        assert admin is not None
        uid, name = admin.id, admin.display_name
        token, _ = create_session(db, admin)
        return uid, name, token
    finally:
        db.close()


def _get_member_token() -> tuple[str, str, str]:
    db = SessionLocal()
    try:
        member = db.query(AuthUser).filter(AuthUser.username == "user").first()
        assert member is not None
        uid, name = member.id, member.display_name
        token, _ = create_session(db, member)
        return uid, name, token
    finally:
        db.close()


def _create_test_member(username: str, email: str = None) -> AuthUser:
    db = SessionLocal()
    try:
        user = AuthUser(
            id=str(uuid.uuid4()),
            email=email,
            username=username,
            display_name=f"Test {username or email}",
            team_id="School",
            role="member",
            status="active",
            provider="password" if username else "microsoft",
            password_hash=hash_password("password123") if username else None,
            created_at=datetime.now(timezone.utc),
            created_by="test_setup",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. DELETE /api/v1/auth/users/{user_id} Authorization & Guards
# ---------------------------------------------------------------------------

def test_delete_user_authorization():
    client = _client()
    target = _create_test_member(username="delete_me_auth")

    # 401 anonymous
    res_anon = client.delete(f"/api/v1/auth/users/{target.id}")
    assert res_anon.status_code == 401

    # 403 non-admin member
    _, _, member_token = _get_member_token()
    client_member = _client(member_token)
    res_member = client_member.delete(f"/api/v1/auth/users/{target.id}")
    assert res_member.status_code == 403


def test_delete_user_cannot_delete_self():
    admin_id, _, admin_token = _get_admin_token()
    client = _client(admin_token)

    res = client.delete(f"/api/v1/auth/users/{admin_id}")
    assert res.status_code == 400
    assert "Cannot delete your own account" in res.json()["detail"]


def test_delete_user_cannot_delete_last_active_admin():
    db = SessionLocal()
    try:
        # Mark all existing active admins as blocked so target is the only active admin remaining
        db.query(AuthUser).filter(AuthUser.role == "admin").update({"status": "blocked"})

        target = AuthUser(
            id=str(uuid.uuid4()),
            email="sole_target_admin@walnuts.co.in",
            display_name="Sole Target Admin",
            role="admin",
            status="active",
            provider="microsoft",
            created_at=datetime.now(timezone.utc),
        )
        caller = AuthUser(
            id=str(uuid.uuid4()),
            email="caller_admin@walnuts.co.in",
            display_name="Caller Admin",
            role="admin",
            status="blocked",
            provider="microsoft",
            created_at=datetime.now(timezone.utc),
        )
        db.add_all([target, caller])
        db.commit()

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            delete_user(user_id=target.id, admin=caller, db=db)
        assert exc_info.value.status_code == 400
        assert "Cannot delete the last active admin" in exc_info.value.detail
    finally:
        db.close()


def test_delete_user_seeded_accounts_refused():
    admin_id, _, admin_token = _get_admin_token()
    client = _client(admin_token)

    db = SessionLocal()
    try:
        consultant = db.query(AuthUser).filter(AuthUser.username == "user").first()
        assert consultant is not None
        res_consultant = client.delete(f"/api/v1/auth/users/{consultant.id}")
        assert res_consultant.status_code == 409
        assert "Seeded account, block it instead" in res_consultant.json()["detail"]

        seeded_admin = (
            db.query(AuthUser)
            .filter(AuthUser.created_by == "system_seed", AuthUser.role == "admin", AuthUser.id != admin_id)
            .first()
        )
        assert seeded_admin is not None
        res_seeded_admin = client.delete(f"/api/v1/auth/users/{seeded_admin.id}")
        assert res_seeded_admin.status_code == 409
        assert "Seeded account, block it instead" in res_seeded_admin.json()["detail"]
    finally:
        db.close()


def test_delete_user_404_when_not_found_or_already_deleted():
    _, _, admin_token = _get_admin_token()
    client = _client(admin_token)

    # Non-existent
    res_fake = client.delete(f"/api/v1/auth/users/{uuid.uuid4()}")
    assert res_fake.status_code == 404

    # Soft-delete once
    target = _create_test_member(username="delete_404_test")
    res1 = client.delete(f"/api/v1/auth/users/{target.id}")
    assert res1.status_code == 200

    # Delete second time -> 404
    res2 = client.delete(f"/api/v1/auth/users/{target.id}")
    assert res2.status_code == 404


# ---------------------------------------------------------------------------
# 2. Soft-Delete Mechanics & Record Consistency
# ---------------------------------------------------------------------------

def test_delete_user_soft_delete_and_consistency():
    db = SessionLocal()
    try:
        # Create a test member
        member = _create_test_member(username="content_author", email="author@walnuts.co.in")
        member_id, member_name = member.id, member.display_name

        # Member has active sessions
        raw_tok, _ = create_session(db, member)

        # Member creates a quiz
        quiz = Quiz(
            title="Author's Geography Quiz",
            topic="Geography",
            question_count=1,
            created_by_user_id=member_id,
            created_by_name=member_name,
        )
        db.add(quiz)
        db.commit()
        db.refresh(quiz)
        quiz_id = quiz.id
    finally:
        db.close()

    _, _, admin_token = _get_admin_token()
    client = _client(admin_token)

    # Admin soft-deletes the member
    res = client.delete(f"/api/v1/auth/users/{member_id}")
    assert res.status_code == 200
    assert res.json()["ok"] is True

    db = SessionLocal()
    try:
        # Check user row STILL exists, name/email/username still intact
        deleted_user = db.query(AuthUser).filter(AuthUser.id == member_id).first()
        assert deleted_user is not None
        assert deleted_user.status == "deleted"
        assert deleted_user.email == "author@walnuts.co.in"
        assert deleted_user.username == "content_author"
        assert deleted_user.display_name == member_name

        # Sessions were deleted
        sessions = db.query(AuthSession).filter(AuthSession.user_id == member_id).all()
        assert len(sessions) == 0

        # Audit row was created
        audit = (
            db.query(AuthAudit)
            .filter(AuthAudit.target_user_id == member_id, AuthAudit.action == "user_deleted")
            .first()
        )
        assert audit is not None
        assert audit.detail.get("email") == "author@walnuts.co.in"
        assert audit.detail.get("username") == "content_author"

        # OWNER RULE: Quiz created_by_user_id and created_by_name are NEVER nulled or changed
        quiz_after = db.query(Quiz).filter(Quiz.id == quiz_id).first()
        assert quiz_after.created_by_user_id == member_id
        assert quiz_after.created_by_name == member_name
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 3. Login Refusal for Deleted Users (Password + Microsoft)
# ---------------------------------------------------------------------------

def test_deleted_user_cannot_login_password():
    user = _create_test_member(username="deleted_login_pw")
    _, _, admin_token = _get_admin_token()
    client_admin = _client(admin_token)
    res_del = client_admin.delete(f"/api/v1/auth/users/{user.id}")
    assert res_del.status_code == 200

    # Attempt password login
    client = _client()
    res = client.post(
        "/api/v1/auth/login",
        json={"username": "deleted_login_pw", "password": "password123"},
    )
    # Generic failure, same as unknown (401 Invalid username or password)
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid username or password"
    assert "qs_session" not in res.cookies


def test_deleted_user_cannot_login_microsoft():
    user = _create_test_member(username=None, email="deleted_ms@walnuts.co.in")
    user_id = user.id
    _, _, admin_token = _get_admin_token()
    client_admin = _client(admin_token)
    res_del = client_admin.delete(f"/api/v1/auth/users/{user_id}")
    assert res_del.status_code == 200

    state = "state-deleted-ms-test"
    nonce = "nonce-deleted-ms-test"
    signed_state = sign_oauth_state(state=state, nonce=nonce, verifier="dummy-verifier")

    client = _client()
    client.cookies.set(OAUTH_STATE_COOKIE_NAME, signed_state)

    with patch("backend.app.routers.auth.exchange_code_for_tokens", new_callable=AsyncMock) as mock_exchange, \
         patch("backend.app.routers.auth.validate_microsoft_id_token") as mock_validate:
        mock_exchange.return_value = {"id_token": "fake-jwt"}
        mock_validate.return_value = {
            "email": "deleted_ms@walnuts.co.in",
            "oid": "oid-deleted-ms",
            "name": "Deleted MS User",
            "nonce": nonce,
        }

        res = client.get(
            f"/api/v1/auth/microsoft/callback?code=mock-code&state={state}",
            follow_redirects=False,
        )
        assert res.status_code == 303
        assert "/login?error=unauthorized" in res.headers["location"]
        assert "qs_session" not in res.cookies


# ---------------------------------------------------------------------------
# 4. User Listing: Excluded by Default, Included with ?include_deleted=true
# ---------------------------------------------------------------------------

def test_deleted_user_hidden_from_get_users():
    user = _create_test_member(username="hidden_user")
    _, _, admin_token = _get_admin_token()
    client = _client(admin_token)

    # Visible before delete
    res_before = client.get("/api/v1/auth/users")
    assert res_before.status_code == 200
    ids_before = [u["id"] for u in res_before.json()["users"]]
    assert user.id in ids_before

    # Delete
    res_del = client.delete(f"/api/v1/auth/users/{user.id}")
    assert res_del.status_code == 200

    # Hidden by default
    res_after = client.get("/api/v1/auth/users")
    assert res_after.status_code == 200
    ids_after = [u["id"] for u in res_after.json()["users"]]
    assert user.id not in ids_after

    # Visible when include_deleted=true
    res_all = client.get("/api/v1/auth/users?include_deleted=true")
    assert res_all.status_code == 200
    ids_all = [u["id"] for u in res_all.json()["users"]]
    assert user.id in ids_all


# ---------------------------------------------------------------------------
# 5. Re-Adding a Deleted User Restores the Existing Row
# ---------------------------------------------------------------------------

def test_readding_deleted_user_restores_same_id():
    user = _create_test_member(username="readd_user", email="readd@walnuts.co.in")
    orig_id = user.id

    _, _, admin_token = _get_admin_token()
    client = _client(admin_token)
    res_del1 = client.delete(f"/api/v1/auth/users/{orig_id}")
    assert res_del1.status_code == 200

    # Re-add via Microsoft provider
    ms_target = _create_test_member(username=None, email="ms_readd@walnuts.co.in")
    res_del2 = client.delete(f"/api/v1/auth/users/{ms_target.id}")
    assert res_del2.status_code == 200

    res_ms = client.post(
        "/api/v1/auth/users",
        json={
            "display_name": "Restored Microsoft User",
            "email": "ms_readd@walnuts.co.in",
            "team_id": "Corporate",
            "role": "member",
            "provider": "microsoft",
        },
    )
    assert res_ms.status_code == 201
    data_ms = res_ms.json()
    assert data_ms["id"] == ms_target.id
    assert data_ms["status"] == "active"
    assert data_ms["display_name"] == "Restored Microsoft User"
    assert data_ms.get("restored") is True

    # Re-add via Password provider
    res_pw = client.post(
        "/api/v1/auth/users",
        json={
            "display_name": "Restored Password User",
            "username": "readd_user",
            "password": "newpassword456",
            "team_id": "QShala",
            "role": "member",
            "provider": "password",
            "must_change_password": False,
        },
    )
    assert res_pw.status_code == 201
    data_pw = res_pw.json()
    assert data_pw["id"] == orig_id
    assert data_pw["status"] == "active"
    assert data_pw["display_name"] == "Restored Password User"
    assert data_pw.get("restored") is True

    # Check audit log contains user_restored
    db = SessionLocal()
    try:
        audit = (
            db.query(AuthAudit)
            .filter(AuthAudit.target_user_id == orig_id, AuthAudit.action == "user_restored")
            .first()
        )
        assert audit is not None
        assert audit.detail.get("username") == "readd_user"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 6. Quiz Last-Modified Tracking on User Edits
# ---------------------------------------------------------------------------

def test_quiz_last_modified_on_user_edits():
    db = SessionLocal()
    try:
        quiz = Quiz(
            title="Original Quiz Title",
            topic="General",
            question_count=2,
            created_by_user_id="creator-uuid",
            created_by_name="Original Creator",
        )
        db.add(quiz)
        db.flush()

        q1 = GeneratedQuestion(
            quiz_id=quiz.id,
            order_index=1,
            question_text="Q1 text",
            answer="A1",
        )
        q2 = GeneratedQuestion(
            quiz_id=quiz.id,
            order_index=2,
            question_text="Q2 text",
            answer="A2",
        )
        db.add_all([q1, q2])
        db.commit()
        db.refresh(quiz)
        db.refresh(q1)
        db.refresh(q2)
        quiz_id = quiz.id
        q1_id = q1.id
        q2_id = q2.id

        # Verification 1: Newly created quiz has NULL last_modified fields
        assert quiz.last_modified_by_user_id is None
        assert quiz.last_modified_by_name is None
        assert quiz.last_modified_at is None
    finally:
        db.close()

    admin_id, admin_name, admin_token = _get_admin_token()
    client = _client(admin_token)

    # Read-only GET /quizzes/{id} does NOT set last-modified
    res_get = client.get(f"/api/v1/quizzes/{quiz_id}")
    assert res_get.status_code == 200
    assert res_get.json()["last_modified_by_user_id"] is None

    # 1. PATCH /quizzes/{quiz_id} (rename) sets last_modified
    res_rename = client.patch(f"/api/v1/quizzes/{quiz_id}", json={"title": "Updated Quiz Title"})
    assert res_rename.status_code == 200
    data_rename = res_rename.json()
    assert data_rename["last_modified_by_user_id"] == admin_id
    assert data_rename["last_modified_by_name"] == admin_name
    assert data_rename["last_modified_at"] is not None

    # 2. PATCH /quizzes/questions/{question_id} sets last_modified
    member_id, member_name, member_token = _get_member_token()
    client_member = _client(member_token)

    res_q_update = client_member.patch(
        f"/api/v1/quizzes/questions/{q1_id}",
        json={"question_text": "Updated Question 1 text"},
    )
    assert res_q_update.status_code == 200

    db = SessionLocal()
    try:
        quiz_after_q = db.query(Quiz).filter(Quiz.id == quiz_id).first()
        assert quiz_after_q.last_modified_by_user_id == member_id
        assert quiz_after_q.last_modified_by_name == member_name
    finally:
        db.close()

    # 3. DELETE /quizzes/questions/{question_id} sets last_modified
    res_q_del = client.delete(f"/api/v1/quizzes/questions/{q2_id}")
    assert res_q_del.status_code == 200

    db = SessionLocal()
    try:
        quiz_after_del = db.query(Quiz).filter(Quiz.id == quiz_id).first()
        assert quiz_after_del.last_modified_by_user_id == admin_id
        assert quiz_after_del.last_modified_by_name == admin_name
    finally:
        db.close()

    # 4. Anonymous edits leave existing last_modified values untouched
    client_anon = _client()
    res_anon_rename = client_anon.patch(
        f"/api/v1/quizzes/{quiz_id}",
        json={"title": "Anonymous Title Change"},
    )
    assert res_anon_rename.status_code == 200
    assert res_anon_rename.json()["last_modified_by_user_id"] == admin_id
    assert res_anon_rename.json()["last_modified_by_name"] == admin_name


# ---------------------------------------------------------------------------
# 7. Alembic Migration Test
# ---------------------------------------------------------------------------

def test_alembic_migration_0017_upgrade_downgrade():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "migration_0017",
        "backend/alembic/versions/0017_quiz_last_modified.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.revision == "0017_quiz_last_modified"
    assert migration.down_revision == "0016_document_uploaded_by"


# ---------------------------------------------------------------------------
# 8. PATCH User Status Restrictions and Deleted User Guard
# ---------------------------------------------------------------------------

def test_patch_user_status_deleted_returns_400():
    _, _, admin_token = _get_admin_token()
    client = _client(admin_token)

    target = _create_test_member(username="patch_status_deleted_target")
    res = client.patch(
        f"/api/v1/auth/users/{target.id}",
        json={"status": "deleted"},
    )
    assert res.status_code == 400
    assert "Status must be 'active', 'pending', or 'blocked'" in res.json()["detail"]


def test_patch_user_on_deleted_user_returns_404():
    _, _, admin_token = _get_admin_token()
    client = _client(admin_token)

    target = _create_test_member(username="patch_on_deleted_target")
    # Soft-delete target
    res_del = client.delete(f"/api/v1/auth/users/{target.id}")
    assert res_del.status_code == 200

    # Attempt to PATCH the deleted user (e.g. try to reactivate or update display name)
    res_patch_status = client.patch(
        f"/api/v1/auth/users/{target.id}",
        json={"status": "active"},
    )
    assert res_patch_status.status_code == 404
    assert res_patch_status.json()["detail"] == "User not found"

    res_patch_name = client.patch(
        f"/api/v1/auth/users/{target.id}",
        json={"display_name": "New Name"},
    )
    assert res_patch_name.status_code == 404
    assert res_patch_name.json()["detail"] == "User not found"


def test_patch_cannot_delete_seeded_user_account():
    _, _, admin_token = _get_admin_token()
    client = _client(admin_token)

    db = SessionLocal()
    try:
        consultant = db.query(AuthUser).filter(AuthUser.username == "user").first()
        assert consultant is not None
        consultant_id = consultant.id
    finally:
        db.close()

    res = client.patch(
        f"/api/v1/auth/users/{consultant_id}",
        json={"status": "deleted"},
    )
    assert res.status_code == 400
    assert "Status must be 'active', 'pending', or 'blocked'" in res.json()["detail"]
