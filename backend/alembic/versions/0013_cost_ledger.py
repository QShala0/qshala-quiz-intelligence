"""Cost tracker tables: cost_events, cost_rate_cards, cost_fx_rates, cost_refresh_runs.

Revision ID: 0013_cost_ledger
Revises: 0012_sharepoint_import_selection
Create Date: 2026-11-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0013_cost_ledger"
down_revision: Union[str, None] = "0012_sharepoint_import_selection"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    tables = set(insp.get_table_names())

    if "cost_events" not in tables:
        op.create_table(
            "cost_events",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("category", sa.String(32), nullable=False),
            sa.Column("operation", sa.String(32), nullable=False),
            sa.Column("document_id", sa.String(36), nullable=True),
            sa.Column("quiz_id", sa.String(64), nullable=True),
            sa.Column("job_id", sa.String(64), nullable=True),
            sa.Column("batch_id", sa.String(64), nullable=True),
            sa.Column("model", sa.String(64), nullable=True),
            sa.Column("units", sa.JSON(), nullable=False),
            sa.Column("rates", sa.JSON(), nullable=False),
            sa.Column("rate_card_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
            sa.Column("fx_rate", sa.Numeric(10, 4), nullable=False, server_default="95"),
            sa.Column("cost_inr", sa.Numeric(14, 4), nullable=False, server_default="0"),
            sa.Column("tax_inr", sa.Numeric(14, 4), nullable=False, server_default="0"),
            sa.Column("source", sa.String(20), nullable=False, server_default="measured"),
            sa.Column("period", sa.String(7), nullable=True),
            sa.Column("dedupe_key", sa.String(128), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
        )
        op.create_index("ix_cost_events_occurred_at", "cost_events", ["occurred_at"])
        op.create_index("ix_cost_events_category_occurred", "cost_events", ["category", "occurred_at"])
        op.create_index("ix_cost_events_document_id", "cost_events", ["document_id"])
        op.create_index("ix_cost_events_period_category", "cost_events", ["period", "category"])
        op.create_index("ix_cost_events_dedupe_key", "cost_events", ["dedupe_key"], unique=True)

    if "cost_rate_cards" not in tables:
        op.create_table(
            "cost_rate_cards",
            sa.Column("version", sa.Integer(), primary_key=True),
            sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("rates", sa.JSON(), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "cost_fx_rates" not in tables:
        op.create_table(
            "cost_fx_rates",
            sa.Column("date", sa.String(10), primary_key=True),
            sa.Column("usd_to_inr", sa.Numeric(10, 4), nullable=False),
            sa.Column("source", sa.String(50), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "cost_refresh_runs" not in tables:
        op.create_table(
            "cost_refresh_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="running"),
            sa.Column("notes", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    insp = inspect(op.get_bind())
    tables = set(insp.get_table_names())

    for tbl in ["cost_refresh_runs", "cost_fx_rates", "cost_rate_cards", "cost_events"]:
        if tbl in tables:
            op.drop_table(tbl)
