# Prompt-engine hardening (11 commits on top of main, output-preserving)

Built on current `main` (after the deck-filter / slot / typo-normalisation work). The baseline is main's own output:
`backend/eval_data/prompt_quiz_golden.json` was recorded on main BEFORE any of these changes and must stay IDENTICAL.

| Commit | Changes output? | Notes |
|---|---|---|
| Golden snapshot + live snapshot tool + blocking test | no | `prompt_quiz_live_snapshot.py` covers the Gemini path (needs the real DB and key) |
| Shared search index, heap top-k, caches, faster duplicate filter, shared HTTP client | no | ~4.4x faster drafts at 20k questions (8.1 s -> 1.8 s on synthetic data) |
| Skip unused bank columns | no | guard tests prove the bank is equal and nothing lazy-loads |
| Serve expired bank while one thread refreshes | no (new or edited questions can show a few seconds later) | `PROMPT_QUIZ_BANK_STALE_S` (default 300, 0 = off) |
| DISTINCT ON tag lookup | no, OFF by default | `PROMPT_QUIZ_TAGS_QUERY=distinct_on` only after `compare_latest_tags.py` prints IDENTICAL and faster |
| Trigram search indexes | no (indexes never change results) | `add_search_indexes.py` (dry run by default) or alembic `0010_search_trigram_indexes` |
| Central model config + daily model check | no (same model values) | `/health` gains `gemini_models`; never affects healthy/unhealthy |
| Gemini circuit breaker | only while Gemini is failing | `PROMPT_QUIZ_BREAKER=0` disables |
| Stage timings + `GET /api/v1/prompt-quiz/metrics` | no (adds `timings_ms`) | in-memory, per instance |
| Backup script + restore drill | no | needs a PRIVATE bucket in `BACKUP_R2_BUCKET`; refuses the public media bucket |

Not built: recoverable / soft delete (needs a UI contract change; see docs/RISK_AND_SCALING_PLAN.md R1).

Measured: 510 passed, 21 skipped, 0 failed (a `test_format_export` test flaked once in one full run and passed alone, on
pristine main, and in two further full runs). Golden IDENTICAL on both the plain and the cached-bank paths. On a 30k-question
Postgres: rare-tag search 41 ms -> 0.4 ms, tag lookup identical and ~20-25% faster, backup -> restore matched all tables.

## Prompt for the worker (Antigravity)

```
You are the worker (QSHALA_PLAN.md section 0 rules and report format apply). GB has said "push it" for this task only.

GOAL: land the prepared, output-preserving prompt-engine hardening WITHOUT changing which questions the engine returns.

SETUP (Windows note: do not change line endings; `git status` must be clean after checkout)
0. If a `git am` is in progress, run `git am --abort`. Delete any local branch perf/prompt-engine-hardening from the earlier attempt (the old patch files are superseded; do not use them).
1. git fetch origin
2. git checkout -B perf/prompt-engine-hardening origin/claude/sweet-hawking-mt6nkf

CHECKS (stop and report on any mismatch; never re-record the golden snapshot to make it pass)
3. Install backend requirements, then: python -m pytest backend/tests -q -p no:cacheprovider
   EXPECTED: about 510 passed, 21 skipped, 0 failed. If exactly one test in test_format_export.py fails, rerun the suite once; it is a known one-off flake. Any other failure: stop and report.
4. python backend/scripts/prompt_quiz_golden.py . compare backend/eval_data/prompt_quiz_golden.json   EXPECTED: IDENTICAL
   GOLDEN_CACHED=1 PROMPT_QUIZ_BANK_TTL_S=600 python backend/scripts/prompt_quiz_golden.py . compare backend/eval_data/prompt_quiz_golden.json   EXPECTED: IDENTICAL

PUBLISH
5. git push -u origin perf/prompt-engine-hardening. `gh` is not installed: do not try to install it; instead print the PR link https://github.com/QShala0/qshala-quiz-intelligence/pull/new/perf/prompt-engine-hardening and paste the table above plus the test and golden results for GB to use as the PR description. Do NOT merge anything into main.

AFTER GB MERGES, ON THE REAL DATABASE (one report per step; change nothing else)
a. BEFORE deploying: with DATABASE_URL and GEMINI_API_KEY set: python backend/scripts/prompt_quiz_live_snapshot.py record before.json, then record again as before2.json and compare it with the first to learn the Gemini noise level.
b. python backend/scripts/add_search_indexes.py (dry run), then --apply in a quiet hour; capture EXPLAIN ANALYZE for a tag search and a subtopic search before and after.
c. python backend/scripts/compare_latest_tags.py. Only if it prints IDENTICAL and distinct_on is faster, set PROMPT_QUIZ_TAGS_QUERY=distinct_on; otherwise leave it unset.
d. Create a PRIVATE R2 bucket (never the public media bucket), set BACKUP_R2_BUCKET, run backup_vault.py --dry-run then for real, schedule it nightly, then run restore_check.py into a scratch database and attach the MATCH output.
e. After deploy: prompt_quiz_live_snapshot.py compare before.json. Report every CHANGED prompt; differences no larger than the baseline noise from (a) are acceptable, anything larger is a stop-and-report.
f. Open /api/v1/prompt-quiz/metrics and /health and paste both.

RULES: never print or store keys or the database URL; do not touch the public media bucket; do not change model names or defaults; do not enable anything that is off by default except as described in (c).
```
