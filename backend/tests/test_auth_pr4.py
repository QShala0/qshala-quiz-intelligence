"""Tests for Auth PR 4:
1. Quiz created_by_user_id and created_by_name (prompt builder & classic builder, signed-in vs anonymous).
2. SharePoint admin routes require admin role (401 unauth, 403 member, 200 admin); open routes work for members.
3. Enforced mode safety: /questions requires session (401 -> 200), /costs/refresh works with X-Cron-Secret alone
   and fails with wrong secret, and enforced-mode matrix covers all public/exempt and M2M routes.
"""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ["REQUIRE_POSTGRES"] = "0"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from backend.app.config import settings
from backend.app.database import Base, SessionLocal
from backend.app.main import app
from backend.app.models.auth import AuthUser
from backend.app.models.document import Document
from backend.app.models.question import Question
from backend.app.models.quiz import Quiz
from backend.app.services.auth.seeds import seed_auth
from backend.app.services.auth.sessions import SESSION_COOKIE_NAME, create_session


@pytest.fixture(autouse=True)
def setup_db():
    db = SessionLocal()
    try:
        Base.metadata.create_all(bind=db.get_bind())
        seed_auth(db)
    finally:
        db.close()
    yield


def _get_user_cookie(username_or_role: str) -> tuple[str, str, str]:
    db = SessionLocal()
    try:
        if username_or_role == "admin":
            user = db.query(AuthUser).filter(AuthUser.role == "admin", AuthUser.status == "active").first()
        else:
            user = db.query(AuthUser).filter(AuthUser.username == "user").first()
        assert user is not None
        uid, display_name = user.id, user.display_name
        raw_token, _ = create_session(db, user)
        return uid, display_name, raw_token
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. Quiz created_by wiring (prompt builder + classic builder)
# ---------------------------------------------------------------------------


def test_prompt_quiz_save_sets_created_by_when_signed_in_and_null_when_anonymous():
    db = SessionLocal()
    try:
        doc = Document(
            filename="geo.pptx",
            title="Geography",
            file_type="pptx",
            storage_path="/tmp/geo.pptx",
        )
        db.add(doc)
        db.flush()
        q = Question(
            document_id=doc.id,
            question_text="What is the capital of India?",
            answer="New Delhi",
            topic="Geography",
            question_type="SLIDE_QA",
            slot="LIVE",
        )
        db.add(q)
        db.commit()
        db.refresh(q)
        qid = q.id
    finally:
        db.close()

    # Anonymous (soft mode) -> created_by_user_id and created_by_name are NULL
    anon_client = TestClient(app)
    res_anon = anon_client.post(
        "/api/v1/prompt-quiz/save",
        json={"prompt": "1 question on geography", "question_ids": [qid]},
    )
    assert res_anon.status_code == 200
    anon_quiz_id = res_anon.json()["id"]

    # Signed-in member -> created_by_user_id and created_by_name are set
    member_id, member_name, member_token = _get_user_cookie("member")
    auth_client = TestClient(
        app,
        cookies={SESSION_COOKIE_NAME: member_token},
        headers={"Origin": "http://localhost:3000"},
    )
    res_auth = auth_client.post(
        "/api/v1/prompt-quiz/save",
        json={"prompt": "1 question on geography signed in", "question_ids": [qid]},
    )
    assert res_auth.status_code == 200
    auth_quiz_id = res_auth.json()["id"]

    # Verify DB and list API
    list_res = auth_client.get("/api/v1/quizzes")
    assert list_res.status_code == 200
    by_id = {item["id"]: item for item in list_res.json()}

    assert by_id[anon_quiz_id]["created_by_user_id"] is None
    assert by_id[anon_quiz_id]["created_by_name"] is None
    assert by_id[auth_quiz_id]["created_by_user_id"] == member_id
    assert by_id[auth_quiz_id]["created_by_name"] == member_name


