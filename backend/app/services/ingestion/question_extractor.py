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

    @staticmethod
    def _is_trans_slide(slide: Optional[Dict[str, Any]]) -> bool:
        if not slide:
            return False
        if slide.get("slide_type") == "ANSWER_TRANSITION":
            return True
        txt = re.sub(r"\s+", " ", (slide.get("extracted_text") or "")).strip()
        return bool(re.match(r"^(?:(?:answer|answers|ans|solution|solutions|ಉತ್ತರ)[\s\!\.\:\-]*)+$", txt, re.I))

    def _infer_qtype_and_payload(
        self,
        subtopic: Optional[str],
        title: str,
        raw_text: str,
        q_text: str,
        options: Optional[List[str]],
        q_imgs: List[str],
        ans_text: str,
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        sub_low = (subtopic or "").lower()
        first_line = ((raw_text or "").splitlines()[0] if raw_text else "").lower()
        hdr_low = f"{title or ''} {first_line}".lower()

        if "list-it" in sub_low or "list it" in sub_low or "list-it" in hdr_low or "list it" in hdr_low or (raw_text or "").count("|") >= 2:
            ans_items = [a.strip() for a in (ans_text or "").split(";") if a.strip()] if ";" in (ans_text or "") else []
            return "LIST_IT", {"type": "LIST_IT", "prompt": q_text, "options": options or [], "answers": ans_items}

        if "image reveal" in sub_low or "reveal round" in sub_low or "image reveal" in hdr_low:
            return "IMAGE_REVEAL", {"type": "IMAGE_REVEAL", "clues": q_imgs, "prompt": q_text}

        if (
            any(k in sub_low for k in ["connect", "market indicators", "common link"])
            or bool(re.match(r"^\s*(?:\d+[\.\)]\s*)?connect\b", hdr_low))
        ):
            elements = [
                {"index": idx + 1, "label": f"Clue {idx + 1}", "text": f"Clue {idx + 1}", "image": img, "image_url": img}
                for idx, img in enumerate(q_imgs)
            ]
            return "CONNECT_4", {
                "type": "CONNECT_4",
                "badge": "Connect 4 • Common Link",
                "prompt": q_text,
                "elements": elements,
                "clues": q_imgs,
                "common_link": ans_text,
                "connection_reveal": ans_text,
            }

        if any(k in sub_low for k in ["closing bell", "buzzer", "buzz", "rapid fire"]):
            return "BUZZER", {"type": "BUZZER"}

        if options:
            return "MULTIPLE_CHOICE", {"type": "QA"}
        return "SLIDE_QA", {"type": "QA"}

    def _extract_list_it_options(self, raw_text: str) -> Tuple[str, Optional[List[str]]]:
        lines = self._strip_nav_and_divider_lines(self._strip_data_uris(raw_text))
        filtered: List[str] = []
        for ln in lines:
            low = ln.lower().strip()
            if re.match(r"^(?:round\s*\d+|list[\s\-]*it!?|question\s*\d*[\.\:]?|pick\s+\d+\s+correct.*)$", low):
                if "round" in low or low in {"list it", "list-it", "list it!"}:
                    continue
            filtered.append(ln.strip())
        if not filtered:
            return raw_text, None
        prompt_idx = 0
        for idx, ln in enumerate(filtered):
            if "?" in ln or ln.endswith(":") or any(w in ln.lower() for w in ["list", "identify", "launch", "select", "pick", "choose", "which", "following"]):
                prompt_idx = idx
                break
        prompt = filtered[prompt_idx]
        opts = [ln for j, ln in enumerate(filtered) if j != prompt_idx and len(ln) <= 120]
        if len(opts) >= 4:
            return prompt, opts
        return prompt, None

    @staticmethod
    def _pick_subtopic(current_subtopic: Optional[str], det_theme: Optional[str]) -> Optional[str]:
        if det_theme and det_theme.strip().lower() not in {"topic", "topics"}:
            cur = (current_subtopic or "").strip().lower()
            if (
                not cur
                or cur in {"topic", "topics", "direct", "general", "dry run", "tie breakers", "buffer questions"}
                or re.match(r"^(?:round|section|part)\s*\d+$", cur)
                or any(tok in cur for tok in (
                    "round", "list-it", "list it", "image reveal", "connect",
                    "buzz", "closing bell", "opening bell", "market indicators",
                    "ticker tangle", "portfolio builder", "audience question",
                ))
            ):
                return det_theme
        return current_subtopic or det_theme

    @staticmethod
    def _is_list_it_slide(slide: Optional[Dict[str, Any]], subtopic: Optional[str]) -> bool:
        if not slide:
            return False
        sub_low = (subtopic or "").lower()
        title_low = (slide.get("title") or "").lower()
        txt = slide.get("extracted_text") or ""
        return (
            "list-it" in sub_low
            or "list it" in sub_low
            or "list-it" in f"{title_low} {txt.lower()}"
            or "list it" in f"{title_low} {txt.lower()}"
            or txt.count("|") >= 2
        )

    def _collect_slide_media(self, slide_list: List[Dict[str, Any]]) -> Tuple[List[str], List[str], List[str], List[Dict[str, Any]]]:
        imgs: List[str] = []
        audio: List[str] = []
        video: List[str] = []
        media: List[Dict[str, Any]] = []
        for s in slide_list:
            imgs.extend(s.get("image_paths") or [])
            s_aud = s.get("audio_paths") or [a["rel_url"] for a in s.get("audio_items", []) if a.get("rel_url")]
            audio.extend(s_aud)
            s_vid = s.get("video_paths") or [v["rel_url"] for v in s.get("video_items", []) if v.get("rel_url")]
            video.extend([v for v in s_vid if "timer" not in (v or "").lower()])
            media.extend(s.get("all_media_items") or [])
        return (
            list(dict.fromkeys(imgs)),
            list(dict.fromkeys(audio)),
            list(dict.fromkeys(video)),
            media,
        )

    def _try_extract_trans_question(
        self,
        slides: List[Dict[str, Any]],
        i: int,
        n: int,
        current_round_num: Optional[int],
        current_subtopic: Optional[str],
        doc_year: Optional[int],
    ) -> Optional[Tuple[Dict[str, Any], int]]:
        """Extract single- or multi-clue Question -> ... -> ANSWER_TRANSITION -> Answer Reveal."""
        if i > 0 and self._is_trans_slide(slides[i - 1]):
            return None
        current = slides[i]
        is_list_it = self._is_list_it_slide(current, current_subtopic)
        # List-It single slides resolve answers from speaker notes unless followed directly by ANSWER_TRANSITION
        if is_list_it and not (i + 2 < n and self._is_trans_slide(slides[i + 1])):
            return None

        trans_idx: Optional[int] = None
        for k in range(i + 1, min(n - 1, i + 4)):
            if self._is_trans_slide(slides[k]) and k + 1 < n:
                trans_idx = k
                break
            st_k = slides[k].get("slide_type")
            if st_k in ("SECTION_MARKER", "ANNOUNCEMENT", "RULES", "SCOREBOARD", "ANSWER"):
                break
            if st_k == "QUESTION" and (slides[k].get("extracted_text") or "").count("|") >= 2:
                break
        if trans_idx is None:
            return None

        ans_slide = slides[trans_idx + 1]
        if ans_slide.get("slide_type") in ("SECTION_MARKER", "RULES", "SCOREBOARD", "ANNOUNCEMENT"):
            return None

        prompt_slides = slides[i:trans_idx]
        best_q_text = ""
        best_options: Optional[List[str]] = None
        det_theme: Optional[str] = None
        det_num: Optional[int] = None
        extra_clues: List[str] = []

        for ps in prompt_slides:
            pt, po, p_th, p_num = self._parse_question_and_options(ps.get("extracted_text", ""))
            if po and not best_options:
                best_options = po
            if p_th and not det_theme:
                det_theme = p_th
            if p_num is not None and det_num is None:
                det_num = p_num
            pt_clean = (pt or "").strip()
            if pt_clean and not re.fullmatch(r"[\d\s\?\.\,\-\(\)]+", pt_clean):
                if len(pt_clean) > len(best_q_text):
                    if best_q_text and best_q_text not in pt_clean:
                        extra_clues.append(best_q_text)
                    best_q_text = pt_clean
                elif pt_clean != best_q_text and pt_clean not in best_q_text and pt_clean not in extra_clues:
                    extra_clues.append(pt_clean)

        ans_text, explanation = self._parse_answer_and_explanation(ans_slide.get("extracted_text", ""))
        if not self._is_usable_answer_text(ans_text) and ans_slide.get("title") and ans_slide.get("title") != "NO_TITLE":
            t_ans, t_exp = self._parse_answer_and_explanation(ans_slide.get("title", ""))
            if self._is_usable_answer_text(t_ans):
                ans_text = t_ans
                if t_exp and not explanation:
                    explanation = t_exp

        ans_notes_clean = self._clean_speaker_notes(ans_slide.get("speaker_notes", ""))
        if not self._is_usable_answer_text(ans_text) and ans_notes_clean:
            n_ans, n_exp = self._parse_answer_and_explanation(ans_notes_clean)
            if self._is_usable_answer_text(n_ans):
                ans_text = n_ans
            if n_exp:
                explanation = f"{explanation}\n{n_exp}".strip()

        q_imgs, q_audio, q_video, ps_media = self._collect_slide_media(prompt_slides)
        ans_imgs, ans_audio, ans_video, ans_media = self._collect_slide_media([ans_slide])
        all_media = list(ps_media) + list(slides[trans_idx].get("all_media_items") or []) + list(ans_media)

        if not self._is_usable_answer_text(ans_text) and (ans_imgs or ans_video or ans_audio):
            ans_text = "See image" if ans_imgs else ("See video" if ans_video else "See audio")

        if not self._is_usable_answer_text(ans_text):
            return None

        # Preserve substantive speaker notes in explanation for visual/connect reveals when answer is concise
        if not explanation and ans_notes_clean and ans_notes_clean.lower() != ans_text.lower():
            explanation = ans_notes_clean
        elif not explanation:
            first_notes = self._clean_speaker_notes(current.get("speaker_notes", ""))
            if first_notes and first_notes.lower() != ans_text.lower():
                explanation = first_notes

        if extra_clues:
            clue_note = "Clues: " + " | ".join(extra_clues)
            explanation = f"{explanation}\n{clue_note}".strip() if explanation else clue_note

        # Check optional explanation/context slide right after ans_slide
        exp_slide = None
        advance_idx = trans_idx + 2
        if trans_idx + 2 < n:
            s3 = slides[trans_idx + 2]
            s3_text = (s3.get("extracted_text") or "").strip()
            s3_title = (s3.get("title") or "").strip().lower()
            if any(w in s3_title or w in s3_text.lower() for w in ["explanation", "trivia", "did you know", "context"]):
                exp_slide = s3
                explanation = f"{explanation}\n{s3_text}".strip()
                ans_imgs = list(dict.fromkeys(ans_imgs + list(s3.get("image_paths") or [])))
                all_media.extend(s3.get("all_media_items") or [])
                advance_idx = trans_idx + 3

        if not best_q_text:
            if "connect" in (current_subtopic or "").lower():
                best_q_text = "What common concept connects these clues and images?"
            elif q_imgs:
                best_q_text = "Identify what is shown in the visual clue(s)."
            else:
                best_q_text = (current.get("title") or current.get("extracted_text") or "").strip()

        raw_first_text = current.get("extracted_text", "")
        q_type, fmt_payload = self._infer_qtype_and_payload(
            current_subtopic, current.get("title", ""), raw_first_text, best_q_text, best_options, q_imgs, ans_text
        )
        if q_type == "CONNECT_4" and len(best_q_text) <= 12 and "connect" in best_q_text.lower():
            best_q_text = "What common concept connects these clues and images?"
            if fmt_payload:
                fmt_payload["prompt"] = best_q_text

        all_imgs = list(dict.fromkeys(q_imgs + ans_imgs))
        all_audio = list(dict.fromkeys(q_audio + ans_audio))
        all_video = list(dict.fromkeys(q_video + ans_video))
        raw_media_refs = list(dict.fromkeys([m["rel_url"] for m in all_media if m.get("rel_url")]))
        combined_notes = "\n".join(x for x in [self._clean_speaker_notes(current.get("speaker_notes", "")), ans_notes_clean] if x).strip()

        return ({
            "slide_id": current.get("id"),
            "answer_slide_id": ans_slide.get("id"),
            "question_text": best_q_text,
            "answer": ans_text,
            "options": best_options,
            "explanation": explanation or self._clean_explanation_fallback(ans_slide.get("extracted_text", ""), ans_text),
            "subtopic": self._pick_subtopic(current_subtopic, det_theme),
            "round_number": current_round_num or det_num,
            "question_type": q_type,
            "format_payload": fmt_payload,
            "source_year": doc_year or 2026,
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
            "media_items": all_media,
            "raw_media_refs": raw_media_refs,
            "speaker_notes": combined_notes,
        }, advance_idx)

    @staticmethod
    def _clean_speaker_notes(notes: str) -> str:
        if not notes:
            return ""
        cleaned = QuestionExtractor._strip_data_uris(notes)
        lines = []
        for ln in cleaned.splitlines():
            s = ln.strip()
            if not s:
                continue
            low = s.lower()
            if "porsche-logo" in low or re.fullmatch(r"https?://\S+", s):
                continue
            lines.append(s)
        return "\n".join(lines).strip()

    def extract_from_slides(self, slides: List[Dict[str, Any]], doc_title: str, doc_year: Optional[int], use_structured: bool = True) -> List[Dict[str, Any]]:
        if use_structured:
            structured = self._extract_structured(slides, doc_year)
            if structured is not None:
                return structured
        extracted_questions = []
        i = 0
        n = len(slides)

        current_round_num: Optional[int] = None
        current_subtopic: Optional[str] = None
        pending_audience = False
        skip_next_question = False

        def _record_question(q_dict: Dict[str, Any]) -> None:
            nonlocal pending_audience, skip_next_question
            if skip_next_question:
                skip_next_question = False
                return
            if pending_audience:
                pending_audience = False
                q_dict["is_audience_question"] = True
                tags = list(q_dict.get("tags") or [])
                if "Audience Question" not in tags:
                    tags.append("Audience Question")
                q_dict["tags"] = tags
            if q_dict.get("speaker_notes"):
                q_dict["speaker_notes"] = self._strip_data_uris(q_dict["speaker_notes"])
            extracted_questions.append(q_dict)

        while i < n:
            current = slides[i]
            slide_type = current.get("slide_type", "CONTENT")
            title = current.get("title", "")
            text = current.get("extracted_text", "")
            notes = current.get("speaker_notes", "")

            # Detect audience question divider slides (e.g. "Audience Question", "Question for Audience")
            combined_marker = f"{title} {text}".strip()
            combined_marker_low = combined_marker.lower()
            if (
                ("audience question" in combined_marker_low or "question for audience" in combined_marker_low or combined_marker_low.strip() == "for audience")
                and (slide_type != "QUESTION" or len(text.split()) <= 10)
            ):
                if "audience questions" in combined_marker_low or "scores reset" in combined_marker_low:
                    current_round_num = 99
                    current_subtopic = "Audience Questions"
                else:
                    pending_audience = True
                i += 1
                continue

            norm_slide_low = re.sub(r"\s+", " ", (text or title or "")).strip().lower()
            norm_title_low = re.sub(r"\s+", " ", (title or "")).strip().lower()
            if (
                re.match(r"^(?:example|sample(?:\s+question)?|demo)\s*[!\.\:]*$", norm_slide_low)
                or re.match(r"^(?:example|sample(?:\s+question)?|demo)\s*[!\.\:]*$", norm_title_low)
                or norm_slide_low.startswith("example:")
                or norm_title_low.startswith("example:")
            ):
                skip_next_question = True
                i += 1
                continue

            if slide_type != "QUESTION" and len(text.split()) <= 12:
                if re.search(r"\b(?:tie[\s\-]*breakers?|buffer\s+questions?)\b", combined_marker_low):
                    current_round_num = None
                    current_subtopic = "Tie Breakers" if "tie" in combined_marker_low else "Buffer Questions"
                    i += 1
                    continue
                if re.search(r"\b(?:scoreboard|winners)\b", combined_marker_low):
                    current_round_num = None
                    current_subtopic = None
                    i += 1
                    continue

            # Skip non-question, bumper, menu board, and preview slides
            if slide_type in ["TITLE", "RULES", "AV_TEST", "TEAM_INTRO", "SCOREBOARD", "ANNOUNCEMENT", "QUESTION_PREVIEW", "ANSWER_TRANSITION", "ANSWER"]:
                i += 1
                continue
            if (
                re.match(r"^(?:ready\s*(?:for\s+q\d*[\.\!]?|[\?\!]?)|(?:question\s+)?for\s+audience)\s*$", norm_slide_low)
                or (norm_slide_low.startswith("topics:") and len(text.split()) <= 30 and "?" not in text)
                or (re.search(r"\bnext\s+round\b", norm_slide_low) and len(text.split()) <= 20 and "?" not in text)
            ):
                i += 1
                continue

            # Check if this slide is a Section / Round Marker
            if slide_type == "SECTION_MARKER" or re.search(r"\bround\s*\d+", f"{title} {text}", re.IGNORECASE):
                t_clean = (title or "").strip()
                x_clean = (text or "").strip()
                if x_clean and re.search(r"\bround\s*\d+", x_clean, re.I):
                    search_src = x_clean
                else:
                    search_src = t_clean or x_clean
                round_match = self.ROUND_REGEX.search(search_src)
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
                        clean_sub = re.split(r"[\r\n]+|\bround\s*\d+\b|\d+\s*questions", clean_sub, flags=re.I)[0].strip("??-: \t")
                        clean_sub = re.sub(r"^(?:Round\s*\d+\s*[-–—:]*\s*)+", "", clean_sub, flags=re.I).strip("–—-: \t")
                        current_subtopic = clean_sub if clean_sub else f"Round {current_round_num}"
                    else:
                        current_subtopic = f"Round {current_round_num}"
                elif len(text.split()) <= 12 and re.search(r"\b(?:tie[\s\-]*breakers?|scoreboard|winners)\b", f"{title} {text}", re.I):
                    current_round_num = None
                    current_subtopic = "Tie Breakers" if "tie" in f"{title} {text}".lower() else None
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
            is_stepped_round = any(k in (current_subtopic or "").lower() for k in ["portfolio builder", "stepped clue", "3 clues", "three clues", "3 parts"])
            is_stepped_round = any(k in (current_subtopic or "").lower() for k in ["portfolio builder", "stepped clue", "3 clues", "three clues", "3 parts"]) and (i + 3 < n and slides[i+1].get("slide_type") != "ANSWER_TRANSITION" and slides[i+2].get("slide_type") != "ANSWER_TRANSITION")
            is_stepped_round = is_stepped_round and i + 3 < n and slides[i+1].get("slide_type") != "ANSWER_TRANSITION" and slides[i+2].get("slide_type") != "ANSWER_TRANSITION"
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

                    _record_question({
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
            is_arrange_round = any(k in (current_subtopic or "").lower() for k in ["ticker tangle", "arrange"]) and (i + 1 < n and slides[i+1].get("slide_type") != "ANSWER_TRANSITION")
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

                    _record_question({
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
            is_connect_round = ("market indicators" in (current_subtopic or "").lower()) and (i + 1 < n and slides[i+1].get("slide_type") != "ANSWER_TRANSITION")
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

                    _record_question({
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
            if is_bumper and (i + 1 < n and not self._is_trans_slide(slides[i+1])) and i + 3 < n and (
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

                    is_buzzer_sub = any(k in (current_subtopic or "").lower() for k in ["closing bell", "buzzer", "buzz", "rapid fire"])
                    q_type = "BUZZER" if is_buzzer_sub else ("MULTIPLE_CHOICE" if options else "SLIDE_QA")
                    fmt_payload = {"type": "BUZZER"} if is_buzzer_sub else {"type": "QA"}

                    _record_question({
                        "slide_id": prompt_slide.get("id"),
                        "answer_slide_id": ans_slide.get("id"),
                        "question_text": q_text or prompt_slide.get("extracted_text", ""),
                        "answer": ans_text,
                        "options": options,
                        "explanation": explanation or ans_slide.get("extracted_text", ""),
                        "subtopic": self._pick_subtopic(current_subtopic, det_theme),
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

            # Pattern A2 / C2 / Multi-slide Reveal: Question -> (0..2 Clues) -> ANSWER_TRANSITION -> Answer Reveal
            if slide_type == "QUESTION":
                trans_res = self._try_extract_trans_question(slides, i, n, current_round_num, current_subtopic, doc_year)
                if trans_res is not None:
                    q_payload, next_i = trans_res
                    _record_question(q_payload)
                    i = next_i
                    continue

            # Pattern A: Question on Slide i, Answer on Slide i + 1
            if slide_type == "QUESTION" and i + 1 < n and slides[i+1].get("slide_type") == "ANSWER":
                ans_slide = slides[i+1]
                q_text, options, det_theme, det_num = self._parse_question_and_options(text)
                ans_text, explanation = self._parse_answer_and_explanation(ans_slide.get("extracted_text", ""))

                # If answer text is still empty, check speaker notes of the answer slide
                if not self._is_usable_answer_text(ans_text) and ans_slide.get("speaker_notes"):
                    ans_text, explanation = self._parse_answer_and_explanation(ans_slide.get("speaker_notes", ""))

                if not self._is_usable_answer_text(ans_text) and ans_slide.get("image_paths"):
                    ans_text = "See image"

                if self._is_usable_answer_text(ans_text):
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

                    _record_question({
                        "slide_id": current.get("id"),
                        "answer_slide_id": ans_slide.get("id"),
                        "question_text": q_text,
                        "answer": ans_text,
                        "options": options,
                        "explanation": explanation or self._clean_explanation_fallback(ans_slide.get("extracted_text", ""), ans_text),
                        "subtopic": self._pick_subtopic(current_subtopic, det_theme),
                        "round_number": current_round_num or det_num,
                        "question_type": self._infer_qtype_and_payload(current_subtopic, title, text, q_text, options, q_imgs, ans_text)[0],
                        "format_payload": self._infer_qtype_and_payload(current_subtopic, title, text, q_text, options, q_imgs, ans_text)[1],
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

                if self._is_usable_answer_text(ans_text):
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

                    _record_question({
                        "slide_id": current.get("id"),
                        "answer_slide_id": ans_slide.get("id"),
                        "question_text": q_text,
                        "answer": ans_text,
                        "options": options,
                        "explanation": combined_exp,
                        "subtopic": self._pick_subtopic(current_subtopic, det_theme),
                        "round_number": current_round_num or det_num,
                        "question_type": self._infer_qtype_and_payload(current_subtopic, title, text, q_text, options, q_imgs, ans_text)[0],
                        "format_payload": self._infer_qtype_and_payload(current_subtopic, title, text, q_text, options, q_imgs, ans_text)[1],
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
            if slide_type == "QUESTION" and not (i > 0 and self._is_trans_slide(slides[i - 1])):
                q_text, options, det_theme, det_num = self._parse_question_and_options(text)
                ans_text = ""
                explanation = ""

                is_list_it = (
                    "list-it" in (current_subtopic or "").lower()
                    or "list it" in (current_subtopic or "").lower()
                    or "list-it" in f"{title} {text}".lower()
                    or "list it" in f"{title} {text}".lower()
                    or text.count("|") >= 2
                )
                clean_notes = self._clean_speaker_notes(notes)
                if is_list_it:
                    list_q, list_opts = self._extract_list_it_options(text)
                    if list_q:
                        q_text = list_q
                    if list_opts:
                        options = list_opts
                # Look in notes first (ensure notes is not just a URL or data URI)
                if clean_notes and not (clean_notes.startswith("http://") or clean_notes.startswith("https://")):
                    ans_text, explanation = self._parse_answer_and_explanation(clean_notes, join_enumerated_list=is_list_it)

                # Look in body text only if explicit answer prefix exists (e.g. "Answer:" or "Ans:")
                if not self._is_usable_answer_text(ans_text) and re.search(r"(?:^|\n)\s*(?:Correct\s+Answer|Solution|Answer|Ans)\s*[:\-]", text, re.IGNORECASE):
                    ans_text, explanation = self._parse_answer_and_explanation(text, join_enumerated_list=is_list_it)

                if is_list_it and not self._is_usable_answer_text(ans_text) and options and len(options) >= 4:
                    ans_text = f"Select matching items from the {len(options)} options on slide (answer key not in slide notes)"
                    if not explanation:
                        explanation = f"Options on slide: {'; '.join(options)}"
                # Only include if a real answer was resolved and not a URL / placeholder / base64 URI
                if self._is_usable_answer_text(ans_text):
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

                    _record_question({
                        "slide_id": current.get("id"),
                        "answer_slide_id": None,
                        "question_text": q_text,
                        "answer": ans_text,
                        "options": options,
                        "explanation": explanation,
                        "subtopic": self._pick_subtopic(current_subtopic, det_theme),
                        "round_number": current_round_num or det_num,
                        "question_type": "LIST_IT" if is_list_it else self._infer_qtype_and_payload(current_subtopic, title, text, q_text, options, q_imgs, ans_text)[0],
                        "format_payload": self._infer_qtype_and_payload(current_subtopic, title, text, q_text, options, q_imgs, ans_text)[1],
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
                        "speaker_notes": clean_notes
                    })
                i += 1
                continue

            i += 1

        # Intra-deck deduplication and consolidation:
        # Handles tournament prelim decks where questions are shown in a "Question Run"
        # and then repeated in the "Answer Reveal Run".
        consolidated = []
        seen_q: Dict[str, Dict[str, Any]] = {}

        generic_prompts = {
            "connect", "what connects", "what connects these clues",
            "what common concept connects these clues and images",
            "what connects all these clues and images",
            "image reveal", "identify", "identify what is shown in the visual clues",
            "identify what is shown in the image clues",
        }
        for q in extracted_questions:
            words = re.sub(r"[^\w\s]", "", (q.get("question_text") or "").lower()).split()
            norm_phrase = " ".join(words[:10]) if words else (q.get("question_text") or "")
            curr_ans = (q.get("answer") or "").strip()
            ans_norm = re.sub(r"[^\w\s]", "", curr_ans.lower()).strip()

            if len(words) < 5 or " ".join(words) in generic_prompts:
                key = f"{norm_phrase}__s{q.get('slide_number')}_{ans_norm[:40]}"
            else:
                key = norm_phrase

            if key in seen_q:
                existing = seen_q[key]
                exist_ans = (existing.get("answer") or "").strip()
                exist_norm = re.sub(r"[^\w\s]", "", exist_ans.lower()).strip()
                if (
                    self._is_usable_answer_text(curr_ans)
                    and self._is_usable_answer_text(exist_ans)
                    and exist_norm
                    and ans_norm
                    and exist_norm != ans_norm
                    and exist_norm not in ans_norm
                    and ans_norm not in exist_norm
                ):
                    uniq_key = f"{key}__s{q.get('slide_number')}"
                    seen_q[uniq_key] = q
                    consolidated.append(q)
                    continue

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

                for field in (
                    "question_images", "answer_images", "image_refs",
                    "question_audio", "answer_audio", "audio_refs",
                    "question_videos", "answer_videos", "video_refs",
                    "raw_media_refs",
                ):
                    if q.get(field):
                        existing[field] = list(dict.fromkeys((existing.get(field) or []) + q[field]))
            else:
                seen_q[key] = q
                consolidated.append(q)

        return consolidated

    @staticmethod
    def _strip_data_uris(text: str) -> str:
        if not text:
            return ""
        cleaned = re.sub(r"data:[a-z0-9.+/-]+;base64,[A-Za-z0-9+/=\s_-]+", "", text, flags=re.IGNORECASE)
        return cleaned.strip()

    @staticmethod
    def _strip_nav_and_divider_lines(text: str) -> List[str]:
        lines = []
        for raw_line in (text or "").splitlines():
            ln = raw_line.strip()
            if not ln:
                continue
            # Drop standalone navigation buttons (Back, Next round, Home, Menu)
            if "porsche-logo" in ln.lower() or ln in {"?", "..."} or re.match(r"^(?:back(?:\s+to\s+menu)?|go\s+back|next(?:\s+(?:round|question))?|home|menu|previous(?:\s+slide)?|skip)[\.\!\:\-\→\←]*$", ln, flags=re.IGNORECASE):
                continue
            # Drop empty table separators like "| |" or "---"
            if re.fullmatch(r"[\s\|\-\+\_]+", ln):
                continue
            lines.append(ln)
        return lines

    @staticmethod
    def _is_usable_answer_text(ans: str) -> bool:
        s = (ans or "").strip()
        if not s:
            return False
        low = s.lower()
        if low.startswith(("http://", "https://", "data:")):
            return False
        stripped = re.sub(r"[\.\!\:\-]+$", "", low).strip()
        if stripped in {"", "see explanation", "answer indicated on slide", "answer", "answers", "ans", "solution", "solutions", "ಉತ್ತರ"}:
            return False
        if re.fullmatch(r"[\s\|\-\_\.\!\?\,\:\;]+", s):
            return False
        return True

    def _clean_explanation_fallback(self, raw_text: str, ans_text: str = "") -> str:
        cleaned = self._strip_data_uris(raw_text)
        lines = [
            ln for ln in self._strip_nav_and_divider_lines(cleaned)
            if not re.match(r"^(?:answer|answers|ans|solution|ಉತ್ತರ)[\s\!\:\.\-]*$", ln, re.IGNORECASE)
        ]
        joined = " ".join(lines).strip()
        if ans_text and joined.strip().lower() == ans_text.strip().lower():
            return ""
        return joined

    def _parse_question_and_options(self, text: str) -> Tuple[str, Optional[List[str]], Optional[str], Optional[int]]:
        clean_raw = self._strip_data_uris(text)
        lines = self._strip_nav_and_divider_lines(clean_raw)
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

            topic_q_m = re.match(r"^([A-Za-z][A-Za-z\s&/\-]{2,28}):\s*Q\s*(\d*)\s*[\.\:]?$", norm_line, re.IGNORECASE)
            if topic_q_m:
                cand_theme = topic_q_m.group(1).strip()
                if not detected_theme and cand_theme.lower() not in {"answer", "ans", "note", "explanation"}:
                    detected_theme = cand_theme
                if detected_q_num is None and topic_q_m.group(2):
                    try:
                        detected_q_num = int(topic_q_m.group(2))
                    except Exception:
                        pass
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

        non_badge_lines = [l for l in lines if not re.match(r"^\*?\s*\d+[\.\*\)]*$", l) and l.strip() not in {"?", "..."}]
        question_text = " ".join(q_lines) if q_lines else (non_badge_lines[0] if non_badge_lines else "")
        # Strip leading question indicators
        question_text = re.sub(r"^\?\s+", "", question_text).strip()
        question_text = re.sub(r"^(?:QUESTION\s*\d*[:\.\-]?|Q\d*[:\.\-]?|\*?\d+[\.\*)]\s*)\s*", "", question_text, flags=re.IGNORECASE)
        question_text = re.sub(r"\s+[A-Za-z][A-Za-z\s&/\-]{2,25}:\s*Q\s*\d*\s*$", "", question_text, flags=re.IGNORECASE).strip()

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

    def _parse_answer_and_explanation(self, text: str, join_enumerated_list: bool = False) -> Tuple[str, str]:
        if not text:
            return "", ""
        cleaned_text = self._strip_data_uris(text)
        if not cleaned_text:
            return "", ""
        # Strip leading bilingual/English answer divider prefixes (e.g. "ಉತ್ತರ!", "ANSWER:")
        cleaned_text = re.sub(r"^(?:ಉತ್ತರ\s*[:\!\.\-]*|ANSWER\s*\d*[:\!\.\-]?)\s*", "", cleaned_text, flags=re.IGNORECASE | re.MULTILINE).strip()

        lines = self._strip_nav_and_divider_lines(cleaned_text)
        # Drop pure number badges, divider leftovers, or question labels
        lines = [
            l for l in lines
            if not re.match(r"^(?:Q\d*[:\.\-]?|\d+[\.\*)]\s*|QUESTION\s*\d*[:\.\-]?|ಉತ್ತರ[!\.:\-]*|answers?[!\.:\-]*)$", l, flags=re.IGNORECASE)
        ]
        if not lines:
            return "", ""

        rebuilt = "\n".join(lines)
        match = self.ANSWER_REGEX.search(rebuilt)
        if match:
            ans = match.group(1).strip()
            if self._is_usable_answer_text(ans):
                explanation = rebuilt.replace(match.group(0), "").strip()
                explanation = re.sub(r"^(?:Explanation\s*(?:&\s*Context)?|Context)\s*[:\-]?\s*", "", explanation, flags=re.IGNORECASE).strip()
                return ans, explanation

        # List-It! rounds store 4-8 valid items in speaker notes (A. ..., B. ..., C. ...)
        if join_enumerated_list and len(lines) >= 2:
            item_lines = [l for l in lines if not re.match(r"^(?:correct\s+)?answers?\s*[:\-]?\s*$", l, re.IGNORECASE) and not l.lower().startswith(("http://", "https://", "explanation", "did you know", "trivia", "note:", "source:"))]
            enum_lines = [l for l in item_lines if re.match(r"^(?:[A-Za-z0-9]{1,2}[\.\)]|\([A-Za-z0-9]{1,2}\)|[-•*])\s*\S+", l)]
            if len(enum_lines) < 3 and len(item_lines) >= 3 and all(len(l) < 85 for l in item_lines):
                enum_lines = item_lines
            if len(enum_lines) >= 3:
                joined = "; ".join(enum_lines)
                if len(joined) <= 1000:
                    return joined, ""

        # Bilingual slide/notes guard: when the first line is non-Latin (e.g. Kannada) and
        # another concise English line is present, prefer the English answer and retain the
        # regional line inside explanation so retrieval/entity-dedup always works cleanly.
        has_indic = lambda s: bool(re.search(r"[\u0900-\u0FFF]", s or ""))
        has_latin = lambda s: bool(re.search(r"[A-Za-z]", s or ""))
        first = lines[0]
        if has_indic(first) and not has_latin(first):
            for idx, cand in enumerate(lines[1:], start=1):
                if (
                    has_latin(cand)
                    and len(cand) < 140
                    and not cand.lower().startswith(("http://", "https://", "explanation", "did you know", "trivia", "note:", "source:"))
                ):
                    rest = [l for j, l in enumerate(lines) if j != idx]
                    return cand, " ".join(rest)

        if len(first) < 120 and not first.endswith("?"):
            return first, " ".join(lines[1:]) if len(lines) > 1 else ""
        return lines[0], " ".join(lines[1:])
