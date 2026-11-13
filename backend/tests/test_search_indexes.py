"""The trigram index definitions must stay in sync with each other and with the SQL the search endpoint generates."""
import importlib.util
import sys
from pathlib import Path

from sqlalchemy import String, func
from sqlalchemy.dialects import postgresql

ROOT = Path(__file__).resolve().parents[2]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_script_and_migration_define_the_same_indexes():
    script = _load(ROOT / "backend/scripts/add_search_indexes.py", "add_search_indexes")
    mig = _load(ROOT / "backend/alembic/versions/0010_search_trigram_indexes.py", "mig0010")
    assert script.INDEXES == mig.INDEXES
    assert mig.down_revision == "0009_format_needs_review_flag"


def test_tags_index_expression_matches_the_query_expression():
    from backend.app.models.question import Question
    sql = str(func.cast(Question.tags, String).ilike("%x%").compile(dialect=postgresql.dialect()))
    assert "CAST(questions.tags AS VARCHAR)" in sql
    script = _load(ROOT / "backend/scripts/add_search_indexes.py", "add_search_indexes2")
    assert any("CAST(tags AS VARCHAR)" in expr for _n, _t, expr in script.INDEXES)


def test_statements_are_idempotent_and_concurrent():
    script = _load(ROOT / "backend/scripts/add_search_indexes.py", "add_search_indexes3")
    stmts = list(script.statements())
    assert stmts[0].startswith("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    assert all("CONCURRENTLY IF NOT EXISTS" in s for s in stmts[1:])
