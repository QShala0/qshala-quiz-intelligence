"""Slices of one upload can now arrive at the same time and in any order (after slice 0). The server must still produce
exactly one deck, byte for byte what was sent, and report it from whichever slice finished the upload."""
import os
import random
import sys
import threading
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.main import app
from backend.app.config import settings
from backend.app.database import SessionLocal
from backend.app.models.document import Document
from backend.app.models.upload_session import UploadSession
from backend.app.routers import documents as documents_router
from backend.app.services.ingestion import cancellation
from backend.app.services.storage import r2_storage

API = "/api/v1"
MiB = 1024 * 1024


class FakeR2:
    is_configured = True

    def __init__(self):
        self.parts, self.staged, self.completed, self.aborted = {}, {}, [], []

    def create_multipart_upload(self, key, content_type=None):
        return f"mp-{uuid.uuid4().hex[:8]}"

    def upload_part(self, key, upload_id, part_number, data):
        self.parts[part_number] = bytes(data)
        return f"etag-{part_number}"

    def upload_bytes(self, key, data, content_type=None):
        self.staged[key] = bytes(data)
        return key

    def download(self, key):
        return self.staged[key]

    def delete(self, keys):
        for k in keys:
            self.staged.pop(k, None)

    def delete_prefix(self, prefix):
        for k in [k for k in self.staged if k.startswith(prefix)]:
            del self.staged[k]
        return 0

    def complete_multipart_upload(self, key, upload_id, parts):
        self.completed.append([p["PartNumber"] for p in parts])

    def abort_multipart_upload(self, key, upload_id):
        self.aborted.append(key)

    def assembled(self):
        return b"".join(self.parts[n] for n in sorted(self.parts))


@pytest.fixture()
def cleanup():
    state = {"upload_ids": [], "filenames": []}
    yield state
    db = SessionLocal()
    for uid in state["upload_ids"]:
        db.query(UploadSession).filter(UploadSession.upload_id == uid).delete()
    for fn in state["filenames"]:
        for d in db.query(Document).filter(Document.filename == fn).all():
            if d.storage_path and os.path.exists(d.storage_path):
                os.remove(d.storage_path)
            db.delete(d)
    db.commit()
    db.close()
    for uid in state["upload_ids"]:
        cancellation.CANCELLED_UPLOADS.discard(uid)
        cancellation.remove_chunk_dir(uid)


@pytest.fixture()
def no_ingestion(monkeypatch):
    scheduled = []
    monkeypatch.setattr(documents_router, "_schedule_ingestion", lambda *a, **k: scheduled.append(1))
    return scheduled


def _send(client, uid, filename, index, total, blob):
    return client.post(f"{API}/documents/upload-chunk", files={"chunk": (filename, blob)},
                       data={"upload_id": uid, "chunk_index": index, "total_chunks": total, "filename": filename})


def _slices(data, size):
    return [data[i:i + size] for i in range(0, len(data), size)]


def _new_upload(cleanup):
    uid = f"up-{uuid.uuid4().hex[:12]}"
    fn = f"par-{uuid.uuid4().hex[:8]}.pptx"
    cleanup["upload_ids"].append(uid)
    cleanup["filenames"].append(fn)
    return uid, fn


# ------------------------------------------------------------------------------------------------ R2 path

def test_r2_fast_path_slices_arriving_out_of_order_assemble_exactly(monkeypatch, cleanup, no_ingestion):
    fake = FakeR2()
    monkeypatch.setattr(settings, "INGESTION_MODE", "cloud_run_job")
    monkeypatch.setattr(r2_storage, "get_r2_service", lambda: fake)
    data = os.urandom(5 * MiB * 3 + 123)
    parts = _slices(data, 5 * MiB)
    uid, fn = _new_upload(cleanup)
    client = TestClient(app)
    order = [0, 2, 3, 1]                                    # slice 0 first, the rest in a different order
    replies = {i: _send(client, uid, fn, i, len(parts), parts[i]) for i in order}
    assert all(r.status_code == 200 for r in replies.values())
    done = [i for i, r in replies.items() if r.json().get("status") == "COMPLETED"]
    assert done == [1]                                      # the slice that arrived last finished the upload
    assert fake.assembled() == data
    assert fake.completed == [[1, 2, 3, 4]]
    assert len(no_ingestion) == 1


