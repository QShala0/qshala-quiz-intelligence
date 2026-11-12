You are the worker (see QSHALA_PLAN.md section 0 for the rules and report format). GB has said "push it" for this task only.

GOAL: land nine prepared patches that speed up and harden the prompt engine WITHOUT changing which questions it returns.

STEPS
1. git fetch origin. Create branch perf/prompt-engine-hardening from origin/claude/sweet-hawking-mt6nkf.
2. Apply the patches in order: git am --keep-cr patches/*.patch   (--keep-cr is required). If one fails, stop and report; do not hand-edit patches.
3. Install backend requirements, then run: python -m pytest backend/tests -q -p no:cacheprovider
   EXPECTED: 399 passed, 8 skipped, exactly 1 failed = test_interpret_difficulty_bias (already fails on main; do NOT change engine behaviour to fix it, report only).
4. Run: python backend/scripts/prompt_quiz_golden.py . compare backend/eval_data/prompt_quiz_golden.json   EXPECTED: IDENTICAL.
   If either check differs, STOP and report the difference. Never re-record the golden snapshot to make it pass.
5. git push -u origin perf/prompt-engine-hardening and open a PR into main titled "Prompt engine hardening (9 output-preserving patches)". Paste the README table from patches/README.md and the test/golden results in the PR body. Do NOT merge unless GB writes "merge it".

AFTER MERGE, ON THE REAL DATABASE (report each result; change nothing else):
a. BEFORE deploying: DATABASE_URL and GEMINI_API_KEY set -> python backend/scripts/prompt_quiz_live_snapshot.py record /tmp/before.json ; repeat once more as /tmp/before2.json and compare it to the first to learn the Gemini noise level.
b. python backend/scripts/add_search_indexes.py (dry run), then --apply in a quiet hour. Capture EXPLAIN ANALYZE of a tag search and a subtopic search before and after.
c. python backend/scripts/compare_latest_tags.py. Only if it prints IDENTICAL and distinct_on is faster, set PROMPT_QUIZ_TAGS_QUERY=distinct_on; otherwise leave it unset.
d. Create a PRIVATE R2 bucket (never the public media bucket), set BACKUP_R2_BUCKET, run backup_vault.py --dry-run then for real, schedule it nightly (Cloud Scheduler), then run restore_check.py into a scratch database and attach the MATCH output.
e. After deploy: prompt_quiz_live_snapshot.py compare /tmp/before.json. Report every CHANGED prompt; differences no larger than the baseline noise from (a) are acceptable, anything else is a stop-and-report.
f. Open /api/v1/prompt-quiz/metrics and /health and paste the results.

RULES: never print or store keys or the database URL; do not touch the public media bucket; do not change model names or any default; do not enable anything that is off by default except as described in (c); one report per step in the QSHALA_PLAN section 0.2 format.
