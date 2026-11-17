# QShala Quiz Intelligence

Internal tool for quiz masters: ingest quiz decks (PPTX) into a question bank, search it, build quizzes from a prompt,
present them live, and export PPTX. Deep background: `QSHALA_HANDOFF.md`, `docs/DEPLOY.md`, `docs/BACKUPS.md`.
Read `graphify-out/GRAPH_REPORT.md` before exploring code (run `graphify update .` after big changes).

## Stack and where things run
- Backend: FastAPI + SQLAlchemy in `backend/app`. Cloud Run service `qshala-quiz-intelligence`, worker job
  `qshala-ingestion-worker` (GCP project `qshala-quiz`, region `asia-south1`). Prod DB is Neon Postgres; local is SQLite.
- Frontend: Next.js 16 (App Router, React 19, Tailwind v4) in `frontend/`, on Vercel. Read `frontend/AGENTS.md` first:
  this Next.js has breaking changes, check `node_modules/next/dist/docs/` before using an API from memory.
- Media in Cloudflare R2. Backups: `.github/workflows/backup.yml`, restore drill: `restore-drill.yml`.

## Deploying (important)
- Merging to `main` deploys only the **frontend** (Vercel). The **backend does not auto-deploy**: it goes to Cloud Run
  by hand (the owner uses Antigravity with a prompt). Say so whenever a change touches `backend/`.
- Work on the assigned feature branch, open a PR into `main`. Never force-push shared branches.

## Commands
- Backend tests: `python -m pytest backend/tests/<file> -q` (run the relevant files, the full suite is slow).
- Frontend: `cd frontend && npx tsc --noEmit && npx eslint <files>`. The lint rule `react-hooks/set-state-in-effect`
  is strict; some older files already have findings, only add none.
- Run locally: `cd frontend && npm run dev`; backend per `docs/dev_setup.md`.

## Domain rules (learned the hard way)
- **Question formats** carry a canonical `format_payload` with an `extraction` dict. A payload without it is ignored by
  every renderer and falls back to the merged question text. Canonical shapes:
  - STEPPED_CLUE: `clues:[{step,text,correct,wrong,images}]` + `scoring_ladder_ok`; one clue per slide everywhere.
  - CONNECT (type is `"CONNECT"`, never `CONNECT_4`): `items:[{order,image}]`, `text_hint`, `answer`; timer media
    (name contains "timer") is never a clue. Grid on one slide, answer on its own slide.
  - Frontend guards in `frontend/src/lib/formatPayloads.ts` must stay in sync with `backend/.../format_slides.py`.
- **Answer must never appear on a question slide.** Answer images are excluded from question images in the exporter.
- **Quiz builder** (`prompt_quiz/engine.py`) excludes `format_needs_review` questions.
- **Bank search** (`GET /questions`): `match=exact` is default (every typed word must appear, matched from the start of
  a word, deck names not matched); `match=smart` is the old semantic ranking. The UI highlights typed words.
- **School PPT export** (`backend/app/services/export`): built on `templates/qshala_school_master.pptx`; artwork lives in
  the slide master and layouts, never as a picture on a slide. Cover = Title Slide layout, Answer divider = Section
  Header, other slides = Blank. Regenerate the master with `backend/scripts/build_school_master.py`.
- **Fonts**: Causten Round only. Body `Causten Round` (standard weight), headings `Causten Round Bold`
  (cover title, Question/Answer N, Answer divider). Answer text is black, never green. Set latin+ea+cs on runs.
  OTF fonts cannot be embedded in PPTX, so the viewing machine needs them installed.
- Startup healers (`heal_specialized.py`) run on every boot and skip payloads that already have `extraction`.

## How the owner likes to work
- Plan first for bigger features, then implement. Keep answers short, minimize manual steps for the owner.
- Security hardening is postponed on purpose. Do not add auth or lock-down work unless asked.
- Verify before claiming done: run the tests, and for slides/UI render and look (LibreOffice `soffice` + `pdftoppm`,
  `pdffonts` to check which fonts are really used).
- Use graphify/targeted greps instead of reading whole files, to save tokens.

## Open backlog
Cloud Build auto-deploy trigger on `main` and disable the old Glennx1 triggers; cap Cloud Run at 1 instance; make the
startup repair scan one-off (it scans every question on each boot); check Neon transfer usage.
