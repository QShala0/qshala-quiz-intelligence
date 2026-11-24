# Antigravity Operating Manual — QShala Quiz Intelligence

This is your operating manual for this repository. Read it fully before your first action in any
session. It exists because specific, repeated mistakes have cost real time and real money on this
project. Every rule below is here because something broke.

**Precedence:** `CLAUDE.md` (domain rules) → this file (process rules) → `frontend/AGENTS.md`
(frontend specifics) → your own judgment. If this file contradicts `CLAUDE.md` on a domain rule,
`CLAUDE.md` wins. If the owner's instruction in chat contradicts this file, the owner wins.

---

## Part 1 — The Five Laws

These are not guidelines. Breaking one of these is a failed task regardless of how good the code is.

### Law 1: Never claim done without verification
"It should work" is not done. "Tests pass" is done. "I rendered the slide and looked at it" is done.
If you cannot verify, say explicitly: *"I could not verify X because Y."* Never paper over it.

### Law 2: Read before you write
Never edit a file you have not read in this session. Never assume an API's shape from memory —
especially Next.js 16 (read `node_modules/next/dist/docs/`) and Gemini API request bodies.

### Law 3: Smallest correct change
A bug fix does not need surrounding cleanup. A one-shot script does not need a helper class. No
backwards-compatibility shims, no feature flags, no defensive validation for states that cannot
happen. Three similar lines beat a premature abstraction.

### Law 4: Destructive actions require explicit permission
`git push --force`, `git reset --hard`, `git clean -f`, `rm -rf`, dropping tables, deleting
branches, `--no-verify`. Ask first, every time. A previous approval does not carry forward.

### Law 5: Report failures loudly
If you reverted something, skipped a test, hit a wall, or made a judgment call the owner might
disagree with — lead with it. Do not bury it at the end. Do not omit it.

---

## Part 2 — GitHub Discipline (read this twice)

This is where agents do the most damage. Follow this section literally.

### 2.1 Branch rules — non-negotiable

| Rule | Why |
|---|---|
| Work only on the assigned feature branch | Pushing to `main` triggers an immediate production deploy |
| Current branch: `claude/wonderful-fermi-cdngu0` | Confirm with `git branch --show-current` before any commit |
| Never force-push a shared branch | Destroys other people's and other sessions' work irrecoverably |
| Never push to `main` without explicit instruction | `main` → Vercel frontend deploy + Cloud Build backend deploy, instantly |
| Never create a PR unless explicitly asked | A PR is a request for human attention; do not spend it unasked |

### 2.2 Before every commit — the five-command check

Run these. Every time. In this order. No exceptions.

```bash
git branch --show-current
```

```bash
git status
```

```bash
git diff
```

```bash
git diff --staged
```

```bash
git log --oneline -5
```

What you are looking for:
- **`git branch --show-current`** — Am I on the right branch? If it says `main`, STOP.
- **`git status`** — Are there files here I did not create? Untracked files might be the owner's
  work in progress. Investigate before staging anything broad.
- **`git diff`** — Does every single changed line belong to the task I was given? If you see
  changes you did not intend, you edited the wrong thing.
- **`git diff --staged`** — Catches an accidental earlier `git add`.
- **`git log --oneline -5`** — Match the repo's commit message style. Read it, do not guess it.

### 2.3 Staging — never use `git add -A` or `git add .`

Stage files by name, always:

```bash
git add backend/app/services/tagging/auto_tagger.py backend/tests/test_tagger.py
```

Then verify what you actually staged:

```bash
git status
```

**Why this matters:** `git add .` has, in real projects, committed `.env` files, API keys, 400MB
of `node_modules`, database dumps, and `settings.local.json`. Once a secret is in git history it is
compromised forever — rotating the key is the only fix. The file name does not have to look
suspicious. If you see anything you did not expect in the staged list, read the file's contents
before continuing.

**Never commit:** `.env`, `.env.*` (except `.env.example`), `*.lock` files you did not intentionally
regenerate, `credentials.json`, `*.pem`, `*.key`, `.claude/settings.local.json`, anything under
`graphify-out/` unless regenerating it was the task, rollback files written by repair scripts.

### 2.4 Commit messages

Follow the existing style in this repo — Conventional Commits with a scope:

```
perf(tokens): reduce Gemini API token waste across 3 call sites

- Disable FORCE_AI_TAGGING: re-enable rule-based confidence gate so
  questions with clear keyword matches skip Gemini
- Cap interpreter_llm output at 800 tokens

Co-Authored-By: <attribution line as instructed>
```

Rules:
- Subject line under 72 chars, imperative mood, lowercase after the colon.
- Explain **why**, not what. The diff shows what.
- Always pass the message via heredoc so formatting survives:

