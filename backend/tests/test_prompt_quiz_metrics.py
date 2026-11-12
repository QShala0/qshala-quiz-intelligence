from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.app.services.prompt_quiz import engine, metrics


_WORDS = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo sierra tango".split()


def _bank():
    return [{"id": f"q{i}", "question_text": f"Banking {_WORDS[i]} {_WORDS[(i * 7 + 3) % 20]} {_WORDS[(i * 11 + 5) % 20]} {i} about rbi", "answer": f"A{i}",
             "topic_l1": "Finance", "subtopic_l2": "Banks and Banking", "secondary_l2": [], "keywords": ["bank", "loan"],
             "difficulty_score": 0.5, "difficulty": "Medium", "tagged": True, "image_dependent": False} for i in range(20)]


def test_draft_records_stage_timings_and_does_not_change_the_questions(monkeypatch):
    metrics.reset()
    monkeypatch.setenv("ENABLE_AI_CURATOR", "false")
    monkeypatch.setenv("ENABLE_INTENT_ENGINE", "false")
    with patch.object(engine, "_load_bank", lambda db, *a, **k: [dict(r) for r in _bank()]), \
         patch("backend.app.config.settings.GEMINI_API_KEY", ""):
        d = engine.build_draft(MagicMock(), "5 questions on banking")
    assert len(d["questions"]) == 5
    assert set(d["timings_ms"]) >= {"interpret", "load_bank", "retrieve", "rank_guard", "select", "total"}
    s = metrics.summary()
    assert s["drafts_in_window"] == 1 and s["last_bank_size"] == 20 and s["interpreter_regex_rate"] == 1.0


def test_metrics_endpoint_reports_percentiles_and_breaker_state():
    metrics.reset()
    for ms in (10, 20, 30, 40, 1000):
        metrics.record({"at": 0, "total_ms": ms, "stages": {"retrieve": ms / 2}, "bank_size": 9,
                        "interpreter": "gemini", "short": False, "curator": "used"})
    from backend.app.main import app
    body = TestClient(app).get("/api/v1/prompt-quiz/metrics").json()
    assert body["drafts_in_window"] == 5 and body["total_ms"]["p50"] == 30 and body["total_ms"]["max"] == 1000
    assert body["gemini_breaker_open"] is False and body["curator_fallback_rate"] == 0.0
    metrics.reset()
