"""Serial upload queue for deck ingestion.

Goal: let a user drop several decks and have them processed one at a time, over time, without
breaking the site. On a 512 MB host, processing decks in parallel is what causes OOM crashes, so the
whole point here is that exactly ONE deck ingests at a time.

How it works
------------
- Upload endpoints call `enqueue_upload()` instead of running the pipeline inline. That inserts an
  `IngestionJob` (source_type=MANUAL_UPLOAD, status=PENDING) and marks the Document `QUEUED`. The
  HTTP request returns immediately.
- A single background worker (`run_worker_loop`, started once at app startup) polls for the oldest
  PENDING job and runs the modern `IngestionPipeline` on it — but only when no MANUAL_UPLOAD job is
  already PROCESSING, so concurrency is capped at 1 across the whole app even if several worker
  loops exist.
- Each job gets its own short-lived DB sessions; the worker never holds a transaction across the
  pipeline run (the pipeline manages its own session).
- A job left PROCESSING by a crash/restart is reconciled on startup: marked FAILED with a clear
  message (never silently re-run, which would duplicate slides). A deliberate retry purges the
  deck's partial artifacts first (see `purge_artifacts`) and re-queues.

This path drives the current `IngestionPipeline.run()`, not the older serverless JobQueueService.
"""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import SessionLocal
from backend.app.models.document import Document, Slide
from backend.app.models.job import IngestionJob
from backend.app.models.question import Question

logger = logging.getLogger(__name__)

SOURCE = "MANUAL_UPLOAD"
_worker_task: "asyncio.Task | None" = None


def _now():
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------- enqueue / status

def enqueue_upload(
    db: Session,
    *,
    document_id: str,
    filename: str,
    batch_id: "str | None" = None,
    client_item_id: "str | None" = None,
) -> IngestionJob:
    """Create a PENDING ingestion job for an uploaded deck and mark the document QUEUED."""
    job = IngestionJob(
        source_type=SOURCE,
        filename=filename,
        document_id=document_id,
        batch_id=batch_id,
        client_item_id=client_item_id,
        status="PENDING",
        current_step="QUEUED",
        max_retries=settings.UPLOAD_QUEUE_MAX_RETRIES,
    )
    db.add(job)
    doc = db.query(Document).filter(Document.id == document_id).first()
    if doc is not None:
        doc.processing_status = "QUEUED"
    db.commit()
    db.refresh(job)
    logger.info(
        "[QUEUE] enqueued %s (doc=%s batch=%s item=%s), position=%d",
        job.id, document_id, batch_id, client_item_id, queue_position(db, job)
    )
    return job


def queue_position(db: Session, job: IngestionJob) -> int:
    """How many PENDING jobs are ahead of this one (0 = next up)."""
    return (db.query(IngestionJob)
            .filter(IngestionJob.source_type == SOURCE, IngestionJob.status == "PENDING",
                    IngestionJob.created_at < job.created_at)
            .count())


# --------------------------------------------------------------------- artifact purge (for safe retry/resume)

def purge_artifacts(db: Session, document_id: str) -> None:
    """Delete the slides/questions/tags produced for a document WITHOUT deleting the Document row or
    the uploaded file, so the pipeline can re-run cleanly. Children are deleted explicitly (not via
    DB cascade) so it works the same on Postgres and on SQLite with foreign keys off."""
    q_ids = [r[0] for r in db.query(Question.id).filter(Question.document_id == document_id).all()]
    if q_ids:
        # Best-effort import of every per-question child table; skip any that aren't present.
        children = []
        try:
            from backend.app.models.qshala_tag import QshalaTag, QshalaTagChange
            children += [QshalaTagChange, QshalaTag]
        except Exception:
            pass
        try:
            from backend.app.models.question_meta import (
                QuestionTopicPath, QuestionRepeat, QuestionEditHistory)
            children += [QuestionTopicPath, QuestionRepeat, QuestionEditHistory]
        except Exception:
            pass
        try:
            from backend.app.models.qshala_tag import QshalaQuestionMetadata
            children += [QshalaQuestionMetadata]
        except Exception:
            pass
        for model in children:
            try:
                db.query(model).filter(model.question_id.in_(q_ids)).delete(synchronize_session=False)
            except Exception as e:
                logger.warning("[QUEUE] purge child %s failed: %s", getattr(model, "__name__", model), e)
        db.query(Question).filter(Question.document_id == document_id).delete(synchronize_session=False)
    db.query(Slide).filter(Slide.document_id == document_id).delete(synchronize_session=False)
    db.commit()


