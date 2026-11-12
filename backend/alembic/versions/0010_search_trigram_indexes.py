"""Trigram GIN indexes for the ILIKE '%text%' knowledge-base filters (Postgres only; no effect on results).

Live databases: prefer `python backend/scripts/add_search_indexes.py --apply`, which builds CONCURRENTLY.

Revision ID: 0010_search_trigram_indexes
Revises: 0009_format_needs_review_flag
Create Date: 2026-11-12
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0010_search_trigram_indexes"
down_revision: Union[str, None] = "0009_format_needs_review_flag"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEXES = [
    ("ix_questions_topic_trgm", "questions", "topic gin_trgm_ops"),
    ("ix_questions_subtopic_trgm", "questions", "subtopic gin_trgm_ops"),
    ("ix_questions_tags_text_trgm", "questions", "(CAST(tags AS VARCHAR)) gin_trgm_ops"),
    ("ix_documents_title_trgm", "documents", "title gin_trgm_ops"),
]


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for name, table, expr in INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} USING gin ({expr})")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for name, _t, _e in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
