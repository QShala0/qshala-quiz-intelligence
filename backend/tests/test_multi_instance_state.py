"""State that used to live in one container's memory must not matter when more than one container serves requests.

"Another container" is simulated by clearing the in-memory flags / registry entries between two calls, because a
different container would not have them. The database (and R2) is what has to carry the truth."""
import asyncio
import sys
import types
import uuid
from pathlib import Path

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.main import app
from backend.app.config import settings
from backend.app.database import SessionLocal
from backend.app.models.document import Document
from backend.app.models.upload_session import UploadSession
from backend.app.routers import documents as documents_router
from backend.app.services.ingestion import cancellation, job_trigger, upload_queue
from backend.app.services.ingestion.pipeline import INGESTION_STATUS_REGISTRY
from backend.app.services.storage import r2_storage

client = TestClient(app)
API = "/api/v1"


class FakeR2:
    is_configured = True

    def __init__(self):
        self.aborted, self.uploaded_files = [], []

    def abort_multipart_upload(self, key, upload_id):
        self.aborted.append((key, upload_id))

    def delete_prefix(self, prefix):
        return 0

    def upload_file(self, key, path):
        self.uploaded_files.append(key)


@pytest.fixture()
def job_mode(monkeypatch):
    monkeypatch.setattr(settings, "INGESTION_MODE", "cloud_run_job")
    fake = FakeR2()
    monkeypatch.setattr(r2_storage, "get_r2_service", lambda: fake)
    return fake


def _session(upload_id):
    db = SessionLocal()
    try:
        rec = db.query(UploadSession).filter(UploadSession.upload_id == upload_id).first()
        return None if rec is None else rec.status
    finally:
        db.close()


def _drop_session(upload_id):
    db = SessionLocal()
    db.query(UploadSession).filter(UploadSession.upload_id == upload_id).delete()
    db.commit()
    db.close()


def _send_slice(upload_id):
    return client.post(f"{API}/documents/upload-chunk", files={"chunk": ("c.part", b"x" * 10)},
                       data={"upload_id": upload_id, "chunk_index": 0, "total_chunks": 1, "filename": "deck.pptx"})


# ------------------------------------------------------------------------------------------------ upload cancel

def test_cancel_before_the_first_slice_is_stored_in_the_database(job_mode):
    upload_id = f"up-{uuid.uuid4().hex[:12]}"
    try:
        assert client.delete(f"{API}/documents/upload-chunk/{upload_id}").status_code == 200
        assert _session(upload_id) == "CANCELLED"               # the cancel no longer lives only in this container's memory

        cancellation.CANCELLED_UPLOADS.discard(upload_id)       # a different container has no in-memory flag
        r = _send_slice(upload_id)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "upload_cancelled"
        assert _session(upload_id) == "CANCELLED"               # and the slice did not start a new upload
    finally:
        cancellation.CANCELLED_UPLOADS.discard(upload_id)
        _drop_session(upload_id)


def test_cancelling_twice_is_harmless(job_mode):
    upload_id = f"up-{uuid.uuid4().hex[:12]}"
    try:
        assert client.delete(f"{API}/documents/upload-chunk/{upload_id}").status_code == 200
        assert client.delete(f"{API}/documents/upload-chunk/{upload_id}").status_code == 200
        assert _session(upload_id) == "CANCELLED"
    finally:
        cancellation.CANCELLED_UPLOADS.discard(upload_id)
        _drop_session(upload_id)


def test_cancelling_an_open_upload_still_aborts_its_r2_multipart_upload(job_mode):
    upload_id = f"up-{uuid.uuid4().hex[:12]}"
    db = SessionLocal()
    db.add(UploadSession(upload_id=upload_id, doc_id=str(uuid.uuid4()), raw_key="raw-uploads/x.pptx", r2_upload_id="r2-id",
                         filename="x.pptx", parts={}, status="OPEN"))
    db.commit()
    db.close()
    try:
        assert client.delete(f"{API}/documents/upload-chunk/{upload_id}").status_code == 200
        assert _session(upload_id) == "CANCELLED"
        assert job_mode.aborted == [("raw-uploads/x.pptx", "r2-id")]
    finally:
        cancellation.CANCELLED_UPLOADS.discard(upload_id)
        _drop_session(upload_id)


def test_no_database_row_is_left_behind_when_ingestion_runs_in_process(monkeypatch):
    monkeypatch.setattr(settings, "INGESTION_MODE", "in_process")
    upload_id = f"up-{uuid.uuid4().hex[:12]}"
    try:
        assert client.delete(f"{API}/documents/upload-chunk/{upload_id}").status_code == 200
        assert _session(upload_id) is None                      # that mode keeps its slices on this container's disk
    finally:
        cancellation.CANCELLED_UPLOADS.discard(upload_id)


# ------------------------------------------------------------------------------------------------ status registry

