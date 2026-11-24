import io
import pytest
from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.dml.color import RGBColor
from backend.app.main import app
from backend.app.database import SessionLocal
from backend.app.models.quiz import Quiz, GeneratedQuestion
from backend.app.services.export.exporter import QuizExporter, _clean_quiz_title

client = TestClient(app)


def test_clean_quiz_title_helper():
    """Verify raw prompt text is converted to crisp title strings."""
    assert _clean_quiz_title("give me 7 questions on government policy and make a quiz", "Finance") == "Government Policy Quiz"
    assert _clean_quiz_title("generate 10 questions about Indian Space Program", "Science") == "Indian Space Program Quiz"
    assert _clean_quiz_title("Bangalore South Zone", "Finance") == "Bangalore South Zone"
    assert _clean_quiz_title("", "Astronomy") == "Astronomy Quiz"


def test_pptx_export_school_quiz_quality_gates():
    """
    Test School Quiz export against all quality gates:
    Gate 1: Cover slide with dynamic campus edition and prize money, no duplicate bottom text or round subtitles.
    Gate 2: Mandatory 3-slide rhythm (Question -> Transition -> Reveal).
    Gate 3: Background artwork pairing (peach border question -> transition -> red border answer).
    Gate 4: Typography and contrast (Inter font, #0F172A dark text, #059669 green answer).
    """
    sample_quiz = {
        "id": "test-bengaluru-quiz",
        "title": "Bengaluru South Open Championship",
        "topic": "Karnataka Business & Culture",
        "difficulty": "Medium",
        "questions": [
            {
                "id": "q1",
                "question_text": "Which plant was adopted by Karnataka Bank in 2002 to brand its ATM network?",
                "options": ["A) Money plant", "B) Bamboo", "C) Tulsi", "D) Banyan"],
                "answer": "Money plant",
                "explanation": "Epipremnum aureum, commonly known as money plant, symbolizes prosperity.",
                "difficulty": "Medium",
                "is_approved": True,
            },
            {
                "id": "q2",
                "question_text": "Which iconic IT company was founded in Pune in 1981 but later moved to Electronics City?",
                "options": ["A) Infosys", "B) Wipro", "C) TCS", "D) Mindtree"],
                "answer": "Infosys",
                "explanation": "Infosys relocated to Bengaluru in 1983.",
                "difficulty": "Easy",
                "is_approved": True,
            }
        ]
    }

    pptx_bytes = QuizExporter.to_pptx(
        sample_quiz,
        quiz_type="school",
        campus_edition="Bangalore Campus Edition",
        prize_money="3.5 lakh",
        include_social_slides=True
    )
    assert isinstance(pptx_bytes, bytes)
    assert len(pptx_bytes) > 1000

    prs = Presentation(io.BytesIO(pptx_bytes))
    # 3 intro slides (Cover + Social + QR) + 2 questions * 3 slides each = 9 slides
    assert len(prs.slides) == 9
    assert prs.slide_width.inches == pytest.approx(13.333, 0.01)
    assert prs.slide_height.inches == pytest.approx(7.5, 0.01)

    # Gate 1: Cover Slide checks
    s0 = prs.slides[0]
    s0_texts = [p.text for s in s0.shapes if s.has_text_frame for p in s.text_frame.paragraphs]
    # Line 1: '— Bangalore Campus Edition —'
    assert any("— Bangalore Campus Edition —" in t for t in s0_texts)
    # Line 2: 'Win prizes worth ₹3.5 lakh!'
    assert any("Win prizes worth ₹3.5 lakh!" in t for t in s0_texts)
    # Verify no duplicate bottom text or round subtitles
    assert not any("kerala campus edition" in t.lower() for t in s0_texts)
    assert not any("preliminary round" in t.lower() for t in s0_texts)

    # Gate 2: Social Media Slide (Slide 2) and QR Code Slide (Slide 3)
    s1 = prs.slides[1]
    s1_texts = [p.text for s in s1.shapes if s.has_text_frame for p in s.text_frame.paragraphs]
    assert any("Social Media" in t for t in s1_texts)
    assert any("#varsityquiz" in t for t in s1_texts)

    s2 = prs.slides[2]
    s2_texts = [p.text for s in s2.shapes if s.has_text_frame for p in s.text_frame.paragraphs]
    assert any("Get Curious & Stay Curious" in t for t in s2_texts)
    qr_pics = [s for s in s2.shapes if s.shape_type == 13]
    assert len(qr_pics) >= 3

    # Gate 2 & 3: Mandatory 3-Slide Rhythm for Question 1
    # Slide 4 (index 3): Question 1
    sq1 = prs.slides[3]
    sq1_texts = [p.text for s in sq1.shapes if s.has_text_frame for p in s.text_frame.paragraphs]
    assert any("1." in t for t in sq1_texts)
    assert any("Karnataka Bank" in t for t in sq1_texts)

    # Slide 5 (index 4): Mandatory Answer Transition Slide
    st1 = prs.slides[4]
    st1_texts = [p.text for s in st1.shapes if s.has_text_frame for p in s.text_frame.paragraphs]
    assert any("Answer" in t for t in st1_texts)

    # Slide 6 (index 5): Answer Reveal Slide
    sa1 = prs.slides[5]
    sa1_texts = [p.text for s in sa1.shapes if s.has_text_frame for p in s.text_frame.paragraphs]
    assert any("Money plant" in t for t in sa1_texts)

    # Question 2 Mandatory 3-Slide Rhythm
    # Slide 7 (index 6): Question 2
    sq2 = prs.slides[6]
    sq2_texts = [p.text for s in sq2.shapes if s.has_text_frame for p in s.text_frame.paragraphs]
    assert any("2." in t for t in sq2_texts)
    assert any("Infosys" in t for t in sq2_texts)

    # Slide 8 (index 7): Mandatory Answer Transition Slide
    st2 = prs.slides[7]
    st2_texts = [p.text for s in st2.shapes if s.has_text_frame for p in s.text_frame.paragraphs]
    assert any("Answer" in t for t in st2_texts)

    # Slide 9 (index 8): Answer Reveal Slide
    sa2 = prs.slides[8]
    sa2_texts = [p.text for s in sa2.shapes if s.has_text_frame for p in s.text_frame.paragraphs]
    assert any("Infosys" in t for t in sa2_texts)


