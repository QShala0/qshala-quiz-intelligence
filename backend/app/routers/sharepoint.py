import logging
from typing import Optional, List
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel
from backend.app.database import get_db
from backend.app.models.sharepoint import SharePointSyncState, SharePointFile
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
def sharepoint_config():
    """Whether the bridge is configured. Never returns secrets."""
    raw_roots = (settings.SHAREPOINT_ROOT_FOLDERS or settings.SHAREPOINT_ROOT_FOLDER or "").strip().strip("/")
    root_list = [f.strip() for f in raw_roots.split(",") if f.strip()]
    return {
        "configured": bool(settings.AZURE_TENANT_ID and settings.AZURE_CLIENT_ID and settings.AZURE_CLIENT_SECRET
                           and (settings.SHAREPOINT_SITE_ID or settings.SHAREPOINT_DRIVE_ID)),
        "has_credentials": bool(settings.AZURE_TENANT_ID and settings.AZURE_CLIENT_ID and settings.AZURE_CLIENT_SECRET),
        "has_site": bool(settings.SHAREPOINT_SITE_ID),
        "has_drive": bool(settings.SHAREPOINT_DRIVE_ID),
        "root_folder": ", ".join(root_list) if root_list else None,
        "root_folders": root_list,
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
        roots = await importer.scope_roots(client, drive_id)
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

        eff = await importer.effective_folder(client, drive_id, folder_id)
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
    limit: int = Query(3, ge=1, le=10),
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
