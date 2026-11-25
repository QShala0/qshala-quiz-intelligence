"""Selective SharePoint importer: browse -> choose -> preview -> import, on top of SharePointClient.

Flow
  plan_import()      expand the chosen files/folders, classify each file (NEW, CHANGED, RETRY, ALREADY_IMPORTED,
                     IN_PROGRESS, POSSIBLE_DUPLICATE) and report counts. Nothing is written.
  stage_import()     same, then mark the wanted files SELECTED (one batch). Nothing is downloaded yet.
  transfer_next()    take a few SELECTED files, download them (size-capped), skip byte-identical decks that are already in
                     QShala, and hand the rest to the normal upload pipeline (a Document, the raw deck in R2, a queued upload job).
                     Short, repeatable calls: the browser (or a scheduler) calls it until nothing remains, so no single web
                     request has to move a whole library and a crash never loses the queue.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models.document import Document
from backend.app.models.sharepoint import SharePointFile, SharePointAllowedFolder
from backend.app.services.deck_names import normalize_deck_name, existing_deck_names, find_deck_in_index
from backend.app.services.storage import r2_storage

logger = logging.getLogger(__name__)

SUPPORTED_EXT = {".pptx", ".pdf"}
MAX_PLAN_ITEMS = int(os.getenv("SHAREPOINT_MAX_PLAN_ITEMS", "2000"))
MAX_DEPTH = int(os.getenv("SHAREPOINT_MAX_DEPTH", "8"))
WALK_CONCURRENCY = max(1, int(os.getenv("SHAREPOINT_WALK_CONCURRENCY", "8")))   # folders listed at the same time
MAX_FILE_BYTES = int(float(os.getenv("SHAREPOINT_MAX_FILE_MB", "1024")) * 1024 * 1024)
SHAREPOINT_MAX_INFLIGHT = int(os.getenv("SHAREPOINT_MAX_INFLIGHT", getattr(settings, "SHAREPOINT_MAX_INFLIGHT", 4)))
SHAREPOINT_BATCH_MAX = int(os.getenv("SHAREPOINT_BATCH_MAX", getattr(settings, "SHAREPOINT_BATCH_MAX", 25)))

class _LoopBoundSemaphore:
    """Module-level semaphore proxy ensuring each asyncio event loop gets its own Semaphore instance,
    avoiding 'bound to a different event loop' errors during testing or across separate event loops."""
    def __init__(self, default_value: int = 4):
        self.default_value = default_value
        self._sems: Dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}

    def _get_sem(self) -> asyncio.Semaphore:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.Semaphore(self.default_value)
        if loop not in self._sems:
            limit = int(os.getenv("SHAREPOINT_MAX_INFLIGHT", getattr(settings, "SHAREPOINT_MAX_INFLIGHT", self.default_value)))
            self._sems[loop] = asyncio.Semaphore(limit)
        return self._sems[loop]

    async def __aenter__(self):
        return await self._get_sem().__aenter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return await self._get_sem().__aexit__(exc_type, exc_val, exc_tb)

    def locked(self) -> bool:
        return self._get_sem().locked()

SHAREPOINT_STREAM_SEMAPHORE = _LoopBoundSemaphore(SHAREPOINT_MAX_INFLIGHT)
STALE_TRANSFER = timedelta(minutes=10)
PLAN_FILE_LIST_CAP = 500
_IN_CHUNK = 400   # stay under SQLite's bound-variable limit

_DONE = ("QUEUED", "COMPLETED", "SKIPPED_DUPLICATE")
_WANTED_ALWAYS = ("NEW", "CHANGED", "RETRY")


class ItemRef(BaseModel):
    id: str
    kind: Literal["file", "folder"]


class Filters(BaseModel):
    name_contains: Optional[str] = None
    folder_contains: Optional[str] = None
    modified_after: Optional[datetime] = None
    modified_before: Optional[datetime] = None


class Selection(BaseModel):
    drive_id: str
    items: List[ItemRef] = Field(min_length=1)
    recursive: bool = True
    filters: Filters = Field(default_factory=Filters)
    include_possible_duplicates: bool = False
    exclude_ids: List[str] = Field(default_factory=list)


# ------------------------------------------------------------------------------------------------ folder limit

async def scope_roots(client, drive_id: str, db: Optional[Session] = None) -> List[Dict[str, str]]:
    """When custom allowed folders are saved in the database for this drive, returns those folders.
    Otherwise falls back to SHAREPOINT_ROOT_FOLDERS or SHAREPOINT_ROOT_FOLDER env vars; else empty list."""
    if db is not None:
        try:
            records = db.query(SharePointAllowedFolder).filter(
                SharePointAllowedFolder.drive_id == drive_id,
                SharePointAllowedFolder.is_active == True,
            ).all()
            if records:
                results = []
                for rec in records:
                    results.append({
                        "id": rec.folder_id,
                        "path": rec.folder_path,
                        "name": rec.display_name or rec.folder_name,
                    })
                return sorted(results, key=lambda r: (r["name"] or "").lower())
        except Exception as e:
            logger.warning(f"Failed to query sharepoint_allowed_folders from database: {e}")

    raw = (getattr(settings, "SHAREPOINT_ROOT_FOLDERS", None)
           or getattr(settings, "SHAREPOINT_ROOT_FOLDER", None)
           or "").strip()
    if not raw:
        return []
    folders = [f.strip().strip("/") for f in raw.split(",") if f.strip().strip("/")]
    if not folders:
        return []
    cache = client.__dict__.setdefault("_scope_cache", {})
    results = []
    for folder in folders:
        key = (drive_id, folder)
        if key in cache:
            results.append(cache[key])
            continue
        try:
            item = await client.get_item_by_path(drive_id, folder)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise ValueError(f"The SharePoint folder “{folder}” set for QShala was not found in this library")
            raise
        if "folder" not in item:
            raise ValueError(f"“{folder}” is not a folder")
        parent = (item.get("parentReference") or {}).get("path") or ""
        root = {"id": item["id"], "path": f"{parent}/{item.get('name')}", "name": item.get("name") or folder}
        cache[key] = root
        results.append(root)
    return results


async def scope_root(client, drive_id: str, db: Optional[Session] = None) -> Optional[Dict[str, str]]:
    """Single-root compatibility helper. Returns the root when exactly 1 is set, else None."""
    roots = await scope_roots(client, drive_id, db=db)
    return roots[0] if len(roots) == 1 else None


def _normalize_path(p: Optional[str]) -> str:
    if not p:
        return ""
    p_str = str(p).strip().replace("\\", "/")
    if "root:" in p_str:
        p_str = p_str.split("root:", 1)[1]
    return p_str.strip("/")


def within_scope(item: Dict[str, Any], root: Union[Optional[Dict[str, str]], List[Dict[str, str]]]) -> bool:
    if not root:
        return True
    roots = [root] if isinstance(root, dict) else root
    if not roots:
        return True
    item_id = item.get("id")
    parent_ref = item.get("parentReference") or {}
    parent_id = parent_ref.get("id")
    raw_parent_path = parent_ref.get("path") or ""
    parent_rel = _normalize_path(raw_parent_path)
    item_name = item.get("name") or ""
    item_rel = f"{parent_rel}/{item_name}".strip("/") if parent_rel else item_name

    for r in roots:
        r_id = r.get("id")
        if item_id and item_id == r_id:
            return True
        if parent_id and parent_id == r_id:
            return True

        r_path_rel = _normalize_path(r.get("path") or r.get("name"))
        if not r_path_rel:
            continue

        if parent_rel == r_path_rel or parent_rel.startswith(r_path_rel + "/"):
            return True

        if item_rel == r_path_rel or item_rel.startswith(r_path_rel + "/"):
            return True

    return False


async def effective_folder(client, drive_id: str, folder_id: Optional[str], db: Optional[Session] = None) -> Optional[str]:
    """The folder id to list.
    - With no folder limit, returns folder_id.
    - With 1 folder limit, library root ('root' or None) resolves to that folder's id.
    - With multiple folder limits, library root ('root' or None) returns None (virtual root handled by caller),
      and any requested folder_id must be inside one of the allowed roots.
    """
    roots = await scope_roots(client, drive_id, db=db)
    if not roots:
        return folder_id

    if len(roots) == 1:
        single = roots[0]
        if not folder_id or folder_id == "root" or folder_id == single["id"]:
            return single["id"]
        item = await client.get_item(drive_id, folder_id)
        if not within_scope(item, [single]):
            raise ValueError("That folder is outside the folder QShala is allowed to read")
        return folder_id

    # Multiple roots
    if not folder_id or folder_id == "root":
        return None
    if any(r["id"] == folder_id for r in roots):
        return folder_id
    item = await client.get_item(drive_id, folder_id)
    if not within_scope(item, roots):
        raise ValueError("That folder is outside the folders QShala is allowed to read")
    return folder_id


# ------------------------------------------------------------------------------------------------ helpers

def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _folder_path(item: Dict[str, Any]) -> str:
    raw = ((item.get("parentReference") or {}).get("path")) or ""
    return raw.split("root:", 1)[1].lstrip("/") if "root:" in raw else ""


def candidate_from_item(item: Dict[str, Any], drive_id: str) -> Dict[str, Any]:
    f = item.get("file") or {}
    return {
        "id": item["id"], "name": item.get("name") or "", "size": int(item.get("size") or 0),
        "etag": item.get("eTag"), "c_tag": item.get("cTag"), "modified": _parse_dt(item.get("lastModifiedDateTime")),
        "web_url": item.get("webUrl"), "folder_path": _folder_path(item), "drive_id": drive_id,
        "quick_xor": (f.get("hashes") or {}).get("quickXorHash"),
    }


def _supported(name: str) -> bool:
    n = name.strip()
    return bool(n) and not n.startswith("~$") and not n.startswith(".") and Path(n).suffix.lower() in SUPPORTED_EXT


def _passes(c: Dict[str, Any], flt: Filters) -> bool:
    if flt.name_contains and flt.name_contains.lower() not in c["name"].lower():
        return False
    if flt.folder_contains and flt.folder_contains.lower() not in (c["folder_path"] or "").lower():
        return False
    mod = c.get("modified")
    if flt.modified_after and mod and mod < _utc(flt.modified_after):
        return False
    if flt.modified_before and mod and mod > _utc(flt.modified_before):
        return False
    return True


def _chunks(seq: List[Any], n: int = _IN_CHUNK):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ------------------------------------------------------------------------------------------------ classification

def classify(db: Session, cands: List[Dict[str, Any]]) -> None:
    """Add 'status' (and 'match_document_id' for duplicate decks) to each candidate, in place."""
    ids = [c["id"] for c in cands]
    tracked: Dict[str, SharePointFile] = {}
    for part in _chunks(ids):
        for row in db.query(SharePointFile).filter(SharePointFile.id.in_(part)).all():
            tracked[row.id] = row

    # Active documents index for normalised name matching (excluding FAILED / CANCELLED)
    deck_index = existing_deck_names(db)

    # the deck a previous import created (newest first), so a failed or deleted import can be done again
    latest_doc: Dict[str, Document] = {}
    for part in _chunks(ids):
        for d in (db.query(Document).filter(Document.sharepoint_file_id.in_(part))
                  .order_by(Document.uploaded_at.asc()).all()):
            latest_doc[d.sharepoint_file_id] = d

    for c in cands:
        t = tracked.get(c["id"])
        c["match_document_id"] = None
        existing = find_deck_in_index(deck_index, c["name"])
        # Name match against existing decks, excluding the deck this same SharePoint file id created earlier
        name_match = existing if (existing and existing.sharepoint_file_id != c["id"]) else None

        if t is not None:
            same = (t.etag == c["etag"])
            if t.sync_status in ("SELECTED", "TRANSFERRING") and same:
                c["status"] = "IN_PROGRESS"
                continue
            if t.sync_status in ("QUEUED", "COMPLETED"):
                doc = latest_doc.get(c["id"])
                if doc is None:
                    if name_match is not None:
                        c["status"] = "ALREADY_IMPORTED"
                        c["match_document_id"] = name_match.id
                    else:
                        c["status"] = "NEW"                      # its deck was deleted: import it again
                elif doc.processing_status in ("FAILED", "CANCELLED"):
                    c["status"] = "RETRY"
                else:
                    c["status"] = "ALREADY_IMPORTED" if same else "CHANGED"
                    if c["status"] == "ALREADY_IMPORTED":
                        c["match_document_id"] = doc.id
                continue
            if t.sync_status == "SKIPPED_DUPLICATE":
                if same:
                    c["status"] = "ALREADY_IMPORTED"
                    c["match_document_id"] = t.match_document_id or (name_match.id if name_match else None)
                else:
                    c["status"] = "CHANGED"
                continue
            if t.sync_status == "FAILED":
                c["status"] = "RETRY"
                continue

        # never seen by the importer or new candidate:
        if name_match is not None:
            c["status"] = "ALREADY_IMPORTED"
            c["match_document_id"] = name_match.id
        else:
            c["status"] = "NEW"


def _wanted(c: Dict[str, Any], sel: Selection) -> bool:
    if c["status"] in _WANTED_ALWAYS:
        return True
    return c["status"] == "POSSIBLE_DUPLICATE" and sel.include_possible_duplicates


# ------------------------------------------------------------------------------------------------ planning

async def collect_candidates(client, sel: Selection, db: Optional[Session] = None) -> Dict[str, Any]:
    """Expand the chosen files/folders into supported, filtered, de-duplicated candidate files."""
    found: Dict[str, Dict[str, Any]] = {}
    state = {"truncated": False}
    excluded = set(sel.exclude_ids)
    roots = await scope_roots(client, sel.drive_id, db=db)

    def add(item: Dict[str, Any]) -> None:
        if "file" not in item or not item.get("id"):
            return
        c = candidate_from_item(item, sel.drive_id)
        if c["id"] in excluded or not _supported(c["name"]) or not _passes(c, sel.filters):
            return
        found.setdefault(c["id"], c)

    # Sibling folders are listed at the same time (a few Graph calls in flight), not one after another: walking a
    # whole library one folder at a time took minutes, which the browser gave up on (a 502) before it finished.
    gate = asyncio.Semaphore(WALK_CONCURRENCY)

    async def walk(folder_id: Optional[str], depth: int) -> None:
        subfolders: List[str] = []
        nxt: Optional[str] = None
        while True:
            async with gate:
                page = await client.list_children(sel.drive_id, folder_id, next_url=nxt)
            for it in page["items"]:
                if "folder" in it:
                    if sel.recursive and depth < MAX_DEPTH and it.get("id"):
                        subfolders.append(it["id"])
                else:
                    add(it)
                if len(found) >= MAX_PLAN_ITEMS:
                    state["truncated"] = True
                    return
                if state["truncated"]:
                    return
            nxt = page["next"]
            if not nxt:
                break
        if subfolders and not state["truncated"]:
            await asyncio.gather(*(walk(s, depth + 1) for s in subfolders))

    for ref in sel.items:
        if state["truncated"]:
            break
        if ref.kind == "folder":
            if (not ref.id or ref.id == "root") and len(roots) > 1:
                await asyncio.gather(*(walk(r["id"], 0) for r in roots))
            else:
                eff = await effective_folder(client, sel.drive_id, None if ref.id == "root" else ref.id, db=db)
                await walk(eff, 0)
        else:
            item = await client.get_item(sel.drive_id, ref.id)
            if not within_scope(item, roots):
                raise ValueError("That file is outside the folder QShala is allowed to read")
            add(item)
    return {"candidates": list(found.values()), "truncated": state["truncated"]}


def _summarise(cands: List[Dict[str, Any]], sel: Selection, truncated: bool) -> Dict[str, Any]:
    batch_max = int(os.getenv("SHAREPOINT_BATCH_MAX", getattr(settings, "SHAREPOINT_BATCH_MAX", 25)))
    counts: Dict[str, int] = {}
    for c in cands:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    wanted = [c for c in cands if _wanted(c, sel)]
    leftover = max(0, len(wanted) - batch_max)
    ordered = sorted(cands, key=lambda c: (0 if _wanted(c, sel) else 1, c["folder_path"] or "", c["name"].lower()))
    to_import_cands = wanted[:batch_max] if leftover > 0 else wanted
    msg = f"{len(to_import_cands)} of {len(wanted)} selected, import the rest in the next batch" if leftover > 0 else None
    return {
        "total_found": len(cands),
        "truncated": truncated or (leftover > 0),
        "counts": counts,
        "to_import": len(to_import_cands),
        "bytes_to_import": sum(c["size"] for c in to_import_cands),
        "batch_capped": leftover > 0,
        "leftover": leftover,
        "message": msg,
        "files": [{
            "id": c["id"], "name": c["name"], "size": c["size"], "folder_path": c["folder_path"],
            "modified": c["modified"].isoformat() if c.get("modified") else None,
            "status": c["status"], "will_import": _wanted(c, sel), "match_document_id": c.get("match_document_id"),
        } for c in ordered[:PLAN_FILE_LIST_CAP]],
    }


async def plan_import(db: Session, client, sel: Selection) -> Dict[str, Any]:
    got = await collect_candidates(client, sel, db=db)
    classify(db, got["candidates"])
    return _summarise(got["candidates"], sel, got["truncated"])


async def stage_import(
    db: Session,
    client,
    sel: Selection,
    importer_user_id: Optional[str] = None,
    importer_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Mark the wanted files SELECTED under one new batch id. Downloads nothing."""
    batch_max = int(os.getenv("SHAREPOINT_BATCH_MAX", getattr(settings, "SHAREPOINT_BATCH_MAX", 25)))
    got = await collect_candidates(client, sel, db=db)
    cands = got["candidates"]
    classify(db, cands)
    batch_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    staged = 0
    wanted = [c for c in cands if _wanted(c, sel)]
    leftover = max(0, len(wanted) - batch_max)
    to_stage = wanted[:batch_max] if leftover > 0 else wanted
    for c in to_stage:
        row = db.query(SharePointFile).filter(SharePointFile.id == c["id"]).first()
        if row is None:
            row = SharePointFile(id=c["id"], name=c["name"])
            db.add(row)
        row.name = c["name"]
        row.web_url = c["web_url"]
        row.etag = c["etag"]
        row.c_tag = c["c_tag"]
        row.file_size_bytes = c["size"]
        row.last_modified_date_time = c["modified"]
        row.drive_id = c["drive_id"]
        row.folder_path = c["folder_path"]
        row.quick_xor_hash = c["quick_xor"]
        row.match_document_id = c.get("match_document_id")
        row.sync_status = "SELECTED"
        row.error_message = None
        row.batch_id = batch_id
        row.selected_at = now
        row.importer_user_id = importer_user_id
        row.importer_name = importer_name
        staged += 1
    db.commit()
    summary = _summarise(cands, sel, got["truncated"])
    msg = f"{staged} of {len(wanted)} selected, import the rest in the next batch" if leftover > 0 else None
    return {
        "batch_id": batch_id,
        "selected": staged,
        "counts": summary["counts"],
        "truncated": got["truncated"] or (leftover > 0),
        "bytes": summary["bytes_to_import"],
        "batch_capped": leftover > 0,
        "leftover": leftover,
        "message": msg,
    }


