import asyncio
import hashlib

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.models.document import Document
from backend.app.models.job import IngestionJob
from backend.app.models.sharepoint import SharePointFile
from backend.app.services.sharepoint import importer
from backend.app.services.sharepoint.graph_client import SharePointClient
from backend.app.services.storage.blob_storage import VercelBlobService

BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/t/oauth2/v2.0/token"
CONTENT = {"f1": b"deck one bytes", "f2": b"deck two bytes", "f3": b"deck three bytes", "f4": b"deck four bytes"}


def _file(item_id, name, size=100, etag="e1", path="/drives/D/root:/2024"):
    return {"id": item_id, "name": name, "size": size, "eTag": etag, "cTag": "c", "file": {"hashes": {"quickXorHash": "h"}},
            "lastModifiedDateTime": "2024-05-01T10:00:00Z", "webUrl": f"https://sp/{name}", "parentReference": {"path": path}}


TREE = {
    "root": {"value": [
        {"id": "fold1", "name": "2024", "folder": {"childCount": 3}},
        _file("f1", "Finals.pptx", len(CONTENT["f1"]), path="/drives/D/root:"),
        _file("x1", "Rules.docx", path="/drives/D/root:"),
    ]},
    "fold1": {"value": [
        _file("f2", "Prelims.pptx", len(CONTENT["f2"])),
        _file("f3", "Semis.pdf", len(CONTENT["f3"])),
        _file("t1", "~$Prelims.pptx"),
        {"id": "fold2", "name": "Archive", "folder": {"childCount": 1}, "parentReference": {"path": "/drives/D/root:/2024"}},
    ]},
    "fold2": {"value": [_file("f4", "Old.pptx", len(CONTENT["f4"]), path="/drives/D/root:/2024/Archive")]},
    "fold3": {"value": [_file("f5", "Curriculum.pptx", len(CONTENT["f1"]), path="/drives/D/root:/School Program")]},
}


def make_handler(state):
    def handler(request: httpx.Request):
        url = str(request.url)
        if url == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        state.setdefault("calls", []).append(url)
        if url.startswith(f"{BASE}/drives/D/root/children"):
            return httpx.Response(200, json=TREE["root"])
        for fid in ("fold1", "fold2", "fold3"):
            if url.startswith(f"{BASE}/drives/D/items/{fid}/children"):
                if state.get("throttle_once") and not state.get("throttled"):
                    state["throttled"] = True
                    return httpx.Response(429, headers={"Retry-After": "1"})
                return httpx.Response(200, json=TREE[fid])
        if url.startswith(f"{BASE}/drives/D/root:/2024"):
            return httpx.Response(200, json={"id": "fold1", "name": "2024", "folder": {}, "parentReference": {"path": "/drives/D/root:"}})
        if url.startswith(f"{BASE}/drives/D/root:/School%20Program") or url.startswith(f"{BASE}/drives/D/root:/School Program"):
            return httpx.Response(200, json={"id": "fold3", "name": "School Program", "folder": {}, "parentReference": {"path": "/drives/D/root:"}})
        for fid, body in CONTENT.items():
            if url == f"{BASE}/drives/D/items/{fid}/content":
                return httpx.Response(200, content=state.get("override", {}).get(fid, body))
        if url.startswith(f"{BASE}/drives/D/items/") and "/children" not in url and "/content" not in url:
            iid = url.split("/items/")[1].split("?")[0]
            for page in TREE.values():
                for it in page["value"]:
                    if it["id"] == iid:
                        return httpx.Response(200, json=it)
        return httpx.Response(404)
    return handler


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def upload_dir(tmp_path, monkeypatch):
    from backend.app.config import settings
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(settings, "INGESTION_MODE", "in_process")
    monkeypatch.setattr(settings, "SHAREPOINT_ROOT_FOLDER", "")


@pytest.fixture
def state():
    return {}


@pytest.fixture
def client(tmp_path, state):
    c = SharePointClient(
        tenant_id="t", client_id="c", client_secret="s", drive_id="D",
        blob_service=VercelBlobService(token=None, local_dir=tmp_path / "blobs"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(make_handler(state))),
    )
    slept = []

    async def no_sleep(seconds):
        slept.append(seconds)

    c._sleep = no_sleep
    c.slept = slept
    return c


def sel(**kw):
    base = dict(drive_id="D", items=[{"id": "root", "kind": "folder"}])
    base.update(kw)
    return importer.Selection(**base)


@pytest.mark.anyio
async def test_plan_walks_folders_and_ignores_unsupported_and_temp_files(db, client):
    plan = await importer.plan_import(db, client, sel())
    names = {f["name"] for f in plan["files"]}
    assert names == {"Finals.pptx", "Prelims.pptx", "Semis.pdf", "Old.pptx"}      # no .docx, no ~$ temp file
    assert plan["counts"] == {"NEW": 4}
    assert plan["to_import"] == 4


