import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    String,
    Integer,
    Boolean,
    DateTime,
    JSON,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import relationship
from backend.app.database import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AuthTeam(Base):
    __tablename__ = "auth_teams"
    __table_args__ = {"extend_existing": True}

    id = Column(String(50), primary_key=True)  # 'School', 'Corporate', 'QShala', 'Consultant'
    name = Column(String(100), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    users = relationship("AuthUser", back_populates="team")


class AuthUser(Base):
    __tablename__ = "auth_users"
    __table_args__ = (
        Index("ix_auth_users_email", "email", unique=True),
        Index("ix_auth_users_username", "username", unique=True),
        Index("ix_auth_users_ms_oid", "ms_oid", unique=True),
        Index("ix_auth_users_team_id", "team_id"),
        Index("ix_auth_users_status", "status"),
        {"extend_existing": True},
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    email = Column(String(255), nullable=True)  # lowercase, unique
    username = Column(String(100), nullable=True)  # unique
    display_name = Column(String(255), nullable=False)
    team_id = Column(String(50), ForeignKey("auth_teams.id", ondelete="SET NULL"), nullable=True)
    role = Column(String(20), nullable=False, default="member")  # 'admin' or 'member'
    status = Column(String(20), nullable=False, default="active")  # 'active', 'pending', 'blocked', 'deleted'
    provider = Column(String(20), nullable=False)  # 'microsoft' or 'password'
    password_hash = Column(String(255), nullable=True)
    ms_oid = Column(String(64), nullable=True)  # unique
    must_change_password = Column(Boolean, nullable=False, default=False)
    failed_logins = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    created_by = Column(String(100), nullable=True)

    team = relationship("AuthTeam", back_populates="users")
    sessions = relationship("AuthSession", back_populates="user", cascade="all, delete-orphan")


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user_id", "user_id"),
        Index("ix_auth_sessions_expires_at", "expires_at"),
        {"extend_existing": True},
    )

    # id is sha256 hex digest of a random 32-byte token (64 hex characters)
    id = Column(String(64), primary_key=True)
    user_id = Column(String(36), ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    user = relationship("AuthUser", back_populates="sessions")


class AuthAudit(Base):
    __tablename__ = "auth_audit"
    __table_args__ = (
        Index("ix_auth_audit_at", "at"),
        Index("ix_auth_audit_actor", "actor_user_id"),
        Index("ix_auth_audit_target", "target_user_id"),
        Index("ix_auth_audit_action", "action"),
        {"extend_existing": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    actor_user_id = Column(String(36), ForeignKey("auth_users.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(50), nullable=False)
    target_user_id = Column(String(36), ForeignKey("auth_users.id", ondelete="SET NULL"), nullable=True)
    detail = Column(JSON, nullable=True)
