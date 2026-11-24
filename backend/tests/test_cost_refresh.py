import os
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Safety env vars before backend imports
os.environ["REQUIRE_POSTGRES"] = "0"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from backend.app.database import Base, SessionLocal
from backend.app.models.cost import CostEvent, CostFxRate, CostRateCard, CostRefreshRun
from backend.app.services.costs import daily_refresh
from backend.app.services.costs.rate_card import seed_rate_card_v1_if_empty, invalidate_rate_card_cache
from backend.app.main import app


@pytest.fixture(autouse=True)
def clean_db():
    invalidate_rate_card_cache()
    db = SessionLocal()
    try:
        Base.metadata.create_all(bind=db.get_bind())
        seed_rate_card_v1_if_empty(db)
    finally:
        db.close()
    yield
    invalidate_rate_card_cache()
    db = SessionLocal()
    try:
        for tbl in (CostEvent, CostFxRate, CostRateCard, CostRefreshRun):
            db.query(tbl).delete()
        db.commit()
    finally:
        db.close()


def test_fetch_fx_rate_primary():
    """Primary frankfurter API succeeds."""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"rates": {"INR": 95.42}}
    mock_client.get.return_value = mock_resp

    rate, source, is_stale = daily_refresh.fetch_fx_rate(target_date=date(2026, 11, 24), client=mock_client)
    assert rate == 95.42
    assert source == "frankfurter"
    assert is_stale is False

    # Check persistence
    db = SessionLocal()
    try:
        row = db.query(CostFxRate).filter(CostFxRate.date == "2026-11-24").first()
        assert row is not None
        assert float(row.usd_to_inr) == 95.42
    finally:
        db.close()


def test_fetch_fx_rate_fallback():
    """Primary frankfurter fails; fallback open.er-api succeeds."""
    mock_client = MagicMock()
    mock_resp1 = MagicMock()
    mock_resp1.status_code = 500

    mock_resp2 = MagicMock()
    mock_resp2.status_code = 200
    mock_resp2.json.return_value = {"rates": {"INR": 94.88}}

    mock_client.get.side_effect = [mock_resp1, mock_resp2]

    rate, source, is_stale = daily_refresh.fetch_fx_rate(target_date=date(2026, 11, 23), client=mock_client)
    assert rate == 94.88
    assert source == "open.er-api"
    assert is_stale is False


def test_fetch_fx_rate_stale_fallback():
    """Both network calls fail; DB fallback provides last known rate."""
    db = SessionLocal()
    try:
        db.add(CostFxRate(date="2026-11-20", usd_to_inr=93.50, source="manual"))
        db.commit()
    finally:
        db.close()

    mock_client = MagicMock()
    mock_client.get.side_effect = Exception("Network offline")

    rate, source, is_stale = daily_refresh.fetch_fx_rate(target_date=date(2026, 11, 24), client=mock_client)
    assert rate == 93.50
    assert is_stale is True


def test_accrue_daily_shared_costs_idempotent():
    """Daily accruals record all 5 categories and are strictly idempotent."""
    d = date(2026, 11, 24)
    events1 = daily_refresh.accrue_daily_shared_costs(for_date=d, fx_rate=95.0)
    assert len(events1) == 5

    categories = {ev.category for ev in events1}
    assert categories == {"cloud_run_service", "neon", "vercel", "other", "r2"}

    # Second run for same date must not create duplicate events
    events2 = daily_refresh.accrue_daily_shared_costs(for_date=d, fx_rate=95.0)
    assert len(events2) == 0

    db = SessionLocal()
    try:
        total_accruals = db.query(CostEvent).filter(CostEvent.source == "accrued").count()
        assert total_accruals == 5
    finally:
        db.close()


def test_run_daily_refresh_flow():
    """Full refresh creates refresh log and returns summary."""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"rates": {"INR": 95.10}}
    mock_client.get.return_value = mock_resp

    summary = daily_refresh.run_daily_refresh(
        for_date=date(2026, 11, 24),
        client=mock_client,
    )
    assert summary["status"] == "ok"
    assert summary["fx_rate"] == 95.10
    assert summary["accruals_recorded"] == 5
    assert summary["run_id"] is not None

    db = SessionLocal()
    try:
        run_row = db.query(CostRefreshRun).filter(CostRefreshRun.id == summary["run_id"]).first()
        assert run_row is not None
        assert run_row.status == "success"
        assert run_row.finished_at is not None
    finally:
        db.close()


def test_catchup_missed_days():
    """Catch-up fills missing days since last run."""
    # Seed a run from 3 days ago
    past_date = datetime.now(timezone.utc) - timedelta(days=3)
    db = SessionLocal()
    try:
        db.add(CostRefreshRun(
            started_at=past_date,
            finished_at=past_date,
            status="success",
            notes="past run",
        ))
        db.commit()
    finally:
        db.close()

    daily_refresh._LAST_CATCHUP_CHECK = None  # Reset throttle
    with patch("backend.app.services.costs.daily_refresh.fetch_fx_rate", return_value=(95.0, "mock", False)):
        caught = daily_refresh.catchup_missed_days(max_days=7)
        assert caught >= 3


def test_refresh_api_endpoint():
    """POST /costs/refresh endpoint authentication and execution."""
    client = TestClient(app)

    # 1. Configured secret requires valid header
    with patch("backend.app.routers.costs.settings") as mock_settings:
        mock_settings.COST_CRON_SECRET = "secret123"

        # No header -> 401
        res = client.post("/costs/refresh")
        assert res.status_code == 401

        # Wrong header -> 401
        res = client.post("/costs/refresh", headers={"X-Cron-Secret": "wrong"})
        assert res.status_code == 401

        # Correct header -> 200
        with patch("backend.app.services.costs.daily_refresh.fetch_fx_rate", return_value=(95.2, "mock", False)):
            res = client.post("/costs/refresh", headers={"X-Cron-Secret": "secret123"})
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "ok"
            assert data["fx_rate"] == 95.2

            # Bad target_date format -> 400
            res = client.post("/costs/refresh?target_date=not-a-date", headers={"X-Cron-Secret": "secret123"})
            assert res.status_code == 400
