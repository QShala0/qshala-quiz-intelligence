"""sharepoint_files: selective importer columns (drive, folder path, hashes, duplicate match, batch).

Revision ID: 0012_sharepoint_import_selection
Revises: 0011_upload_batch_client_id
Create Date: 2026-11-17
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0012_sharepoint_import_selection"
down_revision: Union[str, None] = "0011_upload_batch_client_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = [
    ("drive_id", sa.String(255)),
    ("folder_path", sa.Text()),
    ("quick_xor_hash", sa.String(128)),
    ("file_sha256", sa.String(64)),
    ("match_document_id", sa.String(36)),
    ("batch_id", sa.String(36)),
    ("selected_at", sa.DateTime(timezone=True)),
]
_INDEXES = [("ix_sharepoint_files_batch_id", ["batch_id"]), ("ix_sharepoint_files_sync_status", ["sync_status"])]


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if "sharepoint_files" not in insp.get_table_names():
        return
    have = {c["name"] for c in insp.get_columns("sharepoint_files")}
    for name, typ in _COLUMNS:
        if name not in have:
            op.add_column("sharepoint_files", sa.Column(name, typ, nullable=True))
    have_ix = {i["name"] for i in insp.get_indexes("sharepoint_files")}
    for name, cols in _INDEXES:
        if name not in have_ix:
            op.create_index(name, "sharepoint_files", cols)


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if "sharepoint_files" not in insp.get_table_names():
        return
    have_ix = {i["name"] for i in insp.get_indexes("sharepoint_files")}
    for name, _ in _INDEXES:
        if name in have_ix:
            op.drop_index(name, table_name="sharepoint_files")
    have = {c["name"] for c in insp.get_columns("sharepoint_files")}
    for name, _ in _COLUMNS:
        if name in have:
            op.drop_column("sharepoint_files", name)
