"""GATE 4: migration 0008, the new columns end to end (pipeline -> database -> API), and the review / edit endpoints."""
import asyncio
import copy
import importlib.util
import json
import uuid
from pathlib import Path

import sys

import sqlalchemy as sa

# conftest puts backend/ on sys.path, where the migrations folder `backend/alembic` would shadow the real alembic package
_shadow = [p for p in sys.path if Path(p).resolve() == Path(__file__).resolve().parents[1]]
for _p in _shadow:
    sys.path.remove(_p)
sys.modules.pop("alembic", None)
try:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    HAVE_ALEMBIC = True
except ImportError:          # alembic is in requirements.txt but not in every dev venv
    HAVE_ALEMBIC = False
finally:
    sys.path[:0] = _shadow
import pytest

from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.util import Inches

from backend.app.config import settings
from backend.app.database import SessionLocal
from backend.app.main import app
from backend.app.models.document import Document
from backend.app.models.question import Question
from backend.app.services.ingestion.pipeline import IngestionPipeline, INGESTION_STATUS_REGISTRY

from test_dedup_before_ai import RecordingEmbedder, CountingLLM
from test_v6_pipeline import FakeGemini

client = TestClient(app)
API = "/api/v1"
FIX = Path(__file__).resolve().parent / "fixtures"
MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0008_format_aware_questions.py"


# ------------------------------------------------------------------------------------------ migration

def _load_migration():
    spec = importlib.util.spec_from_file_location("m0008", MIGRATION)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _run(engine, fn):
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            fn()


def _cols(engine):
    return {c["name"]: c for c in sa.inspect(engine).get_columns("questions")}


@pytest.mark.skipif(not HAVE_ALEMBIC, reason="alembic is not installed in this environment")
def test_migration_0008_up_down_and_is_idempotent(tmp_path):
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    with engine.begin() as c:       # a pre-0008 questions table holding data
        c.exec_driver_sql("CREATE TABLE questions (id VARCHAR(36) PRIMARY KEY, question_text TEXT NOT NULL, "
                          "question_format VARCHAR(20) DEFAULT 'STANDARD_QA')")
        c.exec_driver_sql("INSERT INTO questions (id, question_text) VALUES ('a', 'old one'), ('b', 'old two')")
    m = _load_migration()
    assert m.down_revision == "0007_usage_tracking" and m.revision == "0008_format_aware_questions"

    _run(engine, m.upgrade)
    cols = _cols(engine)
    assert {"play_mode", "slot", "topic_label"} <= set(cols)
    assert cols["play_mode"]["nullable"] and cols["topic_label"]["nullable"] and not cols["slot"]["nullable"]
    with engine.connect() as c:     # existing rows default to slot=LIVE, nothing else changes
        rows = c.exec_driver_sql("SELECT id, question_text, slot, play_mode, topic_label FROM questions ORDER BY id").fetchall()
    assert rows == [("a", "old one", "LIVE", None, None), ("b", "old two", "LIVE", None, None)]

    _run(engine, m.upgrade)         # running it twice (boot-time column adds may have run first) is harmless
    assert {"play_mode", "slot", "topic_label"} <= set(_cols(engine))

    _run(engine, m.downgrade)
    after = _cols(engine)
    assert not ({"play_mode", "slot", "topic_label"} & set(after)) and {"id", "question_text", "question_format"} <= set(after)
    with engine.connect() as c:
        assert c.exec_driver_sql("SELECT count(*) FROM questions").scalar() == 2      # data survived the round trip
    _run(engine, m.downgrade)       # and so is downgrading twice


def test_model_and_boot_time_columns_match_the_migration():
    cols = Question.__table__.c
    assert cols.slot.nullable is False and cols.slot.server_default is not None
    assert cols.play_mode.nullable and cols.topic_label.nullable
    # the app's test database (built by ensure_schema_columns / create_all) carries them too
    assert {"play_mode", "slot", "topic_label"} <= {c["name"] for c in sa.inspect(SessionLocal().get_bind()).get_columns("questions")}


# ------------------------------------------------------------------------------------------ pipeline -> db -> API

