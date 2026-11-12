"""PPTX export v1 for format-aware questions: STATIC expansion, no animation authoring.

    IMAGE_REVEAL   4 progressive slides (all covers -> one more lifted each slide) + ANSWER + answer slide
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


def _text(slide, x, y, w, h, text, size=20, bold=False, color="0F172A", align="left", font="Inter", name=None):
    from pptx.util import Inches, Pt
    from pptx.enum.text import PP_ALIGN
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
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
        p.font.name = font
        p.font.color.rgb = _rgb(color)
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


def _answer_marker(prs, blank):
    s = prs.slides.add_slide(blank)
    _text(s, 3.0, 2.8, 7.333, 1.8, "ANSWER", size=54, bold=True, align="center", name="Title 1")
    return s


def _notes(slide, text: str) -> None:
    slide.notes_slide.notes_text_frame.text = text


# ------------------------------------------------------------------------------------------------ per-format slides

def _reveal_slides(prs, blank, d: Dict[str, Any], P: Dict[str, Any], resolve: Callable[[str], Optional[Path]], blob: str, idx: int):
    steps = P["reveal_steps"]
    full = resolve(P.get("full_image")) if P.get("full_image") else None
    X, Y, W, H = 0.7, 1.5, 6.0, 5.4
    cells = {"top-left": (0, 0), "top-right": (1, 0), "bottom-left": (0, 1), "bottom-right": (1, 1)}
    for k in range(len(steps)):
        s = prs.slides.add_slide(blank)
        _text(s, 0.7, 0.35, 11.9, 0.9, P.get("prompt") or d["question_text"], size=26, bold=True, name="Title 1")
        pic = _picture(s, full, X, Y, W, H, name="Full Image")
        # the covers sit on the picture as placed (scaled to fit, centred), not on the box it was fitted into
        X0, Y0, W0, H0 = (X, Y, W, H) if pic is None else (pic.left / 914400.0, pic.top / 914400.0, pic.width / 914400.0, pic.height / 914400.0)
        for i, st in enumerate(steps):
            if i < k:
                continue                                    # this cover is already lifted on this slide
            cx, cy = cells.get(st.get("position"), (i % 2, i // 2))
            c, w_ = st.get("correct"), st.get("wrong")
            ladder = f"+{c} / {MINUS}{abs(w_)}" if w_ else f"+{c} / 0"
            _box(s, X0 + cx * W0 / 2, Y0 + cy * H0 / 2, W0 / 2, H0 / 2, "1E293B", "475569", f"{st['step']}\n{ladder}", size=20,
                 name=f"Cover {st['step']}")
            if st.get("cover"):                                  # the deck's own cover tile, when it has one
                _picture(s, resolve(st["cover"]), X0 + cx * W0 / 2, Y0 + cy * H0 / 2, W0 / 2, H0 / 2, name=f"Cover Tile {st['step']}")
        lines = "\n".join(f"{st['step']}.  +{st['correct']} / {MINUS}{abs(st['wrong'])}" if st.get("wrong") else f"{st['step']}.  +{st['correct']} / 0"
                          for st in steps)
        _text(s, 7.3, 1.6, 5.3, 3.0, "Points if answered after cover\n" + lines, size=18, color="334155")
        _text(s, 7.3, 5.9, 5.3, 0.8, f"Covers lifted: {k} of {len(steps)}", size=14, color="64748B")
        if k == 0:
            _notes(s, blob)
    _answer_marker(prs, blank)
    sa = prs.slides.add_slide(blank)
    _text(sa, 0.7, 0.35, 11.9, 1.0, d["answer"], size=34, bold=True, color="059669", name="Title 1")
    _picture(sa, full, 3.2, 1.6, 6.9, 5.5, name="Full Image")


def _list_it_slides(prs, blank, d, P, resolve, blob, idx):
    def grid(slide, marks: bool):
        for i, o in enumerate(P["options"][:9]):
            col, row = i % 3, i // 3
            x, y = 0.8 + col * 4.0, 2.2 + row * 1.5
            text = o["text"]
            if marks:
                good = o.get("correct")
                prefix = "✓  " if good else ("✗  " if good is False else "")
                _box(slide, x, y, 3.8, 1.3, "DCFCE7" if good else ("FEE2E2" if good is False else "F1F5F9"),
                     "16A34A" if good else ("DC2626" if good is False else "CBD5E1"), prefix + text, size=18, color="14532D" if good else ("7F1D1D" if good is False else "334155"),
                     name=f"Option {i + 1}")
            else:
                _box(slide, x, y, 3.8, 1.3, "F8FAFC", "94A3B8", text, size=18, color="0F172A", name=f"Option {i + 1}")

    s = prs.slides.add_slide(blank)
    title = P.get("prompt") or d["question_text"]
    if P.get("team"):
        title += f"\nTeam {P['team']}"
    _text(s, 0.8, 0.3, 11.7, 1.7, title, size=26, bold=True, name="Title 1")
    grid(s, False)
    _notes(s, blob)
    _answer_marker(prs, blank)
    sa = prs.slides.add_slide(blank)
    _text(sa, 0.8, 0.3, 11.7, 1.7, "Correct answers are ticked", size=26, bold=True, color="059669", name="Title 1")
    grid(sa, True)


def _connect_slides(prs, blank, d, P, resolve, blob, idx):
    s = prs.slides.add_slide(blank)
    _text(s, 0.7, 0.3, 11.9, 0.9, "Connect", size=34, bold=True, name="Title 1")
    if P.get("text_hint"):
        _text(s, 0.7, 1.15, 11.9, 0.8, P["text_hint"], size=22, color="334155")
    items = P.get("items") or []
    n = len(items)
    cols = 3 if n > 4 else 2 if n > 1 else 1
    rows = max(1, -(-n // cols))
    cw, ch = 3.6, min(1.65, 4.6 / rows)
    for i, it in enumerate(items):
        col, row = i % cols, i // cols
        _picture(s, resolve(it.get("image")), 0.9 + col * (cw + 0.3), 2.1 + row * (ch + 0.15), cw, ch, name=f"Picture {i + 1}")
    _notes(s, blob)
    _answer_marker(prs, blank)
    sa = prs.slides.add_slide(blank)
    _text(sa, 0.7, 2.6, 11.9, 2.0, d["answer"], size=40, bold=True, color="059669", align="center", name="Title 1")


def _stepped_slides(prs, blank, d, P, resolve, blob, idx):
    for i, c in enumerate(P["clues"]):
        s = prs.slides.add_slide(blank)
        pts = ""
        if c.get("correct") is not None:
            w_ = c.get("wrong") or 0
            pts = f": +{c['correct']}/{('-' + str(abs(w_))) if w_ else '0'}"
        _text(s, 0.7, 0.3, 11.9, 0.9, f"Q{idx}. Clue {i + 1}{pts}", size=26, bold=True, color="C2410C", name="Title 1")
        _text(s, 0.7, 1.5, 7.0, 5.0, c.get("text") or "", size=24, color="0F172A")
        imgs = c.get("images") or []
        for j, u in enumerate(imgs[:3]):
            _picture(s, resolve(u), 8.2, 1.5 + j * 1.9, 4.4, 1.8, name=f"Picture {j + 1}")
        if i == 0:
            _notes(s, blob)
    _answer_marker(prs, blank)
    sa = prs.slides.add_slide(blank)
    _text(sa, 0.7, 2.6, 11.9, 2.0, d["answer"], size=40, bold=True, color="059669", align="center", name="Title 1")


def _arrange_slides(prs, blank, d, P, resolve, blob, idx):
    from pptx.util import Inches, Pt
    items = sorted(P["items"], key=lambda i: i["label"])

    def draw(slide, with_values: bool):
        for i, it in enumerate(items):
            col = i % 4
            x, y = 0.5 + col * 3.2, 2.0
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
            _picture(slide, resolve(it.get("image")), x, y + 1.1, 3.0, 3.2, shapes=g.shapes, name="Picture")

    s = prs.slides.add_slide(blank)
    _text(s, 0.7, 0.3, 11.9, 1.4, P.get("prompt") or d["question_text"], size=26, bold=True, name="Title 1")
    draw(s, False)
    _notes(s, blob)
    _answer_marker(prs, blank)
    sa = prs.slides.add_slide(blank)
    order = P.get("correct_order") or []
    head = ("Correct order: " + " – ".join(str(x) for x in order)) if order else "Correct order not recorded"
    _text(sa, 0.7, 0.3, 11.9, 1.4, head, size=26, bold=True, color="059669", name="Title 1")
    draw(sa, True)


def _mcq_slides(prs, blank, d, P, resolve, blob, idx):
    s = prs.slides.add_slide(blank)
    _text(s, 0.7, 0.3, 11.9, 0.8, f"{idx}.*" if P.get("star") else f"{idx}.", size=26, bold=True, color="64748B", name="Title 1")
    _text(s, 0.7, 1.2, 11.9, 2.4, P.get("prompt") or d["question_text"], size=26, bold=True)
    _text(s, 0.9, 3.8, 11.5, 3.2, "\n".join(o["text"] for o in P["options"]), size=24, color="334155")
    _notes(s, blob)
    _answer_marker(prs, blank)
    sa = prs.slides.add_slide(blank)
    _text(sa, 0.7, 2.6, 11.9, 2.0, d["answer"], size=40, bold=True, color="059669", align="center", name="Title 1")


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
                        resolve: Callable[[str], Optional[Path]]) -> bool:
    """Append the slides of one question. False (nothing added) when the payload cannot be expanded."""
    fmt = export_format_of(payload)
    if not fmt:
        return False
    blob, _urls = build_blob(fmt, payload, question_text, answer, explanation, options, meta)
    before = len(prs.slides)
    d = {"question_text": question_text, "answer": answer}
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
