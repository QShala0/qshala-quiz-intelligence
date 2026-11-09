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


def test_p0_3_duplicate_candidates_query_count_and_payload(client):
    """P0-3: list_duplicate_candidates issues <= 8 queries even with many duplicate pairs."""
    db = SessionLocal()
    try:
        doc = db.query(Document).first()
        doc_id = doc.id if doc else "dummy-doc-id"

        # Create 5 parent questions
        parents = []
        for i in range(5):
            p = Question(
                id=str(uuid.uuid4()),
                document_id=doc_id,
                question_text=f"Parent question {i}",
                answer=f"Parent answer {i}",
                topic="General Knowledge",
                duplicate_status="UNIQUE"
            )
            db.add(p)
            parents.append(p)
        db.commit()

        # Create 10 duplicate questions pointing to the parents with high similarity
        for i in range(10):
            parent = parents[i % len(parents)]
            dup = Question(
                id=str(uuid.uuid4()),
                document_id=doc_id,
                question_text=f"Duplicate question {i}",
                answer=f"Duplicate answer {i}",
                topic="General Knowledge",
                duplicate_status="POSSIBLE_DUPLICATE",
                duplicate_similarity=0.98 + (i * 0.001),
                duplicate_of_id=parent.id
            )
            db.add(dup)
        db.commit()
    finally:
        db.close()

    statements = []
    def _capture_stmt(conn, cur, st, p, ctx, many):
        statements.append(st)

    event.listen(engine, "before_cursor_execute", _capture_stmt)
    try:
        res = client.get("/api/v1/questions/duplicates?limit=10")
        assert res.status_code == 200
        data = res.json()
        assert len(data) >= 10

        # Assert duplicate_of_text is populated correctly for all rows with duplicate_of_id
        checked_with_dup = 0
        for item in data:
            if item.get("duplicate_of_id"):
                assert item.get("duplicate_of_text") is not None, f"Expected text for {item['id']}"
                checked_with_dup += 1
        assert checked_with_dup >= 10

        # Assert query count <= 8 (typically 5 queries: 1 main, 1 selectinload, 3 enrich)
        assert len(statements) <= 8, f"Expected <= 8 queries, got {len(statements)}: {statements}"
    finally:
        event.remove(engine, "before_cursor_execute", _capture_stmt)


def test_p1_1_stats_endpoint_query_count_and_cache(client):
    """P1-1: /stats issues <= 3 queries on cache miss and 0 queries on cache hit."""
    statements = []
    def _capture_stmt(conn, cur, st, p, ctx, many):
        statements.append(st)

    # Invalidate cache before test
    from backend.app.routers.stats import invalidate_stats_cache
    invalidate_stats_cache()

    event.listen(engine, "before_cursor_execute", _capture_stmt)
    try:
        # First call: cache miss, must be <= 3 queries (was 8)
        res1 = client.get("/api/v1/stats")
        assert res1.status_code == 200
        miss_count = len(statements)
        assert miss_count <= 3, f"Expected <= 3 queries on cache miss, got {miss_count}: {statements}"

        # Second call: cache hit within TTL, must be 0 queries
        statements.clear()
        res2 = client.get("/api/v1/stats")
        assert res2.status_code == 200
        assert res2.json() == res1.json()
        assert len(statements) == 0, f"Expected 0 queries on cache hit, got {len(statements)}"
    finally:
        event.remove(engine, "before_cursor_execute", _capture_stmt)
        invalidate_stats_cache()


def test_p1_3_multi_deck_statement_count_regression(client):
    """P1-3: Statement count regression tests on a seeded multi-deck database.
    Asserts counts (never wall-clock time):
      - list_documents <= 2
      - list_duplicate_candidates <= 8
      - stats <= 3
    """
    db = SessionLocal()
    try:
        # Seed 5 decks with 10 slides each
        doc_ids = []
        for d_idx in range(5):
            d_id = f"p1-3-deck-{d_idx}-{uuid.uuid4()}"
            doc_ids.append(d_id)
            doc = Document(
                id=d_id,
                filename=f"regression_deck_{d_idx}.pptx",
                title=f"Regression Deck {d_idx}",
                year=2024,
                file_type="pptx",
                storage_path=f"/fake/regression_{d_idx}.pptx",
                file_size_bytes=12000,
                processing_status="COMPLETED",
                slide_count=10,
                question_count=4
            )
            db.add(doc)
            for s_idx in range(1, 11):
                slide = Slide(
                    id=str(uuid.uuid4()),
                    document_id=d_id,
                    slide_number=s_idx,
                    slide_type="QUESTION",
                    extracted_text=f"Deck {d_idx} Slide {s_idx} content"
                )
                db.add(slide)

        # Seed 5 parent questions
        parent_ids = []
        for p_idx in range(5):
            pid = str(uuid.uuid4())
            parent_ids.append(pid)
            pq = Question(
                id=pid,
                document_id=doc_ids[p_idx],
                question_text=f"Canonical parent question {p_idx}",
                answer=f"Canonical answer {p_idx}",
                topic="Technology",
                duplicate_status="UNIQUE"
            )
            db.add(pq)

        # Seed 15 duplicate questions pointing to parents
        for dup_idx in range(15):
            parent_id = parent_ids[dup_idx % len(parent_ids)]
            doc_id = doc_ids[dup_idx % len(doc_ids)]
            dq = Question(
                id=str(uuid.uuid4()),
                document_id=doc_id,
                question_text=f"Variant duplicate question {dup_idx}",
                answer=f"Variant answer {dup_idx}",
                topic="Technology",
                duplicate_status="POSSIBLE_DUPLICATE",
                duplicate_similarity=0.93 + (dup_idx * 0.003),
                duplicate_of_id=parent_id
            )
            db.add(dq)

        db.commit()
    finally:
        db.close()

    statements = []
    def _capture_stmt(conn, cur, st, p, ctx, many):
        statements.append(st)

    event.listen(engine, "before_cursor_execute", _capture_stmt)
    try:
        # 1. list_documents statement count <= 2
        statements.clear()
        res_docs = client.get("/api/v1/documents?limit=50")
        assert res_docs.status_code == 200
        assert len(statements) <= 2, f"list_documents exceeded statement cap: {len(statements)} statements: {statements}"

        # 2. list_duplicate_candidates statement count <= 8
        statements.clear()
        res_dups = client.get("/api/v1/questions/duplicates?limit=50")
        assert res_dups.status_code == 200
        assert len(statements) <= 8, f"list_duplicate_candidates exceeded statement cap: {len(statements)} statements: {statements}"

        # 3. get_stats statement count <= 3
        from backend.app.routers.stats import invalidate_stats_cache
        invalidate_stats_cache()
        statements.clear()
        res_stats = client.get("/api/v1/stats")
        assert res_stats.status_code == 200
        assert len(statements) <= 3, f"get_stats exceeded statement cap: {len(statements)} statements: {statements}"
    finally:
        event.remove(engine, "before_cursor_execute", _capture_stmt)
