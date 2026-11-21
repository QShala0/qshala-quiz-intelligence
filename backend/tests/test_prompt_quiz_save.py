"""Saving a prompt-quiz selection as a quiz (audit Q12): it used to fail with a TypeError on every call."""
import uuid

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.database import SessionLocal
from backend.app.models.document import Document
from backend.app.models.question import Question
from backend.app.models.quiz import GeneratedQuestion, Quiz, RetrievalSource

client = TestClient(app)


def _question_with_media():
    doc_id, q_id = str(uuid.uuid4()), str(uuid.uuid4())
    tag = uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        db.add(Document(id=doc_id, filename="vault.pptx", title="Vault Deck", file_type="pptx", storage_path="dummy",
                        file_size_bytes=1, processing_status="COMPLETED"))
        db.flush()
        db.add(Question(
            id=q_id, document_id=doc_id, topic="Banking", subtopic="Central Banks",
            question_text=f"Which institution issues the rupee note number {tag}?", answer="Reserve Bank of India",
            options=["A) RBI", "B) SEBI"], explanation="RBI issues currency.",
            question_images=["https://cdn.example.test/slides/x/media/q.png"],
            answer_images=["https://cdn.example.test/slides/x/media/a.png"],
            image_refs=["https://cdn.example.test/slides/x/media/q.png"],
            question_audio=["https://cdn.example.test/slides/x/media/q.mp3"],
            question_videos=["https://cdn.example.test/slides/x/media/q.mp4"],
            visual_clues="a banknote", question_type="SLIDE_QA", grade_min=5, grade_max=9,
        ))
        db.commit()
    finally:
        db.close()
    return doc_id, q_id


def test_save_creates_a_quiz_with_the_questions_media_and_a_source_link():
    doc_id, q_id = _question_with_media()
    r = client.post("/api/v1/prompt-quiz/save", json={
        "prompt": "currency questions", "title": "My Currency Quiz", "question_ids": [q_id],
        "brief": {"query": ["currency", "banking"], "hard_bias": "easy"},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1 and body["title"] == "My Currency Quiz"

    db = SessionLocal()
    try:
        quiz = db.query(Quiz).get(body["quiz_id"])
        assert quiz.topic == "currency, banking" and quiz.difficulty == "easy"
        assert quiz.raw_prompt == "currency questions" and quiz.question_count == 1
        gq = db.query(GeneratedQuestion).filter(GeneratedQuestion.quiz_id == quiz.id).one()
        assert gq.answer == "Reserve Bank of India" and gq.options == ["A) RBI", "B) SEBI"]
        assert gq.explanation == "RBI issues currency."
        assert gq.question_images == ["https://cdn.example.test/slides/x/media/q.png"]
        assert gq.answer_images == ["https://cdn.example.test/slides/x/media/a.png"]
        assert gq.question_audio == ["https://cdn.example.test/slides/x/media/q.mp3"]
        assert gq.question_video == ["https://cdn.example.test/slides/x/media/q.mp4"]
        assert gq.visual_clues == "a banknote" and (gq.grade_min, gq.grade_max) == (5, 9)
        src = db.query(RetrievalSource).filter(RetrievalSource.generated_question_id == gq.id).one()
        assert src.historical_question_id == q_id and src.document_id == doc_id
        assert src.document_title == "Vault Deck"
    finally:
        db.close()

    got = client.get(f"/api/v1/quizzes/{body['quiz_id']}")
    assert got.status_code == 200 and len(got.json()["questions"]) == 1


def test_save_keeps_the_requested_order_and_skips_unknown_ids():
    _, a = _question_with_media()
    _, b = _question_with_media()
    r = client.post("/api/v1/prompt-quiz/save", json={"prompt": "x", "question_ids": [b, "no-such-id", a]})
    assert r.status_code == 200 and r.json()["count"] == 2
    db = SessionLocal()
    try:
        rows = (db.query(GeneratedQuestion).filter(GeneratedQuestion.quiz_id == r.json()["quiz_id"])
                .order_by(GeneratedQuestion.order_index).all())
        srcs = [db.query(RetrievalSource).filter(RetrievalSource.generated_question_id == g.id).one().historical_question_id
                for g in rows]
        assert srcs == [b, a]
    finally:
        db.close()


def test_default_title_is_the_typed_prompt_and_rename_keeps_the_prompt():
    _, q = _question_with_media()
    prompt = "  20 questions on   cricket and the IPL, school safe "
    r = client.post("/api/v1/prompt-quiz/save", json={
        "prompt": prompt, "question_ids": [q], "brief": {"framing": "A cricket-flavoured round", "query": "cricket"}})
    assert r.status_code == 200, r.text
    qid = r.json()["quiz_id"]
    assert r.json()["title"] == "20 questions on cricket and the IPL, school safe"

    assert client.patch(f"/api/v1/quizzes/{qid}", json={"title": "Friday cricket"}).status_code == 200
    got = client.get(f"/api/v1/quizzes/{qid}").json()
    assert got["title"] == "Friday cricket"
    assert got["raw_prompt"] == prompt


def test_prompt_title_cuts_long_prompts_at_a_word():
    from backend.app.routers.prompt_quiz import prompt_title
    long = "quiz on banking " * 40
    t = prompt_title(long)
    assert len(t) <= 255 and t.endswith("…") and "bankin…" not in t
    assert prompt_title("  hi   there ") == "hi there"
    assert prompt_title("") == ""


def test_save_with_no_matching_questions_is_a_404():
    r = client.post("/api/v1/prompt-quiz/save", json={"prompt": "x", "question_ids": ["nope"]})
    assert r.status_code == 404
