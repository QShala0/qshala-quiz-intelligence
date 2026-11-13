"""Dynamic Intuitive Intent Engine powered by Gemini.

Zero-hardcoding architecture:
Transforms natural language prompts into rich semantic retrieval briefs.
Dynamically extracts core intent, corrects typos, generates 15-25 semantic
expansion terms (entities, synonyms, related concepts), and maps to live
vault taxonomy without brittle static Python dictionaries.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Set
from sqlalchemy.orm import Session

from backend.app.services.prompt_quiz.retrieval import normalize_compound_typos, parse_prompt, WORD_NUMS

logger = logging.getLogger(__name__)

INTENT_TIMEOUT_S = float(os.getenv("INTENT_TIMEOUT_S", "4.0"))

_TAXONOMY_CACHE: Dict[str, Any] = {"at": 0.0, "taxonomy": None}
_TAXONOMY_TTL_S = 120.0  # refresh from DB every 2 minutes


def get_live_vault_taxonomy(db: Session) -> Dict[str, List[str]]:
    """Fetches all valid topics and subtopics currently populated in the database."""
    now = time.monotonic()
    if _TAXONOMY_CACHE["taxonomy"] is not None and (now - _TAXONOMY_CACHE["at"]) < _TAXONOMY_TTL_S:
        return _TAXONOMY_CACHE["taxonomy"]

    try:
        from backend.app.models.question import Question
        rows = db.query(Question.topic, Question.subtopic).distinct().all()
        taxonomy: Dict[str, Set[str]] = {}
        for top, sub in rows:
            if top and top.strip():
                t = top.strip()
                taxonomy.setdefault(t, set())
                if sub and sub.strip():
                    taxonomy[t].add(sub.strip())

        result = {k: sorted(list(v)) for k, v in taxonomy.items()}
        _TAXONOMY_CACHE["taxonomy"] = result
        _TAXONOMY_CACHE["at"] = now
        return result
    except Exception as e:
        logger.warning(f"Failed to fetch live vault taxonomy ({e}), using default fallback")
        return {
            "General Knowledge": ["General Trivia", "Modern History", "Space and Astronomy"],
            "Culture": ["Art and Artists", "Heritage", "Festivals and Traditions", "Literature"],
            "Politics": ["Governance and Constitution", "Elections"],
            "Business": ["Brands", "Startups", "Founders", "Advertising", "Automobiles"],
            "Finance": ["Banks and Banking", "Central Banking", "Coins and Notes", "Economics"],
            "Technology": ["Artificial Intelligence", "Consumer Tech and Gadgets", "Inventions and Inventors"],
            "Sports": ["Cricket", "Olympics and Multi-Sport Events", "Other Sports"],
            "Entertainment": ["Celebrities", "Musicians and Bands"],
        }


INTENT_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "normalized_subject": {
            "type": "STRING",
            "description": "The clean, corrected core topic/subject of the quiz, fixing any typos (e.g. 'improtant epople' -> 'important people', 'scinetist' -> 'scientist'). For general, broad, or zero-topic queries (e.g. 'quiz for kids', 'give me 5 questions', 'school quiz', 'family quiz', 'easy questions'), return empty string ''."
        },
        "count": {
            "type": "INTEGER",
            "description": "The number of questions requested (default 10 if unstated)."
        },
        "target_audience": {
            "type": "STRING",
            "enum": ["school", "college", "corporate", "general"],
            "description": "Audience persona. 'school' if kids, school students, class 6-12, or assembly; 'college' for campus/fest; 'corporate' for workplace; else 'general'."
        },
        "india_focused": {
            "type": "BOOLEAN",
            "description": "True if the user asked for Indian context, Indian students, or an Indian city/state."
        },
        "locality": {
            "type": "STRING",
            "nullable": True,
            "description": "Specific city or state if requested (e.g. 'Bengaluru', 'Mumbai', 'Karnataka'), otherwise null."
        },
        "mapped_topics_l1": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "Only set if the user explicitly requested a single specific category (e.g. 'sports only' -> ['Sports']). For multidisciplinary, persona, or thematic prompts (people, freedom fighters, scientists, space missions, pioneers, inventions, history, culture), leave empty [] so all relevant questions can be found."
        },
        "mapped_subtopics_l2": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "Leave empty [] unless the user explicitly requested a specific subtopic by name (e.g. 'cricket', 'cryptocurrency', 'taxation'). DO NOT guess or constrain subtopics for thematic, historical, or persona prompts."
        },
        "expansion_terms": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "15-25 rich semantic search terms: synonyms, famous names/entities, abbreviations, key concepts, and milestones related to the subject. Never include generic words like 'quiz' or 'question'."
        },
        "negative_terms": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "Concepts or motifs that clash with the requested tone or audience (e.g. adult satire, tax loopholes, scams for a school quiz)."
        },
        "format_filter": {
            "type": "STRING",
            "enum": ["CONNECT_4", "STEPPED_CLUE", "ARRANGE_ORDER"],
            "nullable": True,
            "description": "ONLY set if the user explicitly requested a specialized format: 'CONNECT_4' for connect 4; 'STEPPED_CLUE' for 3-clues; 'ARRANGE_ORDER' for ordering. MUST BE NULL/NONE for normal prompts so all formats can be included."
        },
        "hard_bias": {
            "type": "STRING",
            "enum": ["easy", "medium", "hard", "balanced", "spicy"],
            "nullable": True,
            "description": "Difficulty bias if requested or implied by audience."
        },
        "no_images": {
            "type": "BOOLEAN",
            "nullable": True,
            "description": "True if the user asked for text only, no images, without pictures, or non-visual questions."
        },
        "framing": {
            "type": "STRING",
            "description": "A crisp, engaging 1-sentence description of this quiz round."
        }
    },
    "required": ["normalized_subject", "count", "target_audience", "expansion_terms", "framing"]
}


def _build_system_prompt(taxonomy: Dict[str, List[str]]) -> str:
    tax_summary = []
    for l1, l2s in sorted(taxonomy.items()):
        tax_summary.append(f"  - {l1}: {', '.join(l2s[:6])}")
    tax_str = "\n".join(tax_summary)

    return (
        "You are the Lead Quiz Intelligence Architect for QShala, India's premier curiosity and quiz platform.\n"
        "Your task is to intuitively deconstruct the user's natural language quiz prompt into deep semantic search signals.\n\n"
        "LIVE VAULT TAXONOMY (The real categories in the database):\n"
        f"{tax_str}\n\n"
        "CRITICAL RULES:\n"
        "1. INTUITIVE UNDERSTANDING & TYPO CORRECTION:\n"
        "   - Autocorrect colloquialisms, slang, and typos (e.g. 'improtant epople' -> 'important people', 'scinetist' -> 'scientist').\n"
        "   - Distinguish SUBJECT from SCOPE & WRAPPERS: If the user asks for 'grade 6 & 7 karnataka state board students everyday science experiments', the subject is 'everyday science and science experiments', the locality is 'Karnataka', and the audience is school.\n"
        "   - Never include wrapper words ('grade', 'state board', 'students', 'relevant', 'around', 'focus on') in normalized_subject or expansion_terms.\n"
        "   - Never include pure country/city names ('india', 'karnataka') in normalized_subject or expansion_terms when a subject exists.\n"
        "   - Never let a geographic scope erase the subject.\n"
        "   - CRITICAL FOR FOLLOW-UPS: When previous_brief is provided and the prompt is a modifier/adjustment ('Focus on India', 'Make it for middle school kids', 'Make it harder', 'Text only', 'Only questions with images', 'Give me 5 more questions'), you MUST preserve normalized_subject, expansion_terms, locality, and mapped_topics_l1 from previous_brief, changing ONLY the adjusted constraint.\n\n"
        "2. SEMANTIC EXPANSION TERMS (Absolute Priority):\n"
        "   - Generate 15-25 rich, high-affinity search terms including specific famous entities, pioneers, role models, technical terms, or synonyms.\n"
        "   - Example: 'important people in India' -> ['kalam', 'ramanujan', 'modi', 'bose', 'wangchuk', 'chanu', 'scientist', 'inventor', 'pioneer', 'leader', 'achiever', 'author', 'freedom fighter', 'isro', 'president', 'nobel'].\n"
        "   - Example: 'fintech in bangalore' -> ['zerodha', 'phonepe', 'cred', 'navi', 'razorpay', 'upi', 'payments', 'startup', 'fintech', 'kamath'].\n\n"
        "3. CROSS-CUTTING & THEMATIC PROMPTS (Crucial):\n"
        "   - Thematic prompts (e.g. 'freedom fighters', 'scientists', 'space missions and astronauts', 'women pioneers', 'important people', 'underdogs', 'inventions', 'history', 'culture') span multiple disciplines.\n"
        "   - For ANY thematic, persona, or multidisciplinary prompt, set mapped_topics_l1: [] and mapped_subtopics_l2: [] so retrieval searches across the entire archive using your rich expansion_terms.\n"
        "   - ONLY set mapped_topics_l1 or mapped_subtopics_l2 if the user explicitly commanded a specific narrow category (e.g. 'only cricket', 'purely finance', 'taxation questions').\n\n"
        "4. AUDIENCE PERSONA CALIBRATION:\n"
        "   - If target_audience == 'school' (kids, school students, assembly): prioritize inspiring thinkers, creators, national role models, curricular curiosity. Add adult satire, corporate tax avoidance, and adult controversy to negative_terms.\n"
        "   - Output strictly valid JSON conforming to the schema."
    )


def deconstruct_prompt(
    prompt: str,
    db: Session,
    count: Optional[int] = None,
    options: Optional[Dict[str, Any]] = None,
    previous_brief: Optional[Dict[str, Any]] = None,
) -> Optional[Brief]:
    """Deconstructs prompt using Gemini with live taxonomy awareness and rich dynamic expansion."""
    from backend.app.config import settings
    api_key = (
        getattr(settings, "GEMINI_API_KEY", None)
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("GEMINI_KEY")
    )
    if not api_key:
        logger.debug("No GEMINI_API_KEY available; using regex fallback")
        return None

    taxonomy = get_live_vault_taxonomy(db)
    system_text = _build_system_prompt(taxonomy)

    user_payload: Dict[str, Any] = {"prompt": prompt}
    if count:
        user_payload["requested_count"] = count
    if previous_brief:
        user_payload["previous_brief"] = {
            "query": previous_brief.get("query"),
            "count": previous_brief.get("count"),
            "audience": (previous_brief.get("filters") or {}).get("audience"),
            "india_focused": (previous_brief.get("filters") or {}).get("india_focused"),
            "locality": previous_brief.get("locality"),
            "hard_bias": previous_brief.get("hard_bias"),
            "topics_l1": previous_brief.get("topics_l1"),
            "subtopics_l2": previous_brief.get("subtopics_l2"),
            "search_terms": previous_brief.get("search_terms"),
            "filters": previous_brief.get("filters"),
            "format_filter": previous_brief.get("format_filter"),
        }
    if options:
        user_payload["explicit_options"] = options

    user_text = json.dumps(user_payload)

    from backend.app.services.ai.model_config import intent_model
    model = intent_model()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    payload = {
        "contents": [{"parts": [{"text": user_text}]}],
        "system_instruction": {"parts": [{"text": system_text}]},
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": INTENT_SCHEMA,
            "temperature": 0.1,
            "maxOutputTokens": 800,
        },
    }

    try:
        from backend.app.services.prompt_quiz._http import guarded_post
        headers = {"x-goog-api-key": api_key}
        res = guarded_post(url, json=payload, headers=headers, timeout=INTENT_TIMEOUT_S)
        if res.status_code != 200:
            logger.warning(f"Intent engine HTTP {res.status_code} on {model}: {res.text[:120]}")
            return None

        data = res.json()
        cands = data.get("candidates") or []
        if not cands:
            return None

        parts = (cands[0].get("content") or {}).get("parts") or []
        raw_text = ""
        for part in reversed(parts):
            if "text" in part and not part.get("thought", False):
                raw_text = part["text"]
                break
        if not raw_text:
            for part in reversed(parts):
                if "text" in part:
                    raw_text = part["text"]
                    break

        if not raw_text:
            return None

        parsed = json.loads(raw_text)

        # Normalize compound typos (e.g. "quizkarnataka" -> "quiz karnataka")
        p_lower = normalize_compound_typos(prompt or "").lower()
        more_m = re.search(r"\b(?:add|give\s+me\s+)?(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|couple|few)\s+more\b", p_lower)
        fewer_m = re.search(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+fewer\b", p_lower)
        double_m = re.search(r"\bdouble\s+it\b", p_lower)
        halve_m = re.search(r"\bhalve\s+it\b", p_lower)
        has_count_in_prompt = bool(re.search(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty)\s+(?:questions?|qs?)\b", p_lower))

        from backend.app.services.prompt_quiz.retrieval import WORD_NUMS
        if previous_brief and more_m:
            raw_val = more_m.group(1).lower()
            val = int(WORD_NUMS.get(raw_val, raw_val))
            final_count = min(50, (previous_brief.get("count") or 10) + val)
        elif previous_brief and fewer_m:
            raw_val = fewer_m.group(1).lower()
            val = int(WORD_NUMS.get(raw_val, raw_val))
            final_count = max(1, (previous_brief.get("count") or 10) - val)
        elif previous_brief and double_m:
            final_count = min(50, (previous_brief.get("count") or 10) * 2)
        elif previous_brief and halve_m:
            final_count = max(1, (previous_brief.get("count") or 10) // 2)
        elif previous_brief and not count and not has_count_in_prompt:
            final_count = int(previous_brief.get("count") or 10)
        else:
            final_count = int(count or (parsed.get("count") if has_count_in_prompt else (previous_brief.get("count") if previous_brief else parsed.get("count"))) or 10)
        final_count = max(1, min(int(final_count), 50))

        raw_sub = parsed.get("normalized_subject")
        if raw_sub is None:
            raw_sub = prompt
        raw_sub = str(raw_sub).strip()

        clean_sub, _, _ = parse_prompt(raw_sub)
        clean_prompt_topical, _, _ = parse_prompt(prompt or "")

        GENERIC_SUBJECTS = {
            "general knowledge", "general knowledge for kids", "general quiz",
            "general questions", "kids quiz", "school quiz", "family quiz",
            "trivia", "quiz", "questions", "general", "general gk", "gk",
            "general trivia", "kids trivia", "middle school quiz", "middle school",
            "school friendly", "india focus", "focus on india", "india",
        }
        GEO_ONLY_WORDS = {
            "india", "indian", "bharat", "karnataka", "bengaluru", "bangalore",
            "mumbai", "bombay", "delhi", "chennai", "madras", "kolkata",
            "calcutta", "hyderabad", "pune", "mysore", "mysuru", "international",
            "global", "world",
        }
        META_MODIFIER_WORDS = {
            "make", "focus", "focused", "perspective", "images", "image", "pictures",
            "picture", "photos", "photo", "visuals", "visual", "only", "more", "less",
            "add", "keep", "middle", "high", "primary", "school", "kids", "friendly",
            "harder", "easier", "tougher", "spicier", "balanced", "warm", "up",
            "students", "student", "relevant", "curriculum", "syllabus",
        }

        has_topic_swap = bool(re.search(
            r"\b(?:replace|swap|change|switch)\b.*\b(?:to|with|for|into)\b|\binstead\s+of\b",
            p_lower,
        ))
        followup_topical_tokens = [
            w for w in re.findall(r"[a-z]+", clean_prompt_topical.lower())
            if w not in GEO_ONLY_WORDS and w not in META_MODIFIER_WORDS and len(w) > 1
        ]
        is_constraint_refinement = (
            bool(previous_brief)
            and not has_topic_swap
            and (
                not followup_topical_tokens
                or raw_sub.lower() in GENERIC_SUBJECTS
                or raw_sub.lower() in GEO_ONLY_WORDS
            )
        )

        # Preserve core topical intent on constraint-only refinements (e.g. "Focus on India", "Make it for middle school kids")
        if is_constraint_refinement and previous_brief:
            subject = str(previous_brief.get("query") or "").strip()
            expansions = list(previous_brief.get("search_terms") or [])
            l1_override = list(previous_brief.get("topics_l1") or [])
            l2_override = list(previous_brief.get("subtopics_l2") or [])
        else:
            if raw_sub.lower() in GENERIC_SUBJECTS or not clean_sub:
                subject = ""
                expansions = []
            else:
                subject = clean_sub if clean_sub else raw_sub
                raw_exp = parsed.get("expansion_terms") or []
                expansions = [
                    str(t).strip() for t in raw_exp
                    if isinstance(t, str) and t.strip() and t.strip().lower() not in GEO_ONLY_WORDS
                ]
            l1_override = None
            l2_override = None

        target_aud = parsed.get("target_audience") or "general"
        prev_filters = dict((previous_brief or {}).get("filters") or {}) if previous_brief else {}
        filters: Dict[str, Any] = dict(prev_filters)

        explicit_india = bool(re.search(r"\b(india|indian|bharat)\b", p_lower))
        explicit_intl = bool(re.search(r"\b(international|global|world|foreign|non-?indian|outside\s+india)\b", p_lower))
        is_universal_stem = any(
            s in p_lower or s in subject.lower()
            for s in (
                "science", "scientific", "experiment", "experiments",
                "physics", "chemistry", "biology", "astronomy", "math", "mathematics",
            )
        )
        if explicit_india:
            filters["india_focused"] = True
            filters["india_international"] = "IN"
        elif explicit_intl:
            filters["india_focused"] = False
            filters["india_international"] = "INTL"
        elif parsed.get("india_focused") is True and not is_universal_stem:
            filters["india_focused"] = True
            filters["india_international"] = "IN"
        elif parsed.get("india_focused") is True and is_universal_stem:
            # Curriculum/state hint on universal STEM subjects: nudge Indian/local questions softly,
            # but keep universal science questions (NA/INTL) eligible unless India-only is explicitly asked.
            filters["india_focused"] = True

        has_no_images = bool(parsed.get("no_images")) or bool(
            re.search(r"\b(no|without|zero|non-?visual|text-?only)\s*(?:of\s+)?(?:images?|pics?|visuals?|photos?)\b|\btext\s+only\b", p_lower)
        )
        wants_images = bool(
            re.search(r"\b(?:only|with)\s+(?:questions?\s+with\s+)?(?:images?|pics?|visuals?|photos?)\b", p_lower)
        ) and not has_no_images
        if has_no_images:
            filters["no_images"] = True
            filters["exclude_image"] = True
            filters.pop("require_images", None)
        elif wants_images:
            filters.pop("no_images", None)
            filters.pop("exclude_image", None)
            filters["require_images"] = True

        if re.search(r"\b(evergreen|timeless|any year)\b", p_lower):
            filters["evergreen"] = True
            filters["evergreen_only"] = True

        is_family_or_school = target_aud == "school" or any(
            w in p_lower for w in ("school", "kid", "child", "family", "student", "assembly")
        )
        if is_family_or_school:
            filters["school_safe"] = True
            filters["audience"] = "school"
        elif target_aud in ("college", "corporate"):
            filters["audience"] = target_aud
        elif not filters.get("audience") and target_aud:
            filters["audience"] = target_aud

        explicit_diff = bool(re.search(
            r"\b(hard(?:er)?|tough(?:er)?|difficult|finals?|killer|spicy|spicier|easy|easier|warm-?up|beginner|gentle|balanced|medium)\b",
            p_lower,
        ))
        prev_bias = (previous_brief or {}).get("hard_bias") if previous_brief else None
        if is_constraint_refinement and previous_brief and not explicit_diff:
            hard_bias = prev_bias
        else:
            hard_bias = parsed.get("hard_bias")
            if hard_bias == "balanced":
                hard_bias = "medium"
            if not hard_bias and explicit_diff:
                if re.search(r"\b(hard(?:er)?|tough(?:er)?|difficult|finals?|killer|spicy|spicier)\b", p_lower):
                    hard_bias = "hard"
                elif re.search(r"\b(easy|easier|warm-?up|beginner|gentle)\b", p_lower):
                    hard_bias = "easy"
                elif re.search(r"\b(balanced|medium)\b", p_lower):
                    hard_bias = "medium"
            if filters.get("audience") == "school" and not hard_bias and not is_constraint_refinement:
                hard_bias = "easy"

        fmt_filter = parsed.get("format_filter")
        if fmt_filter in ("STANDARD_QA", "STANDARD", "", "NONE", None):
            fmt_filter = (previous_brief or {}).get("format_filter") if is_constraint_refinement else None

        # Detect Karnataka / Indian localities deterministically (handles glued typos like 'quizkarnataka')
        locality = parsed.get("locality")
        if not locality and "karnataka" in p_lower:
            locality = "Karnataka"
        if locality and locality.lower() not in ("global", "international", "world") and not explicit_intl:
            filters["india_focused"] = True
            if not is_universal_stem and not is_constraint_refinement:
                filters["india_international"] = "IN"
        if not locality and previous_brief and not explicit_intl:
            locality = previous_brief.get("locality")

        # For thematic / multidisciplinary prompts (including everyday science & experiments),
        # unconstrain L1/L2 filters so BM25 semantic search finds matches across Science/Tech/GK decks.
        thematic_signals = (
            "people", "person", "personality", "leader", "pioneer", "scientist",
            "science", "scientific", "experiment", "experiments", "physics", "chemistry", "biology",
            "astronaut", "space", "mission", "freedom", "fighter", "women", "achiever",
            "inventor", "hero", "startup", "founder", "underdog", "inventions",
            "history", "heritage", "movement", "revolution"
        )
        is_thematic = any(s in subject.lower() or s in p_lower for s in thematic_signals)
        if l1_override is not None:
            l1s = [] if is_thematic else l1_override
            l2s = [] if is_thematic else l2_override
        else:
            l1s = [] if is_thematic else (parsed.get("mapped_topics_l1") or [])
            l2s = [] if is_thematic else (parsed.get("mapped_subtopics_l2") or [])

        framing = (
            (previous_brief or {}).get("framing")
            if is_constraint_refinement and (previous_brief or {}).get("framing")
            else (parsed.get("framing") or f"A curated round on {subject or prompt}")
        )
        effective_aud = filters.get("audience") or target_aud

        from backend.app.services.prompt_quiz.engine import Brief
        brief = Brief(
                prompt=prompt,
                query=subject,
                count=final_count,
                filters=filters,
                ramp=True,
                hard_bias=hard_bias,
                framing=framing,
                source="gemini_dynamic_intent",
                format_filter=fmt_filter,
                locality=locality,
                venue_type=effective_aud,
                tone=[],
                cross_topic_padding=False,
                topics_l1=l1s,
                subtopics_l2=l2s,
                search_terms=expansions,
        )
        logger.info(
                f"Dynamic Intent parsed: subject='{subject}', expansions={len(expansions)}, aud='{target_aud}', l1={brief.topics_l1}"
        )
        return brief

    except Exception as e:
        logger.warning(f"Intent engine attempt failed: {e}")

    return None
