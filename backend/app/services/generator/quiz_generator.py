import json
import logging
import re
import random
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from backend.app.models.quiz import Quiz, GeneratedQuestion, RetrievalSource
from backend.app.models.question import Question
from backend.app.services.ai.factory import get_llm_provider
from backend.app.services.retrieval.hybrid_retriever import HybridRetriever
from backend.app.services.retrieval.context_builder import ContextBuilder
from backend.app.services.validation.validator import QuestionValidator
from backend.app.schemas.quiz import QuizGenerateRequest

logger = logging.getLogger(__name__)

PERSONALITY_PROMPTS = {
    "CURIOSITY_STORYTELLER": (
        "Host Persona: The Storyteller & Curiosity Coach.\n"
        "Style Guidelines: Weave an engaging narrative hook into the question and explanation. "
        "Use curiosity triggers ('Did you know?', 'In an astonishing turn of events...'), "
        "human interest details, and memorable trivia that sparks wonder and leaves students eager to learn more."
    ),
    "DETECTIVE_PUZZLER": (
        "Host Persona: The Detective & Puzzle Master.\n"
        "Style Guidelines: Frame the question as an intriguing mystery or deduction challenge. "
        "Provide progressive clues ('Clue 1: ...', 'Clue 2: ...') that reward analytical and lateral thinking "
        "rather than pure recall."
    ),
    "TOURNAMENT_PRO": (
        "Host Persona: Tournament Quizmaster (Classic Buzzer).\n"
        "Style Guidelines: Crisp, authoritative, high-energy contest questions with strictly unambiguous factual answers. "
        "Format questions with clear, direct phrasing suitable for high-stakes competition buzzer rounds."
    ),
    "SOCRATIC_EXPLORER": (
        "Host Persona: Socratic Discussion Host.\n"
        "Style Guidelines: Thought-provoking, inquiry-driven questions that connect facts to deeper concepts. "
        "Include a 'Host Discussion Prompt' in the explanation to stimulate lively classroom debate."
    )
}