@pytest.mark.anyio
async def test_non_recursive_and_filters(db, client):
    assert {f["name"] for f in (await importer.plan_import(db, client, sel(recursive=False)))["files"]} == {"Finals.pptx"}
    only_pdf = await importer.plan_import(db, client, sel(filters={"name_contains": "semis"}))
    assert [f["name"] for f in only_pdf["files"]] == ["Semis.pdf"]
    by_folder = await importer.plan_import(db, client, sel(filters={"folder_contains": "archive"}))
    assert [f["name"] for f in by_folder["files"]] == ["Old.pptx"]
    none = await importer.plan_import(db, client, sel(filters={"modified_after": "2025-01-01T00:00:00Z"}))
    assert none["total_found"] == 0


@pytest.mark.anyio
async def test_specific_files_and_exclusions(db, client):
    picked = await importer.plan_import(db, client, sel(items=[{"id": "f2", "kind": "file"}, {"id": "f3", "kind": "file"}]))
    assert {f["name"] for f in picked["files"]} == {"Prelims.pptx", "Semis.pdf"}
    skipped = await importer.plan_import(db, client, sel(exclude_ids=["f2", "f4"]))
    assert {f["name"] for f in skipped["files"]} == {"Finals.pptx", "Semis.pdf"}


@pytest.mark.anyio
async def test_classification_already_imported_changed_and_duplicate_by_name(db, client):
    db.add(SharePointFile(id="f1", name="Finals.pptx", etag="e1", sync_status="COMPLETED"))      # same etag: done
    db.add(SharePointFile(id="f2", name="Prelims.pptx", etag="OLD", sync_status="COMPLETED"))    # etag moved: changed
    for fid, name in (("f1", "Finals.pptx"), ("f2", "Prelims.pptx")):                              # their decks exist
        db.add(Document(id=f"doc-{fid}", filename=name, title=name, file_type="pptx", storage_path="x",
                        sharepoint_file_id=fid, processing_status="COMPLETED"))
    db.add(Document(id="d1", filename="Semis.pdf", title="Semis", file_type="pdf", storage_path="x",
                    file_size_bytes=len(CONTENT["f3"])))                                          # uploaded by hand
    db.commit()
    plan = await importer.plan_import(db, client, sel())
    status = {f["name"]: f["status"] for f in plan["files"]}
    assert status["Finals.pptx"] == "ALREADY_IMPORTED"
    assert status["Prelims.pptx"] == "CHANGED"
    assert status["Semis.pdf"] == "ALREADY_IMPORTED"
    assert status["Old.pptx"] == "NEW"
    assert plan["to_import"] == 2                       # CHANGED + NEW


@pytest.mark.anyio
async def test_stage_then_transfer_creates_jobs_in_small_steps(db, client):
    staged = await importer.stage_import(db, client, sel(items=[{"id": "fold1", "kind": "folder"}]))
    assert staged["selected"] == 3                      # Prelims, Semis, Old
    assert db.query(IngestionJob).count() == 0          # staging downloads nothing
    assert {r.sync_status for r in db.query(SharePointFile).all()} == {"SELECTED"}

    first = await importer.transfer_next(db, client, limit=2, batch_id=staged["batch_id"])
    assert first["queued"] == 2 and first["remaining"] == 1
    second = await importer.transfer_next(db, client, limit=2, batch_id=staged["batch_id"])
    assert second["remaining"] == 0
    jobs = db.query(IngestionJob).all()
    assert len(jobs) == 3
    # the normal upload pipeline's job: queued, tied to a Document, no SharePoint-only state machine
    assert all(j.status == "PENDING" and j.current_step == "QUEUED" and j.source_type == "MANUAL_UPLOAD" and j.document_id for j in jobs)
    docs = {d.sharepoint_file_id: d for d in db.query(Document).all()}
    assert set(docs) == {"f2", "f3", "f4"}
    d2 = docs["f2"]
    assert d2.source == "SHAREPOINT" and d2.filename == "Prelims.pptx" and d2.processing_status == "QUEUED"
    assert d2.file_sha256 == hashlib.sha256(CONTENT["f2"]).hexdigest() and d2.file_size_bytes == len(CONTENT["f2"])
    row = db.query(SharePointFile).filter_by(id="f2").one()
    assert row.sync_status == "QUEUED"


