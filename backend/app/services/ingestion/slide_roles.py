"""Slide roles, question units and the section state machine for tournament decks.

Layout names differ from deck to deck, so roles come from TEXT and shape structure, never from layout names.
Question numbers printed on slides restart or skip, so units are NEVER grouped by number:

    unit = the content slides up to an ANSWER marker slide ("ANSWER" / "Answer!")  +  the one answer slide after it

List It has no ANSWER slide (the question slide reveals its own answers), so a slide carrying the List-It
signature (>= 6 entrance-animated overlay pictures) is a complete unit by itself.

Everything here is pure Python on slide dicts (as produced by the parser): no database, no network.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

# ----------------------------------------------------------------------------------------------- vocab

ROUND_HEADER = "ROUND_HEADER"
RULES = "RULES"
DIVIDER = "DIVIDER"
ANSWER_MARKER = "ANSWER_MARKER"
DECOR = "DECOR"
CONTENT = "CONTENT"
# section markers are roles of their own so the state machine can see them
SECTION_EXAMPLE = "SECTION_EXAMPLE"
SECTION_AUDIENCE = "SECTION_AUDIENCE"
SECTION_TIEBREAK = "SECTION_TIEBREAK"
SECTION_BUFFER = "SECTION_BUFFER"

SECTION_MAIN, SECTION_EXAMPLE_NAME, SECTION_AUDIENCE_NAME, SECTION_TIEBREAK_NAME, SECTION_BUFFER_NAME = (
    "MAIN", "EXAMPLE", "AUDIENCE", "TIEBREAK", "BUFFER")

SLOT_LIVE, SLOT_RESERVE, SLOT_EXAMPLE = "LIVE", "RESERVE", "EXAMPLE"

_ANSWER_RE = re.compile(r"^\W*(?:the\s+)?(?:answers?|ans)\W*$", re.I)
_DIVIDER_RE = re.compile(r"^\W*(question|team|q)\s*\.?\s*(\d+)\W*$", re.I)
_EXAMPLE_RE = re.compile(r"^\W*(?:examples?|sample(?:\s+question)?)\W*$", re.I)
_AUDIENCE_RE = re.compile(r"^\W*audience\s+questions?\W*(?:\d+\W*)?$", re.I)
_TIEBREAK_RE = re.compile(r"^\W*tie[\s\-]*breaker?s?\W*(?:\d+\W*)?$", re.I)
_BUFFER_RE = re.compile(r"^\W*buffer(?:\s+questions?)?\W*$", re.I)
_DECOR_TEXT_RE = re.compile(r"^\W*(scores?|scoreboard|leaderboard|thank\s*you|the\s+finalists|break|intermission)\W*$", re.I)
_ROUND_LINE_RE = re.compile(r"^\W*round\s*(\d+)\W*(?:[-–—:]+\s*(.+))?$", re.I)
_ROUND_ANYWHERE_RE = re.compile(r"\bround\s*(\d+)\b", re.I)
_TOPIC_BOARD_RE = re.compile(r"^\W*topics?\b", re.I)

_LADDER_PAIR_RE = re.compile(r"([+\-–]?\s*\d+)\s*/\s*([+\-–]?\s*\d+)")
_CLUE_LABEL_RE = re.compile(r"(?:Q\d+\.\s*)?Clue\s*\d", re.I)
_BADGE_RE = re.compile(r"^\d\s*\|\s*\+\d+/-?\d+$")
_RULE_WORDS_RE = re.compile(
    r"\b(\d+\s*questions?|\d+\s*topics?|on\s+the\s+buzzer|on\s+buzzer|directed|each\s+team|rules?|"
    r"correct\s*answer|for\s+a\s+pass|for\s+a\s+correct|for\s+direct|\d+\s*correct\b)", re.I)

# Minimum entrance-animated overlay pictures that make a slide a List-It slide.
LISTIT_MIN_OVERLAYS = 6
# A picture reused on this many slides is deck furniture (divider art, timer, music), not clue media.
NOISE_MIN_SLIDES = 3


def _norm(text: Optional[str]) -> str:
    return re.sub(r"[ \t]+", " ", (text or "").replace("\r", "")).strip()


def _lines(text: str) -> List[str]:
    return [l.strip() for l in text.split("\n") if l.strip()]


def is_timer_shape(shape: Dict[str, Any]) -> bool:
    return (shape.get("name") or "").strip().lower().startswith("timer")


# Pictures on slides exported by QShala (export/format_slides.py) are named "QSHALA ...". A progressive Image Reveal shows
# the same picture on several consecutive slides; that is one question's picture, not timer / divider art.
EXPORT_SHAPE_PREFIX = "QSHALA "


def is_export_shape(shape: Dict[str, Any]) -> bool:
    return (shape.get("name") or "").startswith(EXPORT_SHAPE_PREFIX)


def noise_media(slides: List[Dict[str, Any]]) -> set:
    """Media identities used on 3+ slides: timer video, decorative GIF slides, round music, divider art."""
    seen: Dict[str, set] = {}
    for s in slides:
        for sh in s.get("shapes") or []:
            key = sh.get("media_md5")
            if key and not is_export_shape(sh):
                seen.setdefault(key, set()).add(s.get("slide_number"))
    return {k for k, v in seen.items() if len(v) >= NOISE_MIN_SLIDES}


def listit_overlays(slide: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Overlay pictures of a List-It slide: pictures given an entrance effect, normally played when their option is
    clicked (interactive sequence), occasionally in the main sequence."""
    effects = list(slide.get("interactive_animations") or []) + list(slide.get("animations") or [])
    entering = {a.get("target_id") for a in effects if a.get("cls") == "entr"}
    return [sh for sh in slide.get("shapes") or [] if sh.get("id") in entering and sh.get("type") == "PICTURE"]


