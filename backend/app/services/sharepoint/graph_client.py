import asyncio
import hashlib
import os
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import httpx
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.models.sharepoint import SharePointSyncState, SharePointFile
from backend.app.models.job import IngestionJob
from backend.app.services.storage.blob_storage import get_blob_service, VercelBlobService

logger = logging.getLogger(__name__)

class SharePointClient:
    """
    Microsoft Graph API client for SharePoint document library ingestion.
    Uses Azure AD OAuth2 client credentials flow with least-privilege Sites.Selected grant.
    Implements delta query synchronization, paging, 410 Gone recovery, and streaming to Vercel Blob.
    """
    GRAPH_BASE = "https://graph.microsoft.com/v1.0"
    LOGIN_BASE = "https://login.microsoftonline.com"

    def __init__(
        self,
        tenant_id: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        site_id: Optional[str] = None,
        drive_id: Optional[str] = None,
        blob_service: Optional[VercelBlobService] = None,
        http_client: Optional[httpx.AsyncClient] = None
    ):
        self.tenant_id = tenant_id or settings.AZURE_TENANT_ID
        self.client_id = client_id or settings.AZURE_CLIENT_ID
        self.client_secret = client_secret or settings.AZURE_CLIENT_SECRET
        self.site_id = site_id or settings.SHAREPOINT_SITE_ID
        self.drive_id = drive_id or settings.SHAREPOINT_DRIVE_ID
        self.blob_service = blob_service or get_blob_service()
        self._custom_http_client = http_client

        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._custom_http_client:
            return self._custom_http_client
        return httpx.AsyncClient(timeout=120.0, follow_redirects=True)

    async def get_access_token(self) -> str:
        """
        Retrieves or refreshes OAuth 2.0 access token via Azure AD client credentials.
        """
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        if not self.tenant_id or not self.client_id or not self.client_secret:
            raise ValueError(
                "Azure AD credentials (AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET) are not configured"
            )

        token_url = f"{self.LOGIN_BASE}/{self.tenant_id}/oauth2/v2.0/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://graph.microsoft.com/.default"
        }

        client = await self._get_http_client()
        should_close = self._custom_http_client is None
        try:
            resp = await client.post(token_url, data=payload)
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data["access_token"]
            expires_in = data.get("expires_in", 3600)
            self._token_expires_at = time.time() + float(expires_in)
            return self._access_token
        finally:
            if should_close:
                await client.aclose()

    async def fetch_delta_page(self, url: str) -> Dict[str, Any]:
        """
        Fetches a delta query page from Microsoft Graph API with Bearer token authentication.
        """
        token = await self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }

        client = await self._get_http_client()
        should_close = self._custom_http_client is None
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 410:
                # HTTP 410 Gone: Delta token expired
                raise httpx.HTTPStatusError("Delta token expired", request=resp.request, response=resp)
            resp.raise_for_status()
            return resp.json()
        finally:
            if should_close:
                await client.aclose()

    async def download_item_content(
        self,
        item_id: str,
        drive_id: Optional[str] = None,
        download_url: Optional[str] = None
    ) -> bytes:
        """
        Streams file content directly from Microsoft Graph or pre-authenticated downloadUrl.
        """
        client = await self._get_http_client()
        should_close = self._custom_http_client is None
        try:
            if download_url:
                resp = await client.get(download_url)
                resp.raise_for_status()
                return resp.content

            target_drive = drive_id or self.drive_id
            if target_drive:
                url = f"{self.GRAPH_BASE}/drives/{target_drive}/items/{item_id}/content"
            elif self.site_id:
                url = f"{self.GRAPH_BASE}/sites/{self.site_id}/drive/items/{item_id}/content"
            else:
                raise ValueError("SharePoint drive_id or site_id is not specified")

            token = await self.get_access_token()
            headers = {"Authorization": f"Bearer {token}"}
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.content
        finally:
            if should_close:
                await client.aclose()

    # ------------------------------------------------------------------ browsing + resilient reads
    _SELECT = "id,name,size,eTag,cTag,file,folder,lastModifiedDateTime,webUrl,parentReference"

    async def _sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def _graph_get(self, url: str, *, params: Optional[Dict[str, Any]] = None, max_retries: int = 4) -> Dict[str, Any]:
        """GET a Graph URL as JSON. Waits out throttling (429/503/504, honouring Retry-After) and refreshes the token once
        on a 401. Only graph.microsoft.com URLs are ever called, so a crafted `next` link from a browser cannot make this
        server send its Graph token anywhere else."""
        if not url.startswith(self.GRAPH_BASE + "/"):
            raise ValueError("Refusing to call a URL that is not Microsoft Graph")
        client = await self._get_http_client()
        should_close = self._custom_http_client is None
        try:
            attempt = 0
            refreshed = False
            while True:
                token = await self.get_access_token()
                resp = await client.get(url, params=params, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
                if resp.status_code == 401 and not refreshed:
                    self._access_token = None
                    refreshed = True
                    continue
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                    try:
                        wait = float(resp.headers.get("Retry-After") or 0)
                    except ValueError:
                        wait = 0.0
                    attempt += 1
                    await self._sleep(min(wait or float(2 ** attempt), 30.0))
                    continue
                resp.raise_for_status()
                return resp.json()
        finally:
            if should_close:
                await client.aclose()

    async def list_drives(self) -> List[Dict[str, Any]]:
        """The document libraries the app can see: the configured drive, or every library of the configured site."""
        if self.drive_id:
            d = await self._graph_get(f"{self.GRAPH_BASE}/drives/{self.drive_id}", params={"$select": "id,name,webUrl"})
            return [{"id": d.get("id") or self.drive_id, "name": d.get("name") or "Documents", "web_url": d.get("webUrl")}]
        if not self.site_id:
            raise ValueError("Set SHAREPOINT_SITE_ID (or SHAREPOINT_DRIVE_ID) first")
        data = await self._graph_get(f"{self.GRAPH_BASE}/sites/{self.site_id}/drives", params={"$select": "id,name,webUrl"})
        return [{"id": d["id"], "name": d.get("name"), "web_url": d.get("webUrl")} for d in data.get("value", [])]

    async def list_children(self, drive_id: str, item_id: Optional[str] = None, next_url: Optional[str] = None) -> Dict[str, Any]:
        """One page of a folder's contents (root when item_id is None). Returns {items, next}."""
        if next_url:
            data = await self._graph_get(next_url)
        else:
            base = f"{self.GRAPH_BASE}/drives/{drive_id}/" + (f"items/{item_id}/children" if item_id else "root/children")
            data = await self._graph_get(base, params={"$select": self._SELECT, "$top": "200"})
        return {"items": data.get("value", []), "next": data.get("@odata.nextLink")}

    async def get_item(self, drive_id: str, item_id: str) -> Dict[str, Any]:
        return await self._graph_get(f"{self.GRAPH_BASE}/drives/{drive_id}/items/{item_id}", params={"$select": self._SELECT})

    async def get_item_by_path(self, drive_id: str, path: str) -> Dict[str, Any]:
        """A folder or file addressed by its path inside the library, e.g. "Quizzes/2024"."""
        from urllib.parse import quote
        clean = quote(path.strip("/"), safe="/")
        return await self._graph_get(f"{self.GRAPH_BASE}/drives/{drive_id}/root:/{clean}", params={"$select": self._SELECT})

    async def search_items(self, drive_id: str, query: str, folder_id: Optional[str] = None, item_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search items in a drive or within a specific folder using Graph search(q='...')."""
        from urllib.parse import quote
        target_folder = folder_id or item_id
        if not query or not query.strip():
            return []
        q = quote(query.strip())
        if target_folder and target_folder != "root":
            url = f"{self.GRAPH_BASE}/drives/{drive_id}/items/{target_folder}/search(q='{q}')"
        else:
            url = f"{self.GRAPH_BASE}/drives/{drive_id}/root/search(q='{q}')"
        data = await self._graph_get(url, params={"$select": self._SELECT, "$top": "100"})
        return data.get("value", [])

    async def download_item_capped(self, drive_id: str, item_id: str, max_bytes: int) -> bytes:
        """Download a file, refusing anything over max_bytes (checked from Content-Length and again while streaming)."""
        url = f"{self.GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content"
        client = await self._get_http_client()
        should_close = self._custom_http_client is None
        try:
            token = await self.get_access_token()
            target, headers, hops = url, {"Authorization": f"Bearer {token}"}, 0
            while True:
                async with client.stream("GET", target, headers=headers, follow_redirects=False) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location") and hops < 3:
                        target, headers, hops = resp.headers["location"], {}, hops + 1   # pre-authenticated URL: send no token
                        continue
                    resp.raise_for_status()
                    declared = int(resp.headers.get("content-length") or 0)
                    if declared and declared > max_bytes:
                        raise ValueError(f"File is {declared // (1024 * 1024)} MB, over the {max_bytes // (1024 * 1024)} MB limit")
                    buf = bytearray()
                    async for chunk in resp.aiter_bytes():
                        buf.extend(chunk)
                        if len(buf) > max_bytes:
                            raise ValueError(f"File is over the {max_bytes // (1024 * 1024)} MB limit")
                    return bytes(buf)
        finally:
            if should_close:
                await client.aclose()

    async def stream_item_to_r2(
        self,
        drive_id: str,
        item_id: str,
        key: str,
        max_bytes: int,
        r2: Any,
        part_size: int,
    ) -> Tuple[str, str, int]:
        """Stream a file directly from SharePoint to R2 under key, enforcing max_bytes and memory bounds."""
        url = f"{self.GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content"
        client = await self._get_http_client()
        should_close = self._custom_http_client is None
        upload_id: Optional[str] = None
        pending_task: Optional[asyncio.Task] = None
        parts: List[Dict[str, Any]] = []
        part_number = 1
        hasher = hashlib.sha256()
        total_bytes = 0
        buf = bytearray()

        try:
            token = await self.get_access_token()
            target, headers, hops = url, {"Authorization": f"Bearer {token}"}, 0
            while True:
                async with client.stream("GET", target, headers=headers, follow_redirects=False) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location") and hops < 3:
                        target, headers, hops = resp.headers["location"], {}, hops + 1
                        continue
                    resp.raise_for_status()
                    declared = int(resp.headers.get("content-length") or 0)
                    if declared and declared > max_bytes:
                        raise ValueError(f"File is {declared // (1024 * 1024)} MB, over the {max_bytes // (1024 * 1024)} MB limit")

                    async for chunk in resp.aiter_bytes():
                        if not chunk:
                            continue
                        chunk_len = len(chunk)
                        total_bytes += chunk_len
                        if total_bytes > max_bytes:
                            raise ValueError(f"File is over the {max_bytes // (1024 * 1024)} MB limit")
                        hasher.update(chunk)

                        chunk_offset = 0
                        while chunk_offset < chunk_len:
                            needed = part_size - len(buf)
                            take = min(needed, chunk_len - chunk_offset)
                            buf.extend(chunk[chunk_offset : chunk_offset + take])
                            chunk_offset += take

                            if len(buf) == part_size:
                                if upload_id is None:
                                    upload_id = await asyncio.to_thread(r2.create_multipart_upload, key)
                                if pending_task is not None:
                                    prev_etag = await pending_task
                                    parts.append({"PartNumber": part_number - 1, "ETag": prev_etag})
                                    pending_task = None
                                part_body = buf
                                buf = bytearray()
                                pending_task = asyncio.create_task(
                                    asyncio.to_thread(r2.upload_part, key, upload_id, part_number, part_body)
                                )
                                part_number += 1
                    break

            # Stream finished successfully
            if upload_id is None:
                # File is smaller than one full part: finish with one upload_bytes and no multipart calls
                public_url = await asyncio.to_thread(r2.upload_bytes, key, bytes(buf))
                return public_url, hasher.hexdigest(), total_bytes

            # Multipart upload was started: await any pending part
            if pending_task is not None:
                prev_etag = await pending_task
                parts.append({"PartNumber": part_number - 1, "ETag": prev_etag})
                pending_task = None

            # Upload the final remainder part if any bytes left in buf
            if len(buf) > 0:
                final_etag = await asyncio.to_thread(r2.upload_part, key, upload_id, part_number, buf)
                parts.append({"PartNumber": part_number, "ETag": final_etag})

            public_url = await asyncio.to_thread(r2.complete_multipart_upload, key, upload_id, parts)
            return public_url, hasher.hexdigest(), total_bytes

        except BaseException:
            if pending_task is not None and not pending_task.done():
                pending_task.cancel()
                try:
                    await pending_task
                except (asyncio.CancelledError, Exception):
                    pass
            if upload_id is not None:
                try:
                    await asyncio.to_thread(r2.abort_multipart_upload, key, upload_id)
                except Exception as abort_err:
                    logger.warning(f"Failed to abort multipart upload {upload_id} for {key}: {abort_err}")
            raise
        finally:
            if should_close:
                await client.aclose()

    async def sync_delta(
        self,
        db: Session,
        drive_id: Optional[str] = None,
        sync_state_id: str = "qshala_main_library"
    ) -> Dict[str, Any]:
        """
        Executes incremental delta sync for a SharePoint document library.
        Paginates until deltaLink is reached, filters .pptx and .pdf, handles 410 Gone recovery,
        persists state in DB, streams new files to Vercel Blob, and queues ingestion jobs.
        """
        target_drive = drive_id or self.drive_id
        if not target_drive and not self.site_id:
            raise ValueError("SharePoint drive_id or site_id must be provided or configured in settings")

        state = db.query(SharePointSyncState).filter(SharePointSyncState.id == sync_state_id).first()
        if not state:
            state = SharePointSyncState(
                id=sync_state_id,
                site_id=self.site_id,
                drive_id=target_drive,
                status="SYNCING"
            )
            db.add(state)
            db.commit()
            db.refresh(state)

        state.status = "SYNCING"
        state.last_error = None
        db.commit()

        root_delta_url = f"{self.GRAPH_BASE}/drives/{target_drive}/root/delta" if target_drive else f"{self.GRAPH_BASE}/sites/{self.site_id}/drive/root/delta"
        initial_url = state.delta_token or root_delta_url
        current_url: Optional[str] = initial_url
        discovered_files: List[Tuple[SharePointFile, Optional[str]]] = []
        latest_delta_link: Optional[str] = None
        deleted_count = 0
        pages_processed = 0

        try:
            while current_url:
                pages_processed += 1
                try:
                    data = await self.fetch_delta_page(current_url)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 410:
                        logger.warning("Delta token expired (410 Gone). Resetting delta token and restarting sync.")
                        state.delta_token = None
                        db.commit()
                        current_url = root_delta_url
                        continue
                    raise

                items = data.get("value", [])
                for item in items:
                    item_id = item.get("id")
                    if not item_id:
                        continue

                    # Handle deleted items
                    if "@removed" in item or item.get("deleted"):
                        existing = db.query(SharePointFile).filter(SharePointFile.id == item_id).first()
                        if existing:
                            existing.sync_status = "DELETED"
                            deleted_count += 1
                        continue

                    # Filter for .pptx and .pdf files only
                    name = item.get("name", "")
                    ext = Path(name).suffix.lower()
                    is_file = "file" in item
                    if not is_file or ext not in [".pptx", ".pdf"]:
                        continue

                    etag = item.get("eTag")
                    c_tag = item.get("cTag")
                    size = item.get("size", 0)
                    mod_str = item.get("lastModifiedDateTime")
                    mod_dt = None
                    if mod_str:
                        try:
                            mod_dt = datetime.fromisoformat(mod_str.replace("Z", "+00:00"))
                        except Exception:
                            mod_dt = datetime.now(timezone.utc)

                    web_url = item.get("webUrl")
                    d_url = item.get("@microsoft.graph.downloadUrl")

                    sp_file = db.query(SharePointFile).filter(SharePointFile.id == item_id).first()
                    if not sp_file:
                        sp_file = SharePointFile(
                            id=item_id,
                            name=name,
                            web_url=web_url,
                            etag=etag,
                            c_tag=c_tag,
                            file_size_bytes=size,
                            last_modified_date_time=mod_dt,
                            sync_status="DISCOVERED"
                        )
                        db.add(sp_file)
                        discovered_files.append((sp_file, d_url))
                    elif sp_file.etag != etag or sp_file.sync_status in ["FAILED", "DISCOVERED"]:
                        sp_file.name = name
                        sp_file.web_url = web_url
                        sp_file.etag = etag
                        sp_file.c_tag = c_tag
                        sp_file.file_size_bytes = size
                        sp_file.last_modified_date_time = mod_dt
                        sp_file.sync_status = "DISCOVERED"
                        sp_file.error_message = None
                        discovered_files.append((sp_file, d_url))

                # Handle pagination
                next_link = data.get("@odata.nextLink")
                delta_link = data.get("@odata.deltaLink")

                if next_link:
                    current_url = next_link
                else:
                    if delta_link:
                        latest_delta_link = delta_link
                    current_url = None

            db.commit()

            # Include any existing files from DB that were previously discovered or failed transfer
            existing_pending = db.query(SharePointFile).filter(
                SharePointFile.sync_status.in_(["DISCOVERED", "FAILED"])
            ).all()
            for ep in existing_pending:
                if not any(f.id == ep.id for f, _ in discovered_files):
                    discovered_files.append((ep, None))

            # Stream newly discovered files into Vercel Blob and enqueue IngestionJobs
            enqueued_jobs = []
            for sp_file, download_url in discovered_files:
                try:
                    file_content = await self.download_item_content(
                        item_id=sp_file.id,
                        drive_id=target_drive,
                        download_url=download_url
                    )

                    blob_pathname = f"sharepoint/{sp_file.id}/{sp_file.name}"
                    blob_result = await self.blob_service.put(
                        pathname=blob_pathname,
                        data=file_content
                    )

                    sp_file.blob_url = blob_result["url"]
                    sp_file.sync_status = "QUEUED"

                    # Check if an active job already exists for this file to avoid duplicate concurrent jobs
                    existing_job = db.query(IngestionJob).filter(
                        IngestionJob.source_file_id == sp_file.id,
                        IngestionJob.status.in_(["PENDING", "PROCESSING"])
                    ).first()

                    if existing_job:
                        existing_job.blob_url = blob_result["url"]
                        existing_job.filename = sp_file.name
                        existing_job.current_step = "DOWNLOAD"
                        existing_job.status = "PENDING"
                        existing_job.retry_count = 0
                        existing_job.error_message = None
                        job = existing_job
                    else:
                        job = IngestionJob(
                            source_type="SHAREPOINT",
                            source_file_id=sp_file.id,
                            filename=sp_file.name,
                            blob_url=blob_result["url"],
                            status="PENDING",
                            current_step="DOWNLOAD",
                            step_data={"sharepoint_item_id": sp_file.id}
                        )
                        db.add(job)

                    db.commit()
                    enqueued_jobs.append(job.id)
                except Exception as file_err:
                    logger.error(f"Error transferring file {sp_file.name} to Blob/JobQueue: {file_err}")
                    sp_file.sync_status = "FAILED"
                    sp_file.error_message = str(file_err)
                    db.commit()

            # Update final sync state & delta token atomically after successful iteration
            if latest_delta_link:
                state.delta_token = latest_delta_link
            state.last_sync_at = datetime.now(timezone.utc)
            state.status = "IDLE"
            state.total_files_tracked = db.query(SharePointFile).filter(SharePointFile.sync_status != "DELETED").count()
            db.commit()

            return {
                "status": "SUCCESS",
                "pages_processed": pages_processed,
                "discovered_files_count": len(discovered_files),
                "enqueued_jobs_count": len(enqueued_jobs),
                "deleted_files_count": deleted_count,
                "enqueued_job_ids": enqueued_jobs,
                "delta_token": state.delta_token
            }

        except Exception as e:
            logger.exception(f"SharePoint delta sync failed: {e}")
            state.status = "FAILED"
            state.last_error = str(e)
            db.commit()
            raise


_global_sharepoint_client: Optional[SharePointClient] = None

def get_sharepoint_client() -> SharePointClient:
    global _global_sharepoint_client
    if _global_sharepoint_client is None:
        _global_sharepoint_client = SharePointClient()
    return _global_sharepoint_client
