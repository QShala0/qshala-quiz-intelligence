"""QShala-domain tagger: Zerodha Varsity Quiz business/finance/culture questions.

Flow for a single question:
  1. extract(rules)                → RuleSignals (free, no tokens)
  2. duplicate_check(question_text)→ existing confirmed tag inherited, if any (free)
  3. llm_tag(question + rules)     → full QShala tag payload (one Gemini call, enum-constrained JSON)
  4. route(rules, llm_output)      → status: 'confirmed' | 'draft' + a short reason

Every output is a QshalaTag row with provenance:
  source='LLM' (or 'HUMAN' / 'RULE'), model_version, taxonomy_version.
A confirmed row never overwrites a human-confirmed row: `promote` checks and refuses.

The tagger uses whichever Gemini model is in settings.QSHALA_TAGGER_MODEL (default:
'gemini-3.5-flash-lite'); the heavier generator model stays on settings.GEMINI_MODEL.
"""
from __future__ import annotations

import asyncio
import os
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models.qshala_tag import QshalaTag, QshalaTagChange
from backend.app.services.ai.gemini_provider import GeminiLLMProvider
from backend.app.services.tagging import qshala_rules
from backend.app.services.tagging import topic_paths

logger = logging.getLogger(__name__)

TAXONOMY_PATH = Path(__file__).resolve().parent / "qshala_taxonomy_v6.json"
TAXONOMY_VERSION = "v6"

ENTITY_TYPES = ["COMPANY", "PERSON", "TERM", "PRODUCT_OR_OBJECT", "BOOK",
                "ORGANISATION", "POLICY", "NUMBER", "PLACE", "EVENT"]
GEOGRAPHY = ["IN", "INTL", "MIXED"]            # v5.1 and earlier
GEOGRAPHY_V6 = ["IN", "INTL", "NA"]             # v6: NA = cannot tell / not about a place
SHELF_LIFE = ["EVERGREEN", "REVIEW_YEARLY", "TIME_SENSITIVE"]
SENSITIVITY = ["NONE", "POLITICAL", "MATURE_REFERENCE"]
FIT = ["good", "weak", "none"]


# ---------------------------------------------------------------------- taxonomy

_TAX_CACHE: Dict[str, Any] = {}


def set_taxonomy_version(version: str) -> None:
    """Dynamically switch taxonomy version (v3, v4, v5, v5.1, v6)."""
    global TAXONOMY_PATH, TAXONOMY_VERSION
    v_clean = version.strip().lower()
    if v_clean in ("v6", "6"):
        TAXONOMY_PATH = Path(__file__).resolve().parent / "qshala_taxonomy_v6.json"
        TAXONOMY_VERSION = "v6"
    elif v_clean in ("v5.1", "5.1", "v5_1"):
        TAXONOMY_PATH = Path(__file__).resolve().parent / "qshala_taxonomy_v5_1.json"
        TAXONOMY_VERSION = "v5.1"
    elif v_clean in ("v5", "5"):
        TAXONOMY_PATH = Path(__file__).resolve().parent / "qshala_taxonomy_v5.json"
        TAXONOMY_VERSION = "v5"
    elif v_clean in ("v4", "4"):
        TAXONOMY_PATH = Path(__file__).resolve().parent / "qshala_taxonomy_v4.json"
        TAXONOMY_VERSION = "v4"
    elif v_clean in ("v3", "3"):
        TAXONOMY_PATH = Path(__file__).resolve().parent / "qshala_taxonomy_v3.json"
        TAXONOMY_VERSION = "v3"
    else:
        raise ValueError(f"Unknown taxonomy version: {version}")
    _TAX_CACHE.clear()


def _load_taxonomy() -> Dict[str, Any]:
    """Loaded once per process. The JSON file has: {level1: [{name, level2: [{name, description}]}]}."""
    if _TAX_CACHE:
        return _TAX_CACHE
    with open(TAXONOMY_PATH, encoding="utf-8") as f:
        data = json.load(f)
    l2_names: List[str] = []
    l1_names: List[str] = []
    l2_to_l1: Dict[str, str] = {}
    for grp in data["level1"]:
        l1 = grp["name"]
        l1_names.append(l1)
        for item in grp["level2"]:
            l2_names.append(item["name"])
            l2_to_l1[item["name"]] = l1
    _TAX_CACHE["raw"] = data
    _TAX_CACHE["l1"] = l1_names
    _TAX_CACHE["l2"] = l2_names
    _TAX_CACHE["l2_to_l1"] = l2_to_l1
    return _TAX_CACHE


