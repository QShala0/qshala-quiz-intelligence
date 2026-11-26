"""Connect loop export: pure layout tests (structure/render tests for each template are added with the renderer)."""
import io
import itertools
import shutil
import uuid

import pytest
from PIL import Image, ImageDraw
from pptx import Presentation

from backend.app.config import settings
from backend.app.services.export.exporter import QuizExporter

from backend.app.services.export.connect_layout import Box, GAP, HINT_CLEAR, MAX_RING_ITEMS, connect_layout

SAFE = (0.62, 0.60, 12.07, 6.27)
S = Box(*SAFE)


@pytest.mark.parametrize("n,hint", list(itertools.product(range(1, MAX_RING_ITEMS + 1), [0, 1, 2])))
def test_ring_boxes_are_inside_and_do_not_collide(n, hint):
    L = connect_layout(n, hint, SAFE)
    assert L["fallback"] is None and len(L["nodes"]) == n and len(L["hints"]) == hint
    for b in L["nodes"] + L["hints"] + [L["title"]]:
        assert b.inside(S), b
    for a, b in itertools.combinations(L["nodes"], 2):
        assert not a.overlaps(b, GAP - 1e-6), (a, b)
    for h in L["hints"]:
        assert not any(h.overlaps(p, HINT_CLEAR - 1e-6) for p in L["nodes"]), h
    for a, b in itertools.combinations(L["hints"], 2):
        assert not a.overlaps(b)
    for p in L["nodes"] + L["hints"]:
        assert not L["title"].overlaps(p), (L["title"], p)


@pytest.mark.parametrize("n", [1])
def test_single_picture_has_no_loop(n):
    assert connect_layout(1, 1, SAFE)["loop"] is None


@pytest.mark.parametrize("n", [11, 12, 20, 0])
def test_too_many_pictures_use_the_grid_fallback(n):
    assert connect_layout(n, 1, SAFE)["fallback"] == "grid"


def test_deterministic():
    assert connect_layout(7, 1, SAFE) == connect_layout(7, 1, SAFE)


def test_hint_prefers_top_then_right_and_centre_when_full():
    assert connect_layout(4, 1, SAFE)["hint_sides"] == ["top"]
    assert connect_layout(6, 1, SAFE)["hint_sides"] == ["right"]
    assert connect_layout(8, 1, SAFE)["hint_sides"] == ["centre"]
    assert connect_layout(10, 1, SAFE)["hint_sides"] == ["centre"]


# ------------------------------------------------------------------------------------------------ structure per template

TEMPLATES = ["qshala_school", "school", "corporate", "minimalist"]
SAFE_BY_TEMPLATE = {
    "qshala_school": (0.62, 0.60, 12.07, 6.27), "minimalist": (0.62, 0.60, 12.07, 6.27),
    "school": (0.70, 1.65, 11.95, 4.45), "corporate": (0.80, 0.70, 9.40, 6.10),
}
SIZES = [(400, 300), (300, 400), (600, 200), (300, 300), (500, 350), (350, 500), (450, 450), (400, 250), (200, 600), (320, 240)]


@pytest.fixture()
def media():
    doc = f"memtest-connect-{uuid.uuid4().hex[:8]}"
    d = settings.SLIDES_DIR / doc / "media"
    d.mkdir(parents=True, exist_ok=True)

    def make(i, size=None, mode="RGB"):
        w, h = size or SIZES[i % len(SIZES)]
        im = Image.effect_noise((w, h), 80).convert(mode)          # noisy, so the ingest parser keeps it (tiny files are dropped)
        if mode == "RGB":
            im = Image.merge("RGB", (im.split()[0], Image.effect_noise((w, h), 60), Image.effect_noise((w, h), 70)))
        ImageDraw.Draw(im).text((5, 5), str(i), fill="white")
        name = f"p{uuid.uuid4().hex[:6]}_{i}.png"
        im.save(d / name)
        return f"/static/slides/{doc}/media/{name}"

    yield make, d
    shutil.rmtree(settings.SLIDES_DIR / doc, ignore_errors=True)


def connect_q(urls, hint="Meghdoot software", answer="SECRETANSWER"):
    return {"id": "c", "is_approved": True, "question_text": "Connect", "answer": answer, "explanation": "", "options": None,
            "question_type": "CONNECT",
            "format_payload": {"type": "CONNECT", "items": [{"order": i + 1, "image": u} for i, u in enumerate(urls)],
                               "text_hint": hint, "answer": answer, "extraction": {"confidence": 1}}}


