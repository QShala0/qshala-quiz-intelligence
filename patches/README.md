# Prompt-engine hardening patches (9)

Built to keep today's output unchanged. Base: head of `claude/sweet-hawking-mt6nkf` (it already contains the
earlier output-identical speed-ups and the risk plan). Apply in order:

    git checkout -b perf/prompt-engine-hardening origin/claude/sweet-hawking-mt6nkf
    git am --keep-cr patches/*.patch        # --keep-cr is required: interpreter_llm.py has CRLF line endings

| # | Patch | Changes output? | Notes |
|---|-------|-----------------|-------|
| 1 | Golden snapshot + live snapshot tool | no | blocking test `test_prompt_quiz_golden.py`; `prompt_quiz_live_snapshot.py` covers the Gemini path |
| 2 | Skip unused bank columns | no | guard tests prove the bank is equal and nothing lazy-loads |
| 3 | Serve expired bank while refreshing | no (new/edited questions can appear a few seconds later) | `PROMPT_QUIZ_BANK_STALE_S` (default 300, 0 = off) |
| 4 | DISTINCT ON tag lookup | no, and OFF by default | `PROMPT_QUIZ_TAGS_QUERY=distinct_on` only after `compare_latest_tags.py` says IDENTICAL and faster |
| 5 | Trigram search indexes | no (indexes never change results) | `add_search_indexes.py` (dry-run default) or alembic 0008 |
| 6 | Central model config + daily model check | no (same model values) | `/health` gains `gemini_models`; never affects healthy/unhealthy |
| 7 | Gemini circuit breaker, no 2nd call after a failure | only while Gemini is failing | `PROMPT_QUIZ_BREAKER=0` disables |
| 8 | Stage timings + `GET /api/v1/prompt-quiz/metrics` | no (adds a `timings_ms` key) | in-memory, per instance |
| 9 | Backup script + restore drill | no | needs a PRIVATE bucket in `BACKUP_R2_BUCKET`; refuses the public media bucket |

Not built: recoverable/soft delete (it needs a UI contract change; see docs/RISK_AND_SCALING_PLAN.md R1).
Measured locally: 399 passed / 8 skipped / 1 failed (`test_interpret_difficulty_bias`, already failing on main);
golden output IDENTICAL; on a 30k-question Postgres the tag search went 41 ms -> 0.37 ms, the tag lookup was identical and ~18% faster.
