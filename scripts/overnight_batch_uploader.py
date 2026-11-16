"""
Autonomous Overnight Batch Uploader for QShala Presentation Decks.

Sequentially uploads the 8 target presentation files using 20MB chunked streaming
to the Cloud Run Service and R2 multipart upload, tracks batch heartbeat, and monitors
queue draining with concurrency=1 until all decks are fully ingested.
"""

import os
import sys
import time
import uuid
import math
import logging
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional
import requests

# Base URL for QShala Cloud Run API
BASE_URL = os.environ.get("API_BASE_URL", "https://qshala-quiz-intelligence-238470261844.asia-southeast1.run.app").rstrip("/")

# Target files in order of ascending size
TARGET_FILES = [
    "Local Questions - 2026 ZVQ Corporate - Reserve.pptx",       # ~124 MB
    "Prelim Starred - 2026 ZVQ College - Reserve.pptx",         # ~166 MB
    "Prelim - 2026 ZVQ College - Reserve.pptx",                 # ~199 MB
    "ZVQ - Prelim Starred - Reserve.pptx",                      # ~226 MB
    "Prelim - 2026 ZVQ Corporate - Reserve.pptx",               # ~234 MB
    "Local Questions - 2026 ZVQ College - Reserve.pptx",        # ~304 MB
    "Varsity Based Questions - 2026 ZVQ Corporate - Reserve.pptx",  # ~462 MB
    "ZVQ - Prelim - Reserve.pptx",                              # ~559 MB
]

CHUNK_SIZE = 20 * 1024 * 1024  # 20 MB slices (compatible with R2 multipart min 5MB)
LOG_FILE = Path("overnight_upload.log")

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("overnight_uploader")


