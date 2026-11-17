"""Search speed and recall changes: vector scan settings, the keyword-only fallback, lighter list queries, the cached
deck/topic lists for the search box, and the embedding backfill."""
import asyncio
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.main import app
from backend.app.database import SessionLocal, engine
from backend.app.models.document import Document, Slide
from backend.app.models.question import Question
from backend.app.routers import questions as questions_router
from backend.app.routers.stats import invalidate_stats_cache
from backend.app.services.ingestion import pipeline
from backend.app.services.retrieval import hybrid_retriever
from backend.app.services.retrieval.hybrid_retriever import HybridRetriever, tune_vector_scan

client = TestClient(app)
API = "/api/v1"
TAG = uuid.uuid4().hex[:8]


@pytest.fixture()
def deck():
    db = SessionLocal()
    doc_id = str(uuid.uuid4())
    db.add(Document(id=doc_id, filename=f"perf-{TAG}.pptx", title=f"Perf deck {TAG}", file_type="pptx",
                    storage_path="x", file_size_bytes=1, processing_status="COMPLETED",
                    progress_detail={"events": [{"msg": "x" * 2000}]}))
    db.commit()
    made = []

    def add(text, answer="ans", embedding=None, duplicate_of_id=None, topic="Geography"):
        s = SessionLocal()
        slide = Slide(document_id=doc_id, slide_number=len(made) + 1, extracted_text=f"slide text {text}")
        s.add(slide)
        s.flush()
        q = Question(document_id=doc_id, slide_id=slide.id, question_text=text, answer=answer, topic=topic,
                     content_hash=uuid.uuid4().hex, embedding=embedding, duplicate_of_id=duplicate_of_id)
        s.add(q)
        s.commit()
        made.append(q.id)
        qid = q.id
        s.close()
        return qid

    yield doc_id, add
    db.query(Question).filter(Question.document_id == doc_id).update({Question.duplicate_of_id: None})
    db.query(Question).filter(Question.document_id == doc_id).delete()
    db.query(Slide).filter(Slide.document_id == doc_id).delete()
    db.query(Document).filter(Document.id == doc_id).delete()
    db.commit()
    db.close()
    questions_router.invalidate_search_scope_cache()


# ------------------------------------------------------------------ vector scan settings

def test_vector_settings_never_break_the_transaction_on_a_database_without_them():
    db = SessionLocal()
    try:
        assert tune_vector_scan(db) is False          # SQLite: no such settings
        assert db.query(Question.id).limit(1).all() is not None  # the session is still usable
    finally:
        db.close()


def test_vector_settings_are_issued_as_set_local(monkeypatch):
    seen = []

    class FakeDb:
        def execute(self, stmt, params=None):
            seen.append((str(stmt), params))

    monkeypatch.setattr(hybrid_retriever, "VECTOR_IVFFLAT_PROBES", 20)
    monkeypatch.setattr(hybrid_retriever, "VECTOR_HNSW_EF_SEARCH", 100)
    monkeypatch.setattr(hybrid_retriever, "VECTOR_ITERATIVE_SCAN", "relaxed_order")
    assert tune_vector_scan(FakeDb()) is True
    assert len(seen) == 1                            # one round trip, not one per setting
    sql, params = seen[0]
    for name in ("ivfflat.probes", "hnsw.ef_search", "hnsw.iterative_scan", "ivfflat.iterative_scan"):
        assert f"set_config('{name}'" in sql
    assert ", true)" in sql                          # local to this transaction
    assert sorted(params.values()) == sorted(["20", "100", "relaxed_order", "relaxed_order"])
    seen.clear()
    monkeypatch.setattr(hybrid_retriever, "VECTOR_ITERATIVE_SCAN", "off")
    tune_vector_scan(FakeDb())
    assert "iterative_scan" not in seen[0][0]


# ------------------------------------------------------------------ keyword-only fallback

def test_smart_search_without_an_embedding_only_loads_rows_with_a_search_word(deck, monkeypatch):
    doc_id, add = deck
    hit = add(f"Which zebrafinch{TAG} river is longest?")
    add(f"Unrelated question {TAG} about cars")

    async def no_embedding(self, q):
        return None
    monkeypatch.setattr(HybridRetriever, "_get_query_embedding", no_embedding)
    db = SessionLocal()
    try:
        res = asyncio.run(HybridRetriever(db).search(query=f"zebrafinch{TAG}", document_ids=[doc_id], limit=10))
    finally:
        db.close()
    assert [r["question"].id for r in res] == [hit]


def test_the_keyword_only_fallback_is_capped(deck, monkeypatch):
    doc_id, add = deck
    for i in range(5):
        add(f"capword{TAG} number {i}")

    async def no_embedding(self, q):
        return None
    monkeypatch.setattr(HybridRetriever, "_get_query_embedding", no_embedding)
    monkeypatch.setattr(hybrid_retriever, "SMART_FALLBACK_CAP", 3)
    db = SessionLocal()
    try:
        res = asyncio.run(HybridRetriever(db).search(query=f"capword{TAG}", document_ids=[doc_id], limit=10))
    finally:
        db.close()
    assert len(res) == 3


# ------------------------------------------------------------------ list queries

