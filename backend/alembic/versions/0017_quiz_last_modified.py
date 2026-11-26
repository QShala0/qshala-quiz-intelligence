"""Add last_modified_by_user_id, last_modified_by_name, and last_modified_at to quizzes table.

Revision ID: 0017_quiz_last_modified
Revises: 0016_document_uploaded_by
Create Date: 2026-11-26
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0017_quiz_last_modified"
down_revision: Union[str, None] = "0016_document_uploaded_by"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if "quizzes" not in insp.get_table_names():
        return

    cols = {c["name"] for c in insp.get_columns("quizzes")}
    indexes = {ix["name"] for ix in insp.get_indexes("quizzes")}

    if "last_modified_by_user_id" not in cols:
        op.add_column(
            "quizzes",
            sa.Column(
                "last_modified_by_user_id",
                sa.String(36),
                sa.ForeignKey("auth_users.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    if "ix_quizzes_last_modified_by_user_id" not in indexes:
        op.create_index("ix_quizzes_last_modified_by_user_id", "quizzes", ["last_modified_by_user_id"])

    if "last_modified_by_name" not in cols:
        op.add_column(
            "quizzes",
            sa.Column("last_modified_by_name", sa.String(255), nullable=True),
        )

    if "last_modified_at" not in cols:
        op.add_column(
            "quizzes",
            sa.Column("last_modified_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if "quizzes" not in insp.get_table_names():
        return

    cols = {c["name"] for c in insp.get_columns("quizzes")}
    indexes = {ix["name"] for ix in insp.get_indexes("quizzes")}

    if "ix_quizzes_last_modified_by_user_id" in indexes:
        op.drop_index("ix_quizzes_last_modified_by_user_id", table_name="quizzes")
    if "last_modified_at" in cols:
        op.drop_column("quizzes", "last_modified_at")
    if "last_modified_by_name" in cols:
        op.drop_column("quizzes", "last_modified_by_name")
    if "last_modified_by_user_id" in cols:
        with op.batch_alter_table("quizzes") as batch_op:
            batch_op.drop_column("last_modified_by_user_id")
