"""Each browser only ever adopts its own upload batch."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.app.database import SessionLocal
from backend.app.main import app
from backend.app.models.upload_batch import UploadBatch


def _waiting_batch(client_id, minutes_ago=0):
    db = SessionLocal()
    try:
        b = UploadBatch(total_expected=1, client_id=client_id, items=[{"client_item_id": f"c-{client_id}", "filename": "a.pptx", "size_bytes": 1}],
                        created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
                        last_seen_at=datetime.now(timezone.utc))
        db.add(b)
        db.commit()
        return b.id
    finally:
        db.close()


def test_each_client_only_sees_its_own_active_batch():
    mine, theirs = _waiting_batch("client-A-1"), _waiting_batch("client-B-1")
    c = TestClient(app)
    assert c.get("/api/v1/jobs/upload-batches/active", params={"client_id": "client-A-1"}).json() == {"batch_id": mine}
    assert c.get("/api/v1/jobs/upload-batches/active", params={"client_id": "client-B-1"}).json() == {"batch_id": theirs}
    assert c.get("/api/v1/jobs/upload-batches/active", params={"client_id": "client-nobody"}).json() == {"batch_id": None}


def test_the_newest_batch_of_a_colleague_is_not_adopted():
    older_mine = _waiting_batch("client-C-1", minutes_ago=3)
    _waiting_batch("client-D-1", minutes_ago=0)                  # newer batch, someone else's
    r = TestClient(app).get("/api/v1/jobs/upload-batches/active", params={"client_id": "client-C-1"})
    assert r.json() == {"batch_id": older_mine}


def test_creating_a_batch_stores_the_client_id_and_old_clients_still_work():
    c = TestClient(app)
    files = [{"client_item_id": "x1", "filename": "deck.pptx", "size_bytes": 10}]
    bid = c.post("/api/v1/jobs/upload-batch", json={"files": files, "client_id": "client-E-1"}).json()["batch_id"]
    db = SessionLocal()
    try:
        assert db.query(UploadBatch).filter(UploadBatch.id == bid).one().client_id == "client-E-1"
    finally:
        db.close()
    assert c.get("/api/v1/jobs/upload-batches/active", params={"client_id": "client-E-1"}).json() == {"batch_id": bid}
    old = c.post("/api/v1/jobs/upload-batch", json={"files": files})          # a client that sends no client_id
    assert old.status_code == 200
