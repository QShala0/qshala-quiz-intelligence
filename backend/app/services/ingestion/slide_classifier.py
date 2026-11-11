import re
from typing import Dict, Any, List, Tuple

class SlideClassifier:
    """
    Context-aware two-pass slide classifier and deck layout analyzer for QShala presentation decks.
    Pass 1: Computes category confidence scores (Questions, Answers, Transitions, Rules, AV Tests).
    Pass 2: Detects deck-level layout (Two-Section, Interleaved, Notes-Based) and uses sequence context
            to handle dramatic-pause transition slides, eliminate previews, and bridge multi-slide reveals.
    """

    QUESTION_INDICATORS = [
        r"\?",
        r"\b(who|what|where|when|why|which|how|identify|name the)\b",
        r"\b(q\d+|question\s*\d*)\b",
        r"^[0-9]+[\.\)]\s+",
        r"\(A\).*\(B\)",
        r"clue\s*\d*",
        r"(?:lowest to highest|highest to lowest|earliest to latest|from 1st to last|chronological order|arrange|ticker tangle)",
        r"\bconnect\b",
    ]

    ANSWER_INDICATORS = [
        r"\b(ans|answer|ans:)\b",
        r"\b(correct answer|solution)\b",
        r"\b(explanation|did you know|trivia)\b",
        r"\b(it is|the answer is|this was)\b",
    ]

    _ANS_TRANS_TOKEN = r"(?:answer|answers|ans|solution|solutions|the\s+answer|let['’]?s\s+see\s+the\s+answer|ಉತ್ತರ)"
    ANSWER_TRANSITION_REGEX = re.compile(
        rf"^\s*{_ANS_TRANS_TOKEN}(?:\s*[\.\:\-\!]?\s+{_ANS_TRANS_TOKEN})*\s*[\.\:\-\!]?\s*$",
        re.IGNORECASE
    )

    TWO_SECTION_DIVIDERS = [
        r"\b(please\s+hand\s+in\s+your\s+sheets|answers\s+follow|let['’]?s\s+discuss\s+(the\s+)?answers|preliminary\s+round\s+answers|prelim\s+answers|time\s+for\s+(the\s+)?answers|now\s+for\s+(the\s+)?answers)\b",
        r"^\s*(answers|solutions)\s*$"
    ]

    SECTION_INDICATORS = [
        r"\b(round\s*\d+|section\s*\d+|part\s*\d+|theme\s*\d+|finals|prelims|tie\s*breaker|topicator|potpourri|rapid\s*fire|list\s*it|buzzer)\b",
        r"\b(intermission|break)\b",
    ]

    TITLE_INDICATORS = [
        r"\b(welcome|guidelines|quiz master|qshala|instructions|thank you|prizes|prize|structure of the quiz)\b",
    ]

    RULES_INDICATORS = [
        r"\b(rules|instructions for prelims|marking scheme|scoring system|\+20 for direct|\+10 for a pass)\b",
    ]

    AV_TEST_INDICATORS = [
        r"\b(av test|audio video test|sound check|mic check)\b",
    ]

    TEAM_INTRO_INDICATORS = [
        r"\b(presenting the finalists|finalists|team \d+|meet the teams)\b",
    ]

    ANNOUNCEMENT_INDICATORS = [
        r"\b(social media|follow @|qshots|hand in your sheets)\b",
        r"^\s*announcement\s*[\:\-]?\s*$"
    ]

    def detect_deck_layout(self, slides: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analyzes deck layout architecture:
        - TWO_SECTION: First half has questions-only (written prelim round),
          followed by an explicit divider ("ANSWERS FOLLOW", "LET'S DISCUSS THE ANSWERS"),
          followed by questions with answers.
        - INTERLEAVED: Questions and answers paired per round throughout (finals/live quiz).
        - NOTES_BASED: Question on slide, answer in speaker notes.
        """
        n = len(slides)
        if n < 6:
            return {"layout_type": "INTERLEAVED", "answers_section_start": None}

        # Search for two-section divider between slide index 4 and n - 3
        divider_idx = None
        for i in range(4, min(n - 2, int(n * 0.85))):
            s = slides[i]
            title = (s.get("title") or "").strip().lower()
            text = (s.get("extracted_text") or "").strip().lower()
            combined = f"{title} {text}"

            for pat in self.TWO_SECTION_DIVIDERS:
                if re.search(pat, combined, re.IGNORECASE):
                    divider_idx = i
                    break
            if divider_idx is not None:
                break

        if divider_idx is not None:
            # Check if there are questions before divider and after divider
            pre_questions = sum(1 for s in slides[:divider_idx] if re.search(r"^\s*\d+[\.\)]|\?", (s.get("extracted_text") or "") + (s.get("title") or "")))
            post_questions = sum(1 for s in slides[divider_idx:] if re.search(r"^\s*\d+[\.\)]|\?", (s.get("extracted_text") or "") + (s.get("title") or "")))

            if pre_questions >= 2 and post_questions >= 2:
                return {
                    "layout_type": "TWO_SECTION",
                    "answers_section_start": divider_idx,
                    "preview_range": (0, divider_idx)
                }

        # Check for notes-based deck
        notes_with_answers = sum(
            1 for s in slides
            if s.get("speaker_notes") and len(s.get("speaker_notes", "").strip()) > 10
            and re.search(r"\b(ans|answer|correct|solution|http)\b", s.get("speaker_notes", ""), re.IGNORECASE)
        )
        if notes_with_answers >= max(5, int(n * 0.35)):
            return {"layout_type": "NOTES_BASED", "answers_section_start": None}

        return {"layout_type": "INTERLEAVED", "answers_section_start": None}

    def score_slide(self, slide_data: Dict[str, Any]) -> Dict[str, float]:
        """Pass 1: Scores an individual slide across categories."""
        text = (slide_data.get("extracted_text") or "").strip()
        title = (slide_data.get("title") or "").strip()
        notes = (slide_data.get("speaker_notes") or "").strip()
        full_text = f"{title}\n{text}\n{notes}".lower()
        title_lower = title.lower()
        text_lower = text.lower()
        word_count = len(text.split())

        scores = {
            "TITLE": 0.1,
            "SECTION_MARKER": 0.0,
            "SCOREBOARD": 0.0,
            "RULES": 0.0,
            "AV_TEST": 0.0,
            "TEAM_INTRO": 0.0,
            "ANNOUNCEMENT": 0.0,
            "ANSWER_TRANSITION": 0.0,
            "QUESTION": 0.0,
            "ANSWER": 0.0,
            "CONTENT": 0.3
        }

        # Dramatic pause answer divider (e.g. "ANSWER", "Answer!", "ಉತ್ತರ!\nAnswer!")
        norm_text = re.sub(r"\s+", " ", text).strip()
        norm_title = re.sub(r"\s+", " ", title).strip()
        combined_short = (norm_title if not norm_text else (norm_text if not norm_title or norm_title == "NO_TITLE" else f"{norm_title} {norm_text}")).strip()
        if (
            self.ANSWER_TRANSITION_REGEX.match(combined_short)
            or self.ANSWER_TRANSITION_REGEX.match(norm_text)
            or (norm_title and norm_title != "NO_TITLE" and self.ANSWER_TRANSITION_REGEX.match(norm_title))
        ):
            scores["ANSWER_TRANSITION"] = 0.98
            return scores

        # Finals / quiz bumper & menu slides that should never be misread as QUESTIONS
        bumper_blob = norm_text or norm_title
        bumper_low = bumper_blob.lower()
        if (
            re.match(r"^(?:ready\s*(?:for\s+q\d*[\.\!]?|[\?\!]?)|(?:question\s+)?for\s+audience|audience\s+questions?|example\s*!?|buffer\s*(?:questions?)?)\s*$", bumper_low)
            or (bumper_low.startswith("topics:") and word_count <= 30 and "?" not in norm_text)
            or (re.search(r"\bnext\s+round\b", bumper_low) and word_count <= 20 and "?" not in norm_text)
        ):
            scores["ANNOUNCEMENT"] = 0.92
            return scores

        # Slide 1 is almost always TITLE
        slide_num = slide_data.get("slide_number", 1)
        if slide_num == 1:
            scores["TITLE"] += 0.7

        # AV Test check
        if any(re.search(pat, full_text) for pat in self.AV_TEST_INDICATORS):
            scores["AV_TEST"] += 0.90

        # Team / Finalists check
        if any(re.search(pat, full_text) for pat in self.TEAM_INTRO_INDICATORS):
            scores["TEAM_INTRO"] += 0.85

        # Announcements / Social Media (only if not an explicit question)
        if any(re.search(pat, full_text) for pat in self.ANNOUNCEMENT_INDICATORS):
            if "?" not in full_text and not re.search(r"^[1-9]\d?[\.\)](?:\s+|[a-z])", text_lower, re.MULTILINE):
                scores["ANNOUNCEMENT"] += 0.80

        # Section markers (Round 1, Round 2, Potpourri)
        if any(re.search(pat, title_lower) for pat in self.SECTION_INDICATORS):
            scores["SECTION_MARKER"] += 0.85
        elif any(re.search(pat, text_lower) for pat in self.SECTION_INDICATORS) and word_count < 10:
            scores["SECTION_MARKER"] += 0.75

        # Scoreboards & Rules
        if any(re.search(pat, full_text) for pat in [r"scoreboard", r"scores", r"leaderboard", r"points"]):
            scores["SCOREBOARD"] += 0.85
        has_explicit_rules = any(
            re.search(pat, full_text)
            for pat in [r"instructions for prelims|marking scheme|scoring system|\+20 for direct|\+10 for a pass"]
        )
        has_generic_rules = bool(re.search(r"\brules\b", full_text))
        is_likely_question_with_word_rules = ("?" in full_text and word_count >= 12 and "rules" not in title_lower)
        if has_explicit_rules or (has_generic_rules and not is_likely_question_with_word_rules):
            scores["RULES"] += 0.85

        # Title indicators
        if any(re.search(pat, title_lower) for pat in self.TITLE_INDICATORS):
            scores["TITLE"] += 0.65

        # Explicit Answer prefixes (only if not a section/transition marker)
        if scores["SECTION_MARKER"] < 0.5:
            if re.search(r"^\s*(ans|answer|solution)\s*[:\-]", text_lower, re.MULTILINE):
                scores["ANSWER"] += 0.90
            elif any(re.search(pat, full_text) for pat in self.ANSWER_INDICATORS):
                scores["ANSWER"] += 0.60

        # Question signals
        q_hits = sum(1 for pat in self.QUESTION_INDICATORS if re.search(pat, full_text))
        if q_hits >= 2:
            scores["QUESTION"] += 0.80
        elif q_hits == 1:
            if "?" in full_text or re.search(r"\b(q\d+|question|name the|identify)\b", full_text):
                scores["QUESTION"] += 0.60
            elif re.search(r"^[1-9]\d?[\.\)](?:\s+|[a-z])", text_lower, re.MULTILINE) or re.search(r"^[0-9]+[\.\)]\s*$", title_lower):
                scores["QUESTION"] += 0.65
            else:
                scores["QUESTION"] += 0.35
        elif text.count("|") >= 2 and word_count >= 6 and scores["SCOREBOARD"] < 0.5 and scores["RULES"] < 0.5 and scores["TEAM_INTRO"] < 0.5:
            # Table/grid prompt slide (e.g. Finals List-It round) whose answers live in speaker notes
            has_real_notes = bool(notes and not notes.strip().startswith(("http://", "https://", "data:image/")))
            non_pipe_lines = [ln for ln in text.splitlines() if ln.strip() and ln.strip() != "| |"]
            if has_real_notes:
                scores["QUESTION"] += 0.70
            elif len(non_pipe_lines) >= 5 and not re.search(r"\b(?:scoreboard|leaderboard)\b", text_lower):
                scores["QUESTION"] += 0.55

        return scores

    def classify(self, slide_data: Dict[str, Any]) -> str:
        """Single-slide classification for backwards compatibility."""
        scores = self.score_slide(slide_data)
        best_type = max(scores.items(), key=lambda x: x[1])[0]
        return best_type

    def classify_deck(self, slides: List[Dict[str, Any]]) -> List[str]:
        """
        Pass 2: Contextual sequence classification across all slides in a deck.
        Corrects ambiguous slides based on layout and preceding/following neighbors.
        """
        if not slides:
            return []

        # Step 1: Detect layout
        layout_info = self.detect_deck_layout(slides)
        layout_type = layout_info["layout_type"]
        answers_start = layout_info.get("answers_section_start")

        # Step 2: Pass 1 raw scoring
        scored_slides = [self.score_slide(s) for s in slides]
        classified_types = [max(sc.items(), key=lambda x: x[1])[0] for sc in scored_slides]

        n = len(slides)

        # Step 3: Handle Two-Section decks (skip question previews before answers start)
        if layout_type == "TWO_SECTION" and answers_start is not None:
            for i in range(answers_start):
                if classified_types[i] == "QUESTION":
                    classified_types[i] = "QUESTION_PREVIEW"

        # Step 4: Contextual neighbor adjustments
        for i in range(n):
            current_type = classified_types[i]
            prev_type = classified_types[i - 1] if i > 0 else None
            next_type = classified_types[i + 1] if i + 1 < n else None
            s_text = (slides[i].get("extracted_text") or "").strip()
            s_text_lower = s_text.lower()

            # Pattern: If next slide is ANSWER_TRANSITION, current slide is part of the question prompt
            # (unless it's a section/bumper/rules header).
            next_norm = re.sub(r"\s+", " ", (slides[i + 1].get("extracted_text") or "")).strip() if i + 1 < n else ""
            is_next_trans = (i + 1 < n and (
                classified_types[i + 1] == "ANSWER_TRANSITION" or
                bool(self.ANSWER_TRANSITION_REGEX.match(next_norm))
            ))
            if is_next_trans and current_type not in ["SECTION_MARKER", "ANNOUNCEMENT", "RULES"]:
                classified_types[i] = "QUESTION"
                continue

            # Pattern: If previous slide was an ANSWER_TRANSITION (e.g. "ANSWER" or "ಉತ್ತರ!\nAnswer!"),
            # the current slide is the actual answer reveal slide (including Round 5 "Guess the Question").
            if prev_type == "ANSWER_TRANSITION":
                if current_type not in ["SECTION_MARKER", "RULES", "SCOREBOARD", "ANNOUNCEMENT"] and scored_slides[i]["SECTION_MARKER"] < 0.6:
                    classified_types[i] = "ANSWER"
                    continue

            # Pattern: If previous slide was a QUESTION, and current slide is CONTENT or ambiguous,
            # promote to ANSWER if not a section marker / scoreboard (and NOT followed by an answer transition)
            elif prev_type == "QUESTION" and current_type in ["CONTENT", "TITLE"]:
                has_upcoming_trans = False
                if scored_slides[i]["ANSWER"] < 0.4 and scored_slides[i]["SECTION_MARKER"] < 0.5 and scored_slides[i]["SCOREBOARD"] < 0.5:
                    for j in range(i + 1, min(n, i + 4)):
                        jt = classified_types[j]
                        jn = re.sub(r"\s+", " ", (slides[j].get("extracted_text") or "")).strip()
                        if jt == "ANSWER_TRANSITION" or self.ANSWER_TRANSITION_REGEX.match(jn):
                            has_upcoming_trans = True
                            break
                        if jt in ["SECTION_MARKER", "ANNOUNCEMENT", "RULES", "SCOREBOARD", "ANSWER"]:
                            break
                        if scored_slides[j]["ANSWER"] >= 0.6:
                            break
                if has_upcoming_trans:
                    classified_types[i] = "QUESTION"
                    continue
                if scored_slides[i]["SECTION_MARKER"] < 0.5 and scored_slides[i]["SCOREBOARD"] < 0.5 and current_type != "ANSWER_TRANSITION" and not is_next_trans:
                    classified_types[i] = "ANSWER"
            elif current_type == "CONTENT" and s_text.count("|") >= 2 and scored_slides[i]["SCOREBOARD"] < 0.5 and scored_slides[i]["RULES"] < 0.5:
                non_pipe = [ln for ln in s_text.splitlines() if ln.strip() and ln.strip() != "| |"]
                if len(non_pipe) >= 5:
                    classified_types[i] = "QUESTION"
                    continue





            # Pattern: If slide was marked ANSWER because of speaker notes or trivia, check if it's actually a QUESTION
            # (never override a slide that immediately follows an ANSWER_TRANSITION divider).
            if current_type == "ANSWER" and prev_type != "ANSWER_TRANSITION" and "?" in (slides[i].get("extracted_text") or ""):
                if scored_slides[i]["QUESTION"] >= 0.5:
                    classified_types[i] = "QUESTION"

        return classified_types
