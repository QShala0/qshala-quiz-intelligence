import uuid
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.database import SessionLocal, Base, engine
from backend.app.main import app
from backend.app.models.document import Document
from backend.app.models.job import IngestionJob
from backend.app.models.sharepoint import SharePointFile, SharePointAllowedFolder
from backend.app.models.sharepoint_index import SharePointIndexItem, SharePointIndexState
from backend.app.services.sharepoint import index as sp_index

client = TestClient(app)


@pytest.fixture()
def db():
    Base.metadata.create_all(bind=engine)
    s = SessionLocal()
    prefix = f"spindex-{uuid.uuid4().hex[:8]}"
    s._test_prefix = prefix
    s._test_doc_ids = []
    s._test_sp_ids = []
    s._test_job_ids = []
    s._test_index_ids = []
    s._test_drive_ids = []

    try:
        yield s
    finally:
        s.rollback()
        # Delete only rows created by this test
        if s._test_job_ids:
            s.query(IngestionJob).filter(IngestionJob.id.in_(s._test_job_ids)).delete(synchronize_session=False)
        if s._test_sp_ids:
            s.query(SharePointFile).filter(SharePointFile.id.in_(s._test_sp_ids)).delete(synchronize_session=False)
        if s._test_doc_ids:
            s.query(Document).filter(Document.id.in_(s._test_doc_ids)).delete(synchronize_session=False)
        if s._test_index_ids:
            s.query(SharePointIndexItem).filter(SharePointIndexItem.id.in_(s._test_index_ids)).delete(synchronize_session=False)
        if s._test_drive_ids:
            s.query(SharePointIndexState).filter(SharePointIndexState.drive_id.in_(s._test_drive_ids)).delete(synchronize_session=False)
            s.query(SharePointIndexItem).filter(SharePointIndexItem.drive_id.in_(s._test_drive_ids)).delete(synchronize_session=False)

        # Safety cleanup by prefix
        pfx = f"{prefix}%"
        s.query(IngestionJob).filter(IngestionJob.id.like(pfx)).delete(synchronize_session=False)
        s.query(SharePointFile).filter(SharePointFile.id.like(pfx) | SharePointFile.name.like(pfx)).delete(synchronize_session=False)
        s.query(Document).filter(Document.id.like(pfx) | Document.filename.like(pfx)).delete(synchronize_session=False)
        s.query(SharePointIndexItem).filter(SharePointIndexItem.id.like(pfx) | SharePointIndexItem.drive_id.like(pfx)).delete(synchronize_session=False)
        s.query(SharePointIndexState).filter(SharePointIndexState.drive_id.like(pfx)).delete(synchronize_session=False)
        s.commit()
        s.close()


@pytest.mark.anyio
async def test_delta_pages_and_deleted_facet(db):
    pfx = db._test_prefix
    drive_id = f"{pfx}-drive-1"
    db._test_drive_ids.append(drive_id)

    # Pre-existing item that will be deleted
    old_deleted_item = SharePointIndexItem(
        id=f"{pfx}-deleted-item",
        drive_id=drive_id,
        name="old_deleted.pptx",
        ext=".pptx",
        is_folder=False,
    )
    db.add(old_deleted_item)
    db.commit()

    page1 = {
        "value": [
            {
                "id": f"{pfx}-folder-1",
                "name": "Quizzes",
                "folder": {"childCount": 2},
                "parentReference": {"id": "root"},
            },
            {
                "id": f"{pfx}-file-1",
                "name": "Math Quiz.pptx",
                "file": {},
                "size": 1024,
                "eTag": "etag-1",
                "lastModifiedDateTime": "2026-11-20T10:00:00Z",
                "parentReference": {"id": f"{pfx}-folder-1"},
            },
            {
                "id": f"{pfx}-deleted-item",
                "@removed": {"reason": "deleted"},
            },
        ],
        "@odata.nextLink": f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta?token=page2",
    }

    page2 = {
        "value": [
            {
                "id": f"{pfx}-file-2",
                "name": "Science Notes.pdf",
                "file": {},
                "size": 2048,
                "eTag": "etag-2",
                "lastModifiedDateTime": "2026-11-21T10:00:00Z",
                "parentReference": {"id": f"{pfx}-folder-1"},
            },
            {
                "id": f"{pfx}-unsupported",
                "name": "readme.txt",
                "file": {},
                "size": 512,
                "parentReference": {"id": f"{pfx}-folder-1"},
            },
        ],
        "@odata.deltaLink": f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta?token=final_delta",
    }

    mock_client = AsyncMock()
    mock_client.GRAPH_BASE = "https://graph.microsoft.com/v1.0"
    mock_client.drive_id = drive_id

    async def fetch_delta_page(url):
        if "token=page2" in url:
            return page2
        return page1

    mock_client.fetch_delta_page = AsyncMock(side_effect=fetch_delta_page)

    res = await sp_index.index_step(db, mock_client, budget_seconds=20, drive_id=drive_id)

    assert res["status"] == "IDLE"
    assert res["finished"] is True
    assert res["items_indexed"] == 3  # folder-1, file-1, file-2

    # Check items in database
    folder = db.query(SharePointIndexItem).filter(SharePointIndexItem.id == f"{pfx}-folder-1").first()
    assert folder is not None
    assert folder.is_folder is True
    assert folder.name == "Quizzes"

    file1 = db.query(SharePointIndexItem).filter(SharePointIndexItem.id == f"{pfx}-file-1").first()
    assert file1 is not None
    assert file1.ext == ".pptx"
    assert file1.size == 1024

    file2 = db.query(SharePointIndexItem).filter(SharePointIndexItem.id == f"{pfx}-file-2").first()
    assert file2 is not None
    assert file2.ext == ".pdf"

    # Unsupported file was not saved
    unsupported = db.query(SharePointIndexItem).filter(SharePointIndexItem.id == f"{pfx}-unsupported").first()
    assert unsupported is None

    # Deleted item was removed
    deleted = db.query(SharePointIndexItem).filter(SharePointIndexItem.id == f"{pfx}-deleted-item").first()
    assert deleted is None

    # Verify state row
    state = db.query(SharePointIndexState).filter(SharePointIndexState.drive_id == drive_id).first()
    assert state is not None
    assert state.delta_link == f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta?token=final_delta"
    assert state.last_full_at is not None


