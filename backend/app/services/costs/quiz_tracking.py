"""Off-request-path cost tracking for quiz calls (intent, curator, interpreter).

Tracking must never break quizzes: every write is fail-safe, non-blocking,
and handled in a daemon thread.
"""
from __future__ import annotations

import logging
import threading
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Fallback estimates for calls that timed out after sending (from docs/COST_AUDIT_2026-11.md averages)
TIMEOUT_ESTIMATES: Dict[str, Dict[str, int]] = {
    "quiz_intent": {"tokens_in": 1080, "tokens_out": 140, "thinking_tokens": 0, "calls": 1},
    "quiz_curator": {"tokens_in": 2390, "tokens_out": 80, "thinking_tokens": 0, "calls": 1},
    "quiz_interpreter": {"tokens_in": 1210, "tokens_out": 150, "thinking_tokens": 0, "calls": 1},
}


def track_quiz_llm_call(
    operation: str,
    model: str,
    usage_metadata: Optional[Dict[str, Any]] = None,
    quiz_id: Optional[str] = None,
    is_timeout: bool = False,
    note: Optional[str] = None,
) -> None:
    """Record a quiz LLM call in a background daemon thread so request latency is never affected."""
    def _run() -> None:
        try:
            from backend.app.services.costs.ledger import record_event

            qid = quiz_id or f"prompt_{uuid.uuid4().hex[:8]}"

            if is_timeout:
                est = dict(TIMEOUT_ESTIMATES.get(operation, {"tokens_in": 1500, "tokens_out": 100, "calls": 1}))
                record_event(
                    category="gemini",
                    operation=operation,
                    model=model,
                    units=est,
                    quiz_id=qid,
                    source="estimated",
                    note=note or "timeout",
                )
            elif usage_metadata:
                units = {
                    "tokens_in": int(usage_metadata.get("promptTokenCount", 0) or 0),
                    "tokens_out": int(usage_metadata.get("candidatesTokenCount", 0) or 0),
                    "thinking_tokens": int(usage_metadata.get("thoughtsTokenCount", 0) or 0),
                    "cached_tokens": int(usage_metadata.get("cachedContentTokenCount", 0) or 0),
                    "calls": 1,
                }
                record_event(
                    category="gemini",
                    operation=operation,
                    model=model,
                    units=units,
                    quiz_id=qid,
                    source="measured",
                    note=note,
                )
        except Exception as e:
            logger.debug("Failed to record quiz LLM call cost (%s): %s", operation, e)

    try:
        t = threading.Thread(target=_run, name=f"cost-{operation}", daemon=True)
        t.start()
    except Exception as e:
        logger.debug("Could not spawn cost recording thread: %s", e)
