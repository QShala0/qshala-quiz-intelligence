# Cost tracker: brief for Antigravity (build a cost ledger, history and dashboard)

Audience: Antigravity (worker). Written 24 Nov 2026 by the "brain". Read `CLAUDE.md` first, then `docs/COST_ESTIMATE.md` and `docs/COST_AUDIT_2026-11.md` (your own audit). This brief **replaces** the "AI cost" section and the `GET /stats/cost` endpoint of the earlier dashboard brief: do not build both. If that dashboard work is already merged, change its cost section to read from the ledger below.

## 0. Rules (hard)

- New branch `feat/cost-ledger`, open a PR into `main`, never push to `main`. Merging to `main` runs the gated Cloud Build (full `pytest backend/tests`), so every test must pass, and the backend needs that deploy to go live. Say so in the PR.
- Production database is `DATABASE_URL` in `.env`. Never write to it from scripts or experiments. Tests use the temp SQLite from `backend/tests/conftest.py`. Any read-only script sets `REQUIRE_POSTGRES=1` and asserts the postgres dialect. If you need to try a write or the migration against Postgres, ask the owner for a Neon branch URL.
- Never print or commit secrets. Do not add auth (postponed on purpose); say in the PR that cost figures are visible to anyone who can open the site.
- Cost bookkeeping must **never** break ingestion or quizzes: every ledger write is wrapped so an error is logged and swallowed, and runs in its own short session, never inside the pipeline's transaction.
- Follow the repo's migration pattern (alembic head is `0012_sharepoint_import_selection`; also see `ensure_schema_columns` and `create_all` in `database.py`). Frontend rules: `frontend/AGENTS.md`, the design rules in section 8.
- Think like a senior engineer: measure first (statement counts, payload size), change one thing at a time, label every number VERIFIED or ESTIMATED, report anything that contradicts this brief.

## 1. The goal in one paragraph

An **append-only cost ledger**. Every time something costs money (a Gemini call, an ingestion run, a quiz prompt, R2 writes, a month of hosting) one row is added with who/what it belongs to (deck, quiz, operation), how much was used, which prices were in force, and the cost in USD and INR. Nothing is overwritten, so history only grows ("incrementally add"). The dashboard and a `/costs` page are just reads of that table. Real invoices can be typed in per month so the tracker can be checked against what Google actually billed.

## 2. What is tracked precisely and what is not (be honest in the UI)

| Cost | How it is captured | Accuracy |
|---|---|---|
| Gemini: ingestion vision, tagging, embeddings | exact tokens from `usageMetadata`, per deck | exact tokens; price from the rate card |
| Gemini: quiz intent, curator, interpreter, other `generate_*` | exact tokens per call, per quiz prompt | exact (today these are NOT logged at all) |
| Cloud Run job (ingestion) | execution seconds x vCPU x GiB x Tier 2 rate, per deck | good (estimated rates) |
| R2 writes | object count x Class A price, per deck | good |
| R2 storage | bytes per deck x $/GB-month, accrued monthly | good (estimated) |
| Cloud Run service, Neon, Vercel, Sentry, other GCP | **shared, not per deck**: monthly accrual from the rate card, replaced by the real invoice when the owner enters it | estimated until an invoice is entered |
| Gemini calls that timed out (curator times out at 4 s but Google still bills it) | cannot read usage, so record an **estimated** row from the rolling average for that operation, flagged | estimated |

