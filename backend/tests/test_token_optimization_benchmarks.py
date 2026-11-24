"""
Token optimization benchmark test suite.
Verifies all implemented token-cost optimizations across the backend:
- Fix 1: AutoTagger confidence gate (FORCE_AI_TAGGING=False, thresholding)
- Fix 2: Curator candidate summaries trimming (deck tags, decimal scores, standard format tags removed)
- Fix 3: Interpreter LLM output token capping (maxOutputTokens: 800)
- Fix 4: Job queue batched tagger documentation
- Fix 5: GeminiProvider.generate_json output cap (1024) and thinking control
- Fix 6: QuizGenerator / ContextBuilder evidence capping (2x), explanation truncation, options N/A removal
- Fix 7 & 8: Multimodal and vision cost control baselines
- Fix 9: Intent engine taxonomy L2 description stripping
"""
import asyncio
import re
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from backend.app.config import settings
from backend.app.services.ai import gemini_provider as gp
from backend.app.services.ai.gemini_provider import GeminiLLMProvider
from backend.app.services.prompt_quiz.curator_llm import _build_curator_prompt
from backend.app.services.prompt_quiz.intent_engine import _build_system_prompt
from backend.app.services.prompt_quiz.interpreter_llm import BRIEF_SCHEMA, interpret_with_gemini
from backend.app.services.retrieval.context_builder import ContextBuilder
from backend.app.services.tagging.auto_tagger import AutoTagger


def _ok_gemini_response(text="{\"status\": \"ok\"}"):
    resp = MagicMock()
    resp.status_code = 200
    resp.is_error = False
    resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": {"promptTokenCount": 150, "candidatesTokenCount": 30, "thoughtsTokenCount": 0},
    }
    return resp


# -----------------------------------------------------------------------------
# Group 1: Fix 1 - Confidence Gate (AutoTagger)
# -----------------------------------------------------------------------------

def test_force_ai_tagging_config_is_disabled():
    """Fix 1: FORCE_AI_TAGGING must default to False to enable rule-based confidence gating."""
    assert settings.FORCE_AI_TAGGING is False, "FORCE_AI_TAGGING should be False to save 30-60% AI calls"


def test_confidence_gate_thresholds():
    """Fix 1: Confidence threshold and ambiguity ratio must match calibrated defaults."""
    assert settings.TAGGING_CONFIDENCE_THRESHOLD == 4.0
    assert settings.TAGGING_AMBIGUITY_RATIO == 0.65


def test_is_confident_high_confidence_skips_ai():
    """Fix 1: Distinct top-scoring topic clears threshold and ratio, returning True."""
    at = AutoTagger()
    scores = {"Sports & Games": 12.0, "Current Affairs & People": 2.5}
    assert at._is_confident(scores) is True


def test_is_confident_ambiguous_escalates_to_ai():
    """Fix 1: Low score or close runner-up score returns False, escalating to Gemini."""
    at = AutoTagger()
    assert at._is_confident({}) is False
    assert at._is_confident({"Science": 2.0}) is False
    assert at._is_confident({"Science": 5.0, "History": 4.0}) is False  # ratio 4/5 = 0.8 > 0.65


# -----------------------------------------------------------------------------
# Group 2: Fix 2 - Curator Candidate Trimming
# -----------------------------------------------------------------------------

def test_curator_candidate_trimming_removes_deck_tags():
    """Fix 2: Deck names are irrelevant to narrative curation and must be stripped from candidate lines."""
    cands = [{
        "id": "q1",
        "question_text": "What is the capital of France?",
        "answer": "Paris",
        "topic": "Geography",
        "difficulty": "Easy",
        "difficulty_score": 0.25,
        "question_format": "STANDARD_QA",
        "document_title": "Mega World Deck 2024",
        "score": 0.95
    }]
    prompt = _build_curator_prompt("France quiz", 1, cands)
    assert "[Deck:" not in prompt
    assert "Mega World Deck" not in prompt


def test_curator_candidate_trimming_removes_difficulty_score():
    """Fix 2: Decimal difficulty score must be removed; the word label is sufficient."""
    cands = [{
        "id": "q1",
        "question_text": "What is the capital of France?",
        "answer": "Paris",
        "topic": "Geography",
        "difficulty": "Easy",
        "difficulty_score": 0.25,
        "question_format": "STANDARD_QA",
    }]
    prompt = _build_curator_prompt("France quiz", 1, cands)
    assert "[Easy]" in prompt
    assert "[Easy 0.25]" not in prompt
    assert "0.25" not in prompt


def test_curator_candidate_trimming_removes_relevance_score():
    """Fix 2: Retriever ranking relevance score [Rel X.XX] must be stripped."""
    cands = [{
        "id": "q1",
        "question_text": "What is the capital of France?",
        "answer": "Paris",
        "topic": "Geography",
        "difficulty": "Easy",
        "score": 0.95
    }]
    prompt = _build_curator_prompt("France quiz", 1, cands)
    assert "[Rel " not in prompt
    assert "0.95" not in prompt


