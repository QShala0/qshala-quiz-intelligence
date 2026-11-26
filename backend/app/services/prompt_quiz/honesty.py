"""Things a quizmaster asks for that the builder cannot do. The draft message must say so instead of describing a quiz that was not built.

Pure and deterministic: `honesty_notes` reads the prompt and the brief and returns short plain sentences. When any are returned
the host message drops Gemini's framing (written before retrieval, so it can promise things the draft does not contain).
"""
import re
from typing import Any, List

_LANGS = r"hindi|telugu|tamil|kannada|malayalam|marathi|bengali|gujarati|punjabi|urdu|odia"

_INJECTION = re.compile(
    r"\bignore\b.{0,30}\b(?:instructions?|rules|prompt)\b|\b(?:show|reveal|print|repeat)\b.{0,25}\bsystem\s+prompt\b|\bsystem\s+prompt\b",
    re.I,
)
_GENERATE = re.compile(
    r"\byou\s+(?:generate|write|make\s+up|create)\b|\bgenerate\s+(?:fresh|new|\d+|some|original)\b|"
    r"\b(?:write|create|make)\s+(?:me\s+)?(?:new|fresh|original)\s+questions?\b|\bfresh\s+questions?\s+you\b|\bnew\s+questions?\s+you\b",
    re.I,
)
_LANGUAGE = re.compile(
    rf"\bbilingual\b|\btanglish\b|\bhinglish\b|\b(?:in|answers?\s+in|questions?\s+in)\s+(?:{_LANGS})\b|\b(?:{_LANGS})\s+(?:quiz|questions?)\b",
    re.I,
)
_ANSWER_ON_QUESTION = re.compile(
    r"\banswers?\b.{0,40}\b(?:on|in|with)\s+(?:the\s+)?question\s+slides?\b|\bshow\s+(?:the\s+)?answers?\s+(?:with|alongside|on)\b",
    re.I,
)
_PAST_QUIZZES = re.compile(
    r"\b(?:last|previous|past|earlier|recent)\s+(?:\d+|one|two|three|four|five|few|couple\s+of)?\s*(?:quiz|quizzes|sessions?|rounds?)\b|"
    r"\blike\s+last\s+time\b|\bsame\s+as\s+(?:last|before)\b|\bnot\s+(?:been\s+)?(?:used|asked)\s+before\b",
    re.I,
)
_BIG_COUNT = re.compile(r"(?<![\w-])(\d{2,5})\s+(?:questions?|qs)\b", re.I)
_NEG_COUNT = re.compile(r"(?<!\w)-\s*(\d+)\s+(?:questions?|qs)\b", re.I)


def honesty_notes(prompt: str, brief: Any, max_questions: int = 50) -> List[str]:
    p = prompt or ""
    notes: List[str] = []

    if _INJECTION.search(p):
        notes.append("I can't act on instructions about my own setup, so I built a general quiz instead.")

    if _GENERATE.search(p):
        notes.append("I only pick from the question bank, I can't write new questions, so every question here is already in the bank.")

    if _LANGUAGE.search(p):
        notes.append("I can't filter or translate by language; the questions come exactly as they are in the bank.")

    if _ANSWER_ON_QUESTION.search(p):
        notes.append("Answers always stay on their own slide and never on the question slide, so that part was not applied.")

    if _PAST_QUIZZES.search(p):
        notes.append("I can't see your earlier quizzes yet, so nothing was excluded because it was used before.")

    neg = _NEG_COUNT.search(p)
    if neg:
        notes.append(f"A negative number of questions makes no sense, so I used {neg.group(1)}.")
    else:
        for m in _BIG_COUNT.finditer(p):
            if int(m.group(1)) > max_questions:
                notes.append(f"A quiz is limited to {max_questions} questions, so I capped it at {max_questions}.")
                break

    deck = getattr(brief, "source_deck", None)
    filters = getattr(brief, "filters", None) or {}
    if deck and not (filters.get("deck_filter") or filters.get("document_ids")):
        notes.append(f"I couldn't find a deck called \"{deck}\" in the bank, so this is not limited to it.")

    return notes
