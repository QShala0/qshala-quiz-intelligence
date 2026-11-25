from typing import Optional
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models.auth import AuthUser
from backend.app.services.auth.sessions import (
    SESSION_COOKIE_NAME,
    verify_and_slide_session,
)


def get_current_user_optional(
    request: Request, db: Session = Depends(get_db)
) -> Optional[AuthUser]:
    """Retrieve the current authenticated user from request state or session cookie if valid,
    bound to the current db session."""
    user_state = getattr(request.state, "user", None)
    if user_state and isinstance(user_state, AuthUser):
        # Re-bind to current request db session so modifications (e.g. change password) persist
        user = db.query(AuthUser).filter(AuthUser.id == user_state.id).first()
        if user:
            return user

    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie_token:
        return None

    user = verify_and_slide_session(db, cookie_token)
    if user:
        request.state.user = user
    return user


def get_current_user(
    user: Optional[AuthUser] = Depends(get_current_user_optional),
) -> AuthUser:
    """Require an authenticated, active user. Raises 401 if missing, 403 if not active."""
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    if user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is blocked or inactive",
        )
    return user


def require_admin(
    user: AuthUser = Depends(get_current_user),
) -> AuthUser:
    """Require an active user with admin role. Raises 403 if not an admin."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return user
