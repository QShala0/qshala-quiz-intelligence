"""
Post-build helpers that make a generated deck correct on ANY template, not just 13.333 x 7.5 in ones.

The exporter lays every slide out on a 13.333 x 7.5 in canvas (that is what the QShala school master uses). A custom
template extracted from a sample deck can be 10 x 5.625 in (Google Slides / older PowerPoint 16:9), 10 x 7.5 in (4:3),
or any other size; before this module the shapes simply ran off the slide.

`fit_deck_to_slide` re-maps the finished slides onto the template's real slide size:
  * uniform scale s = min(W/13.333, H/7.5) with the leftover space split evenly (letterbox), so nothing is distorted
  * full-slide background pictures are snapped to exactly W x H (they must bleed to every edge)
  * font sizes, line spacing and outline widths are scaled by s, so text still fits its box
  * placeholders that inherit their position from the layout are left alone
`remove_empty_placeholders` deletes the empty "Click to add title/text" boxes a layout leaves on generated slides.
"""
from __future__ import annotations

import logging
from typing import Any

from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
from pptx.util import Inches

logger = logging.getLogger(__name__)

BASE_W = Inches(13.333)
BASE_H = Inches(7.5)
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

_KEEP_EMPTY = {  # placeholders whose job is not text: leave them even when "empty"
    PP_PLACEHOLDER.PICTURE, PP_PLACEHOLDER.CHART, PP_PLACEHOLDER.TABLE, PP_PLACEHOLDER.MEDIA_CLIP,
    PP_PLACEHOLDER.SLIDE_NUMBER, PP_PLACEHOLDER.FOOTER, PP_PLACEHOLDER.DATE,
}


def _close(a: float, b: float, tol: float = 0.03) -> bool:
    return b != 0 and abs(a - b) / b <= tol


def needs_fit(prs: Any) -> bool:
    return not (_close(prs.slide_width, BASE_W, 0.01) and _close(prs.slide_height, BASE_H, 0.01))


def _own_xfrm(shape):
    """The shape's own <a:xfrm>/<p:xfrm>, or None when its position is inherited from the layout."""
    el = shape._element
    found = el.xpath("./p:spPr/a:xfrm | ./p:grpSpPr/a:xfrm | ./p:xfrm")
    return found[0] if found else None


def _is_background_picture(shape, W: int, H: int) -> bool:
    try:
        if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
            return False
        if abs(shape.left) > 0.02 * W or abs(shape.top) > 0.02 * H:
            return False
        for tw, th in ((W, H), (BASE_W, BASE_H)):
            if _close(shape.width, tw) and _close(shape.height, th):
                return True
    except Exception:
        pass
    return False


def _scale_text_metrics(slide, s: float) -> None:
    """Scale every explicit font size / line spacing / outline width on the slide by s."""
    root = slide._element
    for tag in ("rPr", "defRPr", "endParaRPr"):
        for el in root.iter(f"{_A}{tag}"):
            sz = el.get("sz")
            if sz and sz.isdigit():
                el.set("sz", str(max(100, int(round(int(sz) * s / 50.0)) * 50)))
    for el in root.iter(f"{_A}spcPts"):
        val = el.get("val")
        if val and val.isdigit():
            el.set("val", str(max(0, int(round(int(val) * s)))))
    for el in root.iter(f"{_A}ln"):
        w = el.get("w")
        if w and w.isdigit():
            el.set("w", str(max(0, int(round(int(w) * s)))))


def _clamp_into_slide(shape, W: int, H: int) -> None:
    """Pull a shape that sticks out of the slide back inside it (also covers placeholders whose geometry is inherited
    from a layout that does not match the slide size, e.g. a 4:3 layout in a 16:9 deck)."""
    l, t, w, h = shape.left, shape.top, shape.width, shape.height
    if None in (l, t, w, h):
        return
    if l >= -0.01 * W and t >= -0.01 * H and l + w <= 1.01 * W and t + h <= 1.01 * H:
        return
    w2, h2 = min(w, W), min(h, H)
    shape.width, shape.height = int(w2), int(h2)
    shape.left = int(min(max(l, 0), W - w2))
    shape.top = int(min(max(t, 0), H - h2))


def fit_deck_to_slide(prs: Any) -> bool:
    """Re-map all slides built on the 13.333 x 7.5 in canvas onto the presentation's real slide size, then make sure
    nothing sticks out of the slide. Returns True when a rescale was needed."""
    W, H = int(prs.slide_width), int(prs.slide_height)
    if not needs_fit(prs):
        for slide in prs.slides:
            for shape in slide.shapes:
                try:
                    _clamp_into_slide(shape, W, H)
                except Exception:
                    continue
        return False
    s = min(W / BASE_W, H / BASE_H)
    ox = (W - BASE_W * s) / 2.0
    oy = (H - BASE_H * s) / 2.0
    for slide in prs.slides:
        for shape in slide.shapes:
            try:
                if _is_background_picture(shape, W, H):
                    shape.left, shape.top, shape.width, shape.height = 0, 0, W, H
                    continue
                if _own_xfrm(shape) is None:
                    _clamp_into_slide(shape, W, H)  # inherits layout geometry: right for this template unless the layout is off
                    continue
                shape.left = int(round(ox + shape.left * s))
                shape.top = int(round(oy + shape.top * s))
                shape.width = int(round(shape.width * s))
                shape.height = int(round(shape.height * s))
                _clamp_into_slide(shape, W, H)
            except Exception as e:  # one odd shape must not sink the whole export
                logger.debug("fit_deck_to_slide: skipped %s (%s)", getattr(shape, "name", "?"), e)
        _scale_text_metrics(slide, s)
    logger.info("Fitted deck to %.2f x %.2f in template (scale %.3f)", W / 914400, H / 914400, s)
    return True


def remove_empty_placeholders(prs: Any) -> int:
    """Delete text placeholders with no text (they show as 'Click to add title' in edit view). Returns count removed."""
    removed = 0
    for slide in prs.slides:
        for shape in list(slide.placeholders):
            try:
                if shape.placeholder_format.type in _KEEP_EMPTY:
                    continue
                if shape.has_text_frame and not shape.text_frame.text.strip():
                    shape._element.getparent().remove(shape._element)
                    removed += 1
            except Exception:
                continue
    return removed
