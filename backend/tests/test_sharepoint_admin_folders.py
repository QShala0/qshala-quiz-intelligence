import pytest
import time
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from backend.app.database import Base, get_db
from backend.app.main import app
from backend.app.config import settings
from backend.app.models.sharepoint import SharePointAllowedFolder
from backend.app.routers.sharepoint import create_admin_token, verify_admin_token
from backend.app.services.sharepoint import importer
from backend.app.services.sharepoint.graph_client import SharePointClient

BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/t/oauth2/v2.0/token"

TREE = {
    "root": {"value": [
        {"id": "fold1", "name": "Quizzes", "folder": {"childCount": 2}, "parentReference": {"path": "/drives/D/root:"}},
        {"id": "fold2", "name": "Admin_Finance", "folder": {"childCount": 1}, "parentReference": {"path": "/drives/D/root:"}},
        {"id": "f_root", "name": "welcome.pptx", "file": {}, "parentReference": {"path": "/drives/D/root:"}},
    ]},
    "fold1": {"value": [
        {"id": "fold1_sub1", "name": "2024 Finals", "folder": {"childCount": 1}, "parentReference": {"path": "/drives/D/root:/Quizzes"}},
        {"id": "f_q1", "name": "Round1.pptx", "size": 100, "file": {"hashes": {"quickXorHash": "h"}}, "parentReference": {"path": "/drives/D/root:/Quizzes"}},
    ]},
    "fold2": {"value": [
        {"id": "f_fin", "name": "budget.pptx", "size": 200, "file": {"hashes": {"quickXorHash": "h"}}, "parentReference": {"path": "/drives/D/root:/Admin_Finance"}},
    ]},
}