def _deck(path: Path):
    prs = Presentation()
    blank = prs.slide_layouts[6]

    def add(*texts, hidden=False):
        s = prs.slides.add_slide(blank)
        for i, t in enumerate(texts):
            tb = s.shapes.add_textbox(Inches(1), Inches(1 + i), Inches(8), Inches(1))
            tb.text_frame.text = t
        if hidden:
            s._element.set("show", "0")

    add("QShala format deck")
    add("TOPICATOR\nROUND 1")
    add("2 questions", "+20 points for a correct answer")
    add("Question 1")
    add("SUSTAINABILITY - 1", "Which material takes four hundred and fifty years to decompose in the sea?")
    add("ANSWER")
    add("Plastic Waste")
    add("Question 2")
    add("SUSTAINABILITY - 1", "Which gas do trees take in and use for photosynthesis in the daytime?", hidden=True)
    add("ANSWER", hidden=True)
    add("Carbon Dioxide", hidden=True)
    add("AUDIENCE QUESTION")
    add("Which planet is known as the red planet in our solar system?")
    add("ANSWER")
    add("Mars")
    prs.save(str(path))


def test_pipeline_stores_format_play_mode_slot_and_topic_label():
    dest = settings.UPLOAD_DIR / f"{uuid.uuid4().hex}.pptx"
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _deck(dest)
    doc_id = str(uuid.uuid4())
    db = SessionLocal()
    db.add(Document(id=doc_id, filename="fmt.pptx", title="fmt", year=2025, file_type="pptx", storage_path=str(dest),
                    file_size_bytes=dest.stat().st_size, processing_status="PROCESSING"))
    db.commit()
    db.close()
    pipe = IngestionPipeline()
    pipe.qshala_tagger.provider = FakeGemini()
    pipe.qshala_tagger.rpm = 0
    pipe.embedding_provider = RecordingEmbedder()
    pipe.llm_provider = CountingLLM()
    asyncio.run(pipe.run(doc_id))
    assert INGESTION_STATUS_REGISTRY[doc_id]["status"] == "COMPLETED", INGESTION_STATUS_REGISTRY[doc_id]

    db = SessionLocal()
    try:
        rows = {q.answer: q for q in db.query(Question).filter(Question.document_id == doc_id).all()}
        assert set(rows) == {"Plastic Waste", "Carbon Dioxide", "Mars"}
        live, spare, aud = rows["Plastic Waste"], rows["Carbon Dioxide"], rows["Mars"]
        assert (live.slot, live.play_mode, live.topic_label, live.question_format) == ("LIVE", "PASS", "Sustainability 1", "STANDARD_QA")
        assert (spare.slot, spare.topic_label) == ("RESERVE", "Sustainability 1")        # same label, kept apart
        assert (aud.slot, aud.play_mode) == ("LIVE", "AUDIENCE")
        assert live.format_needs_review is False and live.format_payload["extraction"]["route"] == "accept" and live.format_payload["round_name"] == "TOPICATOR"
        assert live.content_hash != spare.content_hash                                   # topic_label is not a dedupe key
        r = client.get(f"{API}/questions/{live.id}").json()
        assert (r["slot"], r["play_mode"], r["topic_label"]) == ("LIVE", "PASS", "Sustainability 1")
        res = client.get(f"{API}/questions", params={"document_id": doc_id, "slot": "RESERVE"}).json()
        assert [x["answer"] for x in res] == ["Carbon Dioxide"]
        res = client.get(f"{API}/questions", params={"document_id": doc_id, "play_mode": "AUDIENCE"}).json()
        assert [x["answer"] for x in res] == ["Mars"]
    finally:
        db.query(Question).filter(Question.document_id == doc_id).delete(synchronize_session=False)
        db.query(Document).filter(Document.id == doc_id).delete(synchronize_session=False)
        db.commit()
        db.close()


# ------------------------------------------------------------------------------------------ review + edit endpoints

def _arrange_payload(order_known=False):
    from backend.app.services.ingestion.question_extractor import QuestionExtractor
    slides = json.loads((FIX / "arrange_reserve.json").read_text(encoding="utf-8"))
    qs = {q["slide_number"]: q for q in QuestionExtractor().extract_from_slides(slides, "t", 2026)}
    return qs[3 if order_known else 132]


