"""Workstream 1 backfill: re-tag answer-led questions (Connect, Image Reveal, generic-text-with-
clue-media) and the untagged backlog through the SAME code path ingestion uses
(backend.app.services.tagging.qshala_batch.tag_questions_batched), so behaviour matches exactly
what a freshly-ingested deck would get. Does NOT reuse tag_remaining_corpus.py -- that script is
rule-only and marks everything confirmed, which would defeat the point of this change.

Targets three buckets (a question can land in more than one; it is only ever re-tagged once):
  (a) answer-led questions with no tag, or only a draft/weak LLM tag.
  (b) every question with no AI tag at all (currently ~505) -- is_answer_led() is evaluated per
      question by tag_questions_batched itself, so a non-answer-led question in this bucket still
      gets the normal (non-answer-led) prompt automatically.
  (c) questions in the review queue (status=draft, source=LLM) whose question is answer-led.

A question with a HUMAN-confirmed tag is never touched, in any bucket.

Usage:
    python backend/scripts/retag_answer_led.py                     # dry run: report only, no DB writes
    python backend/scripts/retag_answer_led.py --limit 50           # dry run, capped to 50 questions
    python backend/scripts/retag_answer_led.py --apply               # write the changes (after you have
                                                                       # reviewed the dry run's report)

Dry run: the candidate SELECT against the real database is wrapped in a READ ONLY transaction and
rolled back (nothing committed); the actual Gemini-tagging pass then runs the exact same
tag_questions_batched() call against a throwaway, in-memory-schema SQLite mirror seeded with
copies of just the targeted questions (and their current tag row, so the HUMAN-protection check
behaves identically to production) -- so what you see in the report is what production would get,
without a single write landing on production. The mirror file is deleted when the script exits.
Gemini is still called for real during the dry run (there is no way to preview realistic output
otherwise); the report prints the token/call cost so you can see what it cost before deciding to
apply.

Apply: re-reads the same candidate set (read-only), writes a JSON rollback snapshot of every
targeted question's OLD tag fields to a timestamped folder OUTSIDE the repo, then re-tags for real
in small batches (one committed transaction per --batch-size questions, default 100), and finally
re-runs the bucket survey and prints before/after counts.

Needs DATABASE_URL set. Never reads or prints the key/DSN value itself.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

# repo root, so `backend.*` imports resolve the same way every other script here does.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Set before backend.app.config/database import so the app's own boot guard is live for a real
# Neon DATABASE_URL: it fails loudly instead of silently falling back to an ephemeral SQLite if
# the connection is flaky, rather than letting this script quietly "succeed" against the wrong DB.
if (os.environ.get("DATABASE_URL") or "").startswith(("postgres://", "postgresql://")):
    os.environ.setdefault("REQUIRE_POSTGRES", "1")

import asyncio  # noqa: E402


def _read_candidates(db):
    """Read-only bucket survey. Always wrapped in a READ ONLY transaction that is rolled back --
    this function never writes, in dry-run OR apply mode (apply writes happen later, separately)."""
    from sqlalchemy import text
    from backend.app.models.question import Question
    from backend.app.models.qshala_tag import QshalaTag
    from backend.app.services.tagging.qshala_rules import is_answer_led

    is_pg = db.get_bind().dialect.name == "postgresql"
    if is_pg:
        db.execute(text("SET TRANSACTION READ ONLY"))

    try:
        questions = db.query(Question).all()
        latest_tag: dict[str, QshalaTag] = {}
        for t in db.query(QshalaTag).order_by(QshalaTag.created_at.desc()).all():
            latest_tag.setdefault(t.question_id, t)   # newest first -> first write wins

        buckets: dict[str, list[str]] = {"a_answer_led_weak": [], "b_untagged": [], "c_review_answer_led": []}
        human_protected = 0
        by_id: dict[str, dict] = {}
        for q in questions:
            tag = latest_tag.get(q.id)
            if tag is not None and tag.status == "superseded":
                tag = None          # a superseded tag was withdrawn on purpose (e.g. a repair): the question needs a new one
            if tag is not None and tag.source == "HUMAN" and tag.status == "confirmed":
                human_protected += 1
                continue
            answer_led = is_answer_led(q.question_text, q.question_format, question_images=q.question_images)
            no_tag = tag is None
            weak_or_draft = tag is not None and (tag.status == "draft" or (tag.fit in ("weak", "none")))
            member_of = []
            if answer_led and (no_tag or weak_or_draft):
                member_of.append("a_answer_led_weak")
            if no_tag:
                member_of.append("b_untagged")
            if tag is not None and tag.status == "draft" and tag.source == "LLM" and answer_led:
                member_of.append("c_review_answer_led")
            if not member_of:
                continue
            for b in member_of:
                buckets[b].append(q.id)
            by_id[q.id] = {
                "question": q,
                "buckets": member_of,
                "old_tag": tag,
            }
        return by_id, buckets, human_protected, len(questions)
    finally:
        db.rollback()      # read-only phase; never commit, even on the "nothing happened" path


def _tag_snapshot(tag) -> dict:
    if tag is None:
        return {"status": None, "topic_l1": None, "subtopic_l2": None, "keywords_l3": None,
                "fit": None, "source": None}
    return {"status": tag.status, "topic_l1": tag.topic_l1, "subtopic_l2": tag.subtopic_l2,
            "keywords_l3": tag.keywords_l3, "fit": tag.fit, "source": tag.source}


def _write_report(out_dir: Path, rows: list[dict], stats: dict, buckets: dict, human_protected: int,
                  total_questions: int, mode: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"retag_answer_led_{mode}_{stamp}.json"
    csv_path = out_dir / f"retag_answer_led_{mode}_{stamp}.csv"

    json_path.write_text(json.dumps({
        "mode": mode, "generated_at": stamp, "total_questions_in_db": total_questions,
        "human_protected_skipped": human_protected,
        "bucket_counts": {k: len(v) for k, v in buckets.items()},
        "gemini_stats": stats, "rows": rows,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["question_id", "buckets", "old_status", "old_topic_l1", "old_subtopic_l2",
                   "old_keywords", "old_fit", "new_status", "new_topic_l1", "new_subtopic_l2",
                   "new_keywords", "new_fit", "new_confidence", "new_reason"])
        for r in rows:
            old, new = r["old"], r["new"]
            w.writerow([r["question_id"], ";".join(r["buckets"]),
                       old["status"], old["topic_l1"], old["subtopic_l2"], old["keywords_l3"], old["fit"],
                       new.get("status"), new.get("topic_l1"), new.get("subtopic_l2"),
                       new.get("keywords_l3"), new.get("fit"), new.get("confidence"), new.get("reason")])
    return json_path


def _make_provider_and_model():
    from backend.app.config import settings
    from backend.app.services.ai.gemini_provider import GeminiLLMProvider
    model = getattr(settings, "QSHALA_TAGGER_MODEL", None) or "gemini-3.5-flash-lite"
    return GeminiLLMProvider(), model


# --------------------------------------------------------------------- dry-run (SQLite mirror)

def _run_dry_run(targets: dict, args) -> tuple[list[dict], dict]:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.app.database import ensure_schema_columns
    from backend.app.models.document import Document
    from backend.app.models.question import Question
    from backend.app.models.qshala_tag import QshalaTag
    from backend.app.services.tagging import qshala_batch

    mirror_path = Path(tempfile.gettempdir()) / f"qshala_retag_mirror_{uuid.uuid4().hex}.db"
    mirror_engine = create_engine(f"sqlite:///{mirror_path}", connect_args={"check_same_thread": False})
    try:
        ensure_schema_columns(force=True, target_engine=mirror_engine, run_repairs=False)
        MirrorSession = sessionmaker(autocommit=False, autoflush=False, bind=mirror_engine)
        mdb = MirrorSession()
        try:
            seen_docs = set()
            mirror_questions = []
            for qid, info in targets.items():
                q = info["question"]
                if q.document_id not in seen_docs:
                    mdb.add(Document(id=q.document_id, filename="(retag dry-run mirror)", title="",
                                     year=0, file_type="pptx", storage_path="x", file_size_bytes=0,
                                     processing_status="COMPLETED"))
                    seen_docs.add(q.document_id)
                mq = Question(id=q.id, document_id=q.document_id, question_text=q.question_text,
                             answer=q.answer, topic=q.topic or "Uncategorised",
                             question_format=q.question_format, format_payload=q.format_payload,
                             explanation=q.explanation, question_images=q.question_images or [],
                             answer_images=q.answer_images or [], visual_clues=getattr(q, "visual_clues", None))
                mdb.add(mq)
                old_tag = info["old_tag"]
                if old_tag is not None:
                    mdb.add(QshalaTag(question_id=q.id, taxonomy_version=old_tag.taxonomy_version,
                                      model_version=old_tag.model_version, source=old_tag.source,
                                      status=old_tag.status, topic_l1=old_tag.topic_l1,
                                      subtopic_l2=old_tag.subtopic_l2, keywords_l3=old_tag.keywords_l3,
                                      fit=old_tag.fit, confidence=old_tag.confidence))
                mirror_questions.append(mq)
            mdb.flush()

            provider, model = _make_provider_and_model()
            stats = asyncio.run(qshala_batch.tag_questions_batched(
                mdb, mirror_questions, provider=provider, model=model, actor="retag_answer_led_dry_run"))
            mdb.commit()      # commits to the THROWAWAY mirror only; production was never touched

            rows = []
            for qid, info in targets.items():
                new_tag = (mdb.query(QshalaTag).filter(QshalaTag.question_id == qid)
                          .order_by(QshalaTag.created_at.desc()).first())
                rows.append({"question_id": qid, "buckets": info["buckets"],
                            "old": _tag_snapshot(info["old_tag"]),
                            "new": {**_tag_snapshot(new_tag),
                                   "confidence": getattr(new_tag, "confidence", None),
                                   "reason": getattr(new_tag, "verification_notes", None)}})
            return rows, stats
        finally:
            mdb.close()
    finally:
        mirror_engine.dispose()
        try:
            mirror_path.unlink(missing_ok=True)
        except Exception:
            pass


# --------------------------------------------------------------------- apply (production)

def _save_snapshot(snapshot_dir: Path, targets: dict) -> Path:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = snapshot_dir / f"retag_answer_led_rollback_{stamp}.json"
    payload = {qid: {"buckets": info["buckets"], "old_tag": _tag_snapshot(info["old_tag"])}
              for qid, info in targets.items()}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _run_apply(db, targets: dict, batch_size: int) -> tuple[list[dict], dict]:
    from backend.app.models.question import Question
    from backend.app.models.qshala_tag import QshalaTag
    from backend.app.services.tagging import qshala_batch

    provider, model = _make_provider_and_model()
    qids = list(targets.keys())
    rows: list[dict] = []
    agg_stats = {"confirmed": 0, "draft": 0, "chunks": 0, "failed_chunks": 0,
                "tokens_in": 0, "tokens_out": 0, "tokens_think": 0, "tokens_cached": 0, "calls": 0}

    for i in range(0, len(qids), batch_size):
        batch_ids = qids[i:i + batch_size]
        batch_questions = db.query(Question).filter(Question.id.in_(batch_ids)).all()
        stats = asyncio.run(qshala_batch.tag_questions_batched(
            db, batch_questions, provider=provider, model=model, actor="retag_answer_led_apply"))
        db.commit()        # small transaction: one commit per batch, not one for the whole run
        for k in agg_stats:
            agg_stats[k] += stats.get(k, 0)

        for qid in batch_ids:
            new_tag = (db.query(QshalaTag).filter(QshalaTag.question_id == qid)
                      .order_by(QshalaTag.created_at.desc()).first())
            rows.append({"question_id": qid, "buckets": targets[qid]["buckets"],
                        "old": _tag_snapshot(targets[qid]["old_tag"]),
                        "new": {**_tag_snapshot(new_tag),
                               "confidence": getattr(new_tag, "confidence", None),
                               "reason": getattr(new_tag, "verification_notes", None)}})
        print(f"  applied batch {i // batch_size + 1}: {len(batch_ids)} questions "
             f"({agg_stats['confirmed']} confirmed, {agg_stats['draft']} draft so far)")
    return rows, agg_stats


# --------------------------------------------------------------------- main

def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    ap.add_argument("--limit", type=int, default=0, help="cap the number of questions processed (0 = no cap)")
    ap.add_argument("--batch-size", type=int, default=100,
                    help="questions per committed transaction during --apply (default 100)")
    ap.add_argument("--out-dir", type=str, default=None,
                    help="where to write the report (and, for --apply, the rollback snapshot). "
                        "Default: ~/.qshala-backfills/ (outside the repo).")
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir) if args.out_dir else Path.home() / ".qshala-backfills"

    from backend.app.database import SessionLocal

    db = SessionLocal()
    is_pg = db.get_bind().dialect.name == "postgresql"
    try:
        targets, buckets, human_protected, total_questions = _read_candidates(db)
    finally:
        pass  # _read_candidates already rolled back; session stays open for apply's writes below

    if args.limit:
        targets = dict(list(targets.items())[:args.limit])

    print(f"DB: {'postgresql' if is_pg else db.get_bind().dialect.name} | {total_questions} questions total")
    print(f"Buckets: a_answer_led_weak={len(buckets['a_answer_led_weak'])} "
         f"b_untagged={len(buckets['b_untagged'])} c_review_answer_led={len(buckets['c_review_answer_led'])}")
    print(f"HUMAN-confirmed (never touched): {human_protected}")
    print(f"Target questions this run (union, deduped{', capped at ' + str(args.limit) if args.limit else ''}): "
         f"{len(targets)}")

    if not targets:
        print("Nothing to do.")
        db.close()
        return 0

    if not args.apply:
        print("\nDRY RUN -- no production writes. Re-tagging against a throwaway SQLite mirror "
             "(Gemini is called for real; nothing lands on the real database).")
        rows, stats = _run_dry_run(targets, args)
        report_path = _write_report(out_dir, rows, stats, buckets, human_protected, total_questions, "dry_run")
        print(f"\nGemini: {stats['calls']} call(s), {stats['tokens_in']:,} in / {stats['tokens_out']:,} out tokens "
             f"({stats['confirmed']} would confirm, {stats['draft']} would stay draft)")
        print(f"Report written to {report_path} (and matching .csv next to it).")
        print("Review at least 20 rows, then re-run with --apply to write these changes.")
    else:
        snapshot_path = _save_snapshot(out_dir / "snapshots", targets)
        print(f"\nRollback snapshot saved to {snapshot_path} -- keep this; it has the OLD values for "
             f"every row this run touches.")
        rows, stats = _run_apply(db, targets, args.batch_size)
        report_path = _write_report(out_dir, rows, stats, buckets, human_protected, total_questions, "applied")
        print(f"\nAPPLIED: {stats['confirmed']} confirmed, {stats['draft']} draft "
             f"({stats['calls']} Gemini call(s), {stats['tokens_in']:,} in / {stats['tokens_out']:,} out tokens)")
        print(f"Report written to {report_path} (and matching .csv next to it).")

        db2 = SessionLocal()
        try:
            _, after_buckets, after_human, after_total = _read_candidates(db2)
        finally:
            db2.close()
        print("\nBefore -> after (remaining in each bucket):")
        for k in buckets:
            print(f"  {k}: {len(buckets[k])} -> {len(after_buckets[k])}")

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
