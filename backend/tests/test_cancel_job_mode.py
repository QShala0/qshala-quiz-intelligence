"""Cancel while a Cloud Run Job worker owns the document (audit Q8)."""
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.main import app
from backend.app.database import SessionLocal
from backend.app.models.document import Document
from backend.app.models.job import IngestionJob
from backend.app.services.ingestion import cancellation

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import worker  # noqa: E402  (backend/worker.py)

client = TestClient(app)


@pytest.fixture(autouse=True)
def _cloud_run_job_mode(monkeypatch):
    """The live-worker guard only applies where a separate worker exists."""
    monkeypatch.setattr(settings, "INGESTION_MODE", "cloud_run_job")


def _doc_with_job(job_age_s=1, job_status="PROCESSING"):
    doc_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.add(Document(id=doc_id, filename="job.pptx", title="job", file_type="pptx",
                        storage_path="dummy", file_size_bytes=1, processing_status="PROCESSING"))
        db.flush()
        job = IngestionJob(id=job_id, filename="job.pptx", source_type="MANUAL_UPLOAD", status=job_status,
                           document_id=doc_id)
        db.add(job)
        db.flush()
        job.updated_at = datetime.now(timezone.utc) - timedelta(seconds=job_age_s)
        db.commit()
    finally:
        db.close()
    return doc_id, job_id


def _doc_exists(doc_id):
    db = SessionLocal()
    try:
        return db.query(Document).filter(Document.id == doc_id).first() is not None
    finally:
        db.close()


def test_cancel_with_a_live_worker_flags_but_does_not_purge():
    doc_id, _ = _doc_with_job(job_age_s=2)
    r = client.post(f"/api/v1/documents/{doc_id}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "CANCELLING"
    assert _doc_exists(doc_id)                                  # the worker still has a row to write into
    db = SessionLocal()
    try:
        assert db.query(Document).get(doc_id).cancel_requested is True
    finally:
        db.close()


def test_cancel_with_a_live_worker_leaves_its_job_row_to_the_worker():
    doc_id, job_id = _doc_with_job(job_age_s=2)
    client.post(f"/api/v1/documents/{doc_id}/cancel")
    db = SessionLocal()
    try:
        assert db.query(IngestionJob).get(job_id).status == "PROCESSING"
    finally:
        db.close()


def test_cancel_with_a_stale_job_still_purges_immediately():
    doc_id, _ = _doc_with_job(job_age_s=3600)                   # worker died long ago
    r = client.post(f"/api/v1/documents/{doc_id}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    assert not _doc_exists(doc_id)


def test_cancel_without_any_job_still_purges_immediately():
    doc_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.add(Document(id=doc_id, filename="x.pptx", title="x", file_type="pptx",
                        storage_path="dummy", file_size_bytes=1, processing_status="PROCESSING"))
        db.commit()
    finally:
        db.close()
    r = client.post(f"/api/v1/documents/{doc_id}/cancel")
    assert r.json()["status"] == "CANCELLED" and not _doc_exists(doc_id)


def test_worker_poller_treats_a_missing_document_as_cancelled():
    doc_id = str(uuid.uuid4())                                  # never existed / already purged
    cancellation.clear_doc_cancel(doc_id)

    async def go():
        await asyncio.wait_for(worker._poll_cancel(doc_id, asyncio.Event()), timeout=10)

    asyncio.run(go())
    assert cancellation.doc_cancel_requested(doc_id)
    cancellation.clear_doc_cancel(doc_id)


def test_worker_poller_still_reacts_to_the_cancel_flag():
    doc_id, _ = _doc_with_job()
    db = SessionLocal()
    try:
        db.query(Document).get(doc_id).cancel_requested = True
        db.commit()
    finally:
        db.close()
    asyncio.run(asyncio.wait_for(worker._poll_cancel(doc_id, asyncio.Event()), timeout=10))
    assert cancellation.doc_cancel_requested(doc_id)
    cancellation.clear_doc_cancel(doc_id)


def test_job_is_marked_cancelled_even_after_its_document_was_purged():
    doc_id, job_id = _doc_with_job()
    assert worker._find_processing_job_id(doc_id) == job_id
    db = SessionLocal()
    try:
        # What deleting the document does to the job row (FK ON DELETE SET NULL), and the row itself is gone.
        db.query(IngestionJob).filter(IngestionJob.id == job_id).update({"document_id": None})
        db.query(Document).filter(Document.id == doc_id).delete()
        db.commit()
    finally:
        db.close()
    worker._write_terminal_job_status(doc_id, None, job_id)
    db = SessionLocal()
    try:
        job = db.query(IngestionJob).get(job_id)
        assert job.status == "CANCELLED" and job.completed_at is not None
    finally:
        db.close()


def test_terminal_status_for_a_normal_failure_is_unchanged():
    doc_id, job_id = _doc_with_job()
    db = SessionLocal()
    try:
        d = db.query(Document).get(doc_id)
        d.processing_status = "FAILED"
        d.processing_error = "boom"
        db.commit()
    finally:
        db.close()
    worker._write_terminal_job_status(doc_id, None, job_id)
    db = SessionLocal()
    try:
        job = db.query(IngestionJob).get(job_id)
        assert job.status == "FAILED" and job.error_message == "boom"
    finally:
        db.close()