def _taxonomy_prompt_block() -> str:
    tax = _load_taxonomy()["raw"]
    lines: List[str] = []
    for grp in tax["level1"]:
        lines.append(f"{grp['name']}:")
        for item in grp["level2"]:
            lines.append(f"  - {item['name']}: {item.get('description', '')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------- prompt + schema

SYSTEM_PROMPT_TEMPLATE = """You tag quiz questions for QShala, a quiz company in Bangalore. The questions come from live quiz decks (Zerodha Varsity Quiz): business, finance, brands, companies, current affairs and culture, mostly India-focused. Return the fields below for each question.

subtopic_l2: choose exactly ONE subtopic from the list, by what the question is ABOUT (its subject), not by where it is set or how it is asked. A bank's logo story is Brand Heritage & Identity; a bank's founding story is Financial History or Founder Stories. When two fit, choose the one a quizmaster would search for first.
secondary_l2: up to 3 other subtopics that also apply, or an empty list.
keywords_l3: 4 to 7 search keywords in Title Case, most specific first — named people, companies, products, places, key terms, and the answer; then one or two broader concept words. No filler words. Do not include facts the question does not support.
entity_type: what kind of thing the ANSWER is.
india_international: IN if the subject is India-centred, INTL if it is outside India, MIXED if both.
shelf_life: EVERGREEN = true in any year; REVIEW_YEARLY = facts or figures that can drift (fees, statistics, ongoing products); TIME_SENSITIVE = tied to a recent event or date.
sensitivity: POLITICAL if it touches contested politics or geopolitics; MATURE_REFERENCE if it mentions crime, drugs, violence or similar; otherwise NONE.
fit: good = a subtopic fits well; weak = the best available fit is imperfect; none = nothing fits.
search_summary: one sentence of at most 25 words saying what the question is about, naming the key entities and the answer.
If the question refers to an image or video you cannot see, tag from the text you have and set fit to weak.

Subtopics, grouped by topic:
"""


SYSTEM_PROMPT_V6_TEMPLATE = """You tag quiz questions for QShala, a quiz company in Bangalore. The questions come from live quiz decks (including the Zerodha Varsity Quiz) covering business, finance, technology, science, history, geography, politics, sports, entertainment and culture, mostly India-focused. Return the fields below for each question.

topics: 1 to 3 entries. Each entry is a theme, one subtopic copied exactly from that theme's list below, and keywords for it. Choose a subtopic by what the question is ABOUT (its subject), not by where it is set or how it is asked. The first entry is the best fit. Add a second or third entry only when the question genuinely sits in more than one area (a cricket league's brand deal fits both Sports Business and Brand Collaborations). When two subtopics fit equally, choose the one a quizmaster would search for first.
keywords (inside each entry): 3 to 6 search keywords in Title Case, most specific first: named people, companies, products, places, key terms and the answer; then one broader concept. No filler words and nothing generic like "Quiz" or "Question". Do not include facts the question does not support.
geo_location: the place the question is about, as specific as is useful (a city, state or country, e.g. "Bengaluru, India" or "Japan"). Empty string if it is not about a place.
india_international: IN if the subject is India-centred, INTL if it is outside India, NA if it cannot be determined or is not about a place.
relevant_year: the year the question is about or tied to (2016 for demonetisation, 1947 for Independence). Use 0 if it is not tied to a particular year.
fit: good = a subtopic fits well; weak = the best available fit is imperfect; none = nothing fits.
search_summary: one sentence of at most 25 words saying what the question is about, naming the key entities and the answer.
If the question refers to an image or video you cannot see, tag from the text you have and set fit to weak.

Subtopics, grouped by theme:
"""


def _system_prompt() -> str:
    if TAXONOMY_VERSION == "v6":
        return SYSTEM_PROMPT_V6_TEMPLATE + _taxonomy_prompt_block()
    return SYSTEM_PROMPT_TEMPLATE + _taxonomy_prompt_block()


def _tag_schema_v6() -> Dict[str, Any]:
    tax = _load_taxonomy()
    return {
        "type": "OBJECT",
        "properties": {
            # Kept deliberately light: a 143-value enum nested inside an array can make Gemini reject the
            # schema as too complex (HTTP 400). The subtopic list is in the prompt and is snapped to the
            # taxonomy in code (_v6_paths); only the small theme list is enforced here.
            "topics": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "theme": {"type": "STRING", "enum": tax["l1"]},
                        "subtopic_l2": {"type": "STRING"},
                        "keywords": {"type": "ARRAY", "items": {"type": "STRING"}},
                    },
                    "required": ["theme", "subtopic_l2", "keywords"],
                },
            },
            "geo_location": {"type": "STRING"},
            "india_international": {"type": "STRING", "enum": GEOGRAPHY_V6},
            "relevant_year": {"type": "INTEGER"},
            "fit": {"type": "STRING", "enum": FIT},
            "search_summary": {"type": "STRING"},
        },
        "required": ["topics", "geo_location", "india_international", "relevant_year", "fit", "search_summary"],
    }