def export(questions, quiz_type):
    quiz = {"id": "qz", "title": "t", "topic": "x", "difficulty": "Balanced", "questions": questions}
    return Presentation(io.BytesIO(QuizExporter.to_pptx(quiz, quiz_type=quiz_type, include_social_slides=False)))


def connect_slides(prs):
    """(question slide, divider, answer slide) of the first Connect question."""
    for i, s in enumerate(prs.slides):
        if any(sh.name == "QSHALA Loop" or sh.name.startswith("QSHALA Picture") for sh in s.shapes):
            return s, prs.slides[i + 1], prs.slides[i + 2]
    raise AssertionError("no Connect question slide")


def slide_text(s):
    return " ".join(sh.text_frame.text for sh in s.shapes if sh.has_text_frame)


def pic_shapes(s):
    return [sh for sh in s.shapes if sh.name.startswith("QSHALA Picture")]


@pytest.mark.parametrize("template", TEMPLATES)
@pytest.mark.parametrize("n,hint", [(2, "A hint"), (3, "A hint"), (4, None), (4, "Hint"), (6, "Hint"), (8, "Hint"), (9, None), (10, "Hint")])
def test_connect_slide_structure_and_bounds(media, template, n, hint):
    make, _ = media
    prs = export([connect_q([make(i) for i in range(n)], hint)], template)
    q, divider, ans = connect_slides(prs)
    W, H = prs.slide_width, prs.slide_height
    assert sorted(p.name for p in pic_shapes(q)) == sorted(f"QSHALA Picture {i}" for i in range(1, n + 1))
    assert [sh.text_frame.text for sh in q.shapes if sh.name == "Heading"] == ["1. Connect"]
    assert len([sh for sh in q.shapes if sh.name == "QSHALA Loop"]) == 1
    assert len([sh for sh in q.shapes if sh.name.startswith("QSHALA Hint")]) == (1 if hint else 0)
    assert "SECRETANSWER" not in slide_text(q) and not q.has_notes_slide
    assert "SECRETANSWER" in slide_text(ans) and "QSHALA-FORMAT" in ans.notes_slide.notes_text_frame.text
    assert not [ph for ph in q.placeholders if not ph.text_frame.text.strip()]
    sx, sy, sw, sh_ = SAFE_BY_TEMPLATE[template]
    lo_x, lo_y, hi_x, hi_y = [int(v * 914400) for v in (sx, sy, sx + sw, sy + sh_)]
    for sh in q.shapes:
        if sh.shape_type == 13 and not sh.name.startswith("QSHALA"):
            continue                                          # the full-slide backdrop picture
        assert 0 <= sh.left and 0 <= sh.top and sh.left + sh.width <= W + 10 and sh.top + sh.height <= H + 10
        assert sh.left >= lo_x - 50 and sh.top >= lo_y - 50 and sh.left + sh.width <= hi_x + 50 \
            and sh.top + sh.height <= hi_y + 50, (template, n, sh.name)


@pytest.mark.parametrize("template,has_bg", [("qshala_school", False), ("school", True), ("corporate", True), ("minimalist", False)])
def test_backdrop_matches_the_template(media, template, has_bg):
    make, _ = media
    q, _, _ = connect_slides(export([connect_q([make(i) for i in range(4)])], template))
    backs = [sh for sh in q.shapes if sh.shape_type == 13 and not sh.name.startswith("QSHALA")]
    assert bool(backs) is has_bg


def test_z_order_loop_then_cards_pictures_hint_title(media):
    make, _ = media
    q, _, _ = connect_slides(export([connect_q([make(i) for i in range(4)])], "minimalist"))
    names = [sh.name for sh in q.shapes]
    assert names.index("QSHALA Loop") < names.index("QSHALA Card 1") < names.index("QSHALA Picture 1") \
        < names.index("QSHALA Hint") < names.index("Heading")


def test_more_than_ten_pictures_use_the_grid(media):
    make, _ = media
    q, divider, ans = connect_slides(export([connect_q([make(i) for i in range(12)])], "minimalist"))
    assert len(pic_shapes(q)) == 12
    assert not [sh for sh in q.shapes if sh.name == "QSHALA Loop"]
    assert "SECRETANSWER" not in slide_text(q)


