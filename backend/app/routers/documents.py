import asyncio
import logging
import os
import shutil
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.orm import Session
from backend.app.database import get_db, is_postgres
from backend.app.models.document import Document, Slide
from backend.app.schemas.document import DocumentResponse, IngestionStatusResponse, SlideResponse
from backend.app.services.ingestion.pipeline import IngestionPipeline, INGESTION_STATUS_REGISTRY
from backend.app.config import settings
from backend.app.services.ingestion.cancellation import (
    valid_upload_id,
    request_upload_cancel,
    upload_cancel_requested,
    remove_chunk_dir,
    request_doc_cancel,
    purge_document,
)

logger = logging.getLogger(__name__)

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

router = APIRouter(prefix="/documents", tags=["Documents"])


def _dispatch_ingestion(db: Session, doc_id: str) -> None:
    """Starts ingestion for a freshly-uploaded document.

    On Postgres (production) the document is simply left PENDING: a separate
    ingestion worker process (backend/worker.py) polls for PENDING documents
    and runs the pipeline there, so a big deck's memory/CPU use never shares
    a process with the one serving search traffic. The worker reports its
    own progress and watches for cancellation through the documents table
    (progress_percentage, current_step, cancel_requested) since it has no
    visibility into this process's in-memory registry/cancel set.

    On SQLite (local/dev) there is no separate worker to run locally, so
    ingestion still runs in this process, same as it always has.
    """
    if is_postgres:
        logger.info(f"[UPLOAD] document queued for the ingestion worker: id={doc_id}, status=PENDING")
        return

    INGESTION_STATUS_REGISTRY[doc_id] = {
        "document_id": doc_id,
        "status": "PROCESSING",
        "progress_percentage": 5,
        "current_step": "1. Extracting content",
        "slides_processed": 0,
        "questions_extracted": 0,
        "error": None
    }
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if doc:
        doc.processing_status = "PROCESSING"
        db.commit()

    pipeline = IngestionPipeline()
    asyncio.create_task(pipeline.run(doc_id))
    logger.info(f"[UPLOAD] ingestion started in-process (local/dev, no pgvector) for doc_id={doc_id}")

@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    year: Optional[int] = Form(None),
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
            year=year or 2024,
            file_type=ext.replace(".", ""),
            storage_path=storage_path,
            file_size_bytes=file_size,
            processing_status="PENDING"
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
    except Exception as e:
        logger.exception(f"[UPLOAD] database record creation failed: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Database error while registering document.")

    logger.info(f"[UPLOAD] document record created: id={doc_id}, status=PENDING")
    _dispatch_ingestion(db, doc_id)

    return doc

