"""Cost tracking API router.

Endpoints:
- POST /costs/refresh: Daily refresh of FX rates, shared cost accruals, and price watch.
  Guarded by X-Cron-Secret header matching COST_CRON_SECRET.
"""
from __future__ import annotations

from datetime import datetime
import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Query, status

from backend.app.config import settings
from backend.app.services.costs.daily_refresh import run_daily_refresh

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/costs", tags=["costs"])


def _verify_cron_secret(x_cron_secret: Optional[str] = Header(None, alias="X-Cron-Secret")) -> None:
    """Validate X-Cron-Secret header against configured COST_CRON_SECRET."""
    expected = (
        getattr(settings, "COST_CRON_SECRET", None)
        or os.getenv("COST_CRON_SECRET")
        or ""
    ).strip()

    # If a secret is configured, strictly enforce matching
    if expected:
        if not x_cron_secret or x_cron_secret.strip() != expected:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: invalid or missing X-Cron-Secret header",
            )
    else:
        # If running in Cloud Run without secret set, warn loudly
        if os.getenv("K_SERVICE"):
            logger.warning("COST_CRON_SECRET is not configured in production environment!")


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
        return summary
    except Exception as e:
        logger.exception("Daily cost refresh failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Daily cost refresh failed: {e}",
        )
