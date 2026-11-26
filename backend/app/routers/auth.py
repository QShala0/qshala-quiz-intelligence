import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import get_db
from backend.app.models.auth import AuthAudit, AuthTeam, AuthUser
from backend.app.services.auth.audit import log_auth_audit
from backend.app.services.auth.dependencies import (
    get_current_user,
    get_current_user_optional,
    require_admin,
)
from backend.app.services.auth.directory import get_directory_users
from backend.app.services.auth.microsoft import (
    OAUTH_STATE_COOKIE_NAME,
    OAUTH_STATE_MAX_AGE,
    build_redirect_url,
    exchange_code_for_tokens,
    generate_code_challenge,
    generate_code_verifier,
    generate_nonce,
    generate_state,
    get_authorize_url,
    get_oauth_redirect_uri,
    sign_oauth_state,
    validate_email_domain,
    validate_microsoft_id_token,
    verify_oauth_state,
)
from backend.app.services.auth.passwords import dummy_verify, hash_password, verify_password
from backend.app.services.auth.sessions import (
    SESSION_COOKIE_NAME,
    clear_session_cookie,
    create_session,
    delete_session,
    delete_user_sessions,
    set_session_cookie,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])

INVALID_CREDENTIALS_MSG = "Invalid username or password"


# ---------------------------------------------------------------------- Pydantic Schemas

class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class CreateUserRequest(BaseModel):
    email: Optional[str] = None
    username: Optional[str] = None
    display_name: str
    team_id: Optional[str] = None
    role: str = "member"  # admin, member
    provider: str  # microsoft, password
    password: Optional[str] = None
    must_change_password: bool = True


class UpdateUserRequest(BaseModel):
    display_name: Optional[str] = None
    team_id: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    new_password: str
    must_change_password: bool = True


def is_seeded_user(user: AuthUser) -> bool:
    """Check if a user is one of the initial seeded accounts (admin or consultant)."""
    if getattr(user, "created_by", None) == "system_seed":
        return True
    if getattr(user, "username", None) == "user":
        return True
    if getattr(user, "email", None):
        raw_admins = settings.AUTH_INITIAL_ADMINS or ""
        seeded_emails = {entry.split(":", 1)[0].strip().lower() for entry in raw_admins.split(",") if ":" in entry}
        if user.email.lower() in seeded_emails:
            return True
    return False


def _serialize_user(user: AuthUser) -> Dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "display_name": user.display_name,
        "team": user.team.name if user.team else user.team_id,
        "team_id": user.team_id,
        "role": user.role,
        "status": user.status,
        "provider": user.provider,
        "must_change_password": user.must_change_password,
        "failed_logins": user.failed_logins,
        "locked_until": user.locked_until.isoformat() if user.locked_until else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "created_by": user.created_by,
        "is_seeded": is_seeded_user(user),
    }


# ---------------------------------------------------------------------- Public / Session Endpoints

