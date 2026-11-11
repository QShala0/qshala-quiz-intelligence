"""Regression tests for Vercel serverless cold-start safety and schema initialization."""
import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, event

from backend.app.database import Base, ensure_schema_columns


def test_ensure_schema_columns_skips_ddl_when_schema_current(tmp_path):
    db_file = tmp_path / "schema_current.db"
    test_engine = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    try:
        assert ensure_schema_columns(force=True, target_engine=test_engine) is True

        executed_sql: list[str] = []

        def _capture_sql(conn, cursor, statement, parameters, context, executemany):
            executed_sql.append(statement.strip())

        event.listen(test_engine, "before_cursor_execute", _capture_sql)
        try:
            # Even with force=True, inspection should see every table/column exists and skip all DDL.
            assert ensure_schema_columns(force=True, target_engine=test_engine) is True
        finally:
            event.remove(test_engine, "before_cursor_execute", _capture_sql)

        ddl_stmts = [
            s for s in executed_sql
            if s.upper().startswith(("ALTER TABLE", "CREATE TABLE", "CREATE INDEX", "CREATE UNIQUE INDEX"))
        ]
        assert ddl_stmts == [], f"Unexpected DDLs on up-to-date schema: {ddl_stmts}"

        # Unforced second call hits the in-process cache and runs zero queries.
        cached_sql: list[str] = []
        event.listen(test_engine, "before_cursor_execute", lambda *a: cached_sql.append(a[2]))
        try:
            assert ensure_schema_columns(force=False, target_engine=test_engine) is True
        finally:
            pass
        assert cached_sql == []
    finally:
        test_engine.dispose()


def test_ensure_schema_columns_adds_only_missing_columns(tmp_path):
    db_file = tmp_path / "schema_legacy.db"
    test_engine = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    try:
        # Simulate a legacy upload_batches table missing last_seen_at before first run
        with test_engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE upload_batches (id VARCHAR(36) PRIMARY KEY);")

        assert ensure_schema_columns(force=True, target_engine=test_engine) is True

        with test_engine.connect() as conn:
            cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(upload_batches);").fetchall()}
        assert "last_seen_at" in cols

        # Subsequent forced check should emit no DDLs
        executed_sql: list[str] = []

        def _capture_sql(conn, cursor, statement, parameters, context, executemany):
            executed_sql.append(statement.strip())

        event.listen(test_engine, "before_cursor_execute", _capture_sql)
        try:
            assert ensure_schema_columns(force=True, target_engine=test_engine) is True
        finally:
            event.remove(test_engine, "before_cursor_execute", _capture_sql)

        assert [s for s in executed_sql if s.upper().startswith(("ALTER TABLE", "CREATE TABLE"))] == []
    finally:
        test_engine.dispose()


