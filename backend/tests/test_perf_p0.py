import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from backend.app.main import app
from backend.app.database import engine, SessionLocal
from backend.app.models.document import Document, Slide
from backend.app.models.question import Question

@pytest.fixture
def client():
    return TestClient(app)

def test_p0_1_list_documents_does_not_contain_slides_and_has_low_query_count(client):
    """P0-1: list_documents must not include slides and must not issue N+1 queries for slides."""
    db = SessionLocal()
    try:
        # Seed 3 documents each having 2 slides
        for i in range(3):
            d_id = f"test-p0-doc-{i}-{uuid.uuid4()}"
            doc = Document(
                id=d_id,
                filename=f"deck_{i}.pptx",
                title=f"Deck {i}",
                year=2024,
                file_type="pptx",
                storage_path=f"/fake/{i}.pptx",
                file_size_bytes=5000,
                processing_status="COMPLETED",
                slide_count=2,
                question_count=0
            )
            db.add(doc)
            for s_num in (1, 2):
                slide = Slide(
                    id=str(uuid.uuid4()),
                    document_id=d_id,
                    slide_number=s_num,
                    slide_type="QUESTION",
                    extracted_text=f"Slide text {s_num}"
                )
                db.add(slide)
        db.commit()
    finally:
        db.close()

    statements = []
    def _capture_stmt(conn, cur, st, p, ctx, many):
        statements.append(st)

    event.listen(engine, "before_cursor_execute", _capture_stmt)
    try:
        res = client.get("/api/v1/documents?limit=10")
        assert res.status_code == 200
        data = res.json()
        assert len(data) >= 3

        # Assert no slides returned in document list item
        for item in data:
            assert "slides" not in item, f"Unexpected 'slides' key in item: {item.get('title')}"

        # Assert query count <= 2 (1 list query + at most 1 optional metadata query)
        assert len(statements) <= 2, f"Expected <= 2 statements, got {len(statements)}: {statements}"
    finally:
        event.remove(engine, "before_cursor_execute", _capture_stmt)


def test_p0_2_duplicate_count_endpoint(client):
    """P0-2: /questions/duplicates/count returns accurate count in 1 query and is not shadowed."""
    db = SessionLocal()
    try:
        doc = db.query(Document).first()
        doc_id = doc.id if doc else "dummy-doc-id"
        # Seed 2 possible duplicates
        for i in range(2):
            q = Question(
                id=str(uuid.uuid4()),
                document_id=doc_id,
                question_text=f"Duplicate test question {i}",
                answer="Test answer",
                topic="General Knowledge",
                duplicate_status="POSSIBLE_DUPLICATE",
                duplicate_similarity=0.95
            )
            db.add(q)
        db.commit()
    finally:
        db.close()

    statements = []
    def _capture_stmt(conn, cur, st, p, ctx, many):
        statements.append(st)

    event.listen(engine, "before_cursor_execute", _capture_stmt)
    try:
        res = client.get("/api/v1/questions/duplicates/count")
        assert res.status_code == 200
        data = res.json()
        assert "count" in data
        assert isinstance(data["count"], int)
        assert data["count"] >= 2
        # Assert exactly 1 query
        assert len(statements) == 1, f"Expected 1 query, got {len(statements)}: {statements}"
    finally:
        event.remove(engine, "before_cursor_execute", _capture_stmt)
