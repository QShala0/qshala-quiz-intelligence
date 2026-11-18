import io
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.database import SessionLocal
from backend.app.models.document import Document
from backend.app.models.question import Question
from backend.app.models.qshala_tag import QshalaTag
from backend.app.services.export.exporter import QuizExporter

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def seed_stress_questions():
    """Ensure hermetic test DB has questions and tags across all domains tested."""
    db = SessionLocal()
    try:
        doc = db.query(Document).filter_by(filename="stress_test_doc.pptx").first()
        if not doc:
            doc = Document(
                id=str(uuid.uuid4()),
                filename="stress_test_doc.pptx",
                title="Stress Test Bank",
                year=2024,
                file_type="pptx",
                storage_path="dummy",
                file_size_bytes=1000,
                processing_status="COMPLETED"
            )
            db.add(doc)
            db.commit()

        sample_domains = [
            ("space", "Space & Astronomy", "Science & Nature", [
                ("Which Indian lunar mission successfully landed the Vikram lander near the Moon's south pole?", "Chandrayaan-3"),
                ("Which solar observation mission was launched by ISRO to the Sun-Earth L1 point in 2023?", "Aditya-L1"),
                ("Who was the first Indian citizen to travel into outer space aboard Soyuz T-11?", "Rakesh Sharma"),
                ("What space telescope launched in 2021 as the scientific successor to Hubble?", "James Webb Space Telescope"),
                ("Which planet in our solar system is known as the Red Planet due to iron oxide?", "Mars"),
                ("What is the name of ISRO's heavy lift launch vehicle nicknamed Fat Boy?", "LVM3"),
            ]),
            ("history", "Indian History", "History & Heritage", [
                ("Which Mauryan emperor embraced Buddhism following the devastating Kalinga War?", "Ashoka"),
                ("In which year did the Dandi March led by Mahatma Gandhi take place?", "1930"),
                ("Who was the first Prime Minister of independent India who delivered the Tryst with Destiny speech?", "Jawaharlal Nehru"),
                ("Which Mughal emperor commissioned the construction of the Taj Mahal in Agra?", "Shah Jahan"),
                ("Who was known as the Iron Man of India for integrating princely states?", "Sardar Vallabhbhai Patel"),
                ("In what year was the Battle of Plassey fought that laid British Company rule in India?", "1757"),
            ]),
            ("sports", "Sports & Games", "Sports & Games", [
                ("Who holds the record for scoring one hundred international centuries in cricket?", "Sachin Tendulkar"),
                ("Which Indian javelin thrower won individual track and field gold at Tokyo 2020?", "Neeraj Chopra"),
                ("In which sport did Dhyan Chand win three consecutive Olympic gold medals for India?", "Field Hockey"),
                ("Who is the only tennis player to have won 14 French Open singles titles at Roland Garros?", "Rafael Nadal"),
                ("Which country won the inaugural ICC Men's T20 World Cup in 2007?", "India"),
                ("Who captained India to victory in the 1983 Cricket World Cup at Lord's?", "Kapil Dev"),
            ]),
            ("geography", "World Geography", "Geography", [
                ("What is the highest mountain peak in the world above sea level?", "Mount Everest"),
                ("Which river is traditionally considered the longest river on planet Earth?", "Nile River"),
                ("Which is the largest desert in the world, classified as a cold desert?", "Antarctica"),
                ("What strait connects the Mediterranean Sea to the Atlantic Ocean?", "Strait of Gibraltar"),
                ("Which South American country has the longest coastline facing the Pacific Ocean?", "Chile"),
                ("What is the capital city of Australia?", "Canberra"),
            ]),
            ("startups", "Indian Startups", "Business & Startups", [
                ("What is a startup valued at over one billion dollars called?", "Unicorn"),
                ("Who coined the term Minimum Viable Product in lean startup methodology?", "Frank Robinson / Eric Ries"),
                ("Which Indian fintech company pioneered QR soundbox payments?", "Paytm"),
                ("What is a pitch deck used for by startup founders?", "Presenting business plans to investors"),
                ("What is venture capital?", "Financing provided to early-stage growth companies"),
                ("Which city is widely regarded as the Silicon Valley of India?", "Bangalore"),
            ]),
            ("science", "Tech & Science", "Science & Technology", [
                ("What does CPU stand for in computer hardware?", "Central Processing Unit"),
                ("Which programming language was created by Guido van Rossum?", "Python"),
                ("What does HTTP stand for in web browsing protocols?", "Hypertext Transfer Protocol"),
                ("Which company developed the Gemini family of AI models?", "Google"),
                ("What is open-source software distributed with?", "Source code that anyone can inspect and modify"),
            ]),
        ]

        existing = db.query(Question).filter(Question.document_id == doc.id).count()
        if existing == 0:
            for kw, sub, top, q_tuples in sample_domains:
                for idx, (q_txt, ans) in enumerate(q_tuples, 1):
                    qid = str(uuid.uuid4())
                    db.add(Question(
                        id=qid,
                        document_id=doc.id,
                        question_text=q_txt,
                        answer=ans,
                        topic=top,
                        subtopic=sub,
                        topics=[top, sub],
                        tags=[kw, "stress"],
                        difficulty="Hard" if idx > 3 else "Medium",
                        difficulty_score=0.85 if idx > 3 else 0.50,
                    ))
                    db.add(QshalaTag(
                        id=str(uuid.uuid4()),
                        question_id=qid,
                        source="LLM",
                        status="confirmed",
                        topic_l1=top,
                        subtopic_l2=sub,
                        keywords_l3=[kw],
                        sensitivity="NONE",
                        shelf_life="EVERGREEN",
                        created_at=datetime.now(timezone.utc),
                    ))
            db.commit()
    finally:
        db.close()