def test_r2_staged_small_slices_arriving_out_of_order_assemble_exactly(monkeypatch, cleanup, no_ingestion):
    fake = FakeR2()
    monkeypatch.setattr(settings, "INGESTION_MODE", "cloud_run_job")
    monkeypatch.setattr(r2_storage, "get_r2_service", lambda: fake)
    data = os.urandom(2 * MiB * 6 + 777)                   # slices under 5 MiB take the staging path
    parts = _slices(data, 2 * MiB)
    uid, fn = _new_upload(cleanup)
    client = TestClient(app)
    rng = random.Random(7)
    rest = list(range(1, len(parts)))
    rng.shuffle(rest)
    replies = {}
    for i in [0] + rest:
        r = _send(client, uid, fn, i, len(parts), parts[i])
        assert r.status_code == 200, r.text
        replies[i] = r.json()
    completed = [i for i, body in replies.items() if body.get("status") == "COMPLETED"]
    assert completed == [([0] + rest)[-1]]                  # exactly one response carries the deck: the last to arrive
    assert fake.assembled() == data
    assert len(fake.completed) == 1 and fake.completed[0] == sorted(fake.completed[0])
    assert fake.staged == {}                                # nothing left behind in staging
    assert len(no_ingestion) == 1


def test_r2_a_repeated_slice_does_not_create_a_second_deck(monkeypatch, cleanup, no_ingestion):
    fake = FakeR2()
    monkeypatch.setattr(settings, "INGESTION_MODE", "cloud_run_job")
    monkeypatch.setattr(r2_storage, "get_r2_service", lambda: fake)
    data = os.urandom(5 * MiB * 2 + 5)
    parts = _slices(data, 5 * MiB)
    uid, fn = _new_upload(cleanup)
    client = TestClient(app)
    _send(client, uid, fn, 0, 3, parts[0])
    _send(client, uid, fn, 2, 3, parts[2])
    first = _send(client, uid, fn, 1, 3, parts[1]).json()
    again = _send(client, uid, fn, 1, 3, parts[1]).json()   # a retry after a lost response
    assert first["status"] == "COMPLETED" and again["status"] == "COMPLETED"
    assert first["document"]["id"] == again["document"]["id"]
    db = SessionLocal()
    assert db.query(Document).filter(Document.filename == fn).count() == 1
    db.close()


# ------------------------------------------------------------------------------------------------ local-disk path

def test_local_path_slices_sent_at_the_same_time_make_exactly_one_deck(monkeypatch, cleanup, no_ingestion):
    monkeypatch.setattr(settings, "INGESTION_MODE", "in_process")
    monkeypatch.setattr(r2_storage, "get_r2_service", lambda: type("Off", (), {"is_configured": False})())
    data = os.urandom(MiB * 8 + 321)
    parts = _slices(data, MiB)
    total = len(parts)
    uid, fn = _new_upload(cleanup)
    assert _send(TestClient(app), uid, fn, 0, total, parts[0]).status_code == 200   # slice 0 goes alone

    barrier = threading.Barrier(total - 1)
    results, errors = {}, []

    def worker(i):
        try:
            c = TestClient(app)
            barrier.wait(timeout=10)
            results[i] = _send(c, uid, fn, i, total, parts[i])
        except Exception as e:                              # pragma: no cover - reported below
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(1, total)]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert not errors, errors
    assert all(r.status_code == 200 for r in results.values()), [r.text for r in results.values()]
    carrying = [r.json() for r in results.values() if r.json().get("status") == "COMPLETED"]
    assert len(carrying) == 1                               # one response carries the deck, the rest say "received"
    db = SessionLocal()
    docs = db.query(Document).filter(Document.filename == fn).all()
    assert len(docs) == 1
    assert Path(docs[0].storage_path).read_bytes() == data  # stitched in order, nothing truncated
    db.close()
    assert len(no_ingestion) == 1