@router.post("/login")
def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Authenticate with username/password, apply 5-attempt lockout, and set qs_session cookie."""
    now = datetime.now(timezone.utc)
    ident = req.username.strip()

    # Look up by username or lowercase email
    user = (
        db.query(AuthUser)
        .filter((AuthUser.username == ident) | (AuthUser.email == ident.lower()))
        .first()
    )

    if user:
        # Check active lockout
        if user.locked_until:
            locked_until = user.locked_until
            if locked_until.tzinfo is None:
                locked_until = locked_until.replace(tzinfo=timezone.utc)
            if now < locked_until:
                dummy_verify()
                log_auth_audit(
                    db,
                    action="login_locked",
                    target_user_id=user.id,
                    detail={"reason": "account_locked", "locked_until": locked_until.isoformat()},
                )
                db.commit()
                # Uniform error message even when locked
                raise HTTPException(status_code=401, detail=INVALID_CREDENTIALS_MSG)
            else:
                # Lockout window has expired: reset attempts
                user.locked_until = None
                user.failed_logins = 0

    # User does not exist, is not a password account, or is soft-deleted
    if not user or user.provider != "password" or user.status == "deleted":
        dummy_verify()
        if user and user.status == "deleted":
            log_auth_audit(
                db,
                action="login_rejected_deleted",
                target_user_id=user.id,
                detail={"identifier": ident, "provider": "password"},
            )
            db.commit()
        raise HTTPException(status_code=401, detail=INVALID_CREDENTIALS_MSG)

    # Validate password
    if not verify_password(req.password, user.password_hash):
        user.failed_logins = (user.failed_logins or 0) + 1
        if user.failed_logins >= 5:
            user.locked_until = now + timedelta(minutes=15)
            log_auth_audit(
                db,
                action="login_locked",
                target_user_id=user.id,
                detail={"reason": "max_attempts_exceeded", "failed_logins": user.failed_logins},
            )
        else:
            log_auth_audit(
                db,
                action="login_failed",
                target_user_id=user.id,
                detail={"reason": "bad_password", "failed_logins": user.failed_logins},
            )
        db.commit()
        raise HTTPException(status_code=401, detail=INVALID_CREDENTIALS_MSG)

    # Success: reset failures and update login timestamp
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now

    if user.status != "active":
        log_auth_audit(
            db,
            action="login_rejected_inactive",
            target_user_id=user.id,
            detail={"status": user.status},
        )
        db.commit()
        raise HTTPException(status_code=403, detail="Account is blocked or inactive")

    # Issue session
    raw_token, session_record = create_session(db, user)
    set_session_cookie(response, raw_token, request)

    log_auth_audit(
        db,
        action="login_success",
        actor_user_id=user.id,
        target_user_id=user.id,
        detail={"provider": "password"},
    )
    db.commit()

    return {"user": _serialize_user(user)}


# ---------------------------------------------------------------------- Microsoft Entra ID OAuth Endpoints

@router.get("/microsoft/login")
def microsoft_login(request: Request):
    """Initiate Microsoft Entra ID PKCE authorization code flow."""
    state = generate_state()
    nonce = generate_nonce()
    verifier = generate_code_verifier()
    challenge = generate_code_challenge(verifier)

    redirect_uri = get_oauth_redirect_uri(str(request.base_url))
    authorize_url = get_authorize_url(
        state=state,
        nonce=nonce,
        code_challenge=challenge,
        redirect_uri=redirect_uri,
    )

    signed_state = sign_oauth_state(state=state, nonce=nonce, verifier=verifier)

    is_https = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https"
        or bool(settings.AUTH_PUBLIC_BASE_URL and settings.AUTH_PUBLIC_BASE_URL.startswith("https"))
    )

    response = RedirectResponse(url=authorize_url, status_code=303)
    response.set_cookie(
        key=OAUTH_STATE_COOKIE_NAME,
        value=signed_state,
        max_age=OAUTH_STATE_MAX_AGE,
        httponly=True,
        secure=is_https,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/microsoft/callback")
async def microsoft_callback(
    request: Request,
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
    error_description: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """Handle Microsoft OAuth 2.0 callback, validate ID token, and map user account."""
    def _error_redirect(err_code: str) -> RedirectResponse:
        resp = RedirectResponse(url=build_redirect_url(f"/login?error={err_code}"), status_code=303)
        resp.delete_cookie(OAUTH_STATE_COOKIE_NAME, path="/")
        return resp

    if error:
        logger.warning(f"Microsoft OAuth returned error: {error} - {error_description}")
        return _error_redirect("oauth_error")

    if not code or not state:
        return _error_redirect("missing_parameters")

    # Read and verify oauth state cookie
    cookie_token = request.cookies.get(OAUTH_STATE_COOKIE_NAME)
    state_payload = verify_oauth_state(cookie_token) if cookie_token else None
    if not state_payload or state_payload.get("state") != state:
        logger.warning("Microsoft OAuth callback state mismatch or expired state cookie")
        return _error_redirect("state_mismatch")

    verifier = state_payload.get("code_verifier")
    nonce = state_payload.get("nonce")
    if not verifier or not nonce:
        return _error_redirect("state_invalid")

    redirect_uri = get_oauth_redirect_uri(str(request.base_url))

    # Exchange code for tokens
    try:
        token_data = await exchange_code_for_tokens(
            code=code,
            verifier=verifier,
            redirect_uri=redirect_uri,
        )
    except Exception as e:
        logger.error(f"Failed to exchange Microsoft OAuth code: {e}")
        return _error_redirect("token_exchange_failed")

    id_token = token_data.get("id_token")
    if not id_token:
        logger.error("Microsoft token response did not contain id_token")
        return _error_redirect("missing_id_token")

    # Validate ID token
    try:
        claims = validate_microsoft_id_token(id_token=id_token, expected_nonce=nonce)
    except Exception as e:
        logger.error(f"Microsoft ID token validation failed: {e}")
        return _error_redirect("invalid_id_token")

    # Read email from email claim, falling back to preferred_username; lowercase it
    email = (claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    if not email:
        logger.warning("No email or preferred_username claim found in ID token")
        return _error_redirect("missing_email")

    # Reject domain not in AUTH_ALLOWED_EMAIL_DOMAINS
    if not validate_email_domain(email):
        logger.warning(f"Email domain rejected for user: {email}")
        return _error_redirect("unauthorized_domain")

    ms_oid = claims.get("oid") or claims.get("sub")
    if not ms_oid:
        logger.warning("No oid or sub claim found in ID token")
        return _error_redirect("missing_oid")

    display_name = claims.get("name") or (email.split("@")[0] if "@" in email else email)

    # Mapping rules:
    # Match by ms_oid, then by email.
    user = db.query(AuthUser).filter(AuthUser.ms_oid == ms_oid).first()
    if not user and email:
        user = db.query(AuthUser).filter(AuthUser.email == email).first()

    now = datetime.now(timezone.utc)

    if user:
        # User found
        if user.status == "deleted":
            log_auth_audit(
                db,
                actor_user_id=user.id,
                action="login_rejected_deleted",
                target_user_id=user.id,
                detail={"email": email, "provider": "microsoft"},
            )
            db.commit()
            return _error_redirect("unauthorized")

        elif user.status == "active":
            if user.ms_oid != ms_oid:
                user.ms_oid = ms_oid
            if not user.display_name and display_name:
                user.display_name = display_name
            user.last_login_at = now
            db.commit()

            # Create session
            raw_token, session_record = create_session(db, user)
            log_auth_audit(
                db,
                actor_user_id=user.id,
                action="microsoft_login",
                target_user_id=user.id,
                detail={"email": email, "ms_oid": ms_oid},
            )
            db.commit()

            response = RedirectResponse(url=build_redirect_url("/"), status_code=303)
            set_session_cookie(response, raw_token, request)
            response.delete_cookie(OAUTH_STATE_COOKIE_NAME, path="/")
            return response

        elif user.status == "pending":
            if user.ms_oid != ms_oid:
                user.ms_oid = ms_oid
                db.commit()
            response = RedirectResponse(url=build_redirect_url("/pending"), status_code=303)
            response.delete_cookie(OAUTH_STATE_COOKIE_NAME, path="/")
            return response

        elif user.status == "blocked":
            response = RedirectResponse(url=build_redirect_url("/login?error=blocked"), status_code=303)
            response.delete_cookie(OAUTH_STATE_COOKIE_NAME, path="/")
            return response

        else:
            response = RedirectResponse(url=build_redirect_url("/pending"), status_code=303)
            response.delete_cookie(OAUTH_STATE_COOKIE_NAME, path="/")
            return response

    else:
        # Not found: create a pending user (name, email, oid, no team) and redirect to /pending.
        new_user = AuthUser(
            id=str(uuid.uuid4()),
            email=email,
            username=None,
            display_name=display_name,
            team_id=None,
            role="member",
            status="pending",
            provider="microsoft",
            password_hash=None,
            ms_oid=ms_oid,
            must_change_password=False,
            failed_logins=0,
            locked_until=None,
            last_login_at=None,
            created_at=now,
            created_by="microsoft_oauth",
        )
        db.add(new_user)
        db.commit()

        log_auth_audit(
            db,
            actor_user_id=new_user.id,
            action="user_registered",
            target_user_id=new_user.id,
            detail={"provider": "microsoft", "email": email, "ms_oid": ms_oid, "status": "pending"},
        )

        response = RedirectResponse(url=build_redirect_url("/pending"), status_code=303)
        response.delete_cookie(OAUTH_STATE_COOKIE_NAME, path="/")
        return response


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Terminate the current session in DB and clear qs_session cookie."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    current_user = getattr(request.state, "user", None)
    if token:
        delete_session(db, token)
        log_auth_audit(
            db,
            action="logout",
            actor_user_id=current_user.id if current_user else None,
            target_user_id=current_user.id if current_user else None,
        )
        db.commit()
    clear_session_cookie(response, request)
    return {"ok": True}


@router.get("/me")
def get_me(
    user: Optional[AuthUser] = Depends(get_current_user_optional),
):
    """Return the profile of the current authenticated user, or null/401 depending on mode."""
    if not user:
        if settings.AUTH_ENFORCED:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return None

    return _serialize_user(user)


@router.post("/change-password")
def change_password(
    req: ChangePasswordRequest,
    user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change current user's password (min 8 chars, password accounts only)."""
    if user.provider != "password":
        raise HTTPException(
            status_code=400,
            detail="Password change only applies to password accounts",
        )
    if not verify_password(req.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Incorrect current password")
    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters long")

    user.password_hash = hash_password(req.new_password)
    user.must_change_password = False
    log_auth_audit(
        db,
        action="password_changed",
        actor_user_id=user.id,
        target_user_id=user.id,
    )
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------- Admin Only Endpoints

class DirectoryUserOut(BaseModel):
    display_name: str
    email: str


@router.get(
    "/directory",
    response_model=List[DirectoryUserOut],
    summary="List available Microsoft 365 directory users for account creation",
)
async def list_directory_users(
    q: Optional[str] = Query(None, description="Optional search term to filter users"),
    _admin: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin-only: fetch eligible directory users from Microsoft Graph.
    Filters out existing users, disabled accounts, and non-allowed domains.
    Results are cached for 5 minutes."""
    return await get_directory_users(db=db, q=q)


@router.get("/users")
def list_users(
    team: Optional[str] = Query(None, description="Filter by team"),
    include_deleted: bool = Query(False, description="Include deleted users"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    _admin: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin-only: list all registered users with pagination and team filter."""
    query = db.query(AuthUser)
    if not include_deleted:
        query = query.filter(AuthUser.status != "deleted")
    if team:
        query = query.filter(AuthUser.team_id == team)
    total = query.count()
    users = (
        query.order_by(AuthUser.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    serialized = [_serialize_user(u) for u in users]
    return {
        "items": serialized,
        "users": serialized,
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    req: CreateUserRequest,
    admin: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin-only: create a new user (pre-registered Microsoft user or password user)."""
    now = datetime.now(timezone.utc)
    provider = req.provider.strip().lower()
    role = req.role.strip().lower()

    if provider not in ("microsoft", "password"):
        raise HTTPException(status_code=400, detail="Provider must be 'microsoft' or 'password'")
    if role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'member'")

    # Rule: Only microsoft users can be admin.
    if provider == "password" and role == "admin":
        raise HTTPException(
            status_code=400,
            detail="Password users cannot be assigned admin role. Only microsoft users can be admin.",
        )

    email = req.email.strip().lower() if req.email else None
    username = req.username.strip() if req.username else None

    if provider == "microsoft":
        if not email:
            raise HTTPException(status_code=400, detail="Email is required for Microsoft provider")
        if settings.AUTH_ALLOWED_EMAIL_DOMAINS:
            allowed = [d.strip().lower() for d in settings.AUTH_ALLOWED_EMAIL_DOMAINS.split(",") if d.strip()]
            if allowed and not any(email.endswith("@" + d) for d in allowed):
                raise HTTPException(
                    status_code=400,
                    detail=f"Email domain not allowed. Allowed domains: {settings.AUTH_ALLOWED_EMAIL_DOMAINS}",
                )
        existing = db.query(AuthUser).filter(AuthUser.email == email).first()
        if existing:
            if existing.status == "deleted":
                if req.team_id:
                    team_exists = db.query(AuthTeam).filter(AuthTeam.id == req.team_id).first()
                    if not team_exists:
                        db.add(AuthTeam(id=req.team_id, name=req.team_id, created_at=now))
                        db.flush()
                existing.status = "active"
                existing.display_name = req.display_name.strip()
                existing.team_id = req.team_id
                existing.role = role
                existing.provider = provider
                log_auth_audit(
                    db,
                    action="user_restored",
                    actor_user_id=admin.id,
                    target_user_id=existing.id,
                    detail={
                        "email": email,
                        "display_name": existing.display_name,
                        "role": role,
                        "provider": provider,
                        "team": req.team_id,
                        "reason": "restored_from_deleted",
                    },
                )
                db.commit()
                res = _serialize_user(existing)
                res["restored"] = True
                return res
            raise HTTPException(status_code=400, detail="User with this email already exists")

    if provider == "password":
        if not username:
            raise HTTPException(status_code=400, detail="Username is required for password provider")
        if not req.password:
            raise HTTPException(status_code=400, detail="Starting password is required for password provider")
        if len(req.password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters long")
        existing = db.query(AuthUser).filter(AuthUser.username == username).first()
        if existing:
            if existing.status == "deleted":
                if req.team_id:
                    team_exists = db.query(AuthTeam).filter(AuthTeam.id == req.team_id).first()
                    if not team_exists:
                        db.add(AuthTeam(id=req.team_id, name=req.team_id, created_at=now))
                        db.flush()
                existing.status = "active"
                existing.display_name = req.display_name.strip()
                existing.team_id = req.team_id
                existing.role = role
                existing.provider = provider
                existing.password_hash = hash_password(req.password)
                existing.must_change_password = req.must_change_password
                existing.failed_logins = 0
                existing.locked_until = None
                log_auth_audit(
                    db,
                    action="user_restored",
                    actor_user_id=admin.id,
                    target_user_id=existing.id,
                    detail={
                        "username": username,
                        "display_name": existing.display_name,
                        "role": role,
                        "provider": provider,
                        "team": req.team_id,
                        "reason": "restored_from_deleted",
                    },
                )
                db.commit()
                res = _serialize_user(existing)
                res["restored"] = True
                return res
            raise HTTPException(status_code=400, detail="User with this username already exists")

    if req.team_id:
        team_exists = db.query(AuthTeam).filter(AuthTeam.id == req.team_id).first()
        if not team_exists:
            # Create team if not already present
            db.add(AuthTeam(id=req.team_id, name=req.team_id, created_at=now))
            db.flush()

    new_user = AuthUser(
        id=str(uuid.uuid4()),
        email=email,
        username=username,
        display_name=req.display_name.strip(),
        team_id=req.team_id,
        role=role,
        status="active",
        provider=provider,
        password_hash=hash_password(req.password) if req.password and provider == "password" else None,
        must_change_password=req.must_change_password if provider == "password" else False,
        failed_logins=0,
        locked_until=None,
        last_login_at=None,
        created_at=now,
        created_by=admin.id,
    )
    db.add(new_user)
    db.flush()

    log_auth_audit(
        db,
        action="user_created",
        actor_user_id=admin.id,
        target_user_id=new_user.id,
        detail={"email": email, "username": username, "role": role, "provider": provider, "team": req.team_id},
    )
    db.commit()

    return _serialize_user(new_user)


@router.patch("/users/{user_id}")
def update_user(
    user_id: str,
    req: UpdateUserRequest,
    admin: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin-only: update user team, role, status, or display name with last-admin guard."""
    target = db.query(AuthUser).filter(AuthUser.id == user_id).first()
    if not target or target.status == "deleted":
        raise HTTPException(status_code=404, detail="User not found")

    changes = {}

    # Rule: password users cannot be admin
    if req.role is not None:
        new_role = req.role.strip().lower()
        if new_role not in ("admin", "member"):
            raise HTTPException(status_code=400, detail="Role must be 'admin' or 'member'")
        if new_role == "admin" and target.provider == "password":
            raise HTTPException(
                status_code=400,
                detail="Password users cannot be assigned admin role. Only microsoft users can be admin.",
            )
        # Check last active admin demotion
        if target.role == "admin" and target.status == "active" and new_role != "admin":
            other_admins = (
                db.query(AuthUser)
                .filter(AuthUser.role == "admin", AuthUser.status == "active", AuthUser.id != target.id)
                .count()
            )
            if other_admins == 0:
                raise HTTPException(status_code=400, detail="Cannot demote or block the last active admin")
        if target.role != new_role:
            changes["role"] = {"old": target.role, "new": new_role}
            target.role = new_role

    # Status changes
    if req.status is not None:
        new_status = req.status.strip().lower()
        if new_status not in ("active", "pending", "blocked"):
            raise HTTPException(status_code=400, detail="Status must be 'active', 'pending', or 'blocked'")
        # Check last active admin blocking/pending
        if target.role == "admin" and target.status == "active" and new_status != "active":
            other_admins = (
                db.query(AuthUser)
                .filter(AuthUser.role == "admin", AuthUser.status == "active", AuthUser.id != target.id)
                .count()
            )
            if other_admins == 0:
                raise HTTPException(status_code=400, detail="Cannot demote or block the last active admin")
        if target.status != new_status:
            changes["status"] = {"old": target.status, "new": new_status}
            target.status = new_status
            if new_status == "blocked":
                # Blocking a user deletes their sessions at once
                deleted_sessions = delete_user_sessions(db, target.id)
                changes["sessions_revoked"] = deleted_sessions

    if req.display_name is not None:
        new_dn = req.display_name.strip()
        if new_dn and target.display_name != new_dn:
            changes["display_name"] = {"old": target.display_name, "new": new_dn}
            target.display_name = new_dn

    if req.team_id is not None:
        new_team = req.team_id.strip() if req.team_id else None
        if target.team_id != new_team:
            changes["team_id"] = {"old": target.team_id, "new": new_team}
            target.team_id = new_team

    if changes:
        log_auth_audit(
            db,
            action="user_updated",
            actor_user_id=admin.id,
            target_user_id=target.id,
            detail=changes,
        )
        db.commit()

    return _serialize_user(target)


@router.post("/users/{user_id}/reset-password")
def admin_reset_password(
    user_id: str,
    req: ResetPasswordRequest,
    admin: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin-only: reset a password account's password and flag must_change_password=True."""
    target = db.query(AuthUser).filter(AuthUser.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.provider != "password":
        raise HTTPException(status_code=400, detail="Cannot reset password for Microsoft provider accounts")
    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters long")

    target.password_hash = hash_password(req.new_password)
    target.must_change_password = req.must_change_password
    target.failed_logins = 0
    target.locked_until = None

    # Delete existing sessions on password reset
    delete_user_sessions(db, target.id)

    log_auth_audit(
        db,
        action="password_reset_by_admin",
        actor_user_id=admin.id,
        target_user_id=target.id,
        detail={"must_change_password": req.must_change_password},
    )
    db.commit()

    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: str,
    admin: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin-only: soft-delete a user account (cannot delete self, last active admin, or seeded user)."""
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    target = db.query(AuthUser).filter(AuthUser.id == user_id).first()
    if not target or target.status == "deleted":
        raise HTTPException(status_code=404, detail="User not found")

    if is_seeded_user(target):
        raise HTTPException(status_code=409, detail="Seeded account, block it instead")

    if target.role == "admin" and target.status == "active":
        other_admins = (
            db.query(AuthUser)
            .filter(AuthUser.role == "admin", AuthUser.status == "active", AuthUser.id != target.id)
            .count()
        )
        if other_admins == 0:
            raise HTTPException(status_code=400, detail="Cannot delete the last active admin")

    target.status = "deleted"
    deleted_sessions = delete_user_sessions(db, target.id)

    log_auth_audit(
        db,
        action="user_deleted",
        actor_user_id=admin.id,
        target_user_id=target.id,
        detail={
            "display_name": target.display_name,
            "email": target.email,
            "username": target.username,
            "sessions_revoked": deleted_sessions,
        },
    )
    db.commit()

    return {"ok": True, "message": "User soft-deleted successfully"}


@router.get("/audit")
def list_audit_logs(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    _admin: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin-only: view paged audit history (newest first)."""
    offset = (page - 1) * limit
    total = db.query(AuthAudit).count()
    rows = (
        db.query(AuthAudit)
        .order_by(AuthAudit.at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = [
        {
            "id": r.id,
            "at": r.at.isoformat() if r.at else None,
            "actor_user_id": r.actor_user_id,
            "action": r.action,
            "target_user_id": r.target_user_id,
            "detail": r.detail,
        }
        for r in rows
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
    }
