from backend.app.services.auth.passwords import hash_password, verify_password, dummy_verify
from backend.app.services.auth.sessions import (
    create_session,
    verify_and_slide_session,
    delete_session,
    delete_user_sessions,
    set_session_cookie,
    clear_session_cookie,
    SESSION_COOKIE_NAME,
)
from backend.app.services.auth.audit import log_auth_audit
from backend.app.services.auth.seeds import seed_auth
from backend.app.services.auth.dependencies import (
    get_current_user_optional,
    get_current_user,
    require_admin,
)

__all__ = [
    "hash_password",
    "verify_password",
    "dummy_verify",
    "create_session",
    "verify_and_slide_session",
    "delete_session",
    "delete_user_sessions",
    "set_session_cookie",
    "clear_session_cookie",
    "SESSION_COOKIE_NAME",
    "log_auth_audit",
    "seed_auth",
    "get_current_user_optional",
    "get_current_user",
    "require_admin",
]
