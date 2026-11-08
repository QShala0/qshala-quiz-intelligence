"""Format-aware questions: play_mode, slot and topic_label on questions.

  play_mode   BUZZER | DIRECTED | PASS | AUDIENCE | TIEBREAK | ALL_ON_SCREEN | BUFFER   (nullable)
  slot        LIVE | RESERVE | EXAMPLE                                                   (NOT NULL, default LIVE)
  topic_label the deck's own Topicator label, e.g. "Sustainability 1"                     (nullable)

Additive and safe on a live database: every existing row becomes slot='LIVE' (the column default) with no play_mode
and no topic_label. `topic` (the taxonomy topic) is untouched. No index is created here on purpose: slot and
play_mode have a handful of values, and a CREATE INDEX on a large live table is better run by hand off-peak.

Questions ingested BEFORE this revision keep their old format_payload (no animation-derived data, no round / team,
no confidence). To get Image Reveal order, List It ticks and crosses, Arrange values and so on for an old deck,
delete the deck and upload it again: the animation data only exists in the file, not in the database.

Revision ID: 0008_format_aware_questions
Revises: 0007_usage_tracking
Create Date: 2026-11-08
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0008_format_aware_questions"
down_revision: Union[str, None] = "0007_usage_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _question_columns() -> set:
    insp = inspect(op.get_bind())
    if "questions" not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns("questions")}


def upgrade() -> None:
    cols = _question_columns()
    if not cols:
        return
    if "play_mode" not in cols:
        op.add_column("questions", sa.Column("play_mode", sa.String(16), nullable=True))
    if "slot" not in cols:
        op.add_column("questions", sa.Column("slot", sa.String(10), nullable=False, server_default="LIVE"))
    if "topic_label" not in cols:
        op.add_column("questions", sa.Column("topic_label", sa.String(100), nullable=True))


def downgrade() -> None:
    cols = _question_columns()
    # batch mode lets SQLite (which cannot DROP COLUMN on older versions) rebuild the table; Postgres drops directly
    with op.batch_alter_table("questions") as batch:
        for name in ("topic_label", "slot", "play_mode"):
            if name in cols:
                batch.drop_column(name)