```bash
git commit -m "$(cat <<'EOF'
fix(export): exclude answer images from question slides

Answer media was leaking onto question slides in the school template
because the exporter filtered by slide index, not by media role.
EOF
)"
```

- Never put a model identifier, session URL, or internal tool name in a commit message, PR body,
  or code comment unless an explicit attribution instruction tells you to.

### 2.5 Pushing

```bash
git push -u origin claude/wonderful-fermi-cdngu0
```

Retry network failures up to 4 times with backoff. Never add `--force` or `--force-with-lease` to
work around a rejection.

**If the push is rejected** because the remote has commits you lack:

```bash
git fetch origin claude/wonderful-fermi-cdngu0 && git merge --no-edit FETCH_HEAD
```

Then push again and **tell the owner it happened**. Those remote commits may be an earlier session's
legitimate work. Do not rebase. Do not force-push. Do not "clean up" the history.

If git refuses the merge as unrelated histories, stop and ask. Do not improvise.

### 2.6 Amending and history rewriting

- **Default: never amend.** Create a new commit.
- If a pre-commit hook fails, the commit **did not happen** — so `--amend` would silently modify the
  *previous*, already-good commit. Fix the issue, re-stage, make a **new** commit.
- Never `git rebase -i`, `git filter-branch`, or `git reset --hard` on anything pushed.
- Never use `--no-verify`. If a hook blocks you, the hook is telling you something true. Fix the
  underlying problem.

### 2.7 Pull requests

Only when explicitly asked. When asked:

1. Check for a template: `ls .github/PULL_REQUEST_TEMPLATE* .github/pull_request_template.md`
   (this repo currently has none — only `.github/workflows/`).
2. Review the **full** branch diff, not just the last commit:
   ```bash
   git diff main...HEAD
   ```
3. Title under 70 chars. Details go in the body.
4. Body: a Summary section (1–3 bullets) and a Test plan section (checklist of what you verified).
5. Return the PR URL so the owner can open it.
6. Reference issues and PRs as `QShala0/qshala-quiz-intelligence#123`, never a bare `#123`.
7. Never write a bare `@name` in a PR body or comment — GitHub notifies that real account. Put roles
   and placeholders in backticks: `` `@reviewer` ``.

### 2.8 Things you may never do on GitHub

- Approve a PR. Merge a PR. Close an issue or PR. (These are human decisions.)
- Comment on a PR or issue unless the owner asked you to.
- Create a repository.
- Touch any repository other than `QShala0/qshala-quiz-intelligence`.
- Disable, skip, or quarantine a failing test to make CI green. Fix the test or fix the code.
- Push an empty commit or close/reopen a PR to re-trigger CI.

### 2.9 The CI and deploy reality on this repo

Understand this clearly, because it is unusual and it has bitten this project before:

- **`.github/workflows/ci.yml`** runs on *every* push to *every* branch. It installs from
  `backend/requirements-dev.lock.txt` (not `requirements.txt`), runs `ruff` (non-blocking), then
  `pytest backend/tests -q --tb=short`, and separately type-checks and builds the frontend.
- **Cloud Build does NOT wait for that CI.** The `qshala-test-gated-deploy` trigger fires on push to
  `main` independently. It runs the full `python -m pytest backend/tests` itself and **any single
  failing test blocks the backend deploy silently** — a stale health-check test once kept the
  Singapore service on an old build for two days with nothing announcing it.
- **Therefore:** after any push that affects `main`, check the Cloud Build result. Do not assume a
  green GitHub CI means the backend deployed.
- Never run `gcloud builds submit` from a local folder. It skips the test gate and uploads untracked
  files.

---

## Part 3 — Testing Discipline

### 3.1 Commands that actually work here

```bash
python -m pytest backend/tests/test_specific_file.py -q
```

```bash
cd frontend && npx tsc --noEmit && npx eslint src/path/to/changed/file.tsx
```

Notes learned the hard way:
- **There is no `pytest-timeout` plugin installed.** `--timeout=60` fails with
  `unrecognized arguments`. Do not pass it.
- There is no `pytest.ini` / `pyproject.toml` pytest config. Run from the repo root.
- If imports fail (`No module named 'fastapi'`), install dependencies:
  ```bash
  pip install -r backend/requirements.txt
  ```
  CI uses `backend/requirements-dev.lock.txt` — prefer that for reproducing a CI failure.
- Run the **relevant** test files, not the full suite, during development. The full suite is slow.
  Run the full suite once before declaring a backend change finished.
- The ESLint rule `react-hooks/set-state-in-effect` is strict and some older files already have
  findings. Your job is to **add zero new ones**, not to fix the old ones.

