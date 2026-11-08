"""Metadata that needs no AI: it comes from the deck's file name and the slide structure.

Everything here is pure Python (no database, no network) so it can be unit-tested and reused by the
ingestion pipeline and by the backfill job.

File naming convention (from the QShala tag spec):

    <date> <college name> <location> <prelim/final> <quiz name> [<language>]

Real names are messy, so the parser is deliberately tolerant: anything it cannot work out is left as
None and can be fixed in the "Deck details" editor.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, asdict
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from backend.app.services.tagging.qshala_rules import CITIES_IN, CITIES_INTL

# ---------------------------------------------------------------------- vocabularies

QUESTION_FORMATS = [
    "QA",
    "CONNECT_4",
    "STEPPED_CLUE",
    "ARRANGE_ORDER",
    "BUZZER",
    "VISUAL_CLUE",
    "AUDIO_CLUE",
    "VIDEO_CLUE",
    "POTPOURRI",
    "IMAGE_REVEAL",
    "LIST_IT",
    "CONNECT",
    "THREE_CLUE",
    "STANDARD_QA",
    "MCQ",
]

STAGE_WORDS: Dict[str, str] = {
    "prelim": "Prelims", "prelims": "Prelims", "preliminary": "Prelims", "preliminaries": "Prelims",
    "final": "Finals", "finals": "Finals",
    "semi": "Semi-finals", "semis": "Semi-finals", "semifinal": "Semi-finals", "semifinals": "Semi-finals",
    "quarterfinal": "Quarter-finals", "quarterfinals": "Quarter-finals",
    "wildcard": "Wildcard", "qualifier": "Qualifiers", "qualifiers": "Qualifiers",
}

LANGUAGES = {
    "english": "English", "hindi": "Hindi", "kannada": "Kannada", "tamil": "Tamil", "telugu": "Telugu",
    "malayalam": "Malayalam", "marathi": "Marathi", "bengali": "Bengali", "gujarati": "Gujarati",
    "punjabi": "Punjabi", "bilingual": "Bilingual",
}

CITY_ALIASES = {
    "bangalore": "Bengaluru", "mysore": "Mysuru", "gurgaon": "Gurugram", "bombay": "Mumbai",
    "madras": "Chennai", "calcutta": "Kolkata", "trivandrum": "Thiruvananthapuram",
    "vizag": "Visakhapatnam", "banaras": "Varanasi", "cochin": "Kochi",
}
EXTRA_CITIES = [
    "vellore", "manipal", "tiruchirappalli", "trichy", "madurai", "visakhapatnam", "vijayawada",
    "varanasi", "dehradun", "shimla", "amritsar", "ludhiana", "kanpur", "agra", "vadodara", "rajkot",
    "thane", "navi mumbai", "pilani", "kharagpur", "dharwad", "hubli", "belagavi", "belgaum", "udupi",
    "warangal", "tirupati", "thrissur", "kozhikode", "calicut", "jodhpur", "udaipur", "faridabad",
    "ghaziabad", "bhilai", "jamshedpur", "cuttack", "siliguri", "imphal", "shillong", "gangtok",
    "puducherry", "pondicherry", "trivandrum", "cochin", "vizag", "banaras", "bombay", "madras", "calcutta",
]

_ALL_CITIES: List[str] = sorted(
    set(list(CITIES_IN.keys()) + list(CITIES_INTL.keys()) + EXTRA_CITIES + list(CITY_ALIASES.keys())),
    key=lambda c: (-len(c.split()), -len(c)),
)

COLLEGE_WORDS = re.compile(
    r"\b(college|university|universities|institute|institution|iit|iim|nit|iiit|bits|iisc|campus|b-?school|"
    r"engineering|management|polytechnic|academy|mba|btech|bba|pes|vit|srm|manipal|christ|symbiosis)\b", re.I)
SCHOOL_WORDS = re.compile(r"\b(school|schools|junior|senior\s+secondary|high\s+school|k-?12)\b", re.I)
CORPORATE_WORDS = re.compile(r"\b(corporate|company|companies|employees?|office|workplace|enterprise|offsite)\b", re.I)

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


@dataclass
class DeckInfo:
    quiz_name: Optional[str] = None
    college: Optional[str] = None
    city: Optional[str] = None
    round_stage: Optional[str] = None
    deck_date: Optional[str] = None          # ISO yyyy-mm-dd when a full date was found
    deck_year: Optional[int] = None
    language: Optional[str] = None
    primary_audience: Optional[str] = None
    is_varsity: bool = False
    source_presentation: Optional[str] = None
    parsed_ok: bool = False                  # True when college/city/stage structure was recognised

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------- filename parser

def _valid_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _extract_date(text: str) -> Tuple[Optional[date], Optional[int], str]:
    """Find a date in `text`. Returns (full_date_or_None, year_or_None, text_with_date_removed)."""
    patterns = [
        # yyyy-mm-dd
        (r"(?<!\d)(20\d{2})[-_./](\d{1,2})[-_./](\d{1,2})(?!\d)", lambda m: (int(m[1]), int(m[2]), int(m[3]))),
        # dd-mm-yyyy / dd.mm.yy  (Indian order)
        (r"(?<!\d)(\d{1,2})[-_./](\d{1,2})[-_./](20\d{2}|\d{2})(?!\d)",
         lambda m: (int(m[3]) if len(m[3]) == 4 else 2000 + int(m[3]), int(m[2]), int(m[1]))),
        # yyyymmdd
        (r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)", lambda m: (int(m[1]), int(m[2]), int(m[3]))),
        # 15 Mar 2024 / 15th March, 2024
        (r"(?<![\dA-Za-z])(\d{1,2})(?:st|nd|rd|th)?[\s\-]+([A-Za-z]{3,9})\.?,?[\s\-]*(20\d{2}|\d{2})(?!\d)",
         lambda m: (int(m[3]) if len(m[3]) == 4 else 2000 + int(m[3]), _MONTHS.get(m[2][:3].lower(), 0), int(m[1]))),
        # Mar 15 2024
        (r"(?<![A-Za-z])([A-Za-z]{3,9})\.?[\s\-]+(\d{1,2})(?:st|nd|rd|th)?,?[\s\-]+(20\d{2})(?!\d)",
         lambda m: (int(m[3]), _MONTHS.get(m[1][:3].lower(), 0), int(m[2]))),
    ]
    for pat, build in patterns:
        for m in re.finditer(pat, text):
            y, mo, d = build(m)
            dt = _valid_date(y, mo, d) if mo else None
            if dt and 2000 <= dt.year <= 2100:
                cleaned = (text[:m.start()] + " " + text[m.end():])
                return dt, dt.year, cleaned
    # Month + year only ("March 2024") or a bare year.
    m = re.search(r"(?<![A-Za-z])([A-Za-z]{3,9})\.?[\s\-]+(20\d{2})(?!\d)", text)
    if m and m[1][:3].lower() in _MONTHS:
        return None, int(m[2]), text[:m.start()] + " " + text[m.end():]
    m = re.search(r"(?<!\d)(20\d{2})(?!\d)", text)
    if m:
        return None, int(m[1]), text[:m.start()] + " " + text[m.end():]
    return None, None, text


def _clean_tokens(text: str) -> List[str]:
    text = text.replace("_", " ")
    text = re.sub(r"\s+[-–—|]+\s+", " ", text)          # spaced separators only; keeps "Inter-College"
    text = re.sub(r"[(),\[\]]", " ", text)
    return [t for t in text.split() if t.strip("-–—|.")]


def _find_subseq(tokens_lc: List[str], words: List[str], start: int = 0) -> Optional[int]:
    n = len(words)
    for i in range(start, len(tokens_lc) - n + 1):
        if tokens_lc[i:i + n] == words:
            return i
    return None


def _norm_tok(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())


def classify_audience(*texts: Optional[str]) -> Optional[str]:
    blob = " ".join(t for t in texts if t)
    if not blob:
        return None
    if SCHOOL_WORDS.search(blob) and not COLLEGE_WORDS.search(blob):
        return "School"
    if CORPORATE_WORDS.search(blob) and not COLLEGE_WORDS.search(blob):
        return "Corporate"
    if COLLEGE_WORDS.search(blob):
        return "College"
    return None


def is_varsity_name(*texts: Optional[str]) -> bool:
    """Zerodha Varsity Quiz decks carry ZVQ in their name; the phrase also counts."""
    blob = " ".join(t for t in texts if t)
    return bool(re.search(r"(?<![A-Za-z])zvq(?![A-Za-z])", blob, re.I)
                or re.search(r"zerodha\s+varsity", blob, re.I))


_NOISE = re.compile(r"\b(?:w(?:ith)?\.?\s+answers?|answers?|copy(?:\s+of)?|final\s+version|v\d+)\b|\(\d+\)", re.I)


def parse_deck_filename(filename: Optional[str], title: Optional[str] = None) -> DeckInfo:
    """Parse a deck file name (or, failing that, its title) into DeckInfo. Never raises."""
    info = DeckInfo(source_presentation=filename or title)
    try:
        base = os.path.basename(filename or title or "")
        stem = os.path.splitext(base)[0] if os.path.splitext(base)[1].lower() in (".pptx", ".ppt", ".pdf") else base
        stem = re.sub(r"\s+", " ", stem.replace("_", " ")).strip()
        if not stem:
            return info

        info.is_varsity = is_varsity_name(stem, title)

        stem = _NOISE.sub(" ", stem)
        stem = re.sub(r"\s+", " ", stem).strip()
        dt, year, rest = _extract_date(stem)
        info.deck_date = dt.isoformat() if dt else None
        info.deck_year = year

        tokens = _clean_tokens(rest)
        norm = [_norm_tok(t) for t in tokens]

        # Language token (usually last, sometimes in brackets).
        lang_idx = None
        for i in range(len(norm) - 1, -1, -1):
            if norm[i] in LANGUAGES:
                lang_idx = i
                info.language = LANGUAGES[norm[i]]
                break

        # Round stage word.
        stage_idx = None
        for i, w in enumerate(norm):
            if w in STAGE_WORDS:
                stage_idx = i
                info.round_stage = STAGE_WORDS[w]
                break

        # City: the known city closest before the stage word (or the last one found).
        city_span: Optional[Tuple[int, int]] = None
        for city in _ALL_CITIES:
            words = city.split()
            start = 0
            while True:
                pos = _find_subseq(norm, words, start)
                if pos is None:
                    break
                end = pos + len(words)
                if stage_idx is None or end <= stage_idx:
                    if city_span is None or end > city_span[1]:
                        city_span = (pos, end)
                start = pos + 1
        if city_span is None:   # no city before the stage word: take the first city anywhere in the name
            for city in _ALL_CITIES:
                pos = _find_subseq(norm, city.split(), 0)
                if pos is not None and (city_span is None or pos < city_span[0]):
                    city_span = (pos, pos + len(city.split()))
        if city_span:
            raw_city = " ".join(norm[city_span[0]:city_span[1]])
            info.city = CITY_ALIASES.get(raw_city, raw_city.title())

        # College: everything before the city (or before the stage word when no city was found).
        ends = [e for e in (city_span[0] if city_span else None, stage_idx) if e is not None]
        college_end = min(ends) if ends else None
        if college_end:
            college = " ".join(tokens[:college_end]).strip(" -–—|.")
            info.college = college or None

        # Quiz name: everything after the stage word, minus the language token.
        quiz_tokens: List[str] = []
        if stage_idx is not None:
            quiz_tokens = [t for i, t in enumerate(tokens) if i > stage_idx and i != lang_idx]
        quiz_name = " ".join(quiz_tokens).strip(" -–—|.")
        if not quiz_name:
            fallback = [t for i, t in enumerate(tokens) if i != lang_idx]
            quiz_name = " ".join(fallback).strip(" -–—|.") or stem
        info.quiz_name = quiz_name

        info.primary_audience = classify_audience(info.college, stem)
        info.parsed_ok = bool(info.city or info.college or info.round_stage)
    except Exception:
        # Tolerant by design: a weird name must never break ingestion.
        info.quiz_name = info.quiz_name or (title or filename)
    return info


# ---------------------------------------------------------------------- question format tracker

_FORMAT_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    # 1. Stepped clues / Portfolio Builder (must precede generic buzzer)
    ("STEPPED_CLUE", re.compile(r"\b(?:portfolio\s*builder|stepped\s*clues?|(?:3|three)[\s\-]*clues?|3\s*parts/clues|\+20\s*/\s*-15)\b", re.I)),
    # 2. Connect 4 / Common Link / Market Indicators (must precede generic buzzer)
    ("CONNECT_4", re.compile(r"\b(?:market\s*indicators|connect\s*4|4\s*pics\s*1\s*word|four\s*images|common\s*(?:theme|link|thread)|what\s*connects|connect(?:ion|ions|s)?\b|\bfind\s+the\s+link\b)", re.I)),
    # 3. Arrange the order / Ticker Tangle
    ("ARRANGE_ORDER", re.compile(r"\b(?:ticker\s*tangle|arrange\b|\bin\s+(?:the\s+)?(?:right|correct)\s+order\b|\border\s+(?:the|them)\b|\bsequenc|chronolog|lowest\s+to\s+highest|earliest\s+to\s+latest)", re.I)),
    # 4. Potpourri
    ("POTPOURRI", re.compile(r"\bpotpourri\b", re.I)),
    # 5. Image reveal
    ("IMAGE_REVEAL", re.compile(r"\bimage\s*reveal\b|\breveal\s*round\b|\bpixelat", re.I)),
    # 6. List it
    ("LIST_IT", re.compile(r"\blist[\s\-]*it\b", re.I)),
    # 7. Rapid Fire / Buzzer (Closing Bell)
    ("BUZZER", re.compile(r"\b(?:closing\s*bell|buzzer\s*round|rapid\s*fire|buzzers?!)\b", re.I)),
    # 8. Standard QA (Opening Bell / Direct)
    ("QA", re.compile(r"\b(?:opening\s*bell|direct\s*questions?|q\s*&\s*a\b|\bqa\b|\bquestion[\s\-]*answer\b)", re.I)),
]
_HEADER_TYPES = {"SECTION_MARKER", "ANNOUNCEMENT", "RULES", "TITLE", "CONTENT"}


def detect_format_in_text(text: str) -> Optional[str]:
    for fmt, pat in _FORMAT_PATTERNS:
        if pat.search(text or ""):
            return fmt
    return None


def infer_formats(slides: List[Dict[str, Any]]) -> Dict[int, str]:
    """Map slide_number -> question format by walking the deck and tracking section headers.

    A section header ("The following questions are Connect questions") sets the format for everything
    after it, until the next header. A round marker with no format words resets to plain QA.
    """
    result: Dict[int, str] = {}
    current = "QA"
    for s in sorted(slides, key=lambda x: x.get("slide_number") or 0):
        stype = s.get("slide_type") or "CONTENT"
        title = (s.get("title") or "")
        title = "" if title == "NO_TITLE" else title
        text = (s.get("extracted_text") or "")
        blob = f"{title} {text}".strip()
        words = len(blob.split())

        # AUDIENCE / BUFFER / TIE-BREAKER sections are not part of the round that came before them: the
        # format resets to plain QA there (their questions carry their own structure).
        if any(re.search(r"^\W*(?:audience\s+questions?|buffer(?:\s+questions?)?|tie[\s\-]*breakers?)\W*$", x, re.I)
               for x in (title, text, blob)):
            current = "QA"
            sn = s.get("slide_number")
            if sn is not None:
                result[int(sn)] = current
            continue

        # Ignore agenda / structure overview slides
        if re.search(r"\b(?:structure\s+of\s+(?:the\s+)?quiz|quiz\s+structure|overview|agenda)\b", blob, re.I):
            sn = s.get("slide_number")
            if sn is not None:
                result[int(sn)] = current
            continue

        if stype in _HEADER_TYPES and blob:
            # Skip social media / follow us announcements
            if re.search(r"\b(?:connect\s+with\s+us|social\s+media|instagram|twitter|follow\s+us)\b", blob, re.I):
                sn = s.get("slide_number")
                if sn is not None:
                    result[int(sn)] = current
                continue

            fmt = detect_format_in_text(blob)
            if stype == "SECTION_MARKER":
                current = fmt or "QA"
            elif fmt and words <= 30:
                # A short non-question slide naming a format works as a header, but a bare "connect"
                # in a slide without round indicators must not.
                if fmt in ("CONNECT_4", "CONNECT") and not re.search(r"\b(?:round|section|set|questions?|following|next|indicators|market|theme|4\s*pics|common|what\s*connects|connect\s*4)\b", blob, re.I):
                    pass
                else:
                    current = fmt
        sn = s.get("slide_number")
        if sn is not None:
            result[int(sn)] = current
    return result


# ---------------------------------------------------------------------- per-question rules

_STAR_CHARS = "★⭐✱✪"
_LEAD_STAR = re.compile(rf"^\s*[*{_STAR_CHARS}]+\s*(?=\S)")
_TRAIL_STAR = re.compile(rf"(?<=\S)\s*[*{_STAR_CHARS}]+\s*$")


def detect_star(question_text: str, slide_text: str = "") -> Tuple[bool, str]:
    """Star-marked questions carry a * (or a star glyph). Returns (is_star, text_without_the_mark)."""
    text = question_text or ""
    cleaned = text
    star = False
    if _LEAD_STAR.search(cleaned):
        cleaned = _LEAD_STAR.sub("", cleaned, count=1)
        star = True
    if _TRAIL_STAR.search(cleaned):
        cleaned = _TRAIL_STAR.sub("", cleaned, count=1)
        star = True
    if not star and slide_text:
        for line in slide_text.splitlines():
            ln = line.strip()
            if ln and re.fullmatch(rf"[*{_STAR_CHARS}]+", ln):
                star = True
                break
        if not star and any(ch in slide_text for ch in _STAR_CHARS):
            star = True
    return star, cleaned.strip()


_SCRIPTS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("Hindi", re.compile(r"[ऀ-ॿ]")),
    ("Bengali", re.compile(r"[ঀ-৿]")),
    ("Punjabi", re.compile(r"[਀-੿]")),
    ("Gujarati", re.compile(r"[઀-૿]")),
    ("Tamil", re.compile(r"[஀-௿]")),
    ("Telugu", re.compile(r"[ఀ-౿]")),
    ("Kannada", re.compile(r"[ಀ-೿]")),
    ("Malayalam", re.compile(r"[ഀ-ൿ]")),
]


def detect_language(text: str, default: Optional[str] = None) -> str:
    letters = [c for c in (text or "") if c.isalpha()]
    if letters:
        for name, pat in _SCRIPTS:
            hits = len(pat.findall(text))
            if hits / len(letters) >= 0.3:
                return name
    return default or "English"


def answer_type_for(q: Dict[str, Any]) -> str:
    """TEXT | TEXT_IMAGE | IMAGE | TEXT_VIDEO | VIDEO | TEXT_AUDIO | AUDIO."""
    ans = (q.get("answer") or "").strip()
    has_text = bool(ans) and not re.fullmatch(r"(?i)(see\s+)?(the\s+)?(image|picture|video|audio|clip)\.?", ans)
    if q.get("answer_videos"):
        return "TEXT_VIDEO" if has_text else "VIDEO"
    if q.get("answer_images"):
        return "TEXT_IMAGE" if has_text else "IMAGE"
    if q.get("answer_audio"):
        return "TEXT_AUDIO" if has_text else "AUDIO"
    return "TEXT"


def clue_media_type(q: Dict[str, Any]) -> str:
    if q.get("question_videos"):
        return "VIDEO"
    if q.get("question_audio"):
        return "AUDIO"
    if q.get("question_images"):
        return "IMAGE"
    return "NONE"


def round_by_slide(slides: List[Dict[str, Any]]) -> Dict[int, Optional[int]]:
    """Round in force at each slide, from real 'ROUND n' header slides only (None for decks without rounds)."""
    out: Dict[int, Optional[int]] = {}
    current: Optional[int] = None
    for s in sorted((x for x in slides if x.get("slide_number") is not None), key=lambda x: int(x["slide_number"])):
        title = s.get("title") or ""
        text = s.get("extracted_text") or ""
        blob = f"{title} {text}".strip()
        if re.search(r"\b(?:audience\s+questions?|buffer\s+questions?)\b", blob, re.I):
            current = None
        elif s.get("slide_type") == "SECTION_MARKER" or re.search(r"\bround\s*\d+", title, re.I):
            m = re.search(r"\bround\s*(\d+)", blob, re.I)
            if m:
                current = int(m.group(1))
        out[int(s["slide_number"])] = current
    return out


class _IngestDecided(Exception):
    """The format-aware extractor already decided this question's format."""