def _doc(status="PROCESSING"):
    db = SessionLocal()
    doc_id = str(uuid.uuid4())
    db.add(Document(id=doc_id, filename="s.pptx", title="s", file_type="pptx", storage_path="x", file_size_bytes=1,
                    processing_status=status, progress_detail={"progress_percentage": 42, "current_step": "Tagging questions"}))
    db.commit()
    db.close()
    return doc_id


def _entry(doc_id, status, pct=5, step="1. Extracting content"):
    return {"document_id": doc_id, "status": status, "progress_percentage": pct, "current_step": step,
            "slides_processed": 0, "questions_extracted": 0, "error": None}


def _drop_doc(doc_id):
    INGESTION_STATUS_REGISTRY.pop(doc_id, None)
    db = SessionLocal()
    db.query(Document).filter(Document.id == doc_id).delete()
    db.commit()
    db.close()


def test_job_mode_ignores_a_stale_running_entry_and_reads_the_database(monkeypatch):
    monkeypatch.setattr(settings, "INGESTION_MODE", "cloud_run_job")
    doc_id = _doc()
    try:
        INGESTION_STATUS_REGISTRY[doc_id] = _entry(doc_id, "PROCESSING")
        body = client.get(f"{API}/documents/{doc_id}/status").json()
        assert body["progress_percentage"] == 42 and body["current_step"] == "Tagging questions"
        INGESTION_STATUS_REGISTRY[doc_id] = _entry(doc_id, "QUEUED")
        assert client.get(f"{API}/documents/{doc_id}/status").json()["progress_percentage"] == 42
    finally:
        _drop_doc(doc_id)


def test_job_mode_still_serves_a_finished_entry(monkeypatch):
    monkeypatch.setattr(settings, "INGESTION_MODE", "cloud_run_job")
    doc_id = _doc()
    try:
        INGESTION_STATUS_REGISTRY[doc_id] = _entry(doc_id, "CANCELLED", pct=0, step="Upload cancelled")
        body = client.get(f"{API}/documents/{doc_id}/status").json()
        assert body["status"] == "CANCELLED" and body["current_step"] == "Upload cancelled"
    finally:
        _drop_doc(doc_id)


def test_in_process_mode_still_uses_the_live_in_memory_entry(monkeypatch):
    monkeypatch.setattr(settings, "INGESTION_MODE", "in_process")
    doc_id = _doc()
    try:
        INGESTION_STATUS_REGISTRY[doc_id] = _entry(doc_id, "PROCESSING", pct=5)
        assert client.get(f"{API}/documents/{doc_id}/status").json()["progress_percentage"] == 5
    finally:
        _drop_doc(doc_id)


# ------------------------------------------------------------------------------------------------ queue

def _schedule(monkeypatch, batch_id=None):
    calls = []
    monkeypatch.setattr(upload_queue, "enqueue_upload", lambda db, **kw: (calls.append("enqueue"), types.SimpleNamespace(id="job-1"))[1])

    async def fake_dispatch(*a, **k):
        return 0
    monkeypatch.setattr(upload_queue, "dispatch_available", fake_dispatch)
    monkeypatch.setattr(job_trigger, "trigger_ingestion_job", lambda doc_id: calls.append("trigger"))
    bg = BackgroundTasks()
    doc = types.SimpleNamespace(id="doc-1", filename="f.pptx", storage_path="f.pptx", processing_status="UPLOADED")
    documents_router._schedule_ingestion(doc, None, bg, raw_already_in_r2=True, batch_id=batch_id)
    return calls, bg


def test_job_mode_uploads_without_a_batch_id_still_go_through_the_queue(job_mode, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_QUEUE_ENABLED", False)
    monkeypatch.delenv("QUEUE_ALL_JOB_UPLOADS", raising=False)
    calls, bg = _schedule(monkeypatch)
    assert calls == ["enqueue"]                                 # one deck at a time, not a job per upload
    assert len(bg.tasks) == 1                                   # and the queue is kicked once


def test_the_switch_restores_a_job_per_upload(job_mode, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_QUEUE_ENABLED", False)
    monkeypatch.setenv("QUEUE_ALL_JOB_UPLOADS", "0")
    calls, _ = _schedule(monkeypatch)
    assert calls == ["trigger"]


def test_batch_uploads_are_queued_either_way(job_mode, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_QUEUE_ENABLED", False)
    monkeypatch.setenv("QUEUE_ALL_JOB_UPLOADS", "0")
    calls, _ = _schedule(monkeypatch, batch_id="batch-1")
    assert calls == ["enqueue"]


def test_the_polling_loop_is_not_started_by_this_change():
    # UPLOAD_QUEUE_ENABLED also starts a database poller in every container; this change must leave its default alone.
    assert settings.UPLOAD_QUEUE_ENABLED is False
