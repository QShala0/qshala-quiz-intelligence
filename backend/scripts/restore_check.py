"""Restore drill: load a backup into a SCRATCH database and check it against the source.

    python backend/scripts/restore_check.py BACKUP.dump postgresql://.../qshala_restore_scratch [SOURCE_DATABASE_URL]

Safety: the target database name must contain "scratch" or "restore", so this can never overwrite the live database.
With a source URL it compares per-table row counts and exits 1 on any difference. A backup that has not been
restored at least once is not a backup; run this after setting up backups and after any schema change.
"""
import subprocess
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])


def counts(database_url: str) -> dict:
    from sqlalchemy import create_engine, inspect, text
    eng = create_engine(database_url)
    out = {}
    with eng.connect() as c:
        for t in sorted(inspect(eng).get_table_names()):
            out[t] = c.execute(text(f'select count(*) from "{t}"')).scalar()
    eng.dispose()
    return out


def main(argv) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    dump, target = argv[1], argv[2]
    source = argv[3] if len(argv) > 3 else None
    from sqlalchemy.engine import make_url
    name = (make_url(target).database or "").lower()
    if "scratch" not in name and "restore" not in name:
        print(f"refusing: target database {name!r} must contain 'scratch' or 'restore'")
        return 2
    from backup_vault import pg_env, pg_tool
    r = subprocess.run([pg_tool("pg_restore"), "--no-owner", "--clean", "--if-exists", "--dbname", name, dump],
                       env=pg_env(target), capture_output=True, text=True)
    if r.returncode not in (0, 1):                       # 1 = warnings such as 'does not exist' on --clean
        print(r.stderr[-800:])
        return 1
    restored = counts(target)
    print(f"restored {len(restored)} tables, {sum(restored.values())} rows")
    if source:
        live = counts(source)
        bad = {t: (live[t], restored.get(t)) for t in live if live[t] != restored.get(t)}
        for t, (a, b) in bad.items():
            print(f"MISMATCH {t}: source {a}, restored {b}")
        print("MATCH" if not bad else "DIFFERENT")
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