# --------------------------------------------------------------------- claim / run

# A deck should finish well within this; a job PROCESSING longer than this had its worker die
# (crash, or the host slept mid-run). Such a job is reclaimed so it can no longer block the queue.
STALE_PROCESSING_MINUTES = 8
_ADVISORY_LOCK_KEY = 778201  # arbitrary, stable; serialises claiming across every process
_EXECUTION_LAST_CHECK: dict = {}


def _check_cloud_run_execution(job_id: str, execution_name: str) -> "str | None":
    """Check Cloud Run execution state at most once per 30s per job."""
    import time
    now_ts = time.time()
    last_ts, last_status = _EXECUTION_LAST_CHECK.get(job_id, (0.0, None))
    if now_ts - last_ts < 30.0 and last_status is not None:
        return last_status
    try:
        from google.cloud import run_v2
        client = run_v2.ExecutionsClient()
        execution = client.get_execution(name=execution_name)
        status = "RUNNING"
        if getattr(execution, "succeeded_count", 0) > 0:
            status = "SUCCEEDED"
        elif getattr(execution, "failed_count", 0) > 0:
            status = "FAILED"
        elif getattr(execution, "cancelled_count", 0) > 0:
            status = "CANCELLED"
        _EXECUTION_LAST_CHECK[job_id] = (now_ts, status)
        return status
    except Exception as e:
        logger.debug("[QUEUE] get_execution check failed for %s: %s", execution_name, e)
        return None


def _sweep_expired_upload_sessions(db: Session, max_age_hours: int = 24) -> int:
    from datetime import timedelta
    try:
        from backend.app.models.upload_session import UploadSession
        cutoff = _now() - timedelta(hours=max_age_hours)
        expired = db.query(UploadSession).filter(
            UploadSession.status == "OPEN",
            UploadSession.updated_at < cutoff
        ).all()
        if not expired:
            return 0
        from backend.app.services.storage.r2_storage import get_r2_service
        r2 = get_r2_service()
        for sess in expired:
            if r2.is_configured and sess.r2_upload_id and sess.raw_key:
                try:
                    r2.abort_multipart_upload(sess.raw_key, sess.r2_upload_id)
                except Exception:
                    pass
            sess.status = "CANCELLED"
        db.commit()
        return len(expired)
    except Exception:
        return 0


def _reclaim_stale(db: Session) -> None:
    """Check and reconcile stale or terminated PROCESSING jobs, so they stop blocking the queue."""
    from datetime import timedelta
    cutoff = _now() - timedelta(minutes=STALE_PROCESSING_MINUTES)
    try:
        processing_jobs = (db.query(IngestionJob)
                 .filter(IngestionJob.source_type == SOURCE, IngestionJob.status.in_(["PROCESSING", "CANCELLING"]))
                 .all())
    except Exception as e:
        logger.warning("[QUEUE] _reclaim_stale failed to query processing jobs: %s", e)
        return 0

    reclaimed = 0
    for job in processing_jobs:
        if job.execution_name:
            c_status = _check_cloud_run_execution(job.id, job.execution_name)
            if c_status == "SUCCEEDED":
                job.status = "COMPLETED"
                job.current_step = "DONE"
                job.completed_at = _now()
                job.updated_at = _now()
                doc = db.query(Document).filter(Document.id == job.document_id).first()
                if doc is not None:
                    doc.processing_status = "COMPLETED"
                reclaimed += 1
                continue
            elif c_status in ("FAILED", "CANCELLED"):
                is_cancelling = (job.current_step == "CANCELLING")
                job.status = "CANCELLED" if is_cancelling else "FAILED"
                job.current_step = "CANCELLED" if is_cancelling else "FAILED"
                job.error_message = "Cancelled by user" if is_cancelling else "Worker container terminated with failure"
                job.completed_at = _now()
                job.updated_at = _now()
                doc = db.query(Document).filter(Document.id == job.document_id).first()
                if doc is not None:
                    doc.processing_status = job.status
                    doc.processing_error = job.error_message
                if job.document_id:
                    purge_artifacts(db, job.document_id)
                reclaimed += 1
                continue
            elif c_status == "RUNNING":
                job.updated_at = _now()
                continue

        j_updated = job.updated_at
        if j_updated is not None and j_updated.tzinfo is None:
            from datetime import timezone
            j_updated = j_updated.replace(tzinfo=timezone.utc)
        if j_updated and j_updated < cutoff:
            is_cancelling = (job.current_step == "CANCELLING")
            job.status = "CANCELLED" if is_cancelling else "FAILED"
            job.current_step = "CANCELLED" if is_cancelling else "FAILED"
            job.error_message = ("Cancelled by user" if is_cancelling else
                                 f"No progress for over {STALE_PROCESSING_MINUTES} min — the worker likely died.")
            job.completed_at = _now()
            job.updated_at = _now()
            doc = db.query(Document).filter(Document.id == job.document_id).first()
            if doc is not None and doc.processing_status not in ("COMPLETED",):
                doc.processing_status = job.status
                doc.processing_error = job.error_message
            if job.document_id:
                purge_artifacts(db, job.document_id)
            reclaimed += 1

    _sweep_expired_upload_sessions(db)
    if reclaimed:
        db.commit()
        logger.warning("[QUEUE] reclaimed %d stale/terminated PROCESSING job(s)", reclaimed)
    return reclaimed


