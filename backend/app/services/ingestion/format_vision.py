"""Optional Gemini check for format extractions that landed in the middle confidence band (0.7 to 0.9).

    >= 0.9   accepted as extracted (no AI call)
    0.7-0.9  one small vision call per question, ONLY when FORMAT_VISION_CHECK=1: "does this extraction match the
             slide pictures?"  Yes -> accepted; no -> the question goes to the review queue
    < 0.7    review queue (no AI call)

Off by default. FORMAT_VISION_MAX_CALLS caps the calls per deck so a badly-built deck cannot run up a bill.
The check looks at the question's own pictures (this repo renders no full-slide images).
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_IMAGES = 4
MAX_SIDE = 1024

PROMPT = (
    "You are checking an automatic reading of a quiz slide. The pictures are the question's own pictures. "
    "The program read this question as format {fmt} with this content:\n{summary}\n\n"
    "Is that reading plausible for these pictures and this text? Answer with JSON only: "
    '{{"ok": true|false, "reason": "<one short sentence>"}}'
)


def vision_check_enabled() -> bool:
    return os.getenv("FORMAT_VISION_CHECK", "").strip().lower() in {"1", "true", "yes", "on"}


def max_calls() -> int:
    try:
        return max(0, int(os.getenv("FORMAT_VISION_MAX_CALLS", "50")))
    except ValueError:
        return 50


def in_band(q: Dict[str, Any]) -> bool:
    ing = q.get("_ingest") or {}
    return ing.get("route") == "vision"


def summarize(q: Dict[str, Any]) -> str:
    p = dict(q.get("format_payload") or {})
    for k in ("extraction", "round_name", "round_number", "team"):
        p.pop(k, None)
    s = json.dumps(p, ensure_ascii=False, default=str)
    return (s[:1500] + "...") if len(s) > 1500 else s


def parse_verdict(text: str) -> Optional[Tuple[bool, str]]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    if "ok" not in d:
        return None
    return bool(d["ok"]), str(d.get("reason") or "")[:200]


def apply_verdict(q: Dict[str, Any], ok: bool, reason: str) -> None:
    ing = q["_ingest"]
    ex = (q.get("format_payload") or {}).get("extraction")
    if ok:
        ing["route"], ing["needs_review"], ing["verified"] = "accept", False, True
        ing["reasons"] = list(ing.get("reasons") or []) + ["vision check agreed"]
    else:
        ing["route"], ing["needs_review"], ing["verified"] = "review", True, False
        ing["reasons"] = list(ing.get("reasons") or []) + [f"vision check disagreed: {reason}"]
    if ex is not None:
        ex["route"], ex["needs_review"], ex["reasons"] = ing["route"], ing["needs_review"], ing["reasons"]


async def default_fetch(url: str) -> Optional[Tuple[bytes, str]]:
    """Bytes of one stored picture (local copy or R2 URL), shrunk to MAX_SIDE. None when unreadable."""
    data: Optional[bytes] = None
    try:
        if url.startswith("/static/slides/"):
            from backend.app.config import settings
            path = settings.SLIDES_DIR / url[len("/static/slides/"):]
            if path.exists():
                data = path.read_bytes()
        elif url.startswith(("http://", "https://")):
            import httpx
            async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": "Mozilla/5.0 (compatible; QShalaIngestion/1.0)"}) as c:
                r = await c.get(url)
                r.raise_for_status()
                data = r.content
        if not data:
            return None
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80)
            return buf.getvalue(), "image/jpeg"
    except Exception as e:
        logger.debug("format vision: could not read %s: %s", url, e)
        return None


async def verify_band(questions: List[Dict[str, Any]], provider: Any,
                      fetch: Callable[[str], Awaitable[Optional[Tuple[bytes, str]]]] = default_fetch,
                      limit: Optional[int] = None) -> Dict[str, int]:
    """Run the check on every in-band question (up to `limit`). Returns {"checked", "agreed", "disagreed", "skipped"}."""
    import base64
    stats = {"checked": 0, "agreed": 0, "disagreed": 0, "skipped": 0}
    cap = max_calls() if limit is None else limit
    for q in questions:
        if not in_band(q):
            continue
        if stats["checked"] >= cap:
            stats["skipped"] += 1
            continue
        urls = list(dict.fromkeys((q.get("question_images") or []) + (q.get("image_refs") or [])))[:MAX_IMAGES]
        media = []
        for u in urls:
            got = await fetch(u)
            if got:
                media.append({"mime_type": got[1], "data": base64.b64encode(got[0]).decode("ascii")})
        if not media:
            stats["skipped"] += 1
            continue
        prompt = PROMPT.format(fmt=q["_ingest"]["question_format"], summary=summarize(q))
        try:
            text, _usage = await provider.generate_multimodal_ex(prompt, media, max_output_tokens=150, thinking_level="minimal",
                                                                 usage_label="format_check")
        except Exception as e:                     # an unavailable key / quota must not block ingestion
            logger.warning("format vision check failed (kept as extracted): %s", type(e).__name__)
            stats["skipped"] += 1
            continue
        verdict = parse_verdict(text)
        if verdict is None:
            stats["skipped"] += 1
            continue
        stats["checked"] += 1
        apply_verdict(q, *verdict)
        stats["agreed" if verdict[0] else "disagreed"] += 1
    return stats