def is_listit_slide(slide: Dict[str, Any]) -> bool:
    ov = listit_overlays(slide)
    classed = [o for o in ov if o.get("overlay_class") in ("tick", "cross")]
    return len(ov) >= LISTIT_MIN_OVERLAYS or len(classed) >= LISTIT_MIN_OVERLAYS


# ----------------------------------------------------------------------------------------------- rules

@dataclass
class Rules:
    n_questions: Optional[int] = None
    ladder: List[Tuple[int, int]] = field(default_factory=list)      # [(+correct, -wrong), ...]
    timer_seconds: Optional[int] = None
    play_mode: Optional[str] = None
    scoring_text: str = ""

    def merge(self, other: "Rules") -> None:
        if other.n_questions is not None and self.n_questions is None:
            self.n_questions = other.n_questions
        if other.ladder and not self.ladder:
            self.ladder = other.ladder
        if other.timer_seconds is not None and self.timer_seconds is None:
            self.timer_seconds = other.timer_seconds
        if other.play_mode and not self.play_mode:
            self.play_mode = other.play_mode
        if other.scoring_text and other.scoring_text not in self.scoring_text:
            self.scoring_text = (self.scoring_text + "\n" + other.scoring_text).strip()


def _int(s: str) -> int:
    return int(s.replace("–", "-").replace(" ", ""))


def parse_rules(text: str) -> Rules:
    """n_questions, points ladder, timer and play mode from a rules slide's text."""
    r = Rules(scoring_text=_norm(text))
    t = text or ""
    m = re.search(r"(\d+)\s*topics?\D{0,12}(\d+)\s*questions?\s*in\s*each", t, re.I)
    if m:
        r.n_questions = int(m.group(1)) * int(m.group(2))
    else:
        m = re.search(r"(\d+)\s*questions?\b", t, re.I)
        if m:
            r.n_questions = int(m.group(1))
    for a, b in _LADDER_PAIR_RE.findall(t):
        try:
            r.ladder.append((abs(_int(a)), abs(_int(b))))
        except ValueError:
            pass
    m = re.search(r"(\d+)\s*(?:s\b|secs?\b|seconds?\b)", t, re.I)
    if m:
        r.timer_seconds = int(m.group(1))
    else:
        m = re.search(r"(\d+)\s*(?:mins?\b|minutes?\b)", t, re.I)
        if m:
            r.timer_seconds = int(m.group(1)) * 60
    low = t.lower()
    if "directed" in low:
        r.play_mode = "DIRECTED"
    elif "buzzer" in low:
        r.play_mode = "BUZZER"
    elif re.search(r"\bpass\b", low):
        r.play_mode = "PASS"
    return r


