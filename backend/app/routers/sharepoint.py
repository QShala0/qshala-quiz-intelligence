import logging
import hashlib
import hmac
import time
from typing import Optional, List, Dict, Any, Tuple
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel
from backend.app.database import get_db
from backend.app.models.sharepoint import SharePointSyncState, SharePointFile, SharePointAllowedFolder
from backend.app.models.job import IngestionJob
from backend.app.config import settings
from backend.app.services.sharepoint import importer
from backend.app.services.sharepoint.graph_client import get_sharepoint_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sharepoint", tags=["sharepoint"])

class SharePointSyncResponse(BaseModel):
    status: str
    pages_processed: int
    discovered_files_count: int
    enqueued_jobs_count: int
    deleted_files_count: int
    enqueued_job_ids: List[str]
    delta_token: Optional[str] = None

@router.post("/sync", response_model=SharePointSyncResponse)
async def trigger_sharepoint_sync(
    drive_id: Optional[str] = None,
    sync_state_id: str = "qshala_main_library",
    db: Session = Depends(get_db)
):
    """
    Triggers an incremental delta sync from Microsoft SharePoint document library.
    Discovers new/updated .pptx and .pdf decks, streams them to Vercel Blob,
    and enqueues background processing jobs.
    """
    if (settings.SHAREPOINT_ROOT_FOLDERS or settings.SHAREPOINT_ROOT_FOLDER or "").strip():
        raise HTTPException(status_code=400, detail="Whole-library sync is switched off while a SharePoint folder limit is set. "
                                                    "Use Import from SharePoint instead.")
    client = get_sharepoint_client()
    try:
        result = await client.sync_delta(db, drive_id=drive_id, sync_state_id=sync_state_id)
        return result
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.exception(f"SharePoint sync error: {e}")
        raise HTTPException(status_code=500, detail=f"SharePoint sync failed: {str(e)}")

@router.get("/status")
def get_sharepoint_sync_status(
    sync_state_id: str = "qshala_main_library",
    db: Session = Depends(get_db)
):
    """
    Returns current sync state, last sync timestamp, tracked file count, and health status.
    """
    state = db.query(SharePointSyncState).filter(SharePointSyncState.id == sync_state_id).first()
    if not state:
        return {
            "id": sync_state_id,
            "status": "NOT_CONFIGURED",
            "last_sync_at": None,
            "total_files_tracked": 0,
            "last_error": None
        }

    return {
        "id": state.id,
        "site_id": state.site_id,
        "drive_id": state.drive_id,
        "status": state.status,
        "last_sync_at": state.last_sync_at.isoformat() if state.last_sync_at else None,
        "total_files_tracked": state.total_files_tracked,
        "last_error": state.last_error
    }

