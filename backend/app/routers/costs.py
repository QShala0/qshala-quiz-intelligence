"""Cost tracking API router.

Endpoints:
- GET /costs/simple: Tiny 6-number summary for dashboard panel (Gemini, database, storage, compute, other, total).
- GET /costs/summary: MTD, last month, and all-time breakdown with Gemini stages and invoice gap calculation.
- GET /costs/timeline: Historical spend by period and category.
- GET /costs/documents: Per-deck cost breakdown (vision, tagging, embedding, job compute, R2, total, INR/q).
- GET /costs/quizzes: Quiz prompt session cost breakdown and average cost per quiz.
- GET /costs/events: Raw paginated ledger events for audit.
- POST /costs/invoices: Add owner invoice with non-destructive correction logic.
- GET /costs/rate-card: View active and historical rate cards.
- PUT /costs/rate-card: Update active rate card version.
- POST /costs/refresh: Daily refresh of FX rates, shared cost accruals, and price watch (guarded by X-Cron-Secret).
- GET /costs/refresh-status: Health and last refresh status.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import get_db
from backend.app.models.cost import CostRateCard, CostRefreshRun
from backend.app.services.costs.aggregation import (
    get_documents_costs,
    get_events_ledger,
    get_quizzes_costs,
    get_simple_costs,
    get_summary_costs,
    get_timeline_costs,
    invalidate_costs_cache,
    record_invoice_entry,
)
from backend.app.services.costs.daily_refresh import run_daily_refresh
from backend.app.services.costs.rate_card import (
    get_active_rate_card,
    invalidate_rate_card_cache,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/costs", tags=["costs"])


class InvoiceCreateRequest(BaseModel):
    category: str = Field(..., description="Category: gemini, neon, r2, cloud_run_service, vercel, other")
    period: str = Field(..., description="Billing period in YYYY-MM format")
    amount: float = Field(..., description="Invoice total amount")
    currency: str = Field("INR", description="Currency (USD or INR)")
    gst: float = Field(0.0, description="GST or tax included in INR")
    note: Optional[str] = Field(None, description="Optional note or reference")


class RateCardUpdateRequest(BaseModel):
    rates: Dict[str, Any] = Field(..., description="New rate card configuration dictionary")
    effective_from: Optional[datetime] = Field(None, description="Effective start timestamp")


def _verify_cron_secret(x_cron_secret: Optional[str] = Header(None, alias="X-Cron-Secret")) -> None:
    """Validate X-Cron-Secret header against configured COST_CRON_SECRET."""
    expected = (
        getattr(settings, "COST_CRON_SECRET", None)
        or os.getenv("COST_CRON_SECRET")
        or ""
    ).strip()

    if expected:
        if not x_cron_secret or x_cron_secret.strip() != expected:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: invalid or missing X-Cron-Secret header",
            )
    else:
        if os.getenv("K_SERVICE"):
            logger.warning("COST_CRON_SECRET is not configured in production environment!")


@router.get("/simple")
def get_costs_simple(
    period: Optional[str] = Query(None, description="Period in YYYY-MM format, or default to current month"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Tiny 6-line summary for the dashboard panel. Cached 30s."""
    return get_simple_costs(db, period=period)


