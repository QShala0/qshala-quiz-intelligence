"""Per-format quotas for a quiz: "10 picture, 10 connect, 10 stepped clue".

A brief carries `format_mix` = {canonical format: how many}. It is read from the prompt by `parse_format_mix` (deterministic, so it
still works when Gemini is down or ignores it) and/or from Gemini's `format_mix` list via `normalize_mix`.
"""
import re
from typing import Any, Dict, Iterable, Optional

from backend.app.services.prompt_quiz.audience import canon_format

# Canonical formats a quiz can be split by. "Picture" means a VISUAL_CLUE question.
MIX_FORMATS = ("STEPPED_CLUE", "CONNECT", "VISUAL_CLUE", "ARRANGE_ORDER", "BUZZER", "STANDARD_QA")

_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "twenty-five": 25, "thirty": 30,
}
_NUM = r"(\d{1,2}|" + "|".join(sorted(_NUM_WORDS, key=len, reverse=True)) + r")"

# Alternation order matters: "stepped clue" must win before a bare "clue" could be read as anything else.
_FORMAT_WORDS = (
    ("STEPPED_CLUE", r"stepped[\s-]*clues?|three[\s-]*clues?|3[\s-]*clues?|clue[\s-]*ladders?"),
    ("CONNECT", r"connect(?:[\s-]*(?:4|four))?"),
    ("VISUAL_CLUE", r"pictures?|visuals?|images?|photos?"),
    ("ARRANGE_ORDER", r"arrange(?:ment)?|ordering|sequenc(?:e|ing)"),
    ("BUZZER", r"buzzers?"),
    ("STANDARD_QA", r"normal|standard|plain|regular|text"),
)
_PATTERN = re.compile(
    r"\b" + _NUM + r"\s+(?:of\s+)?(?:the\s+)?(?:(" + "|".join(f"(?P<{k}>{v})" for k, v in _FORMAT_WORDS) + r"))\b",
    re.I,
)

# Display names for messages.
FORMAT_LABELS = {
    "STEPPED_CLUE": "stepped clue", "CONNECT": "connect", "VISUAL_CLUE": "picture",
    "ARRANGE_ORDER": "ordering", "BUZZER": "buzzer", "STANDARD_QA": "normal",
}


def _to_int(raw: str) -> Optional[int]:
    raw = raw.lower()
    if raw.isdigit():
        return int(raw)
    return _NUM_WORDS.get(raw)


def parse_format_mix(prompt: str) -> Dict[str, int]:
    """{'VISUAL_CLUE': 10, 'CONNECT': 10, 'STEPPED_CLUE': 10} from '10 picture rounds, 10 connect, 10 stepped clue'."""
    mix: Dict[str, int] = {}
    for m in _PATTERN.finditer(prompt or ""):
        n = _to_int(m.group(1))
        fmt = next((k for k, _ in _FORMAT_WORDS if m.group(k)), None)
        if n and n > 0 and fmt:
            mix[fmt] = mix.get(fmt, 0) + n
    return mix


def normalize_mix(raw: Any) -> Dict[str, int]:
    """Accepts Gemini's [{'format': 'CONNECT_4', 'count': 10}, ...] or an already-built dict; drops junk."""
    items: Iterable
    if isinstance(raw, dict):
        items = [{"format": k, "count": v} for k, v in raw.items()]
    elif isinstance(raw, list):
        items = raw
    else:
        return {}
    mix: Dict[str, int] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            n = int(it.get("count"))
        except (TypeError, ValueError):
            continue
        fmt = canon_format(str(it.get("format") or ""))
        if n > 0 and fmt in MIX_FORMATS:
            mix[fmt] = mix.get(fmt, 0) + n
    return mix


def resolve_mix(prompt: str, gemini_mix: Any, total: int) -> Dict[str, int]:
    """Prefer what the prompt literally says; fall back to Gemini's reading. A mix that is one format filling the whole quiz
    is not a mix (the single-format filter handles it), so it is returned empty."""
    mix = parse_format_mix(prompt) or normalize_mix(gemini_mix)
    if len(mix) == 1 and next(iter(mix.values())) >= total:
        return {}
    return mix


def describe_mix(mix: Dict[str, int]) -> str:
    return ", ".join(f"{n} {FORMAT_LABELS.get(f, f.lower())}" for f, n in mix.items())
