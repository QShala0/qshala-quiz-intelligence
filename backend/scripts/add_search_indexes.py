"""Add trigram (pg_trgm) GIN indexes so the knowledge-base filters that use ILIKE '%text%' stop scanning every row.

    python backend/scripts/add_search_indexes.py            # dry run: prints what it would do
    python backend/scripts/add_search_indexes.py --apply    # CREATE INDEX CONCURRENTLY (does not block reads or writes)

Indexes change how a row is found, never which rows match, so API output is unchanged. Postgres only; needs
permission to CREATE EXTENSION pg_trgm (allowed on Neon). Safe to re-run (IF NOT EXISTS). Alembic revision
0010_search_trigram_indexes does the same for environments that run migrations.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# (index name, table, expression). The tags expression must match the query's CAST(questions.tags AS VARCHAR).
INDEXES = [
    ("ix_questions_topic_trgm", "questions", "topic gin_trgm_ops"),
    ("ix_questions_subtopic_trgm", "questions", "subtopic gin_trgm_ops"),
    ("ix_questions_tags_text_trgm", "questions", "(CAST(tags AS VARCHAR)) gin_trgm_ops"),
    ("ix_documents_title_trgm", "documents", "title gin_trgm_ops"),
]


def statements():
    yield "CREATE EXTENSION IF NOT EXISTS pg_trgm"
    for name, table, expr in INDEXES:
        yield f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} USING gin ({expr})"


def main() -> int:
    from backend.app.database import engine
    if engine.dialect.name != "postgresql":
        print("Postgres only; nothing to do on", engine.dialect.name)
        return 0
    apply = "--apply" in sys.argv
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for sql in statements():
            print(("APPLY  " if apply else "DRY    ") + sql)
            if apply:
                conn.exec_driver_sql(sql)
    print("done" if apply else "dry run only; re-run with --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
