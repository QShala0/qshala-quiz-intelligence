"""One place that says which Gemini model each role uses.

Every role resolves to the same value it did before (settings.GEMINI_MODEL, which defaults to gemini-3.5-flash-lite;
the tagger and embedder keep their own settings). The optional GEMINI_MODEL_<ROLE> variables let one role be pointed
at a different model later without touching code; all are unset by default.
"""
import os
from typing import Any, Dict, Optional

DEFAULT_MODEL = "gemini-3.5-flash-lite"
EMBEDDING_MODEL = "gemini-embedding-001"


def _settings():
    from backend.app.config import settings
    return settings


def _role(env_name: str) -> str:
    override = (os.getenv(env_name) or "").strip()
    return override or (getattr(_settings(), "GEMINI_MODEL", None) or DEFAULT_MODEL)


def interpreter_model() -> str:
    return _role("GEMINI_MODEL_INTERPRETER")


def intent_model() -> str:
    return _role("GEMINI_MODEL_INTENT")


def curator_model() -> str:
    return _role("GEMINI_MODEL_CURATOR")


def tagger_model() -> str:
    return getattr(_settings(), "QSHALA_TAGGER_MODEL", None) or DEFAULT_MODEL


def models_in_use() -> dict:
    return {"interpreter": interpreter_model(), "intent": intent_model(), "curator": curator_model(),
            "tagger": tagger_model(), "embedding": EMBEDDING_MODEL}


def thinking_config_rest(model: str, level: str = "minimal") -> Optional[Dict[str, Any]]:
    """REST generationConfig.thinkingConfig for `model` ('thinkingLevel' on 3.x, 'thinkingBudget' on 2.5)."""
    lvl = (level or "minimal").lower()
    if any(tag in model for tag in ("3.1", "3.5", "3.6", "3.8", "4-")):
        return {"thinkingLevel": lvl.upper()}
    if "2.5" in model:
        return {"thinkingBudget": 128 if "pro" in model else (0 if lvl == "minimal" else 128)}
    return None
