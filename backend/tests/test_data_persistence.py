"""Guards against the silent-SQLite data-loss trap.

The /health endpoint must report which database is live and whether it persists, and REQUIRE_POSTGRES
must make a missing/unreachable Postgres fail loudly at startup instead of falling back to SQLite.
"""
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.main import app

client = TestClient(app)


def test_health_reports_database_backend_and_persistence():
    resp = client.get("/api/v1/health")
    r = resp.json()
    # status/status-code agree, and reflect a live check -- not a hardcoded "healthy"
    assert r["status"] in ("healthy", "unhealthy")
    assert resp.status_code == (200 if r["status"] == "healthy" else 503)
    assert r["database"] in ("postgres", "sqlite")
    assert isinstance(r["persistent"], bool)
    # on sqlite the endpoint must warn; on postgres it must not
    assert (r["warning"] is None) == (r["database"] == "postgres")
    # live checks are actually reported, not assumed
    assert set(r["checks"].keys()) == {"database_reachable", "gemini_key_configured", "r2_configured", "sharepoint_configured"}
    assert isinstance(r["checks"]["database_reachable"], bool)
    assert isinstance(r["checks"]["gemini_key_configured"], bool)
    # sqlite (not persistent) or an unreachable DB must surface as a problem, not get silently
    # swallowed into a "healthy" response
    if not r["persistent"] or not r["checks"]["database_reachable"]:
        assert r["problems"]


def test_require_postgres_refuses_to_boot_on_sqlite():
    """With REQUIRE_POSTGRES=1 and no DATABASE_URL, importing the DB layer must raise rather than
    silently use an ephemeral SQLite file."""
    repo_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, REQUIRE_POSTGRES="1", DATABASE_URL="")
    proc = subprocess.run(
        [sys.executable, "-c", "import backend.app.database"],
        cwd=str(repo_root),
        env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode != 0
    assert "REQUIRE_POSTGRES" in (proc.stderr + proc.stdout)
