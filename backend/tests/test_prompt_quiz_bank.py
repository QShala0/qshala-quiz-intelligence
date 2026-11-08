"""Prompt-quiz bank loading (audit M6): same bank as before, loaded more cheaply."""
import random
import uuid
from datetime import datetime, timedelta, timezone

from backend.app.database import SessionLocal
from backend.app.models.document import Document
from backend.app.models.question import Question
from backend.app.models.qshala_tag import QshalaTag
from backend.app.services.prompt_quiz import engine


def _old_guardian(hits, exclude_ids=None):
    """The pre-M6 implementation, kept here as the reference."""
    out, seen = [], []
    for h in hits:
        if exclude_ids and h["id"] in exclude_ids:
            continue
        fp = engine._fp(h["question_text"])
        if any(fp and len(fp & s) / max(len(fp | s), 1) > 0.7 for s in seen):
            continue
        seen.append(fp)
        out.append(h)
    return out


def test_guardian_keeps_exactly_the_same_questions_as_the_all_pairs_version():
    rnd = random.Random(7)
    words = [f"w{i}" for i in range(40)]
    hits = []
    for i in range(400):
        base = rnd.sample(words, rnd.randint(0, 14))
        if i and rnd.random() < 0.4:                       # near-copies of an earlier question
            base = engine.tokenize(hits[rnd.randrange(len(hits))]["question_text"])[:14]
            if base and rnd.random() < 0.5:
                base[rnd.randrange(len(base))] = "zzz"
        hits.append({"id": f"id{i}", "question_text": " ".join(base)})
    exclude = {f"id{i}" for i in range(0, 400, 17)}
    new = engine.guardian(hits, None, exclude_ids=exclude)      # the brief is not used by the guardian
    assert [h["id"] for h in new] == [h["id"] for h in _old_guardian(hits, exclude)]
    assert len(new) < len(hits)                            # the fixture really does contain near-duplicates


def _document(db):
    d = Document(id=str(uuid.uuid4()), filename="bank.pptx", title="bank", file_type="pptx", storage_path="x",
                 file_size_bytes=1, processing_status="COMPLETED")
    db.add(d)
    db.flush()
    return d


def _question(db, text, doc_id):
    q = Question(id=str(uuid.uuid4()), document_id=doc_id, question_text=text, answer="a", topic="General",
                 content_hash=uuid.uuid4().hex)
    db.add(q)
    db.flush()
    return q


def _tag(db, qid, status, l1, minutes):
    db.add(QshalaTag(question_id=qid, taxonomy_version="v6", source="LLM", status=status, topic_l1=l1,
                     created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes)))


def test_latest_tag_selection_matches_the_old_python_rule():
    db = SessionLocal()
    doc = _document(db)
    try:
        q_conf = _question(db, f"confirmed beats newer draft {uuid.uuid4()}?", doc.id)
        q_two = _question(db, f"newest of two confirmed {uuid.uuid4()}?", doc.id)
        q_draft = _question(db, f"only drafts newest wins {uuid.uuid4()}?", doc.id)
        q_none = _question(db, f"superseded only {uuid.uuid4()}?", doc.id)
        _tag(db, q_conf.id, "confirmed", "Conf", 30)
        _tag(db, q_conf.id, "draft", "NewerDraft", 5)
        _tag(db, q_two.id, "confirmed", "OldConf", 60)
        _tag(db, q_two.id, "confirmed", "NewConf", 10)
        _tag(db, q_draft.id, "draft", "OldDraft", 50)
        _tag(db, q_draft.id, "draft", "NewDraft", 20)
        _tag(db, q_none.id, "superseded", "Gone", 20)
        db.commit()
        got = engine._latest_tags(db)
        assert got[q_conf.id].topic_l1 == "Conf"
        assert got[q_two.id].topic_l1 == "NewConf"
        assert got[q_draft.id].topic_l1 == "NewDraft"
        assert q_none.id not in got
        # and the whole bank agrees
        bank = {b["id"]: b for b in engine._load_bank_uncached(db)}
        assert bank[q_conf.id]["topic_l1"] == "Conf" and bank[q_conf.id]["tagged"] is True
        assert bank[q_none.id]["tagged"] is False
    finally:
        db.rollback()
        qids = [q_conf.id, q_two.id, q_draft.id, q_none.id]
        db.query(QshalaTag).filter(QshalaTag.question_id.in_(qids)).delete(synchronize_session=False)
        db.query(Question).filter(Question.document_id == doc.id).delete(synchronize_session=False)
        db.query(Document).filter(Document.id == doc.id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_bank_cache_reuses_one_load_within_the_ttl_and_hands_out_copies(monkeypatch):
    monkeypatch.setenv("PROMPT_QUIZ_BANK_TTL_S", "60")
    engine.invalidate_bank_cache()
    calls = []
    real = engine._load_bank_uncached

    def counting(db, slots=None):
        calls.append(1)
        return [{"id": "x", "question_text": "t"}]

    monkeypatch.setattr(engine, "_load_bank_uncached", counting)
    db = SessionLocal()
    try:
        a = engine._load_bank(db)
        a[0]["score"] = 9.9                                # a caller scribbling on its copy ...
        b = engine._load_bank(db)
        assert len(calls) == 1 and "score" not in b[0]     # ... does not leak into the next request
        monkeypatch.setenv("PROMPT_QUIZ_BANK_TTL_S", "0")
        engine._load_bank(db)
        assert len(calls) == 2                             # off -> always reloads
        monkeypatch.setenv("PROMPT_QUIZ_BANK_TTL_S", "60")
        engine.invalidate_bank_cache()
        engine._load_bank(db)
        assert len(calls) == 3
    finally:
        engine.invalidate_bank_cache()
        monkeypatch.setattr(engine, "_load_bank_uncached", real)
        db.close()


def test_bank_query_does_not_fetch_the_embedding_column():
    from sqlalchemy import event
    from backend.app.database import engine as db_engine
    seen = []

    def grab(conn, cursor, statement, *a):
        if "FROM questions" in statement:
            seen.append(statement)

    event.listen(db_engine, "before_cursor_execute", grab)
    db = SessionLocal()
    try:
        engine._load_bank_uncached(db)
    finally:
        event.remove(db_engine, "before_cursor_execute", grab)
        db.close()
    assert seen and all("questions.embedding," not in s and "questions.embedding " not in s.replace("embedding_model", "")
                        for s in seen)