def test_local_path_a_slice_still_being_written_is_never_counted(monkeypatch, cleanup, no_ingestion):
    monkeypatch.setattr(settings, "INGESTION_MODE", "in_process")
    monkeypatch.setattr(r2_storage, "get_r2_service", lambda: type("Off", (), {"is_configured": False})())
    uid, fn = _new_upload(cleanup)
    chunk_dir = settings.STORAGE_DIR / "tmp_chunks" / uid
    chunk_dir.mkdir(parents=True, exist_ok=True)
    (chunk_dir / "chunk_00001.part.tmp").write_bytes(b"half written")   # another slice mid-write
    r = _send(TestClient(app), uid, fn, 0, 2, b"A" * 100)
    assert r.status_code == 200 and r.json()["status"] == "CHUNK_RECEIVED"   # 1 finished slice of 2, not 2


# ------------------------------------------------------------------------------------------------ slices of one upload take turns

def test_slot_lets_one_slice_of_an_upload_run_at_a_time_and_cleans_up():
    import asyncio

    async def go():
        running, peak, order = 0, 0, []

        async def slice_work(name, upload_id):
            nonlocal running, peak
            async with documents_router._upload_slot(upload_id):
                running += 1
                peak = max(peak, running)
                await asyncio.sleep(0.02)
                order.append(name)
                running -= 1

        await asyncio.gather(*[slice_work(f"a{i}", "same") for i in range(5)])
        assert peak == 1 and len(order) == 5
        assert documents_router._UPLOAD_SLOTS == {}                    # nothing left behind

        running, peak = 0, 0
        await asyncio.gather(slice_work("x", "one"), slice_work("y", "two"))
        assert peak == 2                                                # different uploads do not wait for each other
        assert documents_router._UPLOAD_SLOTS == {}

    asyncio.run(go())


def test_slot_is_released_when_a_slice_fails():
    import asyncio

    async def go():
        with pytest.raises(RuntimeError):
            async with documents_router._upload_slot("boom"):
                raise RuntimeError("R2 down")
        async with documents_router._upload_slot("boom"):               # the next slice is not stuck behind the failure
            pass
        assert documents_router._UPLOAD_SLOTS == {}

    asyncio.run(go())


def test_r2_path_slices_sent_at_the_same_time_are_stored_one_after_another(monkeypatch, cleanup, no_ingestion):
    import time

    class SlowFakeR2(FakeR2):
        active, peak = 0, 0

        def upload_part(self, key, upload_id, part_number, data):
            SlowFakeR2.active += 1
            SlowFakeR2.peak = max(SlowFakeR2.peak, SlowFakeR2.active)
            time.sleep(0.05)                                            # a real R2 upload takes time
            try:
                return super().upload_part(key, upload_id, part_number, data)
            finally:
                SlowFakeR2.active -= 1

    fake = SlowFakeR2()
    monkeypatch.setattr(settings, "INGESTION_MODE", "cloud_run_job")
    monkeypatch.setattr(r2_storage, "get_r2_service", lambda: fake)
    data = os.urandom(5 * MiB * 5 + 99)
    parts = _slices(data, 5 * MiB)
    total = len(parts)
    uid, fn = _new_upload(cleanup)
    results, errors = {}, []
    with TestClient(app) as client:                                     # one event loop for every request, like the server
        assert _send(client, uid, fn, 0, total, parts[0]).status_code == 200
        barrier = threading.Barrier(total - 1)

        def worker(i):
            try:
                barrier.wait(timeout=10)
                results[i] = _send(client, uid, fn, i, total, parts[i])
            except Exception as e:                                      # pragma: no cover
                errors.append(repr(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(1, total)]
        [t.start() for t in threads]
        [t.join(timeout=60) for t in threads]
    assert not errors, errors
    assert all(r.status_code == 200 for r in results.values()), [r.text for r in results.values()]
    assert SlowFakeR2.peak == 1                                         # never two part uploads of one upload at once
    assert fake.assembled() == data                                     # and no slice was lost to a racing update
    assert len([r for r in results.values() if r.json().get("status") == "COMPLETED"]) == 1
    assert len(no_ingestion) == 1
