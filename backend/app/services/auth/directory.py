"""Microsoft 365 Directory service for user discovery via Microsoft Graph API.

Fetches eligible organizational users from Microsoft Graph using an app-only
client credentials token from the login Azure AD app (AUTH_MS_CLIENT_ID / AUTH_MS_CLIENT_SECRET).
Includes in-memory token and directory list caching (5 minutes), pagination support,
and filtering against allowed domains, guest accounts, and existing local users.
"""
import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException, status
import httpx
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models.auth import AuthUser
from backend.app.services.auth.microsoft import validate_email_domain

logger = logging.getLogger(__name__)

# Constants
DIRECTORY_CACHE_TTL = 300.0  # 5 minutes
MAX_DIRECTORY_USERS = 5000  # Cap at 5000 directory users to prevent runaway pagination
TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
GRAPH_USERS_URL = (
    "https://graph.microsoft.com/v1.0/users"
    "?$select=id,displayName,mail,userPrincipalName,accountEnabled&$top=999"
)

# In-memory caches
_app_token: Optional[str] = None
_app_token_expires_at: float = 0.0

_directory_cache: Optional[List[Dict[str, str]]] = None
_directory_cached_at: float = 0.0


def clear_directory_cache() -> None:
    """Clear cached token and directory list (useful for tests and manual invalidation)."""
    global _app_token, _app_token_expires_at, _directory_cache, _directory_cached_at
    _app_token = None
    _app_token_expires_at = 0.0
    _directory_cache = None
    _directory_cached_at = 0.0


def get_login_app_credentials() -> Tuple[str, str, str]:
    """Resolve login app credentials (tenant_id, client_id, client_secret).
    Uses ONLY AUTH_MS_CLIENT_ID and AUTH_MS_CLIENT_SECRET (no fallback to SharePoint credentials)."""
    tenant_id = settings.AZURE_TENANT_ID or "common"
    client_id = settings.AUTH_MS_CLIENT_ID or ""
    client_secret = settings.AUTH_MS_CLIENT_SECRET or ""
    return tenant_id, client_id, client_secret


async def get_directory_app_token(http_client: Optional[httpx.AsyncClient] = None) -> str:
    """Acquire or return cached app-only access token for Microsoft Graph."""
    global _app_token, _app_token_expires_at
    now = time.time()
    if _app_token and now < (_app_token_expires_at - 120):
        return _app_token

    tenant_id, client_id, client_secret = get_login_app_credentials()
    if not client_id or not client_secret:
        logger.warning("Microsoft login app client ID or secret is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Directory access is not enabled. Ask a Microsoft 365 admin to grant User.Read.All to the login app.",
        )

    token_url = TOKEN_URL_TEMPLATE.format(tenant_id=tenant_id)
    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
    }

    should_close = False
    client = http_client
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
        should_close = True

    try:
        resp = await client.post(token_url, data=payload)
        if resp.status_code != 200:
            req_id = (
                resp.headers.get("client-request-id")
                or resp.headers.get("x-ms-request-id")
                or resp.headers.get("request-id")
                or "unknown"
            )
            logger.warning(
                "Microsoft token endpoint returned HTTP %d (request-id: %s)",
                resp.status_code,
                req_id,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Directory access is not enabled. Ask a Microsoft 365 admin to grant User.Read.All to the login app.",
            )
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Directory access is not enabled. Ask a Microsoft 365 admin to grant User.Read.All to the login app.",
            )
        expires_in = float(data.get("expires_in", 3600))
        _app_token = token
        _app_token_expires_at = now + expires_in
        return _app_token
    except HTTPException:
        raise
    except Exception:
        logger.error("Failed to connect to Microsoft token endpoint")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Directory access is not enabled. Ask a Microsoft 365 admin to grant User.Read.All to the login app.",
        )
    finally:
        if should_close:
            await client.aclose()