@pytest.mark.anyio
async def test_folder_paths_and_scope_roots(db):
    pfx = db._test_prefix
    drive_id = f"{pfx}-drive-scope"
    db._test_drive_ids.append(drive_id)

    # Hierarchy:
    # Quizzes (root 1) -> 2024 -> Quiz_2024.pptx
    # QShala School Program (root 2) -> School_Program.pptx
    # Marketing (outside scope) -> Flyer.pptx
    f_quizzes = SharePointIndexItem(id=f"{pfx}-f-q", drive_id=drive_id, name="Quizzes", is_folder=True, parent_id="root")
    f_sub_q = SharePointIndexItem(id=f"{pfx}-f-subq", drive_id=drive_id, name="2024", is_folder=True, parent_id=f"{pfx}-f-q")
    file_q = SharePointIndexItem(id=f"{pfx}-file-q", drive_id=drive_id, name="Quiz_2024.pptx", ext=".pptx", is_folder=False, size=100, parent_id=f"{pfx}-f-subq")

    f_school = SharePointIndexItem(id=f"{pfx}-f-s", drive_id=drive_id, name="QShala School Program", is_folder=True, parent_id="root")
    file_s = SharePointIndexItem(id=f"{pfx}-file-s", drive_id=drive_id, name="School_Program.pdf", ext=".pdf", is_folder=False, size=200, parent_id=f"{pfx}-f-s")

    f_mkt = SharePointIndexItem(id=f"{pfx}-f-mkt", drive_id=drive_id, name="Marketing", is_folder=True, parent_id="root")
    file_mkt = SharePointIndexItem(id=f"{pfx}-file-mkt", drive_id=drive_id, name="Flyer.pptx", ext=".pptx", is_folder=False, size=300, parent_id=f"{pfx}-f-mkt")

    state = SharePointIndexState(drive_id=drive_id, status="IDLE", last_full_at=datetime.now(timezone.utc), items_indexed=5)

    db.add_all([f_quizzes, f_sub_q, file_q, f_school, file_s, f_mkt, file_mkt, state])
    db.commit()

    # Scope configured to Quizzes and QShala School Program
    with patch("backend.app.config.settings.SHAREPOINT_ROOT_FOLDERS", "Quizzes,QShala School Program"), \
         patch("backend.app.services.sharepoint.importer.scope_roots", AsyncMock(return_value=[
             {"id": f"{pfx}-f-q", "name": "Quizzes", "path": "Quizzes"},
             {"id": f"{pfx}-f-s", "name": "QShala School Program", "path": "QShala School Program"},
         ])):
        res = await sp_index.get_not_imported_from_index(db, client=None, drive_id=drive_id)

    assert res["total"] == 2
    assert res["count"] == 2
    file_names = {f["name"] for f in res["files"]}
    assert file_names == {"Quiz_2024.pptx", "School_Program.pdf"}

    q_file = next(f for f in res["files"] if f["name"] == "Quiz_2024.pptx")
    assert q_file["folder_path"] == "Quizzes/2024"
    assert q_file["root"] == "Quizzes"

    s_file = next(f for f in res["files"] if f["name"] == "School_Program.pdf")
    assert s_file["folder_path"] == "QShala School Program"
    assert s_file["root"] == "QShala School Program"


