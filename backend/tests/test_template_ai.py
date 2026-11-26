"""AI-assisted template role detection: digest stays small, bad model output is dropped, failure falls back to rules."""
import io

import pytest
from pptx import Presentation
from pptx.util import Inches

from backend.app.services.export import template_ai
from backend.app.services.export.template_extractor import TemplateExtractor, _short_answer_slide


def _deck(n_content=12, divider_text="उत्तर", with_english_divider=False) -> bytes:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    title_layout, content_layout, blank = prs.slide_layouts[0], prs.slide_layouts[1], prs.slide_layouts[6]
    s = prs.slides.add_slide(title_layout)
    s.shapes.title.text = "Quiz Night"
    for i in range(n_content):
        sl = prs.slides.add_slide(content_layout)
        sl.shapes.title.text = f"Question {i + 1}"
        if i == 3:
            d = prs.slides.add_slide(blank)
            tb = d.shapes.add_textbox(Inches(1), Inches(3), Inches(6), Inches(1))
            tb.text_frame.text = "Answer" if with_english_divider else divider_text
    return prs_bytes(prs)


def prs_bytes(prs) -> bytes:
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def test_digest_is_capped_and_has_no_images():
    prs = Presentation(io.BytesIO(_deck(n_content=40)))
    slides = list(prs.slides)
    idx = template_ai.candidate_indexes(slides, _short_answer_slide)
    assert len(idx) <= template_ai.MAX_DIGEST_SLIDES and 0 in idx
    digest = template_ai.build_digest(slides, idx, 13.33, 7.5)
    assert all(sh["kind"] != "picture" for s in digest["slides"] for sh in s["shapes"])
    assert len(str(digest)) < 6000


def test_validate_drops_bad_fields():
    raw = {
        "cover_idx": 0, "question_idx": 99, "answer_reveal_idx": 0, "answer_idx": True,
        "question_box": {"left": 1, "top": 1, "width": 50, "height": 3},   # outside slide
        "answer_box": {"left": 1, "top": 1.2, "width": 10, "height": 4},
        "spacing_pt": {"title_to_body": 500, "between_options": 12},
    }
    out = template_ai.validate(raw, [0, 1, 2], 13.33, 7.5)
    assert out["cover_idx"] == 0
    assert "question_idx" not in out and "answer_idx" not in out
    assert "answer_reveal_idx" not in out          # cover cannot be the reveal slide
    assert "question_box" not in out and out["answer_box"]["width"] == 10
    assert out["spacing_pt"] == {"between_options": 12.0}


def test_failure_falls_back_to_rules(monkeypatch, tmp_path):
    monkeypatch.setattr(template_ai, "_call_gemini", lambda d: None)
    data = TemplateExtractor(_deck(with_english_divider=True)).extract("t", output_dir=tmp_path / "a")
    meta = data["layout_metadata"]["layouts"]
    assert meta["ai_used"] is False
    assert meta["divider_layout"] is not None


def test_ai_finds_non_english_divider(monkeypatch, tmp_path):
    prs = Presentation(io.BytesIO(_deck()))
    slides = list(prs.slides)
    reveal = next(i for i, s in enumerate(slides) if any(
        sh.has_text_frame and sh.text_frame.text == "उत्तर" for sh in s.shapes))

    def fake(digest):
        assert reveal in [s["i"] for s in digest["slides"]]
        return {"answer_reveal_idx": reveal, "answer_box": {"left": 1.0, "top": 2.0, "width": 10.0, "height": 3.0},
                "spacing_pt": {"title_to_body": 18}}

    monkeypatch.setattr(template_ai, "_call_gemini", fake)
    data = TemplateExtractor(_deck()).extract("t", output_dir=tmp_path / "b")
    meta = data["layout_metadata"]["layouts"]
    assert meta["ai_used"] is True
    assert meta["divider_deck_index"] == reveal
    assert meta["divider_slide_index"] is not None          # the reveal slide was kept in the clean master
    assert meta["geometry"]["answer_box"]["top"] == 2.0
    assert meta["spacing_pt"]["title_to_body"] == 18.0
    assert data["title_size_pt"] == 44.0 and data["body_size_pt"] == 28.0


def test_rule_divider_is_not_overridden(monkeypatch, tmp_path):
    monkeypatch.setattr(template_ai, "_call_gemini", lambda d: {"answer_reveal_idx": 2})
    data = TemplateExtractor(_deck(with_english_divider=True)).extract("t", output_dir=tmp_path / "c")
    assert "divider_deck_index" not in data["layout_metadata"]["layouts"]


def test_no_api_key_means_no_call():
    # conftest clears the key; the real call path must return None quietly
    assert template_ai._call_gemini({"slides": []}) is None
