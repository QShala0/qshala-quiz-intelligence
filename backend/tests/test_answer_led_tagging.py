"""Tests for Workstream 1 (answer-led tagging): Connect / Image Reveal / generic-text questions
are classified from their ANSWER instead of their own boilerplate question text.

Covers:
  1. The batch prompt carries the answer-led block + instruction for Connect/Image Reveal/generic
     text, and omits both for ordinary Standard QA.
  2. Routing: an answer-led item with fit=good and no rule signals is confirmed; weak stays draft;
     an ordinary item with fit=good and no rule signals is still draft (unchanged behaviour).
  3. "Connect 4" is stripped from an answer-led item's keywords; the answer is present as a
     deterministic fallback tag when the LLM call fails.
  4. A HUMAN-confirmed tag is never overwritten (regression).
  5. Golden examples: Ratan Tata, Sachin Tendulkar, Dabbawalas, "Owners of Mumbai based sports
     franchise teams".
"""
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.database import SessionLocal
from backend.app.models.document import Document
from backend.app.models.question import Question
from backend.app.models.qshala_tag import QshalaTag
from backend.app.services.tagging import qshala_batch
from backend.app.services.tagging.qshala_rules import is_answer_led, answer_led_fallback_tag
from backend.app.services.tagging.qshala_tagger import _route
import backend.app.services.tagging.qshala_rules as qshala_rules

CONNECT_QUESTION_TEXT = ("[Connect · Common Link]\n"
                         "What common thread or concept connects all four clues and images?")


# --------------------------------------------------------------------- helpers

def _make_question(db, doc_id, *, question_text, answer, question_format="STANDARD_QA",
                   question_images=None, explanation=None, format_payload=None):
    q = Question(id=str(uuid.uuid4()), document_id=doc_id, question_text=question_text,
                answer=answer, topic="Uncategorised", question_format=question_format,
                question_images=question_images or [], explanation=explanation,
                format_payload=format_payload)
    db.add(q)
    db.flush()
    return q


def _cleanup(doc_id):
    db = SessionLocal()
    try:
        db.query(QshalaTag).filter(QshalaTag.question_id.in_(
            [q.id for q in db.query(Question).filter(Question.document_id == doc_id).all()])).delete(
            synchronize_session=False)
        db.query(Question).filter(Question.document_id == doc_id).delete(synchronize_session=False)
        db.query(Document).filter(Document.id == doc_id).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _new_doc(db):
    doc_id = str(uuid.uuid4())
    db.add(Document(id=doc_id, filename="t.pptx", title="t", year=2022, file_type="pptx",
                    storage_path="x", file_size_bytes=1, processing_status="PROCESSING"))
    db.flush()
    return doc_id


class CannedProvider:
    """Returns a fixed per-n result (or a shared default) for every `[n=X]` marker in the prompt."""
    def __init__(self, by_n=None, default=None):
        self.by_n = by_n or {}
        self.default = default
        self.calls = 0
        self.last_prompt = None

    async def generate_json_schema(self, prompt, schema, system_instruction=None, model=None,
                                   thinking_level=None, temperature=0.0):
        import re
        self.calls += 1
        self.last_prompt = prompt
        ns = [int(m) for m in re.findall(r"\[n=(\d+)\]", prompt)]
        results = []
        for n in ns:
            item = dict(self.by_n.get(n) or self.default or _good_item())
            item["n"] = n
            results.append(item)
        return {"results": results}, {"in": 1, "out": 1}


def _good_item(theme="Business", subtopic="Business Leaders", keywords=None, fit="good"):
    return {"topics": [{"theme": theme, "subtopic_l2": subtopic, "keywords": keywords or ["Example"]}],
            "geo_location": "", "india_international": "NA", "relevant_year": 0,
            "fit": fit, "search_summary": "ok"}


# --------------------------------------------------------------------- is_answer_led / fallback tag

def test_is_answer_led_true_for_connect_and_image_reveal_regardless_of_text():
    assert is_answer_led("some arbitrary text of any length here for the test", "CONNECT")
    assert is_answer_led("some arbitrary text of any length here for the test", "CONNECT_4")
    assert is_answer_led("some arbitrary text of any length here for the test", "IMAGE_REVEAL")


def test_is_answer_led_true_for_generic_short_text_with_images():
    assert is_answer_led(CONNECT_QUESTION_TEXT, "STANDARD_QA", question_images=["a.png"]) is False  # >8 words
    assert is_answer_led("What connects these?", "STANDARD_QA", question_images=["a.png"]) is True


