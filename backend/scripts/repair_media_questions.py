"""Workstream 4 backfill: put back audio/video clips and clip questions that older ingestion lost.

For each deck you name, the ORIGINAL pptx is re-read offline with today's extractor (no Gemini, no uploads), and the
result is compared with the questions already stored for that deck:
  (a) a stored question (matched by slide number) that lacks one of its clips gets the clip attached;
  (b) a clip question with no stored question is added, unless the same question text and answer already exist
      anywhere in the library (same hash = same question: it is skipped, as ingestion would). Added questions are
      marked needs-review, untagged (run retag_answer_led.py afterwards) and have no embedding yet.

Nothing is uploaded: every media file was stored in R2 when the deck was first ingested, under a name the parser
derives the same way each time. The script only writes database links, and only to files that answer a public HEAD
request. A question with any unreachable file is skipped and listed.

Usage:
    python backend/scripts/repair_media_questions.py --deck DOC_ID "C:/path/deck.pptx" [--deck ...]     # dry run
    python backend/scripts/repair_media_questions.py --deck DOC_ID "C:/path/deck.pptx" --apply          # write
    python backend/scripts/repair_media_questions.py --rollback FILE                                    # undo

Reports and the rollback file go to --out-dir (default: temp folder, outside the repo).
Needs DATABASE_URL set; never prints it. The deck file is untrusted input: it is only opened as a zip/pptx.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
if (os.environ.get("DATABASE_URL") or "").startswith(("postgres://", "postgresql://")):
    os.environ.setdefault("REQUIRE_POSTGRES", "1")

MEDIA_FIELDS = ("audio_refs", "question_audio", "answer_audio", "video_refs", "question_videos", "answer_videos", "raw_media_refs")
IMAGE_FIELDS = ("image_refs", "question_images", "answer_images")


def rewrite_urls(obj, doc_id: str, base: str):
    """Deep-copy `obj` with the parser's local media addresses turned into the public ones production uses."""
    prefix = f"/static/slides/{doc_id}/media/"
    if isinstance(obj, str):
        return f"{base}/slides/{doc_id}/media/{obj[len(prefix):]}" if obj.startswith(prefix) else obj
    if isinstance(obj, list):
        return [rewrite_urls(x, doc_id, base) for x in obj]
    if isinstance(obj, dict):
        return {k: rewrite_urls(v, doc_id, base) for k, v in obj.items()}
    return obj


def collect_urls(obj, out: set) -> set:
    if isinstance(obj, str) and obj.startswith("http"):
        out.add(obj)
    elif isinstance(obj, list):
        for x in obj:
            collect_urls(x, out)
    elif isinstance(obj, dict):
        for v in obj.values():
            collect_urls(v, out)
    return out


def plan_attach(stored_fields: dict, new_q: dict) -> dict:
    """{field: merged list} for the media fields where the new extraction has a file the stored question lacks."""
    changes = {}
    for f in MEDIA_FIELDS:
        have = list(stored_fields.get(f) or [])
        want = [u for u in (new_q.get(f) or []) if u not in have]
        if want:
            changes[f] = have + want
    return changes


def url_exists(u: str) -> bool:
    try:
        urllib.request.urlopen(urllib.request.Request(u, method="HEAD", headers={"User-Agent": "Mozilla/5.0"}), timeout=30)
        return True
    except Exception:
        return False


