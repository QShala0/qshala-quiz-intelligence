"""M4: ingestion writes metadata rows, topic paths and tag mirrors in a few batches instead of per question, with the
same result as the one-by-one helpers."""
import sys
import uuid
from pathlib import Path

from sqlalchemy import event

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.database import SessionLocal, engine
from backend.app.models.document import Document
from backend.app.models.qshala_tag import QshalaQuestionMetadata, QshalaTag
from backend.app.models.question import Question
from backend.app.models.question_meta import QuestionTopicPath
from backend.app.services import question_meta_service as qms
from backend.app.services.tagging import topic_paths

import test_dedup_before_ai as dedup_helpers


def _doc(db, tag):
    doc = Document(id=str(uuid.uuid4()), filename=f"{tag}.pptx", title=tag, file_type="pptx", storage_path="x",
                   file_size_bytes=1, processing_status="PROCESSING", year=2025)
    db.add(doc)
    db.flush()
    return doc


def _questions(db, doc, n, tag):
    out = []
    for i in range(n):
        q = Question(id=str(uuid.uuid4()), content_hash=f"{tag}-{i}", document_id=doc.id, question_text=f"q{i}",
                     answer=f"a{i}", topic="Science", subtopic="Physics", tags=["alpha", f"hash{i}"])
        db.add(q)
        out.append(q)
    db.flush()
    return out


def _meta_snapshot(row):
    cols = [c.name for c in QshalaQuestionMetadata.__table__.columns if c.name not in ("question_id", "id", "created_at", "updated_at")]
    return {c: getattr(row, c) for c in cols}


def test_bulk_metadata_and_paths_match_the_one_by_one_helpers():
    tag = uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        doc = _doc(db, tag)
        slow = _questions(db, doc, 3, f"{tag}s")
        fast = _questions(db, doc, 3, f"{tag}f")
        qmetas = [{"question_number": i + 1, "round_number": 2, "question_format": "STEPPED_CLUE", "star_marked": i == 0}
                  for i in range(3)]
        deck = qms.upsert_deck_metadata(db, doc)

        for q, m in zip(slow, qmetas):
            qms.ensure_question_metadata(db, q, m, deck, doc)
            topic_paths.replace_paths(db, q.id, [{"l1": q.topic, "l2": q.subtopic, "keywords": q.tags or []}],
                                      source="RULES", record_history=False, question=q)
        qms.add_new_question_metadata(db, list(zip(fast, qmetas)), deck, doc)
        for q in fast:
            topic_paths.add_new_question_paths(db, q, [{"l1": q.topic, "l2": q.subtopic, "keywords": q.tags or []}], "RULES")
        db.flush()

        for s, f in zip(slow, fast):
            ms = db.query(QshalaQuestionMetadata).filter_by(question_id=s.id).one()
            mf = db.query(QshalaQuestionMetadata).filter_by(question_id=f.id).one()
            assert _meta_snapshot(ms) == _meta_snapshot(mf)
            ps = [(p["l1"], p["l2"], p["keywords"], p["source"], p["is_custom"], p["position"]) for p in topic_paths.get_paths(db, s.id)]
            pf = [(p["l1"], p["l2"], p["keywords"], p["source"], p["is_custom"], p["position"]) for p in topic_paths.get_paths(db, f.id)]
            assert ps == pf and ps
            assert (s.topic, s.subtopic, s.topics, s.tags) == (f.topic, f.subtopic, f.topics, f.tags)
    finally:
        db.rollback()
        db.close()


def test_tag_mirror_uses_the_newest_tag_and_respects_hand_edits():
    tag = uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        doc = _doc(db, tag)
        qs = _questions(db, doc, 3, tag)
        deck = qms.upsert_deck_metadata(db, doc)
        qms.add_new_question_metadata(db, [(q, {}) for q in qs], deck, doc)
        db.flush()
        # question 0: two tags, the newer one wins; question 1: geography was set by hand; question 2: no tag at all
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        for q, geo, intl, year, age in [(qs[0], "Old", "IN", 1999, 10), (qs[0], "New", "INTL", 2024, 1), (qs[1], "Tagged", "IN", 2020, 1)]:
            db.add(QshalaTag(question_id=q.id, source="LLM", geo_region=geo, india_international=intl, relevant_year=year,
                             created_at=now - timedelta(minutes=age)))
        db.flush()
        db.query(QshalaQuestionMetadata).filter_by(question_id=qs[1].id).one().manual_fields = ["geo_region"]
        db.query(QshalaQuestionMetadata).filter_by(question_id=qs[1].id).one().geo_region = "Hand"
        db.flush()

        n = qms.apply_latest_tags_to_meta(db, [q.id for q in qs])

        metas = {q.id: db.query(QshalaQuestionMetadata).filter_by(question_id=q.id).one() for q in qs}
        assert n == 2
        assert (metas[qs[0].id].geo_region, metas[qs[0].id].india_international, metas[qs[0].id].relevant_year) == ("New", "INTL", 2024)
        assert metas[qs[1].id].geo_region == "Hand" and metas[qs[1].id].india_international == "IN"
        assert metas[qs[2].id].geo_region is None
    finally:
        db.rollback()
        db.close()


def _remove_deck(doc_id):
    """The test database is shared by the whole session: leave no deck behind for tests that count questions."""
    from backend.app.models.document import Slide
    from backend.app.models.question_meta import QuestionEditHistory
    db = SessionLocal()
    try:
        ids = [r[0] for r in db.query(Question.id).filter(Question.document_id == doc_id).all()]
        if ids:
            for model in (QuestionTopicPath, QshalaQuestionMetadata, QshalaTag, QuestionEditHistory):
                db.query(model).filter(model.question_id.in_(ids)).delete(synchronize_session=False)
            db.query(Question).filter(Question.id.in_(ids)).delete(synchronize_session=False)
        db.query(Slide).filter(Slide.document_id == doc_id).delete(synchronize_session=False)
        db.query(Document).filter(Document.id == doc_id).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_persisting_a_deck_does_not_scale_its_queries_with_the_question_count():
    def count_statements(n_questions):
        tag = uuid.uuid4().hex[:8]
        seen = []

        def on_exec(conn, cursor, statement, params, context, executemany):
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", on_exec)
        try:
            doc_id = dedup_helpers._ingest(dedup_helpers._items(tag, list(range(1, n_questions + 1))),
                                           dedup_helpers.RecordingEmbedder(), name=f"bulk-{tag}.pptx")
        finally:
            event.remove(engine, "before_cursor_execute", on_exec)
        _remove_deck(doc_id)
        return len(seen)

    small, large = count_statements(3), count_statements(30)
    # Before: roughly six statements per question, so 27 more questions cost 150+ more. Now: a few batches.
    assert large - small < 40, (small, large)
