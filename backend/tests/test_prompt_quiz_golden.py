"""Guards the prompt engine's output. Runs the seeded, no-network golden harness and compares it with the committed snapshot.

If this fails, the engine now picks different questions (or says different things) for the same prompts. If that is
intended, re-record deliberately and say why in the commit:
    python backend/scripts/prompt_quiz_golden.py . record backend/eval_data/prompt_quiz_golden.json
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "backend" / "eval_data" / "prompt_quiz_golden.json"
SCRIPT = ROOT / "backend" / "scripts" / "prompt_quiz_golden.py"


def _run(extra_env=None):
    env = {**os.environ, **(extra_env or {})}
    return subprocess.run([sys.executable, str(SCRIPT), str(ROOT), "compare", str(SNAPSHOT)],
                          capture_output=True, text=True, env=env, timeout=300)


def test_engine_output_matches_golden_snapshot():
    r = _run()
    assert r.returncode == 0 and "IDENTICAL" in r.stdout, r.stdout[-1500:] + r.stderr[-500:]


def test_engine_output_matches_golden_snapshot_through_the_bank_cache():
    r = _run({"GOLDEN_CACHED": "1", "PROMPT_QUIZ_BANK_TTL_S": "600"})
    assert r.returncode == 0 and "IDENTICAL" in r.stdout, r.stdout[-1500:] + r.stderr[-500:]
