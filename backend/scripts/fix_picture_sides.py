"""Workstream 3 backfill: a picture that a question shows is listed once, as a question picture.

Answer slides usually repeat the question's pictures, and older ingestion copied them into `answer_images`
as well, so one picture counted as both question and answer. This applies the same rule ingestion now uses
(backend.app.services.ingestion.picture_sides.separate_picture_sides) to the stored questions:
`answer_images` loses every picture that is also in `question_images`. `question_images`, `image_refs` and
every picture file are left alone, so nothing disappears from a question's gallery and no file is deleted.

Skipped (never touched):
  * questions whose pictures a person edited (a question_edit_history row on a picture field),
  * questions whose only answer-side pictures are the repeats and that have no usable answer text
    (the picture itself is the answer there).

Usage:
    python backend/scripts/fix_picture_sides.py                  # dry run: reads only, writes nothing
    python backend/scripts/fix_picture_sides.py --limit 50       # dry run on 50 questions
    python backend/scripts/fix_picture_sides.py --apply          # writes, after saving a rollback file
    python backend/scripts/fix_picture_sides.py --rollback FILE  # put the old answer_images back

Dry run and apply both write a JSON + CSV report and (apply) a rollback snapshot to a timestamped folder
OUTSIDE the repo (--out-dir, default: the system temp folder). Apply writes in small transactions
(--batch-size, default 200) and prints before/after overlap counts.

Needs DATABASE_URL set. Never reads or prints the DSN.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
if (os.environ.get("DATABASE_URL") or "").startswith(("postgres://", "postgresql://")):
    os.environ.setdefault("REQUIRE_POSTGRES", "1")

PICTURE_FIELDS = ("question_images", "answer_images", "image_refs", "images")


def _overlap(q) -> bool:
    return bool(set(q.question_images or []) & set(q.answer_images or []))


def plan_changes(questions, edited_ids: set) -> tuple[list[dict], dict]:
    """[{question_id, old_answer_images, new_answer_images, ...}] and counts. Pure: no database access."""
    from backend.app.services.ingestion.picture_sides import separate_picture_sides
    rows: list[dict] = []
    counts = {"overlapping": 0, "skipped_human_edited": 0, "skipped_picture_is_the_answer": 0, "to_change": 0}
    for q in questions:
        if not _overlap(q):
            continue
        counts["overlapping"] += 1
        if q.id in edited_ids:
            counts["skipped_human_edited"] += 1
            continue
        probe = {"answer": q.answer, "question_images": list(q.question_images or []),
                 "answer_images": list(q.answer_images or [])}
        separate_picture_sides(probe)
        if probe["answer_images"] == list(q.answer_images or []):
            counts["skipped_picture_is_the_answer"] += 1
            continue
        counts["to_change"] += 1
        rows.append({"question_id": q.id, "format": q.question_format, "document_id": q.document_id,
                     "old_answer_images": list(q.answer_images or []), "new_answer_images": probe["answer_images"],
                     "question_images": list(q.question_images or [])})
    return rows, counts


def _edited_question_ids(db) -> set:
    from backend.app.models.question_meta import QuestionEditHistory
    q = db.query(QuestionEditHistory.question_id).filter(QuestionEditHistory.field.in_(PICTURE_FIELDS)).distinct()
    return {r[0] for r in q.all()}


def _write_reports(out_dir: Path, mode: str, rows: list[dict], counts: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jp = out_dir / f"fix_picture_sides_{mode}_{stamp}.json"
    jp.write_text(json.dumps({"mode": mode, "generated_at": stamp, "counts": counts, "rows": rows}, indent=1), encoding="utf-8")
    with (out_dir / f"fix_picture_sides_{mode}_{stamp}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["question_id", "format", "document_id", "n_question_images", "n_old_answer_images", "n_new_answer_images"])
        for r in rows:
            w.writerow([r["question_id"], r["format"], r["document_id"], len(r["question_images"]),
                        len(r["old_answer_images"]), len(r["new_answer_images"])])
    return jp


def _survey(db) -> dict:
    from backend.app.models.question import Question
    qs = db.query(Question.id, Question.question_images, Question.answer_images).all()
    return {"questions": len(qs), "with_overlap": sum(1 for _, a, b in qs if set(a or []) & set(b or []))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    ap.add_argument("--limit", type=int, default=None, help="only look at this many overlapping questions")
    ap.add_argument("--batch-size", type=int, default=200)
    ap.add_argument("--out-dir", default=None, help="folder for reports and the rollback file (outside the repo)")
    ap.add_argument("--rollback", default=None, help="rollback JSON written by a previous --apply")
    args = ap.parse_args()

    from sqlalchemy import text
    from backend.app.database import SessionLocal
    from backend.app.models.question import Question

    out_dir = Path(args.out_dir) if args.out_dir else Path(tempfile.gettempdir()) / "qshala_picture_sides"
    if out_dir.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        print("Refusing to write reports inside the repo; pass --out-dir outside it.")
        return 2

    db = SessionLocal()
    try:
        is_pg = db.get_bind().dialect.name == "postgresql"
        print("database:", db.get_bind().dialect.name)

        if args.rollback:
            data = json.loads(Path(args.rollback).read_text(encoding="utf-8"))
            for i in range(0, len(data["rows"]), args.batch_size):
                for r in data["rows"][i:i + args.batch_size]:
                    q = db.get(Question, r["question_id"])
                    if q is not None:
                        q.answer_images = r["old_answer_images"]
                db.commit()
            print("rolled back", len(data["rows"]), "questions;", _survey(db))
            return 0

        if is_pg:
            db.execute(text("SET TRANSACTION READ ONLY"))
        before = _survey(db)
        edited = _edited_question_ids(db)
        candidates = [q for q in db.query(Question).all() if _overlap(q)]
        if args.limit:
            candidates = candidates[:args.limit]
        rows, counts = plan_changes(candidates, edited)
        db.rollback()
        report = _write_reports(out_dir, "apply" if args.apply else "dry_run", rows, counts)
        print("before:", before)
        print("plan:", counts)
        print("report:", report)
        if not args.apply:
            print("dry run only: nothing was written. Re-run with --apply to write.")
            return 0

        snap = out_dir / f"fix_picture_sides_ROLLBACK_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        snap.write_text(json.dumps({"rows": rows}, indent=1), encoding="utf-8")
        print("rollback file:", snap)
        for i in range(0, len(rows), args.batch_size):
            for r in rows[i:i + args.batch_size]:
                q = db.get(Question, r["question_id"])
                if q is not None and list(q.answer_images or []) == r["old_answer_images"]:   # unchanged since planning
                    q.answer_images = r["new_answer_images"]
            db.commit()
        print("after:", _survey(db))
        return 0
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    sys.exit(main())
