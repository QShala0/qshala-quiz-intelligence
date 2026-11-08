"""Builds a tiny PPTX with the structures the format-aware parser must read (Phase 0 fixture generator).

    python backend/tests/fixtures/make_test_pptx.py out.pptx

Slides
  1  List It        9 text-box options + 9 overlay pictures (green tick x6, red cross x3); clicking an option
                    plays an entrance effect on its overlay (interactiveSeq, as in the real decks)
  2  Image Reveal   full picture + 4 groups (cover tile + "n | +x/-y" badge); p:timing has a mainSeq with
                    4 exit effects (preset 10) AND an interactiveSeq with a decoy exit effect
  3  Arrange        4 groups (label, name, picture); the groups are scaled (ext != chExt) to test transforms
  4  Hidden         p:sld show="0"
The generated file is not committed (*.pptx is git-ignored); tests call build() into tmp_path.
"""
import io
import sys
from pathlib import Path

from lxml import etree
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Emu, Pt

NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"

OPTIONS_CORRECT = ["SBI", "Reliance", "L&T", "ITC", "Bajaj Auto", "Tata Steel"]
OPTIONS_WRONG = ["Nestle", "Airtel", "Sun Pharma"]
REVEAL_ORDER = ["bottom-left", "bottom-right", "top-right", "top-left"]   # badge 1..4
ARRANGE_ITEMS = [(1, "Pushpa 2", "11,301"), (2, "Kantara", "3,604"), (3, "Kalki", "18,518"), (4, "RRR", "7,609")]


def _png(colour, mark=None, size=(120, 120)) -> bytes:
    im = Image.new("RGB", size, colour)
    d = ImageDraw.Draw(im)
    if mark == "tick":
        d.line([(25, 65), (50, 90), (95, 30)], fill=(255, 255, 255), width=12)
    elif mark == "cross":
        d.line([(25, 25), (95, 95)], fill=(255, 255, 255), width=12)
        d.line([(95, 25), (25, 95)], fill=(255, 255, 255), width=12)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _timing_xml(main, interactive=()):
    """main: [(spid, 'exit'|'entr', presetID)] one click each; interactive: [(trigger_spid, spid, cls, presetID)]."""
    ids = iter(range(3, 1000))

    def effect(spid, cls, preset, node="clickEffect"):
        return (f'<p:par><p:cTn id="{next(ids)}" fill="hold"><p:stCondLst><p:cond delay="indefinite"/></p:stCondLst>'
                f'<p:childTnLst><p:par><p:cTn id="{next(ids)}" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst>'
                f'<p:childTnLst><p:par><p:cTn id="{next(ids)}" presetID="{preset}" presetClass="{cls}" presetSubtype="0" '
                f'fill="hold" nodeType="{node}"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>'
                f'<p:set><p:cBhvr><p:cTn id="{next(ids)}" dur="1" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst></p:cTn>'
                f'<p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl><p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst>'
                f'</p:cBhvr><p:to><p:strVal val="{"hidden" if cls == "exit" else "visible"}"/></p:to></p:set>'
                f'</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>')

    main_xml = "".join(effect(*m) for m in main)
    inter_xml = ""
    for trig, spid, cls, preset in interactive:
        inter_xml += (f'<p:seq concurrent="1" nextAc="seek"><p:cTn id="{next(ids)}" restart="whenNotActive" fill="hold" '
                      f'evtFilter="cancelBubble" nodeType="interactiveSeq"><p:stCondLst><p:cond evt="onClick" delay="0">'
                      f'<p:tgtEl><p:spTgt spid="{trig}"/></p:tgtEl></p:cond></p:stCondLst><p:endSync evt="end" delay="0">'
                      f'<p:rtn val="all"/></p:endSync><p:childTnLst>{effect(spid, cls, preset, "clickEffect")}</p:childTnLst>'
                      f'</p:cTn><p:nextCondLst><p:cond evt="onClick" delay="0"><p:tgtEl><p:spTgt spid="{trig}"/></p:tgtEl></p:cond>'
                      f'</p:nextCondLst></p:seq>')
    return (f'<p:timing xmlns:p="{NS_P}"><p:tnLst><p:par><p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot">'
            f'<p:childTnLst><p:seq concurrent="1" nextAc="seek"><p:cTn id="2" dur="indefinite" nodeType="mainSeq">'
            f'<p:childTnLst>{main_xml}</p:childTnLst></p:cTn><p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl>'
            f'<p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst><p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl>'
            f'<p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst></p:seq>{inter_xml}</p:childTnLst></p:cTn></p:par></p:tnLst></p:timing>')


def _set_timing(slide, xml):
    sld = slide._element
    for old in sld.findall(f"{{{NS_P}}}timing"):
        sld.remove(old)
    el = etree.fromstring(xml)
    ext = sld.find(f"{{{NS_P}}}extLst")
    if ext is not None:
        ext.addprevious(el)
    else:
        sld.append(el)


def _pic(shapes, colour, mark, left, top, w, h, name=None):
    p = shapes.add_picture(io.BytesIO(_png(colour, mark)), Emu(left), Emu(top), Emu(w), Emu(h))
    if name:
        p.name = name
    return p