def make_test_client(handler):
    return SharePointClient(
        tenant_id="t", client_id="c", client_secret="s",
        site_id="site1", drive_id="D",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def mock_graph_handler(request: httpx.Request):
    url = str(request.url)
    if url == TOKEN_URL:
        return httpx.Response(200, json={"access_token": "mock_tok", "expires_in": 3600})
    if url.startswith(f"{BASE}/drives/D/root/children"):
        return httpx.Response(200, json=TREE["root"])
    if url.startswith(f"{BASE}/drives/D/items/fold1/children"):
        return httpx.Response(200, json=TREE["fold1"])
    if url.startswith(f"{BASE}/drives/D/items/fold2/children"):
        return httpx.Response(200, json=TREE["fold2"])
    if url.startswith(f"{BASE}/drives/D/items/fold1"):
        return httpx.Response(200, json={"id": "fold1", "name": "Quizzes", "folder": {}, "parentReference": {"path": "/drives/D/root:"}})
    if url.startswith(f"{BASE}/drives/D/items/fold2"):
        return httpx.Response(200, json={"id": "fold2", "name": "Admin_Finance", "folder": {}, "parentReference": {"path": "/drives/D/root:"}})
    if url.startswith(f"{BASE}/drives/D/items/f_q1"):
        return httpx.Response(200, json=TREE["fold1"]["value"][1])
    if url.startswith(f"{BASE}/drives/D/items/f_fin"):
        return httpx.Response(200, json=TREE["fold2"]["value"][0])
    if "search(q=" in url:
        # Mock search response
        if "Round1" in url or "pptx" in url:
            return httpx.Response(200, json={"value": [
                {
                    "id": "f_q1",
                    "name": "Round1.pptx",
                    "size": 1024,
                    "lastModifiedDateTime": "2024-01-01T00:00:00Z",
                    "file": {"hashes": {"quickXorHash": "h"}},
                    "parentReference": {"id": "fold1", "name": "Quizzes", "path": "/drives/D/root:/Quizzes"},
                },
                {
                    "id": "fold1",
                    "name": "Quizzes",
                    "folder": {"childCount": 2},
                    "parentReference": {"path": "/drives/D/root:"},
                }
            ]})
        return httpx.Response(200, json={"value": []})
    return httpx.Response(404, json={"error": {"message": "Not found"}})


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def api_client(db_session, monkeypatch):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    sp_client = make_test_client(mock_graph_handler)
    monkeypatch.setattr("backend.app.routers.sharepoint.get_sharepoint_client", lambda: sp_client)

    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


def test_admin_password_verification(api_client, monkeypatch):
    monkeypatch.setattr(settings, "SHAREPOINT_ADMIN_PASSWORD", "secret123")

    # Wrong password
    res = api_client.post("/api/v1/sharepoint/admin/verify", json={"password": "wrong"})
    assert res.status_code == 401
    assert "Incorrect admin password" in res.json()["detail"]

    # Correct password
    res = api_client.post("/api/v1/sharepoint/admin/verify", json={"password": "secret123"})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "token" in data
    assert verify_admin_token(data["token"]) is True


def test_admin_token_expiration_and_tampering(monkeypatch):
    monkeypatch.setattr(settings, "SHAREPOINT_ADMIN_PASSWORD", "testpass")
    token = create_admin_token()
    assert verify_admin_token(token) is True

    # Tampered signature
    parts = token.split(":")
    tampered = f"{parts[0]}:deadbeef"
    assert verify_admin_token(tampered) is False

    # Expired token
    expired = f"{int(time.time()) - 100}:{parts[1]}"
    assert verify_admin_token(expired) is False


def test_admin_endpoints_require_token(api_client):
    res = api_client.get("/api/v1/sharepoint/admin/browse?drive_id=D")
    assert res.status_code == 401

    res = api_client.get("/api/v1/sharepoint/admin/allowed-folders?drive_id=D")
    assert res.status_code == 401

    res = api_client.post("/api/v1/sharepoint/admin/allowed-folders", json={"drive_id": "D", "folder_id": "fold1"})
    assert res.status_code == 401


def test_admin_browse_and_manage_folders(api_client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "SHAREPOINT_ADMIN_PASSWORD", "pass123")
    auth_res = api_client.post("/api/v1/sharepoint/admin/verify", json={"password": "pass123"}).json()
    token = auth_res["token"]
    headers = {"x-admin-token": token}

    # 1. Admin browse at root: sees all folders (Quizzes, Admin_Finance)
    res = api_client.get("/api/v1/sharepoint/admin/browse?drive_id=D", headers=headers)
    assert res.status_code == 200
    data = res.json()
    folder_names = [f["name"] for f in data["folders"]]
    assert "Quizzes" in folder_names
    assert "Admin_Finance" in folder_names
    # Initially neither is allowed
    for f in data["folders"]:
        assert f["is_allowed"] is False

    # 2. Add Quizzes to allowed folders
    add_res = api_client.post(
        "/api/v1/sharepoint/admin/allowed-folders",
        headers=headers,
        json={
            "drive_id": "D",
            "folder_id": "fold1",
            "folder_name": "Quizzes",
            "folder_path": "/drives/D/root:/Quizzes",
            "display_name": "Team Quizzes",
            "is_active": True,
        }
    )
    assert add_res.status_code == 200
    assert add_res.json()["folder"]["folder_name"] == "Quizzes"

    # 3. List allowed folders
    list_res = api_client.get("/api/v1/sharepoint/admin/allowed-folders?drive_id=D", headers=headers)
    assert list_res.status_code == 200
    folders = list_res.json()["folders"]
    assert len(folders) == 1
    assert folders[0]["folder_id"] == "fold1"
    assert folders[0]["display_name"] == "Team Quizzes"

    # 4. Admin browse now shows Quizzes marked as is_allowed: True
    res2 = api_client.get("/api/v1/sharepoint/admin/browse?drive_id=D", headers=headers)
    quizzes_f = next(f for f in res2.json()["folders"] if f["id"] == "fold1")
    assert quizzes_f["is_allowed"] is True
    admin_f = next(f for f in res2.json()["folders"] if f["id"] == "fold2")
    assert admin_f["is_allowed"] is False

    # 5. Delete from allowed folders
    del_res = api_client.delete(f"/api/v1/sharepoint/admin/allowed-folders/fold1?drive_id=D", headers=headers)
    assert del_res.status_code == 200

    list_res2 = api_client.get("/api/v1/sharepoint/admin/allowed-folders?drive_id=D", headers=headers)
    assert len(list_res2.json()["folders"]) == 0


@pytest.mark.anyio
async def test_quizmaster_scope_enforcement_with_db(db_session, monkeypatch):
    """When a folder is allowed in DB, quizmaster browse and import are strictly confined to it."""
    # Add Quizzes to DB allowed folders
    db_folder = SharePointAllowedFolder(
        drive_id="D",
        folder_id="fold1",
        folder_name="Quizzes",
        folder_path="/drives/D/root:/Quizzes",
        display_name="Quizzes",
        is_active=True,
    )
    db_session.add(db_folder)
    db_session.commit()

    sp_client = make_test_client(mock_graph_handler)

    # 1. scope_roots returns the DB folder
    roots = await importer.scope_roots(sp_client, "D", db=db_session)
    assert len(roots) == 1
    assert roots[0]["id"] == "fold1"

    # 2. effective_folder resolves root to fold1
    eff = await importer.effective_folder(sp_client, "D", None, db=db_session)
    assert eff == "fold1"

    # 3. Trying to access Admin_Finance folder (fold2) throws ValueError
    with pytest.raises(ValueError, match="outside"):
        await importer.effective_folder(sp_client, "D", "fold2", db=db_session)

    # 4. Plan inside allowed folder succeeds
    plan = await importer.plan_import(
        db_session,
        sp_client,
        importer.Selection(
            drive_id="D",
            items=[importer.ItemRef(id="f_q1", kind="file")],
        ),
    )
    assert plan["total_found"] == 1
    assert plan["files"][0]["name"] == "Round1.pptx"

    # 5. Plan for a file outside the allowed folder (f_fin in fold2) raises ValueError
    with pytest.raises(ValueError, match="outside"):
        await importer.plan_import(
            db_session,
            sp_client,
            importer.Selection(
                drive_id="D",
                items=[importer.ItemRef(id="f_fin", kind="file")],
            ),
        )


def test_admin_search_files_and_folders(api_client, monkeypatch):
    """Admin keyword search queries SharePoint and returns matching files and folders with allowed status."""
    monkeypatch.setattr(settings, "SHAREPOINT_ADMIN_PASSWORD", "secret123")
    token = create_admin_token()
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Search without auth fails 401
    unauth = api_client.get("/api/v1/sharepoint/admin/search?drive_id=D&q=Round1")
    assert unauth.status_code == 401

    # 2. Search with auth returns files and folders
    res = api_client.get("/api/v1/sharepoint/admin/search?drive_id=D&q=Round1", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["query"] == "Round1"
    assert len(data["files"]) == 1
    assert data["files"][0]["name"] == "Round1.pptx"
    assert data["files"][0]["parent_folder_id"] == "fold1"
    assert data["files"][0]["parent_is_allowed"] is False
    assert len(data["folders"]) == 1
    assert data["folders"][0]["name"] == "Quizzes"

    # 3. Save fold1 as allowed, then search again: parent_is_allowed is now True
    api_client.post(
        "/api/v1/sharepoint/admin/allowed-folders",
        json={
            "drive_id": "D",
            "folder_id": "fold1",
            "folder_name": "Quizzes",
            "folder_path": "/drives/D/root:/Quizzes",
            "is_active": True,
        },
        headers=headers,
    )
    res2 = api_client.get("/api/v1/sharepoint/admin/search?drive_id=D&q=Round1", headers=headers)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["files"][0]["parent_is_allowed"] is True
