"""Slide structure read straight from the slide XML: hidden flag, layout name, every shape with its
geometry and group membership, and the main-sequence animations.

XML only -- no picture, audio or video is held in memory here. The two things that need file bytes
(a media identity hash and the tick/cross colour of a List-It overlay) are supplied by the caller as
callbacks, so the parser keeps control of how the deck's zip is read (one entry at a time, streamed).

Shape geometry is stored in the shape's OWN coordinate space plus the chain of group transforms
(outermost first); `absolute_box` applies that chain to get slide coordinates.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from lxml import etree
from pptx.enum.shapes import MSO_SHAPE_TYPE

logger = logging.getLogger(__name__)

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
_R_LINK = "{%s}link" % NS["r"]

def _xp(el, expr):
    """XPath with the p/a/r prefixes on any element (python-pptx's own xpath() only exists on its element classes)."""
    return etree._Element.xpath(el, expr, namespaces=NS)


MD5Fn = Callable[[str], Optional[str]]
OverlayFn = Callable[[str], str]

# A slide needs at least this many entrance-animated pictures (main or click-triggered) before overlay colours are read.
MIN_OVERLAYS = 6


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return text.replace("\x0b", "\n").replace("\r\n", "\n").replace("\r", "\n").replace("​", "").replace("\xa0", " ").strip()


def _group_xform(shape) -> Dict[str, List[int]]:
    xfrm = shape._element.grpSpPr.xfrm if getattr(shape._element, "grpSpPr", None) is not None else None
    if xfrm is None:
        return {"off": [0, 0], "ext": [0, 0], "chOff": [0, 0], "chExt": [0, 0]}
    return {
        "off": [int(xfrm.off.x), int(xfrm.off.y)], "ext": [int(xfrm.ext.cx), int(xfrm.ext.cy)],
        "chOff": [int(xfrm.chOff.x), int(xfrm.chOff.y)], "chExt": [int(xfrm.chExt.cx), int(xfrm.chExt.cy)],
    }


def absolute_box(shape: Dict[str, Any]) -> Tuple[float, float, float, float]:
    """(x, y, w, h) of a shape on the slide, after applying every enclosing group's transform (innermost first)."""
    x, y, w, h = float(shape.get("x") or 0), float(shape.get("y") or 0), float(shape.get("w") or 0), float(shape.get("h") or 0)
    for g in reversed(shape.get("groups") or []):
        cx, cy = g["chExt"]
        sx = g["ext"][0] / cx if cx else 1.0
        sy = g["ext"][1] / cy if cy else 1.0
        x = g["off"][0] + (x - g["chOff"][0]) * sx
        y = g["off"][1] + (y - g["chOff"][1]) * sy
        w, h = w * sx, h * sy
    return x, y, w, h


def absolute_centre(shape: Dict[str, Any]) -> Tuple[float, float]:
    x, y, w, h = absolute_box(shape)
    return x + w / 2.0, y + h / 2.0


def _media_ref(slide, shape) -> Tuple[Optional[str], Optional[str]]:
    """(zip_name, kind) of the media a picture/media shape points at: the video/audio file if there is one,
    otherwise the picture itself. (None, None) for shapes with no embedded media."""
    el = shape._element
    try:
        for tag, kind in (("videoFile", "video"), ("audioFile", "audio")):
            for node in el.iter("{%s}%s" % (NS["a"], tag)):
                rid = node.get(_R_LINK)
                if rid and not slide.part.rels[rid].is_external:
                    return str(slide.part.related_part(rid).partname).lstrip("/"), kind
        rid = getattr(el, "blip_rId", None)
        if rid:
            return str(slide.part.related_part(rid).partname).lstrip("/"), "image"
    except Exception as e:           # a dangling relationship must never stop parsing
        logger.debug("media ref unreadable for shape %s: %s", getattr(shape, "name", "?"), e)
    return None, None


def read_animations(slide, id_to_name: Dict[int, str]) -> List[Dict[str, Any]]:
    """Effects of the MAIN sequence in click order. Effects inside an interactive sequence (triggered by
    clicking some shape) are not part of the presentation flow and are ignored."""
    sld = slide._element
    timing = sld.find("{%s}timing" % NS["p"])
    if timing is None:
        return []
    mains = _xp(timing, './/p:cTn[@nodeType="mainSeq"]')
    if not mains:
        return []
    out: List[Dict[str, Any]] = []
    for ctn in _xp(mains[0], './/p:cTn[@presetClass and (@nodeType="clickEffect" or @nodeType="withEffect" or @nodeType="afterEffect")]'):
        if _xp(ctn, 'ancestor::p:cTn[@nodeType="interactiveSeq"]'):
            continue
        spids = _xp(ctn, ".//p:spTgt/@spid")
        if not spids:
            continue
        try:
            preset = int(ctn.get("presetID", "0"))
        except ValueError:
            preset = 0
        tid = int(spids[0])
        out.append({"order": len(out) + 1, "cls": ctn.get("presetClass"), "preset": preset,
                    "node_type": ctn.get("nodeType"), "target_id": tid, "target_name": id_to_name.get(tid, "")})
    return out


def read_interactive_animations(slide, id_to_name: Dict[int, str]) -> List[Dict[str, Any]]:
    """Effects played when a shape is clicked (interactive sequences): List It reveals each overlay when its option is
    clicked. Kept apart from the main sequence, which alone carries the Image Reveal order."""
    timing = slide._element.find("{%s}timing" % NS["p"])
    if timing is None:
        return []
    out: List[Dict[str, Any]] = []
    for seq in _xp(timing, './/p:cTn[@nodeType="interactiveSeq"]'):
        trig = _xp(seq, "./p:stCondLst/p:cond/p:tgtEl/p:spTgt/@spid")
        trigger_id = int(trig[0]) if trig else None
        for ctn in _xp(seq, ".//p:cTn[@presetClass]"):
            spids = _xp(ctn, ".//p:spTgt/@spid")
            if not spids:
                continue
            try:
                preset = int(ctn.get("presetID", "0"))
            except ValueError:
                preset = 0
            tid = int(spids[0])
            out.append({"order": len(out) + 1, "trigger_id": trigger_id,
                        "trigger_name": id_to_name.get(trigger_id, "") if trigger_id is not None else "",
                        "cls": ctn.get("presetClass"), "preset": preset, "target_id": tid,
                        "target_name": id_to_name.get(tid, "")})
    return out


def _walk(slide, shapes, groups: List[Dict], path: List[str], out: List[Dict[str, Any]], md5_fn: Optional[MD5Fn],
          path_ids: Optional[List[int]] = None) -> None:
    path_ids = path_ids or []
    for s in shapes:
        try:
            st = s.shape_type
        except Exception:
            st = None
        is_group = st == MSO_SHAPE_TYPE.GROUP
        rec: Dict[str, Any] = {
            "id": int(s.shape_id), "name": s.name or "", "type": "OTHER", "text": "",
            "x": 0, "y": 0, "w": 0, "h": 0,
            "groups": list(groups), "path": list(path), "path_ids": list(path_ids),
            "media_md5": None, "media_kind": None, "overlay_class": None,
            "zip_name": None, "media_url": None,
        }
        try:
            rec["x"], rec["y"], rec["w"], rec["h"] = int(s.left or 0), int(s.top or 0), int(s.width or 0), int(s.height or 0)
        except Exception:
            pass
        if is_group:
            rec["type"] = "GROUP"
            xf = _group_xform(s)
            rec["x"], rec["y"], rec["w"], rec["h"] = xf["off"][0], xf["off"][1], xf["ext"][0], xf["ext"][1]
            out.append(rec)
            try:
                _walk(slide, s.shapes, groups + [xf], path + [s.name or ""], out, md5_fn, path_ids + [int(s.shape_id)])
            except Exception as e:
                logger.debug("group %s not walkable: %s", s.name, e)
            continue
        if s._element.tag.endswith("}pic"):
            zip_name, kind = _media_ref(slide, s)
            rec["type"] = "MEDIA" if kind in ("video", "audio") else "PICTURE"
            rec["media_kind"] = kind
            rec["zip_name"] = zip_name
            if zip_name and md5_fn is not None:
                rec["media_md5"] = md5_fn(zip_name)
        elif getattr(s, "has_text_frame", False) and s.has_text_frame:
            rec["type"] = "TEXT_BOX"
            rec["text"] = _clean(s.text_frame.text)
        elif getattr(s, "has_table", False) and s.has_table:
            rec["type"] = "TABLE"
            rec["text"] = "\n".join(" | ".join(_clean(c.text) for c in row.cells) for row in s.table.rows)
        elif st == MSO_SHAPE_TYPE.AUTO_SHAPE:
            rec["type"] = "AUTO_SHAPE"
        out.append(rec)


def extract_slide_structure(slide, md5_fn: Optional[MD5Fn] = None, overlay_fn: Optional[OverlayFn] = None) -> Dict[str, Any]:
    """{hidden, layout_name, shapes, animations} for one python-pptx slide. Never raises."""
    result: Dict[str, Any] = {"hidden": False, "layout_name": "", "shapes": [], "animations": [],
                              "interactive_animations": []}
    try:
        result["hidden"] = slide._element.get("show") == "0"
    except Exception:
        pass
    try:
        result["layout_name"] = slide.slide_layout.name or ""
    except Exception:
        pass
    try:
        shapes: List[Dict[str, Any]] = []
        _walk(slide, slide.shapes, [], [], shapes, md5_fn)
        id_to_name = {s["id"]: s["name"] for s in shapes}
        anims = read_animations(slide, id_to_name)
        inter = read_interactive_animations(slide, id_to_name)
        if overlay_fn is not None:
            entering = {a["target_id"] for a in anims + inter if a["cls"] == "entr"}
            cands = [s for s in shapes if s["id"] in entering and s["type"] == "PICTURE" and s.get("zip_name")]
            if len(cands) >= MIN_OVERLAYS:
                for s in cands:
                    try:
                        s["overlay_class"] = overlay_fn(s["zip_name"])
                    except Exception as e:
                        logger.debug("overlay colour unreadable for %s: %s", s["zip_name"], e)
                        s["overlay_class"] = "unknown"
        result["shapes"] = shapes
        result["animations"] = anims
        result["interactive_animations"] = inter
    except Exception as e:
        logger.warning("slide structure unreadable: %s", e)
    return result


def classify_overlay_pixels(im) -> str:
    """'tick' (green), 'cross' (red) or 'unknown' from the opaque, coloured pixels of a PIL image."""
    import colorsys
    im = im.convert("RGBA")
    im.thumbnail((48, 48))
    greens = reds = 0
    for r, g, b, a in im.getdata():
        if a < 128:
            continue
        h, sat, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        if sat < 0.35 or v < 0.2:
            continue                      # white strokes, grey, black outlines carry no signal
        deg = h * 360.0
        if 70 <= deg <= 170:
            greens += 1
        elif deg <= 20 or deg >= 340:
            reds += 1
    if greens + reds < 8:
        return "unknown"
    if greens > 2 * reds:
        return "tick"
    if reds > 2 * greens:
        return "cross"
    return "unknown"
