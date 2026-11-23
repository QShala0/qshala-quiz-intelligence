import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.app.database import SessionLocal, Base, engine
from backend.app.main import app
from backend.app.models.document import Document
from backend.app.models.sharepoint import SharePointFile
from backend.app.services.deck_names import normalize_deck_name, find_deck_by_name
from backend.app.services.sharepoint import importer

client = TestClient(app)


@pytest.fixture()
def db():
    Base.metadata.create_all(bind=engine)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.query(SharePointFile).delete()
        s.query(Document).delete()
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
    doc1 = Document(
        id=str(uuid.uuid4()),
        filename="Existing_Deck.pptx",
        title="Existing Deck",
        file_type="pptx",
        storage_path="dummy",
        processing_status="COMPLETED",
    )
    doc_failed = Document(
        id=str(uuid.uuid4()),
        filename="Failed_Deck.pptx",
        title="Failed Deck",
        file_type="pptx",
        storage_path="dummy",
        processing_status="FAILED",
    )
    db.add_all([doc1, doc_failed])
    db.commit()

    payload = {
        "files": [
            {"client_item_id": "c1", "filename": "existing_deck.ppt", "size_bytes": 100},
            {"client_item_id": "c2", "filename": "Failed_Deck.pptx", "size_bytes": 200},
            {"client_item_id": "c3", "filename": "Fresh_Deck.pptx", "size_bytes": 300},
            {"client_item_id": "c4", "filename": "fresh   deck.pdf", "size_bytes": 400},
        ]
    }
    r = client.post("/api/v1/jobs/upload-batch", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()

    assert "batch_id" in body
    rej = body.get("rejected_duplicates", [])
    assert len(rej) == 2

    # First rejected is existing deck in DB
    rej1 = next(x for x in rej if x["filename"] == "existing_deck.ppt")
    assert rej1["existing_document_id"] == doc1.id
    assert rej1["existing_title"] == "Existing Deck"

    # Second rejected is intra-batch duplicate of Fresh_Deck.pptx
    rej2 = next(x for x in rej if x["filename"] == "fresh   deck.pdf")
    assert rej2["existing_document_id"] is None


def test_single_and_chunk_upload_rejects_duplicate_with_409(db):
    doc = Document(
        id=str(uuid.uuid4()),
        filename="Alpha_Quiz.pptx",
        title="Alpha Quiz",
        file_type="pptx",
        storage_path="dummy",
        processing_status="COMPLETED",
    )
    db.add(doc)
    db.commit()

    # Single-file endpoint
    r = client.post(
        "/api/v1/documents/upload",
        files={"file": ("alpha quiz.pdf", b"dummy content", "application/pdf")},
    )
    assert r.status_code == 409
    body = r.json()
    assert body["detail"]["code"] == "duplicate_name"
    assert "already in QShala" in body["detail"]["detail"]

    # Chunk upload endpoint
    r_chunk = client.post(
        "/api/v1/documents/upload-chunk",
        data={
            "upload_id": "upload_12345",
            "chunk_index": 0,
            "total_chunks": 1,
            "filename": "alpha_quiz.ppt",
        },
        files={"chunk": ("chunk", b"dummy bytes", "application/octet-stream")},
    )
    assert r_chunk.status_code == 409
    body_chunk = r_chunk.json()
    assert body_chunk["detail"]["code"] == "duplicate_name"
    assert "already in QShala" in body_chunk["detail"]["detail"]


def test_classify_marks_same_name_already_imported_regardless_of_size_and_changed(db):
    # doc-sp1 created by sp1 earlier
    doc_sp1 = Document(
        id="doc-sp1",
        filename="Tracked_Deck.pptx",
        title="Tracked Deck",
        file_type="pptx",
        storage_path="dummy",
        sharepoint_file_id="sp1",
        processing_status="COMPLETED",
    )
    # doc-manual created with different name/size
    doc_manual = Document(
        id="doc-manual",
        filename="Manual_Upload.pptx",
        title="Manual Upload",
        file_type="pptx",
        storage_path="dummy",
        file_size_bytes=99999,
        processing_status="COMPLETED",
    )
    sp_file = SharePointFile(
        id="sp1",
        name="Tracked_Deck.pptx",
        etag="old_etag",
        sync_status="COMPLETED",
    )
    db.add_all([doc_sp1, doc_manual, sp_file])
    db.commit()

    candidates = [
        # Same normalized name as doc_manual, but different size (100 vs 99999)
        {"id": "sp-new-1", "name": "manual upload.pdf", "size": 100, "etag": "e1"},
        # Same sharepoint id as sp1, but etag changed
        {"id": "sp1", "name": "Tracked_Deck.pptx", "size": 500, "etag": "new_etag"},
        # Completely new file
        {"id": "sp-fresh", "name": "Fresh_Deck.pptx", "size": 300, "etag": "e3"},
    ]

    importer.classify(db, candidates)
    res = {c["id"]: c for c in candidates}

    assert res["sp-new-1"]["status"] == "ALREADY_IMPORTED"
    assert res["sp-new-1"]["match_document_id"] == doc_manual.id

    assert res["sp1"]["status"] == "CHANGED"

    assert res["sp-fresh"]["status"] == "NEW"


@pytest.mark.anyio
async def test_transfer_next_skips_by_name_without_downloading(db):
    doc = Document(
        id="existing-doc-id",
        filename="Trivia_Championship.pptx",
        title="Trivia Championship",
        file_type="pptx",
        storage_path="dummy",
        processing_status="COMPLETED",
    )
    sp_file = SharePointFile(
        id="sp-cand-1",
        name="trivia   championship.pdf",
        drive_id="drive-1",
        sync_status="SELECTED",
    )
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
    assert sp_file.error_message == "A deck named trivia   championship.pdf is already in QShala"


@pytest.mark.anyio
async def test_sharepoint_not_imported_endpoint(db):
    # Document exists for deck1
    doc = Document(
        id="doc-deck1",
        filename="Deck_One.pptx",
        title="Deck One",
        file_type="pptx",
        storage_path="dummy",
        processing_status="COMPLETED",
    )
    db.add(doc)
    db.commit()

    mock_client = AsyncMock()
    mock_client.list_drives = AsyncMock(return_value=[{"id": "drive-test"}])

    # 2 files in folder: Deck_One.pptx (already imported by name) and Deck_Two.pptx (NEW)
    mock_client.list_children = AsyncMock(return_value={
        "next": None,
        "items": [
            {
                "id": "file-1",
                "name": "deck one.pdf",
                "size": 12345,
                "file": {"hashes": {}},
                "lastModifiedDateTime": "2026-11-20T10:00:00Z",
                "parentReference": {"path": "/drives/drive-test/root:/Quizzes"},
            },
            {
                "id": "file-2",
                "name": "Deck_Two.pptx",
                "size": 67890,
                "file": {"hashes": {}},
                "lastModifiedDateTime": "2026-11-21T10:00:00Z",
                "parentReference": {"path": "/drives/drive-test/root:/Quizzes"},
            },
        ],
    })

    with patch("backend.app.routers.sharepoint.get_sharepoint_client", return_value=mock_client), \
         patch("backend.app.services.sharepoint.importer.scope_roots", AsyncMock(return_value=[])):

        # First call with refresh=1 to ensure cache is primed with our mock data
        r = client.get("/api/v1/sharepoint/not-imported?drive_id=drive-test&refresh=1")
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["count"] == 1
        assert len(body["files"]) == 1
        file_entry = body["files"][0]
        assert file_entry["name"] == "Deck_Two.pptx"
        assert file_entry["size"] == 67890

        # Change mock children to verify cache is honored
        mock_client.list_children = AsyncMock(return_value={"next": None, "items": []})

        r_cached = client.get("/api/v1/sharepoint/not-imported?drive_id=drive-test")
        assert r_cached.status_code == 200
        assert r_cached.json()["count"] == 1

        # Now call with refresh=1, cache is bypassed
        r_refreshed = client.get("/api/v1/sharepoint/not-imported?drive_id=drive-test&refresh=1")
        assert r_refreshed.status_code == 200
        assert r_refreshed.json()["count"] == 0
