"""Cost estimate and per-deck usage rows for Gemini calls (audit Q13 & Cost Tracker Phase 1).

Prices read from the unified CostRateCard (with fallback to verified defaults).
Thinking tokens are billed as output.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Verified gemini-3.5-flash-lite list prices, USD per million tokens.
# Thinking tokens are billed as output.
PRICE_IN_PER_M = float(os.getenv("GEMINI_PRICE_IN_PER_M", "0.30"))
PRICE_OUT_PER_M = float(os.getenv("GEMINI_PRICE_OUT_PER_M", "2.50"))
PRICE_BATCH_IN_PER_M = float(os.getenv("GEMINI_PRICE_BATCH_IN_PER_M", "0.15"))
PRICE_BATCH_OUT_PER_M = float(os.getenv("GEMINI_PRICE_BATCH_OUT_PER_M", "1.25"))

# Cached prompt tokens are billed at a fraction of the input price ($0.03 / $0.30 = 0.10).
CACHED_INPUT_RATIO = float(os.getenv("GEMINI_CACHED_INPUT_RATIO", "0.10"))
USD_TO_INR = float(os.getenv("USD_TO_INR", "95.0"))


def estimate_cost_usd(
    tokens_in: int = 0,
    tokens_out: int = 0,
    thinking: int = 0,
    cached: int = 0,
    model: Optional[str] = None,
    is_batch: bool = False,
) -> float:
    """`tokens_in` is Gemini's promptTokenCount, which already includes cached tokens.

    Queries the active CostRateCard if available, falling back to module defaults.
    """
    cached = min(max(cached or 0, 0), tokens_in or 0)
    fresh_in = max(0, (tokens_in or 0) - cached)

    in_price = PRICE_BATCH_IN_PER_M if is_batch else PRICE_IN_PER_M
    out_price = PRICE_BATCH_OUT_PER_M if is_batch else PRICE_OUT_PER_M
    cached_ratio = CACHED_INPUT_RATIO

    try:
        from backend.app.services.costs.rate_card import get_active_rate_card
        card = get_active_rate_card()
        models_cfg = card.get("models", {})
        m_name = (model or "").lower().strip()
        m_rates = models_cfg.get(m_name) or models_cfg.get("default")
        if m_rates:
            if is_batch:
                in_price = float(m_rates.get("batch_input_per_m", in_price))
                out_price = float(m_rates.get("batch_output_per_m", out_price))
            else:
                in_price = float(m_rates.get("input_per_m", in_price))
                out_price = float(m_rates.get("output_per_m", out_price))
            cached_price = float(m_rates.get("cached_input_per_m", in_price * cached_ratio))
            cached_ratio = cached_price / in_price if in_price > 0 else 0.0
    except Exception:
        pass  # Defensive: keep hardcoded defaults if rate card service is not yet initialized

    return (
        fresh_in / 1e6 * in_price
        + cached / 1e6 * in_price * cached_ratio
        + ((tokens_out or 0) + (thinking or 0)) / 1e6 * out_price
    )


def save_usage(db, document_id: str, stage: str, model: Optional[str], usage: Dict[str, Any]) -> None:
    """Add one usage row for a deck/stage to `db` (the caller commits).

    Preserves existing IngestionUsage table bookkeeping so legacy views keep working.
    Never raises: cost bookkeeping must not break an ingestion.
    """
    try:
        if not document_id or not usage or not int(usage.get("calls", 0) or 0):
            return
        from backend.app.models.usage import IngestionUsage

        db.add(
            IngestionUsage(
                document_id=document_id,
                stage=stage,
                model=model,
                calls=int(usage.get("calls", 0) or 0),
                tokens_in=int(usage.get("in", 0) or 0),
                tokens_out=int(usage.get("out", 0) or 0),
                thinking_tokens=int(usage.get("think", 0) or 0),
                cached_tokens=int(usage.get("cached", 0) or 0),
            )
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("could not record %s usage for %s: %s", stage, document_id, type(e).__name__)
