"""Usage and cost recording (audit Q13): thinking and cached tokens counted, per-deck usage rows saved."""
import uuid

from backend.app.database import SessionLocal
from backend.app.models.usage import IngestionUsage
from backend.app.services.ai import gemini_provider as gp
from backend.app.services.ai.usage_cost import estimate_cost_usd, save_usage


def test_thinking_tokens_are_billed_as_output_and_cached_at_a_discount():
    base = estimate_cost_usd(1_000_000, 1_000_000)
    assert abs(base - (0.30 + 2.50)) < 1e-9
    assert abs(estimate_cost_usd(0, 0, thinking=1_000_000) - 2.50) < 1e-9
    # 400k of the 1M prompt tokens were cached -> cheaper than uncached
    assert estimate_cost_usd(1_000_000, 0, cached=400_000) < estimate_cost_usd(1_000_000, 0)
    # cached can never exceed the prompt tokens
    assert estimate_cost_usd(100, 0, cached=10_000) >= 0


def test_running_totals_include_cached_tokens():
    gp.usage_snapshot(reset=True)
    gp.record_usage("vision", {"in": 100, "out": 10, "think": 5, "cached": 40})
    gp.record_usage("vision", {"in": 100, "out": 10, "think": 5})
    snap = gp.usage_snapshot(reset=True)["vision"]
    assert snap == {"calls": 2, "in": 200, "out": 20, "think": 10, "cached": 40}


def test_usage_response_parsing_keeps_cached_and_thinking():
    u = gp._usage_from_response({"usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 3,
                                                   "thoughtsTokenCount": 2, "cachedContentTokenCount": 4}})
    assert u == {"in": 9, "out": 3, "think": 2, "cached": 4}


def test_save_usage_writes_a_row_and_skips_empty_usage():
    doc_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        save_usage(db, doc_id, "vision", "m", {"calls": 0})        # nothing to record
        save_usage(db, doc_id, "tagging", "m", {"calls": 3, "in": 30, "out": 6, "think": 2, "cached": 5})
        db.commit()
        rows = db.query(IngestionUsage).filter(IngestionUsage.document_id == doc_id).all()
        assert len(rows) == 1
        r = rows[0]
        assert (r.stage, r.calls, r.tokens_in, r.tokens_out, r.thinking_tokens, r.cached_tokens) == \
               ("tagging", 3, 30, 6, 2, 5)
    finally:
        db.query(IngestionUsage).filter(IngestionUsage.document_id == doc_id).delete()
        db.commit()
        db.close()
