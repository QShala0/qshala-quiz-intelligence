import asyncio
import copy
import json
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, UploadFile, File, Form, BackgroundTasks, HTTPException, Query
from sqlalchemy.orm import Session, noload
from backend.app.database import get_db
from backend.app.models.document import Document, Slide
from backend.app.schemas.document import DocumentResponse, DocumentListItem, IngestionStatusResponse, SlideResponse
from backend.app.services.ingestion.pipeline import IngestionPipeline, INGESTION_STATUS_REGISTRY
from backend.app.config import settings
from backend.app.services.ingestion.cancellation import (
    valid_upload_id,
    request_upload_cancel,
    upload_cancel_requested,
    remove_chunk_dir,
    request_doc_cancel,
    worker_is_alive,
    purge_document,
)

logger = logging.getLogger(__name__)


def _schedule_ingestion(
    doc,
    db: Session,
    background_tasks: BackgroundTasks,
    raw_already_in_r2: bool = False,
    batch_id: Optional[str] = None,
    client_item_id: Optional[str] = None,
) -> None:
    """Start ingestion for an uploaded deck.

    Three modes, in priority order:
      1. INGESTION_MODE=cloud_run_job -- offload to a separate Cloud Run Job execution (worker.py)
         so a heavy deck can't starve the API process's own request handling or memory. See
         job_trigger.py. Opt-in; default is unchanged (in_process below).
      2. UPLOAD_QUEUE_ENABLED (default on) -- enqueue a job and let the single serial worker
         process it over time — several uploads then ingest one-at-a-time instead of all at once
         (which OOMs a small host).
      3. Neither -- the old immediate per-upload background task, in-process.

    `raw_already_in_r2` is set by /upload-chunk when it streamed the slices straight into an R2
    multipart upload as they arrived (see _upload_chunk_direct_to_r2) -- in that case the raw deck
    is already sitting at the exact key this function would otherwise stage it to, so the
    read-local-file-and-re-upload step below is skipped entirely.
    """
    if settings.INGESTION_MODE == "cloud_run_job":
        from backend.app.services.ingestion.job_trigger import trigger_ingestion_job, IngestionJobTriggerError
        from backend.app.services.storage.r2_storage import get_r2_service
        # Deliberately does NOT write to INGESTION_STATUS_REGISTRY: that dict is this process's
        # own memory, but the pipeline runs inside a separate worker container for this mode. An
        # entry written here would never be updated again -- the worker can't see this process's
        # memory any more than this process can see the worker's -- which would permanently mask
        # the real status behind a frozen "just started" snapshot. (This was a real bug in the
        # first version of this code path: every poll kept returning this stale entry instead of
        # falling through to live data.) The /status endpoint's existing DB fallback
        # (Document.processing_status/slide_count/question_count/processing_error, all written
        # by the pipeline itself regardless of which process runs it) is what actually reflects
        # progress here -- the same fallback that already covers "a document this process never
        # saw ingestion start for," e.g. after a restart.

        # The raw deck this process just streamed to local disk (doc.storage_path) lives only in
        # THIS container's own ephemeral filesystem -- the worker Job runs in a completely
        # separate container with its own empty disk and never sees it (this caused every
        # cloud_run_job upload to fail with FileNotFoundError in pptx_parser). Stage it in R2
        # under a key the worker can derive from doc.id/extension, so worker.py can fetch it
        # before handing off to the unmodified pipeline, which still just reads doc.storage_path.
        r2 = get_r2_service()
        if not r2.is_configured:
            logger.error(
                "[UPLOAD] INGESTION_MODE=cloud_run_job requires R2 to hand the raw deck to the "
                "worker, but R2 isn't configured (doc_id=%s)", doc.id,
            )
            doc.processing_status = "FAILED"
            doc.processing_error = (
                "Server misconfiguration: R2 storage must be configured for Cloud Run Job "
                "ingestion mode (the worker can't otherwise see the uploaded file)."
            )
            db.commit()
            return
        if raw_already_in_r2:
            logger.info("[UPLOAD] raw deck already staged in R2 via chunk-streamed multipart upload: doc_id=%s", doc.id)
        else:
            try:
                raw_key = f"raw-uploads/{doc.id}{os.path.splitext(doc.storage_path)[1]}"
                r2.upload_file(raw_key, doc.storage_path)
                logger.info("[UPLOAD] staged raw deck in R2 for worker: doc_id=%s key=%s", doc.id, raw_key)
            except Exception as e:
                logger.exception("[UPLOAD] failed to stage raw deck in R2 for doc_id=%s: %s", doc.id, e)
                doc.processing_status = "FAILED"
                doc.processing_error = f"Failed to stage upload for ingestion worker: {e}"
                db.commit()
                return

        if settings.UPLOAD_QUEUE_ENABLED or batch_id:
            from backend.app.services.ingestion import upload_queue
            job = upload_queue.enqueue_upload(
                db, document_id=doc.id, filename=doc.filename,
                batch_id=batch_id, client_item_id=client_item_id
            )
            background_tasks.add_task(upload_queue.dispatch_available)
            logger.info("[UPLOAD] queued for cloud_run_job doc_id=%s (job=%s)", doc.id, job.id)
            return

        try:
            trigger_ingestion_job(doc.id)
            logger.info("[UPLOAD] Cloud Run Job triggered for doc_id=%s", doc.id)
        except IngestionJobTriggerError as e:
            # Fail loudly and visibly rather than leaving the deck stuck at PENDING with no
            # indication why -- same philosophy as the rest of this priority plan's fixes.
            logger.error("[UPLOAD] failed to start ingestion job for doc_id=%s: %s", doc.id, e)
            doc.processing_status = "FAILED"
            doc.processing_error = str(e)
            db.commit()
        return
    if settings.UPLOAD_QUEUE_ENABLED or batch_id:
        from backend.app.services.ingestion import upload_queue
        job = upload_queue.enqueue_upload(
            db, document_id=doc.id, filename=doc.filename,
            batch_id=batch_id, client_item_id=client_item_id
        )
        pos = upload_queue.queue_position(db, job)
        INGESTION_STATUS_REGISTRY[doc.id] = {
            "document_id": doc.id, "status": "QUEUED", "progress_percentage": 0,
            "current_step": f"Queued (position {pos + 1}) — processing one deck at a time",
            "slides_processed": 0, "questions_extracted": 0, "error": None,
        }
        # Kick a drainer so the queue is actually worked off, without relying on the long-lived
        # background worker (which may not run under some deployments). Serial + idempotent: if a
        # drainer is already going, this one exits immediately.
        background_tasks.add_task(upload_queue.drain)
        logger.info("[UPLOAD] queued doc_id=%s (job=%s, position=%d)", doc.id, job.id, pos)
    else:
        INGESTION_STATUS_REGISTRY[doc.id] = {
            "document_id": doc.id, "status": "PROCESSING", "progress_percentage": 5,
            "current_step": "1. Extracting content", "slides_processed": 0,
            "questions_extracted": 0, "error": None,
        }
        background_tasks.add_task(IngestionPipeline().run, doc.id)
        logger.info("[UPLOAD] background ingestion scheduled for doc_id=%s", doc.id)


