"""Automated tests for Natural Language Database Querying (NL-DBQ) and Archive Intelligence.

Covers the 21-use-case matrix approved in prompt_database_interaction_plan.md:
1. Frequency & Repeats ("most asked questions across zvq", "never repeated in 2024")
2. Competition Stages ("prelims questions", "hardest questions across zvq finals")
3. Play Modes ("buzzer round questions", "tiebreakers")
4. Editorial Flags & Curiosity ("starred questions", "curiosity-driven")
5. Multi-Modal Media ("questions with audio or video")
6. Hybrid Thematic Queries ("most asked tech questions across zvq")
7. Provenance Payload Verification (_view carries provenance fields)
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
from backend.app.config import settings
from backend.app.services.prompt_quiz import engine
from backend.app.services.prompt_quiz.retrieval import parse_prompt, Index
from backend.app.services.prompt_quiz.audience import passes_hard


@pytest.fixture(autouse=True)
def _no_gemini(monkeypatch):
    """Force deterministic regex & local execution."""
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "", raising=False)
    monkeypatch.setenv("ENABLE_AI_CURATOR", "false")
    yield


def _make_archive_bank():
    """Generates a rich, heterogeneous bank representing the ZVQ and national quiz archive."""
    bank = [
        # ZVQ Questions
        {
            "id": "zvq-1",
            "question_text": "Which company founded by Narayana Murthy was the first Indian firm listed on NASDAQ?",
            "answer": "Infosys",
            "topic_l1": "Business & Startups",
            "subtopic_l2": "Indian IT",
            "keywords": ["infosys", "nasdaq", "narayana murthy", "tech"],
            "difficulty": "Easy",
            "difficulty_score": 0.25,
            "document_id": "doc-zvq-1",
            "document_title": "ZVQ 2023 National Finals",
            "document_filename": "zvq_2023_finals.pptx",
            "is_varsity": True,
            "round_stage": "finals",
            "play_mode": "DIRECTED",
            "occurrence_count": 8,
            "repeat_count": 7,
            "star_marked": True,
            "source_year": 2023,
            "curiosity_score": 8,
            "has_audio": False,
            "has_video": False,
            "image_dependent": False,
            "tagged": True,
        },
        {
            "id": "zvq-2",
            "question_text": "What semiconductor architecture design company was acquired by SoftBank in 2016 for $32 billion?",
            "answer": "ARM Holdings",
            "topic_l1": "Technology & Space",
            "subtopic_l2": "Computing & Chips",
            "keywords": ["arm", "softbank", "semiconductors", "tech", "chips"],
            "difficulty": "Hard",
            "difficulty_score": 0.88,
            "document_id": "doc-zvq-1",
            "document_title": "ZVQ 2023 National Finals",
            "document_filename": "zvq_2023_finals.pptx",
            "is_varsity": True,
            "round_stage": "finals",
            "play_mode": "BUZZER",
            "occurrence_count": 5,
            "repeat_count": 4,
            "star_marked": True,
            "source_year": 2023,
            "curiosity_score": 9,
            "has_audio": False,
            "has_video": False,
            "image_dependent": False,
            "tagged": True,
        },
        {
            "id": "zvq-3",
            "question_text": "Which Indian ecommerce unicorn was founded in 2007 by former Amazon employees Sachin Bansal and Binny Bansal?",
            "answer": "Flipkart",
            "topic_l1": "Business & Startups",
            "subtopic_l2": "E-Commerce",
            "keywords": ["flipkart", "bansal", "ecommerce", "startups"],
            "difficulty": "Easy",
            "difficulty_score": 0.30,
            "document_id": "doc-zvq-2",
            "document_title": "ZVQ 2024 College Prelims",
            "document_filename": "zvq_2024_prelims.pptx",
            "is_varsity": True,
            "round_stage": "prelims",
            "play_mode": "DIRECTED",
            "occurrence_count": 12,
            "repeat_count": 11,
            "star_marked": False,
            "source_year": 2024,
            "curiosity_score": 6,
            "has_audio": False,
            "has_video": False,
            "image_dependent": False,
            "tagged": True,
        },
        {
            "id": "zvq-4",
            "question_text": "Identify the voice in this famous 1969 Apollo 11 audio transmission declaring 'That's one small step for man'.",
            "answer": "Neil Armstrong",
            "topic_l1": "Technology & Space",
            "subtopic_l2": "Space Missions",
            "keywords": ["apollo", "armstrong", "space", "moon", "audio"],
            "difficulty": "Medium",
            "difficulty_score": 0.50,
            "document_id": "doc-zvq-2",
            "document_title": "ZVQ 2024 College Prelims",
            "document_filename": "zvq_2024_prelims.pptx",
            "is_varsity": True,
            "round_stage": "prelims",
            "play_mode": "BUZZER",
            "occurrence_count": 2,
            "repeat_count": 1,
            "star_marked": True,
            "source_year": 2024,
            "curiosity_score": 10,
            "has_audio": True,
            "has_video": False,
            "image_dependent": True,
            "tagged": True,
        },
        {
            "id": "zvq-5",
            "question_text": "Which fast-payment protocol developed by NPCI processed over 100 billion transactions in India in 2023?",
            "answer": "UPI (Unified Payments Interface)",
            "topic_l1": "Business & Startups",
            "subtopic_l2": "Fintech",
            "keywords": ["upi", "npci", "fintech", "payments", "tech"],
            "difficulty": "Medium",
            "difficulty_score": 0.40,
            "document_id": "doc-zvq-3",
            "document_title": "Zerodha Varsity 2024 Fresh Quiz",
            "document_filename": "zvq_2024_fresh.pptx",
            "is_varsity": True,
            "round_stage": "prelims",
            "play_mode": "DIRECTED",
            "occurrence_count": 1,
            "repeat_count": 0,
            "star_marked": False,
            "source_year": 2024,
            "curiosity_score": 7,
            "has_audio": False,
            "has_video": False,
            "image_dependent": False,
            "tagged": True,
        },
        {
            "id": "zvq-6",
            "question_text": "Sudden death tiebreaker: Name the element with atomic number 79.",
            "answer": "Gold",
            "topic_l1": "Science & Nature",
            "subtopic_l2": "Chemistry",
            "keywords": ["gold", "chemistry", "element", "atomic number"],
            "difficulty": "Medium",
            "difficulty_score": 0.45,
            "document_id": "doc-zvq-1",
            "document_title": "ZVQ 2023 National Finals",
            "document_filename": "zvq_2023_finals.pptx",
            "is_varsity": True,
            "round_stage": "finals",
            "play_mode": "TIEBREAK",
            "occurrence_count": 3,
            "repeat_count": 2,
            "star_marked": False,
            "source_year": 2023,
            "curiosity_score": 7,
            "has_audio": False,
            "has_video": False,
            "image_dependent": False,
            "tagged": True,
        },
        # Non-ZVQ / General School Questions
        {
            "id": "gen-1",
            "question_text": "What is the capital city of Australia situated between Sydney and Melbourne?",
            "answer": "Canberra",
            "topic_l1": "History & Culture",
            "subtopic_l2": "Geography",
            "keywords": ["australia", "canberra", "capital", "geography"],
            "difficulty": "Easy",
            "difficulty_score": 0.20,
            "document_id": "doc-school-1",
            "document_title": "Inter-School General Quiz 2024",
            "document_filename": "interschool_2024.pptx",
            "is_varsity": False,
            "round_stage": "prelims",
            "play_mode": "DIRECTED",
            "occurrence_count": 20,
            "repeat_count": 19,
            "star_marked": False,
            "source_year": 2024,
            "curiosity_score": 4,
            "has_audio": False,
            "has_video": False,
            "image_dependent": False,
            "tagged": True,
        },
        {
            "id": "gen-2",
            "question_text": "Which planetary spacecraft recorded this audio roar of lightning in Jupiter's atmosphere?",
            "answer": "Voyager 1",
            "topic_l1": "Technology & Space",
            "subtopic_l2": "Astronomy",
            "keywords": ["voyager", "jupiter", "lightning", "space", "audio"],
            "difficulty": "Hard",
            "difficulty_score": 0.85,
            "document_id": "doc-school-2",
            "document_title": "National Science Open 2024",
            "document_filename": "science_open_2024.pptx",
            "is_varsity": False,
            "round_stage": "finals",
            "play_mode": "BUZZER",
            "occurrence_count": 1,
            "repeat_count": 0,
            "star_marked": True,
            "source_year": 2024,
            "curiosity_score": 10,
            "has_audio": True,
            "has_video": True,
            "image_dependent": True,
            "tagged": True,
        },
    ]
    return bank


# ==============================================================================
# CATEGORY 1: FREQUENCY & REPEATS
# ==============================================================================

def test_parse_prompt_most_asked_zvq():
    """Verify parse_prompt extracts pure database aggregation for most asked ZVQ."""
    q, filters, count = parse_prompt("give me 15 most asked questions across zvq")
    assert q == ""  # BM25 query is clean
    assert count == 15
    assert filters.get("sort_by") == "most_asked"
    assert filters.get("repeat_filter") == "repeated_only"
    assert filters.get("is_varsity") is True


def test_build_draft_most_asked_zvq_ranks_by_occurrences(monkeypatch):
    """Most asked questions across ZVQ should filter to ZVQ only and rank by occurrence_count DESC."""
    bank = _make_archive_bank()
    monkeypatch.setattr(engine, "_load_bank", lambda db, *a, **kw: bank)

    mock_db = MagicMock()
    draft = engine.build_draft(mock_db, "give me 3 most asked questions across zvq")
    picked = draft["questions"]
    assert len(picked) == 3

    # All questions must be varsity
    for q in picked:
        assert q["is_varsity"] is True

    # gen-1 had occurrence_count=20, but it is NOT varsity, so it must NOT appear
    assert not any(q["id"] == "gen-1" for q in picked)

    # Ordering must strictly respect occurrence_count (Flipkart=12, Infosys=8, ARM=5)
    assert picked[0]["id"] == "zvq-3"
    assert picked[0]["occurrence_count"] == 12
    assert picked[1]["id"] == "zvq-1"
    assert picked[1]["occurrence_count"] == 8
    assert picked[2]["id"] == "zvq-2"
    assert picked[2]["occurrence_count"] == 5


def test_build_draft_never_repeated_in_2024(monkeypatch):
    """Never repeated questions in 2024 should filter for occurrence_count == 1 and year == 2024."""
    bank = _make_archive_bank()
    monkeypatch.setattr(engine, "_load_bank", lambda db, *a, **kw: bank)

    mock_db = MagicMock()
    draft = engine.build_draft(mock_db, "questions from 2024 that have never been repeated")
    picked = draft["questions"]
    assert len(picked) >= 1
    for q in picked:
        assert q["occurrence_count"] == 1
        assert q["source_year"] == 2024


# ==============================================================================
# CATEGORY 2: COMPETITION STAGES & DIFFICULTY
# ==============================================================================

def test_build_draft_hardest_zvq_finals(monkeypatch):
    """Hardest questions across ZVQ finals should filter to finals and order by difficulty_score DESC."""
    bank = _make_archive_bank()
    monkeypatch.setattr(engine, "_load_bank", lambda db, *a, **kw: bank)

    mock_db = MagicMock()
    draft = engine.build_draft(mock_db, "hardest questions across zvq finals")
    picked = draft["questions"]
    assert len(picked) >= 2
    for q in picked:
        assert q["is_varsity"] is True
        assert q["round_stage"] == "finals"

    # Highest difficulty first: ARM (0.88) before Gold (0.45) before Infosys (0.25)
    assert picked[0]["id"] == "zvq-2"
    assert picked[0]["difficulty_score"] == 0.88


def test_passes_hard_round_stage_filtering():
    """Verify passes_hard accurately respects round_stage filters."""
    cand_prelim = {"round_stage": "prelims", "document_title": "ZVQ Prelims"}
    cand_final = {"round_stage": "finals", "document_title": "ZVQ Finals"}

    assert passes_hard(cand_prelim, {"round_stage": "prelims"}) is True
    assert passes_hard(cand_final, {"round_stage": "prelims"}) is False
    assert passes_hard(cand_final, {"round_stage": "finals"}) is True


# ==============================================================================
# CATEGORY 3: PLAY MODES & ROUND MECHANICS
# ==============================================================================

def test_build_draft_buzzer_round_zvq(monkeypatch):
    """Buzzer round questions across ZVQ should filter exclusively to play_mode == BUZZER."""
    bank = _make_archive_bank()
    monkeypatch.setattr(engine, "_load_bank", lambda db, *a, **kw: bank)

    mock_db = MagicMock()
    draft = engine.build_draft(mock_db, "buzzer round questions across zvq")
    picked = draft["questions"]
    assert len(picked) == 2
    for q in picked:
        assert q["is_varsity"] is True
        assert q["play_mode"] == "BUZZER"


def test_build_draft_tiebreaker_questions(monkeypatch):
    """Tiebreaker questions should filter for play_mode == TIEBREAK."""
    bank = _make_archive_bank()
    monkeypatch.setattr(engine, "_load_bank", lambda db, *a, **kw: bank)

    mock_db = MagicMock()
    draft = engine.build_draft(mock_db, "tiebreaker questions")
    picked = draft["questions"]
    assert len(picked) == 1
    assert picked[0]["id"] == "zvq-6"
    assert picked[0]["play_mode"] == "TIEBREAK"


# ==============================================================================
# CATEGORY 4: EDITORIAL FLAGS & CURIOSITY
# ==============================================================================

def test_build_draft_starred_questions(monkeypatch):
    """Starred questions query filters to star_marked == True."""
    bank = _make_archive_bank()
    monkeypatch.setattr(engine, "_load_bank", lambda db, *a, **kw: bank)

    mock_db = MagicMock()
    draft = engine.build_draft(mock_db, "starred questions from college prelims")
    picked = draft["questions"]
    assert len(picked) >= 1
    for q in picked:
        assert q["star_marked"] is True
        assert q["round_stage"] == "prelims"


def test_build_draft_curiosity_ranking(monkeypatch):
    """Curiosity-driven ranking sorts by curiosity_score DESC."""
    bank = _make_archive_bank()
    monkeypatch.setattr(engine, "_load_bank", lambda db, *a, **kw: bank)

    mock_db = MagicMock()
    draft = engine.build_draft(mock_db, "curiosity-driven questions")
    picked = draft["questions"]
    scores = [q.get("curiosity_score", 0) for q in picked]
    assert scores == sorted(scores, reverse=True)


# ==============================================================================
# CATEGORY 5: MULTI-MODAL MEDIA QUERIES
# ==============================================================================

def test_build_draft_audio_video_questions(monkeypatch):
    """Audio/video questions query filters only items having media."""
    bank = _make_archive_bank()
    monkeypatch.setattr(engine, "_load_bank", lambda db, *a, **kw: bank)

    mock_db = MagicMock()
    draft = engine.build_draft(mock_db, "questions with audio or video clips")
    picked = draft["questions"]
    assert len(picked) == 2
    for q in picked:
        assert q["has_audio"] or q["has_video"]


# ==============================================================================
# CATEGORY 6: HYBRID THEMATIC & ANALYTICAL QUERIES
# ==============================================================================

def test_hybrid_most_asked_tech_questions(monkeypatch):
    """'10 most asked tech questions across zvq' combines keyword relevance with repetition boost."""
    bank = _make_archive_bank()
    monkeypatch.setattr(engine, "_load_bank", lambda db, *a, **kw: bank)

    b = engine.interpret("10 most asked tech questions across zvq")
    assert "tech" in b.query
    assert b.sort_by == "most_asked"
    assert b.filters.get("is_varsity") is True

    mock_db = MagicMock()
    draft = engine.build_draft(mock_db, "10 most asked tech questions across zvq")
    picked = draft["questions"]
    # All picked items must be varsity tech questions
    for q in picked:
        assert q["is_varsity"] is True
        text_blob = f"{q.get('question_text', '')} {q.get('topic_l1', '')} {q.get('subtopic_l2', '')}".lower()
        assert any(k in text_blob for k in ["tech", "semiconductor", "arm", "apollo", "softbank", "space", "chips", "computing", "it", "infosys", "nasdaq", "narayana murthy", "fintech", "upi"])


# ==============================================================================
# CATEGORY 7: PROVENANCE VIEW ENRICHMENT
# ==============================================================================

def test_view_contains_provenance_attributes():
    """Verify _view() exports all analytical provenance fields in the JSON payload."""
    candidate = {
        "id": "test-1",
        "question_text": "Sample question?",
        "answer": "Sample answer",
        "occurrence_count": 5,
        "repeat_count": 4,
        "is_varsity": True,
        "round_stage": "prelims",
        "star_marked": True,
        "source_year": 2024,
        "curiosity_score": 9,
        "has_audio": True,
        "has_video": False,
        "play_mode": "BUZZER",
    }
    view = engine._view(candidate)
    assert view["occurrence_count"] == 5
    assert view["repeat_count"] == 4
    assert view["is_varsity"] is True
    assert view["round_stage"] == "prelims"
    assert view["star_marked"] is True
    assert view["source_year"] == 2024
    assert view["curiosity_score"] == 9
    assert view["has_audio"] is True
    assert view["has_video"] is False
    assert view["play_mode"] == "BUZZER"
