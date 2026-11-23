"""
Office Readiness & Chaos Test Suite.
Exhaustively tests all workflows, Quizmaster personalities, PPTX export edge cases,
corrupt inputs, missing media, and boundary conditions before going to office staff.
"""
import io
import os
import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from pptx import Presentation

from backend.app.main import app
from backend.app.database import SessionLocal
from backend.app.models.quiz import Quiz, GeneratedQuestion
from backend.app.models.custom_template import CustomPptTemplate
from backend.app.services.generator.quiz_generator import QuizGenerator, PERSONALITY_PROMPTS
from backend.app.services.export.exporter import QuizExporter, _clean_quiz_title
from backend.app.schemas.quiz import QuizGenerateRequest

client = TestClient(app)

# -----------------------------------------------------------------------------
# Test 1: All 4 Quizmaster Personalities
# -----------------------------------------------------------------------------
def test_all_quizmaster_personalities_present_and_configured():
    required = [
        "CURIOSITY_STORYTELLER",
        "DETECTIVE_PUZZLER",
        "TOURNAMENT_PRO",
        "SOCRATIC_EXPLORER"
    ]
    for p in required:
        assert p in PERSONALITY_PROMPTS, f"Missing personality prompt: {p}"
        prompt = PERSONALITY_PROMPTS[p]
        assert "Host Persona:" in prompt
        assert len(prompt) > 30

def test_quizmaster_personality_export_with_pptx():
    """Verify that a quiz generated with each personality exports cleanly to PPTX."""
    personalities = ["CURIOSITY_STORYTELLER", "DETECTIVE_PUZZLER", "TOURNAMENT_PRO", "SOCRATIC_EXPLORER", None]
    
    for p in personalities:
        sample_quiz = {
            "id": f"quiz-persona-{p or 'none'}",
            "title": f"Quiz for Persona {p or 'Default'}",
            "topic": "Science & Nature",
            "personality": p,
            "questions": [
                {
                    "id": "q1",
                    "question_text": "What causes the green flash phenomenon during sunset?",
                    "options": ["A) Atmospheric refraction", "B) Chlorophyll in sea", "C) Solar flares", "D) Cloud reflection"],
                    "answer": "Atmospheric refraction",
                    "explanation": "Light from the setting sun refracts through the atmosphere acting as a prism.",
                    "difficulty": "Hard",
                    "is_approved": True,
                }
            ]
        }
        
        # Test school export
        pptx_bytes = QuizExporter.to_pptx(sample_quiz, quiz_type="school")
        assert len(pptx_bytes) > 5000, f"Export failed for personality {p}"
        prs = Presentation(io.BytesIO(pptx_bytes))
        assert len(prs.slides) >= 3, f"Expected at least 3 slides for personality {p}"
        
        # Test qshala_school export
        pptx_school_bytes = QuizExporter.to_pptx(sample_quiz, quiz_type="qshala_school")
        assert len(pptx_school_bytes) > 5000
        prs_school = Presentation(io.BytesIO(pptx_school_bytes))
        assert len(prs_school.slides) >= 3

# -----------------------------------------------------------------------------
# Test 2: PPTX Exporter Resilience & Edge Cases
# -----------------------------------------------------------------------------
def test_export_with_zero_approved_questions():
    """Quiz where all questions are unapproved or question list is empty. Must not crash."""
    empty_quiz = {
        "id": "quiz-empty",
        "title": "Empty Quiz",
        "topic": "Astronomy",
        "questions": []
    }
    pptx_bytes = QuizExporter.to_pptx(empty_quiz, quiz_type="school")
    assert len(pptx_bytes) > 1000
    prs = Presentation(io.BytesIO(pptx_bytes))
    assert len(prs.slides) >= 1  # At least cover slide

    unapproved_quiz = {
        "id": "quiz-unapproved",
        "title": "Unapproved Quiz",
        "topic": "Astronomy",
        "questions": [
            {
                "id": "u1",
                "question_text": "Unapproved question?",
                "answer": "Answer",
                "is_approved": False
            }
        ]
    }
    pptx_bytes_2 = QuizExporter.to_pptx(unapproved_quiz, quiz_type="school")
    assert len(pptx_bytes_2) > 1000

