from datetime import datetime, timezone, timedelta
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from backend.app.database import SessionLocal, Base, engine
from backend.app.main import app
from backend.app.models.document import Document
from backend.app.models.job import IngestionJob
from backend.app.models.sharepoint import SharePointFile
from backend.app.models.sharepoint_index import SharePointIndexItem, SharePointIndexState
from backend.app.models.upload_batch import UploadBatch
from backend.app.services.deck_names import (
    normalize_deck_name,
    existing_deck_names,
    find_deck_in_index,
    find_deck_by_name,
)
from backend.app.services.sharepoint import importer

client = TestClient(app)


@pytest.fixture()
def db():
    Base.metadata.create_all(bind=engine)
    s = SessionLocal()
    prefix = f"dupguard-{uuid.uuid4().hex[:8]}"
    s._test_prefix = prefix
    s._test_doc_ids = []
    s._test_sp_ids = []
    s._test_batch_ids = []
    s._test_job_ids = []

    try:
        yield s
    finally:
        s.rollback()
        # Delete only rows created by this test
        if s._test_job_ids:
            s.query(IngestionJob).filter(IngestionJob.id.in_(s._test_job_ids)).delete(synchronize_session=False)
        if s._test_batch_ids:
            s.query(UploadBatch).filter(UploadBatch.id.in_(s._test_batch_ids)).delete(synchronize_session=False)
        if s._test_sp_ids:
            s.query(SharePointFile).filter(SharePointFile.id.in_(s._test_sp_ids)).delete(synchronize_session=False)
        if s._test_doc_ids:
            s.query(Document).filter(Document.id.in_(s._test_doc_ids)).delete(synchronize_session=False)

        # Safety cleanup for any rows created with this test's unique prefix
        pfx = f"{prefix}%"
        s.query(SharePointIndexItem).filter(SharePointIndexItem.id.like(pfx) | SharePointIndexItem.drive_id.like(pfx)).delete(synchronize_session=False)
        s.query(SharePointIndexState).filter(SharePointIndexState.drive_id.like(pfx)).delete(synchronize_session=False)
        s.query(IngestionJob).filter(IngestionJob.id.like(pfx)).delete(synchronize_session=False)
        s.query(UploadBatch).filter(UploadBatch.id.like(pfx)).delete(synchronize_session=False)
        s.query(SharePointFile).filter(SharePointFile.id.like(pfx) | SharePointFile.name.like(pfx)).delete(synchronize_session=False)
        s.query(Document).filter(Document.id.like(pfx) | Document.filename.like(pfx)).delete(synchronize_session=False)
        s.commit()
        s.close()


def test_normalize_deck_name():
    assert normalize_deck_name("Quiz_1_Round.pptx") == "quiz 1 round"
    assert normalize_deck_name("  Quiz   1__Round.PPT  ") == "quiz 1 round"
    assert normalize_deck_name("MY DECK.PDF") == "my deck"
    assert normalize_deck_name("multiple___underscores   and   spaces.pdf") == "multiple underscores and spaces"
    assert normalize_deck_name("some_deck_without_extension") == "some deck without extension"
    assert normalize_deck_name("") == ""
    assert normalize_deck_name(None) == ""


