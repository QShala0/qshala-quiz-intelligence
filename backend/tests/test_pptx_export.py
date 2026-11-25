import io
import uuid
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
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
    s0 = prs.slides[0]
    s0_texts = [p.text for s in s0.shapes if s.has_text_frame for p in s.text_frame.paragraphs]
    assert any("Varsity Corporate Finance Challenge" in t for t in s0_texts)
    assert any("Executive Track 2026" in t for t in s0_texts)

    # Verify corporate cover layout formatting, left-alignment, and positioning
    from pptx.enum.text import PP_ALIGN
    corp_tb = next(s for s in s0.shapes if s.has_text_frame and "Varsity Corporate Finance Challenge" in s.text_frame.text)
    assert corp_tb.left.inches == pytest.approx(0.88, 0.05)
    assert corp_tb.top.inches == pytest.approx(4.15, 0.05)
    assert corp_tb.width.inches == pytest.approx(5.40, 0.05)
    p_title = corp_tb.text_frame.paragraphs[0]
    assert p_title.alignment == PP_ALIGN.LEFT
    assert p_title.font.bold is True
    p_sub = corp_tb.text_frame.paragraphs[1]
    assert p_sub.alignment == PP_ALIGN.LEFT
    assert "Executive Track 2026" in p_sub.text

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
    2. Question slides carry no notes; answer slides carry only the source deck's original notes.
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

    # 2. Question Slide Notes: none at all (the answer must never sit in question notes)
    q_notes = q_slide.notes_slide.notes_text_frame.text if q_slide.has_notes_slide else ""
    assert q_notes == ""

    # 3. Answer Slide Notes: only the source deck's original notes, never generated boilerplate
    a_slide = prs.slides[3]
    a_notes = a_slide.notes_slide.notes_text_frame.text if a_slide.has_notes_slide else ""
    assert "Official Answer:" not in a_notes
    assert "Presenter Talking Points:" not in a_notes
    assert "Award +10 points" not in a_notes
    assert "Source:" not in a_notes


def test_zvq_and_zsv_quiz_type_endpoint_variants():
    """Verify that export endpoints accept all ZVQ and ZSV subtype variants without 422 validation errors."""
    client = TestClient(app)
    db = SessionLocal()
    quiz = Quiz(
        id=str(uuid.uuid4()),
        title="Varsity Markets Prelims",
        topic="Finance",
        difficulty="Balanced",
    )
    db.add(quiz)
    gq = GeneratedQuestion(
        id=str(uuid.uuid4()),
        quiz_id=quiz.id,
        question_text="What is a depository participant (DP)?",
        answer="An agent of the depository (e.g. NSDL or CDSL)",
        is_approved=True,
    )
    db.add(gq)
    db.commit()

    try:
        variants = ["zvq", "zvq_school", "zvq-school", "zsv", "zsv_school", "zvq_corporate", "zsv_corporate", "school", "corporate"]
        for v in variants:
            res_get = client.get(f"/api/v1/quizzes/{quiz.id}/export?format=pptx&quiz_type={v}")
            assert res_get.status_code == 200, f"GET with quiz_type={v} failed with {res_get.status_code}: {res_get.text}"
            assert len(res_get.content) > 1000

        payload = {
            "title": "Zerodha Varsity Economics",
            "topic": "Economics",
            "questions": [
                {
                    "question_text": "What is the repo rate?",
                    "answer": "The rate at which the central bank lends money to commercial banks.",
                    "is_approved": True,
                }
            ],
        }
        for v in ["zvq_school", "zvq-school", "zsv", "zvq_corporate"]:
            res_post = client.post(f"/api/v1/quizzes/export?format=pptx&quiz_type={v}", json=payload)
            assert res_post.status_code == 200, f"POST with quiz_type={v} failed with {res_post.status_code}: {res_post.text}"
            assert len(res_post.content) > 1000
    finally:
        db.query(GeneratedQuestion).filter(GeneratedQuestion.quiz_id == quiz.id).delete()
        db.query(Quiz).filter(Quiz.id == quiz.id).delete()
        db.commit()
        db.close()