def _store(extracted, well_tagged=True, **over):
    doc_id, q_id = str(uuid.uuid4()), str(uuid.uuid4())
    db = SessionLocal()
    db.add(Document(id=doc_id, filename="a.pptx", title="a", year=2025, file_type="pptx", storage_path="x",
                    file_size_bytes=1, processing_status="COMPLETED"))
    db.add(Question(id=q_id, document_id=doc_id, question_text=extracted["question_text"], answer=extracted["answer"],
                    topic="General", question_format=extracted["question_format"], format_payload=extracted["format_payload"],
                    play_mode=extracted["play_mode"], slot=extracted["slot"],
                    format_needs_review=bool(extracted["_ingest"]["needs_review"]) if "_ingest" in extracted else False, **over))
    if well_tagged:      # a person already confirmed its tags, so only the FORMAT flag can keep it in the queue
        from backend.app.models.qshala_tag import QshalaTag
        db.add(QshalaTag(question_id=q_id, taxonomy_version="v6", source="HUMAN", status="confirmed", topic_l1="General",
                         subtopic_l2="Trivia", fit="good", confidence=0.95, verified_by="quizmaster"))
    db.commit()
    db.close()
    return doc_id, q_id


def _drop(doc_id, q_id):
    db = SessionLocal()
    try:
        from backend.app.models.qshala_tag import QshalaTag
        db.query(QshalaTag).filter(QshalaTag.question_id == q_id).delete(synchronize_session=False)
        db.query(Question).filter(Question.id == q_id).delete(synchronize_session=False)
        db.query(Document).filter(Document.id == doc_id).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_flagged_format_reading_joins_the_needs_review_queue_and_leaves_when_confirmed():
    ex = _arrange_payload()
    assert ex["_ingest"]["needs_review"]
    d, q = _store(ex)
    try:
        ids = {r["id"] for r in client.get(f"{API}/questions/needs-review", params={"limit": 200}).json()}
        assert q in ids and client.get(f"{API}/questions/{q}").json()["format_needs_review"] is True
        n0 = client.get(f"{API}/questions/needs-review/count").json()["count"]
        r = client.post(f"{API}/questions/{q}/accept-format-review")
        assert r.status_code == 200
        ext = r.json()["format_payload"]["extraction"]
        assert ext["needs_review"] is False and ext["reviewed"] is True and r.json()["format_needs_review"] is False
        ids = {x["id"] for x in client.get(f"{API}/questions/needs-review", params={"limit": 200}).json()}
        assert q not in ids and client.get(f"{API}/questions/needs-review/count").json()["count"] == n0 - 1
    finally:
        _drop(d, q)


def test_arrange_order_can_be_entered_by_hand():
    ex = _arrange_payload()
    assert ex["format_payload"]["order_source"] == "none" and ex["format_payload"]["correct_order"] == []
    d, q = _store(ex)
    try:
        url = f"{API}/questions/{q}/arrange-order"
        assert client.put(url, json={"correct_order": [1, 2, 3]}).status_code == 400          # not every label
        assert client.put(url, json={"correct_order": [1, 1, 2, 3]}).status_code == 400
        r = client.put(url, json={"correct_order": [4, 3, 2, 1]})
        assert r.status_code == 200
        body = r.json()
        p = body["format_payload"]
        assert p["correct_order"] == [4, 3, 2, 1] and p["order_source"] == "human"
        assert p["extraction"]["needs_review"] is False and p["extraction"]["reviewed"] is True
        assert body["format_needs_review"] is False
        assert body["answer"] == "D → C → B → A"
        ids = {x["id"] for x in client.get(f"{API}/questions/needs-review", params={"limit": 200}).json()}
        assert q not in ids
        r = client.put(url, json={"correct_order": []})                                        # clearing sends it back to review
        assert r.json()["format_payload"]["order_source"] == "none" and r.json()["format_payload"]["extraction"]["needs_review"]
        assert r.json()["format_needs_review"] is True
    finally:
        _drop(d, q)


