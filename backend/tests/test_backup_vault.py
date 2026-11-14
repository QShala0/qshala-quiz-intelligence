import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _mod():
    spec = importlib.util.spec_from_file_location("backup_vault_mod", ROOT / "backend/scripts/backup_vault.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["backup_vault_mod"] = m
    spec.loader.exec_module(m)
    return m


def test_retention_keeps_newest_30_daily_and_12_monthly():
    m = _mod()
    daily = [f"backups/daily/2026/{(i // 28) + 1:02d}/qshala-2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}-020000.dump" for i in range(40)]
    monthly = [f"backups/monthly/2025/{i:02d}/qshala-2025{i:02d}01-020000.dump" for i in range(1, 13)] + \
              ["backups/monthly/2026/01/qshala-20260101-020000.dump", "backups/monthly/2026/02/qshala-20260201-020000.dump"]
    other = ["media/slides/a.png", "backups/notes.txt"]
    doomed = m.select_deletions(daily + monthly + other, 30, 12)
    assert doomed == sorted(daily)[:10] + sorted(monthly)[:2]
    assert not any(k in doomed for k in other)           # never touches anything that is not a backup file


def test_key_layout_and_password_stays_out_of_the_command_line():
    m = _mod()
    now = datetime(2026, 11, 12, 2, 0, 5, tzinfo=timezone.utc)
    assert m.make_key(now) == "backups/daily/2026/11/qshala-20261112-020005.dump"
    assert m.make_key(now, monthly=True).startswith("backups/monthly/2026/11/")
    env = m.pg_env("postgresql://user:s3cret@db.example.com:5432/qshala?sslmode=require")
    assert env["PGPASSWORD"] == "s3cret" and env["PGHOST"] == "db.example.com" and env["PGSSLMODE"] == "require"


def test_refuses_the_public_media_bucket(monkeypatch, capsys):
    m = _mod()
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setenv("BACKUP_R2_BUCKET", "qshala-media")
    assert m.main(["--dry-run"]) == 2 and "public media bucket" in capsys.readouterr().out


def test_version_mismatch_gives_a_clear_hint(monkeypatch, tmp_path):
    import subprocess
    m = _mod()
    fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=(
        "pg_dump: error: aborting because of server version mismatch\n"
        "pg_dump: detail: server version: 18.7; pg_dump version: 17.12"))
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: fake)
    try:
        m.dump_to(tmp_path / "x.dump", "postgresql://u:p@h/db")
        raise AssertionError("expected a failure")
    except RuntimeError as e:
        assert "server version mismatch" in str(e) and "HINT" in str(e) and "postgresql-client" in str(e)


def test_newest_installed_postgres_tools_are_chosen_for_dump_and_restore(monkeypatch, tmp_path):
    m = _mod()
    for v in ("16", "17", "18"):
        d = tmp_path / "postgresql" / v / "bin"
        d.mkdir(parents=True)
        (d / "pg_dump").write_text("")
        (d / "pg_restore").write_text("")
    (tmp_path / "postgresql" / "notaversion" / "bin").mkdir(parents=True)
    monkeypatch.delenv("PG_BIN_DIR", raising=False)
    monkeypatch.setattr(m.glob, "glob", lambda pattern: [str(p) for p in (tmp_path / "postgresql").glob("*/bin")])
    # Compare with separators normalised so the test holds on Windows dev machines too
    # (the code runs on Linux in CI and Cloud Run, where these paths already use "/").
    norm = lambda p: p.replace("\\", "/")
    assert norm(m.pg_tool("pg_dump")).endswith("/18/bin/pg_dump")
    assert norm(m.pg_tool("pg_restore")).endswith("/18/bin/pg_restore")  # same version for both, always
    monkeypatch.setenv("PG_BIN_DIR", "/opt/pg/bin")
    assert norm(m.pg_tool("pg_restore")) == "/opt/pg/bin/pg_restore"
    monkeypatch.delenv("PG_BIN_DIR")
    monkeypatch.setattr(m.glob, "glob", lambda pattern: [])
    assert m.pg_tool("pg_restore") == "pg_restore"                    # nothing installed under /usr/lib: fall back to PATH


def test_unreadable_dump_gives_a_clear_error(monkeypatch, tmp_path):
    import subprocess
    m = _mod()
    calls = []

    def fake_run(cmd, **k):
        calls.append(cmd[0])
        if "pg_dump" in cmd[0]:
            (tmp_path / "x.dump").write_bytes(b"data")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 1, "", "pg_restore: error: unsupported version (1.16) in file header")

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    try:
        m.dump_to(tmp_path / "x.dump", "postgresql://u:p@h/db")
        raise AssertionError("expected a failure")
    except RuntimeError as e:
        assert "could not be read back" in str(e) and "unsupported version" in str(e) and "HINT" in str(e)