def _tag_schema() -> Dict[str, Any]:
    """OpenAPI-style schema for Gemini's response_schema. Upper-case types; strict enums.

    We constrain the subtopic to the taxonomy's L2 names so the model can't invent one.
    """
    if TAXONOMY_VERSION == "v6":
        return _tag_schema_v6()
    tax = _load_taxonomy()
    return {
        "type": "OBJECT",
        "properties": {
            "subtopic_l2": {"type": "STRING", "enum": tax["l2"]},
            "secondary_l2": {"type": "ARRAY", "items": {"type": "STRING", "enum": tax["l2"]}, "maxItems": 3},
            "keywords_l3": {"type": "ARRAY", "items": {"type": "STRING"}, "minItems": 4, "maxItems": 7},
            "entity_type": {"type": "STRING", "enum": ENTITY_TYPES},
            "india_international": {"type": "STRING", "enum": GEOGRAPHY},
            "shelf_life": {"type": "STRING", "enum": SHELF_LIFE},
            "sensitivity": {"type": "STRING", "enum": SENSITIVITY},
            "fit": {"type": "STRING", "enum": FIT},
            "search_summary": {"type": "STRING"},
        },
        "required": ["subtopic_l2", "keywords_l3", "entity_type", "india_international",
                     "shelf_life", "sensitivity", "fit", "search_summary"],
    }


def _user_prompt(question_text: str, answer: str, rules: qshala_rules.RuleSignals,
                 visual_clues: str = "", deck_year: Optional[int] = None) -> str:
    if TAXONOMY_VERSION == "v6":
        parts = [f"QUESTION: {question_text}", f"ANSWER: {answer}"]
        if visual_clues:
            parts.append(f"VISUAL CLUES (from the question's images): {visual_clues[:600]}")
        if deck_year:
            parts.append(f"This question was used in a quiz held in {deck_year}. If it refers to a recent event, "
                         f"the relevant year is probably close to that.")
        if rules.brand_hints_l1:
            parts.append(f"Rule hint: likely theme(s) {', '.join(rules.brand_hints_l1)}. Confirm or override from the question.")
        if rules.image_dependent:
            parts.append("Rule hint: this question may depend on an image that is not shown. Tag from the text and set fit=weak if unsure.")
        return "\n".join(parts)
    hints: List[str] = []
    if rules.brand_hints_l1:
        hints.append(f"Rule hints — likely topic(s): {', '.join(rules.brand_hints_l1)}. Confirm or override from the question itself.")
    if rules.image_dependent:
        hints.append("Rule hint — this question appears to depend on an image that is NOT shown. Tag from text and set fit=weak.")
    hint_block = ("\n" + "\n".join(hints)) if hints else ""
    return f"QUESTION: {question_text}\nANSWER: {answer}{hint_block}"


# ---------------------------------------------------------------------- error helpers

_KEY_RE = re.compile(r"(key=)[^&'\"\s]+", re.I)


def safe_error(e: BaseException) -> str:
    """Error text for logs, the database and the UI: API keys removed, Google's reason added."""
    msg = str(e)
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            detail = (resp.json().get("error") or {}).get("message") or resp.text
        except Exception:
            detail = getattr(resp, "text", "") or ""
        if detail and detail[:60] not in msg:
            msg = f"{msg} | {detail[:240]}"
    return _KEY_RE.sub(r"\1***", msg)


