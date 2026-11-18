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
from typing import Any, Dict, List, Literal, Optional

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models.document import Document
from backend.app.models.sharepoint import SharePointFile
from backend.app.services.storage import r2_storage

logger = logging.getLogger(__name__)

SUPPORTED_EXT = {".pptx", ".pdf"}
MAX_PLAN_ITEMS = int(os.getenv("SHAREPOINT_MAX_PLAN_ITEMS", "2000"))
MAX_DEPTH = int(os.getenv("SHAREPOINT_MAX_DEPTH", "8"))
MAX_FILE_BYTES = int(float(os.getenv("SHAREPOINT_MAX_FILE_MB", "600")) * 1024 * 1024)
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

async def scope_root(client, drive_id: str) -> Optional[Dict[str, str]]:
    """When SHAREPOINT_ROOT_FOLDER is set (e.g. "Quizzes"), the folder everything must stay inside; else None."""
    folder = (getattr(settings, "SHAREPOINT_ROOT_FOLDER", None) or "").strip().strip("/")
    if not folder:
        return None
    cache = client.__dict__.setdefault("_scope_cache", {})
    key = (drive_id, folder)
    if key in cache:
        return cache[key]
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
    return root


def within_scope(item: Dict[str, Any], root: Optional[Dict[str, str]]) -> bool:
    if root is None or item.get("id") == root["id"]:
        return True
    p = (item.get("parentReference") or {}).get("path") or ""
    return p == root["path"] or p.startswith(root["path"] + "/")


async def effective_folder(client, drive_id: str, folder_id: Optional[str]) -> Optional[str]:
    """The folder id to list. With a folder limit, the library root means the limited folder, and any other folder must be inside it."""
    root = await scope_root(client, drive_id)
    if root is None:
        return folder_id
    if not folder_id or folder_id == "root" or folder_id == root["id"]:
        return root["id"]
    item = await client.get_item(drive_id, folder_id)
    if not within_scope(item, root):
        raise ValueError("That folder is outside the folder QShala is allowed to read")
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
    """Add 'status' (and 'match_document_id' for possible duplicates) to each candidate, in place."""
    ids = [c["id"] for c in cands]
    tracked: Dict[str, SharePointFile] = {}
    for part in _chunks(ids):
        for row in db.query(SharePointFile).filter(SharePointFile.id.in_(part)).all():
            tracked[row.id] = row

    names = sorted({c["name"].lower() for c in cands})
    docs_by_name: Dict[str, List[Document]] = {}
    for part in _chunks(names):
        for d in db.query(Document).filter(func.lower(Document.filename).in_(part)).all():
            docs_by_name.setdefault((d.filename or "").lower(), []).append(d)

    # the deck a previous import created (newest first), so a failed or deleted import can be done again
    latest_doc: Dict[str, Document] = {}
    for part in _chunks(ids):
        for d in (db.query(Document).filter(Document.sharepoint_file_id.in_(part))
                  .order_by(Document.uploaded_at.asc()).all()):
            latest_doc[d.sharepoint_file_id] = d

    for c in cands:
        t = tracked.get(c["id"])
        c["match_document_id"] = None
        if t is not None:
            same = (t.etag == c["etag"])
            if t.sync_status in ("SELECTED", "TRANSFERRING") and same:
                c["status"] = "IN_PROGRESS"
                continue
            if t.sync_status in ("QUEUED", "COMPLETED"):
                doc = latest_doc.get(c["id"])
                if doc is None:
                    c["status"] = "NEW"                      # its deck was deleted: import it again
                elif doc.processing_status == "FAILED":
                    c["status"] = "RETRY"
                else:
                    c["status"] = "ALREADY_IMPORTED" if same else "CHANGED"
                continue
            if t.sync_status == "SKIPPED_DUPLICATE":
                c["status"] = "ALREADY_IMPORTED" if same else "CHANGED"
                continue
            if t.sync_status == "FAILED":
                c["status"] = "RETRY"
                continue
        # never seen by the importer: was the same deck uploaded by hand (or by an earlier run)?
        match = next((d for d in docs_by_name.get(c["name"].lower(), [])
                      if int(d.file_size_bytes or 0) == c["size"] and d.sharepoint_file_id != c["id"]), None)
        if match is not None and t is None:
            c["status"] = "POSSIBLE_DUPLICATE"
            c["match_document_id"] = match.id
        else:
            c["status"] = "NEW"


