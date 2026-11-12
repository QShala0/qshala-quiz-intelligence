"""Check that the DISTINCT ON tag lookup returns exactly what the current window-function lookup returns, and time both.

    DATABASE_URL=postgresql://... python backend/scripts/compare_latest_tags.py

Exit code 0 only when both pick the same tag row for every question. Only then, and only if it is faster, set
PROMPT_QUIZ_TAGS_QUERY=distinct_on. Read-only.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> int:
    from sqlalchemy import text
    from backend.app.database import SessionLocal
    from backend.app.services.prompt_quiz import engine
    db = SessionLocal()
    try:
        if db.get_bind().dialect.name != "postgresql":
            print("needs a Postgres DATABASE_URL")
            return 2
        res = {}
        for name, fn in (("window", engine._latest_tags), ("distinct_on", engine._latest_tags_distinct_on)):
            best = None
            for _ in range(3):
                t = time.perf_counter()
                out = fn(db)
                dt = time.perf_counter() - t
                best = dt if best is None else min(best, dt)
            res[name] = (best, {k: v.id for k, v in out.items()})
            print(f"{name:12s} {best * 1000:9.1f} ms   {len(out)} questions")
        same = res["window"][1] == res["distinct_on"][1]
        print("IDENTICAL" if same else "DIFFERENT")
        n = db.execute(text("select count(*) from qshala_tags")).scalar()
        print(f"qshala_tags rows: {n}")
        return 0 if same else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
