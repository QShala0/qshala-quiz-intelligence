"""
Multi-signal question format classifier.

Three detection layers:
  1. STRUCTURAL — image count + answer length (catches image-only Connect 4)
  2. TEXT — regex on question/answer text (catches text-explicit formats)
  3. ROUND — round name metadata (catches round-level labeling)

GB's rule: use the answer + structural signals. Don't overthink it.
"""
import re
import logging
from typing import Optional, Tuple, Dict, Any, List

logger = logging.getLogger(__name__)

STANDARD_QA   = "STANDARD_QA"
CONNECT_4     = "CONNECT_4"
STEPPED_CLUE  = "STEPPED_CLUE"
ARRANGE_ORDER = "ARRANGE_ORDER"


# ═══════════════════════════════════════════════════════════════
# LAYER 1: STRUCTURAL (image count + answer length)
# ═══════════════════════════════════════════════════════════════

def _structural_connect4(
    question_text: str,
    answer_text: str,
    question_images: Optional[List[str]] = None,
    answer_images: Optional[List[str]] = None,
) -> bool:
    """
    Connect 4 structural signal:
    - 3 or 4 question images (the clue elements)
    - Short answer (< 80 chars — it's just the connecting concept/name)
    - Question text is short or empty (the images ARE the question)

    This catches the Puneeth Rajkumar / Lays cases where there's NO text
    signal at all — just 4 brand logos and a one-word answer.
    """
    q_imgs = question_images or []
    a_text = (answer_text or "").strip()
    q_text = (question_text or "").strip()

    if 3 <= len(q_imgs) <= 6 and len(a_text) < 80:
        # Strong: lots of question images + short answer + short/no question text
        if len(q_text) < 100:
            return True
        # Medium: lots of question images + short answer even with longer text
        if len(q_imgs) >= 4:
            return True

    return False


def _structural_stepped_clue(
    question_text: str,
    question_images: Optional[List[str]] = None,
) -> bool:
    """
    Stepped Clue structural signal:
    - Question text is LONG (contains multiple clue paragraphs)
    - OR has 3+ numbered sections in the text
    """
    q_text = (question_text or "").strip()

    # Multiple numbered sections suggests progressive clues
    numbered = re.findall(r'(?:^|\n)\s*(?:clue|step|hint)\s*\d', q_text, re.I)
    if len(numbered) >= 2:
        return True

    return False


# ═══════════════════════════════════════════════════════════════
# LAYER 2: TEXT (regex on question + answer text)
# ═══════════════════════════════════════════════════════════════

# ── Connect 4 text patterns ──
_CONNECT_ANSWER_RE = [
    re.compile(r"\b(common\s+(link|thread|connection|factor))\b", re.I),
    re.compile(r"\b(connects?\s+(them|these|all))\b", re.I),
    re.compile(r"\b(they\s+are\s+all)\b", re.I),
    re.compile(r"\b(the\s+link\s+is)\b", re.I),
    re.compile(r"\b(what\s+(connects|links))\b", re.I),
]

_CONNECT_QUESTION_RE = [
    re.compile(r"^\s*(?:\d+[\.\)]\s*)?connect\b|\b(connect\s*4|connect\s+(?:these|them|all)|common\s+link|what\s+(?:links|connects))\b", re.I),
    re.compile(r"\b(what\s+do\s+.{3,60}\s+have\s+in\s+common)\b", re.I),
    re.compile(r"connect\s*[-–]?\s*4", re.I),
]

def _text_connect4(q: str, a: str) -> bool:
    if any(p.search(a) for p in _CONNECT_ANSWER_RE):
        return True
    if any(p.search(q) for p in _CONNECT_QUESTION_RE):
        return True
    return False


# ── Stepped Clue text patterns ──
_STEPPED_RE = [
    re.compile(r"clue\s*[#]?\s*[123]", re.I),
    re.compile(r"\+\s*20\s*/\s*-\s*15", re.I),
    re.compile(r"\+\s*15\s*/\s*-\s*10", re.I),
    re.compile(r"\+\s*10\s*/\s*-\s*5", re.I),
    re.compile(r"\b(decreasing\s+value|progressive\s+clue|buzzer\s+round)\b", re.I),
    re.compile(r"\b(portfolio\s+builder)\b", re.I),
]

def _text_stepped(q: str, a: str) -> bool:
    if any(p.search(q) for p in _STEPPED_RE):
        return True
    if any(p.search(a) for p in _STEPPED_RE):
        return True
    return False


# ── Arrange text patterns ──
_ARRANGE_QUESTION_RE = [
    re.compile(r"\b(arrange|rearrange)\b", re.I),
    re.compile(r"\b(chronological\s+order)\b", re.I),
    re.compile(r"\b(correct\s+(order|sequence))\b", re.I),
    re.compile(r"\b(oldest\s+to\s+newest|earliest\s+to\s+latest)\b", re.I),
    re.compile(r"\b(ticker\s+tangle)\b", re.I),
    re.compile(r"\b(lowest\s+to\s+highest|highest\s+to\s+lowest)\b", re.I),
]

