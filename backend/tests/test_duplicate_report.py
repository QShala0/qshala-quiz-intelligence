"""Tests for Workstream 2 (duplicate report dialog + Repeated flags):

  1. build_duplicate_report() shape: reconciliation, already_in_library, repeated_in_this_deck,
     possible_duplicates_kept, provenance_warnings passthrough (pure unit test).
  2. A cross-deck exact repeat produces a report entry naming the deck it's already in, and still
     writes a QuestionRepeat row (existing behaviour, re-asserted here).
  3. A repeat within the SAME upload is counted in the report but gets no QuestionRepeat row (the
     owner's decision: no "Repeated" flag for intra-deck repeats, just a count).
  4. One failing repeat-provenance write (_batch_commit_provenance) does not lose the others --
     the per-item savepoint fix.

Reuses the real-deck pipeline harness from test_dedup_before_ai.py (same fixtures the repo
already uses for dedup-before-AI tests) for #2 and #3, so these exercise the real ingestion path,
not just the deduplicator in isolation.
"""
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # test_dedup_before_ai is a sibling module

from backend.app.database import SessionLocal
from backend.app.models.document import Document
from backend.app.models.question import Question
from backend.app.models.question_meta import QuestionRepeat
from backend.app.services.deduplication.deduplicator import Deduplicator, build_duplicate_report
from backend.app.services import question_meta_service

from test_dedup_before_ai import _ingest, _items, RecordingEmbedder


# --------------------------------------------------------------------- build_duplicate_report (unit)

def test_build_duplicate_report_shape():
    exact_duplicates = [
        {"duplicate_type": "EXACT_HASH", "already_in_deck_title": "Deck A", "question_number": 5},
        {"duplicate_type": "EXACT_HASH", "already_in_deck_title": "Deck B", "question_number": 12},
        {"duplicate_type": "INTRA_BATCH_EXACT"},
    ]
    final_candidates = [
        {"question_text": "Q1", "duplicate_status": "UNIQUE"},
        {"question_text": "Q2", "duplicate_status": "POSSIBLE_DUPLICATE",
         "duplicate_similarity": 0.95, "duplicate_of_id": "abc"},
    ]
    report = build_duplicate_report(
        deck_title="My Deck", extracted=5, saved=2,
        exact_duplicates=exact_duplicates, final_candidates=final_candidates,
        provenance_warnings=["one went wrong"])

    assert report["deck_title"] == "My Deck"
    assert report["reconciliation"] == {"extracted": 5, "saved": 2, "dropped": 3}
    assert len(report["already_in_library"]) == 2
    assert {"already_in_deck_title": "Deck A", "question_number": 5} in report["already_in_library"]
    assert report["repeated_in_this_deck"] == 1
    assert len(report["possible_duplicates_kept"]) == 1
    assert report["possible_duplicates_kept"][0]["similarity"] == 0.95
    assert report["skipped_media_slides"] == []
    assert report["provenance_warnings"] == ["one went wrong"]


def test_build_duplicate_report_defaults_are_empty():
    report = build_duplicate_report(deck_title="D", extracted=0, saved=0,
                                    exact_duplicates=[], final_candidates=[])
    assert report["already_in_library"] == []
    assert report["repeated_in_this_deck"] == 0
    assert report["possible_duplicates_kept"] == []
    assert report["skipped_media_slides"] == []
    assert report["provenance_warnings"] == []


# --------------------------------------------------------------------- end-to-end via the real pipeline

def test_cross_deck_repeat_report_names_the_original_deck():
    tag = uuid.uuid4().hex[:8]
    doc1 = _ingest(_items(tag, [1, 2, 3, 4]), RecordingEmbedder(), name="original.pptx")
    doc2 = _ingest(_items(tag, [1, 2, 3, 4]), RecordingEmbedder(), name="reupload.pptx")

    db = SessionLocal()
    try:
        doc2_row = db.query(Document).filter(Document.id == doc2).first()
        report = doc2_row.duplicate_report
        assert report is not None
        assert report["reconciliation"] == {"extracted": 4, "saved": 0, "dropped": 4}
        assert len(report["already_in_library"]) == 4
        # Document.title is stored without its file extension (see pipeline.py's title derivation).
        assert all(e["already_in_deck_title"] == "original" for e in report["already_in_library"])
        assert report["repeated_in_this_deck"] == 0

        reps = db.query(QuestionRepeat).filter(QuestionRepeat.document_id == doc2).all()
        assert len(reps) == 4
        assert all(r.deck_title == "reupload" for r in reps)   # the NEW deck where it recurred
    finally:
        db.close()