def is_rate_limit(e: BaseException) -> bool:
    resp = getattr(e, "response", None)
    code = getattr(resp, "status_code", None)
    return code == 429 or "429" in str(e)[:200] or "too many requests" in str(e).lower()


def retry_after_seconds(e: BaseException) -> Optional[float]:
    resp = getattr(e, "response", None)
    try:
        v = resp.headers.get("retry-after") if resp is not None else None
        return min(90.0, float(v)) if v else None
    except Exception:
        return None


# ---------------------------------------------------------------------- rate limiter

_limiter = {"next": 0.0, "lock": None}


async def _wait_for_slot(rpm: float) -> None:
    """Space Gemini calls so we stay under `rpm` requests per minute (0 = no limit). Shared by every upload."""
    if not rpm or rpm <= 0:
        return
    import time
    if _limiter["lock"] is None:
        _limiter["lock"] = asyncio.Lock()
    async with _limiter["lock"]:
        now = time.monotonic()
        start = max(now, _limiter["next"])
        _limiter["next"] = start + 60.0 / rpm
    if start > now:
        await asyncio.sleep(start - now)


# ---------------------------------------------------------------------- the core call

def _canonical_hash(question_text: str, answer: str) -> str:
    norm = re.sub(r"\s+", " ", f"{question_text}||{answer}".lower()).strip()
    return hashlib.sha256(norm.encode()).hexdigest()[:32]


async def _call_llm(provider: GeminiLLMProvider, model: str, question_text: str,
                    answer: str, rules: qshala_rules.RuleSignals,
                    thinking_level: str = "low", visual_clues: str = "",
                    deck_year: Optional[int] = None) -> Tuple[Dict[str, Any], Dict[str, int], int]:
    """One Gemini call. Returns (parsed_json, usage_dict, latency_ms)."""
    system = _system_prompt()
    user = _user_prompt(question_text, answer, rules, visual_clues=visual_clues, deck_year=deck_year)
    schema = _tag_schema()
    t0 = time.perf_counter()
    data, usage = await provider.generate_json_schema(
        prompt=user,
        schema=schema,
        system_instruction=system,
        model=model,
        thinking_level=thinking_level,
        temperature=0.0,
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)
    return data, usage, latency_ms


# ---------------------------------------------------------------------- routing

def _route(rules: qshala_rules.RuleSignals, llm: Dict[str, Any]) -> Tuple[str, float, str]:
    """Decide status (confirmed / draft) and a confidence estimate.

    - LLM fit='good' AND no rule conflict → confirmed, 0.90.
    - LLM fit='weak' or fit='none'       → draft, 0.50.
    - Rule geography disagrees with LLM  → draft, 0.60. (route to review.)
    - LLM-only, no rule hits at all      → draft, 0.70.  (reviewer should look.)
    """
    fit = llm.get("fit", "weak")
    reasons: List[str] = []

    if fit in ("weak", "none"):
        return ("draft", 0.50, f"model marked fit={fit}")

    # Geography disagreement is the strongest cheap signal we have.
    if (rules.india_international and llm.get("india_international") not in (None, "NA")
            and rules.india_international != llm["india_international"]):
        reasons.append(f"geography mismatch (rule {rules.india_international} vs LLM {llm['india_international']})")
        return ("draft", 0.60, "; ".join(reasons))

    # Brand-hint agreement is a soft confirmation but not a hard rule — the LLM can override.
    rule_l1s = set(rules.brand_hints_l1 or [])
    llm_l1 = _load_taxonomy()["l2_to_l1"].get(llm.get("subtopic_l2", ""))
    rule_signals_fired = bool(rules.city_name or rules.brand_hints_l1 or rules.shelf_life or rules.sensitivity)

    if rule_signals_fired and (not rule_l1s or llm_l1 in rule_l1s):
        return ("confirmed", 0.90, "LLM fit=good, no rule conflict")
    if not rule_signals_fired:
        return ("draft", 0.70, "no rule signals fired; recommend quick review")

    # Rules fired but L1 doesn't match any brand-hint L1 — not fatal, but worth a look.
    return ("draft", 0.75, f"brand hints suggested {sorted(rule_l1s)}; LLM chose {llm_l1}")