def test_arrange_order_endpoint_rejects_other_formats_and_computed_orders_stay_computed():
    d, q = _store(_arrange_payload(order_known=True))
    try:
        assert client.get(f"{API}/questions/{q}").json()["format_payload"]["order_source"] == "computed"
    finally:
        _drop(d, q)
    d, q = _store({"question_text": "Who?", "answer": "A", "question_format": "STANDARD_QA", "format_payload": {"type": "STANDARD_QA"},
                   "play_mode": None, "slot": "LIVE"})
    try:
        assert client.put(f"{API}/questions/{q}/arrange-order", json={"correct_order": [1, 2, 3, 4]}).status_code == 400
        assert client.patch(f"{API}/questions/{q}", json={"slot": "SPARE"}).status_code == 400
        assert client.patch(f"{API}/questions/{q}", json={"play_mode": "NOPE"}).status_code == 400
        r = client.patch(f"{API}/questions/{q}", json={"slot": "reserve", "play_mode": "buffer"})
        assert r.status_code == 200 and (r.json()["slot"], r.json()["play_mode"]) == ("RESERVE", "BUFFER")
    finally:
        _drop(d, q)


def test_rows_without_the_new_columns_set_read_as_live():
    doc_id, q_id = str(uuid.uuid4()), str(uuid.uuid4())
    db = SessionLocal()
    db.add(Document(id=doc_id, filename="o.pptx", title="o", year=2020, file_type="pptx", storage_path="x",
                    file_size_bytes=1, processing_status="COMPLETED"))
    db.add(Question(id=q_id, document_id=doc_id, question_text="Old?", answer="Yes", topic="General"))     # an old-style row
    db.commit()
    db.close()
    try:
        r = client.get(f"{API}/questions/{q_id}").json()
        assert r["slot"] == "LIVE" and r["play_mode"] is None and r["topic_label"] is None
    finally:
        _drop(doc_id, q_id)


MIGRATION_9 = MIGRATION.parent / "0009_format_needs_review_flag.py"


@pytest.mark.skipif(not HAVE_ALEMBIC, reason="alembic is not installed in this environment")
def test_migration_0009_adds_the_review_flag_defaulting_to_false(tmp_path):
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'm9.db').as_posix()}")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE questions (id VARCHAR(36) PRIMARY KEY, question_text TEXT NOT NULL)")
        c.exec_driver_sql("INSERT INTO questions (id, question_text) VALUES ('a', 'x')")
    spec = importlib.util.spec_from_file_location("m0009", MIGRATION_9)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.down_revision == "0008_format_aware_questions"
    _run(engine, m.upgrade)
    assert not _cols(engine)["format_needs_review"]["nullable"]
    with engine.connect() as c:
        assert c.exec_driver_sql("SELECT format_needs_review FROM questions").scalar() in (0, False)
    _run(engine, m.upgrade)                                   # idempotent
    _run(engine, m.downgrade)
    assert "format_needs_review" not in _cols(engine)
    with engine.connect() as c:
        assert c.exec_driver_sql("SELECT count(*) FROM questions").scalar() == 1


def test_startup_healer_leaves_format_aware_questions_alone():
    """main.py runs heal_specialized_questions on every boot. It rewrites old-style Connect / Stepped rows into the
    legacy payload shapes; it must not touch a row read by format-aware ingestion."""
    from backend.app.services.ingestion.heal_specialized import heal_specialized_questions
    from backend.app.services.ingestion.question_extractor import QuestionExtractor
    slides = {n: json.loads((FIX / f"{n}.json").read_text(encoding="utf-8")) for n in ("dsu_mini", "three_clues_reserve")}
    qs = {}
    for name, sl in slides.items():
        qs.update({(name, q["slide_number"]): q for q in QuestionExtractor().extract_from_slides(sl, "t", 2026)})
    connect, stepped = qs[("dsu_mini", 140)], qs[("three_clues_reserve", 3)]
    assert connect["question_format"] == "CONNECT" and stepped["question_format"] == "STEPPED_CLUE"
    d1, q1 = _store(connect)
    d2, q2 = _store(stepped)
    try:
        before = {i: (client.get(f"{API}/questions/{i}").json()) for i in (q1, q2)}
        db = SessionLocal()
        try:
            heal_specialized_questions(db)
        finally:
            db.close()
        for i in (q1, q2):
            after = client.get(f"{API}/questions/{i}").json()
            for key in ("question_text", "question_format", "question_type", "format_payload"):
                assert after[key] == before[i][key], (i, key)
        assert client.get(f"{API}/questions/{q1}").json()["question_format"] == "CONNECT"
    finally:
        _drop(d1, q1)
        _drop(d2, q2)
