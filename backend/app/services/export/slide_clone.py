"""Slide-level helpers for custom templates: duplicate a real slide, drop a slide, fill its text in place.

A custom template's real title slide and Answer divider are kept as-is (logos, shapes, art, formatting) and only their
text is replaced, instead of being rebuilt from scratch on a layout.
"""
from __future__ import annotations

import copy
import logging
from typing import Any, Optional

from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml.ns import qn
from pptx.util import Emu

logger = logging.getLogger(__name__)

_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_SKIP_RELS = {RT.SLIDE_LAYOUT, RT.NOTES_SLIDE}
_SHAPE_TAGS = {qn(t) for t in ("p:sp", "p:pic", "p:graphicFrame", "p:grpSp", "p:cxnSp")} | {
    "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent"
}


def duplicate_slide(prs: Any, src: Any) -> Any:
    """Append a copy of `src` (same layout, background, shapes, pictures, hyperlinks) and return it."""
    new = prs.slides.add_slide(src.slide_layout)
    for shp in list(new.shapes):
        shp._element.getparent().remove(shp._element)

    new_cSld, src_cSld = new._element.find(qn("p:cSld")), src._element.find(qn("p:cSld"))
    old_bg = new_cSld.find(qn("p:bg"))
    if old_bg is not None:
        new_cSld.remove(old_bg)
    src_bg = src_cSld.find(qn("p:bg"))

    rid_map: dict[str, str] = {}
    for rid, rel in src.part.rels.items():
        if rel.reltype in _SKIP_RELS:
            continue
        try:
            if rel.is_external:
                rid_map[rid] = new.part.relate_to(rel.target_ref, rel.reltype, is_external=True)
            else:
                rid_map[rid] = new.part.relate_to(rel.target_part, rel.reltype)
        except Exception as e:  # a broken relationship must not sink the whole export
            logger.warning("duplicate_slide: could not copy relationship %s (%s)", rid, e)

    def _remap(el) -> None:
        for node in el.iter():
            for attr, val in list(node.attrib.items()):
                if attr.startswith("{%s}" % _R_NS) and val in rid_map:
                    node.set(attr, rid_map[val])

    if src_bg is not None:
        bg = copy.deepcopy(src_bg)
        _remap(bg)
        new_cSld.insert(0, bg)

    tree = new.shapes._spTree
    for el in src.shapes._spTree:
        if el.tag not in _SHAPE_TAGS:
            continue
        clone = copy.deepcopy(el)
        _remap(clone)
        tree.append(clone)
    return new


def drop_slide(prs: Any, slide: Any) -> None:
    """Remove a slide from the deck (its part is not saved once nothing references it)."""
    for sld_id in list(prs.slides._sldIdLst):
        if prs.part.related_part(sld_id.rId) is slide.part:
            prs.part.drop_rel(sld_id.rId)
            prs.slides._sldIdLst.remove(sld_id)
            return


# ----------------------------------------------------------------------------------------------------------------
# In-place text filling
# ----------------------------------------------------------------------------------------------------------------

def _first_size_pt(shape) -> float:
    try:
        for par in shape.text_frame.paragraphs:
            for r in par.runs:
                if r.font.size:
                    return float(r.font.size.pt)
    except Exception:
        pass
    return 0.0


def _text_shapes(slide) -> list:
    """Top-level shapes (and group members) that can hold text, in z-order."""
    found = []

    def walk(shapes):
        for sh in shapes:
            if sh.shape_type == MSO_SHAPE_TYPE.GROUP and hasattr(sh, "shapes"):
                walk(sh.shapes)
            elif getattr(sh, "has_text_frame", False) and sh.has_text_frame:
                found.append(sh)

    walk(slide.shapes)
    return found


def _ph_type(sh) -> Optional[Any]:
    try:
        return sh.placeholder_format.type if sh.is_placeholder else None
    except Exception:
        return None


def find_title_and_subtitle(slide) -> tuple[Optional[Any], Optional[Any]]:
    """Title = title placeholder, else the text shape with the biggest font; subtitle = the next text shape below it."""
    shapes = _text_shapes(slide)
    title = next((s for s in shapes if _ph_type(s) in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE)), None)
    if title is None:
        with_text = [s for s in shapes if s.text_frame.text.strip()]
        if with_text:
            title = max(with_text, key=lambda s: (_first_size_pt(s), s.width * s.height if s.width and s.height else 0))
    if title is None:
        return None, None
    sub = next((s for s in shapes if _ph_type(s) == PP_PLACEHOLDER.SUBTITLE and s is not title), None)
    if sub is None:
        t_top = title.top or 0
        below = [s for s in shapes if s is not title and s.text_frame.text.strip() and (s.top or 0) >= t_top]
        if below:
            sub = min(below, key=lambda s: ((s.top or 0), -(_first_size_pt(s))))
    return title, sub


def fill_text(shape, text: str) -> None:
    """Replace a shape's text but keep its formatting: the first run's style is reused, extra runs/paragraphs go."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()] or [""]
    tf = shape.text_frame
    paras = list(tf.paragraphs)
    p0 = paras[0]
    runs = list(p0.runs)
    orig_len = max(1, len(tf.text.strip()))
    if runs:
        runs[0].text = lines[0]
        for r in runs[1:]:
            r._r.getparent().remove(r._r)
    else:
        p0.add_run().text = lines[0]
    for p in paras[1:]:
        p._p.getparent().remove(p._p)
    prev = p0._p
    for ln in lines[1:]:
        clone = copy.deepcopy(p0._p)
        prev.addnext(clone)
        prev = clone
        rs = clone.findall(qn("a:r"))
        if rs:
            t = rs[0].find(qn("a:t"))
            if t is not None:
                t.text = ln
    # A much longer title than the template's sample text would overflow its box: shrink gently.
    longest = max(len(ln) for ln in lines)
    if longest > 1.6 * orig_len and longest > 28:
        factor = max(0.6, (1.6 * orig_len) / longest)
        for par in tf.paragraphs:
            for r in par.runs:
                if r.font.size:
                    r.font.size = Emu(int(r.font.size * factor))
