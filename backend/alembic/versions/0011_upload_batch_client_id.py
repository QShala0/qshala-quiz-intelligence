"""upload_batches.client_id: lets each browser find its own upload batch, not a colleague's.

Revision ID: 0011_upload_batch_client_id
Revises: 0010_search_trigram_indexes
Create Date: 2026-11-13
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0011_upload_batch_client_id"
down_revision: Union[str, None] = "0010_search_trigram_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if "upload_batches" not in insp.get_table_names():
        return
    if "client_id" not in {c["name"] for c in insp.get_columns("upload_batches")}:
        op.add_column("upload_batches", sa.Column("client_id", sa.String(64), nullable=True))
    if "ix_upload_batches_client_id" not in {i["name"] for i in insp.get_indexes("upload_batches")}:
        op.create_index("ix_upload_batches_client_id", "upload_batches", ["client_id"])


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if "upload_batches" not in insp.get_table_names():
        return
    if "ix_upload_batches_client_id" in {i["name"] for i in insp.get_indexes("upload_batches")}:
        op.drop_index("ix_upload_batches_client_id", table_name="upload_batches")
    if "client_id" in {c["name"] for c in insp.get_columns("upload_batches")}:
        op.drop_column("upload_batches", "client_id")
