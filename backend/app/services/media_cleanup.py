"""Deleting picture files that a question no longer uses ("Edit images").

When a picture is removed from a question, the stored file is deleted too, but only when nothing else still points at
it. A file can be shared: another question (duplicates and "Repeated" rows reuse pictures), a saved quiz, a slide,
or a 30-day trash snapshot that could still be restored. If any of those mention the file, or if
anything about the check is uncertain, the file is kept. Only files this app can safely identify as its own are ever
deleted: objects under the configured R2 public URL, and pictures the editor itself saved to local disk.

Switch off with DELETE_REMOVED_IMAGES=0 (removed pictures are then only detached from the question).
"""
import logging
import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from sqlalchemy import String, cast, inspect, literal, or_, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# The video-camera placeholder icons the viewer hides. They are shared decoration, never deleted.
PLACEHOLDER_FILENAMES = {
    "slide_41_img_1.png", "slide_40_img_1.png", "slide_49_img_1.png",
    "slide_53_img_1.png", "slide_120_img_1.png", "slide_122_img_1.png",
    "image16.png",
}

# Columns that can hold a picture URL, per kind of table (the same names are used in the trash holding tables).
_REF_COLUMNS = ("question_images", "answer_images", "image_refs", "raw_media_refs", "format_payload", "image_paths")


def enabled() -> bool:
    return (os.getenv("DELETE_REMOVED_IMAGES", "1").strip().lower() not in ("0", "false", "no", "off"))


def is_placeholder(url: str) -> bool:
    name = (url or "").replace("\\", "/").rstrip("/").split("/")[-1].lower()
    return name in PLACEHOLDER_FILENAMES


def storage_key(url: str) -> Optional[str]:
    """The part of a picture URL that identifies the stored file, the same whether it is written as a full CDN URL or as
    a /static/... path (so both spellings of one file are recognised as the same file)."""
    if not url:
        return None
    path = urlparse(url).path if "://" in url else url.split("?")[0]
    path = path.lstrip("/")
    for prefix in ("api/v1/static/", "static/"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    return path or None


def _like(fragment: str) -> str:
    esc = fragment.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def _patterns(key: str) -> List[str]:
    """Match the key only where a path starts: after a "/" (inside a URL or path) or right after a quote (a bare key in
    JSON). So "a.png" is not found inside "banana.png", while every real spelling of the same file is still found."""
    pats = set()
    for k in (key, unquote(key)):
        pats.add(_like("/" + k))
        pats.add(_like('"' + k))
    return list(pats)


def is_referenced(db: Session, key: str) -> bool:
    """True if any row anywhere mentions this file, or if that cannot be established (then the file is kept)."""
    from backend.app.models.document import Slide
    from backend.app.models.question import Question
    from backend.app.models.quiz import GeneratedQuestion

    pats = _patterns(key)
    try:
        for model in (Question, GeneratedQuestion, Slide):
            cols = [getattr(model, c) for c in _REF_COLUMNS if hasattr(model, c)]
            if not cols:
                continue
            cond = or_(*[cast(c, String).like(p, escape="\\") for c in cols for p in pats])
            if db.query(literal(1)).select_from(model).filter(cond).limit(1).first() is not None:
                return True

        # Deleted decks and question banks are kept in holding tables for a while so they can be restored.
        insp = inspect(db.get_bind())
        for table in insp.get_table_names():
            if not re.match(r"^trash_.+_(questions|slides|documents)$", table):
                continue
            cols = [c["name"] for c in insp.get_columns(table) if c["name"] in _REF_COLUMNS]
            if not cols:
                continue
            clauses, params = [], {}
            for ci, col in enumerate(cols):
                for pi, p in enumerate(pats):
                    name = f"p{ci}_{pi}"
                    clauses.append(f'CAST("{col}" AS TEXT) LIKE :{name} ESCAPE \'\\\'')
                    params[name] = p
            sql = text(f'SELECT 1 FROM "{table}" WHERE ' + " OR ".join(clauses) + " LIMIT 1")
            if db.execute(sql, params).first() is not None:
                return True
        return False
    except Exception as e:
        logger.warning("[media-cleanup] could not check references for %s, keeping it: %s", key, type(e).__name__)
        try:
            db.rollback()
        except Exception:
            pass
        return True


def _target(url: str, r2) -> Optional[Tuple[str, object]]:
    """How to delete this URL, or None if it is not a file this app owns."""
    base = getattr(r2, "public_base_url", "") if r2 is not None else ""
    if base and url.startswith(base + "/"):
        key = unquote(url[len(base) + 1:].split("?")[0])
        return ("r2", key) if key and not key.endswith("/") else None
    local_prefix = "/static/slides/questions/"
    if url.startswith(local_prefix):
        from backend.app.config import settings
        root = (settings.SLIDES_DIR / "questions").resolve()
        path = (settings.SLIDES_DIR / url[len("/static/slides/"):]).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return None
        return ("local", path)
    return None


def delete_unreferenced(db: Session, urls: Iterable[str]) -> Dict[str, List[str]]:
    """Delete each URL's stored file when nothing else uses it. Call this AFTER the change has been committed, so the
    question's own row no longer lists the picture. Never raises: a failure keeps the file and is logged."""
    out: Dict[str, List[str]] = {"deleted": [], "kept": [], "skipped": []}
    urls = sorted({u for u in urls if u})
    if not urls:
        return out
    if not enabled():
        out["skipped"] = urls
        return out
    try:
        from backend.app.services.storage.r2_storage import get_media_storage
        r2 = get_media_storage()
    except Exception:
        r2 = None
    for url in urls:
        key = storage_key(url)
        target = _target(url, r2)
        if target is None or not key or is_placeholder(url) or not key.isascii():
            out["skipped"].append(url)            # not ours, a shared placeholder, or a name we cannot check safely
            continue
        if is_referenced(db, key):
            out["kept"].append(url)
            continue
        try:
            kind, where = target
            if kind == "r2":
                r2.delete_object(where)
            else:
                Path(where).unlink(missing_ok=True)
            out["deleted"].append(url)
            logger.info("[media-cleanup] deleted unused picture %s", key)
        except Exception as e:
            out["kept"].append(url)
            logger.warning("[media-cleanup] could not delete %s: %s", key, type(e).__name__)
    return out
