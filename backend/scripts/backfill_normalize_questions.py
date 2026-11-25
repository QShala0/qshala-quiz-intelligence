"""Normalize existing questions: strip MCQ letter prefix from answers, drop Points/Round lines from question text,
turn empty options on non-MCQ into NULL, remap format QA -> STANDARD_QA, and flag empty/very short rows for review.

    python backend/scripts/backfill_normalize_questions.py --out-dir DIR           # dry run
    python backend/scripts/backfill_normalize_questions.py --out-dir DIR --apply
    python backend/scripts/backfill_normalize_questions.py --rollback FILE

Needs DATABASE_URL; rollback file goes to --out-dir (outside the repo).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
if (os.environ.get("DATABASE_URL") or "").startswith(("postgres://", "postgresql://")):
    os.environ.setdefault("REQUIRE_POSTGRES", "1")

FIELDS = ("question_format", "question_text", "answer", "options", "format_needs_review")


def compute_changes(q) -> dict:
    """{field: new_value} for one question; empty when nothing to change. Never touches formatted payload headers."""
    from backend.app.services.ingestion.question_normalizer import (
        needs_review_flags, normalize_answer, normalize_options, normalize_question_format, normalize_question_text)
    fmt = normalize_question_format(q.question_format)
    new = {
        "question_format": fmt,
        "question_text": normalize_question_text(q.question_text),
        "answer": normalize_answer(q.answer, fmt),
        "options": normalize_options(q.options, fmt),
    }
    if any(needs_review_flags(new["question_text"], new["answer"]).values()) and not q.format_needs_review:
        new["format_needs_review"] = True
    return {k: v for k, v in new.items() if v != getattr(q, k)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--rollback", default=None)
    args = ap.parse_args()

    from backend.app.database import SessionLocal
    from backend.app.models.question import Question

    out_dir = Path(args.out_dir) if args.out_dir else Path(tempfile.gettempdir()) / "qshala_normalize"
    if out_dir.resolve().is_relative_to(ROOT):
        print("Refusing to write reports inside the repo; pass --out-dir outside it.")
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    db = SessionLocal()
    try:
        print("database:", db.get_bind().dialect.name)
        if args.rollback:
            data = json.loads(Path(args.rollback).read_text(encoding="utf-8"))
            for qid, old in data.items():
                q = db.get(Question, qid)
                if q is not None:
                    for k, v in old.items():
                        setattr(q, k, v)
            db.commit()
            print("rolled back", len(data), "questions")
            return 0
        changes, old = {}, {}
        for q in db.query(Question).yield_per(500):
            c = compute_changes(q)
            if c:
                changes[q.id] = c
                old[q.id] = {k: getattr(q, k) for k in c}
        counts = collections.Counter(k for c in changes.values() for k in c)
        print("questions to change:", len(changes), dict(counts))
        rp = out_dir / f"normalize_{'apply' if args.apply else 'dry_run'}_{stamp}.json"
        rp.write_text(json.dumps({"changes": changes, "old": old}, indent=1, default=str), encoding="utf-8")
        print("report:", rp)
        db.rollback()
        if not args.apply:
            print("dry run only: nothing was written. Re-run with --apply to write.")
            return 0
        snap = out_dir / f"normalize_ROLLBACK_{stamp}.json"
        snap.write_text(json.dumps(old, indent=1), encoding="utf-8")
        print("rollback file:", snap)
        ids = list(changes)
        for i in range(0, len(ids), 100):
            for qid in ids[i:i + 100]:
                q = db.get(Question, qid)
                for k, v in changes[qid].items():
                    setattr(q, k, v)
            db.commit()
        print("applied to", len(ids), "questions")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
