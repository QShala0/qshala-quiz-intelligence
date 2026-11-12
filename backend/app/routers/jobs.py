import logging
import os
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from backend.app.database import get_db
from backend.app.models.document import Document
from backend.app.models.job import IngestionJob
from backend.app.models.upload_batch import UploadBatch
from backend.app.services.jobs.job_queue import get_job_queue_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["Ingestion Jobs"])


@router.post("/process-upload-next")
async def process_upload_next():
    """Process or dispatch queued deck uploads up to capacity. Safe to call from an external
    cron as a reliable driver that doesn't depend on the in-process worker."""
    from backend.app.services.ingestion import upload_queue
    try:
        dispatched = await upload_queue.dispatch_available()
        return {"status": "SUCCESS" if dispatched else "IDLE", "processed": dispatched, "dispatched": dispatched}
    except Exception as e:
        logger.warning("process-upload-next failed: %s", e)
        return {"status": "ERROR", "error": str(e)[:300]}


@router.get("/upload-queue")
def upload_queue_status(db: Session = Depends(get_db)):
    """What the upload queue is doing right now, so the state is never a mystery."""
    from sqlalchemy import func
    rows = db.query(IngestionJob).filter(IngestionJob.source_type == "MANUAL_UPLOAD").\
        order_by(IngestionJob.created_at.desc()).limit(50).all()
    counts = dict(db.query(IngestionJob.status, func.count(IngestionJob.id)).
                  filter(IngestionJob.source_type == "MANUAL_UPLOAD").
                  group_by(IngestionJob.status).all())
    return {
        "counts": {k: int(v) for k, v in counts.items()},
        "jobs": [{"id": j.id, "filename": j.filename, "status": j.status,
                  "step": j.current_step, "error": j.error_message,
                  "document_id": j.document_id,
                  "updated_at": j.updated_at.isoformat() if j.updated_at else None} for j in rows],
    }


# --------------------------------------------------------------------- Multi-deck upload batches (Phase M2)

class BatchFileItem(BaseModel):
    client_item_id: str
    filename: str
    size_bytes: int = 0


class CreateBatchRequest(BaseModel):
    files: List[BatchFileItem]


@router.post("/upload-batch")
def create_upload_batch(payload: CreateBatchRequest, db: Session = Depends(get_db)):
    """Register a new multi-deck upload batch of up to 25 PPTX/PDF files."""
    if not payload.files or len(payload.files) > 25:
        raise HTTPException(status_code=400, detail="Batch must contain between 1 and 25 files.")

    for f in payload.files:
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in [".pptx", ".ppt", ".pdf"]:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file format '{ext}' for file '{f.filename}'. Only PPTX, PPT, and PDF are allowed."
            )
        if not f.client_item_id or not f.client_item_id.strip():
            raise HTTPException(status_code=400, detail="Each file must have a non-empty client_item_id.")

    items_data = [f.model_dump() for f in payload.files]
    batch = UploadBatch(total_expected=len(payload.files), items=items_data)
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return {"batch_id": batch.id}


@router.post("/upload-batch/{batch_id}/heartbeat")
def upload_batch_heartbeat(batch_id: str, db: Session = Depends(get_db)):
    """Keep the upload batch active while a client tab is open."""
    batch = db.query(UploadBatch).filter(UploadBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Upload batch not found")
    batch.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "OK", "batch_id": batch_id}


