"""Backfill: remove video/audio "poster" icons that older ingestion stored as the question's pictures.

A video or audio clip on a slide is drawn with a small poster picture (a camera or a speaker). Older ingestion kept
that icon as a question picture, so a clip question showed the icon next to "Watch Video Clue". The parser no longer
does this (see PPTXParser._poster_zip_names). This script cleans the questions already stored.

Only exact, reviewed icon files are removed (the MD5 list below, checked by eye against the real pictures), and only
from questions that actually have a video or audio clip. The clip itself, real pictures, List It ticks and crosses
and navigation icons are never touched. Questions whose pictures a person edited are skipped.

Usage:
    python backend/scripts/strip_video_posters.py                 # dry run: reads only, writes nothing
    python backend/scripts/strip_video_posters.py --apply         # writes, after saving a rollback file
    python backend/scripts/strip_video_posters.py --rollback FILE # puts the old lists back

Picture files are only READ over their public address (no upload, no delete). Reports and the rollback file go to a
folder outside the repo (--out-dir). Needs DATABASE_URL set; never prints it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
if (os.environ.get("DATABASE_URL") or "").startswith(("postgres://", "postgresql://")):
    os.environ.setdefault("REQUIRE_POSTGRES", "1")

# Reviewed by eye: speaker and camera poster icons (PowerPoint draws one of these on every clip).
POSTER_MD5 = {
    "6c238a7c740d7aa1b6550a60f1bccd86", "60b8942ff97e870b5529fe72909c675b", "6e13f4fa280abd08e8b44347e3a2e42a",
    "4c36b6c1297e5257e4d990808b708d36", "7913836678302f904e1c40d7cb690402", "eadbe94c4830c4be6312c33182eada2e",
    "7389d456bfd0d5c5bf97b18fa3266c35", "1d9498edfc582a13e1f550f0ea19caf3", "52381144698602877fcd1576c7d60d39",
    "988b55f54e887b2215d37c0433ba36d9",
}
LISTS = ("question_images", "answer_images", "image_refs")
MAX_ICON_BYTES = 25000          # posters are tiny; larger files are never fetched
PICTURE_FIELDS = ("question_images", "answer_images", "image_refs", "images")


def _open(url: str, method: str = "GET"):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=30)


def poster_md5_of(url: str):
    """md5 of the picture at `url` if it is small enough to be a poster, else None. Read-only."""
    try:
        size = int(_open(url, "HEAD").headers.get("content-length") or 0)
        if not size or size > MAX_ICON_BYTES:
            return None
        return hashlib.md5(_open(url).read()).hexdigest()
    except Exception:
        return None


def plan_changes(questions, edited_ids: set, is_poster) -> tuple[list[dict], dict]:
    """Pure planning step. `is_poster(url) -> bool`. Returns (rows, counts)."""
    rows: list[dict] = []
    counts = {"clip_questions": 0, "with_poster": 0, "skipped_human_edited": 0, "to_change": 0}
    for q in questions:
        if not ((q.video_refs or []) or (q.audio_refs or [])):
            continue
        counts["clip_questions"] += 1
        new = {c: [u for u in (getattr(q, c) or []) if not is_poster(u)] for c in LISTS}
        old = {c: list(getattr(q, c) or []) for c in LISTS}
        if new == old:
            continue
        counts["with_poster"] += 1
        if q.id in edited_ids:
            counts["skipped_human_edited"] += 1
            continue
        counts["to_change"] += 1
        rows.append({"question_id": q.id, "document_id": q.document_id, "old": old, "new": new})
    return rows, counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--batch-size", type=int, default=100)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--rollback", default=None)
    args = ap.parse_args()

    from sqlalchemy import text
    from backend.app.database import SessionLocal
    from backend.app.models.question import Question
    from backend.app.models.question_meta import QuestionEditHistory

    out_dir = Path(args.out_dir) if args.out_dir else Path(tempfile.gettempdir()) / "qshala_posters"
    if out_dir.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        print("Refusing to write reports inside the repo; pass --out-dir outside it.")
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        print("database:", db.get_bind().dialect.name)
        if args.rollback:
            data = json.loads(Path(args.rollback).read_text(encoding="utf-8"))
            for i in range(0, len(data["rows"]), args.batch_size):
                for r in data["rows"][i:i + args.batch_size]:
                    q = db.get(Question, r["question_id"])
                    if q is not None:
                        for c in LISTS:
                            setattr(q, c, r["old"][c])
                db.commit()
            print("rolled back", len(data["rows"]), "questions")
            return 0

        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION READ ONLY"))
        edited = {r[0] for r in db.query(QuestionEditHistory.question_id)
                  .filter(QuestionEditHistory.field.in_(PICTURE_FIELDS)).distinct().all()}
        clip_qs = [q for q in db.query(Question).all() if (q.video_refs or []) or (q.audio_refs or [])]
        urls = sorted({u for q in clip_qs for c in LISTS for u in (getattr(q, c) or [])})
        with ThreadPoolExecutor(8) as ex:
            md5s = dict(zip(urls, ex.map(poster_md5_of, urls)))
        rows, counts = plan_changes(clip_qs, edited, lambda u: md5s.get(u) in POSTER_MD5)
        db.rollback()
        counts["urls_checked"] = len(urls)
        counts["icons_found"] = sum(1 for m in md5s.values() if m in POSTER_MD5)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report = out_dir / f"strip_video_posters_{'apply' if args.apply else 'dry_run'}_{stamp}.json"
        report.write_text(json.dumps({"counts": counts, "rows": rows}, indent=1), encoding="utf-8")
        print("plan:", counts)
        print("report:", report)
        if not args.apply:
            print("dry run only: nothing was written. Re-run with --apply to write.")
            return 0
        snap = out_dir / f"strip_video_posters_ROLLBACK_{stamp}.json"
        snap.write_text(json.dumps({"rows": rows}, indent=1), encoding="utf-8")
        print("rollback file:", snap)
        for i in range(0, len(rows), args.batch_size):
            for r in rows[i:i + args.batch_size]:
                q = db.get(Question, r["question_id"])
                if q is not None and all(list(getattr(q, c) or []) == r["old"][c] for c in LISTS):   # unchanged since planning
                    for c in LISTS:
                        setattr(q, c, r["new"][c])
            db.commit()
        print("applied", len(rows), "questions")
        return 0
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    sys.exit(main())
