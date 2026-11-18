"""Fixes from the code review of the Phase 2 branch."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.config import settings
from backend.app.database import SessionLocal, engine
from backend.app.main import app
from backend.app.models.document import Document
from backend.app.models.job import IngestionJob
from backend.app.services.ai import gemini_provider as gp
from backend.app.services.deduplication.deduplicator import Deduplicator
from backend.app.services.export import exporter as ex
from backend.app.services.ingestion import pipeline as pipeline_mod
from backend.app.services.ingestion.pipeline import embedding_model_label, exact_duplicate_indices
from scripts import add_fk_indexes as fk

client = TestClient(app)


@pytest.fixture(autouse=True)
def _cloud_run_job_mode(monkeypatch):
    """The live-worker guard only applies where a separate worker exists."""
    monkeypatch.setattr(settings, "INGESTION_MODE", "cloud_run_job")


# ------------------------------------------------------------------ 1: dedup keeps candidates and vectors aligned
def _unit(dim_index, n=768):
    v = [0.0] * n
    v[dim_index] = 1.0
    return v


def test_candidate_without_embedding_does_not_shift_intra_batch_matches():
    tag = uuid.uuid4().hex[:8]
    a = {"question_text": f"Which planet is called the red planet number {tag}?", "answer": "Mars", "slide_id": None}
    b = {"question_text": f"Who wrote the national anthem of India titled {tag}?", "answer": "Rabindranath Tagore", "slide_id": None}
    c = {"question_text": f"Who wrote the national anthem of India titled {tag} again?", "answer": "Rabindranath Tagore", "slide_id": None}
    db = SessionLocal()
    try:
        kept, dropped, _ = Deduplicator().deduplicate_batch(
            db, [a, b, c], [None, _unit(700), _unit(700)], doc_id=str(uuid.uuid4()))
    finally:
        db.close()
    by_text = {k["question_text"]: k for k in kept}
    assert len(kept) == 3 and not dropped
    assert by_text[c["question_text"]]["duplicate_status"] == "POSSIBLE_DUPLICATE"
    # It must point at B (the question it really matches), not at A (the vector-less question before it).
    assert by_text[c["question_text"]]["duplicate_of_id"] == by_text[b["question_text"]]["id"]
    assert by_text[c["question_text"]]["duplicate_of_id"] != by_text[a["question_text"]]["id"]
    assert by_text[a["question_text"]]["duplicate_status"] == "UNIQUE"


# ------------------------------------------------------------------ 2: batch cancel / delete respect a live worker
def _doc_and_job(job_age_s, batch_id=None, make_file=False):
    doc_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
    path = settings.UPLOAD_DIR / f"{doc_id}.pptx"
    if make_file:
        settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"deck")
    db = SessionLocal()
    try:
        db.add(Document(id=doc_id, filename="d.pptx", title="d", file_type="pptx", storage_path=str(path),
                        file_size_bytes=4, processing_status="PROCESSING"))
        db.flush()
        job = IngestionJob(id=job_id, filename="d.pptx", source_type="MANUAL_UPLOAD", status="PROCESSING",
                           document_id=doc_id, batch_id=batch_id)
        db.add(job)
        db.flush()
        job.updated_at = datetime.now(timezone.utc) - timedelta(seconds=job_age_s)
        db.commit()
    finally:
        db.close()
    return doc_id, job_id, path


def _state(doc_id, job_id):
    db = SessionLocal()
    try:
        d = db.query(Document).filter(Document.id == doc_id).first()
        j = db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
        return (d.cancel_requested if d else None, d.processing_status if d else None, j.status if j else None)
    finally:
        db.close()


def test_cancel_batch_with_a_live_worker_flags_instead_of_purging():
    batch = str(uuid.uuid4())
    doc_id, job_id, path = _doc_and_job(job_age_s=2, batch_id=batch, make_file=True)
    r = client.post(f"/api/v1/jobs/upload-batch/{batch}/cancel")
    assert r.status_code == 200, r.text
    flagged, status, job_status = _state(doc_id, job_id)
    assert flagged is True and status == "PROCESSING"          # document kept, worker told to stop
    assert job_status == "PROCESSING"                          # the worker writes the terminal status itself
    assert path.exists()                                       # the deck file was not removed under it
    path.unlink()


def test_cancel_batch_in_process_mode_still_cleans_up_immediately(monkeypatch):
    # No separate worker exists in this mode and nothing refreshes the job row, so a "fresh" job is not a live worker.
    monkeypatch.setattr(settings, "INGESTION_MODE", "in_process")
    batch = str(uuid.uuid4())
    doc_id, job_id, path = _doc_and_job(job_age_s=2, batch_id=batch, make_file=True)
    r = client.post(f"/api/v1/jobs/upload-batch/{batch}/cancel")
    assert r.status_code == 200, r.text
    _flagged, status, job_status = _state(doc_id, job_id)
    assert job_status == "CANCELLED" and status == "CANCELLED" and not path.exists()


def test_cancel_batch_with_a_dead_worker_still_cleans_up():
    batch = str(uuid.uuid4())
    doc_id, job_id, path = _doc_and_job(job_age_s=3600, batch_id=batch, make_file=True)
    r = client.post(f"/api/v1/jobs/upload-batch/{batch}/cancel")
    assert r.status_code == 200, r.text
    _flagged, status, job_status = _state(doc_id, job_id)
    assert job_status == "CANCELLED" and status == "CANCELLED"
    assert not path.exists()


def test_delete_document_with_a_live_worker_asks_the_worker_to_stop():
    doc_id, job_id, _ = _doc_and_job(job_age_s=2)
    r = client.delete(f"/api/v1/documents/{doc_id}")
    assert r.status_code == 200 and "worker" in r.json()["message"]
    flagged, status, _ = _state(doc_id, job_id)
    assert flagged is True and status == "PROCESSING"


def test_delete_document_without_a_worker_deletes_it():
    doc_id, job_id, _ = _doc_and_job(job_age_s=3600)
    assert client.delete(f"/api/v1/documents/{doc_id}").status_code == 200
    assert _state(doc_id, job_id)[0] is None


# ------------------------------------------------------------------ 4 + 7: SSRF filter and DNS lookups
def test_each_host_is_resolved_once_not_twice_per_picture(monkeypatch):
    calls = []

    def fake_getaddrinfo(host, port, *a, **k):
        calls.append(host)
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    ex._HOST_CACHE.clear()
    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
    for i in range(5):
        assert ex._url_allowed(f"https://cdn.example.test/slides/d/media/{i}.png")
        assert ex._url_allowed(f"https://cdn.example.test/slides/d/media/{i}.png")
    assert calls == ["cdn.example.test"]
    ex._HOST_CACHE.clear()


# ------------------------------------------------------------------ 5: embedding_model is not claimed when unsure
def test_embedding_model_label_is_none_when_local_vectors_may_have_been_substituted(monkeypatch):
    provider = gp.GeminiEmbeddingProvider(api_key="test-key-not-real")
    monkeypatch.setattr(gp, "ALLOW_LOCAL_EMBEDDING_FALLBACK", False)
    assert embedding_model_label(provider) == "gemini-embedding-001"
    monkeypatch.setattr(gp, "ALLOW_LOCAL_EMBEDDING_FALLBACK", True)
    assert embedding_model_label(provider) is None
    monkeypatch.setattr(gp, "ALLOW_LOCAL_EMBEDDING_FALLBACK", False)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")        # the provider falls back to the configured key
    assert embedding_model_label(gp.GeminiEmbeddingProvider(api_key="")) is None


# ------------------------------------------------------------------ 6: index script handles broken / equivalent indexes
def test_invalid_index_is_dropped_and_rebuilt(monkeypatch):
    monkeypatch.setattr(fk, "invalid_indexes", lambda eng: {"ix_slides_document_id"})
    assert [m[0] for m in fk.missing_indexes(engine)] == ["ix_slides_document_id"]
    stmts = fk.plan(engine)
    assert stmts == ["DROP INDEX IF EXISTS ix_slides_document_id",
                     "CREATE INDEX IF NOT EXISTS ix_slides_document_id ON slides (document_id)"]
    monkeypatch.undo()
    fk.apply(engine)                                       # leave the shared test database intact
    assert fk.missing_indexes(engine) == []


def test_an_equivalent_index_under_another_name_counts_as_present():
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ix_slides_document_id"))
        conn.execute(text("CREATE INDEX my_slide_idx ON slides (document_id)"))
    try:
        assert fk.missing_indexes(engine) == []            # no duplicate index would be created
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP INDEX IF EXISTS my_slide_idx"))
        fk.apply(engine)
    assert fk.missing_indexes(engine) == []


# ------------------------------------------------------------------ 8: one definition of the tier-1 rule
def test_tier1_rule_is_shared_by_the_pipeline_precheck_and_the_deduplicator():
    assert Deduplicator.tier1_exact_indices(["a", "b", "a", "c", "b"], {"c"}) == {2, 3, 4}
    tag = uuid.uuid4().hex[:8]
    qs = [{"question_text": f"Question one {tag}?", "answer": "A"},
          {"question_text": f"Question two {tag}?", "answer": "B"},
          {"question_text": f"Question one {tag}?", "answer": "A"}]
    db = SessionLocal()
    try:
        skip = exact_duplicate_indices(db, qs)
        kept, dropped, _ = Deduplicator().deduplicate_batch(db, [dict(q) for q in qs], [None, None, None],
                                                            doc_id=str(uuid.uuid4()))
    finally:
        db.close()
    assert skip == {2}
    assert len(dropped) == len(skip) and len(kept) == 2
