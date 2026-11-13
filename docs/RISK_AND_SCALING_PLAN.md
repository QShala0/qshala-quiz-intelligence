# QShala — Risk Register and Scaling Plan (Brain → Worker)

Audience: the worker (Antigravity). Same rules as `QSHALA_PLAN.md` §0 apply: no push/PR/deploy without GB writing "push it" for that phase, never print or store keys, every change ships with a test or script that proves it, stop and report when a number looks too good or an assumption is false, report at every gate in the §0.2 format.

Status: written from a code read plus one synthetic benchmark. Timings below are **estimates on synthetic data (SQLite, no network)**, not production measurements. Phase 0 exists to replace them with real numbers before anything is built.

## 1. What this is

QShala is an internal tool, so the risks are not "heavy traffic". They are (a) **growth** of the vault (13 years of decks, still ingesting), (b) **time** (answers go stale, model names get retired, data outlives its schema), and (c) **single points of failure** that are fine at today's size. The register is ordered by `likelihood x damage`, soonest first.

## 2. Risk register (priority order)

| # | Pri | Risk | Evidence in code | When it bites (estimate) | Damage |
|---|-----|------|------------------|--------------------------|--------|
| R1 | P0 | **No backup or restore path for the vault.** One request can hard-delete everything; SQLite fallback is silent in prod. | `DELETE /questions?all=true` hard-deletes (`routers/questions.py` `delete_all_questions_endpoint`); no backup/export code anywhere; `database.py:156-172` falls back to SQLite with a warning only | Any bad day (mis-click, bad deploy, Neon incident). Not growth-dependent | Catastrophic: 13 years of work |
| R2 | P0 | **Whole bank loaded into memory per instance and rebuilt every 10 s.** | `prompt_quiz/engine.py` `_load_bank_uncached` loads every row; `_latest_tags` runs a window function over all tag rows; the first request after TTL expiry pays for it; the index is rebuilt then too | ~20-50k questions: warm draft 2.3 s at 20k after the recent speed-ups, plus a multi-second spike every 10 s; memory shares a 2 GB container with ingestion | Slow or timed-out drafts; OOM restarts |
| R3 | P0 | **Hard dependency on Gemini with no guard rails.** Model names are hard-coded, no spend cap, no breaker, silent fallback to regex. | model ids in `interpreter_llm.py`, `curator_llm.py` (different default from interpreter), `intent_engine.py`, `qshala_tagger.py`, `gemini_provider.py`; `EMBEDDING_DIM=768` fixed in the column type (`models/question.py:84`) | Whenever a model id is retired or renamed, a quota is hit, or a bug loops calls | Quietly worse quizzes, or a surprise bill; changing embedding model needs full re-embed |
| R4 | P1 | **Content rot.** Time-sensitive answers go stale ("current PM of...") and nothing prompts re-verification. | `shelf_life` / `temporal_nature` exist as tags only; no `verified_at` or review queue | Steadily, from now on | Wrong answer read out at a live event |
| R5 | P1 | **Multi-instance correctness is assumed away.** | `INGESTION_STATUS_REGISTRY` and the bank/taxonomy caches are per-process; upload-queue docs say "one process per container"; no max-instances config in the repo | The day Cloud Run scales past one instance (default allows many) | Upload progress that disappears or flaps; duplicate work |
| R6 | P1 | **Search and tag aggregation scan the table.** | `cast(tags AS String) ILIKE '%x%'` and `ILIKE '%topic%'` have no trigram/GIN index (`routers/questions.py`); `/questions/tags` aggregates in Python (2.9 s at 20k, measured) | ~50-100k rows | Slow knowledge-base screens |
| R7 | P1 | **Two migration systems plus startup mutation.** | 16 `ALTER TABLE` in `database.py` `ensure_schema_columns` alongside alembic (7 versions); `Base.metadata.create_all` at import; legacy backfill runs the tagger over unhashed rows **at boot** | Every cold start grows slower; two instances racing at boot | Slow or failing startups; schema drift |
| R8 | P1 | **Ingestion memory ceiling.** | Dockerfile sets `UPLOAD_CONCURRENCY=32`, `VISION_CONCURRENCY=12`, `TAG_CONCURRENCY=8`, `BIG_DECODE_CONCURRENCY=6` against `SERVER_MEMORY_LIMIT_MB=2048`; startup marks interrupted decks FAILED | As decks get image-heavier | Restart loops, lost uploads |
| R9 | P1 | **CI cannot protect quality.** | `main` carries a failing test (`test_interpret_difficulty_bias`); lint is `continue-on-error`; no prompt-quality or latency regression check | Every future change | Regressions ship unnoticed |
| R10 | P2 | **Unbounded table growth.** | No retention code for superseded `qshala_tags`, `question_edit_history`, usage rows, ingestion jobs, upload sessions | Months | Slower bank load, bigger bills |
| R11 | P2 | **Taxonomy lives in four places.** | `V6_THEMES` in `interpreter_llm.py`, taxonomy file, live DB taxonomy in `intent_engine.py`, city/synonym lists in `retrieval.py` and `engine.py` | Every taxonomy edit | Silent retrieval misses |
| R12 | P2 | **Little observability.** | Sentry optional, plain logs, no stage timings or metrics | When something is slow and nobody can say why | Long debugging |
| R13 | P2 | **No per-person identity.** One shared API key; `NEXT_PUBLIC_API_KEY` still works as a fallback and would ship the key to browsers | `proxy.ts`, `lib/api.ts`, `.env.example` | When the team grows or the tool is shared | No audit trail; key leakage |
| R14 | P3 | **SQLite dedup fallback is silently partial.** | `duplicate_detector.py` fallback `.limit(5000)` | Dev/SQLite only | Missed duplicates in non-prod |

