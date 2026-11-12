"""Shared source-series grouping and fuzzy deck/source query resolution.

Used by:
  - /documents/sources (to present grouped source series + decks in UI pickers)
  - /questions search (so Questions Bank source filtering & text queries stay scoped cleanly)
  - /prompt-quiz engine (so Prompt Quiz detects sources like "Agastya" / "aghtya" / "Set 2 Prelims")
"""
from __future__ import annotations

import difflib
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


_GENERIC_DECK_WORDS: Set[str] = {
    "quiz", "quizzes", "prelims", "prelim", "finals", "final", "regionals", "regional",
    "round", "rounds", "set", "sets", "part", "parts", "level", "levels", "session",
    "sessions", "deck", "decks", "question", "questions", "answer", "answers", "series",
    "edition", "editions", "annual", "school", "schools", "college", "colleges",
    "corporate", "state", "board", "india", "indian", "australian", "american", "british",
    "karnataka", "bengaluru", "bangalore", "mumbai", "delhi", "chennai", "hyderabad",
    "kolkata", "pune", "world", "global", "international", "general", "knowledge", "trivia",
    "history", "science", "scientific", "business", "finance", "economy", "technology",
    "tech", "politics", "political", "sports", "sport", "entertainment", "culture",
    "geography", "everyday", "experiments", "experiment", "students", "student", "grade",
    "class", "test", "practice", "sample", "semi", "quarter", "inter", "intra", "mega",
    "open", "national", "junior", "senior", "high", "middle", "primary", "varsity",
    "modern", "ancient", "radio", "wireless", "music", "movie", "movies", "film", "films",
    "cricket", "football", "startup", "startups", "company", "companies", "brand", "brands",
    "logo", "logos", "fintech", "banking", "bank", "food", "festivals", "people", "person",
    "physics", "chemistry", "biology", "astronomy", "space", "nature", "environment",
    "health", "mathematics", "math", "easy", "medium", "hard", "tough", "difficult",
    "visual", "audio", "video", "image", "images", "clue", "clues", "connect", "arrange",
    "stepped", "buzzer", "preliminary", "quarterfinal", "semifinal", "tiebreaker",
    "edition", "archive", "upload", "uploaded", "source", "sources", "based", "related",
    "topic", "topics", "subtopic", "foundation", "challenge", "championship", "contest",
    "bowl", "fest", "megaquiz", "master", "masters", "junior", "senior", "kids", "family",
}