# ------------------------------------------------------------------------------------------------ transfer

async def _hand_off_to_pipeline(
    db: Session,
    row: SharePointFile,
    data: Optional[bytes] = None,
    sha: Optional[str] = None,
    *,
    pre_staged: Optional[Tuple[str, Optional[str], int, str]] = None,
    fallback_user_id: Optional[str] = None,
    fallback_user_name: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """Hand a downloaded deck to the SAME ingestion path a manual upload uses: a Document row, the raw deck staged in R2 where
    the Cloud Run worker fetches it (raw-uploads/<document id><ext>), and a queued upload job. Progress then shows on the
    Uploads page like any other deck. R2 trouble raises, so the file is marked FAILED, never saved where the worker cannot see it."""
    from backend.app.services.ingestion import upload_queue
    ext = Path(row.name or "").suffix.lower()

    if pre_staged is not None:
        doc_id, raw_url, size_bytes, file_sha = pre_staged
        storage_path = str(settings.UPLOAD_DIR / f"{doc_id}{ext}")
    else:
        if data is None or sha is None:
            raise ValueError("Either data+sha or pre_staged must be provided to _hand_off_to_pipeline")
        doc_id = str(uuid.uuid4())
        storage_path = str(settings.UPLOAD_DIR / f"{doc_id}{ext}")
        cloud_worker = settings.INGESTION_MODE == "cloud_run_job"
        size_bytes = len(data)
        file_sha = sha

        raw_url = None
        r2 = r2_storage.get_media_storage()
        if r2 is not None:
            raw_url = await asyncio.to_thread(r2.upload_bytes, f"raw-uploads/{doc_id}{ext}", data)
        elif cloud_worker:
            raise RuntimeError("R2 storage must be configured so the ingestion worker can read the deck")
        if not cloud_worker:                       # in-process ingestion reads the deck from local disk
            Path(storage_path).parent.mkdir(parents=True, exist_ok=True)
            Path(storage_path).write_bytes(data)

    doc = Document(
        id=doc_id, filename=row.name, title=Path(row.name).stem.replace("_", " ").title(),
        year=None, file_type=ext.lstrip("."), storage_path=storage_path, blob_url=raw_url,       # the deck's own year comes from its name; the import year is not it
        source="SHAREPOINT", sharepoint_file_id=row.id, file_size_bytes=size_bytes, file_sha256=file_sha,
        processing_status="PROCESSING",
        uploaded_by_user_id=row.importer_user_id if row.importer_user_id is not None else fallback_user_id,
        uploaded_by_name=row.importer_name if row.importer_name is not None else fallback_user_name,
    )
    db.add(doc)
    db.commit()
    job = upload_queue.enqueue_upload(db, document_id=doc_id, filename=row.name)
    return {"document_id": doc_id, "job_id": job.id, "raw_url": raw_url}


def _remaining(db: Session, batch_id: Optional[str]) -> int:
    q = db.query(func.count(SharePointFile.id)).filter(SharePointFile.sync_status.in_(["SELECTED", "TRANSFERRING"]))
    if batch_id:
        q = q.filter(SharePointFile.batch_id == batch_id)
    return int(q.scalar() or 0)


async def transfer_next(
    db: Session,
    client,
    limit: int = 3,
    batch_id: Optional[str] = None,
    fallback_user_id: Optional[str] = None,
    fallback_user_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Transfer up to `limit` SELECTED files concurrently (bounded by SHAREPOINT_MAX_INFLIGHT). Safe to call from several tabs at once."""
    batch_max = int(os.getenv("SHAREPOINT_BATCH_MAX", getattr(settings, "SHAREPOINT_BATCH_MAX", 25)))
    limit = max(1, min(int(limit), batch_max))
    now = datetime.now(timezone.utc)
    # a worker that died mid-download leaves TRANSFERRING behind; give those files back
    db.query(SharePointFile).filter(
        SharePointFile.sync_status == "TRANSFERRING", SharePointFile.updated_at < now - STALE_TRANSFER
    ).update({"sync_status": "SELECTED"}, synchronize_session=False)
    db.commit()

    # Atomically claim up to `limit` rows so concurrent calls never claim the same row
    q = db.query(SharePointFile).filter(SharePointFile.sync_status == "SELECTED")
    if batch_id:
        q = q.filter(SharePointFile.batch_id == batch_id)
    candidates = q.order_by(SharePointFile.selected_at, SharePointFile.id).limit(limit * 2).all()

    claimed_rows: List[SharePointFile] = []
    for cand in candidates:
        if len(claimed_rows) >= limit:
            break
        claimed = db.query(SharePointFile).filter(
            SharePointFile.id == cand.id, SharePointFile.sync_status == "SELECTED"
        ).update({"sync_status": "TRANSFERRING", "updated_at": datetime.now(timezone.utc)}, synchronize_session=False)
        db.commit()
        if claimed:
            db.refresh(cand)
            claimed_rows.append(cand)

    if not claimed_rows:
        return {"queued": 0, "skipped_duplicate": 0, "failed": 0, "job_ids": [], "document_ids": [], "remaining": _remaining(db, batch_id)}

    db_lock = asyncio.Lock()
    deck_index = existing_deck_names(db)

    async def _transfer_one(row_id: str, row_drive_id: str, row_name: str) -> Dict[str, Any]:
        # Re-check the name right before downloading
        async with db_lock:
            existing = find_deck_in_index(deck_index, row_name)
            if existing is not None and existing.sharepoint_file_id != row_id:
                row = db.query(SharePointFile).filter_by(id=row_id).one()
                row.sync_status = "SKIPPED_DUPLICATE"
                row.match_document_id = existing.id
                row.error_message = f"A deck named {row_name} is already in QShala"
                db.commit()
                return {"status": "skipped_duplicate"}

        r2 = r2_storage.get_media_storage()
        cloud_worker = (settings.INGESTION_MODE == "cloud_run_job")
        r2_configured = (r2 is not None and getattr(r2, "is_configured", True))
        use_streaming = (cloud_worker and r2_configured)

        if use_streaming:
            doc_id = str(uuid.uuid4())
            ext = Path(row_name or "").suffix.lower()
            key = f"raw-uploads/{doc_id}{ext}"
            part_size = int(os.getenv("SHAREPOINT_PART_MB", getattr(settings, "SHAREPOINT_PART_MB", 32))) * 1024 * 1024
            staged_key: Optional[str] = None
            try:
                async with SHAREPOINT_STREAM_SEMAPHORE:
                    raw_url, sha, size_bytes = await client.stream_item_to_r2(
                        row_drive_id, row_id, key, MAX_FILE_BYTES, r2, part_size
                    )
                staged_key = key
                async with db_lock:
                    row = db.query(SharePointFile).filter_by(id=row_id).one()
                    row.file_sha256 = sha
                    twin = db.query(Document).filter(Document.file_sha256 == sha).first()
                    if twin is not None:
                        await asyncio.to_thread(r2.delete_object, key)
                        staged_key = None
                        row.sync_status = "SKIPPED_DUPLICATE"
                        row.match_document_id = twin.id
                        row.error_message = f"Identical file already in QShala as “{twin.title or twin.filename}”"
                        db.commit()
                        return {"status": "skipped_duplicate"}
                    else:
                        handoff = await _hand_off_to_pipeline(
                            db,
                            row,
                            pre_staged=(doc_id, raw_url, size_bytes, sha),
                            fallback_user_id=fallback_user_id,
                            fallback_user_name=fallback_user_name,
                        )
                        row.blob_url = handoff["raw_url"]
                        row.sync_status = "QUEUED"
                        row.error_message = None
                        db.commit()
                        return {
                            "status": "queued",
                            "job_id": handoff["job_id"],
                            "document_id": handoff["document_id"],
                        }
            except httpx.HTTPStatusError as e:
                if staged_key:
                    try:
                        await asyncio.to_thread(r2.delete_object, staged_key)
                    except Exception:
                        pass
                async with db_lock:
                    row = db.query(SharePointFile).filter_by(id=row_id).one()
                    _fail(db, row, _http_message(e))
                return {"status": "failed"}
            except Exception as e:
                logger.warning("SharePoint transfer failed for %s: %s", row_name, e)
                if staged_key:
                    try:
                        await asyncio.to_thread(r2.delete_object, staged_key)
                    except Exception:
                        pass
                async with db_lock:
                    db.rollback()
                    row = db.query(SharePointFile).filter_by(id=row_id).one()
                    _fail(db, row, str(e) or type(e).__name__)
                return {"status": "failed"}
        else:
            try:
                async with SHAREPOINT_STREAM_SEMAPHORE:
                    data = await client.download_item_capped(row_drive_id, row_id, MAX_FILE_BYTES)
                sha = hashlib.sha256(data).hexdigest()
                async with db_lock:
                    row = db.query(SharePointFile).filter_by(id=row_id).one()
                    row.file_sha256 = sha
                    twin = db.query(Document).filter(Document.file_sha256 == sha).first()
                    if twin is not None:
                        row.sync_status = "SKIPPED_DUPLICATE"
                        row.match_document_id = twin.id
                        row.error_message = f"Identical file already in QShala as “{twin.title or twin.filename}”"
                        db.commit()
                        return {"status": "skipped_duplicate"}
                    else:
                        handoff = await _hand_off_to_pipeline(
                            db,
                            row,
                            data=data,
                            sha=sha,
                            fallback_user_id=fallback_user_id,
                            fallback_user_name=fallback_user_name,
                        )
                        row.blob_url = handoff["raw_url"]
                        row.sync_status = "QUEUED"
                        row.error_message = None
                        db.commit()
                        return {
                            "status": "queued",
                            "job_id": handoff["job_id"],
                            "document_id": handoff["document_id"],
                        }
            except httpx.HTTPStatusError as e:
                async with db_lock:
                    db.rollback()
                    row = db.query(SharePointFile).filter_by(id=row_id).one()
                    _fail(db, row, _http_message(e))
                return {"status": "failed"}
            except Exception as e:
                logger.warning("SharePoint transfer failed for %s: %s", row_name, e)
                async with db_lock:
                    db.rollback()
                    row = db.query(SharePointFile).filter_by(id=row_id).one()
                    _fail(db, row, str(e) or type(e).__name__)
                return {"status": "failed"}

    tasks = [_transfer_one(r.id, r.drive_id, r.name) for r in claimed_rows]
    results = await asyncio.gather(*tasks)

    out = {"queued": 0, "skipped_duplicate": 0, "failed": 0, "job_ids": [], "document_ids": []}
    for res in results:
        if res.get("status") == "queued":
            out["queued"] += 1
            out["job_ids"].append(res["job_id"])
            out["document_ids"].append(res["document_id"])
        elif res.get("status") == "skipped_duplicate":
            out["skipped_duplicate"] += 1
        elif res.get("status") == "failed":
            out["failed"] += 1

    out["remaining"] = _remaining(db, batch_id)
    return out


def _http_message(e: httpx.HTTPStatusError) -> str:
    code = e.response.status_code
    if code in (401, 403):
        return "SharePoint refused access to this file (check the app's site permission)"
    if code == 404:
        return "File no longer exists in SharePoint"
    if code == 423:
        return "File is locked in SharePoint right now; retry later"
    return f"SharePoint returned HTTP {code}"


def _fail(db: Session, row: SharePointFile, message: str) -> None:
    row.sync_status = "FAILED"
    row.error_message = message[:500]
    db.commit()


def import_status(db: Session, batch_id: Optional[str] = None) -> Dict[str, Any]:
    q = db.query(SharePointFile.sync_status, func.count(SharePointFile.id)).filter(SharePointFile.batch_id.isnot(None))
    if batch_id:
        q = q.filter(SharePointFile.batch_id == batch_id)
    counts = {status: n for status, n in q.group_by(SharePointFile.sync_status).all()}
    fq = db.query(SharePointFile).filter(SharePointFile.sync_status == "FAILED")
    if batch_id:
        fq = fq.filter(SharePointFile.batch_id == batch_id)
    failed = [{"id": f.id, "name": f.name, "error": f.error_message} for f in fq.limit(20).all()]
    skipped_q = db.query(SharePointFile).filter(SharePointFile.sync_status == "SKIPPED_DUPLICATE")
    if batch_id:
        skipped_q = skipped_q.filter(SharePointFile.batch_id == batch_id)
    skipped = [{"id": f.id, "name": f.name, "message": f.error_message, "match_document_id": f.match_document_id}
               for f in skipped_q.limit(20).all()]
    return {"batch_id": batch_id, "counts": counts, "remaining": _remaining(db, batch_id), "failed": failed,
            "skipped_duplicates": skipped}
