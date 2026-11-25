---
name: gate-review
description: Review an Antigravity gate report or PR before approving a phase or merging. Checks CI, claims vs evidence, mocked numbers, and rule violations. Use when the owner pastes a gate report or asks to review/merge a PR.
---

The owner pastes Antigravity's report. Treat it as claims, not facts. Do these, in parallel where possible:

1. **PR state**: `gh pr view <n> --json state,mergeStateStatus,statusCheckRollup`. If any backend or frontend check failed, fetch the failing log (`gh run view <id> --log-failed`) and name the real cause. Flaky infra (e.g. Google Fonts fetch in CI) vs a real test failure are different verdicts.
2. **Diff scope**: `gh pr diff <n> --name-only`. Flag unrelated files, edits to shared tests, deleted tests or skips added to make CI green.
3. **Claims vs evidence**: for every number, check it is real. Red flags: round token counts, output tokens of 0 on a vision call, `doc_123` style ids, "simulated" or `Fake*` helpers, figures that do not match known baselines (vision about 1,156 in / 91 out; tagging about 5,688 in / 1,681 out per 20 questions).
4. **Rule check** against `ANTIGRAVITY.md` and `CLAUDE.md`: no prod writes, no secrets, no `asia-south1`, dry-run defaults, answer never on a question slide, security not added unasked, commands correct for this project (project `qshala-quiz`, service `qshala-quiz-intelligence`, region `asia-southeast1`).
5. **Commands handed to the owner**: sanity-check every gcloud/PowerShell command for wrong project, service or account names, quoting, encoding, and ordering (for example a secret must exist before a scheduler job uses it, and code must be deployed before its endpoint is called).

Output, short: verdict (approve / approve with fixes / not ready), then the problems in order of severity, then a paste-ready follow-up prompt for Antigravity if fixes are needed. Never merge: ask the owner first. After a merge, run the deploy check.