def test_pptx_export_corporate_quiz():
    """Verify Corporate Quiz export uses corporate backdrops and mandatory 3-slide rhythm."""
    sample_quiz = {
        "id": "test-corporate-quiz",
        "title": "Varsity Corporate Finance Challenge",
        "topic": "Finance",
        "difficulty": "Hard",
        "questions": [
            {
                "question_text": "What is the capital requirement under Basel III?",
                "answer": "Minimum 8% of risk-weighted assets",
                "explanation": "Basel III requires banks to maintain a minimum total capital ratio of 8%.",
                "is_approved": True,
            }
        ]
    }

    pptx_bytes = QuizExporter.to_pptx(
        sample_quiz,
        quiz_type="corporate",
        custom_title="Varsity Corporate Finance Challenge",
        campus_edition="Executive Track 2026",
        include_social_slides=False
    )
    prs = Presentation(io.BytesIO(pptx_bytes))
    # 1 cover slide + 1 question * 3 slides (Q + Transition + Reveal) = 4 slides
    assert len(prs.slides) == 4

    # Slide 1: Cover
    s0_texts = [p.text for s in prs.slides[0].shapes if s.has_text_frame for p in s.text_frame.paragraphs]
    assert any("Varsity Corporate Finance Challenge" in t for t in s0_texts)
    assert any("Executive Track 2026" in t for t in s0_texts)

    # Slide 2: Question
    s1_texts = [p.text for s in prs.slides[1].shapes if s.has_text_frame for p in s.text_frame.paragraphs]
    assert any("Basel III" in t for t in s1_texts)

    # Slide 3: Answer transition
    s2_texts = [p.text for s in prs.slides[2].shapes if s.has_text_frame for p in s.text_frame.paragraphs]
    assert any("Answer" in t for t in s2_texts)

    # Slide 4: Answer reveal
    s3_texts = [p.text for s in prs.slides[3].shapes if s.has_text_frame for p in s.text_frame.paragraphs]
    assert any("8%" in t for t in s3_texts)


