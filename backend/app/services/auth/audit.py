from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

from backend.app.models.auth import AuthAudit


def log_auth_audit(
    db: Session,
    action: str,
    actor_user_id: Optional[str] = None,
    target_user_id: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> AuthAudit:
    """Record an audit trail entry for authentication and administrative actions."""
    entry = AuthAudit(
        at=datetime.now(timezone.utc),
        actor_user_id=actor_user_id,
        action=action,
        target_user_id=target_user_id,
        detail=detail or {},
    )
    db.add(entry)
    db.flush()
    return entry
