"""Tests for numbered-box & grouped-badge Image Reveal ingestion, cover/timer exclusion, animated PPTX export, and round-trip."""
import io
import json
import shutil
import sys
import uuid
from pathlib import Path

from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Emu

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
import make_test_pptx

from backend.app.config import settings
from backend.app.services.export import format_slides as fs
from backend.app.services.ingestion.pptx_parser import PPTXParser
from backend.app.services.ingestion.pptx_structure import read_animations
from backend.app.services.ingestion.question_extractor import QuestionExtractor

FIX = Path(__file__).resolve().parent / "fixtures"
NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def _png_bytes(colour=(120, 160, 210), size=(600, 450), label="IMG"):
    im = Image.new("RGB", size, colour)
    draw = ImageDraw.Draw(im)
    # Draw varied grid so PNG compresses to > 4 KB (pptx_parser skips < 2048 B as spacers)
    for x in range(0, size[0], 11):
        c = ((colour[0] + x * 7) % 256, (colour[1] + x * 13) % 256, (colour[2] + x * 19) % 256)
        draw.line([(x, 0), (size[0] - x, size[1])], fill=c, width=4)
    draw.text((20, 20), label, fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _build_numbered_box_deck(path: Path, title_text="5. Dwarf planet", with_scores=False):
    """Build a synthetic numbered-box covers Image Reveal deck: question slide + Answer divider + answer slide."""
    prs = Presentation()
    prs.slide_width = Emu(12192000)
    prs.slide_height = Emu(6858000)
    blank = prs.slide_layouts[6]

    # Slide 1: Question slide
    s1 = prs.slides.add_slide(blank)
    tb = s1.shapes.add_textbox(Emu(978108), Emu(579662), Emu(9061239), Emu(1421002))
    tb.name = "Title 1"
    tb.text_frame.text = title_text + (" +5/-0 +20/-15 +15/-10 +10/-5" if with_scores else "")

    # Full picture
    full_pic = s1.shapes.add_picture(
        io.BytesIO(_png_bytes((40, 90, 160), (400, 400), "FULL")),
        Emu(4174304), Emu(2205546), Emu(3538586), Emu(3567829),
    )
    full_pic.name = "Picture 4"

    # 4 numbered rectangles ("1".."4") arranged 2x2: 4=TL, 3=TR, 1=BL, 2=BR
    rects = [
        ("4", 4174304, 2205546, 1769293, 1783914, "Rectangle 26"),
        ("3", 5943600, 2205546, 1769293, 1783914, "Rectangle 15"),
        ("1", 4174304, 3989460, 1769293, 1783914, "Rectangle 16"),
        ("2", 5943600, 3989460, 1769293, 1783914, "Rectangle 17"),
    ]
    spids = {}
    for label, x, y, w, h, name in rects:
        shp = s1.shapes.add_shape(1, Emu(x), Emu(y), Emu(w), Emu(h))
        shp.name = name
        shp.text_frame.text = label
        spids[label] = shp.shape_id

    # Timer media-like picture named "Timer 1min 1" (simulating timer poster "5" tile)
    timer_pic = s1.shapes.add_picture(
        io.BytesIO(_png_bytes((255, 200, 0), (200, 120), "5")),
        Emu(9347505), Emu(3118336), Emu(2297612), Emu(1292407),
    )
    timer_pic.name = "Timer 1min 1"

    # interactiveSeq click exits on the 4 rectangles (as in numbered-box cover source decks)
    inter = [(spids[lbl], spids[lbl], "exit", 10) for lbl in ("4", "3", "1", "2")]
    make_test_pptx._set_timing(s1, make_test_pptx._timing_xml([], interactive=inter))

    # Slide 2: Answer divider
    s2 = prs.slides.add_slide(blank)
    t2 = s2.shapes.add_textbox(Emu(3000000), Emu(2500000), Emu(6000000), Emu(1500000))
    t2.name = "Title 1"
    t2.text_frame.text = "Answer!"

    # Slide 3: Answer slide
    s3 = prs.slides.add_slide(blank)
    t3 = s3.shapes.add_textbox(Emu(1500000), Emu(800000), Emu(8000000), Emu(900000))
    t3.name = "Title 1"
    t3.text_frame.text = "Pluto"
    ans_pic = s3.shapes.add_picture(
        io.BytesIO(_png_bytes((40, 90, 160), (400, 400), "ANS")),
        Emu(4174304), Emu(2205546), Emu(3538586), Emu(3567829),
    )
    ans_pic.name = "Picture 2"

    # Pad deck with dummy text slides so a 2-slide image isn't >30% of slides (noise threshold)
    for k in range(5):
        ds = prs.slides.add_slide(blank)
        dt = ds.shapes.add_textbox(Emu(1000000), Emu(1000000), Emu(5000000), Emu(1000000))
        dt.text_frame.text = f"Filler slide {k + 1}"

    prs.save(str(path))


def test_numbered_box_reveal_ingestion_and_cover_exclusion(tmp_path):
    deck = tmp_path / "numbered_box_test.pptx"
    _build_numbered_box_deck(deck, title_text="5. Dwarf planet", with_scores=True)
    doc_id = f"nbtest-{uuid.uuid4().hex[:8]}"
    try:
        slides = PPTXParser(str(deck), doc_id, write_to_disk=True).parse()
        qs = QuestionExtractor().extract_from_slides(slides, "numbered", 2026)
        q = next(x for x in qs if x["question_format"] == "IMAGE_REVEAL")
        assert q["question_format"] == "IMAGE_REVEAL"
        assert q["question_text"] == "Dwarf planet"
        assert q["answer"] == "Pluto"
        P = q["format_payload"]
        assert P["prompt"] == "Dwarf planet"
        assert P["timer_seconds"] == 60
        assert P["full_image"] is not None
        assert len(P["reveal_steps"]) == 4
        assert [s["step"] for s in P["reveal_steps"]] == [1, 2, 3, 4]
        assert [s["position"] for s in P["reveal_steps"]] == [
            "bottom-left", "bottom-right", "top-right", "top-left"
        ]
        assert [(s["correct"], s["wrong"]) for s in P["reveal_steps"]] == [
            (5, 0), (20, -15), (15, -10), (10, -5)
        ]
        # Timer poster ("5" tile) must NEVER be in question_images or image_refs
        assert q["question_images"] == [P["full_image"]]
        assert len(q["answer_images"]) == 1
        assert len(q["image_refs"]) == 2
        assert all(P["full_image"] == u or u in q["answer_images"] for u in q["image_refs"])
    finally:
        shutil.rmtree(settings.SLIDES_DIR / doc_id, ignore_errors=True)


def test_grouped_badge_cover_artwork_excluded_from_question_images():
    slides = json.loads((FIX / "dsu_mini.json").read_text(encoding="utf-8"))
    qs = QuestionExtractor().extract_from_slides(slides, "grouped", 2025)
    rev = next(q for q in qs if q["question_format"] == "IMAGE_REVEAL")
    P = rev["format_payload"]
    assert P["full_image"] is not None
    # Cover tiles must NOT appear in question_images or image_refs
    cover_urls = {s["cover"] for s in P["reveal_steps"] if s.get("cover")}
    assert len(cover_urls) == 4
    assert rev["question_images"] == [P["full_image"]]
    assert not (cover_urls & set(rev["image_refs"]))


def test_reveal_export_single_slide_animation_xml_and_round_trip(tmp_path):
    full_img = tmp_path / "full.png"
    full_img.write_bytes(_png_bytes((30, 120, 90), (600, 450), "FULL"))
    q_dict = {
        "id": "q-rev-1",
        "question_text": "Dwarf planet",
        "answer": "Pluto",
        "question_format": "IMAGE_REVEAL",
        "format_payload": {
            "type": "IMAGE_REVEAL",
            "prompt": "Dwarf planet",
            "full_image": str(full_img),
            "timer_seconds": 60,
            "reveal_steps": [
                {"step": 1, "cover": None, "position": "bottom-left", "correct": 20, "wrong": -15},
                {"step": 2, "cover": None, "position": "bottom-right", "correct": 15, "wrong": -10},
                {"step": 3, "cover": None, "position": "top-right", "correct": 10, "wrong": -5},
                {"step": 4, "cover": None, "position": "top-left", "correct": 5, "wrong": 0},
            ],
            "answer": "Pluto",
            "extraction": {"confidence": 0.97, "needs_review": False, "route": "accept", "reasons": []},
        },
        "question_images": [str(full_img)],
        "answer_images": [str(full_img)],
    }

    prs = Presentation()
    prs.slide_width = Emu(12192000)
    prs.slide_height = Emu(6858000)
    blank = prs.slide_layouts[6]
    blob, _urls = fs.build_blob("IMAGE_REVEAL", q_dict["format_payload"], "Dwarf planet", "Pluto", "", None, {})
    fs._reveal_slides(prs, blank, q_dict, q_dict["format_payload"],
                     lambda u: Path(u) if u and Path(u).exists() else None, blob, 1)

    # Must be 3 slides: 1 animated question slide + Answer divider + Answer slide (NOT 4 static question slides)
    assert len(prs.slides) == 3

    out_pptx = tmp_path / "exported_reveal.pptx"
    prs.save(str(out_pptx))

    # Inspect saved XML for 4 click-triggered fade exit effects in mainSeq in order 1..4
    prs2 = Presentation(str(out_pptx))
    q_slide = prs2.slides[0]
    id_to_name = {sh.shape_id: sh.name for sh in q_slide.shapes}
    anims = read_animations(q_slide, id_to_name)
    assert len(anims) == 4
    assert [a["cls"] for a in anims] == ["exit"] * 4
    assert [a["preset"] for a in anims] == [10] * 4
    assert [a["node_type"] for a in anims] == ["clickEffect"] * 4
    assert [a["target_name"] for a in anims] == [
        "Cover 1", "Cover 2", "Cover 3", "Cover 4"
    ]

    # Verify XML has animEffect transition="out" filter="fade"
    timing = q_slide._element.find(f"{{{NS_P}}}timing")
    assert timing is not None
    effects = timing.xpath(".//*[local-name()='animEffect']")
    assert len(effects) == 4
    assert all(e.get("transition") == "out" and e.get("filter") == "fade" for e in effects)

    # Answer slide text must be black (000000), never green
    ans_title = next(sh for sh in prs2.slides[2].shapes if sh.has_text_frame)
    assert str(ans_title.text_frame.paragraphs[0].font.color.rgb) == "000000"

    # Round-trip test WITHOUT speaker-notes blob (forces structural extraction)
    for sl in prs2.slides:
        if sl.has_notes_slide:
            sl.notes_slide.notes_text_frame.text = ""
    # Add filler slides so full_img appearing on Q & A isn't treated as deck furniture (>30% of slides)
    for k in range(5):
        ds = prs2.slides.add_slide(prs2.slide_layouts[6])
        ds.shapes.add_textbox(Emu(1000000), Emu(1000000), Emu(5000000), Emu(1000000)).text_frame.text = f"Filler {k}"
    stripped_pptx = tmp_path / "exported_no_notes.pptx"
    prs2.save(str(stripped_pptx))

    doc_id = f"rt-{uuid.uuid4().hex[:8]}"
    try:
        parsed_slides = PPTXParser(str(stripped_pptx), doc_id, write_to_disk=True).parse()
        qs = QuestionExtractor().extract_from_slides(parsed_slides, "rt", 2026)
        rt = next(x for x in qs if x["question_format"] == "IMAGE_REVEAL")
        assert rt["question_format"] == "IMAGE_REVEAL"
        assert rt["answer"] == "Pluto"
        assert [s["position"] for s in rt["format_payload"]["reveal_steps"]] == [
            "bottom-left", "bottom-right", "top-right", "top-left"
        ]
        assert rt["format_payload"]["timer_seconds"] == 60
        assert len(rt["question_images"]) == 1
    finally:
        shutil.rmtree(settings.SLIDES_DIR / doc_id, ignore_errors=True)