async def fetch_directory_users_from_graph(
    http_client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, str]]:
    """Fetch all users from Microsoft Graph, paginating up to MAX_DIRECTORY_USERS (5000).

    Results are cached in memory for DIRECTORY_CACHE_TTL (5 minutes).
    Filtered for accountEnabled=True, non-guest accounts, and allowed domains.
    """
    global _directory_cache, _directory_cached_at
    now = time.time()
    if _directory_cache is not None and (now - _directory_cached_at) < DIRECTORY_CACHE_TTL:
        return _directory_cache

    token = await get_directory_app_token(http_client=http_client)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    url: Optional[str] = GRAPH_USERS_URL
    raw_users: List[Dict[str, Any]] = []

    should_close = False
    client = http_client
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        should_close = True

    try:
        while url and len(raw_users) < MAX_DIRECTORY_USERS:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                req_id = (
                    resp.headers.get("client-request-id")
                    or resp.headers.get("request-id")
                    or resp.headers.get("x-ms-request-id")
                    or "unknown"
                )
                logger.warning(
                    "Microsoft Graph /users returned HTTP %d (request-id: %s)",
                    resp.status_code,
                    req_id,
                )
                if resp.status_code in (401, 403):
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Directory access is not enabled. Ask a Microsoft 365 admin to grant User.Read.All to the login app.",
                    )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Directory service is currently unavailable.",
                )

            data = resp.json()
            items = data.get("value", [])
            raw_users.extend(items)
            url = data.get("@odata.nextLink")

        if len(raw_users) > MAX_DIRECTORY_USERS:
            raw_users = raw_users[:MAX_DIRECTORY_USERS]
    except HTTPException:
        raise
    except Exception:
        logger.error("Failed to connect to Microsoft Graph API")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Directory service is currently unavailable.",
        )
    finally:
        if should_close:
            await client.aclose()

    # Filter raw users
    valid_users: List[Dict[str, str]] = []
    seen_emails = set()

    for u in raw_users:
        # 1. accountEnabled must be True
        if u.get("accountEnabled") is not True:
            continue

        # 2. Extract email (mail, falling back to userPrincipalName)
        mail = (u.get("mail") or "").strip()
        upn = (u.get("userPrincipalName") or "").strip()
        email = (mail or upn).lower()
        if not email or "@" not in email:
            continue

        # 3. Drop guests / external accounts
        if (u.get("userType") or "").lower() == "guest":
            continue
        if "#ext#" in upn.lower() or "#ext#" in email.lower():
            continue

        # 4. Domain validation against AUTH_ALLOWED_EMAIL_DOMAINS
        if not validate_email_domain(email):
            continue

        # Deduplicate by email
        if email in seen_emails:
            continue
        seen_emails.add(email)

        display_name = (u.get("displayName") or "").strip() or email
        valid_users.append({
            "display_name": display_name,
            "email": email,
        })

    _directory_cache = valid_users
    _directory_cached_at = now
    return valid_users


async def get_directory_users(
    db: Session,
    q: Optional[str] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, str]]:
    """Retrieve filtered, sorted Microsoft 365 directory users.

    Excludes users already present in auth_users.
    Supports optional ?q= substring filtering on display_name and email.
    """
    all_users = await fetch_directory_users_from_graph(http_client=http_client)

    # Exclude emails already present in auth_users (excluding deleted accounts so deleted users can be re-added)
    existing_emails = {
        row[0].strip().lower()
        for row in db.query(AuthUser.email)
        .filter(AuthUser.email.isnot(None), AuthUser.status != "deleted")
        .all()
        if row[0]
    }
    available = [u for u in all_users if u["email"].lower() not in existing_emails]

    # Optional server-side search filter ?q=
    if q and q.strip():
        term = q.strip().lower()
        available = [
            u for u in available
            if term in u["display_name"].lower() or term in u["email"].lower()
        ]

    # Sort alphabetically by display name (case-insensitive)
    available.sort(key=lambda u: u["display_name"].lower())
    return available