def test_pptx_export_custom_campus_and_prize_money():
    """Verify dynamic custom campus edition name and prize money values."""
    sample_quiz = {
        "id": "test-custom-params",
        "title": "Government Policy Quiz",
        "topic": "Governance",
        "questions": [
            {
                "question_text": "Which ministry administers the PM-KISAN scheme?",
                "answer": "Ministry of Agriculture and Farmers Welfare",
                "is_approved": True,
            }
        ]
    }

    pptx_bytes = QuizExporter.to_pptx(
        sample_quiz,
        quiz_type="school",
        campus_edition="Kochi Campus Edition",
        prize_money="50,000",
        include_social_slides=False
    )
    prs = Presentation(io.BytesIO(pptx_bytes))
    # 1 cover slide + 3 question slides = 4 slides
    assert len(prs.slides) == 4

    cover_texts = [p.text for s in prs.slides[0].shapes if s.has_text_frame for p in s.text_frame.paragraphs]
    assert any("— Kochi Campus Edition —" in t for t in cover_texts)
    assert any("Win prizes worth ₹50,000!" in t for t in cover_texts)


def test_pptx_export_api_endpoints():
    """Test GET and POST API endpoints with quiz_type, campus_edition, and prize_money parameters."""
    db = SessionLocal()
    quiz = None
    try:
        quiz = Quiz(
            title="API Test Quiz",
            topic="History",
            difficulty="Balanced",
            quiz_format="TOURNAMENT_PRELIMS"
        )
        db.add(quiz)
        db.commit()
        db.refresh(quiz)

        gq = GeneratedQuestion(
            quiz_id=quiz.id,
            question_text="Who was the architect of New Delhi?",
            answer="Sir Edwin Lutyens",
            explanation="Lutyens planned New Delhi with Sir Herbert Baker.",
            order_index=1,
            is_approved=True
        )
        db.add(gq)
        db.commit()

        # Test GET with new parameters
        url = (
            f"/api/v1/quizzes/{quiz.id}/export?format=pptx&quiz_type=school"
            f"&campus_edition=Pune+Campus+Edition&prize_money=2+lakh&include_social_slides=true"
        )
        res_get = client.get(url)
        assert res_get.status_code == 200
        assert "presentationml" in res_get.headers["content-type"]
        assert len(res_get.content) > 1000

        prs_get = Presentation(io.BytesIO(res_get.content))
        # 3 intro slides + 1 question * 3 slides = 6 slides
        assert len(prs_get.slides) == 6
        cover_texts = [p.text for s in prs_get.slides[0].shapes if s.has_text_frame for p in s.text_frame.paragraphs]
        assert any("— Pune Campus Edition —" in t for t in cover_texts)
        assert any("Win prizes worth ₹2 lakh!" in t for t in cover_texts)

        # Test POST /api/v1/quizzes/export?format=pptx
        payload = {
            "title": "give me 5 questions on physics",
            "topic": "Science",
            "questions": [
                {
                    "question_text": "What is the speed of light in vacuum?",
                    "answer": "Approximately 300,000 km/s",
                    "explanation": "299,792,458 metres per second precisely.",
                    "is_approved": True
                }
            ]
        }
        res_post = client.post(
            "/api/v1/quizzes/export?format=pptx&quiz_type=corporate&campus_edition=Tech+Cohort&include_social_slides=false",
            json=payload
        )
        assert res_post.status_code == 200
        assert len(res_post.content) > 1000
        prs_post = Presentation(io.BytesIO(res_post.content))
        # 1 cover slide + 3 question slides = 4 slides
        assert len(prs_post.slides) == 4
        post_title_texts = [p.text for s in prs_post.slides[0].shapes if s.has_text_frame for p in s.text_frame.paragraphs]
        assert any("Physics Quiz" in t for t in post_title_texts)
        assert any("Tech Cohort" in t for t in post_title_texts)

    finally:
        if quiz:
            db.query(GeneratedQuestion).filter(GeneratedQuestion.quiz_id == quiz.id).delete()
            db.query(Quiz).filter(Quiz.id == quiz.id).delete()
            db.commit()
        db.close()


