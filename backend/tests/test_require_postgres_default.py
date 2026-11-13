"""Production (Cloud Run) must not silently run on a disk that gets wiped."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _require_postgres(extra_env):
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "REQUIRE_POSTGRES", "K_SERVICE", "VERCEL",
                                                            "VERCEL_ENV", "ALLOW_EPHEMERAL_DB")}
    env.update(extra_env)
    r = subprocess.run([sys.executable, "-c", "from backend.app.config import settings; print(settings.REQUIRE_POSTGRES)"],
                       capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)
    assert r.returncode == 0, r.stderr[-400:]
    return r.stdout.strip().splitlines()[-1] == "True"


def test_local_development_is_unchanged():
    assert _require_postgres({}) is False


def test_cloud_run_requires_postgres_by_default():
    assert _require_postgres({"K_SERVICE": "qshala-api"}) is True
    assert _require_postgres({"K_SERVICE": "qshala-api", "REQUIRE_POSTGRES": ""}) is True


def test_cloud_run_demo_can_opt_out_explicitly():
    assert _require_postgres({"K_SERVICE": "qshala-api", "ALLOW_EPHEMERAL_DB": "1"}) is False
    assert _require_postgres({"K_SERVICE": "qshala-api", "REQUIRE_POSTGRES": "0"}) is False


def test_vercel_without_a_database_is_still_allowed():
    assert _require_postgres({"VERCEL": "1"}) is False


def test_cloud_run_without_database_url_fails_loudly_at_import():
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "REQUIRE_POSTGRES", "ALLOW_EPHEMERAL_DB")}
    env.update({"K_SERVICE": "qshala-api"})
    r = subprocess.run([sys.executable, "-c", "import backend.app.database"], capture_output=True, text=True, env=env,
                       cwd=ROOT, timeout=120)
    assert r.returncode != 0 and "REQUIRE_POSTGRES" in r.stderr
