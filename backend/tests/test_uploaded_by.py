"""Tests for Document.uploaded_by_user_id and uploaded_by_name (PR: feat/uploaded-by):
1. Local upload (/documents/upload) sets uploaded_by_* from session, NULL when anonymous, and GET /documents returns it.
2. Chunked local upload (/documents/upload-chunk) sets uploaded_by_* from session, NULL when anonymous.
3. SharePoint import (/sharepoint/import -> /sharepoint/transfer-next): stamps importer at /import onto SharePointFile
   and copies it onto Document in _hand_off_to_pipeline even if another user/browser polls /transfer-next.
4. JobQueueService.step_extract_slides leaves uploaded_by_* NULL for automatic syncs and copies from step_data if present.
"""
import io
import os
import uuid
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
from backend.app.models.job import IngestionJob
from backend.app.models.sharepoint import SharePointAllowedFolder, SharePointFile
from backend.app.services.auth.seeds import seed_auth
from backend.app.services.auth.sessions import SESSION_COOKIE_NAME, create_session
from backend.app.services.jobs.job_queue import JobQueueService


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


def test_local_upload_sets_uploaded_by_when_signed_in_and_null_when_anonymous():
    with patch("backend.app.routers.documents._schedule_ingestion"):
        anon = TestClient(app)
        f1 = f"anon_{uuid.uuid4().hex[:8]}.pptx"
        r_anon = anon.post(
            "/api/v1/documents/upload",
            files={"file": (f1, io.BytesIO(b"PK\x03\x04dummy"), "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
        )
        assert r_anon.status_code == 200, r_anon.text
        anon_id = r_anon.json()["id"]
        assert r_anon.json()["uploaded_by_user_id"] is None
        assert r_anon.json()["uploaded_by_name"] is None

        uid, uname, token = _get_user_cookie("member")
        auth = TestClient(
            app,
            cookies={SESSION_COOKIE_NAME: token},
            headers={"Origin": "http://localhost:3000"},
        )
        f2 = f"auth_{uuid.uuid4().hex[:8]}.pptx"
        r_auth = auth.post(
            "/api/v1/documents/upload",
            files={"file": (f2, io.BytesIO(b"PK\x03\x04dummy"), "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
        )
        assert r_auth.status_code == 200, r_auth.text
        auth_id = r_auth.json()["id"]
        assert r_auth.json()["uploaded_by_user_id"] == uid
        assert r_auth.json()["uploaded_by_name"] == uname

        # List API returns uploaded_by_user_id and uploaded_by_name
        list_res = auth.get("/api/v1/documents")
        assert list_res.status_code == 200
        by_id = {d["id"]: d for d in list_res.json()}
        assert by_id[anon_id]["uploaded_by_name"] is None
        assert by_id[auth_id]["uploaded_by_user_id"] == uid
        assert by_id[auth_id]["uploaded_by_name"] == uname


def test_chunked_upload_sets_uploaded_by_when_signed_in_and_null_when_anonymous(monkeypatch):
    monkeypatch.setattr(settings, "INGESTION_MODE", "in_process")
    with patch("backend.app.routers.documents._schedule_ingestion"):
        anon = TestClient(app)
        up1 = uuid.uuid4().hex
        f1 = f"chunk_anon_{uuid.uuid4().hex[:8]}.pptx"
        r1 = anon.post(
            "/api/v1/documents/upload-chunk",
            data={"upload_id": up1, "chunk_index": "0", "total_chunks": "1", "filename": f1},
            files={"chunk": (f1, io.BytesIO(b"PK\x03\x04chunk"), "application/octet-stream")},
        )
        assert r1.status_code == 200, r1.text
        doc1_id = r1.json()["document"]["id"]

        uid, uname, token = _get_user_cookie("member")
        auth = TestClient(
            app,
            cookies={SESSION_COOKIE_NAME: token},
            headers={"Origin": "http://localhost:3000"},
        )
        up2 = uuid.uuid4().hex
        f2 = f"chunk_auth_{uuid.uuid4().hex[:8]}.pptx"
        r2 = auth.post(
            "/api/v1/documents/upload-chunk",
            data={"upload_id": up2, "chunk_index": "0", "total_chunks": "1", "filename": f2},
            files={"chunk": (f2, io.BytesIO(b"PK\x03\x04chunk"), "application/octet-stream")},
        )
        assert r2.status_code == 200, r2.text
        doc2_id = r2.json()["document"]["id"]

    db = SessionLocal()
    try:
        d1 = db.query(Document).filter(Document.id == doc1_id).first()
        d2 = db.query(Document).filter(Document.id == doc2_id).first()
        assert d1 is not None and d1.uploaded_by_user_id is None and d1.uploaded_by_name is None
        assert d2 is not None and d2.uploaded_by_user_id == uid and d2.uploaded_by_name == uname
    finally:
        db.close()


def test_sharepoint_import_stamps_importer_from_stage_import_through_transfer_next(monkeypatch):
    item_id = f"sp_{uuid.uuid4().hex[:8]}"
    drive_id = f"drv_{uuid.uuid4().hex[:8]}"
    fname = f"SP_Import_{uuid.uuid4().hex[:8]}.pptx"

    db = SessionLocal()
    try:
        db.add(
            SharePointAllowedFolder(
                drive_id=drive_id,
                folder_id="fold_sp",
                folder_name="Quizzes",
                folder_path="Quizzes",
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    mock_sp = MagicMock()
    mock_sp.get_item = AsyncMock(
        return_value={
            "id": item_id,
            "name": fname,
            "size": 1234,
            "file": {"hashes": {"quickXorHash": "qh1"}},
            "parentReference": {"id": "fold_sp", "path": f"/drives/{drive_id}/root:/Quizzes"},
            "webUrl": "https://sp.example.com/file",
            "eTag": "e1",
            "cTag": "c1",
            "lastModifiedDateTime": "2026-01-01T00:00:00Z",
        }
    )
    mock_sp.download_item_capped = AsyncMock(return_value=b"PK\x03\x04sharepoint-deck-bytes")

    monkeypatch.setattr("backend.app.routers.sharepoint.get_sharepoint_client", lambda: mock_sp)
    monkeypatch.setattr(settings, "INGESTION_MODE", "in_process")
    monkeypatch.setattr("backend.app.services.sharepoint.importer.r2_storage.get_media_storage", lambda: None)

    member_id, member_name, member_token = _get_user_cookie("member")
    _, _, admin_token = _get_user_cookie("admin")

    member_client = TestClient(
        app,
        cookies={SESSION_COOKIE_NAME: member_token},
        headers={"Origin": "http://localhost:3000"},
    )
    admin_client = TestClient(
        app,
        cookies={SESSION_COOKIE_NAME: admin_token},
        headers={"Origin": "http://localhost:3000"},
    )

    # Member clicks /import -> stamps member on SharePointFile
    imp_res = member_client.post(
        "/api/v1/sharepoint/import",
        json={"drive_id": drive_id, "items": [{"id": item_id, "kind": "file"}]},
    )
    assert imp_res.status_code == 200, imp_res.text
    batch_id = imp_res.json()["batch_id"]

    db = SessionLocal()
    try:
        sp_row = db.query(SharePointFile).filter(SharePointFile.id == item_id).first()
        assert sp_row is not None
        assert sp_row.importer_user_id == member_id
        assert sp_row.importer_name == member_name
    finally:
        db.close()

    # Different user (admin) polls /transfer-next -> Document still gets member (the /import caller)
    with patch("backend.app.services.ingestion.upload_queue.dispatch_available"), patch(
        "backend.app.services.ingestion.upload_queue.enqueue_upload"
    ) as mock_enq:
        mock_job = MagicMock()
        mock_job.id = "job-1"
        mock_enq.return_value = mock_job
        tr_res = admin_client.post(f"/api/v1/sharepoint/transfer-next?batch_id={batch_id}&limit=5")
        assert tr_res.status_code == 200, tr_res.text
        doc_ids = tr_res.json()["document_ids"]
        assert len(doc_ids) == 1
        doc_id = doc_ids[0]

    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        assert doc is not None
        assert doc.uploaded_by_user_id == member_id
        assert doc.uploaded_by_name == member_name
    finally:
        db.close()


@pytest.mark.anyio
async def test_job_queue_automatic_sync_stays_null_and_requeue_copies_step_data(tmp_path):
    pptx_file = tmp_path / "auto.pptx"
    pptx_file.write_bytes(b"dummy")

    db = SessionLocal()
    try:
        auto_job = IngestionJob(
            source_type="SHAREPOINT",
            filename="auto.pptx",
            blob_url="https://example.com/auto.pptx",
            status="PROCESSING",
            current_step="EXTRACT_SLIDES",
            step_data={"local_path": str(pptx_file)},
        )
        db.add(auto_job)
        db.commit()
        db.refresh(auto_job)

        svc = JobQueueService(blob_service=MagicMock())
        with patch("backend.app.services.jobs.job_queue.PPTXParser") as mock_parser:
            mock_parser.return_value.parse.return_value = []
            await svc.step_extract_slides(auto_job, db)

        doc = db.query(Document).filter(Document.id == auto_job.document_id).first()
        assert doc is not None
        assert doc.uploaded_by_user_id is None
        assert doc.uploaded_by_name is None
    finally:
        db.close()