def test_edge_cases(media):
    make, d = media
    (d / "broken.png").write_bytes(b"not an image")
    urls = [make(0), f"/static/slides/{d.parent.name}/media/broken.png", "/static/slides/none/missing.png",
            make(1, (2000, 100)), make(2, (100, 2000)), make(3, (80, 80), "RGBA")]
    long_hint = "Devanagari हिन्दी पाठ " + "very long hint text " * 8
    q, _, _ = connect_slides(export([connect_q(urls + [urls[0]], long_hint)], "minimalist"))
    pics = pic_shapes(q)
    assert len(pics) == 5                                       # two bad pictures skipped, the repeated one kept
    assert len(pics) == len([sh for sh in q.shapes if sh.name.startswith("QSHALA Card")])      # no empty frames
    hint = [sh for sh in q.shapes if sh.name == "QSHALA Hint"][0]
    assert 18 <= hint.text_frame.paragraphs[0].runs[0].font.size.pt <= 28 and "हिन्दी" in hint.text_frame.text
    one, _, _ = connect_slides(export([connect_q([make(0)], None)], "minimalist"))
    assert not [sh for sh in one.shapes if sh.name == "QSHALA Loop" or sh.name.startswith("QSHALA Hint")]
    two, _, _ = connect_slides(export([connect_q([make(0), make(1)], None)], "minimalist"))
    assert len(pic_shapes(two)) == 2


def test_round_trip_keeps_items_hint_answer_and_format(media, tmp_path, monkeypatch):
    from backend.app.services.ingestion.pptx_parser import PPTXParser
    from backend.app.services.ingestion.question_extractor import QuestionExtractor
    import backend.app.services.storage.r2_storage as r2
    monkeypatch.setattr(r2, "get_media_storage", lambda: None)
    make, _ = media
    quiz = {"id": "qz", "title": "t", "topic": "x", "difficulty": "Balanced",
            "questions": [connect_q([make(i) for i in range(6)], "Meghdoot software")]}
    f = tmp_path / "rt.pptx"
    f.write_bytes(QuizExporter.to_pptx(quiz, quiz_type="minimalist", include_social_slides=False))
    slides = PPTXParser(str(f), f"memtest-rt-{uuid.uuid4().hex[:6]}", write_to_disk=False).parse()
    back = QuestionExtractor().extract_from_slides(slides, "t", 2026)
    c = [q for q in back if (q.get("format_payload") or {}).get("type") == "CONNECT"]
    assert len(c) == 1
    P = c[0]["format_payload"]
    assert len(P["items"]) == 6 and P["text_hint"] == "Meghdoot software" and P["answer"] == "SECRETANSWER"


# ------------------------------------------------------------------------------------------------ custom templates

from backend.tests.test_template_robustness import store, _deck, _upload, _export, client as tpl_client, PPTX_MIME  # noqa: E402,F401


@pytest.mark.parametrize("n,hint", [(3, "Hint"), (4, None), (6, "Hint"), (8, "Hint")])
def test_custom_template_connect_slide(store, media, n, hint):
    make, _ = media
    tpl = _upload(_deck(), name=f"Connect Theme {n}")
    try:
        quiz = {"id": "cq", "title": "Custom", "topic": "x",
                "questions": [connect_q([make(i) for i in range(n)], hint)]}
        r = _export(tpl["id"], quiz)
        assert r.status_code == 200, r.text
        prs = Presentation(io.BytesIO(r.content))
        q, divider, ans = connect_slides(prs)
        assert len(pic_shapes(q)) == n and len([sh for sh in q.shapes if sh.name == "QSHALA Loop"]) == 1
        assert [sh.text_frame.text for sh in q.shapes if sh.name == "Heading"] == ["1. Connect"]
        assert not [ph for ph in q.placeholders if not ph.text_frame.text.strip()]      # no "Click to add text"
        assert "SECRETANSWER" not in slide_text(q) and "SECRETANSWER" in slide_text(ans)
        for sh in q.shapes:
            if sh.shape_type == 13 and not sh.name.startswith("QSHALA"):
                continue
            assert 0 <= sh.left and 0 <= sh.top and sh.left + sh.width <= prs.slide_width + 10 and sh.top + sh.height <= prs.slide_height + 10
    finally:
        tpl_client.delete(f"/api/v1/templates/{tpl['id']}")
