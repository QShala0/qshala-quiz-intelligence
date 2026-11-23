import os
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.services.export.template_extractor import TemplateExtractor
from backend.app.services.export.exporter import QuizExporter
from backend.app.models.custom_template import CustomPptTemplate

client = TestClient(app)
SAMPLE_DECK = Path("backend/sample_decks/Australian_History_Quiz_2022.pptx")


def test_template_extractor_direct(tmp_path):
    assert SAMPLE_DECK.exists(), "Sample deck must exist for testing"
    extractor = TemplateExtractor(SAMPLE_DECK)
    data = extractor.extract(
        name="Test Extracted Theme",
        category="qshala_school",
        description="Unit test extraction",
        output_dir=tmp_path / "custom_tpl",
    )
    assert data["name"] == "Test Extracted Theme"
    assert data["category"] == "qshala_school"
    assert data["aspect_ratio"] == "16:9"
    assert data["body_size_pt"] == 28.0  # 28pt uniform standard
    assert Path(data["storage_path"]).exists()


def test_templates_api_crud_and_categories():
    assert SAMPLE_DECK.exists()
    
    # 1. Create a QShala School template
    with open(SAMPLE_DECK, "rb") as f:
        resp = client.post(
            "/api/v1/templates/extract",
            files={"file": ("sample.pptx", f, "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
            data={"name": "School Theme Alpha", "category": "qshala_school", "description": "School category test"},
        )
    assert resp.status_code == 200, resp.text
    school_tpl = resp.json()
    assert school_tpl["name"] == "School Theme Alpha"
    assert school_tpl["category"] == "qshala_school"
    assert school_tpl["body_size_pt"] == 28.0

    # 2. Create a ZVQ template
    with open(SAMPLE_DECK, "rb") as f:
        resp2 = client.post(
            "/api/v1/templates/extract",
            files={"file": ("sample2.pptx", f, "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
            data={"name": "ZVQ Championship Theme", "category": "zvq", "description": "ZVQ category test"},
        )
    assert resp2.status_code == 200, resp2.text
    zvq_tpl = resp2.json()
    assert zvq_tpl["category"] == "zvq"

    # 3. List all
    all_res = client.get("/api/v1/templates")
    assert all_res.status_code == 200
    all_tpls = all_res.json()
    ids = [t["id"] for t in all_tpls]
    assert school_tpl["id"] in ids
    assert zvq_tpl["id"] in ids

    # 4. Filter by category
    school_only = client.get("/api/v1/templates?category=qshala_school").json()
    assert any(t["id"] == school_tpl["id"] for t in school_only)
    assert not any(t["id"] == zvq_tpl["id"] for t in school_only)

    zvq_only = client.get("/api/v1/templates?category=zvq").json()
    assert any(t["id"] == zvq_tpl["id"] for t in zvq_only)
    assert not any(t["id"] == school_tpl["id"] for t in zvq_only)

    # 5. Download master PPTX
    dl_res = client.get(f"/api/v1/templates/{school_tpl['id']}/download")
    assert dl_res.status_code == 200
    assert len(dl_res.content) > 1000
    assert "application/vnd.openxmlformats-officedocument.presentationml.presentation" in dl_res.headers["content-type"]

    # 6. Clean up
    del1 = client.delete(f"/api/v1/templates/{school_tpl['id']}")
    assert del1.status_code == 200
    del2 = client.delete(f"/api/v1/templates/{zvq_tpl['id']}")
    assert del2.status_code == 200


def test_export_quiz_with_custom_template():
    # 1. Create a custom template
    with open(SAMPLE_DECK, "rb") as f:
        resp = client.post(
            "/api/v1/templates/extract",
            files={"file": ("sample.pptx", f, "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
            data={"name": "Export Test Theme", "category": "qshala_school"},
        )
    assert resp.status_code == 200
    tpl = resp.json()
    tpl_id = tpl["id"]

    try:
        # 2. Export a sample quiz payload with template_id
        quiz_payload = {
            "id": "quiz-test-custom-tpl",
            "title": "Aussie History Quiz",
            "topic": "Australian History",
            "questions": [
                {
                    "question_text": "Who explored Botany Bay in 1770?",
                    "answer": "Captain James Cook",
                    "explanation": "He mapped the eastern coastline.",
                    "is_approved": True,
                }
            ],
        }

        export_resp = client.post(
            f"/api/v1/quizzes/export?format=pptx&template_id={tpl_id}",
            json=quiz_payload,
        )
        assert export_resp.status_code == 200
        assert len(export_resp.content) > 1000
        assert export_resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.presentationml.presentation"

    finally:
        client.delete(f"/api/v1/templates/{tpl_id}")


def test_multi_slide_preview_and_theme_extraction():
    assert SAMPLE_DECK.exists()
    with open(SAMPLE_DECK, "rb") as f:
        resp = client.post(
            "/api/v1/templates/extract",
            files={"file": ("sample_theme.pptx", f, "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
            data={"name": "Theme & Multi Preview Test", "category": "zvq"},
        )
    assert resp.status_code == 200
    tpl = resp.json()
    tpl_id = tpl["id"]

    try:
        # Check that layout_metadata contains theme and color scheme
        meta = tpl.get("layout_metadata") or {}
        theme = meta.get("theme") or {}
        assert "color_scheme" in theme, "Extracted template must contain theme color scheme"
        assert "fonts" in theme or "typeface_scheme" in theme, "Extracted template must contain theme fonts"
        assert len(theme["color_scheme"]) > 0, "Color scheme must have entries"

        # Check preview endpoints for all slide tabs
        for slide_tab in ("cover", "question", "divider", "answer"):
            prev_resp = client.get(f"/api/v1/templates/{tpl_id}/preview?slide={slide_tab}")
            assert prev_resp.status_code == 200, f"Preview for slide={slide_tab} failed: {prev_resp.status_code}"
            assert prev_resp.headers["content-type"].startswith("image/")
            assert len(prev_resp.content) > 100

    finally:
        client.delete(f"/api/v1/templates/{tpl_id}")

