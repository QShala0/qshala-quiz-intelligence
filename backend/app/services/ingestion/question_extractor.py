import os
import re
import logging
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# Format-aware ingestion (units found from ANSWER slides + structure-based formats). FORMAT_AWARE_INGESTION=0 restores
# the older round-number driven extraction below, e.g. for a quick rollback.
def format_aware_enabled() -> bool:
    return os.getenv("FORMAT_AWARE_INGESTION", "1").strip().lower() not in {"0", "false", "no", "off"}

class QuestionExtractor:
    """
    Extracts structured question-answer pairs from classified slides.
    Supports:
    - Pattern A: Consecutive Slide Pair (Slide i = QUESTION, Slide i+1 = ANSWER)
    - Pattern C: Lookahead Triplet (Slide i = QUESTION, Slide i+1 = CLUE/CONTENT, Slide i+2 = ANSWER)
    - Pattern B: Self-contained Slide (Answer in speaker notes or body text)
    - Section/Round Tracking: Captures round number and round subtopic
    """

    MCQ_PATTERN = re.compile(
        r"(?:^|\n)\s*([A-D1-4][\.\)]\s*|\([A-D1-4]\)\s*)(.+?)(?=(?:\n\s*(?:[A-D1-4][\.\)]|\([A-D1-4]\))|$))",
        re.DOTALL | re.IGNORECASE
    )

    ANSWER_REGEX = re.compile(
        r"(?:^|\n)\s*(?:Correct\s+Answer|Solution|Answer|Ans)\s*[:\-]\s*(.+?)(?:\n|$)",
        re.IGNORECASE
    )

    ROUND_REGEX = re.compile(
        r"Round\s*(\d+)(?:\s*[:\-]?\s*(.*))?",
        re.IGNORECASE
    )

    KNOWN_THEMES = [
        "CREATIVITY", "LEADERSHIP", "SUSTAINABILITY", "ENTREPRENEURSHIP",
        "TECHNOLOGY", "PROBLEM -SOLVING", "PROBLEM-SOLVING", "PROBLEM SOLVING",
        "INNOVATION", "FINANCE", "BUSINESS", "SCIENCE", "NATURE", "CULTURE",
        "HISTORY", "GEOGRAPHY", "SPORTS", "ENTERTAINMENT", "LITERATURE",
        "GENERAL KNOWLEDGE"
    ]

    MIN_STRUCTURED_UNITS = 1

    def _extract_structured(self, slides: List[Dict[str, Any]], doc_year: Optional[int]) -> Optional[List[Dict[str, Any]]]:
        """Format-aware path: units come from the ANSWER slides, formats from slide structure. None when the deck has
        no ANSWER-marker units (older / differently built decks), which then use the legacy patterns below."""
        if not format_aware_enabled():
            return None
        try:
            from backend.app.services.ingestion.slide_roles import analyze_deck
            from backend.app.services.ingestion.format_extractors import extract_deck
            analysis = analyze_deck(slides)
            if len(analysis.units) < self.MIN_STRUCTURED_UNITS:
                return None
            questions = extract_deck(slides, analysis, self)
        except Exception as e:                       # never fail the deck because of the new path
            logger.warning("format-aware extraction failed, using the legacy extractor: %s", type(e).__name__)
            return None
        for q in questions:
            q["source_year"] = doc_year or 2026
        return questions

    def extract_from_slides(self, slides: List[Dict[str, Any]], doc_title: str, doc_year: Optional[int]) -> List[Dict[str, Any]]:
        structured = self._extract_structured(slides, doc_year)
        if structured is not None:
            return structured
        extracted_questions = []
        i = 0
        n = len(slides)

        current_round_num: Optional[int] = None
        current_subtopic: Optional[str] = None

        while i < n:
            current = slides[i]
            slide_type = current.get("slide_type", "CONTENT")
            title = current.get("title", "")
            text = current.get("extracted_text", "")
            notes = current.get("speaker_notes", "")

            # Skip non-question and preview slides
            if slide_type in ["TITLE", "RULES", "AV_TEST", "TEAM_INTRO", "SCOREBOARD", "ANNOUNCEMENT", "QUESTION_PREVIEW", "ANSWER_TRANSITION"]:
                i += 1
                continue

            # Check if audience question marker
            if "audience question" in text.lower() or "audience question" in title.lower():
                current_round_num = 99
                current_subtopic = "Audience Questions"
                i += 1
                continue

            # Check if this slide is a Section / Round Marker
            if slide_type == "SECTION_MARKER" or re.search(r"\bround\s*\d+", f"{title} {text}", re.IGNORECASE):
                round_match = self.ROUND_REGEX.search(f"{title} {text}")
                if round_match:
                    try:
                        current_round_num = int(round_match.group(1))
                    except Exception:
                        pass
                    sub = round_match.group(2)
                    if not sub or len(sub.strip()) <= 2:
                        pre = re.search(r"([A-Za-z\s–-]+?)\s*\n?\s*Round\s*\d+", f"{title} {text}", re.I)
                        if pre:
                            cand = pre.group(1).strip().lstrip("–-:	 ").strip()
                            if cand and cand.lower() not in ["structure of the quiz", "quiz", "structure"]:
                                sub = cand

                    if sub and len(sub.strip()) > 2:
                        clean_sub = sub.strip().lstrip("–-:	 ").strip()
                        clean_sub = re.split(r"\n|\d+\s*questions", clean_sub, flags=re.I)[0].strip()
                        clean_sub = re.sub(r"^(?:Round\s*\d+\s*[-–—:]*\s*)+", "", clean_sub, flags=re.I).strip("–—-: \t")
                        current_subtopic = clean_sub if clean_sub else f"Round {current_round_num}"
                i += 1
                continue

            if text.strip().lower() in ["buffer", "buffer question", "buffer questions"]:
                i += 1
                continue

            # -------------------------------------------------------------
            # TOURNAMENT FORMAT PATTERNS (ZVQ Finals & Multi-Round Decks)
            # -------------------------------------------------------------

            # 1. Stepped Clue (Portfolio Builder / 3 Clues decreasing points):
            # Bumper (i) -> Clue 1 (i+1) -> Clue 2 (i+2) -> Clue 3 (i+3) -> Answer Trans (i+4) -> Reveal (i+5)
            is_stepped_round = (current_round_num == 3 or "portfolio builder" in (current_subtopic or "").lower())
            if is_stepped_round and i + 5 < n and (
                slides[i+4].get("slide_type") == "ANSWER_TRANSITION" or
                re.match(r"^\s*(answer|answers|ans)\s*$", (slides[i+4].get("extracted_text") or "").strip(), re.I)
            ):
                c1_slide = slides[i+1]
                c2_slide = slides[i+2]
                c3_slide = slides[i+3]
                trans_slide = slides[i+4]
                ans_slide = slides[i+5]

                c1_txt = (c1_slide.get("extracted_text") or "").strip()
                c2_txt = (c2_slide.get("extracted_text") or "").strip()
                c3_txt = (c3_slide.get("extracted_text") or "").strip()
                ans_text, explanation = self._parse_answer_and_explanation(ans_slide.get("extracted_text", ""))

                if ans_text and not (ans_text.startswith("http://") or ans_text.startswith("https://")):
                    q_imgs = list(c1_slide.get("image_paths") or []) + list(c2_slide.get("image_paths") or []) + list(c3_slide.get("image_paths") or [])
                    ans_imgs = list(ans_slide.get("image_paths") or [])
                    all_imgs = list(dict.fromkeys(q_imgs + ans_imgs))

                    q_media = list(c1_slide.get("all_media_items") or []) + list(c2_slide.get("all_media_items") or []) + list(c3_slide.get("all_media_items") or []) + list(ans_slide.get("all_media_items") or [])
                    raw_media_refs = list(dict.fromkeys([m["rel_url"] for m in q_media if m.get("rel_url")]))

                    q_full = f"Clue 1 (+20/-15): {c1_txt}\n\nClue 2 (+15/-10): {c2_txt}\n\nClue 3 (+10/-5): {c3_txt}"

                    format_payload = {
                        "type": "STEPPED_CLUE",
                        "clues": [
                            {"step": 1, "points": "+20/-15", "text": c1_txt, "image": q_imgs[0] if len(q_imgs) > 0 else None},
                            {"step": 2, "points": "+15/-10", "text": c2_txt, "image": q_imgs[1] if len(q_imgs) > 1 else None},
                            {"step": 3, "points": "+10/-5", "text": c3_txt, "image": q_imgs[2] if len(q_imgs) > 2 else None},
                        ],
                        "answer": ans_text
                    }

                    extracted_questions.append({
                        "slide_id": c1_slide.get("id"),
                        "answer_slide_id": ans_slide.get("id"),
                        "question_text": q_full,
                        "answer": ans_text,
                        "options": [],
                        "explanation": explanation or ans_slide.get("extracted_text", ""),
                        "subtopic": current_subtopic or "Portfolio Builder",
                        "round_number": current_round_num or 3,
                        "question_type": "STEPPED_CLUE",
                        "format_payload": format_payload,
                        "source_year": doc_year or 2026,
                        "slide_number": c1_slide.get("slide_number"),
                        "answer_slide_number": ans_slide.get("slide_number"),
                        "source_slide_range": f"Slide {current.get('slide_number')}-{ans_slide.get('slide_number')}",
                        "image_refs": all_imgs,
                        "question_images": q_imgs,
                        "answer_images": ans_imgs,
                        "audio_refs": [],
                        "question_audio": [],
                        "answer_audio": [],
                        "video_refs": [],
                        "question_videos": [],
                        "answer_videos": [],
                        "media_items": q_media,
                        "raw_media_refs": raw_media_refs,
                        "speaker_notes": f"{notes}\n{ans_slide.get('speaker_notes', '')}".strip()
                    })
                    i += 6
                    continue

            # 2. Ticker Tangle / Arrange Order:
            # Bumper (i) -> Instruction/Prompt (i+1) -> Answer Trans (i+2) -> Reveal (i+3)
            is_arrange_round = (current_round_num == 2 or "ticker tangle" in (current_subtopic or "").lower())
            p_text_probe = (slides[i+1].get("extracted_text") or "") if i+1 < n else ""
            is_arrange_text = bool(re.search(r"lowest\s+to\s+highest|highest\s+to\s+lowest|earliest\s+to\s+latest|from\s+1st\s+to\s+last|chronological\s+order|ticker\s+tangle|arrange", p_text_probe, re.I))

            if (is_arrange_round or is_arrange_text) and i + 3 < n and (
                slides[i+2].get("slide_type") == "ANSWER_TRANSITION" or
                re.match(r"^\s*(answer|answers|ans)\s*$", (slides[i+2].get("extracted_text") or "").strip(), re.I)
            ):
                prompt_slide = slides[i+1]
                trans_slide = slides[i+2]
                ans_slide = slides[i+3]

                p_full = (prompt_slide.get("extracted_text") or "").strip()
                ans_text, explanation = self._parse_answer_and_explanation(ans_slide.get("extracted_text", ""))

                if ans_text and not (ans_text.startswith("http://") or ans_text.startswith("https://")):
                    q_imgs = list(prompt_slide.get("image_paths") or [])
                    ans_imgs = list(ans_slide.get("image_paths") or [])
                    all_imgs = list(dict.fromkeys(q_imgs + ans_imgs))

                    q_media = list(prompt_slide.get("all_media_items") or []) + list(ans_slide.get("all_media_items") or [])
                    # Never include countdown timer video in trivia video refs
                    raw_media_refs = list(dict.fromkeys([m["rel_url"] for m in q_media if m.get("rel_url") and "timer" not in m["rel_url"].lower()]))

                    format_payload = {
                        "type": "ARRANGE_ORDER",
                        "instruction": p_full,
                        "raw_answer": ans_text,
                        "has_timer": True,
                        "timer_seconds": 60,
                        "scoring": {"all_correct": "+20", "two_correct": "+10", "one_correct": "+5"}
                    }

                    extracted_questions.append({
                        "slide_id": prompt_slide.get("id"),
                        "answer_slide_id": ans_slide.get("id"),
                        "question_text": p_full,
                        "answer": ans_text,
                        "options": [],
                        "explanation": explanation or ans_slide.get("extracted_text", ""),
                        "subtopic": current_subtopic or "Ticker Tangle",
                        "round_number": current_round_num or 2,
                        "question_type": "ARRANGE_ORDER",
                        "format_payload": format_payload,
                        "source_year": doc_year or 2026,
                        "slide_number": prompt_slide.get("slide_number"),
                        "answer_slide_number": ans_slide.get("slide_number"),
                        "source_slide_range": f"Slide {current.get('slide_number')}-{ans_slide.get('slide_number')}",
                        "image_refs": all_imgs,
                        "question_images": q_imgs,
                        "answer_images": ans_imgs,
                        "audio_refs": [],
                        "question_audio": [],
                        "answer_audio": [],
                        "video_refs": [],
                        "question_videos": [],
                        "answer_videos": [],
                        "media_items": q_media,
                        "raw_media_refs": raw_media_refs,
                        "speaker_notes": f"{notes}\n{ans_slide.get('speaker_notes', '')}".strip()
                    })
                    i += 4
                    continue

            # 3. Market Indicators / Connect 4:
            # Bumper (i) -> Clues (i+1) -> Answer Trans (i+2) -> Reveal (i+3)
            is_connect_round = (current_round_num == 4 or "market indicators" in (current_subtopic or "").lower())
            if is_connect_round and i + 3 < n and (
                slides[i+2].get("slide_type") == "ANSWER_TRANSITION" or
                re.match(r"^\s*(answer|answers|ans)\s*$", (slides[i+2].get("extracted_text") or "").strip(), re.I)
            ):
                prompt_slide = slides[i+1]
                trans_slide = slides[i+2]
                ans_slide = slides[i+3]

                p_full = (prompt_slide.get("extracted_text") or "").strip()
                ans_text, explanation = self._parse_answer_and_explanation(ans_slide.get("extracted_text", ""))

                if ans_text and not (ans_text.startswith("http://") or ans_text.startswith("https://")):
                    q_imgs = list(prompt_slide.get("image_paths") or [])
                    ans_imgs = list(ans_slide.get("image_paths") or [])
                    all_imgs = list(dict.fromkeys(q_imgs + ans_imgs))

                    q_media = list(prompt_slide.get("all_media_items") or []) + list(ans_slide.get("all_media_items") or [])
                    raw_media_refs = list(dict.fromkeys([m["rel_url"] for m in q_media if m.get("rel_url")]))

                    q_text = p_full if len(p_full) > 25 else "What connects all these clues and images?"

                    format_payload = {
                        "type": "CONNECT_4",
                        "clues": q_imgs,
                        "connection_reveal": ans_text
                    }

                    extracted_questions.append({
                        "slide_id": prompt_slide.get("id"),
                        "answer_slide_id": ans_slide.get("id"),
                        "question_text": q_text,
                        "answer": ans_text,
                        "options": [],
                        "explanation": explanation or ans_slide.get("extracted_text", ""),
                        "subtopic": current_subtopic or "Market Indicators",
                        "round_number": current_round_num or 4,
                        "question_type": "CONNECT_4",
                        "format_payload": format_payload,
                        "source_year": doc_year or 2026,
                        "slide_number": prompt_slide.get("slide_number"),
                        "answer_slide_number": ans_slide.get("slide_number"),
                        "source_slide_range": f"Slide {current.get('slide_number')}-{ans_slide.get('slide_number')}",
                        "image_refs": all_imgs,
                        "question_images": q_imgs,
                        "answer_images": ans_imgs,
                        "audio_refs": [],
                        "question_audio": [],
                        "answer_audio": [],
                        "video_refs": [],
                        "question_videos": [],
                        "answer_videos": [],
                        "media_items": q_media,
                        "raw_media_refs": raw_media_refs,
                        "speaker_notes": f"{notes}\n{ans_slide.get('speaker_notes', '')}".strip()
                    })
                    i += 4
                    continue

            # 4. Bumper -> Question Prompt -> Answer Trans -> Reveal (General / Round 1 & Round 5)
            # When current slide is a short Bumper (e.g. "Question 1"), prompt is on i+1
            is_bumper = bool(re.match(r"^\s*Question\s*\d*\s*$", text.strip(), re.I)) or (len(text.strip()) <= 15 and "question" in text.lower())
            if is_bumper and i + 3 < n and (
                slides[i+2].get("slide_type") == "ANSWER_TRANSITION" or
                re.match(r"^\s*(answer|answers|ans)\s*$", (slides[i+2].get("extracted_text") or "").strip(), re.I)
            ):
                prompt_slide = slides[i+1]
                trans_slide = slides[i+2]
                ans_slide = slides[i+3]

                q_text, options, det_theme, det_num = self._parse_question_and_options(prompt_slide.get("extracted_text", ""))
                ans_text, explanation = self._parse_answer_and_explanation(ans_slide.get("extracted_text", ""))

                if ans_text and not (ans_text.startswith("http://") or ans_text.startswith("https://")):
                    q_imgs = list(prompt_slide.get("image_paths") or [])
                    ans_imgs = list(ans_slide.get("image_paths") or [])
                    all_imgs = list(dict.fromkeys(q_imgs + ans_imgs))

                    q_audio = list(prompt_slide.get("audio_paths") or [a["rel_url"] for a in prompt_slide.get("audio_items", []) if a.get("rel_url")])
                    ans_audio = list(ans_slide.get("audio_paths") or [a["rel_url"] for a in ans_slide.get("audio_items", []) if a.get("rel_url")])
                    all_audio = list(dict.fromkeys(q_audio + ans_audio))

                    q_video = [v["rel_url"] for v in prompt_slide.get("video_items", []) if v.get("rel_url") and "timer" not in v["rel_url"].lower()]
                    ans_video = [v["rel_url"] for v in ans_slide.get("video_items", []) if v.get("rel_url")]
                    all_video = list(dict.fromkeys(q_video + ans_video))

                    q_media = list(prompt_slide.get("all_media_items") or []) + list(ans_slide.get("all_media_items") or [])
                    raw_media_refs = list(dict.fromkeys([m["rel_url"] for m in q_media if m.get("rel_url")]))

                    q_type = "BUZZER" if current_round_num == 5 else ("MULTIPLE_CHOICE" if options else "SLIDE_QA")
                    fmt_payload = {"type": "BUZZER"} if current_round_num == 5 else {"type": "QA"}

                    extracted_questions.append({
                        "slide_id": prompt_slide.get("id"),
                        "answer_slide_id": ans_slide.get("id"),
                        "question_text": q_text or prompt_slide.get("extracted_text", ""),
                        "answer": ans_text,
                        "options": options,
                        "explanation": explanation or ans_slide.get("extracted_text", ""),
                        "subtopic": current_subtopic or det_theme,
                        "round_number": current_round_num or det_num,
                        "question_type": q_type,
                        "format_payload": fmt_payload,
                        "source_year": doc_year or 2026,
                        "slide_number": prompt_slide.get("slide_number"),
                        "answer_slide_number": ans_slide.get("slide_number"),
                        "source_slide_range": f"Slide {current.get('slide_number')}-{ans_slide.get('slide_number')}",
                        "image_refs": all_imgs,
                        "question_images": q_imgs,
                        "answer_images": ans_imgs,
                        "audio_refs": all_audio,
                        "question_audio": q_audio,
                        "answer_audio": ans_audio,
                        "video_refs": all_video,
                        "question_videos": q_video,
                        "answer_videos": ans_video,
                        "media_items": q_media,
                        "raw_media_refs": raw_media_refs,
                        "speaker_notes": f"{notes}\n{ans_slide.get('speaker_notes', '')}".strip()
                    })
                    i += 4
                    continue

            # Pattern A2: Question -> ANSWER_TRANSITION (e.g. "ANSWER") -> Answer Reveal Slide
            if slide_type == "QUESTION" and i + 2 < n and slides[i+1].get("slide_type") == "ANSWER_TRANSITION":
                trans_slide = slides[i+1]
                ans_slide = slides[i+2]
                q_text, options, det_theme, det_num = self._parse_question_and_options(text)
                ans_text, explanation = self._parse_answer_and_explanation(ans_slide.get("extracted_text", ""))

                if not ans_text and ans_slide.get("title") and ans_slide.get("title") != "NO_TITLE":
                    ans_text = ans_slide.get("title")

                if not ans_text and ans_slide.get("speaker_notes"):
                    ans_text, exp_n = self._parse_answer_and_explanation(ans_slide.get("speaker_notes", ""))
                    if exp_n:
                        explanation = f"{explanation}\n{exp_n}".strip()

                if ans_text and not (ans_text.startswith("http://") or ans_text.startswith("https://")):
                    # Check if next slide (i+3) is an explanation or context slide
                    exp_slide = None
                    advance_steps = 3
                    if i + 3 < n:
                        s3 = slides[i+3]
                        s3_text = (s3.get("extracted_text") or "").strip()
                        s3_title = (s3.get("title") or "").strip().lower()
                        if any(w in s3_title or w in s3_text.lower() for w in ["explanation", "trivia", "did you know", "context"]):
                            exp_slide = s3
                            explanation = f"{explanation}\n{s3_text}".strip()
                            advance_steps = 4

                    q_imgs = list(current.get("image_paths") or [])
                    ans_imgs = list(ans_slide.get("image_paths") or [])
                    if exp_slide:
                        ans_imgs.extend(exp_slide.get("image_paths") or [])
                    all_imgs = list(dict.fromkeys(q_imgs + ans_imgs))

                    q_audio = list(current.get("audio_paths") or [a["rel_url"] for a in current.get("audio_items", []) if a.get("rel_url")])
                    ans_audio = list(ans_slide.get("audio_paths") or [a["rel_url"] for a in ans_slide.get("audio_items", []) if a.get("rel_url")])
                    all_audio = list(dict.fromkeys(q_audio + ans_audio))

                    q_video = list(current.get("video_paths") or [v["rel_url"] for v in current.get("video_items", []) if v.get("rel_url")])
                    ans_video = list(ans_slide.get("video_paths") or [v["rel_url"] for v in ans_slide.get("video_items", []) if v.get("rel_url")])
                    all_video = list(dict.fromkeys(q_video + ans_video))

                    q_media = list(current.get("all_media_items") or []) + list(trans_slide.get("all_media_items") or []) + list(ans_slide.get("all_media_items") or [])
                    if exp_slide:
                        q_media.extend(exp_slide.get("all_media_items") or [])

                    raw_media_refs = list(dict.fromkeys([m["rel_url"] for m in q_media if m.get("rel_url")]))

                    extracted_questions.append({
                        "slide_id": current.get("id"),
                        "answer_slide_id": ans_slide.get("id"),
                        "question_text": q_text,
                        "answer": ans_text,
                        "options": options,
                        "explanation": explanation or ans_slide.get("extracted_text", ""),
                        "subtopic": current_subtopic or det_theme,
                        "round_number": current_round_num or det_num,
                        "question_type": "MULTIPLE_CHOICE" if options else "SLIDE_QA",
                        "source_year": doc_year or 2024,
                        "slide_number": current.get("slide_number"),
                        "answer_slide_number": ans_slide.get("slide_number"),
                        "source_slide_range": f"Slide {current.get('slide_number')}-{ans_slide.get('slide_number')}",
                        "image_refs": all_imgs,
                        "question_images": q_imgs,
                        "answer_images": ans_imgs,
                        "audio_refs": all_audio,
                        "question_audio": q_audio,
                        "answer_audio": ans_audio,
                        "video_refs": all_video,
                        "question_videos": q_video,
                        "answer_videos": ans_video,
                        "media_items": q_media,
                        "raw_media_refs": raw_media_refs,
                        "speaker_notes": f"{notes}\n{ans_slide.get('speaker_notes', '')}".strip()
                    })
                    i += advance_steps
                    continue

            # Pattern C2: Question -> Clue Slide -> ANSWER_TRANSITION -> Answer Reveal Slide
            if slide_type == "QUESTION" and i + 3 < n and slides[i+2].get("slide_type") == "ANSWER_TRANSITION":
                clue_slide = slides[i+1]
                trans_slide = slides[i+2]
                ans_slide = slides[i+3]
                q_text, options, det_theme, det_num = self._parse_question_and_options(text)
                ans_text, explanation = self._parse_answer_and_explanation(ans_slide.get("extracted_text", ""))

                if not ans_text and ans_slide.get("title") and ans_slide.get("title") != "NO_TITLE":
                    ans_text = ans_slide.get("title")

                if ans_text and not (ans_text.startswith("http://") or ans_text.startswith("https://")):
                    clue_text = clue_slide.get("extracted_text", "")
                    combined_exp = f"{explanation}\nClue: {clue_text}".strip() if clue_text else explanation
                    q_imgs = list(current.get("image_paths") or []) + list(clue_slide.get("image_paths") or [])
                    ans_imgs = list(ans_slide.get("image_paths") or [])
                    all_imgs = list(dict.fromkeys(q_imgs + ans_imgs))

                    q_audio = list(current.get("audio_paths") or [a["rel_url"] for a in current.get("audio_items", []) if a.get("rel_url")]) + list(clue_slide.get("audio_paths") or [a["rel_url"] for a in clue_slide.get("audio_items", []) if a.get("rel_url")])
                    ans_audio = list(ans_slide.get("audio_paths") or [a["rel_url"] for a in ans_slide.get("audio_items", []) if a.get("rel_url")])
                    all_audio = list(dict.fromkeys(q_audio + ans_audio))

                    q_video = list(current.get("video_paths") or [v["rel_url"] for v in current.get("video_items", []) if v.get("rel_url")]) + list(clue_slide.get("video_paths") or [v["rel_url"] for v in clue_slide.get("video_items", []) if v.get("rel_url")])
                    ans_video = list(ans_slide.get("video_paths") or [v["rel_url"] for v in ans_slide.get("video_items", []) if v.get("rel_url")])
                    all_video = list(dict.fromkeys(q_video + ans_video))

                    q_media = list(current.get("all_media_items") or []) + list(clue_slide.get("all_media_items") or []) + list(trans_slide.get("all_media_items") or []) + list(ans_slide.get("all_media_items") or [])
                    raw_media_refs = list(dict.fromkeys([m["rel_url"] for m in q_media if m.get("rel_url")]))

                    extracted_questions.append({
                        "slide_id": current.get("id"),
                        "answer_slide_id": ans_slide.get("id"),
                        "question_text": q_text,
                        "answer": ans_text,
                        "options": options,
                        "explanation": combined_exp,
                        "subtopic": current_subtopic or det_theme,
                        "round_number": current_round_num or det_num,
                        "question_type": "MULTIPLE_CHOICE" if options else "SLIDE_QA",
                        "source_year": doc_year or 2024,
                        "slide_number": current.get("slide_number"),
                        "answer_slide_number": ans_slide.get("slide_number"),
                        "source_slide_range": f"Slide {current.get('slide_number')}-{ans_slide.get('slide_number')}",
                        "image_refs": all_imgs,
                        "question_images": q_imgs,
                        "answer_images": ans_imgs,
                        "audio_refs": all_audio,
                        "question_audio": q_audio,
                        "answer_audio": ans_audio,
                        "video_refs": all_video,
                        "question_videos": q_video,
                        "answer_videos": ans_video,
                        "media_items": q_media,
                        "raw_media_refs": raw_media_refs,
                        "speaker_notes": f"{notes}\n{ans_slide.get('speaker_notes', '')}".strip()
                    })
                    i += 4
                    continue

            # Pattern A: Question on Slide i, Answer on Slide i + 1
            if slide_type == "QUESTION" and i + 1 < n and slides[i+1].get("slide_type") == "ANSWER":
                ans_slide = slides[i+1]
                q_text, options, det_theme, det_num = self._parse_question_and_options(text)
                ans_text, explanation = self._parse_answer_and_explanation(ans_slide.get("extracted_text", ""))

                # If answer text is still empty, check speaker notes of the answer slide
                if not ans_text and ans_slide.get("speaker_notes"):
                    ans_text, explanation = self._parse_answer_and_explanation(ans_slide.get("speaker_notes", ""))

                if ans_text and not (ans_text.startswith("http://") or ans_text.startswith("https://")):
                    q_imgs = list(current.get("image_paths") or [])
                    ans_imgs = list(ans_slide.get("image_paths") or [])
                    all_imgs = list(dict.fromkeys(q_imgs + ans_imgs))

                    q_audio = list(current.get("audio_paths") or [a["rel_url"] for a in current.get("audio_items", []) if a.get("rel_url")])
                    ans_audio = list(ans_slide.get("audio_paths") or [a["rel_url"] for a in ans_slide.get("audio_items", []) if a.get("rel_url")])
                    all_audio = list(dict.fromkeys(q_audio + ans_audio))

                    q_video = list(current.get("video_paths") or [v["rel_url"] for v in current.get("video_items", []) if v.get("rel_url")])
                    ans_video = list(ans_slide.get("video_paths") or [v["rel_url"] for v in ans_slide.get("video_items", []) if v.get("rel_url")])
                    all_video = list(dict.fromkeys(q_video + ans_video))

                    q_media = list(current.get("all_media_items") or []) + list(ans_slide.get("all_media_items") or [])
                    raw_media_refs = list(dict.fromkeys([m["rel_url"] for m in q_media if m.get("rel_url")]))

                    extracted_questions.append({
                        "slide_id": current.get("id"),
                        "answer_slide_id": ans_slide.get("id"),
                        "question_text": q_text,
                        "answer": ans_text,
                        "options": options,
                        "explanation": explanation or ans_slide.get("extracted_text", ""),
                        "subtopic": current_subtopic or det_theme,
                        "round_number": current_round_num or det_num,
                        "question_type": "MULTIPLE_CHOICE" if options else "SLIDE_QA",
                        "source_year": doc_year or 2024,
                        "slide_number": current.get("slide_number"),
                        "answer_slide_number": ans_slide.get("slide_number"),
                        "source_slide_range": f"Slide {current.get('slide_number')}-{ans_slide.get('slide_number')}",
                        "image_refs": all_imgs,
                        "question_images": q_imgs,
                        "answer_images": ans_imgs,
                        "audio_refs": all_audio,
                        "question_audio": q_audio,
                        "answer_audio": ans_audio,
                        "video_refs": all_video,
                        "question_videos": q_video,
                        "answer_videos": ans_video,
                        "media_items": q_media,
                        "raw_media_refs": raw_media_refs,
                        "speaker_notes": f"{notes}\n{ans_slide.get('speaker_notes', '')}".strip()
                    })
                    i += 2
                    continue

            # Pattern C: Question on Slide i, Clue/Image on Slide i + 1, Answer on Slide i + 2
            if slide_type == "QUESTION" and i + 2 < n and slides[i+2].get("slide_type") == "ANSWER":
                middle_slide = slides[i+1]
                ans_slide = slides[i+2]
                q_text, options, det_theme, det_num = self._parse_question_and_options(text)
                ans_text, explanation = self._parse_answer_and_explanation(ans_slide.get("extracted_text", ""))

                if ans_text and not (ans_text.startswith("http://") or ans_text.startswith("https://")):
                    mid_text = middle_slide.get("extracted_text", "").strip()
                    if mid_text.lower() in ["answer", "answers", "the answer", "solution", "solutions"]:
                        mid_text = ""
                    combined_exp = f"{explanation}\nClue: {mid_text}".strip() if mid_text else explanation
                    q_imgs = list(current.get("image_paths") or []) + list(middle_slide.get("image_paths") or [])
                    ans_imgs = list(ans_slide.get("image_paths") or [])
                    all_imgs = list(dict.fromkeys(q_imgs + ans_imgs))

                    q_audio = list(current.get("audio_paths") or [a["rel_url"] for a in current.get("audio_items", []) if a.get("rel_url")]) + list(middle_slide.get("audio_paths") or [a["rel_url"] for a in middle_slide.get("audio_items", []) if a.get("rel_url")])
                    ans_audio = list(ans_slide.get("audio_paths") or [a["rel_url"] for a in ans_slide.get("audio_items", []) if a.get("rel_url")])
                    all_audio = list(dict.fromkeys(q_audio + ans_audio))

                    q_video = list(current.get("video_paths") or [v["rel_url"] for v in current.get("video_items", []) if v.get("rel_url")]) + list(middle_slide.get("video_paths") or [v["rel_url"] for v in middle_slide.get("video_items", []) if v.get("rel_url")])
                    ans_video = list(ans_slide.get("video_paths") or [v["rel_url"] for v in ans_slide.get("video_items", []) if v.get("rel_url")])
                    all_video = list(dict.fromkeys(q_video + ans_video))

                    q_media = list(current.get("all_media_items") or []) + list(middle_slide.get("all_media_items") or []) + list(ans_slide.get("all_media_items") or [])
                    raw_media_refs = list(dict.fromkeys([m["rel_url"] for m in q_media if m.get("rel_url")]))

                    extracted_questions.append({
                        "slide_id": current.get("id"),
                        "answer_slide_id": ans_slide.get("id"),
                        "question_text": q_text,
                        "answer": ans_text,
                        "options": options,
                        "explanation": combined_exp,
                        "subtopic": current_subtopic or det_theme,
                        "round_number": current_round_num or det_num,
                        "question_type": "MULTIPLE_CHOICE" if options else "SLIDE_QA",
                        "source_year": doc_year or 2024,
                        "slide_number": current.get("slide_number"),
                        "answer_slide_number": ans_slide.get("slide_number"),
                        "source_slide_range": f"Slide {current.get('slide_number')}-{ans_slide.get('slide_number')}",
                        "image_refs": all_imgs,
                        "question_images": q_imgs,
                        "answer_images": ans_imgs,
                        "audio_refs": all_audio,
                        "question_audio": q_audio,
                        "answer_audio": ans_audio,
                        "video_refs": all_video,
                        "question_videos": q_video,
                        "answer_videos": ans_video,
                        "media_items": q_media,
                        "raw_media_refs": raw_media_refs,
                        "speaker_notes": f"{notes}\n{ans_slide.get('speaker_notes', '')}".strip()
                    })
                    i += 3
                    continue

            # Pattern B: Question and Answer on same slide (notes or trailing text)
            if slide_type == "QUESTION":
                q_text, options, det_theme, det_num = self._parse_question_and_options(text)
                ans_text = ""
                explanation = ""

                # Look in notes first (ensure notes is not just a URL)
                if notes and not (notes.strip().startswith("http://") or notes.strip().startswith("https://")):
                    ans_text, explanation = self._parse_answer_and_explanation(notes)

                # Look in body text only if explicit answer prefix exists (e.g. "Answer:" or "Ans:")
                if not ans_text and re.search(r"(?:^|\n)\s*(?:Correct\s+Answer|Solution|Answer|Ans)\s*[:\-]", text, re.IGNORECASE):
                    ans_text, explanation = self._parse_answer_and_explanation(text)

                # Only include if a real answer was resolved and not an HTTP link
                if ans_text and ans_text.lower() not in ["see explanation", "answer indicated on slide", ""] and not (ans_text.startswith("http://") or ans_text.startswith("https://")):
                    q_imgs = list(current.get("image_paths") or [])
                    ans_imgs = []
                    all_imgs = q_imgs
                    q_audio = list(current.get("audio_paths") or [a["rel_url"] for a in current.get("audio_items", []) if a.get("rel_url")])
                    ans_audio = []
                    all_audio = q_audio
                    q_video = list(current.get("video_paths") or [v["rel_url"] for v in current.get("video_items", []) if v.get("rel_url")])
                    ans_video = []
                    all_video = q_video
                    q_media = list(current.get("all_media_items") or [])
                    raw_media_refs = list(dict.fromkeys([m["rel_url"] for m in q_media if m.get("rel_url")]))

                    extracted_questions.append({
                        "slide_id": current.get("id"),
                        "answer_slide_id": None,
                        "question_text": q_text,
                        "answer": ans_text,
                        "options": options,
                        "explanation": explanation,
                        "subtopic": current_subtopic or det_theme,
                        "round_number": current_round_num or det_num,
                        "question_type": "MULTIPLE_CHOICE" if options else "SLIDE_QA",
                        "source_year": doc_year or 2024,
                        "slide_number": current.get("slide_number"),
                        "answer_slide_number": None,
                        "source_slide_range": f"Slide {current.get('slide_number')}",
                        "image_refs": all_imgs,
                        "question_images": q_imgs,
                        "answer_images": ans_imgs,
                        "audio_refs": all_audio,
                        "question_audio": q_audio,
                        "answer_audio": ans_audio,
                        "video_refs": all_video,
                        "question_videos": q_video,
                        "answer_videos": ans_video,
                        "media_items": q_media,
                        "raw_media_refs": raw_media_refs,
                        "speaker_notes": notes
                    })
                i += 1
                continue

            i += 1

        # Intra-deck deduplication and consolidation:
        # Handles tournament prelim decks where questions are shown in a "Question Run"
        # and then repeated in the "Answer Reveal Run".
        consolidated = []
        seen_q: Dict[str, Dict[str, Any]] = {}

        for q in extracted_questions:
            # Normalize first 10 words for fuzzy matching
            words = re.sub(r"[^\w\s]", "", q["question_text"].lower()).split()
            key = " ".join(words[:10]) if words else q["question_text"]

            if key in seen_q:
                existing = seen_q[key]
                curr_ans = (q.get("answer") or "").strip()
                exist_ans = (existing.get("answer") or "").strip()
                # Prioritize instance with verified answer from an answer slide
                curr_is_better = (
                    (bool(curr_ans) and not bool(exist_ans)) or
                    (bool(q.get("answer_slide_number")) and not bool(existing.get("answer_slide_number"))) or
                    (len(curr_ans) > len(exist_ans))
                )
                if curr_is_better:
                    existing["answer"] = q["answer"]
                    existing["explanation"] = q["explanation"] or existing["explanation"]
                    existing["answer_slide_number"] = q.get("answer_slide_number")
                    existing["source_slide_range"] = q.get("source_slide_range")

                if not existing.get("subtopic") and q.get("subtopic"):
                    existing["subtopic"] = q["subtopic"]
                if existing.get("round_number") is None and q.get("round_number") is not None:
                    existing["round_number"] = q["round_number"]

                # Merge image lists while maintaining role segregation
                if q.get("question_images"):
                    existing["question_images"] = list(dict.fromkeys((existing.get("question_images") or []) + q["question_images"]))
                if q.get("answer_images"):
                    existing["answer_images"] = list(dict.fromkeys((existing.get("answer_images") or []) + q["answer_images"]))
                if q.get("image_refs"):
                    existing["image_refs"] = list(dict.fromkeys((existing.get("image_refs") or []) + q["image_refs"]))

                # Merge audio lists while maintaining role segregation
                if q.get("question_audio"):
                    existing["question_audio"] = list(dict.fromkeys((existing.get("question_audio") or []) + q["question_audio"]))
                if q.get("answer_audio"):
                    existing["answer_audio"] = list(dict.fromkeys((existing.get("answer_audio") or []) + q["answer_audio"]))
                if q.get("audio_refs"):
                    existing["audio_refs"] = list(dict.fromkeys((existing.get("audio_refs") or []) + q["audio_refs"]))

                # Merge video lists while maintaining role segregation
                if q.get("question_videos"):
                    existing["question_videos"] = list(dict.fromkeys((existing.get("question_videos") or []) + q["question_videos"]))
                if q.get("answer_videos"):
                    existing["answer_videos"] = list(dict.fromkeys((existing.get("answer_videos") or []) + q["answer_videos"]))
                if q.get("video_refs"):
                    existing["video_refs"] = list(dict.fromkeys((existing.get("video_refs") or []) + q["video_refs"]))

                if q.get("raw_media_refs"):
                    existing["raw_media_refs"] = list(dict.fromkeys((existing.get("raw_media_refs") or []) + q["raw_media_refs"]))
            else:
                seen_q[key] = q
                consolidated.append(q)

        return consolidated

    def _parse_question_and_options(self, text: str) -> Tuple[str, Optional[List[str]], Optional[str], Optional[int]]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return text, None, None, None

        q_lines = []
        options = []
        detected_theme: Optional[str] = None
        detected_q_num: Optional[int] = None

        for line in lines:
            norm_line = line.strip()
            if re.match(r"^QUESTION\s*\d*[:\.\-]?$", norm_line, re.IGNORECASE):
                continue

            # Standalone question number badge like "1.", "2.", "11.*", "*13.", "14.*"
            num_m = re.match(r"^\*?\s*(\d+)[\.\*\)]*$", norm_line)
            if num_m:
                try:
                    detected_q_num = int(num_m.group(1))
                except Exception:
                    pass
                continue

            # Standalone theme badge like "CREATIVITY", "LEADERSHIP", "SUSTAINABILITY"
            clean_upper = re.sub(r"[\s\-]+", " ", norm_line.upper()).strip()
            matched_theme = next((t for t in self.KNOWN_THEMES if re.sub(r"[\s\-]+", " ", t) == clean_upper), None)
            if matched_theme:
                detected_theme = matched_theme.replace(" -", "-").title()
                continue

            # Check if this line is an option: (A), A., A), [A], 1., 1)
            if re.match(r"^(\([A-D1-4]\)|\[[A-D1-4]\]|[A-D1-4][\.\)])\s+", norm_line, re.IGNORECASE):
                options.append(norm_line)
            else:
                if not options:
                    q_lines.append(norm_line)

        question_text = " ".join(q_lines) if q_lines else lines[0]
        # Strip leading question indicators
        question_text = re.sub(r"^(?:QUESTION\s*\d*[:\.\-]?|Q\d*[:\.\-]?|\*?\d+[\.\*)]\s*)\s*", "", question_text, flags=re.IGNORECASE)

        # Strip trailing theme if it leaked into the question line
        for theme in self.KNOWN_THEMES:
            t_pat = rf"(?:\s+(?:\*?\d+[\.\*]*\s*)?{re.escape(theme)}\s*)$"
            m = re.search(t_pat, question_text, re.IGNORECASE)
            if m:
                if not detected_theme:
                    detected_theme = theme.replace(" -", "-").title()
                question_text = question_text[:m.start()].strip()
                break

        # Strip trailing question number if at the end of the line
        trailing_num = re.search(r"\s+\*?(\d+)[\.\*]+\s*$", question_text)
        if trailing_num:
            if detected_q_num is None:
                try:
                    detected_q_num = int(trailing_num.group(1))
                except Exception:
                    pass
            question_text = question_text[:trailing_num.start()].strip()

        return question_text.strip(), (options if len(options) >= 2 else None), detected_theme, detected_q_num

    def _parse_answer_and_explanation(self, text: str) -> Tuple[str, str]:
        if not text:
            return "", ""
        cleaned_text = re.sub(r"^ANSWER\s*\d*[:\.\-]?\s*", "", text, flags=re.IGNORECASE).strip()
        match = self.ANSWER_REGEX.search(cleaned_text)
        if match:
            ans = match.group(1).strip()
            explanation = cleaned_text.replace(match.group(0), "").strip()
            explanation = re.sub(r"^(?:Explanation\s*(?:&\s*Context)?|Context)\s*[:\-]?\s*", "", explanation, flags=re.IGNORECASE).strip()
            return ans, explanation

        lines = [l.strip() for l in cleaned_text.splitlines() if l.strip()]
        # Ignore lines that are just numbers, bullet numbers, or question labels like "1.", "2.", "14.*"
        lines = [l for l in lines if not re.match(r"^(?:Q\d*[:\.\-]?|\d+[\.\*)]\s*|QUESTION\s*\d*[:\.\-]?)$", l, flags=re.IGNORECASE)]
        if lines:
            first = lines[0]
            if len(first) < 120 and not first.endswith("?"):
                return first, " ".join(lines[1:]) if len(lines) > 1 else ""
            return lines[0], " ".join(lines[1:])

        return "", ""
