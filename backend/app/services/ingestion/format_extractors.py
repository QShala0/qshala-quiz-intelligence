"""Format-aware extraction: one question dict (and its format_payload) per question unit.

`slide_roles.analyze_deck` finds the units; this module decides each unit's format from the slide STRUCTURE (text
roles, group shapes, animations, pictures) and extracts the payload. The result is the ONE source of truth for
`question_format`: tagging/format_classifier reads it instead of classifying again.

Formats: LIST_IT, IMAGE_REVEAL, CONNECT, STEPPED_CLUE, ARRANGE_ORDER, MCQ, STANDARD_QA.
Every result carries a confidence (>= 0.9 accept, 0.7-0.9 optional vision check, < 0.7 needs review).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

LF = chr(10)

from backend.app.services.ingestion.pptx_structure import absolute_box, absolute_centre
from backend.app.services.ingestion.slide_roles import (
    DeckAnalysis, RoundInfo, Unit, SLOT_EXAMPLE, SECTION_MAIN, is_timer_shape, is_export_shape, parse_rules)

ACCEPT_AT = 0.9
VISION_AT = 0.7

FMT_STANDARD_QA = "STANDARD_QA"
FMT_LIST_IT = "LIST_IT"
FMT_IMAGE_REVEAL = "IMAGE_REVEAL"
FMT_CONNECT = "CONNECT"
FMT_STEPPED_CLUE = "STEPPED_CLUE"
FMT_ARRANGE_ORDER = "ARRANGE_ORDER"
FMT_MCQ = "MCQ"

DEFAULT_LADDER = [(20, 15), (15, 10), (10, 5)]
LIST_IT_OPTIONS, LIST_IT_TICKS, LIST_IT_CROSSES = 9, 6, 3

_BADGE_RE = re.compile(r"^(\d)\s*(?:\||\n)\s*\+?\s*(\d+)\s*/\s*[-–]?\s*(\d+)$")
_CLUE_LABEL_RE = re.compile(r"^(?:Q?\d+\.\s*)?Clue\s*(\d)\s*(?::\s*([+\-]?\d+)\s*/\s*([+\-]?\d+))?", re.I)
_NUMBER_BADGE_RE = re.compile(r"^\W*(\d{1,3})[^\w*]*(\*?)\W*$")
_PUNCT_BADGE_RE = re.compile(r"^\W*\d{1,3}[.*)]+\W*$")
_CONNECT_TITLE_RE = re.compile(r"^(?:\d+\.\s*)?connect\b", re.I)
_LARGER_IMAGE_RE = re.compile(r"^\W*\[?\s*larger image on next slide\s*\]?\W*$", re.I)
_TOPIC_LABEL_RE = re.compile(r"^([A-Z][A-Z &]*[A-Z])\s*-\s*(\d)$")
_ANSWER_LETTER_RE = re.compile(r"^\s*([A-D])\s*[.)]\s*(.*)$", re.S)
_OPTION_LETTER_RE = re.compile(r"^\s*\(?([A-D])[.)]\s+(.*)$")
_ARRANGE_WORDS_RE = re.compile(r"\b(arrange|rearrange|order|sequence|sort|rank)\b|\bfrom\s+\w+\s+to\s+\w+\b|\b\w+est\s+to\s+\w+est\b",
                               re.I)
_ARRANGE_ROUND_RE = re.compile(r"ticker\s*tangle|arrange", re.I)
_CONNECT_ROUND_RE = re.compile(r"\bconnect", re.I)
_STEPPED_ROUND_RE = re.compile(r"three\s*clues?|3\s*clues?|portfolio\s*builder|stepped", re.I)
_MCQ_ROUND_RE = re.compile(r"\bmcq\b|multiple\s*choice", re.I)

_ASC_WORDS = {"lowest", "earliest", "oldest", "first", "weakest", "slowest", "north", "smallest", "shortest", "least",
              "youngest", "cheapest", "fewest", "lightest", "nearest", "ascending", "west"}
_DESC_WORDS = {"highest", "latest", "newest", "last", "largest", "strongest", "fastest", "south", "longest", "most",
               "oldest_last", "biggest", "heaviest", "farthest", "descending", "greatest", "east"}

_UNIT_MULT = {"thousand": 1e3, "k": 1e3, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "million": 1e6, "mn": 1e6,
              "crore": 1e7, "crores": 1e7, "cr": 1e7, "billion": 1e9, "bn": 1e9, "trillion": 1e12, "tn": 1e12}

# ------------------------------------------------------------------------------------------------ small helpers


def _lines(text: str) -> List[str]:
    return [l.strip() for l in (text or "").replace("\r", "").split("\n") if l.strip()]


def _text_shapes(slide: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [s for s in slide.get("shapes") or []
            if s.get("type") == "TEXT_BOX" and (s.get("text") or "").strip() and not is_timer_shape(s)]


def _reading_order(shapes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Top-to-bottom rows, left-to-right inside a row (positions after applying group transforms)."""
    if not shapes:
        return []
    boxed = [(absolute_centre(s), absolute_box(s)[3], s) for s in shapes]
    boxed.sort(key=lambda b: b[0][1])
    rows: List[List[Tuple[Tuple[float, float], float, Dict[str, Any]]]] = []
    for b in boxed:
        if rows:
            row = rows[-1]
            avg_cy = sum(r[0][1] for r in row) / len(row)
            avg_h = sum(r[1] for r in row) / len(row)
            if abs(b[0][1] - avg_cy) <= max(avg_h * 0.5, 1.0):
                row.append(b)
                continue
        rows.append([b])
    out: List[Dict[str, Any]] = []
    for row in rows:
        out.extend(r[2] for r in sorted(row, key=lambda b: b[0][0]))
    return out


