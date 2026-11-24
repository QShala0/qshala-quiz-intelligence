"""Regression tests for custom PPT templates (extract from a sample deck -> store -> export).

Each test pins a failure that actually broke production exports:
  * template files vanished after a Cloud Run restart and exports silently used the default deck
  * a slide master other than the first one was invisible, so a detected layout "did not exist"
  * a big question photo was mistaken for the template background; GIF backgrounds were never loaded
  * the saved master dragged the sample deck's media along (37 MB master from a 90 MB deck)
  * 10 x 5.625 / 4:3 templates got shapes laid out for 13.333 x 7.5 and ran off the slide
  * empty "Click to add title" placeholders were left on every generated slide
"""
import io
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pptx import Presentation
from pptx.util import Emu, Inches

from backend.app.main import app
from backend.app.services.export import template_extractor, template_store
from backend.app.services.export.exporter import _all_layouts, _find_layout
from backend.app.services.export.slide_fit import fit_deck_to_slide, remove_empty_placeholders
from backend.app.services.export.template_extractor import TemplateExtractor

client = TestClient(app)
SAMPLE_DECK = Path("backend/sample_decks/Australian_History_Quiz_2022.pptx")
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

QUIZ = {
    "id": "robust-1",
    "title": "Robustness Quiz",
    "topic": "History",
    "questions": [
        {"question_text": "Who explored Botany Bay in 1770? " * 3, "answer": "Captain James Cook",
         "explanation": "He mapped the east coast.", "is_approved": True},
        {"question_text": "Which ocean is the largest?", "answer": "Pacific",
         "options": ["Atlantic", "Pacific", "Indian", "Arctic"], "is_approved": True},
    ],
}


def _png(color, size=(320, 180), fmt="PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt)
    return buf.getvalue()


def _deck(width_in=13.333, height_in=7.5, content_slides=3, bg_bytes=None, odd_photo=None) -> bytes:
    """A small sample deck: cover (layout 0) + question slides (layout 1, 'Title and Content') + an 'Answer' slide."""
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(width_in), Inches(height_in)
    cover = prs.slides.add_slide(prs.slide_layouts[0])
    cover.shapes.title.text = "Sample Quiz"
    for i in range(content_slides):
        sl = prs.slides.add_slide(prs.slide_layouts[1])
        sl.shapes.title.text = f"Question {i + 1}"
        if bg_bytes:
            sl.shapes.add_picture(io.BytesIO(bg_bytes), 0, 0, prs.slide_width, prs.slide_height)
    if odd_photo:
        sl = prs.slides.add_slide(prs.slide_layouts[1])
        sl.shapes.add_picture(io.BytesIO(odd_photo), 0, 0, prs.slide_width, prs.slide_height)
    div = prs.slides.add_slide(prs.slide_layouts[6])
    tb = div.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1))
    tb.text_frame.text = "Answer"
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


