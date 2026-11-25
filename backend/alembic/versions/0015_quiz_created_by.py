"""Add created_by_user_id and created_by_name to quizzes table.

Revision ID: 0015_quiz_created_by
Revises: 0014_auth_tables
Create Date: 2026-11-25
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0015_quiz_created_by"
down_revision: Union[str, None] = "0014_auth_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if "quizzes" not in insp.get_table_names():
        return

    cols = {c["name"] for c in insp.get_columns("quizzes")}
    indexes = {ix["name"] for ix in insp.get_indexes("quizzes")}

    if "created_by_user_id" not in cols:
        op.add_column(
            "quizzes",
            sa.Column(
                "created_by_user_id",
                sa.String(36),
                sa.ForeignKey("auth_users.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    if "ix_quizzes_created_by_user_id" not in indexes:
        op.create_index("ix_quizzes_created_by_user_id", "quizzes", ["created_by_user_id"])

    if "created_by_name" not in cols:
        op.add_column(
            "quizzes",
            sa.Column("created_by_name", sa.String(255), nullable=True),
        )


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if "quizzes" not in insp.get_table_names():
        return

    cols = {c["name"] for c in insp.get_columns("quizzes")}
    indexes = {ix["name"] for ix in insp.get_indexes("quizzes")}

    if "ix_quizzes_created_by_user_id" in indexes:
        op.drop_index("ix_quizzes_created_by_user_id", table_name="quizzes")
    if "created_by_name" in cols:
        op.drop_column("quizzes", "created_by_name")
    if "created_by_user_id" in cols:
        with op.batch_alter_table("quizzes") as batch_op:
            batch_op.drop_column("created_by_user_id")
