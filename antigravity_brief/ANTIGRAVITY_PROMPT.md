# TASK FOR ANTIGRAVITY: bring the QShala web app fully on-brand (June 2026 Brand Guidelines)

You are the builder. The analysis is already done by a separate reviewer; do not re-derive it. Read, in this order:
1. `antigravity_brief/BRAND_SPEC.md`: the distilled rules from the official guideline (source of truth, wins over everything below).
2. `antigravity_brief/ASSET_MAP.md`: which brand file goes where.
3. `antigravity_brief/brand-tokens.css`: ready-to-paste tokens.
4. `CLAUDE.md` and `frontend/AGENTS.md` (this Next.js 16 has breaking changes; check `frontend/node_modules/next/dist/docs/` before using any API from memory).
Raw brand files live in `OneDrive_2026-11-24/Brand Elements/` (PDF guideline, `QT/` mascot art, `Rebranded Fonts/`). Never copy raw originals into the app (they are 4000+ px, 96 MB total); run `python antigravity_brief/prepare_brand_assets.py` to produce web-ready files.

## Situation (verified, do not re-verify)
- Branch `feat/qt-brand-foundation` is already merged into `main`. It added QT poses (`frontend/public/qt/*.webp`), Causten woff2 files (`frontend/src/fonts/`), `components/ui/*`, `app/design/page.tsx`.
- BUT the live theme drifts from the official guideline:
  - `layout.tsx` loads **Plus Jakarta Sans**; guideline says **Causten Round** (woff2 already exist but are unused for body).
  - `globals.css` + `docs/DESIGN.md` use muted colours (`#F8B42C`, `#5BB4DE`, soft ink `#232733`, "never pure black") and invented greens/reds. Official: Sky `#30B2E7`, Sun `#FDB913`, Green `#75B543`, Black `#000000`, cream page.
  - A hand-drawn SVG cat (`mascot/QCat.tsx`) exists. Official QT art must replace invented drawings.
  - Logo component is not the official wordmark lockup.
- Fix the drift. Do not invent new brand elements.

## Work packages (do in order, one commit each, on a new branch `feat/brand-guidelines-2026` off `main`)
1. **Assets**: run the prep script; confirm outputs in `frontend/public/brand/` (logos, favicon, og image) and `frontend/public/qt/` (curated, optimised QT SVG/WebP, see ASSET_MAP). Keep each file under ~150 KB; prefer SVG for mascot.
2. **Fonts**: load Causten Round (400/500/600/700/800/900 woff2 via `next/font/local`) as the only UI font. Headings = Causten Round Black (900) / ExtraBold; body = Regular/Medium. Remove Plus Jakarta Sans. Do not touch `backend/.../export` fonts (see "Do not touch").
3. **Tokens**: replace the palette block in `globals.css` with `brand-tokens.css`. Keep the existing Tailwind variable *names* (`--color-sun`, `--color-sky`, `--color-slate-*`, `--color-amber-*`, `--color-blue-*`) so no component breaks, but remap their values to the brand colours/tints. Add `--color-grass` (green). Re-map success to brand green; keep a red only for destructive/error (guideline shows red only as "wrong" marker; this is a functional exception, document it).
4. **Logo + chrome**: `ui/Logo.tsx` uses the official lockups (primary stacked, "Alt Stack" horizontal for sidebar/topbar, white version on coloured backgrounds). Respect clear space = height of the "Q" on all sides. Always show the ® mark. Never stretch, rotate, shadow, tint off-brand, or lower opacity. Update favicon/`icon.png`, metadata title/OG image.
5. **Mascot**: replace `QCat.tsx`/`YarnPlayCat.tsx` usages with official QT assets via `ui/QT.tsx` (extend its pose table from ASSET_MAP). Delete the hand-drawn cat once nothing imports it. Keep reduced-motion support and fixed dimensions (no layout shift).
6. **Screens**: restyle in this order: shell (Sidebar/Topbar), Home, Question Bank (`knowledge-base`), Generate, Quizzes, Uploads, Settings, 404/empty/loading/error states (use QT poses from ASSET_MAP), `StagePresenter` (live present mode: big, high contrast, brand-colour accents, cream/black). Voice and copy must follow BRAND_SPEC section 3.
7. **Docs**: rewrite `docs/DESIGN.md` to match the official guideline (palette, fonts, mascot rules, easter eggs table kept if still true). Update `app/design/page.tsx` as the living style guide.

## Hard constraints
- Do NOT change any behaviour, API call, data model, backend file, or test. This is visual/copy only.
- Do NOT touch the PPTX exporter, `qshala_school_master.pptx`, or fonts in `backend/`. (Open question for the owner: guideline names Mikado as the school primary font while the exporter uses Causten Round per the School Content Style Guide; leave as is.)
- Do not add auth/hardening work. Do not deploy, push, or open a PR; commit locally only and stop. The owner says "push it" when ready.
- Never use colours outside the 3 brand colours + black/white/cream (+ tints of them, + functional error red). No gradients that blend brand colours, no drop shadows on logo/mascot.
- Frontend lint rule `react-hooks/set-state-in-effect` is strict: add no new findings.

## Verification (required before you say done)
- `cd frontend && npx tsc --noEmit && npx eslint <changed files> && npm run build`
- Run the dev server and screenshot every route at 1440 px and 390 px wide; look at them. Check: no Plus Jakarta, no stray old colours (`grep -rn "F8B42C\|5BB4DE\|Plus_Jakarta" frontend/src` must return nothing), logo has ® and clear space, contrast of text on yellow/blue/green is acceptable (use black text on Sun and Sky; white only on dark or on Green at large sizes ≥ 24 px bold; check with a contrast calculator).
- Write `antigravity_brief/REPORT.md`: what changed, screenshots list, anything you could not do, any guideline ambiguity you resolved and how.