### 3.2 Which tests to trust

Some backend tests fail on the owner's machine because of the local `.env` — notably
`test_qa_tournament_overhaul` and `test_require_postgres_refuses_to_boot_on_sqlite`. These pass in
the Cloud Build gate. **The gate is the authority.** Do not "fix" a test that only fails locally
because of environment configuration; confirm the cause first.

### 3.3 Writing tests

- Mock all network calls. Tests must never hit the real Gemini API, R2, or SharePoint.
  `backend/tests/conftest.py` asserts no real Gemini key and no real R2 storage is present.
- Match existing patterns — read a neighbouring test file before writing a new one.
- Test behaviour, not implementation details. A test that asserts on prompt *wording* will break on
  every harmless copy edit.
- When a test fails, diagnose which is wrong: the code or the test. Do **not** change production
  code to satisfy a test with a bad assumption, and do **not** weaken a test to match broken code.
  Say which one you concluded and why.

### 3.4 Verification for slides and UI

`CLAUDE.md` requires you to actually look at output, not just assert on it:

```bash
soffice --headless --convert-to pdf output.pptx && pdftoppm -png -r 80 output.pdf slide
```

```bash
pdffonts output.pdf
```

Then **read the image with your image tool**. Confirm: no answer on a question slide, Causten Round
fonts actually used, answer text black not green, one clue per slide for STEPPED_CLUE.

For frontend work, start the dev server and use the feature in a browser. Check the golden path
*and* the edge cases. If you cannot test the UI, say so explicitly rather than claiming success —
type checks verify code correctness, not feature correctness.

---

## Part 4 — Repo-Specific Landmines

These will waste your time if you do not know them in advance.

### 4.1 Line endings
`backend/app/services/prompt_quiz/interpreter_llm.py` (and possibly others) uses `\r\r\n` — a double
carriage return. String-matching edits will fail silently-looking failures. Diagnose with:

```bash
cat -A path/to/file.py | sed -n '170,185p'
```

If an edit tool cannot match, do a byte-level replacement in Python rather than guessing at
whitespace.

### 4.2 Format payloads
A question's `format_payload` must contain an `extraction` dict. **A payload without it is silently
ignored by every renderer** and falls back to merged question text. Canonical shapes are in
`CLAUDE.md` — read them. The type is `"CONNECT"`, never `CONNECT_4`.
`frontend/src/lib/formatPayloads.ts` and `backend/.../format_slides.py` must stay in sync; changing
one without the other is a bug.

### 4.3 Repair scripts
Everything in `backend/scripts/` (`repair_media_questions.py`, `fix_picture_sides.py`,
`retag_answer_led.py`, etc.) is **dry-run by default** and requires `--apply`. They write a rollback
file outside the repo. Never add `--apply` to a command the owner did not ask you to apply.

### 4.4 Startup maintenance
Legacy backfill, bilingual repair, and `heal_specialized.py` are **OFF** on Cloud Run by default
(`startup_maintenance_mode()` in `database.py`). To run once: set
`STARTUP_MAINTENANCE_MODE=background` for one deploy, then remove it. Do not leave it on.

### 4.5 Region
GCP project `qshala-quiz`, region **`asia-southeast1` (Singapore) only**. The Mumbai `asia-south1`
copy was deleted 19 Nov 2026. **Never create anything in `asia-south1`.**

### 4.6 Cloud Run sizing
Set by hand; the pipeline never changes it. Service: 4Gi, 2 vCPU, max 1 instance, min 1 (kept warm
by owner decision, 21 Nov 2026). Job: 8Gi, 3600s. Verify live values with
`gcloud run services describe` before relying on them. Env vars and secrets set by hand survive
deploys — only the image changes.

### 4.7 Topics vs format labels
A deck's round or format label ("THREE CLUES", "Portfolio Builder") is **never** a topic, and a
format name is **never** a tag. See `is_format_or_round_label` in `qshala_rules.py`. The topic comes
from the answer.

### 4.8 Explore efficiently
Read `graphify-out/GRAPH_REPORT.md` before exploring code. Use targeted greps, not whole-file reads.
Run `graphify update .` after big structural changes.

---

## Part 5 — Cost and Token Awareness

This project pays per Gemini token and the owner watches the bill.

- Every Gemini call needs `maxOutputTokens` and an explicit thinking budget. An uncapped
  `generate_json` can burn thousands of thinking tokens on a classification task.
- Prefer batched calls over per-item calls. `tag_questions_batched` sends the ~8K-token taxonomy once
  per 20 questions instead of once per question.
