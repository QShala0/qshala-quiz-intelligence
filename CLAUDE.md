# QShala Quiz Intelligence

Internal tool for quiz masters: ingest quiz decks (PPTX) into a question bank, search it, build quizzes from a prompt,
present them live, and export PPTX. Deep background: `QSHALA_HANDOFF.md`, `docs/DEPLOY.md`, `docs/BACKUPS.md`.
Read `graphify-out/GRAPH_REPORT.md` before exploring code (run `graphify update .` after big changes).

## Stack and where things run
- Backend: FastAPI + SQLAlchemy in `backend/app`. Cloud Run service `qshala-quiz-intelligence`, worker job
  `qshala-ingestion-worker` (GCP project `qshala-quiz`, region `asia-southeast1` Singapore only; the Mumbai `asia-south1`
  copy was deleted on 19 Nov 2026, never create anything there). Prod DB is Neon Postgres; local is SQLite.
- Cloud Run sizing (set by hand, the pipeline never changes it): service 4Gi, 2 vCPU, max 1 instance, CPU only during requests;
  owner decided on 21 Nov 2026 to keep 1 instance warm (min 1). Job 8Gi, 3600 s. Verify live values with
  `gcloud run services describe` before relying on them. A cold start (min 0) took about 16 s.
- A SharePoint import is split: the **service** downloads each file from Microsoft Graph and stages it in R2
  (`raw-uploads/<doc_id><ext>`); the **job** ingests it. Since PR 30 (21 Nov 2026) the service streams each file to R2 as a
  multipart upload (`stream_item_to_r2`, parts of `SHAREPOINT_PART_MB`=128, one part uploading while the next fills), so memory
  per file is about two parts, not the file size. `SHAREPOINT_MAX_INFLIGHT`=4 files download at once, `SHAREPOINT_BATCH_MAX`=25
  files per import batch (the UI says "N of M selected, import the rest in the next batch"). All three are Cloud Run env vars,
  tunable without a deploy. Known gaps: two identical files in the same batch can both get ingested; an R2 lifecycle rule to
  abort incomplete multipart uploads after 1 day is wanted.
- Frontend: Next.js 16 (App Router, React 19, Tailwind v4) in `frontend/`, on Vercel. Read `frontend/AGENTS.md` first:
  this Next.js has breaking changes, check `node_modules/next/dist/docs/` before using an API from memory.
- Media in Cloudflare R2. Backups: `.github/workflows/backup.yml`, restore drill: `restore-drill.yml`.

## Deploying (important)
- Merging to `main` deploys the **frontend** (Vercel) and, through the Cloud Build trigger `qshala-test-gated-deploy`
  (`deploy/cloudbuild.yaml`), the **backend**: it runs the FULL `python -m pytest backend/tests`, builds one image, deploys the
  service and updates the job to the same image. **Any failing test blocks the backend deploy** and nothing says so loudly
  (a stale health-check test kept Singapore on an old build for two days). After pushing backend changes, check the build.
  Never deploy with `gcloud builds submit` from a local folder: it skips the tests and includes untracked files.
- Settings and env vars set by hand on Cloud Run survive deploys (only the image changes). Some tests fail on the owner's
  machine because of the local `.env` (e.g. `test_qa_tournament_overhaul`, `test_require_postgres_refuses_to_boot_on_sqlite`)
  but pass in the gate; the gate is the authority.
- Work on the assigned feature branch, open a PR into `main`. Never force-push shared branches.
- **Cloud Run Environment Variables & Secrets**: Every deployed service in `asia-southeast1` MUST have:
  - Core: `DATABASE_URL` (Neon secret), `GEMINI_API_KEY` (secret), `R2_ACCOUNT_ID`, `R2_BUCKET_NAME`, `R2_PUBLIC_BASE_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`.
  - SharePoint / Azure AD: `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` (mounted from Secret Manager `azure-client-secret:latest`), `SHAREPOINT_SITE_ID`, `SHAREPOINT_ROOT_FOLDER="Quizzes"`, `SHAREPOINT_DRIVE_ID`, `SHAREPOINT_MAX_FILE_MB="1024"`. If migrating or creating services, verify with `/health` checks.
  - Auth (PR 1-4): `AUTH_ENFORCED` (default off in repo, soft mode), `AUTH_INITIAL_ADMINS` (comma list email:Team), `AUTH_SEED_CONSULTANT_PASSWORD` (default pass123), `AUTH_ALLOWED_EMAIL_DOMAINS` (default walnuts.co.in), `AUTH_PUBLIC_BASE_URL`, `AUTH_MS_CLIENT_ID`, `AUTH_MS_CLIENT_SECRET`. Turn `AUTH_ENFORCED=on` on Cloud Run only after PR 4 verification. All data stays shared between everyone; login identifies people and blocks outsiders. Cookie `qs_session` carries raw 32-byte token (7d sliding, 30d absolute ceiling). Mutating cookie requests enforce CSRF Origin check (exempt with valid `X-API-Key`). Only Microsoft accounts can be admin. Last active admin protected from demote/block/delete. Seed runs idempotently on startup.

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
    (name contains "timer") is never a clue. Export is a loop on one slide (pictures on a rounded-rectangle line, hint in a
    box on the line, "N. Connect" in the middle; geometry in `export/connect_layout.py`, per-template safe boxes and theme in
    `exporter.py` `_connect_theme`); more than 10 pictures fall back to the plain grid. Answer on its own slide.
  - Frontend guards in `frontend/src/lib/formatPayloads.ts` must stay in sync with `backend/.../format_slides.py`.
