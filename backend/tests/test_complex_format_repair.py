"""Arrange the Order reading (full names, reveal values, no answer in the question), year-style values, 2-3 clue Connect,
and the pure parts of repair_complex_formats.py."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.services.ingestion import format_extractors as fe

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import repair_complex_formats as rcf  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


# ------------------------------------------------------------------------------------------ values that are years or ranges

@pytest.mark.parametrize("text,expected", [
    ("1st time in 2006", 2006.0), ("1st time in 1975", 1975.0), ("2nd term 2011", 2011.0),
    ("2021-26", 2023.5), ("2021–2026", 2023.5), ("1990-1995", 1992.5), ("20 to 30", 25.0),
    ("Incumbent", fe._ONGOING_VALUE), ("Present", fe._ONGOING_VALUE), ("till date", fe._ONGOING_VALUE),
    ("Kozhikode 1988", 1988.0), ("₹36,660", 36660.0), ("12 March 1950", None), ("many", None),
])
def test_parse_value_years_ranges_and_ongoing(text, expected):
    assert fe.parse_value(text) == expected


def test_a_chronology_with_an_incumbent_sorts_last():
    vals = {"K M Mani": fe.parse_value("1st time in 1975"), "T M Thomas Isaac": fe.parse_value("1st time in 2006"),
            "K N Balagopal": fe.parse_value("2021-26"), "V D Satheesan": fe.parse_value("Incumbent")}
    assert sorted(vals, key=vals.get) == ["K M Mani", "T M Thomas Isaac", "K N Balagopal", "V D Satheesan"]


# ------------------------------------------------------------------------------------------ item names and reveal values

def test_a_name_wrapped_over_two_lines_stays_whole_and_the_value_follows_it():
    q = {"text": "T M", "all_lines": ["T M", "Thomas Isaac"], "n_shapes": 1, "value": "Thomas Isaac"}
    a = {"text": "T M", "all_lines": ["T M", "Thomas Isaac", "1st time in 2006"], "n_shapes": 1, "value": "Thomas Isaac 1st time in 2006"}
    assert fe._arrange_name_and_value(q, a) == ("T M Thomas Isaac", "1st time in 2006")


def test_a_one_line_name_and_its_value():
    q = {"text": "Rapido", "all_lines": ["Rapido"], "n_shapes": 1, "value": None}
    a = {"text": "Rapido", "all_lines": ["Rapido", "2015"], "n_shapes": 1, "value": "2015"}
    assert fe._arrange_name_and_value(q, a) == ("Rapido", "2015")


def test_when_the_name_does_not_match_the_reveal_the_old_value_is_kept():
    q = {"text": "Rapido", "all_lines": ["Rapido"], "n_shapes": 1, "value": None}
    a = {"text": "Rapido Bike Taxi", "all_lines": ["Rapido Bike Taxi", "2015"], "n_shapes": 1, "value": "2015"}
    assert fe._arrange_name_and_value(q, a) == ("Rapido", "2015")


def test_the_question_text_does_not_give_away_the_order():
    from backend.app.services.ingestion.question_extractor import QuestionExtractor
    qs = QuestionExtractor().extract_from_slides(json.loads((FIX / "arrange_reserve.json").read_text(encoding="utf-8")), "t", 2026)
    arr = {q["slide_number"]: q for q in qs}[3]
    p = arr["format_payload"]
    assert p["correct_order"] == [3, 1, 4, 2]
    by_label = {it["label"]: it["text"] for it in p["items"]}
    shown_order = ", ".join(by_label[l] for l in sorted(by_label))
    correct_order = ", ".join(by_label[l] for l in p["correct_order"])
    assert arr["question_text"].endswith(shown_order) and not arr["question_text"].endswith(correct_order)
    assert arr["answer"] == " → ".join(by_label[l] for l in p["correct_order"])


# ------------------------------------------------------------------------------------------ repair script (pure parts)

def _stored(fmt="ARRANGE_ORDER", **kw):
    base = dict(question_format=fmt, format_payload={"type": fmt, "items": [{"text": "Item 1"}]}, question_text="old", answer="1",
                explanation="x", tags=["Startups", "Arrange the Order (Ticker Tangle)"], subtopic="Ticker Tangle", id="q1",
                question_type=fmt, format_needs_review=False, content_hash="h", question_images=[], answer_images=[],
                image_refs=[], audio_refs=[], question_audio=[], answer_audio=[], video_refs=[], question_videos=[],
                answer_videos=[], raw_media_refs=[], slide_id="s")
    base.update(kw)
    return SimpleNamespace(**base)


def _new(fmt="ARRANGE_ORDER", answer="A → B", review=False, slide=7):
    return {"question_format": fmt, "question_type": fmt, "question_text": "Q (asc) B, A", "answer": answer, "explanation": "",
            "slide_number": slide, "format_payload": {"type": fmt, "extraction": {"needs_review": review}},
            "_ingest": {"needs_review": review, "reasons": []}, "question_images": ["u1"], "answer_images": []}


def test_only_old_shape_rows_of_these_formats_are_candidates():
    assert rcf.is_old_shape(_stored("LIST_IT", format_payload={"type": "LIST_IT"}))
    assert rcf.is_old_shape(_stored("CONNECT_4", format_payload={}))
    assert not rcf.is_old_shape(_stored("CONNECT", format_payload={"extraction": {"confidence": 0.9}}))
    assert not rcf.is_old_shape(_stored("STANDARD_QA", format_payload={}))


def test_plan_row_replaces_answer_and_drops_the_format_tag_and_clears_the_embedding():
    ch = rcf.plan_row(_stored(), _new(), rewrite=lambda o: o)
    assert ch["answer"] == "A → B" and ch["question_text"].startswith("Q (asc)")
    assert ch["tags"] == ["Startups"]
    assert ch["embedding"] is None and ch["embedding_model"] is None
    assert ch["format_payload"]["extraction"] and ch["question_images"] == ["u1"]
    assert ch["content_hash"] != "h"


def test_connect_4_becomes_connect():
    ch = rcf.plan_row(_stored("CONNECT_4", format_payload={}), _new("CONNECT", answer="Bangle"), rewrite=lambda o: o)
    assert ch["question_format"] == "CONNECT" and ch["question_type"] == "CONNECT"


def test_arrange_is_always_replaced_but_others_only_when_the_new_reading_is_clean():
    assert rcf.accept_new("ARRANGE_ORDER", _new(review=True))
    assert rcf.accept_new("LIST_IT", _new("LIST_IT", answer="a; b"))
    assert not rcf.accept_new("LIST_IT", _new("LIST_IT", answer="a; b", review=True))
    assert not rcf.accept_new("IMAGE_REVEAL", _new("IMAGE_REVEAL", answer=""))


def test_matching_uses_the_format_family_and_the_nearest_slide_once():
    cands = [_new("LIST_IT", slide=10), _new("ARRANGE_ORDER", slide=8), _new("ARRANGE_ORDER", slide=7)]
    i, c = rcf.match_new(_stored(), 7, cands, set())
    assert i == 2
    i2, c2 = rcf.match_new(_stored(), 7, cands, {2})
    assert i2 == 1                                  # a used candidate is skipped; slide 8 is within 2 of 7
    assert rcf.match_new(_stored(), 20, cands, set()) == (None, None)
    assert rcf.match_new(_stored("CONNECT_4"), 10, [_new("CONNECT", slide=10)], set())[0] == 0


def test_a_mislabelled_plain_question_is_matched_by_its_text_and_needs_a_clean_new_reading():
    stored = _stored("LIST_IT", format_payload={"type": "LIST_IT"}, question_text="This is the closeup view of which everyday object?")
    plain = _new("STANDARD_QA", answer="Ball point pen", slide=60)
    plain["question_text"] = "This is the closeup view of which everyday object? "
    unrelated = _new("STANDARD_QA", answer="x", slide=61)
    unrelated["question_text"] = "Narinder Singh Kapany is known for fibre optics"
    i, c = rcf.match_new(stored, 59, [unrelated, plain], set())
    assert i == 1 and c is plain
    assert rcf.match_new(stored, 59, [unrelated], set()) == (None, None)
    assert rcf.accept_new("LIST_IT", plain)
    assert not rcf.accept_new("LIST_IT", _new("STANDARD_QA", answer="x", review=True))
    assert not rcf.accept_new("ARRANGE_ORDER", _new("STANDARD_QA", answer="x", review=True))   # cross-format needs a clean reading


@pytest.mark.parametrize("title,expected", [
    ("Districts of Madhya Pradesh in the order of contribution to GDP (Highest to Lowest)", "desc"),
    ("Price of Masala Dosa at different restaurants (from lowest to highest)", "asc"),
    ("Founding year of Bengaluru based startups (earliest to latest)", "asc"),
    ("Airports in Kerala by year of establishment (oldest to newest)", "asc"),
    ("Countries by quantity of tea produced (Highest to Lowest)", "desc"),
    ("Arrange these", None),
])
def test_direction_is_read_from_the_x_to_y_phrase_even_after_another_to(title, expected):
    assert fe.direction_from_title(title) == expected


def test_a_mislabelled_plain_question_keeps_its_own_text_and_answer():
    stored = _stored("LIST_IT", format_payload={"type": "LIST_IT"}, question_text="Which object is this?", answer="Floppy Disc")
    new = _new("STANDARD_QA", answer="Floppy Disc", slide=47)
    new["question_text"] = "Team Presentations Feedback time Which object is this?"      # neighbouring slide text merged in
    ch = rcf.plan_row(stored, new, rewrite=lambda o: o, keep_text=True)
    assert ch["question_format"] == "STANDARD_QA" and "question_text" not in ch and "answer" not in ch
    assert "content_hash" not in ch and ch["embedding"] is None


def test_an_arrange_reading_replaces_a_mislabelled_row_even_when_its_order_is_not_computable():
    unsure = _new("ARRANGE_ORDER", answer="Correct order not recorded", review=True)
    assert rcf.accept_new("CONNECT", unsure)                 # the stored "Slide 52" answer is wrong anyway
    assert not rcf.accept_new("CONNECT", _new("STANDARD_QA", answer="x", review=True))


@pytest.mark.parametrize("title,expected", [
    ("Digital payment methods by transaction cost for merchants (Lowest → Highest)", "asc"),
    ("Banks by branches (Highest → Lowest)", "desc"),
    ("Banks by branches (highest -> lowest)", "desc"),
])
def test_direction_understands_an_arrow(title, expected):
    assert fe.direction_from_title(title) == expected