class FakeR2:
    """In-memory stand-in for CloudflareR2Service (only what template_store uses)."""
    def __init__(self):
        self.objects = {}

    def upload_file(self, key, path, content_type=None):
        self.objects[key] = Path(path).read_bytes()
        return key

    def download_to_file(self, key, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.objects[key])
        return Path(dest)

    def delete_prefix(self, prefix):
        gone = [k for k in self.objects if k.startswith(prefix)]
        for k in gone:
            del self.objects[k]
        return len(gone)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Route template storage to a temp dir + a fake R2, like production but hermetic."""
    r2 = FakeR2()
    monkeypatch.setattr(template_store, "CUSTOM_TEMPLATES_DIR", tmp_path / "tpl")
    monkeypatch.setattr(template_extractor, "CUSTOM_TEMPLATES_DIR", tmp_path / "tpl")
    monkeypatch.setattr(template_store, "_storage", lambda: r2)
    return SimpleNamespace(r2=r2, dir=tmp_path / "tpl")


def _upload(deck_bytes: bytes, name="Robust Theme") -> dict:
    r = client.post("/api/v1/templates/extract",
                    files={"file": ("sample.pptx", deck_bytes, PPTX_MIME)},
                    data={"name": name, "category": "qshala_school"})
    assert r.status_code == 200, r.text
    return r.json()


def _export(tpl_id: str, quiz=QUIZ):
    return client.post(f"/api/v1/quizzes/export?format=pptx&template_id={tpl_id}", json=quiz)


def _overflowing(prs):
    W, H = prs.slide_width, prs.slide_height
    bad = []
    for i, s in enumerate(prs.slides):
        for sh in s.shapes:
            if sh.left is None:
                continue
            if sh.left < -0.011 * W or sh.top < -0.011 * H or sh.left + sh.width > 1.011 * W or sh.top + sh.height > 1.011 * H:
                bad.append((i, sh.name))
    return bad


# ---------------------------------------------------------------- durability (the "keeps breaking" bug)

def test_template_survives_a_server_restart(store):
    tpl = _upload(_deck())
    assert "master.pptx" in store.r2.objects.__iter__().__next__() or any(k.endswith("master.pptx") for k in store.r2.objects)

    shutil.rmtree(store.dir)  # Cloud Run restart: local disk wiped, database row and R2 survive
    assert not store.dir.exists()

    listed = {t["id"]: t for t in client.get("/api/v1/templates").json()}
    assert listed[tpl["id"]]["files_ok"] is True

    resp = _export(tpl["id"])
    assert resp.status_code == 200
    assert len(Presentation(io.BytesIO(resp.content)).slides) >= 3
    assert (store.dir / tpl["id"] / "master.pptx").exists()  # restored from R2

    shutil.rmtree(store.dir)
    assert client.get(f"/api/v1/templates/{tpl['id']}/download").status_code == 200
    shutil.rmtree(store.dir)
    assert client.get(f"/api/v1/templates/{tpl['id']}/preview?slide=cover").status_code == 200
    assert client.delete(f"/api/v1/templates/{tpl['id']}").status_code == 200
    assert not any(k.startswith(template_store.r2_prefix(tpl["id"])) for k in store.r2.objects)


def test_unrecoverable_template_fails_loudly_instead_of_exporting_the_default_deck(store, monkeypatch):
    tpl = _upload(_deck())
    shutil.rmtree(store.dir)
    # A template saved before durable storage existed / on a server without R2: nothing to restore from.
    monkeypatch.setattr(template_store, "_storage", lambda: None)

    listed = {t["id"]: t for t in client.get("/api/v1/templates").json()}
    assert listed[tpl["id"]]["files_ok"] is False
    resp = _export(tpl["id"])
    assert resp.status_code == 409
    assert "Upload the sample deck again" in resp.json()["detail"]
    assert client.get(f"/api/v1/templates/{tpl['id']}/download").status_code == 409
    client.delete(f"/api/v1/templates/{tpl['id']}")


def test_unknown_template_id_is_a_404_not_a_default_deck():
    resp = _export("does-not-exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------- extraction quality

def test_master_has_no_sample_slides_and_stays_small(tmp_path):
    big_photo = _png((10, 20, 30), size=(2400, 1350))
    deck = _deck(odd_photo=big_photo)
    data = TemplateExtractor(deck).extract("t", output_dir=tmp_path / "m")
    master = Presentation(data["storage_path"])
    assert len(master.slides) == 0
    assert len(list(master.slide_layouts)) >= 1
    assert data["file_size_bytes"] < len(deck)  # sample content is not carried along


def test_question_photo_is_not_mistaken_for_the_template_background(tmp_path):
    art = _png((255, 200, 0), size=(640, 360))
    photo = _png((12, 99, 200), size=(640, 360))
    deck = _deck(bg_bytes=art, odd_photo=photo)
    out = tmp_path / "bg"
    data = TemplateExtractor(deck).extract("t", output_dir=out)
    q_file = out / data["layout_metadata"]["backgrounds"]["question_bg"]
    with Image.open(q_file) as im:
        assert im.convert("RGB").getpixel((5, 5)) == (255, 200, 0)  # the repeated art, not the one-off photo


def test_gif_background_is_converted_so_the_exporter_can_load_it(tmp_path):
    gif = _png((0, 160, 80), size=(640, 360), fmt="GIF")
    data = TemplateExtractor(_deck(bg_bytes=gif)).extract("t", output_dir=tmp_path / "g")
    names = set(data["layout_metadata"]["backgrounds"].values())
    assert names and all(n.endswith((".png", ".jpg")) for n in names)
    for n in names:
        assert (tmp_path / "g" / n).exists()


def test_question_layout_is_the_one_the_deck_actually_uses(tmp_path):
    data = TemplateExtractor(_deck(content_slides=4)).extract("t", output_dir=tmp_path / "l")
    lay = data["layout_metadata"]["layouts"]
    assert lay["question_layout"] == "Title and Content"
    assert lay["cover_layout"] == "Title Slide"


def test_layouts_in_secondary_masters_are_found():
    layout_a = SimpleNamespace(name="Title Slide")
    layout_b = SimpleNamespace(name="1_Title and Content")
    prs = SimpleNamespace(
        slide_layouts=[layout_a],  # what python-pptx exposes: first master only
        slide_masters=[SimpleNamespace(slide_layouts=[layout_a]), SimpleNamespace(slide_layouts=[layout_b])],
    )
    assert _find_layout(prs, "1_Title and Content") is layout_b
    assert _find_layout(prs, "nope") is None
    assert [l.name for l in _all_layouts(prs)] == ["Title Slide", "1_Title and Content"]


# ---------------------------------------------------------------- export on non-standard templates

@pytest.mark.parametrize("w,h", [(10.0, 5.625), (10.0, 7.5), (13.333, 7.5)])
def test_export_fits_any_template_slide_size(store, w, h):
    bg = _png((250, 230, 150), size=(640, 360))
    tpl = _upload(_deck(width_in=w, height_in=h, bg_bytes=bg))
    resp = _export(tpl["id"])
    assert resp.status_code == 200
    prs = Presentation(io.BytesIO(resp.content))
    assert abs(prs.slide_width / 914400 - w) < 0.01 and abs(prs.slide_height / 914400 - h) < 0.01
    assert _overflowing(prs) == []
    client.delete(f"/api/v1/templates/{tpl['id']}")


def test_export_leaves_no_empty_placeholders_and_no_default_artwork(store):
    tpl = _upload(_deck())  # plain template with NO background art
    prs = Presentation(io.BytesIO(_export(tpl["id"]).content))
    for slide in prs.slides:
        for sh in slide.placeholders:
            if sh.has_text_frame:
                assert sh.text_frame.text.strip(), f"empty placeholder {sh.name} left on a slide"
        # a custom template must not get the built-in QShala/ZVQ artwork pasted over it
        assert not [s for s in slide.shapes if s.shape_type == 13 and s.width >= prs.slide_width * 0.95]
    client.delete(f"/api/v1/templates/{tpl['id']}")


def test_fit_deck_scales_text_and_snaps_backgrounds():
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(10), Inches(5.625)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    pic = slide.shapes.add_picture(io.BytesIO(_png((1, 2, 3))), 0, 0, Inches(13.333), Inches(7.5))
    tb = slide.shapes.add_textbox(Inches(1.0), Inches(1.0), Inches(11.333), Inches(2.0))
    tb.text_frame.text = "hello"
    tb.text_frame.paragraphs[0].runs[0].font.size = Emu(28 * 12700)
    assert fit_deck_to_slide(prs) is True
    assert (pic.left, pic.top, pic.width, pic.height) == (0, 0, prs.slide_width, prs.slide_height)
    assert abs(tb.width - Inches(11.333) * 0.75) < 2000
    assert abs(tb.text_frame.paragraphs[0].runs[0].font.size.pt - 21.0) < 0.6
    assert _overflowing(prs) == []


def test_fit_is_a_noop_for_the_standard_size_and_remove_empty_placeholders_counts():
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[1])  # title + body placeholders, both empty
    assert fit_deck_to_slide(prs) is False
    assert remove_empty_placeholders(prs) == 2
    assert len(list(slide.placeholders)) == 0


def test_real_sample_deck_exports_inside_its_slide(store):
    assert SAMPLE_DECK.exists()
    tpl = _upload(SAMPLE_DECK.read_bytes(), name="Aussie")
    prs = Presentation(io.BytesIO(_export(tpl["id"]).content))
    assert _overflowing(prs) == []
    client.delete(f"/api/v1/templates/{tpl['id']}")
