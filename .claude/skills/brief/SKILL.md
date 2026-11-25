---
name: brief
description: Write a paste-ready implementation brief for Antigravity (the worker agent) in the standard QShala format. Use when the owner wants a prompt, brief or task for Antigravity.
---

Write ONE paste-ready brief for Antigravity. The owner pastes it as is, so output a single fenced `text` block, nothing after it except at most two lines of owner-side steps.

First gather facts, do not guess: read the files the task touches (targeted greps, `graphify-out/GRAPH_REPORT.md`), check live values (`gcloud run services describe`, `git log`, open PRs) when the brief depends on them.

Structure of the brief:
1. **Context**: what exists, why this task, which docs to read (`CLAUDE.md`, `ANTIGRAVITY.md`, any `docs/*` brief). One short paragraph.
2. **Goal**: the outcome in plain words, plus what is explicitly out of scope.
3. **Hard rules**: new branch from latest `main`, open a PR and never merge it, no production DB writes (read-only with `REQUIRE_POSTGRES=1`, `SET TRANSACTION READ ONLY`), no secrets printed or committed, never create Cloud Scheduler jobs or secrets (give the owner exact PowerShell commands instead), no `asia-south1`, dry-run first for any data script.
4. **Phases**: small, ordered, each with "done means" and a stop. Say "stop after each phase, send the gate report, wait for my go" for anything larger than one PR.
5. **Verification required**: tests to run (the full suite for shared code), tsc/eslint with no new findings, rendered screenshots for UI or slides, real (not mocked) evidence for any number.
6. **Gate report format**: Done, Not done, Numbers, Evidence, Surprises, Risks, Questions for the owner. Lead with failures.

Style: plain, specific, no filler. Name files and functions. State thresholds as numbers. Where the owner must act (keys, console steps), list those steps separately, short, in PowerShell for Windows.