@router.get("/summary")
def get_costs_summary(
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Comprehensive MTD, last month, and all-time summary with Gemini stage breakdown. Cached 30s."""
    return get_summary_costs(db)


@router.get("/timeline")
def get_costs_timeline(
    granularity: str = Query("month", description="Timeline granularity: month or day"),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Historical timeline of costs grouped by period and category. Cached 30s."""
    return get_timeline_costs(db, granularity=granularity)


@router.get("/documents")
def get_costs_by_documents(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: str = Query("cost_desc", description="Sort order: cost_desc, date_desc, title_asc"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Per-deck cost breakdown including vision, tagging, embedding, job compute, and R2. Cached 30s."""
    return get_documents_costs(db, limit=limit, offset=offset, sort=sort)


@router.get("/quizzes")
def get_costs_by_quizzes(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Cost breakdown per quiz prompt session, plus overall average per prompt. Cached 30s."""
    return get_quizzes_costs(db, limit=limit, offset=offset)


@router.get("/events")
def get_costs_events(
    category: Optional[str] = Query(None, description="Filter by category"),
    document_id: Optional[str] = Query(None, description="Filter by document id"),
    quiz_id: Optional[str] = Query(None, description="Filter by quiz session id"),
    from_date: Optional[datetime] = Query(None, description="Filter occurred_at >= from_date"),
    to_date: Optional[datetime] = Query(None, description="Filter occurred_at <= to_date"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Paginated raw cost ledger rows for auditing."""
    return get_events_ledger(
        db,
        category=category,
        document_id=document_id,
        quiz_id=quiz_id,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )


@router.post("/invoices")
def create_invoice_entry(
    req: InvoiceCreateRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Record an owner invoice figure.

    Supersedes any previous entry for (category, period) without deleting history.
    """
    ev = record_invoice_entry(
        db,
        category=req.category,
        period=req.period,
        amount=req.amount,
        currency=req.currency,
        gst=req.gst,
        note=req.note,
    )
    return {
        "status": "recorded",
        "id": ev.id,
        "category": ev.category,
        "period": ev.period,
        "cost_inr": float(ev.cost_inr),
        "cost_usd": float(ev.cost_usd),
        "source": ev.source,
        "dedupe_key": ev.dedupe_key,
    }


@router.get("/rate-card")
def get_rate_card(
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Get active rate card and all historical versions."""
    active = get_active_rate_card(db=db)
    all_cards = (
        db.query(CostRateCard)
        .order_by(CostRateCard.version.desc())
        .all()
    )
    history = [
        {
            "version": rc.version,
            "effective_from": rc.effective_from.isoformat() if rc.effective_from else None,
            "status": rc.status,
            "rates": rc.rates,
        }
        for rc in all_cards
    ]
    return {
        "active": active,
        "history": history,
    }


@router.put("/rate-card")
def update_rate_card(
    req: RateCardUpdateRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Create a new rate card version and mark it active.

    Previous rate cards are marked archived. Historical events keep their own recorded rates.
    """
    latest = db.query(CostRateCard).order_by(CostRateCard.version.desc()).first()
    new_version = (latest.version + 1) if latest else 1

    # Mark existing active cards as archived
    db.query(CostRateCard).filter(CostRateCard.status == "active").update({"status": "archived"})

    eff_from = req.effective_from or datetime.now(timezone.utc)
    new_card = CostRateCard(
        version=new_version,
        effective_from=eff_from,
        status="active",
        rates=req.rates,
    )
    db.add(new_card)
    db.commit()

    invalidate_rate_card_cache()
    invalidate_costs_cache()

    return {
        "status": "updated",
        "version": new_version,
        "effective_from": eff_from.isoformat(),
    }


@router.post("/refresh")
def refresh_costs(
    dry_run: bool = Query(False, description="Preview refresh without writing ledger rows"),
    target_date: Optional[str] = Query(None, description="Optional target date in YYYY-MM-DD format"),
    x_cron_secret: Optional[str] = Header(None, alias="X-Cron-Secret"),
) -> Dict[str, Any]:
    """Execute the daily cost refresh run.

    Idempotent: safe to run multiple times for the same date.
    """
    _verify_cron_secret(x_cron_secret)

    parsed_date = None
    if target_date:
        try:
            parsed_date = datetime.strptime(target_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid target_date format. Expected YYYY-MM-DD",
            )

    try:
        summary = run_daily_refresh(for_date=parsed_date, dry_run=dry_run)
        invalidate_costs_cache()
        return summary
    except Exception as e:
        logger.exception("Daily cost refresh failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Daily cost refresh failed: {e}",
        )


@router.get("/refresh-status")
def get_refresh_status(
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Get status of the latest daily refresh run."""
    last_run = (
        db.query(CostRefreshRun)
        .order_by(CostRefreshRun.finished_at.desc())
        .first()
    )
    if not last_run:
        return {
            "status": "never_run",
            "last_refresh_at": None,
        }

    return {
        "id": last_run.id,
        "status": last_run.status,
        "started_at": last_run.started_at.isoformat() if last_run.started_at else None,
        "finished_at": last_run.finished_at.isoformat() if last_run.finished_at else None,
        "notes": last_run.notes,
    }
