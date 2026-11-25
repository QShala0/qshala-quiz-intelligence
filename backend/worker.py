"""Standalone ingestion worker (QShala priority plan: ingestion split out of the API monolith).

Runs ONE document's ingestion pipeline to completion, then exits. Meant to run as a Cloud Run
Job execution -- each execution processes exactly one deck, triggered by job_trigger.py from the
API service, which passes which document to process as the DOCUMENT_ID env var (a per-execution
container override, not a static env var on the Job resource itself).

Usage:
    DOCUMENT_ID=<uuid> python -m backend.worker          # how Cloud Run Jobs invokes it
    python -m backend.worker <uuid>                       # CLI arg, for local testing

This does NOT change how the API service processes uploads by default -- INGESTION_MODE stays
"in_process" unless explicitly set to "cloud_run_job" (see config.py and job_trigger.py). This
file is simply unreachable/unused in the default configuration.
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

# Same sys.path bootstrap as main.py, so `backend.app.*` imports resolve when this file is run
# directly (python -m backend.worker) rather than imported from inside the FastAPI app.
import types
_current_file = Path(__file__).resolve()
_backend_dir = _current_file.parent
_project_root = _backend_dir.parent

if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

if "backend" not in sys.modules:
    backend_pkg = types.ModuleType("backend")
    backend_pkg.__path__ = [str(_backend_dir)]
    sys.modules["backend"] = backend_pkg

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("qshala.worker")

CANCEL_POLL_INTERVAL_S = 5.0


async def _poll_cancel(document_id: str, stop_event: asyncio.Event) -> None:
    """Mirrors Document.cancel_requested (set cross-process by the cancel API endpoint) into the
    in-memory CANCELLED_DOCS set that the pipeline's raise_if_cancelled() checks throughout --
    the existing cancellation mechanism, unmodified, just fed from the DB instead of only from
    an in-process API call. Stops once the pipeline finishes (stop_event) or cancellation fires.
    """
    from backend.app.database import SessionLocal
    from backend.app.models.document import Document
    from backend.app.services.ingestion.cancellation import request_doc_cancel

    while not stop_event.is_set():
        try:
            db = SessionLocal()
            try:
                doc = db.query(Document).filter(Document.id == document_id).first()
                if doc is None:
                    # The row is gone (cancelled or deleted from the API side): nothing left to ingest into.
                    logger.info("[WORKER] doc_id=%s no longer exists, stopping", document_id)
                    request_doc_cancel(document_id)
                    return
                if doc.cancel_requested:
                    logger.info("[WORKER] cancel_requested detected for doc_id=%s", document_id)
                    request_doc_cancel(document_id)
                    return
            finally:
                db.close()
        except Exception as e:
            logger.warning("[WORKER] cancel poll failed (continuing): %s", e)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=CANCEL_POLL_INTERVAL_S)
        except asyncio.TimeoutError:
            pass


PROGRESS_SYNC_INTERVAL_S = 2.0


def _sync_progress_once(document_id: str) -> None:
    """Copies the pipeline's live in-process progress snapshot (current step, % done, the
    activity log, this container's memory use) out to Document.progress_detail, so the API
    process -- which can't see this worker container's own memory any more than it can see the
    worker's filesystem (see the R2 raw-upload fix) -- has something to hand back from /status
    besides the coarse PROCESSING/COMPLETED/FAILED column. The upload page already renders all
    of this (detail, activity log, "server memory X of 2048 MB"); it just needs the worker to
    actually publish it somewhere cross-process-visible instead of leaving it to die with the
    container when the execution exits.
    """
    from backend.app.services.ingestion.pipeline import INGESTION_STATUS_REGISTRY

    snapshot = INGESTION_STATUS_REGISTRY.get(document_id)
    if not snapshot:
        return
    # Shallow-copy the dict AND its events list: pipeline.py keeps appending to that same list
    # object on the main event loop while this runs on a worker thread (asyncio.to_thread), and
    # json-serializing a list another thread is mutating mid-append is a real (if rare) race.
    # The individual event dicts are never mutated after creation, so copying just these two
    # containers is enough to fully isolate this snapshot from further pipeline writes.
    safe_snapshot = dict(snapshot)
    if isinstance(safe_snapshot.get("events"), list):
        safe_snapshot["events"] = list(safe_snapshot["events"])

    from backend.app.database import SessionLocal
    from backend.app.models.document import Document

    db = SessionLocal()
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc is not None:
            doc.progress_detail = safe_snapshot
        from backend.app.models.job import IngestionJob
        job = db.query(IngestionJob).filter(
            IngestionJob.document_id == document_id,
            IngestionJob.status.in_(["PROCESSING", "CANCELLING"])
        ).first()
        if job is not None:
            job.updated_at = now
        db.commit()
    except Exception as e:
        logger.warning("[WORKER] progress sync failed (continuing): %s", e)
    finally:
        db.close()


async def _sync_progress_to_db(document_id: str, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        await asyncio.to_thread(_sync_progress_once, document_id)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=PROGRESS_SYNC_INTERVAL_S)
        except asyncio.TimeoutError:
            pass


def _fetch_raw_deck(document_id: str) -> None:
    """Downloads the raw deck from R2 to doc.storage_path if it isn't already on this
    container's local disk. The API service staged it there (see documents.py's
    _schedule_ingestion, cloud_run_job branch) under a deterministic key precisely because this
    worker runs in its own container with an empty filesystem -- doc.storage_path is only a
    meaningful local path again once this runs. A no-op for every other ingestion mode, and for
    local/manual testing where the file is already sitting on disk.

    Verifies the result explicitly and raises a diagnostic error on any failure, rather than
    letting a missing/empty file surface two layers down as a bare FileNotFoundError inside the
    pptx/pdf parser (which is what happened in production Nov 2 2026 -- the underlying cause that
    time was dispatch_available running this deck's pipeline in-process instead of ever calling
    this function at all; this is the safety net for that bug and any future one like it).
    """
    from backend.app.database import SessionLocal
    from backend.app.models.document import Document
    from backend.app.services.storage.r2_storage import get_r2_service

    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc is None:
            raise RuntimeError(f"Document {document_id} not found")
        if doc.storage_path and os.path.exists(doc.storage_path) and os.path.getsize(doc.storage_path) > 0:
            logger.info("[WORKER] raw deck already on local disk: doc_id=%s path=%s", document_id, doc.storage_path)
            return
        r2 = get_r2_service()
        if not r2.is_configured:
            raise RuntimeError(
                f"R2 is not configured in this worker container, but the raw deck for doc_id="
                f"{document_id} isn't on local disk ({doc.storage_path}) -- nothing to process. "
                f"Check R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY/R2_BUCKET_NAME/"
                f"R2_PUBLIC_BASE_URL are set on the Cloud Run JOB resource, not just the Service."
            )
        raw_key = f"raw-uploads/{doc.id}{os.path.splitext(doc.storage_path)[1]}"
        dest = Path(doc.storage_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            r2.download_to_file(raw_key, dest)
        except Exception as e:
            raise RuntimeError(
                f"Failed to download raw deck from R2 for doc_id={document_id} "
                f"(bucket={r2.bucket_name!r} key={raw_key!r}): {e}"
            ) from e
        if not dest.exists() or dest.stat().st_size == 0:
            raise RuntimeError(
                f"R2 download for doc_id={document_id} (key={raw_key!r}) reported success but "
                f"left no file (or an empty file) at {dest} -- treating as a failed fetch."
            )
        logger.info(
            "[WORKER] fetched raw deck from R2: doc_id=%s key=%s bytes=%d",
            document_id, raw_key, dest.stat().st_size,
        )
    finally:
        db.close()


def _find_processing_job_id(document_id: str) -> "str | None":
    """The id of this document's PROCESSING job, captured before ingestion starts. Needed afterwards because
    purging a cancelled document sets ingestion_jobs.document_id to NULL, so the job can no longer be found by it."""
    from backend.app.database import SessionLocal
    from backend.app.models.job import IngestionJob
    try:
        db = SessionLocal()
        try:
            job = (db.query(IngestionJob)
                   .filter(IngestionJob.document_id == document_id, IngestionJob.status.in_(["PROCESSING", "CANCELLING"]))
                   .order_by(IngestionJob.created_at.desc()).first())
            return job.id if job is not None else None
        finally:
            db.close()
    except Exception as e:
        logger.warning("[WORKER] could not look up job for doc_id=%s: %s", document_id, e)
        return None


def _write_terminal_job_status(document_id: str, error: "str | None" = None, job_id: "str | None" = None) -> None:
    """Finds the IngestionJob for this document in PROCESSING status and marks it with the terminal outcome."""
    from datetime import datetime, timezone
    from backend.app.database import SessionLocal
    from backend.app.models.document import Document
    from backend.app.models.job import IngestionJob

    now = datetime.now(timezone.utc)
    try:
        db = SessionLocal()
        try:
            if job_id:
                job = db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
            else:
                job = (
                    db.query(IngestionJob)
                    .filter(IngestionJob.document_id == document_id, IngestionJob.status.in_(["PROCESSING", "CANCELLING"]))
                    .order_by(IngestionJob.created_at.desc())
                    .first()
                )
            if job is None:
                return  # No matching job (legacy direct trigger) = no-op
            doc = db.query(Document).filter(Document.id == document_id).first()
            doc_status = doc.processing_status if doc else None
            if doc is None and job_id:
                # The document was purged while this worker ran: that is a cancellation, not a failure.
                job.status = "CANCELLED"
                job.current_step = "CANCELLED"
                job.completed_at = now
                job.error_message = "Cancelled by user"
            elif doc_status == "COMPLETED":
                job.status = "COMPLETED"
                job.current_step = "DONE"
                job.completed_at = now
            elif doc_status == "CANCELLED" or (doc and doc.cancel_requested):
                job.status = "CANCELLED"
                job.current_step = "CANCELLED"
                job.completed_at = now
                job.error_message = "Cancelled by user"
            else:
                job.status = "FAILED"
                job.error_message = error or (doc.processing_error if doc else None) or "Ingestion did not complete"
            job.updated_at = now
            db.commit()
            logger.info("[WORKER] wrote terminal status %s for job %s (doc=%s)", job.status, job.id, document_id)
        finally:
            db.close()
    except Exception as e:
        logger.warning("[WORKER] failed to write terminal job status for doc_id=%s: %s", document_id, e)


def _best_effort_dispatch_next() -> None:
    try:
        from backend.app.services.ingestion.upload_queue import dispatch_available
        loop = asyncio.new_event_loop()
        try:
            # force_cloud_run_job=True: this call only ever runs from inside a Cloud Run Job
            # worker container (this file). Never let it fall back to running the next deck's
            # pipeline in-process here just because this container's own INGESTION_MODE wasn't
            # explicitly set -- that path skips _fetch_raw_deck() and crashes with
            # FileNotFoundError on the next deck (see dispatch_available's docstring).
            loop.run_until_complete(dispatch_available(force_cloud_run_job=True))
        finally:
            loop.close()
    except Exception as e:
        logger.debug("[WORKER] best-effort dispatch next skipped: %s", e)

async def _run(document_id: str) -> None:
    import time
    from backend.app.services.ingestion.pipeline import IngestionPipeline
    from backend.app.services.costs.ledger import cost_scope

    stop_event = asyncio.Event()
    job_id = await asyncio.to_thread(_find_processing_job_id, document_id)
    poller = asyncio.create_task(_poll_cancel(document_id, stop_event))
    syncer = asyncio.create_task(_sync_progress_to_db(document_id, stop_event))
    t0 = time.perf_counter()
    try:
        logger.info("[WORKER] starting ingestion for doc_id=%s", document_id)
        with cost_scope(document_id=document_id, job_id=job_id):
            await asyncio.to_thread(_fetch_raw_deck, document_id)
            await IngestionPipeline().run(document_id)
        logger.info("[WORKER] finished ingestion for doc_id=%s", document_id)
    finally:
        duration_s = max(0.1, time.perf_counter() - t0)
        stop_event.set()
        poller.cancel()
        syncer.cancel()
        try:
            await poller
        except asyncio.CancelledError:
            pass
        try:
            await syncer
        except asyncio.CancelledError:
            pass
        # Record Cloud Run Job compute cost
        try:
            from backend.app.services.costs.ledger import record_event
            vcpu = float(os.getenv("CLOUD_RUN_JOB_VCPU", "2.0"))
            gib = float(os.getenv("CLOUD_RUN_JOB_GIB", "8.0"))
            effective_job_id = job_id or f"job_{document_id}"
            record_event(
                category="cloud_run_job",
                operation="job_compute",
                units={
                    "duration_s": duration_s,
                    "vcpu": vcpu,
                    "gib": gib,
                },
                document_id=document_id,
                job_id=job_id,
                source="measured",
                dedupe_key=f"job:{effective_job_id}",
            )
        except Exception as _ce:
            logger.warning("[WORKER] failed to record job compute cost: %s", _ce)

        # One last sync so the final "Finishing up" / completion detail and activity log entries
        # the pipeline wrote in its last moments aren't lost to the PROGRESS_SYNC_INTERVAL_S gap.
        await asyncio.to_thread(_sync_progress_once, document_id)
        await asyncio.to_thread(_write_terminal_job_status, document_id, None, job_id)
        await asyncio.to_thread(_best_effort_dispatch_next)


def _get_doc_status(document_id: str) -> "str | None":
    from backend.app.database import SessionLocal
    from backend.app.models.document import Document
    try:
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == document_id).first()
            return doc.processing_status if doc else None
        finally:
            db.close()
    except Exception:
        return None


def main() -> int:
    document_id = os.environ.get("DOCUMENT_ID") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not document_id:
        logger.error("[WORKER] no DOCUMENT_ID env var or CLI arg given -- nothing to process")
        return 1
    try:
        asyncio.run(_run(document_id))
        final_status = _get_doc_status(document_id)
        if final_status == "FAILED":
            logger.error("[WORKER] doc_id=%s ended with FAILED status -- exiting 1", document_id)
            return 1
        return 0
    except Exception as e:
        # The pipeline itself already marks the Document row FAILED with processing_error on
        # exception (see pipeline.py's top-level except block) -- this is a last-resort net so a
        # truly unexpected crash (e.g. a DB connection the pipeline couldn't reach at all) still
        # shows up as a failed Cloud Run Job execution instead of silently exiting 0.
        logger.exception("[WORKER] unhandled error processing doc_id=%s: %s", document_id, e)
        try:
            _write_terminal_job_status(document_id, error=str(e))
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
