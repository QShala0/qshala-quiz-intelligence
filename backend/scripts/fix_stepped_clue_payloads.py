"""Repair STEPPED_CLUE questions in the bank so every surface (live presenter, quiz review, PPT export)
reveals the clues one at a time instead of showing all three at once.

Each question's format_payload is rewritten into the one shape the renderers require:

    {type: "STEPPED_CLUE", scoring_ladder_ok: bool, extraction: {...},
     clues: [{step, text, correct, wrong, images}], answer}

export_format_of() (PPT export) needs the `extraction` block; the frontend isStepped() guard needs
`scoring_ladder_ok`; and every renderer reads clue `correct`/`wrong`/`images` — not the legacy
`points_correct`/`image` keys the healer used to write, which is why the clues collapsed into one block.

Clues are taken from the existing structured payload (remapping the legacy field names) when they carry
real text, otherwise reconstructed by parsing the merged "• Clue N (+x / -y): ..." question_text.

    python backend/scripts/fix_stepped_clue_payloads.py            # dry run: report what would change
    python backend/scripts/fix_stepped_clue_payloads.py --apply    # write the changes

Idempotent: a question already in canonical shape is left untouched. Needs DATABASE_URL set.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root

LADDER = [(1, 20, -15), (2, 15, -10), (3, 10, -5)]
_PLACEHOLDER = re.compile(r"^clue\s*\d+$", re.I)


def _clue_text(c: dict) -> str:
    t = (c.get("text") or "").strip()
    return "" if _PLACEHOLDER.match(t) else t   # a literal "Clue 1" is a placeholder, not real clue text


def _clue_images(c: dict) -> list:
    imgs = c.get("images")
    if isinstance(imgs, list):
        return [u for u in imgs if u]
    one = c.get("image") or c.get("image_url")
    return [one] if one else []


def canonical_payload(payload, question_text, images, answer, parse_stepped_clues):
    """Return the stepped payload in canonical shape, or None if the clues cannot be recovered."""
    payload = dict(payload or {})
    src = payload.get("clues") if isinstance(payload.get("clues"), list) else []
    imgs = [u for u in (images or []) if u]

    usable = [c for c in src if isinstance(c, dict) and _clue_text(c)]
    clues = []
    if usable:
        for i, c in enumerate(usable):
            clues.append({
                "step": c.get("step") or (i + 1),
                "text": _clue_text(c),
                "correct": c.get("correct", c.get("points_correct")),
                "wrong": c.get("wrong", c.get("points_wrong")),
                "images": _clue_images(c),
            })
    else:
        tiers = parse_stepped_clues(question_text or "")
        if not all(tiers):
            return None
        for i, (step, cor, wrg) in enumerate(LADDER):
            clues.append({"step": step, "text": tiers[i].strip(), "correct": cor, "wrong": wrg,
                          "images": [imgs[i]] if i < len(imgs) else []})

    ladder = {s: (cor, wrg) for s, cor, wrg in LADDER}
    for c in clues:
        cor, wrg = ladder.get(c["step"], (None, None))
        if c.get("correct") is None:
            c["correct"] = cor
        if c.get("wrong") is None:
            c["wrong"] = wrg

    out = dict(payload)
    out["type"] = "STEPPED_CLUE"
    out["clues"] = clues
    out["scoring_ladder_ok"] = bool(payload.get("scoring_ladder_ok", True))
    out["answer"] = payload.get("answer") or answer or ""
    if not isinstance(out.get("extraction"), dict):
        out["extraction"] = {"confidence": 0.9, "needs_review": False, "route": "accept",
                             "reasons": ["normalized stepped-clue payload"]}
    return out


def is_canonical(p) -> bool:
    if not isinstance(p, dict) or p.get("type") != "STEPPED_CLUE":
        return False
    if not isinstance(p.get("extraction"), dict) or "scoring_ladder_ok" not in p:
        return False
    clues = p.get("clues")
    if not isinstance(clues, list) or not clues:
        return False
    for c in clues:
        if not isinstance(c, dict) or not (c.get("text") or "").strip():
            return False
        if "correct" not in c or "wrong" not in c or not isinstance(c.get("images"), list):
            return False
    return True


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = ap.parse_args(argv)

    from backend.app.database import SessionLocal
    from backend.app.models.question import Question
    from backend.app.services.ingestion.heal_specialized import parse_stepped_clues

    db = SessionLocal()
    try:
        qs = db.query(Question).filter(Question.question_format.in_(["STEPPED_CLUE", "THREE_CLUE"])).all()
        changed = skipped = failed = 0
        for q in qs:
            cur = q.format_payload or q.format_data
            if is_canonical(cur):
                skipped += 1
                continue
            imgs = list(q.question_images or q.image_refs or [])
            new = canonical_payload(cur, q.question_text, imgs, q.answer, parse_stepped_clues)
            if not new:
                failed += 1
                print(f"  UNFIXABLE {q.id}: no structured clues and question_text is not parseable")
                continue
            changed += 1
            print(f"  fix {q.id}: {len(new['clues'])} clues, points {[ (c['correct'], c['wrong']) for c in new['clues'] ]}")
            if args.apply:
                q.question_format = "STEPPED_CLUE"
                q.question_type = "STEPPED_CLUE"
                q.format_payload = new
                q.format_data = new
        print(f"\n{'APPLIED' if args.apply else 'DRY RUN'}: {changed} to fix, {skipped} already canonical, "
              f"{failed} unfixable (of {len(qs)} stepped-clue questions)")
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
