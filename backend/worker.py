"""Ingestion worker: runs document ingestion in its own process, separate
from the web process that serves the API and search traffic.

Why this exists: FastAPI's BackgroundTasks used to run the ingestion
pipeline inside the same process (and event loop) handling search requests.
A big, image-heavy deck can use a lot of memory and CPU while it ingests --
sharing a process meant that could slow down or, on a tight memory ceiling,
crash the same process serving everyone else's search queries. This worker
polls for documents left in "PENDING" status by the upload endpoints and
processes them one at a time, in complete isolation from the web process.

Deploy this as a second service pointing at the same repo/branch as the web
service, with the start command:

    python -m backend.worker

It needs the same environment variables as the web service (DATABASE_URL,
GEMINI_API_KEY, R2/blob storage settings, etc.) -- it uses the same
database and the same ingestion pipeline code, just in its own process.

Only runs the queue-based path on Postgres. On SQLite (no DATABASE_URL set,
i.e. local/dev) the web process still ingests in-process as before, so
there's nothing for this worker to do locally -- it idles harmlessly if you
run it there anyway.
"""
import asyncio
import logging
import time

from sqlalchemy import text

from backend.app.database import SessionLocal, is_postgres
from backend.app.models.document import Document
from backend.app.services.ingestion.pipeline import IngestionPipeline, INGESTION_STATUS_REGISTRY
from backend.app.services.ingestion.cancellation import request_doc_cancel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [worker] %(levelname)s: %(message)s")
logger = logging.getLogger("ingestion_worker")

POLL_INTERVAL_SECONDS = 5
WATCH_INTERVAL_SECONDS = 2  # how often an in-flight job's progress/cancel flag is checked
IDLE_HEARTBEAT_EVERY = 24  # ~every 2 minutes of idling (POLL_INTERVAL_SECONDS * 24)


def _claim_next_document() -> "str | None":
    """Atomically claims the oldest PENDING document.

    Postgres: SELECT ... FOR UPDATE SKIP LOCKED makes this safe even if more
    than one worker instance is ever run at once -- each claims a different
    row, nothing is double-processed. SQLite has no such locking story, but
    the queue-based path only runs on Postgres anyway (see module docstring),
    so the plain fallback below is just for local experimentation.
    """
    db = SessionLocal()
    try:
        if is_postgres:
            row = db.execute(text(
                "UPDATE documents SET processing_status = 'CLAIMED' "
                "WHERE id = ("
                "  SELECT id FROM documents WHERE processing_status = 'PENDING' "
                "  ORDER BY uploaded_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED"
                ") RETURNING id"
            )).fetchone()
            db.commit()
            return row[0] if row else None
        else:
            doc = db.query(Document).filter(Document.processing_status == "PENDING") \
                .order_by(Document.uploaded_at.asc()).first()
            if not doc:
                return None
            doc.processing_status = "CLAIMED"
            db.commit()
            return doc.id
    except Exception:
        logger.exception("failed while claiming the next document; will retry")
        db.rollback()
        return None
    finally:
        db.close()


async def _watch_job(document_id: str, stop_event: asyncio.Event) -> None:
    """While a job runs, mirrors its progress into the documents table and
    relays any cancellation request from the database into this process's
    in-memory cancel set.

    The pipeline (running in this same process, via IngestionPipeline.run)
    writes live progress into INGESTION_STATUS_REGISTRY and checks
    request_doc_cancel()/doc_cancel_requested() -- both are plain in-process
    state. The web process has no visibility into either, so this bridges
    them through the one thing both processes share: the database.
    """
    last_mirrored = (None, None)
    while not stop_event.is_set():
        reg = INGESTION_STATUS_REGISTRY.get(document_id)
        db = SessionLocal()
        try:
            if reg:
                pct = reg.get("progress_percentage")
                step = reg.get("current_step")
                if (pct, step) != last_mirrored:
                    db.execute(
                        text("UPDATE documents SET progress_percentage = :pct, current_step = :step WHERE id = :id"),
                        {"pct": pct, "step": step, "id": document_id},
                    )
                    db.commit()
                    last_mirrored = (pct, step)

            cancelled = db.execute(
                text("SELECT cancel_requested FROM documents WHERE id = :id"),
                {"id": document_id},
            ).scalar()
            if cancelled:
                request_doc_cancel(document_id)
        except Exception:
            logger.exception(f"progress/cancel watch failed for {document_id} (continuing)")
            db.rollback()
        finally:
            db.close()

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=WATCH_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


async def _run_one(document_id: str) -> None:
    logger.info(f"picked up document {document_id}")
    start = time.monotonic()

    stop_event = asyncio.Event()
    watch_task = asyncio.create_task(_watch_job(document_id, stop_event))

    pipeline = IngestionPipeline()
    try:
        await pipeline.run(document_id)
        logger.info(f"finished document {document_id} in {time.monotonic() - start:.1f}s")
    except Exception:
        logger.exception(f"ingestion crashed for document {document_id}")
        # Best-effort: don't leave it stuck as CLAIMED/PROCESSING forever if
        # the pipeline's own error handling didn't get a chance to run.
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == document_id).first()
            if doc and doc.processing_status not in ("COMPLETED", "FAILED", "CANCELLED"):
                doc.processing_status = "FAILED"
                doc.processing_error = "The ingestion worker crashed while processing this document."
                db.commit()
        except Exception:
            logger.exception(f"also failed to mark {document_id} as FAILED after the crash")
            db.rollback()
        finally:
            db.close()
    finally:
        stop_event.set()
        await watch_task


async def main() -> None:
    logger.info(f"starting, polling every {POLL_INTERVAL_SECONDS}s (is_postgres={is_postgres})")
    idle_ticks = 0
    while True:
        try:
            document_id = _claim_next_document()
        except Exception:
            logger.exception("unexpected error in the poll loop; backing off")
            document_id = None

        if document_id:
            idle_ticks = 0
            await _run_one(document_id)
            continue

        idle_ticks += 1
        if idle_ticks % IDLE_HEARTBEAT_EVERY == 0:
            logger.info("idle, waiting for uploads...")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