@pytest.mark.anyio
async def test_identical_deck_already_in_qshala_is_skipped_not_reimported(db, client):
    db.add(Document(id="d2", filename="renamed.pptx", title="Renamed", file_type="pptx", storage_path="x",
                    file_sha256=hashlib.sha256(CONTENT["f2"]).hexdigest()))
    db.commit()
    staged = await importer.stage_import(db, client, sel(items=[{"id": "f2", "kind": "file"}]))
    res = await importer.transfer_next(db, client, batch_id=staged["batch_id"])
    assert res["skipped_duplicate"] == 1 and res["queued"] == 0
    row = db.query(SharePointFile).filter_by(id="f2").one()
    assert row.sync_status == "SKIPPED_DUPLICATE" and row.match_document_id == "d2"
    assert db.query(IngestionJob).count() == 0 and db.query(Document).filter_by(source="SHAREPOINT").count() == 0


@pytest.mark.anyio
async def test_oversized_file_fails_cleanly_without_stopping_the_batch(db, client, monkeypatch):
    monkeypatch.setattr(importer, "MAX_FILE_BYTES", 20)
    client_state_override = {"f3": b"x" * 100}
    # f3 is 100 bytes (> 20) while f2 is 14 bytes (< 20)
    client._custom_http_client = httpx.AsyncClient(transport=httpx.MockTransport(make_handler({"override": client_state_override})))
    staged = await importer.stage_import(db, client, sel(items=[{"id": "f2", "kind": "file"}, {"id": "f3", "kind": "file"}]))
    res = await importer.transfer_next(db, client, limit=5, batch_id=staged["batch_id"])
    assert res["queued"] == 1 and res["failed"] == 1
    bad = db.query(SharePointFile).filter_by(id="f3").one()
    assert bad.sync_status == "FAILED" and "limit" in bad.error_message


@pytest.mark.anyio
async def test_throttling_is_waited_out_and_retried(db, client, state):
    state["throttle_once"] = True
    plan = await importer.plan_import(db, client, sel(items=[{"id": "fold1", "kind": "folder"}], recursive=False))
    assert plan["total_found"] == 2
    assert client.slept == [1.0]                        # honoured Retry-After once


@pytest.mark.anyio
async def test_only_graph_urls_are_ever_called(client):
    with pytest.raises(ValueError):
        await client._graph_get("https://evil.example.com/steal")
    with pytest.raises(ValueError):
        await client.list_children("D", None, next_url="https://evil.example.com/page2")


@pytest.mark.anyio
async def test_stale_transferring_files_are_given_back(db, client):
    from datetime import datetime, timedelta, timezone
    db.add(SharePointFile(id="f2", name="Prelims.pptx", etag="e1", drive_id="D", sync_status="TRANSFERRING",
                          updated_at=datetime.now(timezone.utc) - timedelta(hours=1)))
    db.commit()
    res = await importer.transfer_next(db, client)
    assert res["queued"] == 1


# ---------------------------------------------------------------- folder limit (SHAREPOINT_ROOT_FOLDER)

@pytest.fixture
def limited(monkeypatch):
    from backend.app.config import settings
    monkeypatch.setattr(settings, "SHAREPOINT_ROOT_FOLDER", "2024")


@pytest.mark.anyio
async def test_folder_limit_maps_library_root_to_the_limited_folder(db, client, limited):
    plan = await importer.plan_import(db, client, sel())               # "root" now means the 2024 folder
    assert {f["name"] for f in plan["files"]} == {"Prelims.pptx", "Semis.pdf", "Old.pptx"}   # Finals.pptx sits outside


@pytest.mark.anyio
async def test_folder_limit_rejects_files_and_folders_outside(db, client, limited):
    with pytest.raises(ValueError):
        await importer.plan_import(db, client, sel(items=[{"id": "f1", "kind": "file"}]))     # root-level deck
    with pytest.raises(httpx.HTTPStatusError):                       # an unknown id is a 404 from Graph, never allowed through
        await importer.effective_folder(client, "D", "some-other-folder-not-in-tree")


@pytest.mark.anyio
async def test_folder_limit_allows_inside_subfolders(db, client, limited):
    assert await importer.effective_folder(client, "D", "fold2") == "fold2"
    ok = await importer.plan_import(db, client, sel(items=[{"id": "f4", "kind": "file"}]))
    assert [f["name"] for f in ok["files"]] == ["Old.pptx"]


@pytest.mark.anyio
async def test_missing_limited_folder_gives_a_clear_error(db, client, monkeypatch):
    from backend.app.config import settings
    monkeypatch.setattr(settings, "SHAREPOINT_ROOT_FOLDER", "DoesNotExist")
    with pytest.raises(ValueError) as e:
        await importer.plan_import(db, client, sel())
    assert "DoesNotExist" in str(e.value)