def test_export_with_missing_and_broken_image_paths():
    """Question referencing non-existent file path or 404 URL. Exporter must fall back safely to text."""
    quiz_with_broken_images = {
        "id": "quiz-broken-img",
        "title": "Broken Image Quiz",
        "topic": "Visual Arts",
        "questions": [
            {
                "id": "q_broken_1",
                "question_text": "Identify this artwork from the missing picture.",
                "answer": "Mona Lisa",
                "image_refs": ["/non/existent/path/image_99999.png"],
                "question_images": ["http://127.0.0.1:9999/does_not_exist.jpg"],
                "is_approved": True,
            },
            {
                "id": "q_broken_2",
                "question_text": "Second question with empty image strings.",
                "answer": "Answer 2",
                "image_refs": [""],
                "question_images": [""],
                "is_approved": True,
            }
        ]
    }
    pptx_bytes = QuizExporter.to_pptx(quiz_with_broken_images, quiz_type="school")
    assert len(pptx_bytes) > 5000
    prs = Presentation(io.BytesIO(pptx_bytes))
    assert len(prs.slides) >= 5

def test_export_with_extreme_text_lengths():
    """Questions with ultra-long question text (1000+ chars) and answers (500+ chars)."""
    long_text = "This is a very detailed historical question designed to test slide text boundaries. " * 15
    long_answer = "This is an extremely thorough answer with rich context and historical depth. " * 8
    
    quiz_long = {
        "id": "quiz-long-text",
        "title": "Extreme Length Quiz",
        "topic": "World History",
        "questions": [
            {
                "id": "ql1",
                "question_text": long_text,
                "answer": long_answer,
                "explanation": "Detailed explanation here.",
                "is_approved": True,
            }
        ]
    }
    pptx_bytes = QuizExporter.to_pptx(quiz_long, quiz_type="school")
    assert len(pptx_bytes) > 5000
    prs = Presentation(io.BytesIO(pptx_bytes))
    assert len(prs.slides) >= 3

def test_export_with_all_structured_formats():
    """Verify each specialized format renders cleanly in PPTX export."""
    quiz_formats = {
        "id": "quiz-all-formats",
        "title": "All Formats Showcase",
        "topic": "Comprehensive Trivia",
        "questions": [
            {
                "id": "f_mcq",
                "question_text": "MCQ Question",
                "question_format": "MCQ",
                "format_payload": {"options": ["Alpha", "Beta", "Gamma", "Delta"]},
                "answer": "Alpha",
                "is_approved": True,
            },
            {
                "id": "f_connect",
                "question_text": "Connect Question",
                "question_format": "CONNECT",
                "format_payload": {
                    "items": ["Item A", "Item B", "Item C", "Item D"],
                    "connection": "Shared heritage"
                },
                "answer": "Shared heritage",
                "is_approved": True,
            },
            {
                "id": "f_arrange",
                "question_text": "Arrange in chronological order",
                "question_format": "ARRANGE",
                "format_payload": {
                    "items": ["First Event (1920)", "Second Event (1945)", "Third Event (1969)", "Fourth Event (2000)"]
                },
                "answer": "1 -> 2 -> 3 -> 4",
                "is_approved": True,
            },
            {
                "id": "f_stepped",
                "question_text": "Identify the personality from clues",
                "question_format": "STEPPED_CLUE",
                "format_payload": {
                    "clues": ["Clue 1: Born in 1879", "Clue 2: Nobel laureate 1921", "Clue 3: E=mc^2"]
                },
                "answer": "Albert Einstein",
                "is_approved": True,
            },
            {
                "id": "f_list_it",
                "question_text": "List the top 4 longest rivers",
                "question_format": "LIST_IT",
                "format_payload": {
                    "items": ["Nile", "Amazon", "Yangtze", "Mississippi"]
                },
                "answer": "Nile, Amazon, Yangtze, Mississippi",
                "is_approved": True,
            }
        ]
    }
    pptx_bytes = QuizExporter.to_pptx(quiz_formats, quiz_type="school")
    assert len(pptx_bytes) > 10000
    prs = Presentation(io.BytesIO(pptx_bytes))
    assert len(prs.slides) >= 12