def test_manual_batch_registration_duplicate_handling(db):
    pfx = db._test_prefix
    doc1 = Document(
        id=f"{pfx}-doc1",
        filename=f"{pfx}_Existing_Deck.pptx",
        title=f"{pfx} Existing Deck",
        file_type="pptx",
        storage_path="dummy",
        processing_status="COMPLETED",
    )
    doc_failed = Document(
        id=f"{pfx}-doc-failed",
        filename=f"{pfx}_Failed_Deck.pptx",
        title=f"{pfx} Failed Deck",
        file_type="pptx",
        storage_path="dummy",
        processing_status="FAILED",
    )
    db._test_doc_ids.extend([doc1.id, doc_failed.id])
    db.add_all([doc1, doc_failed])
    db.commit()

    payload = {
        "files": [
            {"client_item_id": "c1", "filename": f"{pfx}_existing_deck.ppt", "size_bytes": 100},
            {"client_item_id": "c2", "filename": f"{pfx}_Failed_Deck.pptx", "size_bytes": 200},
            {"client_item_id": "c3", "filename": f"{pfx}_Fresh_Deck.pptx", "size_bytes": 300},
            {"client_item_id": "c4", "filename": f"{pfx}_fresh   deck.pdf", "size_bytes": 400},
        ]
    }
    r = client.post("/api/v1/jobs/upload-batch", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()

    assert "batch_id" in body
    db._test_batch_ids.append(body["batch_id"])
    rej = body.get("rejected_duplicates", [])
    assert len(rej) == 2

    # First rejected is existing deck in DB
    rej1 = next(x for x in rej if x["filename"] == f"{pfx}_existing_deck.ppt")
    assert rej1["existing_document_id"] == doc1.id
    assert rej1["existing_title"] == f"{pfx} Existing Deck"

    # Second rejected is intra-batch duplicate of Fresh_Deck.pptx
    rej2 = next(x for x in rej if x["filename"] == f"{pfx}_fresh   deck.pdf")
    assert rej2["existing_document_id"] is None


def test_single_and_chunk_upload_rejects_duplicate_with_409(db):
    pfx = db._test_prefix
    doc = Document(
        id=f"{pfx}-alpha",
        filename=f"{pfx}_Alpha_Quiz.pptx",
        title=f"{pfx} Alpha Quiz",
        file_type="pptx",
        storage_path="dummy",
        processing_status="COMPLETED",
    )
    db._test_doc_ids.append(doc.id)
    db.add(doc)
    db.commit()

    # Single-file endpoint
    r = client.post(
        "/api/v1/documents/upload",
        files={"file": (f"{pfx} alpha quiz.pdf", b"dummy content", "application/pdf")},
    )
    assert r.status_code == 409
    body = r.json()
    assert body["detail"]["code"] == "duplicate_name"
    assert "already in QShala" in body["detail"]["detail"]

    # Chunk upload endpoint
    r_chunk = client.post(
        "/api/v1/documents/upload-chunk",
        data={
            "upload_id": f"{pfx}_upload_12345",
            "chunk_index": 0,
            "total_chunks": 1,
            "filename": f"{pfx}_alpha_quiz.ppt",
        },
        files={"chunk": ("chunk", b"dummy bytes", "application/octet-stream")},
    )
    assert r_chunk.status_code == 409
    body_chunk = r_chunk.json()
    assert body_chunk["detail"]["code"] == "duplicate_name"
    assert "already in QShala" in body_chunk["detail"]["detail"]


def test_classify_marks_same_name_already_imported_regardless_of_size_and_changed(db):
    pfx = db._test_prefix
    # doc-sp1 created by sp1 earlier
    doc_sp1 = Document(
        id=f"{pfx}-doc-sp1",
        filename=f"{pfx}_Tracked_Deck.pptx",
        title=f"{pfx} Tracked Deck",
        file_type="pptx",
        storage_path="dummy",
        sharepoint_file_id=f"{pfx}-sp1",
        processing_status="COMPLETED",
    )
    # doc-manual created with different name/size
    doc_manual = Document(
        id=f"{pfx}-doc-manual",
        filename=f"{pfx}_Manual_Upload.pptx",
        title=f"{pfx} Manual Upload",
        file_type="pptx",
        storage_path="dummy",
        file_size_bytes=99999,
        processing_status="COMPLETED",
    )
    sp_file = SharePointFile(
        id=f"{pfx}-sp1",
        name=f"{pfx}_Tracked_Deck.pptx",
        etag="old_etag",
        sync_status="COMPLETED",
    )
    db._test_doc_ids.extend([doc_sp1.id, doc_manual.id])
    db._test_sp_ids.append(sp_file.id)
    db.add_all([doc_sp1, doc_manual, sp_file])
    db.commit()

    candidates = [
        # Same normalized name as doc_manual, but different size (100 vs 99999)
        {"id": f"{pfx}-sp-new-1", "name": f"{pfx} manual upload.pdf", "size": 100, "etag": "e1"},
        # Same sharepoint id as sp1, but etag changed
        {"id": f"{pfx}-sp1", "name": f"{pfx}_Tracked_Deck.pptx", "size": 500, "etag": "new_etag"},
        # Completely new file
        {"id": f"{pfx}-sp-fresh", "name": f"{pfx}_Fresh_Deck.pptx", "size": 300, "etag": "e3"},
    ]

    importer.classify(db, candidates)
    res = {c["id"]: c for c in candidates}

    assert res[f"{pfx}-sp-new-1"]["status"] == "ALREADY_IMPORTED"
    assert res[f"{pfx}-sp-new-1"]["match_document_id"] == doc_manual.id

    assert res[f"{pfx}-sp1"]["status"] == "CHANGED"

    assert res[f"{pfx}-sp-fresh"]["status"] == "NEW"


@pytest.mark.anyio
async def test_transfer_next_skips_by_name_without_downloading(db):
    pfx = db._test_prefix
    doc = Document(
        id=f"{pfx}-doc-trivia",
        filename=f"{pfx}_Trivia_Championship.pptx",
        title=f"{pfx} Trivia Championship",
        file_type="pptx",
        storage_path="dummy",
        processing_status="COMPLETED",
    )
    sp_file = SharePointFile(
        id=f"{pfx}-sp-cand-1",
        name=f"{pfx} trivia   championship.pdf",
        drive_id=f"{pfx}-drive-1",
        sync_status="SELECTED",
    )
    db._test_doc_ids.append(doc.id)
    db._test_sp_ids.append(sp_file.id)
    db.add_all([doc, sp_file])
    db.commit()

    mock_client = AsyncMock()
    mock_client.stream_item_to_r2 = AsyncMock()
    mock_client.download_item_capped = AsyncMock()

    res = await importer.transfer_next(db, mock_client, limit=1)

    assert res["skipped_duplicate"] == 1
    assert res["queued"] == 0

    # Ensure download functions were never called
    mock_client.stream_item_to_r2.assert_not_called()
    mock_client.download_item_capped.assert_not_called()

    # Verify SharePointFile was updated
    db.refresh(sp_file)
    assert sp_file.sync_status == "SKIPPED_DUPLICATE"
    assert sp_file.match_document_id == doc.id
    assert sp_file.error_message == f"A deck named {pfx} trivia   championship.pdf is already in QShala"


@pytest.mark.anyio
async def test_sharepoint_not_imported_endpoint(db):
    pfx = db._test_prefix
    # Document exists for deck1
    doc = Document(
        id=f"{pfx}-doc-deck1",
        filename=f"{pfx}_Deck_One.pptx",
        title=f"{pfx} Deck One",
        file_type="pptx",
        storage_path="dummy",
        processing_status="COMPLETED",
    )
    db._test_doc_ids.append(doc.id)
    db.add(doc)
    db.commit()

    drive_id = f"{pfx}-drive-test"

    folder = SharePointIndexItem(
        id=f"{pfx}-folder-quizzes",
        drive_id=drive_id,
        parent_id="root",
        name="Quizzes",
        is_folder=True,
        ext=None,
        size=0,
        modified=datetime.now(timezone.utc),
    )
    f1 = SharePointIndexItem(
        id=f"{pfx}-file-1",
        drive_id=drive_id,
        parent_id=folder.id,
        name=f"{pfx} deck one.pdf",
        is_folder=False,
        ext=".pdf",
        size=12345,
        modified=datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc),
    )
    f2 = SharePointIndexItem(
        id=f"{pfx}-file-2",
        drive_id=drive_id,
        parent_id=folder.id,
        name=f"{pfx}_Deck_Two.pptx",
        is_folder=False,
        ext=".pptx",
        size=67890,
        modified=datetime(2026, 11, 21, 10, 0, 0, tzinfo=timezone.utc),
    )
    db.add_all([folder, f1, f2])
    db.commit()

    r = client.get(f"/api/v1/sharepoint/not-imported?drive_id={drive_id}")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["count"] == 1
    assert len(body["files"]) == 1
    file_entry = body["files"][0]
    assert file_entry["name"] == f"{pfx}_Deck_Two.pptx"
    assert file_entry["size"] == 67890