- Before adding a new LLM call, ask whether a rule-based path can answer it. The confidence gate in
  `auto_tagger.py` skips Gemini entirely for unambiguous questions — that pattern is the preferred
  one.
- When trimming a prompt to save tokens, you must prove output is unchanged. Write or run a
  benchmark. `backend/tests/test_token_optimization_benchmarks.py` is the model for this.
- **If a token optimization changes output quality, revert that specific optimization.** Cost savings
  never justify a quality regression. Report which one you reverted and why.

---

## Part 6 — Code Standards

### Comments
Default to **none**. Write one only when the *why* is non-obvious: a hidden constraint, a subtle
invariant, a workaround for a specific bug, behaviour that would surprise a reader. Never explain
what well-named code already says. Never reference the current task, ticket, or caller
("added for the Y flow", "handles issue #123") — that belongs in the commit message and rots.

One short line maximum. No multi-paragraph docstrings, no comment blocks.

### Security
- Never introduce command injection, XSS, SQL injection, or path traversal. Parameterize queries.
  Validate at system boundaries only (user input, external APIs) — trust internal code.
- Never log secrets, API keys, or tokens.
- **Security hardening is postponed on this project on purpose.** Do not add auth, rate limiting, or
  lock-down work unless explicitly asked. (`CLAUDE.md`)

### Scope
Do not add features, refactors, or abstractions beyond the task. Do not leave half-finished
implementations. Do not rename unused variables to `_var` or leave `// removed` comments — if
something is genuinely unused, delete it.

---

## Part 7 — Communication Standards

- State what you are about to do in one sentence before your first tool call.
- Give short updates at real moments: a finding, a direction change, a blocker. One sentence each.
- Do not narrate your deliberation. State decisions and results.
- Reference code as `file_path:line_number` so the owner can navigate.
- End-of-turn summary: one or two sentences. What changed, what is next.
- Keep answers short. The owner has said so explicitly. Minimize manual steps required of them.
- Plan first for bigger features, then implement. (`CLAUDE.md`)

---

## Part 8 — Pre-Flight and Post-Flight Checklists

### Before starting any task
- [ ] Read `CLAUDE.md`, this file, and `frontend/AGENTS.md` if touching frontend
- [ ] Read `graphify-out/GRAPH_REPORT.md` before exploring code
- [ ] `git branch --show-current` — am I on the assigned feature branch?
- [ ] `git status` — is the tree clean? If not, whose work is this?
- [ ] Do I understand the task well enough to state the acceptance criteria? If not, ask.

### Before every commit
- [ ] Right branch confirmed
- [ ] `git diff` reviewed line by line — every change is intentional
- [ ] Files staged **by name**, never `git add .`
- [ ] `git status` re-checked after staging — nothing unexpected
- [ ] No secrets, no `.env`, no large binaries, no rollback files
- [ ] Relevant tests run and passing
- [ ] Commit message explains **why**, matches repo style, heredoc-formatted

### Before saying "done"
- [ ] Tests actually ran — I have the output, I am not assuming
- [ ] For slides/UI: rendered and visually inspected, or explicitly stated I could not
- [ ] Frontend: `npx tsc --noEmit` clean, `npx eslint` adds zero new findings
- [ ] Pushed to the feature branch
- [ ] If the change affects `main`: Cloud Build result checked
- [ ] Every failure, revert, skip, and judgment call is reported in my summary
- [ ] No TODOs or dead code left behind

---

## Part 9 — When You Are Stuck

In order of preference:

1. **Read more code.** Most blockers are a wrong assumption about existing behaviour.
2. **Check the docs that ship with the dependency.** Especially `node_modules/next/dist/docs/` —
   Next.js 16 has breaking changes your memory predates.
3. **Reproduce the failure in isolation** with a minimal script in the scratchpad directory.
4. **Ask the owner** with a specific question and the two or three options you see. "It does not
   work" is not a question; "the batch tagger needs ORM objects but `step_tag` has dicts — reorder
   INSERT before TAG, or convert in place?" is.

Never do these when stuck:
- Delete or bypass the obstacle (`--no-verify`, skipping a test, `rm -rf`)
- Guess at an API shape and hope
- Claim partial success
- Loop on the same failing command more than twice without changing approach

---

## Part 10 — The One-Paragraph Version

Work only on the assigned feature branch. Read before you write. Stage files by name and read your
own diff before every commit. Never force-push, never amend, never `git add .`, never create a PR
or comment on GitHub unasked, never skip a test to get green. Cap every Gemini call. Verify by
running tests and by actually looking at rendered output. Make the smallest correct change and
nothing more. When something fails or you reverted something, lead with it. When genuinely stuck,
ask a specific question rather than improvising around the obstacle.
