"""Chunked, review-gated batch tagging for deck ingestion.

Why this exists
---------------
The per-question tagger (`QshalaTagger.tag`) is correct but makes one Gemini call per question with
rate-limit spacing, which dominates ingestion time. A naive "one call for the whole deck" fixes the
speed but overflows Gemini's output-token ceiling on a large deck (the JSON array is truncated and
`json.loads` throws), which is the failure that stalled ingestion partway through.

This module sends one Gemini call per *chunk* of questions (default 12), so each response stays well
under the token ceiling. Every returned item is run through the SAME `_route()` confidence gate the
single-question path uses, so low-confidence / weak-fit results still land in `draft` (the review
queue) exactly as before. A chunk that fails, times out, or returns short does not abort the deck:
its questions are tagged `draft` with a reason and ingestion continues — the pipeline always reaches
a terminal state.

Quiz-master feedback loop
-------------------------
`recent_human_exemplars()` pulls the most recent HUMAN-confirmed tags and injects them into each
chunk prompt as worked examples. Every correction a quiz master makes in the review UI therefore
improves tagging on the next deck, with no training step.

This module reuses the tagger's taxonomy, prompt, schema, routing and path-normalisation helpers;
it does not duplicate that logic.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.app.models.qshala_tag import QshalaTag, QshalaTagChange
from backend.app.models.question import Question
from backend.app.services.tagging import qshala_rules, topic_paths
from backend.app.services.tagging.qshala_tagger import (
    TAXONOMY_VERSION, GEOGRAPHY_V6, _system_prompt, _tag_schema_v6,
    _v6_paths, _route, _clean_year, safe_error, is_rate_limit, retry_after_seconds,
)

logger = logging.getLogger(__name__)

# Questions per Gemini call. Each question's answer is only ~200 output tokens, so 20 per call stays far below
# the output ceiling while sending the (large, identical) taxonomy prompt 40% fewer times than 12 per call.
# Any question the model skips in a chunk is re-asked on its own (see run_chunk), never silently dropped.
CHUNK_SIZE = int(os.getenv("QSHALA_TAG_CHUNK_SIZE", "20"))
# Chunks in flight at once. 8 assumes a paid-tier Gemini key (billing linked) -- on the free tier,
# drop this back down via the env var. Retry/backoff below (MAX_CHUNK_RETRIES, is_rate_limit) still
# protects against a 429 if this is set higher than the account's actual RPM allows.
CHUNK_CONCURRENCY = int(os.getenv("QSHALA_TAG_CHUNK_CONCURRENCY", os.getenv("CHUNK_CONCURRENCY", "8")))
# A chunk slower than this is abandoned and its questions go to review. It must be longer than the HTTP timeout of
# a single Gemini request (90 s): the old 75 s limit gave up on a request that was still legitimately running (and
# still being billed) and then re-sent the whole chunk.
CHUNK_TIMEOUT_S = float(os.getenv("QSHALA_TAG_CHUNK_TIMEOUT_S", "120"))
MAX_CHUNK_RETRIES = 3


def _batch_schema() -> Dict[str, Any]:
    """A list of per-question v6 payloads, each echoing its input index `n`."""
    item = _tag_schema_v6()
    props = dict(item["properties"])
    props["n"] = {"type": "INTEGER"}
    item = {**item, "properties": props, "required": ["n"] + list(item.get("required", []))}
    return {"type": "OBJECT",
            "properties": {"results": {"type": "ARRAY", "items": item}},
            "required": ["results"]}


# --------------------------------------------------------------------- feedback loop

def recent_human_exemplars(db: Session, limit: int = 8) -> List[Dict[str, str]]:
    """Most recent HUMAN-confirmed tags as compact worked examples. These are what quiz masters
    produced by clarifying/accepting in the review UI; reusing them teaches the model this vault's
    judgement. One indexed query; the caller reads it once per deck."""
    try:
        rows = (db.query(QshalaTag, Question.question_text)
                .join(Question, Question.id == QshalaTag.question_id)
                .filter(QshalaTag.source == "HUMAN", QshalaTag.status == "confirmed",
                        QshalaTag.topic_l1.isnot(None))
                .order_by(QshalaTag.created_at.desc())
                .limit(limit).all())
    except Exception as e:                       # never let the feedback query break ingestion
        logger.warning("exemplar query failed (continuing without): %s", safe_error(e))
        return []
    out: List[Dict[str, str]] = []
    for tag, qtext in rows:
        q = (qtext or "").strip().replace("\n", " ")
        if q:
            out.append({"q": q[:200], "theme": tag.topic_l1 or "", "subtopic": tag.subtopic_l2 or ""})
    return out


def _exemplar_block(exemplars: List[Dict[str, str]]) -> str:
    if not exemplars:
        return ""
    lines = ["QUIZ-MASTER-VERIFIED EXAMPLES (match this judgement; do not quote them back):"]
    for e in exemplars:
        lines.append(f'- "{e["q"]}" -> theme: {e["theme"]} | subtopic: {e["subtopic"]}')
    return "\n".join(lines)


def _batch_user_prompt(items: List[Dict[str, Any]], exemplars: List[Dict[str, str]]) -> str:
    parts: List[str] = []
    ex = _exemplar_block(exemplars)
    if ex:
        parts.append(ex)
    parts.append(f"Tag the following {len(items)} questions. Return `results` with one object per "
                 f"question, each carrying its `n` (the number shown). Tag every question; never skip one.")
    for it in items:
        block = [f"[n={it['n']}] QUESTION: {it['question_text']}", f"ANSWER: {it['answer']}"]
        if it.get("visual_clues"):
            block.append(f"VISUAL CLUES: {it['visual_clues'][:400]}")
        rules = it["rules"]
        if rules.brand_hints_l1:
            block.append(f"Rule hint: likely theme(s) {', '.join(rules.brand_hints_l1)}.")
        if rules.image_dependent:
            block.append("Rule hint: may depend on an unseen image; tag from text, set fit=weak if unsure.")
        parts.append("\n".join(block))
    return "\n\n".join(parts)


# --------------------------------------------------------------------- writing results

def _write_item(db: Session, q: Dict[str, Any], llm_out: Dict[str, Any], model: str,
                latency_ms: int, actor: str, usage: Optional[Dict[str, int]] = None) -> QshalaTag:
    """Persist one routed result. Mirrors QshalaTagger._finish_v6 so draft/confirmed behaviour and
    topic-path writing are identical to the single-question path."""
    rules = q["rules"]
    paths = _v6_paths(llm_out)
    first = paths[0] if paths else {}
    routed = {"fit": llm_out.get("fit", "weak"),
              "india_international": llm_out.get("india_international"),
              "subtopic_l2": first.get("l2")}
    status, confidence, reason = _route(rules, routed)
    if not paths:
        status, confidence, reason = "draft", 0.30, "model returned no usable topic"
    geo = llm_out.get("india_international")

    existing = (db.query(QshalaTag)
                .filter(QshalaTag.status == "confirmed", QshalaTag.question_id == q["question_id"])
                .order_by(QshalaTag.created_at.desc()).first())
    if existing and existing.source == "HUMAN":
        return existing                           # never re-tag over a human-confirmed row

    row = QshalaTag(
        question_id=q["question_id"], taxonomy_version=TAXONOMY_VERSION, model_version=model,
        source="LLM", topic_l1=first.get("l1"), subtopic_l2=first.get("l2"),
        keywords_l3=first.get("keywords", []),
        secondary_l2=[p["l2"] for p in paths[1:] if p.get("l2")],
        city_name=rules.city_name,
        geo_region=(llm_out.get("geo_location") or "").strip() or rules.geo_region,
        india_international=geo if geo in GEOGRAPHY_V6 else "NA",
        relevant_year=_clean_year(llm_out.get("relevant_year")),
        fit=llm_out.get("fit"), search_summary=llm_out.get("search_summary"),
        confidence=confidence, status=status, latency_ms=latency_ms,
        # A chunk's token usage is split evenly across the questions it returned, so per-row figures are
        # an average share; summed over a chunk they equal what Gemini billed for that call.
        tokens_in=(usage or {}).get("in"), tokens_out=(usage or {}).get("out"),
        thinking_tokens=(usage or {}).get("think"), cached_tokens=(usage or {}).get("cached"),
        verification_notes=f"auto-routed (batch): {reason}",
    )
    db.add(row)
    db.flush()
    if status == "confirmed":
        if existing:
            existing.status = "superseded"
            db.add(QshalaTagChange(question_id=q["question_id"], from_tag_id=existing.id,
                                   to_tag_id=row.id, change_type="SUPERSEDE", actor=actor, reason=reason))
        db.add(QshalaTagChange(question_id=q["question_id"], from_tag_id=None, to_tag_id=row.id,
                               change_type="PROMOTE", actor=actor, reason=reason))
    if paths and not topic_paths.has_human_paths(db, q["question_id"]):
        topic_paths.replace_paths(db, q["question_id"], paths, source="LLM", actor=actor,
                                  question=q.get("question_obj"))

    question_obj = q.get("question_obj")
    if not question_obj:
        from backend.app.models.question import Question
        question_obj = db.query(Question).get(q["question_id"])
    if question_obj:
        from backend.app.services.tagging.format_classifier import classify_and_extract
        q_format, f_data = classify_and_extract(
            q.get("question_text") or getattr(question_obj, "question_text", "") or "",
            q.get("answer") or getattr(question_obj, "answer", "") or "",
            question_images=q.get("question_images") or getattr(question_obj, "question_images", None),
            answer_images=q.get("answer_images") or getattr(question_obj, "answer_images", None),
            round_name=q.get("round_name") or q.get("round_type") or f"{getattr(question_obj, 'subtopic', '') or ''} {f'Round {question_obj.round_number}' if getattr(question_obj, 'round_number', None) else ''}".strip() or None,
            stored_format=getattr(question_obj, "question_format", None),
            stored_payload=getattr(question_obj, "format_payload", None),
        )
        if getattr(question_obj, "question_format", None) not in ("LIST_IT", "IMAGE_REVEAL"):
            question_obj.question_format = q_format
        if f_data is not None:
            question_obj.format_data = f_data

    return row


def _write_to_review(db: Session, q: Dict[str, Any], model: str, reason: str) -> QshalaTag:
    """Park a question in the review queue (draft, confidence 0) when its chunk could not be tagged."""
    existing = (db.query(QshalaTag)
                .filter(QshalaTag.status == "confirmed", QshalaTag.question_id == q["question_id"])
                .order_by(QshalaTag.created_at.desc()).first())
    if existing and existing.source == "HUMAN":
        return existing
    row = QshalaTag(question_id=q["question_id"], taxonomy_version=TAXONOMY_VERSION,
                    model_version=model, source="LLM", fit="none", confidence=0.0,
                    status="draft", verification_notes=f"needs review: {reason}")
    db.add(row)
    db.flush()
    return row


# --------------------------------------------------------------------- orchestrator

async def tag_questions_batched(db: Session, questions: List[Question], *, provider, model: str,
                                actor: str = "deck_upload", thinking_level: str = "low",
                                rpm: float = 0.0) -> Dict[str, int]:
    """Tag a list of Question rows in chunks. The caller owns the transaction; this flushes rows and
    does NOT commit (so it composes with the pipeline's own commit). Always returns; uncertain or
    failed questions are left as `draft`. Returns counts {confirmed, draft, chunks, failed_chunks}."""
    items: List[Dict[str, Any]] = []
    for idx, q in enumerate(questions):
        qtext, ans = (q.question_text or "").strip(), (q.answer or "").strip()
        if not qtext or not ans:
            continue
        items.append({
            "n": idx, "question_id": q.id, "question_obj": q,
            "question_text": qtext, "answer": ans,
            "visual_clues": getattr(q, "visual_clues", "") or "",
            "rules": qshala_rules.extract(qtext, ans, taxonomy_version=TAXONOMY_VERSION),
        })

    exemplars = recent_human_exemplars(db)
    chunks = [items[i:i + CHUNK_SIZE] for i in range(0, len(items), CHUNK_SIZE)]
    schema, system = _batch_schema(), _system_prompt()
    sem = asyncio.Semaphore(CHUNK_CONCURRENCY)
    stats = {"confirmed": 0, "draft": 0, "chunks": len(chunks), "failed_chunks": 0,
             "tokens_in": 0, "tokens_out": 0, "tokens_think": 0, "tokens_cached": 0, "calls": 0}

    async def run_chunk(chunk: List[Dict[str, Any]]) -> Dict[int, Any]:
        """Tags one chunk. Returns {n: (result, latency_ms, usage_share)}. If the model returns fewer
        items than asked, only the missing ones are asked again (a few tokens) rather than the whole
        chunk or parking them straight into review."""
        got: Dict[int, Any] = {}
        pending = list(chunk)
        async with sem:
            for attempt in range(MAX_CHUNK_RETRIES):
                if not pending:
                    break
                try:
                    if rpm and rpm > 0:
                        await asyncio.sleep(60.0 / rpm)
                    prompt = _batch_user_prompt(pending, exemplars)
                    t0 = time.perf_counter()
                    data, usage = await asyncio.wait_for(
                        provider.generate_json_schema(
                            prompt=prompt, schema=schema, system_instruction=system,
                            model=model, thinking_level=thinking_level, temperature=0.0),
                        timeout=CHUNK_TIMEOUT_S)
                    latency = int((time.perf_counter() - t0) * 1000)
                    asked = {it["n"] for it in pending}
                    returned = {int(r["n"]): r for r in (data.get("results") or [])
                                if isinstance(r, dict) and "n" in r and int(r["n"]) in asked}
                    usage = usage or {}
                    stats["calls"] += 1
                    stats["tokens_in"] += int(usage.get("in", 0) or 0)
                    stats["tokens_out"] += int(usage.get("out", 0) or 0)
                    stats["tokens_think"] += int(usage.get("think", 0) or 0)
                    stats["tokens_cached"] += int(usage.get("cached", 0) or 0)
                    k = max(1, len(returned))
                    share = {"in": int(usage.get("in", 0) or 0) // k, "out": int(usage.get("out", 0) or 0) // k,
                             "think": int(usage.get("think", 0) or 0) // k,
                             "cached": int(usage.get("cached", 0) or 0) // k}
                    for n, r in returned.items():
                        got[n] = (r, latency, share)
                    pending = [it for it in pending if it["n"] not in got]
                except Exception as e:
                    if attempt == MAX_CHUNK_RETRIES - 1:
                        logger.error("batch chunk failed after %d attempts: %s",
                                     MAX_CHUNK_RETRIES, safe_error(e))
                        break
                    # Match the per-question tagger's proven 429 backoff: 6s, 12s, 24s (honour
                    # Retry-After when Google sends it). Non-rate-limit errors retry faster.
                    wait = (retry_after_seconds(e) or min(60, 6 * (2 ** attempt))) if is_rate_limit(e) \
                        else 1.5 * (attempt + 1)
                    await asyncio.sleep(wait)
        return got

    results = await asyncio.gather(*[run_chunk(c) for c in chunks])
    for chunk, got in zip(chunks, results):
        if not got:
            stats["failed_chunks"] += 1
        for q in chunk:
            res = got.get(q["n"])
            try:
                if res is None:
                    _write_to_review(db, q, model, "batch chunk failed; parked for review")
                    stats["draft"] += 1
                else:
                    llm_out, latency, usage_share = res
                    row = _write_item(db, q, llm_out, model, latency, actor, usage=usage_share)
                    stats["confirmed" if row.status == "confirmed" else "draft"] += 1
            except Exception as e:
                logger.error("tag write failed for %s: %s", q["question_id"], safe_error(e))
                try:
                    _write_to_review(db, q, model, f"write error: {safe_error(e)[:120]}")
                    stats["draft"] += 1
                except Exception:
                    pass

    logger.info("batch tagging: %s", stats)
    return stats
