"""Which text on a Connect slide is a real clue.

A Connect slide is pictures, and sometimes one text clue ("The lowest female singing voice", "August 7") sitting on the
line between them. Nothing else is a clue and must never be shown as one: the slide title ("3. Connect:"), the
instruction ("What common thread or concept connects all four clues and images?") and round labels ("Question for our
Audience"). `text_hint` has held all of these, so the export, the extractor and the repair code share this one rule.
"""
from __future__ import annotations

import re
from typing import List, Optional

SEPARATOR = " / "

# "3. Connect", "3. Connect:", "Connect 4": the title block. "Connected by rail" and "Connecticut" are not titles.
_TITLE = re.compile(r"^\W*(?:\d{1,3}\s*[.):]?\s*)?connect(?:\s*(?:4|four))?\b", re.I)

# Instructions to the contestants, however they are worded.
_PROMPTS = tuple(re.compile(p, re.I) for p in (
    r"\bcommon (?:thread|link|factor|theme|concept|connection)\b",
    r"\bwhat (?:connects?|links?|ties|do (?:these|they|all)|is the (?:link|connection|common))\b",
    r"\bconnects? (?:all|these|them|the|four|4)\b",
    r"\b(?:the )?(?:four|4|five|5|six|6) (?:clues|images|pictures|elements|items)\b",
    r"\bclues? (?:and|&) (?:images?|pictures?)\b",
    r"\b(?:identify|guess|find|name|spot) (?:the )?(?:connect|link|connection|common)",
    r"\blook at the\b",
))

# Round, section and audience labels.
_LABELS = tuple(re.compile(p, re.I) for p in (
    r"\bquestion for (?:our|the|an?) audience\b",
    r"\baudience (?:question|round|connect)\b",
    r"^for (?:our |the )?audience$",
    r"^(?:bonus|audience|tie[- ]?breaker|buzzer|warm[- ]?up|prelims?|finals?)(?: (?:question|round))?$",
    r"^(?:round|set|question|q)\s*\d*$",
))


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def is_connect_clue_text(text) -> bool:
    """True for a text clue; False for a title, an instruction, a label, or a question."""
    t = _norm(text)
    if not t:
        return False
    if _TITLE.match(t) or t.endswith("?"):
        return False
    return not any(p.search(t) for p in _PROMPTS) and not any(p.search(t) for p in _LABELS)


def split_clue_parts(hint) -> List[str]:
    """The real text clues in a stored `text_hint` (parts are joined with " / "). A hint with a Connect title line is
    the slide's title block, so none of it is a clue."""
    raw = str(hint or "")
    if any(_TITLE.match(line.strip()) for line in raw.splitlines() if line.strip()):
        return []
    return [p for p in (_norm(x) for x in raw.split(SEPARATOR)) if is_connect_clue_text(p)]


def clean_text_hint(hint) -> Optional[str]:
    """What `text_hint` should store: the real clues only, or None."""
    return SEPARATOR.join(split_clue_parts(hint)) or None