def test_curator_candidate_trimming_format_tag_behavior():
    """Fix 2: STANDARD_QA tag must be omitted; non-standard formats like CONNECT must be preserved."""
    cands = [
        {
            "id": "q1",
            "question_text": "Standard question",
            "answer": "Ans 1",
            "topic": "General",
            "difficulty": "Medium",
            "question_format": "STANDARD_QA",
        },
        {
            "id": "q2",
            "question_text": "Connect clue",
            "answer": "Ans 2",
            "topic": "General",
            "difficulty": "Hard",
            "question_format": "CONNECT",
        }
    ]
    prompt = _build_curator_prompt("Mixed quiz", 2, cands)
    assert "[STANDARD_QA]" not in prompt
    assert "[CONNECT]" in prompt


# -----------------------------------------------------------------------------
# Group 3: Fix 3 - Interpreter LLM Output Cap
# -----------------------------------------------------------------------------

def test_interpreter_llm_has_max_output_tokens_cap():
    """Fix 3: Interpreter LLM must cap output tokens at 800 to prevent runaway output costs."""
    post_mock = MagicMock()
    post_mock.status_code = 200
    post_mock.is_error = False
    post_mock.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "{\"query\": \"cricket\", \"count\": 5}"}]}}]
    }
    with patch.object(settings, "GEMINI_API_KEY", "TEST_KEY"):
        with patch("backend.app.services.prompt_quiz._http.guarded_post", return_value=post_mock) as p_post:
            interpret_with_gemini("give me 5 cricket questions")
    assert p_post.called
    cfg = p_post.call_args.kwargs["json"]["generationConfig"]
    assert cfg.get("maxOutputTokens") == 800


def test_interpreter_llm_preserves_temperature_and_schema():
    """Fix 3: Interpreter generationConfig must preserve temperature=0.0 and response_schema."""
    post_mock = MagicMock()
    post_mock.status_code = 200
    post_mock.is_error = False
    post_mock.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "{\"query\": \"tech\", \"count\": 10}"}]}}]
    }
    with patch.object(settings, "GEMINI_API_KEY", "TEST_KEY"):
        with patch("backend.app.services.prompt_quiz._http.guarded_post", return_value=post_mock) as p_post:
            interpret_with_gemini("10 tech questions")
    cfg = p_post.call_args.kwargs["json"]["generationConfig"]
    assert cfg.get("temperature") == 0.0
    assert cfg.get("response_schema") == BRIEF_SCHEMA


# -----------------------------------------------------------------------------
# Group 4: Fix 4 - Job Queue Batched Tagger Documentation
# -----------------------------------------------------------------------------

def test_job_queue_has_batched_tagger_todo_comment():
    """Fix 4: step_tag in job_queue.py must have the documented TODO for tag_questions_batched."""
    with open("backend/app/services/jobs/job_queue.py", "r", encoding="utf-8") as f:
        src = f.read()
    assert "TODO: Migrate to tag_questions_batched for ~80% input token savings" in src


# -----------------------------------------------------------------------------
# Group 5: Fix 5 - generate_json Output Cap and Thinking Control
# -----------------------------------------------------------------------------

def test_generate_json_has_max_output_tokens_1024():
    """Fix 5: generate_json must cap output tokens at 1024 with temperature 0.0."""
    post_mock = AsyncMock(return_value=_ok_gemini_response())
    p = GeminiLLMProvider(api_key="TEST_KEY")
    with patch("httpx.AsyncClient.post", new=post_mock):
        asyncio.run(p.generate_json("classify this question"))
    cfg = post_mock.call_args.kwargs["json"]["generationConfig"]
    assert cfg.get("maxOutputTokens") == 1024
    assert cfg.get("temperature") == 0.0


def test_generate_json_thinking_config_on_gemini_3():
    """Fix 5: Gemini 3.x models must set thinking_config to MINIMAL."""
    post_mock = AsyncMock(return_value=_ok_gemini_response())
    p = GeminiLLMProvider(api_key="TEST_KEY")
    p.model = "gemini-3.5-flash-lite"
    with patch("httpx.AsyncClient.post", new=post_mock):
        asyncio.run(p.generate_json("classify this question"))
    cfg = post_mock.call_args.kwargs["json"]["generationConfig"]
    assert cfg.get("thinking_config") == {"thinking_level": "MINIMAL"}


