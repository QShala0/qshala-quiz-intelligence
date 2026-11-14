"""Repair CONNECT (Connect 4) questions in the bank so every surface (live presenter, quiz review, PPT
export) shows the four clue images as one grid with the answer on a separate slide — instead of mixing the
answer into the question or treating the countdown timer as a clue.

Each question's format_payload is rewritten into the shape the renderers require:

    {type: "CONNECT", items: [{order, image}], text_hint, item_labels, scoring, answer, extraction}

The legacy healer wrote type "CONNECT_4" with an `elements` list and no `extraction`, which export_format_of()
and the frontend isConnect() guard both reject, so the structured grid never rendered. Clue images are taken
from the existing payload (items or elements) when present, else from the question's images; any countdown
timer graphic (timer media is named "...timer...") is dropped.

    python backend/scripts/fix_connect_payloads.py            # dry run: report what would change
    python backend/scripts/fix_connect_payloads.py --apply    # write the changes

Idempotent: a question already in canonical shape is left untouched. Needs DATABASE_URL set.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root

_TIMER = re.compile(r"timer", re.I)
_CONNECT_HEADER = re.compile(r"^\[connect[^\]]*\]\s*", re.I)


def _clue_images(payload, images):
    """Ordered, de-duplicated clue image URLs with the countdown timer and blanks removed."""
    urls = []
    for key in ("items", "elements"):
        src = payload.get(key)
        if isinstance(src, list) and src:
            for it in src:
                if isinstance(it, dict):
                    u = it.get("image") or it.get("image_url")
                    if u:
                        urls.append(u)
            if urls:
                break
    if not urls:
        urls = list(images or [])
    out, seen = [], set()
    for u in urls:
        if u and not _TIMER.search(u) and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def canonical_connect_payload(payload, question_text, images, answer):
    """Return the connect payload in canonical shape, or None if it has no clue images."""
    payload = dict(payload or {})
    imgs = _clue_images(payload, images)
    if not imgs:
        return None
    items = [{"order": i + 1, "image": u} for i, u in enumerate(imgs)]

    hint = payload.get("text_hint") or payload.get("prompt")
    if hint:
        hint = _CONNECT_HEADER.sub("", str(hint)).strip() or None

    labels = payload.get("item_labels")
    labels = [l for l in labels if isinstance(l, str) and not re.fullmatch(r"clue\s*\d+", l.strip(), re.I)] \
        if isinstance(labels, list) else []

    scoring = payload.get("scoring") if isinstance(payload.get("scoring"), dict) else {}
    ans = payload.get("answer") or payload.get("common_link") or payload.get("connection_reveal") or answer or ""

    out = {k: v for k, v in payload.items()
           if k not in ("elements", "common_link", "connection_reveal", "prompt", "sub_hint")}
    out["type"] = "CONNECT"
    out["items"] = items
    out["text_hint"] = hint
    out["item_labels"] = labels
    out["scoring"] = {"correct": scoring.get("correct"), "wrong": scoring.get("wrong")}
    out["answer"] = ans
    if not isinstance(out.get("extraction"), dict):
        out["extraction"] = {"confidence": 0.9, "needs_review": False, "route": "accept",
                             "reasons": ["normalized connect payload"]}
    return out


def is_canonical(p) -> bool:
    if not isinstance(p, dict) or p.get("type") != "CONNECT":
        return False
    if not isinstance(p.get("extraction"), dict):
        return False
    items = p.get("items")
    if not isinstance(items, list) or not items:
        return False
    for it in items:
        if not isinstance(it, dict) or not it.get("image") or _TIMER.search(it["image"]):
            return False
    return True


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = ap.parse_args(argv)

    from backend.app.database import SessionLocal
    from backend.app.models.question import Question

    db = SessionLocal()
    try:
        qs = db.query(Question).filter(Question.question_format.in_(["CONNECT", "CONNECT_4"])).all()
        changed = skipped = failed = 0
        for q in qs:
            cur = q.format_payload or q.format_data
            if is_canonical(cur):
                skipped += 1
                continue
            imgs = list(q.question_images or q.image_refs or [])
            new = canonical_connect_payload(cur, q.question_text, imgs, q.answer)
            if not new:
                failed += 1
                print(f"  UNFIXABLE {q.id}: no clue images found")
                continue
            changed += 1
            print(f"  fix {q.id}: {len(new['items'])} quadrants, answer={new['answer'][:40]!r}")
            if args.apply:
                q.question_format = "CONNECT"
                q.question_type = "CONNECT"
                q.format_payload = new
                q.format_data = new
        print(f"\n{'APPLIED' if args.apply else 'DRY RUN'}: {changed} to fix, {skipped} already canonical, "
              f"{failed} unfixable (of {len(qs)} connect questions)")
        if args.apply:
            db.commit()
            print("committed.")
        elif changed:
            print("re-run with --apply to write these changes.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
