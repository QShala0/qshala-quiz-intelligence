import pytest
from unittest.mock import MagicMock
import httpx

from backend.app.services.prompt_quiz import engine, interpreter_llm


def test_ask_20_from_bank_of_10_on_topic_no_padding(monkeypatch):
    sports_samples = [
        ("Cricket", "Who won the first ICC Cricket World Cup in 1975 at Lords?"),
        ("Tennis", "Which player has won the most Wimbledon gentlemen singles titles in history?"),
        ("Football", "Which country hosted the 2014 FIFA World Cup tournament?"),
        ("Hockey", "How many Olympic gold medals has the Indian hockey team won?"),
        ("Badminton", "Who was the first Indian woman badminton player to achieve World No 1 ranking?"),
        ("Swimming", "Michael Phelps holds the record for the most Olympic gold medals won in swimming."),
        ("Athletics", "Usain Bolt set the world record in the 100 metres sprint in Berlin."),
        ("Chess", "Viswanathan Anand became India's first Grandmaster in the year 1988."),
        ("Golf", "Tiger Woods won his first Masters Tournament at Augusta National in 1997."),
        ("Boxing", "Mary Kom won six world championship titles in women amateur boxing."),
    ]
    # 10 distinct Sports questions
    fake_bank = []
    for i, (sub, text) in enumerate(sports_samples):
        fake_bank.append({
            "id": f"sports_{i}",
            "question_text": text,
            "answer": f"Answer {i}",
            "topic_l1": "Sports",
            "subtopic_l2": sub,
            "secondary_l2": [],
            "keywords": [sub],
            "india_international": "IN",
            "shelf_life": "EVERGREEN",
            "sensitivity": "NONE",
            "image_dependent": False,
            "question_images": [],
            "answer_images": [],
            "image_refs": [],
            "search_summary": "",
            "difficulty": "Medium",
            "difficulty_score": 0.5,
            "document_id": "doc1",
            "tagged": True,
            "question_format": "STANDARD_QA",
            "city_name": None,
            "geo_region": None,
            "primary_audience": None,
        })
    # 30 distinct Entertainment questions
    for i in range(30):
        fake_bank.append({
            "id": f"movies_{i}",
            "question_text": f"Film number {i}: Which director created the cinematic masterpiece feature {i*7+3}?",
            "answer": f"Director {i}",
            "topic_l1": "Entertainment",
            "subtopic_l2": "Cinema",
            "secondary_l2": [],
            "keywords": ["Cinema"],
            "india_international": "IN",
            "shelf_life": "EVERGREEN",
            "sensitivity": "NONE",
            "image_dependent": False,
            "question_images": [],
            "answer_images": [],
            "image_refs": [],
            "search_summary": "",
            "difficulty": "Medium",
            "difficulty_score": 0.5,
            "document_id": "doc1",
            "tagged": True,
            "question_format": "STANDARD_QA",
            "city_name": None,
            "geo_region": None,
            "primary_audience": None,
        })

    monkeypatch.setattr(engine, "_load_bank", lambda db, slots=None: fake_bank)

    mock_db = MagicMock()
    res = engine.build_draft(mock_db, prompt="20 questions on sports", count=20)
    ful = res["fulfillment"]
    assert ful["delivered"] == 10
    assert ful["short"] is True
    assert any(s.get("action") == "add_adjacent" for s in ful["suggestions"])
    # No off-topic question appears when padding is off
    assert len(res["questions"]) == 10
    assert all(q["topic_l1"] == "Sports" for q in res["questions"])


def test_previous_brief_locality_preserved_make_it_harder():
    prev_brief = {
        "prompt": "10 questions on Bangalore startups",
        "query": "startups",
        "count": 10,
        "filters": {"india_focused": True},
        "locality": "Bengaluru",
        "hard_bias": None,
    }

    b = engine.interpret("make it harder", count=None, previous_brief=prev_brief)
    assert b.locality == "Bengaluru"
    assert b.hard_bias == "hard"


def test_gemini_failure_falls_back_to_regex(monkeypatch):
    def raise_err(*args, **kwargs):
        raise httpx.ConnectTimeout("Connection timeout")

    monkeypatch.setattr(httpx.Client, "post", raise_err)

    # interpret should fall back to regex
    b = engine.interpret("10 questions on banking")
    assert b.source == "regex"
    assert b.count == 10

    # And build_draft still returns successfully
    mock_db = MagicMock()
    fake_bank = [{
        "id": "q1",
        "question_text": "Banking question 1 about loans and interest",
        "answer": "Bank",
        "topic_l1": "Finance",
        "subtopic_l2": "Banking",
        "secondary_l2": [],
        "keywords": ["Banking"],
        "india_international": "IN",
        "shelf_life": "EVERGREEN",
        "sensitivity": "NONE",
        "image_dependent": False,
        "question_images": [],
        "answer_images": [],
        "image_refs": [],
        "search_summary": "",
        "difficulty": "Medium",
        "difficulty_score": 0.5,
        "document_id": "doc1",
        "tagged": True,
        "question_format": "STANDARD_QA",
        "city_name": None,
        "geo_region": None,
        "primary_audience": None,
    }]
    monkeypatch.setattr(engine, "_load_bank", lambda db, slots=None: fake_bank)
    res = engine.build_draft(mock_db, prompt="10 questions on banking", count=10)
    assert "brief" in res
    assert "questions" in res
    assert "fulfillment" in res