def test_is_answer_led_false_without_media_or_known_format():
    assert is_answer_led("What connects these?", "STANDARD_QA") is False       # no images/media
    assert is_answer_led("This is a genuinely long and completely ordinary question text here.",
                         "STANDARD_QA", question_images=["a.png"]) is False    # too many words (11)


def test_answer_led_fallback_tag():
    assert answer_led_fallback_tag("Ratan Tata", "x", "CONNECT_4") == "Ratan Tata"
    assert answer_led_fallback_tag("", "x", "CONNECT_4") is None
    assert answer_led_fallback_tag("a" * 61, "x", "CONNECT_4") is None                  # too long
    assert answer_led_fallback_tag("https://example.com/x", "x", "CONNECT_4") is None   # URL
    assert answer_led_fallback_tag("Ratan Tata", "x", "STANDARD_QA") is None             # not answer-led


def test_clue_hint_coerces_non_string_labels():
    # Regression: production has at least one deck where a clue item's "label" is an int (its
    # order number), which crashed str.join before this was coerced rather than dropped.
    labels, hint = qshala_batch._clue_hint({"items": [{"label": 1}, {"label": "Two"}], "text_hint": "h"})
    assert labels == ["1", "Two"]
    assert hint == "h"


# --------------------------------------------------------------------- prompt content

def test_prompt_marks_answer_led_items_and_adds_instruction():
    items = [
        {"n": 0, "question_text": CONNECT_QUESTION_TEXT, "answer": "Ratan Tata",
         "question_format": "CONNECT_4", "explanation": "Tata Group chairman.",
         "clue_labels": ["Clue 1", "Clue 2"], "text_hint": "", "answer_led": True,
         "rules": qshala_rules.extract(CONNECT_QUESTION_TEXT, "Ratan Tata")},
        {"n": 1, "question_text": "Who wrote the Indian constitution's preamble draft?",
         "answer": "B.R. Ambedkar", "question_format": "STANDARD_QA", "explanation": "",
         "clue_labels": [], "text_hint": "", "answer_led": False,
         "rules": qshala_rules.extract("Who wrote the Indian constitution's preamble draft?", "B.R. Ambedkar")},
    ]
    prompt = qshala_batch._batch_user_prompt(items, [])
    assert qshala_batch.ANSWER_LED_INSTRUCTION in prompt
    assert "[n=0] FORMAT: CONNECT_4 (ANSWER-LED" in prompt
    assert "ANSWER (the subject; the clue pictures are not shown to you): Ratan Tata" in prompt
    assert "CLUE LABELS: Clue 1, Clue 2" in prompt
    assert "[n=1] QUESTION: Who wrote" in prompt
    assert "FORMAT: STANDARD_QA" not in prompt


def test_prompt_has_no_answer_led_block_for_pure_standard_qa_batch():
    items = [
        {"n": 0, "question_text": "Who wrote the Indian constitution's preamble draft?",
         "answer": "B.R. Ambedkar", "question_format": "STANDARD_QA", "explanation": "",
         "clue_labels": [], "text_hint": "", "answer_led": False,
         "rules": qshala_rules.extract("Who wrote the Indian constitution's preamble draft?", "B.R. Ambedkar")},
    ]
    prompt = qshala_batch._batch_user_prompt(items, [])
    assert qshala_batch.ANSWER_LED_INSTRUCTION not in prompt
    assert "ANSWER-LED" not in prompt


def test_prompt_suppresses_image_dependent_hint_for_answer_led_items():
    short_text = "What connects these clues and images?"
    rules = qshala_rules.extract(short_text, "Ratan Tata")
    assert rules.image_dependent    # short (<12-word) text trips the rule regardless of format
    items = [{"n": 0, "question_text": short_text, "answer": "Ratan Tata",
             "question_format": "CONNECT_4", "explanation": "", "clue_labels": [], "text_hint": "",
             "answer_led": True, "rules": rules}]
    prompt = qshala_batch._batch_user_prompt(items, [])
    assert "may depend on an unseen image" not in prompt


# --------------------------------------------------------------------- routing

def test_route_confirms_answer_led_good_fit_with_no_rule_signals():
    rules = qshala_rules.extract(CONNECT_QUESTION_TEXT, "Ratan Tata")
    llm = {"fit": "good", "india_international": "NA", "subtopic_l2": "Business Leaders"}
    status, confidence, reason = _route(rules, llm, answer_led=True)
    assert status == "confirmed"
    assert "answer-led" in reason


