"""Topic pools + honest fulfillment reporting. Pure functions.

on-topic    = what the quizmaster asked about (L2 match if L2s were asked for, else L1 match, else BM25 hit).
adjacent    = passes hard rules, not on-topic, but still retrieved. Only added if the quizmaster allowed padding.
"""
from typing import Any, Callable, Dict, List, Optional, Sequence

from backend.app.services.prompt_quiz.audience import HARD_FLAGS, difficulty_penalty, passes_hard

Candidate = Dict[str, Any]
_NEVER_OFFER_RELAX = {"school_safe"}        # a safety rule is never offered as a "relax this" suggestion

# Minimum BM25 score to be considered relevant. Questions below this
# matched the query only by accident (a stray word overlap).
MIN_RELEVANCE_SCORE = 0.4


def _l2_names(c: Candidate) -> set:
    names = {(c.get("subtopic_l2") or "").lower()} | {(x or "").lower() for x in (c.get("secondary_l2") or [])}
    names.discard("")
    return names


def is_on_topic(c: Candidate, l1s: Sequence[str], l2s: Sequence[str], min_semantic_score: float = 1.2) -> bool:
    score = float(c.get("score") or 0.0)
    if score < MIN_RELEVANCE_SCORE:
        return False
    # If the candidate has a strong semantic score matching the query & expansion terms,
    # it is genuinely on-topic regardless of strict database taxonomy tagging discrepancies.
    if score >= min_semantic_score:
        return True
    l1set = {x.lower() for x in l1s or []}
    l2set = {x.lower() for x in l2s or []}
    if l2set:
        return bool(_l2_names(c) & l2set)
    if l1set:
        return (c.get("topic_l1") or "").lower() in l1set
    return True


def split_pools(pool: List[Candidate], *, l1s: Sequence[str], l2s: Sequence[str], filters: Dict[str, Any],
                format_filter: Optional[str]):
    """Returns (on_topic_ok, adjacent_ok, on_topic_all, effective_l2s).
    `_ok` lists satisfy the hard rules. If the asked-for L2 names match nothing in the pool (the LLM named a
    subtopic that does not exist), they are dropped and the L1 is used instead."""
    l2s = list(l2s or [])
    if l2s and not any(_l2_names(c) & {x.lower() for x in l2s} for c in pool):
        l2s = []
    l1s = list(l1s or [])
    if l1s and not any((c.get("topic_l1") or "").lower() in {x.lower() for x in l1s} for c in pool):
        l1s = []
    on_all = [c for c in pool if is_on_topic(c, l1s, l2s)]
    on_ok = [c for c in on_all if passes_hard(c, filters, format_filter)]
    on_ids = {id(c) for c in on_all}
    adj = [c for c in pool if id(c) not in on_ids and float(c.get("score") or 0.0) > 0.0
           and passes_hard(c, filters, format_filter)]
    return on_ok, adj, on_all, l2s


def blocked_counts(on_all: List[Candidate], filters: Dict[str, Any], format_filter: Optional[str]) -> Dict[str, int]:
    """For each hard rule: how many on-topic questions are excluded ONLY by that rule."""
    out: Dict[str, int] = {}
    for flag in HARD_FLAGS:
        if flag in _NEVER_OFFER_RELAX:
            continue
        n = sum(1 for c in on_all
                if not passes_hard(c, filters, format_filter) and passes_hard(c, filters, format_filter, skip=flag))
        if n:
            out[flag] = n
    return out


def build_fulfillment(*, requested: int, picked: List[Candidate], padded: int, adjacent_available: int,
                      blocked: Dict[str, int], hard_bias: Optional[str], padding_allowed: bool) -> Dict[str, Any]:
    delivered = len(picked)
    off_diff = sum(1 for c in picked if difficulty_penalty(c, hard_bias) >= 0.5)
    short = delivered < requested
    suggestions: List[Dict[str, Any]] = []
    if short and adjacent_available > 0 and not padding_allowed:
        suggestions.append({"action": "add_adjacent", "count": min(requested - delivered, adjacent_available)})
    if short:
        for flag, n in blocked.items():
            suggestions.append({"action": "relax", "flag": flag, "count": n})

    if not short:
        note = f"Here are {delivered} questions."
    else:
        note = f"Found {delivered} of {requested} that fit."
        if padded:
            note += f" {padded} are from adjacent topics."
        elif adjacent_available > 0 and not padding_allowed:
            note += f" I can add {min(requested - delivered, adjacent_available)} from adjacent topics if you want."
        elif not suggestions:
            note += " That is everything the bank has for this request."
    if off_diff:
        note += f" {off_diff} sit outside the difficulty you asked for."
    return {"requested": requested, "delivered": delivered, "short": short, "padded": padded,
            "off_difficulty": off_diff, "suggestions": suggestions, "note": note}