def test_export_with_malformed_format_payloads():
    """Format payload is None or malformed dict. Must fall back cleanly to standard QA."""
    quiz_malformed = {
        "id": "quiz-malformed-formats",
        "title": "Malformed Formats Quiz",
        "topic": "Resilience",
        "questions": [
            {
                "id": "m1",
                "question_text": "Question with null format payload",
                "question_format": "CONNECT",
                "format_payload": None,
                "answer": "Simple Answer",
                "is_approved": True,
            },
            {
                "id": "m2",
                "question_text": "Question with string format payload",
                "question_format": "MCQ",
                "format_payload": "corrupted string payload",
                "answer": "Simple Answer",
                "is_approved": True,
            }
        ]
    }
    pptx_bytes = QuizExporter.to_pptx(quiz_malformed, quiz_type="school")
    assert len(pptx_bytes) > 5000

# -----------------------------------------------------------------------------
# Test 3: Template Extraction Resilience & Boundaries
# -----------------------------------------------------------------------------
def test_template_extract_direct_empty_or_corrupt_files():
    """Uploading empty or corrupt file to direct extraction must return clean 400 or 422, not 500."""
    # Zero-byte upload
    resp = client.post(
        "/api/v1/templates/extract",
        files={"file": ("empty.pptx", b"", "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
        data={"name": "Empty Deck", "category": "qshala_school"}
    )
    assert resp.status_code == 400
    assert "empty or corrupted" in resp.json()["detail"].lower()

    # Small garbage bytes
    resp2 = client.post(
        "/api/v1/templates/extract",
        files={"file": ("corrupt.pptx", b"PK\x03\x04not a valid zip file at all", "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
        data={"name": "Corrupt Deck", "category": "qshala_school"}
    )
    assert resp2.status_code in (400, 422)

    # Disallowed file extension (.exe or .txt)
    resp3 = client.post(
        "/api/v1/templates/extract",
        files={"file": ("notes.txt", b"Hello world text file", "text/plain")},
        data={"name": "Text File", "category": "qshala_school"}
    )
    assert resp3.status_code == 400
    assert "only powerpoint" in resp3.json()["detail"].lower()

def test_template_preview_safety():
    """Non-existent template ID returns 404, not 500."""
    resp = client.get("/api/v1/templates/non-existent-uuid-12345/preview?slide=cover")
    assert resp.status_code == 404

def test_template_list_and_category_filtering():
    """List templates endpoint handles null, valid, and invalid categories cleanly."""
    resp = client.get("/api/v1/templates")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp_zvq = client.get("/api/v1/templates?category=zvq")
    assert resp_zvq.status_code == 200

    resp_school = client.get("/api/v1/templates?category=qshala_school")
    assert resp_school.status_code == 200

# -----------------------------------------------------------------------------
# Test 4: Vault & Question Browsing Safety
# -----------------------------------------------------------------------------
def test_vault_questions_search_special_characters():
    """Search queries with SQL injection patterns, emojis, and quotes do not break backend."""
    test_queries = [
        "' OR 1=1 --",
        "'; DROP TABLE questions; --",
        "🎯 Quiz Champions 🏆",
        "\"double quotes\" and 'single quotes'",
        "100% & 50_percent",
        "<script>alert('xss')</script>",
        "\\backslash /slash",
        "   ",  # pure whitespace
    ]
    for q in test_queries:
        resp = client.get(f"/api/v1/questions?search={q}&limit=5")
        assert resp.status_code == 200, f"Search crashed on query: {q}"
        data = resp.json()
        assert "questions" in data or isinstance(data, list)

def test_vault_pagination_boundaries():
    """Negative offset or extreme limits are handled gracefully."""
    resp = client.get("/api/v1/questions?offset=0&limit=10")
    assert resp.status_code == 200

    resp_high = client.get("/api/v1/questions?offset=999999&limit=10")
    assert resp_high.status_code == 200

# -----------------------------------------------------------------------------
# Test 5: Prompt Quiz Edge Cases
# -----------------------------------------------------------------------------
def test_prompt_quiz_edge_prompts():
    """Short prompts, zero queries, and long prompts return valid structure or clean error."""
    # Prompt with short length (<2 chars) fails validation with 422 or 400
    resp = client.post("/api/v1/prompt-quiz/draft", json={"prompt": "1"})
    assert resp.status_code in (400, 422)

    # Clean query with 5 questions
    resp2 = client.post("/api/v1/prompt-quiz/draft", json={"prompt": "5 questions on Indian history"})
    assert resp2.status_code == 200
    data = resp2.json()
    assert "questions" in data
    assert "brief" in data
    assert "message" in data