@router.post("/upload-chunk")
async def upload_document_chunk(
    chunk: UploadFile = File(...),
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    filename: str = Form(...),
    title: Optional[str] = Form(None),
    year: Optional[int] = Form(None),
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
        raise HTTPException(status_code=409, detail="Upload was cancelled.")

    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".pptx", ".ppt", ".pdf"]:
        logger.warning(f"[UPLOAD-CHUNK] rejected unsupported extension: {ext}")
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format '{ext}'. Only PDF and PowerPoint (PPT, PPTX) files are supported."
        )

    chunk_dir = settings.STORAGE_DIR / "tmp_chunks" / upload_id
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunk_path = chunk_dir / f"chunk_{chunk_index:05d}.part"

    # Stream chunk to disk
    with open(chunk_path, "wb") as f:
        while content := await chunk.read(64 * 1024):
            f.write(content)

    # A cancel may have landed while this slice was still streaming in: drop everything.
    if upload_cancel_requested(upload_id):
        remove_chunk_dir(upload_id)
        raise HTTPException(status_code=409, detail="Upload was cancelled.")

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

    file_size = 0
    with open(storage_path, "wb") as outfile:
        for part_file in sorted(chunk_dir.glob("chunk_*.part")):
            with open(part_file, "rb") as infile:
                shutil.copyfileobj(infile, outfile)
            file_size += os.path.getsize(part_file)

    # Enforce the same ceiling as the direct path (chunked bypasses Vercel's limit but
    # must still respect MAX_UPLOAD_MB so a runaway upload can't fill the disk).
    if file_size > MAX_FILE_SIZE_BYTES:
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
                    f"upload size ({MAX_FILE_SIZE_MB:.0f} MB). Raise MAX_UPLOAD_MB on the backend.")
        )

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
            year=year or 2024,
            file_type=ext.replace(".", ""),
            storage_path=storage_path,
            file_size_bytes=file_size,
            processing_status="PENDING"
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
    except Exception as e:
        logger.exception(f"[UPLOAD-CHUNK] database record creation failed: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Database error while registering document.")

    logger.info(f"[UPLOAD-CHUNK] file reassembled successfully: doc_id={doc_id}, size={file_size} bytes")
    _dispatch_ingestion(db, doc_id)

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
def cancel_chunked_upload(upload_id: str):
    """Cancel an in-progress chunked upload: refuse any further slices and delete what arrived."""
    if not valid_upload_id(upload_id):
        raise HTTPException(status_code=400, detail="Invalid upload_id.")
    request_upload_cancel(upload_id)
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

    if is_postgres:
        # The pipeline may be running in a separate worker process, which has
        # no visibility into this process's in-memory registry -- so absence
        # from the registry no longer means "nothing is running" the way it
        # does in the single-process (local/dev) case below. There's no
        # reliable way from here to tell "actively running in the worker"
        # apart from "genuinely orphaned", so an active-looking row is always
        # just flagged and left for whichever process owns it (if any) to
        # notice and clean up at its next checkpoint -- same as a same-process
        # cancel already does. A row stuck from a crashed worker (rare) won't
        # self-heal this way; deleting it directly still works unconditionally.
        if doc.processing_status in ("PENDING", "CLAIMED", "PROCESSING"):
            doc.cancel_requested = True
            db.commit()
            return {"status": "CANCELLING", "document_id": document_id}
        purge_document(db, document_id)
        INGESTION_STATUS_REGISTRY[document_id] = {
            "document_id": document_id, "status": "CANCELLED", "progress_percentage": 0,
            "current_step": "Upload cancelled", "slides_processed": 0, "questions_extracted": 0, "error": None,
        }
        return {"status": "CANCELLED", "document_id": document_id}

    reg = INGESTION_STATUS_REGISTRY.get(document_id)
    if reg and reg.get("status") == "PROCESSING":
        reg["current_step"] = "Cancelling..."
        return {"status": "CANCELLING", "document_id": document_id}

    # Nothing is actively running for this id (e.g. server restarted mid-ingest): clean up now.
    purge_document(db, document_id)
    INGESTION_STATUS_REGISTRY[document_id] = {
        "document_id": document_id, "status": "CANCELLED", "progress_percentage": 0,
        "current_step": "Upload cancelled", "slides_processed": 0, "questions_extracted": 0, "error": None,
    }
    return {"status": "CANCELLED", "document_id": document_id}

@router.get("", response_model=List[DocumentResponse])
def list_documents(db: Session = Depends(get_db)):
    return db.query(Document).order_by(Document.created_at.desc()).all()

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

    # The pipeline may be running in a separate worker process (production,
    # Postgres), which has no way to write into this process's in-memory
    # registry -- these columns are what it reports progress through instead.
    default_pct = 100 if doc.processing_status == "COMPLETED" else 0
    default_step = "Completed" if doc.processing_status == "COMPLETED" else doc.processing_status
    return {
        "document_id": doc.id,
        "status": doc.processing_status,
        "progress_percentage": doc.progress_percentage if doc.progress_percentage is not None else default_pct,
        "current_step": doc.current_step or default_step,
        "slides_processed": doc.slide_count,
        "questions_extracted": doc.question_count,
        "error": doc.processing_error
    }

@router.delete("/{document_id}")
def delete_document(document_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # The pipeline may be running in this process (local/dev) or in a
    # separate worker (production) -- request_doc_cancel() covers the former,
    # the cancel_requested column (polled by the worker) covers the latter.
    if doc.processing_status in ("PENDING", "CLAIMED", "PROCESSING"):
        request_doc_cancel(document_id)  # stops a same-process pipeline instead of orphaning it
        doc.cancel_requested = True
        db.commit()

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
