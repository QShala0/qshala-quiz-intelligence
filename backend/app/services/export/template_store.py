"""
Durable storage for extracted custom PPT templates.

Why this exists: a template is a folder of files (master.pptx, background images, previews). On Cloud Run the local
disk is wiped on every restart, scale-to-zero and deploy, while the database row survives. Before this module the row
kept pointing at files that no longer existed, and exports silently fell back to the default deck.

Contract:
  * extraction writes to the local cache dir  <CUSTOM_TEMPLATES_DIR>/<template_id>/
  * `upload_template_dir` copies every file there to R2 under  custom-templates/<template_id>/  (when R2 is configured)
    and returns the file names; the router stores them in layout_metadata["stored_files"]
  * everything that reads a template calls `ensure_local_dir(tpl)` first: it returns the local folder, re-downloading
    the files from R2 when the local cache is gone, or None when the template really is unrecoverable
  * without R2 (local dev) behaviour is unchanged: local disk only
"""
from __future__ import annotations

import logging
import mimetypes
import os
import shutil
import threading
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path(__file__).resolve().parents[3] / "storage" / "custom_templates"
CUSTOM_TEMPLATES_DIR = Path(os.getenv("CUSTOM_TEMPLATES_DIR") or _DEFAULT_DIR)
MASTER_NAME = "master.pptx"
R2_ROOT = "custom-templates"

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(template_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(template_id, threading.Lock())


def _storage():
    """The configured R2 service, or None (local dev / R2 not set up)."""
    try:
        from backend.app.services.storage.r2_storage import get_media_storage
        return get_media_storage()
    except Exception as e:  # pragma: no cover - import problems must never break exports
        logger.warning("R2 unavailable for template storage: %s", e)
        return None


def r2_prefix(template_id: str) -> str:
    return f"{R2_ROOT}/{template_id}/"


def local_dir_for(template_id: str) -> Path:
    return CUSTOM_TEMPLATES_DIR / template_id


def upload_template_dir(template_id: str, src_dir: Path) -> List[str]:
    """Copy every file in `src_dir` to R2. Returns the uploaded file names ([] when R2 is not configured).
    Raises when R2 IS configured but an upload fails: a template that cannot be recovered must not be saved."""
    svc = _storage()
    if svc is None:
        logger.warning(
            "R2 is not configured: template %s is stored on local disk only and will be lost when the server restarts.",
            template_id,
        )
        return []
    names: List[str] = []
    for p in sorted(Path(src_dir).iterdir()):
        if not p.is_file():
            continue
        ctype = mimetypes.guess_type(p.name)[0]
        if p.suffix.lower() == ".pptx":
            ctype = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        svc.upload_file(r2_prefix(template_id) + p.name, str(p), content_type=ctype)
        names.append(p.name)
    return names


def stored_files(tpl: Any) -> List[str]:
    meta = getattr(tpl, "layout_metadata", None) or {}
    files = meta.get("stored_files") or []
    return [f for f in files if isinstance(f, str) and "/" not in f and "\\" not in f]


def _candidate_dirs(tpl: Any) -> List[Path]:
    dirs: List[Path] = []
    sp = getattr(tpl, "storage_path", None)
    if sp:
        dirs.append(Path(sp).parent)
    dirs.append(local_dir_for(tpl.id))
    return dirs


def ensure_local_dir(tpl: Any) -> Optional[Path]:
    """Folder holding this template's files on this machine, restoring it from R2 if needed. None = unrecoverable."""
    for d in _candidate_dirs(tpl):
        if (d / MASTER_NAME).is_file():
            return d

    files = stored_files(tpl)
    svc = _storage()
    if not files or svc is None or MASTER_NAME not in files:
        return None

    dest = local_dir_for(tpl.id)
    with _lock_for(tpl.id):
        if (dest / MASTER_NAME).is_file():  # another request restored it while we waited
            return dest
        tmp = dest.with_name(dest.name + ".restoring")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            for name in files:
                svc.download_to_file(r2_prefix(tpl.id) + name, tmp / name)
            shutil.rmtree(dest, ignore_errors=True)
            os.replace(tmp, dest)
        except Exception as e:
            logger.error("Could not restore template %s from R2: %s", tpl.id, e)
            shutil.rmtree(tmp, ignore_errors=True)
            return None
    logger.info("Restored template %s from R2 (%d files)", tpl.id, len(files))
    return dest


def master_path(tpl: Any) -> Optional[Path]:
    d = ensure_local_dir(tpl)
    return (d / MASTER_NAME) if d else None


def files_ok(tpl: Any) -> bool:
    """Cheap check (no download): can this template still be used?"""
    if any((d / MASTER_NAME).is_file() for d in _candidate_dirs(tpl)):
        return True
    return bool(stored_files(tpl)) and MASTER_NAME in stored_files(tpl) and _storage() is not None


def delete_template_files(tpl: Any) -> None:
    for d in {*_candidate_dirs(tpl)}:
        if d.is_dir() and d.name == tpl.id:  # never remove a folder that is not this template's own
            shutil.rmtree(d, ignore_errors=True)
    svc = _storage()
    if svc is not None:
        try:
            svc.delete_prefix(r2_prefix(tpl.id))
        except Exception as e:
            logger.warning("Could not delete R2 files for template %s: %s", tpl.id, e)