@pytest.mark.anyio
async def test_multiple_folder_limits_allows_both_roots_and_their_contents(db, client, monkeypatch):
    from backend.app.config import settings
    monkeypatch.setattr(settings, "SHAREPOINT_ROOT_FOLDER", "2024, School Program")

    roots = await importer.scope_roots(client, "D")
    assert len(roots) == 2
    assert {r["name"] for r in roots} == {"2024", "School Program"}

    # "root" walks both folders
    plan = await importer.plan_import(db, client, sel())
    names = {f["name"] for f in plan["files"]}
    assert names == {"Prelims.pptx", "Semis.pdf", "Old.pptx", "Curriculum.pptx"}
    # Finals.pptx sits in root outside both folders, so it's excluded
    assert "Finals.pptx" not in names


@pytest.mark.anyio
async def test_multiple_folder_limits_effective_folder_and_outside_rejection(db, client, monkeypatch):
    from backend.app.config import settings
    monkeypatch.setattr(settings, "SHAREPOINT_ROOT_FOLDER", "2024, School Program")

    # "root" or None returns None for multiple roots (caller provides virtual root)
    assert await importer.effective_folder(client, "D", "root") is None
    assert await importer.effective_folder(client, "D", None) is None

    # Selecting fold1 or fold3 or fold2 works
    assert await importer.effective_folder(client, "D", "fold1") == "fold1"
    assert await importer.effective_folder(client, "D", "fold3") == "fold3"
    assert await importer.effective_folder(client, "D", "fold2") == "fold2"

    # Selecting outside file fails
    with pytest.raises(ValueError):
        await importer.plan_import(db, client, sel(items=[{"id": "f1", "kind": "file"}]))



@pytest.mark.anyio
async def test_deck_is_staged_in_r2_for_the_worker_exactly_like_a_manual_upload(db, client, monkeypatch, tmp_path):
    from backend.app.config import settings
    from backend.app.services.storage import r2_storage
    monkeypatch.setattr(settings, "INGESTION_MODE", "cloud_run_job")
    uploaded = {}

    class FakeR2:
        def upload_bytes(self, key, data, content_type=None):
            uploaded["key"], uploaded["size"] = key, len(data)
            return f"https://r2.example/{key}"

    monkeypatch.setattr(r2_storage, "get_media_storage", lambda: FakeR2())
    db.add(SharePointFile(id="f2", name="Prelims & Finals 2024.pptx", etag="e1", drive_id="D", sync_status="SELECTED"))
    db.commit()
    res = await importer.transfer_next(db, client)
    assert res["queued"] == 1 and len(res["document_ids"]) == 1
    doc = db.query(Document).one()
    assert uploaded["key"] == f"raw-uploads/{doc.id}.pptx"                 # the key worker.py derives from the document
    assert uploaded["size"] == len(CONTENT["f2"])
    assert doc.filename == "Prelims & Finals 2024.pptx" and doc.source == "SHAREPOINT" and doc.sharepoint_file_id == "f2"
    assert doc.year is None            # the import year is not the deck's year: nothing invented is stored
    assert doc.uploaded_at is not None  # the ingestion date shown on the Uploads page
    assert not (tmp_path / "uploads").exists()                              # nothing left on the API instance's disk
    job = db.query(IngestionJob).one()
    assert job.document_id == doc.id and job.source_type == "MANUAL_UPLOAD" and job.status == "PENDING"
    assert db.query(SharePointFile).filter_by(id="f2").one().blob_url == f"https://r2.example/raw-uploads/{doc.id}.pptx"


@pytest.mark.anyio
async def test_r2_failure_fails_the_file_and_creates_nothing(db, client, monkeypatch):
    from backend.app.config import settings
    from backend.app.services.storage import r2_storage
    monkeypatch.setattr(settings, "INGESTION_MODE", "cloud_run_job")

    class BrokenR2:
        def upload_bytes(self, key, data, content_type=None):
            raise RuntimeError("R2 down")

    monkeypatch.setattr(r2_storage, "get_media_storage", lambda: BrokenR2())
    db.add(SharePointFile(id="f2", name="Prelims.pptx", etag="e1", drive_id="D", sync_status="SELECTED"))
    db.commit()
    res = await importer.transfer_next(db, client)
    assert res["failed"] == 1 and res["queued"] == 0
    assert db.query(SharePointFile).filter_by(id="f2").one().sync_status == "FAILED"
    assert db.query(IngestionJob).count() == 0 and db.query(Document).count() == 0


@pytest.mark.anyio
async def test_cloud_worker_mode_without_r2_fails_clearly(db, client, monkeypatch):
    from backend.app.config import settings
    monkeypatch.setattr(settings, "INGESTION_MODE", "cloud_run_job")
    db.add(SharePointFile(id="f2", name="Prelims.pptx", etag="e1", drive_id="D", sync_status="SELECTED"))
    db.commit()
    res = await importer.transfer_next(db, client)
    assert res["failed"] == 1
    assert "R2" in db.query(SharePointFile).filter_by(id="f2").one().error_message


