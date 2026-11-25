"""Circuit breaker shared by the prompt engine's Gemini calls (interpreter, intent engine, curator).

After PROMPT_QUIZ_BREAKER_FAILS (default 3) consecutive service failures (timeouts, connection errors, HTTP 429/5xx) the
engine skips Gemini for PROMPT_QUIZ_BREAKER_COOLDOWN_S (default 60) and uses its deterministic path straight away
instead of waiting for each call to time out. After the cooldown one failed call re-opens it; a success closes it.
Healthy Gemini: this never triggers and nothing changes. PROMPT_QUIZ_BREAKER=0 turns it off.
"""
import os
import threading
import time

_lock = threading.Lock()
_state = {"consecutive": 0, "open_until": 0.0, "total_failures": 0}


def _threshold() -> int:
    try:
        return max(1, int(os.getenv("PROMPT_QUIZ_BREAKER_FAILS", "3")))
    except ValueError:
        return 3


def _cooldown() -> float:
    try:
        return max(1.0, float(os.getenv("PROMPT_QUIZ_BREAKER_COOLDOWN_S", "60")))
    except ValueError:
        return 60.0


def enabled() -> bool:
    return os.getenv("PROMPT_QUIZ_BREAKER", "1").strip().lower() not in ("0", "false", "no")


def allow() -> bool:
    if not enabled():
        return True
    with _lock:
        return time.monotonic() >= _state["open_until"]


def record_success() -> None:
    with _lock:
        _state["consecutive"] = 0


def record_failure() -> None:
    with _lock:
        _state["total_failures"] += 1
        _state["consecutive"] += 1
        if enabled() and _state["consecutive"] >= _threshold():
            _state["open_until"] = time.monotonic() + _cooldown()
            _state["consecutive"] = _threshold() - 1     # half-open: one more failure after the cooldown re-opens it


def failure_mark() -> int:
    """A counter that rises with every service failure; compare before and after a call to see if it failed."""
    with _lock:
        return _state["total_failures"]


def reset() -> None:
    with _lock:
        _state.update({"consecutive": 0, "open_until": 0.0, "total_failures": 0})


def is_service_failure(exc: BaseException) -> bool:
    import httpx
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return isinstance(exc, httpx.HTTPError)
