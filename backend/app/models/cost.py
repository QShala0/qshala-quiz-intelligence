"""Cost tracking models: append-only cost ledger, rate cards, FX rates, and refresh runs.

Deliberately has no foreign keys to documents or quizzes so history survives deletes,
matching the pattern established by IngestionUsage.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Numeric, Text, JSON, DateTime, Index
from backend.app.database import Base


class CostEvent(Base):
    """Append-only cost ledger event."""
    __tablename__ = "cost_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    occurred_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    category = Column(String(32), nullable=False)
    # gemini | cloud_run_job | cloud_run_service | neon | r2 | vercel | other
    operation = Column(String(32), nullable=False)
    # ingest_vision | ingest_tagging | ingest_embedding | quiz_intent | quiz_curator |
    # quiz_interpreter | search_embedding | generate_other | job_compute | r2_put |
    # r2_storage | fixed_monthly | invoice
    document_id = Column(String(36), nullable=True, index=True)
    quiz_id = Column(String(64), nullable=True)
    job_id = Column(String(64), nullable=True)
    batch_id = Column(String(64), nullable=True)
    model = Column(String(64), nullable=True)
    units = Column(JSON, nullable=False, default=dict)
    # tokens_in, tokens_out, thinking_tokens, cached_tokens, calls, duration_s, vcpu, gib, bytes, objects, gb_months
    rates = Column(JSON, nullable=False, default=dict)
    # snapshot of rates used for calculation
    rate_card_version = Column(Integer, nullable=False, default=1)
    cost_usd = Column(Numeric(12, 6), nullable=False, default=0.0)
    fx_rate = Column(Numeric(10, 4), nullable=False, default=95.0)
    cost_inr = Column(Numeric(14, 4), nullable=False, default=0.0)
    tax_inr = Column(Numeric(14, 4), nullable=False, default=0.0)
    source = Column(String(20), nullable=False, default="measured")
    # measured | estimated | billed | manual
    period = Column(String(7), nullable=True)  # YYYY-MM
    dedupe_key = Column(String(128), nullable=True, unique=True)
    note = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_cost_events_category_occurred", "category", "occurred_at"),
        Index("ix_cost_events_period_category", "period", "category"),
    )


class CostRateCard(Base):
    """Versioned rate cards containing all system unit prices."""
    __tablename__ = "cost_rate_cards"

    version = Column(Integer, primary_key=True)
    effective_from = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    status = Column(String(20), nullable=False, default="active")  # active | proposed | superseded
    rates = Column(JSON, nullable=False)
    note = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class CostFxRate(Base):
    """Daily exchange rates for USD to INR."""
    __tablename__ = "cost_fx_rates"

    date = Column(String(10), primary_key=True)  # YYYY-MM-DD
    usd_to_inr = Column(Numeric(10, 4), nullable=False)
    source = Column(String(50), nullable=False)  # frankfurter | open.er-api | manual | fallback
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class CostRefreshRun(Base):
    """Log of daily scheduled cost refresh executions."""
    __tablename__ = "cost_refresh_runs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    started_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    finished_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, default="running")  # running | success | failed | partial
    notes = Column(Text, nullable=True)
