"""PPTX export v1 for format-aware questions: STATIC expansion, no animation authoring.

    IMAGE_REVEAL   1 animated slide (4 click-triggered fade-exit covers in mainSeq) + ANSWER + answer slide
    LIST_IT        question slide (9 options) + ANSWER + answer slide with ticks and crosses
    CONNECT        grid slide (up to 9 pictures, hint) + ANSWER + answer slide
    STEPPED_CLUE   one slide per clue + ANSWER + answer slide
    ARRANGE_ORDER  items with labels (grouped) + ANSWER + answer slide with the values and the order
    MCQ            stem and options + ANSWER + answer slide

The first slide of every question carries a machine-readable copy of the question in its speaker notes
(`QSHALA-FORMAT v1` + JSON). Picture URLs inside it are replaced by {"$img": n}, n being the position of the picture in
the order the question's slides show their distinct pictures, so a re-upload of the exported deck restores the SAME
payload with the new picture URLs (ingestion/format_extractors.restore_from_export). The slides themselves are
ordinary slides a person can read, edit and present.
"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.app.services.connect_text import SEPARATOR, split_clue_parts

logger = logging.getLogger(__name__)

MARKER = "QSHALA-FORMAT v1"
EXPORT_FORMATS = ("LIST_IT", "IMAGE_REVEAL", "CONNECT", "STEPPED_CLUE", "ARRANGE_ORDER", "MCQ")
_IMG_KEYS = {"image", "images", "cover", "full_image"}
MINUS = "−"           # typed minus, so exported ladders are never mistaken for rules text on re-ingestion


# ------------------------------------------------------------------------------------------------ the notes blob

def _walk_urls(node: Any, urls: List[str]) -> Any:
    """Deep copy of `node` with every picture URL replaced by {"$img": index}; `urls` collects the distinct URLs."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in _IMG_KEYS:
                out[k] = _swap(v, urls)
            else:
                out[k] = _walk_urls(v, urls)
        return out
    if isinstance(node, list):
        return [_walk_urls(v, urls) for v in node]
    return node


def _swap(v: Any, urls: List[str]) -> Any:
    if isinstance(v, str) and v:
        if v not in urls:
            urls.append(v)
        return {"$img": urls.index(v)}
    if isinstance(v, list):
        return [_swap(x, urls) for x in v]
    return v


def tokenize_payload(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    urls: List[str] = []
    return _walk_urls(payload, urls), urls


def detokenize_payload(node: Any, urls: List[str]) -> Any:
    if isinstance(node, dict):
        if set(node) == {"$img"}:
            i = node["$img"]
            if not isinstance(i, int) or not (0 <= i < len(urls)):
                raise IndexError(f"picture {i} is not on the slides")
            return urls[i]
        return {k: detokenize_payload(v, urls) for k, v in node.items()}
    if isinstance(node, list):
        return [detokenize_payload(v, urls) for v in node]
    return node


def build_blob(fmt: str, payload: Dict[str, Any], question_text: str, answer: str, explanation: str,
               options: Optional[List[str]], meta: Dict[str, Any]) -> Tuple[str, List[str]]:
    clean = {k: v for k, v in payload.items() if k != "extraction"}
    tok, urls = tokenize_payload(clean)
    blob = {"v": 1, "format": fmt, "payload": tok, "question_text": question_text, "answer": answer,
            "explanation": explanation, "options": options, "meta": meta}
    return MARKER + "\n" + json.dumps(blob, ensure_ascii=False, separators=(",", ":")), urls


def parse_blob(notes: Optional[str]) -> Optional[Dict[str, Any]]:
    if not notes or MARKER not in notes:
        return None
    try:
        body = notes.split(MARKER, 1)[1].strip()
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("v") != 1 or data.get("format") not in EXPORT_FORMATS or not isinstance(data.get("payload"), dict):
        return None
    return data


# ------------------------------------------------------------------------------------------------ slide helpers

def _rgb(h: str):
    from pptx.dml.color import RGBColor
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _text(slide, x, y, w, h, text, size=20, bold=False, color="0F172A", align="left", font="Causten Round", name=None, shapes=None):
    from pptx.util import Inches, Pt
    from pptx.enum.text import PP_ALIGN
    target = shapes if shapes is not None else slide.shapes
    tb = target.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    lines = str(text).split("\n")
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = line
        p.alignment = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}[align]
        p.font.size = Pt(size)
        p.font.bold = bold
        p.font.name = "Causten Round Bold" if bold and font == "Causten Round" else font
        p.font.color.rgb = _rgb(color)
        for r in p.runs:
            r.font.size = Pt(size)
            r.font.bold = bold
            r.font.name = font
            r.font.color.rgb = _rgb(color)
    return tb


