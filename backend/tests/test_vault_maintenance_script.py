"""The weekly maintenance script runs both whole-vault passes, once, and the workflow is scheduled weekly."""
import importlib.util
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _restore_environment():
    """Loading the script sets STARTUP_MAINTENANCE_MODE / REQUIRE_POSTGRES; keep that out of every other test."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


def _load_script():
    spec = importlib.util.spec_from_file_location("run_vault_maintenance", ROOT / "backend" / "scripts" / "run_vault_maintenance.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_runs_the_repair_pass_and_the_heal_pass_once_each(monkeypatch):
    calls = []
    mod = _load_script()
    stats = {"stepped_healed": 1, "connect4_healed": 2, "reclassified_arrange": 0, "reclassified_visual": 0}
    with patch("backend.app.database.run_startup_repairs", lambda *a, **k: calls.append("repairs")), \
         patch("backend.app.services.ingestion.heal_specialized.heal_specialized_questions",
               lambda db, *a, **k: (calls.append("heal"), stats)[1]):
        assert mod.run() == stats
    assert calls == ["repairs", "heal"]


def test_script_reports_failure_with_a_non_zero_exit_and_no_secrets(monkeypatch, capsys, caplog):
    mod = _load_script()
    secret = "postgresql://user:hunter2@host/db"
    monkeypatch.setenv("DATABASE_URL", secret)

    def boom(*a, **k):
        raise RuntimeError(f"could not connect to {secret}")

    with patch("backend.app.database.run_startup_repairs", boom):
        assert mod.main() == 1
    out = capsys.readouterr()
    assert "hunter2" not in out.out + out.err + caplog.text


def test_script_forces_startup_passes_off_and_requires_postgres_by_default():
    text = (ROOT / "backend" / "scripts" / "run_vault_maintenance.py").read_text(encoding="utf-8")
    assert 'os.environ["STARTUP_MAINTENANCE_MODE"] = "off"' in text
    assert 'os.environ.setdefault("REQUIRE_POSTGRES", "1")' in text


def test_workflow_runs_weekly_and_can_be_started_by_hand():
    wf = (ROOT / ".github" / "workflows" / "vault-maintenance.yml").read_text(encoding="utf-8")
    m = re.search(r'cron:\s*"([^"]+)"', wf)
    assert m, "no cron schedule"
    minute, hour, dom, month, dow = m.group(1).split()
    assert dom == "*" and month == "*" and dow.isdigit(), "must run on one weekday, i.e. weekly"
    assert "workflow_dispatch" in wf
    assert "run_vault_maintenance.py" in wf and "secrets.DATABASE_URL" in wf and 'REQUIRE_POSTGRES: "1"' in wf
