"""Which side of a question a picture belongs to.

Quiz decks very often repeat the question's pictures on the answer slide (the answer reveal shows the same
logo, the same four Connect clues, the same Arrange items, plus the answer). The extractors copied every
picture on the answer slide into `answer_images`, so one picture was listed as both a question picture and an
answer picture. Rule: a picture already shown with the question is a question picture and appears once.
`answer_images` keeps only pictures that are new on the answer side.

One exception: when the answer slide adds nothing but those repeated pictures (no usable answer text), the
picture itself is the answer, so the list is left alone.

PICTURE_SIDE_RULES_ENABLED=0 switches the rule off (restores the old behaviour).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

_PLACEHOLDER_ANSWERS = {"", "see image", "see images", "see picture", "see video", "see audio", "n/a", "-"}


def picture_side_rules_enabled() -> bool:
    return os.getenv("PICTURE_SIDE_RULES_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def _has_real_answer(q: Dict[str, Any]) -> bool:
    return (q.get("answer") or "").strip().lower() not in _PLACEHOLDER_ANSWERS


def separate_picture_sides(q: Dict[str, Any]) -> Dict[str, Any]:
    """Remove from answer_images every picture that is also in question_images. Mutates and returns `q`.
    image_refs (the union) is unchanged, so nothing disappears from the question's gallery or the exports."""
    if not picture_side_rules_enabled():
        return q
    qi: List[str] = list(q.get("question_images") or [])
    ai: List[str] = list(q.get("answer_images") or [])
    if not qi or not ai:
        return q
    shown = set(qi)
    fresh = [u for u in ai if u not in shown]
    if len(fresh) == len(ai):
        return q                                  # nothing repeated
    if not fresh and not _has_real_answer(q):
        return q                                  # the repeated picture is the only answer there is
    q["answer_images"] = fresh
    return q


def separate_picture_sides_all(questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for q in questions:
        try:
            separate_picture_sides(q)
        except Exception:                         # never fail a deck over a display rule
            pass
    return questions
