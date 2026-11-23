"""Clear tags that are nothing but a format name ("Video Clue", "Visual Clue", "Stepped Clue", ...) so the questions are tagged again.

Why: an older tagger added the format name as a keyword and, when the model returned no usable keywords, that was the ONLY
tag a question had ("Video Clue" on a rattlesnake question). A format name is never a tag (`is_format_or_round_label`).

For every question whose active, non-human tag rows carry only format names as keywords (and that has no human-confirmed tag):
  * those tag rows are marked `superseded` (the history stays), and
  * `questions.tags` loses its format-name entries.
Run `retag_answer_led.py` afterwards: it treats a question with only superseded tags as untagged and tags it from the question
and its answer. Questions a person tagged are never touched.

    python backend/scripts/fix_format_only_tags.py --out-dir DIR              # dry run: counts, per deck, and the id list
    python backend/scripts/fix_format_only_tags.py --out-dir DIR --apply
    python backend/scripts/fix_format_only_tags.py --rollback FILE

Needs DATABASE_URL set; never prints it. Reports and the rollback file go to --out-dir (outside the repo).
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


def only_format_keywords(keywords) -> bool:
    """True when there is at least one keyword and every one is a format / round label."""
    from backend.app.services.tagging.qshala_rules import is_format_or_round_label
    kws = [k for k in (keywords or []) if (k or "").strip()]
    return bool(kws) and all(is_format_or_round_label(k) for k in kws)


def clean_question_tags(tags) -> list:
    from backend.app.services.tagging.qshala_rules import is_format_or_round_label
    return [t for t in (tags or []) if not is_format_or_round_label(t)]


def select_targets(db):
    """[(question, [tag rows to supersede])] for questions whose only AI/rule tags are format names."""
    from backend.app.models.qshala_tag import QshalaTag
    from backend.app.models.question import Question
    by_q = collections.defaultdict(list)
    for t in db.query(QshalaTag).filter(QshalaTag.status != "superseded").all():
        by_q[t.question_id].append(t)
    targets = []
    for qid, rows in by_q.items():
        if any(r.source == "HUMAN" for r in rows):
            continue
        if rows and all(only_format_keywords(r.keywords_l3) for r in rows):
            targets.append((qid, rows))
    out = []
    for i in range(0, len(targets), 500):
        chunk = targets[i:i + 500]
        qs = {q.id: q for q in db.query(Question).filter(Question.id.in_([c[0] for c in chunk])).all()}
        for qid, rows in chunk:
            if qid in qs:
                out.append((qs[qid], rows))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--rollback", default=None)
    args = ap.parse_args()

    from sqlalchemy import text
    from backend.app.database import SessionLocal
    from backend.app.models.document import Document
    from backend.app.models.qshala_tag import QshalaTag
    from backend.app.models.question import Question

    out_dir = Path(args.out_dir) if args.out_dir else Path(tempfile.gettempdir()) / "qshala_format_only_tags"
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
            for tid in data["superseded_tags"]:
                t = db.get(QshalaTag, tid)
                if t is not None:
                    t.status = data["old_status"].get(tid, "confirmed")
            for qid, tags in data["old_question_tags"].items():
                q = db.get(Question, qid)
                if q is not None:
                    q.tags = tags
            db.commit()
            print("rolled back", len(data["old_question_tags"]), "questions")
            return 0
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION READ ONLY"))
        targets = select_targets(db)
        docs = {d.id: d.filename for d in db.query(Document).filter(
            Document.id.in_({q.document_id for q, _ in targets})).all()} if targets else {}
        per_deck = collections.Counter((docs.get(q.document_id, "?")[:60], q.question_format) for q, _ in targets)
        print("questions with only a format name as their tag:", len(targets))
        for (deck, fmt), n in per_deck.most_common(40):
            print(f"  {n:4} {fmt:14} {deck}")
        old_status = {t.id: t.status for _q, rows in targets for t in rows}
        snapshot = {"superseded_tags": list(old_status), "old_status": old_status,
                    "old_question_tags": {q.id: list(q.tags or []) for q, _ in targets}}
        rp = out_dir / f"format_only_tags_{'apply' if args.apply else 'dry_run'}_{stamp}.json"
        rp.write_text(json.dumps({**snapshot, "question_ids": [q.id for q, _ in targets]}, indent=1), encoding="utf-8")
        print("report:", rp)
        db.rollback()
        if not args.apply:
            print("dry run only: nothing was written. Re-run with --apply to write.")
            return 0
        snap = out_dir / f"format_only_tags_ROLLBACK_{stamp}.json"
        snap.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
        print("rollback file:", snap)
        for i in range(0, len(targets), 100):
            for q, rows in targets[i:i + 100]:
                q_live = db.get(Question, q.id)
                if q_live is not None:
                    q_live.tags = clean_question_tags(q_live.tags)
                for t in rows:
                    t_live = db.get(QshalaTag, t.id)
                    if t_live is not None:
                        t_live.status = "superseded"
            db.commit()
        print("applied to", len(targets), "questions. Now run retag_answer_led.py, then backfill_embeddings.py")
        return 0
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    sys.exit(main())
