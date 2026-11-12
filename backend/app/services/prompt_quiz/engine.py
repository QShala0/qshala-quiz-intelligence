"""Prompt-quiz engine: one sentence in, a curated draft quiz out.

Five personas, each a small deterministic step:
  Interpreter - turns the prompt into a Brief (topic words, count, filters, difficulty mix).
                Uses Gemini when GEMINI_API_KEY is set (natural-language understanding),
                and falls back to the regex parse_prompt() otherwise. Gemini ONLY reads the
                prompt and emits the structured Brief; it never selects or writes questions.
  Librarian   - retrieves candidates (BM25 over the bank; uses qshala_tags when present,
                falls back to the older Question.topic/subtopic/tags)
  Guardian    - drops duplicates, image-only, sensitive or time-sensitive items per brief
  Editor      - picks N with topic variety and an easy->hard ramp
  Host        - writes the one-line framing shown to the user

Retrieval, ranking and selection stay 100% deterministic Python — the LLM's role is
strictly prompt -> Brief, so there is zero hallucination risk in the questions served.
"""
from __future__ import annotations
import logging
logger = logging.getLogger(__name__)
import os
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlalchemy import case, func
from sqlalchemy.orm import Session, defer

from backend.app.models.question import Question
try:  # tag tables may be absent or renamed on some branches; engine degrades gracefully
    from backend.app.models.qshala_tag import QshalaTag, QshalaQuestionMetadata
except Exception:  # pragma: no cover
    QshalaTag = None
    QshalaQuestionMetadata = None

from backend.app.services.prompt_quiz.retrieval import Index, normalize_compound_typos, parse_prompt, tokenize, WORD_NUMS
from backend.app.services.prompt_quiz.interpreter_llm import V6_THEMES
from backend.app.services.prompt_quiz.audience import (
    audience_score, defaults_for_audience, difficulty_penalty, passes_hard
)
from backend.app.services.prompt_quiz.fulfillment import (
    blocked_counts, build_fulfillment, split_pools
)

DIFF_RANK = {"easy": 0, "medium": 1, "hard": 2}


@dataclass
class Brief:
    prompt: str
    query: str
    count: int
    filters: Dict[str, Any] = field(default_factory=dict)
    ramp: bool = True
    hard_bias: Optional[str] = None  # "easy" | "hard" | "medium" | None
    framing: Optional[str] = None    # LLM host framing, if the Interpreter produced one
    source: str = "regex"            # "gemini" | "regex" — which interpreter produced this brief
    format_filter: Optional[str] = None # CONNECT_4 | STEPPED_CLUE | ARRANGE_ORDER | STANDARD_QA
    locality: Optional[str] = None
    venue_type: Optional[str] = None
    tone: List[str] = field(default_factory=list)
    cross_topic_padding: bool = False
    topics_l1: List[str] = field(default_factory=list)
    subtopics_l2: List[str] = field(default_factory=list)
    search_terms: List[str] = field(default_factory=list)
    narrative_arc: Optional[str] = None
    # Reserve spares and example slides are left out of a quiz unless asked for
    include_reserve: bool = False
    include_example: bool = False

    def public(self) -> Dict[str, Any]:
        return asdict(self)


# ---------- Interpreter ----------
def _bias_from_text(p: str) -> Optional[str]:
    return "hard" if re.search(r"\b(hard(?:er)?|tough(?:er)?|difficult|finals?|killer|spicy|spicier|mushkil)\b", p) else \
           "easy" if re.search(r"\b(easy|easier|warm-?up|beginner|gentle|aasan|saral|tone\s+it\s+down)\b", p) else \
           "medium" if re.search(r"\b(balanced|medium|not\s+too\s+hard|not\s+too\s+easy)\b", p) else None


def _brief_from_gemini(prompt: str, gem: Dict[str, Any], count: Optional[int],
                       previous_brief: Optional[Dict[str, Any]]) -> Brief:
    prev_filters = dict((previous_brief or {}).get("filters") or {})

    # count: explicit UI count arg wins; else model's count; else inherit previous; else 10.
    n = count or gem.get("count") or (previous_brief or {}).get("count") or 10
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 10
    n = max(1, min(n, 50))

    topics_l1 = list(gem.get("topics_l1") or (previous_brief or {}).get("topics_l1") or [])
    subtopics_l2 = list(gem.get("subtopics_l2") or (previous_brief or {}).get("subtopics_l2") or [])

    p_norm = normalize_compound_typos(prompt or "").lower()
    raw_query = str(gem.get("query") or "").strip()
    _mod_queries = {
        "", "india", "india focus", "focus on india", "middle school",
        "middle school kids", "school friendly", "text only", "no images",
        "only questions with images", "only with images", "international", "global",
    }
    if previous_brief and (not raw_query or raw_query.lower() in _mod_queries):
        raw_query = str(previous_brief.get("query") or "").strip()
    query_bits = [raw_query] if raw_query else []
    if not raw_query:
        query_bits += [str(t) for t in topics_l1 if t]
        query_bits += [str(t) for t in subtopics_l2 if t]
    query = re.sub(r"\s+", " ", " ".join(b for b in query_bits if b)).strip()
    from backend.app.services.prompt_quiz.topic_normalize import dedupe_query
    query = dedupe_query(query)

    filters: Dict[str, Any] = dict(prev_filters)
    if gem.get("india_focused") is not None:
        ind_val = gem.get("india_focused")
        if ind_val is False:
            explicit_intl_ask = bool(re.search(r"\b(international|global|world|foreign|non-?indian|outside\s+india)\b", p_norm))
            if explicit_intl_ask:
                filters["india_focused"] = False
                filters["india_international"] = "INTL"
        elif ind_val is True:
            filters["india_focused"] = True
            explicit_india_ask = bool(re.search(r"\b(india|indian|bharat|hindustan|desh|indian\s+focus|focus\s+on\s+india)\b", p_norm))
            universal_stem_ask = bool(re.search(
                r"\b(science|scientific|experiment|experiments|physics|chemistry|biology|math|mathematics|astronomy|space|technology|coding)\b",
                p_norm,
            ))
            if explicit_india_ask or not universal_stem_ask:
                filters["india_international"] = "IN"
            else:
                filters.pop("india_international", None)
    if gem.get("evergreen") is not None:
        val = bool(gem.get("evergreen"))
        filters["evergreen"] = val
        if val:
            filters["evergreen_only"] = True
        else:
            filters.pop("evergreen_only", None)
    if gem.get("no_images") is not None:
        val = bool(gem.get("no_images"))
        filters["no_images"] = val
        if val:
            filters["exclude_image"] = True
            filters.pop("require_images", None)
        else:
            filters.pop("exclude_image", None)
    if re.search(r"\b(?:only|with)\s+(?:questions?\s+with\s+)?(?:images?|pics?|visuals?|photos?)\b", p_norm) and not gem.get("no_images"):
        filters.pop("no_images", None)
        filters.pop("exclude_image", None)
        filters["require_images"] = True
    if gem.get("school_safe") is not None:
        filters["school_safe"] = bool(gem.get("school_safe"))
    if gem.get("audience"):
        filters["audience"] = gem.get("audience")

    raw_bias = gem.get("hard_bias")
    if raw_bias is None and previous_brief:
        raw_bias = previous_brief.get("hard_bias")
    if not raw_bias:
        raw_bias = _bias_from_text(prompt)
    raw_bias = (raw_bias or "").lower()
    bias = (
        "hard"
        if raw_bias in ("hard", "spicy")
        else "easy"
        if raw_bias == "easy"
        else ("medium" if raw_bias in ("medium", "balanced") else None)
    )
    if bias is None and not previous_brief:
        bias = _bias_from_text(prompt)

    framing = (gem.get("framing") or "").strip() or (previous_brief or {}).get("framing") or None
    fmt = gem.get("format_filter") or (previous_brief or {}).get("format_filter")

    raw_terms = gem.get("search_terms") or (previous_brief or {}).get("search_terms") or []
    search_terms = [str(t).strip() for t in raw_terms
                    if isinstance(t, str) and t.strip() and len(t.strip()) < 60][:8]

    locality = gem.get("locality") or (previous_brief or {}).get("locality")
    venue_type = gem.get("venue_type") or (previous_brief or {}).get("venue_type")
    tone = list(gem.get("tone") or (previous_brief or {}).get("tone") or [])
    cross_topic_padding = bool(gem.get("cross_topic_padding") if gem.get("cross_topic_padding") is not None else (previous_brief or {}).get("cross_topic_padding", False))

    prev_b = previous_brief or {}
    return Brief(
        include_reserve=bool(prev_b.get('include_reserve', False)),
        include_example=bool(prev_b.get('include_example', False)),
        prompt=prompt,
        query=query,
        count=n,
        filters=filters,
        hard_bias=bias,
        framing=framing,
        source="gemini",
        format_filter=fmt,
        locality=locality,
        venue_type=venue_type,
        tone=tone,
        cross_topic_padding=cross_topic_padding,
        topics_l1=topics_l1,
        subtopics_l2=subtopics_l2,
        search_terms=search_terms,
    )