def test_classic_quiz_generate_sets_created_by_when_signed_in_and_null_when_anonymous():
    async def _fake_generate(self, req, created_by_user_id=None, created_by_name=None):
        quiz = Quiz(
            title=f"{req.topic} – Tournament Prelims",
            topic=req.topic,
            question_count=req.question_count,
            status="READY",
            created_by_user_id=created_by_user_id,
            created_by_name=created_by_name,
        )
        self.db.add(quiz)
        self.db.commit()
        self.db.refresh(quiz)
        return quiz

    with patch("backend.app.services.generator.quiz_generator.QuizGenerator.generate_quiz", new=_fake_generate):
        anon_client = TestClient(app)
        res_anon = anon_client.post("/api/v1/quizzes/generate", json={"topic": "Science", "question_count": 5})
        assert res_anon.status_code == 200
        assert res_anon.json()["created_by_user_id"] is None
        assert res_anon.json()["created_by_name"] is None

        admin_id, admin_name, admin_token = _get_user_cookie("admin")
        admin_client = TestClient(
            app,
            cookies={SESSION_COOKIE_NAME: admin_token},
            headers={"Origin": "http://localhost:3000"},
        )
        res_admin = admin_client.post("/api/v1/quizzes/generate", json={"topic": "History", "question_count": 5})
        assert res_admin.status_code == 200
        assert res_admin.json()["created_by_user_id"] == admin_id
        assert res_admin.json()["created_by_name"] == admin_name


# ---------------------------------------------------------------------------
# 2. SharePoint admin role gate
# ---------------------------------------------------------------------------


def test_sharepoint_admin_routes_require_admin_and_open_routes_unchanged(monkeypatch):
    mock_sp = MagicMock()
    mock_sp.list_children = AsyncMock(return_value={"items": [], "next": None})
    mock_sp.search_items = AsyncMock(return_value=[])
    mock_sp.list_drives = AsyncMock(return_value=[{"id": "D", "name": "Documents"}])
    mock_sp.get_item = AsyncMock(return_value={"id": "fold1", "name": "Quizzes", "parentReference": {"path": "/drives/D/root:"}})
    monkeypatch.setattr("backend.app.routers.sharepoint.get_sharepoint_client", lambda: mock_sp)
    monkeypatch.setattr(settings, "SHAREPOINT_ADMIN_PASSWORD", "testpass")

    _, _, member_token = _get_user_cookie("member")
    _, _, admin_token = _get_user_cookie("admin")

    anon = TestClient(app)
    member = TestClient(
        app,
        cookies={SESSION_COOKIE_NAME: member_token},
        headers={"Origin": "http://localhost:3000"},
    )
    admin = TestClient(
        app,
        cookies={SESSION_COOKIE_NAME: admin_token},
        headers={"Origin": "http://localhost:3000"},
    )

    admin_calls = [
        ("POST", "/api/v1/sharepoint/admin/verify", {"json": {"password": "testpass"}}),
        ("GET", "/api/v1/sharepoint/admin/browse?drive_id=D", {}),
        ("GET", "/api/v1/sharepoint/admin/search?drive_id=D&q=quiz", {}),
        ("GET", "/api/v1/sharepoint/admin/allowed-folders?drive_id=D", {}),
        ("POST", "/api/v1/sharepoint/admin/allowed-folders", {"json": {"drive_id": "D", "folder_id": "fold1", "folder_name": "Quizzes", "folder_path": "Quizzes"}}),
        ("DELETE", "/api/v1/sharepoint/admin/allowed-folders/fold1?drive_id=D", {}),
        ("POST", "/api/v1/sharepoint/not-imported/hide-folder", {"json": {"folder_id": "fold_h", "drive_id": "D", "path": "Hidden"}}),
        ("DELETE", "/api/v1/sharepoint/not-imported/hide-folder/fold_h", {}),
    ]

    for method, url, kwargs in admin_calls:
        r_anon = anon.request(method, url, **kwargs)
        assert r_anon.status_code == 401, f"Expected 401 for anon on {method} {url}, got {r_anon.status_code}"

        r_mem = member.request(method, url, **kwargs)
        assert r_mem.status_code == 403, f"Expected 403 for member on {method} {url}, got {r_mem.status_code}"

        r_adm = admin.request(method, url, **kwargs)
        assert r_adm.status_code == 200, f"Expected 200 for admin on {method} {url}, got {r_adm.status_code}: {r_adm.text}"

    # Open routes stay accessible to signed-in members
    for url in [
        "/api/v1/sharepoint/config",
        "/api/v1/sharepoint/status",
        "/api/v1/sharepoint/files",
        "/api/v1/sharepoint/drives",
        "/api/v1/sharepoint/import-status",
        "/api/v1/sharepoint/index/status",
    ]:
        r_open = member.get(url)
        assert r_open.status_code == 200, f"Expected 200 for member on open route {url}, got {r_open.status_code}"