reap_dead_workers = _reclaim_stale


def claim_next(db: Session) -> "IngestionJob | None":
    """Claim the oldest PENDING upload job, but only if none is actively PROCESSING. The whole
    check-and-claim is serialised ACROSS PROCESSES with a Postgres transaction-level advisory lock,
    so even when the web server runs several worker processes, at most one deck ingests at a time."""
    from backend.app.database import is_postgres
    from sqlalchemy import text

    if is_postgres:
        try:
            # Held until this transaction ends (commit/rollback); no leaked locks. Serialises claimers.
            db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ADVISORY_LOCK_KEY})
        except Exception as e:
            logger.warning("[QUEUE] could not acquire advisory lock: %s", e)
            return None

    try:
        _reclaim_stale(db)   # a dead PROCESSING job must not block the queue forever
    except Exception as e:
        logger.warning("[QUEUE] _reclaim_stale failed: %s", e)

    busy = (db.query(IngestionJob)
            .filter(IngestionJob.source_type == SOURCE, IngestionJob.status.in_(["PROCESSING", "CANCELLING"]))
            .count())
    max_concurrency = max(1, getattr(settings, "INGESTION_MAX_CONCURRENCY", 1))
    if busy >= max_concurrency:
        db.commit()      # release the advisory lock; capacity reached
        return None

    pending_jobs = (db.query(IngestionJob)
           .filter(IngestionJob.source_type == SOURCE, IngestionJob.status == "PENDING")
           .order_by(IngestionJob.created_at)
           .all())

    # Backoff schedule: 15s, 30s, 60s, 120s, 300s
    BACKOFF_DELAYS = [0, 15, 30, 60, 120, 300]
    selected_job = None
    for j in pending_jobs:
        if not j.retry_count or j.retry_count == 0:
            selected_job = j
            break
        delay_sec = BACKOFF_DELAYS[min(j.retry_count, len(BACKOFF_DELAYS) - 1)]
        ref_dt = j.updated_at or j.created_at
        if ref_dt is not None and ref_dt.tzinfo is None:
            from datetime import timezone
            ref_dt = ref_dt.replace(tzinfo=timezone.utc)
        elapsed = (_now() - ref_dt).total_seconds() if ref_dt else 999999
        if elapsed >= delay_sec:
            selected_job = j
            break

    if selected_job is None:
        db.commit()
        return None

    selected_job.status = "PROCESSING"
    selected_job.current_step = "INGESTING"
    selected_job.updated_at = _now()
    db.commit()          # sets PROCESSING and releases the lock atomically
    return selected_job


async def _run_job(job_id: str, document_id: str) -> None:
    """Run the modern pipeline for one job and record the outcome. Own sessions only."""
    from backend.app.services.ingestion.pipeline import IngestionPipeline
    ok, err = True, None
    try:
        await IngestionPipeline().run(document_id)            # manages its own DB session
    except Exception as e:                                    # pragma: no cover - defensive
        ok, err = False, str(e)[:500]
        logger.exception("[QUEUE] pipeline crashed for doc=%s", document_id)

    db = SessionLocal()
    try:
        job = db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
        doc = db.query(Document).filter(Document.id == document_id).first()
        doc_ok = bool(doc) and doc.processing_status == "COMPLETED"
        if job:
            if ok and doc_ok:
                job.status, job.current_step, job.completed_at = "COMPLETED", "DONE", _now()
            else:
                job.status = "FAILED"
                job.error_message = err or (doc.processing_error if doc else None) or "ingestion did not complete"
            job.updated_at = _now()
        db.commit()
    finally:
        db.close()