@pytest.mark.anyio
async def test_step_budget_time_stops_and_resumes(db):
    pfx = db._test_prefix
    drive_id = f"{pfx}-drive-budget"
    db._test_drive_ids.append(drive_id)

    mock_client = AsyncMock()
    mock_client.GRAPH_BASE = "https://graph.microsoft.com/v1.0"
    mock_client.drive_id = drive_id

    page1 = {
        "value": [
            {"id": f"{pfx}-b1", "name": "B1.pptx", "file": {}, "parentReference": {"id": "root"}},
        ],
        "@odata.nextLink": f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta?token=step2",
    }
    page2 = {
        "value": [
            {"id": f"{pfx}-b2", "name": "B2.pptx", "file": {}, "parentReference": {"id": "root"}},
        ],
        "@odata.deltaLink": f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta?token=step_final",
    }

    mock_client.fetch_delta_page = AsyncMock(side_effect=[page1, page2])

    # Run step 1 with budget 0: stops immediately after 1 page
    with patch("time.time", side_effect=[100.0, 100.0, 150.0, 150.0]):
        step1 = await sp_index.index_step(db, mock_client, budget_seconds=20, drive_id=drive_id)

    assert step1["finished"] is False
    assert step1["items_indexed"] == 1
    state1 = db.query(SharePointIndexState).filter(SharePointIndexState.drive_id == drive_id).one()
    assert state1.delta_link == f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta?token=step2"

    # Run step 2: resumes from step2 and completes
    step2 = await sp_index.index_step(db, mock_client, budget_seconds=20, drive_id=drive_id)
    assert step2["finished"] is True
    assert step2["items_indexed"] == 2
    state2 = db.query(SharePointIndexState).filter(SharePointIndexState.drive_id == drive_id).one()
    assert state2.delta_link == f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta?token=step_final"
    assert state2.last_full_at is not None


