"""AI-assisted slide-role and geometry detection for custom PPT templates.

One small, text-only Gemini call per template upload. It looks at a compact digest of at most MAX_DIGEST_SLIDES
representative slides (never the whole deck, never images) and returns which slide is the cover, the question,
the answer-reveal divider and the answer slide, plus the text boxes and spacing of the question/answer slides.

Everything is validated; any bad field is dropped so the rule-based result in template_extractor stays in charge.
Any failure returns None and the extractor behaves exactly as it did before this module existed.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from pptx.util import Emu

logger = logging.getLogger(__name__)

MAX_DIGEST_SLIDES = 8
MAX_SCAN_SLIDES = 60
MAX_SHAPES_PER_SLIDE = 10
TEXT_SNIPPET_CHARS = 60
TIMEOUT_S = 20.0
OPERATION = "template_classify"

# Fixed by the style guide: title 44pt, body 28pt (never inferred from the sample deck).
TITLE_SIZE_PT = 44.0
BODY_SIZE_PT = 28.0

_BOX = {
    "type": "OBJECT",
    "properties": {
        "left": {"type": "NUMBER"}, "top": {"type": "NUMBER"},
        "width": {"type": "NUMBER"}, "height": {"type": "NUMBER"},
    },
}

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "cover_idx": {"type": "INTEGER"},
        "question_idx": {"type": "INTEGER"},
        "answer_reveal_idx": {"type": "INTEGER"},
        "answer_idx": {"type": "INTEGER"},
        "question_box": _BOX,
        "answer_box": _BOX,
        "media_box": _BOX,
        "spacing_pt": {
            "type": "OBJECT",
            "properties": {
                "title_to_body": {"type": "NUMBER"},
                "between_options": {"type": "NUMBER"},
            },
        },
    },
}

_SYSTEM = (
    "You inspect a digest of a quiz-master PowerPoint template. Each slide has an 'i' index, its layout name and its "
    "shapes (kind, name, box in inches, text snippet, font size). Decide the role of each slide. Roles: "
    "cover = the opening title slide; question = a slide that asks a quiz question (text, maybe options or a picture); "
    "answer_reveal = a short divider slide that only announces the answer in any language (e.g. 'Answer', 'ಉತ್ತರ', "
    "'उत्तर') or is an image-only divider; answer = a slide that shows the actual answer. "
    "Return ONLY indexes 'i' that appear in the digest. Omit a field when no slide fits. "
    "Boxes are in inches and must come from shapes you can see (question text box, answer text box, picture box). "
    "spacing_pt is the vertical gap in points between the title and body text, and between option lines, if visible."
)


# --------------------------------------------------------------------------- digest
def _inches(v) -> float:
    try:
        return round(float(Emu(int(v)).inches), 2)
    except Exception:
        return 0.0


def _shape_kind(shape) -> str:
    try:
        if shape.is_placeholder:
            return "placeholder"
    except Exception:
        pass
    st = str(getattr(shape, "shape_type", "")).lower()
    if "picture" in st:
        return "picture"
    if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
        return "text"
    return "shape"


def _font_size(shape) -> Optional[float]:
    try:
        for p in shape.text_frame.paragraphs:
            for r in p.runs:
                if r.font.size:
                    return round(float(r.font.size.pt), 1)
    except Exception:
        pass
    return None


def candidate_indexes(slides: List[Any], is_divider) -> List[int]:
    """Rule-picked slide positions to show the model: slide 0, the first slide of each distinct layout, any divider."""
    scan = slides[:MAX_SCAN_SLIDES]
    picked: List[int] = []
    seen_layouts: set = set()
    for i, sl in enumerate(scan):
        try:
            lname = sl.slide_layout.name
        except Exception:
            lname = ""
        important = i == 0 or is_divider(sl)
        if important or lname not in seen_layouts:
            if len(picked) < MAX_DIGEST_SLIDES:
                picked.append(i)
            elif important and i not in picked:
                # a divider must not be starved by earlier layouts: swap out the last non-first, non-divider pick
                for j in range(len(picked) - 1, 0, -1):
                    if not is_divider(scan[picked[j]]):
                        picked[j] = i
                        break
            seen_layouts.add(lname)
    return sorted(set(picked))


def build_digest(slides: List[Any], indexes: List[int], slide_w_in: float, slide_h_in: float) -> Dict[str, Any]:
    out: List[Dict[str, Any]] = []
    for i in indexes:
        sl = slides[i]
        shapes = []
        for sh in list(sl.shapes)[:MAX_SHAPES_PER_SLIDE]:
            entry: Dict[str, Any] = {
                "kind": _shape_kind(sh),
                "box": [_inches(sh.left or 0), _inches(sh.top or 0), _inches(sh.width or 0), _inches(sh.height or 0)],
            }
            if getattr(sh, "has_text_frame", False):
                txt = re.sub(r"\s+", " ", sh.text_frame.text or "").strip()
                if txt:
                    entry["text"] = txt[:TEXT_SNIPPET_CHARS]
                    fs = _font_size(sh)
                    if fs:
                        entry["pt"] = fs
            shapes.append(entry)
        try:
            lname = sl.slide_layout.name
        except Exception:
            lname = ""
        out.append({"i": i, "layout": lname, "shapes": shapes})
    return {"slide_in": [slide_w_in, slide_h_in], "slides": out}


# --------------------------------------------------------------------------- validation
def _valid_box(box: Any, w: float, h: float) -> Optional[Dict[str, float]]:
    if not isinstance(box, dict):
        return None
    try:
        left, top, bw, bh = (float(box[k]) for k in ("left", "top", "width", "height"))
    except Exception:
        return None
    if bw < 1.0 or bh < 0.5:
        return None
    if left < -0.01 or top < -0.01 or left + bw > w + 0.05 or top + bh > h + 0.05:
        return None
    return {"left": round(left, 2), "top": round(top, 2), "width": round(bw, 2), "height": round(bh, 2)}


def validate(raw: Any, allowed: List[int], slide_w: float, slide_h: float) -> Dict[str, Any]:
    """Keep only fields that are well formed and refer to slides that were in the digest."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in ("cover_idx", "question_idx", "answer_reveal_idx", "answer_idx"):
        v = raw.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and v in allowed:
            out[key] = v
    for key in ("question_box", "answer_box", "media_box"):
        b = _valid_box(raw.get(key), slide_w, slide_h)
        if b:
            out[key] = b
    sp = raw.get("spacing_pt")
    if isinstance(sp, dict):
        clean = {}
        for k in ("title_to_body", "between_options"):
            try:
                val = float(sp.get(k))
            except (TypeError, ValueError):
                continue
            if 0 <= val <= 72:
                clean[k] = round(val, 1)
        if clean:
            out["spacing_pt"] = clean
    # A cover cannot also be the reveal slide.
    if out.get("cover_idx") is not None and out.get("cover_idx") == out.get("answer_reveal_idx"):
        out.pop("answer_reveal_idx", None)
    return out