def _clue_media(slide: Dict[str, Any], noise: set, kind: str) -> List[Dict[str, Any]]:
    """Pictures (kind='image') or videos (kind='video') that belong to the question: servable, not deck furniture."""
    want = ("PICTURE",) if kind == "image" else ("MEDIA",)
    out = []
    for s in slide.get("shapes") or []:
        if s.get("type") not in want or is_timer_shape(s) or not s.get("media_url"):
            continue
        if kind == "image" and s.get("media_kind") not in (None, "image"):
            continue
        if kind == "video" and s.get("media_kind") != "video":
            continue
        if s.get("media_md5") in noise and not is_export_shape(s):
            continue
        out.append(s)
    return _reading_order(out)


def _urls(shapes: List[Dict[str, Any]]) -> List[str]:
    return list(dict.fromkeys(s["media_url"] for s in shapes if s.get("media_url")))


def topic_label(slide: Dict[str, Any]) -> Optional[str]:
    for c in [slide.get("title") or ""] + [s["text"] for s in _text_shapes(slide)]:
        for line in _lines(c):
            m = _TOPIC_LABEL_RE.match(re.sub(r"\s+", " ", line))
            if m:
                return f"{m.group(1).title()} {m.group(2)}"
    return None


def _answer_of(ans: Optional[Dict[str, Any]], extractor) -> Tuple[str, str]:
    """(answer, explanation) from the answer slide, using the text shapes in reading order (title first)."""
    if not ans:
        return "", ""
    lines: List[str] = []
    for s in _text_shapes(ans):
        t = s["text"].strip()
        if _NUMBER_BADGE_RE.match(t):
            continue
        lines.append(t)
    text = "\n".join(lines) if lines else (ans.get("extracted_text") or "")
    if not lines:
        text = ans.get("extracted_text") or ""
    return extractor._parse_answer_and_explanation(text)


# ------------------------------------------------------------------------------------------------ value parsing


def parse_value(text: str) -> Optional[float]:
    """Numeric value of an Arrange item's reveal text: years, %, lakh/crore/million/billion, ranges. None if unsure."""
    t = (text or "").replace("\u20b9", " ").replace("$", " ").replace("\u20ac", " ").replace("\u00a3", " ")
    t = t.replace(",", "").strip()
    if not t:
        return None
    nums = list(re.finditer(r"\d+(?:\.\d+)?", t))
    if not nums or len(nums) > 2:
        return None
    if len(nums) == 2:       # a range ("1990-1995", "20 to 30"): the midpoint, but only when nothing else is going on
        between = t[nums[0].end():nums[1].start()].strip().lower()
        if between not in ("-", "\u2013", "\u2014", "to", "and"):
            return None
        return (float(nums[0].group()) + float(nums[1].group())) / 2.0
    val = float(nums[0].group())
    tail = re.match(r"\s*(%|[A-Za-z]+)", t[nums[0].end():])
    unit = (tail.group(1) if tail else "").lower()
    if unit in _UNIT_MULT:
        val *= _UNIT_MULT[unit]
    elif unit and unit not in ("%", "ad", "bc", "ce", "bce", "st", "nd", "rd", "th", "km", "kms", "m", "kg", "cm", "yrs",
                               "years", "year", "mph", "kmph", "c", "f", "crs"):
        return None
    return val


def direction_from_title(title: str) -> Optional[str]:
    low = (title or "").lower()
    m = re.search(r"\b([a-z]+)\s+to\s+([a-z]+)\b", low)
    if m:
        a, b = m.group(1), m.group(2)
        if a in _ASC_WORDS:
            return "asc"
        if a in _DESC_WORDS:
            return "desc"
    words = re.findall(r"[a-z]+", low)
    asc = [w for w in words if w in _ASC_WORDS and w not in ("first", "last")]
    desc = [w for w in words if w in _DESC_WORDS and w not in ("first", "last")]
    if asc and not desc:
        return "asc"
    if desc and not asc:
        return "desc"
    return None


# ------------------------------------------------------------------------------------------------ result container


class Ctx:
    """Everything one unit's extractor needs."""

    def __init__(self, unit: Unit, by_no: Dict[int, Dict[str, Any]], analysis: DeckAnalysis, extractor):
        self.unit = unit
        self.slides = [by_no[n] for n in unit.content_slides]
        self.first = self.slides[0]
        self.ans = by_no.get(unit.answer_slide) if unit.answer_slide else None
        self.extras = [by_no[n] for n in unit.extra_answer_slides]
        self.noise = analysis.noise
        self.round: Optional[RoundInfo] = analysis.rounds[unit.round_key] if unit.round_key is not None else None
        self.round_name = unit.round_name or ""
        self.rules = self.round.rules if self.round else parse_rules("")
        self.extractor = extractor
        self.reasons: List[str] = []

    def all_text_shapes(self) -> List[Dict[str, Any]]:
        out = []
        for s in self.slides:
            out.extend(_text_shapes(s))
        return out

    def question_images(self) -> List[Dict[str, Any]]:
        out = []
        for s in self.slides:
            out.extend(_clue_media(s, self.noise, "image"))
        return out

    def question_videos(self) -> List[Dict[str, Any]]:
        out = []
        for s in self.slides:
            out.extend(_clue_media(s, self.noise, "video"))
        return out

    def answer_images(self) -> List[Dict[str, Any]]:
        out = []
        for s in ([self.ans] if self.ans else []) + self.extras:
            out.extend(_clue_media(s, self.noise, "image"))
        return out

    def answer_videos(self) -> List[Dict[str, Any]]:
        out = []
        for s in ([self.ans] if self.ans else []) + self.extras:
            out.extend(_clue_media(s, self.noise, "video"))
        return out

    def timer_seconds(self) -> Optional[int]:
        if self.rules.timer_seconds:
            return self.rules.timer_seconds
        for s in self.slides:
            if any(is_timer_shape(sh) for sh in s.get("shapes") or []):
                return 60
        return None

    def answer(self) -> Tuple[str, str]:
        return _answer_of(self.ans, self.extractor)


