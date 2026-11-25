import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from fastapi import Request, Response
from sqlalchemy.orm import Session, joinedload

from backend.app.models.auth import AuthSession, AuthUser

SESSION_COOKIE_NAME = "qs_session"
SLIDING_LIFETIME = timedelta(days=7)
ABSOLUTE_LIFETIME = timedelta(days=30)
DB_UPDATE_THROTTLE = timedelta(minutes=5)


def hash_token(raw_token: str) -> str:
    """Compute sha256 hex digest of a raw session token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_session(db: Session, user: AuthUser) -> Tuple[str, AuthSession]:
    """Create a new session for a user, return (raw_token, session_record)."""
    raw_token = secrets.token_hex(32)
    session_id = hash_token(raw_token)
    now = datetime.now(timezone.utc)
    expires_at = now + SLIDING_LIFETIME

    session_record = AuthSession(
        id=session_id,
        user_id=user.id,
        created_at=now,
        expires_at=expires_at,
        last_seen_at=now,
    )
    db.add(session_record)
    db.commit()
    db.refresh(session_record)
    return raw_token, session_record


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def verify_and_slide_session(
    db: Session, raw_token: Optional[str]
) -> Optional[AuthUser]:
    """Verify raw token from session cookie, enforce sliding + absolute limits,
    throttle DB updates, and return active user (or None if invalid/expired/blocked)."""
    if not raw_token or not isinstance(raw_token, str):
        return None

    session_id = hash_token(raw_token)
    session_record = (
        db.query(AuthSession)
        .options(joinedload(AuthSession.user).joinedload(AuthUser.team))
        .filter(AuthSession.id == session_id)
        .first()
    )

    if not session_record or not session_record.user:
        return None

    user = session_record.user
    if user.status != "active":
        # Inactive or blocked users cannot use existing sessions
        delete_user_sessions(db, user.id)
        return None

    now = datetime.now(timezone.utc)
    created_at = _ensure_utc(session_record.created_at)
    expires_at = _ensure_utc(session_record.expires_at)
    last_seen_at = _ensure_utc(session_record.last_seen_at)

    absolute_max = created_at + ABSOLUTE_LIFETIME

    # Check expiration
    if now >= expires_at or now >= absolute_max:
        db.delete(session_record)
        db.commit()
        return None

    # Slide session if throttle window passed
    if last_seen_at is None or (now - last_seen_at) >= DB_UPDATE_THROTTLE:
        new_expires_at = min(now + SLIDING_LIFETIME, absolute_max)
        session_record.last_seen_at = now
        session_record.expires_at = new_expires_at
        db.commit()

    return user


def delete_session(db: Session, raw_token: Optional[str]) -> bool:
    """Delete a single session by raw token (e.g. on logout)."""
    if not raw_token:
        return False
    session_id = hash_token(raw_token)
    session_record = (
        db.query(AuthSession).filter(AuthSession.id == session_id).first()
    )
    if session_record:
        db.delete(session_record)
        db.commit()
        return True
    return False


def delete_user_sessions(db: Session, user_id: str) -> int:
    """Delete all sessions for a user (e.g. when blocked or password reset)."""
    count = (
        db.query(AuthSession).filter(AuthSession.user_id == user_id).delete()
    )
    db.commit()
    return count


def is_request_secure(request: Optional[Request] = None) -> bool:
    """Determine if cookie should be set with Secure=True."""
    if bool(os.getenv("K_SERVICE")):
        return True
    if request:
        proto = request.headers.get("x-forwarded-proto", "").lower()
        if proto == "https" or request.url.scheme == "https":
            return True
    return False


def set_session_cookie(
    response: Response,
    raw_token: str,
    request: Optional[Request] = None,
    max_age: int = 30 * 86400,
) -> None:
    """Set the qs_session HTTP cookie with required security attributes."""
    secure = is_request_secure(request)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(
    response: Response, request: Optional[Request] = None
) -> None:
    """Clear the qs_session cookie."""
    secure = is_request_secure(request)
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=secure,
    )
