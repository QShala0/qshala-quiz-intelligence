"""A picture the question already shows is a question picture and is listed once; the answer keeps only new pictures."""
from backend.app.services.ingestion.picture_sides import separate_picture_sides
from backend.app.services.ingestion.question_extractor import QuestionExtractor


def _slide(no, kind, title, text, imgs=()):
    return {"slide_number": no, "slide_type": kind, "title": title, "extracted_text": text, "speaker_notes": "",
            "image_paths": list(imgs), "video_paths": [], "has_images": bool(imgs), "metadata_json": {}}


def _deck(q_imgs, a_imgs, answer_text="Balance Sheet"):
    return [
        _slide(1, "QUESTION", "1.", "1.\n\nThe book Romancing the ________ is about finance. Fill in the blank.", q_imgs),
        _slide(2, "ANSWER_TRANSITION", "ANSWER", "ANSWER"),
        _slide(3, "ANSWER", answer_text, answer_text, a_imgs),
    ]


def _extract(slides):
    return QuestionExtractor().extract_from_slides(slides, "t", 2026)


# ----------------------------------------------------------------------------------------------- the rule itself

def test_repeated_picture_is_listed_once_as_a_question_picture():
    q = {"answer": "Balance Sheet", "question_images": ["a.png"], "answer_images": ["a.png"], "image_refs": ["a.png"]}
    separate_picture_sides(q)
    assert q["question_images"] == ["a.png"] and q["answer_images"] == [] and q["image_refs"] == ["a.png"]


def test_new_answer_picture_is_kept_and_the_repeat_is_dropped():
    q = {"answer": "Surf Excel", "question_images": ["ad.png"], "answer_images": ["ad.png", "logo.png"]}
    separate_picture_sides(q)
    assert q["answer_images"] == ["logo.png"]


def test_a_picture_that_only_the_answer_has_is_untouched():
    q = {"answer": "Surf Excel", "question_images": [], "answer_images": ["logo.png"]}
    separate_picture_sides(q)
    assert q["answer_images"] == ["logo.png"] and q["question_images"] == []


def test_answer_slide_with_no_text_keeps_its_repeated_picture():
    """No usable answer text: the picture is the answer, so it must not be dropped."""
    q = {"answer": "", "question_images": ["a.png"], "answer_images": ["a.png"]}
    separate_picture_sides(q)
    assert q["answer_images"] == ["a.png"]
    q2 = {"answer": "See image", "question_images": ["a.png"], "answer_images": ["a.png"]}
    separate_picture_sides(q2)
    assert q2["answer_images"] == ["a.png"]


def test_a_genuine_question_picture_is_never_dropped():
    q = {"answer": "x", "question_images": ["a.png", "b.png"], "answer_images": ["a.png", "b.png", "c.png"]}
    separate_picture_sides(q)
    assert q["question_images"] == ["a.png", "b.png"] and q["answer_images"] == ["c.png"]


def test_switch_off_restores_old_behaviour(monkeypatch):
    monkeypatch.setenv("PICTURE_SIDE_RULES_ENABLED", "0")
    q = {"answer": "x", "question_images": ["a.png"], "answer_images": ["a.png"]}
    separate_picture_sides(q)
    assert q["answer_images"] == ["a.png"]


# ----------------------------------------------------------------------------------------------- through the extractor

def test_extractor_does_not_list_a_repeated_picture_on_both_sides():
    qs = _extract(_deck(["a.png"], ["a.png"]))
    assert len(qs) == 1
    q = qs[0]
    assert q["question_images"] == ["a.png"]
    assert q["answer_images"] == []
    assert q["image_refs"] == ["a.png"]


def test_extractor_keeps_the_new_answer_picture():
    q = _extract(_deck(["ad.png"], ["ad.png", "logo.png"], "Surf Excel"))[0]
    assert q["question_images"] == ["ad.png"]
    assert q["answer_images"] == ["logo.png"]
    assert set(q["image_refs"]) == {"ad.png", "logo.png"}


def test_legacy_extractor_applies_the_same_rule():
    qs = QuestionExtractor().extract_from_slides(_deck(["a.png"], ["a.png"]), "t", 2026, use_structured=False)
    assert qs and all(not (set(q["question_images"]) & set(q["answer_images"])) for q in qs)


# ----------------------------------------------------------------------------------------------- the backfill's planning

class _Q:
    def __init__(self, id, qi, ai, answer="Some answer", fmt="STANDARD_QA"):
        self.id, self.question_images, self.answer_images, self.answer = id, qi, ai, answer
        self.question_format, self.document_id = fmt, "d1"


def test_backfill_plan_changes_only_what_the_rule_allows():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "fix_picture_sides", Path(__file__).resolve().parents[1] / "scripts" / "fix_picture_sides.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    qs = [
        _Q("a", ["1.png"], ["1.png", "2.png"]),                 # repeat dropped, new kept
        _Q("b", ["1.png"], ["1.png"]),                          # repeat dropped (real answer text)
        _Q("c", ["1.png"], ["1.png"], answer=""),               # picture is the answer: untouched
        _Q("d", ["1.png"], ["1.png"]),                          # a person edited its pictures: untouched
        _Q("e", ["1.png"], ["9.png"]),                          # no overlap: not a candidate
    ]
    rows, counts = mod.plan_changes(qs, edited_ids={"d"})
    assert {r["question_id"]: r["new_answer_images"] for r in rows} == {"a": ["2.png"], "b": []}
    assert counts == {"overlapping": 4, "skipped_human_edited": 1, "skipped_picture_is_the_answer": 1, "to_change": 2}