- **Answer must never appear on a question slide.** Answer images are excluded from question images in the exporter.
- **Quiz builder** (`prompt_quiz/engine.py`) excludes `format_needs_review` questions.
- **Bank search** (`GET /questions`): `match=exact` is default (every typed word must appear, matched from the start of
  a word, deck names not matched); `match=smart` is the old semantic ranking. The UI highlights typed words.
- **School PPT export** (`backend/app/services/export`): built on `templates/qshala_school_master.pptx`; artwork lives in
  the slide master and layouts, never as a picture on a slide. Cover = Title Slide layout, Answer divider = Section
  Header, other slides = Blank. Regenerate the master with `backend/scripts/build_school_master.py`.
- **Fonts**: Causten Round only. Body `Causten Round` (standard weight), headings `Causten Round Bold`
  (cover title, Question N, Answer divider; answer slides carry only the answer). Answer text is black, never green. Set latin+ea+cs on runs.
  OTF fonts cannot be embedded in PPTX, so the viewing machine needs them installed.
- Startup repair passes (legacy backfill, bilingual repair, `heal_specialized.py`) are OFF on Cloud Run by default
  (`startup_maintenance_mode()` in `database.py`). To run them once, set `STARTUP_MAINTENANCE_MODE=background` for one
  deploy, then remove it. They skip payloads that already have `extraction`.
- **Topics**: a deck's round or format label ("THREE CLUES", "Portfolio Builder") is never a topic and the format name is never
  a tag (`is_format_or_round_label` in `qshala_rules.py`); the topic comes from the answer. Repair scripts in `backend/scripts`
  (`repair_media_questions.py`, `repair_three_clue_questions.py`, `fix_picture_sides.py`, `strip_video_posters.py`,
  `retag_answer_led.py`, `backfill_embeddings.py`, `repair_complex_formats.py`, `fix_format_only_tags.py`) are dry-run by default, take `--apply`, and write a rollback file outside the repo.

## How the owner likes to work
- Plan first for bigger features, then implement. Keep answers short, minimize manual steps for the owner.
- Security hardening is postponed on purpose. Do not add auth or lock-down work unless asked.
- Verify before claiming done: run the tests, and for slides/UI render and look (LibreOffice `soffice` + `pdftoppm`,
  `pdffonts` to check which fonts are really used).
- Use graphify/targeted greps instead of reading whole files, to save tokens.

## Brain and worker (how work flows)
- Claude Code is the **brain**: analysis, plans, briefs, review, small direct fixes. Antigravity is the **worker** that implements
  briefs; its rules are in `ANTIGRAVITY.md` (it wins on process, this file wins on domain rules).
- A brief to Antigravity always has: context, hard rules, phases, and the gate-report format (Done, Not done, Numbers, Evidence,
  Surprises, Risks, Questions). Use `/brief`. Review its gate report with `/gate-review` before approving a phase or merging.
- Never trust a report alone: open the PR, read CI, and check "real" evidence is not mocked (token counts, row samples).
- Merging to `main` is the owner's call. Ask first, then merge, then check the Cloud Build with `/deploy-check`.
- Slides use Causten Round; the UI follows the tokens in `docs/DESIGN.md`. Commits: `feat(scope):`, `fix(scope):`, `docs:`, `test:`.
- This repo is **public**: never commit keys, emails, tokens or customer data, not even in docs or `.claude/`.

## Cost ledger (`backend/app/services/costs`, `cost_*` tables)
- Tracking must never break ingestion or quizzes: every write is fail-safe, in its own session, off the request path.
- Ingestion cost rows use `cost_scope` with a per-run id in the dedupe key, so re-ingesting a deck records again.
- Sources: Gemini tokens measured from usage metadata; Cloud Run from Cloud Monitoring `billable_instance_time`; fixed costs
  are accruals until a real invoice (`POST /costs/invoices`) supersedes them. Do not label inferred numbers "measured".
- Owner pays only for Cloud Run, Gemini, Neon and R2 (no Vercel Pro, no other GCP services). Rate: about 95 INR per USD.

## Open backlog
- After PR 30 deploys: do a real import of about 25 decks and watch Cloud Run peak memory; add the R2 lifecycle rule for
  incomplete multipart uploads; decide whether duplicate files inside one batch need a guard.
- Verify the Singapore service setting (4Gi, min 1) and watch the monthly Cloud Run cost against the Google bill.
- Check Neon data transfer usage; set a Google billing budget alert.