## 3. Order of work

`Phase 0` measure -> `A` safety net -> `B` bank scalability -> `C` Gemini resilience -> `D` multi-instance and ingestion -> `E` search at scale -> `F` data lifecycle -> `G` content freshness -> `H` observability. A may overlap with 0. B-F depend on A's golden harness and green CI. G is a feature and goes last.

Rule for B and E: **a speed change must not change output.** Prove it with `backend/scripts/prompt_quiz_golden.py` (record on current `main`, compare after) and, for aggregations, an equivalence test against the old implementation. A change that alters behaviour is reported as a decision for GB, never shipped silently.

---

## Phase 0 — Measure the real thing (no behaviour changes)

Goal: replace estimates with production numbers.

- [ ] Row counts for `questions`, `qshala_tags` (all vs current), `question_edit_history`, usage tables, `ingestion_jobs`, `upload_sessions`; table and index sizes. Save to `docs/baselines/vault_size.md`.
- [ ] Time `GET /questions/tags`, `GET /questions/tag-preview`, `POST /prompt-quiz/draft` (3 prompts) against the real Neon database, cold and warm. Save to `docs/baselines/latency_prod.md`.
- [ ] Record peak memory of one Cloud Run instance during a draft and during a large upload.
- [ ] Record Cloud Run settings actually in force: max/min instances, memory, CPU, concurrency, request timeout. Put them in `docs/dev_setup.md`.
- [ ] Record Neon plan limits (storage, compute, autosuspend, point-in-time-restore window).
- [ ] Record Gemini spend for the last 30 days by feature (the usage model exists).

**GATE 0:** report the numbers. The brain re-ranks R1-R14 with them. Do not start A-H before this.

## Phase A — Safety net (R1, R9, R14)

- [ ] **Backups.** Nightly logical dump of the database to a private R2 prefix (separate bucket or prefix from public media), 30 daily + 12 monthly retention, plus confirmation that R2 media is not the only copy. Script in `backend/scripts/backup_vault.py`, scheduled by Cloud Scheduler or the existing job runner.
- [ ] **Restore drill.** `docs/restore.md` plus a script that restores the latest dump into a scratch database and checks row counts against the source. Run it once and attach the output. A backup that has not been restored is not a backup.
- [ ] **Production must have a real database.** When `K_SERVICE` is set and the database is SQLite, fail startup with a clear error (an `ALLOW_EPHEMERAL_DB=1` override for demos).
- [ ] **No one-request wipes.** `DELETE /questions?all=true` and `DELETE /documents/{id}` become soft deletes (a `deleted_at` column, excluded from the bank load and from search), with a purge endpoint that requires a second confirmation token and is separate from normal use. Tests: soft-deleted rows disappear from search, the bank and exports, and restoring brings them back.
- [ ] **Green `main`.** Fix or correctly quarantine-with-a-reason the failing test (never skip it to get green; if it encodes a wrong expectation, fix the expectation and say why). Make ruff blocking for new files.
- [ ] **Add the golden harness to CI** as a non-blocking job first, blocking after one clean week.
- [ ] `duplicate_detector` SQLite fallback: log a warning when the 5,000-row cap truncates.

