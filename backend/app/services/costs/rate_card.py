"""Versioned cost rate card management and unit price calculations.

Prices are stored and versioned as data, not code.
Version 1 is seeded with the verified rates from docs/COST_AUDIT_2026-11.md.
"""
from __future__ import annotations

import copy
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Default verified Rate Card V1 (from docs/COST_AUDIT_2026-11.md)
DEFAULT_RATE_CARD_V1: Dict[str, Any] = {
    "version": 1,
    "effective_from": "2026-11-23T00:00:00Z",
    "usd_to_inr": 95.0,
    "models": {
        "gemini-3.5-flash-lite": {
            "input_per_m": 0.30,
            "output_per_m": 2.50,
            "batch_input_per_m": 0.15,
            "batch_output_per_m": 1.25,
            "cached_input_per_m": 0.03,
        },
        "gemini-embedding-001": {
            "input_per_m": 0.15,
            "output_per_m": 0.0,
            "batch_input_per_m": 0.075,
            "batch_output_per_m": 0.0,
            "cached_input_per_m": 0.0,
        },
        "default": {
            "input_per_m": 0.30,
            "output_per_m": 2.50,
            "batch_input_per_m": 0.15,
            "batch_output_per_m": 1.25,
            "cached_input_per_m": 0.03,
        },
    },
    "cloud_run": {
        "region": "asia-southeast1",
        "tier": 2,
        "vcpu_active_per_s": 0.00003360,
        "gib_active_per_s": 0.00000350,
        "idle_min_instance_per_unit_s": 0.00000350,  # per vCPU-s and per GiB-s
        "requests_per_m": 0.40,
    },
    "r2": {
        "storage_per_gb_month": 0.015,
        "storage_free_gb": 10.0,
        "class_a_per_m": 4.50,
        "class_a_free_m": 1.0,
        "class_b_per_m": 0.36,
        "class_b_free_m": 10.0,
    },
    "neon": {
        "cu_hour": 0.106,
        "storage_per_gb_month": 0.35,
        "storage_free_gb": 0.5,
    },
    "fixed_monthly_usd": {
        "vercel": 20.0,  # Pro seat
        "sentry": 0.0,   # Free tier (or 26.0 Team)
        "other_gcp": 3.0,  # Artifact Registry, Cloud Build, logging
    },
}

_CACHED_RATE_CARD: Optional[Dict[str, Any]] = None
_RATE_CARD_LOCK = threading.Lock()


def invalidate_rate_card_cache() -> None:
    """Clear the in-memory cached rate card so the next read fetches from the DB."""
    global _CACHED_RATE_CARD
    with _RATE_CARD_LOCK:
        _CACHED_RATE_CARD = None


def apply_env_overrides(card: Dict[str, Any]) -> Dict[str, Any]:
    """Apply environment variable overrides (e.g. USD_TO_INR, GEMINI_PRICE_IN_PER_M)."""
    c = copy.deepcopy(card)

    env_fx = os.getenv("USD_TO_INR")
    if env_fx:
        try:
            c["usd_to_inr"] = float(env_fx)
        except ValueError:
            pass

    env_in = os.getenv("GEMINI_PRICE_IN_PER_M")
    env_out = os.getenv("GEMINI_PRICE_OUT_PER_M")
    env_bin = os.getenv("GEMINI_PRICE_BATCH_IN_PER_M")
    env_bout = os.getenv("GEMINI_PRICE_BATCH_OUT_PER_M")

    for m in c.get("models", {}).values():
        if env_in:
            try:
                m["input_per_m"] = float(env_in)
            except ValueError:
                pass
        if env_out:
            try:
                m["output_per_m"] = float(env_out)
            except ValueError:
                pass
        if env_bin:
            try:
                m["batch_input_per_m"] = float(env_bin)
            except ValueError:
                pass
        if env_bout:
            try:
                m["batch_output_per_m"] = float(env_bout)
            except ValueError:
                pass

    return c


