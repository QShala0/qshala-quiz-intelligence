"""One shared httpx client for the prompt-quiz LLM calls, so each call reuses an open connection instead of
paying a new TLS handshake. The client is rebuilt if httpx.Client is swapped out (tests patch it)."""
from __future__ import annotations

import threading
from typing import Any

import httpx

_lock = threading.Lock()
_client: Any = None
_client_cls: Any = None


def get_client() -> Any:
    global _client, _client_cls
    cls = httpx.Client
    if _client is None or _client_cls is not cls:
        with _lock:
            if _client is None or _client_cls is not cls:
                _client = cls()
                _client_cls = cls
    return _client


class BreakerOpen(Exception):
    """Gemini is being skipped because it has been failing; callers already treat any exception as 'use the fallback'."""


def guarded_post(url: str, *, json: Any, headers: Any, timeout: float) -> Any:
    from backend.app.services.prompt_quiz import llm_breaker
    if not llm_breaker.allow():
        raise BreakerOpen("Gemini circuit breaker is open")
    try:
        resp = get_client().post(url, json=json, headers=headers, timeout=timeout)
    except Exception as e:
        if llm_breaker.is_service_failure(e):
            llm_breaker.record_failure()
        raise
    code = getattr(resp, "status_code", 200)
    if isinstance(code, int) and (code == 429 or code >= 500):
        llm_breaker.record_failure()
    else:
        llm_breaker.record_success()
    return resp
