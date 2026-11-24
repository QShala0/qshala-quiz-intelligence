import logging
import mimetypes
from pathlib import Path
from typing import Optional, List, Union, BinaryIO

from backend.app.config import settings

logger = logging.getLogger(__name__)


class CloudflareR2Service:
    """
    Client for Cloudflare R2 object storage (S3-compatible API).

    R2 is the preferred media store for QShala's extracted slide images, audio
    clips, and video clues: storage is ~$0.015/GB-month and, unlike Vercel Blob,
    R2 charges nothing to download (egress) -- which matters a lot here since
    quiz media gets replayed repeatedly in the Slide Viewer / Media Viewer.

    This service is purely additive: when R2 credentials aren't configured,
    `is_configured` is False and callers should keep using local disk exactly
    as before. Nothing breaks if R2 is never set up.
    """

    def __init__(self):
        self.account_id = settings.R2_ACCOUNT_ID
        self.access_key_id = settings.R2_ACCESS_KEY_ID
        self.secret_access_key = settings.R2_SECRET_ACCESS_KEY
        self.bucket_name = settings.R2_BUCKET_NAME
        self.public_base_url = (settings.R2_PUBLIC_BASE_URL or "").rstrip("/")

        self.is_configured = bool(
            self.account_id and self.access_key_id and self.secret_access_key
            and self.bucket_name and self.public_base_url
        )

        self._client = None
        if self.is_configured:
            try:
                import boto3
                from botocore.config import Config as BotoConfig

                self._client = boto3.client(
                    "s3",
                    endpoint_url=f"https://{self.account_id}.r2.cloudflarestorage.com",
                    aws_access_key_id=self.access_key_id,
                    aws_secret_access_key=self.secret_access_key,
                    config=BotoConfig(
                        signature_version="s3v4",
                        max_pool_connections=32,
                        retries={"max_attempts": 5, "mode": "adaptive"},
                    ),
                    region_name="auto",
                )
            except Exception as e:
                logger.warning(f"[CloudflareR2Service] Failed to initialize R2 client, falling back to local disk: {e}")
                self.is_configured = False
                self._client = None

    def upload_bytes(
        self,
        key: str,
        data: bytes,
        content_type: Optional[str] = None,
        content_disposition: Optional[str] = None,
    ) -> str:
        """
        Uploads bytes to R2 under `key` and returns the public HTTPS URL.
        Raises if R2 isn't configured or the upload fails -- callers should
        catch this and fall back to local disk.
        """
        if not self.is_configured or self._client is None:
            raise RuntimeError("CloudflareR2Service is not configured")

        clean_key = key.lstrip("/").replace("\\", "/")
        if not content_type:
            content_type, _ = mimetypes.guess_type(clean_key)
            content_type = content_type or "application/octet-stream"

        self._client.put_object(
            Bucket=self.bucket_name,
            Key=clean_key,
            Body=data,
            ContentType=content_type,
            **({"ContentDisposition": content_disposition} if content_disposition else {}),
        )
        url = f"{self.public_base_url}/{clean_key}"
        logger.info(f"[CloudflareR2Service] Uploaded {clean_key} ({len(data)} bytes) -> {url}")
        return url

    def upload_file(self, key: str, path: str, content_type: Optional[str] = None) -> str:
        """
        Uploads a file from disk to R2 and returns its public HTTPS URL, without ever holding the
        file in memory (boto3 streams it; large files go up as bounded multipart chunks).
        Used for slide media so a 200 MB deck doesn't have to sit in RAM while it uploads.
        """
        if not self.is_configured or self._client is None:
            raise RuntimeError("CloudflareR2Service is not configured")

        clean_key = key.lstrip("/").replace("\\", "/")
        if not content_type:
            content_type, _ = mimetypes.guess_type(clean_key)
            content_type = content_type or "application/octet-stream"

        from boto3.s3.transfer import TransferConfig
        # One transfer thread per call (the caller already uploads several files in parallel) and
        # 8 MB parts: at most ~one part per in-flight upload is buffered.
        config = TransferConfig(
            multipart_threshold=16 * 1024 * 1024,
            multipart_chunksize=8 * 1024 * 1024,
            max_concurrency=2,
            use_threads=False,
        )
        self._client.upload_file(
            Filename=str(path),
            Bucket=self.bucket_name,
            Key=clean_key,
            ExtraArgs={"ContentType": content_type},
            Config=config,
        )
        url = f"{self.public_base_url}/{clean_key}"
        logger.info(f"[CloudflareR2Service] Uploaded {clean_key} from file -> {url}")
        return url

    def create_multipart_upload(self, key: str, content_type: Optional[str] = None) -> str:
        """
        Starts an S3-compatible multipart upload and returns its UploadId. Lets a chunked
        frontend upload stream straight into R2 one part per chunk, instead of writing the
        full deck to local disk first and uploading it as a second pass (see upload_part /
        complete_multipart_upload below, used together from documents.py's /upload-chunk).
        """
        if not self.is_configured or self._client is None:
            raise RuntimeError("CloudflareR2Service is not configured")
        clean_key = key.lstrip("/").replace("\\", "/")
        if not content_type:
            content_type, _ = mimetypes.guess_type(clean_key)
            content_type = content_type or "application/octet-stream"
        resp = self._client.create_multipart_upload(
            Bucket=self.bucket_name, Key=clean_key, ContentType=content_type,
        )
        return resp["UploadId"]

    def upload_part(self, key: str, upload_id: str, part_number: int, data: Union[bytes, bytearray]) -> str:
        """
        Uploads one part of an in-progress multipart upload and returns its ETag (needed later
        to complete the upload). Parts may arrive out of order -- S3/R2 only cares about
        PartNumber, not upload order -- so callers may retry or reorder freely as long as each
        part keeps the same PartNumber. Every part except the last must be >= 5 MB.
        """
        if not self.is_configured or self._client is None:
            raise RuntimeError("CloudflareR2Service is not configured")
        clean_key = key.lstrip("/").replace("\\", "/")
        resp = self._client.upload_part(
            Bucket=self.bucket_name, Key=clean_key, UploadId=upload_id,
            PartNumber=part_number, Body=data,
        )
        return resp["ETag"]

    def complete_multipart_upload(self, key: str, upload_id: str, parts: List[dict]) -> str:
        """
        Finishes a multipart upload once every part has been uploaded. `parts` is
        [{"PartNumber": int, "ETag": str}, ...] -- order doesn't matter, this sorts them by
        PartNumber itself, since R2/S3 requires the Parts list ascending. Returns the object's
        public HTTPS URL, same shape as upload_bytes/upload_file.
        """
        if not self.is_configured or self._client is None:
            raise RuntimeError("CloudflareR2Service is not configured")
        clean_key = key.lstrip("/").replace("\\", "/")
        self._client.complete_multipart_upload(
            Bucket=self.bucket_name, Key=clean_key, UploadId=upload_id,
            MultipartUpload={"Parts": sorted(parts, key=lambda p: p["PartNumber"])},
        )
        url = f"{self.public_base_url}/{clean_key}"
        logger.info(f"[CloudflareR2Service] Completed multipart upload {clean_key} -> {url}")
        return url

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        """
        Cancels an in-progress multipart upload and discards any parts already received, so
        they don't linger as billable incomplete-upload storage. Best-effort: swallows errors
        since this only ever runs during cleanup (user cancelled, or a size limit was hit).
        """
        if not self.is_configured or self._client is None:
            return
        clean_key = key.lstrip("/").replace("\\", "/")
        try:
            self._client.abort_multipart_upload(Bucket=self.bucket_name, Key=clean_key, UploadId=upload_id)
        except Exception as e:
            logger.warning(f"[CloudflareR2Service] Failed to abort multipart upload {clean_key}: {e}")

    def upload(
        self,
        pathname: str,
        data: Union[bytes, BinaryIO],
        content_type: Optional[str] = None
    ) -> str:
        """Convenience wrapper accepting a file-like object or raw bytes, mirroring
        the shape of VercelBlobService.put() so call sites can swap storage backends
        with minimal changes."""
        if hasattr(data, "read"):
            body_bytes = data.read()
            if isinstance(body_bytes, str):
                body_bytes = body_bytes.encode("utf-8")
        else:
            body_bytes = bytes(data)
        return self.upload_bytes(pathname, body_bytes, content_type=content_type)

    def delete(self, keys: List[str]) -> None:
        if not self.is_configured or self._client is None or not keys:
            return
        for key in keys:
            clean_key = key
            if self.public_base_url and key.startswith(self.public_base_url):
                clean_key = key[len(self.public_base_url):].lstrip("/")
            try:
                self._client.delete_object(Bucket=self.bucket_name, Key=clean_key)
            except Exception as e:
                logger.warning(f"[CloudflareR2Service] Failed to delete {clean_key}: {e}")

    def delete_object(self, key: str) -> None:
        """Delete one object by key (or public URL). Unlike delete(), a failure is raised so the caller can tell.
        Deleting a key that does not exist is not an error (S3 semantics)."""
        if not self.is_configured or self._client is None or not key:
            return
        clean_key = key
        if self.public_base_url and key.startswith(self.public_base_url):
            clean_key = key[len(self.public_base_url):].lstrip("/")
        self._client.delete_object(Bucket=self.bucket_name, Key=clean_key)

    def delete_prefix(self, prefix: str) -> int:
        """Delete every object whose key starts with `prefix`. Returns how many were removed."""
        if not self.is_configured or self._client is None:
            return 0
        clean_prefix = prefix.lstrip("/").replace("\\", "/")
        removed = 0
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket_name, Prefix=clean_prefix):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if not objs:
                continue
            try:
                self._client.delete_objects(Bucket=self.bucket_name, Delete={"Objects": objs, "Quiet": True})
                removed += len(objs)
            except Exception as e:
                logger.warning(f"[CloudflareR2Service] Failed to delete prefix batch under {clean_prefix}: {e}")
        logger.info(f"[CloudflareR2Service] Deleted {removed} objects under {clean_prefix}")
        return removed

    def download(self, key_or_url: str) -> bytes:
        if not self.is_configured or self._client is None:
            raise RuntimeError("CloudflareR2Service is not configured")
        clean_key = key_or_url
        if self.public_base_url and key_or_url.startswith(self.public_base_url):
            clean_key = key_or_url[len(self.public_base_url):].lstrip("/")
        resp = self._client.get_object(Bucket=self.bucket_name, Key=clean_key)
        return resp["Body"].read()

    def download_to_file(self, key_or_url: str, dest_path: Union[str, Path]) -> Path:
        """
        Downloads an object from R2 directly to `dest_path`.
        Accepts full public URL, /static/slides/... path, or raw S3 key.
        """
        if not self.is_configured or self._client is None:
            raise RuntimeError("CloudflareR2Service is not configured")
        clean_key = str(key_or_url).strip()
        if self.public_base_url and clean_key.startswith(self.public_base_url):
            clean_key = clean_key[len(self.public_base_url):].lstrip("/")
        elif clean_key.startswith("/static/"):
            clean_key = clean_key[len("/static/"):].lstrip("/")
        elif clean_key.startswith("/"):
            clean_key = clean_key.lstrip("/")

        p = Path(dest_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self.bucket_name, clean_key, str(p))
        return p


_global_r2_service: Optional[CloudflareR2Service] = None


def get_r2_service() -> CloudflareR2Service:
    global _global_r2_service
    if _global_r2_service is None:
        _global_r2_service = CloudflareR2Service()
    return _global_r2_service


def get_media_storage() -> Optional[CloudflareR2Service]:
    """
    Returns the configured R2 service, or None when R2 isn't set up (meaning
    callers should fall back to local disk / existing behavior unchanged).
    """
    svc = get_r2_service()
    return svc if svc.is_configured else None