_FILE_EXT_RE = re.compile(r"\.(?:pptx|ppt|pdf|docx?|xlsx?)$", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_SET_ROUND_RE = re.compile(
    r"\b(?:set|part|round|vol|volume|session|edition|level|ep|episode)\s*#?\s*\d+[a-z]?\b",
    re.IGNORECASE,
)
_STAGE_RE = re.compile(
    r"\b(?:semi[-\s]?finals?|quarter[-\s]?finals?|tie[-\s]?breakers?|prelims?|preliminary|finals?|qualifiers?|heats?)\b",
    re.IGNORECASE,
)
_HASH_NUM_RE = re.compile(r"#\s*\d+\b")
_SEPARATORS_RE = re.compile(r"[_/\\]+")
_EMPTY_PARENS_RE = re.compile(r"\(\s*\)|\[\s*\]")


def normalize_deck_text(text: str) -> str:
    """Lowercase text with file extension removed and separators collapsed to single spaces."""
    s = _FILE_EXT_RE.sub("", (text or "").strip())
    s = _SEPARATORS_RE.sub(" ", s)
    s = re.sub(r"[-–—]+", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", normalize_deck_text(text))


def derive_source_series(title: str, filename: str = "") -> str:
    """Derive a clean series/source group name from a document title or filename.

    Examples:
        "Agastya Regionals Set 2 Prelims 2026" -> "Agastya Regionals"
        "Agastya Regionals Set 1 Finals 2026.pptx" -> "Agastya Regionals"
    """
    raw = (title or "").strip() or (filename or "").strip()
    if not raw:
        return "Untitled Source"
    cleaned = _FILE_EXT_RE.sub("", raw)
    cleaned = _SEPARATORS_RE.sub(" ", cleaned)
    fallback = re.sub(r"\s+", " ", cleaned).strip()

    s = _YEAR_RE.sub(" ", fallback)
    s = _SET_ROUND_RE.sub(" ", s)
    s = _STAGE_RE.sub(" ", s)
    s = _HASH_NUM_RE.sub(" ", s)
    s = _EMPTY_PARENS_RE.sub(" ", s)
    s = re.sub(r"\s*[-–—:,]+\s*$", "", s)
    s = re.sub(r"^\s*[-–—:,]+\s*", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) >= 2 else fallback


def _levenshtein(a: str, b: str) -> int:
    """Edit distance between two short lowercase strings."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]


def _fuzzy_score(tok: str, candidate: str) -> float:
    """Return >0 similarity score if `tok` is a plausible typo for `candidate`, else 0.0."""
    if len(tok) < 5 or len(candidate) < 5:
        return 0.0
    if abs(len(tok) - len(candidate)) > 3:
        return 0.0
    dist = _levenshtein(tok, candidate)
    ratio = difflib.SequenceMatcher(None, tok, candidate).ratio()
    if dist <= 1 and ratio >= 0.72:
        return ratio + 0.1
    if tok[0] == candidate[0] and len(candidate) >= 6:
        if (dist <= 2 and ratio >= 0.72) or (dist <= 3 and len(tok) >= 6 and ratio >= 0.75):
            return ratio
    return 0.0


def build_source_catalog(docs: Sequence[Any]) -> Dict[str, Any]:
    """Group uploaded documents into source series plus flat deck entries for UI pickers."""
    decks: List[Dict[str, Any]] = []
    groups: Dict[str, Dict[str, Any]] = {}

    for d in docs:
        if isinstance(d, dict):
            doc_id = str(d.get("id") or "")
            title = str(d.get("title") or "")
            filename = str(d.get("filename") or "")
            year = d.get("year")
            q_count = int(d.get("question_count") or 0)
            status = str(d.get("processing_status") or "")
        else:
            doc_id = str(getattr(d, "id", "") or "")
            title = str(getattr(d, "title", "") or "")
            filename = str(getattr(d, "filename", "") or "")
            year = getattr(d, "year", None)
            q_count = int(getattr(d, "question_count", 0) or 0)
            status = str(getattr(d, "processing_status", "") or "")

        if not doc_id or status == "FAILED":
            continue

        display_title = title.strip() or filename.strip() or doc_id
        series_label = derive_source_series(title, filename)
        series_key = normalize_deck_text(series_label) or doc_id

        decks.append({
            "id": doc_id,
            "title": display_title,
            "filename": filename,
            "series": series_label,
            "series_key": series_key,
            "year": year,
            "question_count": q_count,
        })

        grp = groups.get(series_key)
        if grp is None:
            grp = {
                "key": series_key,
                "label": series_label,
                "document_ids": [],
                "deck_count": 0,
                "question_count": 0,
                "years": set(),
            }
            groups[series_key] = grp
        grp["document_ids"].append(doc_id)
        grp["deck_count"] += 1
        grp["question_count"] += q_count
        if year is not None:
            grp["years"].add(int(year))

    series_list = []
    for grp in groups.values():
        series_list.append({
            "key": grp["key"],
            "label": grp["label"],
            "document_ids": grp["document_ids"],
            "deck_count": grp["deck_count"],
            "question_count": grp["question_count"],
            "years": sorted(grp["years"], reverse=True),
        })
    series_list.sort(key=lambda g: (-g["question_count"], -g["deck_count"], g["label"].lower()))
    decks.sort(key=lambda d: (d["series"].lower(), d["title"].lower()))
    return {"series": series_list, "decks": decks}


def _narrow_matched_docs(
    q_norm: str,
    matched_docs: List[Dict[str, Any]],
    distinctive_tokens: Set[str],
    is_prompt: bool,
) -> tuple[List[Dict[str, Any]], Set[str]]:
    """Apply explicit set/prelims/finals/year qualifiers to narrow a multi-deck source match."""
    narrowed = list(matched_docs)
    consumed: Set[str] = set()
    if len(narrowed) <= 1:
        return narrowed, consumed

    # 1. Explicit "set 2", "part 1", "round 3"
    for m in re.finditer(r"\b(set|part|round|vol|session|edition)\s*#?\s*(\d+[a-z]?)\b", q_norm):
        phrase = f"{m.group(1)} {m.group(2)}"
        sub = [d for d in narrowed if phrase in d["_blob"]]
        if 0 < len(sub) < len(narrowed):
            narrowed = sub
            consumed.update([m.group(1), m.group(2)])

    # 2. Explicit "prelims" / "prelim" (unambiguous stage qualifier)
    if re.search(r"\bprelims?\b", q_norm):
        sub = [d for d in narrowed if re.search(r"\bprelims?\b", d["_blob"])]
        if 0 < len(sub) < len(narrowed):
            narrowed = sub
            consumed.update({"prelim", "prelims"})

    # 3. "finals" / "final" only when bound to deck phrasing (so "a tough finals round from Agastya" stays broad)
    finals_explicit = not is_prompt or bool(
        re.search(
            r"\b(?:set\s*\d+|regionals?|prelims?|part\s*\d+|round\s*\d+)\s+finals?\b|\bfinals?\s+(?:19|20)\d{2}\b",
            q_norm,
        )
    )
    if not finals_explicit and distinctive_tokens:
        dist_pat = "|".join(re.escape(t) for t in distinctive_tokens)
        finals_explicit = bool(
            re.search(rf"\b(?:{dist_pat})\s+(?:set\s*\d+\s+)?finals?\b", q_norm)
            or re.search(rf"\bfinals?\s+(?:of|from)\s+(?:the\s+)?(?:{dist_pat})\b", q_norm)
        )
    if finals_explicit and re.search(r"\bfinals?\b", q_norm):
        sub = [d for d in narrowed if re.search(r"\bfinals?\b", d["_blob"])]
        if 0 < len(sub) < len(narrowed):
            narrowed = sub
            consumed.update({"final", "finals"})

    # 4. Explicit 4-digit year if it narrows to a subset
    for ym in _YEAR_RE.finditer(q_norm):
        yr = ym.group(0)
        sub = [d for d in narrowed if yr in d["_blob"]]
        if 0 < len(sub) < len(narrowed):
            narrowed = sub
            consumed.add(yr)

    return narrowed, consumed


def resolve_deck_query(
    query_text: str,
    docs: Sequence[Dict[str, Any]],
    *,
    extra_excluded: Optional[Iterable[str]] = None,
    is_prompt: bool = False,
) -> Dict[str, Any]:
    """Resolve explicit or typo-tolerant deck/source references in a prompt or search query.

    Returns:
        {
            "doc_ids": List[str],              # matched document IDs (empty if no source match)
            "tokens": List[str],               # canonical source tokens (e.g. ["agastya"])
            "matched_input_tokens": List[str], # raw tokens in query_text that mapped to source
            "labels": List[str],               # readable series/deck labels for UI badges
            "remaining_query": str,            # query_text minus matched source tokens
        }
    """
    empty = {
        "doc_ids": [],
        "tokens": [],
        "matched_input_tokens": [],
        "labels": [],
        "remaining_query": (query_text or "").strip(),
    }
    if not query_text or not docs:
        return empty

    excluded = set(_GENERIC_DECK_WORDS)
    if extra_excluded:
        excluded.update(str(w).strip().lower() for w in extra_excluded if str(w).strip())

    # Deduplicate documents cleanly by ID and build normalized searchable blobs from title + filename
    doc_entries: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    for d in docs:
        doc_id = str(d.get("id") or d.get("document_id") or "").strip()
        title = str(d.get("title") or d.get("document_title") or "").strip()
        filename = str(d.get("filename") or d.get("document_filename") or "").strip()
        if not doc_id or doc_id in seen_ids or (not title and not filename):
            continue
        seen_ids.add(doc_id)
        blob = f"{normalize_deck_text(title)} {normalize_deck_text(filename)}".strip()
        doc_entries.append({
            "id": doc_id,
            "title": title or filename,
            "filename": filename,
            "_blob": blob,
            "_tokens": set(_tokenize(blob)),
        })

    if not doc_entries:
        return empty

    title_df: Dict[str, int] = {}
    for entry in doc_entries:
        for tok in entry["_tokens"]:
            if len(tok) >= 4 and not tok.isdigit() and tok not in excluded:
                title_df[tok] = title_df.get(tok, 0) + 1

    n_docs = len(doc_entries)
    max_allowed = max(1, int(n_docs * 0.85)) if n_docs >= 3 else n_docs
    distinctive_tokens = {tok for tok, df in title_df.items() if 1 <= df <= max_allowed}

    q_norm = normalize_deck_text(query_text)
    raw_tokens = _tokenize(query_text)

    canon_tokens: List[str] = []
    matched_input_tokens: List[str] = []
    matched_doc_sets: List[Set[str]] = []

    for tok in raw_tokens:
        if tok in distinctive_tokens:
            if tok not in canon_tokens:
                canon_tokens.append(tok)
            if tok not in matched_input_tokens:
                matched_input_tokens.append(tok)
            matched_doc_sets.append({d["id"] for d in doc_entries if tok in d["_tokens"]})
            continue

        if tok in excluded or tok.isdigit() or len(tok) < 5:
            continue

        best_cand: Optional[str] = None
        best_score = 0.0
        for d_tok in distinctive_tokens:
            sc = _fuzzy_score(tok, d_tok)
            if sc > best_score:
                best_score = sc
                best_cand = d_tok
        if best_cand:
            if best_cand not in canon_tokens:
                canon_tokens.append(best_cand)
            if tok not in matched_input_tokens:
                matched_input_tokens.append(tok)
            matched_doc_sets.append({d["id"] for d in doc_entries if best_cand in d["_tokens"]})

    # Fallback for direct Questions Bank searches that type a multi-word title substring without distinctive words
    if not canon_tokens and not is_prompt and len(q_norm) >= 5 and len(raw_tokens) >= 2:
        sub_docs = [d for d in doc_entries if q_norm in d["_blob"]]
        if 0 < len(sub_docs) < max(2, n_docs):
            matched_docs = sub_docs
            matched_input_tokens = list(raw_tokens)
            canon_tokens = [t for t in raw_tokens if len(t) >= 3][:3]
            matched_doc_sets = [{d["id"] for d in sub_docs}]

    if not matched_doc_sets:
        return empty

    # Intersect matched sets if multiple distinctive tokens were given (unless disjoint, then union)
    combined_ids = set.intersection(*matched_doc_sets) if len(matched_doc_sets) > 1 else set(matched_doc_sets[0])
    if not combined_ids:
        combined_ids = set.union(*matched_doc_sets)

    matched_docs = [d for d in doc_entries if d["id"] in combined_ids]
    narrowed_docs, qualifier_tokens = _narrow_matched_docs(
        q_norm, matched_docs, set(canon_tokens), is_prompt=is_prompt
    )
    for qt in qualifier_tokens:
        if qt not in matched_input_tokens:
            matched_input_tokens.append(qt)

    used_set = set(matched_input_tokens)
    remaining_tokens = [t for t in raw_tokens if t not in used_set]
    remaining_query = " ".join(remaining_tokens).strip()

    if len(narrowed_docs) == 1:
        labels = [narrowed_docs[0]["title"]]
    else:
        series_counts: Dict[str, int] = {}
        for d in narrowed_docs:
            ser = derive_source_series(d["title"], d["filename"])
            series_counts[ser] = series_counts.get(ser, 0) + 1
        labels = [
            f"{ser} ({cnt} decks)" if cnt > 1 else ser
            for ser, cnt in sorted(series_counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
        ]

    return {
        "doc_ids": [d["id"] for d in narrowed_docs],
        "tokens": canon_tokens,
        "matched_input_tokens": matched_input_tokens,
        "labels": labels,
        "remaining_query": remaining_query,
    }
