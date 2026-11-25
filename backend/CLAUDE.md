# Backend rules (FastAPI + SQLAlchemy, `backend/app`)

Root `CLAUDE.md` rules apply too. This file only adds what matters when editing backend code.

## Running things
- Use the repo venv from the repo root: `venv/Scripts/python.exe -m pytest backend/tests/<file> -q`, `PYTHONPATH=.` for scripts.
- On Windows set `PYTHONIOENCODING=utf-8` before printing rupee signs or non-ASCII.
- Tests use a temp SQLite via `backend/tests/conftest.py`; a test must never delete shared rows (a fixture that wipes `Document`
  breaks unrelated search tests). Scope cleanup to the ids the test created.
- The full gate (`python -m pytest backend/tests`) decides the deploy. Run the full suite before opening a PR that touches shared code.

## Database
- Prod is Neon Postgres, local is SQLite. The app silently falls back to SQLite if Neon is unreachable, so scripts that touch prod
  must set `REQUIRE_POSTGRES=1`, assert the postgres dialect, and use `SET TRANSACTION READ ONLY` unless the owner approved writes.
- New ORM tables are created on boot by `_ensure_schema()` (`create_all`); an Alembic revision is still added for history.
- Scripts that change data are dry-run by default, take `--apply`, and write a rollback file outside the repo.

## Gemini calls
- Every call sets `maxOutputTokens` and an explicit thinking budget; classification and curation use `thinkingBudget: 0`.
- Record usage through the cost scope; never let recording raise into the caller.
- Prefer a rule-based path over a new LLM call. Prove any prompt trim leaves output unchanged (see the token benchmark tests).

## Gotchas
- Some files use `\r\r\n` line endings; if an edit will not match, check with `cat -A` and replace at byte level.
- `format_payload` needs an `extraction` dict; keep `format_slides.py` in sync with `frontend/src/lib/formatPayloads.ts`.
- Never create anything in `asia-south1`; Cloud Run lives only in `asia-southeast1`.