Each row carries `source`: `measured` (from the provider's own numbers), `estimated`, `billed` (copied from an invoice), `manual`. Report-only: a "fully loaded cost per deck" view may allocate shared costs by share of questions, shown separately and labelled as an allocation.

## 3. Data model (new tables, no foreign keys to documents or quizzes so history survives deletes, like `ingestion_usage`)

`cost_events` (append-only):
- `id` uuid, `occurred_at` timestamptz (indexed), `category` (`gemini`|`cloud_run_job`|`cloud_run_service`|`neon`|`r2`|`vercel`|`other`), `operation` (`ingest_vision`|`ingest_tagging`|`ingest_embedding`|`quiz_intent`|`quiz_curator`|`quiz_interpreter`|`search_embedding`|`generate_other`|`job_compute`|`r2_put`|`r2_storage`|`fixed_monthly`|`invoice`),
- `document_id` (nullable, indexed), `quiz_id` (nullable), `job_id`, `batch_id`, `model`,
- `units` JSON (tokens_in, tokens_out, thinking_tokens, cached_tokens, calls, duration_s, vcpu, gib, bytes, objects, gb_months),
- `rates` JSON (the unit prices used), `rate_card_version`,
- `cost_usd` numeric, `fx_rate` numeric, `cost_inr` numeric, `tax_inr` numeric default 0,
- `source`, `period` (`YYYY-MM`, for accruals and invoices), `dedupe_key` (nullable, **unique**), `note`.
- Indexes: `occurred_at`, `(category, occurred_at)`, `document_id`, `(period, category)`. Unique on `dedupe_key`.

`cost_rate_cards` (versioned, editable as data, not code): `version`, `effective_from`, `json` with every price: Gemini by model (`gemini-3.5-flash-lite` standard $0.30 in / $2.50 out, batch $0.15 / $1.25, cached input $0.03, embeddings `gemini-embedding-001` $0.15 in), Cloud Run Tier 2 (vCPU $0.0000336/s, GiB $0.0000035/s, idle min instance $0.0000035/s each, requests $0.40/M), R2 ($0.015/GB-month, free 10 GB; Class A $4.50/M, Class B $0.36/M), Neon (CU-hour $0.106, storage $0.35/GB-month), fixed monthly items (Vercel, Sentry, other) in USD, and `usd_to_inr` (95). Seed version 1 from these verified values; an env var may override the rate; the current card is cached in memory. Thinking tokens bill as output; cached tokens at the cached rate.

`cost_fx_rates` (`date` pk, `usd_to_inr`, `source`) and `cost_refresh_runs` (`id`, `started_at`, `finished_at`, `status`, `notes`) hold the daily exchange rate and the daily-run history (see section 5). `rate_card_proposal` (pending price changes found by the daily price watch, awaiting owner approval) lives in `cost_rate_cards` with `status='proposed'`.

**Prices change, so each event stores the rates and FX it used.** Past rows are never silently re-priced. Provide a dry-run script `backend/scripts/recompute_cost_events.py` (dry-run by default, `--apply`, writes a rollback file outside the repo, per the repo convention) for the case where a price was entered wrongly.

## 4. Recording design (where and how)

Module `backend/app/services/costs/ledger.py`:
- `record_event(**fields)`: builds the row, computes USD/INR from the current rate card, inserts with `ON CONFLICT (dedupe_key) DO NOTHING` (Postgres) or a caught `IntegrityError` (SQLite). Never raises.
- `cost_scope(document_id=..., quiz_id=..., job_id=..., batch_id=...)`: a context manager using `contextvars`, so the Gemini provider can attribute a call to the deck/quiz without changing every function signature. Use ContextVar (it is copied into asyncio tasks and `asyncio.to_thread`); verify it survives the worker threads used by vision/tagging concurrency and add a test for that.
- **Aggregate, do not write per call.** A deck makes about 76 vision calls; at about 60 ms per cross-region round trip a write per call is wasteful. The scope accumulates tokens per `(operation, model)` in memory and flushes once when the scope exits (in a `finally`, so cancelled or failed decks are still charged for what was spent). Quiz prompts write one row per LLM call, off the request path (background task or thread), best-effort.
- Calls with no active scope go to an `unattributed` bucket flushed every N calls and on shutdown (`dedupe_key` null).

Hooks (all of them; this is the main correctness risk):
1. `backend/app/services/ai/gemini_provider.py`: every method that reads `usageMetadata` (`generate_json`, `generate_json_schema`, `generate_multimodal_ex`, `describe_image` including its MAX_TOKENS retry, and the embedding calls). Keep the existing `record_usage` / `usage_snapshot` behaviour and `ingestion_usage` rows working (do not remove them yet).
2. The raw httpx quiz calls: `prompt_quiz/curator_llm.py`, `intent_engine.py`, `interpreter_llm.py` (they use `guarded_post` and currently ignore `usageMetadata`). Parse `usageMetadata` (including `thoughtsTokenCount`) and record per call, with the `quiz_id`/prompt id if the engine has one (otherwise a generated prompt id). On timeout or exception **after the request was sent**, record an estimated row (average of the last 50 rows of that operation) with `note="timeout"`.
3. Embeddings: check whether the embedding response returns token counts; if not, estimate from characters (chars / 4) and mark `source=estimated`.
4. Cloud Run job: in `backend/worker.py` / the pipeline wrapper record `job_compute` at the end (success **or** failure): duration from the real start and end, vCPU and GiB read from env (`CLOUD_RUN_JOB_VCPU`, `CLOUD_RUN_JOB_GIB`, defaults 2 and 8 from your audit), `dedupe_key = "job:<job_id>"`.
5. R2: count objects and bytes uploaded per deck (the R2 service already logs each upload; add a counter inside the deck's scope) and record `r2_put` (Class A ops) and a storage snapshot (`bytes`, which feeds storage accrual) with `dedupe_key = "r2put:<document_id>"`. Also record raw-deck bytes.
6. Fix the price defaults in `usage_cost.py` as proposed in section 9 of your audit (0.30 / 2.50, batch 0.15 / 1.25, INR 95), but make `usage_cost.py` read the rate card so there is one source of prices.

Also apply the two audit recommendations that make the tracker's numbers correct and cheaper, **only if the owner agrees** (ask in the PR, do not bundle): `thinkingBudget: 0` for the curator and intent calls (they time out today), and the Cloud Run sizing.

## 5. Daily refresh, daily accruals and invoices (shared costs)

- **The tracker refreshes itself every day (no manual steps).** One daily run, `POST /costs/refresh`, called by **Cloud Scheduler** once a day (03:00 IST, region `asia-southeast1` only, never Mumbai; the first 3 scheduler jobs per billing account are free). The endpoint requires a shared-secret header (`X-Cron-Secret`, value in env `COST_CRON_SECRET` stored as a Cloud Run secret; this is a minimal guard on a write endpoint, not an auth system). Creating the scheduler job and the secret is a GCP change: list the exact `gcloud` commands in the PR for the owner to approve and run, do not run them yourself. Each daily run is idempotent and does, in order:
  1. **Exchange rate**: fetch today's USD to INR from a free no-key source (primary: frankfurter.app, ECB data; fallback: open.er-api.com), store it in `cost_fx_rates (date, usd_to_inr, source)` and make it the rate for new events. If both fail, keep the last known rate and flag `fx_stale=true` in the summary (never block).
  2. **Daily accruals** for shared costs, one row per category per day, `dedupe_key = "accrual:<category>:<YYYY-MM-DD>"`: `cloud_run_service` (vCPU + GiB x idle rate x 86,400 s x min instances), `neon` (rate card fixed daily share, or real consumption when the optional Neon pull is configured), `vercel`/`other` (monthly amount / days in month), and `r2_storage` (GB-days from the sum of recorded bytes per live deck, minus the free 10 GB pro rata). Cloud Run active time and request charges are added from the real figures when available, else estimated.
  3. **Price watch (flag, never auto-apply)**: fetch the Gemini pricing page (`https://ai.google.dev/gemini-api/docs/pricing`) and compare the `gemini-3.5-flash-lite` and `gemini-embedding-001` prices with the active rate card. If they differ, store a pending `rate_card_proposal` and show a one-line notice on the cost panel ("Gemini prices changed: review"). A price only takes effect when the owner approves it (`PUT /costs/rate-card`), because a scraping mistake must never silently change the books. Cloud Run, Neon and R2 prices stay in the rate card (edited by hand when they change).
  4. **Optional real pulls** (only if configured, each read-only, failure just logs): Neon consumption API, Cloudflare R2 analytics (storage and Class A/B counts), BigQuery billing export (Gemini and GCP actuals). When a real figure for a past day or month arrives it is stored as `source=billed` and supersedes the estimate under the no-double-counting rule below.
  5. **Health**: write the run time to `cost_refresh_runs (started_at, finished_at, status, notes)`. The summary returns `last_refresh_at`; the panel shows "Updated today 03:00" or a quiet "Last updated N days ago" if it has not run for more than 2 days.
- **Catch-up, so a missed day never leaves a hole**: on app start and on the first cost request of a day, if `last_refresh_at` is older than 24 h, run the same refresh in the background and fill every missing day since the last run (accrual keys make this safe to repeat across restarts). This also covers the days before the scheduler is set up.
- Per-event costs (Gemini calls, job runs, R2 puts) are still written at the moment they happen, as in section 4; the daily run only adds the shared costs and keeps rates and the exchange rate current.
- `POST /costs/invoices`: the owner types a real figure per `(category, period)` with amount, currency and GST. Stored as `source=billed`, `operation=invoice`, `dedupe_key = "invoice:<category>:<period>"` (a second entry for the same key updates by inserting a **correction row** with a note and marking the earlier one `superseded` in `units`, never by deleting).
- **No double counting.** For each `(category, period)` headline = the invoice if one exists, else the sum of accruals/measured rows. For Gemini the headline = invoice if present else measured, and the response also returns `measured`, `billed` and `gap` (billed minus measured = spend the tracker missed; this is the figure that proves whether the tracker is complete). Write this rule as one function with tests.

## 6. API (all GET endpoints cached 30 s, at most 3 SQL statements each, one SQL pass with GROUP BY; never load rows into Python to sum them)

- **`GET /costs/simple`** (what the dashboard uses; one query, cached 30 s) -> for `period` = `month` (default) or `all`: `{period, total_inr, lines:[{key:"tagging"|"quiz"|"database"|"storage"|"hosting", label, inr, approx:bool}], last_refresh_at, fx_stale, price_notice|null}`. Mapping: `tagging` = Gemini ingestion (tagging + image reading + embeddings); `quiz` = Gemini quiz calls (intent, curator, interpreter) and other Gemini; `database` = Neon compute + storage; `storage` = R2 (storage + operations); `hosting` = Cloud Run service + job + Vercel + other. `approx` is true when any part of that line is estimated rather than measured or invoiced.
- `GET /costs/summary` -> month to date, last month, all time, each split by category; Gemini split `ingestion` (vision / tagging / embedding), `quiz` (intent / curator / interpreter), `other`. Includes `as_of`, `rate_card_version`, `sources` (how much is measured / estimated / billed).
- `GET /costs/timeline?granularity=month` -> months x category (history). All time starts from the first event or invoice.
- `GET /costs/documents?limit&offset&sort` -> per deck: title, questions, Gemini by stage, job compute, R2 puts, R2 storage per month, total INR, INR per question, plus the allocated shared cost as a separate column. Deleted decks show as "Deleted deck" with their historical cost kept. Join to `documents` by `document_id` in the same query.
- `GET /costs/events?category&document_id&from&to&cursor` -> paginated ledger rows for audit.
- `GET /costs/quizzes` -> cost per quiz prompt session (sum by `quiz_id`), average per prompt.
- `POST /costs/refresh` (secret header, idempotent, returns what it did) and `GET /costs/refresh-status`.
- `POST /costs/invoices` as above. `GET /costs/rate-card`, `PUT /costs/rate-card` (creates a new version; old events keep their own rates).

## 7. History (backfill, run once, idempotent)

Script `backend/scripts/backfill_cost_events.py`, dry-run by default, `--apply`, rollback file outside the repo (same convention as the other repair scripts), read-only toward everything except inserting `cost_events` with dedupe keys (so running it twice adds nothing):
- From `ingestion_usage` (since 13 Nov 2026): one row per usage row, priced with the **correct** rates (not the old 0.075 / 0.30), `source=measured`, `dedupe_key="iu:<id>"`.
- For decks with no usage rows (ingested before logging): estimate tagging from `qshala_tags.tokens_in/out` where present and vision from the measured average (1,156 in / 91 out per image, 1.4 images per question), `source=estimated`, `note="backfill estimate"`.
- Job compute: from `ingestion_jobs` start and finish times, estimated, `dedupe_key="job:<id>"`.
- R2: per deck bytes (documents.file_size_bytes plus media bytes from a read-only R2 listing) as storage snapshots.
- Report totals by month before applying. The owner then types the Aug to Nov invoices in the `/costs` page so history to date matches the real bills (the app cannot know spend from before logging began).

## 8. Frontend

- **Dashboard cost panel: must be very simple (hard rule).** One small compact panel (a single thin border, no shadow, no icon bubbles, no chart, no colour except slate text and the one accent). At first glance it shows only: the title "Spend", a tiny `This month | All time` switch, and six short lines, label left and amount right (tabular-nums): **Tagging**, **Quiz prompts**, **Database**, **Storage**, **Hosting**, then a hairline and **Total**. A "~" before an amount means it is estimated. One muted line underneath: "Updated today 03:00" (or the stale warning). That is all that is visible by default, from `GET /costs/simple`.
- A small "Details" disclosure (chevron, collapsed by default, remembers nothing) expands inline to show the breakdown: Gemini split into image reading / tagging / embeddings / intent / curator, billed vs tracked gap, Cloud Run vs Vercel, R2 storage vs operations, the price-change notice if there is one, and a link "Full cost history" to `/costs`. Details load only when opened (`GET /costs/summary`), so the dashboard stays fast.
- Loads independently from the rest of the dashboard; on failure show one muted line "Cost unavailable. Retry", never an error banner.
- No per-deck tables, ledgers, tokens or rates on the dashboard. Those live only on `/costs`.
- New page `/costs` ("Cost history"): month by category table (history), a per-deck table (sortable, shows INR per question), a quizzes line (average INR per prompt), a ledger view (paginated), and an "Enter an invoice" form (category, month, amount, GST). Empty states are quiet.
- Follow `frontend/AGENTS.md` (read `node_modules/next/dist/docs/` first). `npx tsc --noEmit`, eslint with no new findings (strict `react-hooks/set-state-in-effect`), `npm run build`. Render at 1440 and 375 px and look at the screenshots.

## 9. Tests (offline only, never call real APIs)

- Price math: input, output, thinking billed as output, cached discount, batch rates, INR conversion; one function, one rate card.
- Scope aggregation: N concurrent calls in threads and asyncio tasks land in one row per `(operation, model)`; the scope flushes on exception and on cancellation; a failing database write never raises into the caller.
- Idempotency: same `dedupe_key` twice inserts once; the daily refresh run twice on the same day inserts once; a refresh after a 3-day gap fills exactly the 3 missing days; an exchange-rate fetch failure keeps the last rate and sets `fx_stale`; a Gemini price change on the pricing page creates a proposal and does **not** change the active rate card; backfill run twice adds nothing. Mock all HTTP (exchange rate, pricing page) in tests.
- No double counting: invoice present vs absent for a `(category, period)`; gap = billed minus measured.
- Deleting a deck keeps its cost rows and the per-deck view shows "Deleted deck".
- Timeout path records an estimated row.
- Query budget: each GET endpoint at most 3 statements using a `before_cursor_execute` counter; assert counts, never wall time.
- Existing suites that touch `usage_cost` and ingestion usage still pass (update `test_usage_tracking.py` expectations for the corrected prices). Compare against a baseline of failures on untouched `main`; do not fix unrelated failures.

## 10. Phases and gates (stop and report after each; do not start the next without a go)

1. **Foundation**: tables (`cost_events`, `cost_rate_cards`, `cost_fx_rates`, `cost_refresh_runs`), migration, rate card, `record_event`, scope, price fix, tests.
2. **Recording**: all Gemini hooks (ingestion, quiz, embeddings, timeouts), job compute, R2 puts. Prove it by running one small deck locally against SQLite with a fake Gemini and showing the ledger rows. Then the **daily refresh** (`POST /costs/refresh`: exchange rate, daily accruals, price watch, catch-up on start) with tests that a missed day is filled and a repeat run adds nothing; give the owner the exact `gcloud scheduler` and secret commands to approve.
3. **Reads and UI**: `GET /costs/simple` and the small dashboard panel with its Details disclosure, then `/costs` page, invoice form, screenshots (panel collapsed and expanded, 1440 and 375 px).
4. **History**: backfill dry run report, then (after approval) apply.
5. **Optional automation (ask first)**: pull real figures instead of typing invoices: BigQuery billing export (needs the owner to enable export; read-only service account), Neon consumption API, Cloudflare R2 analytics. Each behind its own env var, read-only, cached 60 minutes, failure shows "not connected".

## 11. Report format (every gate)

Done / Not done / Numbers (statement counts, payload sizes, sample ledger rows with real values) / Evidence (test output) / Surprises or contradictions with this brief / Risks accepted / Questions for the owner. Mention explicitly: which Gemini call sites you found that this brief did not list, and the sum of tokens recorded for one real ingestion compared with the token counts in the existing `ingestion_usage` for the same deck (they must match).