@pytest.mark.anyio
async def test_failed_or_deleted_imports_can_be_done_again(db, client):
    # f1: queued earlier but its deck failed -> RETRY;  f2: queued earlier but the deck was deleted -> NEW;  f3: fine -> already imported
    for fid, name in (("f1", "Finals.pptx"), ("f2", "Prelims.pptx"), ("f3", "Semis.pdf")):
        db.add(SharePointFile(id=fid, name=name, etag="e1", sync_status="QUEUED"))
    db.add(Document(id="d1", filename="Finals.pptx", title="F", file_type="pptx", storage_path="x", sharepoint_file_id="f1", processing_status="FAILED"))
    db.add(Document(id="d3", filename="Semis.pdf", title="S", file_type="pdf", storage_path="x", sharepoint_file_id="f3", processing_status="COMPLETED"))
    db.commit()
    plan = await importer.plan_import(db, client, sel())
    status = {f["name"]: f["status"] for f in plan["files"]}
    assert status["Finals.pptx"] == "RETRY"
    assert status["Prelims.pptx"] == "NEW"
    assert status["Semis.pdf"] == "ALREADY_IMPORTED"


# ==============================================================================
# Streaming to R2, In-Flight Concurrency, and Batch Capping Tests
# ==============================================================================

class RecordingFakeR2:
    def __init__(self, slow_upload_delay: float = 0.0, fail_upload_part: bool = False):
        self.is_configured = True
        self.slow_upload_delay = slow_upload_delay
        self.fail_upload_part = fail_upload_part
        self.created_uploads = []
        self.uploaded_parts = []
        self.completed_uploads = []
        self.aborted_uploads = []
        self.uploaded_bytes = []
        self.deleted_objects = []
        self.current_pending_uploads = 0
        self.max_pending_uploads = 0

    def create_multipart_upload(self, key: str, content_type=None) -> str:
        upload_id = f"upload_{len(self.created_uploads) + 1}"
        self.created_uploads.append((key, upload_id))
        return upload_id

    def upload_part(self, key: str, upload_id: str, part_number: int, data) -> str:
        self.current_pending_uploads += 1
        if self.current_pending_uploads > self.max_pending_uploads:
            self.max_pending_uploads = self.current_pending_uploads
        if self.slow_upload_delay:
            import time
            time.sleep(self.slow_upload_delay)
        self.current_pending_uploads -= 1
        if self.fail_upload_part:
            raise RuntimeError("R2 upload_part network failure")
        etag = f'"etag_{part_number}"'
        self.uploaded_parts.append({
            "key": key,
            "upload_id": upload_id,
            "part_number": part_number,
            "size": len(data),
            "etag": etag,
            "data": bytes(data)
        })
        return etag

    def complete_multipart_upload(self, key: str, upload_id: str, parts) -> str:
        self.completed_uploads.append((key, upload_id, parts))
        return f"https://pub.r2.dev/{key}"

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        self.aborted_uploads.append((key, upload_id))

    def upload_bytes(self, key: str, data: bytes, content_type=None) -> str:
        self.uploaded_bytes.append((key, len(data), bytes(data)))
        return f"https://pub.r2.dev/{key}"

    def delete_object(self, key: str) -> None:
        self.deleted_objects.append(key)


@pytest.mark.anyio
async def test_streaming_40mb_stream_part_count_order_and_sha():
    """A 40 MB stream with a small test part_size has the right part count, parts in order,
    no part over part_size, and sha and size match the whole file."""
    # 40 chunks of 1 MB = 40 MB
    one_mb = b"A" * (1024 * 1024)
    part_size = 8 * 1024 * 1024  # 8 MB parts -> exactly 5 parts

    hasher = hashlib.sha256()
    for _ in range(40):
        hasher.update(one_mb)
    expected_sha = hasher.hexdigest()

    async def chunk_gen():
        for _ in range(40):
            yield one_mb

    async def stream_handler(request: httpx.Request):
        url = str(request.url)
        if url == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(200, content=chunk_gen())

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(stream_handler))
    sp_client = SharePointClient(tenant_id="t", client_id="c", client_secret="s", drive_id="D")
    sp_client._custom_http_client = mock_client

    fake_r2 = RecordingFakeR2()
    url, sha, size = await sp_client.stream_item_to_r2(
        drive_id="D", item_id="item_40m", key="raw-uploads/test40.pptx",
        max_bytes=50 * 1024 * 1024, r2=fake_r2, part_size=part_size
    )

    assert size == 40 * 1024 * 1024
    assert sha == expected_sha
    assert url == "https://pub.r2.dev/raw-uploads/test40.pptx"
    assert len(fake_r2.uploaded_parts) == 5
    # Parts in order
    assert [p["part_number"] for p in fake_r2.uploaded_parts] == [1, 2, 3, 4, 5]
    # No part over part_size
    assert all(p["size"] == part_size for p in fake_r2.uploaded_parts)
    assert len(fake_r2.completed_uploads) == 1
    assert len(fake_r2.completed_uploads[0][2]) == 5


