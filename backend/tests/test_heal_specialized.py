"""Tests for heal_specialized.py ensuring Connect 4 and Stepped Clue questions
are normalized into clean, canonical data structures with zero off-by-one shifts.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.models.document import Document
from backend.app.models.question import Question
from backend.app.services.ingestion.heal_specialized import (
    parse_stepped_clues,
    heal_specialized_questions,
)


def test_parse_stepped_clues_standard():
    text = (
        "Clue 1: +20/-15 Founded in 1992, it introduced screen-based electronic trading to India.\n"
        "Clue 2: +15/-10 Its benchmark index was launched in 1996 and tracks 50 blue-chip stocks.\n"
        "Clue 3: +10/-5 It operates the NIFTY 50 index and is located in BKC, Mumbai."
    )
    c1, c2, c3 = parse_stepped_clues(text)
    assert c1 is not None and c2 is not None and c3 is not None
    assert "Founded in 1992" in c1
    assert "benchmark index was launched in 1996" in c2
    assert "NIFTY 50" in c3
    assert "+20/-15" not in c1
    assert "+15/-10" not in c2
    assert "+10/-5" not in c3


def test_parse_stepped_clues_with_ocr_noise():
    text = (
        "(+20/-15): 3. Clue 1: +20/-15 Founded by Jon and Mark in 2011 to split bills with friends.\n"
        "(+15/-10): 2. Clue 2: +15/-10 Used globally by roommates to track shared expenses.\n"
        "(+10/-5): 1. Clue 3: +10/-5 Famous for its 'Simplify Debts' feature."
    )
    c1, c2, c3 = parse_stepped_clues(text)
    assert c1 is not None
    assert c1 == "Founded by Jon and Mark in 2011 to split bills with friends."
    assert c2 == "Used globally by roommates to track shared expenses."
    assert c3 == "Famous for its 'Simplify Debts' feature."


def test_parse_stepped_clues_incomplete():
    text = "Only one clue here without other markers."
    c1, c2, c3 = parse_stepped_clues(text)
    assert c1 is None
    assert c2 is None
    assert c3 is None


@pytest.fixture
def in_memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    doc = Document(
        id="test-doc-1",
        filename="test.pptx",
        title="Test Presentation",
        file_type="pptx",
        storage_path="/tmp/test.pptx",
    )
    session.add(doc)
    session.commit()

    yield session
    session.close()


def test_heal_specialized_stepped_clue(in_memory_db):
    q = Question(
        id="test-stepped-1",
        document_id="test-doc-1",
        topic="Finance",
        question_text=(
            "Clue 1: (+20/-15) First hint.\n"
            "Clue 2: (+15/-10) Second hint.\n"
            "Clue 3: (+10/-5) Third hint."
        ),
        answer="National Stock Exchange",
        question_format="STEPPED_CLUE",
        question_type="STEPPED_CLUE",
        question_images=["http://img1.png", "http://img2.png", "http://img3.png"],
    )
    in_memory_db.add(q)
    in_memory_db.commit()

    stats = heal_specialized_questions(in_memory_db)
    assert stats["stepped_healed"] == 1

    healed_q = in_memory_db.query(Question).filter(Question.id == "test-stepped-1").first()
    assert healed_q.question_format == "STEPPED_CLUE"
    payload = healed_q.format_payload
    assert payload["type"] == "STEPPED_CLUE"
    assert len(payload["clues"]) == 3

    tier1 = payload["clues"][0]
    assert tier1["step"] == 1
    assert tier1["points_correct"] == 20
    assert tier1["points_wrong"] == -15
    assert tier1["text"] == "First hint."
    assert tier1["image_url"] == "http://img1.png"

    tier2 = payload["clues"][1]
    assert tier2["step"] == 2
    assert tier2["points_correct"] == 15
    assert tier2["points_wrong"] == -10
    assert tier2["text"] == "Second hint."

    tier3 = payload["clues"][2]
    assert tier3["step"] == 3
    assert tier3["points_correct"] == 10
    assert tier3["points_wrong"] == -5
    assert tier3["text"] == "Third hint."

    # Verify clean question_text in markdown
    assert "Portfolio Builder" in healed_q.question_text
    assert "Stepped Clues" in healed_q.question_text
    assert "Clue 1 (+20 / -15):" in healed_q.question_text
    assert "First hint." in healed_q.question_text


def test_heal_specialized_connect4(in_memory_db):
    q = Question(
        id="test-c4-1",
        document_id="test-doc-1",
        topic="Finance",
        question_text="Look at the 4 elements. What connects them?",
        answer="Reserve Bank of India",
        question_format="CONNECT_4",
        question_type="CONNECT_4",
        format_payload={
            "type": "CONNECT_4",
            "items": [
                {"label": "Mint Road, Mumbai"},
                {"label": "Governor Shaktikanta Das"},
                {"label": "Repo Rate & Reverse Repo"},
                {"label": "Issue of Banknotes"},
            ],
            "common_link": "Reserve Bank of India",
        },
    )
    in_memory_db.add(q)
    in_memory_db.commit()

    stats = heal_specialized_questions(in_memory_db)
    assert stats["connect4_healed"] == 1

    healed_q = in_memory_db.query(Question).filter(Question.id == "test-c4-1").first()
    assert healed_q.question_format == "CONNECT_4"
    assert "Connect 4" in healed_q.question_text
    assert "Common Link" in healed_q.question_text
    assert len(healed_q.format_payload["elements"]) == 4


def test_heal_reclassifies_mislabeled(in_memory_db):
    q_fund = Question(
        id="6914a915-73e1-41a9-a205-120fdaa5f67b",
        document_id="test-doc-1",
        topic="Business",
        question_text="Stages in startup fundraising",
        answer="Pre-seed -> Seed -> Series A -> Series B",
        question_format="CONNECT_4",
        question_type="CONNECT_4",
    )
    in_memory_db.add(q_fund)
    in_memory_db.commit()

    stats = heal_specialized_questions(in_memory_db)
    assert stats["reclassified_arrange"] == 1

    healed = in_memory_db.query(Question).filter(Question.id == "6914a915-73e1-41a9-a205-120fdaa5f67b").first()
    assert healed.question_format == "ARRANGE_ORDER"
    assert healed.question_type == "ARRANGE_ORDER"


def test_arrange_order_format_and_heal_preserve_timer_fields(in_memory_db):
    from backend.app.services.ingestion.question_formats import detect_and_structure_format

    fmt, payload = detect_and_structure_format(
        "Arrange in chronological order: A, B, C, D",
        "A -> B -> C -> D",
        deck_round_format="ARRANGE_ORDER",
    )
    assert fmt == "ARRANGE_ORDER"
    assert payload["has_timer"] is True
    assert payload["timer_seconds"] == 60
    assert payload["time_limit_seconds"] == 60

    q_arr = Question(
        id="test-arr-timer-1",
        document_id="test-doc-1",
        topic="Business",
        question_text="Arrange from oldest to newest",
        answer="A -> B -> C -> D",
        question_format="ARRANGE_ORDER",
        question_type="ARRANGE_ORDER",
        format_payload={"type": "ARRANGE_ORDER", "time_limit_seconds": 60},
    )
    in_memory_db.add(q_arr)
    in_memory_db.commit()

    heal_specialized_questions(in_memory_db)
    in_memory_db.refresh(q_arr)
    assert q_arr.format_payload["has_timer"] is True
    assert q_arr.format_payload["timer_seconds"] == 60
    assert q_arr.format_payload["time_limit_seconds"] == 60