def _textbox(shapes, text, left, top, w, h, name=None):
    tb = shapes.add_textbox(Emu(left), Emu(top), Emu(w), Emu(h))
    tb.text_frame.text = text
    if name:
        tb.name = name
    return tb


def build(path) -> dict:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(9144000), Emu(5143500)
    blank = prs.slide_layouts[6]
    info = {}

    # ---- 1. List It
    s1 = prs.slides.add_slide(blank)
    _textbox(s1.shapes, "List It!", 300000, 50000, 2000000, 400000, "Title 1")
    inter, texts = [], OPTIONS_CORRECT + OPTIONS_WRONG
    overlays = []
    for i, t in enumerate(texts):
        col, row = i % 3, i // 3
        x, y = 500000 + col * 2800000, 700000 + row * 1300000
        opt = _textbox(s1.shapes, t, x, y, 2200000, 500000, f"Option {i + 1}")
        right = t in OPTIONS_CORRECT
        pic = _pic(s1.shapes, (30, 160, 60) if right else (200, 40, 40), "tick" if right else "cross",
                   x + 2250000, y, 450000, 450000, f"Picture {i + 3}")   # names say nothing about colour
        overlays.append((pic.shape_id, right))
        inter.append((opt.shape_id, pic.shape_id, "entr", 22))     # clicking an option reveals its overlay
    _set_timing(s1, _timing_xml([], interactive=inter))
    info["list_it"] = {"slide": 1, "overlays": overlays, "options": texts,
                       "option_ids": [t for t, _, _, _ in inter]}

    # ---- 2. Image Reveal
    s2 = prs.slides.add_slide(blank)
    _pic(s2.shapes, (90, 120, 200), None, 0, 0, 9144000, 5143500, "Full Image")
    half_w, half_h = 9144000 // 2, 5143500 // 2
    cells = {"top-left": (0, 0), "top-right": (half_w, 0), "bottom-left": (0, half_h), "bottom-right": (half_w, half_h)}
    ladder = ["+20/-15", "+15/-10", "+10/-5", "+5/0"]
    groups = []
    for k, pos in enumerate(REVEAL_ORDER, start=1):
        g = s2.shapes.add_group_shape()
        g.name = f"Group {k}"
        _pic(g.shapes, (20 * k, 20 * k, 20 * k), None, cells[pos][0], cells[pos][1], half_w, half_h, f"Cover {k}")
        _textbox(g.shapes, f"{k} | {ladder[k - 1]}", cells[pos][0] + 1000000, cells[pos][1] + 900000, 2000000, 500000, f"Badge {k}")
        groups.append((g.shape_id, pos))
    first_group_id = groups[0][0]
    decoy = s2.shapes.add_textbox(Emu(100000), Emu(100000), Emu(500000), Emu(300000))
    decoy.text_frame.text = "?"
    decoy.name = "Help button"
    # reveal order in the main sequence is deliberately NOT the shape order in the tree
    main = [(gid, "exit", 10) for gid, _ in groups]
    _set_timing(s2, _timing_xml(main, interactive=[(decoy.shape_id, first_group_id, "exit", 10)]))
    info["image_reveal"] = {"slide": 2, "groups": groups, "decoy_trigger": decoy.shape_id}

    # ---- 3. Arrange (grouped, scaled group transform)
    s3 = prs.slides.add_slide(blank)
    _textbox(s3.shapes, "Arrange the budgets from highest to lowest", 300000, 50000, 8000000, 400000, "Title 1")
    pos = [(300000, 600000), (4700000, 600000), (300000, 2800000), (4700000, 2800000)]
    arr = []
    for (label, name, _val), (gx, gy) in zip(ARRANGE_ITEMS, pos):
        g = s3.shapes.add_group_shape()
        g.name = f"Group {label}"
        _textbox(g.shapes, str(label), gx, gy, 400000, 400000, "Label")
        _textbox(g.shapes, name, gx + 500000, gy, 2500000, 400000, "Name")
        _pic(g.shapes, (150, 150, 150), None, gx, gy + 450000, 3000000, 1500000, "Picture")
        arr.append(g.shape_id)
        # make the group's own space differ from the slide space: children drawn at 2x, displayed at 1x
        xfrm = g._element.grpSpPr.find(f"{{{NS_A}}}xfrm")
        ext, chExt = xfrm.find(f"{{{NS_A}}}ext"), xfrm.find(f"{{{NS_A}}}chExt")
        chExt.set("cx", str(int(ext.get("cx")) * 2)), chExt.set("cy", str(int(ext.get("cy")) * 2))
    info["arrange"] = {"slide": 3, "groups": arr}

    # ---- 4. Hidden slide
    s4 = prs.slides.add_slide(blank)
    _textbox(s4.shapes, "Spare question", 300000, 300000, 4000000, 400000)
    s4._element.set("show", "0")
    info["hidden"] = {"slide": 4}

    prs.save(str(path))
    return info


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "format_test.pptx")
    print(build(out))