# --------------------------------------------------------------------------- model call
def _call_gemini(digest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Blocking, single call. Returns the parsed JSON dict or None. Never raises."""
    model = "unknown"
    try:
        from backend.app.config import settings
        from backend.app.services.ai.model_config import template_model, thinking_config_rest
        from backend.app.services.prompt_quiz._http import guarded_post

        key = (getattr(settings, "GEMINI_API_KEY", None) or "").strip().strip("\"'")
        if not key:
            return None
        model = template_model()
        tc = thinking_config_rest(model, "minimal")
        payload = {
            "contents": [{"parts": [{"text": "TEMPLATE DIGEST:\n" + json.dumps(digest, ensure_ascii=False, separators=(",", ":"))}]}],
            "system_instruction": {"parts": [{"text": _SYSTEM}]},
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": SCHEMA,
                "temperature": 0.0,
                "maxOutputTokens": 800,
                **({"thinkingConfig": tc} if tc else {}),
            },
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        resp = guarded_post(url, json=payload, headers={"x-goog-api-key": key}, timeout=TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
        _track(model, data.get("usageMetadata"))
        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        text = next((p["text"] for p in reversed(parts) if "text" in p and not p.get("thought")), "")
        clean = (text or "").strip()
        clean = re.sub(r"^```(?:json)?|```$", "", clean).strip()
        parsed = json.loads(clean)
        return parsed if isinstance(parsed, dict) else None
    except Exception as e:  # noqa: BLE001 - every failure means "use the rules"
        logger.info("Template AI classification unavailable, using rules: %s", type(e).__name__)
        return None


def _track(model: str, usage: Optional[Dict[str, Any]]) -> None:
    try:
        from backend.app.services.costs.quiz_tracking import track_quiz_llm_call
        track_quiz_llm_call(OPERATION, model, usage_metadata=usage, note="template upload")
    except Exception:
        pass


def classify_template(slides: List[Any], slide_w_in: float, slide_h_in: float, is_divider) -> Optional[Dict[str, Any]]:
    """Return validated role/geometry hints, or None when the model is unavailable or says nothing usable."""
    if not slides:
        return None
    try:
        indexes = candidate_indexes(slides, is_divider)
        digest = build_digest(slides, indexes, slide_w_in, slide_h_in)
        raw = _call_gemini(digest)
        if raw is None:
            return None
        result = validate(raw, indexes, slide_w_in, slide_h_in)
        return result or None
    except Exception as e:  # noqa: BLE001
        logger.info("Template AI classification failed, using rules: %s", type(e).__name__)
        return None