_ARRANGE_ANSWER_RE = [
    re.compile(r"[A-D]\s*[→➔➜>]\s*[A-D]\s*[→➔➜>]\s*[A-D]", re.I),
    re.compile(r"\b(correct\s+order|the\s+sequence)\b", re.I),
]

def _text_arrange(q: str, a: str) -> bool:
    if any(p.search(q) for p in _ARRANGE_QUESTION_RE):
        return True
    if any(p.search(a) for p in _ARRANGE_ANSWER_RE):
        return True
    return False


# ═══════════════════════════════════════════════════════════════
# LAYER 3: ROUND NAME
# ═══════════════════════════════════════════════════════════════

_ROUND_CONNECT_RE = re.compile(r"\b(connect|common\s*link|market\s*indicators)\b", re.I)
_ROUND_STEPPED_RE = re.compile(r"\b(portfolio\s+builder|stepped|decreasing|buzzer\s+clue)\b", re.I)
_ROUND_ARRANGE_RE = re.compile(r"\b(ticker\s+tangle|arrange|chronolog|sequence)\b", re.I)


def _round_format(round_name: Optional[Any]) -> Optional[str]:
    """Check if the round name itself identifies the format."""
    if not round_name:
        return None
    r = str(round_name).strip()
    # Check in priority order
    if _ROUND_STEPPED_RE.search(r):
        return STEPPED_CLUE
    if _ROUND_CONNECT_RE.search(r):
        return CONNECT_4
    if _ROUND_ARRANGE_RE.search(r):
        return ARRANGE_ORDER
    return None


# ═══════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════

def classify_format(
    question_text: str,
    answer_text: str,
    question_images: Optional[List[str]] = None,
    answer_images: Optional[List[str]] = None,
    round_name: Optional[str] = None,
) -> str:
    """
    Classify a question into one of four format types using all available signals.

    Priority: Stepped Clue > Connect 4 > Arrange > Standard.
    Each type is checked across all three layers; first match wins.
    """
    q = question_text or ""
    a = answer_text or ""

    # ── Stepped Clue (most unambiguous — points notation is unique) ──
    if _text_stepped(q, a):
        return STEPPED_CLUE
    if _structural_stepped_clue(q, question_images):
        return STEPPED_CLUE

    # ── List-It / Image Reveal / Buzzer guard (never misclassify grid/multi-image slides as Connect 4) ──
    if re.search(r"\b(?:list[\s\-]*it|image\s*reveal|closing\s*bell|rapid\s*fire|buzzers?|buzz(?!\s+aldrin))\b", f"{q} {round_name or ''}", re.I):
        if re.search(r"\blist[\s\-]*it\b", f"{q} {round_name or ''}", re.I):
            return "LIST_IT"
        if not _text_connect4(q, a):
            if re.search(r"\bimage\s*reveal\b", f"{round_name or ''}", re.I):
                return "IMAGE_REVEAL"
            if re.search(r"\b(?:closing\s*bell|rapid\s*fire|buzzers?|buzz(?!\s+aldrin))\b", f"{round_name or ''}", re.I):
                return "BUZZER"

    # ── Connect 4 ──
    if _structural_connect4(q, a, question_images, answer_images):
        return CONNECT_4
    if _text_connect4(q, a):
        return CONNECT_4

    # ── Arrange ──
    if _text_arrange(q, a):
        return ARRANGE_ORDER

    # ── Round name as fallback ──
    round_fmt = _round_format(round_name)
    if round_fmt:
        return round_fmt

    return STANDARD_QA


def classify_and_extract(
    question_text: str,
    answer_text: str,
    question_images: Optional[List[str]] = None,
    answer_images: Optional[List[str]] = None,
    round_name: Optional[str] = None,
    stored_format: Optional[str] = None,
    stored_payload: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Classify format AND build initial format_data from available fields.

    The format is decided ONCE, at ingestion, from the slide structure (ingestion/format_extractors). A question that
    carries that result (`stored_payload["extraction"]`) keeps it: this returns the stored format and no format_data.
    The text/regex guessing below is only for older rows ingested before that existed.

    For Connect 4 with images: auto-populates elements from question_images.
    For others: returns structured format_data where available.
    """
    if isinstance(stored_payload, dict) and stored_payload.get("extraction") and stored_format:
        return stored_format, None
    fmt = classify_format(question_text, answer_text, question_images, answer_images, round_name)
    format_data = None

    if fmt == CONNECT_4 and question_images and len(question_images) >= 3:
        format_data = {
            "type": "CONNECT_4",
            "prompt": question_text.strip() if question_text and len(question_text.strip()) >= 15 else "What connects these elements?",
            "elements": [
                {"label": f"Clue {i+1}", "image_url": img, "explanation": ""}
                for i, img in enumerate(question_images)
            ],
            "common_link": answer_text or "",
        }

    if fmt != STANDARD_QA:
        logger.info(f"Classified as {fmt}: q_images={len(question_images or [])}, "
                     f"answer={answer_text[:40]}...")

    return fmt, format_data