def test_route_answer_led_weak_fit_stays_draft():
    rules = qshala_rules.extract(CONNECT_QUESTION_TEXT, "Ratan Tata")
    llm = {"fit": "weak", "india_international": "NA", "subtopic_l2": "Business Leaders"}
    status, _, _ = _route(rules, llm, answer_led=True)
    assert status == "draft"


def test_route_normal_item_good_fit_no_rule_signals_still_drafts():
    # Unchanged existing behaviour: without answer_led, a good-fit item with no rule signals
    # firing still goes to draft for review.
    rules = qshala_rules.extract("A perfectly ordinary longer question nobody has rule hints for.", "An Answer")
    llm = {"fit": "good", "india_international": "NA", "subtopic_l2": "Business Leaders"}
    status, confidence, reason = _route(rules, llm, answer_led=False)
    assert status == "draft"
    assert confidence == 0.70


# --------------------------------------------------------------------- end to end via tag_questions_batched

def test_connect_question_good_fit_confirms_end_to_end():
    db = SessionLocal()
    doc_id = _new_doc(db)
    try:
        q = _make_question(db, doc_id, question_text=CONNECT_QUESTION_TEXT, answer="Ratan Tata",
                           question_format="CONNECT_4")
        provider = CannedProvider(default=_good_item(keywords=["Ratan Tata", "Connect 4", "Tata Group"]))
        asyncio.run(qshala_batch.tag_questions_batched(db, [q], provider=provider, model="fake"))
        db.commit()
        row = db.query(QshalaTag).filter(QshalaTag.question_id == q.id).one()
        assert row.status == "confirmed"
        assert "answer-led" in (row.verification_notes or "")
        # format-label keyword stripped as a safety net
        assert all(k.strip().lower() not in qshala_batch.FORMAT_LABEL_KEYWORDS for k in (row.keywords_l3 or []))
        assert "Ratan Tata" in (row.keywords_l3 or [])
    finally:
        db.close()
        _cleanup(doc_id)


def test_connect_question_weak_fit_stays_draft_end_to_end():
    db = SessionLocal()
    doc_id = _new_doc(db)
    try:
        q = _make_question(db, doc_id, question_text=CONNECT_QUESTION_TEXT, answer="Some Obscure Thing",
                           question_format="CONNECT_4")
        provider = CannedProvider(default=_good_item(fit="weak"))
        asyncio.run(qshala_batch.tag_questions_batched(db, [q], provider=provider, model="fake"))
        db.commit()
        row = db.query(QshalaTag).filter(QshalaTag.question_id == q.id).one()
        assert row.status == "draft"
    finally:
        db.close()
        _cleanup(doc_id)


def test_standard_qa_good_fit_no_rule_signals_still_drafts_end_to_end():
    db = SessionLocal()
    doc_id = _new_doc(db)
    try:
        q = _make_question(db, doc_id,
                           question_text="A perfectly ordinary longer question nobody has rule hints for.",
                           answer="An Answer", question_format="STANDARD_QA")
        provider = CannedProvider(default=_good_item())
        asyncio.run(qshala_batch.tag_questions_batched(db, [q], provider=provider, model="fake"))
        db.commit()
        row = db.query(QshalaTag).filter(QshalaTag.question_id == q.id).one()
        assert row.status == "draft"          # unchanged existing behaviour
    finally:
        db.close()
        _cleanup(doc_id)


def test_answer_present_as_fallback_tag_when_provider_fails():
    """Exercises the deterministic fallback directly (pipeline.py's final_tags block uses the
    same helper); the batch tagger path above covers the LLM-keyword side of the safety net."""
    assert answer_led_fallback_tag("Ratan Tata", CONNECT_QUESTION_TEXT, "CONNECT_4") == "Ratan Tata"