@pytest.mark.anyio
async def test_streaming_overlap_upload_and_download_at_most_one_pending():
    """The upload of one part overlaps the download of the next (slow fake upload_part),
    but never more than one upload is pending per file."""
    # 3 parts of 2 MB each
    part_size = 2 * 1024 * 1024
    chunk_1mb = b"B" * (1024 * 1024)

    downloaded_chunks = []

    async def chunk_gen():
        for i in range(6):
            downloaded_chunks.append(i)
            yield chunk_1mb

    async def stream_handler(request: httpx.Request):
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(200, content=chunk_gen())

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(stream_handler))
    sp_client = SharePointClient(tenant_id="t", client_id="c", client_secret="s", drive_id="D")
    sp_client._custom_http_client = mock_client

    fake_r2 = RecordingFakeR2(slow_upload_delay=0.03)
    url, sha, size = await sp_client.stream_item_to_r2(
        drive_id="D", item_id="item_overlap", key="raw-uploads/test_overlap.pptx",
        max_bytes=10 * 1024 * 1024, r2=fake_r2, part_size=part_size
    )

    assert size == 6 * 1024 * 1024
    assert len(fake_r2.uploaded_parts) == 3
    # Exactly at most one pending upload task at all times
    assert fake_r2.max_pending_uploads == 1
    assert len(downloaded_chunks) == 6


@pytest.mark.anyio
async def test_streaming_file_under_one_part_uses_upload_bytes():
    """A file under one part uses upload_bytes and no multipart calls."""
    data = b"small file content under one part"
    part_size = 10 * 1024 * 1024

    async def stream_handler(request: httpx.Request):
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(200, content=data)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(stream_handler))
    sp_client = SharePointClient(tenant_id="t", client_id="c", client_secret="s", drive_id="D")
    sp_client._custom_http_client = mock_client

    fake_r2 = RecordingFakeR2()
    url, sha, size = await sp_client.stream_item_to_r2(
        drive_id="D", item_id="item_small", key="raw-uploads/small.pptx",
        max_bytes=20 * 1024 * 1024, r2=fake_r2, part_size=part_size
    )

    assert size == len(data)
    assert sha == hashlib.sha256(data).hexdigest()
    assert len(fake_r2.created_uploads) == 0  # No multipart calls
    assert len(fake_r2.uploaded_parts) == 0
    assert len(fake_r2.uploaded_bytes) == 1
    assert fake_r2.uploaded_bytes[0][0] == "raw-uploads/small.pptx"
    assert fake_r2.uploaded_bytes[0][1] == len(data)


@pytest.mark.anyio
async def test_streaming_over_max_bytes_aborts_multipart():
    """A stream over max_bytes aborts the multipart upload and raises the same error."""
    chunk = b"C" * (1024 * 1024)  # 1 MB
    max_bytes = 3 * 1024 * 1024      # 3 MB limit
    part_size = 1 * 1024 * 1024      # 1 MB part

    async def chunk_gen():
        for _ in range(5):  # 5 MB > 3 MB limit
            yield chunk

    async def stream_handler(request: httpx.Request):
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(200, content=chunk_gen())

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(stream_handler))
    sp_client = SharePointClient(tenant_id="t", client_id="c", client_secret="s", drive_id="D")
    sp_client._custom_http_client = mock_client

    fake_r2 = RecordingFakeR2()
    with pytest.raises(ValueError, match="limit"):
        await sp_client.stream_item_to_r2(
            drive_id="D", item_id="item_oversize", key="raw-uploads/oversize.pptx",
            max_bytes=max_bytes, r2=fake_r2, part_size=part_size
        )

    # Multipart was started when first 1 MB was filled, then aborted when 4 MB arrived
    assert len(fake_r2.created_uploads) == 1
    assert len(fake_r2.aborted_uploads) == 1
    assert fake_r2.aborted_uploads[0] == fake_r2.created_uploads[0]