def test_intra_deck_repeat_counted_but_gets_no_repeat_row():
    # A deck's own PPTX extraction already merges a slide that is literally repeated as a
    # "question run" / "answer reveal run" (see question_extractor.py's intra-deck consolidation),
    # so two truly duplicate slides never even reach deduplicate_batch as separate candidates in a
    # real deck. INTRA_BATCH_EXACT is for the different case this exercises directly: two
    # candidates sharing a question+answer hash that extraction did NOT already merge (e.g. the
    # same question copy-pasted into two different rounds) -- same layer the repo's existing
    # test_review_fixes.py / test_deduplication_threshold.py intra-batch tests exercise.
    db = SessionLocal()
    new_doc_id = str(uuid.uuid4())
    try:
        db.add(Document(id=new_doc_id, filename="x.pptx", title="x", year=2022, file_type="pptx",
                        storage_path="x", file_size_bytes=1, processing_status="PROCESSING"))
        db.commit()
        cand_a = {"question_text": "Same question text?", "answer": "Same Answer", "slide_id": "s1"}
        cand_b = {"question_text": "Same question text?", "answer": "Same Answer", "slide_id": "s2"}
        final_candidates, exact_duplicates, warnings = Deduplicator().deduplicate_batch(
            db=db, candidates=[cand_a, cand_b], candidate_embeddings=[None, None], doc_id=new_doc_id)
        report = build_duplicate_report(deck_title="x", extracted=2, saved=len(final_candidates),
                                        exact_duplicates=exact_duplicates, final_candidates=final_candidates,
                                        provenance_warnings=warnings)

        assert len(final_candidates) == 1          # only one copy kept
        assert report["repeated_in_this_deck"] == 1
        assert report["already_in_library"] == []
        assert report["reconciliation"] == {"extracted": 2, "saved": 1, "dropped": 1}

        reps = db.query(QuestionRepeat).filter(QuestionRepeat.document_id == new_doc_id).count()
        assert reps == 0       # no Repeated flag for a same-deck repeat (owner's decision)
    finally:
        db.close()


# --------------------------------------------------------------------- savepoint resilience (unit)

def _make_existing_question(db, tag):
    doc_id = str(uuid.uuid4())
    db.add(Document(id=doc_id, filename="orig.pptx", title=f"Original {tag}", year=2022,
                    file_type="pptx", storage_path="x", file_size_bytes=1,
                    processing_status="COMPLETED"))
    db.flush()
    q = Question(id=str(uuid.uuid4()), document_id=doc_id, question_text=f"Q {tag}",
                answer=f"A {tag}", topic="Uncategorised", content_hash=f"hash-{tag}")
    db.add(q)
    db.flush()
    return q


def test_one_failed_repeat_write_does_not_lose_the_others(monkeypatch):
    tag = uuid.uuid4().hex[:8]
    db = SessionLocal()
    new_doc_id = str(uuid.uuid4())
    try:
        db.add(Document(id=new_doc_id, filename="new.pptx", title="New Deck", year=2022,
                        file_type="pptx", storage_path="x", file_size_bytes=1,
                        processing_status="PROCESSING"))
        q_good1 = _make_existing_question(db, f"{tag}-good1")
        q_bad = _make_existing_question(db, f"{tag}-bad")
        q_good2 = _make_existing_question(db, f"{tag}-good2")
        db.commit()

        real_add_repeat = question_meta_service.add_repeat

        def flaky_add_repeat(db_, original, doc_id, qmeta, slide_range, match_type="EXACT", actor=None):
            if original.id == q_bad.id:
                raise RuntimeError("simulated write failure")
            return real_add_repeat(db_, original, doc_id, qmeta, slide_range, match_type=match_type, actor=actor)

        monkeypatch.setattr(question_meta_service, "add_repeat", flaky_add_repeat)

        updates = [
            (q_good1, None, {"_meta": {"question_number": 1}, "source_slide_range": "1-2"}),
            (q_bad, None, {"_meta": {"question_number": 2}, "source_slide_range": "3-4"}),
            (q_good2, None, {"_meta": {"question_number": 3}, "source_slide_range": "5-6"}),
        ]
        ok, warnings = Deduplicator()._batch_commit_provenance(db, updates, new_doc_id)

        assert ok == 2
        assert len(warnings) == 1
        assert q_bad.id in warnings[0]

        reps = db.query(QuestionRepeat).filter(QuestionRepeat.document_id == new_doc_id).all()
        assert sorted(r.question_id for r in reps) == sorted([q_good1.id, q_good2.id])
        assert db.query(QuestionRepeat).filter(QuestionRepeat.question_id == q_bad.id).count() == 0

        # The session must still be usable after the mid-batch exception (the savepoint absorbed it).
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
    finally:
        db.close()
