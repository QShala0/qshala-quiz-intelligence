"""Tests for Phase 1 (Foundation): rate cards, pricing math, ledger recording, and cost scopes."""
import asyncio
import concurrent.futures
import threading
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base, SessionLocal
from backend.app.models.cost import CostEvent, CostFxRate, CostRateCard, CostRefreshRun
from backend.app.services.costs.ledger import (
    CostScope,
    cost_scope,
    get_current_cost_scope,
    record_event,
    record_unattributed_call,
    flush_unattributed,
)
from backend.app.services.costs.rate_card import (
    DEFAULT_RATE_CARD_V1,
    calculate_event_cost,
    get_active_rate_card,
    invalidate_rate_card_cache,
    seed_rate_card_v1_if_empty,
)


@pytest.fixture(autouse=True)
def _ensure_tables_and_clean_rate_card():
    invalidate_rate_card_cache()
    # Create all tables on the temp sqlite engine
    db = SessionLocal()
    try:
        Base.metadata.create_all(bind=db.get_bind())
    finally:
        db.close()
    yield
    invalidate_rate_card_cache()


def test_rate_card_seeding_and_caching():
    db = SessionLocal()
    try:
        # Clear any existing rate cards
        db.query(CostRateCard).delete()
        db.commit()
        invalidate_rate_card_cache()

        inserted = seed_rate_card_v1_if_empty(db)
        assert inserted is True

        # Second seed is idempotent
        inserted_again = seed_rate_card_v1_if_empty(db)
        assert inserted_again is False

        card = get_active_rate_card(db)
        assert card["version"] == 1
        assert card["usd_to_inr"] == 95.0
        assert card["models"]["gemini-3.5-flash-lite"]["input_per_m"] == 0.30
        assert card["models"]["gemini-3.5-flash-lite"]["output_per_m"] == 2.50
    finally:
        db.close()


def test_price_math_standard_batch_cached_thinking():
    # 1. Standard Gemini call: 1M in ($0.30), 1M out ($2.50), thinking billed as output
    usd, inr, rates, ver, fx = calculate_event_cost(
        category="gemini",
        operation="quiz_curator",
        units={"tokens_in": 1_000_000, "tokens_out": 500_000, "thinking_tokens": 500_000},
        model="gemini-3.5-flash-lite",
        fx_rate=95.0,
    )
    # 1M in = $0.30, 1M total out (500k + 500k) = $2.50 -> $2.80 total
    assert abs(usd - 2.80) < 1e-6
    assert abs(inr - (2.80 * 95.0)) < 1e-4
    assert rates["input_per_m"] == 0.30
    assert rates["output_per_m"] == 2.50

    # 2. Batch Gemini call: 50% discount
    b_usd, b_inr, b_rates, _, _ = calculate_event_cost(
        category="gemini",
        operation="ingest_tagging",
        units={"tokens_in": 1_000_000, "tokens_out": 1_000_000},
        model="gemini-3.5-flash-lite",
        is_batch=True,
        fx_rate=95.0,
    )
    assert abs(b_usd - (0.15 + 1.25)) < 1e-6
    assert abs(b_inr - (1.40 * 95.0)) < 1e-4
    assert b_rates["is_batch"] is True

    # 3. Cached input discount: 1M in with 400k cached
    # fresh = 600k * 0.30/1M = $0.18
    # cached = 400k * 0.03/1M = $0.012
    # total in = $0.192
    c_usd, _, c_rates, _, _ = calculate_event_cost(
        category="gemini",
        operation="ingest_tagging",
        units={"tokens_in": 1_000_000, "cached_tokens": 400_000, "tokens_out": 0},
        model="gemini-3.5-flash-lite",
    )
    assert abs(c_usd - 0.192) < 1e-6
    assert c_rates["cached_input_per_m"] == 0.03