@pytest.mark.anyio
async def test_410_gone_resets_token_and_restarts(db):
    pfx = db._test_prefix
    drive_id = f"{pfx}-drive-410"
    db._test_drive_ids.append(drive_id)

    state = SharePointIndexState(
        drive_id=drive_id,
        delta_link="https://graph.microsoft.com/v1.0/drives/d/root/delta?token=expired",
        status="IDLE",
        last_full_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    db.add(state)
    db.commit()

    mock_client = AsyncMock()
    mock_client.GRAPH_BASE = "https://graph.microsoft.com/v1.0"
    mock_client.drive_id = drive_id

    req = httpx.Request("GET", "https://graph.microsoft.com/v1.0/drives/d/root/delta?token=expired")
    resp_410 = httpx.Response(410, request=req)
    err_410 = httpx.HTTPStatusError("Delta token expired", request=req, response=resp_410)

    success_page = {
        "value": [
            {"id": f"{pfx}-fresh-1", "name": "Fresh.pptx", "file": {}, "parentReference": {"id": "root"}},
        ],
        "@odata.deltaLink": f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta?token=fresh_final",
    }

    mock_client.fetch_delta_page = AsyncMock(side_effect=[err_410, success_page])

    res = await sp_index.index_step(db, mock_client, budget_seconds=20, drive_id=drive_id)

    assert res["finished"] is True
    db.refresh(state)
    assert state.delta_link == f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta?token=fresh_final"


@pytest.mark.anyio
async def test_lease_held_returns_busy(db):
    pfx = db._test_prefix
    drive_id = f"{pfx}-drive-busy"
    db._test_drive_ids.append(drive_id)

    state = SharePointIndexState(
        drive_id=drive_id,
        status="RUNNING",
        lease_until=datetime.now(timezone.utc) + timedelta(minutes=5),
        items_indexed=42,
    )
    db.add(state)
    db.commit()

    mock_client = AsyncMock()
    mock_client.fetch_delta_page = AsyncMock()

    res = await sp_index.index_step(db, mock_client, budget_seconds=20, drive_id=drive_id)

    assert res["status"] == "busy"
    assert res["items_indexed"] == 42
    assert res["finished"] is False
    mock_client.fetch_delta_page.assert_not_called()


@pytest.mark.anyio
async def test_not_imported_duplicate_and_sorting_and_paging(db):
    pfx = db._test_prefix
    drive_id = f"{pfx}-drive-filter"
    db._test_drive_ids.append(drive_id)

    now = datetime.now(timezone.utc)

    # Stored deck in QShala
    doc = Document(
        id=f"{pfx}-doc1",
        filename=f"{pfx}_Alpha_Quiz.pptx",
        title=f"{pfx} Alpha Quiz",
        file_type="pptx",
        storage_path="dummy",
        processing_status="COMPLETED",
    )
    db._test_doc_ids.append(doc.id)
    db.add(doc)

    # 4 index items:
    # 1. Matches doc1 name (duplicate) -> excluded
    # 2. Newer (yesterday)
    # 3. Older (2 days ago)
    # 4. Undated (None) -> undated last
    item_dupe = SharePointIndexItem(
        id=f"{pfx}-i-dupe", drive_id=drive_id, name=f"{pfx}   alpha__quiz.pdf", ext=".pdf",
        is_folder=False, modified=now - timedelta(days=1), parent_id="root",
    )
    item_newer = SharePointIndexItem(
        id=f"{pfx}-i-newer", drive_id=drive_id, name=f"{pfx}_Beta_Deck.pptx", ext=".pptx",
        is_folder=False, modified=now - timedelta(days=1), parent_id="root",
    )
    item_older = SharePointIndexItem(
        id=f"{pfx}-i-older", drive_id=drive_id, name=f"{pfx}_Gamma_Deck.pptx", ext=".pptx",
        is_folder=False, modified=now - timedelta(days=2), parent_id="root",
    )
    item_undated = SharePointIndexItem(
        id=f"{pfx}-i-undated", drive_id=drive_id, name=f"{pfx}_Delta_Deck.pptx", ext=".pptx",
        is_folder=False, modified=None, parent_id="root",
    )

    state = SharePointIndexState(
        drive_id=drive_id,
        status="IDLE",
        last_full_at=now,
        items_indexed=4,
    )

    db.add_all([item_dupe, item_newer, item_older, item_undated, state])
    db.commit()

    with patch("backend.app.config.settings.SHAREPOINT_ROOT_FOLDERS", ""):
        # Full list: dupe excluded -> 3 files
        full = await sp_index.get_not_imported_from_index(db, client=None, drive_id=drive_id, limit=200, offset=0)
        assert full["total"] == 3
        assert full["count"] == 3
        assert [f["name"] for f in full["files"]] == [
            f"{pfx}_Beta_Deck.pptx",
            f"{pfx}_Gamma_Deck.pptx",
            f"{pfx}_Delta_Deck.pptx",
        ]

        # Paging: limit=1, offset=0
        p1 = await sp_index.get_not_imported_from_index(db, client=None, drive_id=drive_id, limit=1, offset=0)
        assert p1["count"] == 1
        assert p1["total"] == 3
        assert p1["files"][0]["name"] == f"{pfx}_Beta_Deck.pptx"

        # Paging: limit=1, offset=1
        p2 = await sp_index.get_not_imported_from_index(db, client=None, drive_id=drive_id, limit=1, offset=1)
        assert p2["count"] == 1
        assert p2["total"] == 3
        assert p2["files"][0]["name"] == f"{pfx}_Gamma_Deck.pptx"

        # Paging: limit=1, offset=2 (undated file)
        p3 = await sp_index.get_not_imported_from_index(db, client=None, drive_id=drive_id, limit=1, offset=2)
        assert p3["count"] == 1
        assert p3["total"] == 3
        assert p3["files"][0]["name"] == f"{pfx}_Delta_Deck.pptx"


@pytest.mark.anyio
async def test_index_never_writes_sharepoint_files_or_ingestion_jobs(db):
    pfx = db._test_prefix
    drive_id = f"{pfx}-drive-pure"
    db._test_drive_ids.append(drive_id)

    mock_client = AsyncMock()
    mock_client.GRAPH_BASE = "https://graph.microsoft.com/v1.0"
    mock_client.drive_id = drive_id

    page = {
        "value": [
            {"id": f"{pfx}-f", "name": "Pure.pptx", "file": {}, "parentReference": {"id": "root"}},
        ],
        "@odata.deltaLink": f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta?token=pure_final",
    }
    mock_client.fetch_delta_page = AsyncMock(return_value=page)

    await sp_index.index_step(db, mock_client, budget_seconds=20, drive_id=drive_id)
    await sp_index.get_not_imported_from_index(db, client=mock_client, drive_id=drive_id)

    # Verify zero records in sharepoint_files or ingestion_jobs
    sp_count = db.query(SharePointFile).filter(SharePointFile.id.like(f"{pfx}%")).count()
    job_count = db.query(IngestionJob).filter(IngestionJob.id.like(f"{pfx}%")).count()

    assert sp_count == 0
    assert job_count == 0