# ---------------------------------------------------------------------------
# 3 & 4. Enforced mode safety & enforced mode matrix
# ---------------------------------------------------------------------------


def test_enforced_mode_questions_and_cost_cron_secret(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_ENFORCED", True)
    monkeypatch.setattr(settings, "COST_CRON_SECRET", "cron-secret-xyz")

    anon = TestClient(app)
    assert anon.get("/api/v1/questions").status_code == 401

    _, _, member_token = _get_user_cookie("member")
    member = TestClient(app, cookies={SESSION_COOKIE_NAME: member_token})
    assert member.get("/api/v1/questions").status_code == 200

    # /costs/refresh works with X-Cron-Secret alone (both /costs and /api/v1/costs)
    for path in ("/costs/refresh?dry_run=true", "/api/v1/costs/refresh?dry_run=true"):
        r_missing = anon.post(path)
        assert r_missing.status_code == 401

        r_wrong = anon.post(path, headers={"X-Cron-Secret": "wrong-secret"})
        assert r_wrong.status_code == 401

        r_ok = anon.post(path, headers={"X-Cron-Secret": "cron-secret-xyz"})
        assert r_ok.status_code == 200
        assert r_ok.json()["status"] == "ok"

    # Other /costs routes remain protected in enforced mode
    assert anon.get("/costs/summary").status_code == 401
    assert anon.get("/api/v1/costs/simple").status_code == 401
    assert member.get("/costs/summary").status_code == 403


def test_enforced_mode_exempt_and_m2m_matrix(monkeypatch):
    """Enforced-mode matrix verifying every exempt public route and M2M key-authenticated route."""
    monkeypatch.setattr(settings, "AUTH_ENFORCED", True)
    monkeypatch.setattr(settings, "API_KEY_SECRET", "m2m-secret-key")

    anon = TestClient(app)

    # Public exact & prefix routes must NOT be blocked with 401 by require_api_key
    exempt_get_routes = [
        "/",
        "/api",
        "/api/v1",
        "/health",
        "/api/health",
        "/api/v1/health",
        "/costs/refresh-status",
        "/api/v1/costs/refresh-status",
        "/docs",
        "/api/v1/docs",
        "/openapi.json",
        "/api/v1/openapi.json",
        "/redoc",
        "/static/nonexistent.png",
        "/api/v1/static/nonexistent.png",
        "/slides/nonexistent.png",
    ]
    for route in exempt_get_routes:
        res = anon.get(route)
        assert res.status_code != 401, f"Exempt route {route} returned 401 in enforced mode"

    # /auth/me is exempt from middleware (reaches router's get_current_user -> "Not authenticated",
    # not middleware's "Authentication required")
    for me_route in ("/auth/me", "/api/v1/auth/me"):
        res_me = anon.get(me_route)
        assert res_me.status_code == 401
        assert res_me.json()["detail"] == "Not authenticated"

    # Protected routes must return 401 without session or X-API-Key
    for protected in ("/api/v1/questions", "/api/v1/quizzes"):
        assert anon.get(protected).status_code == 401

    # Machine-to-machine routes with X-API-Key pass middleware in enforced mode
    assert anon.get("/api/v1/questions", headers={"X-API-Key": "m2m-secret-key"}).status_code == 200
    assert anon.post("/api/v1/jobs/process-upload-next", headers={"X-API-Key": "m2m-secret-key"}).status_code != 401
