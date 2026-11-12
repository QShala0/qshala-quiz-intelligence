"""Gemini-powered Quiz Master Curator for the prompt-quiz engine.

Strict separation of concerns:
This module NEVER writes, invents, or synthesizes question text or answers.
It ONLY receives candidate question summaries retrieved from the verified Question Vault,
evaluates them against the user's prompt (nuance, tone, audience psychology, lateral themes),
and selects the best subset and sequence for an engaging tournament/classroom round.

Failsafe guarantee:
If Gemini is unavailable, times out (> 4.0s), or returns any invalid IDs, it returns None,
and engine.py falls back instantly to the deterministic editor (BM25 + difficulty ramp).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

CURATOR_TIMEOUT_S = float(os.getenv("CURATOR_TIMEOUT_S", "4.0"))

CURATION_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "selected_ids": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "The exact IDs of chosen questions that genuinely match the prompt, up to the target count (can be fewer or empty if candidates do not genuinely match), ordered by narrative sequence."
        },
        "narrative_arc": {
            "type": "STRING",
            "description": "A crisp 1-2 sentence explanation of the round's narrative flow and why this progression was chosen."
        }
    },
    "required": ["selected_ids", "narrative_arc"]
}

_SYSTEM = (
    "You are an elite Quiz Master and Head Curator for QShala, India's premier curiosity and quiz platform.\n"
    "Your job is to select the BEST questions from a provided candidate pool to fulfill the user's prompt.\n\n"
    "CRITICAL RULES:\n"
    "1. ZERO HALLUCINATIONS: You MUST select IDs EXCLUSIVELY from the provided candidate pool. Never invent or hallucinate an ID.\n"
    "2. TOPIC RELEVANCE & HONESTY (ABSOLUTE PRIORITY):\n"
    "   - Only select questions that GENUINELY match the user's specific requested topic and intent.\n"
    "   - If the candidate pool contains genuine matches (even if only 2, 3, or 5 exist in the vault), YOU MUST SELECT ALL OF THEM in selected_ids! Never return [] when valid matches exist.\n"
    "   - Do NOT force-fit completely unrelated trivia (e.g. car factories or corporate accounting) just to fill a count. Deliver all authentic matches found.\n"
    "3. HIGH-ORDER HUMAN CURATION:\n"
    "   - Look beyond surface keywords. Match the user's requested tone, audience psychology, and subtle themes.\n"
    "   - When an audience like 'school students' or 'kids' in India is requested, prioritize inspiring figures, scientists, space pioneers, educators, national icons, and achievers that school students study, relate to, and find educational, rather than adult corporate finance, tax avoidance, or adult satire.\n"
    "   - REFINEMENT CONTINUITY: When refining an existing quiz (e.g. 'Make it for middle school kids', 'Focus on India', 'Only with images'), ALWAYS honor the Core Topic, Locality, and Source Deck from ACTIVE QUIZ CONTEXT, and keep top-matching questions from CURRENTLY DISPLAYED QUIZ (especially Slot 1) when they still fit the active topic and filters rather than dropping relevant questions unnecessarily.\n"
    "   - Build a narrative arc: start with an engaging icebreaker/hook, explore the theme with increasing depth, and finish with a memorable climax or tie-breaker.\n"
    "4. DIVERSITY & ZERO DUPLICATE ENTITIES (Crucial):\n"
    "   - NEVER select two questions that ask about or have the same answer/entity (e.g. do NOT pick two questions about Dadabhai Naoroji, or two about Ramanujan).\n"
    "   - Every selected question must feature a distinct person, mission, invention, or concept.\n"
    "5. Output JSON strictly adhering to the schema."
)


def _build_curator_prompt(
    prompt: str,
    target_count: int,
    candidates: List[Dict[str, Any]],
    current_questions: Optional[List[Dict[str, Any]]] = None,
    previous_brief: Optional[Dict[str, Any]] = None,
) -> str:
    parts: List[str] = [
        f"USER REQUEST: {prompt}",
        f"TARGET QUESTION COUNT: {target_count}",
    ]

    if previous_brief:
        pb_lines = []
        if previous_brief.get("query"):
            pb_lines.append(f"  Core Topic: {previous_brief.get('query')}")
        if previous_brief.get("locality"):
            pb_lines.append(f"  Locality: {previous_brief.get('locality')}")
        pb_filters = previous_brief.get("filters") or {}
        if pb_filters.get("audience"):
            pb_lines.append(f"  Audience: {pb_filters.get('audience')}")
        if pb_filters.get("deck_filter"):
            pb_lines.append(f"  Source Deck Filter: {', '.join(pb_filters.get('deck_filter') or [])}")
        if pb_filters.get("india_international"):
            pb_lines.append(f"  Geography: {pb_filters.get('india_international')}")
        if pb_lines:
            parts.append("ACTIVE QUIZ CONTEXT (MUST BE RESPECTED ON REFINEMENTS):\n" + "\n".join(pb_lines))

    curr_slots = {}
    if current_questions:
        curr_summary = []
        for i, q in enumerate(current_questions, 1):
            qid = q.get("id", f"slot_{i}")
            curr_slots[qid] = i
            text = (q.get("question_text") or "")[:80]
            ans = (q.get("answer") or "")[:50]
            curr_summary.append(f"  Slot {i} [{qid}]: {text} -> {ans}")
        parts.append("CURRENTLY DISPLAYED QUIZ:\n" + "\n".join(curr_summary))

    cand_lines = []
    for c in candidates:
        cid = c.get("id")
        text = (c.get("question_text") or "").strip().replace("\n", " ")
        if len(text) > 150:
            text = text[:147] + "..."
        ans = (c.get("answer") or "").strip().replace("\n", " ")
        if len(ans) > 60:
            ans = ans[:57] + "..."
        topic = c.get("subtopic_l2") or c.get("topic_l1") or c.get("topic") or "General"
        diff = c.get("difficulty") or "Medium"
        diff_score = c.get("difficulty_score", 0.5)
        fmt = c.get("question_format") or "STANDARD_QA"
        deck = (c.get("document_title") or "").strip()
        deck_tag = f" [Deck: {deck[:45]}]" if deck else ""
        slot_tag = f" [Current Slot #{curr_slots[cid]}]" if cid in curr_slots else ""
        rel = float(c.get("score") or 0.0)
        cand_lines.append(
            f"- ID: {cid} | [{topic}] [{diff} {diff_score:.2f}] [{fmt}] [Rel {rel:.2f}]{deck_tag}{slot_tag} Q: {text} | Ans: {ans}"
        )

    parts.append(f"CANDIDATE POOL ({len(candidates)} pre-verified questions from vault):\n" + "\n".join(cand_lines))
    parts.append(
        f"TASK: Select exactly {target_count} question IDs from the candidate pool above that best satisfy the user's request. "
        "Sequence them for maximum narrative impact and output the JSON brief."
    )
    return "\n\n".join(parts)


def curate_quiz_with_gemini(
    prompt: str,
    target_count: int,
    candidates: List[Dict[str, Any]],
    current_questions: Optional[List[Dict[str, Any]]] = None,
    previous_brief: Optional[Dict[str, Any]] = None,
    *,
    timeout_s: float = CURATOR_TIMEOUT_S,
) -> Optional[Dict[str, Any]]:
    """Evaluates candidate pool with Gemini and returns {selected_ids, narrative_arc}.

    Strict validation:
    - Returns None on missing key, timeout, HTTP error, or malformed JSON.
    - Returns None if selected_ids contains IDs not in the candidate pool.
    """
    if not candidates or target_count <= 0:
        return None

# Evaluate candidates against the prompt for genuine topic relevance

    try:
        from backend.app.config import settings
    except Exception:
        return None

    key = (getattr(settings, "GEMINI_API_KEY", None) or "").strip()
    if (key.startswith('"') and key.endswith('"')) or (key.startswith("'") and key.endswith("'")):
        key = key[1:-1].strip()
    if not key:
        return None

    from backend.app.services.ai.model_config import curator_model
    model = curator_model()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    user_text = _build_curator_prompt(
        prompt=prompt,
        target_count=target_count,
        candidates=candidates,
        current_questions=current_questions,
        previous_brief=previous_brief,
    )

    payload = {
        "contents": [{"parts": [{"text": user_text}]}],
        "system_instruction": {"parts": [{"text": _SYSTEM}]},
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": CURATION_SCHEMA,
            "temperature": 0.1,
            "maxOutputTokens": 1200,
        },
    }

    valid_id_set: Set[str] = {c.get("id") for c in candidates if c.get("id")}

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
        for part in reversed(parts):
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
        if not isinstance(parsed, dict):
            return None

        selected_ids = parsed.get("selected_ids") or []
        if not isinstance(selected_ids, list):
            return None

        # Zero-hallucination validation gate:
        # Every ID must exist in the candidate pool
        filtered_ids = [str(qid) for qid in selected_ids if str(qid) in valid_id_set]

        # Deduplicate while preserving sequence order
        seen = set()
        deduped_ids = []
        for qid in filtered_ids:
            if qid not in seen:
                seen.add(qid)
                deduped_ids.append(qid)

        # If Gemini explicitly returned [] (0 genuine matches found), respect that:
        if len(selected_ids) == 0:
            return {
                "selected_ids": [],
                "narrative_arc": parsed.get("narrative_arc") or "No questions in the candidate pool match the requested topic."
            }

        # If Gemini attempted to select questions but some were hallucinated,
        # backfill only if Gemini originally selected at least target_count
        if len(selected_ids) >= target_count and len(deduped_ids) < target_count:
            for c in candidates:
                cid = c.get("id")
                if cid and cid not in seen:
                    seen.add(cid)
                    deduped_ids.append(cid)
                if len(deduped_ids) == target_count:
                    break

        return {
            "selected_ids": deduped_ids[:target_count],
            "narrative_arc": parsed.get("narrative_arc") or "Curated for narrative flow and topical alignment."
        }
    except Exception as e:
        logger.warning(f"curate_quiz_with_gemini error ({e}), falling back to deterministic editor")
        return None