@router.get("/files")
def list_sharepoint_files(
    sync_status: Optional[str] = Query(None, description="Filter by status: DISCOVERED, QUEUED, COMPLETED, FAILED, DELETED"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Lists files discovered in SharePoint along with their synchronization and ingestion status.
    """
    query = db.query(SharePointFile)
    if sync_status:
        query = query.filter(SharePointFile.sync_status == sync_status)

    total = query.count()
    items = query.order_by(SharePointFile.last_modified_date_time.desc().nullslast()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "files": [
            {
                "id": f.id,
                "name": f.name,
                "web_url": f.web_url,
                "file_size_bytes": f.file_size_bytes,
                "last_modified_date_time": f.last_modified_date_time.isoformat() if f.last_modified_date_time else None,
                "blob_url": f.blob_url,
                "sync_status": f.sync_status,
                "error_message": f.error_message
            }
            for f in items
        ]
    }

@router.post("/files/{file_id}/re-queue")
def requeue_sharepoint_file(
    file_id: str,
    db: Session = Depends(get_db)
):
    """
    Re-queues a failed or pending SharePoint file for ingestion.
    """
    sp_file = db.query(SharePointFile).filter(SharePointFile.id == file_id).first()
    if not sp_file:
        raise HTTPException(status_code=404, detail="SharePoint file not found")

    if not sp_file.blob_url:
        raise HTTPException(status_code=400, detail="File has no blob_url yet; run sync first")

    # Create new job
    job = IngestionJob(
        source_type="SHAREPOINT",
        source_file_id=sp_file.id,
        filename=sp_file.name,
        blob_url=sp_file.blob_url,
        status="PENDING",
        current_step="DOWNLOAD",
        step_data={"sharepoint_item_id": sp_file.id}
    )
    sp_file.sync_status = "QUEUED"
    sp_file.error_message = None
    db.add(job)
    db.commit()
    db.refresh(job)

    return {"message": "File re-queued successfully", "job_id": job.id}


# ------------------------------------------------------------------------------------------------ admin access control

ADMIN_TOKEN_LIFETIME = 14400  # 4 hours


def _get_admin_secret_key() -> bytes:
    pw = (settings.SHAREPOINT_ADMIN_PASSWORD or "qshala-admin").strip()
    secret = (getattr(settings, "API_KEY_SECRET", None) or "qshala-sp-salt").strip()
    return hashlib.sha256(f"{pw}:{secret}".encode()).digest()


def create_admin_token() -> str:
    exp = int(time.time()) + ADMIN_TOKEN_LIFETIME
    msg = f"sp_admin:{exp}".encode()
    sig = hmac.new(_get_admin_secret_key(), msg, hashlib.sha256).hexdigest()
    return f"{exp}:{sig}"


def verify_admin_token(token: Optional[str]) -> bool:
    if not token or ":" not in token:
        return False
    try:
        parts = token.split(":", 1)
        exp = int(parts[0])
        sig = parts[1]
        if time.time() > exp:
            return False
        expected = hmac.new(_get_admin_secret_key(), f"sp_admin:{exp}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False


def require_admin_auth(x_admin_token: Optional[str] = Header(None)) -> bool:
    if not verify_admin_token(x_admin_token):
        raise HTTPException(status_code=401, detail="Invalid or expired admin session. Please log in again.")
    return True


class AdminVerifyRequest(BaseModel):
    password: str


class AdminFolderRequest(BaseModel):
    drive_id: str
    folder_id: str
    folder_name: Optional[str] = None
    folder_path: Optional[str] = None
    display_name: Optional[str] = None
    is_active: bool = True


@router.post("/admin/verify")
def admin_verify(req: AdminVerifyRequest):
    expected_pw = (settings.SHAREPOINT_ADMIN_PASSWORD or "qshala-admin").strip()
    if not hmac.compare_digest(req.password.strip().encode(), expected_pw.encode()):
        raise HTTPException(status_code=401, detail="Incorrect admin password")
    token = create_admin_token()
    return {"success": True, "token": token, "expires_in": ADMIN_TOKEN_LIFETIME}


@router.get("/admin/browse")
async def admin_browse(
    drive_id: str,
    folder_id: Optional[str] = None,
    next_url: Optional[str] = None,
    _auth: bool = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    """Admin full-drive SharePoint browser without scope restrictions.
    Marks each folder with whether it is currently allowed for the team."""
    client = get_sharepoint_client()
    try:
        eff = None if not folder_id or folder_id == "root" else folder_id
        page = await client.list_children(drive_id, eff, next_url=next_url)
    except Exception as e:
        raise _graph_error(e)

    allowed_records = db.query(SharePointAllowedFolder).filter(
        SharePointAllowedFolder.drive_id == drive_id
    ).all()
    allowed_map = {rec.folder_id: rec for rec in allowed_records}

    folders, files = [], []
    for it in page.get("items", []):
        if "folder" in it:
            fid = it["id"]
            rec = allowed_map.get(fid)
            parent_ref = it.get("parentReference", {})
            raw_path = parent_ref.get("path", "")
            fpath = raw_path.split("root:", 1)[1].lstrip("/") if "root:" in raw_path else ""
            full_path = f"{fpath}/{it.get('name')}".strip("/") if fpath else (it.get("name") or "")

            folders.append({
                "id": fid,
                "name": it.get("name"),
                "path": full_path,
                "child_count": (it.get("folder") or {}).get("childCount"),
                "is_allowed": rec.is_active if rec else False,
                "allowed_id": rec.id if rec else None,
                "display_name": rec.display_name if rec else None,
            })
        elif "file" in it:
            files.append({
                "id": it["id"],
                "name": it.get("name"),
                "size": int(it.get("size") or 0),
                "modified": it.get("lastModifiedDateTime"),
            })

    return {
        "drive_id": drive_id,
        "folder_id": folder_id,
        "next_url": page.get("next"),
        "folders": sorted(folders, key=lambda f: (f["name"] or "").lower()),
        "files": sorted(files, key=lambda f: (f["name"] or "").lower()),
    }


@router.get("/admin/search")
async def admin_search(
    drive_id: str,
    q: str = Query(..., min_length=1),
    folder_id: Optional[str] = None,
    _auth: bool = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    """Keyword search across file and folder names in SharePoint.
    Returns matching files (with parent folder context) and matching folders (with allowed status)."""
    client = get_sharepoint_client()
    try:
        raw_items = await client.search_items(drive_id, q, folder_id=folder_id)
    except Exception as e:
        raise _graph_error(e)

    allowed_records = db.query(SharePointAllowedFolder).filter(
        SharePointAllowedFolder.drive_id == drive_id
    ).all()
    allowed_folder_ids = {rec.folder_id: rec for rec in allowed_records if rec.is_active}

    folders, files = [], []
    for it in raw_items:
        parent_ref = it.get("parentReference", {})
        raw_path = parent_ref.get("path", "")
        parent_id = parent_ref.get("id")
        fpath = raw_path.split("root:", 1)[1].lstrip("/") if "root:" in raw_path else ""

        if "folder" in it:
            fid = it["id"]
            rec = allowed_folder_ids.get(fid)
            full_path = f"{fpath}/{it.get('name')}".strip("/") if fpath else (it.get("name") or "")
            folders.append({
                "id": fid,
                "name": it.get("name"),
                "path": full_path,
                "child_count": (it.get("folder") or {}).get("childCount"),
                "is_allowed": rec is not None,
                "allowed_id": rec.id if rec else None,
                "display_name": rec.display_name if rec else None,
            })
        elif "file" in it:
            parent_is_allowed = parent_id in allowed_folder_ids if parent_id else False
            files.append({
                "id": it["id"],
                "name": it.get("name"),
                "size": int(it.get("size") or 0),
                "modified": it.get("lastModifiedDateTime"),
                "parent_folder_id": parent_id,
                "parent_folder_path": fpath,
                "parent_folder_name": fpath.split("/")[-1] if fpath else "Root",
                "parent_is_allowed": parent_is_allowed,
            })

    return {
        "drive_id": drive_id,
        "query": q,
        "total_results": len(folders) + len(files),
        "folders": sorted(folders, key=lambda f: (f["name"] or "").lower()),
        "files": sorted(files, key=lambda f: (f["name"] or "").lower()),
    }


@router.get("/admin/allowed-folders")
def admin_get_allowed_folders(
    drive_id: Optional[str] = None,
    _auth: bool = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    query = db.query(SharePointAllowedFolder)
    if drive_id:
        query = query.filter(SharePointAllowedFolder.drive_id == drive_id)
    records = query.order_by(SharePointAllowedFolder.folder_name.asc()).all()
    return {
        "folders": [
            {
                "id": r.id,
                "drive_id": r.drive_id,
                "folder_id": r.folder_id,
                "folder_name": r.folder_name,
                "folder_path": r.folder_path,
                "display_name": r.display_name,
                "is_active": r.is_active,
                "added_by": r.added_by,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in records
        ]
    }


@router.post("/admin/allowed-folders")
async def admin_save_allowed_folder(
    req: AdminFolderRequest,
    _auth: bool = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    client = get_sharepoint_client()
    folder_name = req.folder_name
    folder_path = req.folder_path
    if not folder_name or not folder_path:
        try:
            item = await client.get_item(req.drive_id, req.folder_id)
            if not folder_name:
                folder_name = item.get("name") or req.folder_id
            if not folder_path:
                p = (item.get("parentReference") or {}).get("path") or ""
                root_part = p.split("root:", 1)[1].lstrip("/") if "root:" in p else ""
                folder_path = f"{root_part}/{folder_name}".strip("/") if root_part else folder_name
        except Exception as e:
            logger.warning(f"Could not retrieve folder details from SharePoint: {e}")
            if not folder_name:
                folder_name = req.folder_id
            if not folder_path:
                folder_path = folder_name

    row = db.query(SharePointAllowedFolder).filter(
        SharePointAllowedFolder.drive_id == req.drive_id,
        SharePointAllowedFolder.folder_id == req.folder_id,
    ).first()

    if row:
        row.is_active = req.is_active
        if req.display_name is not None:
            row.display_name = req.display_name
        if folder_name:
            row.folder_name = folder_name
        if folder_path:
            row.folder_path = folder_path
    else:
        row = SharePointAllowedFolder(
            drive_id=req.drive_id,
            folder_id=req.folder_id,
            folder_name=folder_name,
            folder_path=folder_path,
            display_name=req.display_name,
            is_active=req.is_active,
            added_by="admin",
        )
        db.add(row)

    db.commit()
    db.refresh(row)
    return {
        "success": True,
        "folder": {
            "id": row.id,
            "drive_id": row.drive_id,
            "folder_id": row.folder_id,
            "folder_name": row.folder_name,
            "folder_path": row.folder_path,
            "display_name": row.display_name,
            "is_active": row.is_active,
        }
    }


@router.delete("/admin/allowed-folders/{folder_id}")
def admin_delete_allowed_folder(
    folder_id: str,
    drive_id: str = Query(...),
    _auth: bool = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    row = db.query(SharePointAllowedFolder).filter(
        SharePointAllowedFolder.drive_id == drive_id,
        SharePointAllowedFolder.folder_id == folder_id,
    ).first()
    if not row:
        row = db.query(SharePointAllowedFolder).filter(
            SharePointAllowedFolder.id == folder_id
        ).first()

    if not row:
        raise HTTPException(status_code=404, detail="Allowed folder not found")

    db.delete(row)
    db.commit()
    return {"success": True, "message": "Folder removed from allowed list"}


# ------------------------------------------------------------------------------------------------ selective importer

def _graph_error(e: Exception) -> HTTPException:
    """Turn Graph / validation failures into clear API errors (never leaks tokens or secrets)."""
    if isinstance(e, ValueError):
        return HTTPException(status_code=400, detail=str(e))
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code in (401, 403):
            return HTTPException(status_code=403, detail="SharePoint refused access. Ask an admin to grant the app access to this "
                                                         "site (Sites.Selected) and check the Azure credentials.")
        if code == 404:
            return HTTPException(status_code=404, detail="That SharePoint item or library was not found.")
        if code == 429:
            return HTTPException(status_code=503, detail="SharePoint is throttling requests. Wait a minute and try again.")
        return HTTPException(status_code=502, detail=f"SharePoint returned HTTP {code}.")
    if isinstance(e, httpx.HTTPError):
        return HTTPException(status_code=502, detail="Could not reach SharePoint.")
    logger.exception("SharePoint importer error")
    return HTTPException(status_code=500, detail="SharePoint importer failed.")


@router.get("/config")
def sharepoint_config(db: Session = Depends(get_db)):
    """Whether the bridge is configured. Never returns secrets."""
    active_folders = []
    try:
        active_folders = db.query(SharePointAllowedFolder).filter(
            SharePointAllowedFolder.is_active == True
        ).all()
    except Exception as e:
        logger.warning(f"Error querying active folders: {e}")

    raw_roots = (settings.SHAREPOINT_ROOT_FOLDERS or settings.SHAREPOINT_ROOT_FOLDER or "").strip().strip("/")
    env_root_list = [f.strip() for f in raw_roots.split(",") if f.strip()]
    if active_folders:
        root_list = [f.display_name or f.folder_name for f in active_folders]
        is_custom = True
    else:
        root_list = env_root_list
        is_custom = False

    return {
        "configured": bool(settings.AZURE_TENANT_ID and settings.AZURE_CLIENT_ID and settings.AZURE_CLIENT_SECRET
                           and (settings.SHAREPOINT_SITE_ID or settings.SHAREPOINT_DRIVE_ID)),
        "has_credentials": bool(settings.AZURE_TENANT_ID and settings.AZURE_CLIENT_ID and settings.AZURE_CLIENT_SECRET),
        "has_site": bool(settings.SHAREPOINT_SITE_ID),
        "has_drive": bool(settings.SHAREPOINT_DRIVE_ID),
        "root_folder": ", ".join(root_list) if root_list else None,
        "root_folders": root_list,
        "is_custom_folders": is_custom,
        "allowed_folders_count": len(active_folders),
        "admin_configured": bool(settings.SHAREPOINT_ADMIN_PASSWORD),
        "max_file_mb": int(importer.MAX_FILE_BYTES / (1024 * 1024)),
        "supported_extensions": sorted(importer.SUPPORTED_EXT),
    }


@router.get("/drives")
async def sharepoint_drives():
    try:
        return {"drives": await get_sharepoint_client().list_drives()}
    except Exception as e:
        raise _graph_error(e)


@router.get("/browse")
async def sharepoint_browse(
    drive_id: str,
    folder_id: Optional[str] = None,
    next_url: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """One page of a folder: sub-folders plus the importable decks in it, each tagged with its import status."""
    client = get_sharepoint_client()
    try:
        roots = await importer.scope_roots(client, drive_id, db=db)
        if len(roots) > 1 and (not folder_id or folder_id == "root"):
            return {
                "drive_id": drive_id,
                "folder_id": None,
                "next_url": None,
                "other_files_hidden": 0,
                "folders": [
                    {"id": r["id"], "name": r["name"], "child_count": None}
                    for r in sorted(roots, key=lambda r: (r["name"] or "").lower())
                ],
                "files": [],
            }

        eff = await importer.effective_folder(client, drive_id, folder_id, db=db)
        if next_url and roots:
            # a continuation link must belong to the folder just checked, never to a different one
            valid_prefix = any(
                next_url.startswith(f"{client.GRAPH_BASE}/drives/{drive_id}/items/{r['id']}/children") or
                (eff and next_url.startswith(f"{client.GRAPH_BASE}/drives/{drive_id}/items/{eff}/children"))
                for r in roots
            )
            if not valid_prefix:
                raise ValueError("That page link does not belong to the allowed folder")
        page = await client.list_children(drive_id, eff, next_url=next_url)
    except Exception as e:
        raise _graph_error(e)
    folders, cands, other = [], [], 0
    for it in page["items"]:
        if "folder" in it:
            folders.append({"id": it["id"], "name": it.get("name"), "child_count": (it.get("folder") or {}).get("childCount")})
        elif "file" in it:
            if importer._supported(it.get("name") or ""):
                cands.append(importer.candidate_from_item(it, drive_id))
            else:
                other += 1
    importer.classify(db, cands)
    return {
        "drive_id": drive_id, "folder_id": folder_id, "next_url": page["next"], "other_files_hidden": other,
        "folders": sorted(folders, key=lambda f: (f["name"] or "").lower()),
        "files": [{
            "id": c["id"], "name": c["name"], "size": c["size"], "status": c["status"],
            "modified": c["modified"].isoformat() if c.get("modified") else None,
            "match_document_id": c.get("match_document_id"),
        } for c in sorted(cands, key=lambda c: c["name"].lower())],
    }


@router.post("/plan")
async def sharepoint_plan(sel: importer.Selection, db: Session = Depends(get_db)):
    """Dry run: what would be imported for this selection? Writes nothing."""
    try:
        return await importer.plan_import(db, get_sharepoint_client(), sel)
    except Exception as e:
        raise _graph_error(e)


@router.post("/import")
async def sharepoint_import(sel: importer.Selection, db: Session = Depends(get_db)):
    """Mark the wanted files for import (one batch). Files move in short steps via /transfer-next."""
    try:
        return await importer.stage_import(db, get_sharepoint_client(), sel)
    except Exception as e:
        raise _graph_error(e)


@router.post("/transfer-next")
async def sharepoint_transfer_next(
    background_tasks: BackgroundTasks,
    limit: int = Query(3, ge=1, le=settings.SHAREPOINT_BATCH_MAX),
    batch_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Download and queue up to `limit` selected files. Call repeatedly until `remaining` is 0. Queued decks are
    processed by the normal ingestion worker, exactly like manual uploads."""
    try:
        res = await importer.transfer_next(db, get_sharepoint_client(), limit=limit, batch_id=batch_id)
    except Exception as e:
        raise _graph_error(e)
    if res.get("queued"):
        from backend.app.services.ingestion import upload_queue
        background_tasks.add_task(upload_queue.dispatch_available)
    return res


@router.get("/import-status")
def sharepoint_import_status(batch_id: Optional[str] = None, db: Session = Depends(get_db)):
    return importer.import_status(db, batch_id)


from backend.app.services.sharepoint import index as sp_index


@router.post("/index/step")
async def sharepoint_index_step(
    drive_id: Optional[str] = None,
    budget_seconds: int = Query(20, ge=1, le=120),
    db: Session = Depends(get_db),
):
    """Run one scan step of the SharePoint file index.
    Protected from overlapping executions via lease."""
    client = get_sharepoint_client()
    try:
        return await sp_index.index_step(db, client, budget_seconds=budget_seconds, drive_id=drive_id)
    except Exception as e:
        raise _graph_error(e)


@router.get("/index/status")
def sharepoint_index_status(
    drive_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Return state, counts and last updated time of the SharePoint file index."""
    return sp_index.get_index_status(db, drive_id=drive_id)


@router.get("/not-imported")
async def sharepoint_not_imported(
    drive_id: Optional[str] = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Candidates in SharePoint that are not yet imported into QShala (read from index).
    Scoped to allowed folders / env roots, duplicate deck names omitted, sorted newest first."""
    client = get_sharepoint_client()
    try:
        return await sp_index.get_not_imported_from_index(
            db, client=client, drive_id=drive_id, limit=limit, offset=offset
        )
    except Exception as e:
        raise _graph_error(e)