def test_draft_simple_prompt():
    resp = client.post("/api/v1/prompt-quiz/draft", json={
        "prompt": "5 questions on Indian startups",
        "count": 5
    })
    assert resp.status_code == 200, f"Status: {resp.status_code}, Body: {resp.text}"
    data = resp.json()
    assert "questions" in data
    assert len(data["questions"]) > 0
    assert "brief" in data


def test_draft_zero_questions():
    resp = client.post("/api/v1/prompt-quiz/draft", json={
        "prompt": "0 questions on cricket",
        "count": 0
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["questions"]) == 0
    assert "Cannot create a quiz with 0 questions" in data.get("message", "")


def test_draft_long_prompt():
    prompt = (
        "A highly competitive 5-question quiz on space exploration, astrophysics, "
        "and India's Chandrayaan and Aditya-L1 missions designed for high school finals. "
        "Include rich trivia, connect questions, and ensure questions are balanced in difficulty."
    )
    resp = client.post("/api/v1/prompt-quiz/draft", json={"prompt": prompt, "count": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["questions"]) > 0


def test_draft_special_characters_prompt():
    prompt = "Startups & Fintech: What's the ₹1 Billion \"Unicorn\" vs. 'Dragon' — round?"
    resp = client.post("/api/v1/prompt-quiz/draft", json={"prompt": prompt, "count": 3})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["questions"]) > 0


def test_draft_refine_flow():
    # 1. Initial draft
    resp1 = client.post("/api/v1/prompt-quiz/draft", json={
        "prompt": "3 questions on Indian history",
        "count": 3
    })
    assert resp1.status_code == 200
    d1 = resp1.json()
    q_list = d1["questions"]
    brief1 = d1["brief"]
    assert len(q_list) > 0

    # 2. Refine: "replace question 1 with science"
    resp2 = client.post("/api/v1/prompt-quiz/draft", json={
        "prompt": "replace question 1 with science",
        "previous_brief": brief1,
        "current_questions": q_list
    })
    assert resp2.status_code == 200
    d2 = resp2.json()
    assert len(d2["questions"]) == len(q_list)

    # 3. Refine: "give me 1 more question"
    resp3 = client.post("/api/v1/prompt-quiz/draft", json={
        "prompt": "give me 1 more question on tech",
        "previous_brief": brief1,
        "current_questions": d2["questions"],
        "keep_ids": [q["id"] for q in d2["questions"] if "id" in q]
    })
    assert resp3.status_code == 200
    d3 = resp3.json()
    assert len(d3["questions"]) >= len(d2["questions"])


def test_draft_swap_single_question():
    resp1 = client.post("/api/v1/prompt-quiz/draft", json={"prompt": "3 questions on sports", "count": 3})
    assert resp1.status_code == 200
    qs = resp1.json()["questions"]
    assert len(qs) > 0
    target_id = qs[0]["id"]

    resp_swap = client.post("/api/v1/prompt-quiz/swap", json={
        "prompt": "sports",
        "question_id": target_id,
        "keep_ids": [q["id"] for q in qs[1:]]
    })
    assert resp_swap.status_code == 200
    new_q = resp_swap.json()
    assert new_q is not None and isinstance(new_q, dict)
    assert "id" in new_q
    assert new_q["id"] != target_id


def test_save_and_fetch_quiz():
    resp1 = client.post("/api/v1/prompt-quiz/draft", json={"prompt": "3 questions on geography", "count": 3})
    assert resp1.status_code == 200
    qs = resp1.json()["questions"]
    assert len(qs) > 0
    q_ids = [q["id"] for q in qs]

    save_resp = client.post("/api/v1/prompt-quiz/save", json={
        "title": "Global Geography Prelims",
        "prompt": "3 questions on geography",
        "question_ids": q_ids,
        "brief": resp1.json()["brief"]
    })
    assert save_resp.status_code == 200
    save_data = save_resp.json()
    quiz_id = save_data.get("quiz_id") or save_data.get("id")
    assert quiz_id is not None

    get_resp = client.get(f"/api/v1/quizzes/{quiz_id}")
    assert get_resp.status_code == 200
    quiz_data = get_resp.json()
    assert quiz_data["title"] == "Global Geography Prelims"
    assert len(quiz_data["questions"]) == len(q_ids)


def test_export_all_themes():
    sample_quiz = {
        "id": "quiz-stress-1",
        "title": "Stress Test Showcase",
        "questions": [
            {
                "id": "q1",
                "question_text": "What is the capital city of Australia?",
                "answer": "Canberra",
                "explanation": "Canberra is the federal capital.",
                "is_approved": True,
            },
            {
                "id": "q2",
                "question_text": "Which planet is known as the Red Planet?",
                "answer": "Mars",
                "explanation": "Rich in iron oxide.",
                "is_approved": True,
            }
        ]
    }
    for theme in ["qshala_school", "school", "corporate", "minimalist"]:
        out_bytes = QuizExporter.to_pptx(
            sample_quiz,
            quiz_type=theme,
            custom_title="Stress Test Showcase",
            show_answer_divider=True
        )
        assert len(out_bytes) > 1000
        zf = zipfile.ZipFile(io.BytesIO(out_bytes))
        assert "[Content_Types].xml" in zf.namelist()
        assert any(f.startswith("ppt/slides/slide") for f in zf.namelist())


def test_extreme_niche_topic_with_adjacent_padding():
    resp = client.post("/api/v1/prompt-quiz/draft", json={
        "prompt": "5 questions on quantum chromodynamics and topological solitons",
        "count": 5
    })
    assert resp.status_code == 200
    data = resp.json()
    prev_b = data.get("brief", {})
    prev_b["cross_topic_padding"] = True
    r2 = client.post("/api/v1/prompt-quiz/draft", json={
        "prompt": "5 questions on quantum chromodynamics and topological solitons",
        "previous_brief": prev_b
    })
    assert r2.status_code == 200
    d2 = r2.json()
    assert len(d2.get("questions", [])) > 0


def test_export_massive_text_blocks():
    giant_q = (
        "In the year 1994, an extraordinary confluence of economic reform, digital telecommunications, "
        "and computing architecture took place in Bangalore, India. Several engineers from prestigious "
        "institutions founded a tech enterprise that transformed software exports across three continents. "
        "The company went on to pioneer global delivery models, listed on international exchanges, and set "
        "new benchmarks for corporate governance and campus infrastructure. "
        "Meanwhile, another parallel group of entrepreneurs started an indigenous hardware assembly division "
        "which later pivoted to FMCG, consumer lighting, and enterprise IT services worldwide. "
        "Identify the flagship campus and the original parent entity described in this competition dossier."
    )
    giant_a = "Infosys Electronics City Campus & Wipro (originally Western India Palm Refined Oil Limited)"
    quiz = {
        "id": "giant-quiz",
        "title": "Giant Text Showcase",
        "questions": [
            {
                "id": "q-giant",
                "question_text": giant_q,
                "answer": giant_a,
                "is_approved": True,
            }
        ]
    }
    out = QuizExporter.to_pptx(
        quiz,
        quiz_type="qshala_school",
        custom_title="Giant Text Showcase"
    )
    assert len(out) > 5000
    zf = zipfile.ZipFile(io.BytesIO(out))
    assert "ppt/slides/slide1.xml" in zf.namelist()


def test_missing_and_corrupt_media_handling():
    quiz = {
        "id": "bad-media-quiz",
        "title": "Bad Media Robustness Test",
        "questions": [
            {
                "id": "bad-remote",
                "question_text": "Remote URL 404 test",
                "answer": "Safe",
                "question_images": ["https://invalid-nonexistent-domain.xyz/bad.jpg"],
                "is_approved": True,
            },
            {
                "id": "bad-local",
                "question_text": "Local path missing test",
                "answer": "Safe",
                "question_images": ["/nonexistent/local/drive/missing_photo.png"],
                "is_approved": True,
            }
        ]
    }
    out = QuizExporter.to_pptx(
        quiz,
        quiz_type="qshala_school",
        custom_title="Bad Media Robustness Test"
    )
    assert len(out) > 5000


def test_sparse_and_minimal_question_fields():
    quiz = {
        "id": "sparse-quiz",
        "title": "Minimal Data Test",
        "questions": [
            {
                "id": "sparse-q",
                "question_text": "Question with no other metadata",
                "is_approved": True,
            }
        ]
    }
    out = QuizExporter.to_pptx(
        quiz,
        quiz_type="qshala_school",
        custom_title="Minimal Data Test"
    )
    assert len(out) > 5000


def test_api_invalid_requests():
    r1 = client.post("/api/v1/prompt-quiz/draft", json={"prompt": ""})
    assert r1.status_code in [200, 422]

    r2 = client.post("/api/v1/prompt-quiz/draft", json={"prompt": "test", "count": -5})
    assert r2.status_code in [200, 422]

    r3 = client.post("/api/v1/prompt-quiz/save", json={
        "title": "Invalid Quiz",
        "prompt": "test",
        "question_ids": []
    })
    assert r3.status_code == 422

    r4 = client.get("/api/v1/quizzes/nonexistent-quiz-id-99999")
    assert r4.status_code == 404