@pytest.mark.anyio
async def test_streaming_midstream_exception_and_upload_part_failure_abort_multipart():
    """An exception mid-stream, and a failing upload_part, abort the multipart upload and leave no pending task."""
    part_size = 1024 * 1024
    chunk = b"D" * (1024 * 1024)

    # Subtest 1: Exception mid-stream
    async def broken_chunk_gen():
        yield chunk
        yield chunk
        raise RuntimeError("SharePoint stream connection dropped")

    async def broken_handler(request: httpx.Request):
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(200, content=broken_chunk_gen())

    mock_client1 = httpx.AsyncClient(transport=httpx.MockTransport(broken_handler))
    sp_client1 = SharePointClient(tenant_id="t", client_id="c", client_secret="s", drive_id="D")
    sp_client1._custom_http_client = mock_client1

    fake_r2_1 = RecordingFakeR2()
    with pytest.raises(RuntimeError, match="SharePoint stream connection dropped"):
        await sp_client1.stream_item_to_r2(
            drive_id="D", item_id="item_broken", key="raw-uploads/broken.pptx",
            max_bytes=10 * 1024 * 1024, r2=fake_r2_1, part_size=part_size
        )

    assert len(fake_r2_1.aborted_uploads) == 1

    # Subtest 2: Failing upload_part
    async def good_chunk_gen():
        yield chunk
        yield chunk

    async def good_handler(request: httpx.Request):
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(200, content=good_chunk_gen())

    mock_client2 = httpx.AsyncClient(transport=httpx.MockTransport(good_handler))
    sp_client2 = SharePointClient(tenant_id="t", client_id="c", client_secret="s", drive_id="D")
    sp_client2._custom_http_client = mock_client2

    fake_r2_2 = RecordingFakeR2(fail_upload_part=True)
    with pytest.raises(RuntimeError, match="R2 upload_part network failure"):
        await sp_client2.stream_item_to_r2(
            drive_id="D", item_id="item_r2_fail", key="raw-uploads/r2_fail.pptx",
            max_bytes=10 * 1024 * 1024, r2=fake_r2_2, part_size=part_size
        )

    assert len(fake_r2_2.aborted_uploads) == 1


@pytest.mark.anyio
async def test_transfer_next_duplicate_deletes_staged_r2_and_creates_no_document(db, client, monkeypatch):
    """A duplicate hash deletes the staged object, creates no Document, and marks SKIPPED_DUPLICATE."""
    from backend.app.config import settings
    from backend.app.services.storage import r2_storage
    monkeypatch.setattr(settings, "INGESTION_MODE", "cloud_run_job")
    fake_r2 = RecordingFakeR2()
    monkeypatch.setattr(r2_storage, "get_media_storage", lambda: fake_r2)

    # Pre-existing Document with the same hash as f2
    f2_sha = hashlib.sha256(CONTENT["f2"]).hexdigest()
    db.add(Document(id="existing_doc", filename="old.pptx", title="Old", file_type="pptx", storage_path="x",
                    file_sha256=f2_sha))
    db.add(SharePointFile(id="f2", name="Prelims.pptx", etag="e1", drive_id="D", sync_status="SELECTED"))
    db.commit()

    res = await importer.transfer_next(db, client)
    assert res["skipped_duplicate"] == 1
    assert res["queued"] == 0
    # Staged object was deleted from R2
    assert len(fake_r2.deleted_objects) == 1
    assert fake_r2.deleted_objects[0].startswith("raw-uploads/")
    # No new Document created
    assert db.query(Document).count() == 1
    row = db.query(SharePointFile).filter_by(id="f2").one()
    assert row.sync_status == "SKIPPED_DUPLICATE"
    assert row.match_document_id == "existing_doc"


@pytest.mark.anyio
async def test_transfer_next_document_uses_pregenerated_id_and_returned_url(db, client, monkeypatch):
    """The new Document uses the pre-generated id and the returned URL, size and sha."""
    from backend.app.config import settings
    from backend.app.services.storage import r2_storage
    monkeypatch.setattr(settings, "INGESTION_MODE", "cloud_run_job")
    fake_r2 = RecordingFakeR2()
    monkeypatch.setattr(r2_storage, "get_media_storage", lambda: fake_r2)

    db.add(SharePointFile(id="f2", name="Prelims.pptx", etag="e1", drive_id="D", sync_status="SELECTED"))
    db.commit()

    res = await importer.transfer_next(db, client)
    assert res["queued"] == 1
    doc_id = res["document_ids"][0]
    doc = db.query(Document).filter_by(id=doc_id).one()

    expected_sha = hashlib.sha256(CONTENT["f2"]).hexdigest()
    assert doc.file_sha256 == expected_sha
    assert doc.file_size_bytes == len(CONTENT["f2"])
    assert doc.blob_url == f"https://pub.r2.dev/raw-uploads/{doc_id}.pptx"
    assert doc.storage_path.endswith(f"{doc_id}.pptx")
    assert db.query(SharePointFile).filter_by(id="f2").one().blob_url == doc.blob_url


