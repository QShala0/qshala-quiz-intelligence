"""A bare place prompt ("make me a quiz on bengaluru") must return questions about the place, never filler.

Regressions covered:
- a deck titled "... Bangalore Campus Edition" made every question in it match "bengaluru" (title was in the BM25 text)
- when nothing cleared the relevance floor, the stray sub-floor hits became the quiz
- "mumbai" / "chennai" contain the topic word "ai", so the bare-place handling never ran
"""
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.prompt_quiz import engine
from backend.app.services.prompt_quiz.engine import (
    Brief, _has_topical_subject, _is_pure_geo_prompt, librarian,
)
from backend.app.services.prompt_quiz.retrieval import Index

CAMPUS_DECK = "Zerodha Varsity Quiz - Bangalore Campus Edition"


def _finance_questions(deck=CAMPUS_DECK):
    rows = [("Which index tracks the top 50 companies listed on the NSE?", "Nifty 50", "Stock Markets"),
            ("What does SIP stand for in mutual fund investing?", "Systematic Investment Plan", "Investing and Funds"),
            ("Which regulator oversees securities markets in India?", "SEBI", "Stock Markets"),
            ("Which insurance cover pays hospital bills?", "Health insurance", "Healthcare Industry")]
    return [{"id": f"fin{i}", "question_text": q, "answer": a, "topic_l1": t, "document_title": deck,
             "india_international": "IN"} for i, (q, a, t) in enumerate(rows)]


def _city_questions():
    return [{"id": "blr1", "question_text": "Which Bengaluru landmark is the seat of the Karnataka legislature?",
             "answer": "Vidhana Soudha", "topic_l1": "Geography", "document_title": "City Trivia Night",
             "india_international": "IN"},
            {"id": "blr2", "question_text": "Which garden city is the capital of Karnataka?", "answer": "Bengaluru",
             "topic_l1": "Geography", "document_title": "City Trivia Night", "india_international": "IN"}]


def _filler(n=20):
    return [{"id": f"oth{i}", "question_text": f"Filler question {i} about cricket", "answer": "x",
             "topic_l1": "Sports", "document_title": "Other deck", "india_international": "IN"} for i in range(n)]


def test_deck_title_does_not_make_unrelated_questions_match_a_place():
    bank = _finance_questions() + _city_questions() + _filler()
    ids = [h["id"] for h in Index(bank).search("bengaluru", {"india_international": "IN"}, top_k=20)]
    assert set(ids) == {"blr1", "blr2"}


def test_quiz_on_a_place_with_no_real_questions_is_empty_not_filler():
    bank = _finance_questions() + _filler()
    brief = Brief(prompt="make me a quiz on bengaluru", query="bengaluru", count=10,
                  filters={"india_international": "IN"})
    assert librarian(bank, brief) == []


def test_weak_matches_are_not_used_to_fill_a_quiz():
    bank = [{"id": f"o{i}", "question_text": f"Question {i} about space {'maharashtra ' * (i % 3)}", "answer": "x",
             "topic_l1": "Science", "document_title": "d"} for i in range(30)]
    brief = Brief(prompt="quiz on mumbai", query="mumbai", count=10)
    assert librarian(bank, brief) == []


def test_padding_still_works_when_the_user_asks_for_related_questions():
    bank = _city_questions() + _filler()
    brief = Brief(prompt="bengaluru, fill the rest", query="bengaluru", count=10, cross_topic_padding=True)
    ids = [h["id"] for h in librarian(bank, brief)]
    assert ids[:2] == ["blr2", "blr1"] or set(ids[:2]) == {"blr1", "blr2"}
    assert len(ids) > 2


@pytest.mark.parametrize("prompt,query,expected", [
    ("make me a quiz on bengaluru", "bengaluru", True),
    ("make me a quiz on mumbai", "mumbai", True),
    ("make me a quiz on chennai", "", True),
    ("i need a quiz on chennai", "i chennai", True),
    ("make me a quiz on bengaluru", "make bengaluru", True),
    ("quiz on bengaluru city trivia", "bengaluru city trivia", True),
    ("make me a quiz on karnataka", "karnataka", True),
    ("startups in bangalore", "bangalore", False),
    ("food in delhi", "delhi", False),
    ("mumbai food and culture 12 questions", "mumbai", False),
    ("fintech in bangalore", "fintech bangalore", False),
    ("air india quiz", "air india", False),
    ("smart cities quiz", "smart cities", False),
])
def test_bare_place_detection(prompt, query, expected):
    assert _is_pure_geo_prompt(prompt, query) is expected


def test_short_topic_words_do_not_match_inside_place_names():
    assert not _has_topical_subject("make me a quiz on mumbai", "mumbai")
    assert not _has_topical_subject("make me a quiz on chennai", "chennai")
    assert _has_topical_subject("quiz on ai and chatbots", "ai and chatbots")


def _full_row(i, row):
    base = {"secondary_l2": [], "keywords": [], "shelf_life": "EVERGREEN", "sensitivity": "NONE", "image_dependent": False,
            "question_images": [], "answer_images": [], "image_refs": [], "search_summary": None, "difficulty": "Medium",
            "difficulty_score": 0.5, "document_id": row["document_title"], "tagged": True, "question_format": "STANDARD_QA",
            "format_payload": None, "city_name": None, "geo_region": None, "primary_audience": None, "subtopic_l2": row["topic_l1"]}
    return {**base, **row}


def _draft(prompt, rows):
    bank = [_full_row(i, r) for i, r in enumerate(rows)]
    with patch.object(engine, "_load_bank", lambda db, *a, **k: [dict(r) for r in bank]), \
         patch("backend.app.config.settings.GEMINI_API_KEY", ""):
        return engine.build_draft(MagicMock(), prompt)


@pytest.mark.parametrize("prompt", ["make me a quiz on bengaluru", "make me a quiz on bangalore"])
def test_bengaluru_quiz_contains_only_bengaluru_questions(prompt):
    draft = _draft(prompt, _finance_questions() + _city_questions() + _filler())
    ids = {q["id"] for q in draft["questions"]}
    assert ids and ids <= {"blr1", "blr2"}, f"unrelated questions in a Bengaluru quiz: {sorted(ids)}"
    assert draft["fulfillment"]["short"] is True


def test_bengaluru_quiz_with_no_city_questions_is_empty_not_finance():
    draft = _draft("make me a quiz on bengaluru", _finance_questions() + _filler())
    assert draft["questions"] == []


def test_request_verbs_do_not_leak_into_the_search_query():
    from backend.app.services.prompt_quiz.retrieval import parse_prompt
    assert parse_prompt("make me a quiz on bengaluru")[0] == "bengaluru"
    assert parse_prompt("build a quiz about mumbai")[0] == "mumbai"
    assert parse_prompt("make 5 questions on space")[0] == "space"


def test_longer_topic_words_still_match_inside_compounds():
    assert _has_topical_subject("fintech in bangalore", "fintech bangalore")
    assert _has_topical_subject("quiz on biotech", "biotech")
