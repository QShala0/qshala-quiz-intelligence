"""Paging: the list endpoint reports the real total in a header, pages never overlap, and the body stays a plain list."""
import uuid

from fastapi.testclient import TestClient

from backend.app.database import SessionLocal
from backend.app.main import app
from backend.app.models.document import Document
from backend.app.models.question import Question


def _seed(n, topic):
    db = SessionLocal()
    try:
        doc = Document(title=f"paging-{topic}", filename="p.pptx", file_type="pptx", storage_path="x", processing_status="COMPLETED")
        db.add(doc)
        db.commit()
        db.add_all([Question(id=str(uuid.uuid4()), document_id=doc.id, question_text=f"Paging question {i} about {topic}",
                             answer=f"A{i}", topic=topic, subtopic="s", difficulty="Easy", content_hash=f"{topic}{i}",
                             duplicate_status="UNIQUE") for i in range(n)])
        db.commit()
    finally:
        db.close()


def test_total_header_counts_every_match_and_pages_do_not_overlap():
    topic = f"PagingTopic{uuid.uuid4().hex[:6]}"
    _seed(25, topic)
    c = TestClient(app)
    seen = []
    for off in (0, 10, 20):
        r = c.get("/api/v1/questions", params={"topic": topic, "limit": 10, "offset": off})
        assert r.status_code == 200 and isinstance(r.json(), list)
        assert r.headers["X-Total-Count"] == "25"
        seen += [q["id"] for q in r.json()]
    assert len(seen) == 25 and len(set(seen)) == 25


def test_total_header_respects_the_same_filters_as_the_list():
    topic = f"PagingTopic{uuid.uuid4().hex[:6]}"
    _seed(7, topic)
    r = TestClient(app).get("/api/v1/questions", params={"topic": topic, "difficulty": "Hard", "limit": 5})
    assert r.json() == [] and r.headers["X-Total-Count"] == "0"
    r = TestClient(app).get("/api/v1/questions", params={"topic": topic, "difficulty": "Easy", "limit": 5})
    assert len(r.json()) == 5 and r.headers["X-Total-Count"] == "7"


def test_total_header_is_exposed_to_browsers_across_origins():
    r = TestClient(app).get("/api/v1/questions", params={"limit": 1}, headers={"Origin": "http://localhost:3000"})
    assert "x-total-count" in r.headers.get("access-control-expose-headers", "").lower()