def extract_deck(deck_path: str, doc_id: str, slide_ids: dict):
    """Offline parse + extraction. R2 is switched off so nothing can be uploaded."""
    import tempfile as _t
    import backend.app.services.storage.r2_storage as r2mod
    r2mod.get_media_storage = lambda: None
    from backend.app.config import settings
    settings.SLIDES_DIR = Path(_t.mkdtemp(prefix="repair_"))
    from backend.app.services.ingestion.pptx_parser import PPTXParser
    from backend.app.services.ingestion.question_extractor import QuestionExtractor
    from backend.app.services.ingestion.slide_classifier import SlideClassifier
    slides = PPTXParser(deck_path, doc_id, write_to_disk=False).parse()
    for s, t in zip(slides, SlideClassifier().classify_deck(slides)):
        s["slide_type"] = t
        s["id"] = slide_ids.get(s["slide_number"])
    ex = QuestionExtractor()
    return slides, ex.extract_from_slides(slides, "repair", None), ex


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deck", nargs=2, action="append", metavar=("DOC_ID", "PPTX"), default=[])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--rollback", default=None)
    args = ap.parse_args()

    from sqlalchemy import text
    from backend.app.database import SessionLocal
    from backend.app.models.document import Document, Slide
    from backend.app.models.question import Question
    from backend.app.models.question_meta import DeckMetadata
    from backend.app.services.deduplication.deduplicator import Deduplicator

    out_dir = Path(args.out_dir) if args.out_dir else Path(tempfile.gettempdir()) / "qshala_media_repair"
    if out_dir.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        print("Refusing to write reports inside the repo; pass --out-dir outside it.")
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    db = SessionLocal()
    try:
        print("database:", db.get_bind().dialect.name)
        if args.rollback:
            data = json.loads(Path(args.rollback).read_text(encoding="utf-8"))
            for qid in data.get("created", []):
                q = db.get(Question, qid)
                if q is not None:
                    db.delete(q)
            for r in data.get("attached", []):
                q = db.get(Question, r["question_id"])
                if q is not None:
                    for f, v in r["old"].items():
                        setattr(q, f, v)
            db.commit()
            print("rolled back: removed", len(data.get("created", [])), "added questions; restored", len(data.get("attached", [])))
            return 0
        if not args.deck:
            print("Name at least one deck: --deck DOC_ID PATH")
            return 2

        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION READ ONLY"))
        library_hashes = {h for (h,) in db.query(Question.content_hash).filter(Question.content_hash.isnot(None)).all()}
        report = {"decks": []}
        attach_rows, create_rows = [], []

        for doc_id, path in args.deck:
            doc = db.get(Document, doc_id)
            if doc is None:
                print("no such deck:", doc_id)
                continue
            slide_rows = db.query(Slide.id, Slide.slide_number).filter(Slide.document_id == doc_id).all()
            slide_ids = {n: i for i, n in slide_rows}
            stored = db.query(Question).filter(Question.document_id == doc_id).all()
            by_slide = {}
            sid_to_no = {i: n for i, n in slide_rows}
            for q in stored:
                by_slide.setdefault(sid_to_no.get(q.slide_id), q)
            base = None
            for q in stored:
                for u in (q.question_images or []) + (q.video_refs or []):
                    if isinstance(u, str) and u.startswith("http") and "/slides/" in u:
                        base = u.split("/slides/")[0]
                        break
                if base:
                    break
            if not base:
                print(doc_id, ": cannot work out the public media address; skipped")
                continue

            slides, new, ex = extract_deck(path, doc_id, slide_ids)
            deck_report = {"document_id": doc_id, "file": os.path.basename(path), "stored": len(stored), "extracted": len(new),
                           "attach": [], "add": [], "duplicate_skipped": [], "unreachable_skipped": [],
                           "unmatched_clip_slides": ex.skipped_media_slides}
            clip_new = [rewrite_urls(q, doc_id, base) for q in new
                        if (q.get("question_audio") or q.get("answer_audio") or q.get("question_videos") or q.get("answer_videos"))]
            # check every referenced file once
            urls = set()
            for q in clip_new:
                collect_urls({k: q.get(k) for k in MEDIA_FIELDS + IMAGE_FIELDS + ("format_payload",)}, urls)
            with ThreadPoolExecutor(8) as pool:
                ok = dict(zip(sorted(urls), pool.map(url_exists, sorted(urls))))

            deck_info = None
            try:
                from backend.app.services.ingestion.deck_metadata import annotate_questions
                from backend.app.services.question_meta_service import deck_row_to_info
                deck_row = db.query(DeckMetadata).filter(DeckMetadata.document_id == doc_id).first()
                deck_info = deck_row_to_info(deck_row) if deck_row is not None else None
                annotate_questions(clip_new, slides, deck_info)
            except Exception as e:
                print("metadata annotation skipped:", type(e).__name__)

            for q in clip_new:
                bad = [u for u in collect_urls({k: q.get(k) for k in MEDIA_FIELDS + IMAGE_FIELDS + ("format_payload",)}, set()) if not ok.get(u)]
                st = by_slide.get(q["slide_number"])
                label = {"slide": q["slide_number"], "question": q["question_text"][:60], "answer": (q["answer"] or "")[:40]}
                if bad:
                    deck_report["unreachable_skipped"].append({**label, "files": [u.split("/")[-1] for u in bad]})
                    continue
                if st is not None:
                    changes = plan_attach({f: getattr(st, f) for f in MEDIA_FIELDS}, q)
                    if changes:
                        row = {"question_id": st.id, "old": {f: list(getattr(st, f) or []) for f in changes}, "new": changes}
                        attach_rows.append(row)
                        deck_report["attach"].append({**label, "fields": sorted(changes)})
                    continue
                h = Deduplicator.compute_hash(q["question_text"], q["answer"])
                if h in library_hashes:
                    deck_report["duplicate_skipped"].append(label)
                    continue
                library_hashes.add(h)
                q["content_hash"] = h
                create_rows.append((doc_id, q))
                deck_report["add"].append(label)
            report["decks"].append(deck_report)
            print(f"{os.path.basename(path)[:50]}: stored {len(stored)}, attach {len(deck_report['attach'])}, add {len(deck_report['add'])}, "
                  f"duplicates skipped {len(deck_report['duplicate_skipped'])}, unreachable {len(deck_report['unreachable_skipped'])}")
        db.rollback()

        rp = out_dir / f"repair_media_{'apply' if args.apply else 'dry_run'}_{stamp}.json"
        rp.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        print("totals: attach", len(attach_rows), "| add", len(create_rows))
        print("report:", rp)
        if not args.apply:
            print("dry run only: nothing was written. Re-run with --apply to write.")
            return 0

        from backend.app.services.question_meta_service import add_new_question_metadata
        created_ids = []
        snap = out_dir / f"repair_media_ROLLBACK_{stamp}.json"
        for r in attach_rows:
            q = db.get(Question, r["question_id"])
            for f, v in r["new"].items():
                setattr(q, f, v)
        db.commit()
        for doc_id, qd in create_rows:
            doc = db.get(Document, doc_id)
            deck_row = db.query(DeckMetadata).filter(DeckMetadata.document_id == doc_id).first()
            qid = str(uuid.uuid4())
            q = Question(
                id=qid, content_hash=qd["content_hash"], document_id=doc_id, slide_id=qd.get("slide_id"),
                answer_slide_id=qd.get("answer_slide_id"), question_text=qd["question_text"], answer=qd["answer"],
                options=qd.get("options"), explanation=qd.get("explanation"), topic="Uncategorised", subtopic=qd.get("subtopic"),
                topics=["Uncategorised"], tags=[], question_format=qd.get("question_format"), question_type=qd.get("question_type", "SLIDE_QA"),
                format_payload=qd.get("format_payload"), play_mode=qd.get("play_mode"), slot=qd.get("slot") or "LIVE",
                topic_label=qd.get("topic_label"), format_needs_review=True, source_year=qd.get("source_year", doc.year),
                round_number=(qd.get("_meta") or {}).get("round_number"),
                provenance_decks=[{"document_id": doc_id, "slide_id": qd.get("slide_id")}], occurrence_count=1,
                source_slide_range=qd.get("source_slide_range"), duplicate_status="UNIQUE",
                **{f: qd.get(f, []) for f in IMAGE_FIELDS + MEDIA_FIELDS if f != "raw_media_refs"},
                raw_media_refs=qd.get("raw_media_refs", []),
            )
            db.add(q)
            db.flush()
            add_new_question_metadata(db, [(q, qd.get("_meta"))], deck_row, doc)
            created_ids.append(qid)
            db.commit()
        snap.write_text(json.dumps({"created": created_ids, "attached": attach_rows}, indent=1), encoding="utf-8")
        print("applied: attached", len(attach_rows), "| added", len(created_ids), "| rollback file:", snap)
        return 0
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    sys.exit(main())
