"""Add uploaded_by_user_id/uploaded_by_name to documents and importer_user_id/importer_name to sharepoint_files.

Revision ID: 0016_document_uploaded_by
Revises: 0015_quiz_created_by
Create Date: 2026-11-25
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0016_document_uploaded_by"
down_revision: Union[str, None] = "0015_quiz_created_by"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    tables = set(insp.get_table_names())

    if "documents" in tables:
        cols = {c["name"] for c in insp.get_columns("documents")}
        indexes = {ix["name"] for ix in insp.get_indexes("documents")}

        if "uploaded_by_user_id" not in cols:
            op.add_column(
                "documents",
                sa.Column(
                    "uploaded_by_user_id",
                    sa.String(36),
                    sa.ForeignKey("auth_users.id", ondelete="SET NULL"),
                    nullable=True,
                ),
            )
        if "ix_documents_uploaded_by_user_id" not in indexes:
            op.create_index("ix_documents_uploaded_by_user_id", "documents", ["uploaded_by_user_id"])

        if "uploaded_by_name" not in cols:
            op.add_column(
                "documents",
                sa.Column("uploaded_by_name", sa.String(255), nullable=True),
            )

    if "sharepoint_files" in tables:
        sp_cols = {c["name"] for c in insp.get_columns("sharepoint_files")}
        if "importer_user_id" not in sp_cols:
            op.add_column(
                "sharepoint_files",
                sa.Column(
                    "importer_user_id",
                    sa.String(36),
                    sa.ForeignKey("auth_users.id", ondelete="SET NULL"),
                    nullable=True,
                ),
            )
        if "importer_name" not in sp_cols:
            op.add_column(
                "sharepoint_files",
                sa.Column("importer_name", sa.String(255), nullable=True),
            )


def downgrade() -> None:
    insp = inspect(op.get_bind())
    tables = set(insp.get_table_names())

    if "sharepoint_files" in tables:
        sp_cols = {c["name"] for c in insp.get_columns("sharepoint_files")}
        if "importer_name" in sp_cols:
            op.drop_column("sharepoint_files", "importer_name")
        if "importer_user_id" in sp_cols:
            with op.batch_alter_table("sharepoint_files") as batch_op:
                batch_op.drop_column("importer_user_id")

    if "documents" in tables:
        cols = {c["name"] for c in insp.get_columns("documents")}
        indexes = {ix["name"] for ix in insp.get_indexes("documents")}
        if "ix_documents_uploaded_by_user_id" in indexes:
            op.drop_index("ix_documents_uploaded_by_user_id", table_name="documents")
        if "uploaded_by_name" in cols:
            op.drop_column("documents", "uploaded_by_name")
        if "uploaded_by_user_id" in cols:
            with op.batch_alter_table("documents") as batch_op:
                batch_op.drop_column("uploaded_by_user_id")
