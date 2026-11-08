"""questions.format_needs_review: a real flag for "the format reading needs a person to look".

The review queue first matched `format_payload` text for `"needs_review": true`, which scans a JSON column. This adds
a plain boolean instead (NOT NULL, default false), so the queue can filter on a column. Every existing row becomes
false: rows ingested before format-aware ingestion carry no format reading to review.

Additive and safe on a live database. No index is created here; on Postgres a partial index is the right shape once the
table is large, and is better created by hand off-peak:

    CREATE INDEX CONCURRENTLY ix_questions_format_needs_review ON questions (id) WHERE format_needs_review;

Revision ID: 0009_format_needs_review_flag
Revises: 0008_format_aware_questions
Create Date: 2026-11-08
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0009_format_needs_review_flag"
down_revision: Union[str, None] = "0008_format_aware_questions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _question_columns() -> set:
    insp = inspect(op.get_bind())
    if "questions" not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns("questions")}


def upgrade() -> None:
    cols = _question_columns()
    if cols and "format_needs_review" not in cols:
        op.add_column("questions", sa.Column("format_needs_review", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    if "format_needs_review" in _question_columns():
        with op.batch_alter_table("questions") as batch:
            batch.drop_column("format_needs_review")