@router.get("/upload-batches/active")
def get_active_upload_batch(db: Session = Depends(get_db)):
    """Return the most recent batch that still has active work, ignoring abandoned batches (>5 min without heartbeat)."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    abandoned_cutoff = now - timedelta(minutes=5)

    batches = db.query(UploadBatch).order_by(UploadBatch.created_at.desc()).limit(20).all()
    for batch in batches:
        jobs = db.query(IngestionJob).filter(IngestionJob.batch_id == batch.id).all()
        jobs_by_cid = {j.client_item_id: j for j in jobs if j.client_item_id}
        registered_items = batch.items or []

        # If any job is currently PENDING or PROCESSING on the server, batch is active
        has_server_active = any(j.status in ("PENDING", "PROCESSING") for j in jobs)
        if has_server_active:
            return {"batch_id": batch.id}

        # Check for WAITING items (bytes not yet sent)
        has_waiting = False
        for item in registered_items:
            cid = item.get("client_item_id")
            if cid not in jobs_by_cid and not item.get("cancelled"):
                has_waiting = True
                break

        if has_waiting:
            last_seen = batch.last_seen_at or batch.created_at
            if last_seen and last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            if last_seen and last_seen >= abandoned_cutoff:
                return {"batch_id": batch.id}

    return {"batch_id": None}


@router.get("/upload-batch/{batch_id}")
def get_upload_batch_status(batch_id: str, db: Session = Depends(get_db)):
    """Get merged status and totals for an upload batch (read-only query)."""
    batch = db.query(UploadBatch).filter(UploadBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Upload batch not found")

    job_docs = (
        db.query(IngestionJob, Document)
        .outerjoin(Document, IngestionJob.document_id == Document.id)
        .filter(IngestionJob.batch_id == batch_id)
        .all()
    )

    pending_job_ids = [
        row[0] for row in db.query(IngestionJob.id)
        .filter(IngestionJob.source_type == "MANUAL_UPLOAD", IngestionJob.status == "PENDING")
        .order_by(IngestionJob.created_at)
        .all()
    ]

    jobs_by_cid = {}
    for j, d in job_docs:
        if j.client_item_id:
            jobs_by_cid[j.client_item_id] = (j, d)

    registered_items = batch.items or []
    items_out = []
    totals = {
        "total": len(registered_items),
        "completed": 0,
        "processing": 0,
        "cancelling": 0,
        "queued": 0,
        "waiting": 0,
        "failed": 0,
        "cancelled": 0,
    }

    for item in registered_items:
        cid = item.get("client_item_id")
        fname = item.get("filename")
        if cid in jobs_by_cid:
            job, doc = jobs_by_cid[cid]
            if job.status == "PENDING":
                st = "QUEUED"
            elif job.status == "PROCESSING":
                if job.current_step == "CANCELLING":
                    st = "CANCELLING"
                else:
                    st = "PROCESSING"
            elif job.status == "COMPLETED":
                st = "COMPLETED"
            elif job.status == "CANCELLED":
                st = "CANCELLED"
            else:
                st = "FAILED"

            if doc:
                if doc.processing_status == "CANCELLED":
                    st = "CANCELLED"
                elif doc.processing_status == "FAILED":
                    st = "FAILED"

            q_pos = pending_job_ids.index(job.id) if (job.id in pending_job_ids and st == "QUEUED") else None

            pct = 0
            step_text = job.current_step
            if st == "CANCELLING":
                step_text = "Cancelling..."
            elif st == "PROCESSING":
                if doc and doc.progress_detail:
                    pct = doc.progress_detail.get("progress_percentage", 0)
                    step_text = (
                        doc.progress_detail.get("current_stage")
                        or doc.progress_detail.get("step")
                        or job.current_step
                    )
            elif st == "COMPLETED":
                pct = 100

            err = job.error_message or (doc.processing_error if doc else None)

            items_out.append({
                "client_item_id": cid,
                "filename": fname or job.filename,
                "status": st,
                "document_id": job.document_id,
                "job_id": job.id,
                "queue_position": q_pos,
                "slides": doc.slide_count if (doc and doc.slide_count is not None) else 0,
                "questions": doc.question_count if (doc and doc.question_count is not None) else 0,
                "step": step_text,
                "percentage": pct,
                "error": err,
                "size_bytes": item.get("size_bytes", 0),
            })
            totals[st.lower()] = totals.get(st.lower(), 0) + 1
        else:
            # No IngestionJob row exists yet -- this file's bytes were never sent, either because
            # it's still waiting in the browser's own upload queue, or because a batch/item cancel
            # hit it before it got that far (see cancel_entire_batch / cancel_pending_batch_items,
            # which flag registered-but-unsent items with "cancelled": True in batch.items since
            # there's no job row for them to mark CANCELLED on). Report that flag here so a client
            # that reloads mid-batch sees CANCELLED instead of the file reappearing as WAITING.
            st = "CANCELLED" if item.get("cancelled") else "WAITING"
            items_out.append({
                "client_item_id": cid,
                "filename": fname,
                "status": st,
                "document_id": None,
                "job_id": None,
                "queue_position": None,
                "slides": 0,
                "questions": 0,
                "step": "Cancelled by user" if st == "CANCELLED" else None,
                "percentage": 0,
                "error": None,
                "size_bytes": item.get("size_bytes", 0),
            })
            totals[st.lower()] += 1

    return {
        "batch_id": batch.id,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "totals": totals,
        "items": items_out,
    }


@router.post("/upload-batch/{batch_id}/cancel-pending")
def cancel_pending_batch_items(batch_id: str, db: Session = Depends(get_db)):
    """Cancels all QUEUED (PENDING) jobs of that batch. Must NOT touch the PROCESSING job."""
    from backend.app.services.ingestion import upload_queue as uq
    from backend.app.services.ingestion.cancellation import request_doc_cancel
    from backend.app.services.storage.r2_storage import get_r2_service

    now = datetime.now(timezone.utc)
    queued_jobs = (
        db.query(IngestionJob)
        .filter(IngestionJob.batch_id == batch_id, IngestionJob.status == "PENDING")
        .all()
    )

    r2 = get_r2_service()
    cancelled_count = 0

    for job in queued_jobs:
        job.status = "CANCELLED"
        job.current_step = "CANCELLED"
        job.completed_at = now
        job.updated_at = now
        job.error_message = "Cancelled by user"

        if job.document_id:
            request_doc_cancel(job.document_id)
            doc = db.query(Document).filter(Document.id == job.document_id).first()
            if doc:
                doc.processing_status = "CANCELLED"
                doc.cancel_requested = True
                if r2.is_configured:
                    ext = os.path.splitext(doc.storage_path or doc.filename or "")[1]
                    raw_key = f"raw-uploads/{doc.id}{ext}"
                    try:
                        r2.delete_object(raw_key)
                        logger.info("[BATCH-CANCEL] removed raw R2 key %s for doc %s", raw_key, doc.id)
                    except Exception as e:
                        logger.warning("[BATCH-CANCEL] failed to delete R2 object %s: %s", raw_key, e)
                if doc.storage_path and os.path.exists(doc.storage_path):
                    try:
                        os.remove(doc.storage_path)
                    except Exception:
                        pass
                try:
                    uq.purge_artifacts(db, doc.id)
                except Exception as e:
                    logger.warning("[BATCH-CANCEL] purge_artifacts failed for doc %s: %s", doc.id, e)

        cancelled_count += 1

    db.commit()
    logger.info("[BATCH-CANCEL] cancelled %d pending job(s) for batch %s", cancelled_count, batch_id)
    return {"batch_id": batch_id, "cancelled_count": cancelled_count}


@router.post("/upload-batch/{batch_id}/cancel")
def cancel_entire_batch(batch_id: str, db: Session = Depends(get_db)):
    """Cancels all active jobs (both PENDING and PROCESSING) in this batch and purges pending artifacts."""
    from backend.app.services.ingestion import upload_queue as uq
    from backend.app.services.ingestion.cancellation import request_doc_cancel
    from backend.app.services.storage.r2_storage import get_r2_service

    now = datetime.now(timezone.utc)
    active_jobs = (
        db.query(IngestionJob)
        .filter(
            IngestionJob.batch_id == batch_id,
            IngestionJob.status.in_(["PENDING", "PROCESSING"])
        )
        .all()
    )

    r2 = get_r2_service()
    cancelled_count = 0

    from backend.app.services.ingestion.cancellation import worker_is_alive

    for job in active_jobs:
        if job.status == "PROCESSING" and job.document_id and worker_is_alive(db, job.document_id):
            # A live Cloud Run Job worker owns this deck. Purging its rows and files now would leave it calling
            # Gemini and then failing on the missing document. Flag it instead: the worker stops at its next
            # checkpoint, removes the document and marks the job CANCELLED itself.
            request_doc_cancel(job.document_id)
            live_doc = db.query(Document).filter(Document.id == job.document_id).first()
            if live_doc:
                live_doc.cancel_requested = True
            cancelled_count += 1
            continue

        job.status = "CANCELLED"
        job.current_step = "CANCELLED"
        job.completed_at = now
        job.updated_at = now
        job.error_message = "Cancelled by user"

        if job.execution_name:
            try:
                from google.cloud import run_v2
                client = run_v2.ExecutionsClient()
                client.cancel_execution(name=job.execution_name)
            except Exception as e:
                logger.warning("[BATCH-CANCEL] failed to cancel Cloud Run execution %s: %s", job.execution_name, e)

        if job.document_id:
            request_doc_cancel(job.document_id)
            doc = db.query(Document).filter(Document.id == job.document_id).first()
            if doc:
                doc.processing_status = "CANCELLED"
                doc.cancel_requested = True
                if r2.is_configured:
                    ext = os.path.splitext(doc.storage_path or doc.filename or "")[1]
                    raw_key = f"raw-uploads/{doc.id}{ext}"
                    try:
                        r2.delete_object(raw_key)
                        logger.info("[BATCH-CANCEL] removed raw R2 key %s for doc %s", raw_key, doc.id)
                    except Exception as e:
                        logger.warning("[BATCH-CANCEL] failed to delete R2 object %s: %s", raw_key, e)
                if doc.storage_path and os.path.exists(doc.storage_path):
                    try:
                        os.remove(doc.storage_path)
                    except Exception:
                        pass
                try:
                    uq.purge_artifacts(db, doc.id)
                except Exception as e:
                    logger.warning("[BATCH-CANCEL] purge_artifacts failed for doc %s: %s", doc.id, e)

        cancelled_count += 1

    # Files still sitting in the browser's own upload queue (bytes never sent) have no
    # IngestionJob row at all, so the loop above never touches them -- yet the frontend's
    # "Cancel Batch" button is advertised (and confirmed by the user) as cancelling every
    # unfinished deck in the batch, these included. Without persisting that here, a page
    # reload re-derives batch state purely from the server (UploadBatch.items + IngestionJob),
    # finds no record of these files being cancelled, and reports them straight back as
    # WAITING/queued -- the batch "reappearing" after a refresh that a cancelled deck should
    # not have survived. Flag them in-place on the registered item so get_upload_batch_status
    # and get_active_upload_batch both see the cancellation after a reload, not just this
    # response's in-memory state.
    batch = db.query(UploadBatch).filter(UploadBatch.id == batch_id).first()
    if batch and batch.items:
        jobs_by_cid = {
            j.client_item_id
            for j in db.query(IngestionJob.client_item_id)
                .filter(IngestionJob.batch_id == batch_id, IngestionJob.client_item_id.isnot(None))
                .all()
        }
        updated_items = []
        changed = False
        for item in batch.items:
            if item.get("client_item_id") not in jobs_by_cid and not item.get("cancelled"):
                item = {**item, "cancelled": True}
                changed = True
            updated_items.append(item)
        if changed:
            batch.items = updated_items  # reassign (not mutate in place) so SQLAlchemy sees the change

    db.commit()
    logger.info("[BATCH-CANCEL] cancelled entire batch %s (%d jobs)", batch_id, cancelled_count)
    return {"batch_id": batch_id, "cancelled_count": cancelled_count}

def to_job_dict(job: IngestionJob) -> Dict[str, Any]:
    step_data = job.step_data or {}
    return {
        "id": job.id,
        "source_type": job.source_type,
        "source_file_id": job.source_file_id,
        "filename": job.filename,
        "blob_url": job.blob_url,
        "status": job.status,
        "current_step": job.current_step,
        "retry_count": job.retry_count,
        "max_retries": job.max_retries,
        "error_message": job.error_message,
        "document_id": job.document_id,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "step_summary": {
            "slides_count": len(step_data.get("slides", [])),
            "media_count": len(step_data.get("media_manifest", [])),
            "media_cursor": step_data.get("media_cursor", 0),
            "questions_count": len(step_data.get("canonical_questions", [])),
            "exact_duplicates": step_data.get("exact_duplicates_count", 0),
        }
    }

@router.get("")
def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status: PENDING, PROCESSING, COMPLETED, FAILED"),
    source_type: Optional[str] = Query(None, description="Filter by source_type: SHAREPOINT, MANUAL_UPLOAD"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """List background ingestion jobs with status and step data summaries."""
    query = db.query(IngestionJob)
    if status:
        query = query.filter(IngestionJob.status == status)
    if source_type:
        query = query.filter(IngestionJob.source_type == source_type)

    total = query.count()
    items = query.order_by(IngestionJob.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "jobs": [to_job_dict(j) for j in items]
    }

@router.get("/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)):
    """Fetch full details for an individual background ingestion job."""
    job = db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    return to_job_dict(job)

@router.post("/process-next")
async def process_next_job_endpoint(db: Session = Depends(get_db)):
    """
    Worker invocation endpoint.
    Processes the next available step of the oldest pending/processing job.
    Designed for recurring cron triggers or client-side polling loops.
    """
    service = get_job_queue_service()
    try:
        job = await service.process_next_job(db)
        if not job:
            return {"status": "NO_JOBS_PENDING", "job": None}
        return {"status": "SUCCESS", "job": to_job_dict(job)}
    except Exception as e:
        logger.exception(f"Error processing next job: {e}")
        return {"status": "ERROR", "error": str(e)}

@router.post("/{job_id}/retry")
async def retry_job(job_id: str, db: Session = Depends(get_db)):
    """Resets a failed job to PENDING with reset retry count."""
    job = db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")

    # Manual-upload jobs re-run the full pipeline, which would duplicate slides/questions if any
    # partial output from the failed attempt is still around — so purge the deck's artifacts first.
    if job.source_type == "MANUAL_UPLOAD":
        from backend.app.services.ingestion.upload_queue import requeue_job, dispatch_available
        requeue_job(db, job)
        try:
            await dispatch_available()
        except Exception as e:
            logger.warning("dispatch_available after retry failed: %s", e)
    else:
        job.status = "PENDING"
        job.retry_count = 0
        job.error_message = None
        db.commit()
    db.refresh(job)
    return {"message": "Job re-queued successfully", "job": to_job_dict(job)}

@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, db: Session = Depends(get_db)):
    """Cancels a job and cleans up associated document resources."""
    from backend.app.services.ingestion.cancellation import request_doc_cancel
    from backend.app.services.storage.r2_storage import get_r2_service
    from backend.app.services.ingestion import upload_queue as uq

    job = db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")

    if job.status in ("COMPLETED", "FAILED", "CANCELLED"):
        return {"message": "Job already in terminal status", "job": to_job_dict(job)}

    now = datetime.now(timezone.utc)

    if job.status == "PROCESSING":
        job.status = "CANCELLING"
        job.cancel_requested = True
        job.current_step = "CANCELLING"
        job.error_message = "Cancelled by user"
        job.updated_at = now
        if job.document_id:
            request_doc_cancel(job.document_id)
            doc = db.query(Document).filter(Document.id == job.document_id).first()
            if doc:
                doc.cancel_requested = True

        if job.execution_name:
            try:
                from google.cloud import run_v2
                client = run_v2.ExecutionsClient()
                client.cancel_execution(name=job.execution_name)
                logger.info("[CANCEL] cancelled Cloud Run execution %s", job.execution_name)
            except Exception as e:
                logger.warning("[CANCEL] failed to cancel Cloud Run execution %s: %s", job.execution_name, e)

        db.commit()
        db.refresh(job)
        return {"message": "Job cancellation requested", "job": to_job_dict(job)}

    job.status = "CANCELLED"
    job.current_step = "CANCELLED"
    job.completed_at = now
    job.updated_at = now
    job.error_message = "Cancelled by user"

    if job.document_id:
        request_doc_cancel(job.document_id)
        doc = db.query(Document).filter(Document.id == job.document_id).first()
        if doc:
            doc.processing_status = "CANCELLED"
            doc.cancel_requested = True
            r2 = get_r2_service()
            if r2.is_configured:
                ext = os.path.splitext(doc.storage_path or doc.filename or "")[1]
                raw_key = f"raw-uploads/{doc.id}{ext}"
                try:
                    r2.delete_object(raw_key)
                except Exception:
                    pass
            if doc.storage_path and os.path.exists(doc.storage_path):
                try:
                    os.remove(doc.storage_path)
                except Exception:
                    pass
            try:
                uq.purge_artifacts(db, doc.id)
            except Exception:
                pass

    db.commit()
    db.refresh(job)
    return {"message": "Job cancelled", "job": to_job_dict(job)}