def _count_statements(fn):
    n = {"c": 0}

    def _c(*a, **k):
        n["c"] += 1
    event.listen(engine, "before_cursor_execute", _c)
    try:
        out = fn()
    finally:
        event.remove(engine, "before_cursor_execute", _c)
    return out, n["c"]


def test_bank_list_still_shows_the_duplicate_text_without_a_query_per_row(deck):
    doc_id, add = deck
    orig = add(f"Original question {TAG}", answer="orig answer")
    for i in range(4):
        add(f"Copy {i} {TAG}", duplicate_of_id=orig)
    r, n = _count_statements(lambda: client.get(f"{API}/questions", params={"document_ids": doc_id, "limit": 50}))
    body = r.json()
    copies = [b for b in body if b["duplicate_of_id"] == orig]
    assert len(copies) == 4
    assert all(c["duplicate_of_text"] == f"Original question {TAG}" and c["duplicate_of_answer"] == "orig answer"
               for c in copies)
    assert all(b["document_title"] == f"Perf deck {TAG}" and b["slide_number"] for b in body)
    assert n <= 9                                   # constant, not one per row


# ------------------------------------------------------------------ search box deck/topic lists

def test_deck_and_topic_lists_are_cached_and_cleared_with_the_stats_cache(deck):
    doc_id, add = deck
    add(f"Cached lists question {TAG}")
    questions_router.invalidate_search_scope_cache()
    _, first = _count_statements(lambda: client.get(f"{API}/questions", params={"query": f"cachedword{TAG}"}))
    _, second = _count_statements(lambda: client.get(f"{API}/questions", params={"query": f"cachedword{TAG}"}))
    assert second <= first - 2                      # the two list queries were skipped
    invalidate_stats_cache()
    assert questions_router._scope_cache["docs"] is None


def test_a_new_deck_name_is_found_after_the_cache_is_cleared(deck):
    doc_id, add = deck
    add(f"Question in the named deck {TAG}")
    questions_router.invalidate_search_scope_cache()
    client.get(f"{API}/questions", params={"query": "warmup"})       # fill the cache
    db = SessionLocal()
    new_id = str(uuid.uuid4())
    db.add(Document(id=new_id, filename=f"Quokkafest{TAG}.pptx", title=f"Quokkafest{TAG}", file_type="pptx",
                    storage_path="x", file_size_bytes=1, processing_status="COMPLETED"))
    db.commit()
    try:
        invalidate_stats_cache()                                    # what ingestion calls when a deck finishes
        docs, _ = questions_router._search_scope_lists(db)
        assert any(d["id"] == new_id for d in docs)
    finally:
        db.query(Document).filter(Document.id == new_id).delete()
        db.commit()
        db.close()


# ------------------------------------------------------------------ embedding text + backfill

def test_embedding_text_matches_the_previous_upload_formula():
    q = {"primary_topic": None, "topics": [], "question_text": "Q?", "answer": "A",
         "visual_clues": "v" * 900, "explanation": "E"}
    old = f"{q.get('primary_topic') or ''} {' '.join(q.get('topics') or [])} {q['question_text']} {q['answer']} " \
          f"{(q.get('visual_clues') or '')[:pipeline.EMBED_VISUAL_CLUES_CHARS]} {q.get('explanation') or ''}".strip()
    assert pipeline.embedding_text(q) == old


class FakeProvider:
    may_substitute_local = False
    model = "fake-embed"

    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    async def get_embeddings(self, texts):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return [[0.1] * 768 for _ in texts]


def test_backfill_dry_run_counts_without_calling_the_provider(deck):
    from backend.scripts.backfill_embeddings import backfill
    _, add = deck
    add(f"No vector {TAG}")
    prov = FakeProvider()
    db = SessionLocal()
    try:
        stats = asyncio.run(backfill(db, apply=False, provider=prov))
    finally:
        db.close()
    assert stats["missing"] >= 1 and stats["embedded"] == 0 and prov.calls == 0


def test_backfill_gives_missing_questions_a_vector_and_skips_ones_that_have_one(deck):
    from backend.scripts.backfill_embeddings import backfill
    _, add = deck
    a = add(f"Needs vector A {TAG}")
    b = add(f"Needs vector B {TAG}")
    has = add(f"Has vector {TAG}", embedding=[0.5] * 768)
    db = SessionLocal()
    try:
        stats = asyncio.run(backfill(db, apply=True, batch=1, provider=FakeProvider()))
        assert stats["failed"] == 0 and stats["embedded"] >= 2
        got = {q.id: q.embedding for q in db.query(Question).filter(Question.id.in_([a, b, has]))}
    finally:
        db.close()
    assert got[a] is not None and got[b] is not None
    assert list(got[has])[0] == pytest.approx(0.5)


def test_backfill_leaves_rows_untouched_when_the_provider_fails(deck):
    from backend.scripts.backfill_embeddings import backfill
    _, add = deck
    a = add(f"Still no vector {TAG}")
    db = SessionLocal()
    try:
        stats = asyncio.run(backfill(db, apply=True, provider=FakeProvider(fail=True)))
        assert stats["embedded"] == 0 and stats["failed"] >= 1
        assert db.query(Question).filter(Question.id == a).one().embedding is None
    finally:
        db.close()
