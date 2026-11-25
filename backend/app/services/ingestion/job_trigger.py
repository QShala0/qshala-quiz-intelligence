import logging
from backend.app.config import settings

logger = logging.getLogger(__name__)


class IngestionJobTriggerError(Exception):
    """Raised when the Cloud Run Job execution couldn't be started."""
    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        self.transient = transient


def trigger_ingestion_job(document_id: str) -> str:
    """Starts one execution of the configured ingestion job, passing DOCUMENT_ID as an env var
    override so worker.py knows which deck to process. Returns the execution name on success.

    Raises IngestionJobTriggerError with transient=True for retryable API limits/errors (429, 503,
    network timeouts), or transient=False for misconfiguration / permission failures (403, 404).
    """
    missing = [name for name, val in (
        ("GCP_PROJECT_ID", settings.GCP_PROJECT_ID),
        ("GCP_REGION", settings.GCP_REGION),
        ("INGESTION_JOB_NAME", settings.INGESTION_JOB_NAME),
    ) if not val]
    if missing:
        raise IngestionJobTriggerError(
            f"INGESTION_MODE=cloud_run_job but {', '.join(missing)} not set. "
            "See the deployment docs for the Cloud Run Job setup steps.",
            transient=False,
        )

    try:
        from google.cloud import run_v2
    except ImportError as e:
        raise IngestionJobTriggerError(
            "google-cloud-run is not installed (should be in backend/requirements.txt)",
            transient=False,
        ) from e

    job_name = (
        f"projects/{settings.GCP_PROJECT_ID}/locations/{settings.GCP_REGION}"
        f"/jobs/{settings.INGESTION_JOB_NAME}"
    )

    try:
        client = run_v2.JobsClient()
        request = run_v2.RunJobRequest(
            name=job_name,
            overrides=run_v2.RunJobRequest.Overrides(
                container_overrides=[
                    run_v2.RunJobRequest.Overrides.ContainerOverride(
                        env=[run_v2.EnvVar(name="DOCUMENT_ID", value=document_id)],
                    ),
                ],
            ),
        )
        operation = client.run_job(request=request)
        metadata = getattr(operation, "metadata", None)
        execution_name = getattr(metadata, "name", None)
        if not execution_name:
            op_obj = getattr(operation, "operation", None)
            execution_name = getattr(op_obj, "name", None) or "unknown"
        logger.info("[INGESTION_JOB] triggered execution=%s for doc_id=%s", execution_name, document_id)
        return execution_name
    except IngestionJobTriggerError:
        raise
    except Exception as e:
        err_str = str(e).lower()
        transient = False
        # Check for transient status signals
        if any(term in err_str for term in [
            "429", "503", "504", "500", "502", "resourceexhausted", "unavailable",
            "deadline exceeded", "timeout", "connection reset", "broken pipe", "temporary failure"
        ]):
            transient = True
        elif hasattr(e, "grpc_status_code"):
            # StatusCode: UNAVAILABLE=14, DEADLINE_EXCEEDED=4, RESOURCE_EXHAUSTED=8, INTERNAL=13
            val = getattr(e.grpc_status_code, "value", None)
            if val in (4, 8, 13, 14):
                transient = True
        elif hasattr(e, "code"):
            try:
                c = e.code() if callable(e.code) else e.code
                if c in (4, 8, 13, 14, 429, 503, 504, 500, 502):
                    transient = True
            except Exception:
                pass

        raise IngestionJobTriggerError(
            f"Failed to start ingestion job for doc_id={document_id}: {e}",
            transient=transient,
        ) from e
