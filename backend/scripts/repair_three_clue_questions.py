"""Repair Three Clues (STEPPED_CLUE) questions that older ingestion stored with placeholder clues and a wrong topic.

What was wrong (ZVQ 24-25 Three Clues and similar decks): each clue is its own slide, but the old path read the whole
question from one slide, so clues 2 and 3 were stored as the placeholders "Additional context clue" and "Direct
giveaway clue", clue labels leaked into the clue text ("... Clue 2: +15/-10"), and the topic became the deck's round
label ("THREE CLUES") with the format name ("Stepped Clue (Portfolio Builder)") added as a tag.

This script re-reads each ORIGINAL deck offline (nothing is uploaded, no AI is called), runs the current
format-aware extractor, and for every stored question that matches an extracted one (same answer, same slide within
2) it replaces: question_text, format_payload (clues with their own text, points and pictures), the picture lists,
format_needs_review and content_hash. It also removes the format name from `tags`, replaces a round/format label used as
`subtopic`, clears the embedding (re-run backfill_embeddings.py) and supersedes the old non-human tag rows so that
retag_answer_led.py tags them again from the real clues and the answer.

Skipped (never touched): questions a person edited (question_edit_history on text, payload or picture fields),
questions whose new pictures are not reachable, and stored questions with no extracted match (listed in the report).

    python backend/scripts/repair_three_clue_questions.py --out-dir DIR --deck DOC_ID PPTX [--deck ...]        # dry run
    python backend/scripts/repair_three_clue_questions.py --out-dir DIR --deck DOC_ID PPTX --apply
    python backend/scripts/repair_three_clue_questions.py --rollback FILE

Needs DATABASE_URL set; never prints it. Reports and the rollback file go to --out-dir (outside the repo).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
if (os.environ.get("DATABASE_URL") or "").startswith(("postgres://", "postgresql://")):
    os.environ.setdefault("REQUIRE_POSTGRES", "1")

FIELDS = ("question_text", "format_payload", "question_images", "answer_images", "image_refs",
          "format_needs_review", "content_hash", "tags", "subtopic", "embedding", "embedding_model")
EDITED_FIELDS = ("question_text", "format_payload", "question_images", "answer_images", "image_refs", "images")
FORMAT_TAGS = {"stepped clue (portfolio builder)", "stepped clue", "three clues", "three clue"}
PLACEHOLDERS = {"additional context clue", "direct giveaway clue"}
MAX_SLIDE_DISTANCE = 2


def norm(s) -> str:
    return " ".join((s or "").lower().split())


def is_broken(stored_payload) -> bool:
    """True when the stored payload is the legacy shape (no extraction block) or carries placeholder clue text."""
    p = stored_payload or {}
    if not p.get("extraction"):
        return True
    return any(norm(c.get("text")) in PLACEHOLDERS for c in p.get("clues", []))


def match_extracted(stored, slide_no: int, by_answer: dict):
    """Best extracted question for a stored one: same answer, nearest slide, within MAX_SLIDE_DISTANCE."""
    cands = by_answer.get(norm(stored.answer), [])
    if not cands or slide_no is None:
        return None
    best = min(cands, key=lambda e: abs((e.get("slide_number") or 0) - slide_no))
    return best if abs((best.get("slide_number") or 0) - slide_no) <= MAX_SLIDE_DISTANCE else None


def plan_row(stored, new_q: dict, rewrite, ok_urls: set | None = None) -> dict | None:
    """{field: new value} for one stored question, or None when nothing would change. Pure: no database access."""
    from backend.app.services.deduplication.deduplicator import Deduplicator
    from backend.app.services.tagging.qshala_rules import is_format_or_round_label
    new = {
        "question_text": new_q["question_text"],
        "format_payload": rewrite({**(new_q.get("format_payload") or {}), "extraction": new_q.get("_ingest") or {}})
        if not (new_q.get("format_payload") or {}).get("extraction") else rewrite(new_q["format_payload"]),
        "question_images": rewrite(new_q.get("question_images") or []),
        "answer_images": rewrite(new_q.get("answer_images") or []),
        "image_refs": rewrite(new_q.get("image_refs") or []),
        "format_needs_review": bool((new_q.get("_ingest") or {}).get("needs_review")),
        "content_hash": Deduplicator.compute_hash(new_q["question_text"], new_q["answer"]),
        "tags": [t for t in (stored.tags or []) if norm(t) not in FORMAT_TAGS],
        "subtopic": None if is_format_or_round_label(stored.subtopic or "") and stored.subtopic else stored.subtopic,
    }
    changed = {k: v for k, v in new.items() if v != getattr(stored, k)}
    if not changed:
        return None
    changed["embedding"] = None
    changed["embedding_model"] = None
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deck", nargs=2, action="append", metavar=("DOC_ID", "PPTX"), default=[])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--rollback", default=None)
    args = ap.parse_args()

    from sqlalchemy import text
    import repair_media_questions as rmq
    from backend.app.database import SessionLocal
    from backend.app.models.document import Slide
    from backend.app.models.qshala_tag import QshalaTag
    from backend.app.models.question import Question
    from backend.app.models.question_meta import QuestionEditHistory

    out_dir = Path(args.out_dir) if args.out_dir else Path(tempfile.gettempdir()) / "qshala_three_clue"
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
            for r in data["rows"]:
                q = db.get(Question, r["question_id"])
                if q is not None:
                    for f, v in r["old"].items():
                        setattr(q, f, v)
            for tid in data.get("superseded_tags", []):
                t = db.get(QshalaTag, tid)
                if t is not None:
                    t.status = "confirmed"
            db.commit()
            print("rolled back", len(data["rows"]), "questions;", len(data.get("superseded_tags", [])), "tags restored")
            return 0
        if not args.deck:
            print("Name at least one deck: --deck DOC_ID PATH")
            return 2
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION READ ONLY"))

        edited = {r[0] for r in db.query(QuestionEditHistory.question_id)
                  .filter(QuestionEditHistory.field.in_(EDITED_FIELDS)).distinct().all()}
        rows, tag_ids, report = [], [], {"decks": []}
        for doc_id, path in args.deck:
            slide_rows = db.query(Slide.id, Slide.slide_number).filter(Slide.document_id == doc_id).all()
            sid_to_no = {i: n for i, n in slide_rows}
            stored = db.query(Question).filter(Question.document_id == doc_id).all()
            base = next((u.split("/slides/")[0] for q in stored for u in (q.question_images or []) + (q.image_refs or [])
                         if isinstance(u, str) and u.startswith("http") and "/slides/" in u), None)
            if not base:
                print(doc_id, ": cannot work out the public media address; skipped")
                continue
            slides, new, _ex = rmq.extract_deck(path, doc_id, {n: i for i, n in slide_rows})
            by_answer: dict = {}
            for q in new:
                if q.get("question_format") == "STEPPED_CLUE":
                    by_answer.setdefault(norm(q.get("answer")), []).append(q)
            rewrite = lambda o: rmq.rewrite_urls(o, doc_id, base)  # noqa: E731
            d = {"document_id": doc_id, "stored": len(stored), "extracted_stepped": sum(map(len, by_answer.values())),
                 "to_fix": 0, "already_ok": 0, "skipped_human_edited": 0, "unreachable_skipped": 0, "no_match": []}
            plans = []
            for st in stored:
                if st.question_format != "STEPPED_CLUE" or not is_broken(st.format_payload) and not _bad_topic(st):
                    d["already_ok"] += 1
                    continue
                if st.id in edited:
                    d["skipped_human_edited"] += 1
                    continue
                n = match_extracted(st, sid_to_no.get(st.slide_id), by_answer)
                if n is None:
                    d["no_match"].append({"id": st.id, "slide": sid_to_no.get(st.slide_id), "text": (st.question_text or "")[:80]})
                    continue
                ch = plan_row(st, n, rewrite)
                if ch:
                    plans.append((st, ch))
            urls = set()
            for _st, ch in plans:
                rmq.collect_urls({k: ch.get(k) for k in ("format_payload", "question_images", "answer_images", "image_refs")}, urls)
            with ThreadPoolExecutor(8) as pool:
                ok = dict(zip(sorted(urls), pool.map(rmq.url_exists, sorted(urls))))
            for st, ch in plans:
                bad = rmq.collect_urls({k: ch.get(k) for k in ("format_payload", "question_images", "answer_images", "image_refs")}, set())
                if any(not ok.get(u) for u in bad):
                    d["unreachable_skipped"] += 1
                    continue
                d["to_fix"] += 1
                rows.append({"question_id": st.id, "document_id": doc_id, "slide": sid_to_no.get(st.slide_id),
                             "answer": st.answer, "old": {k: getattr(st, k) for k in ch}, "new": ch,
                             "old_text": (st.question_text or "")[:120], "new_text": ch.get("question_text", "")[:120]})
                tag_ids += [t for (t,) in db.query(QshalaTag.id).filter(
                    QshalaTag.question_id == st.id, QshalaTag.status != "superseded", QshalaTag.source != "HUMAN").all()]
            report["decks"].append(d)
            print({k: (v if k != "no_match" else len(v)) for k, v in d.items()})
        db.rollback()
        rp = out_dir / f"repair_three_clue_{'apply' if args.apply else 'dry_run'}_{stamp}.json"
        rp.write_text(json.dumps({**report, "tags_to_supersede": len(tag_ids), "rows": rows}, indent=1, default=str), encoding="utf-8")
        print("questions to fix:", len(rows), "| old tag rows to supersede:", len(tag_ids))
        print("report:", rp)
        if not args.apply:
            print("dry run only: nothing was written. Re-run with --apply to write.")
            return 0
        snap = out_dir / f"repair_three_clue_ROLLBACK_{stamp}.json"
        snap.write_text(json.dumps({"rows": rows, "superseded_tags": tag_ids}, indent=1, default=str), encoding="utf-8")
        print("rollback file:", snap)
        for i in range(0, len(rows), 50):
            for r in rows[i:i + 50]:
                q = db.get(Question, r["question_id"])
                if q is not None:
                    for f, v in r["new"].items():
                        setattr(q, f, v)
            db.commit()
        for i in range(0, len(tag_ids), 200):
            for t in db.query(QshalaTag).filter(QshalaTag.id.in_(tag_ids[i:i + 200])).all():
                t.status = "superseded"
            db.commit()
        print("applied", len(rows), "questions; superseded", len(tag_ids), "tag rows")
        return 0
    finally:
        db.rollback()
        db.close()


def _bad_topic(q) -> bool:
    from backend.app.services.tagging.qshala_rules import is_format_or_round_label
    return bool(q.subtopic and is_format_or_round_label(q.subtopic)) or any(norm(t) in FORMAT_TAGS for t in (q.tags or []))


if __name__ == "__main__":
    sys.exit(main())
