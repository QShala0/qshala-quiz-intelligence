"""Gemini-powered Interpreter for the prompt-quiz engine.

Strict separation of concerns: this module ONLY turns a natural-language prompt
(optionally plus the previous Brief, when refining) into a structured JSON object.
It NEVER selects, ranks, or writes questions — retrieval stays 100% deterministic
in engine.py (Index.search / librarian / guardian / editor).

It is intentionally synchronous (the prompt-quiz route is a sync FastAPI endpoint
that runs in a worker thread) and dependency-light: one blocking httpx call with a
hard timeout. Any failure — missing key, timeout, HTTP error, malformed JSON —
returns None, and engine.py falls back to the regex parse_prompt() path. Nothing
here can fail the request.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Keep the timeout configurable; defaults to 8.0s per Prompt Engine v2.1 spec.
INTERPRETER_TIMEOUT_S = float(os.getenv("INTERPRETER_TIMEOUT_S", "8.0"))

# v6 L1 themes, surfaced to the model so topics_l1 comes back in our vocabulary.
V6_THEMES = [
    "Finance", "Business", "Economy", "Technology", "Science",
    "History", "Geography", "Politics", "Sports", "Entertainment", "Culture",
]

from backend.app.services.tagging.qshala_tagger import _load_taxonomy as _tax
try:
    _V6_L2 = _tax()["l2"]
except Exception:
    _V6_L2 = []

# OpenAPI-style schema (upper-case types) matching GeminiLLMProvider.generate_json_schema.
BRIEF_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "count": {"type": "INTEGER"},
        "query": {"type": "STRING"},
        "topics_l1": {"type": "ARRAY", "items": {"type": "STRING"}},
        "subtopics_l2": {"type": "ARRAY", "items": {"type": "STRING"}},
        "hard_bias": {"type": "STRING",
                      "enum": ["easy", "medium", "hard", "balanced", "spicy"], "nullable": True},
        "audience": {"type": "STRING",
                     "enum": ["school", "college", "corporate", "general"], "nullable": True},
        "india_focused": {"type": "BOOLEAN", "nullable": True},
        "evergreen": {"type": "BOOLEAN", "nullable": True},
        "no_images": {"type": "BOOLEAN", "nullable": True},
        "format_filter": {"type": "STRING", "enum": ["CONNECT_4", "STEPPED_CLUE", "ARRANGE_ORDER", "STANDARD_QA"], "nullable": True},
        "framing": {"type": "STRING"},
        "locality": {"type": "STRING", "nullable": True},
        "venue_type": {"type": "STRING", "nullable": True},
        "tone": {"type": "ARRAY", "items": {"type": "STRING"}},
        "school_safe": {"type": "BOOLEAN", "nullable": True},
        "cross_topic_padding": {"type": "BOOLEAN", "nullable": True},
        "search_terms": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "5-8 related search keywords/phrases a BM25 engine "
                           "should use to find questions on this topic. Include "
                           "synonyms, abbreviations, proper nouns, league/event "
                           "names. Do NOT include generic words like 'question' "
                           "or 'quiz'."
        },
    },
    "required": ["count", "query", "topics_l1", "framing"],
}

_L2_RULE = (
    f"- subtopics_l2: finer subtopics drawn ONLY from this list (return [] if none fit): {', '.join(_V6_L2)}.\n"
    if _V6_L2 else
    "- subtopics_l2: candidate finer subtopics in plain words (free text is fine).\n"
)

_SYSTEM = (
    "You are the Interpreter for a quiz-building tool. You translate a quiz master's "
    "natural-language request into a structured brief. You do NOT write, choose, or "
    "rank questions — a separate deterministic engine does that from a curated bank. "
    "Only output the brief.\n"
    "Rules:\n"
    "- count: how many questions they want (default 10 if unstated).\n"
    "- format_filter: 'CONNECT_4' if the user asks for connect 4 / common link questions, 'STEPPED_CLUE' if stepped clues / portfolio builder, 'ARRANGE_ORDER' if arrange / chronological order; otherwise null.\n"
    "- query: a short, clean semantic topic phrase for keyword search — strip filler "
    "like 'give me', 'questions', 'round', counts, and difficulty/audience words.\n"
    f"- topics_l1: zero or more themes drawn ONLY from this list: {', '.join(V6_THEMES)}. Must be copied from this list, never invented. For cross-cutting or entity prompts (e.g. important people, leaders, inventions, records) that cut across domains (science, business, history, politics, sports), leave topics_l1 as [] so questions across all categories can be retrieved.\n"
    + _L2_RULE +
    "- search_terms: 5-8 extra search keywords a text-match engine should "
    "use to find questions on this topic. Think synonyms, abbreviations, "
    "proper nouns (league names, event names, famous people). "
    "Example: query='cricket IPL' -> search_terms=['Indian Premier League', "
    "'T20', 'franchise cricket', 'BCCI', 'Mumbai Indians', 'CSK'].\n"
    "- hard_bias / audience: null unless the prompt clearly implies one.\n"
    "- school, kids, class 8, assembly -> audience=school, school_safe=true, evergreen=true, hard_bias=easy.\n"
    "- college, fest, hostel -> audience=college, hard_bias=medium.\n"
    "- corporate, offsite -> audience=corporate, hard_bias=medium.\n"
    "- a city or state -> locality set to it (the exact name, e.g. 'Bengaluru'); india_focused=true when clearly Indian.\n"
    "- cross_topic_padding=true only for phrases like 'or related', 'whatever you have', 'fill the rest'.\n"
    "- india, Indian, local -> india_focused=true; international, global, world -> india_focused=false; else null.\n"
    "- evergreen / no_images: true/false only if clearly implied, else null.\n"
    "- framing: a plain one-sentence read-back of the request (no question counts, "
    "no 'twenty athletic challenges'). Include the venue_type in the user's own words "
    "if they gave one. Example: 'A sports round for the engineering college fest in "
    "Bangalore, medium difficulty.' Never embellish or invent context.\n"
    "Never override anything explicit in the sentence.\n"
    "When a PREVIOUS BRIEF is given, apply the follow-up as an edit to it (e.g. 'make it "
    "harder' changes only hard_bias; 'give me 5 more' raises count; 'swap cricket for tech' "
    "changes query/topics) and return the full updated brief."
)


def _build_prompt(
    prompt: str,
    previous_brief: Optional[Dict[str, Any]],
    current_questions: Optional[List[Dict[str, Any]]] = None
) -> str:
    parts = []
    if previous_brief:
        slim = {k: previous_brief.get(k) for k in
                ("count", "query", "topics_l1", "subtopics_l2", "hard_bias",
                 "audience", "india_focused", "evergreen", "no_images",
                 "locality", "venue_type", "tone", "school_safe",
                 "cross_topic_padding", "format_filter", "search_terms")
                if previous_brief.get(k) not in (None, [], "")}
        parts.append("PREVIOUS BRIEF:\n" + json.dumps(slim, ensure_ascii=False))
    if current_questions:
        q_lines = []
        for i, q in enumerate(current_questions[:15]):
            idx = q.get("index") or (i + 1)
            topic = q.get("topic") or q.get("subtopic_l2") or q.get("topic_l1") or ""
            q_text = (q.get("question_text") or q.get("question") or "")[:90]
            q_lines.append(f"{idx}. [{topic}] {q_text}")
        if q_lines:
            parts.append("CURRENT QUESTIONS IN QUIZ:\n" + "\n".join(q_lines))
    if previous_brief or current_questions:
        parts.append("FOLLOW-UP REQUEST:\n" + (prompt or ""))
    else:
        parts.append("REQUEST:\n" + (prompt or ""))
    return "\n\n".join(parts)


def interpret_with_gemini(
    prompt: str,
    previous_brief: Optional[Dict[str, Any]] = None,
    current_questions: Optional[List[Dict[str, Any]]] = None,
    *,
    timeout_s: float = INTERPRETER_TIMEOUT_S,
) -> Optional[Dict[str, Any]]:
    """Return the Interpreter JSON dict, or None on any failure (caller falls back).

    Synchronous and self-contained: builds the same request shape as
    GeminiLLMProvider.generate_json_schema but with a blocking httpx client and a
    short timeout, so it is safe to call from a sync route without an event loop.
    """
    try:
        from backend.app.config import settings
    except Exception:
        return None

    key = (getattr(settings, "GEMINI_API_KEY", None) or "").strip()
    if (key.startswith('"') and key.endswith('"')) or (key.startswith("'") and key.endswith("'")):
        key = key[1:-1].strip()
    if not key:
        return None

    from backend.app.services.ai.model_config import interpreter_model
    model = interpreter_model()
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent")
    payload = {
        "contents": [{"parts": [{"text": _build_prompt(prompt, previous_brief, current_questions)}]}],
        "system_instruction": {"parts": [{"text": _SYSTEM}]},
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": BRIEF_SCHEMA,
            "temperature": 0.0,
        },
    }

    try:
        from backend.app.services.prompt_quiz._http import guarded_post
        headers = {"x-goog-api-key": key} if key else {}
        resp = guarded_post(url, json=payload, headers=headers, timeout=timeout_s)
        resp.raise_for_status()
        data = resp.json()
        cands = data.get("candidates") or []
        if not cands:
            return None
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = ""
        for part in reversed(parts):                 # final (non-thought) text part
            if "text" in part and not part.get("thought", False):
                text = part["text"]
                break
        if not text:
            for part in reversed(parts):
                if "text" in part:
                    text = part["text"]
                    break
        clean = (text or "").strip()
        if clean.startswith("```json"):
            clean = clean[7:]
        elif clean.startswith("```"):
            clean = clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
        parsed = json.loads(clean.strip())
        if isinstance(parsed, dict):
            # Snap invented topic names to the real v6 taxonomy
            try:
                from backend.app.services.prompt_quiz.topic_normalize import normalize_topics, dedupe_query
                from backend.app.services.tagging.qshala_tagger import _load_taxonomy
                tax = _load_taxonomy()
                valid_l1, valid_l2, dropped = normalize_topics(
                    parsed.get("topics_l1") or [],
                    parsed.get("subtopics_l2") or [],
                    l1_names=tax["l1"],
                    l2_names=tax["l2"],
                    l2_to_l1=tax["l2_to_l1"],
                )
                parsed["topics_l1"] = valid_l1
                parsed["subtopics_l2"] = valid_l2
                if parsed.get("query"):
                    parsed["query"] = dedupe_query(parsed["query"])
            except Exception:
                pass  # normalization is best-effort
            return parsed
        return None
    except Exception as e:
        logger.info("Gemini interpreter unavailable, falling back to regex parse_prompt: %s", type(e).__name__)
        return None
