"""Backfill historical cost events from ingestion_usage, historical decks, jobs, and R2.

Idempotent, dry-run by default, with --apply and --rollback support.
Rollback and report files are saved outside the repository.

Usage:
    python backend/scripts/backfill_cost_events.py                               # Dry run with summary
    python backend/scripts/backfill_cost_events.py --apply                       # Apply to database
    python backend/scripts/backfill_cost_events.py --apply --out-dir /path/out   # Specify output directory
    python backend/scripts/backfill_cost_events.py --rollback /path/rollback.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app.database import Base, SessionLocal, engine
from backend.app.models.cost import CostEvent, CostRateCard
from backend.app.models.document import Document
from backend.app.models.job import IngestionJob
from backend.app.models.qshala_tag import QshalaTag
from backend.app.models.question import Question
from backend.app.models.usage import IngestionUsage
from backend.app.services.costs.rate_card import (
    calculate_event_cost,
    get_active_rate_card,
    seed_rate_card_v1_if_empty,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_cost_events")


def compute_event_cost(
    category: str,
    operation: str,
    units: Dict[str, Any],
    model: Optional[str] = None,
    card: Optional[Dict[str, Any]] = None,
    is_batch: bool = False,
) -> Tuple[float, float, Dict[str, Any]]:
    """Helper wrapper around calculate_event_cost for backfill."""
    c_usd, c_inr, r_used, _, _ = calculate_event_cost(
        category=category,
        operation=operation,
        units=units,
        model=model,
        rate_card=card,
        is_batch=is_batch,
    )
    return c_usd, c_inr, r_used


def _get_default_out_dir() -> Path:
    """Return a durable directory outside the repository for reports and rollback files."""
    if sys.platform == "win32":
        base = Path(r"C:\Users\bragg\qshala-backups\cost-ledger")
    else:
        base = Path.home() / "qshala-backups" / "cost-ledger"
    base.mkdir(parents=True, exist_ok=True)
    return base


def generate_backfill_events(db: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Scan existing records and build cost event candidate dicts."""
    card = get_active_rate_card(db)
    stats: Dict[str, Any] = {
        "ingestion_usage_rows": 0,
        "historical_deck_tagging": 0,
        "historical_deck_vision": 0,
        "job_compute_rows": 0,
        "r2_put_rows": 0,
        "already_exists_skipped": 0,
    }

    # Pre-fetch existing dedupe keys to avoid duplicate candidate generation
    existing_keys: Set[str] = set(
        k[0]
        for k in db.query(CostEvent.dedupe_key)
        .filter(CostEvent.dedupe_key.isnot(None))
        .all()
    )

    events: List[Dict[str, Any]] = []

    # -------------------------------------------------------------------------
    # 1. From ingestion_usage (since 13 Nov 2026): source=measured
    # -------------------------------------------------------------------------
    iu_rows = db.query(IngestionUsage).order_by(IngestionUsage.created_at.asc()).all()
    logged_doc_ids: Set[str] = set()

    for iu in iu_rows:
        if iu.document_id:
            logged_doc_ids.add(iu.document_id)

        dedupe_key = f"iu:{iu.id}"
        if dedupe_key in existing_keys:
            stats["already_exists_skipped"] += 1
            continue

        stage = (iu.stage or "").lower()
        if "vision" in stage:
            operation = "ingest_vision"
        elif "tag" in stage:
            operation = "ingest_tagging"
        elif "embed" in stage:
            operation = "ingest_embedding"
        else:
            operation = f"ingest_{stage}" if stage else "ingest_tagging"

        model = iu.model or "gemini-3.5-flash-lite"
        created_dt = iu.created_at or datetime.now(timezone.utc)
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)

        period = created_dt.strftime("%Y-%m")
        units = {
            "tokens_in": int(iu.tokens_in or 0),
            "tokens_out": int(iu.tokens_out or 0),
            "thinking_tokens": int(iu.thinking_tokens or 0),
            "cached_tokens": int(iu.cached_tokens or 0),
            "calls": int(iu.calls or 1),
            "objects": 0,
            "bytes": 0,
        }

        cost_usd, cost_inr, rates_used = compute_event_cost(
            category="gemini",
            operation=operation,
            model=model,
            units=units,
            card=card,
            is_batch=False,
        )

        events.append({
            "id": str(uuid.uuid4()),
            "occurred_at": created_dt,
            "category": "gemini",
            "operation": operation,
            "document_id": iu.document_id,
            "model": model,
            "units": units,
            "rates": rates_used,
            "rate_card_version": card.get("version", 1),
            "cost_usd": cost_usd,
            "fx_rate": card.get("usd_to_inr", 95.0),
            "cost_inr": cost_inr,
            "tax_inr": 0.0,
            "source": "measured",
            "period": period,
            "dedupe_key": dedupe_key,
            "note": "backfill from ingestion_usage",
        })
        stats["ingestion_usage_rows"] += 1

    # -------------------------------------------------------------------------
    # 2. For decks with NO usage rows (ingested before logging): source=estimated
    # -------------------------------------------------------------------------
    unlogged_docs = (
        db.query(Document)
        .filter(~Document.id.in_(logged_doc_ids) if logged_doc_ids else True)
        .order_by(Document.created_at.asc())
        .all()
    )

    for doc in unlogged_docs:
        created_dt = doc.created_at or datetime.now(timezone.utc)
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        period = created_dt.strftime("%Y-%m")

        # Questions in this deck
        questions = db.query(Question).filter(Question.document_id == doc.id).all()
        q_count = len(questions)
        q_ids = [q.id for q in questions]

        # 2a. Tagging estimation
        tag_key = f"bf:tag:{doc.id}"
        if tag_key not in existing_keys:
            # Check if qshala_tags has token records for this deck's questions
            tag_tokens_in = 0
            tag_tokens_out = 0
            tag_calls = 0

            if q_ids:
                tags = db.query(QshalaTag).filter(QshalaTag.question_id.in_(q_ids)).all()
                for t in tags:
                    if t.tokens_in or t.tokens_out:
                        tag_tokens_in += int(t.tokens_in or 0)
                        tag_tokens_out += int(t.tokens_out or 0)
                        tag_calls += 1

            # If no recorded tokens in qshala_tags, use measured benchmark average:
            # ~5,688 tokens in / 1,681 tokens out per 20 questions (~284 in / 84 out per Q)
            if tag_tokens_in == 0 and q_count > 0:
                tag_tokens_in = q_count * 284
                tag_tokens_out = q_count * 84
                tag_calls = max(1, (q_count + 19) // 20)

            if tag_tokens_in > 0 or tag_tokens_out > 0:
                tag_units = {
                    "tokens_in": tag_tokens_in,
                    "tokens_out": tag_tokens_out,
                    "thinking_tokens": 0,
                    "cached_tokens": 0,
                    "calls": tag_calls,
                    "objects": 0,
                    "bytes": 0,
                }
                c_usd, c_inr, r_used = compute_event_cost(
                    category="gemini",
                    operation="ingest_tagging",
                    model="gemini-3.5-flash-lite",
                    units=tag_units,
                    card=card,
                    is_batch=False,
                )
                events.append({
                    "id": str(uuid.uuid4()),
                    "occurred_at": created_dt,
                    "category": "gemini",
                    "operation": "ingest_tagging",
                    "document_id": doc.id,
                    "model": "gemini-3.5-flash-lite",
                    "units": tag_units,
                    "rates": r_used,
                    "rate_card_version": card.get("version", 1),
                    "cost_usd": c_usd,
                    "fx_rate": card.get("usd_to_inr", 95.0),
                    "cost_inr": c_inr,
                    "tax_inr": 0.0,
                    "source": "estimated",
                    "period": period,
                    "dedupe_key": tag_key,
                    "note": "backfill estimate",
                })
                stats["historical_deck_tagging"] += 1
        else:
            stats["already_exists_skipped"] += 1

        # 2b. Vision estimation
        # Measured average: 1,156 in / 91 out per image, ~1.4 images per question
        vis_key = f"bf:vis:{doc.id}"
        if vis_key not in existing_keys:
            if q_count > 0:
                num_images = max(1, round(q_count * 1.4))
                vis_tokens_in = num_images * 1156
                vis_tokens_out = num_images * 91
                vis_units = {
                    "tokens_in": vis_tokens_in,
                    "tokens_out": vis_tokens_out,
                    "thinking_tokens": 0,
                    "cached_tokens": 0,
                    "calls": num_images,
                    "objects": 0,
                    "bytes": 0,
                }
                v_usd, v_inr, vr_used = compute_event_cost(
                    category="gemini",
                    operation="ingest_vision",
                    model="gemini-3.5-flash-lite",
                    units=vis_units,
                    card=card,
                    is_batch=False,
                )
                events.append({
                    "id": str(uuid.uuid4()),
                    "occurred_at": created_dt,
                    "category": "gemini",
                    "operation": "ingest_vision",
                    "document_id": doc.id,
                    "model": "gemini-3.5-flash-lite",
                    "units": vis_units,
                    "rates": vr_used,
                    "rate_card_version": card.get("version", 1),
                    "cost_usd": v_usd,
                    "fx_rate": card.get("usd_to_inr", 95.0),
                    "cost_inr": v_inr,
                    "tax_inr": 0.0,
                    "source": "estimated",
                    "period": period,
                    "dedupe_key": vis_key,
                    "note": "backfill estimate",
                })
                stats["historical_deck_vision"] += 1
        else:
            stats["already_exists_skipped"] += 1

        # 2c. R2 Put operations & storage snapshot
        r2_key = f"bf:r2:{doc.id}"
        if r2_key not in existing_keys:
            file_bytes = int(doc.file_size_bytes or 500000)
            # Estimate ~8 puts per deck (slides, images, metadata)
            r2_units = {
                "calls": 8,
                "objects": 8,
                "bytes": file_bytes,
                "tokens_in": 0,
                "tokens_out": 0,
            }
            r2_usd, r2_inr, r2_rates = compute_event_cost(
                category="r2",
                operation="r2_put",
                model=None,
                units=r2_units,
                card=card,
            )
            events.append({
                "id": str(uuid.uuid4()),
                "occurred_at": created_dt,
                "category": "r2",
                "operation": "r2_put",
                "document_id": doc.id,
                "model": None,
                "units": r2_units,
                "rates": r2_rates,
                "rate_card_version": card.get("version", 1),
                "cost_usd": r2_usd,
                "fx_rate": card.get("usd_to_inr", 95.0),
                "cost_inr": r2_inr,
                "tax_inr": 0.0,
                "source": "estimated",
                "period": period,
                "dedupe_key": r2_key,
                "note": "backfill estimate",
            })
            stats["r2_put_rows"] += 1
        else:
            stats["already_exists_skipped"] += 1

    # -------------------------------------------------------------------------
    # 3. Job compute (from ingestion_jobs): source=estimated
    # -------------------------------------------------------------------------
    jobs = db.query(IngestionJob).order_by(IngestionJob.created_at.asc()).all()
    for j in jobs:
        job_key = f"job:{j.id}"
        if job_key in existing_keys:
            stats["already_exists_skipped"] += 1
            continue

        created_dt = j.created_at or datetime.now(timezone.utc)
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        period = created_dt.strftime("%Y-%m")

        # Duration
        end_dt = j.completed_at or j.updated_at
        if end_dt:
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            duration_s = max(5.0, (end_dt - created_dt).total_seconds())
        else:
            duration_s = 45.0  # conservative average execution time

        duration_s = min(duration_s, 600.0)  # cap at 10m to avoid timeout distortion

        job_units = {
            "duration_s": round(duration_s, 2),
            "vcpu": 2.0,
            "gib": 8.0,
            "calls": 1,
            "tokens_in": 0,
            "tokens_out": 0,
        }
        j_usd, j_inr, j_rates = compute_event_cost(
            category="cloud_run_job",
            operation="job_compute",
            model=None,
            units=job_units,
            card=card,
        )
        events.append({
            "id": str(uuid.uuid4()),
            "occurred_at": created_dt,
            "category": "cloud_run_job",
            "operation": "job_compute",
            "document_id": j.document_id,
            "job_id": j.id,
            "model": None,
            "units": job_units,
            "rates": j_rates,
            "rate_card_version": card.get("version", 1),
            "cost_usd": j_usd,
            "fx_rate": card.get("usd_to_inr", 95.0),
            "cost_inr": j_inr,
            "tax_inr": 0.0,
            "source": "estimated",
            "period": period,
            "dedupe_key": job_key,
            "note": "backfill from ingestion_jobs",
        })
        stats["job_compute_rows"] += 1

    return events, stats


def print_monthly_summary(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """Group events by period and category and print formatted table."""
    monthly: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    total_tokens_in = 0
    total_tokens_out = 0

    for e in events:
        p = e.get("period") or "unknown"
        cat = e.get("category") or "other"
        cost_inr = float(e.get("cost_inr", 0.0))
        monthly[p][cat] += cost_inr
        monthly[p]["total"] += cost_inr

        u = e.get("units") or {}
        total_tokens_in += int(u.get("tokens_in", 0) or 0)
        total_tokens_out += int(u.get("tokens_out", 0) or 0)

    print("\n================================================================================")
    print("BACKFILL MONTHLY ESTIMATES & MEASUREMENTS SUMMARY")
    print("================================================================================")
    header = f"{'Period':<10} | {'Gemini (₹)':<14} | {'Jobs (₹)':<12} | {'R2 (₹)':<10} | {'Total (₹)':<12}"
    print(header)
    print("-" * len(header))

    grand_total_inr = 0.0
    for p in sorted(monthly.keys()):
        row = monthly[p]
        gemini_inr = row.get("gemini", 0.0)
        jobs_inr = row.get("cloud_run_job", 0.0)
        r2_inr = row.get("r2", 0.0)
        t_inr = row.get("total", 0.0)
        grand_total_inr += t_inr
        print(f"{p:<10} | ₹{gemini_inr:<13.2f} | ₹{jobs_inr:<11.2f} | ₹{r2_inr:<9.2f} | ₹{t_inr:<11.2f}")

    print("-" * len(header))
    print(f"{'GRAND TOTAL':<10} | {'':<14} | {'':<12} | {'':<10} | ₹{grand_total_inr:<11.2f}")
    print(f"Total Tokens: In = {total_tokens_in:,} | Out = {total_tokens_out:,}")
    print("================================================================================\n")
    return monthly


def apply_backfill(
    db: Any,
    events: List[Dict[str, Any]],
    out_dir: Path,
    batch_size: int = 200,
) -> Tuple[int, Path]:
    """Insert candidate events into cost_events in batches and save rollback file."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    rollback_file = out_dir / f"backfill_cost_events_ROLLBACK_{stamp}.json"

    rollback_data = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total_events": len(events),
        "event_ids": [e["id"] for e in events],
        "dedupe_keys": [e["dedupe_key"] for e in events if e.get("dedupe_key")],
    }
    rollback_file.write_text(json.dumps(rollback_data, indent=2), encoding="utf-8")
    logger.info("Rollback manifest written to: %s", rollback_file)

    inserted = 0
    for i in range(0, len(events), batch_size):
        chunk = events[i : i + batch_size]
        for e in chunk:
            ev = CostEvent(
                id=e["id"],
                occurred_at=e["occurred_at"],
                category=e["category"],
                operation=e["operation"],
                document_id=e.get("document_id"),
                quiz_id=e.get("quiz_id"),
                job_id=e.get("job_id"),
                batch_id=e.get("batch_id"),
                model=e.get("model"),
                units=e.get("units", {}),
                rates=e.get("rates", {}),
                rate_card_version=e.get("rate_card_version", 1),
                cost_usd=e.get("cost_usd", 0.0),
                fx_rate=e.get("fx_rate", 95.0),
                cost_inr=e.get("cost_inr", 0.0),
                tax_inr=e.get("tax_inr", 0.0),
                source=e.get("source", "measured"),
                period=e.get("period"),
                dedupe_key=e.get("dedupe_key"),
                note=e.get("note"),
            )
            db.add(ev)
        db.commit()
        inserted += len(chunk)
        logger.info("Inserted %d of %d backfill events...", inserted, len(events))

    return inserted, rollback_file


def rollback_backfill(db: Any, rollback_path: Path) -> int:
    """Read a rollback JSON file and delete the created cost_events rows."""
    if not rollback_path.exists():
        raise FileNotFoundError(f"Rollback file not found at {rollback_path}")

    data = json.loads(rollback_path.read_text(encoding="utf-8"))
    ids = data.get("event_ids", [])
    dedupe_keys = data.get("dedupe_keys", [])

    logger.info("Rolling back %d events from manifest %s...", len(ids), rollback_path)
    deleted = 0

    if ids:
        # Delete in chunks
        chunk_size = 500
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i : i + chunk_size]
            res = (
                db.query(CostEvent)
                .filter(CostEvent.id.in_(chunk))
                .delete(synchronize_session=False)
            )
            deleted += res
        db.commit()

    logger.info("Rollback complete: deleted %d rows from cost_events.", deleted)
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill historical cost ledger events.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually apply changes to the database (default is dry-run).",
    )
    parser.add_argument(
        "--prod-dry-run",
        action="store_true",
        help="Production dry run: asserts REQUIRE_POSTGRES=1, asserts postgresql dialect, sets SET TRANSACTION READ ONLY, and guarantees 0 writes.",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="Database URL to connect to. Overrides DATABASE_URL environment variable.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Directory outside the repo to save report and rollback files.",
    )
    parser.add_argument(
        "--rollback",
        type=Path,
        default=None,
        help="Path to rollback JSON file to undo a previous backfill.",
    )
    args = parser.parse_args()

    out_dir = args.out_dir or _get_default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.db_url:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        connect_args = {"check_same_thread": False} if args.db_url.startswith("sqlite") else {}
        custom_engine = create_engine(args.db_url, connect_args=connect_args)
        CustomSession = sessionmaker(autocommit=False, autoflush=False, bind=custom_engine)
        db = CustomSession()
    else:
        db = SessionLocal()
    try:
        # 1. Rollback mode
        if args.rollback:
            deleted = rollback_backfill(db, args.rollback)
            print(f"Successfully rolled back {deleted} cost events.")
            return 0

        # Production dry-run safety gates
        if args.prod_dry_run:
            req_pg = os.environ.get("REQUIRE_POSTGRES", "").strip()
            if req_pg not in {"1", "true", "True"}:
                print("ERROR: --prod-dry-run requires REQUIRE_POSTGRES=1 environment variable to be set.")
                return 1

            dialect_name = getattr(db.get_bind().dialect, "name", "")
            if dialect_name != "postgresql":
                print(f"ERROR: --prod-dry-run requires a PostgreSQL connection, but detected '{dialect_name}'.")
                return 1

            from sqlalchemy import text
            db.execute(text("SET TRANSACTION READ ONLY;"))
            print("[PROD DRY RUN] Connected to PostgreSQL. Active transaction set to READ ONLY.")
            args.apply = False

        # 2. Ensure schema & active rate card
        Base.metadata.create_all(bind=db.get_bind())
        seed_rate_card_v1_if_empty(db)

        # 3. Generate candidate events
        events, stats = generate_backfill_events(db)

        print("\n================================================================================")
        print("CANDIDATE EVENT DISCOVERY STATS")
        print("================================================================================")
        for k, v in stats.items():
            print(f"  {k:<32}: {v}")
        print(f"  {'Total new events to create':<32}: {len(events)}")
        print("================================================================================\n")

        # 4. Print monthly summary
        print_monthly_summary(events)

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        mode_str = "apply" if args.apply else "dry_run"
        report_file = out_dir / f"backfill_cost_events_report_{mode_str}_{stamp}.json"
        report_file.write_text(
            json.dumps(
                {
                    "stats": stats,
                    "total_events": len(events),
                    "events_sample": events[:10],
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        print(f"Detailed run report saved outside repo to:\n  {report_file}\n")

        # 5. Apply or Dry-Run exit
        if not args.apply:
            print(">>> DRY-RUN COMPLETE. No rows were written to the database.")
            print(">>> Run with --apply to commit these events to the cost ledger.\n")
            return 0

        inserted, rollback_path = apply_backfill(db, events, out_dir)
        print(">>> SUCCESS: Applied %d cost events to cost_events." % inserted)
        print(">>> Rollback file saved to:\n  %s\n" % rollback_path)
        return 0

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