class QuizGenerator:
    """
    Intelligent Quiz Compilation Engine for QShala.
    Supports Vault-First compilation (pulling from verified historical questions by difficulty tier)
    and AI-assisted novel generation (grounded strictly in knowledge base facts).
    """

    def __init__(self, db: Session):
        self.db = db
        self.llm = get_llm_provider()
        self.retriever = HybridRetriever(db)
        self.validator = QuestionValidator(db)

    @staticmethod
    def _sanitize_query(topic: str, subtopic: Optional[str] = None) -> str:
        parts = [topic.strip()] if topic else []
        if subtopic and subtopic.strip():
            parts.append(subtopic.strip())
        return " ".join(parts)

    async def generate_quiz(self, req: QuizGenerateRequest) -> Quiz:
        # Step 1: Clean Retrieval Query (do not contaminate vector search with prompt boilerplate)
        clean_query = f"{req.topic} {req.subtopic or ''}".strip()

        # Step 2: Quiz Format & Difficulty Resolution
        format_titles = {
            "TOURNAMENT_PRELIMS": "Tournament Prelims",
            "STAGE_FINALS": "Stage Finals",
            "CLASSROOM_WARMUP": "Classroom Warmup (QShots)",
            "THEMATIC_SPECIAL": "Thematic Deep-Dive",
            "GENERAL_CHAMPIONSHIP": "General Championship"
        }
        q_format = getattr(req, "quiz_format", None) or "TOURNAMENT_PRELIMS"
        format_label = format_titles.get(q_format, "Quiz")
        quiz_title = f"{req.topic} – {format_label}"

        grade_min = getattr(req, "grade_min", None)
        grade_max = getattr(req, "grade_max", None)
        aud_type = getattr(req, "audience_type", None) or "general"
        audience_label = "General Audience"

        # Resolve difficulty distribution
        dist = req.difficulty_distribution or {}
        easy_count = dist.get("easy", 0)
        medium_count = dist.get("medium", 0)
        hard_count = dist.get("hard", 0)

        if (easy_count + medium_count + hard_count) != req.question_count:
            diff_lower = (req.difficulty or "balanced").lower()
            if "easy-heavy" in diff_lower:
                easy_count = max(1, round(req.question_count * 0.6))
                medium_count = max(1, round(req.question_count * 0.3))
                hard_count = req.question_count - easy_count - medium_count
            elif "hard-heavy" in diff_lower:
                hard_count = max(1, round(req.question_count * 0.6))
                medium_count = max(1, round(req.question_count * 0.3))
                easy_count = req.question_count - hard_count - medium_count
            elif diff_lower == "easy":
                easy_count, medium_count, hard_count = req.question_count, 0, 0
            elif diff_lower == "hard":
                easy_count, medium_count, hard_count = 0, 0, req.question_count
            else:  # Balanced default
                if req.question_count == 10:
                    easy_count, medium_count, hard_count = 3, 5, 2
                elif req.question_count == 20:
                    easy_count, medium_count, hard_count = 5, 10, 5
                else:
                    easy_count = max(1, round(req.question_count * 0.25))
                    hard_count = max(1, round(req.question_count * 0.25))
                    medium_count = req.question_count - easy_count - hard_count
            dist = {"easy": easy_count, "medium": medium_count, "hard": hard_count}

        target_difficulties = ["Easy"] * easy_count + ["Medium"] * medium_count + ["Hard"] * hard_count
        while len(target_difficulties) < req.question_count:
            target_difficulties.append("Medium")
        # Step 2: Create Quiz record
        personality = req.personality or None
        quiz_obj = Quiz(
            title=quiz_title,
            topic=req.topic,
            subtopic=req.subtopic,
            quiz_format=q_format,
            audience_type=req.audience_type,
            grades=req.grades or [],
            age_range=req.age_range,
            grade_min=grade_min,
            grade_max=grade_max,
            difficulty=req.difficulty or "Balanced",
            difficulty_distribution=dist,
            question_count=req.question_count,
            question_types=req.question_types,
            generation_mode=req.generation_mode,
            tags=req.tags or [],
            personality=personality,
            style=req.style,
            raw_prompt=req.raw_prompt,
            status="READY"
        )
        self.db.add(quiz_obj)
        self.db.flush()

        # Step 3: Vault-First Retrieval or Hybrid Search with Tag Overlap
        clean_query = self._sanitize_query(req.topic, req.subtopic)
        if req.tags:
            clean_query = f"{clean_query} {' '.join(req.tags)}"

        retrieved_items = await self.retriever.search(
            query=clean_query,
            topic=req.topic,
            tags=req.tags,
            grade_min=None,
            grade_max=None,
            difficulty=req.difficulty,
            limit=max(req.question_count * 4, 30)
        )

        # Reserve spares and example slides never go into a generated quiz
        retrieved_items = [it for it in retrieved_items if (getattr(it["question"], "slot", None) or "LIVE") == "LIVE"]

        context_data = ContextBuilder.build_prompt_context(retrieved_items)
        evidence_context = context_data["evidence_context"]
        source_mapping = context_data["source_mapping"]
        style_examples = context_data["style_examples"]

        generated_raw_questions: List[Dict[str, Any]] = []

        # Check if user wants Vault Curation (HISTORICAL) or Vault is sufficient
        if req.generation_mode == "HISTORICAL":
            # Vault-First Compilation: compile questions tier by tier with tag prioritization
            by_diff: Dict[str, List[Dict[str, Any]]] = {"Easy": [], "Medium": [], "Hard": []}
            for item in retrieved_items:
                d = item["question"].difficulty or "Medium"
                by_diff.setdefault(d, []).append(item)

            # Within each tier, sort by tag_overlap descending
            for k in by_diff:
                by_diff[k].sort(key=lambda it: it.get("tag_overlap", 0), reverse=True)

            selected_items: List[Dict[str, Any]] = []
            selected_items.extend(by_diff["Easy"][:easy_count])
            selected_items.extend(by_diff["Medium"][:medium_count])
            selected_items.extend(by_diff["Hard"][:hard_count])

            # If shortfall, backfill from remaining pool
            if len(selected_items) < req.question_count:
                selected_ids = {it["question"].id for it in selected_items}
                for it in retrieved_items:
                    if it["question"].id not in selected_ids:
                        selected_items.append(it)
                        if len(selected_items) == req.question_count:
                            break

            if not selected_items:
                raise ValueError(
                    f"No questions found in the database for topic '{req.topic}'. "
                    f"Please select an indexed topic from the database or upload relevant decks."
                )

            for i, item in enumerate(selected_items[:req.question_count], start=1):
                hist_q = item["question"]
                assigned_diff = hist_q.difficulty if hist_q.difficulty in ["Easy", "Medium", "Hard"] else (target_difficulties[i - 1] if i - 1 < len(target_difficulties) else "Medium")
                hist_raw = list(getattr(hist_q, "raw_media_refs", []) or [])
                hist_videos = [r for r in hist_raw if isinstance(r, str) and r.lower().endswith(('.mp4', '.m4v', '.mov', '.webm'))]
                hist_audios = list(getattr(hist_q, "question_audio", []) or []) or [r for r in hist_raw if isinstance(r, str) and r.lower().endswith(('.mp3', '.wav', '.m4a', '.ogg'))]
                generated_raw_questions.append({
                    "question_text": hist_q.question_text,
                    "options": hist_q.options,
                    "answer": hist_q.answer,
                    "explanation": hist_q.explanation or "Historical QShala competition question.",
                    "difficulty": assigned_diff,
                    "grade_min": grade_min,
                    "grade_max": grade_max,
                    "topic": hist_q.topic,
                    "question_type": hist_q.question_type,
                    "format_payload": getattr(hist_q, "format_payload", None),
                    "image_refs": list(getattr(hist_q, "image_refs", []) or []),
                    "question_images": list(getattr(hist_q, "question_images", []) or []),
                    "answer_images": list(getattr(hist_q, "answer_images", []) or []),
                    "visual_clues": getattr(hist_q, "visual_clues", None),
                    "audio_refs": list(getattr(hist_q, "audio_refs", []) or []) or hist_audios,
                    "question_audio": hist_audios,
                    "answer_audio": list(getattr(hist_q, "answer_audio", []) or []),
                    "video_refs": hist_videos,
                    "question_video": hist_videos,
                    "raw_media_refs": hist_raw,
                    "provenance": {
                        "historical_question_id": hist_q.id,
                        "document_id": item["document"].id,
                        "slide_id": item["slide"].id if item["slide"] else None,
                        "document_title": item["document"].title,
                        "slide_number": item["slide"].slide_number if item["slide"] else None,
                        "relevance_score": item["relevance_score"],
                        "source_quote": item.get("quote")
                    }
                })

        else:
            # AI Generation Mode (NEW, REMIX, or SIMILAR)
            mode_instructions = {
                "NEW": "Generate FRESH, NOVEL questions grounded in the retrieved historical knowledge base facts. Do NOT copy historical questions word for word.",
                "REMIX": "Take the concepts from the historical questions but create substantially different questions with novel framing, reverse clues, or perspective shifts.",
                "SIMILAR": "Generate questions that closely mirror the structure, intellectual depth, and topic angle of the retrieved historical examples."
            }.get(req.generation_mode, "Generate fresh, engaging questions.")

            is_mcq = "MULTIPLE_CHOICE" in req.question_types
            format_rule = (
                "Format: 4 distinct Multiple-Choice Options ('A) ...', 'B) ...', 'C) ...', 'D) ...') with a clear answer."
                if is_mcq else
                "Format: QShala Question Slide + Next Slide Answer with Explanation (NO multiple choice options, options must be null)."
            )

            format_behavior_prompts = {
                "TOURNAMENT_PRELIMS": "Tournament Prelims: Crisp, authoritative questions with single-answer clarity for paper/screen scoring.",
                "STAGE_FINALS": "Stage Finals: Dramatic, multi-layered clues with escalating intellectual tension, suitable for live pounce/bounce rounds.",
                "CLASSROOM_WARMUP": "Classroom Warmup (QShots): Curiosity-sparking trivia loaded with 'Did You Know?' nuggets and discussion hooks.",
                "THEMATIC_SPECIAL": "Thematic Deep-Dive: Specialized questions exploring surprising facets and connections within the theme.",
                "GENERAL_CHAMPIONSHIP": "General Mixed Bag: Dynamic multi-genre questions with diverse trivia angles."
            }
            format_behavior = format_behavior_prompts.get(q_format, "High-standard QShala trivia format.")

            system_instruction = (
                "You are the Lead Quiz Master at QShala. Your mission is to craft captivating, curiosity-inducing "
                f"quiz questions designed for {format_label} ({format_behavior}).\n"
                f"{format_rule}\n"
                "Slide 1 (Question Slide): An engaging, curiosity-driven question or narrative clue.\n"
                "Slide 2 (Answer Slide): The clear, unambiguous answer, followed by a rich educational explanation and backstory.\n"
                "Ground all questions strictly in the provided evidence. Always reference the source tag (e.g. [SRC-1]).\n"
                "You must return a valid JSON object matching the requested schema."
            )

            personality_guide = f"- {PERSONALITY_PROMPTS[req.personality]}\n" if req.personality and req.personality in PERSONALITY_PROMPTS else ""
            tag_clause = f"- Target Concept Tags: {', '.join(req.tags)}\n" if req.tags else ""
            user_prompt = f"""
Requirements:
- Topic: {req.topic}
- Quiz Format: {format_label} ({format_behavior})
{tag_clause}{personality_guide}- Format: {format_rule}
- Difficulty Preset: {req.difficulty}
- Question Count: {req.question_count}
- Exact Difficulty Distribution:
  * Easy: {easy_count} questions
  * Medium: {medium_count} questions
  * Hard: {hard_count} questions
- Note on Difficulty Calibration:
  * Easy is direct recognition / foundational recall.
  * Medium is associative comparison / cause-and-effect.
  * Hard requires multi-step deduction or lateral synthesis.
- Generation Mode: {req.generation_mode} ({mode_instructions})

Available QShala Evidence from Historical Archives:
{evidence_context}

Few-shot Style Examples from QShala Archive:
{json.dumps(style_examples, indent=2)}

Please return a JSON object with this exact structure:
{{
  "title": "{quiz_title}",
  "questions": [
    {{
      "question_text": "...",
      "options": { '["A) ...", "B) ...", "C) ...", "D) ..."]' if is_mcq else 'null' },
      "answer": "...",
      "explanation": "...",
      "difficulty": "Easy",
      "grade_min": {grade_min if grade_min is not None else 'null'},
      "grade_max": {grade_max if grade_max is not None else 'null'},
      "topic": "{req.topic}",
      "question_type": "{'MULTIPLE_CHOICE' if is_mcq else 'SLIDE_QA'}",
      "source_tag": "SRC-1",
      "provenance_quote": "...",
      "provenance_rationale": "..."
    }}
  ]
}}
"""
            try:
                llm_response = await self.llm.generate_json(user_prompt, system_instruction=system_instruction)
                raw_list = llm_response.get("questions", [])

                for item in raw_list:
                    src_tag = item.get("source_tag", "SRC-1")
                    src_meta = source_mapping.get(src_tag) or (list(source_mapping.values())[0] if source_mapping else {})
                    item["provenance"] = {
                        "historical_question_id": src_meta.get("historical_question_id"),
                        "document_id": src_meta.get("document_id"),
                        "slide_id": src_meta.get("slide_id"),
                        "document_title": src_meta.get("document_title"),
                        "slide_number": src_meta.get("slide_number"),
                        "relevance_score": src_meta.get("relevance_score", 0.9),
                        "source_quote": item.get("provenance_quote") or (retrieved_items[0].get("quote") if retrieved_items else None),
                        "rationale": item.get("provenance_rationale") or "Derived from QShala archive."
                    }
                    generated_raw_questions.append(item)
            except Exception as e:
                logger.exception(f"Error calling LLM for quiz generation: {e}")
                from backend.app.services.ai.local_provider import LocalLLMProvider
                fallback_llm = LocalLLMProvider()
                mock_res = await fallback_llm.generate_json(user_prompt)
                for item in mock_res.get("questions", []):
                    src_meta = list(source_mapping.values())[0] if source_mapping else {}
                    item["provenance"] = {
                        "historical_question_id": src_meta.get("historical_question_id"),
                        "document_id": src_meta.get("document_id"),
                        "slide_id": src_meta.get("slide_id"),
                        "document_title": src_meta.get("document_title", "Historical QShala Archive"),
                        "slide_number": src_meta.get("slide_number", 1),
                        "relevance_score": 0.95,
                        "source_quote": item.get("provenance", {}).get("source_quote"),
                        "rationale": item.get("provenance", {}).get("rationale")
                    }
                    generated_raw_questions.append(item)

        # Step 4: Validate and Persist Questions
        for idx, q_data in enumerate(generated_raw_questions, start=1):
            assigned_diff = q_data.get("difficulty") or (target_difficulties[idx - 1] if idx - 1 < len(target_difficulties) else "Medium")
            q_data["difficulty"] = assigned_diff

            val_result = await self.validator.validate_question(
                question=q_data,
                target_grade_min=grade_min,
                target_grade_max=grade_max,
                target_difficulty=assigned_diff,
                evidence_context=evidence_context,
                audience_type=req.audience_type or "general"
            )

            gen_q = GeneratedQuestion(
                quiz_id=quiz_obj.id,
                order_index=idx,
                question_text=q_data["question_text"],
                options=q_data.get("options"),
                answer=q_data["answer"],
                explanation=q_data.get("explanation"),
                difficulty=assigned_diff,
                grade_min=grade_min,
                grade_max=grade_max,
                topic=q_data.get("topic", req.topic),
                question_type=q_data.get("question_type", "SLIDE_QA"),
                format_payload=q_data.get("format_payload"),
                image_refs=q_data.get("image_refs") or [],
                question_images=q_data.get("question_images") or [],
                answer_images=q_data.get("answer_images") or [],
                visual_clues=q_data.get("visual_clues"),
                audio_refs=q_data.get("audio_refs") or [],
                question_audio=q_data.get("question_audio") or [],
                answer_audio=q_data.get("answer_audio") or [],
                video_refs=q_data.get("video_refs") or [],
                question_video=q_data.get("question_video") or [],
                raw_media_refs=q_data.get("raw_media_refs") or [],
                validation_status=val_result["overall_status"],
                validation_details=val_result["details"],
                duplicate_score=val_result["duplicate_score"],
                is_approved=True
            )
            self.db.add(gen_q)
            self.db.flush()

            prov = q_data.get("provenance") or {}
            retrieval_src = RetrievalSource(
                generated_question_id=gen_q.id,
                historical_question_id=prov.get("historical_question_id"),
                document_id=prov.get("document_id"),
                slide_id=prov.get("slide_id"),
                document_title=prov.get("document_title"),
                slide_number=prov.get("slide_number"),
                relevance_score=prov.get("relevance_score", 1.0),
                source_quote=prov.get("source_quote"),
                rationale=prov.get("rationale")
            )
            self.db.add(retrieval_src)

        self.db.commit()
        self.db.refresh(quiz_obj)
        return quiz_obj

    async def execute_question_action(
        self,
        question_id: str,
        action: str,
        custom_instruction: Optional[str] = None
    ) -> GeneratedQuestion:
        """
        Executes granular actions:
        - 'regenerate': generate replacement question for same slot
        - 'make_easier': simplify vocabulary and clues
        - 'make_harder': increase deduction difficulty
        - 'generate_similar': generate sibling question on same topic
        """
        gen_q = self.db.query(GeneratedQuestion).filter(GeneratedQuestion.id == question_id).first()
        if not gen_q:
            raise ValueError(f"Question with ID {question_id} not found")

        quiz = gen_q.quiz
        new_diff = gen_q.difficulty
        if action == "make_easier":
            new_diff = "Easy"
        elif action == "make_harder":
            new_diff = "Hard"

        has_options = bool(gen_q.options and len(gen_q.options) >= 2)
        opt_instructions = '4 distinct choices: ["A) ...", "B) ...", "C) ...", "D) ..."]' if has_options else "null (no options for open slide QA format)"

        prompt = f"""
Current Question: {gen_q.question_text}
Current Answer: {gen_q.answer}
Current Options: {gen_q.options}
Action requested: {action.upper()}
Target Difficulty: {new_diff}
Target Grade: Grades {gen_q.grade_min}–{gen_q.grade_max}
Custom instructions: {custom_instruction or 'None'}

Please construct a revised or newly regenerated question keeping QShala's engaging quiz style.
Options format: {opt_instructions}

Return JSON:
{{
  "question_text": "...",
  "options": { '["A) ...", "B) ...", "C) ...", "D) ..."]' if has_options else 'null' },
  "answer": "...",
  "explanation": "...",
  "difficulty": "{new_diff}"
}}
"""
        res = await self.llm.generate_json(prompt)

        gen_q.question_text = res.get("question_text", gen_q.question_text)
        gen_q.options = res.get("options", gen_q.options)
        gen_q.answer = res.get("answer", gen_q.answer)
        gen_q.explanation = res.get("explanation", gen_q.explanation)
        gen_q.difficulty = new_diff

        # Revalidate
        val_result = await self.validator.validate_question(
            question={
                "question_text": gen_q.question_text,
                "answer": gen_q.answer,
                "options": gen_q.options,
                "difficulty": gen_q.difficulty,
                "provenance": {"source_quote": gen_q.explanation or "Regenerated from vault."}
            },
            target_grade_min=gen_q.grade_min or 3,
            target_grade_max=gen_q.grade_max or 5,
            target_difficulty=gen_q.difficulty,
            evidence_context=gen_q.explanation or ""
        )
        gen_q.validation_status = val_result["overall_status"]
        gen_q.validation_details = val_result["details"]
        gen_q.duplicate_score = val_result["duplicate_score"]

        self.db.commit()
        self.db.refresh(gen_q)
        return gen_q
