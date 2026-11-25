"""Question normalizer: idempotent text-level fixes applied at ingest time and during backfill.

Fixes:
- MCQ letter prefix on answer (``A. Net Asset Value`` → ``Net Asset Value``)
- Scoring/round artifacts in question body (``Points: 10``, ``Round 2:`` lines)
- Excessive blank lines (3+ → 2)
- Excessive inline spaces / tabs
- Empty options list on non-MCQ questions ([] → None)
- ``question_format`` alias ``QA`` → ``STANDARD_QA``
- Short junk questions and empty answers flagged for review
"""
from __future__ import annotations

import re
import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical format set (includes display-only aliases kept as-is)
# ---------------------------------------------------------------------------
CANONICAL_FORMATS: Set[str] = {
    "STANDARD_QA",
    "CONNECT",
    "CONNECT_4",
    "LIST_IT",
    "IMAGE_REVEAL",
    "STEPPED_CLUE",
    "ARRANGE_ORDER",
    "MCQ",
    # Display-only aliases (valid; renderer handles them)
    "VISUAL_CLUE",
    "VIDEO_CLUE",
    "AUDIO",
    "BUZZER",
    "POTPOURRI",
}

# Alias remaps: OLD → canonical
FORMAT_ALIASES: Dict[str, str] = {
    "QA": "STANDARD_QA",
    "THREE_CLUE": "STEPPED_CLUE",
}

# Minimum meaningful lengths
MIN_QUESTION_LEN = 10
MIN_ANSWER_LEN = 1

# Regex: a leading MCQ letter prefix on the answer field, e.g. "A. " / "A) " / "(A) "
_MCQ_ANS_PREFIX = re.compile(r"^(?:\([A-D]\)|[A-D][.)])\s+")
# "A. R. Rahman": the remainder starts with another initial, so the "A." is part of a name, not an MCQ marker
_INITIAL_AFTER = re.compile(r"^[A-Z]\.\s")

# Lines that are purely scoring / round metadata and should not appear in question body
_SCORING_LINE = re.compile(
    r"^(Points?|Bonus|Score)\s*[:\-]\s*[\d]+\s*$",
    re.IGNORECASE,
)
_ROUND_HEADER_LINE = re.compile(
    r"^Round\s+\d+\s*[:\-]?\s*$",
    re.IGNORECASE,
)
# "Clue N (+20 / -15)" standalone badge lines that bleed into plain STANDARD_QA text
_CLUE_BADGE_LINE = re.compile(
    r"^Clue\s*\d+\s*\([+\-\d\s/]+\)\s*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public normalizers
# ---------------------------------------------------------------------------

def normalize_question_format(fmt: Optional[str]) -> str:
    """Return the canonical question_format string, applying alias remaps."""
    if not fmt:
        return "STANDARD_QA"
    mapped = FORMAT_ALIASES.get(fmt, fmt)
    if mapped not in CANONICAL_FORMATS:
        logger.warning("Unknown question_format %r will be stored as-is; add to CANONICAL_FORMATS if intentional", fmt)
    return mapped


def normalize_answer(answer: Optional[str], question_format: str = "STANDARD_QA") -> str:
    """Strip MCQ letter prefix from the answer field.

    E.g.  ``A. Net Asset Value (NAV)``  →  ``Net Asset Value (NAV)``
          ``A) Joint Accounts``          →  ``Joint Accounts``
    Only applied when the prefix pattern matches; never trims real single-letter answers.
    """
    if not answer:
        return answer or ""
    original = answer.strip()
    stripped = _MCQ_ANS_PREFIX.sub("", original, count=1).strip()
    if not stripped or stripped == original:
        return original
    if question_format != "MCQ" and _INITIAL_AFTER.match(stripped):
        return original
    return stripped


def normalize_question_text(text: Optional[str]) -> str:
    """Remove scoring/round artifacts; normalize whitespace."""
    if not text:
        return text or ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned: List[str] = []
    for line in lines:
        stripped = line.rstrip()
        if _SCORING_LINE.match(stripped.strip()):
            continue
        if _ROUND_HEADER_LINE.match(stripped.strip()):
            continue
        if _CLUE_BADGE_LINE.match(stripped.strip()):
            continue
        cleaned.append(stripped)
    result = "\n".join(cleaned)
    # Collapse 3+ consecutive blank lines to 2
    result = re.sub(r"\n{3,}", "\n\n", result)
    # Collapse 3+ consecutive spaces/tabs (but not newlines) to 1
    result = re.sub(r"[ \t]{3,}", " ", result)
    return result.strip()


def normalize_options(options: Any, question_format: str) -> Optional[List[str]]:
    """Return None for empty options lists on non-MCQ questions."""
    if question_format != "MCQ" and isinstance(options, list) and len(options) == 0:
        return None
    return options


def needs_review_flags(question_text: str, answer: str) -> Dict[str, bool]:
    """Return a dict of review flags for a question after normalization."""
    qt = (question_text or "").strip()
    ans = (answer or "").strip()
    return {
        "empty_question": not qt,
        "very_short_question": bool(qt) and len(qt) < MIN_QUESTION_LEN,
        "empty_answer": not ans,
    }


def normalize_question_dict(q: Dict[str, Any]) -> Dict[str, Any]:
    """Apply all normalizers to a question dict (in-place) and return it.

    Idempotent — safe to call multiple times.
    """
    q["question_format"] = normalize_question_format(q.get("question_format"))
    q["question_text"] = normalize_question_text(q.get("question_text"))
    q["answer"] = normalize_answer(q.get("answer"), q.get("question_format", "STANDARD_QA"))
    q["options"] = normalize_options(q.get("options"), q.get("question_format", "STANDARD_QA"))

    flags = needs_review_flags(q.get("question_text", ""), q.get("answer", ""))
    if any(flags.values()):
        q.setdefault("_ingest", {})
        q["_ingest"]["needs_review"] = True
        q["_ingest"].setdefault("review_reasons", [])
        for key, triggered in flags.items():
            if triggered:
                q["_ingest"]["review_reasons"].append(key)

    return q


def validate_before_save(q: Dict[str, Any]) -> List[str]:
    """Return a list of validation error strings (empty = OK).

    Called right before the Question ORM object is built to catch issues early.
    Does NOT block ingest (the pipeline logs warnings); a future strict-mode
    flag can turn these into exceptions.
    """
    errors: List[str] = []
    qt = (q.get("question_text") or "").strip()
    ans = (q.get("answer") or "").strip()
    fmt = q.get("question_format", "STANDARD_QA")
    opts = q.get("options")

    if not qt:
        errors.append("question_text is empty")
    elif len(qt) < MIN_QUESTION_LEN:
        errors.append(f"question_text too short ({len(qt)} chars): {qt!r}")

    if not ans:
        errors.append("answer is empty")

    if fmt not in CANONICAL_FORMATS and fmt not in FORMAT_ALIASES:
        errors.append(f"unknown question_format: {fmt!r}")

    if fmt != "MCQ" and isinstance(opts, list) and len(opts) == 0:
        errors.append("empty options list on non-MCQ question — should be None")

    return errors
