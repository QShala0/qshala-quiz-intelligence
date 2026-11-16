"""The whole-vault repair and heal passes, run on purpose (weekly, from .github/workflows/vault-maintenance.yml).

    python backend/scripts/run_vault_maintenance.py

These passes read every question, so they no longer run every time the app or the worker starts (see
STARTUP_MAINTENANCE_MODE in backend/app/database.py). This script is the scheduled, once-a-week replacement:
  1. run_startup_repairs: backfill rows saved without a content hash, and repair broken bilingual / deck extractions;
  2. heal_specialized_questions: rewrite old-style Connect and Stepped-clue rows into the canonical form.
Both are idempotent: rows that are already correct are left alone.

Environment: DATABASE_URL (Postgres). REQUIRE_POSTGRES=1 is set by default so it can never quietly run against a
local SQLite file. Never prints the database URL or any key.
"""
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Importing the database module must not also run the passes at import time: this script runs them exactly once.
os.environ["STARTUP_MAINTENANCE_MODE"] = "off"
os.environ.setdefault("REQUIRE_POSTGRES", "1")

logger = logging.getLogger("vault-maintenance")


def run() -> dict:
    from backend.app import database
    from backend.app.services.ingestion.heal_specialized import heal_specialized_questions

    logger.info("Database dialect: %s", database.engine.dialect.name)
    t0 = time.monotonic()
    database.run_startup_repairs()
    logger.info("Repair pass finished in %.1fs", time.monotonic() - t0)

    t1 = time.monotonic()
    db = database.SessionLocal()
    try:
        stats = heal_specialized_questions(db)
    finally:
        db.close()
    logger.info("Heal pass finished in %.1fs: %s", time.monotonic() - t1, stats)
    return stats


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        run()
    except Exception as e:  # fail the workflow run loudly; the message never contains the database URL
        logger.error("Vault maintenance failed: %s", type(e).__name__)
        return 1
    logger.info("Vault maintenance done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
