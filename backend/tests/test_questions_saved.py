"""While AI tagging is still running, the stored questions are already searchable: the status says how many, so the
upload page can link to them straight away."""
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.main import app
from backend.app.config import settings
from backend.app.database import SessionLocal
from backend.app.models.document import Document
from backend.app.models.question import Question
from backend.app.services.ingestion.pipeline import INGESTION_STATUS_REGISTRY

import test_dedup_before_ai as dedup_helpers   # the same small pipeline harness the other ingestion tests use

client = TestClient(app)
API = "/api/v1"


def test_status_from_the_database_carries_questions_saved(monkeypatch):
    monkeypatch.setattr(settings, "INGESTION_MODE", "cloud_run_job")      # the worker's progress mirror is what is read
    db = SessionLocal()
    doc_id = str(uuid.uuid4())
    db.add(Document(id=doc_id, filename="s.pptx", title="s", file_type="pptx", storage_path="x", file_size_bytes=1,
                    processing_status="PROCESSING",
                    progress_detail={"progress_percentage": 90, "current_step": "8. Tagging questions", "questions_saved": 24}))
    db.commit()
    db.close()
    try:
        body = client.get(f"{API}/documents/{doc_id}/status").json()
        assert body["questions_saved"] == 24 and body["status"] == "PROCESSING"
    finally:
        db = SessionLocal()
        db.query(Document).filter(Document.id == doc_id).delete()
        db.commit()
        db.close()


def test_status_before_the_questions_are_saved_has_none(monkeypatch):
    monkeypatch.setattr(settings, "INGESTION_MODE", "cloud_run_job")
    db = SessionLocal()
    doc_id = str(uuid.uuid4())
    db.add(Document(id=doc_id, filename="s2.pptx", title="s2", file_type="pptx", storage_path="x", file_size_bytes=1,
                    processing_status="PROCESSING", progress_detail={"progress_percentage": 40}))
    db.commit()
    db.close()
    try:
        assert client.get(f"{API}/documents/{doc_id}/status").json()["questions_saved"] is None
    finally:
        db = SessionLocal()
        db.query(Document).filter(Document.id == doc_id).delete()
        db.commit()
        db.close()


def test_the_pipeline_publishes_how_many_questions_are_saved_before_tagging_finishes():
    tag = uuid.uuid4().hex[:8]
    doc_id = dedup_helpers._ingest(dedup_helpers._items(tag, [1, 2, 3]), dedup_helpers.RecordingEmbedder(),
                                   name=f"saved-{tag}.pptx")
    reg = INGESTION_STATUS_REGISTRY[doc_id]
    assert reg["questions_saved"] == 3
    assert any("Saved 3 questions" in e["msg"] and "question bank" in e["msg"] for e in reg["events"])
    db = SessionLocal()
    try:
        assert db.query(Question).filter(Question.document_id == doc_id).count() == 3
    finally:
        db.close()
