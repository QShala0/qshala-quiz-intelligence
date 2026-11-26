"""Only real text clues may become a Connect hint; titles, instructions and labels never do."""
import copy
import json
from pathlib import Path

import pytest

from backend.app.services.connect_text import clean_text_hint, is_connect_clue_text, split_clue_parts
from backend.app.services.ingestion.question_extractor import QuestionExtractor

# Real text clues from the reference slides.
CLUES = [
    "The lowest female singing voice",
    "Meghdoot software – introduced in 2003",
    "August 7",
    "Unit of measuring light intensity",
    "Connected by the same railway line",
    "Connecticut's state bird",
]

# Seen on exported slides, none of them a clue.
NOT_CLUES = [
    "What common thread or concept connects all four clues and images?",
    "Question for our Audience",
    "3. Connect:",
    "3. Connect",
    "Connect 4",
    "What connects these?",
    "Identify the link between the pictures",
    "Look at the 4 elements.",
    "Audience question",
    "Bonus question",
    "Round 2",
    "Which common factor links them?",
]


@pytest.mark.parametrize("text", CLUES)
def test_real_text_clues_are_kept(text):
    assert is_connect_clue_text(text)
    assert split_clue_parts(text) == [text]


@pytest.mark.parametrize("text", NOT_CLUES)
def test_titles_instructions_and_labels_are_not_clues(text):
    assert not is_connect_clue_text(text)
    assert split_clue_parts(text) == []
    assert clean_text_hint(text) is None


def test_a_title_block_is_dropped_whole():
    # "3. Connect:" with a second line is the slide's title block, so none of it is a clue.
    assert split_clue_parts("3. Connect:\nThe name you can bank") == []
    assert split_clue_parts("3. Connect: The name you can bank") == []


def test_only_the_clues_survive_a_mixed_hint():
    mixed = "What common thread or concept connects all four clues and images? / The lowest female singing voice / Question for our Audience"
    assert split_clue_parts(mixed) == ["The lowest female singing voice"]
    assert clean_text_hint(mixed) == "The lowest female singing voice"


def test_empty_and_none():
    for v in (None, "", "   ", " / "):
        assert split_clue_parts(v) == []
        assert clean_text_hint(v) is None


def test_extractor_keeps_the_clue_and_skips_the_boilerplate_texts():
    fx = json.loads((Path(__file__).parent / "fixtures" / "dsu_mini.json").read_text(encoding="utf-8"))
    slides = copy.deepcopy(fx)
    slide = next(s for s in slides if s.get("slide_number") == 148)
    for i, text in enumerate(["What common thread or concept connects all four clues and images?", "Question for our Audience"]):
        slide["shapes"].append({"type": "TEXT_BOX", "name": f"Extra {i}", "text": text})
    qs = {q["slide_number"]: q for q in QuestionExtractor().extract_from_slides(slides, "t", 2026)}
    hint = qs[148]["format_payload"]["text_hint"]
    assert hint == "Unit of measuring light intensity", hint