def test_never_overwrites_human_confirmed_tag():
    db = SessionLocal()
    doc_id = _new_doc(db)
    try:
        q = _make_question(db, doc_id, question_text=CONNECT_QUESTION_TEXT, answer="Ratan Tata",
                           question_format="CONNECT_4")
        human_row = QshalaTag(question_id=q.id, taxonomy_version="v6", model_version="human",
                              source="HUMAN", status="confirmed", topic_l1="Business",
                              subtopic_l2="Business Leaders", keywords_l3=["Ratan Tata"], fit="good",
                              confidence=1.0)
        db.add(human_row)
        db.commit()
        provider = CannedProvider(default=_good_item(subtopic="Corporate Scandals"))
        asyncio.run(qshala_batch.tag_questions_batched(db, [q], provider=provider, model="fake"))
        db.commit()
        rows = db.query(QshalaTag).filter(QshalaTag.question_id == q.id).all()
        confirmed = [r for r in rows if r.status == "confirmed"]
        assert len(confirmed) == 1
        assert confirmed[0].source == "HUMAN"
        assert confirmed[0].subtopic_l2 == "Business Leaders"     # untouched by the LLM result
    finally:
        db.close()
        _cleanup(doc_id)


# --------------------------------------------------------------------- golden examples

def test_golden_ratan_tata():
    db = SessionLocal()
    doc_id = _new_doc(db)
    try:
        q = _make_question(db, doc_id, question_text=CONNECT_QUESTION_TEXT, answer="Ratan Tata",
                           question_format="CONNECT_4")
        provider = CannedProvider(default=_good_item(
            theme="Business", subtopic="Business Leaders",
            keywords=["Ratan Tata", "Tata Group", "Tata Sons", "Indian Industrialists", "Indian Conglomerates"]))
        asyncio.run(qshala_batch.tag_questions_batched(db, [q], provider=provider, model="fake"))
        db.commit()
        row = db.query(QshalaTag).filter(QshalaTag.question_id == q.id).one()
        assert row.status == "confirmed"
        assert row.topic_l1 == "Business"
        assert "Ratan Tata" in row.keywords_l3
    finally:
        db.close()
        _cleanup(doc_id)


def test_golden_sachin_tendulkar():
    db = SessionLocal()
    doc_id = _new_doc(db)
    try:
        q = _make_question(db, doc_id, question_text=CONNECT_QUESTION_TEXT, answer="Sachin Tendulkar",
                           question_format="CONNECT_4")
        provider = CannedProvider(default=_good_item(
            theme="Sports", subtopic="Cricket",
            keywords=["Sachin Tendulkar", "Indian Cricket", "Master Blaster"]))
        asyncio.run(qshala_batch.tag_questions_batched(db, [q], provider=provider, model="fake"))
        db.commit()
        row = db.query(QshalaTag).filter(QshalaTag.question_id == q.id).one()
        assert row.status == "confirmed"
        assert row.topic_l1 == "Sports"
    finally:
        db.close()
        _cleanup(doc_id)


def test_golden_dabbawalas():
    db = SessionLocal()
    doc_id = _new_doc(db)
    try:
        q = _make_question(db, doc_id, question_text=CONNECT_QUESTION_TEXT, answer="Dabbawalas",
                           question_format="CONNECT_4")
        provider = CannedProvider(default=_good_item(
            theme="Business", subtopic="Startups",
            keywords=["Dabbawalas", "Mumbai Tiffin Service", "Logistics"]))
        asyncio.run(qshala_batch.tag_questions_batched(db, [q], provider=provider, model="fake"))
        db.commit()
        row = db.query(QshalaTag).filter(QshalaTag.question_id == q.id).one()
        assert row.status == "confirmed"
        assert "Dabbawalas" in row.keywords_l3
    finally:
        db.close()
        _cleanup(doc_id)


def test_golden_mumbai_sports_franchise_owners():
    db = SessionLocal()
    doc_id = _new_doc(db)
    try:
        q = _make_question(db, doc_id, question_text=CONNECT_QUESTION_TEXT,
                           answer="Owners of Mumbai based sports franchise teams",
                           question_format="CONNECT_4")
        provider = CannedProvider(default={
            "topics": [
                {"theme": "Sports", "subtopic_l2": "Sports Business",
                 "keywords": ["Mumbai Indians", "Mumbai City FC", "Franchise Owners"]},
                {"theme": "Geography", "subtopic_l2": "Indian Cities",
                 "keywords": ["Mumbai"]},
            ],
            "geo_location": "Mumbai, India", "india_international": "IN", "relevant_year": 0,
            "fit": "good", "search_summary": "Mumbai sports franchise owners.",
        })
        asyncio.run(qshala_batch.tag_questions_batched(db, [q], provider=provider, model="fake"))
        db.commit()
        row = db.query(QshalaTag).filter(QshalaTag.question_id == q.id).one()
        assert row.status == "confirmed"
        assert row.topic_l1 == "Sports"
    finally:
        db.close()
        _cleanup(doc_id)