def _wanted(c: Dict[str, Any], sel: Selection) -> bool:
    if c["status"] in _WANTED_ALWAYS:
        return True
    return c["status"] == "POSSIBLE_DUPLICATE" and sel.include_possible_duplicates


# ------------------------------------------------------------------------------------------------ planning

async def collect_candidates(client, sel: Selection) -> Dict[str, Any]:
    """Expand the chosen files/folders into supported, filtered, de-duplicated candidate files."""
    found: Dict[str, Dict[str, Any]] = {}
    state = {"truncated": False}
    excluded = set(sel.exclude_ids)
    root = await scope_root(client, sel.drive_id)

    def add(item: Dict[str, Any]) -> None:
        if "file" not in item or not item.get("id"):
            return
        c = candidate_from_item(item, sel.drive_id)
        if c["id"] in excluded or not _supported(c["name"]) or not _passes(c, sel.filters):
            return
        found.setdefault(c["id"], c)

    async def walk(folder_id: Optional[str], depth: int) -> None:
        nxt: Optional[str] = None
        while True:
            page = await client.list_children(sel.drive_id, folder_id, next_url=nxt)
            for it in page["items"]:
                if "folder" in it:
                    if sel.recursive and depth < MAX_DEPTH and it.get("id"):
                        await walk(it["id"], depth + 1)
                else:
                    add(it)
                if len(found) >= MAX_PLAN_ITEMS:
                    state["truncated"] = True
                    return
                if state["truncated"]:
                    return
            nxt = page["next"]
            if not nxt:
                return

    for ref in sel.items:
        if state["truncated"]:
            break
        if ref.kind == "folder":
            await walk(await effective_folder(client, sel.drive_id, None if ref.id == "root" else ref.id), 0)
        else:
            item = await client.get_item(sel.drive_id, ref.id)
            if not within_scope(item, root):
                raise ValueError("That file is outside the folder QShala is allowed to read")
            add(item)
    return {"candidates": list(found.values()), "truncated": state["truncated"]}


def _summarise(cands: List[Dict[str, Any]], sel: Selection, truncated: bool) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    for c in cands:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    wanted = [c for c in cands if _wanted(c, sel)]
    ordered = sorted(cands, key=lambda c: (0 if _wanted(c, sel) else 1, c["folder_path"] or "", c["name"].lower()))
    return {
        "total_found": len(cands), "truncated": truncated, "counts": counts,
        "to_import": len(wanted), "bytes_to_import": sum(c["size"] for c in wanted),
        "files": [{
            "id": c["id"], "name": c["name"], "size": c["size"], "folder_path": c["folder_path"],
            "modified": c["modified"].isoformat() if c.get("modified") else None,
            "status": c["status"], "will_import": _wanted(c, sel), "match_document_id": c.get("match_document_id"),
        } for c in ordered[:PLAN_FILE_LIST_CAP]],
    }


async def plan_import(db: Session, client, sel: Selection) -> Dict[str, Any]:
    got = await collect_candidates(client, sel)
    classify(db, got["candidates"])
    return _summarise(got["candidates"], sel, got["truncated"])


async def stage_import(db: Session, client, sel: Selection) -> Dict[str, Any]:
    """Mark the wanted files SELECTED under one new batch id. Downloads nothing."""
    got = await collect_candidates(client, sel)
    cands = got["candidates"]
    classify(db, cands)
    batch_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    staged = 0
    for c in cands:
        if not _wanted(c, sel):
            continue
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
        staged += 1
    db.commit()
    summary = _summarise(cands, sel, got["truncated"])
    return {"batch_id": batch_id, "selected": staged, "counts": summary["counts"], "truncated": got["truncated"],
            "bytes": summary["bytes_to_import"]}


