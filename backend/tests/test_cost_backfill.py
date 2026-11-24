"""Tests for history backfill script (backend/scripts/backfill_cost_events.py).

Tests:
1. Measured events from ingestion_usage with correct rate card v1 pricing.
2. Estimated events for decks without usage rows (tagging + vision + R2).
3. Job compute events from ingestion_jobs.
4. Idempotency (running twice produces zero new events).
5. Dry-run writes 0 rows to DB.
6. Apply writes rows and generates a rollback file.
7. Rollback removes created rows cleanly.
"""
from __future__ import annotations

import json
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.models.cost import CostEvent, CostRateCard
from backend.app.models.document import Document
from backend.app.models.job import IngestionJob
from backend.app.models.qshala_tag import QshalaTag
from backend.app.models.question import Question
from backend.app.models.usage import IngestionUsage
from backend.app.services.costs.rate_card import (
    invalidate_rate_card_cache,
    seed_rate_card_v1_if_empty,
)
from backend.scripts.backfill_cost_events import (
    apply_backfill,
    generate_backfill_events,
    print_monthly_summary,
    rollback_backfill,
)


@pytest.fixture
def test_db():
    """Isolated in-memory SQLite database session for backfill tests."""
    invalidate_rate_card_cache()
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    seed_rate_card_v1_if_empty(db)
    try:
        yield db
    finally:
        db.close()
        engine.dispose()
        invalidate_rate_card_cache()


def test_backfill_from_ingestion_usage(test_db):
    """Verify measured events are extracted from ingestion_usage with correct v1 rates."""
    db = test_db
    doc_id = str(uuid.uuid4())
    now = datetime(2026, 11, 14, 10, 0, 0, tzinfo=timezone.utc)

    # Add ingestion usage rows (vision and tagging)
    iu1 = IngestionUsage(
        id=str(uuid.uuid4()),
        document_id=doc_id,
        stage="vision",
        model="gemini-3.5-flash-lite",
        calls=10,
        tokens_in=11560,
        tokens_out=910,
        thinking_tokens=0,
        cached_tokens=0,
        created_at=now,
    )
    iu2 = IngestionUsage(
        id=str(uuid.uuid4()),
        document_id=doc_id,
        stage="tagging",
        model="gemini-3.5-flash-lite",
        calls=1,
        tokens_in=5688,
        tokens_out=1681,
        thinking_tokens=0,
        cached_tokens=0,
        created_at=now,
    )
    db.add_all([iu1, iu2])
    db.commit()

    events, stats = generate_backfill_events(db)
    assert stats["ingestion_usage_rows"] == 2
    assert len(events) == 2

    # Check vision event
    vis_ev = next(e for e in events if e["operation"] == "ingest_vision")
    assert vis_ev["source"] == "measured"
    assert vis_ev["dedupe_key"] == f"iu:{iu1.id}"
    assert vis_ev["units"]["tokens_in"] == 11560
    assert vis_ev["units"]["tokens_out"] == 910
    # Price check: 11560 * 0.30/1e6 + 910 * 2.50/1e6 = 0.003468 + 0.002275 = 0.005743 USD
    assert abs(vis_ev["cost_usd"] - 0.005743) < 1e-5
    assert vis_ev["cost_inr"] > 0
    assert vis_ev["period"] == "2026-11"

    # Check tagging event
    tag_ev = next(e for e in events if e["operation"] == "ingest_tagging")
    assert tag_ev["source"] == "measured"
    assert tag_ev["dedupe_key"] == f"iu:{iu2.id}"
    assert tag_ev["units"]["tokens_in"] == 5688
    assert tag_ev["units"]["tokens_out"] == 1681
    # Price check: 5688 * 0.30/1e6 + 1681 * 2.50/1e6 = 0.0017064 + 0.0042025 = 0.0059089 USD
    assert abs(tag_ev["cost_usd"] - 0.0059089) < 1e-5


def test_backfill_historical_decks_without_usage_rows(test_db):
    """Verify decks without usage rows get estimated tagging, vision, and R2 events."""
    db = test_db
    doc_id = str(uuid.uuid4())
    oct_date = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)

    doc = Document(
        id=doc_id,
        filename="ancient_history.pptx",
        title="Ancient History Prelims",
        year=2024,
        file_type="pptx",
        storage_path="/storage/ancient_history.pptx",
        file_size_bytes=1048576,  # 1MB
        created_at=oct_date,
    )
    db.add(doc)

    # Add 10 questions to this deck
    for i in range(10):
        q = Question(
            id=str(uuid.uuid4()),
            document_id=doc_id,
            question_text=f"Historical Question {i}?",
            answer=f"Historical Answer {i}",
            topic="History",
        )
        db.add(q)
    db.commit()

    events, stats = generate_backfill_events(db)
    assert stats["historical_deck_tagging"] == 1
    assert stats["historical_deck_vision"] == 1
    assert stats["r2_put_rows"] == 1
    assert len(events) == 3

    # Vision estimate check: 10 Qs * 1.4 = 14 images -> 14 * 1156 = 16184 in, 14 * 91 = 1274 out
    vis = next(e for e in events if e["operation"] == "ingest_vision")
    assert vis["source"] == "estimated"
    assert vis["note"] == "backfill estimate"
    assert vis["units"]["tokens_in"] == 16184
    assert vis["units"]["tokens_out"] == 1274
    assert vis["period"] == "2026-10"

    # Tagging estimate check: 10 Qs * 284 in = 2840 in, 10 * 84 out = 840 out
    tag = next(e for e in events if e["operation"] == "ingest_tagging")
    assert tag["source"] == "estimated"
    assert tag["units"]["tokens_in"] == 2840
    assert tag["units"]["tokens_out"] == 840
    assert tag["period"] == "2026-10"

    # R2 put check
    r2 = next(e for e in events if e["operation"] == "r2_put")
    assert r2["category"] == "r2"
    assert r2["units"]["objects"] == 8
    assert r2["units"]["bytes"] == 1048576


