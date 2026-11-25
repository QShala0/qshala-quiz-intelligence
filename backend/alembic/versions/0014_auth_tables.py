"""Auth tables: auth_teams, auth_users, auth_sessions, auth_audit.

Revision ID: 0014_auth_tables
Revises: 0013_cost_ledger
Create Date: 2026-11-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0014_auth_tables"
down_revision: Union[str, None] = "0013_cost_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    tables = set(insp.get_table_names())

    if "auth_teams" not in tables:
        op.create_table(
            "auth_teams",
            sa.Column("id", sa.String(50), primary_key=True),
            sa.Column("name", sa.String(100), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "auth_users" not in tables:
        op.create_table(
            "auth_users",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("email", sa.String(255), unique=True, nullable=True),
            sa.Column("username", sa.String(100), unique=True, nullable=True),
            sa.Column("display_name", sa.String(255), nullable=False),
            sa.Column("team_id", sa.String(50), sa.ForeignKey("auth_teams.id", ondelete="SET NULL"), nullable=True),
            sa.Column("role", sa.String(20), nullable=False, server_default="member"),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("provider", sa.String(20), nullable=False),
            sa.Column("password_hash", sa.String(255), nullable=True),
            sa.Column("ms_oid", sa.String(64), unique=True, nullable=True),
            sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.String(100), nullable=True),
        )
        op.create_index("ix_auth_users_email", "auth_users", ["email"], unique=True)
        op.create_index("ix_auth_users_username", "auth_users", ["username"], unique=True)
        op.create_index("ix_auth_users_ms_oid", "auth_users", ["ms_oid"], unique=True)
        op.create_index("ix_auth_users_team_id", "auth_users", ["team_id"])
        op.create_index("ix_auth_users_status", "auth_users", ["status"])

    if "auth_sessions" not in tables:
        op.create_table(
            "auth_sessions",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
        op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    if "auth_audit" not in tables:
        op.create_table(
            "auth_audit",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("auth_users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("action", sa.String(50), nullable=False),
            sa.Column("target_user_id", sa.String(36), sa.ForeignKey("auth_users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("detail", sa.JSON(), nullable=True),
        )
        op.create_index("ix_auth_audit_at", "auth_audit", ["at"])
        op.create_index("ix_auth_audit_actor", "auth_audit", ["actor_user_id"])
        op.create_index("ix_auth_audit_target", "auth_audit", ["target_user_id"])
        op.create_index("ix_auth_audit_action", "auth_audit", ["action"])


def downgrade() -> None:
    op.drop_table("auth_audit")
    op.drop_table("auth_sessions")
    op.drop_table("auth_users")
    op.drop_table("auth_teams")