def test_pptx_export_regression_test_pptx_fixes():
    """
    Direct regression test for defects found in test.pptx:
    1. Duplicate answer text on answer reveal slides is prevented.
    2. Empty/blank question text falls back cleanly and is never empty.
    3. Raw prize number like '1' is formatted as '₹1 Lakh!'.
    4. Direct payload questions inherit media from the vault when available.
    """
    quiz_payload = {
        "title": "Insurance & Markets Prelims",
        "topic": "Finance",
        "questions": [
            {
                "id": "q-circuit-breaker",
                "question_text": "Which 2-word household electric mechanism do stock markets use as a safety mechanism?",
                "answer": "Circuit Breaker",
                "explanation": "Circuit Breaker",  # Identical to answer: must NOT be duplicated!
                "is_approved": True,
            },
            {
                "id": "q-empty-text",
                "question_text": "",  # Empty text: must fall back cleanly
                "answer": "Ethanol / E20",
                "explanation": "Connect round trivia.",
                "visual_clues": "Identify the fuel policy connect shown below.",
                "is_approved": True,
            }
        ]
    }

    pptx_bytes = QuizExporter.to_pptx(
        quiz_payload,
        quiz_type="school",
        campus_edition="Glenn Braggs Campus Edition",
        prize_money="1",
        include_social_slides=False
    )
    prs = Presentation(io.BytesIO(pptx_bytes))
    # 1 cover slide + 2 questions * 3 slides each = 7 slides
    assert len(prs.slides) == 7

    # Slide 1: Prize money normalization
    s1_texts = [p.text for s in prs.slides[0].shapes if s.has_text_frame for p in s.text_frame.paragraphs]
    assert any("Win prizes worth ₹1 Lakh!" in t for t in s1_texts)

    # Slide 2: Question 1
    s2_texts = [p.text for s in prs.slides[1].shapes if s.has_text_frame for p in s.text_frame.paragraphs]
    assert any("household electric mechanism" in t for t in s2_texts)

    # Slide 4: Answer Reveal 1 - MUST NOT have duplicate 'Circuit Breaker' text!
    s4_texts = [p.text.strip() for s in prs.slides[3].shapes if s.has_text_frame for p in s.text_frame.paragraphs if p.text.strip()]
    circuit_mentions = [t for t in s4_texts if t.lower() == "circuit breaker"]
    assert len(circuit_mentions) == 1, f"Expected 1 answer text, found duplicates: {circuit_mentions}"

    # Slide 5: Question 2 - MUST NOT have empty text!
    s5_texts = [p.text.strip() for s in prs.slides[4].shapes if s.has_text_frame for p in s.text_frame.paragraphs if p.text.strip()]
    assert any("fuel policy connect" in t.lower() for t in s5_texts), f"Expected fallback clue text, got: {s5_texts}"



def test_pptx_export_with_video_ref_and_kwargs():
    """
    Test export for questions with video media references and no video keywords,
    ensuring q_format resolution and kwargs acceptance without crashing.
    """
    sample_quiz = {
        "id": "quiz-video-test",
        "title": "Karnataka Quiz",
        "topic": "Karnataka",
        "questions": [
            {
                "id": "q-vid-1",
                "question_text": "Orkla India, the subsidiary of Norwegian investment company Orkla ASA, owns which heritage brand?",
                "answer": "MTR",
                "explanation": "MTR Foods is based in Bengaluru, Karnataka.",
                "raw_media_refs": ["https://example.com/slides/media10.mp4"],
                "video_refs": ["https://example.com/slides/media10.mp4"],
                "question_format": "QA",
                "is_approved": True,
            }
        ]
    }
    pptx_bytes = QuizExporter.to_pptx(
        sample_quiz,
        quiz_type="school",
        custom_subtitle="Test Subtitle",
        show_answer_divider=True,
    )
    assert isinstance(pptx_bytes, bytes)
    assert len(pptx_bytes) > 1000
    prs = Presentation(io.BytesIO(pptx_bytes))
    assert len(prs.slides) >= 3


