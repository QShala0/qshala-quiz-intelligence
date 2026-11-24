"""Cost ledger aggregation, invoice recording, and reporting service.

Rules:
- All GET queries run at most 3 SQL statements each, using SQL passes with GROUP BY.
- Never load all rows into Python to sum them.
- No double counting: headline = invoice if present, else sum of measured/accrued events.
- All GET endpoints cached for 30 seconds.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
import json
import logging
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import and_, case, desc, func, or_
from sqlalchemy.orm import Session

from backend.app.models.cost import CostEvent, CostFxRate, CostRateCard, CostRefreshRun
from backend.app.models.document import Document
from backend.app.services.costs.rate_card import get_active_rate_card

logger = logging.getLogger(__name__)

# 30-second in-memory cache for GET endpoints
_CACHE: Dict[str, Tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()
CACHE_TTL_SECONDS = 30.0


def cached_30s(key_prefix: str):
    """Decorator to cache query results in memory for 30 seconds."""
    def decorator(fn: Callable):
        def wrapper(*args, **kwargs):
            # Build cache key from prefix and stringified arguments (excluding db session)
            arg_strs = [str(a) for a in args if not isinstance(a, Session)]
            kwarg_strs = [f"{k}={v}" for k, v in sorted(kwargs.items()) if not isinstance(v, Session)]
            cache_key = f"{key_prefix}:{':'.join(arg_strs)}:{':'.join(kwarg_strs)}"

            now = time.monotonic()
            with _CACHE_LOCK:
                if cache_key in _CACHE:
                    ts, val = _CACHE[cache_key]
                    if now - ts < CACHE_TTL_SECONDS:
                        return val

            result = fn(*args, **kwargs)

            with _CACHE_LOCK:
                _CACHE[cache_key] = (now, result)
            return result
        return wrapper
    return decorator


def invalidate_costs_cache() -> None:
    """Clear all cached cost aggregation responses."""
    with _CACHE_LOCK:
        _CACHE.clear()


def resolve_no_double_counting(
    measured_or_accrued: float,
    invoice_billed: Optional[float],
) -> Tuple[float, float, float, float]:
    """Calculate headline, measured, billed, and gap per (category, period).

    Rule:
    - If invoice exists: headline = invoice_billed
    - Else: headline = measured_or_accrued
    - gap = billed - measured (when billed is present, else 0.0)

    Returns:
        (headline, measured, billed, gap)
    """
    measured = round(float(measured_or_accrued or 0.0), 2)
    if invoice_billed is not None and invoice_billed > 0.0:
        billed = round(float(invoice_billed), 2)
        headline = billed
        gap = round(billed - measured, 2)
    else:
        billed = 0.0
        headline = measured
        gap = 0.0
    return headline, measured, billed, gap


@cached_30s("costs:simple")
def get_simple_costs(db: Session, period: Optional[str] = None) -> Dict[str, Any]:
    """Tiny summary for the dashboard panel.

    Returns 6 numbers: Gemini, database, storage, compute, other, and total (in INR).
    Runs 1-2 SQL queries with GROUP BY.
    """
    current_period = period or datetime.now(timezone.utc).strftime("%Y-%m")

    # 1. SQL pass: aggregate non-invoice events for the period by category
    ev_query = (
        db.query(
            CostEvent.category,
            func.sum(CostEvent.cost_inr).label("total_inr"),
            func.max(case((CostEvent.source == "estimated", 1), else_=0)).label("has_estimated"),
        )
        .filter(
            CostEvent.period == current_period,
            CostEvent.operation != "invoice",
        )
        .group_by(CostEvent.category)
    )
    ev_rows = ev_query.all()

    # 2. SQL pass: aggregate active invoices for the period
    inv_query = (
        db.query(
            CostEvent.category,
            func.sum(CostEvent.cost_inr).label("billed_inr"),
        )
        .filter(
            CostEvent.period == current_period,
            CostEvent.operation == "invoice",
            or_(CostEvent.note == None, ~CostEvent.note.like("%superseded%")),
        )
        .group_by(CostEvent.category)
    )
    inv_rows = inv_query.all()
    invoices_by_cat = {r.category: float(r.billed_inr or 0.0) for r in inv_rows}

    # Category buckets
    bucket_sums: Dict[str, float] = {
        "gemini": 0.0,
        "database": 0.0,
        "storage": 0.0,
        "compute": 0.0,
        "other": 0.0,
    }
    bucket_approx: Dict[str, bool] = {k: False for k in bucket_sums}

    for cat, total_inr, has_est in ev_rows:
        val = float(total_inr or 0.0)
        is_approx = bool(has_est)
        if cat == "gemini":
            bucket_sums["gemini"] += val
            bucket_approx["gemini"] = bucket_approx["gemini"] or is_approx
        elif cat == "neon":
            bucket_sums["database"] += val
            bucket_approx["database"] = bucket_approx["database"] or is_approx
        elif cat == "r2":
            bucket_sums["storage"] += val
            bucket_approx["storage"] = bucket_approx["storage"] or is_approx
        elif cat in ("cloud_run_service", "cloud_run_job"):
            bucket_sums["compute"] += val
            bucket_approx["compute"] = bucket_approx["compute"] or is_approx
        else:
            bucket_sums["other"] += val
            bucket_approx["other"] = bucket_approx["other"] or is_approx

    # Apply invoice resolution
    for cat, billed in invoices_by_cat.items():
        if cat == "gemini":
            bucket_sums["gemini"], _, _, _ = resolve_no_double_counting(bucket_sums["gemini"], billed)
            bucket_approx["gemini"] = False
        elif cat == "neon":
            bucket_sums["database"], _, _, _ = resolve_no_double_counting(bucket_sums["database"], billed)
            bucket_approx["database"] = False
        elif cat == "r2":
            bucket_sums["storage"], _, _, _ = resolve_no_double_counting(bucket_sums["storage"], billed)
            bucket_approx["storage"] = False
        elif cat in ("cloud_run_service", "cloud_run_job", "compute"):
            bucket_sums["compute"], _, _, _ = resolve_no_double_counting(bucket_sums["compute"], billed)
            bucket_approx["compute"] = False
        else:
            bucket_sums["other"], _, _, _ = resolve_no_double_counting(bucket_sums["other"], billed)
            bucket_approx["other"] = False

    total_inr = round(sum(bucket_sums.values()), 2)
    lines = [
        {"key": "gemini", "label": "Gemini AI", "inr": round(bucket_sums["gemini"], 2), "approx": bucket_approx["gemini"]},
        {"key": "database", "label": "Neon Database", "inr": round(bucket_sums["database"], 2), "approx": bucket_approx["database"]},
        {"key": "storage", "label": "Cloudflare R2 Storage", "inr": round(bucket_sums["storage"], 2), "approx": bucket_approx["storage"]},
        {"key": "compute", "label": "Cloud Run Compute", "inr": round(bucket_sums["compute"], 2), "approx": bucket_approx["compute"]},
        {"key": "other", "label": "Vercel & GCP Other", "inr": round(bucket_sums["other"], 2), "approx": bucket_approx["other"]},
    ]

    # Meta information
    last_refresh = db.query(CostRefreshRun).order_by(CostRefreshRun.finished_at.desc()).first()
    last_refresh_at = last_refresh.finished_at.isoformat() if last_refresh and last_refresh.finished_at else None

    last_fx = db.query(CostFxRate).order_by(CostFxRate.date.desc()).first()
    fx_stale = "stale" in (last_fx.source if last_fx else "") or "fallback" in (last_fx.source if last_fx else "")

    price_notice = None
    proposed_rc = db.query(CostRateCard).filter(CostRateCard.status == "proposed").first()
    if proposed_rc:
        price_notice = f"Gemini prices changed: review rate card v{proposed_rc.version}"

    return {
        "period": current_period,
        "gemini": round(bucket_sums["gemini"], 2),
        "database": round(bucket_sums["database"], 2),
        "storage": round(bucket_sums["storage"], 2),
        "compute": round(bucket_sums["compute"], 2),
        "other": round(bucket_sums["other"], 2),
        "total": total_inr,
        "total_inr": total_inr,
        "currency": "INR",
        "lines": lines,
        "last_refresh_at": last_refresh_at,
        "fx_stale": fx_stale,
        "price_notice": price_notice,
    }


@cached_30s("costs:summary")
def get_summary_costs(db: Session) -> Dict[str, Any]:
    """Detailed summary comparing MTD, last month, and all time.

    Splits Gemini into ingestion (vision/tagging/embedding) and quiz (intent/curator/interpreter).
    """
    now = datetime.now(timezone.utc)
    curr_period = now.strftime("%Y-%m")
    # Previous month calculation
    first_of_curr = date(now.year, now.month, 1)
    last_month_end = first_of_curr - timedelta(days=1)
    prev_period = last_month_end.strftime("%Y-%m")

    # 1. SQL pass: aggregate non-invoice events by (period, category, operation, source)
    agg_rows = (
        db.query(
            CostEvent.period,
            CostEvent.category,
            CostEvent.operation,
            CostEvent.source,
            func.sum(CostEvent.cost_inr).label("sum_inr"),
            func.sum(CostEvent.cost_usd).label("sum_usd"),
        )
        .filter(CostEvent.operation != "invoice")
        .group_by(CostEvent.period, CostEvent.category, CostEvent.operation, CostEvent.source)
        .all()
    )

    # 2. SQL pass: aggregate active invoices by (period, category)
    inv_rows = (
        db.query(
            CostEvent.period,
            CostEvent.category,
            func.sum(CostEvent.cost_inr).label("sum_inr"),
            func.sum(CostEvent.cost_usd).label("sum_usd"),
        )
        .filter(
            CostEvent.operation == "invoice",
            or_(CostEvent.note == None, ~CostEvent.note.like("%superseded%")),
        )
        .group_by(CostEvent.period, CostEvent.category)
        .all()
    )
    invoices = {}
    for r in inv_rows:
        invoices[(r.period, r.category)] = float(r.sum_inr or 0.0)

    # Structure periods
    def _make_period_structure():
        return {
            "headline_inr": 0.0,
            "measured_inr": 0.0,
            "billed_inr": 0.0,
            "gap_inr": 0.0,
            "categories": {},
            "gemini_breakdown": {
                "ingestion": {"vision": 0.0, "tagging": 0.0, "embedding": 0.0, "total": 0.0},
                "quiz": {"intent": 0.0, "curator": 0.0, "interpreter": 0.0, "total": 0.0},
                "other": 0.0,
                "total": 0.0,
            },
            "sources": {"measured": 0.0, "accrued": 0.0, "billed": 0.0, "estimated": 0.0},
        }

    periods_data = {
        "current_month": _make_period_structure(),
        "last_month": _make_period_structure(),
        "all_time": _make_period_structure(),
    }

    for p_row in agg_rows:
        period_str = p_row.period
        cat = p_row.category
        op = p_row.operation
        src = p_row.source or "measured"
        inr = float(p_row.sum_inr or 0.0)

        targets = ["all_time"]
        if period_str == curr_period:
            targets.append("current_month")
        elif period_str == prev_period:
            targets.append("last_month")

        for t in targets:
            struct = periods_data[t]
            struct["categories"][cat] = struct["categories"].get(cat, 0.0) + inr
            struct["sources"][src] = struct["sources"].get(src, 0.0) + inr

            # Gemini breakdown
            if cat == "gemini":
                gb = struct["gemini_breakdown"]
                gb["total"] += inr
                if "vision" in op:
                    gb["ingestion"]["vision"] += inr
                    gb["ingestion"]["total"] += inr
                elif "tag" in op:
                    gb["ingestion"]["tagging"] += inr
                    gb["ingestion"]["total"] += inr
                elif "embed" in op:
                    gb["ingestion"]["embedding"] += inr
                    gb["ingestion"]["total"] += inr
                elif "curator" in op:
                    gb["quiz"]["curator"] += inr
                    gb["quiz"]["total"] += inr
                elif "intent" in op:
                    gb["quiz"]["intent"] += inr
                    gb["quiz"]["total"] += inr
                elif "interpreter" in op:
                    gb["quiz"]["interpreter"] += inr
                    gb["quiz"]["total"] += inr
                else:
                    gb["other"] += inr

    # Apply invoices and resolve headline / gap
    for t_key, struct in periods_data.items():
        total_headline = 0.0
        total_measured = sum(struct["categories"].values())
        total_billed = 0.0

        target_period = curr_period if t_key == "current_month" else (prev_period if t_key == "last_month" else None)

        for cat, measured in struct["categories"].items():
            if target_period:
                billed = invoices.get((target_period, cat), 0.0)
            else:
                billed = sum(v for (p, c), v in invoices.items() if c == cat)

            h, m, b, _ = resolve_no_double_counting(measured, billed if billed > 0.0 else None)
            struct["categories"][cat] = round(h, 2)
            total_headline += h
            total_billed += b

        struct["headline_inr"] = round(total_headline, 2)
        struct["measured_inr"] = round(total_measured, 2)
        struct["billed_inr"] = round(total_billed, 2)
        struct["gap_inr"] = round(total_billed - total_measured, 2) if total_billed > 0.0 else 0.0

    # Rate card & health
    active_rc = get_active_rate_card()
    last_refresh = db.query(CostRefreshRun).order_by(CostRefreshRun.finished_at.desc()).first()

    return {
        "as_of": now.isoformat(),
        "rate_card_version": int(active_rc.get("version", 1)),
        "current_fx_rate": float(active_rc.get("usd_to_inr", 95.0)),
        "periods": periods_data,
        "last_refresh_at": last_refresh.finished_at.isoformat() if last_refresh and last_refresh.finished_at else None,
    }


@cached_30s("costs:timeline")
def get_timeline_costs(db: Session, granularity: str = "month") -> List[Dict[str, Any]]:
    """Historical timeline of costs grouped by period and category.

    Single SQL query with GROUP BY period, category.
    """
    rows = (
        db.query(
            CostEvent.period,
            CostEvent.category,
            func.sum(CostEvent.cost_inr).label("inr"),
            func.sum(CostEvent.cost_usd).label("usd"),
        )
        .filter(CostEvent.operation != "invoice")
        .group_by(CostEvent.period, CostEvent.category)
        .order_by(CostEvent.period.asc())
        .all()
    )

    by_period: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        p = r.period or "unknown"
        if p not in by_period:
            by_period[p] = {"period": p, "total_inr": 0.0, "total_usd": 0.0, "categories": {}}
        c_inr = round(float(r.inr or 0.0), 2)
        c_usd = round(float(r.usd or 0.0), 4)
        by_period[p]["categories"][r.category] = c_inr
        by_period[p]["total_inr"] = round(by_period[p]["total_inr"] + c_inr, 2)
        by_period[p]["total_usd"] = round(by_period[p]["total_usd"] + c_usd, 4)

    return list(by_period.values())


@cached_30s("costs:documents")
def get_documents_costs(
    db: Session,
    limit: int = 50,
    offset: int = 0,
    sort: str = "cost_desc",
) -> Dict[str, Any]:
    """Per-deck breakdown of costs (Gemini by stage, Job compute, R2 put, total).

    Runs one SQL GROUP BY query joined with documents table.
    """
    # Group cost events by document_id and operation/category
    agg_sub = (
        db.query(
            CostEvent.document_id,
            func.sum(case((CostEvent.operation.like("%vision%"), CostEvent.cost_inr), else_=0.0)).label("vision_inr"),
            func.sum(case((CostEvent.operation.like("%tag%"), CostEvent.cost_inr), else_=0.0)).label("tagging_inr"),
            func.sum(case((CostEvent.operation.like("%embed%"), CostEvent.cost_inr), else_=0.0)).label("embed_inr"),
            func.sum(case((CostEvent.category == "cloud_run_job", CostEvent.cost_inr), else_=0.0)).label("job_compute_inr"),
            func.sum(case((CostEvent.category == "r2", CostEvent.cost_inr), else_=0.0)).label("r2_inr"),
            func.sum(CostEvent.cost_inr).label("total_inr"),
            func.count(CostEvent.id).label("event_count"),
            func.min(CostEvent.occurred_at).label("first_seen"),
        )
        .filter(CostEvent.document_id != None)
        .group_by(CostEvent.document_id)
        .subquery()
    )

    query = (
        db.query(
            agg_sub.c.document_id,
            agg_sub.c.vision_inr,
            agg_sub.c.tagging_inr,
            agg_sub.c.embed_inr,
            agg_sub.c.job_compute_inr,
            agg_sub.c.r2_inr,
            agg_sub.c.total_inr,
            agg_sub.c.event_count,
            agg_sub.c.first_seen,
            Document.title,
            Document.filename,
            Document.question_count,
        )
        .outerjoin(Document, Document.id == agg_sub.c.document_id)
    )

    if sort == "date_desc":
        query = query.order_by(agg_sub.c.first_seen.desc())
    elif sort == "title_asc":
        query = query.order_by(Document.title.asc())
    else:
        query = query.order_by(agg_sub.c.total_inr.desc())

    total_count = query.count()
    rows = query.offset(offset).limit(limit).all()

    items = []
    for r in rows:
        q_count = r.question_count or 0
        tot = round(float(r.total_inr or 0.0), 2)
        cost_per_q = round(tot / q_count, 2) if q_count > 0 else 0.0
        items.append({
            "document_id": r.document_id,
            "title": r.title or r.filename or "Deleted deck",
            "question_count": q_count,
            "vision_inr": round(float(r.vision_inr or 0.0), 2),
            "tagging_inr": round(float(r.tagging_inr or 0.0), 2),
            "embed_inr": round(float(r.embed_inr or 0.0), 2),
            "job_compute_inr": round(float(r.job_compute_inr or 0.0), 2),
            "r2_inr": round(float(r.r2_inr or 0.0), 2),
            "total_inr": tot,
            "cost_per_question_inr": cost_per_q,
            "first_seen": r.first_seen.isoformat() if r.first_seen else None,
        })

    return {
        "total": total_count,
        "items": items,
        "limit": limit,
        "offset": offset,
    }


@cached_30s("costs:quizzes")
def get_quizzes_costs(
    db: Session,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Quiz prompt session cost aggregation."""
    rows = (
        db.query(
            CostEvent.quiz_id,
            func.sum(CostEvent.cost_inr).label("total_inr"),
            func.count(CostEvent.id).label("call_count"),
            func.min(CostEvent.occurred_at).label("started_at"),
            func.max(CostEvent.occurred_at).label("finished_at"),
        )
        .filter(CostEvent.quiz_id != None)
        .group_by(CostEvent.quiz_id)
        .order_by(desc("started_at"))
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = []
    total_inr_sum = 0.0
    for r in rows:
        inr = round(float(r.total_inr or 0.0), 4)
        total_inr_sum += inr
        items.append({
            "quiz_id": r.quiz_id,
            "total_inr": inr,
            "call_count": r.call_count,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        })

    avg_per_prompt = round(total_inr_sum / len(items), 4) if items else 0.0

    return {
        "items": items,
        "average_cost_per_quiz_inr": avg_per_prompt,
        "count": len(items),
    }


def get_events_ledger(
    db: Session,
    category: Optional[str] = None,
    document_id: Optional[str] = None,
    quiz_id: Optional[str] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Paginated raw cost ledger rows for auditing."""
    q = db.query(CostEvent)
    if category:
        q = q.filter(CostEvent.category == category)
    if document_id:
        q = q.filter(CostEvent.document_id == document_id)
    if quiz_id:
        q = q.filter(CostEvent.quiz_id == quiz_id)
    if from_date:
        q = q.filter(CostEvent.occurred_at >= from_date)
    if to_date:
        q = q.filter(CostEvent.occurred_at <= to_date)

    total = q.count()
    rows = q.order_by(CostEvent.occurred_at.desc()).offset(offset).limit(limit).all()

    items = []
    for r in rows:
        items.append({
            "id": r.id,
            "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
            "category": r.category,
            "operation": r.operation,
            "model": r.model,
            "document_id": r.document_id,
            "quiz_id": r.quiz_id,
            "job_id": r.job_id,
            "units": r.units,
            "cost_usd": float(r.cost_usd) if r.cost_usd is not None else 0.0,
            "cost_inr": float(r.cost_inr) if r.cost_inr is not None else 0.0,
            "fx_rate": float(r.fx_rate) if r.fx_rate is not None else 95.0,
            "source": r.source,
            "dedupe_key": r.dedupe_key,
            "note": r.note,
        })

    return {
        "total": total,
        "items": items,
        "limit": limit,
        "offset": offset,
    }


def record_invoice_entry(
    db: Session,
    category: str,
    period: str,
    amount: float,
    currency: str = "INR",
    gst: float = 0.0,
    note: Optional[str] = None,
) -> CostEvent:
    """Record an owner-typed invoice figure.

    Updates earlier entry for the same (category, period) by superseding it in units,
    never by deleting. Invalidates cached GET responses.
    """
    currency_upper = (currency or "INR").upper().strip()
    active_rc = get_active_rate_card()
    active_fx = float(active_rc.get("usd_to_inr", 95.0))

    if currency_upper == "USD":
        cost_usd = float(amount)
        cost_inr = cost_usd * active_fx + float(gst)
    else:
        cost_inr = float(amount) + float(gst)
        cost_usd = cost_inr / active_fx

    # 1. Check if an active invoice for (category, period) already exists
    existing = (
        db.query(CostEvent)
        .filter(
            CostEvent.category == category,
            CostEvent.period == period,
            CostEvent.operation == "invoice",
            or_(CostEvent.note == None, ~CostEvent.note.like("%superseded%")),
        )
        .all()
    )

    new_id = str(uuid.uuid4())
    for old in existing:
        old_units = dict(old.units or {})
        old_units["superseded"] = True
        old_units["superseded_by"] = new_id
        old.units = old_units
        old.note = f"{old.note or ''} [superseded by {new_id[:8]}]".strip()
        # Rename dedupe_key so new row can use invoice:<category>:<period>
        old.dedupe_key = f"invoice:{category}:{period}:superseded:{old.id[:8]}"

    # 2. Insert new invoice row
    new_event = CostEvent(
        id=new_id,
        occurred_at=datetime.now(timezone.utc),
        category=category,
        operation="invoice",
        units={"amount": amount, "currency": currency_upper, "gst": gst},
        cost_usd=round(cost_usd, 4),
        cost_inr=round(cost_inr, 2),
        fx_rate=round(active_fx, 4),
        source="billed",
        period=period,
        dedupe_key=f"invoice:{category}:{period}",
        note=note or f"Owner invoice for {category} ({period})",
    )
    db.add(new_event)
    db.commit()

    invalidate_costs_cache()
    return new_event