def test_generate_json_thinking_config_on_gemini_2_5():
    """Fix 5: Non-3.x models (e.g. 2.5) must set thinking_budget to 0."""
    post_mock = AsyncMock(return_value=_ok_gemini_response())
    p = GeminiLLMProvider(api_key="TEST_KEY")
    p.model = "gemini-2.5-flash"
    with patch("httpx.AsyncClient.post", new=post_mock):
        asyncio.run(p.generate_json("classify this question"))
    cfg = post_mock.call_args.kwargs["json"]["generationConfig"]
    assert cfg.get("thinking_config") == {"thinking_budget": 0}


# -----------------------------------------------------------------------------
# Group 6: Fix 6 - Evidence Blocks Capping in Quiz Generator
# -----------------------------------------------------------------------------

def _make_mock_retrieved_item(i, explanation="Short explanation.", options=None):
    q = MagicMock()
    q.id = f"q-{i}"
    q.topic = "History"
    q.difficulty = "Medium"
    q.grade_min = 6
    q.grade_max = 8
    q.question_text = f"Sample question {i}?"
    q.answer = f"Answer {i}"
    q.options = options
    q.explanation = explanation

    doc = MagicMock()
    doc.id = f"doc-{i}"
    doc.title = f"Document {i}"

    slide = MagicMock()
    slide.id = f"slide-{i}"
    slide.slide_number = i

    return {
        "question": q,
        "document": doc,
        "slide": slide,
        "relevance_score": 0.9,
        "quote": "Sample slide quote",
    }


def test_evidence_blocks_capped_at_2x_question_count():
    """Fix 6: ContextBuilder must allow capping evidence items to 2x target count."""
    items = [_make_mock_retrieved_item(i) for i in range(1, 31)]
    ctx = ContextBuilder.build_prompt_context(items, max_items=10)
    # 10 items should produce 10 evidence blocks (SRC-1 to SRC-10)
    assert "[SRC-10]" in ctx["evidence_context"]
    assert "[SRC-11]" not in ctx["evidence_context"]
    assert len(ctx["source_mapping"]) == 10


def test_style_examples_truncate_long_explanations():
    """Fix 6: Style exemplar explanations > 100 characters must be truncated with '...'."""
    long_expl = "A" * 150
    items = [_make_mock_retrieved_item(1, explanation=long_expl)]
    ctx = ContextBuilder.build_prompt_context(items)
    expl = ctx["style_examples"][0]["explanation"]
    assert len(expl) <= 100
    assert expl.endswith("...")


def test_style_examples_preserve_short_explanations():
    """Fix 6: Short explanations must remain untruncated."""
    short_expl = "A brief explanation under 100 chars."
    items = [_make_mock_retrieved_item(1, explanation=short_expl)]
    ctx = ContextBuilder.build_prompt_context(items)
    assert ctx["style_examples"][0]["explanation"] == short_expl


def test_evidence_blocks_omit_options_na():
    """Fix 6: Options: N/A must not be rendered when question has empty/None options."""
    items = [_make_mock_retrieved_item(1, options=None)]
    ctx = ContextBuilder.build_prompt_context(items)
    assert "Options: N/A" not in ctx["evidence_context"]


# -----------------------------------------------------------------------------
# Group 7: Fix 7 & Fix 8 - Multimodal & Vision Cost Control Baselines
# -----------------------------------------------------------------------------

def test_vision_max_output_tokens_configured():
    """Fix 8: Vision prompt output limit is capped to prevent massive image descriptions."""
    assert gp.VISION_MAX_OUTPUT_TOKENS == 300


def test_transcribe_audio_executes_cleanly():
    """Fix 7: transcribe_audio generates multimodal transcript faithfully."""
    post_mock = AsyncMock(return_value=_ok_gemini_response("Spoken text"))
    p = GeminiLLMProvider(api_key="TEST_KEY")
    with patch("httpx.AsyncClient.post", new=post_mock):
        out = asyncio.run(p.transcribe_audio(b"audiobytes", "audio/mp3"))
    assert out == "Spoken text"


# -----------------------------------------------------------------------------
# Group 8: Fix 9 - Intent Engine Taxonomy Descriptions
# -----------------------------------------------------------------------------

def test_intent_engine_system_prompt_strips_l2_descriptions():
    """Fix 9: Parenthetical descriptions in L2 categories must be stripped."""
    tax = {
        "Finance": ["Banks and Banking (Commercial banking, loans)", "Central Banking (RBI, regulation)"]
    }
    prompt = _build_system_prompt(tax)
    assert "Banks and Banking" in prompt
    assert "Central Banking" in prompt
    assert "(Commercial banking, loans)" not in prompt
    assert "(RBI, regulation)" not in prompt


def test_intent_engine_system_prompt_format():
    """Fix 9: Taxonomy categories are rendered cleanly without clutter."""
    tax = {
        "Science": ["Physics", "Chemistry", "Biology"]
    }
    prompt = _build_system_prompt(tax)
    assert "- Science: Physics, Chemistry, Biology" in prompt
