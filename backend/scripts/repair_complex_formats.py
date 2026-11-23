"""Repair Arrange the Order, List It, Image Reveal and Connect questions that older ingestion stored in the old shape.

What was wrong: decks ingested before the format-aware extractor existed went through the older slide-pattern path. For
Arrange the Order that path took the FIRST WORD of the reveal slide as the answer (so the answer was "1", "Slide 52" or
a single name), called the items "Item 1..4" and kept the reveal text as the explanation. List It, Image Reveal and
Connect rows from the same decks are in the old shape too (no `extraction` block, so every renderer ignores their
payload and falls back to the merged question text).

This script re-reads each ORIGINAL deck offline (nothing is uploaded, no AI is called) with today's extractor and, for every
stored question in the old shape, replaces its text, answer, explanation, format payload, picture/audio/video lists,
review flag and hash with the freshly extracted question for the same slide. It also drops a format name used as a tag
and clears the embedding (run backfill_embeddings.py afterwards).

Matching: same format family (ARRANGE_ORDER, LIST_IT, IMAGE_REVEAL, CONNECT/CONNECT_4) and the nearest extracted question
within 2 slides. Arrange rows are always replaced (their old answers are wrong). List It, Image Reveal and Connect rows are
replaced only when the new reading has an answer and is not flagged for review; the rest are listed in the report.
Never touched: questions a person edited, questions whose new pictures are not reachable, rows with no match.

    python backend/scripts/repair_complex_formats.py --out-dir DIR --deck DOC_ID PPTX [--deck ...]     # dry run
    python backend/scripts/repair_complex_formats.py --out-dir DIR --decks-dir DIR2                      # decks named <doc_id>.pptx
    python backend/scripts/repair_complex_formats.py --out-dir DIR --decks-dir DIR2 --apply
    python backend/scripts/repair_complex_formats.py --rollback FILE

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

FAMILY = {"ARRANGE_ORDER": "ARRANGE_ORDER", "LIST_IT": "LIST_IT", "IMAGE_REVEAL": "IMAGE_REVEAL",
          "CONNECT": "CONNECT", "CONNECT_4": "CONNECT"}
URL_FIELDS = ("format_payload", "question_images", "answer_images", "image_refs", "audio_refs", "question_audio",
              "answer_audio", "video_refs", "question_videos", "answer_videos", "raw_media_refs")
EDITED_FIELDS = ("question_text", "answer", "format_payload", "question_images", "answer_images", "image_refs", "images")
FORMAT_TAGS = {"arrange the order (ticker tangle)", "arrange the order", "ticker tangle", "list it", "list it!", "image reveal",
               "connect", "connect (4 pictures)", "connect 4"}
MAX_SLIDE_DISTANCE = 2


def norm(s) -> str:
    return " ".join((s or "").lower().split())


def is_old_shape(q) -> bool:
    return FAMILY.get(q.question_format or "") is not None and not (q.format_payload or {}).get("extraction")


def accept_new(family: str, new_q: dict) -> bool:
    """Arrange rows (same format) are always replaced; everything else only when the new reading is clean."""
    if family == "ARRANGE_ORDER" and FAMILY.get(new_q.get("question_format") or "") == "ARRANGE_ORDER":
        return True
    ing = new_q.get("_ingest") or {}
    return bool((new_q.get("answer") or "").strip()) and not ing.get("needs_review")


def plan_row(stored, new_q: dict, rewrite, keep_text: bool = False) -> dict | None:
    """{field: new value} for one stored question, or None when nothing would change. Pure: no database access.
    keep_text: the slide was only mislabelled (a plain question typed as List It): keep its stored text and answer."""
    from backend.app.services.deduplication.deduplicator import Deduplicator
    from backend.app.services.tagging.qshala_rules import is_format_or_round_label
    new = {
        "question_text": new_q["question_text"],
        "answer": new_q["answer"],
        "explanation": new_q.get("explanation") or "",
        "question_format": new_q["question_format"],
        "question_type": new_q.get("question_type") or new_q["question_format"],
        "format_needs_review": bool((new_q.get("_ingest") or {}).get("needs_review")),
        "content_hash": Deduplicator.compute_hash(new_q["question_text"], new_q["answer"]),
        "tags": [t for t in (stored.tags or []) if norm(t) not in FORMAT_TAGS],
        "subtopic": None if stored.subtopic and is_format_or_round_label(stored.subtopic) else stored.subtopic,
    }
    for f in URL_FIELDS:
        val = new_q.get(f)
        if f == "format_payload":
            val = rewrite(val or {})
        else:
            val = rewrite(val if val is not None else [])
        new[f] = val
    if keep_text:
        for f in ("question_text", "answer", "explanation", "content_hash"):
            new.pop(f)
    changed = {k: v for k, v in new.items() if v != getattr(stored, k, None)}
    if not changed:
        return None
    changed["embedding"] = None
    changed["embedding_model"] = None
    return changed


def _words(s) -> set:
    return {w for w in norm(s).replace("’", "'").split() if len(w) > 2}


def similar(a, b, threshold: float = 0.5) -> bool:
    """Word overlap (share of the shorter text's words found in the other): the old and new readings of one slide."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= threshold


def match_new(stored, slide_no, candidates: list, used: set):
    """Same-format extracted question nearest to the stored slide. Failing that (the old path mislabelled the slide, e.g.
    plain questions typed as List It), the nearest question of any format whose text clearly is the same slide."""
    fam = FAMILY[stored.question_format]
    near = [(abs((c.get("slide_number") or 0) - (slide_no or 0)), i, c) for i, c in enumerate(candidates) if i not in used]
    near = [p for p in near if p[0] <= MAX_SLIDE_DISTANCE]
    same = [p for p in near if FAMILY.get(p[2].get("question_format") or "") == fam]
    if same:
        _d, i, c = min(same, key=lambda p: (p[0], p[1]))
        return i, c
    other = [p for p in near if similar(stored.question_text, p[2].get("question_text"))]
    if other:
        _d, i, c = min(other, key=lambda p: (p[0], p[1]))
        return i, c
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deck", nargs=2, action="append", metavar=("DOC_ID", "PPTX"), default=[])
    ap.add_argument("--decks-dir", default=None, help="folder of decks named <doc_id>.pptx")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--rollback", default=None)
    args = ap.parse_args()

    from sqlalchemy import text
    import repair_media_questions as rmq
    from backend.app.database import SessionLocal
    from backend.app.models.document import Document, Slide
    from backend.app.models.question import Question
    from backend.app.models.question_meta import QuestionEditHistory

    out_dir = Path(args.out_dir) if args.out_dir else Path(tempfile.gettempdir()) / "qshala_complex_formats"
    if out_dir.resolve().is_relative_to(ROOT):
        print("Refusing to write reports inside the repo; pass --out-dir outside it.")
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    decks = list(args.deck)
    if args.decks_dir:
        for f in sorted(Path(args.decks_dir).glob("*.pptx")):
            decks.append((f.stem, str(f)))

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
            db.commit()
            print("rolled back", len(data["rows"]), "questions")
            return 0
        if not decks:
            print("Name at least one deck: --deck DOC_ID PATH, or --decks-dir DIR")
            return 2

        edited = {r[0] for r in db.query(QuestionEditHistory.question_id)
                  .filter(QuestionEditHistory.field.in_(EDITED_FIELDS)).distinct().all()}
        rows, report = [], {"decks": []}
        for doc_id, path in decks:
            doc = db.get(Document, doc_id)
            slide_rows = db.query(Slide.id, Slide.slide_number).filter(Slide.document_id == doc_id).all()
            sid_to_no = {i: n for i, n in slide_rows}
            stored = [q for q in db.query(Question).filter(Question.document_id == doc_id).all() if is_old_shape(q)]
            d = {"document_id": doc_id, "filename": doc.filename if doc else None, "old_shape": len(stored), "to_fix": 0,
                 "skipped_human_edited": 0, "skipped_new_reading_not_clean": [], "unreachable_skipped": 0, "no_match": [],
                 "unchanged": 0}
            if not stored:
                report["decks"].append(d)
                continue
            base = next((u.split("/slides/")[0] for q in db.query(Question).filter(Question.document_id == doc_id).all()
                         for u in (q.question_images or []) + (q.image_refs or [])
                         if isinstance(u, str) and u.startswith("http") and "/slides/" in u), None)
            if not base:
                print(doc_id[:8], ": cannot work out the public media address; skipped")
                continue
            _slides, new, _ex = rmq.extract_deck(path, doc_id, {n: i for i, n in slide_rows})
            rewrite = lambda o: rmq.rewrite_urls(o, doc_id, base)  # noqa: E731
            used: set = set()
            plans = []
            for st in sorted(stored, key=lambda q: sid_to_no.get(q.slide_id) or 0):
                if st.id in edited:
                    d["skipped_human_edited"] += 1
                    continue
                idx, n = match_new(st, sid_to_no.get(st.slide_id), new, used)
                if n is None:
                    d["no_match"].append({"id": st.id, "slide": sid_to_no.get(st.slide_id), "format": st.question_format,
                                          "text": (st.question_text or "")[:80]})
                    continue
                used.add(idx)
                if not accept_new(FAMILY[st.question_format], n):
                    d["skipped_new_reading_not_clean"].append({"id": st.id, "slide": sid_to_no.get(st.slide_id),
                                                               "format": st.question_format, "answer": (n.get("answer") or "")[:60],
                                                               "reasons": (n.get("_ingest") or {}).get("reasons")})
                    continue
                cross = FAMILY[st.question_format] != FAMILY.get(n.get("question_format") or "")
                ch = plan_row(st, n, rewrite, keep_text=cross)
                if ch is None:
                    d["unchanged"] += 1
                    continue
                plans.append((st, ch, n))
            urls = set()
            for _st, ch, _n in plans:
                rmq.collect_urls({k: ch.get(k) for k in URL_FIELDS}, urls)
            with ThreadPoolExecutor(8) as pool:
                ok = dict(zip(sorted(urls), pool.map(rmq.url_exists, sorted(urls))))
            for st, ch, n in plans:
                bad = rmq.collect_urls({k: ch.get(k) for k in URL_FIELDS}, set())
                if any(not ok.get(u) for u in bad):
                    d["unreachable_skipped"] += 1
                    continue
                d["to_fix"] += 1
                rows.append({"question_id": st.id, "document_id": doc_id, "slide": sid_to_no.get(st.slide_id),
                             "format": st.question_format, "new_format": ch.get("question_format", st.question_format),
                             "old": {k: getattr(st, k) for k in ch}, "new": ch,
                             "old_answer": (st.answer or "")[:100], "new_answer": (ch.get("answer") or st.answer or "")[:100],
                             "old_text": (st.question_text or "")[:120], "new_text": (ch.get("question_text") or "")[:160]})
            report["decks"].append(d)
            print({k: (v if not isinstance(v, list) else len(v)) for k, v in d.items() if k != "document_id"})
        db.rollback()
        rp = out_dir / f"repair_complex_{'apply' if args.apply else 'dry_run'}_{stamp}.json"
        rp.write_text(json.dumps({**report, "rows": rows}, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
        print("questions to fix:", len(rows))
        print("report:", rp)
        if not args.apply:
            print("dry run only: nothing was written. Re-run with --apply to write.")
            return 0
        snap = out_dir / f"repair_complex_ROLLBACK_{stamp}.json"
        snap.write_text(json.dumps({"rows": rows}, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
        print("rollback file:", snap)
        for i in range(0, len(rows), 50):
            for r in rows[i:i + 50]:
                q = db.get(Question, r["question_id"])
                if q is not None:
                    for f, v in r["new"].items():
                        setattr(q, f, v)
            db.commit()
        print("applied", len(rows), "questions. Now run backfill_embeddings.py")
        return 0
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    sys.exit(main())
