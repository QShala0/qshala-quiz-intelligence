"""Daily check that every configured Gemini model still exists. Uses the models.get metadata call: no tokens are spent.

A retired or misspelled model otherwise fails quietly (the prompt engine falls back to its regex path and quizzes just
get worse). get_status() never blocks and never raises: it returns the last result and refreshes in the background.
"""
import logging
import threading
import time
from typing import Any, Dict, Optional

from backend.app.services.ai.model_config import models_in_use

logger = logging.getLogger(__name__)
_TTL_S = 24 * 3600
_state: Dict[str, Any] = {"at": 0.0, "result": None, "running": False}
_lock = threading.Lock()


def _api_key() -> str:
    from backend.app.config import settings
    key = (getattr(settings, "GEMINI_API_KEY", None) or "").strip()
    if len(key) > 1 and key[0] == key[-1] and key[0] in "\"'":
        key = key[1:-1].strip()
    return key


def check_models(timeout_s: float = 5.0) -> Optional[Dict[str, Any]]:
    key = _api_key()
    if not key:
        return None
    from backend.app.services.prompt_quiz._http import get_client
    out: Dict[str, Any] = {}
    for role, model in models_in_use().items():
        try:
            r = get_client().get(f"https://generativelanguage.googleapis.com/v1beta/models/{model}",
                                 headers={"x-goog-api-key": key}, timeout=timeout_s)
            out[role] = {"model": model, "ok": r.status_code == 200, "status": r.status_code}
            if r.status_code != 200:
                logger.warning("Gemini model check: %s model %r returned HTTP %s", role, model, r.status_code)
        except Exception as e:
            out[role] = {"model": model, "ok": None, "error": type(e).__name__}
    return out


def _refresh() -> None:
    try:
        res = check_models()
        with _lock:
            _state["result"], _state["at"] = res, time.monotonic()
    except Exception as e:
        logger.warning("Gemini model check failed: %s", e)
    finally:
        with _lock:
            _state["running"] = False


def get_status() -> Optional[Dict[str, Any]]:
    """Last known result (None before the first check finishes, or with no API key). Starts a refresh when it is stale."""
    with _lock:
        stale = time.monotonic() - _state["at"] > _TTL_S
        start = stale and not _state["running"]
        if start:
            _state["running"] = True
        result = _state["result"]
    if start:
        threading.Thread(target=_refresh, name="gemini-model-check", daemon=True).start()
    return result
