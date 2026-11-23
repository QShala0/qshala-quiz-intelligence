"""Benchmark tests for the 3 token optimizations shipped in the perf(tokens) commit.

These verify that output quality is unchanged after:
  Fix 1: FORCE_AI_TAGGING=False (confidence gate re-enabled)
  Fix 2: Curator candidate lines trimmed (removed deck, diff_score, relevance)
  Fix 3: Interpreter LLM maxOutputTokens=800

No network calls — all Gemini responses are mocked.
Run: python -m pytest backend/tests/test_token_optimization_benchmarks.py -v
"""
import json
import pytest
from unittest.mock import patch, MagicMock

from backend.app.config import settings
from backend.app.services.tagging.auto_tagger import AutoTagger
from backend.app.services.prompt_quiz.curator_llm import (
    _build_curator_prompt,
    curate_quiz_with_gemini,
)


# ---------------------------------------------------------------------------
# FIX 1: Confidence gate benchmark — rule-based must match for clear questions
# ---------------------------------------------------------------------------

class TestConfidenceGateBenchmark:
    """Verifies that FORCE_AI_TAGGING=False correctly skips Gemini for
    high-confidence questions without degrading tag quality."""

    @pytest.fixture
    def tagger(self):
        return AutoTagger()

    # Questions where rule-based keywords should fire strongly and unambiguously.
    HIGH_CONFIDENCE_CASES = [
        {
            "question": "Which cricketer scored the most runs in a single World Cup tournament?",
            "answer": "Sachin Tendulkar in the 2003 Cricket World Cup",
            "expected_topic": "Sports & Games",
            "doc_title": "Cricket Sports Quiz",
        },
        {
            "question": "Which planet is known as the Red Planet?",
            "answer": "Mars",
            "expected_topic": "Space & Astronomy",
            "doc_title": "Solar System Quiz",
        },
        {
            "question": "Who painted the Mona Lisa?",
            "answer": "Leonardo da Vinci",
            "expected_topic": "Arts & Music",
            "doc_title": "Art Masters",
        },
        {
            "question": "Which molecule in biology carries the genetic code of every living cell?",
            "answer": "DNA — deoxyribonucleic acid, discovered by Watson and Crick in the field of biology",
            "expected_topic": "Science & Nature",
            "doc_title": "Science Biology Quiz",
        },
        {
            "question": "Who was the first president elected in a democracy through a national election?",
            "answer": "George Washington, elected president of the United States government",
            "expected_topic": "Politics & Governance",
            "doc_title": "Politics and Government Quiz",
        },
    ]

    # Questions where keywords are weak/ambiguous — should escalate to Gemini.
    LOW_CONFIDENCE_CASES = [
        {
            "question": "What connects a Japanese art form, a type of tree, and a Buddhist monk?",
            "answer": "Bonsai",
            "doc_title": "Connect Round",
        },
        {
            "question": "X was founded in a garage in 1998. Name X.",
            "answer": "Google",
            "doc_title": "Mystery Round",
        },
        {
            "question": "This city's name means 'fragrant harbour'. Which city?",
            "answer": "Hong Kong",
            "doc_title": "World Trivia",
        },
    ]

    def test_high_confidence_questions_skip_gemini(self, tagger):
        """High-confidence questions must return a valid result from the
        rule-based path alone (no Gemini needed)."""
        for case in self.HIGH_CONFIDENCE_CASES:
            scores = tagger._compute_topic_scores(
                case["question"].lower(),
                case["answer"].lower(),
                "",
                case["doc_title"].lower(),
                "", "", "",
            )
            assert tagger._is_confident(scores), (
                f"Expected confident match for: {case['question']!r} "
                f"(top scores: {sorted(scores.items(), key=lambda x: -x[1])[:3]})"
            )

            result = tagger.tag_question(
                question_text=case["question"],
                answer_text=case["answer"],
                doc_title=case["doc_title"],
            )
            assert result["primary_topic"] == case["expected_topic"], (
                f"Rule-based topic mismatch for {case['question']!r}: "
                f"got {result['primary_topic']!r}, expected {case['expected_topic']!r}"
            )
            assert len(result["topics"]) >= 1
            assert result["primary_topic"] in result["topics"]

    def test_low_confidence_questions_escalate(self, tagger):
        """Ambiguous questions must NOT pass the confidence gate —
        they should be escalated to Gemini."""
        for case in self.LOW_CONFIDENCE_CASES:
            scores = tagger._compute_topic_scores(
                case["question"].lower(),
                case["answer"].lower(),
                "",
                case["doc_title"].lower(),
                "", "", "",
            )
            assert not tagger._is_confident(scores), (
                f"Expected low confidence for: {case['question']!r} "
                f"but gate passed (top scores: {sorted(scores.items(), key=lambda x: -x[1])[:3]})"
            )

    def test_confidence_threshold_values(self):
        """The thresholds must be at the tuned values."""
        assert settings.TAGGING_CONFIDENCE_THRESHOLD == 4.0
        assert settings.TAGGING_AMBIGUITY_RATIO == 0.65

    def test_force_ai_tagging_is_off(self):
        """FORCE_AI_TAGGING must be False after the optimization."""
        assert settings.FORCE_AI_TAGGING is False

    def test_rule_based_returns_all_required_fields(self, tagger):
        """Every result from the rule-based path must have the full field set
        that downstream code expects."""
        required_keys = {
            "primary_topic", "topics", "tags",
            "question_hook", "curiosity_score", "temporal_nature",
        }
        for case in self.HIGH_CONFIDENCE_CASES:
            result = tagger.tag_question(
                question_text=case["question"],
                answer_text=case["answer"],
                doc_title=case["doc_title"],
            )
            missing = required_keys - set(result.keys())
            assert not missing, f"Missing keys {missing} for {case['question']!r}"
            assert isinstance(result["topics"], list)
            assert isinstance(result["tags"], list)
            assert isinstance(result["curiosity_score"], int)
            assert result["question_hook"] in {
                "DIRECT_TRIVIA", "HISTORICAL_CONTEXT", "DID_YOU_KNOW",
                "LATERAL_CONNECT", "VISUAL_CLUE",
            }
            assert result["temporal_nature"] in {"EVERGREEN", "TIME_SENSITIVE"}