def get_active_rate_card(db: Optional[Session] = None) -> Dict[str, Any]:
    """Return the active rate card dict, checking in-memory cache, then DB, then seed default."""
    global _CACHED_RATE_CARD
    with _RATE_CARD_LOCK:
        if _CACHED_RATE_CARD is not None:
            return _CACHED_RATE_CARD

    card_data = None
    if db is not None:
        try:
            from backend.app.models.cost import CostRateCard
            row = (
                db.query(CostRateCard)
                .filter(CostRateCard.status == "active")
                .order_by(CostRateCard.version.desc())
                .first()
            )
            if row and isinstance(row.rates, dict):
                card_data = copy.deepcopy(row.rates)
                card_data["version"] = row.version
        except Exception as e:
            logger.debug("Could not query CostRateCard from DB: %s", e)

    if card_data is None:
        # Fallback to seed v1
        card_data = copy.deepcopy(DEFAULT_RATE_CARD_V1)

    card_with_overrides = apply_env_overrides(card_data)

    with _RATE_CARD_LOCK:
        if db is not None:
            _CACHED_RATE_CARD = card_with_overrides

    return card_with_overrides


def seed_rate_card_v1_if_empty(db: Session) -> bool:
    """Insert CostRateCard v1 if no rate cards exist in the database. Returns True if inserted."""
    try:
        from backend.app.models.cost import CostRateCard
        existing = db.query(CostRateCard.version).first()
        if existing is not None:
            return False
        row = CostRateCard(
            version=1,
            effective_from=datetime(2026, 11, 23, 0, 0, tzinfo=timezone.utc),
            status="active",
            rates=DEFAULT_RATE_CARD_V1,
            note="Verified baseline seeded from docs/COST_AUDIT_2026-11.md",
        )
        db.add(row)
        db.commit()
        invalidate_rate_card_cache()
        return True
    except Exception as e:
        db.rollback()
        logger.warning("Could not seed CostRateCard v1: %s", e)
        return False