_ROUND_NAME_MODE = [
    (re.compile(r"topicator", re.I), "PASS"),
    (re.compile(r"list[\s\-]*it", re.I), "DIRECTED"),
    (re.compile(r"image\s*reveal|connect|rapid\s*fire|hotspot|three\s*clues?|3\s*clues?|portfolio|closing\s*bell", re.I), "BUZZER"),
    (re.compile(r"ticker\s*tangle|arrange|mcq|multiple", re.I), "ALL_ON_SCREEN"),
]


def default_play_mode(round_name: Optional[str], rules: Optional[Rules]) -> Optional[str]:
    if rules and rules.play_mode:
        return rules.play_mode
    for pat, mode in _ROUND_NAME_MODE:
        if round_name and pat.search(round_name):
            return mode
    return None


# ----------------------------------------------------------------------------------------------- dataclasses

@dataclass
class SlideRole:
    slide_number: int
    role: str
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RoundInfo:
    key: int                                   # position of the round in the deck (0-based)
    number: Optional[int]
    name: Optional[str]
    header_slide: int
    rules: Rules = field(default_factory=Rules)
    rules_slides: List[int] = field(default_factory=list)
    play_mode: Optional[str] = None
    live_units: int = 0
    n_questions: Optional[int] = None
    needs_review: bool = False
    review_reason: Optional[str] = None


@dataclass
class Unit:
    index: int
    content_slides: List[int]
    marker_slide: Optional[int]
    answer_slide: Optional[int]
    extra_answer_slides: List[int]
    self_contained: bool
    round_key: Optional[int]
    round_number: Optional[int]
    round_name: Optional[str]
    section: str
    slot: str
    play_mode: Optional[str]
    team: Optional[int]
    hidden: bool
    needs_review: bool = False
    review_reason: Optional[str] = None
    topic_label: Optional[str] = None

    @property
    def first_slide(self) -> int:
        return self.content_slides[0]

    @property
    def last_slide(self) -> int:
        return self.extra_answer_slides[-1] if self.extra_answer_slides else (
            self.answer_slide or self.marker_slide or self.content_slides[-1])


@dataclass
class DeckAnalysis:
    roles: Dict[int, SlideRole]
    units: List[Unit]
    rounds: List[RoundInfo]
    noise: set

    def live_counts(self) -> Dict[Optional[str], int]:
        c: Counter = Counter()
        for u in self.units:
            if u.section == SECTION_MAIN and u.slot == SLOT_LIVE:
                c[u.round_name] += 1
        return dict(c)

    def as_dict(self) -> Dict[str, Any]:
        return {"units": [asdict(u) for u in self.units], "rounds": [asdict(r) for r in self.rounds]}


# ----------------------------------------------------------------------------------------------- roles

def _shape_text_list(slide: Dict[str, Any]) -> List[str]:
    return [sh["text"] for sh in slide.get("shapes") or [] if sh.get("type") == "TEXT_BOX" and sh.get("text")]


def _is_picture_only(slide: Dict[str, Any]) -> bool:
    """No text, and every non-timer shape is a picture/media or group."""
    if _norm(slide.get("extracted_text")):
        return False
    shapes = [s for s in slide.get("shapes") or [] if not is_timer_shape(s)]
    return bool(shapes) and all(s.get("type") in ("PICTURE", "MEDIA", "GROUP") for s in shapes)


