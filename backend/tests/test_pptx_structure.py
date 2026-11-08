"""GATE 1: the parser reads hidden flag, layout, shapes (with group geometry) and main-sequence animations from XML."""
import re
import shutil
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))

from backend.app.config import settings
from backend.app.services.ingestion.pptx_parser import PPTXParser
from backend.app.services.ingestion.pptx_structure import absolute_box, absolute_centre
import make_test_pptx


@pytest.fixture(scope="module")
def parsed(tmp_path_factory):
    d = tmp_path_factory.mktemp("struct")
    deck = d / "t.pptx"
    info = make_test_pptx.build(deck)
    doc = f"memtest-{uuid.uuid4().hex[:8]}"
    slides = PPTXParser(str(deck), doc, write_to_disk=False).parse()
    yield slides, info
    shutil.rmtree(settings.SLIDES_DIR / doc, ignore_errors=True)


def test_hidden_and_layout(parsed):
    slides, _ = parsed
    assert [s["hidden"] for s in slides] == [False, False, False, True]
    assert all(s["layout_name"] == "Blank" for s in slides)


def test_list_it_overlays_classified_from_pixels(parsed):
    slides, info = parsed
    s = slides[0]
    overlays = [x for x in s["shapes"] if x["overlay_class"]]
    assert len(overlays) == 9
    assert sum(o["overlay_class"] == "tick" for o in overlays) == 6
    assert sum(o["overlay_class"] == "cross" for o in overlays) == 3
    # entrance animations are in the main sequence, one per overlay, click order = shape order
    assert [a["cls"] for a in s["animations"]] == ["entr"] * 9
    assert [a["target_id"] for a in s["animations"]] == [sid for sid, _ in info["list_it"]["overlays"]]
    # each overlay's nearest option (by centre) is the option it was built next to
    opts = [x for x in s["shapes"] if x["name"].startswith("Option")]
    truth = dict(info["list_it"]["overlays"])
    for o in overlays:
        ox, oy = absolute_centre(o)
        nearest = min(opts, key=lambda p: (absolute_centre(p)[0] - ox) ** 2 + (absolute_centre(p)[1] - oy) ** 2)
        assert (nearest["text"] in make_test_pptx.OPTIONS_CORRECT) == truth[o["id"]] == (o["overlay_class"] == "tick")


def test_image_reveal_main_sequence_only(parsed):
    slides, info = parsed
    s = slides[1]
    group_ids = [gid for gid, _ in info["image_reveal"]["groups"]]
    anims = s["animations"]
    assert [a["target_id"] for a in anims] == group_ids                       # decoy interactive effect ignored
    assert {a["cls"] for a in anims} == {"exit"} and {a["preset"] for a in anims} == {10}
    assert {a["node_type"] for a in anims} == {"clickEffect"}
    assert [a["order"] for a in anims] == [1, 2, 3, 4]
    assert anims[0]["target_name"] == "Group 1"
    badges = {x["text"] for x in s["shapes"] if re.match(r"^\d \| \+\d+/-?\d+$", x["text"] or "")}
    assert badges == {"1 | +20/-15", "2 | +15/-10", "3 | +10/-5", "4 | +5/0"}


def test_group_membership_and_geometry(parsed):
    slides, info = parsed
    s = slides[1]
    groups = {x["id"]: x for x in s["shapes"] if x["type"] == "GROUP"}
    assert set(groups) == {gid for gid, _ in info["image_reveal"]["groups"]}
    covers = [x for x in s["shapes"] if x["name"].startswith("Cover")]
    assert len(covers) == 4 and all(c["path"] and len(c["groups"]) == 1 for c in covers)
    full = next(x for x in s["shapes"] if x["name"] == "Full Image")
    fx, fy, fw, fh = absolute_box(full)
    # cover k sits in the quadrant named by REVEAL_ORDER[k-1]
    for k, pos in enumerate(make_test_pptx.REVEAL_ORDER, start=1):
        cx, cy = absolute_centre(next(c for c in covers if c["name"] == f"Cover {k}"))
        assert (("bottom" if cy > fy + fh / 2 else "top") + "-" + ("right" if cx > fx + fw / 2 else "left")) == pos


def test_group_transform_scales_children(parsed):
    slides, info = parsed
    s = slides[2]
    pics = [x for x in s["shapes"] if x["type"] == "PICTURE"]
    assert len(pics) == 4 and all(len(p["groups"]) == 1 for p in pics)
    for p in pics:
        g = p["groups"][0]
        assert g["chExt"][0] == 2 * g["ext"][0]
        _, _, w, h = absolute_box(p)
        assert w == pytest.approx(p["w"] / 2) and h == pytest.approx(p["h"] / 2)
    labels = sorted(x["text"] for x in s["shapes"] if x["name"] == "Label")
    assert labels == ["1", "2", "3", "4"]


def test_media_md5_stable_and_no_bytes_kept(parsed):
    slides, _ = parsed
    md5s = [x["media_md5"] for sl in slides for x in sl["shapes"] if x["type"] == "PICTURE"]
    assert all(re.fullmatch(r"[0-9a-f]{32}", m) for m in md5s)
    for sl in slides:
        for item in sl["all_media_items"]:
            assert "raw_bytes" not in item
        for sh in sl["shapes"]:
            assert not any(isinstance(v, (bytes, bytearray)) for v in sh.values())


def test_parser_file_keeps_crlf():
    raw = (Path(__file__).resolve().parents[1] / "app" / "services" / "ingestion" / "pptx_parser.py").read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n") and b"\r\n" in raw