def test_price_math_cloud_run_r2_neon():
    # Cloud Run Job: 100 seconds, 2 vCPU, 8 GiB
    # vCPU = 100 * 2 * 0.0000336 = 0.00672
    # GiB  = 100 * 8 * 0.0000035 = 0.00280
    # Total = 0.00952
    job_usd, job_inr, _, _, _ = calculate_event_cost(
        category="cloud_run_job",
        operation="job_compute",
        units={"duration_s": 100, "vcpu": 2, "gib": 8},
        fx_rate=95.0,
    )
    assert abs(job_usd - 0.00952) < 1e-5
    assert abs(job_inr - (0.00952 * 95.0)) < 1e-4

    # R2 put: 1,000 objects Class A
    # 1,000 / 1e6 * 4.50 = $0.0045
    r2_usd, _, _, _, _ = calculate_event_cost(
        category="r2",
        operation="r2_put",
        units={"objects": 1_000},
    )
    assert abs(r2_usd - 0.0045) < 1e-6

    # Neon: 10 CU-hours, 2 GB-months
    # 10 * 0.106 = 1.06, 2 * 0.35 = 0.70 -> $1.76
    neon_usd, _, _, _, _ = calculate_event_cost(
        category="neon",
        operation="fixed_monthly",
        units={"cu_hours": 10, "gb_months": 2},
    )
    assert abs(neon_usd - 1.76) < 1e-6


def test_idempotent_event_recording_with_dedupe_key():
    dedupe = f"test:dedupe:{uuid.uuid4()}"

    ev1 = record_event(
        category="gemini",
        operation="quiz_intent",
        units={"tokens_in": 1000, "tokens_out": 200},
        dedupe_key=dedupe,
    )
    assert ev1 is not None

    # Second call with the same dedupe_key is idempotent and returns None or existing
    ev2 = record_event(
        category="gemini",
        operation="quiz_intent",
        units={"tokens_in": 1000, "tokens_out": 200},
        dedupe_key=dedupe,
    )

    db = SessionLocal()
    try:
        rows = db.query(CostEvent).filter(CostEvent.dedupe_key == dedupe).all()
        assert len(rows) == 1
        assert rows[0].category == "gemini"
        assert rows[0].operation == "quiz_intent"
    finally:
        db.close()


def test_scope_aggregates_multiple_calls_into_single_rows():
    doc_id = str(uuid.uuid4())

    with cost_scope(document_id=doc_id) as scope:
        assert get_current_cost_scope() is scope
        # Simulate 10 vision calls and 2 tagging calls
        for _ in range(10):
            scope.add_gemini_usage(
                operation="ingest_vision",
                model="gemini-3.5-flash-lite",
                tokens_in=1156,
                tokens_out=91,
                calls=1,
            )
        for _ in range(2):
            scope.add_gemini_usage(
                operation="ingest_tagging",
                model="gemini-3.5-flash-lite",
                tokens_in=5688,
                tokens_out=1681,
                calls=1,
            )
        # R2 uploads
        scope.add_r2_usage(operation="r2_put", objects=10, bytes_uploaded=5_000_000)

    # After exit, check database
    db = SessionLocal()
    try:
        events = db.query(CostEvent).filter(CostEvent.document_id == doc_id).all()
        # Exactly 3 rows: 1 for vision, 1 for tagging, 1 for r2
        assert len(events) == 3

        ops = {e.operation: e for e in events}
        assert "ingest_vision" in ops
        assert ops["ingest_vision"].units["calls"] == 10
        assert ops["ingest_vision"].units["tokens_in"] == 11560
        assert ops["ingest_vision"].units["tokens_out"] == 910

        assert "ingest_tagging" in ops
        assert ops["ingest_tagging"].units["calls"] == 2
        assert ops["ingest_tagging"].units["tokens_in"] == 11376
        assert ops["ingest_tagging"].units["tokens_out"] == 3362

        assert "r2_put" in ops
        assert ops["r2_put"].units["objects"] == 10
        assert ops["r2_put"].units["bytes"] == 5_000_000
    finally:
        db.close()