def test_vercel_lazy_runtime_init_and_diagnostic_bypass(tmp_path):
    tmp_db = tmp_path / "vercel_lazy.db"
    repo_root = Path(__file__).resolve().parent.parent.parent
    script = f"""
import json, os, shutil
import backend.app.database as db_mod

import_cached = len(db_mod._ensured_schema_keys)
calls = 0
orig_ensure = db_mod.ensure_schema_columns

def wrapped_ensure(*args, **kwargs):
    global calls
    calls += 1
    return orig_ensure(*args, **kwargs)

db_mod.ensure_schema_columns = wrapped_ensure

import backend.main as entry
from fastapi.testclient import TestClient

calls_after_import = calls
ready_after_import = entry._runtime_ready

client = TestClient(entry.app)
r_ping = client.get("/api/ping")
calls_after_ping = calls

r_diag = client.get("/api/diagnostics")
calls_after_diag = calls

r_health = client.get("/api/v1/health")
calls_after_health = calls

r_stats_1 = client.get("/api/v1/stats")
calls_after_stats_1 = calls
ready_after_stats_1 = entry._runtime_ready

r_stats_2 = client.get("/api/v1/stats")
calls_after_stats_2 = calls

print(json.dumps({{
    "import_cached": import_cached,
    "calls_after_import": calls_after_import,
    "ready_after_import": ready_after_import,
    "ping_status": r_ping.status_code,
    "ping_json": r_ping.json(),
    "calls_after_ping": calls_after_ping,
    "diag_status": r_diag.status_code,
    "diag_keys": sorted(r_diag.json().keys()),
    "diag_schema_ready": r_diag.json().get("schema_ready"),
    "calls_after_diag": calls_after_diag,
    "health_status": r_health.status_code,
    "calls_after_health": calls_after_health,
    "stats_1_status": r_stats_1.status_code,
    "calls_after_stats_1": calls_after_stats_1,
    "ready_after_stats_1": ready_after_stats_1,
    "stats_2_status": r_stats_2.status_code,
    "calls_after_stats_2": calls_after_stats_2,
}}))
"""
    env = os.environ.copy()
    env.update({
        "VERCEL": "1",
        "DATABASE_URL": f"sqlite:///{tmp_db.as_posix()}",
        "REQUIRE_POSTGRES": "0",
        "PYTHONPATH": str(repo_root),
    })
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"Subprocess failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    data = json.loads(proc.stdout.strip().splitlines()[-1])

    assert data["import_cached"] == 0
    assert data["calls_after_import"] == 0
    assert data["ready_after_import"] is False
    assert data["ping_status"] == 200
    assert data["ping_json"]["schema_ready"] is False
    assert data["calls_after_ping"] == 0
    assert data["diag_status"] == 200
    assert {"db_backend", "db_persistent", "db_host", "schema_ready"}.issubset(set(data["diag_keys"]))
    assert data["diag_schema_ready"] is False
    assert data["calls_after_diag"] == 0
    assert data["health_status"] in (200, 503)
    assert data["calls_after_health"] == 0
    assert data["stats_1_status"] == 200
    assert data["calls_after_stats_1"] == 1
    assert data["ready_after_stats_1"] is True
    assert data["stats_2_status"] == 200
    assert data["calls_after_stats_2"] == 1


def test_vercel_config_skips_bundle_copies_when_postgres_and_r2_configured():
    repo_root = Path(__file__).resolve().parent.parent.parent
    script = """
import json, shutil

copied = []
orig_copy2 = shutil.copy2

def tracking_copy2(src, dst, *args, **kwargs):
    copied.append((str(src), str(dst)))
    return orig_copy2(src, dst, *args, **kwargs)

shutil.copy2 = tracking_copy2
import backend.app.config as cfg
print(json.dumps({"copied": copied, "is_vercel": cfg.is_vercel}))
"""
    env = os.environ.copy()
    env.update({
        "VERCEL": "1",
        "DATABASE_URL": "postgresql://user:pass@localhost:5432/qshala",
        "R2_BUCKET_NAME": "qshala-media",
        "R2_PUBLIC_BASE_URL": "https://pub.example.com",
        "PYTHONPATH": str(repo_root),
    })
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"Subprocess failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["is_vercel"] is True
    assert data["copied"] == []


def test_vercel_postgres_pool_and_timeout_config():
    repo_root = Path(__file__).resolve().parent.parent.parent
    script = """
import json
import sqlalchemy
from sqlalchemy.pool import NullPool

captured = []
orig_create_engine = sqlalchemy.create_engine

def fake_create_engine(url, **kwargs):
    captured.append({
        "url": url,
        "poolclass": kwargs.get("poolclass").__name__ if kwargs.get("poolclass") else None,
        "connect_args": kwargs.get("connect_args"),
        "pool_keys": sorted([k for k in kwargs if k.startswith("pool_") or k == "max_overflow"]),
    })
    return orig_create_engine("sqlite:///:memory:")

sqlalchemy.create_engine = fake_create_engine
import backend.app.database as db_mod
print(json.dumps({
    "captured": captured,
    "db_backend": db_mod.DB_BACKEND,
    "db_persistent": db_mod.DB_PERSISTENT,
    "ensured_keys": len(db_mod._ensured_schema_keys),
}))
"""
    env = os.environ.copy()
    env.update({
        "VERCEL": "1",
        "DATABASE_URL": "postgresql://user:pass@ep-demo-pooler.ap-southeast-1.aws.neon.tech/neondb",
        "REQUIRE_POSTGRES": "1",
        "PYTHONPATH": str(repo_root),
    })
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"Subprocess failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert len(data["captured"]) == 1
    call = data["captured"][0]
    assert call["poolclass"] == "NullPool"
    assert call["connect_args"] == {"connect_timeout": 10}
    assert call["pool_keys"] == []
    assert data["db_backend"] == "postgres"
    assert data["db_persistent"] is True
    assert data["ensured_keys"] == 0