# Determine environment: Vercel serverless has a hard 4.5 MB request ceiling.
# Off Vercel (Railway, a VM, local dev) the app streams the upload straight to disk,
# so the ceiling is configurable via the MAX_UPLOAD_MB env var (default 2048 MB = 2 GB).
# Both the direct and chunked upload paths enforce this same ceiling.
is_vercel = bool(os.environ.get("VERCEL"))

def _max_upload_bytes() -> int:
    if is_vercel:
        return 4 * 1024 * 1024 + 512 * 1024  # 4.5 MB hard platform limit on Vercel serverless
    try:
        mb = int(os.environ.get("MAX_UPLOAD_MB", "2048"))
    except (TypeError, ValueError):
        mb = 2048
    return max(mb, 1) * 1024 * 1024

MAX_FILE_SIZE_BYTES = _max_upload_bytes()
MAX_FILE_SIZE_MB = MAX_FILE_SIZE_BYTES / (1024 * 1024)


def _max_chunked_upload_bytes() -> int:
    # Chunked uploads slice decks into <= 3.5 MB requests on Vercel, bypassing the
    # per-request 4.5 MB serverless limit; the total assembled file ceiling must
    # therefore follow MAX_UPLOAD_MB rather than Vercel's single-request cap.
    try:
        mb = int(os.environ.get("MAX_UPLOAD_MB", "2048"))
    except (TypeError, ValueError):
        mb = 2048
    return max(mb, 1) * 1024 * 1024


MAX_CHUNKED_FILE_BYTES = _max_chunked_upload_bytes()
MAX_CHUNKED_FILE_MB = MAX_CHUNKED_FILE_BYTES / (1024 * 1024)
R2_MIN_PART_BYTES = 5 * 1024 * 1024  # S3 / R2 requires non-final multipart parts >= 5 MiB


def _chunk_stage_prefix(upload_id: str) -> str:
    return f"raw-chunks/{upload_id}/"


def _chunk_stage_key(upload_id: str, chunk_index: int) -> str:
    return f"raw-chunks/{upload_id}/{chunk_index:05d}"

router = APIRouter(prefix="/documents", tags=["Documents"])