@pytest.mark.anyio
async def test_transfer_next_25_files_concurrency_and_failure_resilience(db, monkeypatch):
    """A 25-file transfer_next runs files concurrently, never exceeds SHAREPOINT_MAX_INFLIGHT active streams,
    and a batch where one file fails still queues the others with correct counts."""
    # Create 25 files
    for i in range(25):
        db.add(SharePointFile(
            id=f"file_{i:02d}", name=f"Deck_{i:02d}.pptx", etag=f"e_{i}",
            drive_id="D", sync_status="SELECTED"
        ))
    db.commit()

    active_streams = 0
    max_active_streams = 0

    class MockClient:
        async def download_item_capped(self, drive_id, item_id, max_bytes):
            nonlocal active_streams, max_active_streams
            active_streams += 1
            if active_streams > max_active_streams:
                max_active_streams = active_streams
            try:
                import anyio
                await anyio.sleep(0.01)
                if item_id == "file_13":
                    raise httpx.HTTPStatusError("Server error", request=None, response=httpx.Response(500))
                return f"content for {item_id}".encode()
            finally:
                active_streams -= 1

    mock_client = MockClient()
    res = await importer.transfer_next(db, mock_client, limit=25)

    assert res["queued"] == 24
    assert res["failed"] == 1
    assert len(res["job_ids"]) == 24
    assert max_active_streams <= importer.SHAREPOINT_MAX_INFLIGHT
    assert db.query(SharePointFile).filter_by(id="file_13").one().sync_status == "FAILED"
    assert db.query(SharePointFile).filter(SharePointFile.sync_status == "QUEUED").count() == 24


@pytest.mark.anyio
async def test_concurrent_transfer_next_calls_never_claim_same_row(db):
    """Two transfer_next calls at once never claim the same row."""
    # Create 6 files
    for i in range(6):
        db.add(SharePointFile(
            id=f"cf_{i}", name=f"Concurrent_{i}.pptx", etag=f"e_{i}",
            drive_id="D", sync_status="SELECTED"
        ))
    db.commit()

    class MockClient:
        async def download_item_capped(self, drive_id, item_id, max_bytes):
            import anyio
            await anyio.sleep(0.01)
            return f"content for {item_id}".encode()

    mock_client = MockClient()
    res1, res2 = await asyncio.gather(
        importer.transfer_next(db, mock_client, limit=3),
        importer.transfer_next(db, mock_client, limit=3)
    )

    assert res1["queued"] + res2["queued"] == 6
    # No overlapping document IDs or job IDs
    assert set(res1["document_ids"]).isdisjoint(set(res2["document_ids"]))
    assert set(res1["job_ids"]).isdisjoint(set(res2["job_ids"]))
    assert db.query(SharePointFile).filter(SharePointFile.sync_status == "QUEUED").count() == 6


def test_transfer_next_limit_validation():
    """limit=25 is accepted and limit=26 is rejected."""
    from fastapi.testclient import TestClient
    from backend.app.config import settings
    from backend.app.main import app

    test_client = TestClient(app)
    endpoint = f"{settings.API_V1_STR}/sharepoint/transfer-next"
    # limit=25 should pass validation (422 would indicate validation rejection)
    resp25 = test_client.post(f"{endpoint}?limit=25")
    assert resp25.status_code != 422

    # limit=26 should fail FastAPI Query validation with 422
    resp26 = test_client.post(f"{endpoint}?limit=26")
    assert resp26.status_code == 422


@pytest.mark.anyio
async def test_stage_and_plan_import_batch_cap_25(db):
    """Where an import batch is staged (/import and its plan), refuse or truncate beyond
    SHAREPOINT_BATCH_MAX files and return a clear message and the number left over."""
    # 30 files in selection
    items = []
    for i in range(30):
        fid = f"bc_file_{i:02d}"
        fname = f"BatchCap_{i:02d}.pptx"
        items.append({"id": fid, "kind": "file"})

    class MockClient30:
        async def get_item(self, drive_id, item_id):
            return {
                "id": item_id, "name": f"{item_id}.pptx", "size": 1000,
                "eTag": "etag1", "cTag": "ctag1", "webUrl": f"https://sp/{item_id}",
                "parentReference": {"path": "/drives/D/root:/Quizzes"},
                "lastModifiedDateTime": "2024-05-01T10:00:00Z",
                "file": {"hashes": {"quickXorHash": "xor1"}}
            }

    mock_client = MockClient30()
    sel_obj = importer.Selection(drive_id="D", items=items)

    plan = await importer.plan_import(db, mock_client, sel_obj)
    assert plan["total_found"] == 30
    assert plan["batch_capped"] is True
    assert plan["to_import"] == 25
    assert plan["leftover"] == 5
    assert plan["message"] == "25 of 30 selected, import the rest in the next batch"

    staged = await importer.stage_import(db, mock_client, sel_obj)
    assert staged["selected"] == 25
    assert staged["batch_capped"] is True
    assert staged["leftover"] == 5
    assert staged["message"] == "25 of 30 selected, import the rest in the next batch"
    assert db.query(SharePointFile).filter(SharePointFile.sync_status == "SELECTED").count() == 25


