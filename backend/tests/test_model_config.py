from unittest.mock import MagicMock, patch

from backend.app.services.ai import model_check, model_config


def test_every_role_resolves_to_the_configured_model_by_default(monkeypatch):
    for v in ("GEMINI_MODEL_INTERPRETER", "GEMINI_MODEL_INTENT", "GEMINI_MODEL_CURATOR"):
        monkeypatch.delenv(v, raising=False)
    with patch("backend.app.config.settings.GEMINI_MODEL", "gemini-test-1"):
        assert model_config.interpreter_model() == model_config.intent_model() == model_config.curator_model() == "gemini-test-1"


def test_role_override_only_affects_that_role(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL_CURATOR", "gemini-curator-x")
    with patch("backend.app.config.settings.GEMINI_MODEL", "gemini-test-1"):
        assert model_config.curator_model() == "gemini-curator-x"
        assert model_config.interpreter_model() == "gemini-test-1"


def test_model_check_flags_a_missing_model_without_spending_tokens():
    fake = MagicMock()
    fake.get.return_value = MagicMock(status_code=404)
    with patch("backend.app.config.settings.GEMINI_API_KEY", "k"), \
         patch("backend.app.services.prompt_quiz._http.get_client", return_value=fake):
        res = model_check.check_models()
    assert all(v["ok"] is False and v["status"] == 404 for v in res.values())
    assert not fake.post.called and fake.get.called          # metadata lookups only


def test_model_check_without_a_key_is_skipped_and_status_never_blocks(monkeypatch):
    with patch("backend.app.config.settings.GEMINI_API_KEY", ""):
        assert model_check.check_models() is None
    monkeypatch.setitem(model_check._state, "at", 0.0)
    monkeypatch.setitem(model_check._state, "running", True)     # a refresh is "in flight": get_status must just return
    monkeypatch.setitem(model_check._state, "result", {"x": 1})
    assert model_check.get_status() == {"x": 1}


def test_health_reports_models_but_does_not_depend_on_them():
    from fastapi.testclient import TestClient
    from backend.app.main import app
    with patch("backend.app.services.ai.model_check.get_status", return_value={"curator": {"ok": False}}):
        body = TestClient(app).get("/health").json()
    assert body["gemini_models"] == {"curator": {"ok": False}}
    assert "status" in body