def test_custom_template_formatting_font_sizes_and_bold_retention():
    """Verify custom template typography formatting: bold retention on questions/answers and font sizes."""
    from backend.app.models.custom_template import CustomPptTemplate

    db = SessionLocal()
    created_tpl_id = None
    tpl = db.query(CustomPptTemplate).filter(CustomPptTemplate.category == "zvq").first()
    if not tpl:
        tpl = db.query(CustomPptTemplate).first()
    if not tpl:
        sample_path = Path("backend/sample_decks/Australian_History_Quiz_2022.pptx")
        if sample_path.exists():
            with open(sample_path, "rb") as f:
                resp = client.post(
                    "/api/v1/templates/extract",
                    files={"file": ("sample.pptx", f, "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
                    data={"name": "Test ZVQ Custom Theme", "category": "zvq"},
                )
            assert resp.status_code == 200
            created_tpl_id = resp.json()["id"]
            tpl = db.query(CustomPptTemplate).filter(CustomPptTemplate.id == created_tpl_id).first()
    assert tpl is not None, "A custom template could not be loaded or extracted for testing"

    try:
        quiz_payload = {
            "title": "Championship Grand Finale",
            "topic": "General Knowledge",
            "questions": [
                {
                    "id": "q1",
                    "question_text": "What prestigious prize is awarded annually in literature, peace, physics, and medicine?",
                    "answer": "The Nobel Prize",
                    "explanation": "Established by the 1895 will of Alfred Nobel.",
                    "options": ["Nobel Prize", "Pulitzer Prize", "Booker Prize", "Fields Medal"],
                    "retrieval_sources": [{"document_title": "World Honors Deck", "slide_number": 5}],
                    "is_approved": True,
                },
                {
                    "id": "q2",
                    "question_text": "Which element has the atomic number 1 and is the most abundant chemical substance in the universe?",
                    "answer": "Hydrogen",
                    "explanation": "Hydrogen constitutes roughly 75% of all baryonic mass.",
                    "options": ["Hydrogen", "Helium", "Oxygen", "Carbon"],
                    "retrieval_sources": [{"document_title": "Chemistry Prelims", "slide_number": 8}],
                    "is_approved": True,
                },
            ],
        }

        pptx_bytes = QuizExporter.to_pptx(
            quiz_payload,
            quiz_type="school",
            template_id=tpl.id,
            db=db,
        )
        assert len(pptx_bytes) > 1000
        prs = Presentation(io.BytesIO(pptx_bytes))

        # Verify that the custom template font scheme was applied across slides
        found_question_bold = False
        found_answer_bold = False
        for slide in prs.slides:
            for sh in slide.shapes:
                if not sh.has_text_frame:
                    continue
                for par in sh.text_frame.paragraphs:
                    for r in par.runs:
                        # Question prompt run should retain bold = True
                        if "prestigious prize" in r.text:
                            assert r.font.bold is True, "Question prompt run must retain bold=True under custom template"
                            found_question_bold = True
                        # Answer title run should retain bold = True
                        if "Nobel Prize" in r.text and par.text.strip() == "The Nobel Prize":
                            assert r.font.bold is True, "Answer title run must retain bold=True under custom template"
                            found_answer_bold = True

        assert found_question_bold, "Question prompt was not found or verified"
        assert found_answer_bold, "Answer title was not found or verified"
    finally:
        if created_tpl_id:
            client.delete(f"/api/v1/templates/{created_tpl_id}")
        db.close()


def test_speaker_notes_source_links_and_answer_removal_on_question_slide():
    """
    Verify that in downloaded PPT slides:
    1. Question slide notes contains source references and links.
    2. Question slide notes does NOT contain the answer or 'Official Answer:' (no answer leak).
    3. Answer slide notes contains source links and context, without obsolete boilerplate.
    4. Works across standard School export and Custom Template export.
    """
    sample_quiz = {
        "id": "quiz-notes-test-clean",
        "title": "Maritime History Championship",
        "topic": "Exploration",
        "questions": [
            {
                "id": "q-notes-1",
                "question_text": "Which celestial event or landmark guided ancient Polynesian navigators across the Pacific Ocean?",
                "answer": "The Southern Cross Constellation",
                "explanation": "Used as a navigational compass by Pacific waypoint voyagers.",
                "visual_clues": "Look at the Southern sky stars.",
                "retrieval_sources": [
                    {
                        "document_title": "Polynesian Wayfinding & Astronomy.pptx",
                        "slide_number": 7,
                        "web_url": "https://qshala.sharepoint.com/:p:/s/Quizzes/Wayfinding_Pacific.pptx",
                    }
                ],
                "is_approved": True,
            }
        ]
    }

    pptx_bytes = QuizExporter.to_pptx(
        sample_quiz,
        quiz_type="school",
        include_social_slides=False,
        show_answer_divider=True,
    )
    assert len(pptx_bytes) > 1000
    prs = Presentation(io.BytesIO(pptx_bytes))

    # Slide index 1: Question Slide
    q_slide = prs.slides[1]
    assert q_slide.has_notes_slide
    q_notes = q_slide.notes_slide.notes_text_frame.text

    # 1. Answer, Presenter Clues, and Source Links MUST NOT be on Question slide notes
    assert "Official Answer:" not in q_notes, "Question slide notes must not contain 'Official Answer:'"
    assert "Southern Cross" not in q_notes, "Question slide notes must not reveal the answer!"
    assert "Presenter Clues:" not in q_notes, "Presenter Clues belong on the Answer slide, not Question slide"
    assert "Source:" not in q_notes, "Source links belong on the Answer slide, not Question slide"

    # Slide index 3: Answer Reveal Slide
    a_slide = prs.slides[3]
    assert a_slide.has_notes_slide
    a_notes = a_slide.notes_slide.notes_text_frame.text

    # 3. Answer slide notes must contain presenter clues, source links, and context, no old boilerplate
    assert "Presenter Talking Points:" not in a_notes
    assert "Context: Used as a navigational compass by Pacific waypoint voyagers." in a_notes
    assert "Presenter Clues: Look at the Southern sky stars." in a_notes
    assert "Source: Polynesian Wayfinding & Astronomy.pptx (Slide 7)" in a_notes
    assert "Link: https://qshala.sharepoint.com/:p:/s/Quizzes/Wayfinding_Pacific.pptx" in a_notes

    # 4. Answer slide must NOT have "Answer {idx}" header badge (separator slide is enough)
    for sh in a_slide.shapes:
        if sh.has_text_frame:
            assert "Answer 1" not in sh.text_frame.text, "Answer slide must not have redundant 'Answer 1' header badge"

    # 5. Answer text on answer slide must be center aligned
    ans_text_frames = [sh.text_frame for sh in a_slide.shapes if sh.has_text_frame and "Southern Cross" in sh.text_frame.text]
    assert len(ans_text_frames) == 1
    assert ans_text_frames[0].paragraphs[0].alignment == PP_ALIGN.CENTER, "Answer text must be center aligned"




def test_optimize_picture_source_shrinks_large_images(tmp_path):
    """Oversized images (>1600px) are downscaled and compressed to keep PPTX exports under Cloud Run limits."""
    from PIL import Image
    from backend.app.services.export.exporter import _optimize_picture_source

    big_png = tmp_path / "huge.png"
    img = Image.new("RGB", (3200, 2400), color=(40, 120, 200))
    img.save(big_png, format="PNG")
    orig_size = big_png.stat().st_size

    with Image.open(big_png) as opened:
        result = _optimize_picture_source(big_png, opened)

    assert isinstance(result, io.BytesIO)
    optimized_bytes = result.getvalue()
    assert len(optimized_bytes) < orig_size
    with Image.open(io.BytesIO(optimized_bytes)) as opt_img:
        assert max(opt_img.size) == 1600


def test_large_pptx_export_uses_r2_download_url(monkeypatch):
    """Exports exceeding Cloud Run's safe payload threshold upload to R2 and return a JSON download_url."""
    import json
    from unittest.mock import MagicMock
    from fastapi.responses import JSONResponse
    from backend.app.routers import quizzes as quizzes_router

    fake_r2 = MagicMock()
    fake_r2.is_configured = True
    fake_r2.upload_bytes.return_value = "https://pub-test.r2.dev/exports/q1/abc_test.pptx"

    monkeypatch.setattr(quizzes_router, "CLOUD_RUN_SAFE_EXPORT_BYTES", 100)
    monkeypatch.setattr("backend.app.services.storage.r2_storage.get_media_storage", lambda: fake_r2)

    resp = quizzes_router._pptx_export_response(b"x" * 250, "qshala_quiz_q1.pptx", "q1")
    assert isinstance(resp, JSONResponse)
    payload = json.loads(resp.body.decode("utf-8"))
    assert payload["download_url"] == "https://pub-test.r2.dev/exports/q1/abc_test.pptx"
    assert payload["filename"] == "qshala_quiz_q1.pptx"
    assert payload["size_bytes"] == 250
    fake_r2.upload_bytes.assert_called_once()