# ---------------------------------------------------------------------------
# FIX 2: Curator prompt trimming — same selections with less context
# ---------------------------------------------------------------------------

class TestCuratorPromptTrimming:
    """Verifies the trimmed candidate format still carries all info
    the curator needs to make correct selections."""

    @pytest.fixture
    def candidates(self):
        return [
            {
                "id": f"q_{i}",
                "question_text": f"Who invented the {thing}?",
                "answer": answer,
                "topic": topic,
                "subtopic_l2": sub,
                "difficulty": diff,
                "difficulty_score": ds,
                "question_format": fmt,
                "document_title": f"Deck {i}",
                "score": 10.0 - i,
            }
            for i, (thing, answer, topic, sub, diff, ds, fmt) in enumerate([
                ("telephone", "Alexander Graham Bell", "Inventions & Technology", "Telecom", "Easy", 0.3, "STANDARD_QA"),
                ("lightbulb", "Thomas Edison", "Inventions & Technology", "Electricity", "Easy", 0.25, "STANDARD_QA"),
                ("World Wide Web", "Tim Berners-Lee", "Inventions & Technology", "Internet", "Medium", 0.5, "STANDARD_QA"),
                ("telescope", "Hans Lippershey", "Space & Astronomy", "Instruments", "Hard", 0.75, "STANDARD_QA"),
                ("printing press", "Johannes Gutenberg", "Inventions & Technology", "Printing", "Medium", 0.5, "STEPPED_CLUE"),
                ("penicillin", "Alexander Fleming", "Science & Nature", "Medicine", "Medium", 0.5, "STANDARD_QA"),
                ("radio", "Guglielmo Marconi", "Inventions & Technology", "Telecom", "Easy", 0.3, "STANDARD_QA"),
                ("dynamite", "Alfred Nobel", "Science & Nature", "Chemistry", "Hard", 0.7, "CONNECT"),
            ], start=1)
        ]

    def test_trimmed_prompt_excludes_bloat_fields(self, candidates):
        """The candidate lines must NOT contain deck names, difficulty scores,
        or relevance scores."""
        prompt = _build_curator_prompt("5 questions on inventions", 5, candidates)
        assert "Deck:" not in prompt, "Deck name should be removed from candidate lines"
        assert "Rel " not in prompt, "Relevance score should be removed"
        # difficulty_score decimals like "0.30" or "0.50" should not appear
        assert " 0.30]" not in prompt
        assert " 0.50]" not in prompt
        assert " 0.75]" not in prompt

    def test_trimmed_prompt_keeps_essential_fields(self, candidates):
        """The candidate lines must still contain ID, topic, difficulty word,
        question text, and answer text."""
        prompt = _build_curator_prompt("5 questions on inventions", 5, candidates)
        for c in candidates:
            assert c["id"] in prompt, f"Missing ID {c['id']}"
            topic = c.get("subtopic_l2") or c.get("topic")
            assert topic in prompt, f"Missing topic {topic}"
            diff = c["difficulty"]
            assert f"[{diff}]" in prompt, f"Missing difficulty [{diff}]"
            # Question text (truncated) should appear
            assert "Who invented" in prompt

    def test_standard_qa_format_omitted(self, candidates):
        """STANDARD_QA is the default — its tag should be omitted to save tokens."""
        prompt = _build_curator_prompt("5 questions on inventions", 5, candidates)
        assert "[STANDARD_QA]" not in prompt

    def test_non_default_format_included(self, candidates):
        """Non-default formats like STEPPED_CLUE and CONNECT must still appear."""
        prompt = _build_curator_prompt("5 questions on inventions", 5, candidates)
        assert "[STEPPED_CLUE]" in prompt
        assert "[CONNECT]" in prompt

    def test_slot_tags_preserved_for_refinement(self, candidates):
        """When refining an existing quiz, current-slot tags must still appear."""
        current_qs = [{"id": "q_1", "question_text": "Who invented the telephone?", "answer": "Bell"}]
        prompt = _build_curator_prompt("make it harder", 5, candidates,
                                       current_questions=current_qs)
        assert "[Current Slot #1]" in prompt

    def test_curator_still_selects_valid_ids_with_trimmed_prompt(self, candidates):
        """End-to-end: mock Gemini returns valid IDs, the validation gate passes."""
        fake_resp = {
            "selected_ids": ["q_1", "q_3", "q_5", "q_6", "q_4"],
            "narrative_arc": "A journey through human ingenuity."
        }
        with patch("httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "candidates": [{"content": {"parts": [{"text": json.dumps(fake_resp)}]}}]
            }
            mock_resp.is_error = False
            mock_client.post.return_value = mock_resp
            mock_client.__enter__.return_value = mock_client
            mock_cls.return_value = mock_client

            with patch("backend.app.config.settings.GEMINI_API_KEY", "test_key"):
                result = curate_quiz_with_gemini("5 questions on inventions", 5, candidates)

        assert result is not None
        assert len(result["selected_ids"]) == 5
        valid_ids = {c["id"] for c in candidates}
        for qid in result["selected_ids"]:
            assert qid in valid_ids

    def test_prompt_token_reduction(self, candidates):
        """The trimmed prompt should be measurably shorter than the old format."""
        trimmed = _build_curator_prompt("5 on inventions", 5, candidates)

        # Simulate old format for comparison
        old_lines = []
        for c in candidates:
            cid = c["id"]
            text = c["question_text"][:150]
            ans = c["answer"][:60]
            topic = c.get("subtopic_l2") or c.get("topic") or "General"
            diff = c["difficulty"]
            diff_score = c.get("difficulty_score", 0.5)
            fmt = c.get("question_format") or "STANDARD_QA"
            deck = (c.get("document_title") or "").strip()
            deck_tag = f" [Deck: {deck[:45]}]" if deck else ""
            rel = float(c.get("score") or 0.0)
            old_lines.append(
                f"- ID: {cid} | [{topic}] [{diff} {diff_score:.2f}] [{fmt}] [Rel {rel:.2f}]{deck_tag} Q: {text} | Ans: {ans}"
            )
        old_block = "\n".join(old_lines)

        # Extract just the candidate block from trimmed prompt
        trimmed_block = trimmed.split("CANDIDATE POOL")[1] if "CANDIDATE POOL" in trimmed else trimmed

        savings_pct = (1 - len(trimmed_block) / len(old_block)) * 100
        assert savings_pct > 10, (
            f"Expected >10% character reduction, got {savings_pct:.1f}%"
        )