class OvernightBatchUploader:
    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url
        self.session = requests.Session()
        self.batch_id: Optional[str] = None
        self.items_map: Dict[str, Dict[str, Any]] = {}  # filename -> item metadata
        self.uploaded_docs: Dict[str, str] = {}  # filename -> doc_id
        self.stop_heartbeat = threading.Event()
        self.heartbeat_thread: Optional[threading.Thread] = None

    def start_heartbeat(self):
        """Send batch heartbeat every 15 seconds to keep the batch active."""
        def _loop():
            while not self.stop_heartbeat.is_set():
                if self.batch_id:
                    try:
                        url = f"{self.base_url}/api/v1/jobs/upload-batch/{self.batch_id}/heartbeat"
                        res = self.session.post(url, timeout=10)
                        if res.status_code == 200:
                            logger.debug("[HEARTBEAT] Batch %s kept alive", self.batch_id)
                        else:
                            logger.warning("[HEARTBEAT] Unexpected status %s: %s", res.status_code, res.text[:200])
                    except Exception as e:
                        logger.warning("[HEARTBEAT] Failed: %s", e)
                self.stop_heartbeat.wait(15)

        self.heartbeat_thread = threading.Thread(target=_loop, daemon=True)
        self.heartbeat_thread.start()

    def register_batch(self, files: List[Path]) -> str:
        """Register the upload batch with all files upfront."""
        batch_items = []
        for f in files:
            cid = f"item_{uuid.uuid4().hex[:10]}"
            size = f.stat().st_size
            self.items_map[f.name] = {
                "client_item_id": cid,
                "filename": f.name,
                "size_bytes": size,
                "path": f
            }
            batch_items.append({
                "client_item_id": cid,
                "filename": f.name,
                "size_bytes": size
            })

        url = f"{self.base_url}/api/v1/jobs/upload-batch"
        payload = {"files": batch_items}
        logger.info("[BATCH] Registering batch of %d files at %s...", len(batch_items), url)

        res = self.session.post(url, json=payload, timeout=30)
        res.raise_for_status()
        data = res.json()
        self.batch_id = data["batch_id"]
        logger.info("[BATCH] Successfully registered Batch ID: %s", self.batch_id)
        self.start_heartbeat()
        return self.batch_id

    def upload_single_chunk(self, upload_id: str, chunk_index: int, total_chunks: int,
                            filename: str, client_item_id: str, chunk_bytes: bytes,
                            max_retries: int = 25) -> Dict[str, Any]:
        """Upload one chunk with robust exponential backoff on retryable HTTP errors and network hiccups."""
        url = f"{self.base_url}/api/v1/documents/upload-chunk"
        backoff = 3.0

        for attempt in range(1, max_retries + 1):
            try:
                files = {"chunk": (f"chunk_{chunk_index}.part", chunk_bytes, "application/octet-stream")}
                data = {
                    "upload_id": upload_id,
                    "chunk_index": chunk_index,
                    "total_chunks": total_chunks,
                    "filename": filename,
                    "batch_id": self.batch_id,
                    "client_item_id": client_item_id,
                }
                res = self.session.post(url, data=data, files=files, timeout=240)

                # HTTP 425: slice_not_ready (session being created by slice 0)
                # HTTP 429: rate limited
                # HTTP 500/502/503/504: transient gateway / cloud errors
                if res.status_code in (425, 429, 500, 502, 503, 504):
                    logger.warning("[CHUNK %d/%d] Attempt %d/%d got HTTP %d (%s). Retrying in %.1fs...",
                                   chunk_index + 1, total_chunks, attempt, max_retries,
                                   res.status_code, res.text[:120], backoff)
                    time.sleep(backoff)
                    backoff = min(backoff * 1.5, 30.0)
                    continue

                if res.status_code != 200:
                    logger.error("[CHUNK %d/%d] Non-retryable HTTP %d: %s",
                                 chunk_index + 1, total_chunks, res.status_code, res.text[:300])
                    res.raise_for_status()

                return res.json()

            except (requests.exceptions.RequestException, Exception) as e:
                logger.warning("[CHUNK %d/%d] Attempt %d/%d network issue: %s. Retrying in %.1fs...",
                               chunk_index + 1, total_chunks, attempt, max_retries, e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 1.5, 30.0)

        raise RuntimeError(f"Failed to upload chunk {chunk_index} after {max_retries} attempts")

    def upload_deck(self, file_path: Path) -> Dict[str, Any]:
        """Upload a full deck in 20MB slices, streaming directly to R2 multipart."""
        filename = file_path.name
        file_size = file_path.stat().st_size
        total_chunks = max(1, math.ceil(file_size / CHUNK_SIZE))
        upload_id = str(uuid.uuid4())
        item_meta = self.items_map.get(filename, {})
        client_item_id = item_meta.get("client_item_id", f"item_{uuid.uuid4().hex[:8]}")

        logger.info("[DECK UPLOAD START] %s (%.1f MB, %d chunks) upload_id=%s",
                    filename, file_size / (1024 * 1024), total_chunks, upload_id)
        start_time = time.time()

        with open(file_path, "rb") as f:
            for idx in range(total_chunks):
                chunk_data = f.read(CHUNK_SIZE)
                c_start = time.time()
                result = self.upload_single_chunk(
                    upload_id=upload_id,
                    chunk_index=idx,
                    total_chunks=total_chunks,
                    filename=filename,
                    client_item_id=client_item_id,
                    chunk_bytes=chunk_data
                )
                c_elapsed = time.time() - c_start
                pct = round(((idx + 1) / total_chunks) * 100)
                logger.info("  [%s] Chunk %d/%d uploaded (%.1f MB) in %.1fs -> %d%%",
                            filename, idx + 1, total_chunks, len(chunk_data) / (1024 * 1024), c_elapsed, pct)

        elapsed = time.time() - start_time
        doc_info = result.get("document", {})
        doc_id = doc_info.get("id")
        if doc_id:
            self.uploaded_docs[filename] = doc_id
        logger.info("[DECK UPLOAD COMPLETE] %s uploaded in %.1fs (%.2f MB/s). Doc ID: %s, Status: %s",
                    filename, elapsed, (file_size / (1024 * 1024)) / max(elapsed, 0.1), doc_id, result.get("status"))

        # Trigger dispatcher to start processing immediately if capacity allows
        self.kick_dispatch()
        return result

    def kick_dispatch(self):
        """Call /api/v1/jobs/process-upload-next to ensure queue keeps moving."""
        try:
            url = f"{self.base_url}/api/v1/jobs/process-upload-next"
            r = self.session.post(url, timeout=15)
            logger.info("[DISPATCH TRIGGER] %s -> %s", url, r.text[:150])
        except Exception as e:
            logger.debug("[DISPATCH TRIGGER] Non-critical error: %s", e)

    def monitor_queue(self, timeout_hours: float = 6.0):
        """Monitor the upload queue until all decks in this batch complete."""
        logger.info("[MONITOR] Starting queue monitoring loop (timeout: %.1f hours)...", timeout_hours)
        start_ts = time.time()
        max_duration = timeout_hours * 3600
        filenames_set = set(self.items_map.keys())

        while time.time() - start_ts < max_duration:
            try:
                # 1. Kick dispatch to guarantee idle capacity claims the next pending deck
                self.kick_dispatch()

                # 2. Inspect queue
                q_url = f"{self.base_url}/api/v1/jobs/upload-queue"
                q_res = self.session.get(q_url, timeout=15)
                if q_res.status_code == 200:
                    q_data = q_res.json()
                    counts = q_data.get("counts", {})
                    jobs = q_data.get("jobs", [])

                    # Find jobs belonging to our target files
                    batch_jobs = [j for j in jobs if j.get("filename") in filenames_set]
                    done_count = sum(1 for j in batch_jobs if j.get("status") == "COMPLETED")
                    failed_count = sum(1 for j in batch_jobs if j.get("status") == "FAILED")
                    proc_jobs = [j for j in batch_jobs if j.get("status") == "PROCESSING"]
                    pend_jobs = [j for j in batch_jobs if j.get("status") == "PENDING"]

                    logger.info("[QUEUE STATUS] Batch progress: %d/%d COMPLETED | %d PROCESSING | %d PENDING | %d FAILED | Total Vault Counts: %s",
                                done_count, len(filenames_set), len(proc_jobs), len(pend_jobs), failed_count, counts)

                    for pj in proc_jobs:
                        logger.info("  --> [PROCESSING] %s (Job %s, Step: %s, Error: %s)",
                                    pj.get("filename"), pj.get("id"), pj.get("step"), pj.get("error"))

                    if done_count + failed_count >= len(filenames_set) and len(filenames_set) > 0:
                        logger.info("[ALL DONE] All %d target files reached terminal status! (%d COMPLETED, %d FAILED)",
                                    len(filenames_set), done_count, failed_count)
                        break

            except Exception as e:
                logger.warning("[MONITOR] Error querying queue status: %s", e)

            time.sleep(20)

    def run(self):
        """Execute the complete overnight ingestion workflow."""
        logger.info("=================================================================")
        logger.info("   QShala Multi-Deck Autonomous Ingestion Pipeline   ")
        logger.info("   Target Decks: %d | Base URL: %s", len(TARGET_FILES), self.base_url)
        logger.info("=================================================================")

        # 1. Verify all target files exist locally
        valid_files = []
        for fname in TARGET_FILES:
            p = Path(fname)
            if not p.exists():
                p = Path(".") / fname
            if p.exists():
                valid_files.append(p)
                logger.info("  [VERIFIED] %s (%.1f MB)", p.name, p.stat().st_size / (1024 * 1024))
            else:
                logger.error("  [MISSING] %s not found on local disk!", fname)

        if not valid_files:
            logger.error("No valid presentation files found! Aborting.")
            return

        total_bytes = sum(f.stat().st_size for f in valid_files)
        logger.info("Total Payload: %.2f GB across %d files.", total_bytes / (1024**3), len(valid_files))

        # 2. Register multi-deck batch
        self.register_batch(valid_files)

        # 3. Upload files sequentially into Cloudflare R2 / Neon queue
        for idx, fpath in enumerate(valid_files, 1):
            success = False
            for deck_attempt in range(1, 4):
                logger.info("--- [%d/%d] Uploading %s (Attempt %d/3) ---", idx, len(valid_files), fpath.name, deck_attempt)
                try:
                    self.upload_deck(fpath)
                    success = True
                    break
                except Exception as e:
                    logger.exception("[DECK UPLOAD ERROR] %s (Attempt %d/3 failed): %s", fpath.name, deck_attempt, e)
                    time.sleep(5)
            if not success:
                logger.error("[DECK PERMANENTLY FAILED] Skipping %s after 3 attempts.", fpath.name)

        logger.info("[ALL UPLOADS COMPLETED] %d/%d decks uploaded to R2 and registered in queue.",
                    len(self.uploaded_docs), len(valid_files))

        # 4. Monitor ingestion processing to completion
        self.monitor_queue(timeout_hours=5.0)

        # 5. Cleanup
        self.stop_heartbeat.set()
        logger.info("Ingestion run finished.")


if __name__ == "__main__":
    uploader = OvernightBatchUploader()
    uploader.run()
