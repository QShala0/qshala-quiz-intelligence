"""Custom templates: the export must reuse the template's REAL title slide and Answer divider.

Before, extraction deleted every sample slide and the exporter rebuilt the cover from scratch on a layout, so anything
drawn on the actual title slide (logo, shapes, text boxes) vanished from the downloaded deck.
"""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
from pptx.util import Inches, Pt

from backend.app.main import app
from backend.app.services.export.slide_clone import (
    drop_slide, duplicate_slide, fill_text, find_title_and_subtitle,
)
from backend.app.services.export.template_extractor import TemplateExtractor
from backend.tests.test_template_robustness import (  # noqa: F401  (store is a pytest fixture)
    QUIZ, _export, _upload, store,
)

client = TestClient(app)


@pytest.fixture
def tpl_factory(store):
    """Upload templates and ALWAYS delete them afterwards (a leftover row would be picked up by other tests)."""
    made = []

    def make(deck: bytes):
        tpl = _upload(deck, name="Cover Fidelity Theme")
        made.append(tpl["id"])
        return tpl

    yield make
    for tid in made:
        client.delete(f"/api/v1/templates/{tid}")


def _png(color, size=(200, 100)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _styled_template() -> bytes:
    """A template whose title slide has art living ON the slide (logo picture, a coloured band, a tagline text box),
    and an 'Answer' divider slide with its own band. Other slides are plain."""
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)

    cover = prs.slides.add_slide(prs.slide_layouts[6])  # blank layout: nothing but what we draw on the slide
    band = cover.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, Inches(5.5), prs.slide_width, Inches(2.0))
    band.name = "CoverBand"
    band.fill.solid()
    band.fill.fore_color.rgb = RGBColor(0xC0, 0x1E, 0x5A)
    logo = cover.shapes.add_picture(io.BytesIO(_png((10, 120, 200))), Inches(0.5), Inches(0.4), Inches(2), Inches(1))
    logo.name = "BrandLogo"
    title = cover.shapes.add_textbox(Inches(1.5), Inches(2.4), Inches(10), Inches(1.5))
    title.name = "BigTitle"
    title.text_frame.text = "Sample Template Title"
    r = title.text_frame.paragraphs[0].runs[0]
    r.font.size, r.font.bold, r.font.name = Pt(54), True, "Georgia"
    r.font.color.rgb = RGBColor(0x11, 0x22, 0x66)
    tag = cover.shapes.add_textbox(Inches(1.5), Inches(4.0), Inches(10), Inches(0.8))
    tag.name = "Tagline"
    tag.text_frame.text = "Sample tagline"
    tag.text_frame.paragraphs[0].runs[0].font.size = Pt(24)

    for i in range(2):
        sl = prs.slides.add_slide(prs.slide_layouts[1])
        sl.shapes.title.text = f"Question {i + 1}"

    div = prs.slides.add_slide(prs.slide_layouts[6])
    dband = div.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(3), prs.slide_height)
    dband.name = "DividerBand"
    dtb = div.shapes.add_textbox(Inches(4), Inches(3), Inches(5), Inches(1.2))
    dtb.text_frame.text = "Answer"
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


def _names(slide):
    return {s.name for s in slide.shapes}


def _text(slide):
    return " | ".join(s.text_frame.text for s in slide.shapes if s.has_text_frame and s.text_frame.text.strip())


# ------------------------------------------------------------------ extraction

def test_extractor_keeps_cover_and_divider_slides(tmp_path):
    data = TemplateExtractor(_styled_template()).extract("t", output_dir=tmp_path / "m")
    layouts = data["layout_metadata"]["layouts"]
    assert layouts["cover_slide_index"] == 0
    assert layouts["divider_slide_index"] == 1
    master = Presentation(data["storage_path"])
    assert len(master.slides) == 2
    assert {"CoverBand", "BrandLogo", "BigTitle"} <= _names(master.slides[0])
    assert "DividerBand" in _names(master.slides[1])


def test_extractor_without_divider_records_none(tmp_path):
    prs = Presentation()
    prs.slides.add_slide(prs.slide_layouts[0]).shapes.title.text = "Only a cover"
    buf = io.BytesIO()
    prs.save(buf)
    data = TemplateExtractor(buf.getvalue()).extract("t", output_dir=tmp_path / "m")
    assert data["layout_metadata"]["layouts"]["divider_slide_index"] is None
    assert data["layout_metadata"]["layouts"]["cover_slide_index"] == 0


# ------------------------------------------------------------------ export

def test_export_uses_the_real_title_slide(tpl_factory):
    tpl = tpl_factory(_styled_template())
    resp = _export(tpl["id"])
    assert resp.status_code == 200
    prs = Presentation(io.BytesIO(resp.content))
    cover = prs.slides[0]
    # art that lives on the template's own slide survives
    assert {"CoverBand", "BrandLogo"} <= _names(cover)
    # the quiz title replaces the sample title, in the same shape and with the template's own formatting
    big = next(s for s in cover.shapes if s.name == "BigTitle")
    assert big.text_frame.text == "Robustness Quiz"
    run = big.text_frame.paragraphs[0].runs[0]
    assert run.font.name == "Georgia" and run.font.size == Pt(54) and run.font.bold is True
    assert str(run.font.color.rgb) == "112266"
    # untouched when no subtitle was supplied
    assert "Sample tagline" in _text(cover)


