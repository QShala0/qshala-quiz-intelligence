"""Question format detection, structuring, and payload management.

Handles specialized quiz formats:
1. CONNECT_4 / CONNECT: Multi-image or multi-clue common link questions.
2. STEPPED_CLUE / PORTFOLIO_BUILDER: 3-step progressive clues with decreasing buzzer points.
3. ARRANGE_ORDER / TICKER_TANGLE: 4 items to sort with graduated scoring.
4. VISUAL_CLUE, AUDIO_CLUE, VIDEO_CLUE, and STANDARD_QA.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


# Format Constants
FMT_QA = "QA"
FMT_CONNECT_4 = "CONNECT_4"
FMT_STEPPED_CLUE = "STEPPED_CLUE"
FMT_ARRANGE_ORDER = "ARRANGE_ORDER"
FMT_BUZZER = "BUZZER"
FMT_VISUAL_CLUE = "VISUAL_CLUE"
FMT_AUDIO_CLUE = "AUDIO_CLUE"
FMT_VIDEO_CLUE = "VIDEO_CLUE"
FMT_POTPOURRI = "POTPOURRI"
FMT_IMAGE_REVEAL = "IMAGE_REVEAL"
FMT_LIST_IT = "LIST_IT"
FMT_STANDARD_QA = "STANDARD_QA"
FMT_CONNECT = "CONNECT"        # CONNECT_4 is the legacy alias of CONNECT
FMT_MCQ = "MCQ"

FORMAT_LABELS: Dict[str, str] = {
    FMT_QA: "Question and Answer (Direct)",
    FMT_CONNECT_4: "Connect 4",
    "CONNECT": "Connect 4",
    FMT_STEPPED_CLUE: "Stepped Clue (Portfolio Builder)",
    "THREE_CLUE": "Stepped Clue (Portfolio Builder)",
    FMT_ARRANGE_ORDER: "Arrange the Order (Ticker Tangle)",
    FMT_BUZZER: "Buzzer (Closing Bell / Rapid Fire)",
    FMT_VISUAL_CLUE: "Visual Clue",
    FMT_AUDIO_CLUE: "Audio Clue",
    FMT_VIDEO_CLUE: "Video Clue",
    FMT_POTPOURRI: "Potpourri",
    FMT_IMAGE_REVEAL: "Image Reveal",
    FMT_LIST_IT: "List It",
    FMT_STANDARD_QA: "Question and Answer",
    FMT_MCQ: "Multiple Choice",
}


# Regex detectors
_STEPPED_RE = re.compile(
    r"(?:clue\s*1\s*:\s*\+?20\s*/\s*-?15|clue\s*2\s*:\s*\+?15\s*/\s*-?10|clue\s*3\s*:\s*\+?10\s*/\s*-?5|"
    r"portfolio\s*builder|3\s*parts/clues?\s*to\s*each\s*question)",
    re.IGNORECASE
)

_ARRANGE_RE = re.compile(
    r"(?:\barrange\b|ticker\s*tangle|chronological(?:\s*order)?|lowest\s+to\s+highest|"
    r"highest\s+to\s+lowest|earliest\s+to\s+latest|from\s+1st\s+to\s+last|correct\s+order|"
    r"in\s+(?:the\s+)?order\s+of)",
    re.IGNORECASE
)

_CONNECT_RE = re.compile(
    r"(?:\bmarket\s*indicators\b|^\s*(?:\d+[\.\)]\s*)?connect\b|\bconnect\s+(?:these|them|all)\b|what\s+(?:connects|links)|common\s*(?:theme|link|thread)|find\s+the\s+link|"
    r"4\s*pics\s*1\s*word|four\s*images|connect\s*4)",
    re.IGNORECASE
)


def detect_and_structure_format(
    question_text: str,
    answer: str,
    options: Optional[List[str]] = None,
    image_refs: Optional[List[str]] = None,
    audio_refs: Optional[List[str]] = None,
    video_refs: Optional[List[str]] = None,
    raw_context: str = "",
    deck_round_format: str = "",
    structured: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Inspects question fields and returns (format_name, format_payload).

    `structured` is the format-aware extractor's result for this question (its `_ingest` block plus payload). When
    it is given, THAT is the format: the guessing below only runs for decks the structure-based path could not read.
    """
    if structured and structured.get("question_format"):
        return structured["question_format"], structured.get("format_payload")
    q_text = question_text or ""
    combined = f"{q_text} {raw_context} {deck_round_format}".strip()
    imgs = image_refs or []
    vids = video_refs or []
    auds = audio_refs or []
    opts = options or []

    # 1. Stepped Clue (Portfolio Builder / 3 Clues buzzer round)
    if _STEPPED_RE.search(combined) or deck_round_format in ("STEPPED_CLUE", "THREE_CLUE", "PORTFOLIO_BUILDER") or (deck_round_format == "BUZZER" and ("clue 1" in q_text.lower() or "+20/-15" in combined.lower())):
        clues: List[Dict[str, Any]] = []
        try:
            from backend.app.services.ingestion.heal_specialized import parse_stepped_clues
            c1, c2, c3 = parse_stepped_clues(q_text)
            if c1 and c2 and c3:
                clues = [
                    {"step": 1, "points": "+20/-15", "points_correct": 20, "points_wrong": -15, "points_label": "+20 / -15", "difficulty_label": "Hard Clue", "text": c1, "image": imgs[0] if len(imgs) > 0 else None, "image_url": imgs[0] if len(imgs) > 0 else None},
                    {"step": 2, "points": "+15/-10", "points_correct": 15, "points_wrong": -10, "points_label": "+15 / -10", "difficulty_label": "Medium Clue", "text": c2, "image": imgs[1] if len(imgs) > 1 else None, "image_url": imgs[1] if len(imgs) > 1 else None},
                    {"step": 3, "points": "+10/-5", "points_correct": 10, "points_wrong": -5, "points_label": "+10 / -5", "difficulty_label": "Giveaway Clue", "text": c3, "image": imgs[2] if len(imgs) > 2 else None, "image_url": imgs[2] if len(imgs) > 2 else None},
                ]
        except Exception:
            pass

        if not clues:
            clues = [
                {"step": 1, "points": "+20/-15", "text": q_text, "image": imgs[0] if imgs else None, "image_url": imgs[0] if imgs else None},
                {"step": 2, "points": "+15/-10", "text": "Additional context clue", "image": imgs[1] if len(imgs) > 1 else None, "image_url": imgs[1] if len(imgs) > 1 else None},
                {"step": 3, "points": "+10/-5", "text": "Direct giveaway clue", "image": imgs[2] if len(imgs) > 2 else None, "image_url": imgs[2] if len(imgs) > 2 else None},
            ]
        payload = {
            "type": FMT_STEPPED_CLUE,
            "badge": "Portfolio Builder • Stepped Clues",
            "clues": clues,
            "rules": "All on buzzer • 1 attempt per clue • Clue 1: +20/-15, Clue 2: +15/-10, Clue 3: +10/-5",
            "answer": answer,
        }
        return FMT_STEPPED_CLUE, payload

    # 2. Arrange Order (Ticker Tangle / Chronology)
    if _ARRANGE_RE.search(combined) or deck_round_format in ("ARRANGE_ORDER", "TICKER_TANGLE"):
        items: List[Dict[str, Any]] = []
        # Check if options contain 4 items (A, B, C, D)
        if opts and len(opts) >= 4:
            for i, opt in enumerate(opts[:4]):
                items.append({"label": i + 1, "key": str(i + 1), "text": opt, "image": imgs[i] if i < len(imgs) else None})
        elif imgs and len(imgs) >= 4:
            for i in range(4):
                items.append({"label": i + 1, "key": str(i + 1), "text": f"Item {i + 1}", "image": imgs[i]})
        else:
            # Extract lines from question text if separated
            lines = [l.strip(" 1234.ABCD)-") for l in q_text.split("\n") if l.strip()]
            if len(lines) >= 4:
                for i in range(4):
                    items.append({"label": i + 1, "key": str(i + 1), "text": lines[i], "image": imgs[i] if i < len(imgs) else None})
            else:
                items = [{"label": n, "key": str(n), "text": f"Item {n}", "image": imgs[n - 1] if len(imgs) >= n else None}
                         for n in (1, 2, 3, 4)]

        payload = {
            "type": FMT_ARRANGE_ORDER,
            "badge": "Ticker Tangle • Arrange Order",
            "instruction": q_text,
            "items": items,
            "scoring": {"all_correct": "+20", "two_correct": "+10", "one_correct": "+5"},
            "has_timer": True,
            "timer_seconds": 60,
            "time_limit_seconds": 60,
        }
        return FMT_ARRANGE_ORDER, payload

    # 2b. List-It! / round-level formats before structural >=4 images Connect-4 fallback
    if (
        deck_round_format == "LIST_IT"
        or re.search(r"\blist[\s\-]*it\b", combined, re.IGNORECASE)
        or ((raw_context or "").count("|") >= 2 and len(opts) >= 4)
        or ((answer or "").lower().startswith("select matching items") and len(opts) >= 4)
        or (len(opts) >= 6 and ";" in (answer or ""))
    ):
        return FMT_LIST_IT, {
            "type": FMT_LIST_IT,
            "badge": "List It",
            "prompt": q_text,
            "options": opts,
        }
    if deck_round_format == "IMAGE_REVEAL" and not _CONNECT_RE.search(q_text):
        return FMT_IMAGE_REVEAL, {"type": FMT_IMAGE_REVEAL, "badge": "Image Reveal"}
    if deck_round_format in ("BUZZER", "CLOSING_BELL", "RAPID_FIRE") and not _CONNECT_RE.search(q_text):
        return FMT_BUZZER, {"type": FMT_BUZZER, "badge": "Buzzer / Rapid Fire"}
    if deck_round_format == "POTPOURRI" and not _CONNECT_RE.search(q_text):
        return FMT_POTPOURRI, {"type": FMT_POTPOURRI, "badge": "Potpourri"}

    # 3. Connect / Connect 4
    if _CONNECT_RE.search(combined) or deck_round_format in ("CONNECT_4", "CONNECT", "MARKET_INDICATORS") or (imgs and len(imgs) >= 4 and not opts and deck_round_format not in ("IMAGE_REVEAL", "LIST_IT", "BUZZER", "POTPOURRI")):
        elements: List[Dict[str, Any]] = []
        for i in range(max(4, len(imgs))):
            img = imgs[i] if i < len(imgs) else None
            elements.append({
                "index": i + 1,
                "label": f"Clue {i + 1}",
                "text": f"Clue {i + 1}",
                "image": img,
                "image_url": img,
                "explanation": "",
            })
        payload = {
            "type": FMT_CONNECT_4,
            "badge": "Connect 4 • Common Link",
            "prompt": q_text or "What common thread or concept connects all four clues and images?",
            "elements": elements,
            "common_link": answer,
            "connection_reveal": answer,
        }
        return FMT_CONNECT_4, payload

    # 4. Specific deck round formats
    if deck_round_format in ("BUZZER", "CLOSING_BELL", "RAPID_FIRE"):
        return FMT_BUZZER, {"type": FMT_BUZZER, "badge": "Buzzer / Rapid Fire"}
    if deck_round_format == "POTPOURRI":
        return FMT_POTPOURRI, {"type": FMT_POTPOURRI, "badge": "Potpourri"}
    if deck_round_format == "IMAGE_REVEAL":
        return FMT_IMAGE_REVEAL, {"type": FMT_IMAGE_REVEAL, "badge": "Image Reveal"}
    if deck_round_format == "LIST_IT":
        return FMT_LIST_IT, {"type": FMT_LIST_IT, "badge": "List It"}

    # 5. Media-specific clues
    if vids:
        return FMT_VIDEO_CLUE, {"type": FMT_VIDEO_CLUE, "video_url": vids[0]}
    if auds:
        return FMT_AUDIO_CLUE, {"type": FMT_AUDIO_CLUE, "audio_url": auds[0]}
    if imgs:
        return FMT_VISUAL_CLUE, {"type": FMT_VISUAL_CLUE, "image_url": imgs[0]}

    # 6. Default QA
    return FMT_QA, {"type": FMT_QA}
