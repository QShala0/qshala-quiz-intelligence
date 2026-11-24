import io
import pytest
from pptx import Presentation
from backend.app.services.export.exporter import QuizExporter
from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app)

def test_qshala_school_pptx_export_structure():
    sample_quiz = {
        "id": "quiz_qshala_demo_001",
        "title": "Wildlife and Biodiversity Challenge",
        "topic": "Environment & Nature",
        "questions": [
            {
                "order_index": 1,
                "question_text": "Which national park in Assam is famous for the one-horned rhinoceros?",
                "answer": "Kaziranga National Park",
                "explanation": "Kaziranga holds two-thirds of the world's great one-horned rhinoceroses and is a UNESCO World Heritage Site.",
                "options": ["Jim Corbett", "Kaziranga", "Gir Forest", "Sundarbans"],
                "is_approved": True,
                "retrieval_sources": [
                    {"document_title": "Assam_Eco_Tour.pptx", "slide_number": 8}
                ]
            },
            {
                "order_index": 2,
                "question_text": "What is the fastest land animal on Earth?",
                "answer": "Cheetah",
                "explanation": "Cheetahs can accelerate from 0 to 60 mph in just 3 seconds.",
                "is_approved": True
            }
        ]
    }

    # 1. Standard export with divider
    pptx_bytes = QuizExporter.to_pptx(
        sample_quiz,
        quiz_type="qshala_school",
        custom_title="Inter-School Wildlife Olympiad",
        custom_subtitle="Grades 6 - 8 • Annual Science Festival",
        show_answer_divider=True
    )
    assert len(pptx_bytes) > 2000
    prs = Presentation(io.BytesIO(pptx_bytes))
    
    # Expected slides:
    # 1. Cover
    # 2. Q1
    # 3. Answer Divider 1
    # 4. Answer 1
    # 5. Q2
    # 6. Answer Divider 2
    # 7. Answer 2
    assert len(prs.slides) == 7

    # Verify Cover Slide
    cover_slide = prs.slides[0]
    cover_text = " ".join([shape.text_frame.text for shape in cover_slide.shapes if shape.has_text_frame])
    assert "Inter-School Wildlife Olympiad" in cover_text
    assert "Grades 6 - 8" in cover_text

    # Verify Q1 Slide
    q1_slide = prs.slides[1]
    q1_text = " ".join([shape.text_frame.text for shape in q1_slide.shapes if shape.has_text_frame])
    assert "Question 1" in q1_text
    assert "Kaziranga" in q1_text
    notes_q1 = q1_slide.notes_slide.notes_text_frame.text if q1_slide.has_notes_slide else ""
    assert notes_q1 == ""

    # Verify Answer Divider Slide
    ans_div_slide = prs.slides[2]
    div_text = " ".join([shape.text_frame.text for shape in ans_div_slide.shapes if shape.has_text_frame])
    assert "Answer" in div_text

    # Verify Answer Reveal Slide
    ans1_slide = prs.slides[3]
    ans1_text = " ".join([shape.text_frame.text for shape in ans1_slide.shapes if shape.has_text_frame])
    assert "Kaziranga National Park" in ans1_text
    assert "UNESCO" in ans1_text
    notes_a1 = ans1_slide.notes_slide.notes_text_frame.text if ans1_slide.has_notes_slide else ""
    assert "Presenter Talking Points" not in notes_a1
    assert "Official Answer" not in notes_a1


def test_qshala_school_pptx_export_without_divider():
    sample_quiz = {
        "id": "quiz_qshala_no_div",
        "title": "Quick Trivia",
        "questions": [
            {
                "order_index": 1,
                "question_text": "What is the capital of France?",
                "answer": "Paris",
                "explanation": "Paris is located along the Seine River.",
                "is_approved": True
            }
        ]
    }

    pptx_bytes = QuizExporter.to_pptx(
        sample_quiz,
        quiz_type="qshala_school",
        show_answer_divider=False
    )
    prs = Presentation(io.BytesIO(pptx_bytes))
    # 1. Cover, 2. Q1, 3. Answer 1 -> total 3 slides
    assert len(prs.slides) == 3


