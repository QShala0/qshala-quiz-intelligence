import uuid
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.database import SessionLocal
from backend.app.models.quiz import Quiz

client = TestClient(app)


def _create_test_quiz(title: str = "Original Quiz Title") -> str:
    quiz_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        quiz = Quiz(
            id=quiz_id,
            title=title,
            topic="General Knowledge",
            question_count=0,
        )
        db.add(quiz)
        db.commit()
    finally:
        db.close()
    return quiz_id


def test_rename_quiz_success():
    quiz_id = _create_test_quiz("Agastya 7th Oct Quiz")
    resp = client.patch(f"/api/v1/quizzes/{quiz_id}", json={"title": "Agastya Finals Oct 2026"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == quiz_id
    assert data["title"] == "Agastya Finals Oct 2026"
    assert data["topic"] == "General Knowledge"

    # Verify persisted in DB
    db = SessionLocal()
    try:
        q = db.query(Quiz).filter(Quiz.id == quiz_id).first()
        assert q is not None
        assert q.title == "Agastya Finals Oct 2026"
    finally:
        db.close()


def test_rename_quiz_trimming():
    quiz_id = _create_test_quiz("Before Trim")
    resp = client.patch(f"/api/v1/quizzes/{quiz_id}", json={"title": "   Trimmed Quiz Title   "})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["title"] == "Trimmed Quiz Title"

    db = SessionLocal()
    try:
        q = db.query(Quiz).filter(Quiz.id == quiz_id).first()
        assert q.title == "Trimmed Quiz Title"
    finally:
        db.close()


def test_rename_quiz_empty_rejected():
    quiz_id = _create_test_quiz("Keep This Title")
    # Empty string
    r1 = client.patch(f"/api/v1/quizzes/{quiz_id}", json={"title": ""})
    assert r1.status_code == 400
    assert "empty" in r1.json()["detail"].lower()

    # Whitespace-only string
    r2 = client.patch(f"/api/v1/quizzes/{quiz_id}", json={"title": "    \t  \n  "})
    assert r2.status_code == 400
    assert "empty" in r2.json()["detail"].lower()

    # Verify original title unchanged
    db = SessionLocal()
    try:
        q = db.query(Quiz).filter(Quiz.id == quiz_id).first()
        assert q.title == "Keep This Title"
    finally:
        db.close()


def test_rename_quiz_too_long_rejected():
    quiz_id = _create_test_quiz("Short Title")
    too_long = "A" * 256
    resp = client.patch(f"/api/v1/quizzes/{quiz_id}", json={"title": too_long})
    assert resp.status_code == 400
    assert "255" in resp.json()["detail"]

    # Boundary check: 255 characters should succeed
    exact_255 = "B" * 255
    resp_exact = client.patch(f"/api/v1/quizzes/{quiz_id}", json={"title": exact_255})
    assert resp_exact.status_code == 200
    assert resp_exact.json()["title"] == exact_255


def test_rename_quiz_unknown_id():
    fake_id = str(uuid.uuid4())
    resp = client.patch(f"/api/v1/quizzes/{fake_id}", json={"title": "New Title"})
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()
