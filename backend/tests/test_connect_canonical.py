import io

from pptx import Presentation

from backend.app.services.export.exporter import QuizExporter
from backend.app.services.export import format_slides
from backend.scripts.fix_connect_payloads import canonical_connect_payload, is_canonical


def test_legacy_connect4_payload_is_made_canonical_and_timer_dropped():
    legacy = {
        "type": "CONNECT_4",
        "elements": [
            {"label": "A", "image": "http://img/a.png"},
            {"label": "B", "image": "http://img/timer.gif"},  # the countdown timer
            {"label": "C", "image": "http://img/c.png"},
            {"label": "D", "image": "http://img/d.png"},
        ],
        "common_link": "Reserve Bank of India",
    }
    assert format_slides.export_format_of(legacy) is None            # rejected before the fix
    out = canonical_connect_payload(legacy, "", [], "Reserve Bank of India")
    assert is_canonical(out)
    assert format_slides.export_format_of(out) == "CONNECT"
    imgs = [it["image"] for it in out["items"]]
    assert imgs == ["http://img/a.png", "http://img/c.png", "http://img/d.png"]   # timer removed
    assert out["answer"] == "Reserve Bank of India"
    assert "elements" not in out and "common_link" not in out


def test_connect_items_built_from_question_images_when_payload_has_none():
    out = canonical_connect_payload(
        {"type": "CONNECT_4", "common_link": "Mint"}, "",
        ["http://img/1.png", "http://img/timer-60.mp4", "http://img/2.png", "http://img/3.png", "http://img/4.png"],
        "Mint",
    )
    assert is_canonical(out)
    assert [it["order"] for it in out["items"]] == [1, 2, 3, 4]
    assert all("timer" not in it["image"] for it in out["items"])


def test_is_canonical_idempotent():
    out = canonical_connect_payload({"type": "CONNECT_4", "elements": [{"image": "http://img/a.png"}]}, "", [], "X")
    assert is_canonical(out)
    again = canonical_connect_payload(out, "", [], "X")
    assert [it["image"] for it in again["items"]] == [it["image"] for it in out["items"]]


def test_export_puts_connect_grid_and_answer_on_separate_slides():
    payload = canonical_connect_payload(
        {"type": "CONNECT_4", "text_hint": "What connects these?",
         "elements": [{"image": f"http://img/{c}.png"} for c in "abcd"]},
        "", [], "Reserve Bank of India",
    )
    quiz = {"title": "t", "topic": "Finance", "questions": [
        {"question_text": "[Connect · Common Link]\nWhat connects these?", "answer": "Reserve Bank of India",
         "question_format": "CONNECT", "format_payload": payload, "is_approved": True}]}
    prs = Presentation(io.BytesIO(QuizExporter.to_pptx(quiz, quiz_type="qshala_school")))
    texts = ["\n".join(s.text_frame.text for s in sl.shapes if s.has_text_frame) for sl in prs.slides]
    # the connect grid slide shows "Connect" + the hint, and the answer is on a different slide
    grid = [t for t in texts if "Connect" in t and "Reserve Bank" not in t]
    answer = [t for t in texts if "Reserve Bank of India" in t]
    assert grid and answer
    assert not any(("What connects these?" in t and "Reserve Bank of India" in t) for t in texts)
