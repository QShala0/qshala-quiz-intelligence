"""Deleting a deck or the whole question bank can be undone."""
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.database import SessionLocal
from backend.app.main import app
from backend.app.models.document import Document, Slide
from backend.app.models.qshala_tag import QshalaTag
from backend.app.models.question import Question
from backend.app.services import trash


def _seed_deck(n=4):
    db = SessionLocal()
    try:
        doc = Document(title=f"trash-deck-{uuid.uuid4().hex[:6]}", filename="t.pptx", file_type="pptx", storage_path="/nonexistent/t.pptx",
                       processing_status="COMPLETED")
        db.add(doc)
        db.commit()
        slide = Slide(document_id=doc.id, slide_number=1, slide_type="QUESTION")
        db.add(slide)
        db.commit()
        qids = []
        for i in range(n):
            q = Question(id=str(uuid.uuid4()), document_id=doc.id, slide_id=slide.id, question_text=f"Trash question {i} {uuid.uuid4().hex[:6]}",
                         answer=f"A{i}", topic="TrashTopic", subtopic="s", difficulty="Easy", content_hash=uuid.uuid4().hex,
                         duplicate_status="UNIQUE", tags=["alpha", "beta"])
            db.add(q)
            qids.append(q.id)
        db.commit()
        db.add_all([QshalaTag(id=str(uuid.uuid4()), question_id=qid, taxonomy_version="v4", topic_l1="Finance",
                              subtopic_l2="Banks and Banking", status="confirmed", source="llm") for qid in qids])
        db.commit()
        return doc.id, qids
    finally:
        db.close()


def _count(sql, **p):
    db = SessionLocal()
    try:
        return db.execute(text(sql), p).scalar()
    finally:
        db.close()


def test_deleting_a_deck_can_be_undone_with_its_questions_slides_and_tags():
    doc_id, qids = _seed_deck()
    c = TestClient(app)
    r = c.delete(f"/api/v1/documents/{doc_id}")
    assert r.status_code == 200 and r.json()["trash_id"]
    tid = r.json()["trash_id"]
    assert _count("select count(*) from questions where document_id = :d", d=doc_id) == 0
    assert _count("select count(*) from documents where id = :d", d=doc_id) == 0
    assert any(i["id"] == tid for i in c.get("/api/v1/trash").json()["items"])

    back = c.post(f"/api/v1/trash/{tid}/restore")
    assert back.status_code == 200 and back.json()["restored"]["questions"] == 4
    assert _count("select count(*) from documents where id = :d", d=doc_id) == 1
    assert _count("select count(*) from slides where document_id = :d", d=doc_id) == 1
    assert _count("select count(*) from questions where document_id = :d", d=doc_id) == 4
    assert _count("select count(*) from qshala_tags where question_id in (select id from questions where document_id = :d)", d=doc_id) == 4
    assert _count("select answer from questions where id = :q", q=qids[0]) == "A0"          # content is back exactly


def test_restore_twice_does_not_duplicate_and_never_overwrites_newer_edits():
    doc_id, qids = _seed_deck(3)
    c = TestClient(app)
    tid = c.delete(f"/api/v1/documents/{doc_id}").json()["trash_id"]
    assert c.post(f"/api/v1/trash/{tid}/restore").json()["total"] > 0
    db = SessionLocal()
    try:
        db.execute(text("update questions set answer = 'EDITED' where id = :q"), {"q": qids[0]})
        db.commit()
    finally:
        db.close()
    again = c.post(f"/api/v1/trash/{tid}/restore").json()
    assert again["total"] == 0
    assert _count("select count(*) from questions where document_id = :d", d=doc_id) == 3
    assert _count("select answer from questions where id = :q", q=qids[0]) == "EDITED"


def test_bulk_delete_of_one_decks_questions_can_be_undone():
    doc_id, qids = _seed_deck(5)
    c = TestClient(app)
    r = c.delete("/api/v1/questions", params={"document_id": doc_id})
    assert r.json()["deleted_count"] == 5 and r.json()["trash_id"]
    assert _count("select count(*) from questions where document_id = :d", d=doc_id) == 0
    assert c.post(f"/api/v1/trash/{r.json()['trash_id']}/restore").json()["restored"]["questions"] == 5
    assert _count("select count(*) from questions where document_id = :d", d=doc_id) == 5


def test_whole_bank_purge_can_be_undone_and_a_bare_delete_is_still_refused():
    doc_id, qids = _seed_deck(3)
    c = TestClient(app)
    assert c.delete("/api/v1/questions").status_code == 400
    before = _count("select count(*) from questions")
    r = c.delete("/api/v1/questions", params={"all": "true"})
    assert r.json()["deleted_count"] == before and _count("select count(*) from questions") == 0
    c.post(f"/api/v1/trash/{r.json()['trash_id']}/restore")
    assert _count("select count(*) from questions") == before


def test_unknown_snapshot_is_a_404_and_expired_snapshots_are_dropped():
    c = TestClient(app)
    assert c.post("/api/v1/trash/doesnotexist/restore").status_code == 404
    doc_id, _ = _seed_deck(2)
    tid = c.delete(f"/api/v1/documents/{doc_id}").json()["trash_id"]
    db = SessionLocal()
    try:
        db.execute(text("update trash_index set created_at = '2000-01-01 00:00:00' where id = :i"), {"i": tid})
        db.commit()
        assert tid in trash.purge_expired(db)
        db.commit()
        from sqlalchemy import inspect
        names = set(inspect(db.get_bind()).get_table_names())
        assert not any(n.startswith(f"trash_{tid}_") for n in names)
    finally:
        db.close()
    assert c.post(f"/api/v1/trash/{tid}/restore").status_code == 404
