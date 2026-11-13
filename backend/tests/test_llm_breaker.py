from unittest.mock import MagicMock, patch

import httpx
import pytest

from backend.app.services.prompt_quiz import _http, engine, llm_breaker


def _client(status=200, exc=None):
    c = MagicMock()
    if exc:
        c.post.side_effect = exc
    else:
        c.post.return_value = MagicMock(status_code=status)
    return c


def _post():
    return _http.guarded_post("http://x", json={}, headers={}, timeout=1)


def test_opens_after_consecutive_service_failures_and_stops_calling_gemini():
    c = _client(exc=httpx.ConnectTimeout("t"))
    with patch.object(_http, "get_client", return_value=c):
        for _ in range(3):
            with pytest.raises(httpx.ConnectTimeout):
                _post()
        with pytest.raises(_http.BreakerOpen):
            _post()
        assert c.post.call_count == 3                       # the 4th call never reached the network


def test_5xx_and_429_count_but_client_errors_and_success_do_not():
    with patch.object(_http, "get_client", return_value=_client(status=503)):
        _post(); _post()
    assert llm_breaker.failure_mark() == 2 and llm_breaker.allow()
    with patch.object(_http, "get_client", return_value=_client(status=400)):
        _post()
    with patch.object(_http, "get_client", return_value=_client(status=200)):
        _post()
    assert llm_breaker.failure_mark() == 2
    with patch.object(_http, "get_client", return_value=_client(status=429)):
        _post()
    assert llm_breaker.failure_mark() == 3 and llm_breaker.allow()     # the successes in between reset the streak


def test_success_resets_the_streak():
    with patch.object(_http, "get_client", return_value=_client(status=500)):
        _post(); _post()
    with patch.object(_http, "get_client", return_value=_client(status=200)):
        _post()
    with patch.object(_http, "get_client", return_value=_client(status=500)):
        _post(); _post()
    assert llm_breaker.allow()                              # 2 + 2 failures, never 3 in a row


def test_half_open_after_cooldown_reopens_on_one_failure(monkeypatch):
    monkeypatch.setenv("PROMPT_QUIZ_BREAKER_COOLDOWN_S", "1")
    with patch.object(_http, "get_client", return_value=_client(status=500)):
        for _ in range(3):
            _post()
        assert not llm_breaker.allow()
        import time
        time.sleep(1.1)
        assert llm_breaker.allow()
        _post()
        assert not llm_breaker.allow()


def test_disabled_breaker_never_blocks(monkeypatch):
    monkeypatch.setenv("PROMPT_QUIZ_BREAKER", "0")
    with patch.object(_http, "get_client", return_value=_client(status=500)):
        for _ in range(6):
            _post()
    assert llm_breaker.allow()


def test_no_second_gemini_call_when_the_first_just_failed(monkeypatch):
    monkeypatch.setenv("ENABLE_INTENT_ENGINE", "true")
    called = []

    def intent_fails(*a, **k):
        llm_breaker.record_failure()                         # as guarded_post does on a timeout
        return None

    import backend.app.services.prompt_quiz.intent_engine as ie
    import backend.app.services.prompt_quiz.interpreter_llm as il
    monkeypatch.setattr(ie, "deconstruct_prompt", intent_fails)
    monkeypatch.setattr(il, "interpret_with_gemini", lambda *a, **k: called.append(1))
    b = engine.interpret("10 questions on banking", db=MagicMock())
    assert b.source == "regex" and called == []


def test_interpreter_is_still_tried_when_the_intent_engine_returns_nothing_without_a_failure(monkeypatch):
    called = []
    import backend.app.services.prompt_quiz.intent_engine as ie
    import backend.app.services.prompt_quiz.interpreter_llm as il
    monkeypatch.setattr(ie, "deconstruct_prompt", lambda *a, **k: None)
    monkeypatch.setattr(il, "interpret_with_gemini", lambda *a, **k: called.append(1))
    engine.interpret("10 questions on banking", db=MagicMock())
    assert called == [1]