def test_backfill_jobs_compute(test_db):
    """Verify job compute events are generated from ingestion_jobs."""
    db = test_db
    job_id = str(uuid.uuid4())
    start = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(seconds=60)  # 60s execution

    job = IngestionJob(
        id=job_id,
        filename="test_deck.pptx",
        status="COMPLETED",
        created_at=start,
        completed_at=end,
    )
    db.add(job)
    db.commit()

    events, stats = generate_backfill_events(db)
    assert stats["job_compute_rows"] == 1

    je = next(e for e in events if e["operation"] == "job_compute")
    assert je["category"] == "cloud_run_job"
    assert je["units"]["duration_s"] == 60.0
    assert je["units"]["vcpu"] == 2.0
    assert je["units"]["gib"] == 8.0
    # 60 * (2 * 0.0000336 + 8 * 0.0000035) = 60 * (0.0000672 + 0.000028) = 60 * 0.0000952 = 0.005712 USD
    assert abs(je["cost_usd"] - 0.005712) < 1e-5
    assert je["period"] == "2026-09"
    assert je["dedupe_key"] == f"job:{job_id}"


def test_backfill_idempotency(test_db):
    """Verify running backfill discovery after apply produces 0 new events."""
    db = test_db
    doc_id = str(uuid.uuid4())
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)

    iu = IngestionUsage(
        id=str(uuid.uuid4()),
        document_id=doc_id,
        stage="tagging",
        tokens_in=1000,
        tokens_out=200,
        created_at=now,
    )
    db.add(iu)
    db.commit()

    events1, stats1 = generate_backfill_events(db)
    assert len(events1) == 1

    # Apply them
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir)
        apply_backfill(db, events1, out_dir)

        # Second discovery run: should find 0 candidates and record already_exists_skipped
        events2, stats2 = generate_backfill_events(db)
        assert len(events2) == 0
        assert stats2["already_exists_skipped"] >= 1


def test_dry_run_vs_apply_and_rollback(test_db):
    """Verify dry run touches no tables, apply commits rows and writes rollback file, rollback restores."""
    db = test_db
    doc_id = str(uuid.uuid4())
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)

    iu = IngestionUsage(
        id=str(uuid.uuid4()),
        document_id=doc_id,
        stage="vision",
        tokens_in=5000,
        tokens_out=250,
        created_at=now,
    )
    db.add(iu)
    db.commit()

    events, stats = generate_backfill_events(db)
    assert len(events) == 1

    # Verify table is currently empty
    assert db.query(CostEvent).count() == 0

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir)

        # 1. Apply backfill
        inserted, rollback_path = apply_backfill(db, events, out_dir)
        assert inserted == 1
        assert rollback_path.exists()
        assert db.query(CostEvent).count() == 1

        # Check rollback file contents
        content = json.loads(rollback_path.read_text(encoding="utf-8"))
        assert content["total_events"] == 1
        assert len(content["event_ids"]) == 1

        # 2. Rollback
        deleted = rollback_backfill(db, rollback_path)
        assert deleted == 1
        assert db.query(CostEvent).count() == 0


def test_monthly_summary_calculation():
    """Verify monthly summary totals calculation and formatting."""
    events = [
        {"period": "2026-10", "category": "gemini", "cost_inr": 150.0, "units": {"tokens_in": 1000, "tokens_out": 200}},
        {"period": "2026-10", "category": "cloud_run_job", "cost_inr": 50.0, "units": {}},
        {"period": "2026-11", "category": "gemini", "cost_inr": 300.0, "units": {"tokens_in": 2500, "tokens_out": 400}},
        {"period": "2026-11", "category": "r2", "cost_inr": 10.0, "units": {}},
    ]
    summary = print_monthly_summary(events)
    assert summary["2026-10"]["gemini"] == 150.0
    assert summary["2026-10"]["cloud_run_job"] == 50.0
    assert summary["2026-10"]["total"] == 200.0
    assert summary["2026-11"]["gemini"] == 300.0
    assert summary["2026-11"]["r2"] == 10.0
    assert summary["2026-11"]["total"] == 310.0