def classify_slide(slide: Dict[str, Any], noise: set, divider_art: set) -> SlideRole:
    """Role of one slide from its own text and structure (context-free part of the classification)."""
    n = int(slide["slide_number"])
    text = _norm(slide.get("extracted_text"))
    lines = _lines(text)
    one_line = " ".join(lines)

    if not text:
        shapes = [s for s in slide.get("shapes") or [] if not is_timer_shape(s)]
        pics = [s for s in shapes if s.get("media_md5")]
        if pics and all(p["media_md5"] in divider_art for p in pics) and _is_picture_only(slide):
            return SlideRole(n, DIVIDER, {"art": True})
        return SlideRole(n, CONTENT, {"empty": True})

    if _ANSWER_RE.match(one_line):
        return SlideRole(n, ANSWER_MARKER)
    m = _DIVIDER_RE.match(one_line)
    if m:
        kind = m.group(1).lower()
        return SlideRole(n, DIVIDER, {"kind": "team" if kind == "team" else "question", "number": int(m.group(2))})
    if _EXAMPLE_RE.match(one_line):
        return SlideRole(n, SECTION_EXAMPLE)
    if _AUDIENCE_RE.match(one_line):
        return SlideRole(n, SECTION_AUDIENCE)
    if _TIEBREAK_RE.match(one_line):
        return SlideRole(n, SECTION_TIEBREAK)
    if _BUFFER_RE.match(one_line):
        return SlideRole(n, SECTION_BUFFER)
    if _DECOR_TEXT_RE.match(one_line):
        return SlideRole(n, DECOR)

    # Round header: "TOPICATOR\nROUND 1", "THREE CLUES\nROUND 3", "ROUND 5 - HOTSPOT", "Round 2 - Ticker Tangle".
    # A header is short; a longer slide that merely mentions a round is a rules slide for it.
    if len(lines) <= 3 and len(one_line.split()) <= 9:
        number = name = None
        for i, ln in enumerate(lines):
            mm = _ROUND_LINE_RE.match(ln)
            if mm:
                number = int(mm.group(1))
                tail = (mm.group(2) or "").strip()
                before = " ".join(lines[:i]).strip()
                name = before or tail or None
                if before and tail:
                    name = f"{before} {tail}".strip()
                break
        if number is not None:
            return SlideRole(n, ROUND_HEADER, {"number": number, "name": _clean_round_name(name)})

    return SlideRole(n, CONTENT)


def _clean_round_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    name = re.sub(r"\s+", " ", name).strip(" -–—:")
    return name or None


def rule_signals(text: str) -> int:
    """How many independent kinds of rule wording a slide has: question count, play mode, timer, points, 'rules'."""
    n = 0
    if re.search(r"\d+\s*(?:questions?|topics?)\b", text, re.I):
        n += 1
    if re.search(r"\b(?:buzzer|directed|each\s+team|one\s+question\s+per\s+team|for\s+a\s+pass)\b", text, re.I):
        n += 1
    if re.search(r"\d+\s*(?:secs?|seconds?|mins?|minutes?)\b", text, re.I):
        n += 1
    if (len(_LADDER_PAIR_RE.findall(text)) >= 2 or re.search(r"\bcorrect\b[^\n]{0,20}[+]\s*\d+", text, re.I)
            or re.search(r"[+]\s*\d+\s*(?:points?\s*)?(?:for|if)\b", text, re.I)):
        n += 1
    if re.search(r"^\W*rules?\b", text, re.I):
        n += 1
    return n


def looks_like_rules(slide: Dict[str, Any], min_signals: int = 1) -> bool:
    text = _norm(slide.get("extracted_text"))
    if not text or len(text) > 700 or any(_BADGE_RE.match(l) for l in _lines(text)):
        return False
    sig = rule_signals(text)
    if "?" in text and sig < 3:
        return False
    return sig >= min_signals


