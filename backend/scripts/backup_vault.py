"""Nightly logical backup of the vault (pg_dump custom format) to a PRIVATE R2 bucket, with retention.

    python backend/scripts/backup_vault.py --dry-run          # show what would happen, change nothing
    python backend/scripts/backup_vault.py                    # dump + verify + upload + prune old backups
    python backend/scripts/backup_vault.py --out-dir /backups # local copy only (no R2), for a drill or a laptop

Environment: DATABASE_URL (Postgres), BACKUP_R2_BUCKET (a PRIVATE bucket), and the usual R2_ACCOUNT_ID /
R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY. The media bucket (R2_BUCKET_NAME) is public (served from r2.dev), so this
script refuses to write to it. Retention: 30 daily + 12 monthly (BACKUP_KEEP_DAILY / BACKUP_KEEP_MONTHLY).
Never prints the database URL or any key. This does not back up the media files themselves; enable R2 object
versioning or replicate that bucket separately.
"""
import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Set

PREFIX = "backups/"


def make_key(now: datetime, monthly: bool = False) -> str:
    kind = "monthly" if monthly else "daily"
    return f"{PREFIX}{kind}/{now:%Y/%m}/qshala-{now:%Y%m%d-%H%M%S}.dump"


def select_deletions(keys: Iterable[str], keep_daily: int, keep_monthly: int) -> List[str]:
    """Keys to delete so only the newest `keep_daily` daily and `keep_monthly` monthly backups remain (names sort by time)."""
    keys = sorted(keys)
    daily = [k for k in keys if k.startswith(PREFIX + "daily/")]
    monthly = [k for k in keys if k.startswith(PREFIX + "monthly/")]
    doomed = daily[:-keep_daily] if keep_daily > 0 else daily
    doomed += monthly[:-keep_monthly] if keep_monthly > 0 else monthly
    return doomed


def pg_env(database_url: str) -> dict:
    """Connection settings as PG* variables so the password never appears on a command line."""
    from sqlalchemy.engine import make_url
    u = make_url(database_url)
    env = dict(os.environ)
    for k, v in (("PGHOST", u.host or u.query.get("host")), ("PGPORT", u.port or u.query.get("port")), ("PGUSER", u.username),
                 ("PGPASSWORD", u.password), ("PGDATABASE", u.database)):
        if v:
            env[k] = str(v)
    if u.query.get("sslmode"):
        env["PGSSLMODE"] = u.query["sslmode"]
    return env


def dump_to(path: Path, database_url: str) -> str:
    r = subprocess.run(["pg_dump", "--format=custom", "--no-owner", "--file", str(path)], env=pg_env(database_url),
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("pg_dump failed: " + r.stderr.strip()[-400:])      # stderr never contains the password
    listing = subprocess.run(["pg_restore", "--list", str(path)], check=True, capture_output=True, text=True).stdout
    if "TABLE DATA" not in listing:
        raise RuntimeError("dump has no table data; refusing to keep it")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _s3():
    import boto3
    acct = os.environ["R2_ACCOUNT_ID"]
    return boto3.client("s3", endpoint_url=f"https://{acct}.r2.cloudflarestorage.com",
                        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], region_name="auto")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-dir", help="write the dump here instead of uploading to R2")
    a = ap.parse_args(argv)
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url.startswith(("postgres://", "postgresql")):
        print("DATABASE_URL must be a Postgres URL")
        return 2
    keep_d, keep_m = int(os.getenv("BACKUP_KEEP_DAILY", "30")), int(os.getenv("BACKUP_KEEP_MONTHLY", "12"))
    now = datetime.now(timezone.utc)
    monthly = now.day == 1
    bucket = os.getenv("BACKUP_R2_BUCKET", "")
    if not a.out_dir:
        if not bucket:
            print("set BACKUP_R2_BUCKET to a private bucket (or use --out-dir)")
            return 2
        if bucket == os.getenv("R2_BUCKET_NAME") or bucket == "qshala-media":
            print("refusing: that is the public media bucket; backups need a private one")
            return 2
    key = make_key(now)
    print(f"plan: pg_dump -> {'local ' + a.out_dir if a.out_dir else 'r2://' + bucket}/{key}"
          + (f" (+ monthly copy)" if monthly else "") + f"; keep {keep_d} daily / {keep_m} monthly")
    if a.dry_run:
        return 0
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "vault.dump"
        digest = dump_to(tmp, db_url)
        size = tmp.stat().st_size
        if a.out_dir:
            dest = Path(a.out_dir) / key
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(tmp.read_bytes())
            print(f"wrote {dest} ({size} bytes, sha256 {digest[:12]}...)")
            return 0
        s3 = _s3()
        for k in [key] + ([make_key(now, monthly=True)] if monthly else []):
            s3.upload_file(str(tmp), bucket, k, ExtraArgs={"Metadata": {"sha256": digest}})
            print(f"uploaded {k} ({size} bytes, sha256 {digest[:12]}...)")
        existing: Set[str] = set()
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=PREFIX):
            existing.update(o["Key"] for o in page.get("Contents", []))
        for k in select_deletions(existing, keep_d, keep_m):
            s3.delete_object(Bucket=bucket, Key=k)
            print("pruned", k)
    return 0


if __name__ == "__main__":
    sys.exit(main())