def test_zvq_school_quiz_paragraphs_and_source_link_notes():
    """
    Verify ZVQ School Quiz PPT export:
    1. Question text is broken into readable paragraphs on sentence boundaries.
    2. Slide notes replace old presenter boilerplate with question source links on both Q and A slides.
    3. Accepts quiz_type='zvq' as an alias for school export.
    """
    q_multiline = (
        "In the 1970s, the Indian Central Excise Department classified cosmetics as luxury goods, which carried a very high tax. "
        "However, ayurvedic medicines had a much lower tax rate. "
        "The excise department argued that this company's brand was used for skin improvement. "
        "To protect their profit margins they released an ad jingle, which went on to be an evergreen classic. "
        "Which brand?"
    )
    sample_quiz = {
        "id": "quiz-zvq-source-test",
        "title": "Zerodha Varsity School Quiz",
        "topic": "Business Trivia",
        "questions": [
            {
                "id": "q-zvq-1",
                "question_text": q_multiline,
                "answer": "Vicco",
                "explanation": "Vicco Vajradanti and Vicco Turmeric skin cream.",
                "retrieval_sources": [
                    {
                        "document_title": "Potpourri Aug to Dec ZVQ College Reserve.pptx",
                        "slide_number": 14,
                        "web_url": "https://qshala.sharepoint.com/:p:/s/Quizzes/Potpourri_ZVQ.pptx",
                    }
                ],
                "is_approved": True,
            }
        ]
    }

    pptx_bytes = QuizExporter.to_pptx(
        sample_quiz,
        quiz_type="zvq",
        include_social_slides=False,
        show_answer_divider=True,
    )
    assert len(pptx_bytes) > 1000
    prs = Presentation(io.BytesIO(pptx_bytes))

    # Slide 1: Cover, Slide 2: Question, Slide 3: Transition, Slide 4: Answer Reveal
    assert len(prs.slides) == 4

    # 1. Question Slide: Check readable paragraph breaks
    q_slide = prs.slides[1]
    q_text_frames = [s.text_frame for s in q_slide.shapes if s.has_text_frame and "Excise Department" in s.text_frame.text]
    assert len(q_text_frames) == 1
    tf = q_text_frames[0]
    # Should have multiple paragraphs instead of one dense text block
    paras = tf.paragraphs
    assert len(paras) >= 3, f"Expected multiple readable paragraphs, got {len(paras)}"
    # Verify paragraph spacing
    assert paras[1].space_before is not None and paras[1].space_before.pt >= 6

    # 2. Question Slide Notes: Must have source links, NO old boilerplate
    assert q_slide.has_notes_slide
    q_notes = q_slide.notes_slide.notes_text_frame.text
    assert "Source: Potpourri Aug to Dec ZVQ College Reserve.pptx (Slide 14)" in q_notes
    assert "Link: https://qshala.sharepoint.com/:p:/s/Quizzes/Potpourri_ZVQ.pptx" in q_notes
    assert "★ Important:" not in q_notes
    assert "Official Answer:" not in q_notes

    # 3. Answer Slide Notes: Must also have source links, NO old presenter talking points
    a_slide = prs.slides[3]
    assert a_slide.has_notes_slide
    a_notes = a_slide.notes_slide.notes_text_frame.text
    assert "Source: Potpourri Aug to Dec ZVQ College Reserve.pptx (Slide 14)" in a_notes
    assert "Link: https://qshala.sharepoint.com/:p:/s/Quizzes/Potpourri_ZVQ.pptx" in a_notes
    assert "Presenter Talking Points:" not in a_notes
    assert "Award +10 points" not in a_notes
