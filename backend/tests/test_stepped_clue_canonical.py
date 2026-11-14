import io

from pptx import Presentation

from backend.app.services.export.exporter import QuizExporter
from backend.app.services.export import format_slides
from backend.app.services.ingestion.heal_specialized import parse_stepped_clues
from backend.scripts.fix_stepped_clue_payloads import canonical_payload, is_canonical

MERGED = (
    "[Portfolio Builder · Stepped Clues]\n\n"
    "• Clue 1 (+20 / -15):\nLaunched on February 1, 2007 adopting the Berliner format.\n\n"
    "• Clue 2 (+15 / -10):\nKnown for the Mark To Market column and the Lounge edition.\n\n"
    "• Clue 3 (+10 / -5):\nIt is from the stable of Hindustan Times Media."
)


def test_legacy_structured_payload_is_made_canonical_and_detected_by_export():
    # The shape the old healer wrote: wrong field names, no extraction, no scoring_ladder_ok.
    legacy = {"type": "STEPPED_CLUE", "answer": "Mint", "clues": [
        {"step": 1, "text": "A", "points_correct": 20, "points_wrong": -15, "image": "x.png"},
        {"step": 2, "text": "B", "points_correct": 15, "points_wrong": -10},
        {"step": 3, "text": "C", "points_correct": 10, "points_wrong": -5},
    ]}
    assert format_slides.export_format_of(legacy) is None            # rejected before the fix
    out = canonical_payload(legacy, "", [], "Mint", parse_stepped_clues)
    assert is_canonical(out)
    assert format_slides.export_format_of(out) == "STEPPED_CLUE"     # now a real stepped-clue question
    assert [c["correct"] for c in out["clues"]] == [20, 15, 10]
    assert [c["wrong"] for c in out["clues"]] == [-15, -10, -5]
    assert out["clues"][0]["images"] == ["x.png"]
    # frontend isStepped() requirements
    assert out["scoring_ladder_ok"] is True and isinstance(out["extraction"], dict)


def test_reconstructs_clues_from_merged_question_text():
    out = canonical_payload({"type": "STEPPED_CLUE"}, MERGED, [], "Mint", parse_stepped_clues)
    assert is_canonical(out)
    assert len(out["clues"]) == 3
    assert "Berliner" in out["clues"][0]["text"]
    assert "Hindustan Times" in out["clues"][2]["text"]
    assert [c["correct"] for c in out["clues"]] == [20, 15, 10]


def test_is_canonical_is_idempotent():
    out = canonical_payload({"type": "STEPPED_CLUE"}, MERGED, [], "Mint", parse_stepped_clues)
    again = canonical_payload(out, MERGED, [], "Mint", parse_stepped_clues)
    assert is_canonical(again)
    assert [c["text"] for c in again["clues"]] == [c["text"] for c in out["clues"]]


def test_export_puts_each_clue_on_its_own_slide():
    payload = canonical_payload({"type": "STEPPED_CLUE"}, MERGED, [], "Mint", parse_stepped_clues)
    quiz = {"title": "t", "topic": "Media", "questions": [
        {"question_text": MERGED, "answer": "Mint", "question_format": "STEPPED_CLUE",
         "format_payload": payload, "is_approved": True}]}
    prs = Presentation(io.BytesIO(QuizExporter.to_pptx(quiz, quiz_type="qshala_school")))
    texts = ["\n".join(s.text_frame.text for s in sl.shapes if s.has_text_frame) for sl in prs.slides]
    assert any("Clue 1" in t for t in texts)
    assert any("Clue 2" in t for t in texts)
    assert any("Clue 3" in t for t in texts)
    # the three clues must never all land on one slide
    assert not any(("Berliner" in t and "Hindustan Times" in t) for t in texts)