def _result(ctx: Ctx, fmt: str, payload: Dict[str, Any], confidence: float, question_text: str, answer: str,
            explanation: str = "", options: Optional[List[str]] = None, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    u = ctx.unit
    reasons = list(ctx.reasons)
    if u.needs_review and u.review_reason and u.review_reason not in reasons:
        reasons.append(u.review_reason)
        confidence = min(confidence, 0.6)
    needs_review = confidence < VISION_AT or u.needs_review
    route = "review" if needs_review else ("accept" if confidence >= ACCEPT_AT else "vision")
    payload.setdefault("type", fmt)
    payload["round_name"] = u.round_name
    payload["round_number"] = u.round_number
    payload["team"] = u.team
    payload["extraction"] = {"confidence": round(confidence, 3), "needs_review": needs_review, "route": route,
                             "reasons": reasons}
    qimgs, qvids = ctx.question_images(), ctx.question_videos()
    aimgs, avids = ctx.answer_images(), ctx.answer_videos()
    q_urls, a_urls = _urls(qimgs), _urls(aimgs)
    first_no, last_no = u.first_slide, u.last_slide
    raw_notes = "\n".join(s.get("speaker_notes") or "" for s in ctx.slides + ([ctx.ans] if ctx.ans else []) if s.get("speaker_notes")).strip()
    notes = ctx.extractor._strip_data_uris(raw_notes) if raw_notes and hasattr(ctx.extractor, "_strip_data_uris") else raw_notes
    d = {
        "slide_id": ctx.first.get("id"),
        "answer_slide_id": ctx.ans.get("id") if ctx.ans else None,
        "question_text": question_text,
        "answer": answer,
        "options": options,
        "explanation": explanation,
        "subtopic": u.round_name,
        "round_number": u.round_number,
        "question_type": fmt,
        "question_format": fmt,
        "format_payload": payload,
        "slide_number": first_no,
        "answer_slide_number": u.answer_slide,
        "source_slide_range": f"Slide {first_no}-{last_no}" if last_no != first_no else f"Slide {first_no}",
        "image_refs": list(dict.fromkeys(q_urls + a_urls)),
        "question_images": q_urls,
        "answer_images": a_urls,
        "audio_refs": [], "question_audio": [], "answer_audio": [],
        "video_refs": list(dict.fromkeys(_urls(qvids) + _urls(avids))),
        "question_videos": _urls(qvids),
        "answer_videos": _urls(avids),
        "media_items": [],
        "raw_media_refs": list(dict.fromkeys(q_urls + a_urls + _urls(qvids) + _urls(avids))),
        "speaker_notes": notes,
        "play_mode": u.play_mode,
        "slot": u.slot,
        "topic_label": u.topic_label or (topic_label(ctx.first) if re.search("topicator", u.round_name or "", re.I) else None),
        "_ingest": {"question_format": fmt, "confidence": round(confidence, 3), "needs_review": needs_review,
                    "route": route, "reasons": reasons, "play_mode": u.play_mode, "slot": u.slot,
                    "round_name": u.round_name, "round_number": u.round_number, "team": u.team,
                    "section": u.section, "unit": u.index},
    }
    if extra:
        d.update(extra)
    return d


# ------------------------------------------------------------------------------------------------ LIST IT


def extract_list_it(ctx: Ctx) -> Dict[str, Any]:
    slide = ctx.first
    shapes = slide.get("shapes") or []
    by_id = {s["id"]: s for s in shapes}
    pairs: Dict[int, int] = {}          # option id -> overlay id (click an option, its overlay appears)
    for a in slide.get("interactive_animations") or []:
        if a.get("cls") == "entr" and a.get("trigger_id") in by_id and a.get("target_id") in by_id:
            pairs[a["trigger_id"]] = a["target_id"]
    option_shapes = [by_id[t] for t in pairs if by_id[t].get("type") == "TEXT_BOX" and by_id[t].get("text", "").strip()]
    option_shapes = _reading_order(option_shapes)
    options = []
    for o in option_shapes:
        cls = by_id[pairs[o["id"]]].get("overlay_class")
        options.append({"text": " ".join(_lines(o["text"])), "correct": True if cls == "tick" else (False if cls == "cross" else None)})
    if not option_shapes:
        # No click animations (old upload or an unusual deck): fall back to nearest overlay by centre
        overlays = [s for s in shapes if s.get("overlay_class") in ("tick", "cross")]
        opts = _reading_order([s for s in _text_shapes(slide) if len(s["text"]) < 60][1:])
        for o in opts:
            if not overlays:
                break
            ox, oy = absolute_centre(o)
            best = min(overlays, key=lambda p: (absolute_centre(p)[0] - ox) ** 2 + (absolute_centre(p)[1] - oy) ** 2)
            options.append({"text": " ".join(_lines(o["text"])), "correct": best["overlay_class"] == "tick"})
        if options:
            ctx.reasons.append("no click animations: options matched to overlays by position")
    texts = [o["text"] for o in options]
    ticks = sum(1 for o in options if o["correct"] is True)
    crosses = sum(1 for o in options if o["correct"] is False)
    conf = 0.97
    if len(options) != LIST_IT_OPTIONS:
        ctx.reasons.append(f"{len(options)} options found, expected {LIST_IT_OPTIONS}")
        conf = 0.5
    if len(set(texts)) != len(texts):
        ctx.reasons.append("duplicate option text")
        conf = min(conf, 0.5)
    if ticks != LIST_IT_TICKS or crosses != LIST_IT_CROSSES:
        ctx.reasons.append(f"{ticks} ticks / {crosses} crosses, expected {LIST_IT_TICKS} / {LIST_IT_CROSSES}")
        conf = min(conf, 0.5)
    option_ids = {o["id"] for o in option_shapes}
    prompt_parts = [s["text"] for s in _text_shapes(slide) if s["id"] not in option_ids and not re.match(r"^\W*team\s*\d+\W*$", s["text"], re.I)
                    and not _NUMBER_BADGE_RE.match(s["text"].strip())]
    prompt = " ".join(" ".join(_lines(p)) for p in prompt_parts).strip()
    correct = [o["text"] for o in options if o["correct"]]
    per_correct = None
    m = re.search(r"\+\s*(\d+)\s*(?:points?\s*)?(?:for|per)\s+each", ctx.rules.scoring_text or "", re.I)
    if m:
        per_correct = int(m.group(1))
    elif ctx.rules.ladder:
        per_correct = ctx.rules.ladder[0][0]
    payload = {"type": FMT_LIST_IT, "prompt": prompt, "timer_seconds": ctx.timer_seconds(), "max_picks": len(correct) or LIST_IT_TICKS,
               "scoring": {"per_correct": per_correct}, "options": options}
    q_text = (prompt + " " + ", ".join(correct)).strip()
    return _result(ctx, FMT_LIST_IT, payload, conf, q_text, ", ".join(correct))


# ------------------------------------------------------------------------------------------------ IMAGE REVEAL


def _reveal_groups(slide: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Top-level groups holding a badge ("1" + "+20/-15"): [{id, group, number, correct, wrong, cover}]."""
    shapes = slide.get("shapes") or []
    out = []
    for g in shapes:
        if g.get("type") != "GROUP" or g.get("path"):
            continue
        kids = [s for s in shapes if g["id"] in (s.get("path_ids") or [])]
        badge = None
        for k in kids:
            m = _BADGE_RE.match((k.get("text") or "").strip())
            if m:
                badge = (int(m.group(1)), int(m.group(2)), -int(m.group(3)))
                break
        if badge:
            cover = next((k for k in kids if k.get("type") == "PICTURE" and k.get("media_url")), None)
            out.append({"id": g["id"], "group": g, "number": badge[0], "correct": badge[1], "wrong": badge[2],
                        "cover": cover["media_url"] if cover else None})
    return out


def is_image_reveal(slide: Dict[str, Any]) -> bool:
    return len(_reveal_groups(slide)) >= 3


def extract_image_reveal(ctx: Ctx) -> Dict[str, Any]:
    slide = next(s for s in ctx.slides if is_image_reveal(s))
    groups = _reveal_groups(slide)
    by_gid = {g["id"]: g for g in groups}
    pics = [s for s in slide.get("shapes") or [] if s.get("type") == "PICTURE" and not s.get("path") and s.get("media_url")
            and not is_timer_shape(s)]
    full = max(pics, key=lambda s: absolute_box(s)[2] * absolute_box(s)[3]) if pics else None
    if full is not None:
        fx, fy, fw, fh = absolute_box(full)
    else:
        boxes = [absolute_box(g["group"]) for g in groups]
        if boxes:
            fx = min(b[0] for b in boxes)
            fy = min(b[1] for b in boxes)
            fw = max(b[0] + b[2] for b in boxes) - fx
            fh = max(b[1] + b[3] for b in boxes) - fy
        else:
            fx = fy = fw = fh = 0.0
    # reveal order = the exit effects of the MAIN sequence (interactive-sequence effects are ignored)
    exits = [a for a in slide.get("animations") or [] if a.get("cls") == "exit" and a.get("preset") == 10
             and a.get("target_id") in by_gid]
    conf = 0.97
    if exits:
        ordered = [by_gid[a["target_id"]] for a in sorted(exits, key=lambda a: a["order"])]
        if len(ordered) != len(groups):
            ctx.reasons.append(f"{len(ordered)} exit effects for {len(groups)} covers")
            conf = 0.6
    else:
        ordered = sorted(groups, key=lambda g: g["number"])
        ctx.reasons.append("no animation data in this upload (re-upload the deck); reveal order taken from the badge numbers")
        conf = 0.8
    if [g["number"] for g in ordered] != list(range(1, len(ordered) + 1)):
        ctx.reasons.append("animation order does not match the badge numbers 1..4")
        conf = min(conf, 0.6)
    steps = []
    for k, g in enumerate(ordered, start=1):
        gx, gy, gw, gh = absolute_box(g["group"])
        cx, cy = gx + gw / 2.0, gy + gh / 2.0
        pos = ("top" if cy < fy + fh / 2.0 else "bottom") + "-" + ("left" if cx < fx + fw / 2.0 else "right")
        steps.append({"step": k, "cover": g["cover"], "position": pos, "correct": g["correct"], "wrong": g["wrong"]})
    if len({s["position"] for s in steps}) != len(steps):
        ctx.reasons.append("two covers share a position")
        conf = min(conf, 0.6)
    prompt_lines = []
    gids = {g["id"] for g in groups}
    for s in _text_shapes(slide):
        if gids & set(s.get("path_ids") or []) or _NUMBER_BADGE_RE.match(s["text"].strip()):
            continue
        prompt_lines.append(" ".join(_lines(s["text"])))
    prompt = " ".join(prompt_lines).strip()
    answer, expl = ctx.answer()
    if not answer:
        ctx.reasons.append("no answer found")
        conf = min(conf, 0.5)
    payload = {"type": FMT_IMAGE_REVEAL, "prompt": prompt, "full_image": full.get("media_url") if full else None,
               "timer_seconds": ctx.timer_seconds(), "reveal_steps": steps, "answer": answer}
    if full is None or not full.get("media_url"):
        ctx.reasons.append("no full image found")
        conf = min(conf, 0.6)
    return _result(ctx, FMT_IMAGE_REVEAL, payload, conf, prompt, answer, expl)


# ------------------------------------------------------------------------------------------------ CONNECT


def is_connect(ctx: Ctx) -> bool:
    for s in ctx.slides:
        for sh in _text_shapes(s):
            if any(_CONNECT_TITLE_RE.match(l) for l in _lines(sh["text"])[:1]):
                return True
    if ctx.unit.section == "AUDIENCE" and len(ctx.question_images()) < 3:
        return False
    return bool(_CONNECT_ROUND_RE.search(ctx.round_name))


def extract_connect(ctx: Ctx) -> Dict[str, Any]:
    pics = ctx.question_images()
    vids = ctx.question_videos()
    items = [{"order": i, "image": p["media_url"]} for i, p in enumerate(pics, start=1)]
    hint_parts = []
    for s in ctx.all_text_shapes():
        t = " ".join(_lines(s["text"]))
        if not t or _CONNECT_TITLE_RE.match(t) and len(t.split()) <= 2 or _NUMBER_BADGE_RE.match(t) or _LARGER_IMAGE_RE.match(t):
            continue
        if len(t.split()) < 2:
            continue                      # "MIX" style tags
        hint_parts.append(re.sub(r"^\d+\.\s*connect\s*", "", t, flags=re.I).strip() or t)
    hint = " / ".join(h for h in hint_parts if h) or None
    raw_notes_1 = ctx.first.get("speaker_notes") or ""
    if raw_notes_1 and hasattr(ctx.extractor, "_strip_data_uris"):
        raw_notes_1 = ctx.extractor._strip_data_uris(raw_notes_1)
    notes = [l for l in _lines(raw_notes_1) if not re.match(r"^https?://", l)]
    labels = notes if notes else []
    conf = 0.95
    n = len(items)
    if n > 9:
        ctx.reasons.append(f"{n} pictures found, more than 9")
        conf = 0.7
    elif n < 4:
        if vids:
            conf = 0.9
            ctx.reasons.append(f"{n} pictures plus a clue video")
        else:
            ctx.reasons.append(f"only {n} pictures and no video")
            conf = 0.5
    answer, expl = ctx.answer()
    if not answer:
        ctx.reasons.append("no answer found")
        conf = min(conf, 0.5)
    scoring = {"correct": None, "wrong": None}
    m = re.search(r"\+\s*(\d+)", ctx.rules.scoring_text or "")
    w = re.search(r"[-–]\s*(\d+)", ctx.rules.scoring_text or "")
    if m:
        scoring["correct"] = int(m.group(1))
    if w:
        scoring["wrong"] = -int(w.group(1))
    payload = {"type": FMT_CONNECT, "items": items, "text_hint": hint, "item_labels": labels, "scoring": scoring, "answer": answer}
    q_text = " ".join(x for x in [hint or "", ", ".join(labels)] if x).strip() or "What connects these?"
    return _result(ctx, FMT_CONNECT, payload, conf, q_text, answer, expl)


# ------------------------------------------------------------------------------------------------ STEPPED CLUE


def _clue_label(slide: Dict[str, Any]) -> Optional[Tuple[Dict[str, Any], re.Match]]:
    for s in _text_shapes(slide):
        for line in _lines(s["text"]):
            m = _CLUE_LABEL_RE.match(line)
            if m and len(line) <= 40:
                return s, m
    return None


def is_stepped_clue(ctx: Ctx) -> bool:
    labelled = sum(1 for s in ctx.slides if _clue_label(s))
    if len(ctx.slides) >= 2 and labelled >= 2:
        return True
    # a Three Clues round whose clue slides lost their labels is still one slide per clue
    return len(ctx.slides) >= 2 and bool(_STEPPED_ROUND_RE.search(ctx.round_name))


def extract_stepped_clue(ctx: Ctx) -> Dict[str, Any]:
    ladder = ctx.rules.ladder[:len(ctx.slides)] if len(ctx.rules.ladder) >= 3 else DEFAULT_LADDER
    clues: List[Dict[str, Any]] = []
    ok = True
    missing = False
    label_nos = []
    for i, s in enumerate(ctx.slides):
        found = _clue_label(s)
        label_shape = found[0] if found else None
        pts = None
        if found:
            m = found[1]
            label_nos.append(int(m.group(1)))
            if m.group(2) is not None:
                pts = (abs(int(m.group(2))), abs(int(m.group(3))))
        text_parts = []
        for sh in _text_shapes(s):
            if label_shape is not None and sh["id"] == label_shape["id"]:
                continue
            if _NUMBER_BADGE_RE.match(sh["text"].strip()):
                continue
            text_parts.append(" ".join(_lines(sh["text"])))
        expected = ladder[i] if i < len(ladder) else None
        if pts is None:
            missing = True
            use = expected
        else:
            use = pts
            if expected is not None and pts != expected:
                ok = False
        imgs = _urls(_clue_media(s, ctx.noise, "image"))
        clues.append({"step": i + 1, "text": " ".join(text_parts).strip(),
                      "correct": use[0] if use else None, "wrong": -use[1] if use else None, "images": imgs})
    if label_nos and label_nos != list(range(1, len(label_nos) + 1)):
        ok = False
    answer, expl = ctx.answer()
    conf = 0.95
    if not ok:
        ctx.reasons.append("clue points or numbering differ from the round's ladder (typo in the deck?)")
        conf = 0.92
    if missing:
        ctx.reasons.append("some clue labels carry no points; the round's ladder was used")
    if not answer:
        ctx.reasons.append("no answer found")
        conf = 0.5
    if not label_nos:
        ctx.reasons.append("no clue labels on the slides; clues taken from the slide order")
        conf = min(conf, 0.85)
    if not all(c["text"] for c in clues):
        ctx.reasons.append("a clue slide has no text")
        conf = min(conf, 0.85)
    payload = {"type": FMT_STEPPED_CLUE, "clues": clues, "scoring_ladder_ok": ok, "answer": answer}
    if missing:
        payload["points_missing"] = True
    q_text = " ".join(f"Clue {c['step']}: {c['text']}" for c in clues)
    return _result(ctx, FMT_STEPPED_CLUE, payload, conf, q_text, answer, expl)


# ------------------------------------------------------------------------------------------------ ARRANGE ORDER


def _arrange_items(slide: Dict[str, Any]) -> Tuple[Dict[int, Dict[str, Any]], int]:
    """Items bound by GROUP membership: {label: {text, value_text, image}}, plus how many label shapes exist."""
    shapes = slide.get("shapes") or []
    groups: Dict[int, List[Dict[str, Any]]] = {}
    for s in shapes:
        pid = (s.get("path_ids") or [None])[0]
        if pid is not None and s.get("type") != "GROUP":
            groups.setdefault(pid, []).append(s)
    items: Dict[int, Dict[str, Any]] = {}
    for pid, members in groups.items():
        texts = [m for m in members if m.get("type") == "TEXT_BOX" and (m.get("text") or "").strip() and not is_timer_shape(m)]
        label_shapes = [t for t in texts if re.fullmatch(r"[1-4]", t["text"].strip())]
        if len(label_shapes) != 1:
            continue
        label = int(label_shapes[0]["text"].strip())
        others = _reading_order([t for t in texts if t is not label_shapes[0]])
        if not others:
            continue
        first_lines = _lines(others[0]["text"])
        name = first_lines[0]
        value_parts = first_lines[1:]
        for extra in others[1:]:
            value_parts.extend(_lines(extra["text"]))
        pic = next((m for m in members if m.get("type") == "PICTURE" and m.get("media_url") and not is_timer_shape(m)), None)
        items[label] = {"text": name, "value": " ".join(value_parts).strip() or None, "image": pic["media_url"] if pic else None}
    n_labels = sum(1 for s in _text_shapes(slide) if re.fullmatch(r"[1-4]", s["text"].strip()))
    return items, n_labels


def is_arrange(ctx: Ctx) -> bool:
    first_text = " ".join(s["text"] for s in ctx.all_text_shapes())
    labels = sum(1 for s in ctx.all_text_shapes() if re.fullmatch(r"[1-4]", s["text"].strip()))
    if _ARRANGE_ROUND_RE.search(ctx.round_name):
        return True
    return labels >= 4 and bool(_ARRANGE_WORDS_RE.search(first_text))


def extract_arrange(ctx: Ctx) -> Dict[str, Any]:
    slide = next((s for s in ctx.slides if _arrange_items(s)[1] >= 3), ctx.first)
    items_q, n_labels = _arrange_items(slide)
    items_a, _ = _arrange_items(ctx.ans) if ctx.ans else ({}, 0)
    conf = 0.95
    if sorted(items_q) != [1, 2, 3, 4]:
        ctx.reasons.append(f"{len(items_q)} complete label/name groups found (of {n_labels} labels); items must be grouped")
        conf = 0.4
    title_parts = []
    for s in _text_shapes(slide):
        if s.get("path_ids"):
            continue
        t = " ".join(_lines(s["text"]))
        if _NUMBER_BADGE_RE.match(t) or re.fullmatch(r"[1-4]", t):
            continue
        title_parts.append(t)
    title = " ".join(title_parts).strip()
    direction = direction_from_title(title)
    items = []
    for label in sorted(items_q):
        q = items_q[label]
        a = items_a.get(label, {})
        value = a.get("value") or q.get("value")
        items.append({"label": label, "text": q["text"], "value": value, "value_num": parse_value(value) if value else None,
                      "image": q.get("image") or a.get("image")})
    nums = [it["value_num"] for it in items]
    correct_order: List[int] = []
    order_source = "none"
    if len(items) == 4 and all(n is not None for n in nums) and len(set(nums)) == 4 and direction:
        correct_order = [it["label"] for it in sorted(items, key=lambda it: it["value_num"], reverse=(direction == "desc"))]
        order_source = "computed"
    elif len(items) == 4:
        why = []
        if any(n is None for n in nums):
            why.append("a reveal value could not be read as a number")
        elif len(set(nums)) != 4:
            why.append("two reveal values are equal")
        if not direction:
            why.append("sort direction not stated in the title")
        ctx.reasons.append("correct order not stated and not computable: " + "; ".join(why) + " (enter it by hand)")
        conf = min(conf, 0.65)
    scoring = {"all_correct": 20, "two_correct": 10, "one_correct": 5}
    names_by_label = {it["label"]: it["text"] for it in items}
    ordered = [names_by_label[l] for l in (correct_order or sorted(names_by_label))]
    answer = " → ".join(ordered) if correct_order else "Correct order not recorded"
    payload = {"type": FMT_ARRANGE_ORDER, "prompt": title, "direction": direction, "timer_seconds": ctx.timer_seconds(),
               "scoring": scoring, "items": items, "correct_order": correct_order, "order_source": order_source}
    q_text = (title + " " + ", ".join(ordered)).strip()
    return _result(ctx, FMT_ARRANGE_ORDER, payload, conf, q_text, answer, extra={"order_source": order_source})


# ------------------------------------------------------------------------------------------------ MCQ


def _mcq_split(ctx: Ctx) -> Tuple[str, List[str], bool]:
    """(stem, option texts, letters_were_printed) from the question slides' text, or ('', [], False)."""
    shapes = []
    for s in ctx.slides:
        shapes.extend(_text_shapes(s))
    texts = []
    for sh in shapes:
        t = sh["text"].strip()
        if _PUNCT_BADGE_RE.match(t) or _LARGER_IMAGE_RE.match(t):          # '3.', '14.*'; a bare '100' may be an option
            continue
        texts.append(t)
    # options in their own shape: all lines short, 3-6 lines, not the first (stem) shape
    for t in texts[1:]:
        ls = _lines(t)
        if 3 <= len(ls) <= 6 and all(len(l) <= 80 and not l.endswith("?") for l in ls):
            stem = " ".join(" ".join(_lines(x)) for x in texts if x is not t)
            return stem.strip(), ls, False
    # options as separate one-line shapes after the stem shape
    if len(texts) >= 3 and len(_lines(texts[0])) >= 1:
        tail = texts[1:]
        if 2 <= len(tail) <= 6 and all(len(_lines(x)) == 1 and len(x) <= 80 and not x.endswith('?') for x in tail):
            return _lines(texts[0])[0] if len(_lines(texts[0])) == 1 else ' '.join(_lines(texts[0])), [x.strip() for x in tail], False
    flat = [l for t in texts for l in _lines(t)]
    lettered = [i for i, l in enumerate(flat) if _OPTION_LETTER_RE.match(l)]
    if len(lettered) >= 2:
        opts = [_OPTION_LETTER_RE.match(flat[i]).group(2).strip() for i in lettered]
        stem = " ".join(flat[:lettered[0]])
        return stem.strip(), opts, True
    qi = max((i for i, l in enumerate(flat) if l.endswith("?") or l.endswith(":")), default=None)
    if qi is not None and 2 <= len(flat) - qi - 1 <= 6:
        return " ".join(flat[:qi + 1]), flat[qi + 1:], False
    return "", [], False


def is_mcq(ctx: Ctx) -> bool:
    if not ctx.ans:
        return False
    first = " ".join(_lines(" ".join(s["text"] for s in _text_shapes(ctx.ans))))[:200]
    if _ANSWER_LETTER_RE.match(first):
        stem, opts, _ = _mcq_split(ctx)
        return len(opts) >= 2
    return bool(_MCQ_ROUND_RE.search(ctx.round_name)) and bool(_mcq_split(ctx)[1])


def extract_mcq(ctx: Ctx) -> Dict[str, Any]:
    stem, opts, printed = _mcq_split(ctx)
    ans_text = " ".join(" ".join(_lines(s["text"])) for s in _text_shapes(ctx.ans)) if ctx.ans else ""
    m = _ANSWER_LETTER_RE.match(ans_text)
    letter = m.group(1).upper() if m else None
    ans_body = (m.group(2) if m else ans_text).strip()
    options = [{"letter": chr(65 + i), "text": o} for i, o in enumerate(opts)]
    conf = 0.95
    norm = lambda x: re.sub(r"\W+", "", x.lower())
    if letter:
        idx = ord(letter) - 65
        if idx >= len(options):
            ctx.reasons.append(f"answer letter {letter} but only {len(options)} options")
            conf = 0.5
        elif ans_body and norm(ans_body) != norm(options[idx]["text"]) and norm(ans_body) not in norm(options[idx]["text"]) \
                and norm(options[idx]["text"]) not in norm(ans_body):
            ctx.reasons.append("answer text does not match the option for that letter")
            conf = 0.6
    else:
        match = [o["letter"] for o in options if norm(o["text"]) == norm(ans_body) and ans_body]
        if len(match) == 1:
            letter = match[0]
            conf = 0.85
            ctx.reasons.append("correct letter found by matching the answer text")
        else:
            ctx.reasons.append("could not tell which option is correct")
            conf = 0.5
    star = False
    for s in ctx.slides:
        for sh in _text_shapes(s):
            mm = _NUMBER_BADGE_RE.match(sh["text"].strip())
            if mm and mm.group(2) == "*":
                star = True
        if (s.get("metadata_json") or {}).get("has_star_shape"):
            star = True
    if len(options) < 2:
        ctx.reasons.append("fewer than 2 options")
        conf = 0.4
    payload = {"type": FMT_MCQ, "prompt": stem, "options": options, "correct_letter": letter, "letters_inferred": not printed,
               "star": star}
    answer = ans_text.strip() or (f"{letter}. {options[ord(letter) - 65]['text']}" if letter and ord(letter) - 65 < len(options) else "")
    opt_list = [f"{o['letter']}) {o['text']}" for o in options]
    return _result(ctx, FMT_MCQ, payload, conf, stem, answer, options=opt_list or None)


# ------------------------------------------------------------------------------------------------ STANDARD QA


def extract_standard(ctx: Ctx) -> Dict[str, Any]:
    parts = []
    star = False
    for s in ctx.slides:
        for sh in _text_shapes(s):
            t = sh["text"].strip()
            nb = _NUMBER_BADGE_RE.match(t)
            if nb:
                star = star or nb.group(2) == "*"
                continue
            if _TOPIC_LABEL_RE.match(re.sub(r"\s+", " ", t)) or _LARGER_IMAGE_RE.match(t):
                continue
            parts.append(" ".join(_lines(t)))
    q_text = " ".join(parts).strip()
    if q_text:
        cleaned = ctx.extractor._parse_question_and_options(q_text)[0]
        q_text = cleaned.strip() or q_text
    answer, expl = ctx.answer()
    vids, imgs = ctx.question_videos(), ctx.question_images()
    media_kind = "VIDEO" if vids else ("IMAGE" if imgs else None)
    conf = 0.9
    if not q_text:
        if imgs or vids:
            q_text = "(see the picture)" if imgs else "(see the clip)"
            conf = 0.8
            ctx.reasons.append("question is carried by media only")
        else:
            ctx.reasons.append("no question text found")
            conf = 0.4
    if not answer:
        ctx.reasons.append("no answer found")
        conf = min(conf, 0.4)
    if not ctx.unit.needs_review and conf == 0.9:
        conf = 0.93
    payload = {"type": FMT_STANDARD_QA, "media_kind": media_kind}
    if star:
        payload["star"] = True
    return _result(ctx, FMT_STANDARD_QA, payload, conf, q_text, answer, expl, extra={"media_kind": media_kind})


# ------------------------------------------------------------------------------------------------ driver


def restore_from_export(ctx: Ctx) -> Optional[Dict[str, Any]]:
    """A deck exported by QShala (export/format_slides.py) carries the question's payload in the speaker notes of its first
    slide. Restore exactly that, with the picture URLs of THIS upload: the {"$img": n} tokens index the distinct pictures
    the question's slides show, in order. None when the unit is not such an export, or its pictures do not line up (then
    the structural readers below take over)."""
    from backend.app.services.export.format_slides import parse_blob, detokenize_payload
    start = next((i for i, s in enumerate(ctx.slides) if parse_blob(s.get("speaker_notes"))), None)
    if start is None:
        return None
    slides = ctx.slides[start:]
    blob = parse_blob(slides[0].get("speaker_notes"))
    urls: List[str] = []
    for s in slides + ([ctx.ans] if ctx.ans else []):
        for sh in s.get("shapes") or []:
            if sh.get("type") == "PICTURE" and sh.get("media_url") and not is_timer_shape(sh):
                if sh["media_url"] not in urls:
                    urls.append(sh["media_url"])
    try:
        payload = detokenize_payload(blob["payload"], urls)
    except IndexError:
        return None
    ctx.slides = slides
    ctx.first = slides[0]
    meta = blob.get("meta") or {}
    saved = {k: payload.get(k) for k in ("round_name", "round_number", "team")}
    ctx.reasons.append("restored from a QShala export")
    d = _result(ctx, blob["format"], payload, 1.0, blob.get("question_text") or "", blob.get("answer") or "",
                blob.get("explanation") or "", options=blob.get("options"))
    d["format_payload"].update(saved)                    # the deck being re-read has no rounds of its own
    d["format_payload"]["extraction"]["source"] = "qshala_export"
    for k in ("play_mode", "slot", "topic_label"):
        if meta.get(k):
            d[k] = meta[k]
    return d


def detect_format(ctx: Ctx) -> str:
    if ctx.unit.self_contained:
        return FMT_LIST_IT
    if any(is_image_reveal(s) for s in ctx.slides):
        return FMT_IMAGE_REVEAL
    if is_stepped_clue(ctx):
        return FMT_STEPPED_CLUE
    if is_arrange(ctx):
        return FMT_ARRANGE_ORDER
    if is_connect(ctx):
        return FMT_CONNECT
    if is_mcq(ctx):
        return FMT_MCQ
    return FMT_STANDARD_QA


_EXTRACTORS = {
    FMT_LIST_IT: extract_list_it, FMT_IMAGE_REVEAL: extract_image_reveal, FMT_CONNECT: extract_connect,
    FMT_STEPPED_CLUE: extract_stepped_clue, FMT_ARRANGE_ORDER: extract_arrange, FMT_MCQ: extract_mcq,
    FMT_STANDARD_QA: extract_standard,
}


def extract_unit(unit: Unit, by_no: Dict[int, Dict[str, Any]], analysis: DeckAnalysis, extractor) -> Dict[str, Any]:
    """The question dict for one unit. Never raises: an extractor crash becomes a low-confidence STANDARD_QA."""
    ctx = Ctx(unit, by_no, analysis, extractor)
    try:
        restored = restore_from_export(ctx)
        if restored is not None:
            return restored
    except Exception as e:
        logger.warning('export restore skipped: %s', type(e).__name__)
    fmt = detect_format(ctx)
    try:
        return _EXTRACTORS[fmt](ctx)
    except Exception as e:                        # a malformed slide must not take the deck down
        ctx.reasons.append(f"{fmt} extraction failed ({type(e).__name__}); kept as a plain question")
        ctx.unit.needs_review = True
        try:
            return extract_standard(ctx)
        except Exception:
            return _result(ctx, FMT_STANDARD_QA, {"type": FMT_STANDARD_QA}, 0.2, "", "")


def with_shapes(slide: Dict[str, Any]) -> Dict[str, Any]:
    """Slides that carry no shape list (older uploads, PDFs, hand-built dicts) get one made from their text and
    picture paths, so the extractors have a single code path. Real shape data is never replaced."""
    if slide.get("shapes"):
        return slide
    shapes: List[Dict[str, Any]] = []
    sid = 1
    for chunk in (slide.get("extracted_text") or "").split(LF * 2):
        chunk = chunk.strip()
        if chunk:
            shapes.append({"id": sid, "name": "Text", "type": "TEXT_BOX", "text": chunk, "x": 0, "y": sid * 100, "w": 100, "h": 50,
                           "groups": [], "path": [], "path_ids": [], "media_md5": None, "media_kind": None,
                           "overlay_class": None, "zip_name": None, "media_url": None})
            sid += 1
    for path in slide.get("image_paths") or []:
        shapes.append({"id": sid, "name": "Picture", "type": "PICTURE", "text": "", "x": 0, "y": sid * 100, "w": 100, "h": 100,
                       "groups": [], "path": [], "path_ids": [], "media_md5": None, "media_kind": "image",
                       "overlay_class": None, "zip_name": None, "media_url": path})
        sid += 1
    for path in slide.get("video_paths") or []:
        if "timer" in path.lower():
            continue
        shapes.append({"id": sid, "name": "Video", "type": "MEDIA", "text": "", "x": 0, "y": sid * 100, "w": 100, "h": 100,
                       "groups": [], "path": [], "path_ids": [], "media_md5": None, "media_kind": "video",
                       "overlay_class": None, "zip_name": None, "media_url": path})
        sid += 1
    out = dict(slide)
    out["shapes"] = shapes
    return out


def extract_deck(slides: List[Dict[str, Any]], analysis: DeckAnalysis, extractor) -> List[Dict[str, Any]]:
    by_no = {int(s["slide_number"]): with_shapes(s) for s in slides if s.get("slide_number") is not None}
    return [extract_unit(u, by_no, analysis, extractor) for u in analysis.units]
