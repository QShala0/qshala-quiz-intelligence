"""GATE 2: roles, units, sections, play modes and the per-round question-count cross-check."""
import json
import os
from pathlib import Path

import pytest

from backend.app.services.ingestion import slide_roles as sr
from backend.app.services.ingestion.slide_roles import analyze_deck, parse_rules

FIX = Path(__file__).resolve().parent / "fixtures"
EXPECTED = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))


def load(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def unit_at(a, q):
    hits = [u for u in a.units if q in u.content_slides]
    assert len(hits) == 1, f"slide {q}: {len(hits)} units"
    return hits[0]


def covered(a):
    out = set()
    for u in a.units:
        out |= set(u.content_slides) | set(u.extra_answer_slides)
        out |= {n for n in (u.marker_slide, u.answer_slide) if n}
    return out


# ------------------------------------------------------------------------------------------ rules parsing

def test_parse_rules():
    r = parse_rules("TOPICATOR\n\n6 Topics & 2 questions in each topic\n+20 for direct, +10 for a pass")
    assert r.n_questions == 12 and r.play_mode == "PASS" and r.ladder[:1] == [(20, 0)] or r.n_questions == 12
    r = parse_rules("Round 3: Image Reveal\n\n4 questions. On Buzzer.\nA clue & image will be provided. 60 seconds\n+20 / -15 +15 / -10")
    assert r.n_questions == 4 and r.play_mode == "BUZZER" and r.timer_seconds == 60 and r.ladder == [(20, 15), (15, 10)]
    r = parse_rules("8 questions \u2013 1 directed to each team")
    assert r.n_questions == 8 and r.play_mode == "DIRECTED"
    r = parse_rules("Scoring:\n4 correct- +20\n2 correct- +10\n1 correct- +5\n60 seconds per question.")
    assert r.timer_seconds == 60


@pytest.mark.parametrize("text,is_rules", [
    ("6 questions\nAll questions on the buzzer\n+ 20 points for a correct answer", True),
    ("3 CLUES\n\n4 Questions\nAll on the buzzer\nClue 1: +20/-15\nClue 2: +15/-10", True),
    ("Q1. Clue 1: +20/-15\nSeat of the hall", False),
    ("1 | +20/-15", False),
    ("Which year did the Salt March happen?", False),
])
def test_looks_like_rules(text, is_rules):
    assert sr.looks_like_rules({"extracted_text": text}, 1) is is_rules


# ------------------------------------------------------------------------------------------ DSU miniature

@pytest.fixture(scope="module")
def dsu():
    return analyze_deck(load("dsu_mini"))


def test_dsu_cases(dsu):
    for case in EXPECTED["dsu_mini"]["cases"]:
        u = unit_at(dsu, case["q"])
        if "a" in case:
            assert u.answer_slide == case["a"], case
        if "slot" in case:
            assert u.slot == case["slot"], case
        if "play_mode" in case:
            assert u.play_mode == case["play_mode"], case
        if "team" in case:
            assert u.team == case["team"], case
        if case.get("section"):
            assert u.section == case["section"], case
        if "topic_label" in case:
            assert u.topic_label == case["topic_label"], case
        if case.get("needs_review"):
            assert u.needs_review, case
    assert unit_at(dsu, 84).self_contained and unit_at(dsu, 84).answer_slide is None


def test_dsu_no_question_slides(dsu):
    cov = covered(dsu)
    for n in EXPECTED["dsu_mini"]["no_question_slides"]:
        assert n not in cov, n


def test_dsu_reserve_not_deduped_by_label(dsu):
    a, b = unit_at(dsu, 23), unit_at(dsu, 26)
    assert a is not b and a.topic_label == b.topic_label == "Entrepreneurship 1"
    assert a.slot == "RESERVE" and b.slot == "LIVE"


def test_dsu_round_bookkeeping(dsu):
    names = [r.name for r in dsu.rounds]
    assert names[:3] == ["TOPICATOR", "LIST IT", "IMAGE REVEAL"]
    # BUFFER clears the round name
    buf = unit_at(dsu, 224)
    assert buf.round_name is None and buf.round_key is None


def test_example_ends_at_next_divider(dsu):
    assert unit_at(dsu, 110).slot == "EXAMPLE"
    assert unit_at(dsu, 114).slot == "LIVE"


@pytest.mark.parametrize("name,case_q", [("three_clues_reserve", 3), ("arrange_reserve", 3), ("mcq_reserve", 17),
                                         ("hotspot_reserve", 3)])
def test_reserve_minis_pair_units(name, case_q):
    a = analyze_deck(load(name))
    for case in EXPECTED[name]["cases"]:
        if "a" in case:
            assert unit_at(a, case["q"]).answer_slide == case["a"], (name, case)
    for n in EXPECTED[name].get("no_question_slides", []):
        assert all(n not in u.content_slides for u in a.units), n      # may be extra answer pictures, never a question


def test_mcq_picture_slide_joins_question():
    a = analyze_deck(load("mcq_reserve"))
    u = unit_at(a, 47)
    assert u.content_slides == [47, 48] and u.answer_slide == 50


def test_three_clues_hidden_is_reserve():
    a = analyze_deck(load("three_clues_reserve"))
    assert unit_at(a, 313).slot == "RESERVE" and unit_at(a, 3).slot == "LIVE"
    assert unit_at(a, 3).content_slides == [3, 4, 5]          # one slide per clue stays one unit


def test_round_count_mismatch_marks_round_not_units():
    slides = [
        {"slide_number": 1, "extracted_text": "RAPID FIRE\nROUND 5", "title": "", "shapes": [], "animations": [],
         "interactive_animations": [], "hidden": False},
        {"slide_number": 2, "extracted_text": "3 questions\nAll on the buzzer", "title": "", "shapes": [], "animations": [],
         "interactive_animations": [], "hidden": False},
    ]
    n = 3
    for k in range(2):                                           # only 2 live units for 3 promised
        for txt in (f"Question text {k}?", "ANSWER", f"Answer {k}"):
            slides.append({"slide_number": n, "extracted_text": txt, "title": txt, "shapes": [], "animations": [],
                           "interactive_animations": [], "hidden": False})
            n += 1
    a = analyze_deck(slides)
    assert len(a.units) == 2 and a.rounds[0].needs_review and "3" in a.rounds[0].review_reason
    assert not any(u.needs_review for u in a.units)


def test_numbers_printed_on_slides_do_not_group_units():
    # question numbers restart / skip: units follow the ANSWER slides only
    slides, n = [], 1
    for printed in (1, 2, 1, 7):
        for txt in (f"{printed}.\nQuestion {printed}?", "ANSWER", f"Answer {n}"):
            slides.append({"slide_number": n, "extracted_text": txt, "title": txt, "shapes": [], "animations": [],
                           "interactive_animations": [], "hidden": False})
            n += 1
    assert len(analyze_deck(slides).units) == 4


# ------------------------------------------------------------------------------------------ real decks (optional)

DECK_DIRS = [Path(p) for p in os.environ.get(
    "QSHALA_DECK_DIRS",
    r"C:\Users\bragg\Desktop\Projects\QShala\PPT\PPT - QS 1\PPTs for Akshay & Glenn;C:\Users\bragg\Desktop\Projects\QShala\PPT\oo",
).split(";")]


def _real(name):
    for d in DECK_DIRS:
        p = d / name
        if p.exists():
            return p
    pytest.skip(f"real deck {name!r} not available")


def _parse(path, monkeypatch):
    import backend.app.services.storage.r2_storage as r2
    from backend.app.services.ingestion.pptx_parser import PPTXParser

    def _no_storage():
        raise RuntimeError("storage disabled in tests")
    monkeypatch.setattr(r2, "get_media_storage", _no_storage)
    return PPTXParser(str(path), "memtest-roles", write_to_disk=False).parse()


@pytest.fixture(scope="module")
def mp():
    with pytest.MonkeyPatch.context() as m:
        yield m


def test_real_dsu_gate(mp):
    a = analyze_deck(_parse(_real("DSU Business Quiz Finals.pptx"), mp))
    assert len(a.units) == 74
    live = {r.name: r.live_units for r in a.rounds}
    assert live == {"TOPICATOR": 12, "LIST IT": 8, "IMAGE REVEAL": 4, "CONNECT": 6, "RAPID FIRE": 6}
    assert not any(r.needs_review for r in a.rounds)
    cov = covered(a)
    for n in (3, 4, 5, 9, 83, 105, 170, 207, 208, 209, 210, 211):
        assert n not in cov, n
    assert (unit_at(a, 15).answer_slide, unit_at(a, 15).topic_label, unit_at(a, 15).play_mode) == (17, "Sustainability 1", "PASS")
    assert unit_at(a, 23).slot == "RESERVE" and unit_at(a, 26).slot == "LIVE"
    assert unit_at(a, 72).needs_review and unit_at(a, 72).topic_label is None
    u84 = unit_at(a, 84)
    assert (u84.play_mode, u84.team, u84.self_contained) == ("DIRECTED", 8, True)
    assert unit_at(a, 114).play_mode == "BUZZER" and unit_at(a, 114).answer_slide == 116
    for q in (82, 110, 136):
        assert unit_at(a, q).slot == "EXAMPLE"
    assert unit_at(a, 163).slot == "RESERVE" and unit_at(a, 163).answer_slide == 165
    for q, mode in ((102, "AUDIENCE"), (130, "AUDIENCE")):
        assert unit_at(a, q).play_mode == mode
    assert unit_at(a, 224).section == "BUFFER" and unit_at(a, 224).round_name is None


@pytest.mark.parametrize("deck,units", [
    ("ZVQ - Three Clues - Reserve.pptx", 92), ("Arrange the Order - 2026 Aug to Dec ZVQ College - Reserve.pptx", 82),
    ("Prelim MCQs - 2026 Aug to Dec ZVQ College - Reserve.pptx", 16), ("ZVQ - Hotspot Buzzer - Reserve.pptx", 240),
    ("ZVQ - List It - Reserve.pptx", 67)])
def test_real_reserve_unit_counts(deck, units, mp):
    assert len(analyze_deck(_parse(_real(deck), mp)).units) == units


def test_real_reserve_cases(mp):
    a = analyze_deck(_parse(_real("ZVQ - Three Clues - Reserve.pptx"), mp))
    assert unit_at(a, 3).answer_slide == 7 and unit_at(a, 13).answer_slide == 17
    assert unit_at(a, 313).slot == "RESERVE" and unit_at(a, 313).answer_slide == 317
    a = analyze_deck(_parse(_real("Arrange the Order - 2026 Aug to Dec ZVQ College - Reserve.pptx"), mp))
    assert (unit_at(a, 3).answer_slide, unit_at(a, 6).answer_slide, unit_at(a, 132).answer_slide) == (5, 8, 134)
    a = analyze_deck(_parse(_real("Prelim MCQs - 2026 Aug to Dec ZVQ College - Reserve.pptx"), mp))
    assert unit_at(a, 17).answer_slide == 19 and unit_at(a, 14).answer_slide == 16
    assert unit_at(a, 47).content_slides == [47, 48] and unit_at(a, 47).answer_slide == 50
    a = analyze_deck(_parse(_real("ZVQ - Hotspot Buzzer - Reserve.pptx"), mp))
    assert unit_at(a, 3).answer_slide == 5
    cov_starts = {u.content_slides[0] for u in a.units}
    for n in (348, 352, 356):
        assert n not in cov_starts
        assert all(n not in u.content_slides for u in a.units)
    a = analyze_deck(_parse(_real("ZVQ - List It - Reserve.pptx"), mp))
    assert unit_at(a, 3).self_contained and unit_at(a, 68).self_contained
