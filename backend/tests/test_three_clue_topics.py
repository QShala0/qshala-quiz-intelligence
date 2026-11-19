"""A round/format label is never a topic, the format name is never a tag, and the three-clue repair recognises
the broken legacy shape and matches stored questions to the re-extracted ones."""
import sys
from pathlib import Path
from types import SimpleNamespace

from backend.app.services.tagging.qshala_rules import classify_taxonomy, is_format_or_round_label

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import repair_three_clue_questions as rep  # noqa: E402


def test_round_and_format_labels_are_not_topics():
    for label in ("THREE CLUES", "Portfolio Builder", "Round 3", "Stepped Clue", "Potpourri", "Reserve", ""):
        assert is_format_or_round_label(label), label
    for label in ("Finance", "Banks and Banking", "Cities and Places"):
        assert not is_format_or_round_label(label), label


def test_format_name_is_not_added_as_a_tag():
    _l1, _l2, keywords = classify_taxonomy("Which body regulates the securities market in India?", "SEBI", "THREE CLUES", "STEPPED_CLUE")
    assert not any("stepped clue" in k.lower() for k in keywords)


def test_legacy_stepped_payload_is_detected_as_broken():
    legacy = {"type": "STEPPED_CLUE", "clues": [{"text": "Real clue"}, {"text": "Additional context clue"}]}
    canonical = {"type": "STEPPED_CLUE", "extraction": {"route": "accept"}, "clues": [{"text": "a"}, {"text": "b"}, {"text": "c"}]}
    assert rep.is_broken(legacy) is True
    assert rep.is_broken(canonical) is False
    assert rep.is_broken({"type": "STEPPED_CLUE", "extraction": {}, "clues": [{"text": "Direct giveaway clue"}]}) is True


def test_stored_question_matches_by_answer_and_nearby_slide():
    by_answer = {"sebi": [{"slide_number": 378, "answer": "SEBI"}, {"slide_number": 443, "answer": "SEBI"}]}
    st = SimpleNamespace(answer="SEBI")
    assert rep.match_extracted(st, 445, by_answer)["slide_number"] == 443
    assert rep.match_extracted(st, 420, by_answer) is None          # too far from any extracted SEBI question
    assert rep.match_extracted(SimpleNamespace(answer="RBI"), 443, by_answer) is None