def _brief_from_regex(prompt: str, count: Optional[int],
                      previous_brief: Optional[Dict[str, Any]],
                      current_questions: Optional[List[Dict[str, Any]]] = None) -> Brief:
    query, raw_filters, n = parse_prompt(prompt or "")
    p = normalize_compound_typos(prompt or "").lower()

    # Partial replacement check: e.g. "change 3 questions to tech", "replace 2 with sports"
    partial_m = re.search(
        r"\b(?:change|replace|swap|make|mix\s+in)\s+(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:of\s+them\s+|questions?\s+)?(?:to|with|for|about|into)?\s*([a-z\s]+?)(?:\s+(?:and\s+)?keep\s+the\s+rest)?$",
        p,
        re.I
    )

    # Relative count expansion (e.g. "5 more", "give me 5 more questions")
    more_m = re.search(r"\b(?:add|give\s+me\s+)?(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|couple|few)\s+more\b", p)
    fewer_m = re.search(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+fewer\b", p)
    double_m = re.search(r"\bdouble\s+it\b", p)
    halve_m = re.search(r"\bhalve\s+it\b", p)
    cut_to_m = re.search(r"\bcut\s+(?:it\s+)?down\s+to\s+(\d{1,2}|one|two|three|four|five)\b", p)

    if partial_m and previous_brief:
        final_n = previous_brief.get("count") or 10
    elif double_m and previous_brief:
        final_n = min(50, (previous_brief.get("count") or 10) * 2)
    elif halve_m and previous_brief:
        final_n = max(1, (previous_brief.get("count") or 10) // 2)
    elif cut_to_m and previous_brief:
        val = cut_to_m.group(1).lower()
        final_n = int(WORD_NUMS.get(val, val))
    elif fewer_m and previous_brief:
        val = fewer_m.group(1).lower()
        sub_n = int(WORD_NUMS.get(val, val))
        final_n = max(1, (previous_brief.get("count") or 10) - sub_n)
    elif more_m and previous_brief:
        raw_val = more_m.group(1).lower()
        val = int(WORD_NUMS.get(raw_val, raw_val))
        final_n = min(50, (previous_brief.get("count") or 10) + val)
    else:
        has_number = bool(re.search(r"\b\d{1,3}\b", p)) or any(
            re.search(r"\b" + re.escape(w) + r"\b", p) for w in WORD_NUMS)
        inherited = (previous_brief or {}).get("count") if previous_brief else None
        final_n = count or (n if has_number else (inherited or 10))
    final_n = max(1, min(int(final_n), 50))

    prev_bias = (previous_brief or {}).get("hard_bias")
    bias = _bias_from_text(p) or prev_bias
    fmt = raw_filters.get("format_filter") or (previous_brief or {}).get("format_filter")

    # Inherit and merge filters
    filters = dict((previous_brief or {}).get("filters") or {})
    # Map raw_filters from parse_prompt
    if "exclude_image" in raw_filters:
        filters["exclude_image"] = True
        filters["no_images"] = True
        filters.pop("require_images", None)
    elif re.search(r"\b(?:only|with)\s+(?:questions?\s+with\s+)?(?:images?|pics?|visuals?|photos?)\b", p):
        filters.pop("exclude_image", None)
        filters.pop("no_images", None)
        filters["require_images"] = True
    if "evergreen_only" in raw_filters:
        filters["evergreen_only"] = True
        filters["evergreen"] = True
    if "school_safe" in raw_filters:
        filters["school_safe"] = True
    if raw_filters.get("india_international") == "IN":
        filters["india_focused"] = True
        filters["india_international"] = "IN"
    elif raw_filters.get("india_international") == "INTL":
        filters["india_focused"] = False
        filters["india_international"] = "INTL"

    # Regex audience hints
    if re.search(r"\b(school|kids?|class\s*\d+|assembly|children)\b", p):
        filters["audience"] = "school"
        filters["school_safe"] = True
    elif re.search(r"\b(college|fest|hostel|campus)\b", p):
        filters["audience"] = "college"
    elif re.search(r"\b(corporate|company|office|offsite)\b", p):
        filters["audience"] = "corporate"

    # Locality detection
    locality = (previous_brief or {}).get("locality")
    city_m = re.search(r"\b(karnataka|bangalore|bengaluru|mumbai|bombay|delhi|new delhi|pune|chennai|madras|kolkata|calcutta|hyderabad)\b", p)
    if city_m:
        raw_city = city_m.group(1).lower()
        city_map = {
            "karnataka": "Karnataka",
            "bangalore": "Bengaluru", "bengaluru": "Bengaluru",
            "mumbai": "Mumbai", "bombay": "Mumbai",
            "delhi": "Delhi", "new delhi": "Delhi",
            "chennai": "Chennai", "madras": "Chennai",
            "kolkata": "Kolkata", "calcutta": "Kolkata",
            "pune": "Pune",
            "hyderabad": "Hyderabad",
        }
        locality = city_map.get(raw_city, raw_city.title())
        filters["india_focused"] = True

    venue_type = (previous_brief or {}).get("venue_type")
    tone = list((previous_brief or {}).get("tone") or [])
    cross_topic_padding = bool((previous_brief or {}).get("cross_topic_padding", False))
    if re.search(r"\b(related|adjacent|whatever you have|fill the rest)\b", p):
        cross_topic_padding = True

    # Detect partial or full topic replacement: e.g. "change 3 questions to tech", "replace sports with tech"
    if partial_m:
        raw_topic = partial_m.group(2).strip()
        cleaned_topic = re.sub(r"\bquestions?\b", "", raw_topic, flags=re.I).strip()
        query = cleaned_topic or "general knowledge"
        matched_l1s = [t for t in V6_THEMES if t.lower() in query.lower()]
        topics_l1 = matched_l1s or []
        subtopics_l2 = []
        search_terms = []
    elif re.search(r"\b(?:replace|swap|change)\s+([a-z\s]+?)\s+(?:with|for|to)\s+([a-z\s]+)\b", p):
        replace_m = re.search(r"\b(?:replace|swap|change)\s+([a-z\s]+?)\s+(?:with|for|to)\s+([a-z\s]+)\b", p)
        query = replace_m.group(2).strip()
        matched_l1s = [t for t in V6_THEMES if t.lower() in query.lower()]
        topics_l1 = matched_l1s or []
        subtopics_l2 = []
        search_terms = []
    else:
        # Detect V6 theme in prompt if not inherited
        matched_l1s = [t for t in V6_THEMES if t.lower() in p]
        topics_l1 = matched_l1s or list((previous_brief or {}).get("topics_l1") or [])
        subtopics_l2 = list((previous_brief or {}).get("subtopics_l2") or [])
        search_terms = list((previous_brief or {}).get("search_terms") or [])

        # If this is a modifier-only refinement on an existing quiz, preserve the previous topic
        _q_mod = query.strip().lower()
        is_modifier_prompt = previous_brief and (
            bool(re.search(r"^(?:make\s+it\s+|more\s+)?(?:harder|easier|tougher|spicier|tough|spicy|easy|warmup|balanced)$", p.strip()))
            or bool(re.search(r"^(?:make\s+it\s+)?(?:for\s+)?(?:middle\s+school\s+|high\s+school\s+|primary\s+school\s+)?(?:school(?:\s+friendly)?|kids|children|college|corporate)$", p.strip()))
            or bool(re.search(r"^(?:focus\s+on\s+india|india\s+focus|focus\s+india|global(?:\s+perspective)?|international)$", p.strip()))
            or bool(re.search(r"^(?:no\s+images?|text\s+only(?:,?\s*no\s+images?)?|without\s+images?|only\s+(?:questions?\s+)?with\s+images?|with\s+images?|images?\s+only)$", p.strip()))
            or bool(re.search(r"^(?:evergreen\s+only|evergreen)$", p.strip()))
            or bool(more_m)
            or _q_mod in (
                "", "make it harder", "harder", "easier", "school", "kids", "middle school",
                "school friendly", "no images", "text only", "only images", "with images",
                "evergreen", "5 more", "more", "india", "india focus", "focus india",
                "international", "global", "world",
            )
        )
        if is_modifier_prompt and previous_brief.get("query") is not None:
            query = previous_brief.get("query") or ""

    framing = (previous_brief or {}).get("framing")

    prev_b = previous_brief or {}
    return Brief(
        include_reserve=bool(prev_b.get('include_reserve', False)),
        include_example=bool(prev_b.get('include_example', False)),
        prompt=prompt,
        query=query,
        count=final_n,
        filters=filters,
        hard_bias=bias,
        framing=framing,
        source="regex",
        format_filter=fmt,
        locality=locality,
        venue_type=venue_type,
        tone=tone,
        cross_topic_padding=cross_topic_padding,
        topics_l1=topics_l1,
        subtopics_l2=subtopics_l2,
        search_terms=search_terms,
    )


def interpret(prompt: str, count: Optional[int] = None, options: Optional[Dict[str, Any]] = None,
              previous_brief: Optional[Dict[str, Any]] = None,
              current_questions: Optional[List[Dict[str, Any]]] = None,
              db: Optional[Session] = None) -> Brief:
    """Prompt -> Brief. Tries the Dynamic Intent Engine first (live taxonomy + rich semantic expansions);
    falls back to Interpreter LLM, then to deterministic regex parser."""
    brief: Optional[Brief] = None

    # Stage 1: Try Dynamic Intent Engine with Live Vault Taxonomy
    if db is not None and os.getenv("ENABLE_INTENT_ENGINE", "true").lower() in ("1", "true", "yes"):
        try:
            from backend.app.services.prompt_quiz.intent_engine import deconstruct_prompt
            brief = deconstruct_prompt(prompt, db, count=count, options=options, previous_brief=previous_brief)
        except Exception as e_intent:
            logger.warning(f"Dynamic intent engine deconstruction failed ({e_intent}), falling back to interpreter")

    if brief is None:
        gem: Optional[Dict[str, Any]] = None
        try:
            from backend.app.services.prompt_quiz.interpreter_llm import interpret_with_gemini
            gem = interpret_with_gemini(prompt or "", previous_brief, current_questions=current_questions)
        except Exception:
            gem = None

        brief = _brief_from_gemini(prompt, gem, count, previous_brief) if gem \
            else _brief_from_regex(prompt, count, previous_brief, current_questions=current_questions)

    # Defaults for audience
    f, hb = defaults_for_audience(brief.filters.get("audience"), brief.filters, brief.hard_bias)
    brief.filters = f
    brief.hard_bias = hb

    pb = previous_brief or {}
    brief.include_reserve = bool(pb.get('include_reserve', brief.include_reserve))
    brief.include_example = bool(pb.get('include_example', brief.include_example))

    # Explicit UI options override whatever either interpreter decided.
    for k, v in (options or {}).items():
        if k == "format_filter":
            brief.format_filter = v or None
        elif k == "cross_topic_padding":
            brief.cross_topic_padding = bool(v)
        elif k == "hard_bias":
            brief.hard_bias = v or None
        elif k == "include_reserve":
            brief.include_reserve = bool(v)
        elif k == "include_example":
            brief.include_example = bool(v)
        elif v in (None, False, "") or (isinstance(v, list) and len(v) == 0):
            brief.filters.pop(k, None)
            if k == "exclude_image":
                brief.filters.pop("no_images", None)
            elif k == "no_images":
                brief.filters.pop("exclude_image", None)
            elif k == "evergreen_only":
                brief.filters.pop("evergreen", None)
            elif k == "evergreen":
                brief.filters.pop("evergreen_only", None)
            elif k == "document_ids":
                brief.filters.pop("source_labels", None)
        else:
            brief.filters[k] = v
            if k == "document_ids" and "deck_filter" not in (options or {}):
                brief.filters.pop("deck_filter", None)
            if k == "exclude_image":
                brief.filters["no_images"] = True
            elif k == "no_images":
                brief.filters["exclude_image"] = True
            elif k == "evergreen_only":
                brief.filters["evergreen"] = True
            elif k == "evergreen":
                brief.filters["evergreen_only"] = True
    return brief


# ---------- Librarian ----------
# The bank is rebuilt from the database on every request (2-3 times per request in places). A short cache lets those
# share one load. PROMPT_QUIZ_BANK_TTL_S=0 turns it off; questions added or edited show up within that many seconds.
_BANK_CACHE: Dict[str, Any] = {"at": 0.0, "key": None, "bank": None}


def _bank_ttl() -> float:
    try:
        return float(os.getenv("PROMPT_QUIZ_BANK_TTL_S", "10"))
    except ValueError:
        return 10.0


def invalidate_bank_cache() -> None:
    _BANK_CACHE.update({"at": 0.0, "key": None, "bank": None})


LIVE_ONLY = ("LIVE",)
ALL_SLOTS = ("LIVE", "RESERVE", "EXAMPLE")


def slots_for(brief: "Brief") -> tuple:
    """Slots a quiz may draw from: LIVE questions always; RESERVE / EXAMPLE only when the brief asks for them."""
    slots = ["LIVE"]
    if getattr(brief, "include_reserve", False):
        slots.append("RESERVE")
    if getattr(brief, "include_example", False):
        slots.append("EXAMPLE")
    return tuple(slots)


_FORMAT_ALIASES = {"CONNECT_4": "CONNECT", "THREE_CLUE": "STEPPED_CLUE", "QA": "STANDARD_QA", "SLIDE_QA": "STANDARD_QA",
                   "MULTIPLE_CHOICE": "MCQ"}


def format_counts(bank: List[Dict[str, Any]]) -> Dict[str, int]:
    """How many questions of each format a bank holds (old names folded into the new ones)."""
    out: Dict[str, int] = {}
    for q in bank:
        f = (q.get("question_format") or "STANDARD_QA").upper()
        f = _FORMAT_ALIASES.get(f, f)
        out[f] = out.get(f, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _load_bank(db: Session, slots: Optional[tuple] = None) -> List[Dict[str, Any]]:
    slots = tuple(slots) if slots else LIVE_ONLY          # default: live questions only
    ttl = _bank_ttl()
    key = (str(db.get_bind().url), slots) if ttl > 0 else None
    now = time.monotonic()
    if ttl > 0 and _BANK_CACHE["bank"] is not None and _BANK_CACHE["key"] == key and now - _BANK_CACHE["at"] < ttl:
        return [dict(r) for r in _BANK_CACHE["bank"]]      # callers may add fields (e.g. score) to what they get
    bank = _load_bank_uncached(db, slots)
    if ttl > 0:
        _BANK_CACHE.update({"at": now, "key": key, "bank": bank})
        return [dict(r) for r in bank]
    return bank


def _latest_tags(db: Session) -> Dict[str, Any]:
    """Per question, the tag the bank uses: the newest confirmed one, else the newest draft. Chosen in SQL so only
    one row per question is loaded (it used to read every tag row, superseded ones included, and sort in Python)."""
    rank = func.row_number().over(
        partition_by=QshalaTag.question_id,
        order_by=(case((QshalaTag.status == "confirmed", 1), else_=0).desc(),
                  QshalaTag.created_at.desc(), QshalaTag.id.desc())).label("rn")
    ranked = (db.query(QshalaTag.id.label("tid"), rank)
              .filter(QshalaTag.status.in_(["confirmed", "draft"])).subquery())
    rows = (db.query(QshalaTag).join(ranked, QshalaTag.id == ranked.c.tid).filter(ranked.c.rn == 1).all())
    return {r.question_id: r for r in rows}


def _load_bank_uncached(db: Session, slots: tuple = LIVE_ONLY) -> List[Dict[str, Any]]:
    # The 768-number embedding vector is never read here; leaving it out roughly halves what is fetched per row.
    qs = (db.query(Question).options(defer(Question.embedding), defer(Question.embedding_model))
          .filter((Question.duplicate_status != "CONFIRMED_DUPLICATE") | (Question.duplicate_status.is_(None)))
          .filter(Question.slot.in_(list(slots)) | Question.slot.is_(None)).all())
    tags: Dict[str, Any] = {}
    try:
        if QshalaTag is None:
            raise RuntimeError("no tag table")
        tags = _latest_tags(db)
    except Exception:
        tags = {}

    meta: Dict[str, Any] = {}
    if QshalaQuestionMetadata is not None:
        try:
            mrows = db.query(
                QshalaQuestionMetadata.question_id,
                QshalaQuestionMetadata.city_name,
                QshalaQuestionMetadata.geo_region,
                QshalaQuestionMetadata.primary_audience,
            ).all()
            for m in mrows:
                meta[m.question_id] = m
        except Exception:
            meta = {}

    docs_map = {}
    docs_filename_map = {}
    try:
        from backend.app.models.document import Document
        for d in db.query(Document.id, Document.title, Document.filename).all():
            docs_map[d.id] = (d.title or d.filename or "")
            docs_filename_map[d.id] = (d.filename or "")
    except Exception:
        docs_map = {}
        docs_filename_map = {}

    bank = []
    for q in qs:
        # Exclude questions with empty text unless they have valid visual clues
        q_text_clean = (q.question_text or "").strip()
        has_img = bool(getattr(q, 'question_images', None) or getattr(q, 'image_refs', None))
        if len(q_text_clean) < 8 and not has_img:
            continue
        t = tags.get(q.id)
        m = meta.get(q.id)
        bank.append({
            "id": q.id,
            "question_text": q.question_text,
            "answer": q.answer,
            "topic_l1": (getattr(t, 'topic_l1', None) if t else None) or q.topic,
            "subtopic_l2": (getattr(t, 'subtopic_l2', None) if t else None) or q.subtopic,
            "secondary_l2": (getattr(t, 'secondary_l2', None) if t else None) or [],
            "keywords": (getattr(t, 'keywords_l3', None) if t and getattr(t, 'keywords_l3', None) else None) or (q.tags or []),
            "india_international": getattr(t, 'india_international', None) if t else None,
            "shelf_life": (getattr(t, 'shelf_life', None) if t else None) or ("TIME_SENSITIVE" if getattr(q, "temporal_nature", None) == "TIME_SENSITIVE" else "EVERGREEN"),
            "sensitivity": (getattr(t, 'sensitivity', None) if t and getattr(t, 'sensitivity', None) else None) or ("NONE" if (t or any(a in (getattr(q, 'audience_suitability', []) or []) for a in ("primary", "middle_school", "high_school", "general"))) else None),
            "image_dependent": has_img,
            "question_images": list(getattr(q, 'question_images', []) or []),
            "answer_images": list(getattr(q, 'answer_images', []) or []),
            "image_refs": list(getattr(q, 'image_refs', []) or []),
            "search_summary": getattr(t, 'search_summary', None) if t else None,
            "difficulty": (q.difficulty or "Medium"),
            "difficulty_score": q.difficulty_score if q.difficulty_score is not None else 0.5,
            "grade_min": getattr(q, "grade_min", 3),
            "grade_max": getattr(q, "grade_max", 12),
            "audience_suitability": list(getattr(q, "audience_suitability", []) or []),
            "document_id": q.document_id,
            "document_title": docs_map.get(q.document_id, ""),
            "document_filename": docs_filename_map.get(q.document_id, ""),
            "tagged": bool(t),
            "question_format": getattr(q, 'question_format', None) or getattr(q, 'question_type', 'STANDARD_QA'),
            "format_payload": getattr(q, 'format_payload', None) or getattr(q, 'format_data', None),
            "slot": getattr(q, 'slot', None) or "LIVE",
            "play_mode": getattr(q, 'play_mode', None),
            "topic_label": getattr(q, 'topic_label', None),
            "city_name": (getattr(t, 'city_name', None) if t else None) or (getattr(m, 'city_name', None) if m else None),
            "geo_region": (getattr(t, 'geo_region', None) if t else None) or (getattr(m, 'geo_region', None) if m else None),
            "primary_audience": getattr(m, 'primary_audience', None) if m else None,
        })
    return bank


# Minimum BM25 score to be considered relevant. Questions below this
# matched the query only by accident (a stray word overlap).
MIN_RELEVANCE_SCORE = 0.4


def librarian(bank: List[Dict[str, Any]], brief: Brief, pool: int = 80) -> List[Dict[str, Any]]:
    if brief.format_filter:
        from backend.app.services.prompt_quiz.audience import canon_format
        want = canon_format(brief.format_filter)
        bank = [q for q in bank if canon_format(q.get("question_format")) == want]
    idx = Index(bank)

    # Gemini search_terms broaden retrieval without changing the Brief the user sees
    search_query = brief.query
    if getattr(brief, 'search_terms', None):
        search_query = brief.query + " " + " ".join(brief.search_terms)

    hits = idx.search(search_query, brief.filters, top_k=pool)

    # ── relevance floor: drop questions with no real term match ──
    if brief.query:
        threshold = 0.2 if len(bank) == 1 else MIN_RELEVANCE_SCORE
        real = [h for h in hits if h.get("score", 0) >= threshold]
        if len(real) >= brief.count:
            hits = real  # enough strong matches — use only those
        elif real:
            hits = real  # some strong matches — use them (may be < count, pad later)
        # else: zero real matches — keep original hits so stage-2/3 fallback can pad

    if len(hits) < brief.count:  # Stage 2: relax hard filters other than safety
        relaxed = {k: v for k, v in brief.filters.items() if k in (
            "school_safe", "exclude_image", "no_images", "evergreen_only", "evergreen", "india_international"
        )}
        seen = {h["id"] for h in hits}
        extra = [h for h in idx.search(search_query, relaxed, top_k=pool) if h["id"] not in seen]
        if brief.query:
            threshold = 0.2 if len(bank) == 1 else MIN_RELEVANCE_SCORE
            extra = [h for h in extra if h.get("score", 0) >= threshold]
        hits += extra

    if len(hits) < brief.count and brief.query and getattr(brief, "cross_topic_padding", False):
        # Stage 3: Not enough matches — pad with RELATED questions, not random ones.
        # Use topic/subtopic from whatever DID match to find neighbours.
        # IMPORTANT: padding scores must be CAPPED below the lowest real match score,
        # so the editor always ranks real matches above padding.
        min_real_score = min((h.get("score", 0) for h in hits), default=0.1) if hits else 0.1
        pad_cap = max(min_real_score * 0.5, 0.05)  # padding scores stay below real matches

        seen = {h["id"] for h in hits}
        relaxed = {k: v for k, v in brief.filters.items() if k in (
            "school_safe", "exclude_image", "no_images", "evergreen_only", "evergreen", "india_international"
        )}

        # 3a: Try searching by the topics of matched questions (contextual padding)
        if hits:
            neighbour_topics = set()
            for h in hits:
                if h.get("topic_l1"):
                    neighbour_topics.add(h["topic_l1"].lower())
                if h.get("subtopic_l2"):
                    neighbour_topics.add(h["subtopic_l2"].lower())
            if neighbour_topics:
                topic_query = " ".join(list(neighbour_topics)[:5])
                topic_hits = idx.search(topic_query, relaxed, top_k=pool)
                threshold = 0.2 if len(bank) == 1 else MIN_RELEVANCE_SCORE
                topic_hits = [h for h in topic_hits if h["id"] not in seen and h.get("score", 0) >= threshold]
                for h in topic_hits:
                    h["score"] = pad_cap  # cap so padding never outranks real matches
                hits += topic_hits
                seen.update(h["id"] for h in topic_hits)

        # 3b: Still short — fill with shuffled broad pool (diverse, not same every time)
        if len(hits) < brief.count:
            broad = [h for h in idx.search("", relaxed, top_k=pool) if h["id"] not in seen]
            for h in broad:
                h["score"] = pad_cap * 0.5  # broad padding ranks below contextual padding
            hits += broad

    return hits


# ---------- Guardian ----------
def _fp(text: str) -> frozenset:
    return frozenset(tokenize(text)[:14])


_PLACEHOLDER_ANS_ENTITIES = {
    "", "answer", "answers", "ans", "solution", "solutions",
    "see image", "see video", "see audio", "see explanation",
    "answer indicated on slide", "ಉತ್ತರ",
}


def _normalize_answer_entity(ans: str) -> str:
    """Extracts a normalized core entity token set from an answer string for robust deduplication.
    Returns '' for placeholder, URL, data-URI, or punctuation-only answers so distinct questions
    with non-entity answers never collapse onto one survivor.
    """
    if not ans:
        return ""
    raw_low = ans.strip().lower()
    if raw_low.startswith(("http://", "https://", "data:")):
        return ""
    # Strip common brackets, slashes, parentheticals: e.g. "Srinivasa Ramanujan / Ramanujan" -> "srinivasa ramanujan"
    primary = re.split(r"[/,;(]", ans)[0].strip().lower()
    primary = re.sub(r"^(word|clue|answer|question|letter|name)\s*:\s*", "", primary).strip()
    # Strip common honorifics/prefixes: Dr., Shri, Sir, Smt, The, A, An
    primary = re.sub(r"\b(dr\.?|shri|sir|smt\.?|mr\.?|mrs\.?|ms\.?|the|a|an)\b", "", primary).strip()
    cleaned = re.sub(r"[^\w\s\u0900-\u0FFF]", "", primary)
    norm_check = re.sub(r"\s+", " ", cleaned).strip()
    if norm_check in _PLACEHOLDER_ANS_ENTITIES:
        return ""
    # Transliteration compression: oo -> u, ee -> i (e.g. Sooryavanshi -> Suryavanshi)
    cleaned = re.sub(r"oo+", "u", cleaned)
    cleaned = re.sub(r"ee+", "i", cleaned)
    tokens = [t for t in cleaned.split() if t]
    joined = " ".join(tokens)
    return "" if joined in _PLACEHOLDER_ANS_ENTITIES else joined


def guardian(hits: List[Dict[str, Any]], brief: Brief, exclude_ids: Optional[Set[str]] = None) -> List[Dict[str, Any]]:
    # Same rule as before (drop a hit whose first-14-token set overlaps an earlier kept hit by more than 70%), but
    # only earlier hits that share a token are compared, via a token -> kept-hits index, instead of every pair.
    out, seen = [], []
    seen_entities: Set[str] = set()
    by_token: Dict[str, List[int]] = {}
    common_geo = {"bengaluru", "bangalore", "mumbai", "delhi", "india", "chennai", "kolkata", "hyderabad", "pune"}
    for h in hits:
        if exclude_ids and h["id"] in exclude_ids:
            continue
        # Entity / answer deduplication: avoid multiple questions testing the exact same entity
        ent = _normalize_answer_entity(h.get("answer") or "")
        if ent and ent not in common_geo and ent in seen_entities:
            continue
        fp = _fp(h["question_text"])
        if fp:
            candidates = {i for tok in fp for i in by_token.get(tok, ())}
            if any(len(fp & seen[i]) / max(len(fp | seen[i]), 1) > 0.7 for i in candidates):
                continue
        seen.append(fp)
        for tok in fp:
            by_token.setdefault(tok, []).append(len(seen) - 1)
        if ent:
            seen_entities.add(ent)
        out.append(h)
    return out


# ---------- Editor ----------
def editor(cands: List[Dict[str, Any]], brief: Brief, preserve_slot1_id: Optional[str] = None) -> List[Dict[str, Any]]:
    n = brief.count
    ordered_cands = list(cands)

    def _rel(c: Dict[str, Any]) -> float:
        rv = c.get("_rank_score")
        return float(rv) if rv is not None else float(c.get("score") or 0.0)

    # Primary: ranked relevance score (_rank_score if computed by build_draft, else BM25 score).
    has_specific_topic = bool(brief.topics_l1 or brief.subtopics_l2 or brief.locality or (brief.query and brief.query.lower() not in ("general knowledge", "trivia", "")))
    if not has_specific_topic and brief.hard_bias == "hard":
        ordered_cands.sort(key=lambda c: (-(c.get("difficulty_score") or 0.5), -_rel(c)))
    elif not has_specific_topic and brief.hard_bias == "easy":
        ordered_cands.sort(key=lambda c: ((c.get("difficulty_score") or 0.5), -_rel(c)))
    elif brief.hard_bias == "hard":
        ordered_cands.sort(key=lambda c: (-_rel(c), -(c.get("difficulty_score") or 0.5)))
    elif brief.hard_bias == "easy":
        ordered_cands.sort(key=lambda c: (-_rel(c), (c.get("difficulty_score") or 0.5)))
    else:
        ordered_cands.sort(key=lambda c: -_rel(c))

    # Only filter by subtopics when the brief carries bank-validated L2 names,
    # or when a non-geographic query explicitly names a full subtopic (e.g. query 'cricket' -> subtopic 'Cricket').
    target_subs = set(brief.subtopics_l2)
    # Only filter by subtopics when the brief explicitly asked for them,
    # never when coming from dynamic intent where BM25 semantic expansion drives relevance.
    if not target_subs and brief.query and not brief.locality and getattr(brief, "source", None) != "gemini_dynamic_intent":
        q_tokens = {w for w in brief.query.lower().split() if len(w) > 3}
        for c in cands:
            sub = c.get("subtopic_l2")
            if sub and sub.lower() in q_tokens:
                target_subs.add(sub)

    if target_subs and not brief.cross_topic_padding and getattr(brief, "source", None) != "gemini_dynamic_intent":
        sub_matched = [c for c in ordered_cands if c.get("subtopic_l2") in target_subs or not c.get("subtopic_l2")]
        prompt_low = (brief.prompt or "").lower()
        matched_in_prompt = {
            s for s in target_subs
            if s and s.lower() in prompt_low and any(c.get("subtopic_l2") == s for c in ordered_cands)
        }
        if (
            len(sub_matched) >= n
            or (matched_in_prompt and sub_matched and getattr(brief, "source", None) != "gemini")
            or (len(target_subs) == 1 and matched_in_prompt and sub_matched)
        ):
            ordered_cands = sub_matched

    chosen: List[Dict[str, Any]] = []
    chosen_entities: Set[str] = set()
    common_geo = {"bengaluru", "bangalore", "mumbai", "delhi", "india", "chennai", "kolkata", "hyderabad", "pune"}
    per_sub: Dict[str, int] = {}
    cap = max(2, -(-n // 3))  # no subtopic takes more than ~a third
    leftovers = []
    for c in ordered_cands:
        ent = _normalize_answer_entity(c.get("answer") or "")
        if ent and ent not in common_geo and ent in chosen_entities:
            continue
        sub = c.get("subtopic_l2") or c.get("topic_l1") or "?"
        if sub not in target_subs and per_sub.get(sub, 0) >= cap:
            leftovers.append(c)
            continue
        per_sub[sub] = per_sub.get(sub, 0) + 1
        chosen.append(c)
        if ent:
            chosen_entities.add(ent)
        if len(chosen) == n:
            break
    for c in leftovers:
        if len(chosen) >= n:
            break
        ent = _normalize_answer_entity(c.get("answer") or "")
        if ent and ent in chosen_entities:
            continue
        chosen.append(c)
        if ent:
            chosen_entities.add(ent)
    if brief.ramp:
        chosen.sort(key=lambda c: c.get("difficulty_score") or 0.5)
        if brief.hard_bias == "hard":
            chosen.sort(key=lambda c: -(c.get("difficulty_score") or 0.5))
    if preserve_slot1_id:
        for idx_s, c in enumerate(chosen):
            if c.get("id") == preserve_slot1_id and idx_s != 0:
                chosen.insert(0, chosen.pop(idx_s))
                break
    return chosen


# ---------- Host ----------
def host(brief: Brief, picked: List[Dict[str, Any]], bank_size: int) -> str:
    if not picked:
        return "I couldn't find anything for that in the bank yet. Try a broader topic."
    # Prefer the Interpreter's witty framing when it produced one and the draft is complete.
    if brief.framing and len(picked) >= brief.count:
        return brief.framing
    subs = {}
    for p in picked:
        s = p.get("subtopic_l2") or p.get("topic_l1")
        if s:
            subs[s] = subs.get(s, 0) + 1
    top = ", ".join(s for s, _ in sorted(subs.items(), key=lambda x: -x[1])[:3])
    short = "" if len(picked) >= brief.count else f" (only {len(picked)} of {brief.count} matched)"
    ramp = "easy to hard" if brief.hard_bias != "hard" else "hardest first"
    return f"{len(picked)} questions{short}, ordered {ramp}. Mostly {top}."


def _view(c: Dict[str, Any]) -> Dict[str, Any]:
    return {k: c.get(k) for k in ("id", "question_text", "answer", "topic_l1", "subtopic_l2", "difficulty_score",
                                  "difficulty", "image_dependent", "shelf_life", "tagged", "score", "document_id",
                                  "document_title", "question_images", "answer_images", "image_refs",
                                  "question_format", "format_payload", "slot", "play_mode", "topic_label")}


_TOPICAL_NOISE_WORDS = {
    "india", "indian", "bharat", "karnataka", "bengaluru", "bangalore",
    "mumbai", "bombay", "delhi", "chennai", "madras", "kolkata", "calcutta",
    "hyderabad", "pune", "mysore", "mysuru", "state", "board", "grade",
    "students", "student", "relevant", "around", "one", "middle", "primary",
    "high", "school", "kids", "children", "friendly", "focus", "focused",
    "based", "syllabus", "curriculum", "cbse", "icse", "ncert", "class",
}

def _detect_deck_scope(prompt_lower: str, bank: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Detect distinctive deck-series names and typos (e.g. 'agastya', 'aghtya', 'set 2 prelims') in the prompt."""
    from backend.app.services.source_grouping import resolve_deck_query

    docs_by_id: Dict[str, Dict[str, str]] = {}
    subtopic_words: Set[str] = set()
    for b in bank:
        did = str(b.get("document_id") or "").strip()
        if did and did not in docs_by_id:
            docs_by_id[did] = {
                "id": did,
                "title": str(b.get("document_title") or "").strip(),
                "filename": str(b.get("document_filename") or "").strip(),
            }
        subtopic_words.update(tokenize(b.get("subtopic_l2") or ""))

    if not docs_by_id:
        return {"doc_ids": [], "tokens": [], "matched_input_tokens": [], "labels": [], "remaining_query": ""}

    v6_words = {w for t in V6_THEMES for w in tokenize(t)}
    return resolve_deck_query(
        prompt_lower,
        list(docs_by_id.values()),
        extra_excluded=(v6_words | subtopic_words),
        is_prompt=True,
    )


def _detect_deck_filter_tokens(prompt_lower: str, bank: List[Dict[str, Any]]) -> List[str]:
    """Detect distinctive deck-series names (e.g. 'agastya') present in both prompt and bank document titles."""
    return _detect_deck_scope(prompt_lower, bank)["tokens"]


def build_draft(db: Session, prompt: str, count: Optional[int] = None,
                options: Optional[Dict[str, Any]] = None,
                previous_brief: Optional[Dict[str, Any]] = None,
                exclude_ids: Optional[Set[str]] = None,
                current_questions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    brief = interpret(prompt, count, options, previous_brief, current_questions=current_questions, db=db)

    # Geographic/locality queries: ONLY when the user's prompt is strictly asking for geographic trivia,
    # NOT when geography is just a qualifier for a subject (e.g. "people in india", "food in delhi", "startups in bangalore").
    geo_names = {"karnataka", "bengaluru", "bangalore", "mumbai", "delhi", "chennai", "pune", "hyderabad", "kolkata", "india", "bharat"}
    q_clean = (brief.query or "").lower().strip()
    p_lower_prompt = (brief.prompt or "").lower().strip()

    # Detect if the prompt has an actual topical subject beyond geography
    non_geo_signals = (
        "people", "person", "personality", "personalities", "figure", "figures", "leader", "leaders",
        "scientist", "scientists", "science", "scientific", "experiment", "experiments",
        "physics", "chemistry", "biology", "space", "astronomy", "agastya", "environment",
        "nature", "health", "mathematics", "math",
        "inventor", "inventors", "author", "authors", "food", "dish", "cuisine",
        "startup", "startups", "company", "companies", "brand", "brands", "tech", "technology", "ai",
        "finance", "bank", "banking", "cricket", "sport", "sports", "movie", "movies", "film", "music",
        "history", "heritage", "art", "law", "court", "women", "symbol", "symbols", "freedom", "education"
    )
    has_topical_subject = any(s in p_lower_prompt or s in q_clean for s in non_geo_signals)

    is_pure_geo = (not has_topical_subject) and (
        q_clean in geo_names
        or (
            q_clean in ("", "general knowledge", "trivia", "quiz")
            and any(re.search(r"\b" + g + r"\b", p_lower_prompt) for g in ["karnataka", "bengaluru", "bangalore", "mumbai", "delhi", "chennai", "pune", "hyderabad", "kolkata"])
        )
    )
    if is_pure_geo:
        brief.topics_l1 = []
        brief.subtopics_l2 = []
        if brief.locality:
            brief.query = brief.locality
        elif any(g in p_lower_prompt for g in ["india", "bharat"]):
            brief.query = "india"
        brief.search_terms = []

    # Broad / vague prompts: "10 questions", "give me 5 questions", "quiz for tonight", "surprise me with 5 questions"
    p_lower = normalize_compound_typos(brief.prompt or "").lower().strip()
    if re.search(r"\b(?:all|any)\s+(?:sources?|decks?|presentations?)\b", p_lower):
        brief.filters.pop("deck_filter", None)
        brief.filters.pop("document_ids", None)
        brief.filters.pop("source_labels", None)

    is_vague = not (
        brief.topics_l1 or brief.subtopics_l2 or brief.locality
        or brief.filters.get("deck_filter") or brief.filters.get("document_ids")
    ) and (
        bool(re.search(r"^(?:give\s+me\s+)?(?:\d+|[a-z]+)?\s*questions?$", p_lower))
        or bool(re.search(r"\b(surprise(?:\s+me)?|tonight|random|quick|standard)\b", p_lower))
        or (brief.query or "").lower() in ("general knowledge", "trivia", "general", "surprise", "tonight", "", "evergreen", "evergreen only")
    ) and not any(k in p_lower for k in (
        "on ", "about ", "finance", "bank", "sport", "cricket", "tech", "food", "ai",
        "brand", "startup", "delhi", "mumbai", "karnataka", "india", "tata", "paytm",
        "jalebi", "science", "experiment", "experiments", "agastya", "physics", "chemistry", "biology",
    ))
    if is_vague:
        brief.query = ""
        brief.search_terms = []

    bank = _load_bank(db, slots_for(brief))

    # Data-driven deck/series filter: when prompt names a distinctive deck series from the bank
    # (e.g. "Agastya", "aghtya", or "Agastya Set 2 Prelims"), restrict retrieval to matching decks.
    matched_source_input_tokens: Set[str] = set()
    if not brief.filters.get("deck_filter") and not brief.filters.get("document_ids"):
        detected_scope = _detect_deck_scope(p_lower, bank)
        if detected_scope.get("tokens"):
            brief.filters["deck_filter"] = detected_scope["tokens"]
        if detected_scope.get("doc_ids"):
            brief.filters["document_ids"] = detected_scope["doc_ids"]
        if detected_scope.get("labels"):
            brief.filters["source_labels"] = detected_scope["labels"]
        matched_source_input_tokens = set(detected_scope.get("matched_input_tokens") or [])
    elif brief.filters.get("document_ids") and not brief.filters.get("source_labels"):
        from backend.app.services.source_grouping import derive_source_series
        sel_ids = {str(x) for x in (brief.filters.get("document_ids") or [])}
        sel_docs = {}
        for b in bank:
            bid = str(b.get("document_id") or "")
            if bid in sel_ids and bid not in sel_docs:
                sel_docs[bid] = (b.get("document_title") or "", b.get("document_filename") or "")
        if len(sel_docs) == 1:
            t_only, f_only = next(iter(sel_docs.values()))
            brief.filters["source_labels"] = [t_only or f_only]
        elif sel_docs:
            ser_counts: Dict[str, int] = {}
            for t_d, f_d in sel_docs.values():
                ser = derive_source_series(t_d, f_d)
                ser_counts[ser] = ser_counts.get(ser, 0) + 1
            brief.filters["source_labels"] = [
                f"{ser} ({cnt} decks)" if cnt > 1 else ser
                for ser, cnt in sorted(ser_counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
            ]

    deck_filter_tokens = {str(t).strip().lower() for t in (brief.filters.get("deck_filter") or []) if str(t).strip()}
    source_drop_tokens = deck_filter_tokens | matched_source_input_tokens
    has_source_filter = bool(deck_filter_tokens or brief.filters.get("document_ids"))

    # 3. Librarian: search broadly across the entire bank
    idx = Index(bank)
    raw_search_query = brief.query or ""
    if getattr(brief, 'search_terms', None):
        raw_search_query = (raw_search_query + " " + " ".join(brief.search_terms)).strip()

    # Check if the brief's own query has genuine topical words left after removing source/deck names & noise words
    query_topical_tokens = [
        w for w in tokenize(brief.query or "")
        if w not in (_TOPICAL_NOISE_WORDS | source_drop_tokens)
    ]
    has_non_source_topic = bool(query_topical_tokens) and not is_pure_geo and not is_vague

    # Strip geo/meta wrapper tokens and deck-filter names from topical search_query when topical words exist,
    # so "karnataka", "india", "state", "board", or deck name/typo alone doesn't distort relevance scoring.
    has_non_geo_topic = has_topical_subject and not is_pure_geo and not is_vague and (not has_source_filter or has_non_source_topic)
    if has_source_filter and not has_non_source_topic:
        # User only asked for a source/deck (e.g. "10 questions from aghtya") -> broad search inside those decks
        search_query = ""
        effective_query_for_floor = ""
        brief.query = ""
        brief.search_terms = []
        brief.topics_l1 = []
        brief.subtopics_l2 = []
    elif (has_non_geo_topic or source_drop_tokens) and raw_search_query:
        drop_from_topical = _TOPICAL_NOISE_WORDS | source_drop_tokens
        filtered_words = [
            w for w in tokenize(raw_search_query)
            if w not in drop_from_topical
        ]
        search_query = " ".join(filtered_words) if filtered_words else raw_search_query
        effective_query_for_floor = " ".join(query_topical_tokens) if (has_source_filter and query_topical_tokens) else brief.query
    else:
        search_query = raw_search_query
        effective_query_for_floor = "" if (has_source_filter and not has_non_geo_topic) else brief.query

    pool_cap = max(300, len(bank))
    hits = idx.search(search_query, filters=None, top_k=pool_cap)

    # Drop hits below the relevance floor from on-topic retrieval
    if effective_query_for_floor:
        hits = [h for h in hits if float(h.get("score") or 0.0) >= MIN_RELEVANCE_SCORE]

    # Domain-coherence guard for pure STEM / Science queries: prevent commercial banking,
    # constitutional/political, or pure sports/entertainment items with stray incidental keyword
    # matches from leaking into Science/Experiments quizzes (e.g. on "Focus on India").
    _stem_query_words = (
        "science", "scientific", "experiment", "experiments",
        "physics", "chemistry", "biology", "astronomy",
    )
    _non_stem_prompt_hints = (
        "business", "finance", "banking", "bank", "company", "startup", "corporate",
        "politics", "political", "constitution", "government", "election", "law",
        "sport", "sports", "cricket", "movie", "cinema", "bollywood", "music", "logo",
    )
    _query_plus_prompt = f"{(brief.query or '').lower()} {p_lower}"
    _is_pure_stem_ask = any(w in _query_plus_prompt for w in _stem_query_words) and not any(
        w in _query_plus_prompt for w in _non_stem_prompt_hints
    )
    if _is_pure_stem_ask and not deck_filter_tokens:
        _NON_STEM_L1 = {"finance", "politics", "business", "economy", "sports", "entertainment"}
        _CORE_STEM_TERMS = {
            "science", "scientific", "experiment", "experiments", "physics", "chemistry",
            "biology", "astronomy", "laboratory", "gravity", "magnet", "magnetism", "acid",
            "base", "refraction", "reflection", "friction", "irrigation", "photosynthesis",
            "microscope", "telescope", "lens", "mirror", "molecule", "atom", "cell",
            "potassium", "plaster", "thermometer", "stethoscope", "circuit", "electricity",
            "isro", "chandrayaan", "mangalyaan", "gaganyaan", "scientist", "rocket",
        }
        coherent_hits = []
        for h in hits:
            l1_low = (h.get("topic_l1") or "").strip().lower()
            if l1_low in _NON_STEM_L1:
                h_blob = " ".join([
                    str(h.get("question_text") or ""),
                    str(h.get("answer") or ""),
                    str(h.get("subtopic_l2") or ""),
                    " ".join(str(k) for k in (h.get("keywords") or [])),
                ]).lower()
                h_toks = set(tokenize(h_blob))
                if not (h_toks & _CORE_STEM_TERMS):
                    continue
            coherent_hits.append(h)
        hits = coherent_hits

    # Broad hits for adjacent pool only (tagged with score capped below real matches)
    if len(hits) < pool_cap:
        min_real_score = min((float(h.get("score") or 0.0) for h in hits), default=0.1) if hits else 0.1
        pad_cap = min(max(min_real_score * 0.5, 0.05), MIN_RELEVANCE_SCORE - 0.05)
        seen = {h["id"] for h in hits}
        broad = idx.search("", filters=None, top_k=pool_cap)
        for b in broad:
            if b["id"] not in seen:
                b_adj = dict(b)
                b_adj["score"] = round(min(pad_cap * 0.5, 0.2), 3) if effective_query_for_floor else 1.0
                hits.append(b_adj)

    # Split into on-topic vs adjacent
    on_ok, adjacent, on_all, effective_l2s = split_pools(
        hits,
        l1s=brief.topics_l1,
        l2s=brief.subtopics_l2,
        filters=brief.filters,
        format_filter=brief.format_filter,
    )
    # Use bank-validated subtopics so the editor never filters by
    # Gemini-invented names that don't exist in the bank
    brief.subtopics_l2 = effective_l2s

    # Preserve top-matching currently displayed questions on constraint refinements (e.g. "School friendly")
    has_topic_swap = bool(re.search(
        r"\b(?:replace|swap|change|switch)\b.*\b(?:to|with|for|into)\b|\binstead\s+of\b",
        p_lower,
    ))
    is_continuity_refinement = bool(previous_brief and current_questions and not has_topic_swap)
    current_slot_bonus: Dict[str, float] = {}
    preserve_slot1_id: Optional[str] = None
    if is_continuity_refinement:
        for slot_idx, cq in enumerate(current_questions):
            cq_id = cq.get("id")
            if cq_id:
                current_slot_bonus[cq_id] = max(0.15, 0.85 - 0.08 * slot_idx)
        preserve_slot1_id = current_questions[0].get("id") if current_questions else None

    # 4. Score each pool item
    has_topical_query = bool((brief.query or "").strip()) and not is_pure_geo and not is_vague

    def _rank_cand(c: Dict[str, Any]) -> float:
        base = float(c.get("score") or 0.0)
        aud = audience_score(
            c,
            locality=brief.locality,
            india_focused=brief.filters.get("india_focused"),
            audience=brief.filters.get("audience"),
            tone=brief.tone,
        )
        if has_topical_query and aud > 0:
            aud = min(aud, max(0.5, 0.5 * base))
        diff_pen = difficulty_penalty(c, brief.hard_bias)
        cont = current_slot_bonus.get(c.get("id"), 0.0) if diff_pen < 0.7 else 0.0
        return base + aud - 0.35 * diff_pen + cont

    for c in on_ok:
        c["_rank_score"] = _rank_cand(c)
    for c in adjacent:
        c["_rank_score"] = _rank_cand(c)
    on_ok.sort(key=lambda c: c["_rank_score"], reverse=True)
    adjacent.sort(key=lambda c: c["_rank_score"], reverse=True)

    # 5. Guardian + Editor on ranked on-topic list
    effective_exclude = set(exclude_ids or set())
    cands = guardian(on_ok, brief, exclude_ids=effective_exclude)

    narrative_arc: Optional[str] = None
    picked: List[Dict[str, Any]] = []

    cur_res = None
    # AI Curator: Evaluate candidates for genuine relevance and curate narrative flow
    if cands and os.getenv("ENABLE_AI_CURATOR", "true").lower() in ("1", "true", "yes"):
        try:
            from backend.app.services.prompt_quiz.curator_llm import curate_quiz_with_gemini
            # Cap curator candidates at top 35 to prevent token latency timeouts under serverless
            curator_pool = cands[:35]
            cur_res = curate_quiz_with_gemini(
                prompt=brief.prompt,
                target_count=brief.count,
                candidates=curator_pool,
                current_questions=current_questions,
                previous_brief=brief.public(),
            )
            if cur_res is not None and "selected_ids" in cur_res:
                cands_by_id = {c["id"]: c for c in curator_pool if c.get("id")}
                seen_cur_ents: Set[str] = set()
                seen_picked_ids: Set[str] = set()
                deduped_cur_picked = []
                matched_before_dedup = 0
                common_geo = {"bengaluru", "bangalore", "mumbai", "delhi", "india", "chennai", "kolkata", "hyderabad", "pune"}
                for qid in cur_res["selected_ids"]:
                    if qid in cands_by_id:
                        matched_before_dedup += 1
                        c = cands_by_id[qid]
                        ent = _normalize_answer_entity(c.get("answer") or "")
                        if ent and ent not in common_geo and ent in seen_cur_ents:
                            continue
                        if ent:
                            seen_cur_ents.add(ent)
                        seen_picked_ids.add(qid)
                        deduped_cur_picked.append(c)
                # Replenish only slots lost to duplicate-entity filtering (preserves honest 0/partial counts).
                target_after_dedup = min(brief.count, matched_before_dedup)
                if len(deduped_cur_picked) < target_after_dedup:
                    for c in cands:
                        cid = c.get("id")
                        if not cid or cid in seen_picked_ids:
                            continue
                        ent = _normalize_answer_entity(c.get("answer") or "")
                        if ent and ent not in common_geo and ent in seen_cur_ents:
                            continue
                        if ent:
                            seen_cur_ents.add(ent)
                        seen_picked_ids.add(cid)
                        deduped_cur_picked.append(c)
                        if len(deduped_cur_picked) >= target_after_dedup:
                            break
                picked = deduped_cur_picked
                narrative_arc = cur_res.get("narrative_arc")
        except Exception as e:
            logger.warning(f"AI Curator invocation failed ({e}), falling back to deterministic editor")

    if not picked and (cur_res is None or not has_topical_query):
        picked = editor(cands, brief, preserve_slot1_id=preserve_slot1_id)
        if picked and cur_res is not None:
            narrative_arc = None

    # 6. Padding if requested and short
    padded = 0
    if brief.cross_topic_padding and len(picked) < brief.count:
        needed = brief.count - len(picked)
        picked_ids = effective_exclude | {p["id"] for p in picked}
        adj_cands = guardian(adjacent, brief, exclude_ids=picked_ids)
        adj_brief = Brief(**{**asdict(brief), "count": needed})
        adj_picked = editor(adj_cands, adj_brief)
        picked.extend(adj_picked)
        padded = len(adj_picked)

    # 7. Fulfillment
    blocked = blocked_counts(on_all, brief.filters, brief.format_filter)
    fulfillment = build_fulfillment(
        requested=brief.count,
        picked=picked,
        padded=padded,
        adjacent_available=len(adjacent),
        blocked=blocked,
        hard_bias=brief.hard_bias,
        padding_allowed=brief.cross_topic_padding,
    )

    # 8. Host message
    msg = fulfillment["note"] if fulfillment.get("short") else host(brief, picked, len(bank))

    stages = ["Interpreter", "Librarian", "Guardian"]
    if narrative_arc:
        stages.append("Curator")
    else:
        stages.append("Editor")
    stages.append("Host")

    brief.narrative_arc = narrative_arc

    return {
        "brief": brief.public(),
        "questions": [_view(p) for p in picked],
        "message": msg,
        "bank_size": len(bank),
        "stages": stages,
        "fulfillment": fulfillment,
        "narrative_arc": narrative_arc,
    }


def swap_one(db: Session, prompt: str, question_id: str, keep_ids: List[str],
             options: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    brief = interpret(prompt, 1, options)
    bank = _load_bank(db, slots_for(brief))
    p_lower = normalize_compound_typos(prompt or "").lower().strip()
    matched_source_toks: Set[str] = set()
    if not brief.filters.get("deck_filter") and not brief.filters.get("document_ids"):
        detected_scope = _detect_deck_scope(p_lower, bank)
        if detected_scope.get("tokens"):
            brief.filters["deck_filter"] = detected_scope["tokens"]
        if detected_scope.get("doc_ids"):
            brief.filters["document_ids"] = detected_scope["doc_ids"]
        matched_source_toks = set(detected_scope.get("matched_input_tokens") or [])
    source_drop = matched_source_toks | {str(t).strip().lower() for t in (brief.filters.get("deck_filter") or []) if str(t).strip()}
    if source_drop and brief.query:
        brief.query = " ".join(w for w in tokenize(brief.query) if w not in source_drop).strip()
    target = next((b for b in bank if b["id"] == question_id), None)
    if target and target.get("subtopic_l2"):  # prefer a neighbour of the swapped question
        brief.query = f"{brief.query} {target['subtopic_l2']}".strip()
    idx = Index(bank)
    search_query = brief.query
    if getattr(brief, 'search_terms', None):
        cleaned_terms = [w for w in tokenize(" ".join(brief.search_terms)) if w not in source_drop]
        if cleaned_terms:
            search_query = (brief.query + " " + " ".join(cleaned_terms)).strip()
    hits = idx.search(search_query, filters=None, top_k=max(300, len(bank)))
    if brief.query:
        hits = [h for h in hits if float(h.get("score") or 0.0) >= MIN_RELEVANCE_SCORE]

    exclude_ids = set(keep_ids) | {question_id}
    ok_hits = [c for c in hits if c["id"] not in exclude_ids and passes_hard(c, brief.filters, brief.format_filter)]
    if not ok_hits:
        return None

    def _rank(c: Dict[str, Any]) -> float:
        aud = audience_score(
            c,
            locality=brief.locality,
            india_focused=brief.filters.get("india_focused"),
            audience=brief.filters.get("audience"),
            tone=brief.tone,
        )
        diff_pen = difficulty_penalty(c, brief.hard_bias)
        return float(c.get("score") or 0.0) + aud - 0.35 * diff_pen

    ok_hits.sort(key=_rank, reverse=True)
    cands = guardian(ok_hits, brief, exclude_ids=exclude_ids)
    return _view(cands[0]) if cands else None