**GATE A:** restore drill output, soft-delete tests, CI green on `main`.

## Phase B — Bank scalability (R2)

Do these in order; each must pass golden-compare as IDENTICAL.

- [ ] **B1. Load only what the bank uses.** `_load_bank_uncached` reads whole `Question` rows but the bank dict never uses `explanation`, `options`, `audio_transcript`, `video_transcript`, `raw_media_refs`. Defer exactly the columns the dict does not read (check each against the dict, and against every `getattr`). An earlier attempt deferred a column that does not exist (`speaker_notes`) and broke ~30 tests; verify every column name against the model. Existing guard test: `test_prompt_quiz_bank.py::test_bank_query_does_not_fetch_the_embedding_column`; extend it to the new columns.
- [ ] **B2. Stale-while-revalidate.** When the bank cache expires, serve the old bank and refresh in a background thread (single-flight lock) instead of making one request pay. Keep `invalidate_bank_cache()` semantics (tests use it with TTL 0). Tests: concurrent requests trigger one reload; a failed reload keeps serving the old bank and logs.
- [ ] **B3. Cheaper tag lookup.** Replace the window function over all tag rows with a maintained "current tag" pointer or the existing `ix_qshala_tags_current` index (`question_id, status, created_at desc`); show `EXPLAIN ANALYZE` before and after on real data.
- [ ] **B4. (Behaviour-changing, needs GB's decision.)** Retrieve candidates in the database (pgvector top-k plus lexical top-k, fused) instead of scoring the whole bank in Python, and cap the search pool at about 300. This changes fulfillment counts and possibly picks. Ship behind `PROMPT_QUIZ_RETRIEVAL=db` with a side-by-side report of 50 prompts; the default stays off until GB approves.
- [ ] Add `timings_ms` per stage to the draft response (feeds Phase H).

**GATE B:** golden IDENTICAL for B1-B3; benchmark table (`BENCH=1 BANK_N=20000 ...`) and the same on a real-size copy; memory per instance before and after.

## Phase C — Gemini resilience (R3)

- [ ] **One place for model ids.** A single settings block (`GEMINI_MODEL_TAGGER`, `_INTERPRETER`, `_CURATOR`, `_EMBEDDING`); remove the per-file hard-coded fallbacks. Explicitly decide the curator default (it currently differs from the interpreter's).
- [ ] **Startup and daily self-test.** Call each configured model once with a tiny request; surface failures in `/health` details and in the logs. Retired models then show up in minutes, not as quietly worse quizzes.
- [ ] **Circuit breaker** shared by interpreter, intent engine and curator: after N consecutive failures skip LLM calls for 60 s and go straight to the deterministic path. Also stop the double call (intent engine fails, interpreter makes the same failing call).
- [ ] **Brief cache.** Key: normalised prompt + previous-brief hash + count + options; TTL 10 min; temperature is already 0 so output is the same. Report hit rate.
- [ ] **Spend guard.** Daily Gemini budget in settings; at 80% log a warning and show it on the usage screen; at 100% degrade to deterministic mode and say so in the draft message. Never fail the request.
- [ ] **Embedding migration tool.** `embedding_model` is already stored per row. Write `backend/scripts/reembed.py` (resumable, rate-limited, dry-run) so a model change is a planned job, not an emergency; document the pgvector reindex step.

**GATE C:** breaker and budget tests (simulate failures and quota), self-test output, cache hit-rate on a replay of recent prompts, and the rupee figure per 1,000 drafts before and after.

## Phase D — Multi-instance and ingestion (R5, R7, R8)

- [ ] **Decide and enforce instance count.** Either set Cloud Run `max-instances=1` explicitly in the deploy config (committed to the repo), or make the pieces below instance-safe. Prefer both for now.
- [ ] **DB-backed ingestion status.** `ingestion_jobs` already stores `status`, `current_step`, `step_data` and `updated_at` (check `routers/jobs.py` for how staleness is judged); make the progress endpoint read from there rather than `INGESTION_STATUS_REGISTRY`, so any instance can answer.
- [ ] **One migration path.** Convert the `ALTER TABLE` list in `ensure_schema_columns` into alembic revisions (idempotent, `IF NOT EXISTS`), run migrations as a release step, and stop `create_all` at import in production.
- [ ] **Backfills are jobs, not boot code.** Move the legacy hash/tag backfill out of startup into `backend/scripts/` or the job runner; startup only checks that it is done.
- [ ] **Adaptive ingestion concurrency.** Derive upload/vision/tag/decode concurrency from the measured memory limit, with the Dockerfile values as ceilings, not defaults. Load test with the largest real deck and attach peak memory. Prefer `INGESTION_MODE=cloud_run_job` for big decks.

**GATE D:** progress works across two instances (simulate by running two workers), migration from an empty and from a current database both land at the same schema, largest-deck test passes under the memory limit.

## Phase E — Search and aggregation at scale (R6)

- [ ] `pg_trgm` GIN indexes for `topic`, `subtopic`, and question text used by `ILIKE '%...%'` filters; for tags, either store as `JSONB` with a GIN index or add a `question_tags` table. Show `EXPLAIN ANALYZE` before and after.
- [ ] **Tag index in SQL** (`/questions/tags`, `/questions/tag-preview`). Keep the response identical: write an equivalence test that runs old (Python) and new (SQL) implementations over the same seeded data and compares output exactly, including tag normalisation (`#`, case, whitespace) and substring matching. A previous SQL rewrite changed match semantics and was reverted; do not repeat that.
- [ ] Keyset pagination for `/questions` with the existing `X-Total-Count` idea only if the count honours every filter (an earlier version ignored the format filter; the frontend expects a plain list).

**GATE E:** equivalence tests pass, p95 numbers at real vault size, no frontend change needed.

## Phase F — Data lifecycle (R10, R11)

- [ ] Retention: superseded `qshala_tags` older than 90 days archived (not deleted without a backup), prune completed `upload_sessions` and `ingestion_jobs` after 30 days, cap `question_edit_history` with archive. Dry-run mode first; report counts.
- [ ] **One taxonomy source.** `V6_THEMES`, city aliases and synonym lists load from the taxonomy file (or the DB table), with a test that fails if any two copies disagree.

**GATE F:** dry-run reports, taxonomy consistency test.

## Phase G — Content freshness (R4)

- [ ] Add `verified_at` and `stale_after` to questions (default: time-sensitive = 180 days, evergreen = none).
- [ ] The prompt engine excludes stale time-sensitive questions by default, with a "include stale" option; the draft shows a count of excluded ones.
- [ ] A review screen for quiz masters: stale list, one-click "still correct" (sets `verified_at`) or "edit answer".

**GATE G:** golden unchanged when no question is stale; new tests for exclusion and the review flow.

## Phase H — Observability (R12, R13)

- [ ] Log stage timings and LLM outcomes (success, timeout, fallback) per draft; expose a small admin page: p50/p95 draft time, Gemini calls and spend per day, fallback rate, queue depth, bank size.
- [ ] Remove the `NEXT_PUBLIC_API_KEY` fallback in production builds (server-side proxy only); record who edited or deleted what once there is a per-person identity.

**GATE H:** screenshot or JSON of the admin view with real data.

---

## 4. Definition of done for the whole plan

Backup restored successfully at least once; draft latency flat as the vault doubles (shown by the benchmark at 2x size); a Gemini outage or retired model produces a visible warning and a deterministic fallback within a minute; no single request can destroy the vault; `main` is green; no change in picked questions except where GB approved it.

## 5. Not in scope

Multi-tenant auth, public access, a rewrite of the ingestion parser, replacing Gemini with another provider.