# ------------------------------------------------------------------------------------------------ transfer

async def _hand_off_to_pipeline(db: Session, row: SharePointFile, data: bytes, sha: str) -> Dict[str, Optional[str]]:
    """Hand a downloaded deck to the SAME ingestion path a manual upload uses: a Document row, the raw deck staged in R2 where
    the Cloud Run worker fetches it (raw-uploads/<document id><ext>), and a queued upload job. Progress then shows on the
    Uploads page like any other deck. R2 trouble raises, so the file is marked FAILED, never saved where the worker cannot see it."""
    from backend.app.services.ingestion import upload_queue
    ext = Path(row.name or "").suffix.lower()
    doc_id = str(uuid.uuid4())
    storage_path = str(settings.UPLOAD_DIR / f"{doc_id}{ext}")
    cloud_worker = settings.INGESTION_MODE == "cloud_run_job"

    raw_url: Optional[str] = None
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
        year=datetime.now(timezone.utc).year, file_type=ext.lstrip("."), storage_path=storage_path, blob_url=raw_url,
        source="SHAREPOINT", sharepoint_file_id=row.id, file_size_bytes=len(data), file_sha256=sha,
        processing_status="PROCESSING",
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


async def transfer_next(db: Session, client, limit: int = 3, batch_id: Optional[str] = None) -> Dict[str, Any]:
    """Transfer up to `limit` SELECTED files. Safe to call from several tabs at once: each file is claimed atomically."""
    limit = max(1, min(int(limit), 10))
    now = datetime.now(timezone.utc)
    # a worker that died mid-download leaves TRANSFERRING behind; give those files back
    db.query(SharePointFile).filter(
        SharePointFile.sync_status == "TRANSFERRING", SharePointFile.updated_at < now - STALE_TRANSFER
    ).update({"sync_status": "SELECTED"}, synchronize_session=False)
    db.commit()

    q = db.query(SharePointFile).filter(SharePointFile.sync_status == "SELECTED")
    if batch_id:
        q = q.filter(SharePointFile.batch_id == batch_id)
    rows = q.order_by(SharePointFile.selected_at, SharePointFile.id).limit(limit).all()

    out = {"queued": 0, "skipped_duplicate": 0, "failed": 0, "job_ids": [], "document_ids": []}
    for row in rows:
        claimed = db.query(SharePointFile).filter(
            SharePointFile.id == row.id, SharePointFile.sync_status == "SELECTED"
        ).update({"sync_status": "TRANSFERRING", "updated_at": datetime.now(timezone.utc)}, synchronize_session=False)
        db.commit()
        if not claimed:
            continue
        db.refresh(row)
        try:
            data = await client.download_item_capped(row.drive_id, row.id, MAX_FILE_BYTES)
            sha = hashlib.sha256(data).hexdigest()
            row.file_sha256 = sha
            twin = db.query(Document).filter(Document.file_sha256 == sha).first()
            if twin is not None:
                row.sync_status = "SKIPPED_DUPLICATE"
                row.match_document_id = twin.id
                row.error_message = f"Identical file already in QShala as “{twin.title or twin.filename}”"
                out["skipped_duplicate"] += 1
            else:
                handoff = await _hand_off_to_pipeline(db, row, data, sha)
                row.blob_url = handoff["raw_url"]
                row.sync_status = "QUEUED"
                row.error_message = None
                out["job_ids"].append(handoff["job_id"])
                out["document_ids"].append(handoff["document_id"])
                out["queued"] += 1
            db.commit()
        except httpx.HTTPStatusError as e:
            _fail(db, row, _http_message(e))
            out["failed"] += 1
        except Exception as e:  # one bad file never stops the batch
            logger.warning("SharePoint transfer failed for %s: %s", row.name, e)
            _fail(db, row, str(e) or type(e).__name__)
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