@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    year: Optional[int] = Form(None),
    batch_id: Optional[str] = Form(None),
    client_item_id: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    logger.info(f"[UPLOAD] request received: filename={file.filename}, content_type={file.content_type}")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in [".pptx", ".ppt", ".pdf"]:
        logger.warning(f"[UPLOAD] rejected unsupported extension: {ext}")
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format '{ext}'. Only PDF and PowerPoint (PPT, PPTX) files are supported."
        )

    doc_id = str(uuid.uuid4())
    save_filename = f"{doc_id}{ext}"
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    storage_path = str(settings.UPLOAD_DIR / save_filename)

    logger.info(f"[UPLOAD] storage started: doc_id={doc_id}, dest={storage_path}")

    # Stream file to disk with strict size limit enforcement
    file_size = 0
    try:
        with open(storage_path, "wb") as buffer:
            chunk_size = 64 * 1024  # 64 KB chunks
            while chunk := await file.read(chunk_size):
                file_size += len(chunk)
                if file_size > MAX_FILE_SIZE_BYTES:
                    buffer.close()
                    if os.path.exists(storage_path):
                        os.remove(storage_path)
                    limit_desc = "4.5 MB on Vercel Serverless" if is_vercel else f"{MAX_FILE_SIZE_MB:.0f} MB"
                    logger.warning(f"[UPLOAD] file exceeds size limit: {file_size} > {MAX_FILE_SIZE_BYTES}")
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"File exceeds maximum allowed upload size ({limit_desc}). "
                            f"Received {file_size / (1024 * 1024):.1f} MB. "
                            + ("To upload via Vercel, run 'python scripts/optimize_qshala_deck.py -i <deck.pptx>' "
                               "to strip heavy media, or raise MAX_UPLOAD_MB on the backend and upload directly."
                               if is_vercel else
                               "Raise the MAX_UPLOAD_MB environment variable on the backend to allow a larger file.")
                        )
                    )
                buffer.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[UPLOAD] failed while saving file: {e}")
        if os.path.exists(storage_path):
            try:
                os.remove(storage_path)
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"Failed to save uploaded file: {str(e)}")

    logger.info(f"[UPLOAD] storage completed: doc_id={doc_id}, size={file_size} bytes")
    clean_title = title or os.path.splitext(file.filename)[0].replace("_", " ").title()

    try:
        doc = Document(
            id=doc_id,
            filename=file.filename,
            title=clean_title,
            year=year or datetime.now().year,
            file_type=ext.replace(".", ""),
            storage_path=storage_path,
            file_size_bytes=file_size,
            processing_status="PROCESSING"
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
    except Exception as e:
        logger.exception(f"[UPLOAD] database record creation failed: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Database error while registering document.")

    logger.info(f"[UPLOAD] document record created: id={doc_id}, status=PROCESSING")

    _schedule_ingestion(doc, db, background_tasks, batch_id=batch_id, client_item_id=client_item_id)
    return doc

_MULTIPART_STATE_FILE = "r2_multipart_state.json"


async def _upload_chunk_direct_to_r2(
    chunk_dir, chunk: UploadFile, upload_id: str, chunk_index: int, total_chunks: int,
    ext: str, filename: str, title: Optional[str], year: Optional[int],
    r2, db: Session, background_tasks: BackgroundTasks,
    batch_id: Optional[str] = None, client_item_id: Optional[str] = None,
):
    """The cloud_run_job+R2 path for /upload-chunk using DB-backed upload_sessions table.
    Every slice goes straight into an R2 multipart upload. Serializes concurrent writes
    cleanly with row locking (SELECT ... FOR UPDATE on Postgres).
    """
    from backend.app.models.upload_session import UploadSession
    from backend.app.database import is_postgres

    # Query upload session; lock row on Postgres
    query = db.query(UploadSession).filter(UploadSession.upload_id == upload_id)
    if is_postgres:
        query = query.with_for_update()
    session_rec = query.first()

    # 1. Idempotency: if already COMPLETED, return the created document
    if session_rec and session_rec.status == "COMPLETED":
        doc_id = session_rec.document_id or session_rec.doc_id
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if doc:
            return {
                "status": "COMPLETED",
                "document": {
                    "id": doc.id,
                    "filename": doc.filename,
                    "title": doc.title,
                    "year": doc.year,
                    "file_type": doc.file_type,
                    "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
                    "storage_path": doc.storage_path,
                    "file_size_bytes": doc.file_size_bytes,
                    "slide_count": doc.slide_count,
                    "question_count": doc.question_count,
                    "processing_status": doc.processing_status,
                    "processing_error": doc.processing_error,
                },
            }

    # 2. Check cancellation
    if (session_rec and session_rec.status == "CANCELLED") or upload_cancel_requested(upload_id):
        if session_rec and session_rec.status != "CANCELLED":
            session_rec.status = "CANCELLED"
            db.commit()
        raise HTTPException(
            status_code=409,
            detail={"code": "upload_cancelled", "detail": "Upload was cancelled."}
        )

    # 3. Missing session
    if not session_rec:
        if chunk_index != 0:
            # Chunk 0 has not yet initialized the session. HTTP 425 informs client to retry with backoff.
            raise HTTPException(
                status_code=425,
                detail={"code": "slice_not_ready", "detail": "Upload session not initialised; retrying slice should succeed"}
            )
        doc_id = str(uuid.uuid4())
        raw_key = f"raw-uploads/{doc_id}{ext}"
        r2_upload_id = await asyncio.to_thread(r2.create_multipart_upload, raw_key)
        session_rec = UploadSession(
            upload_id=upload_id,
            doc_id=doc_id,
            raw_key=raw_key,
            r2_upload_id=r2_upload_id,
            parts={},
            total_bytes=0,
            filename=filename,
            title=title,
            year=year,
            batch_id=batch_id,
            client_item_id=client_item_id,
            status="OPEN",
        )
        db.add(session_rec)
        db.commit()
        db.refresh(session_rec)

    parts_map = copy.deepcopy(session_rec.parts or {})
    staging = parts_map.get("_staging")

    already_stored = (
        (isinstance(staging, dict) and str(chunk_index) in (staging.get("received") or {}))
        or (staging is None and str(chunk_index + 1) in parts_map)
    )
    if already_stored:
        received_count = len(staging.get("received") or {}) if isinstance(staging, dict) else len(parts_map)
        return {
            "status": "CHUNK_RECEIVED",
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "progress_percentage": round((received_count / max(total_chunks, 1)) * 100),
        }

    body = await chunk.read()
    new_total = (session_rec.total_bytes or 0) + len(body)
    if new_total > MAX_CHUNKED_FILE_BYTES:
        try:
            r2.abort_multipart_upload(session_rec.raw_key, session_rec.r2_upload_id)
        except Exception:
            pass
        if isinstance(staging, dict):
            try:
                await asyncio.to_thread(r2.delete_prefix, _chunk_stage_prefix(upload_id))
            except Exception:
                pass
        session_rec.status = "CANCELLED"
        db.commit()
        remove_chunk_dir(upload_id)
        raise HTTPException(
            status_code=413,
            detail=(f"Upload ({new_total / (1024 * 1024):.1f} MB so far) exceeds the maximum "
                    f"upload size ({MAX_CHUNKED_FILE_MB:.0f} MB). Raise MAX_UPLOAD_MB on the backend."),
        )

    is_small_nonfinal = len(body) < R2_MIN_PART_BYTES and chunk_index < (total_chunks - 1)
    if staging is None and not is_small_nonfinal:
        # Fast-path: slice >= 5 MiB (or sole/final slice) goes straight into R2 multipart
        etag = await asyncio.to_thread(r2.upload_part, session_rec.raw_key, session_rec.r2_upload_id, chunk_index + 1, body)
        parts_map[str(chunk_index + 1)] = etag
        session_rec.parts = parts_map
        session_rec.total_bytes = new_total
        session_rec.updated_at = datetime.now(timezone.utc)
        db.commit()
    else:
        # Stage < 5 MiB slices in R2 under raw-chunks/{upload_id}/ and flush contiguous >= 5 MiB parts
        if staging is None:
            fast_parts = sorted(int(k) for k in parts_map.keys() if k.isdigit())
            contiguous_fast = 0
            for pn in fast_parts:
                if pn == contiguous_fast + 1:
                    contiguous_fast += 1
                else:
                    break
            staging = {
                "received": {str(idx): 0 for idx in range(contiguous_fast)},
                "next_flush_idx": contiguous_fast,
                "next_part_num": contiguous_fast + 1,
            }

        received_map = dict(staging.get("received") or {})
        received_map[str(chunk_index)] = len(body)
        next_flush_idx = int(staging.get("next_flush_idx", 0))
        next_part_num = int(staging.get("next_part_num", 1))
        all_received = (len(received_map) == total_chunks)

        batches_to_flush = []
        scan_idx = next_flush_idx
        while scan_idx < total_chunks and str(scan_idx) in received_map:
            batch_indices = []
            run_bytes = 0
            end_scan = scan_idx
            while end_scan < total_chunks and str(end_scan) in received_map:
                batch_indices.append(end_scan)
                run_bytes += int(received_map[str(end_scan)])
                end_scan += 1
                if run_bytes >= R2_MIN_PART_BYTES:
                    break
            can_flush_tail = all_received and (end_scan == total_chunks)
            if run_bytes < R2_MIN_PART_BYTES and not can_flush_tail:
                break
            batches_to_flush.append(batch_indices)
            scan_idx = end_scan

        flushed_indices = {idx for batch in batches_to_flush for idx in batch}
        if chunk_index not in flushed_indices:
            await asyncio.to_thread(r2.upload_bytes, _chunk_stage_key(upload_id, chunk_index), body)

        for batch_indices in batches_to_flush:
            buf_parts = []
            keys_to_delete = []
            for idx in batch_indices:
                if idx == chunk_index:
                    buf_parts.append(body)
                else:
                    skey = _chunk_stage_key(upload_id, idx)
                    part_bytes = await asyncio.to_thread(r2.download, skey)
                    buf_parts.append(part_bytes)
                    keys_to_delete.append(skey)
            merged_part = buf_parts[0] if len(buf_parts) == 1 else b"".join(buf_parts)
            etag = await asyncio.to_thread(
                r2.upload_part, session_rec.raw_key, session_rec.r2_upload_id, next_part_num, merged_part
            )
            parts_map[str(next_part_num)] = etag
            next_part_num += 1
            next_flush_idx = batch_indices[-1] + 1
            if keys_to_delete:
                try:
                    await asyncio.to_thread(r2.delete, keys_to_delete)
                except Exception:
                    pass

        staging["received"] = received_map
        staging["next_flush_idx"] = next_flush_idx
        staging["next_part_num"] = next_part_num
        parts_map["_staging"] = staging
        session_rec.parts = parts_map
        session_rec.total_bytes = new_total
        session_rec.updated_at = datetime.now(timezone.utc)
        db.commit()

    if (session_rec.status == "CANCELLED") or upload_cancel_requested(upload_id):
        try:
            r2.abort_multipart_upload(session_rec.raw_key, session_rec.r2_upload_id)
        except Exception:
            pass
        if isinstance(parts_map.get("_staging"), dict):
            try:
                await asyncio.to_thread(r2.delete_prefix, _chunk_stage_prefix(upload_id))
            except Exception:
                pass
        session_rec.status = "CANCELLED"
        db.commit()
        remove_chunk_dir(upload_id)
        raise HTTPException(status_code=409, detail={"code": "upload_cancelled", "detail": "Upload was cancelled."})

    staging_state = parts_map.get("_staging")
    if isinstance(staging_state, dict):
        received_count = len(staging_state.get("received") or {})
        is_complete = (
            received_count == total_chunks
            and int(staging_state.get("next_flush_idx", 0)) == total_chunks
        )
    else:
        received_count = len(parts_map)
        is_complete = (received_count >= total_chunks)

    if not is_complete:
        return {
            "status": "CHUNK_RECEIVED",
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "progress_percentage": round((received_count / max(total_chunks, 1)) * 100),
        }

    if isinstance(staging_state, dict):
        try:
            await asyncio.to_thread(r2.delete_prefix, _chunk_stage_prefix(upload_id))
        except Exception as e:
            logger.warning(f"[UPLOAD-CHUNK] failed to cleanup staged chunks for {upload_id}: {e}")

    # All parts received -- finish multipart upload
    sorted_parts = [
        {"PartNumber": int(pn), "ETag": tag}
        for pn, tag in sorted(((k, v) for k, v in parts_map.items() if k.isdigit()), key=lambda x: int(x[0]))
    ]
    await asyncio.to_thread(r2.complete_multipart_upload, session_rec.raw_key, session_rec.r2_upload_id, sorted_parts)

    doc_id = session_rec.doc_id
    save_filename = f"{doc_id}{ext}"
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    storage_path = str(settings.UPLOAD_DIR / save_filename)

    try:
        remove_chunk_dir(upload_id)
    except Exception as e:
        logger.warning(f"[UPLOAD-CHUNK] failed to remove temporary state dir {chunk_dir}: {e}")

    clean_title = session_rec.title or os.path.splitext(session_rec.filename)[0].replace("_", " ").title()

    try:
        doc = Document(
            id=doc_id,
            filename=session_rec.filename,
            title=clean_title,
            year=session_rec.year or datetime.now().year,
            file_type=ext.replace(".", ""),
            storage_path=storage_path,
            file_size_bytes=session_rec.total_bytes,
            processing_status="PROCESSING",
        )
        db.add(doc)
        session_rec.status = "COMPLETED"
        session_rec.document_id = doc_id
        db.commit()
        db.refresh(doc)
    except Exception as e:
        logger.exception(f"[UPLOAD-CHUNK] database record creation failed: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Database error while registering document.")

    logger.info(f"[UPLOAD-CHUNK] file streamed directly to R2: doc_id={doc_id} key={session_rec.raw_key}")

    _schedule_ingestion(
        doc, db, background_tasks, raw_already_in_r2=True,
        batch_id=session_rec.batch_id, client_item_id=session_rec.client_item_id
    )

    return {
        "status": "COMPLETED",
        "document": {
            "id": doc.id,
            "filename": doc.filename,
            "title": doc.title,
            "year": doc.year,
            "file_type": doc.file_type,
            "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
            "storage_path": doc.storage_path,
            "file_size_bytes": doc.file_size_bytes,
            "slide_count": doc.slide_count,
            "question_count": doc.question_count,
            "processing_status": doc.processing_status,
            "processing_error": doc.processing_error,
        },
    }

@router.post("/upload-chunk")
async def upload_document_chunk(
    background_tasks: BackgroundTasks,
    chunk: UploadFile = File(...),
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    filename: str = Form(...),
    title: Optional[str] = Form(None),
    year: Optional[int] = Form(None),
    batch_id: Optional[str] = Form(None),
    client_item_id: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """
    Receives one slice of a presentation file (the frontend sends 20 MB slices).
    When all slices are received, stitches the complete file together and starts ingestion.
    A cancelled upload_id (see DELETE /upload-chunk/{upload_id}) is refused with 409.
    """
    if not valid_upload_id(upload_id):
        raise HTTPException(status_code=400, detail="Invalid upload_id.")
    if upload_cancel_requested(upload_id):
        remove_chunk_dir(upload_id)
        raise HTTPException(status_code=409, detail={"code": "upload_cancelled", "detail": "Upload was cancelled."})

    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".pptx", ".ppt", ".pdf"]:
        logger.warning(f"[UPLOAD-CHUNK] rejected unsupported extension: {ext}")
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format '{ext}'. Only PDF and PowerPoint (PPT, PPTX) files are supported."
        )

    # When ingestion runs on a separate Cloud Run Job and R2 is configured, stream each slice
    # straight into an R2 multipart upload as it arrives instead of writing it to local disk and
    # stitching a full copy afterward -- the worker already fetches the raw deck from R2 (see
    # _fetch_raw_deck in worker.py), so a local copy here is pure overhead that mode never needs.
    # Every other mode (in_process, the upload queue, or R2 not configured) falls through to the
    # original local-disk path below completely untouched.
    from backend.app.services.storage.r2_storage import get_r2_service
    r2 = get_r2_service()
    if settings.INGESTION_MODE == "cloud_run_job" and r2.is_configured:
        try:
            return await _upload_chunk_direct_to_r2(
                chunk_dir=None, chunk=chunk, upload_id=upload_id,
                chunk_index=chunk_index, total_chunks=total_chunks,
                ext=ext, filename=filename, title=title, year=year,
                r2=r2, db=db, background_tasks=background_tasks,
                batch_id=batch_id, client_item_id=client_item_id,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("[UPLOAD-CHUNK] direct to R2 failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Direct R2 chunk upload error: {type(e).__name__}: {str(e)}")

    chunk_dir = settings.STORAGE_DIR / "tmp_chunks" / upload_id
    chunk_dir.mkdir(parents=True, exist_ok=True)

    if chunk_index == 0:
        meta_path = chunk_dir / "meta.json"
        try:
            meta_path.write_text(json.dumps({"batch_id": batch_id, "client_item_id": client_item_id}))
        except Exception:
            pass

    chunk_path = chunk_dir / f"chunk_{chunk_index:05d}.part"

    # Stream chunk to disk
    with open(chunk_path, "wb") as f:
        while content := await chunk.read(64 * 1024):
            f.write(content)

    # A cancel may have landed while this slice was still streaming in: drop everything.
    if upload_cancel_requested(upload_id):
        remove_chunk_dir(upload_id)
        raise HTTPException(status_code=409, detail={"code": "upload_cancelled", "detail": "Upload was cancelled."})

    parts = list(chunk_dir.glob("chunk_*.part"))
    if len(parts) < total_chunks:
        return {
            "status": "CHUNK_RECEIVED",
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "progress_percentage": round(((len(parts)) / total_chunks) * 100)
        }

    # All chunks received! Stitch them together
    doc_id = str(uuid.uuid4())
    save_filename = f"{doc_id}{ext}"
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    storage_path = str(settings.UPLOAD_DIR / save_filename)

    def _stitch() -> int:
        total = 0
        with open(storage_path, "wb") as outfile:
            for part_file in sorted(chunk_dir.glob("chunk_*.part")):
                with open(part_file, "rb") as infile:
                    shutil.copyfileobj(infile, outfile)
                total += os.path.getsize(part_file)
        return total

    # Joining up to hundreds of MB of slices is blocking disk work: keep it off the event loop.
    file_size = await asyncio.to_thread(_stitch)

    # Enforce the same ceiling as the direct path (chunked bypasses Vercel's limit but
    # must still respect MAX_UPLOAD_MB so a runaway upload can't fill the disk).
    if file_size > MAX_CHUNKED_FILE_BYTES:
        for p in (storage_path,):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        try:
            shutil.rmtree(chunk_dir)
        except Exception:
            pass
        raise HTTPException(
            status_code=413,
            detail=(f"Reassembled file ({file_size / (1024 * 1024):.1f} MB) exceeds the maximum "
                    f"upload size ({MAX_CHUNKED_FILE_MB:.0f} MB). Raise MAX_UPLOAD_MB on the backend.")
        )

    saved_batch_id = None
    saved_client_item_id = None
    meta_path = chunk_dir / "meta.json"
    if meta_path.exists():
        try:
            m = json.loads(meta_path.read_text())
            saved_batch_id = m.get("batch_id")
            saved_client_item_id = m.get("client_item_id")
        except Exception:
            pass

    # Clean up chunk directory
    try:
        shutil.rmtree(chunk_dir)
    except Exception as e:
        logger.warning(f"[UPLOAD-CHUNK] failed to remove temporary chunk directory {chunk_dir}: {e}")

    clean_title = title or os.path.splitext(filename)[0].replace("_", " ").title()

    try:
        doc = Document(
            id=doc_id,
            filename=filename,
            title=clean_title,
            year=year or datetime.now().year,
            file_type=ext.replace(".", ""),
            storage_path=storage_path,
            file_size_bytes=file_size,
            processing_status="PROCESSING"
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
    except Exception as e:
        logger.exception(f"[UPLOAD-CHUNK] database record creation failed: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Database error while registering document.")

    logger.info(f"[UPLOAD-CHUNK] file reassembled successfully: doc_id={doc_id}, size={file_size} bytes")

    _schedule_ingestion(
        doc, db, background_tasks,
        batch_id=saved_batch_id, client_item_id=saved_client_item_id
    )

    return {
        "status": "COMPLETED",
        "document": {
            "id": doc.id,
            "filename": doc.filename,
            "title": doc.title,
            "year": doc.year,
            "file_type": doc.file_type,
            "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
            "storage_path": doc.storage_path,
            "file_size_bytes": doc.file_size_bytes,
            "slide_count": doc.slide_count,
            "question_count": doc.question_count,
            "processing_status": doc.processing_status,
            "processing_error": doc.processing_error
        }
    }

@router.delete("/upload-chunk/{upload_id}")
def cancel_chunked_upload(upload_id: str, db: Session = Depends(get_db)):
    """Cancel an in-progress chunked upload: refuse any further slices and delete what arrived."""
    if not valid_upload_id(upload_id):
        raise HTTPException(status_code=400, detail="Invalid upload_id.")
    request_upload_cancel(upload_id)

    # If this upload was streaming straight into an R2 multipart upload (cloud_run_job mode),
    # abort that too -- otherwise the parts already received linger in R2 as billable incomplete
    # storage forever, since nothing else ever completes or expires them.
    from backend.app.models.upload_session import UploadSession
    from backend.app.services.storage.r2_storage import get_r2_service
    session_rec = db.query(UploadSession).filter(UploadSession.upload_id == upload_id).first()
    if session_rec:
        if session_rec.status == "OPEN" and session_rec.r2_upload_id:
            try:
                r2 = get_r2_service()
                r2.abort_multipart_upload(session_rec.raw_key, session_rec.r2_upload_id)
                r2.delete_prefix(_chunk_stage_prefix(upload_id))
            except Exception as e:
                logger.warning(f"[UPLOAD-CHUNK] failed to abort R2 multipart upload for {upload_id}: {e}")
        session_rec.status = "CANCELLED"
        db.commit()

    remove_chunk_dir(upload_id)
    logger.info(f"[UPLOAD-CHUNK] upload {upload_id} cancelled by user, slices removed")
    return {"status": "CANCELLED", "upload_id": upload_id}

@router.post("/{document_id}/cancel")
def cancel_ingestion(document_id: str, db: Session = Depends(get_db)):
    """
    Cancel ingestion of a document that is still processing. The pipeline stops at its next
    checkpoint (between slides, between questions, before each stage) and then deletes the
    document row, the stored deck, extracted media and R2 objects.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        return {"status": "CANCELLED", "detail": "Document already removed."}
    if doc.processing_status == "COMPLETED":
        raise HTTPException(status_code=409, detail="Ingestion already completed; delete the document instead.")

    request_doc_cancel(document_id)
    # Also set the DB flag: if ingestion is running in a separate worker process (Cloud Run Job
    # mode), the in-memory CANCELLED_DOCS set above is invisible to it -- the worker's cancel
    # poller picks this up instead. Harmless no-op for the in-process path.
    doc.cancel_requested = True
    db.commit()
    reg = INGESTION_STATUS_REGISTRY.get(document_id)
    if reg and reg.get("status") == "PROCESSING":
        reg["current_step"] = "Cancelling..."
        return {"status": "CANCELLING", "document_id": document_id}

    # Cloud Run Job mode: this process never has a registry entry, because the pipeline runs in the worker's
    # container. If that worker is alive (it refreshes its job row every few seconds) purging here would delete
    # the document and its media out from under it: it would keep calling Gemini, then fail on the missing
    # row and leave orphaned uploads. Leave cancel_requested set; the worker's poller sees it, stops at its next
    # checkpoint and purges the document itself.
    if worker_is_alive(db, document_id):
        return {"status": "CANCELLING", "document_id": document_id}

    # Nothing is actively running for this id (e.g. server restarted mid-ingest): clean up now.
    purge_document(db, document_id)
    INGESTION_STATUS_REGISTRY[document_id] = {
        "document_id": document_id, "status": "CANCELLED", "progress_percentage": 0,
        "current_step": "Upload cancelled", "slides_processed": 0, "questions_extracted": 0, "error": None,
    }
    return {"status": "CANCELLED", "document_id": document_id}

@router.get("", response_model=List[DocumentListItem])
def list_documents(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return (db.query(Document)
            .options(noload(Document.slides))
            .order_by(Document.created_at.desc())
            .offset(offset).limit(limit)
            .all())

@router.get("/sources")
def list_document_sources(db: Session = Depends(get_db)):
    """Return uploaded decks grouped by source series alongside flat deck entries for pickers."""
    from backend.app.services.source_grouping import build_source_catalog

    docs = (
        db.query(
            Document.id,
            Document.title,
            Document.filename,
            Document.year,
            Document.question_count,
            Document.processing_status,
        )
        .order_by(Document.created_at.desc())
        .all()
    )
    rows = [
        {
            "id": d.id,
            "title": d.title,
            "filename": d.filename,
            "year": d.year,
            "question_count": d.question_count or 0,
            "processing_status": d.processing_status,
        }
        for d in docs
    ]
    return build_source_catalog(rows)

@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(document_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc

@router.get("/{document_id}/slides", response_model=List[SlideResponse])
def get_document_slides(document_id: str, db: Session = Depends(get_db)):
    slides = db.query(Slide).filter(Slide.document_id == document_id).order_by(Slide.slide_number.asc()).all()
    return slides

@router.get("/{document_id}/status", response_model=IngestionStatusResponse)
def get_ingestion_status(document_id: str, db: Session = Depends(get_db)):
    if document_id in INGESTION_STATUS_REGISTRY:
        from backend.app.services.ingestion import progress as _progress
        _progress.update(INGESTION_STATUS_REGISTRY[document_id])   # fresh memory reading on every poll
        return INGESTION_STATUS_REGISTRY[document_id]

    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # doc.progress_detail is the cross-process mirror worker.py writes every few seconds under
    # INGESTION_MODE=cloud_run_job (see worker.py's _sync_progress_to_db) -- a periodic snapshot
    # of the same live detail/activity-log/server-memory registry entry that pipeline.py writes
    # continuously, which this process can only read directly when ingestion runs in its own
    # memory (the INGESTION_STATUS_REGISTRY fast path above). Falls back to the coarse
    # PROCESSING/COMPLETED/FAILED-only view when no snapshot has landed yet (e.g. right after the
    # job was triggered, before its first sync tick).
    pd = doc.progress_detail or {}
    return {
        "document_id": doc.id,
        "status": doc.processing_status,
        "progress_percentage": pd.get(
            "progress_percentage", 100 if doc.processing_status == "COMPLETED" else 0
        ),
        "current_step": pd.get("current_step") or (
            "Completed" if doc.processing_status == "COMPLETED" else doc.processing_status
        ),
        "slides_processed": doc.slide_count,
        "questions_extracted": doc.question_count,
        "error": doc.processing_error or pd.get("error"),
        "detail": pd.get("detail"),
        "events": pd.get("events"),
        "memory_mb": pd.get("memory_mb"),
        "peak_memory_mb": pd.get("peak_memory_mb"),
        "memory_limit_mb": pd.get("memory_limit_mb"),
    }

@router.delete("/{document_id}")
def delete_document(document_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    reg = INGESTION_STATUS_REGISTRY.get(document_id)
    if reg and reg.get("status") == "PROCESSING":
        request_doc_cancel(document_id)  # stop the running pipeline instead of orphaning it

    # A Cloud Run Job worker in another container owns this deck: deleting the rows now would pull them out from
    # under it. Flag it; the worker stops at its next checkpoint and removes the document itself.
    if worker_is_alive(db, document_id):
        request_doc_cancel(document_id)
        doc.cancel_requested = True
        db.commit()
        return {"message": "Deletion requested: the running worker is being stopped and will remove the document"}

    if os.path.exists(doc.storage_path):
        try:
            os.remove(doc.storage_path)
        except Exception:
            pass

    from backend.app.services.question_meta_service import remove_repeats_for_document
    remove_repeats_for_document(db, document_id)  # other questions stop listing this deck as a repeat
    db.delete(doc)
    db.commit()
    return {"message": "Document deleted successfully"}


@router.post("/{document_id}/repair")
def repair_document_extractions(document_id: str, dry_run: bool = False, db: Session = Depends(get_db)):
    """Idempotently repair broken bilingual / bumper / List-It / Connect extractions for one document."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    from backend.app.services.ingestion.repair_bilingual import repair_broken_deck_extractions
    summary = repair_broken_deck_extractions(db, dry_run=dry_run, document_id=document_id)
    return {"document_id": document_id, "dry_run": dry_run, "summary": summary}
