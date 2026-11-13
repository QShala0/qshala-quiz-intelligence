# Backend dependencies

- `requirements.txt` — the packages the app needs, with loose lower bounds. Edit this when you add a package.
- `requirements.lock.txt` — the exact versions the Docker image installs (Python 3.12). Generated, do not edit by hand.
- `requirements-dev.in` / `requirements-dev.lock.txt` — the same plus pytest and ruff; CI installs this one.

A rebuild therefore gets the same versions every time instead of whatever is newest that day.

## Upgrading (do this on purpose, not by accident)

```bash
pip install uv
cd backend
uv pip compile requirements.txt --python-version 3.12 --python-platform linux -o requirements.lock.txt
uv pip compile requirements.txt requirements-dev.in --python-version 3.12 --python-platform linux -o requirements-dev.lock.txt
# to bump just one package:  add  --upgrade-package <name>  to both commands
git diff requirements.lock.txt        # read what changed
```

Then run the full test suite on Python 3.12 and the golden check before merging:
`python -m pytest backend/tests -q` and `python backend/scripts/prompt_quiz_golden.py . compare backend/eval_data/prompt_quiz_golden.json`.
