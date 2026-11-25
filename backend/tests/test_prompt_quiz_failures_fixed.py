import pytest
from backend.app.services.prompt_quiz.retrieval import parse_prompt
from backend.app.services.prompt_quiz.engine import _brief_from_regex, Brief, _load_bank
from backend.app.services.source_grouping import resolve_deck_query

def test_deck_scope_ignores_common_prose_and_stopwords():
    docs = [
        {"id": "d-law", "title": "South Zone AmiQuiz Curiae", "filename": "law.pptx"},
        {"id": "d-feat", "title": "22. QH Jr Quirky World Feats", "filename": "quirky.pptx"},
        {"id": "d-sus", "title": "Carmel Moodbidri Sustainability Workshop", "filename": "sus.pptx"},
        {"id": "d-art", "title": "16. QH Jr Art Smart", "filename": "smart.pptx"},
        {"id": "d-acad", "title": "Mitra academy CulminationQuiz", "filename": "acad.pptx"},
        {"id": "d-ag", "title": "Agastya Regionals Set 2 Prelims 2026", "filename": "ag.pptx"},
    ]
    # Prose words that previously hijacked the search MUST NOT match
    assert resolve_deck_query("team offsite with mixed age groups", docs, is_prompt=True)["tokens"] == []
    assert resolve_deck_query("interesting word origins or etymology", docs, is_prompt=True)["tokens"] == []
    assert resolve_deck_query("quick snappy rapid fire questions", docs, is_prompt=True)["tokens"] == []
    assert resolve_deck_query("corporate sustainability and clean energy", docs, is_prompt=True)["tokens"] == []
    assert resolve_deck_query("smart business founders", docs, is_prompt=True)["tokens"] == []
    assert resolve_deck_query("academic knowledge", docs, is_prompt=True)["tokens"] == []

    # Explicit series / cues still match cleanly
    assert resolve_deck_query("questions from aghtya", docs, is_prompt=True)["tokens"] == ["agastya"]
    assert resolve_deck_query("Agastya Set 2 Prelims", docs, is_prompt=True)["tokens"] == ["agastya"]


def test_negated_media_does_not_set_audio_or_video():
    _, f1, _ = parse_prompt("15 questions on Indian food and cinema — no images or video clues please")
    assert f1.get("exclude_image") is True
    assert f1.get("audio_or_video") is not True

    _, f2, _ = parse_prompt("8 rapid fire questions with no audio/video")
    assert f2.get("audio_or_video") is not True

    b = _brief_from_regex("10 questions on pop culture, no visual clues, no video", count=10, previous_brief=None)
    assert b.filters.get("exclude_image") is True or b.filters.get("no_images") is True
    assert b.filters.get("audio_or_video") is not True


def test_audience_does_not_misfire_on_b_school_or_negated_school():
    _, f1, _ = parse_prompt("challenging questions for a b-school crowd")
    assert f1.get("school_safe") is not True

    b1 = _brief_from_regex("challenging questions for a b-school crowd", count=10, previous_brief=None)
    assert b1.filters.get("audience") != "school"

    _, f2, _ = parse_prompt("college finals round, no basic school-level stuff")
    assert f2.get("school_safe") is not True

    b2 = _brief_from_regex("college finals round, no basic school-level stuff", count=10, previous_brief=None)
    assert b2.filters.get("audience") == "college"


def test_year_does_not_leak_on_topic_or_negated_years():
    _, f1, _ = parse_prompt("5 questions on quantum computing breakthroughs in 2026")
    assert f1.get("year") is None

    b1 = _brief_from_regex("5 questions on quantum computing breakthroughs in 2026", count=5, previous_brief=None)
    assert b1.filters.get("year") is None

    _, f2, _ = parse_prompt("5 questions on Indian unicorns founded after 2020")
    assert f2.get("year") is None

    b2 = _brief_from_regex("5 questions on Indian unicorns founded after 2020", count=5, previous_brief=None)
    assert b2.filters.get("year") is None

    _, f3, _ = parse_prompt("10 questions about Indian cinema EXCEPT no recent 2024/2025/2026")
    assert f3.get("year") is None

    # Explicit deck archive year DOES match
    _, f4, _ = parse_prompt("10 questions from 2024 decks")
    assert f4.get("year") == 2024


def test_brief_public_serialization_omits_none_source_deck():
    b = Brief(prompt="test", query="test", count=5)
    pub = b.public()
    assert "source_deck" not in pub
    assert "must_match_criteria" not in pub

    b.source_deck = "Agastya"
    pub2 = b.public()
    assert pub2.get("source_deck") == "Agastya"
