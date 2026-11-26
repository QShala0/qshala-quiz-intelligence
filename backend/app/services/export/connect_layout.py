"""Pure geometry for the Connect "loop" slide (no pptx objects, inches everywhere).

Pictures sit ON a rounded-rectangle loop and cover the line beneath them; the hint box sits on the middle of an edge; the
title ("N. Connect") is centred inside the loop. `connect_layout` returns boxes only, so it can be unit tested without
python-pptx. The renderer lives in `format_slides._connect_slides`.

Ring model: a cols x rows grid whose border cells are the slots. Pictures are edge-anchored (flush to the safe box, evenly
spaced), and the loop passes through the slot centres.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

MAX_RING_ITEMS = 10          # more pictures than this fall back to the old grid (D6)
GAP = 0.15                   # minimum gap between pictures
HINT_CLEAR = 0.10            # minimum clear space between a hint box and any picture
TITLE_W, TITLE_H = 5.0, 0.9
HINT_H_EDGE, HINT_W_TOPBOTTOM = 1.05, 4.4          # hint on a horizontal edge
HINT_W_SIDE, HINT_H_SIDE = 2.6, 1.8                # hint on a vertical edge
HINT_W_CENTRE, HINT_H_CENTRE = 4.4, 1.05           # hint in the middle (no free edge slot)
LOOP_RADIUS = 0.35

# slot tables: (cols, rows, [(col, row), ...]) clockwise from the top-left, 0-indexed
_FULL_3X3 = [(0, 0), (1, 0), (2, 0), (2, 1), (2, 2), (1, 2), (0, 2), (0, 1)]
_FULL_4X3 = [(0, 0), (1, 0), (2, 0), (3, 0), (3, 1), (3, 2), (2, 2), (1, 2), (0, 2), (0, 1)]
_RINGS: Dict[int, Tuple[int, int, List[Tuple[int, int]]]] = {
    2: (3, 3, [(0, 1), (2, 1)]),
    3: (3, 2, [(0, 0), (2, 0), (1, 1)]),
    4: (2, 2, [(0, 0), (1, 0), (1, 1), (0, 1)]),
    5: (3, 2, [(0, 0), (1, 0), (2, 0), (2, 1), (0, 1)]),
    6: (3, 2, [(0, 0), (1, 0), (2, 0), (2, 1), (1, 1), (0, 1)]),
    7: (3, 3, [s for s in _FULL_3X3 if s != (0, 1)]),
    8: (3, 3, _FULL_3X3),
    9: (4, 3, [s for s in _FULL_4X3 if s != (0, 1)]),
    10: (4, 3, _FULL_4X3),
}
# with a hint, 6 pictures use a 3-row ring so the right edge is free for the hint box
_RING_6_WITH_HINT = (3, 3, [(0, 0), (1, 0), (2, 0), (2, 2), (1, 2), (0, 2)])


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    def overlaps(self, o: "Box", pad: float = 0.0) -> bool:
        return not (self.x2 + pad <= o.x or o.x2 + pad <= self.x or self.y2 + pad <= o.y or o.y2 + pad <= self.y)

    def inside(self, o: "Box", eps: float = 1e-6) -> bool:
        return self.x >= o.x - eps and self.y >= o.y - eps and self.x2 <= o.x2 + eps and self.y2 <= o.y2 + eps


def _centred(cx: float, cy: float, w: float, h: float) -> Box:
    return Box(cx - w / 2, cy - h / 2, w, h)


def _clamp_into(b: Box, safe: Box) -> Box:
    x = min(max(b.x, safe.x), safe.x2 - b.w)
    y = min(max(b.y, safe.y), safe.y2 - b.h)
    return Box(x, y, b.w, b.h)


def _single(n_hint: int, safe: Box) -> dict:
    """N=1: title on top, one big picture, hint box(es) underneath. No loop."""
    title = Box(safe.x + (safe.w - TITLE_W) / 2, safe.y, TITLE_W, TITLE_H)
    hint_block = n_hint * (HINT_H_EDGE + 0.15)
    top = title.y2 + 0.2
    bottom = safe.y2 - hint_block
    pw, ph = min(8.0, safe.w), min(5.0, bottom - top)
    node = Box(safe.x + (safe.w - pw) / 2, top + (bottom - top - ph) / 2, pw, ph)
    hints = [Box(safe.x + (safe.w - HINT_W_CENTRE) / 2, safe.y2 - (n_hint - i) * (HINT_H_EDGE + 0.15) + 0.15,
                 HINT_W_CENTRE, HINT_H_EDGE) for i in range(n_hint)]
    return {"fallback": None, "nodes": [node], "hints": hints, "hint": hints[0] if hints else None, "loop": None,
            "title": title, "hint_sides": ["bottom"] * n_hint}


def _ring(n_items: int, n_hint: int, safe: Box, ph_cap: Optional[float]) -> Tuple[dict, int]:
    cols, rows, slots = _RING_6_WITH_HINT if (n_items == 6 and n_hint) else _RINGS[n_items]
    max_w, max_h = (3.2, 2.3) if cols <= 3 else (2.4, 1.9)
    pw = min(max_w, (safe.w - (cols - 1) * GAP) / cols)
    ph = min(max_h, (safe.h - (rows - 1) * GAP) / rows)
    if ph_cap is not None:
        ph = min(ph, ph_cap)
    xs = [safe.x + pw / 2 + i * (safe.w - pw) / (cols - 1) for i in range(cols)]
    ys = [safe.y + ph / 2 + j * (safe.h - ph) / (rows - 1) for j in range(rows)]
    nodes = [_centred(xs[c], ys[r], pw, ph) for c, r in slots]
    loop = Box(xs[0], ys[0], xs[-1] - xs[0], ys[-1] - ys[0])
    cx, cy = loop.x + loop.w / 2, loop.y + loop.h / 2

    # a side hint must fit between the picture rows above and below it
    side_h = min(HINT_H_SIDE, max(HINT_H_EDGE, safe.h - 2 * ph - 2 * HINT_CLEAR)) if rows >= 3 else HINT_H_SIDE
    # hint candidates: the middle of each edge, in the order top, right, bottom, left (D2), then the centre
    cand = [
        ("top", _clamp_into(_centred(cx, ys[0], HINT_W_TOPBOTTOM, HINT_H_EDGE), safe)),
        ("right", _clamp_into(_centred(xs[-1], cy, HINT_W_SIDE, side_h), safe)),
        ("bottom", _clamp_into(_centred(cx, ys[-1], HINT_W_TOPBOTTOM, HINT_H_EDGE), safe)),
        ("left", _clamp_into(_centred(xs[0], cy, HINT_W_SIDE, side_h), safe)),
    ]
    hints: List[Box] = []
    sides: List[str] = []
    for side, b in cand:
        if len(hints) >= n_hint:
            break
        if any(b.overlaps(n, HINT_CLEAR) for n in nodes) or any(b.overlaps(h, HINT_CLEAR) for h in hints):
            continue
        hints.append(b)
        sides.append(side)

    title = _centred(cx, cy, TITLE_W, TITLE_H)
    centre_hints = n_hint - len(hints)
    if centre_hints > 0:                              # no free edge: stack title and hint(s) in the middle
        hh = HINT_H_CENTRE
        block = TITLE_H + centre_hints * (0.15 + hh)
        title = Box(cx - TITLE_W / 2, cy - block / 2, TITLE_W, TITLE_H)
        y = title.y2 + 0.15
        for _ in range(centre_hints):
            hints.append(Box(cx - HINT_W_CENTRE / 2, y, HINT_W_CENTRE, hh))
            sides.append("centre")
            y += hh + 0.15
    out = {"fallback": None, "nodes": nodes, "hints": hints, "hint": hints[0] if hints else None, "loop": loop,
           "title": title, "hint_sides": sides}
    return out, centre_hints


def connect_layout(n_items: int, has_hint, safe_box: Tuple[float, float, float, float]) -> dict:
    """Boxes for a Connect question.

    `has_hint` is a bool or the number of hint boxes (0..2). `safe_box` is (x, y, w, h) in inches. Returns
    {fallback, nodes, hints, hint, loop, title, hint_sides}; `fallback == "grid"` (and nothing else useful) when the
    picture count does not fit a ring. Deterministic.
    """
    safe = Box(*safe_box)
    n_hint = int(has_hint) if not isinstance(has_hint, bool) else (1 if has_hint else 0)
    n_hint = max(0, min(2, n_hint))
    if n_items < 1 or n_items > MAX_RING_ITEMS:
        return {"fallback": "grid", "nodes": [], "hints": [], "hint": None, "loop": None, "title": None, "hint_sides": []}
    if n_items == 1:
        return _single(n_hint, safe)

    out, centre_hints = _ring(n_items, n_hint, safe, None)
    for _ in range(3):                                # hints in the middle need room: shrink the picture rows to fit
        if not centre_hints:
            break
        need = TITLE_H + centre_hints * (0.15 + HINT_H_CENTRE) + 2 * HINT_CLEAR + 0.1
        out, now = _ring(n_items, n_hint, safe, (safe.h - need) / 2)
        if now == centre_hints:
            break
        centre_hints = now
    return out