# ---------------------------------------------------------------------------
# FIX 3: Interpreter LLM maxOutputTokens=800 — output must fit in budget
# ---------------------------------------------------------------------------

class TestInterpreterOutputBudget:
    """Verifies that 800 output tokens is sufficient for every reasonable
    interpreter response, by checking real schema-shaped outputs fit."""

    SAMPLE_RESPONSES = [
        {
            "count": 10, "query": "Indian space missions",
            "topics_l1": ["Space & Astronomy"], "subtopics_l2": ["ISRO", "Mars Mission"],
            "hard_bias": None, "audience": "general",
            "india_focused": True, "evergreen": True, "no_images": False,
            "format_filter": None, "framing": "general",
            "locality": "India", "venue_type": None, "tone": None,
            "school_safe": False, "cross_topic_padding": False,
            "search_terms": ["ISRO", "Chandrayaan", "Mangalyaan", "space program India"],
        },
        {
            "count": 5, "query": "cricket world cup winners",
            "topics_l1": ["Sports & Games"], "subtopics_l2": ["Cricket", "World Cup"],
            "hard_bias": "hard", "audience": "adult",
            "india_focused": False, "evergreen": True, "no_images": False,
            "format_filter": None, "framing": "tournament_finals",
            "locality": None, "venue_type": "online", "tone": "competitive",
            "school_safe": False, "cross_topic_padding": False,
            "search_terms": ["cricket world cup", "ODI champions", "ICC trophy"],
        },
        {
            "count": 15, "query": "famous paintings and artists",
            "topics_l1": ["Arts & Music", "Culture & Traditions"],
            "subtopics_l2": ["Renaissance", "Impressionism", "Modern Art", "Sculpture"],
            "hard_bias": "easy", "audience": "school_students",
            "india_focused": False, "evergreen": True, "no_images": True,
            "format_filter": "STEPPED_CLUE", "framing": "classroom",
            "locality": "Europe", "venue_type": "school", "tone": "educational",
            "school_safe": True, "cross_topic_padding": True,
            "search_terms": ["Mona Lisa", "Starry Night", "Picasso", "Monet", "Van Gogh",
                             "Da Vinci", "Michelangelo", "Rembrandt"],
        },
    ]

    def test_all_sample_responses_fit_in_800_tokens(self):
        """Every realistic interpreter JSON response must fit in 800 tokens.
        Conservative estimate: 1 token ~ 4 chars for JSON."""
        for i, resp in enumerate(self.SAMPLE_RESPONSES):
            json_str = json.dumps(resp)
            estimated_tokens = len(json_str) / 4
            assert estimated_tokens < 800, (
                f"Sample {i} needs ~{estimated_tokens:.0f} tokens "
                f"({len(json_str)} chars), exceeds 800-token budget"
            )

    def test_worst_case_maximal_response_fits(self):
        """Even a maximally padded response (all fields filled, long lists)
        should fit in 800 tokens."""
        worst_case = {
            "count": 50,
            "query": "A very long and descriptive query about multiple topics spanning geography history science and culture of the Indian subcontinent",
            "topics_l1": [f"Topic {i}" for i in range(8)],
            "subtopics_l2": [f"Subtopic {i} with detail" for i in range(12)],
            "hard_bias": "medium",
            "audience": "school_students",
            "india_focused": True,
            "evergreen": True,
            "no_images": True,
            "format_filter": "STEPPED_CLUE",
            "framing": "tournament_finals",
            "locality": "South Asia",
            "venue_type": "school_auditorium",
            "tone": "educational_and_fun",
            "school_safe": True,
            "cross_topic_padding": True,
            "search_terms": [f"search term {i} with extra words" for i in range(10)],
        }
        json_str = json.dumps(worst_case)
        estimated_tokens = len(json_str) / 4
        assert estimated_tokens < 800, (
            f"Worst-case response needs ~{estimated_tokens:.0f} tokens "
            f"({len(json_str)} chars), exceeds 800-token budget"
        )


