# Production-readiness batch (9 items, 5 commits)

Stacked on the prompt-engine hardening branch. The same commits are available as patch files in
`patches/prod-readiness/` (apply with `git -c core.autocrlf=false am --keep-cr patches/prod-readiness/*.patch`; the folder has a
`.gitattributes` so Windows leaves them untouched), but checking out the branch is simpler and is the preferred route.

| # | Item | What changed | Where |
|---|------|--------------|-------|
| 1 | Lists capped at 100 | `GET /questions` sends the real total in `X-Total-Count` (same filters; body is still a plain list) with an id tie-break so pages never overlap. Knowledge Base shows "Showing N of M" and **Load more**. | `routers/questions.py`, `useVaultQuestions.ts`, `knowledge-base/page.tsx` |
| 2 | Failed loads look empty | Shared `LoadErrorBanner` with Retry on the quiz library, quiz page and uploads page (the Knowledge Base already had one). | `components/LoadErrorBanner.tsx` + 3 pages |
| 3 | Quizzes saved in one browser only | The server is the source of truth: edits/deletes show as done only after it confirms; failures are visible and the editor stays open; the browser copy is a labelled fallback; quizzes deleted elsewhere no longer reappear. | `quizzes/page.tsx`, `quizzes/[id]/page.tsx`, `quizStorage.ts`, `QuestionCard.tsx` |
| 4 | One shared upload batch | Each browser sends a random `client_id`; "resume my upload" only finds its own batch (new column `upload_batches.client_id`, alembic `0011`). Old clients without an id behave as before. | `jobs.py`, `UploadContext.tsx`, `lib/clientId.ts` |
| 5 | Backups | Nightly GitHub Actions workflow + step-by-step guide (private bucket, secrets, restore drill). The script itself shipped in the previous batch. | `.github/workflows/backup.yml`, `docs/BACKUPS.md` |
| 6 | Dangerous database default | On Cloud Run (`K_SERVICE` set) `REQUIRE_POSTGRES` now defaults to ON; `ALLOW_EPHEMERAL_DB=1` opts out for a throwaway demo. Local dev and Vercel unchanged. | `config.py` |
| 7 | One request can wipe the vault | Deck deletes and the bulk question delete first copy the affected rows into holding tables (same transaction); `GET /trash` lists them, `POST /trash/{id}/restore` puts them back (never overwrites newer rows); kept 30 days. Tested on SQLite and real Postgres. | `services/trash.py`, `routers/trash.py` |
| 8 | No safe deploy path | `deploy/cloudbuild.yaml`: tests -> build -> push -> deploy -> update worker job, images tagged by commit; `export_cloudrun_settings.sh` to record live settings in git; `docs/DEPLOY.md` with rollback commands. | `deploy/`, `docs/DEPLOY.md` |
| 9 | Unpinned dependencies | `requirements.lock.txt` and `requirements-dev.lock.txt` (Python 3.12); Docker and CI install from them; `backend/DEPENDENCIES.md` explains upgrading. | `Dockerfile`, `ci.yml` |

Verified: 526 backend tests pass on Python 3.12 installed from the lock only (21 skipped, 0 failed); prompt-engine golden output
IDENTICAL (plain and cached paths); frontend `tsc` clean and `npm run build` succeeds; migrations 0010/0011 run, downgrade and
re-upgrade on real Postgres; 16 headless-browser checks on the production build (paging, error banners, outage and recovery,
failed save/delete stay visible, a quiz deleted elsewhere does not reappear, upload lookup scoped to the browser's client id).

## Things only a person can do (the code cannot)

1. **Before deploying item 6:** confirm `DATABASE_URL` is set on the Cloud Run service. Without it the new revision refuses to
   start; Cloud Run keeps serving the previous revision, so nothing breaks, but the deploy will not go through.
2. Create the private backup bucket and add the five GitHub secrets (docs/BACKUPS.md), run the workflow once, do one restore drill.
3. In Cloud Build, point the main-branch trigger at `deploy/cloudbuild.yaml`, grant its service account the roles listed in
   docs/DEPLOY.md, then disable the old source-deploy trigger (and the separate worker-sync trigger). Check the substitution
   defaults at the top of that file.
4. Run `deploy/export_cloudrun_settings.sh`, review for secrets, commit the two `.live.yaml` files. Consider `max-instances=1` until
   in-memory upload progress is made multi-instance safe.
5. Run `python backend/scripts/add_search_indexes.py --apply` (or alembic) on the live database in a quiet hour.

## Prompt for the worker (Antigravity)

```
You are the worker (QSHALA_PLAN.md section 0 rules and report format apply). GB has said "push it" for this task only.

GOAL: land the prepared production-readiness batch. Do not change behaviour beyond what docs/PROD_READINESS_PATCHES.md describes.

SETUP (Windows: do not change line endings; `git status` must be clean after checkout)
0. If a `git am` is in progress, run `git am --abort`. Delete any old local branch named prod-readiness or perf/prompt-engine-hardening.
1. git fetch origin
2. git checkout -B prod-readiness origin/claude/sweet-hawking-mt6nkf
   (Fallback only if that branch is unavailable: apply patches/prod-readiness/*.patch in order with
    `git -c core.autocrlf=false am --keep-cr`; stop and report if any fails. Never hand-edit a patch.)

CHECKS (stop and report on any mismatch; never re-record the golden snapshot to make anything pass)
3. Backend, Python 3.12: pip install -r backend/requirements-dev.lock.txt ; python -m pytest backend/tests -q -p no:cacheprovider
   EXPECTED: about 526 passed, 21 skipped, 0 failed. If exactly one test in test_format_export.py fails, rerun once (known one-off flake).
4. python backend/scripts/prompt_quiz_golden.py . compare backend/eval_data/prompt_quiz_golden.json   EXPECTED: IDENTICAL
5. Frontend: cd frontend ; npm ci ; npx tsc --noEmit ; npm run build   EXPECTED: no type errors, build succeeds.
6. Alembic on a scratch Postgres: alembic upgrade head, then alembic downgrade 0010_search_trigram_indexes, then upgrade head again. EXPECTED: single head 0011_upload_batch_client_id, all three steps succeed.

PUBLISH
7. git push -u origin prod-readiness. `gh` is not installed: do not install it; print the PR link
   https://github.com/QShala0/qshala-quiz-intelligence/pull/new/prod-readiness and paste the table from docs/PROD_READINESS_PATCHES.md plus the
   results of steps 3-6 for GB to use as the PR description. Do NOT merge anything into main.

AFTER GB MERGES (one report per step, change nothing else)
a. Confirm DATABASE_URL is set on the Cloud Run service BEFORE the deploy that contains item 6.
b. Follow docs/BACKUPS.md: create the PRIVATE bucket, add the five secrets, run the "Nightly vault backup" workflow, then run the restore drill and paste the MATCH output.
c. Follow docs/DEPLOY.md: switch the Cloud Build trigger to deploy/cloudbuild.yaml, then disable the old trigger(s). Run deploy/export_cloudrun_settings.sh and show GB the output before committing it.
d. After deploy: open the Knowledge Base and check "Showing N of M" and Load more; open /quizzes and /uploads; confirm GET /api/v1/trash returns {"items": []}.

RULES: never print or store keys or the database URL; do not touch the public media bucket; do not change model names or defaults; do not enable anything that is off by default.
```
