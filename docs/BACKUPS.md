# Backups and the restore drill

What is backed up: the whole Postgres database (questions, tags, decks, slides, quizzes, edit history) as a `pg_dump`,
nightly, to a **private** R2 bucket. 30 daily and 12 monthly copies are kept.

What is NOT in it: the media files (slide images, uploaded decks) that live in the media bucket. Turn on object versioning
for that bucket in Cloudflare (or replicate it) separately.

## One-time setup (about 15 minutes)

1. Cloudflare -> R2 -> **Create bucket**, e.g. `qshala-backups`. Keep **public access OFF**. Never reuse the media bucket:
   it is served publicly, and the script refuses to write to it.
2. R2 -> Manage API tokens -> create a token with **Object Read & Write**, scoped to only that bucket. Copy the access key id and secret.
3. GitHub -> repo Settings -> Secrets and variables -> Actions -> add: `DATABASE_URL` (the Neon connection string),
   `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `BACKUP_R2_BUCKET` (= `qshala-backups`).
4. Actions -> **Nightly vault backup** -> Run workflow, and confirm it finishes green and an object appears under `backups/daily/...`.
5. Watch the workflow: GitHub emails people watching the repo when a scheduled run fails. Make sure a team inbox is among them.

## Postgres version note

`pg_dump` and `pg_restore` must be the same major version as the database server or newer (Neon currently runs 18.x, and the
workflow installs client 18). A dump made by version N can only be restored with version N or newer. If Neon upgrades the
server, change the number in `.github/workflows/backup.yml` and use that version for restore drills.

## Restore drill (do it once now, then after any big schema change)

A backup that has never been restored is only a hope. Restore into a scratch database, never the live one:

```bash
# 1. download one backup (any S3 client works; this uses the AWS CLI against R2)
aws s3 cp s3://qshala-backups/backups/daily/2026/11/<file>.dump ./latest.dump \
  --endpoint-url https://<ACCOUNT_ID>.r2.cloudflarestorage.com
# 2. create an empty scratch database (a free Neon branch works well), e.g. .../qshala_restore_scratch
# 3. restore and compare row counts with the live database
python backend/scripts/restore_check.py latest.dump "<SCRATCH_DATABASE_URL>" "<LIVE_DATABASE_URL>"
```

It prints `MATCH` when every table has the same number of rows. The script refuses any target whose database name does not
contain `scratch` or `restore`.

## Real disaster

Restore the newest good dump into a fresh Neon branch, check it with the command above, then point `DATABASE_URL`
at it. Neon's own point-in-time restore is a second safety net: check your plan's retention window and write it down here.