_dispatch_lock = asyncio.Lock()
_last_dispatch_time: float = 0.0


async def dispatch_available(max_decks: int = 200, force_cloud_run_job: bool = False) -> int:
    """Loop claiming jobs while capacity remains.
    - If INGESTION_MODE == "cloud_run_job" (or force_cloud_run_job=True): triggers a Cloud Run
      Job execution (worker.py) for each claimed job. The job stays PROCESSING until the worker
      closes it. On trigger failure, marks job and document FAILED immediately and continues.
    - Otherwise: runs IngestionPipeline().run() in-process for each claimed job.

    `force_cloud_run_job` exists for exactly one caller: worker.py's own self-dispatch after it
    finishes a deck. The worker's container may not have INGESTION_MODE set in its own env (it
    was only ever wired onto the API Service's env, never the Job resource's) -- if that read
    defaulted to "in_process", this function would run the NEXT queued deck's pipeline directly
    inside the current worker container instead of triggering a separate Job execution. That
    in-process path never calls worker.py's _fetch_raw_deck(), so the next deck's raw file --
    staged only in R2 by the API service -- is never pulled down, and parsing crashes with
    FileNotFoundError. (This happened in production, Nov 2 2026: two decks uploaded in one
    batch, the second one crashed exactly this way, same Cloud Run execution ID as the first.)
    A worker calling this to dispatch its successor is BY DEFINITION only ever reachable when
    cloud_run_job mode is active -- worker.py itself only runs as a Cloud Run Job execution -- so
    it forces that branch rather than trusting its own process's possibly-unset config.
    Returns the number of jobs dispatched in this invocation.
    """
    global _last_dispatch_time
    import time
    if _dispatch_lock.locked():
        return 0

    async with _dispatch_lock:
        _last_dispatch_time = time.time()
        dispatched = 0
        while dispatched < max_decks:
            db = SessionLocal()
            try:
                job = claim_next(db)
                if job is None:
                    break
                job_id, document_id = job.id, job.document_id
            finally:
                db.close()

            dispatched += 1
            if force_cloud_run_job or settings.INGESTION_MODE == "cloud_run_job":
                from backend.app.services.ingestion.job_trigger import (
                    trigger_ingestion_job,
                    IngestionJobTriggerError,
                )
                try:
                    # Non-blocking async thread execution
                    execution_name = await asyncio.to_thread(trigger_ingestion_job, document_id)
                    logger.info("[QUEUE] triggered Cloud Run Job for doc_id=%s (job=%s execution=%s)", document_id, job_id, execution_name)
                    succ_db = SessionLocal()
                    try:
                        s_job = succ_db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
                        if s_job:
                            s_job.execution_name = execution_name
                            s_job.updated_at = _now()
                            succ_db.commit()
                    finally:
                        succ_db.close()
                except IngestionJobTriggerError as e:
                    err_msg = str(e)
                    logger.error("[QUEUE] failed to trigger Cloud Run Job for doc_id=%s (job=%s transient=%s): %s", document_id, job_id, e.transient, err_msg)
                    fail_db = SessionLocal()
                    try:
                        f_job = fail_db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
                        f_doc = fail_db.query(Document).filter(Document.id == document_id).first()
                        if f_job:
                            if e.transient and (f_job.retry_count or 0) < 5:
                                f_job.status = "PENDING"
                                f_job.current_step = "QUEUED"
                                f_job.retry_count = (f_job.retry_count or 0) + 1
                                f_job.error_message = f"Transient trigger error (attempt {f_job.retry_count}/5): {err_msg}"
                                f_job.updated_at = _now()
                                if f_doc and f_doc.processing_status not in ("COMPLETED", "CANCELLED"):
                                    f_doc.processing_status = "QUEUED"
                                    f_doc.processing_error = f_job.error_message
                                fail_db.commit()
                                # F2 FIX: Stop dispatch loop immediately on transient API failure!
                                break
                            else:
                                f_job.status = "FAILED"
                                f_job.error_message = err_msg
                                f_job.updated_at = _now()
                                if f_doc and f_doc.processing_status not in ("COMPLETED", "CANCELLED"):
                                    f_doc.processing_status = "FAILED"
                                    f_doc.processing_error = err_msg
                                fail_db.commit()
                    finally:
                        fail_db.close()
                except Exception as e:
                    err_msg = str(e)
                    logger.error("[QUEUE] unexpected error triggering Cloud Run Job for doc_id=%s: %s", document_id, err_msg)
                    fail_db = SessionLocal()
                    try:
                        f_job = fail_db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
                        f_doc = fail_db.query(Document).filter(Document.id == document_id).first()
                        if f_job:
                            f_job.status = "FAILED"
                            f_job.error_message = err_msg
                            f_job.updated_at = _now()
                        if f_doc and f_doc.processing_status not in ("COMPLETED", "CANCELLED"):
                            f_doc.processing_status = "FAILED"
                            f_doc.processing_error = err_msg
                        fail_db.commit()
                    finally:
                        fail_db.close()
            else:
                await _run_job(job_id, document_id)

        return dispatched


