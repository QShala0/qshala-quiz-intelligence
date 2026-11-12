"""Recoverable bulk deletes.

Before a deck or the whole question bank is deleted, the affected rows are copied (server side, inside the same
transaction) into holding tables named trash_<id>_<table>. Nothing is read into Python, so it is fast and safe at any
size. A restore copies back every row that is not already present. Holding tables are removed after
TRASH_RETENTION_DAYS (default 30).

What this does NOT bring back: files already removed from disk or object storage (the uploaded deck file), and
embeddings are restored as saved. Restoring never overwrites rows that exist now.
"""
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Restore order matters (parents before children). Only these tables are ever snapshotted or restored.
TABLES = ["documents", "slides", "questions", "qshala_tags", "qshala_question_metadata", "question_topic_paths",
          "question_edit_history", "qshala_tag_changes", "question_repeats"]

_QS = "SELECT id FROM questions WHERE document_id = :doc"


def retention_days() -> int:
    try:
        return max(1, int(os.getenv("TRASH_RETENTION_DAYS", "30")))
    except ValueError:
        return 30


def _ensure_index(db: Session) -> None:
    db.execute(text("CREATE TABLE IF NOT EXISTS trash_index (id VARCHAR(40) PRIMARY KEY, label VARCHAR(200), "
                    "kind VARCHAR(40), created_at TIMESTAMP, restored_at TIMESTAMP, row_counts TEXT)"))


def _where(kind: str, table: str, doc: Optional[str]) -> Tuple[str, dict]:
    """The WHERE clause that selects this table's rows for the snapshot ('' = every row)."""
    if kind == "all_questions":
        return ("", {}) if table in ("questions", "qshala_tags", "qshala_question_metadata", "question_topic_paths",
                                     "question_edit_history", "qshala_tag_changes", "question_repeats") else ("1 = 0", {})
    p = {"doc": doc}
    return {
        "documents": ("id = :doc", p), "slides": ("document_id = :doc", p), "questions": ("document_id = :doc", p),
        "question_repeats": (f"document_id = :doc OR question_id IN ({_QS})", p),
    }.get(table, (f"question_id IN ({_QS})", p))


def snapshot(db: Session, kind: str, label: str, document_id: Optional[str] = None) -> str:
    """Copy what is about to be deleted into holding tables and return the snapshot id. kind: 'deck' or 'all_questions'.
    Runs in the caller's transaction: if the delete then fails and rolls back, the snapshot goes with it."""
    assert kind in ("deck", "all_questions")
    _ensure_index(db)
    sid = f"{datetime.now(timezone.utc):%Y%m%d%H%M%S}{secrets.token_hex(2)}"
    existing = set(inspect(db.get_bind()).get_table_names())
    counts: Dict[str, int] = {}
    for table in TABLES:
        if table not in existing:
            continue
        where, params = _where(kind, table, document_id)
        if where == "1 = 0":
            continue
        clause = f" WHERE {where}" if where else ""
        db.execute(text(f"CREATE TABLE trash_{sid}_{table} AS SELECT * FROM {table}{clause}"), params)
        counts[table] = int(db.execute(text(f"SELECT COUNT(*) FROM trash_{sid}_{table}")).scalar() or 0)
    db.execute(text("INSERT INTO trash_index (id, label, kind, created_at, row_counts) VALUES (:i, :l, :k, :c, :r)"),
               {"i": sid, "l": label[:200], "k": kind, "c": datetime.now(timezone.utc).replace(tzinfo=None), "r": json.dumps(counts)})
    return sid


def list_snapshots(db: Session) -> List[dict]:
    _ensure_index(db)
    rows = db.execute(text("SELECT id, label, kind, created_at, restored_at, row_counts FROM trash_index ORDER BY created_at DESC")).all()
    return [{"id": r[0], "label": r[1], "kind": r[2], "created_at": str(r[3]), "restored_at": str(r[4]) if r[4] else None,
             "row_counts": json.loads(r[5] or "{}")} for r in rows]


def _live_columns(db: Session, table: str) -> List[str]:
    return [c["name"] for c in inspect(db.get_bind()).get_columns(table)]


def restore(db: Session, sid: str) -> Dict[str, int]:
    """Copy every snapshotted row that is not currently present back into its table. Returns rows restored per table."""
    if not sid.isalnum():
        raise ValueError("bad snapshot id")
    _ensure_index(db)
    row = db.execute(text("SELECT id FROM trash_index WHERE id = :i"), {"i": sid}).first()
    if not row:
        raise KeyError(sid)
    insp = inspect(db.get_bind())
    existing = set(insp.get_table_names())
    restored: Dict[str, int] = {}
    for table in TABLES:
        held = f"trash_{sid}_{table}"
        if held not in existing or table not in existing:
            continue
        cols = [c for c in _live_columns(db, table) if c in {x["name"] for x in insp.get_columns(held)}]
        pk = insp.get_pk_constraint(table).get("constrained_columns") or []
        collist = ", ".join(cols)
        sel = ", ".join(f"h.{c}" for c in cols)
        guard = " AND ".join(f"t.{c} = h.{c}" for c in pk) if pk else "1 = 0"
        res = db.execute(text(f"INSERT INTO {table} ({collist}) SELECT {sel} FROM {held} h "
                              f"WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE {guard})"))
        restored[table] = int(res.rowcount or 0)
    db.execute(text("UPDATE trash_index SET restored_at = :n WHERE id = :i"),
               {"n": datetime.now(timezone.utc).replace(tzinfo=None), "i": sid})
    return restored


def purge_expired(db: Session, days: Optional[int] = None) -> List[str]:
    """Drop holding tables older than the retention window. Returns the snapshot ids removed."""
    _ensure_index(db)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days or retention_days())
    old = [r[0] for r in db.execute(text("SELECT id FROM trash_index WHERE created_at < :c"), {"c": cutoff}).all()]
    for sid in old:
        drop(db, sid)
    return old


def drop(db: Session, sid: str) -> None:
    if not sid.isalnum():
        raise ValueError("bad snapshot id")
    for table in TABLES:
        db.execute(text(f"DROP TABLE IF EXISTS trash_{sid}_{table}"))
    db.execute(text("DELETE FROM trash_index WHERE id = :i"), {"i": sid})
