import os
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

# Safety env vars before backend imports
os.environ["REQUIRE_POSTGRES"] = "0"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from backend.app.database import Base, SessionLocal
from backend.app.models.auth import AuthUser
from backend.app.models.cost import CostEvent, CostFxRate, CostRateCard, CostRefreshRun
from backend.app.models.document import Document
from backend.app.services.auth.dependencies import require_admin
from backend.app.services.costs.aggregation import (
    invalidate_costs_cache,
    resolve_no_double_counting,
)
from backend.app.services.costs.rate_card import seed_rate_card_v1_if_empty, invalidate_rate_card_cache
from backend.app.services.costs.ledger import record_event
from backend.app.main import app


@pytest.fixture(autouse=True)
def clean_db():
    invalidate_rate_card_cache()
    invalidate_costs_cache()
    mock_admin = AuthUser(id="test-admin-override", role="admin", status="active", display_name="Admin Override")
    app.dependency_overrides[require_admin] = lambda: mock_admin
    db = SessionLocal()
    try:
        Base.metadata.create_all(bind=db.get_bind())
        seed_rate_card_v1_if_empty(db)
    finally:
        db.close()
    yield
    app.dependency_overrides.pop(require_admin, None)
    invalidate_rate_card_cache()
    invalidate_costs_cache()
    db = SessionLocal()
    try:
        db.query(Document).filter(Document.id == "doc-123").delete()
        for tbl in (CostEvent, CostFxRate, CostRateCard, CostRefreshRun):
            db.query(tbl).delete()
        db.commit()
    finally:
        db.close()


def test_resolve_no_double_counting():
    """Verify headline and gap calculation when invoice is present vs absent."""
    # 1. No invoice: headline is measured spend, gap is 0
    h1, m1, b1, g1 = resolve_no_double_counting(measured_or_accrued=120.50, invoice_billed=None)
    assert h1 == 120.50
    assert m1 == 120.50
    assert b1 == 0.0
    assert g1 == 0.0

    # 2. Invoice present: headline is billed amount, gap = billed - measured
    h2, m2, b2, g2 = resolve_no_double_counting(measured_or_accrued=120.50, invoice_billed=150.00)
    assert h2 == 150.00
    assert m2 == 120.50
    assert b2 == 150.00
    assert g2 == 29.50


def test_get_costs_simple_endpoint():
    """GET /costs/simple returns tiny 6 numbers and lines array."""
    client = TestClient(app)
    curr_period = datetime.now(timezone.utc).strftime("%Y-%m")

    # Record some sample events
    record_event(category="gemini", operation="ingest_vision", cost_usd=0.50, cost_inr=47.50, period=curr_period)
    record_event(category="neon", operation="daily_accrual", cost_usd=1.00, cost_inr=95.00, period=curr_period)
    record_event(category="r2", operation="r2_storage", cost_usd=0.20, cost_inr=19.00, period=curr_period)
    record_event(category="cloud_run_service", operation="daily_accrual", cost_usd=2.00, cost_inr=190.00, period=curr_period)
    record_event(category="vercel", operation="daily_accrual", cost_usd=0.66, cost_inr=62.70, period=curr_period)

    res = client.get("/costs/simple")
    assert res.status_code == 200
    data = res.json()

    assert data["period"] == curr_period
    assert data["gemini"] == 47.50
    assert data["database"] == 95.00
    assert data["storage"] == 19.00
    assert data["compute"] == 190.00
    assert data["other"] == 62.70
    assert data["total"] == round(47.50 + 95.00 + 19.00 + 190.00 + 62.70, 2)
    assert len(data["lines"]) == 5


def test_get_costs_summary_gemini_breakdown():
    """GET /costs/summary breaks down Gemini into vision, tagging, embed, and quizzes."""
    client = TestClient(app)
    curr_period = datetime.now(timezone.utc).strftime("%Y-%m")

    record_event(category="gemini", operation="ingest_vision", cost_inr=50.0, period=curr_period)
    record_event(category="gemini", operation="ingest_tagging_v6", cost_inr=30.0, period=curr_period)
    record_event(category="gemini", operation="ingest_embedding", cost_inr=5.0, period=curr_period)
    record_event(category="gemini", operation="quiz_curator", cost_inr=15.0, period=curr_period)
    record_event(category="gemini", operation="quiz_intent", cost_inr=10.0, period=curr_period)

    res = client.get("/costs/summary")
    assert res.status_code == 200
    data = res.json()

    gb = data["periods"]["current_month"]["gemini_breakdown"]
    assert gb["ingestion"]["vision"] == 50.0
    assert gb["ingestion"]["tagging"] == 30.0
    assert gb["ingestion"]["embedding"] == 5.0
    assert gb["ingestion"]["total"] == 85.0
    assert gb["quiz"]["curator"] == 15.0
    assert gb["quiz"]["intent"] == 10.0
    assert gb["quiz"]["total"] == 25.0
    assert gb["total"] == 110.0


