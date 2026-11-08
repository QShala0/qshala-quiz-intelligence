"""GATE 0: the format-aware ingestion fixtures load and are internally consistent."""
import json
import re
import sys
import zipfile
from pathlib import Path

import pytest

FIX = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(FIX))

DECKS = ["dsu_mini", "listit_reserve", "three_clues_reserve", "arrange_reserve", "mcq_reserve", "hotspot_reserve"]
SLIDE_KEYS = {"slide_number", "title", "extracted_text", "speaker_notes", "image_paths", "video_paths",
              "all_media_items", "hidden", "layout_name", "shapes", "animations",
              "interactive_animations"}
SHAPE_KEYS = {"id", "name", "type", "text", "x", "y", "w", "h", "groups", "path", "media_md5", "media_kind",
              "overlay_class"}
ANIM_KEYS = {"order", "cls", "preset", "node_type", "target_id", "target_name"}


def load(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def by_no(slides):
    return {s["slide_number"]: s for s in slides}


@pytest.mark.parametrize("name", DECKS)
def test_fixture_schema(name):
    slides = load(name)
    assert slides, name
    nums = [s["slide_number"] for s in slides]
    assert nums == sorted(set(nums))
    for s in slides:
        assert SLIDE_KEYS <= set(s), (name, s["slide_number"], SLIDE_KEYS - set(s))
        for sh in s["shapes"]:
            assert SHAPE_KEYS <= set(sh)
            assert len(sh["groups"]) == len(sh["path"])
        for a in s["animations"]:
            assert ANIM_KEYS <= set(a)


def test_expected_cases_reference_existing_slides():
    exp = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))
    assert set(exp) == set(DECKS)
    for name, block in exp.items():
        slides = by_no(load(name))
        for case in block["cases"]:
            assert case["q"] in slides, (name, case)
            if "a" in case:
                assert case["a"] in slides, (name, case)
        for n in block.get("no_question_slides", []):
            assert n in slides or name == "hotspot_reserve", (name, n)


def test_dsu_has_the_documented_cases():
    d = by_no(load("dsu_mini"))
    li = d[84]
    ov = [s for s in li["shapes"] if s["overlay_class"]]
    assert sum(o["overlay_class"] == "tick" for o in ov) == 6 and sum(o["overlay_class"] == "cross" for o in ov) == 3
    assert len([s for s in li["shapes"] if s["name"].startswith("Option")]) == 9
    assert len(li["interactive_animations"]) == 9 and li["animations"] == []
    assert d[23]["hidden"] and not d[26]["hidden"]
    ir = d[114]
    badges = [s["text"] for s in ir["shapes"] if re.match(r"^\d\s*[|\n]\s*\+\d+/-?\d+$", s["text"] or "")]
    assert [b.replace("\n", " | ") for b in badges] == ["1 | +20/-15", "2 | +15/-10", "3 | +10/-5", "4 | +5/0"]
    assert [a["cls"] for a in ir["animations"]] == ["exit"] * 4 and {a["preset"] for a in ir["animations"]} == {10}
    assert len([s for s in d[140]["shapes"] if s["type"] == "PICTURE"]) == 9
    assert d[156]["video_paths"] and not d[156]["image_paths"]


def test_noise_media_is_reused_on_three_or_more_slides():
    from collections import Counter
    c = Counter()
    for s in load("dsu_mini"):
        for md5 in {sh["media_md5"] for sh in s["shapes"] if sh["media_md5"]}:
            c[md5] += 1
    assert any(v >= 3 for v in c.values())


def test_builder_is_reproducible(tmp_path):
    import build_fixtures
    for name, fn in build_fixtures.DECKS.items():
        assert json.loads(json.dumps(fn())) == load(name), f"{name}.json is stale: re-run build_fixtures.py"


def test_generated_pptx_structure(tmp_path):
    import make_test_pptx
    from pptx import Presentation
    out = tmp_path / "t.pptx"
    info = make_test_pptx.build(out)
    prs = Presentation(str(out))
    assert len(prs.slides) == 4
    with zipfile.ZipFile(out) as z:
        s1, s2, s3, s4 = (z.read(f"ppt/slides/slide{i}.xml").decode() for i in range(1, 5))
    assert s1.count("presetClass=\"entr\"") == 9 and s1.count("<p:pic>") == 9 and s1.count("interactiveSeq") == 9
    assert s2.count("nodeType=\"mainSeq\"") == 1 and s2.count("nodeType=\"interactiveSeq\"") == 1
    assert s2.count("presetClass=\"exit\"") == 5 and s2.count("<p:grpSp>") == 4   # 4 main + 1 decoy
    assert s3.count("<p:grpSp>") == 4
    assert 'show="0"' in s4 and 'show="0"' not in s1
    # group child space really differs from slide space
    g = prs.slides[2].shapes[1]
    xfrm = g._element.grpSpPr.xfrm
    assert xfrm.chExt.cx == 2 * xfrm.ext.cx
    assert len(info["list_it"]["overlays"]) == 9
