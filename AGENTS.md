# Repository Rules & Git Workflow Guidelines

This document outlines mandatory guidelines for all AI agents and contributors working in this repository (`qshala`).

## 1. Branching & Git Discipline (Strictly Enforced)

1. **One Task, One Branch**:
   - **Never** commit directly onto `main` for new feature or bugfix development.
   - **Never** pile unrelated changes onto an existing topic or old fix branch (e.g. do not put duplicate resolution or tagging changes onto an older template branch).
   - Before starting any task, sync with `origin/main` and create a dedicated, descriptively named branch:
     - Features: `feat/<short-descriptive-name>` (e.g. `feat/duplicate-choice-selection`)
     - Fixes: `fix/<short-descriptive-name>` (e.g. `fix/question-bank-formatting`)
     - Chores/Refactors: `chore/<short-descriptive-name>`

2. **Commit Hygiene**:
   - Use Conventional Commits: `feat(...)`, `fix(...)`, `test(...)`, `refactor(...)`, `chore(...)`.
   - Keep commits atomic and self-contained with informative commit messages.
   - Never commit broken imports, missing modules, or unverified changes.

3. **Mandatory Pre-Merge Verification**:
   - Run tests and builds before merging into `main`:
     - **Frontend**: `npm --prefix frontend run build` (or `tsc --noEmit`) and unit tests (`npm --prefix frontend test -- --run`).
     - **Backend**: `pytest backend/tests/... -v`.
   - Both must pass completely before merging.

4. **Integration & Deployment**:
   - Push the topic branch to `origin`: `git push origin <branch-name>`.
   - Merge into `main` cleanly and push `origin main` to trigger live CI/CD deployments (Vercel & Cloud Run).

---

## 2. Engineering & Architecture Standards

1. **Database & Foreign Key Integrity**:
   - When modifying questions or canonical records, always preserve primary keys (`id` UUIDs) so external quiz references, deck pointers, and repeat provenance remain intact.
   - If consolidating duplicates, update the canonical record in place and clean up duplicate candidates cleanly.

2. **UI & API Consistency**:
   - Keep TypeScript types (`frontend/src/lib/types.ts`) synchronized with backend Pydantic schemas (`backend/app/schemas/`).