async def process_one() -> bool:
    """Claim and run/dispatch queued deck(s) if capacity is available. Returns True if at least one was dispatched."""
    dispatched = await dispatch_available(max_decks=1)
    return dispatched > 0


_draining = False


async def drain(max_decks: int = 200) -> int:
    """Process or dispatch queued decks.
    In cloud_run_job mode, dispatch_available() triggers executions for all available capacity slots.
    In in_process mode, processes decks sequentially until queue empty or busy.
    """
    global _draining
    if _draining:                 # this process is already draining; don't start a second drainer
        return 0
    _draining = True
    done = 0
    try:
        done = await dispatch_available(max_decks=max_decks)
    finally:
        _draining = False
    if done:
        logger.info("[QUEUE] drain processed/dispatched %d deck(s)", done)
    return done


async def run_worker_loop() -> None:
    poll = max(1, int(settings.UPLOAD_QUEUE_POLL_SECONDS))
    logger.info("[QUEUE] upload worker started (poll=%ss, serial)", poll)
    while True:
        try:
            did = await process_one()
        except asyncio.CancelledError:
            logger.info("[QUEUE] upload worker stopping")
            raise
        except Exception as e:                                # never let the loop die
            logger.exception("[QUEUE] worker tick error: %s", e)
            did = False
        await asyncio.sleep(0 if did else poll)               # drain fast, idle slow


def reconcile_on_startup(db: Session) -> None:
    """A job left PROCESSING means the process died mid-run. Don't silently re-run (would duplicate
    slides); mark it FAILED with a clear message so the user can retry it deliberately."""
    stuck = (db.query(IngestionJob)
             .filter(IngestionJob.source_type == SOURCE, IngestionJob.status.in_(["PROCESSING", "CANCELLING"]))
             .all())
    for job in stuck:
        job.status = "FAILED"
        job.error_message = ("Server restarted mid-ingestion (often the deck is too large for memory). "
                             "Retry it to re-process from a clean slate.")
        job.updated_at = _now()
        doc = db.query(Document).filter(Document.id == job.document_id).first()
        if doc is not None and doc.processing_status not in ("COMPLETED",):
            doc.processing_status = "FAILED"
            doc.processing_error = job.error_message
    if stuck:
        db.commit()
        logger.warning("[QUEUE] reconciled %d interrupted upload job(s) as FAILED", len(stuck))


def start_worker(loop=None) -> None:
    """Start the single worker task (idempotent)."""
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return
    _worker_task = asyncio.ensure_future(run_worker_loop())


def requeue_job(db: Session, job: IngestionJob) -> None:
    """Deliberate retry: purge partial artifacts, then set the job back to PENDING and doc to QUEUED."""
    if job.document_id:
        purge_artifacts(db, job.document_id)
        doc = db.query(Document).filter(Document.id == job.document_id).first()
        if doc is not None:
            doc.processing_status = "QUEUED"
            doc.processing_error = None
    job.status = "PENDING"
    job.current_step = "QUEUED"
    job.error_message = None
    job.retry_count = (job.retry_count or 0) + 1
    job.updated_at = _now()
    db.commit()