def test_documents_costs_breakdown():
    """GET /costs/documents returns per-deck costs and cost per question."""
    client = TestClient(app)
    db = SessionLocal()
    try:
        db.add(Document(
            id="doc-123",
            filename="history.pptx",
            title="World History Finals",
            file_type="pptx",
            storage_path="/tmp/history.pptx",
            question_count=20,
        ))
        db.commit()
    finally:
        db.close()

    record_event(category="gemini", operation="ingest_vision", document_id="doc-123", cost_inr=40.0)
    record_event(category="gemini", operation="ingest_tagging", document_id="doc-123", cost_inr=10.0)
    record_event(category="cloud_run_job", operation="job_compute", document_id="doc-123", cost_inr=30.0)
    record_event(category="r2", operation="r2_put", document_id="doc-123", cost_inr=5.0)

    res = client.get("/costs/documents")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    doc_item = data["items"][0]
    assert doc_item["document_id"] == "doc-123"
    assert doc_item["title"] == "World History Finals"
    assert doc_item["vision_inr"] == 40.0
    assert doc_item["tagging_inr"] == 10.0
    assert doc_item["job_compute_inr"] == 30.0
    assert doc_item["r2_inr"] == 5.0
    assert doc_item["total_inr"] == 85.0
    assert doc_item["cost_per_question_inr"] == round(85.0 / 20, 2)


def test_quizzes_costs_aggregation():
    """GET /costs/quizzes groups by quiz_id and calculates average cost."""
    client = TestClient(app)
    record_event(category="gemini", operation="quiz_intent", quiz_id="q1", cost_inr=0.20)
    record_event(category="gemini", operation="quiz_curator", quiz_id="q1", cost_inr=0.60)
    record_event(category="gemini", operation="quiz_intent", quiz_id="q2", cost_inr=0.20)
    record_event(category="gemini", operation="quiz_curator", quiz_id="q2", cost_inr=0.80)

    res = client.get("/costs/quizzes")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 2
    assert data["average_cost_per_quiz_inr"] == round((0.80 + 1.00) / 2, 4)


def test_post_invoice_and_correction():
    """POST /costs/invoices creates invoice and handles corrections by superseding."""
    client = TestClient(app)
    period = "2026-11"

    # 1. Post initial invoice
    payload1 = {
        "category": "neon",
        "period": period,
        "amount": 190.0,
        "currency": "INR",
        "gst": 0.0,
        "note": "Initial Neon invoice",
    }
    res1 = client.post("/costs/invoices", json=payload1)
    assert res1.status_code == 200
    inv1_id = res1.json()["id"]

    # Check that GET /costs/simple reflects the invoice
    res_simple1 = client.get(f"/costs/simple?period={period}")
    assert res_simple1.json()["database"] == 190.0

    # 2. Post corrected invoice for same category & period
    payload2 = {
        "category": "neon",
        "period": period,
        "amount": 210.0,
        "currency": "INR",
        "gst": 18.0,
        "note": "Revised Neon invoice with GST",
    }
    res2 = client.post("/costs/invoices", json=payload2)
    assert res2.status_code == 200

    # Check that previous invoice was superseded, not deleted
    db = SessionLocal()
    try:
        old_row = db.query(CostEvent).filter(CostEvent.id == inv1_id).first()
        assert old_row is not None
        assert old_row.units.get("superseded") is True
    finally:
        db.close()

    # Verify GET /costs/simple now reflects the revised invoice (210 + 18 = 228)
    res_simple2 = client.get(f"/costs/simple?period={period}")
    assert res_simple2.json()["database"] == 228.0


def test_rate_card_endpoints():
    """GET and PUT /costs/rate-card versioning."""
    client = TestClient(app)

    res = client.get("/costs/rate-card")
    assert res.status_code == 200
    active = res.json()["active"]
    assert active["version"] == 1

    # Update rate card to v2
    new_rates = dict(active)
    new_rates["models"]["gemini-3.5-flash-lite"]["input_per_m"] = 0.25
    res_put = client.put("/costs/rate-card", json={"rates": new_rates})
    assert res_put.status_code == 200
    assert res_put.json()["version"] == 2

    # Verify active is now v2
    res2 = client.get("/costs/rate-card")
    assert res2.json()["active"]["version"] == 2
    assert len(res2.json()["history"]) == 2


def test_all_time_greater_than_or_equal_current_month():
    """All-time headline must always be >= current month, even across historical invoices."""
    client = TestClient(app)
    curr_period = datetime.now(timezone.utc).strftime("%Y-%m")
    prev_period = "2026-10"

    # Current month has 100 INR events
    record_event(category="gemini", operation="ingest_vision", cost_inr=100.0, period=curr_period)

    # Previous month has an invoice of 200 INR
    record_event(category="neon", operation="invoice", cost_inr=200.0, period=prev_period, source="billed")

    res = client.get("/costs/summary")
    assert res.status_code == 200
    data = res.json()

    # Top-level direct keys and periods keys both populated
    assert "current_month" in data
    assert "last_month" in data
    assert "all_time" in data

    # All time must include current month + historical invoice
    assert data["all_time"]["headline_inr"] >= data["current_month"]["headline_inr"]
    assert data["all_time"]["headline_inr"] == 300.0
    assert data["current_month"]["headline_inr"] == 100.0
    assert data["last_month"]["headline_inr"] == 200.0
    assert data["periods"]["all_time"]["headline_inr"] == 300.0