def test_scope_flushes_on_exception():
    doc_id = str(uuid.uuid4())

    try:
        with cost_scope(document_id=doc_id) as scope:
            scope.add_gemini_usage(
                operation="ingest_vision",
                model="gemini-3.5-flash-lite",
                tokens_in=2000,
                tokens_out=150,
            )
            raise RuntimeError("Pipeline failed mid-deck")
    except RuntimeError:
        pass

    db = SessionLocal()
    try:
        events = db.query(CostEvent).filter(CostEvent.document_id == doc_id).all()
        assert len(events) == 1
        assert events[0].units["tokens_in"] == 2000
        assert events[0].units["tokens_out"] == 150
    finally:
        db.close()


def test_scope_thread_safety_with_concurrent_workers():
    doc_id = str(uuid.uuid4())

    with cost_scope(document_id=doc_id) as scope:
        def worker(w_id):
            for _ in range(5):
                scope.add_gemini_usage(
                    operation="ingest_vision",
                    model="gemini-3.5-flash-lite",
                    tokens_in=100,
                    tokens_out=10,
                )

        # 4 concurrent threads accumulating into the same scope
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    db = SessionLocal()
    try:
        events = db.query(CostEvent).filter(CostEvent.document_id == doc_id).all()
        assert len(events) == 1
        # 4 threads * 5 calls = 20 calls = 2000 in, 200 out
        assert events[0].units["calls"] == 20
        assert events[0].units["tokens_in"] == 2000
        assert events[0].units["tokens_out"] == 200
    finally:
        db.close()


def test_unattributed_call_buffering_and_flush():
    flush_unattributed()  # clean start

    # Record 3 unattributed calls
    record_unattributed_call(
        category="gemini",
        operation="search_embedding",
        model="gemini-embedding-001",
        units={"tokens_in": 50, "tokens_out": 0, "calls": 1},
    )
    record_unattributed_call(
        category="gemini",
        operation="search_embedding",
        model="gemini-embedding-001",
        units={"tokens_in": 50, "tokens_out": 0, "calls": 1},
    )

    flush_unattributed()

    db = SessionLocal()
    try:
        ev = (
            db.query(CostEvent)
            .filter(CostEvent.operation == "search_embedding", CostEvent.note == "unattributed call aggregate")
            .order_by(CostEvent.occurred_at.desc())
            .first()
        )
        assert ev is not None
        assert ev.units["tokens_in"] == 100
        assert ev.units["calls"] == 2
    finally:
        db.close()


def test_database_write_failure_never_raises():
    # Calling record_event with invalid/poisoned session or closed connection must not raise
    failing_engine = create_engine("sqlite:///:memory:")
    FailingSession = sessionmaker(bind=failing_engine)
    sess = FailingSession()
    # Close session immediately to force failure on execute
    sess.close()

    # Must swallow error and return None without raising
    res = record_event(
        category="gemini",
        operation="quiz_intent",
        units={"tokens_in": 500},
        db_session=sess,
    )
    assert res is None

def test_alembic_0013_upgrade_and_downgrade(monkeypatch):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    m = importlib.import_module("backend.alembic.versions.0013_cost_ledger")

    test_eng = create_engine("sqlite:///:memory:")
    with test_eng.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)

        monkeypatch.setattr(m, "op", op)

        # Run upgrade
        m.upgrade()

        from sqlalchemy import inspect
        insp = inspect(conn)
        tables = set(insp.get_table_names())
        assert "cost_events" in tables
        assert "cost_rate_cards" in tables
        assert "cost_fx_rates" in tables
        assert "cost_refresh_runs" in tables

        # Run downgrade
        m.downgrade()
        insp = inspect(conn)
        tables_after = set(insp.get_table_names())
        assert "cost_events" not in tables_after