# ---------------------------------------------------------------------- v6 helpers

def _v6_paths(llm_out: Dict[str, Any]) -> List[Dict[str, Any]]:
    import difflib
    tax = _load_taxonomy()
    known = {n.lower(): n for n in tax["l2"]}
    raw = []
    for t in (llm_out.get("topics") or [])[:3]:
        if not isinstance(t, dict):
            continue
        l2 = (t.get("subtopic_l2") or "").strip()
        if l2 and l2.lower() not in known:
            close = difflib.get_close_matches(l2.lower(), list(known), n=1, cutoff=0.82)
            if close:
                l2 = known[close[0]]
        kws = t.get("keywords")
        raw.append({"l1": t.get("theme"), "l2": l2, "keywords": (kws if isinstance(kws, list) else [])[:6]})
    return topic_paths.normalise_paths(raw)


def _clean_year(v: Any) -> Optional[int]:
    try:
        y = int(v)
    except (TypeError, ValueError):
        return None
    return y if 1000 <= y <= datetime.now(timezone.utc).year + 2 else None


# ---------------------------------------------------------------------- public API

class QshalaTagger:
    """Thin, DB-aware tagger. Instantiate once per request / job; it holds the Gemini client."""

    def __init__(self, provider: Optional[GeminiLLMProvider] = None, model: Optional[str] = None,
                 thinking_level: Optional[str] = None):
        self.provider = provider or GeminiLLMProvider()
        self.rpm = float(os.getenv("QSHALA_TAGGER_RPM", "12"))   # free tier allows 15/min; raise on a paid key (0 = off)
        self.model = model or getattr(settings, "QSHALA_TAGGER_MODEL", None) or "gemini-3.5-flash-lite"
        self.thinking_level = thinking_level or "low"

    # ---- Tag one question. Does NOT commit the row by default; caller decides.

    async def tag(
        self,
        db: Session,
        *,
        question_id: str,
        question_text: str,
        answer: str,
        actor: str = "system",
        commit: bool = True,
        visual_clues: str = "",
        deck_year: Optional[int] = None,
        question: Optional[Any] = None,
    ) -> QshalaTag:
        """Full pipeline for one question. Returns the inserted QshalaTag row (uncommitted if commit=False).

        For taxonomy v6 the topic paths are written to `question_topic_paths` (and mirrored onto `question`
        when it is passed), unless a person has already set paths for this question.
        """
        # Step 1: rules.
        rules = qshala_rules.extract(question_text, answer, taxonomy_version=TAXONOMY_VERSION)

        # Step 2: duplicate check against previously confirmed tags.
        canon = _canonical_hash(question_text, answer)
        existing = (
            db.query(QshalaTag)
            .filter(QshalaTag.status == "confirmed")
            .filter(QshalaTag.question_id == question_id)
            .order_by(QshalaTag.created_at.desc())
            .first()
        )
        if existing and existing.source == "HUMAN":
            # Never re-tag over a human-confirmed row. Return the existing row unchanged.
            logger.info("qshala_tagger: skipping re-tag; HUMAN-confirmed row exists for %s", question_id)
            return existing

        # Step 3: LLM call (two retries with a short back-off for rate limits and network blips).
        llm_out: Dict[str, Any] = {}
        max_attempts = 6
        for attempt in range(max_attempts):
            try:
                await _wait_for_slot(self.rpm)
                llm_out, usage, latency_ms = await _call_llm(
                    self.provider, self.model, question_text, answer, rules,
                    thinking_level=self.thinking_level, visual_clues=visual_clues, deck_year=deck_year)
                break
            except Exception as e:
                if attempt == max_attempts - 1 or isinstance(e, ValueError) or (not is_rate_limit(e) and attempt >= 2):
                    logger.error("LLM call failed for %s: %s", question_id, safe_error(e))
                    raise (ValueError if isinstance(e, ValueError) else RuntimeError)(safe_error(e)) from None
                # Rate limited (429): wait much longer, and honour Retry-After when Google sends it.
                if is_rate_limit(e):
                    wait = retry_after_seconds(e) or min(60, 6 * (2 ** attempt))
                else:
                    wait = 1.5 * (attempt + 1)
                logger.warning("LLM call for %s hit %s, retrying in %.0fs", question_id,
                               "a rate limit" if is_rate_limit(e) else "an error", wait)
                await asyncio.sleep(wait)

        if TAXONOMY_VERSION == "v6":
            return self._finish_v6(db, question_id=question_id, rules=rules, llm_out=llm_out, usage=usage,
                                   latency_ms=latency_ms, actor=actor, commit=commit, existing=existing,
                                   question=question)

        # Step 4: route.
        status, confidence, reason = _route(rules, llm_out)
        l2 = llm_out.get("subtopic_l2")
        l1 = _load_taxonomy()["l2_to_l1"].get(l2) if l2 else None

        row = QshalaTag(
            question_id=question_id,
            taxonomy_version=TAXONOMY_VERSION,
            model_version=self.model,
            source="LLM",
            topic_l1=l1,
            subtopic_l2=l2,
            keywords_l3=llm_out.get("keywords_l3", []),
            secondary_l2=llm_out.get("secondary_l2", []),
            entity_type=llm_out.get("entity_type"),
            city_name=rules.city_name,
            geo_region=rules.geo_region,
            india_international=llm_out.get("india_international"),
            shelf_life=(rules.shelf_life if rules.shelf_life == "TIME_SENSITIVE" else None)
                       or llm_out.get("shelf_life")
                       or rules.shelf_life,
            sensitivity=llm_out.get("sensitivity") or rules.sensitivity or "NONE",
            fit=llm_out.get("fit"),
            search_summary=llm_out.get("search_summary"),
            confidence=confidence,
            status=status,
            tokens_in=usage.get("in"),
            tokens_out=usage.get("out"),
            thinking_tokens=usage.get("think"), cached_tokens=usage.get("cached"),
            latency_ms=latency_ms,
            verification_notes=f"auto-routed: {reason}",
        )
        db.add(row)
        db.flush()

        # If we're confirming, supersede the previous confirmed row (if any) and log the change.
        if status == "confirmed" and existing:
            existing.status = "superseded"
            db.add(QshalaTagChange(
                question_id=question_id, from_tag_id=existing.id, to_tag_id=row.id,
                change_type="SUPERSEDE", actor=actor, reason=reason,
            ))
        if status == "confirmed":
            db.add(QshalaTagChange(
                question_id=question_id, from_tag_id=None, to_tag_id=row.id,
                change_type="PROMOTE", actor=actor, reason=reason,
            ))

        if commit:
            db.commit()
            db.refresh(row)
        return row

    def _finish_v6(self, db: Session, *, question_id: str, rules: qshala_rules.RuleSignals,
                   llm_out: Dict[str, Any], usage: Dict[str, int], latency_ms: int, actor: str,
                   commit: bool, existing: Optional[QshalaTag], question: Optional[Any]) -> QshalaTag:
        paths = _v6_paths(llm_out)
        first = paths[0] if paths else {}
        routed = {"fit": llm_out.get("fit", "weak"), "india_international": llm_out.get("india_international"),
                  "subtopic_l2": first.get("l2")}
        status, confidence, reason = _route(rules, routed)
        if not paths:
            status, confidence, reason = "draft", 0.30, "model returned no usable topic"
        geo = llm_out.get("india_international")
        row = QshalaTag(
            question_id=question_id, taxonomy_version=TAXONOMY_VERSION, model_version=self.model, source="LLM",
            topic_l1=first.get("l1"), subtopic_l2=first.get("l2"),
            keywords_l3=first.get("keywords", []),
            secondary_l2=[p["l2"] for p in paths[1:] if p.get("l2")],
            city_name=rules.city_name,
            geo_region=(llm_out.get("geo_location") or "").strip() or rules.geo_region,
            india_international=geo if geo in GEOGRAPHY_V6 else "NA",
            relevant_year=_clean_year(llm_out.get("relevant_year")),
            fit=llm_out.get("fit"), search_summary=llm_out.get("search_summary"),
            confidence=confidence, status=status,
            tokens_in=usage.get("in"), tokens_out=usage.get("out"), thinking_tokens=usage.get("think"),
            cached_tokens=usage.get("cached"),
            latency_ms=latency_ms, verification_notes=f"auto-routed: {reason}",
        )
        db.add(row)
        db.flush()
        if status == "confirmed" and existing:
            existing.status = "superseded"
            db.add(QshalaTagChange(question_id=question_id, from_tag_id=existing.id, to_tag_id=row.id,
                                   change_type="SUPERSEDE", actor=actor, reason=reason))
        if status == "confirmed":
            db.add(QshalaTagChange(question_id=question_id, from_tag_id=None, to_tag_id=row.id,
                                   change_type="PROMOTE", actor=actor, reason=reason))
        if paths and not topic_paths.has_human_paths(db, question_id):
            topic_paths.replace_paths(db, question_id, paths, source="LLM", actor=actor, question=question)

        q_target = question
        if q_target is None:
            from backend.app.models.question import Question
            q_target = db.query(Question).get(question_id)
        if q_target is not None:
            from backend.app.services.tagging.format_classifier import classify_and_extract
            q_format, f_data = classify_and_extract(
                getattr(q_target, "question_text", "") or "",
                getattr(q_target, "answer", "") or "",
                question_images=getattr(q_target, "question_images", None),
                answer_images=getattr(q_target, "answer_images", None),
                round_name=getattr(q_target, "round_name", None) or f"{getattr(q_target, 'subtopic', '') or ''} {f'Round {q_target.round_number}' if getattr(q_target, 'round_number', None) else ''}".strip() or None,
                stored_format=getattr(q_target, "question_format", None),
                stored_payload=getattr(q_target, "format_payload", None),
            )
            if getattr(q_target, "question_format", None) not in ("LIST_IT", "IMAGE_REVEAL"):
                q_target.question_format = q_format
            if f_data is not None:
                q_target.format_data = f_data

        if commit:
            db.commit()
            db.refresh(row)
        return row

    # ---- Human review API.

    def human_override(
        self,
        db: Session,
        *,
        question_id: str,
        new_tags: Dict[str, Any],
        actor: str,
        notes: Optional[str] = None,
        commit: bool = True,
    ) -> QshalaTag:
        """Store a human-authored tag row. Supersedes any current confirmed row.

        `new_tags` is a dict with the same keys the LLM returns, plus optional fact_check_url.
        """
        existing = (
            db.query(QshalaTag)
            .filter(QshalaTag.status == "confirmed")
            .filter(QshalaTag.question_id == question_id)
            .order_by(QshalaTag.created_at.desc())
            .first()
        )
        l2 = new_tags.get("subtopic_l2")
        l1 = _load_taxonomy()["l2_to_l1"].get(l2) if l2 else None
        row = QshalaTag(
            question_id=question_id, taxonomy_version=TAXONOMY_VERSION, model_version="human", source="HUMAN",
            topic_l1=l1, subtopic_l2=l2,
            keywords_l3=new_tags.get("keywords_l3", []),
            secondary_l2=new_tags.get("secondary_l2", []),
            entity_type=new_tags.get("entity_type"),
            city_name=new_tags.get("city_name"),
            geo_region=new_tags.get("geo_region"),
            india_international=new_tags.get("india_international"),
            shelf_life=new_tags.get("shelf_life"),
            sensitivity=new_tags.get("sensitivity", "NONE"),
            fit=new_tags.get("fit", "good"),
            search_summary=new_tags.get("search_summary"),
            confidence=1.0,
            status="confirmed",
            verified_by=actor,
            verified_at=datetime.now(timezone.utc),
            verification_notes=notes,
            fact_check_url=new_tags.get("fact_check_url"),
        )
        db.add(row)
        db.flush()
        if existing:
            existing.status = "superseded"
            db.add(QshalaTagChange(
                question_id=question_id, from_tag_id=existing.id, to_tag_id=row.id,
                change_type="HUMAN_OVERRIDE", actor=actor, reason=notes or "",
            ))
        db.add(QshalaTagChange(
            question_id=question_id, from_tag_id=None, to_tag_id=row.id,
            change_type="PROMOTE", actor=actor, reason="human-confirmed",
        ))
        if commit:
            db.commit()
            db.refresh(row)
        return row

    @staticmethod
    def current(db: Session, question_id: str) -> Optional[QshalaTag]:
        """The current confirmed tag row for a question, or None."""
        return (
            db.query(QshalaTag)
            .filter(QshalaTag.question_id == question_id, QshalaTag.status == "confirmed")
            .order_by(QshalaTag.created_at.desc())
            .first()
        )