def test_existing_deck_names_ignores_failed_returns_newest_single_query(db):
    pfx = db._test_prefix
    now = datetime.now(timezone.utc)

    # 1. doc_failed (FAILED) and doc_cancelled (CANCELLED) should be ignored
    doc_failed = Document(
        id=f"{pfx}-fail",
        filename=f"{pfx}_Bad_Deck.pptx",
        title=f"{pfx} Bad Deck",
        file_type="pptx",
        storage_path="dummy",
        processing_status="FAILED",
        uploaded_at=now - timedelta(hours=1),
    )
    doc_cancelled = Document(
        id=f"{pfx}-cancel",
        filename=f"{pfx}_Cancelled_Deck.pptx",
        title=f"{pfx} Cancelled Deck",
        file_type="pptx",
        storage_path="dummy",
        processing_status="CANCELLED",
        uploaded_at=now - timedelta(hours=1),
    )

    # 2. Duplicate names: older vs newer
    doc_older = Document(
        id=f"{pfx}-older",
        filename=f"{pfx}_Dupe_Name.pptx",
        title=f"{pfx} Older Version",
        file_type="pptx",
        storage_path="dummy",
        processing_status="COMPLETED",
        uploaded_at=now - timedelta(days=2),
    )
    doc_newer = Document(
        id=f"{pfx}-newer",
        filename=f"{pfx}   dupe_name.pdf",
        title=f"{pfx} Newer Version",
        file_type="pdf",
        storage_path="dummy",
        processing_status="COMPLETED",
        uploaded_at=now - timedelta(days=1),
    )

    # 3. Add 25 active documents for query count test
    docs_25 = []
    for i in range(25):
        docs_25.append(
            Document(
                id=f"{pfx}-bulk-{i}",
                filename=f"{pfx}_Bulk_File_{i}.pptx",
                title=f"{pfx} Bulk File {i}",
                file_type="pptx",
                storage_path="dummy",
                processing_status="COMPLETED",
                uploaded_at=now,
            )
        )

    all_docs = [doc_failed, doc_cancelled, doc_older, doc_newer] + docs_25
    db._test_doc_ids.extend([d.id for d in all_docs])
    db.add_all(all_docs)
    db.commit()

    # Query count listener on engine
    queries = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        # Filter out schema or transaction bookkeeping if any; record statements
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        index = existing_deck_names(db)
        # Look up 25 names in index
        for i in range(25):
            res = find_deck_in_index(index, f"{pfx}_Bulk_File_{i}.pptx")
            assert res is not None
            assert res.id == f"{pfx}-bulk-{i}"
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)

    # Verify exactly 1 database query was run for the entire index build and 25 lookups
    assert len(queries) == 1

    # Verify FAILED and CANCELLED are not in index
    assert find_deck_in_index(index, f"{pfx}_Bad_Deck.pptx") is None
    assert find_deck_in_index(index, f"{pfx}_Cancelled_Deck.pptx") is None

    # Verify duplicate names return the newest document
    dupe_res = find_deck_in_index(index, f"{pfx}_Dupe_Name.pptx")
    assert dupe_res is not None
    assert dupe_res.id == doc_newer.id
    assert dupe_res.title == f"{pfx} Newer Version"


