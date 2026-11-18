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
}


def make_handler(state):
    def handler(request: httpx.Request):
        url = str(request.url)
        if url == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        state.setdefault("calls", []).append(url)
        if url.startswith(f"{BASE}/drives/D/root/children"):
            return httpx.Response(200, json=TREE["root"])
        for fid in ("fold1", "fold2"):
            if url.startswith(f"{BASE}/drives/D/items/{fid}/children"):
                if state.get("throttle_once") and not state.get("throttled"):
                    state["throttled"] = True
                    return httpx.Response(429, headers={"Retry-After": "1"})
                return httpx.Response(200, json=TREE[fid])
        if url.startswith(f"{BASE}/drives/D/root:/2024"):
            return httpx.Response(200, json={"id": "fold1", "name": "2024", "folder": {}, "parentReference": {"path": "/drives/D/root:"}})
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
async def test_classification_already_imported_changed_and_possible_duplicate(db, client):
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
    assert status["Semis.pdf"] == "POSSIBLE_DUPLICATE"
    assert status["Old.pptx"] == "NEW"
    assert plan["to_import"] == 2                       # CHANGED + NEW; the duplicate is held back by default
    with_dups = await importer.plan_import(db, client, sel(include_possible_duplicates=True))
    assert with_dups["to_import"] == 3


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