def _box(slide, x, y, w, h, fill: str, line: Optional[str] = None, text: str = "", size=16, color="FFFFFF", bold=True,
         font="Inter", name=None):
    from pptx.util import Inches, Pt
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        shp.name = name
    shp.fill.solid()
    shp.fill.fore_color.rgb = _rgb(fill)
    if line:
        shp.line.color.rgb = _rgb(line)
    else:
        shp.line.fill.background()
    tf = shp.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    for i, ln in enumerate(str(text).split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = ln
        p.alignment = PP_ALIGN.CENTER
        p.font.size = Pt(size)
        p.font.bold = bold
        p.font.name = font
        p.font.color.rgb = _rgb(color)
        for r in p.runs:
            r.font.size = Pt(size)
            r.font.bold = bold
            r.font.name = font
            r.font.color.rgb = _rgb(color)
    return shp


def _picture(slide, path: Optional[Path], x, y, w, h, shapes=None, name="Picture"):
    """Picture scaled to fit the box (aspect kept, centred). Returns the shape, or None when the file is unreadable."""
    from pptx.util import Inches
    if not path:
        return None
    try:
        from PIL import Image
        with Image.open(path) as im:
            iw, ih = im.size
        if iw <= 0 or ih <= 0:
            return None
        s = min(w / iw, h / ih)
        pw, ph = iw * s, ih * s
        target = shapes if shapes is not None else slide.shapes
        pic = target.add_picture(str(path), Inches(x + (w - pw) / 2), Inches(y + (h - ph) / 2), Inches(pw), Inches(ph))
        pic.name = "QSHALA " + name          # see slide_roles.EXPORT_SHAPE_PREFIX
        return pic
    except Exception as e:
        logger.warning("export: could not add picture %s: %s", path, e)
        return None


def _backdrop(slide, bg: Optional[Path]) -> None:
    """Apply question_bg.png as full-slide background (must be called right after add_slide, before other shapes)."""
    if not bg or not bg.exists():
        return
    from pptx.util import Inches
    slide.shapes.add_picture(str(bg), 0, 0, Inches(13.333), Inches(7.5))


def _answer_marker(prs, blank, bg: Optional[Path] = None, layout=None):
    """The "Answer" divider. With a School master layout it is that layout's title placeholder over the master
    artwork; otherwise a text box (on the backdrop picture when one is given)."""
    if layout is not None:
        s = prs.slides.add_slide(layout)
        if s.shapes.title is not None and hasattr(s.shapes.title, "text_frame"):
            s.shapes.title.text_frame.text = "Answer"
            s.shapes.title.name = "Heading"
        else:
            _text(s, 3.0, 2.8, 7.333, 1.8, "ANSWER", size=54, bold=True, align="center", name="Heading")
        return s
    s = prs.slides.add_slide(blank)
    _backdrop(s, bg)
    _text(s, 3.0, 2.8, 7.333, 1.8, "ANSWER", size=54, bold=True, align="center", name="Heading")
    return s


def _notes(slide, text: str) -> None:
    slide.notes_slide.notes_text_frame.text = text


# ------------------------------------------------------------------------------------------------ per-format slides

def _add_reveal_exit_timing(slide, spids: List[int]) -> None:
    """Write <p:timing> with click-triggered fade exit (presetID=10) animations in mainSeq for each cover spid."""
    if not spids:
        return
    from lxml import etree

    ns_p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    ids = iter(range(3, 1000))
    pars_xml = []
    for spid in spids:
        pars_xml.append(
            f'<p:par><p:cTn id="{next(ids)}" fill="hold">'
            f'<p:stCondLst><p:cond delay="indefinite"/></p:stCondLst>'
            f'<p:childTnLst><p:par><p:cTn id="{next(ids)}" fill="hold">'
            f'<p:stCondLst><p:cond delay="0"/></p:stCondLst>'
            f'<p:childTnLst><p:par><p:cTn id="{next(ids)}" presetID="10" presetClass="exit" '
            f'presetSubtype="0" fill="hold" grpId="0" nodeType="clickEffect">'
            f'<p:stCondLst><p:cond delay="0"/></p:stCondLst>'
            f'<p:childTnLst>'
            f'<p:animEffect transition="out" filter="fade"><p:cBhvr>'
            f'<p:cTn id="{next(ids)}" dur="500"/><p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl>'
            f'</p:cBhvr></p:animEffect>'
            f'<p:set><p:cBhvr><p:cTn id="{next(ids)}" dur="1" fill="hold">'
            f'<p:stCondLst><p:cond delay="499"/></p:stCondLst></p:cTn>'
            f'<p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl>'
            f'<p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst>'
            f'</p:cBhvr><p:to><p:strVal val="hidden"/></p:to></p:set>'
            f'</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>'
        )
    timing_xml = (
        f'<p:timing xmlns:p="{ns_p}"><p:tnLst><p:par>'
        f'<p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot"><p:childTnLst>'
        f'<p:seq concurrent="1" nextAc="seek"><p:cTn id="2" dur="indefinite" nodeType="mainSeq">'
        f'<p:childTnLst>{"".join(pars_xml)}</p:childTnLst></p:cTn>'
        f'<p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>'
        f'<p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst>'
        f'</p:seq></p:childTnLst></p:cTn></p:par></p:tnLst></p:timing>'
    )
    sld = slide._element
    for old in sld.findall(f"{{{ns_p}}}timing"):
        sld.remove(old)
    el = etree.fromstring(timing_xml)
    ext = sld.find(f"{{{ns_p}}}extLst")
    if ext is not None:
        ext.addprevious(el)
    else:
        sld.append(el)


def _reveal_slides(prs, blank, d: Dict[str, Any], P: Dict[str, Any], resolve: Callable[[str], Optional[Path]], blob: str, idx: int):
    from pptx.util import Inches

    bg = d.get("question_bg")
    steps = sorted(P["reveal_steps"], key=lambda st: st.get("step", 0))
    full = resolve(P.get("full_image")) if P.get("full_image") else None
    X, Y, W, H = 1.0, 1.6, 5.8, 5.0
    cells = {"top-left": (0, 0), "top-right": (1, 0), "bottom-left": (0, 1), "bottom-right": (1, 1)}

    s = prs.slides.add_slide(blank)
    _backdrop(s, bg)
    _text(s, 1.0, 0.55, 11.3, 0.9, P.get("prompt") or d["question_text"], size=26, bold=True, name="Title 1")
    pic = _picture(s, full, X, Y, W, H, name="Full Image")
    # Covers sit directly over the placed full picture (scaled to fit, centred)
    X0, Y0, W0, H0 = (X, Y, W, H) if pic is None else (pic.left / 914400.0, pic.top / 914400.0, pic.width / 914400.0, pic.height / 914400.0)

    cover_spids: List[int] = []
    for i, st in enumerate(steps):
        cx, cy = cells.get(st.get("position"), (i % 2, i // 2))
        qx, qy, qw, qh = X0 + cx * W0 / 2, Y0 + cy * H0 / 2, W0 / 2, H0 / 2
        cover_path = resolve(st["cover"]) if st.get("cover") else None
        c, w_ = st.get("correct"), st.get("wrong")
        if cover_path:
            grp = s.shapes.add_group_shape()
            grp.name = f"QSHALA Group {st['step']}"
            cpic = _picture(s, cover_path, qx, qy, qw, qh, shapes=grp.shapes, name=f"Cover {st['step']}")
            if cpic is not None:
                cpic.left, cpic.top, cpic.width, cpic.height = Inches(qx), Inches(qy), Inches(qw), Inches(qh)
            badge_txt = f"{st['step']} | +{c}/{MINUS}{abs(w_)}" if c is not None and w_ is not None else str(st["step"])
            _text(s, qx + qw * 0.15, qy + qh * 0.3, qw * 0.7, qh * 0.4, badge_txt,
                  size=18, bold=True, align="center", color="FFFFFF", shapes=grp.shapes, name=f"Badge {st['step']}")
            cover_spids.append(grp.shape_id)
        else:
            box = _box(s, qx, qy, qw, qh, "1E293B", "475569", str(st["step"]), size=28,
                       name=f"Cover {st['step']}")
            cover_spids.append(box.shape_id)

    _add_reveal_exit_timing(s, cover_spids)

    has_scores = any(st.get("correct") is not None for st in steps)
    if has_scores:
        lines = "\n".join(
            f"{st['step']}.  +{st['correct']} / {MINUS}{abs(st['wrong'])}"
            if st.get("wrong") else f"{st['step']}.  +{st.get('correct', 0)} / 0"
            for st in steps
        )
        _text(s, 7.3, 1.7, 5.0, 3.0, "Points if answered after cover\n" + lines, size=18, color="334155")
    if P.get("timer_seconds"):
        _text(s, 7.3, 5.0, 5.0, 0.8, f"Timer: {P['timer_seconds']}s", size=16, bold=True, color="475569", name="Timer")

    _answer_marker(prs, blank, bg, d.get("answer_layout"))
    sa = prs.slides.add_slide(blank)
    _backdrop(sa, bg)
    _text(sa, 1.0, 0.6, 11.3, 1.0, d["answer"], size=34, bold=True, color="0F172A", align="center", name="Title 1")
    _picture(sa, full, 3.2, 1.7, 6.9, 4.8, name="Full Image")
    _notes(sa, blob)


def _list_it_slides(prs, blank, d, P, resolve, blob, idx):
    bg = d.get("question_bg")

    def grid(slide, marks: bool):
        for i, o in enumerate(P["options"][:9]):
            col, row = i % 3, i // 3
            x, y = 1.0 + col * 3.85, 2.2 + row * 1.4
            text = o["text"]
            if marks:
                good = o.get("correct")
                prefix = "✓  " if good else ("✗  " if good is False else "")
                _box(slide, x, y, 3.65, 1.2, "DCFCE7" if good else ("FEE2E2" if good is False else "F1F5F9"),
                     "16A34A" if good else ("DC2626" if good is False else "CBD5E1"), prefix + text, size=18, color="14532D" if good else ("7F1D1D" if good is False else "334155"),
                     name=f"Option {i + 1}")
            else:
                _box(slide, x, y, 3.65, 1.2, "F8FAFC", "94A3B8", text, size=18, color="0F172A", name=f"Option {i + 1}")

    s = prs.slides.add_slide(blank)
    _backdrop(s, bg)
    title = P.get("prompt") or d["question_text"]
    if P.get("team"):
        title += f"\nTeam {P['team']}"
    _text(s, 1.0, 0.55, 11.3, 1.5, title, size=26, bold=True, name="Title 1")
    grid(s, False)
    _answer_marker(prs, blank, bg, d.get("answer_layout"))
    sa = prs.slides.add_slide(blank)
    _backdrop(sa, bg)
    _text(sa, 1.0, 0.55, 11.3, 1.5, "Correct answers are ticked", size=26, bold=True, color="0F172A", name="Title 1")
    grid(sa, True)
    _notes(sa, blob)


# ------------------------------------------------------------------------------------------------ Connect (loop)

CONNECT_DEFAULT_THEME: Dict[str, Any] = {
    "font_head": "Causten Round", "font_body": "Causten Round", "bold_head": True,
    "line": "0B2A3A", "hint_fill": "0B2A3A", "hint_text": "FFFFFF", "card": "FFFFFF", "title_color": "0B2A3A",
    "safe_box": (0.62, 0.60, 12.07, 6.27),
}


def _set_run_font(run, name: str, size: float, bold: bool, color: str) -> None:
    """Set latin + east-asian + complex-script typefaces so Devanagari and CJK text keep the template font."""
    from pptx.util import Pt
    from lxml import etree
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = name
    run.font.color.rgb = _rgb(color)
    rpr = run._r.get_or_add_rPr()
    ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
    for tag in ("ea", "cs"):
        el = rpr.find(f"{{{ns}}}{tag}")
        if el is None:
            el = etree.SubElement(rpr, f"{{{ns}}}{tag}")
        el.set("typeface", name)


def _hint_font_pt(text: str, w: float, h: float) -> float:
    """Largest size from 28 pt down to 18 pt at which `text` fits the box (about 0.55 em per character, 1.2 line height)."""
    for size in (28, 26, 24, 22, 20, 18):
        per_line = max(1, int(max(0.5, w - 0.4) * 72 / (size * 0.55)))
        lines, cur = 1, 0
        for wd in str(text).split():
            ln = len(wd)
            while ln > per_line:                      # a very long word breaks across lines
                lines, ln = lines + 1, ln - per_line
            if cur and cur + 1 + ln > per_line:
                lines, cur = lines + 1, ln
            else:
                cur = cur + 1 + ln if cur else ln
        if lines * size * 1.2 / 72 <= h - 0.2:
            return size
    return 18


def _rounded(slide, box, fill: Optional[str], line: Optional[str], line_pt: float, radius: float, name: str):
    from pptx.util import Inches, Pt
    from pptx.enum.shapes import MSO_SHAPE
    shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(box.x), Inches(box.y), Inches(box.w), Inches(box.h))
    shp.name = name
    shp.adjustments[0] = min(0.5, radius / max(0.01, min(box.w, box.h)))
    if fill:
        shp.fill.solid()
        shp.fill.fore_color.rgb = _rgb(fill)
    else:
        shp.fill.background()
    if line:
        shp.line.color.rgb = _rgb(line)
        shp.line.width = Pt(line_pt)
    else:
        shp.line.fill.background()
    shp.shadow.inherit = False
    return shp


def _readable_size(path: Optional[Path]) -> Optional[Tuple[int, int]]:
    if not path:
        return None
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
        return (w, h) if w > 0 and h > 0 else None
    except Exception as e:
        logger.warning("export: unreadable Connect picture %s: %s", path, e)
        return None


def _prepare_connect_title(slide, text: str, box, theme: Dict[str, Any]):
    """Put "N. Connect" in the layout's title placeholder when it has one (master styling applies), else a text box;
    remove every other EMPTY placeholder so no "Click to add text" remains."""
    from pptx.util import Inches
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    title_shape = None
    title_id = slide.shapes.title.shape_id if slide.shapes.title is not None else None
    for ph in list(slide.placeholders):
        if ph.shape_id == title_id and title_shape is None and hasattr(ph, "text_frame"):
            title_shape = ph
        elif not (ph.has_text_frame and ph.text_frame.text.strip()):
            ph._element.getparent().remove(ph._element)
    if title_shape is not None:
        title_shape.left, title_shape.top = Inches(box.x), Inches(box.y)
        title_shape.width, title_shape.height = Inches(box.w), Inches(box.h)
        tf = title_shape.text_frame
        tf.word_wrap = True
        tf.text = text
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        title_shape.name = "Heading"
        tb = title_shape
    else:
        tb = _text(slide, box.x, box.y, box.w, box.h, text, size=40, bold=False, color=theme["title_color"], align="center",
                   font=theme["font_body"], name="Heading")
        tb.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    for p in tb.text_frame.paragraphs:
        p.alignment = PP_ALIGN.CENTER
        for r in p.runs:
            _set_run_font(r, theme["font_body"], 40, False, theme["title_color"])
    return tb


def _connect_slides(prs, blank, d, P, resolve, blob, idx):
    """Connect as a loop: pictures joined by thick lines round a rounded rectangle, the hint in a box on the line and
    "N. Connect" in the middle (geometry in connect_layout.py). Answer on its own slide, as before."""
    from .connect_layout import connect_layout
    from pptx.util import Inches
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    theme = {**CONNECT_DEFAULT_THEME, **(d.get("theme") or {})}
    bg = d.get("question_bg")
    paths = [resolve(it.get("image")) for it in (P.get("items") or [])]
    ready = [p for p in paths if _readable_size(p)]                       # unreadable or missing pictures are skipped
    parts = split_clue_parts(P.get("text_hint"))                           # titles, instructions and labels are not clues
    if len(parts) > 2:
        parts = [parts[0], " ".join(parts[1:])]
    lay = connect_layout(len(ready), len(parts), theme["safe_box"])
    if lay["fallback"] == "grid":
        logger.warning("Connect has %d pictures; using the plain grid", len(ready))
        return _connect_grid(prs, blank, d, P, blob, idx, ready)

    s = prs.slides.add_slide(blank)
    _backdrop(s, bg)
    if lay["loop"] is not None:
        _rounded(s, lay["loop"], None, theme["line"], 4.5, 0.35, "QSHALA Loop")
    for i, (path, box) in enumerate(zip(ready, lay["nodes"]), start=1):
        iw, ih = _readable_size(path)
        k = min(box.w / iw, box.h / ih)
        pw, ph = iw * k, ih * k
        px, py = box.x + (box.w - pw) / 2, box.y + (box.h - ph) / 2
        card = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(px), Inches(py), Inches(pw), Inches(ph))
        card.name = f"QSHALA Card {i}"
        card.fill.solid()
        card.fill.fore_color.rgb = _rgb(theme["card"])
        card.line.fill.background()
        card.shadow.inherit = False
        if _picture(s, path, px, py, pw, ph, name=f"Picture {i}") is None:
            card._element.getparent().remove(card._element)
    for j, (text, hb) in enumerate(zip(parts, lay["hints"])):
        shp = _rounded(s, hb, theme["hint_fill"], None, 0, 0.18, "QSHALA Hint" if j == 0 else f"QSHALA Hint {j + 1}")
        tf = shp.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = text
        face = theme["font_head"]
        if face == "Causten Round":                       # the bold face is its own family name (see _text)
            face = "Causten Round Bold"
        _set_run_font(r, face, _hint_font_pt(text, hb.w, hb.h), True, theme["hint_text"])
    _prepare_connect_title(s, f"{idx}. Connect", lay["title"], theme)

    _answer_marker(prs, blank, bg, d.get("answer_layout"))
    sa = prs.slides.add_slide(blank)
    _backdrop(sa, bg)
    _text(sa, 1.0, 2.6, 11.3, 2.0, d["answer"], size=40, bold=True, color="0F172A", align="center", name="Title 1")
    _notes(sa, blob)


def _connect_grid(prs, blank, d, P, blob, idx, paths):
    """The old plain grid, kept for Connect questions with more pictures than a ring can hold."""
    bg = d.get("question_bg")
    s = prs.slides.add_slide(blank)
    _backdrop(s, bg)
    _text(s, 1.0, 0.55, 11.3, 0.9, f"{idx}. Connect", size=34, bold=True, name="Heading")
    clues = split_clue_parts(P.get("text_hint"))
    if clues:
        _text(s, 1.0, 1.4, 11.3, 0.8, SEPARATOR.join(clues), size=22, color="334155", name="QSHALA Hint")
    n = len(paths)
    cols = 3 if n > 4 else 2 if n > 1 else 1
    rows = max(1, -(-n // cols))
    cw, ch = 3.6, min(1.65, 4.0 / rows)
    for i, path in enumerate(paths):
        col, row = i % cols, i // cols
        _picture(s, path, 1.1 + col * (cw + 0.3), 2.3 + row * (ch + 0.15), cw, ch, name=f"Picture {i + 1}")
    _answer_marker(prs, blank, bg, d.get("answer_layout"))
    sa = prs.slides.add_slide(blank)
    _backdrop(sa, bg)
    _text(sa, 1.0, 2.6, 11.3, 2.0, d["answer"], size=40, bold=True, color="0F172A", align="center", name="Title 1")
    _notes(sa, blob)



def _stepped_slides(prs, blank, d, P, resolve, blob, idx):
    bg = d.get("question_bg")
    for i, c in enumerate(P["clues"]):
        s = prs.slides.add_slide(blank)
        _backdrop(s, bg)
        pts = ""
        if c.get("correct") is not None:
            w_ = c.get("wrong") or 0
            pts = f": +{c['correct']}/{('-' + str(abs(w_))) if w_ else '0'}"
        _text(s, 1.0, 0.55, 11.3, 0.9, f"Q{idx}. Clue {i + 1}{pts}", size=26, bold=True, color="C2410C", name="Heading")
        _text(s, 1.0, 1.7, 6.8, 4.4, c.get("text") or "", size=24, color="0F172A")
        imgs = c.get("images") or []
        for j, u in enumerate(imgs[:3]):
            _picture(s, resolve(u), 8.2, 1.7 + j * 1.5, 4.2, 1.4, name=f"Picture {j + 1}")
    _answer_marker(prs, blank, bg, d.get("answer_layout"))
    sa = prs.slides.add_slide(blank)
    _backdrop(sa, bg)
    _text(sa, 1.0, 2.6, 11.3, 2.0, d["answer"], size=40, bold=True, color="0F172A", align="center", name="Title 1")
    _notes(sa, blob)


def _arrange_slides(prs, blank, d, P, resolve, blob, idx):
    from pptx.util import Inches, Pt
    bg = d.get("question_bg")
    items = sorted(P["items"], key=lambda i: i["label"])

    def draw(slide, with_values: bool):
        for i, it in enumerate(items):
            col = i % 4
            x, y = 0.9 + col * 3.0, 2.0
            g = slide.shapes.add_group_shape()
            g.name = f"Item {it['label']}"
            lab = g.shapes.add_textbox(Inches(x), Inches(y), Inches(0.7), Inches(0.7))
            lab.text_frame.text = str(it["label"])
            lab.text_frame.paragraphs[0].font.size = Pt(28)
            lab.text_frame.paragraphs[0].font.bold = True
            lab.name = "Label"
            nm = g.shapes.add_textbox(Inches(x + 0.8), Inches(y), Inches(2.2), Inches(0.9))
            nm.text_frame.word_wrap = True
            nm.text_frame.text = it["text"] + (("\n" + it["value"]) if (with_values and it.get("value")) else "")
            nm.text_frame.paragraphs[0].font.size = Pt(18)
            nm.text_frame.paragraphs[0].font.bold = True
            nm.name = "Name"
            _picture(slide, resolve(it.get("image")), x, y + 1.1, 2.8, 3.0, shapes=g.shapes, name="Picture")

    s = prs.slides.add_slide(blank)
    _backdrop(s, bg)
    _text(s, 1.0, 0.55, 11.3, 1.3, P.get("prompt") or d["question_text"], size=26, bold=True, name="Title 1")
    draw(s, False)
    _answer_marker(prs, blank, bg, d.get("answer_layout"))
    sa = prs.slides.add_slide(blank)
    _backdrop(sa, bg)
    order = P.get("correct_order") or []
    head = ("Correct order: " + " – ".join(str(x) for x in order)) if order else "Correct order not recorded"
    _text(sa, 1.0, 0.55, 11.3, 1.3, head, size=26, bold=True, color="0F172A", name="Title 1")
    draw(sa, True)
    _notes(sa, blob)


def _mcq_slides(prs, blank, d, P, resolve, blob, idx):
    bg = d.get("question_bg")
    s = prs.slides.add_slide(blank)
    _backdrop(s, bg)
    _text(s, 1.0, 0.55, 11.3, 0.8, f"{idx}.*" if P.get("star") else f"{idx}.", size=26, bold=True, color="64748B", name="Title 1")
    _text(s, 1.0, 1.4, 11.3, 2.3, P.get("prompt") or d["question_text"], size=26, bold=True)
    _text(s, 1.1, 3.8, 9.3, 2.5, "\n".join(o["text"] for o in P["options"]), size=24, color="334155")
    _answer_marker(prs, blank, bg, d.get("answer_layout"))
    sa = prs.slides.add_slide(blank)
    _backdrop(sa, bg)
    _text(sa, 1.0, 2.6, 11.3, 2.0, d["answer"], size=40, bold=True, color="0F172A", align="center", name="Title 1")
    _notes(sa, blob)


_BUILDERS = {
    "IMAGE_REVEAL": _reveal_slides, "LIST_IT": _list_it_slides, "CONNECT": _connect_slides,
    "STEPPED_CLUE": _stepped_slides, "ARRANGE_ORDER": _arrange_slides, "MCQ": _mcq_slides,
}

_REQUIRED = {
    "IMAGE_REVEAL": ("reveal_steps",), "LIST_IT": ("options",), "CONNECT": ("items",), "STEPPED_CLUE": ("clues",),
    "ARRANGE_ORDER": ("items",), "MCQ": ("options",),
}


# ------------------------------------------------------------------------------------------------ entry point

def export_format_of(payload: Any) -> Optional[str]:
    """The format name when this payload was written by format-aware ingestion and has a static expansion, else None."""
    if not isinstance(payload, dict) or not isinstance(payload.get("extraction"), dict):
        return None
    fmt = payload.get("type")
    if fmt not in EXPORT_FORMATS:
        return None
    if not all(isinstance(payload.get(k), list) and payload.get(k) for k in _REQUIRED[fmt]):
        return None
    return fmt


def add_format_question(prs, blank_layout, *, payload: Dict[str, Any], question_text: str, answer: str, explanation: str,
                        options: Optional[List[str]], meta: Dict[str, Any], idx: int,
                        resolve: Callable[[str], Optional[Path]],
                        question_bg: Optional[Path] = None, answer_layout=None,
                        theme: Optional[Dict[str, Any]] = None) -> bool:
    """Append the slides of one question. False (nothing added) when the payload cannot be expanded."""
    fmt = export_format_of(payload)
    if not fmt:
        return False
    blob, _urls = build_blob(fmt, payload, question_text, answer, explanation, options, meta)
    before = len(prs.slides)
    d = {"question_text": question_text, "answer": answer, "question_bg": question_bg, "answer_layout": answer_layout, "theme": theme}
    try:
        _BUILDERS[fmt](prs, blank_layout, d, payload, resolve, blob, idx)
    except Exception as e:                                     # leave the deck as it was; the caller falls back
        logger.warning("format export failed for %s: %s", fmt, e)
        _drop_slides_after(prs, before)
        return False
    return True


def _drop_slides_after(prs, count: int) -> None:
    ids = prs.slides._sldIdLst
    while len(prs.slides) > count:
        sld = ids[-1]
        prs.part.drop_rel(sld.rId)
        ids.remove(sld)
