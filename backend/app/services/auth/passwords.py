from typing import Optional
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

_ph = PasswordHasher()

# Pre-computed dummy hash for timing-attack mitigation when a user is not found
_DUMMY_HASH = _ph.hash("dummy-timing-password-string-prevent-attack")


def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2id."""
    return _ph.hash(password)


def verify_password(password: str, hashed: Optional[str]) -> bool:
    """Verify a plaintext password against an Argon2id hash."""
    if not hashed:
        return False
    try:
        return _ph.verify(hashed, password)
    except Argon2Error:
        return False
    except Exception:
        return False


def dummy_verify() -> None:
    """Perform a dummy password verification to neutralize timing attacks."""
    try:
        _ph.verify(_DUMMY_HASH, "wrong-password")
    except Exception:
        pass