def annotate_questions(
    questions: List[Dict[str, Any]],
    slides: List[Dict[str, Any]],
    deck: Optional[DeckInfo] = None,
) -> List[Dict[str, Any]]:
    """Attach the no-AI per-question fields as q["_meta"]. Mutates and returns `questions`.

    Also strips a star marker from the question text so the same question hashes identically whether or
    not one deck starred it (that is what lets the Repeated tag find it across decks).
    """
    fmt_by_slide = infer_formats(slides)
    slide_text_by_no = {int(s.get("slide_number")): (s.get("extracted_text") or "")
                        for s in slides if s.get("slide_number") is not None}
    per_round: Dict[Optional[int], int] = {}
    rounds = round_by_slide(slides)
    star_shape_slides = {int(s["slide_number"]) for s in slides
                         if s.get("slide_number") is not None and (s.get("metadata_json") or {}).get("has_star_shape")}
    for idx, q in enumerate(questions, start=1):
        sn = q.get("slide_number")
        ing = q.get("_ingest")          # set by the format-aware extractor: the one source of truth for the format
        rnd = ing.get("round_number") if ing is not None else (rounds.get(int(sn)) if sn else None)
        per_round[rnd] = per_round.get(rnd, 0) + 1
        star, cleaned = detect_star(q.get("question_text") or "", slide_text_by_no.get(int(sn), "") if sn else "")
        if not star and sn and int(sn) in star_shape_slides:
            star = True
        if star and cleaned:
            q["question_text"] = cleaned
        lang_default = deck.language if deck and deck.language else None
        round_fmt = fmt_by_slide.get(int(sn), "QA") if sn else "QA"
        if ing is not None:
            final_fmt = ing["question_format"]
            q["question_type"] = final_fmt
        try:
            if ing is not None:
                raise _IngestDecided()
            from backend.app.services.ingestion.question_formats import detect_and_structure_format
            detected_fmt, payload = detect_and_structure_format(
                question_text=q.get("question_text") or "",
                answer=q.get("answer") or "",
                options=q.get("options"),
                image_refs=q.get("question_images") or q.get("image_refs"),
                audio_refs=q.get("question_audio") or q.get("audio_refs"),
                video_refs=q.get("question_videos") or q.get("video_refs"),
                raw_context=slide_text_by_no.get(int(sn), "") if sn else "",
                deck_round_format=round_fmt,
            )
            q["question_type"] = detected_fmt
            q["format_payload"] = payload
            final_fmt = detected_fmt
        except _IngestDecided:
            pass
        except Exception:
            final_fmt = round_fmt

        q["_meta"] = {
            "question_number": idx,
            "round_number": rnd,
            "question_in_round": per_round[rnd],
            "question_format": final_fmt,
            "star_marked": star,
            "answer_type": answer_type_for(q),
            "media_type": clue_media_type(q),
            "language": detect_language(f"{q.get('question_text') or ''} {q.get('answer') or ''}", lang_default),
        }
    return questions


def question_label(question_number: Optional[int], round_number: Optional[int], question_in_round: Optional[int]) -> str:
    """'Round 3 Q4' when the deck has rounds, otherwise 'Q14'."""
    if round_number and question_in_round:
        return f"Round {round_number} Q{question_in_round}"
    if question_number:
        return f"Q{question_number}"
    return ""