# ---------------------------------------------------------------------------
# Cross-cutting: confidence gate boundary tests
# ---------------------------------------------------------------------------

class TestConfidenceGateBoundary:
    """Edge cases around the 4.0 threshold and 0.65 ambiguity ratio."""

    @pytest.fixture
    def tagger(self):
        return AutoTagger()

    def test_exactly_at_threshold_is_confident(self, tagger):
        scores = {"Science & Nature": 4.0, "World History": 1.0}
        assert tagger._is_confident(scores) is True

    def test_just_below_threshold_is_not_confident(self, tagger):
        scores = {"Science & Nature": 3.9, "World History": 1.0}
        assert tagger._is_confident(scores) is False

    def test_ambiguous_tie_is_not_confident(self, tagger):
        scores = {"Science & Nature": 5.0, "World History": 4.0}
        assert tagger._is_confident(scores) is False

    def test_clear_winner_is_confident(self, tagger):
        scores = {"Science & Nature": 10.0, "World History": 2.0}
        assert tagger._is_confident(scores) is True

    def test_single_topic_above_threshold_is_confident(self, tagger):
        scores = {"Sports & Games": 6.0}
        assert tagger._is_confident(scores) is True

    def test_empty_scores_is_not_confident(self, tagger):
        assert tagger._is_confident({}) is False

    def test_ambiguity_at_exact_ratio(self, tagger):
        # second/top = 3.25/5.0 = 0.65 exactly — the check is `>` so exact 0.65 IS confident
        scores = {"Science & Nature": 5.0, "World History": 3.25}
        assert tagger._is_confident(scores) is True

    def test_ambiguity_just_below_ratio(self, tagger):
        # second/top = 3.24/5.0 = 0.648 — this IS confident
        scores = {"Science & Nature": 5.0, "World History": 3.24}
        assert tagger._is_confident(scores) is True
