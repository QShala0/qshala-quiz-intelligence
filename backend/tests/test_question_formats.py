import pytest
from backend.app.services.ingestion.question_formats import (
    FMT_CONNECT_4,
    FMT_STEPPED_CLUE,
    FMT_ARRANGE_ORDER,
    FMT_QA,
    detect_and_structure_format,
)
from backend.app.services.export.exporter import QuizExporter


def test_detect_stepped_clue_portfolio_builder():
    q_text = (
        "Portfolio Builder Round\n"
        "Clue 1: +20/-15 Obscure history of stock exchange.\n"
        "Clue 2: +15/-10 Founded in Bombay in 1875.\n"
        "Clue 3: +10/-5 Phiroze Jeejeebhoy Towers."
    )
    fmt, payload = detect_and_structure_format(
        question_text=q_text,
        answer="Bombay Stock Exchange (BSE)",
        options=None,
    )
    assert fmt == FMT_STEPPED_CLUE
    assert payload is not None
    assert payload["type"] == FMT_STEPPED_CLUE
    assert len(payload["clues"]) == 3
    assert payload["clues"][0]["points"] == "+20/-15"
    assert payload["clues"][1]["points"] == "+15/-10"
    assert payload["clues"][2]["points"] == "+10/-5"


def test_detect_arrange_order_ticker_tangle():
    q_text = "Arrange these four companies from earliest to latest founding year:"
    options = ["Infosys", "Tata Consultancy Services", "Wipro", "HCL"]
    fmt, payload = detect_and_structure_format(
        question_text=q_text,
        answer="TCS -> Wipro -> HCL -> Infosys",
        options=options,
    )
    assert fmt == FMT_ARRANGE_ORDER
    assert payload is not None
    assert payload["type"] == FMT_ARRANGE_ORDER
    assert len(payload["items"]) == 4
    assert payload["items"][0]["label"] == 1 and payload["items"][0]["key"] == "1"     # numeric labels 1-4, not A-D
    assert payload["items"][0]["text"] == "Infosys"


def test_detect_connect_4():
    q_text = "What connects these 4 companies / logos?"
    imgs = ["img1.png", "img2.png", "img3.png", "img4.png"]
    fmt, payload = detect_and_structure_format(
        question_text=q_text,
        answer="All unicorns backed by Peak XV",
        image_refs=imgs,
    )
    assert fmt == FMT_CONNECT_4
    assert payload is not None
    assert payload["type"] == FMT_CONNECT_4
    assert len(payload["elements"]) == 4


def test_pptx_export_with_question_formats():
    quiz = {
        "title": "QShala Master Formats Test",
        "topic": "Business Quiz",
        "difficulty": "Challenging",
        "questions": [
            {
                "id": "q1",
                "question_text": "What connects these 4 entities?",
                "answer": "Bangalore Startups",
                "question_format": FMT_CONNECT_4,
                "format_payload": {
                    "type": FMT_CONNECT_4,
                    "prompt": "What connects these 4 entities?",
                    "elements": [
                        {"label": "Swiggy", "image": None},
                        {"label": "Zerodha", "image": None},
                        {"label": "Flipkart", "image": None},
                        {"label": "Razorpay", "image": None},
                    ],
                    "connection_reveal": "Bangalore Startups",
                },
                "is_approved": True,
            },
            {
                "id": "q2",
                "question_text": "Portfolio Builder 3-Stage Question",
                "answer": "NSE",
                "question_format": FMT_STEPPED_CLUE,
                "format_payload": {
                    "type": FMT_STEPPED_CLUE,
                    "badge": "Portfolio Builder • Stepped Clues",
                    "clues": [
                        {"step": 1, "points": "+20/-15", "text": "Set up NAL Academy."},
                        {"step": 2, "points": "+15/-10", "text": "Mega IPO planned."},
                        {"step": 3, "points": "+10/-5", "text": "Blooming marigold logo."},
                    ],
                },
                "is_approved": True,
            },
            {
                "id": "q3",
                "question_text": "Arrange these market crashes chronologically:",
                "answer": "B -> D -> A -> C",
                "question_format": FMT_ARRANGE_ORDER,
                "format_payload": {
                    "type": FMT_ARRANGE_ORDER,
                    "badge": "Ticker Tangle • Arrange Order",
                    "instruction": "Arrange these market crashes chronologically:",
                    "items": [
                        {"key": "A", "text": "Wall Street Crash", "annotation": "1929"},
                        {"key": "B", "text": "Tulip Mania", "annotation": "1637"},
                        {"key": "C", "text": "Black Monday", "annotation": "1987"},
                        {"key": "D", "text": "South Sea Bubble", "annotation": "1720"},
                    ],
                    "correct_sequence": ["B", "D", "A", "C"],
                    "scoring": {"all_correct": "+20", "two_correct": "+10", "one_correct": "+5"},
                },
                "is_approved": True,
            },
        ],
    }

    pptx_bytes = QuizExporter.to_pptx(quiz, quiz_type="school")
    assert isinstance(pptx_bytes, bytes)
    assert len(pptx_bytes) > 1000
