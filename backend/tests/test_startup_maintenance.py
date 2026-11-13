"""The whole-vault repair passes must never delay the app from answering requests (on Cloud Run)."""
import threading
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import main as app_main
from backend.app.database import startup_maintenance_mode


def test_mode_defaults_background_on_cloud_run_and_sync_elsewhere(monkeypatch):
    monkeypatch.delenv("STARTUP_MAINTENANCE_MODE", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert startup_maintenance_mode() == "sync"                  # local dev and tests behave exactly as before
    monkeypatch.setenv("K_SERVICE", "qshala-api")
    assert startup_maintenance_mode() == "background"
    monkeypatch.setenv("STARTUP_MAINTENANCE_MODE", "sync")
    assert startup_maintenance_mode() == "sync"                  # explicit override wins
    monkeypatch.setenv("STARTUP_MAINTENANCE_MODE", "nonsense")
    assert startup_maintenance_mode() == "background"            # unknown value falls back to the default


def test_a_slow_repair_does_not_delay_the_app(monkeypatch):
    monkeypatch.setenv("STARTUP_MAINTENANCE_MODE", "background")
    finished = threading.Event()

    def slow_repairs(*a, **k):
        time.sleep(2.0)
        finished.set()

    with patch("backend.app.database.run_startup_repairs", slow_repairs):
        t0 = time.monotonic()
        with TestClient(app_main.app) as c:
            started_in = time.monotonic() - t0
            assert not finished.is_set(), "the app waited for the repair instead of serving"
            assert started_in < 1.5
            health = c.get("/health").json()
            assert health["startup_maintenance"]["state"] in ("running", "done")
            for th in threading.enumerate():
                if th.name == "startup-maintenance":
                    th.join(timeout=10)
            assert finished.is_set()
            assert app_main.STARTUP_MAINTENANCE["state"] == "done"


def test_a_failing_repair_never_stops_the_app(monkeypatch):
    monkeypatch.setenv("STARTUP_MAINTENANCE_MODE", "background")

    def boom(*a, **k):
        raise RuntimeError("repair exploded")

    with patch("backend.app.database.run_startup_repairs", boom):
        with TestClient(app_main.app) as c:
            for th in threading.enumerate():
                if th.name == "startup-maintenance":
                    th.join(timeout=10)
            assert c.get("/health").status_code in (200, 503)           # still answering
            assert app_main.STARTUP_MAINTENANCE["state"] == "failed"
            assert app_main.STARTUP_MAINTENANCE["error"] == "RuntimeError"


def test_sync_mode_runs_inline_and_starts_no_thread(monkeypatch):
    monkeypatch.setenv("STARTUP_MAINTENANCE_MODE", "sync")
    with TestClient(app_main.app):
        assert not any(t.name == "startup-maintenance" for t in threading.enumerate())
        assert app_main.STARTUP_MAINTENANCE["mode"] == "sync"
        assert app_main.STARTUP_MAINTENANCE["state"] == "done"