def calculate_event_cost(
    category: str,
    operation: str,
    units: Dict[str, Any],
    model: Optional[str] = None,
    rate_card: Optional[Dict[str, Any]] = None,
    fx_rate: Optional[float] = None,
    is_batch: bool = False,
) -> Tuple[float, float, Dict[str, Any], int, float]:
    """Calculate USD and INR costs based on active rate card.

    Returns:
        (cost_usd, cost_inr, rates_used, rate_card_version, fx_rate)
    """
    card = rate_card or get_active_rate_card()
    active_fx = float(fx_rate or card.get("usd_to_inr", 95.0))
    version = int(card.get("version", 1))
    units = units or {}

    cost_usd = 0.0
    rates_used: Dict[str, Any] = {}

    if category == "gemini":
        models_cfg = card.get("models", {})
        m_name = (model or "").lower().strip()
        m_rates = models_cfg.get(m_name) or models_cfg.get("default") or {}

        if is_batch:
            in_rate = float(m_rates.get("batch_input_per_m", 0.15))
            out_rate = float(m_rates.get("batch_output_per_m", 1.25))
        else:
            in_rate = float(m_rates.get("input_per_m", 0.30))
            out_rate = float(m_rates.get("output_per_m", 2.50))
        cached_rate = float(m_rates.get("cached_input_per_m", 0.03))

        rates_used = {
            "input_per_m": in_rate,
            "output_per_m": out_rate,
            "cached_input_per_m": cached_rate,
            "is_batch": is_batch,
        }

        tokens_in = int(units.get("tokens_in", 0) or 0)
        cached_tokens = min(int(units.get("cached_tokens", 0) or 0), tokens_in)
        fresh_in = max(0, tokens_in - cached_tokens)
        tokens_out = int(units.get("tokens_out", 0) or 0)
        thinking_tokens = int(units.get("thinking_tokens", 0) or 0)
        total_out = tokens_out + thinking_tokens

        cost_usd = (
            (fresh_in / 1e6 * in_rate)
            + (cached_tokens / 1e6 * cached_rate)
            + (total_out / 1e6 * out_rate)
        )

    elif category == "cloud_run_job":
        cr_cfg = card.get("cloud_run", {})
        vcpu_rate = float(cr_cfg.get("vcpu_active_per_s", 0.00003360))
        gib_rate = float(cr_cfg.get("gib_active_per_s", 0.00000350))
        rates_used = {
            "vcpu_active_per_s": vcpu_rate,
            "gib_active_per_s": gib_rate,
        }

        duration_s = float(units.get("duration_s", 0.0) or 0.0)
        vcpu = float(units.get("vcpu", 2.0) or 2.0)
        gib = float(units.get("gib", 8.0) or 8.0)

        cost_usd = duration_s * (vcpu * vcpu_rate + gib * gib_rate)

    elif category == "cloud_run_service":
        cr_cfg = card.get("cloud_run", {})
        idle_rate = float(cr_cfg.get("idle_min_instance_per_unit_s", 0.00000350))
        vcpu_rate = float(cr_cfg.get("vcpu_active_per_s", 0.00003360))
        gib_rate = float(cr_cfg.get("gib_active_per_s", 0.00000350))
        rates_used = {
            "idle_min_instance_per_unit_s": idle_rate,
            "vcpu_active_per_s": vcpu_rate,
            "gib_active_per_s": gib_rate,
        }

        duration_s = float(units.get("duration_s", 86400.0) or 86400.0)
        vcpu = float(units.get("vcpu", 2.0) or 2.0)
        gib = float(units.get("gib", 4.0) or 4.0)
        min_instances = int(units.get("min_instances", 1) or 1)

        # Idle warm instances cost
        idle_units = (vcpu + gib) * min_instances
        idle_cost = duration_s * idle_units * idle_rate

        # Active time cost if any
        active_s = float(units.get("active_seconds", 0.0) or 0.0)
        active_cost = active_s * (vcpu * vcpu_rate + gib * gib_rate)

        cost_usd = idle_cost + active_cost

    elif category == "r2":
        r2_cfg = card.get("r2", {})
        class_a_rate = float(r2_cfg.get("class_a_per_m", 4.50))
        storage_rate = float(r2_cfg.get("storage_per_gb_month", 0.015))
        rates_used = {
            "class_a_per_m": class_a_rate,
            "storage_per_gb_month": storage_rate,
        }

        if operation == "r2_put":
            objects = int(units.get("objects", 0) or 0)
            cost_usd = objects / 1e6 * class_a_rate
        elif operation == "r2_storage":
            gb_months = float(units.get("gb_months", 0.0) or 0.0)
            cost_usd = gb_months * storage_rate
        else:
            objects = int(units.get("objects", 0) or 0)
            gb_months = float(units.get("gb_months", 0.0) or 0.0)
            cost_usd = (objects / 1e6 * class_a_rate) + (gb_months * storage_rate)

    elif category == "neon":
        neon_cfg = card.get("neon", {})
        cu_rate = float(neon_cfg.get("cu_hour", 0.106))
        storage_rate = float(neon_cfg.get("storage_per_gb_month", 0.35))
        rates_used = {
            "cu_hour": cu_rate,
            "storage_per_gb_month": storage_rate,
        }

        cu_hours = float(units.get("cu_hours", 0.0) or 0.0)
        gb_months = float(units.get("gb_months", 0.0) or 0.0)
        cost_usd = (cu_hours * cu_rate) + (gb_months * storage_rate)

    elif category in ("vercel", "other"):
        fixed_cfg = card.get("fixed_monthly_usd", {})
        cat_key = "vercel" if category == "vercel" else "other_gcp"
        monthly_rate = float(fixed_cfg.get(cat_key, 0.0))
        rates_used = {f"{cat_key}_monthly_usd": monthly_rate}

        # Daily share or provided USD
        if "cost_usd" in units:
            cost_usd = float(units["cost_usd"])
        elif "days" in units:
            days = float(units["days"])
            cost_usd = monthly_rate / 30.0 * days
        else:
            cost_usd = monthly_rate / 30.0

    elif "cost_usd" in units:
        cost_usd = float(units["cost_usd"])

    cost_inr = round(cost_usd * active_fx, 4)
    cost_usd = round(cost_usd, 6)

    return cost_usd, cost_inr, rates_used, version, active_fx