def _round_from_rules_slide(slide: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    """'Round 3: Image Reveal / 4 questions...' or 'TOPICATOR / 6 Topics...' -> (number, name)."""
    lines = _lines(_norm(slide.get("extracted_text")))
    if not lines:
        return None, None
    m = _ROUND_ANYWHERE_RE.search(lines[0])
    if m:
        tail = re.sub(r"(?i)\bround\s*\d+\s*[-–—:]*", "", lines[0]).strip(" -–—:")
        return int(m.group(1)), _clean_round_name(tail)
    first = lines[0]
    if len(first.split()) <= 4 and not _RULE_WORDS_RE.search(first):
        return None, _clean_round_name(first)
    return None, None


# ----------------------------------------------------------------------------------------------- analysis

def analyze_deck(slides: List[Dict[str, Any]]) -> DeckAnalysis:
    """Roles for every slide, the question units, and per-round bookkeeping (with the n_questions cross-check)."""
    ordered = sorted((s for s in slides if s.get("slide_number") is not None), key=lambda s: int(s["slide_number"]))
    by_no = {int(s["slide_number"]): s for s in ordered}
    nums = [int(s["slide_number"]) for s in ordered]
    noise = noise_media(ordered)

    # divider art: a picture-only slide whose picture is deck furniture
    art_counts: Counter = Counter()
    for s in ordered:
        if _is_picture_only(s):
            for sh in s.get("shapes") or []:
                if sh.get("media_md5"):
                    art_counts[sh["media_md5"]] += 1
    divider_art = {k for k, v in art_counts.items() if v >= NOISE_MIN_SLIDES and k in noise}

    roles: Dict[int, SlideRole] = {n: classify_slide(by_no[n], noise, divider_art) for n in nums}
    if nums and roles[nums[0]].role == CONTENT:          # a one-line first slide ("Hotspot Reserve", "Prelim MCQs") is the deck title
        first = by_no[nums[0]]
        ft = _norm(first.get("extracted_text"))
        nxt_marker = len(nums) > 1 and roles[nums[1]].role == ANSWER_MARKER
        if ft and "?" not in ft and len(ft.split()) <= 8 and not nxt_marker and not is_listit_slide(first):
            roles[nums[0]] = SlideRole(nums[0], DECOR, {"deck_title": True})

    rounds: List[RoundInfo] = []
    units: List[Unit] = []
    claimed: set = set()

    # ---- pass 1: walk in order, tracking round / section / team; build units at their anchors
    cur_round: Optional[RoundInfo] = None
    section = SECTION_MAIN
    team: Optional[int] = None
    rules_open = False                # a round header was seen and no question material yet
    last_boundary_idx = -1           # index into `nums` after which a unit's content run may begin
    i = 0

    def round_name() -> Optional[str]:
        return cur_round.name if cur_round else None

    def make_unit(content_idx: List[int], marker_idx: Optional[int], answer_idx: Optional[int], self_contained: bool) -> Unit:
        content = [nums[k] for k in content_idx]
        first = by_no[content[0]]
        hidden = bool(first.get("hidden"))
        if section == SECTION_EXAMPLE_NAME:
            slot = SLOT_EXAMPLE
        else:
            slot = SLOT_RESERVE if hidden else SLOT_LIVE
        if section == SECTION_AUDIENCE_NAME:
            mode = "AUDIENCE"
        elif section == SECTION_TIEBREAK_NAME:
            mode = "TIEBREAK"
        elif section == SECTION_BUFFER_NAME:
            mode = "BUFFER"         # its own section, not a buzzer round (decided with the product owner)
        elif team is not None:
            mode = "DIRECTED"
        else:
            mode = (cur_round.play_mode if cur_round else None)
        u = Unit(
            index=len(units), content_slides=content,
            marker_slide=nums[marker_idx] if marker_idx is not None else None,
            answer_slide=nums[answer_idx] if answer_idx is not None else None,
            extra_answer_slides=[], self_contained=self_contained,
            round_key=cur_round.key if (cur_round and section in (SECTION_MAIN, SECTION_EXAMPLE_NAME, SECTION_AUDIENCE_NAME,
                                                                  SECTION_TIEBREAK_NAME)) else None,
            round_number=cur_round.number if cur_round and section != SECTION_BUFFER_NAME else None,
            round_name=round_name() if section != SECTION_BUFFER_NAME else None,
            section=section, slot=slot, play_mode=mode, team=team, hidden=hidden)
        if not self_contained and answer_idx is None:
            u.needs_review, u.review_reason = True, "no answer slide after the ANSWER marker"
        return u

    while i < len(nums):
        n = nums[i]
        s = by_no[n]
        role = roles[n]

        if role.role == ROUND_HEADER:
            cur_round = RoundInfo(key=len(rounds), number=role.detail.get("number"), name=role.detail.get("name"),
                                  header_slide=n)
            rounds.append(cur_round)
            section, team, rules_open = SECTION_MAIN, None, True
            last_boundary_idx = i
            i += 1
            continue

        if role.role in (SECTION_EXAMPLE, SECTION_AUDIENCE, SECTION_TIEBREAK, SECTION_BUFFER):
            section = {SECTION_EXAMPLE: SECTION_EXAMPLE_NAME, SECTION_AUDIENCE: SECTION_AUDIENCE_NAME,
                       SECTION_TIEBREAK: SECTION_TIEBREAK_NAME, SECTION_BUFFER: SECTION_BUFFER_NAME}[role.role]
            if section == SECTION_BUFFER_NAME:
                cur_round = None            # BUFFER clears the round name; its questions are mixed formats
            team = None
            rules_open = False
            last_boundary_idx = i
            i += 1
            continue

        if role.role == DIVIDER:
            team = role.detail.get("number") if role.detail.get("kind") == "team" else None
            if section in (SECTION_EXAMPLE_NAME, SECTION_AUDIENCE_NAME):
                section = SECTION_MAIN
            rules_open = False
            last_boundary_idx = i
            i += 1
            continue

        if role.role == DECOR:
            last_boundary_idx = i
            i += 1
            continue

        if role.role == ANSWER_MARKER:
            # content run: back from the marker over unclaimed content slides, stopping at any boundary
            j = i - 1
            run: List[int] = []
            hid = None
            while j > last_boundary_idx and nums[j] not in claimed and roles[nums[j]].role == CONTENT:
                h = bool(by_no[nums[j]].get("hidden"))
                if hid is not None and h != hid:
                    break
                hid = h
                run.append(j)
                j -= 1
            run.reverse()
            ans_idx = None
            if i + 1 < len(nums) and roles[nums[i + 1]].role == CONTENT:
                ans_idx = i + 1
            if run:
                u = make_unit(run, i, ans_idx, False)
                units.append(u)
                claimed.update(u.content_slides)
                claimed.add(n)
                if ans_idx is not None:
                    claimed.add(nums[ans_idx])
                    # number-only picture slides right after the answer are extra answer images
                    k = ans_idx + 1
                    while k < len(nums) and roles[nums[k]].role == CONTENT and _is_number_only_picture(by_no[nums[k]]):
                        u.extra_answer_slides.append(nums[k])
                        claimed.add(nums[k])
                        k += 1
                    last_boundary_idx = k - 1
                    i = k
                else:
                    last_boundary_idx = i
                    i += 1
                rules_open = False
                continue
            # a marker with no question before it: not a unit
            last_boundary_idx = i
            i += 1
            continue

        # CONTENT slide
        if is_listit_slide(s) and n not in claimed:
            u = make_unit([i], None, None, True)
            units.append(u)
            claimed.add(n)
            last_boundary_idx = i
            rules_open = False
            i += 1
            continue

        followed_by_marker = i + 1 < len(nums) and roles[nums[i + 1]].role == ANSWER_MARKER
        if not followed_by_marker and looks_like_rules(s, 1 if (rules_open and cur_round is not None) else 2):
            number, name = _round_from_rules_slide(s)
            if (cur_round is None and (number is not None or name)) or (
                    cur_round is not None and number is not None and cur_round.number is not None
                    and number != cur_round.number):
                # rules slide of a round whose header slide does not exist: it opens the round itself
                cur_round = RoundInfo(key=len(rounds), number=number, name=name, header_slide=n)
                rounds.append(cur_round)
                section, team = SECTION_MAIN, None
            roles[n] = SlideRole(n, RULES)
            if cur_round is not None:
                cur_round.rules_slides.append(n)
                cur_round.rules.merge(parse_rules(s.get("extracted_text") or ""))
                if cur_round.name is None and name:
                    cur_round.name = name
                if cur_round.number is None and number is not None:
                    cur_round.number = number
            last_boundary_idx = i
            i += 1
            continue

        if looks_like_topic_board(s):
            roles[n] = SlideRole(n, DECOR, {"topic_board": True})
            last_boundary_idx = i
            i += 1
            continue

        i += 1

    # ---- pass 2: Topicator topic labels, per-round bookkeeping and the n_questions cross-check
    for u in units:
        r_name = (u.round_name or "")
        if u.section in (SECTION_MAIN, SECTION_AUDIENCE_NAME) and re.search(r"topicator", r_name, re.I) and not u.self_contained:
            u.topic_label = topic_label_of(by_no[u.content_slides[0]])
            if u.topic_label is None and u.section == SECTION_MAIN:
                u.needs_review, u.review_reason = True, "Topicator question without a topic label"
    for r in rounds:
        r.play_mode = default_play_mode(r.name, r.rules)
        r.n_questions = r.rules.n_questions
    for u in units:
        if u.round_key is not None and u.play_mode is None and u.section == SECTION_MAIN:
            u.play_mode = rounds[u.round_key].play_mode
    for u in units:      # a unit that failed on its own (no topic label...) does not count towards the round
        if u.section == SECTION_MAIN and u.slot == SLOT_LIVE and u.round_key is not None and not u.needs_review:
            rounds[u.round_key].live_units += 1
    for r in rounds:
        if r.n_questions is not None and r.live_units != r.n_questions:
            r.needs_review = True
            r.review_reason = f"rules say {r.n_questions} questions, found {r.live_units} live"
            # The flag is on the ROUND: a reserve bank legitimately holds more units than the rules say, so
            # the units themselves are not demoted (Phase 3 decides what a flagged round means for them).
    return DeckAnalysis(roles=roles, units=units, rounds=rounds, noise=noise)


_TOPIC_LABEL_RE = re.compile(r"^([A-Z][A-Z &]*[A-Z])\s*-\s*(\d)$")


def topic_label_of(slide: Dict[str, Any]) -> Optional[str]:
    """'SUSTAINABILITY - 1' / 'PROBLEM SOLVING  - 1' -> 'Sustainability 1' (from the title or any one-line text box)."""
    cands = [slide.get("title") or ""] + _shape_text_list(slide)
    for c in cands:
        for line in _lines(c):
            m = _TOPIC_LABEL_RE.match(re.sub(r"\s+", " ", line.strip()))
            if m:
                return f"{m.group(1).title()} {m.group(2)}"
    return None


def looks_like_topic_board(slide: Dict[str, Any]) -> bool:
    text = _norm(slide.get("extracted_text"))
    return bool(_TOPIC_BOARD_RE.match(text)) and len(re.findall(r"\bQ\d\b", text)) >= 2


def _is_number_only_picture(slide: Dict[str, Any]) -> bool:
    text = _norm(slide.get("extracted_text"))
    has_pic = any(sh.get("type") == "PICTURE" for sh in slide.get("shapes") or [])
    return has_pic and (not text or bool(re.fullmatch(r"\W*\d{1,3}\W*", text)))