def test_export_fills_subtitle_when_given(tpl_factory):
    tpl = tpl_factory(_styled_template())
    resp = client.post(
        f"/api/v1/quizzes/export?format=pptx&template_id={tpl['id']}&custom_subtitle=Grade%206%20Finals",
        json=QUIZ,
    )
    assert resp.status_code == 200
    cover = Presentation(io.BytesIO(resp.content)).slides[0]
    assert "Grade 6 Finals" in _text(cover)
    assert "Sample tagline" not in _text(cover)


def test_every_answer_divider_is_a_copy_of_the_template_divider(tpl_factory):
    tpl = tpl_factory(_styled_template())
    resp = _export(tpl["id"])
    prs = Presentation(io.BytesIO(resp.content))
    dividers = [s for s in prs.slides if "DividerBand" in _names(s)]
    assert len(dividers) == len(QUIZ["questions"])     # one per question, the exemplar itself is not left behind
    assert all("Answer" in _text(d) for d in dividers)


def test_cover_is_not_restyled_by_custom_template_fonts(tpl_factory):
    tpl = tpl_factory(_styled_template())
    prs = Presentation(io.BytesIO(_export(tpl["id"]).content))
    tag = next(s for s in prs.slides[0].shapes if s.name == "Tagline")
    run = tag.text_frame.paragraphs[0].runs[0]
    assert run.font.size == Pt(24)       # not forced to a body size by _apply_custom_style


def test_template_saved_before_this_change_still_exports(tpl_factory):
    """Old templates have no cover_slide_index/divider_slide_index and an empty master: export from layouts."""
    tpl = tpl_factory(_styled_template())
    from backend.app.database import SessionLocal
    from backend.app.models.custom_template import CustomPptTemplate
    db = SessionLocal()
    try:
        row = db.query(CustomPptTemplate).filter(CustomPptTemplate.id == tpl["id"]).first()
        meta = dict(row.layout_metadata or {})
        layouts = dict(meta.get("layouts", {}))
        layouts.pop("cover_slide_index", None)
        layouts.pop("divider_slide_index", None)
        meta["layouts"] = layouts
        row.layout_metadata = meta
        db.commit()
    finally:
        db.close()
    resp = _export(tpl["id"])
    assert resp.status_code == 200
    prs = Presentation(io.BytesIO(resp.content))
    assert "Robustness Quiz" in _text(prs.slides[0])


# ------------------------------------------------------------------ helpers

def test_duplicate_slide_copies_pictures_and_background():
    prs = Presentation()
    src = prs.slides.add_slide(prs.slide_layouts[6])
    src.shapes.add_picture(io.BytesIO(_png((1, 2, 3))), 0, 0, Inches(2), Inches(1)).name = "Pic"
    src.background.fill.solid()
    src.background.fill.fore_color.rgb = RGBColor(9, 9, 9)
    clone = duplicate_slide(prs, src)
    pic = next(s for s in clone.shapes if s.name == "Pic")
    assert pic.shape_type == MSO_SHAPE_TYPE.PICTURE and pic.image.blob
    assert clone._element.find(".//{http://schemas.openxmlformats.org/presentationml/2006/main}bg") is not None
    buf = io.BytesIO()
    prs.save(buf)                        # package still valid with both slides
    assert len(Presentation(io.BytesIO(buf.getvalue())).slides) == 2
    drop_slide(prs, src)
    assert len(prs.slides) == 1


def test_fill_text_keeps_first_run_format_and_handles_multiple_lines():
    prs = Presentation()
    sl = prs.slides.add_slide(prs.slide_layouts[6])
    tb = sl.shapes.add_textbox(0, 0, Inches(5), Inches(1))
    tb.text_frame.text = "old text"
    tb.text_frame.paragraphs[0].runs[0].font.size = Pt(40)
    fill_text(tb, "line one\nline two")
    paras = tb.text_frame.paragraphs
    assert [p.text for p in paras] == ["line one", "line two"]
    assert all(p.runs[0].font.size == Pt(40) for p in paras)


def test_find_title_and_subtitle_prefers_placeholders_then_biggest_text():
    prs = Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[0])
    s1.shapes.title.text = "T"
    s1.placeholders[1].text = "S"
    t, sub = find_title_and_subtitle(s1)
    assert t.text_frame.text == "T" and sub.text_frame.text == "S"

    prs2 = Presentation()
    s2 = prs2.slides.add_slide(prs2.slide_layouts[6])
    small = s2.shapes.add_textbox(0, Inches(4), Inches(4), Inches(1))
    small.text_frame.text = "small"
    small.text_frame.paragraphs[0].runs[0].font.size = Pt(18)
    big = s2.shapes.add_textbox(0, Inches(1), Inches(4), Inches(1))
    big.text_frame.text = "big"
    big.text_frame.paragraphs[0].runs[0].font.size = Pt(60)
    t2, sub2 = find_title_and_subtitle(s2)
    assert t2.text_frame.text == "big" and sub2.text_frame.text == "small"
