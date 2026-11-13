"""Record / compare what the REAL prompt engine (live database, live Gemini) picks for a fixed prompt list.

The seeded golden check runs with Gemini off, so it cannot see the interpreter, intent engine or curator. Run this
against the real vault before and after any change that could touch them:

    python backend/scripts/prompt_quiz_live_snapshot.py record  /tmp/before.json
    ... make the change, redeploy or restart ...
    python backend/scripts/prompt_quiz_live_snapshot.py compare /tmp/before.json

Needs DATABASE_URL and GEMINI_API_KEY in the environment. Gemini at temperature 0 is close to repeatable but not
perfect, so record twice on unchanged code first (record A, compare A) to learn the baseline noise.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
PROMPTS = ROOT / "backend" / "eval_data" / "prompt_quiz_prompts.txt"


def run_all():
    from backend.app.database import SessionLocal
    from backend.app.services.prompt_quiz import engine
    out = {}
    db = SessionLocal()
    try:
        for p in [l.strip() for l in PROMPTS.read_text().splitlines() if l.strip()]:
            d = engine.build_draft(db, p)
            b = d["brief"]
            out[p] = {"ids": [q["id"] for q in d["questions"]], "count": b["count"], "query": b["query"],
                      "source": b["source"], "hard_bias": b["hard_bias"], "stages": d["stages"], "message": d["message"]}
    finally:
        db.close()
    return out


def main():
    mode, path = sys.argv[1], Path(sys.argv[2])
    if mode == "record":
        path.write_text(json.dumps(run_all(), indent=1, sort_keys=True))
        print(f"recorded {path}")
        return 0
    old, new = json.loads(path.read_text()), run_all()
    changed = 0
    for p, o in old.items():
        n = new.get(p, {})
        same_set, same_order = set(o["ids"]) == set(n.get("ids", [])), o["ids"] == n.get("ids")
        if not same_order or o["query"] != n.get("query") or o["hard_bias"] != n.get("hard_bias"):
            changed += 1
            kept = len(set(o["ids"]) & set(n.get("ids", [])))
            print(f"CHANGED  {p!r}: kept {kept}/{len(o['ids'])} ids, same set={same_set}, "
                  f"query {o['query']!r}->{n.get('query')!r}, source {o['source']}->{n.get('source')}")
    print(f"{changed} of {len(old)} prompts changed")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
