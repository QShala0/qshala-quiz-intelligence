# Claude setup for QShala

What lives where, and how to set up the claude.ai "QShala Brain" Project (the chat side). The repo side (Claude Code) is already
configured by files in this repo.

## Repo side (automatic, nothing to do)
| File | Purpose |
|---|---|
| `CLAUDE.md`, `backend/CLAUDE.md`, `frontend/CLAUDE.md` + `AGENTS.md` | Rules Claude Code loads on every session; backend/frontend ones load only in those folders |
| `ANTIGRAVITY.md` | Operating manual for the worker agent |
| `.claude/settings.json` | Shared permissions (safe commands pre-approved, `.env` unreadable) and the guard hook |
| `.claude/hooks/guard.js` | Blocks `gcloud builds submit`, force-push, `--no-verify`, staging `.env`, `asia-south1`, editing `.env` |
| `.claude/skills/` | `/brief`, `/gate-review`, `/deploy-check`, `/render-check` |
| `.claude/agents/` | `code-reviewer`, `pptx-verifier`, `db-reader` (read-only prod DB) |

Keep `CLAUDE.md` under about 150 lines. When a rule only matters in one folder, move it to that folder's `CLAUDE.md`. This repo is
public: no keys, emails or customer data in any of these files.

## claude.ai Project (create once)
1. claude.ai, Projects, **Create project**, name it `QShala Brain`.
2. Paste the text below into **Project instructions**.
3. Add these **Project knowledge** files (re-upload when they change a lot): `CLAUDE.md`, `ANTIGRAVITY.md`, `docs/DESIGN.md`,
   `docs/DEPLOY.md`, `QSHALA_HANDOFF.md`, `docs/COST_ESTIMATE.md`, `docs/COST_TRACKER_BRIEF.md`.

### Project instructions (paste this)
```text
You are the lead engineer ("the brain") for QShala Quiz Intelligence: an internal tool where quiz masters ingest PPTX quiz decks
into a question bank, search it, build quizzes from a prompt, present them live and export PPTX. Stack: FastAPI + SQLAlchemy on
Cloud Run (project qshala-quiz, region asia-southeast1 only), Neon Postgres, Cloudflare R2, Next.js 16 on Vercel, Gemini for
tagging, vision and quiz curation. The attached files are the source of truth; if they conflict with your memory, they win.

How we work: I (the owner) talk to you; an agent called Antigravity implements. You analyse, plan, write briefs and review its
gate reports. Never assume its reports are correct: ask me for the PR link or CI result, and challenge numbers that look mocked.

Answer style: short and decision-ready. Lead with the verdict. One paste-ready prompt in a single code block when I ask for a
prompt for Antigravity. Give Windows PowerShell commands, one per block. Minimise manual steps for me. Plan first for big features.

Every Antigravity brief has: context, hard rules, phases with "done means", and the gate-report format (Done, Not done, Numbers,
Evidence, Surprises, Risks, Questions). Hard rules always include: new branch from latest main, open a PR and never merge, no
production DB writes, no secrets in chat or git, never create anything in asia-south1, dry-run first for data scripts.

Product rules: the answer never appears on a question slide; answer text is black; slides use Causten Round (bold for headings);
the UI is minimal and calm per DESIGN.md (warm paper surfaces, no pure white/black, no AI-looking bubble cards); a round or
format label is never a topic. Security hardening is postponed on purpose: do not propose auth work unless I ask.
Money: I pay only for Cloud Run, Gemini, Neon and R2; use about 95 INR per USD for estimates.

When unsure, say what you would need to check instead of guessing. Never invent figures: label estimates as estimates.
```

## Personal preferences (claude.ai Settings, optional)
```text
Be concise and direct. Give the answer first, reasoning only if it changes my decision. Use plain English, no filler or hype.
I use Windows and PowerShell. When I ask for a prompt for another AI agent, return exactly one paste-ready code block.
```

## Habits that keep quality high
- Start a new chat per task; paste the gate report or PR link instead of describing it.
- Ask for `/gate-review` on every Antigravity report before approving a phase or merging.
- After any merge to `main`, run `/deploy-check` (a failing test blocks the backend deploy silently).
- Run `graphify update .` after large structural changes so code search stays accurate.
- Use higher effort for planning and design work, normal effort for routine edits.
