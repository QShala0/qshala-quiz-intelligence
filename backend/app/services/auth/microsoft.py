"""Microsoft Entra ID OAuth with PKCE and ID token validation."""
import base64
import hashlib
import logging
import secrets
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWK, PyJWKClient

from backend.app.config import settings

logger = logging.getLogger(__name__)

OAUTH_STATE_COOKIE_NAME = "qs_ms_oauth"
OAUTH_STATE_MAX_AGE = 600  # 10 minutes

# Cached PyJWKClient per tenant ID
_jwk_clients: Dict[str, PyJWKClient] = {}


def generate_code_verifier() -> str:
    """Generate a random URL-safe PKCE code verifier."""
    return secrets.token_urlsafe(64)


def generate_code_challenge(verifier: str) -> str:
    """Compute S256 code challenge from code_verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def generate_state() -> str:
    """Generate random state token."""
    return secrets.token_urlsafe(32)


def generate_nonce() -> str:
    """Generate random nonce token."""
    return secrets.token_urlsafe(32)


def get_oauth_signing_secret() -> str:
    """Secret key used to sign and verify oauth state cookie."""
    return (
        settings.API_KEY_SECRET
        or settings.AUTH_MS_CLIENT_SECRET
        or settings.AZURE_CLIENT_SECRET
        or "qshala-oauth-state-secret-signing-key"
    )


def sign_oauth_state(state: str, nonce: str, verifier: str) -> str:
    """Encode state, nonce, and code_verifier into a signed short-lived JWT."""
    payload = {
        "state": state,
        "nonce": nonce,
        "code_verifier": verifier,
        "exp": int(time.time()) + OAUTH_STATE_MAX_AGE,
    }
    return jwt.encode(payload, get_oauth_signing_secret(), algorithm="HS256")


def verify_oauth_state(token: str) -> Optional[Dict[str, Any]]:
    """Decode and verify signed state cookie, returning payload or None."""
    if not token:
        return None
    try:
        return jwt.decode(
            token,
            get_oauth_signing_secret(),
            algorithms=["HS256"],
            options={"require": ["state", "nonce", "code_verifier", "exp"]},
        )
    except Exception as e:
        logger.warning(f"Failed to verify oauth state cookie: {e}")
        return None


def get_tenant_id() -> str:
    """Resolve Azure Tenant ID from settings."""
    return settings.AZURE_TENANT_ID or "common"


def get_client_id() -> str:
    """Resolve Microsoft Client ID from settings."""
    return settings.AUTH_MS_CLIENT_ID or settings.AZURE_CLIENT_ID or ""


def get_client_secret() -> str:
    """Resolve Microsoft Client Secret from settings."""
    return settings.AUTH_MS_CLIENT_SECRET or settings.AZURE_CLIENT_SECRET or ""


def build_redirect_url(path: str) -> str:
    """Build redirect URL rooted at AUTH_PUBLIC_BASE_URL (or relative path if unset)."""
    base = (settings.AUTH_PUBLIC_BASE_URL or "").rstrip("/")
    rel_path = "/" + path.lstrip("/")
    if base:
        return f"{base}{rel_path}"
    return rel_path


def get_oauth_redirect_uri(request_base_url: Optional[str] = None) -> str:
    """Get canonical redirect URI for Microsoft callback."""
    base = (settings.AUTH_PUBLIC_BASE_URL or "").rstrip("/")
    if base:
        return f"{base}/api/v1/auth/microsoft/callback"
    if request_base_url:
        return f"{request_base_url.rstrip('/')}/api/v1/auth/microsoft/callback"
    return "http://localhost:8000/api/v1/auth/microsoft/callback"


def get_authorize_url(
    state: str,
    nonce: str,
    code_challenge: str,
    redirect_uri: str,
    *,
    tenant_id: Optional[str] = None,
    client_id: Optional[str] = None,
) -> str:
    """Build Microsoft OAuth 2.0 authorization URL with PKCE."""
    tid = tenant_id or get_tenant_id()
    cid = client_id or get_client_id()
    base_url = f"https://login.microsoftonline.com/{tid}/oauth2/v2.0/authorize"
    params = {
        "client_id": cid,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{base_url}?{urlencode(params)}"


def get_jwk_client(tenant_id: str) -> PyJWKClient:
    """Get or create cached PyJWKClient for tenant."""
    if tenant_id not in _jwk_clients:
        jwks_url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        _jwk_clients[tenant_id] = PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=3600)
    return _jwk_clients[tenant_id]


async def exchange_code_for_tokens(
    code: str,
    verifier: str,
    redirect_uri: str,
    *,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Exchange authorization code with Microsoft for tokens using PKCE."""
    cid = client_id or get_client_id()
    csec = client_secret or get_client_secret()
    tid = tenant_id or get_tenant_id()
    token_url = f"https://login.microsoftonline.com/{tid}/oauth2/v2.0/token"

    payload = {
        "client_id": cid,
        "client_secret": csec,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(token_url, data=payload)
        if resp.status_code != 200:
            logger.error("Microsoft token exchange failed: %s %s", resp.status_code, resp.text)
            raise ValueError(f"Token exchange failed with HTTP {resp.status_code}: {resp.text}")
        return resp.json()


def validate_microsoft_id_token(
    id_token: str,
    expected_nonce: str,
    *,
    client_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    signing_key: Optional[Any] = None,
    jwks_keys: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Validate Microsoft ID token signature, issuer, audience, tenant id, nonce, and expiry."""
    cid = client_id or get_client_id()
    tid = tenant_id or get_tenant_id()
    expected_issuer = f"https://login.microsoftonline.com/{tid}/v2.0"

    key = signing_key
    if not key and jwks_keys:
        unverified_headers = jwt.get_unverified_header(id_token)
        kid = unverified_headers.get("kid")
        matching = [k for k in jwks_keys if k.get("kid") == kid]
        if not matching:
            raise ValueError(f"No key matching kid '{kid}' found in provided jwks_keys")
        key = PyJWK(matching[0]).key
    elif not key:
        jwk_client = get_jwk_client(tid)
        key = jwk_client.get_signing_key_from_jwt(id_token).key

    # Decode and validate signature, exp, iss, aud
    claims = jwt.decode(
        id_token,
        key=key,
        algorithms=["RS256"],
        audience=cid,
        issuer=expected_issuer,
        options={
            "require": ["exp", "iss", "aud", "nonce"],
            "verify_exp": True,
            "verify_iss": True,
            "verify_aud": True,
        },
    )

    # Validate tid claim
    token_tid = claims.get("tid")
    if token_tid != tid:
        raise ValueError(f"Token tid '{token_tid}' does not match expected tenant '{tid}'")

    # Validate nonce
    token_nonce = claims.get("nonce")
    if token_nonce != expected_nonce:
        raise ValueError("Token nonce does not match expected nonce")

    return claims


def validate_email_domain(email: str) -> bool:
    """Check if email domain is allowed by AUTH_ALLOWED_EMAIL_DOMAINS."""
    if not email or "@" not in email:
        return False
    domain = email.split("@", 1)[1].strip().lower()
    allowed = [d.strip().lower() for d in settings.AUTH_ALLOWED_EMAIL_DOMAINS.split(",") if d.strip()]
    return domain in allowed