def test_two_same_named_files_in_one_batch_caught_through_in_memory_index(db):
    pfx = db._test_prefix
    payload = {
        "files": [
            {"client_item_id": "c1", "filename": f"{pfx}_Unique_Batch_File.pptx", "size_bytes": 100},
            {"client_item_id": "c2", "filename": f"{pfx}   unique__batch_file.pdf", "size_bytes": 200},
        ]
    }
    r = client.post("/api/v1/jobs/upload-batch", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    if "batch_id" in body:
        db._test_batch_ids.append(body["batch_id"])

    rej = body.get("rejected_duplicates", [])
    assert len(rej) == 1
    assert rej[0]["filename"] == f"{pfx}   unique__batch_file.pdf"
    assert rej[0]["existing_document_id"] is None
    assert rej[0]["existing_title"] == f"{pfx}_Unique_Batch_File.pptx"


@pytest.mark.anyio
async def test_sharepoint_not_imported_lists_newest_first(db):
    pfx = db._test_prefix
    drive_id = f"{pfx}-drive-order"

    folder = SharePointIndexItem(
        id=f"{pfx}-folder-quizzes",
        drive_id=drive_id,
        parent_id="root",
        name="Quizzes",
        is_folder=True,
        ext=None,
        size=0,
        modified=datetime.now(timezone.utc),
    )
    db.add(folder)

    def item(n, name, when_dt):
        return SharePointIndexItem(
            id=f"{pfx}-file-{n}",
            drive_id=drive_id,
            parent_id=folder.id,
            name=f"{pfx}_{name}.pptx",
            is_folder=False,
            ext=".pptx",
            size=50000,
            modified=when_dt,
        )

    db.add_all([
        item(1, "Old", datetime(2019, 3, 1, 10, 0, 0, tzinfo=timezone.utc)),
        item(2, "Newest", datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)),
        item(3, "Undated", None),
        item(4, "Middle", datetime(2023, 6, 1, 10, 0, 0, tzinfo=timezone.utc)),
    ])
    db.commit()

    r = client.get(f"/api/v1/sharepoint/not-imported?drive_id={drive_id}")
    assert r.status_code == 200, r.text
    assert [f["name"].split("_", 1)[1] for f in r.json()["files"]] == ["Newest.pptx", "Middle.pptx", "Old.pptx", "Undated.pptx"]