def test_quizzes_export_api_router_qshala_school():
    sample_quiz = {
        "id": "quiz_api_export_test",
        "title": "Astronomy Quiz",
        "topic": "Astronomy",
        "questions": [
            {
                "order_index": 1,
                "question_text": "Which planet is known as the Red Planet?",
                "answer": "Mars",
                "explanation": "Mars appears red due to iron oxide on its surface.",
                "is_approved": True
            }
        ]
    }

    # Test POST /api/v1/quizzes/export with quiz_type=qshala_school
    response = client.post(
        "/api/v1/quizzes/export?format=pptx&quiz_type=qshala_school&custom_title=Cosmic+Journey",
        json=sample_quiz
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    assert len(response.content) > 1000

    prs = Presentation(io.BytesIO(response.content))
    assert len(prs.slides) >= 3
    cover_text = " ".join([shape.text_frame.text for shape in prs.slides[0].shapes if shape.has_text_frame])
    assert "Cosmic Journey" in cover_text


def test_qshala_school_causten_regular_bold_headings_black_answer_and_media_fit(tmp_path):
    """Style rules: Causten Round (standard weight) for body text, Bold only on headings (cover title, question and
    answer number, the Answer divider),
    answers in black (never green), and images sized to the card without leaving the safe area."""
    from PIL import Image
    from pptx.util import Inches
    from backend.app.services.export import exporter as ex

    img_dir = ex.Path(ex.__file__).resolve().parents[4] / "backend" / "storage" / "_pytest_qs_fit"
    img_dir.mkdir(parents=True, exist_ok=True)
    try:
        refs = []
        for name, size in (("land", (1600, 900)), ("port", (900, 1400))):
            Image.new("RGB", size, (60, 120, 180)).save(img_dir / f"{name}.png")
            refs.append(f"backend/storage/_pytest_qs_fit/{name}.png")
        quiz = {"title": "t", "topic": "GK", "questions": [
            {"question_text": "Name this landmark.", "answer": "Taj Mahal", "explanation": "Built by Shah Jahan.",
             "image_refs": [refs[0]], "is_approved": True},
            {"question_text": "x " * 140, "answer": "Kabaddi", "explanation": "y " * 60,
             "image_refs": [refs[1]], "is_approved": True},
        ]}
        prs = Presentation(io.BytesIO(QuizExporter.to_pptx(
            quiz, quiz_type="qshala_school", custom_title="Quiz Hobby Class!",
            custom_subtitle="Place your bets\nWorkshop 9")))
    finally:
        import shutil
        shutil.rmtree(img_dir, ignore_errors=True)

    bold_texts, pics = [], 0
    for slide in prs.slides:
        for sh in slide.shapes:
            if sh.shape_type == 13 and sh.width < Inches(13):  # pictures other than the full-bleed backdrop
                pics += 1
                assert sh.left >= Inches(0.9) and sh.left + sh.width <= Inches(12.45)
                assert sh.top >= Inches(1.5) and sh.top + sh.height <= Inches(6.25)
            if not sh.has_text_frame:
                continue
            for par in sh.text_frame.paragraphs:
                for r in par.runs:
                    assert r.font.name in ("Causten Round", "Causten Round Bold"), r.font.name
                    if r.font.name == "Causten Round Bold":
                        bold_texts.append(r.text)
                    if r.font.color and r.font.color.type is not None:
                        assert str(r.font.color.rgb).upper() != "059669"  # answers are black, not green
    assert pics == 4  # question + answer image for both questions
    assert set(bold_texts) == {"Quiz Hobby Class!", "Question 1", "Answer 1", "Question 2", "Answer 2", "Answer"}


def test_qshala_school_answer_image_never_on_question_slide(tmp_path):
    """An image tied to the answer (e.g. the branded product/ad) must not appear on the question slide in the
    School template, or it gives the answer away. A distinct question image must still show."""
    from PIL import Image
    from pptx.util import Inches
    from backend.app.services.export import exporter as ex

    img_dir = ex.Path(ex.__file__).resolve().parents[4] / "backend" / "storage" / "_pytest_ans_leak"
    img_dir.mkdir(parents=True, exist_ok=True)
    try:
        for name in ("reveal", "clue"):
            Image.new("RGB", (800, 500), (120, 120, 120)).save(img_dir / f"{name}.png")
        reveal = "backend/storage/_pytest_ans_leak/reveal.png"
        clue = "backend/storage/_pytest_ans_leak/clue.png"
        quiz = {"title": "t", "topic": "GK", "questions": [
            # Q1: the only image is also the answer image -> must be dropped from the question slide
            {"question_text": "Which product, whose name means 'little horn' in Italian, was born from a 1959 wafer innovation?",
             "answer": "Cornetto", "image_refs": [reveal], "answer_images": [reveal], "is_approved": True},
            # Q2: a genuine question clue image, not an answer image -> must stay on the question slide
            {"question_text": "Name this landmark.", "answer": "Taj Mahal",
             "question_images": [clue], "is_approved": True},
        ]}
        prs = Presentation(io.BytesIO(QuizExporter.to_pptx(quiz, quiz_type="qshala_school", show_answer_divider=True)))
    finally:
        import shutil
        shutil.rmtree(img_dir, ignore_errors=True)

    def content_pics(slide):
        # pictures that are not the full-bleed backdrop
        return [sh for sh in slide.shapes if sh.shape_type == 13 and sh.width < Inches(13)]

    def slide_with(text):
        for sl in prs.slides:
            if any(sh.has_text_frame and text in sh.text_frame.text for sh in sl.shapes):
                return sl
        raise AssertionError(f"no slide with {text!r}")

    # Q1: answer image dropped from the question, present on the answer
    assert len(content_pics(slide_with("Question 1"))) == 0
    assert len(content_pics(slide_with("Answer 1"))) >= 1
    # Q2: distinct clue image stays on the question
    assert len(content_pics(slide_with("Question 2"))) >= 1


def test_qshala_school_template_lives_in_the_slide_master_not_on_slides():
    """The artwork must come from the deck's slide master / layouts (not selectable), never from a picture stuck on
    each slide; the theme fonts are Causten Round; every run, including ea/cs, names a Causten face."""
    import re
    import zipfile
    from pptx.util import Inches
    quiz = {"title": "t", "topic": "GK", "questions": [
        {"question_text": "Which planet is the Red Planet?", "answer": "Mars", "is_approved": True}]}
    raw = QuizExporter.to_pptx(quiz, quiz_type="qshala_school", custom_title="Hobby Quiz", custom_subtitle="Round 1")
    prs = Presentation(io.BytesIO(raw))

    # artwork: in the master and the cover/divider layouts, on no slide
    assert any(sh.shape_type == 13 for sh in prs.slide_master.shapes)
    layouts = {l.name: l for l in prs.slide_layouts}
    assert any(sh.shape_type == 13 for sh in layouts["Title Slide"].shapes)
    assert any(sh.shape_type == 13 for sh in layouts["Section Header"].shapes)
    for slide in prs.slides:
        assert not any(sh.shape_type == 13 for sh in slide.shapes), "background picture found on a slide"

    # cover = Title Slide layout, divider = Section Header, the rest sit on the master panel
    names = [s.slide_layout.name for s in prs.slides]
    assert names == ["Title Slide", "Blank", "Section Header", "Blank"], names
    assert prs.slides[0].shapes.title.text_frame.text == "Hobby Quiz"
    assert prs.slides[2].shapes.title.text_frame.text == "Answer"

    # theme fonts are Causten, not Aptos/Calibri
    theme = zipfile.ZipFile(io.BytesIO(raw)).read("ppt/theme/theme1.xml").decode()
    assert re.search(r'<a:majorFont><a:latin typeface="Causten Round Bold"', theme)
    assert re.search(r'<a:minorFont><a:latin typeface="Causten Round"', theme)

    # every run names a Causten face for latin + ea + cs
    ok = {"Causten Round", "Causten Round Bold"}
    for slide in prs.slides:
        for sh in slide.shapes:
            if not sh.has_text_frame:
                continue
            for par in sh.text_frame.paragraphs:
                for r in par.runs:
                    rPr = r._r.rPr
                    faces = {el.get("typeface") for el in rPr if el.tag.split("}")[1] in ("latin", "ea", "cs")}
                    assert faces and faces <= ok, (r.text, faces)


def test_qshala_school_font_size_28_and_image_in_next_slide():
    """QShala School style rule: Question text font size must be 28pt everywhere (never reduced).
    If there is no space for the image alongside the question at 28pt, question slide has
    '(image in next slide)' and the image is placed on a dedicated following slide."""
    from PIL import Image
    from pptx.util import Inches
    import shutil
    from backend.app.services.export import exporter as ex

    img_dir = ex.Path(ex.__file__).resolve().parents[4] / "backend" / "storage" / "_pytest_font28"
    img_dir.mkdir(parents=True, exist_ok=True)
    try:
        img_path = img_dir / "unicorn_wiz.png"
        Image.new("RGB", (1000, 600), (40, 80, 160)).save(img_path)

        q_long = (
            "While a 'unicorn' denotes an unlisted company that has achieved a valuation of over $1 billion, "
            "a '_____' is one that raises $1 billion in a single investor round. "
            "Flipkart was the first such Indian company when it raised $1 billion in July 2014. "
            "The term is named after a mythical creature that is depicted in Western cultures as winged, horned, and capable of breathing fire. "
            "Which term/mythical creature?"
        )

        quiz = {
            "title": "School Championship",
            "topic": "Business & Startups",
            "questions": [
                {
                    "order_index": 1,
                    "question_text": q_long,
                    "answer": "Dragon",
                    "explanation": "A dragon company raises $1B in a single round.",
                    "question_images": [f"backend/storage/_pytest_font28/unicorn_wiz.png"],
                    "is_approved": True,
                },
                {
                    "order_index": 2,
                    "question_text": "What is the capital of Australia?",
                    "answer": "Canberra",
                    "is_approved": True,
                }
            ]
        }

        raw = QuizExporter.to_pptx(quiz, quiz_type="qshala_school", show_answer_divider=True)
        prs = Presentation(io.BytesIO(raw))

        # Q1 Long: should have Question slide (Slide 1) and Image slide (Slide 2), then Divider (3), Answer (4)
        q1_slide = prs.slides[1]
        q1_texts = [sh.text_frame.text for sh in q1_slide.shapes if sh.has_text_frame]
        assert any("Question 1" in t for t in q1_texts)
        assert any("(image in next slide)" in t for t in q1_texts)

        # Check font sizes on Q1 slide: question runs must be 28pt, sub heading 20pt
        found_28 = False
        found_sub_20 = False
        for sh in q1_slide.shapes:
            if sh.has_text_frame and "unicorn" in sh.text_frame.text:
                for par in sh.text_frame.paragraphs:
                    for r in par.runs:
                        if "unicorn" in r.text:
                            assert r.font.size.pt == 28.0
                            found_28 = True
                        if "(image in next slide)" in r.text:
                            assert r.font.size.pt == 20.0
                            assert r.font.italic is True
                            found_sub_20 = True
        assert found_28
        assert found_sub_20

        # Dedicated image slide
        img_slide = prs.slides[2]
        img_texts = [sh.text_frame.text for sh in img_slide.shapes if sh.has_text_frame]
        assert any("Question 1" in t for t in img_texts)
        pics = [sh for sh in img_slide.shapes if sh.shape_type == 13 and sh.width < Inches(13)]
        assert len(pics) == 1

        # Q2 Pure Text Question: Question text run must be 28pt
        q2_slide = prs.slides[5]
        for sh in q2_slide.shapes:
            if sh.has_text_frame and "capital" in sh.text_frame.text:
                for par in sh.text_frame.paragraphs:
                    for r in par.runs:
                        if "capital" in r.text:
                            assert r.font.size.pt == 28.0
    finally:
        shutil.rmtree(img_dir, ignore_errors=True)


@pytest.mark.parametrize("quiz_type", ["qshala_school", "zsv"])
def test_export_puts_original_deck_notes_on_answer_slide_only(quiz_type):
    import uuid
    from backend.app.database import SessionLocal
    from backend.app.models.document import Document, Slide
    from backend.app.models.question import Question

    db = SessionLocal()
    doc_id, qs_id, as_id, q_id = (str(uuid.uuid4()) for _ in range(4))
    try:
        db.add(Document(id=doc_id, filename="orig.pptx", title="orig", file_type="pptx", storage_path="orig.pptx"))
        db.flush()
        db.add(Slide(id=qs_id, document_id=doc_id, slide_number=1, speaker_notes="Host: ask slowly."))
        db.add(Slide(id=as_id, document_id=doc_id, slide_number=2, speaker_notes="Fun fact: Mars is red due to iron oxide."))
        db.flush()
        db.add(Question(id=q_id, document_id=doc_id, slide_id=qs_id, answer_slide_id=as_id,
                        question_text="Which planet is the Red Planet?", answer="Mars", topic="Space"))
        db.commit()

        quiz = {"id": "q_notes", "title": "Notes", "questions": [
            {"id": q_id, "order_index": 1, "question_text": "Which planet is the Red Planet?",
             "answer": "Mars", "is_approved": True}]}
        prs = Presentation(io.BytesIO(QuizExporter.to_pptx(quiz, quiz_type=quiz_type, show_answer_divider=True)))

        q_slide, ans_slide = prs.slides[1], prs.slides[3]
        q_notes = q_slide.notes_slide.notes_text_frame.text if q_slide.has_notes_slide else ""
        assert "Mars" not in q_notes and q_notes == ""
        a_notes = ans_slide.notes_slide.notes_text_frame.text
        assert "Host: ask slowly." in a_notes
        assert "Fun fact: Mars is red due to iron oxide." in a_notes
        assert "Presenter Talking Points" not in a_notes
    finally:
        db.query(Question).filter(Question.id == q_id).delete()
        db.query(Slide).filter(Slide.document_id == doc_id).delete()
        db.query(Document).filter(Document.id == doc_id).delete()
        db.commit()
        db.close()
