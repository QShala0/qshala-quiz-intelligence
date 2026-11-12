import re
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models.question import Question
from backend.app.models.quiz import Quiz, GeneratedQuestion, RetrievalSource
from backend.app.services.prompt_quiz import engine

router = APIRouter(prefix="/prompt-quiz", tags=["Prompt Quiz"])

STARTERS = [
    "10 evergreen questions on Indian startups",
    "A tough 15-question finals round on banking and money",
    "Warm-up quiz on brands and logos, no images",
    "20 questions on cricket and the IPL, school safe",
    "A fintech and UPI round, 8 questions",
    "Something fun on food and festivals for a family night",
]


class DraftReq(BaseModel):
    prompt: str = Field(..., min_length=2, max_length=600)
    count: Optional[int] = Field(default=None, ge=0, le=50)
    options: Dict[str, Any] = {}
    # Refinement: pass the `brief` from a prior /draft response alongside a follow-up prompt
    # ("make it harder", "swap cricket for tech", "give me 5 more") to refine instead of restart.
    previous_brief: Optional[Dict[str, Any]] = None
    keep_ids: List[str] = []
    exclude_ids: List[str] = []
    current_questions: Optional[List[Dict[str, Any]]] = None


class SwapReq(BaseModel):
    prompt: str
    question_id: str
    keep_ids: List[str] = []
    options: Dict[str, Any] = {}


class SaveReq(BaseModel):
    title: Optional[str] = None
    prompt: str
    question_ids: List[str] = Field(..., min_length=1, max_length=50)
    brief: Optional[Dict[str, Any]] = None


WORD_MAP = {
    'a': 1, 'an': 1, 'another': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
    'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10
}
STOP_WORDS = {
    'a', 'an', 'another', 'more', 'extra', 'additional', 'new', 'questions',
    'question', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight',
    'nine', 'ten'
}


def parse_add_intent(prompt: str) -> Optional[Dict[str, Any]]:
    p = prompt.strip()
    if re.search(r'\b(?:replace|swap|change|remove|delete|drop)\b', p, re.I):
        return None
    has_verb = bool(re.search(r'\b(?:add|give\s+me|include|append|insert)\b', p, re.I))
    has_more = bool(re.search(r'\bmore\b', p, re.I))
    if not (has_verb or has_more):
        return None

    cnt = 1
    cnt_m = re.search(r'\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|another|a|an)\b', p, re.I)
    if cnt_m:
        raw = cnt_m.group(1).lower()
        cnt = int(WORD_MAP.get(raw, raw if raw.isdigit() else 1))

    topic = None
    # 1. '... question(s) [based on/about/on/from/in/for/of/related to] <topic>'
    topic_m = re.search(r'(?:questions?|qs?|more)\s+(?:based\s+on|about|on|from|in|for|of|related\s+to)\s+(.+)$', p, re.I)
    if topic_m:
        topic = topic_m.group(1).strip()
    else:
        # 2. 'add [cnt] <topic> question(s)'
        topic_m2 = re.search(r'\b(?:add|give\s+me|include|append|insert)\s+(?:(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|another|a|an)\s+)?(.+?)\s+(?:questions?|qs?)\b', p, re.I)
        if topic_m2:
            candidate = topic_m2.group(1).strip()
            cleaned = re.sub(r'\b(?:more|extra|additional|new)\b', '', candidate, flags=re.I).strip()
            if cleaned.lower() not in STOP_WORDS and not cleaned.isdigit() and len(cleaned) > 1:
                topic = cleaned
        else:
            # 3. 'add 1 more on <topic>' without word question
            topic_m3 = re.search(r'\b(?:more|add)\s+(?:based\s+on|about|on|from|in|for|of|related\s+to)\s+(.+)$', p, re.I)
            if topic_m3:
                topic = topic_m3.group(1).strip()

    if topic:
        topic = re.sub(r'[\.\?!]+$', '', topic).strip()
        if topic.lower() in STOP_WORDS or topic.isdigit() or len(topic) <= 1:
            topic = None

    return {'count': cnt, 'topic': topic}


def _inherit_brief_options(req: DraftReq) -> Dict[str, Any]:
    """Preserve prior hard/source constraints during single-slot swaps, partial swaps, and add-ons
    unless the caller explicitly overrides them in req.options."""
    opts = dict(req.options or {})
    prev_b = req.previous_brief or {}
    prev_filters = prev_b.get("filters") or {}

    if prev_b.get("hard_bias") and "hard_bias" not in opts:
        opts["hard_bias"] = prev_b.get("hard_bias")
    if prev_b.get("format_filter") and "format_filter" not in opts:
        opts["format_filter"] = prev_b.get("format_filter")
    for k in (
        "audience", "school_safe", "evergreen", "no_images",
        "india_focused", "document_ids", "deck_filter", "source_labels",
    ):
        if k in prev_filters and k not in opts:
            opts[k] = prev_filters[k]
    return opts


@router.get("/format-counts")
def format_counts(include_reserve: bool = False, include_example: bool = False, db: Session = Depends(get_db)):
    """How many questions of each format the quiz builder can draw from, and how many questions sit in each slot.
    RESERVE (hidden spares) and EXAMPLE slides are excluded unless asked for."""
    from sqlalchemy import func as _f
    slots = ["LIVE"] + (["RESERVE"] if include_reserve else []) + (["EXAMPLE"] if include_example else [])
    bank = engine._load_bank(db, tuple(slots))
    by_slot = {s or "LIVE": int(n) for s, n in db.query(Question.slot, _f.count(Question.id)).group_by(Question.slot).all()}
    return {
        "counts": engine.format_counts(bank),
        "total": len(bank),
        "slots": {s: by_slot.get(s, 0) for s in engine.ALL_SLOTS},
        "included_slots": slots,
    }


@router.get("/starters")
def starters():
    return {"starters": STARTERS}


@router.post("/draft")
def draft(req: DraftReq, db: Session = Depends(get_db)):
    # 0 questions check:
    if re.search(r"\b(?:0|zero)\s+questions?\b", req.prompt, re.I) or req.count == 0:
        return {
            "brief": {"prompt": req.prompt, "count": 0, "query": ""},
            "questions": [],
            "message": "Cannot create a quiz with 0 questions. Please specify at least 1 question.",
            "bank_size": len(engine._load_bank(db)),
            "stages": ["Interpreter"],
            "fulfillment": {
                "requested": 0,
                "delivered": 0,
                "short": False,
                "note": "Cannot create a quiz with 0 questions."
            }
        }
    # 1. Single slot targeted replacement check:
    # e.g., "replace question 3 with fintech", "change card 10 to tech", "swap question 5", "replace #4 with cricket"
    slot_match = re.search(
        r"\b(?:replace|change|swap)\s+(?:(?:question|slot|card|#)\s*(\d{1,2})|(\d{1,2})(?:st|nd|rd|th)\s+(?:question|slot|card))\b(?:\s+(?:with|for|to)\s+(.*))?",
        req.prompt,
        re.I
    )
    if slot_match and req.current_questions:
        slot_num = int(slot_match.group(1) or slot_match.group(2))
        if 1 <= slot_num <= len(req.current_questions):
            target_q = req.current_questions[slot_num - 1]
            target_id = target_q.get("id")
            other_ids = [q["id"] for i, q in enumerate(req.current_questions) if i != slot_num - 1 and "id" in q]
            sub_topic = (slot_match.group(3) or "").strip()

            swap_prompt = sub_topic if sub_topic else (
                (req.previous_brief or {}).get("query") or req.prompt
            )
            effective_exclude = set(other_ids) | {target_id} | set(req.exclude_ids or [])

            swap_opts = _inherit_brief_options(req)
            new_q = engine.swap_one(db, swap_prompt, target_id, keep_ids=other_ids, options=swap_opts)
            if not new_q:
                sub_res = engine.build_draft(
                    db, swap_prompt, count=1, options=swap_opts, exclude_ids=effective_exclude
                )
                if sub_res.get("questions"):
                    new_q = sub_res["questions"][0]

            if new_q:
                bank = engine._load_bank(db, engine.ALL_SLOTS)
                bank_by_id = {b["id"]: engine._view(b) for b in bank}
                updated_questions = []
                for i, q in enumerate(req.current_questions):
                    if i == slot_num - 1:
                        updated_questions.append(new_q)
                    else:
                        qid = q.get("id")
                        updated_questions.append(bank_by_id.get(qid, q))

                topic_desc = f"with a {sub_topic} question" if sub_topic else "with another question"
                prev_b = req.previous_brief or {"query": swap_prompt, "count": len(updated_questions)}
                return {
                    "brief": prev_b,
                    "questions": updated_questions,
                    "message": f"Replaced question {slot_num} {topic_desc} while keeping the rest of the quiz.",
                    "bank_size": len(bank),
                    "stages": ["Interpreter", "Librarian", "Guardian", "Editor", "Host"],
                    "fulfillment": {
                        "requested": len(updated_questions),
                        "delivered": len(updated_questions),
                        "short": False,
                        "note": f"Replaced question {slot_num}."
                    }
                }

    # 1b. Partial replacement check: e.g. "change 3 questions to tech", "replace 2 with sports", "mix in 3 tech questions"
    partial_m = re.search(
        r"\b(?:change|replace|swap|make|mix\s+in)\s+(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:of\s+them\s+|questions?\s+)?(?:to|with|for|about|into)?\s*([a-z\s]+?)(?:\s+(?:and\s+)?keep\s+the\s+rest)?$",
        req.prompt,
        re.I
    )
    if partial_m and req.current_questions and len(req.current_questions) > 1:
        WORD_MAP = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10}
        raw_cnt = partial_m.group(1).lower()
        cnt = int(WORD_MAP.get(raw_cnt, raw_cnt))
        raw_topic = partial_m.group(2).strip()
        new_topic = re.sub(r"\bquestions?\b", "", raw_topic, flags=re.I).strip() or "general knowledge"

        total_len = len(req.current_questions)
        replace_count = min(cnt, total_len)
        keep_count = total_len - replace_count

        kept_set = set(req.keep_ids or [])
        excluded_set = set(req.exclude_ids or [])

        # Identify which slots to replace:
        replace_indices = []
        # Priority 1: any explicitly excluded/unchecked slots
        for i, q in enumerate(req.current_questions):
            qid = q.get("id")
            if qid in excluded_set and qid not in kept_set:
                replace_indices.append(i)
                if len(replace_indices) == replace_count:
                    break

        # Priority 2: unlocked slots from the end
        if len(replace_indices) < replace_count:
            for i in reversed(range(total_len)):
                qid = req.current_questions[i].get("id")
                if i not in replace_indices and qid not in kept_set:
                    replace_indices.append(i)
                    if len(replace_indices) == replace_count:
                        break

        # If still not enough (e.g. all were locked), take unlocked slots from anywhere
        if len(replace_indices) < replace_count:
            for i in range(total_len):
                if i not in replace_indices:
                    replace_indices.append(i)
                    if len(replace_indices) == replace_count:
                        break

        replace_indices_set = set(replace_indices)
        keep_indices = [i for i in range(total_len) if i not in replace_indices_set]
        kept_qids = {req.current_questions[i].get("id") for i in keep_indices if "id" in req.current_questions[i]}
        effective_exclude = kept_qids | excluded_set

        # Fetch new candidate questions for the replacement slots on new_topic
        partial_opts = _inherit_brief_options(req)
        sub_res = engine.build_draft(
            db, new_topic, count=len(replace_indices), options=partial_opts, exclude_ids=effective_exclude
        )
        fresh_questions = sub_res.get("questions") or []

        bank = engine._load_bank(db, engine.ALL_SLOTS)
        bank_by_id = {b["id"]: engine._view(b) for b in bank}

        updated_questions = [None] * total_len
        # Put kept questions in their original slots
        for idx in keep_indices:
            q = req.current_questions[idx]
            qid = q.get("id")
            updated_questions[idx] = bank_by_id.get(qid, q)

        # Put fresh questions in the replace slots
        fresh_idx = 0
        for idx in replace_indices:
            if fresh_idx < len(fresh_questions):
                updated_questions[idx] = fresh_questions[fresh_idx]
                fresh_idx += 1
            else:
                q = req.current_questions[idx]
                qid = q.get("id")
                updated_questions[idx] = bank_by_id.get(qid, q)

        final_questions = [q for q in updated_questions if q is not None]
        prev_b = req.previous_brief or {"query": new_topic, "count": len(final_questions)}
        return {
            "brief": prev_b,
            "questions": final_questions,
            "message": f"Replaced {len(replace_indices)} questions with {new_topic} while keeping the other {len(keep_indices)} questions.",
            "bank_size": len(bank),
            "stages": ["Interpreter", "Librarian", "Guardian", "Editor", "Host"],
            "fulfillment": {
                "requested": len(final_questions),
                "delivered": len(final_questions),
                "short": False,
                "note": f"Replaced {len(replace_indices)} questions with {new_topic}."
            }
        }

    # 1c. Slot removal check: e.g. "remove question 3", "delete slot 2", "drop card 4"
    del_match = re.search(
        r"\b(?:remove|delete|drop)\s+(?:(?:question|slot|card|#)\s*(\d{1,2})|(\d{1,2})(?:st|nd|rd|th)\s+(?:question|slot|card))\b",
        req.prompt,
        re.I
    )
    if del_match and req.current_questions and len(req.current_questions) > 1:
        slot_num = int(del_match.group(1) or del_match.group(2))
        if 1 <= slot_num <= len(req.current_questions):
            bank = engine._load_bank(db, engine.ALL_SLOTS)
            bank_by_id = {b["id"]: engine._view(b) for b in bank}
            updated_questions = [
                bank_by_id.get(q["id"], q)
                for i, q in enumerate(req.current_questions)
                if i != slot_num - 1 and "id" in q
            ]
            prev_b = dict(req.previous_brief or {"query": "general", "count": len(updated_questions)})
            prev_b["count"] = len(updated_questions)
            return {
                "brief": prev_b,
                "questions": updated_questions,
                "message": f"Removed question {slot_num} while keeping the rest of the quiz.",
                "bank_size": len(bank),
                "stages": ["Interpreter", "Librarian", "Guardian", "Editor", "Host"],
                "fulfillment": {
                    "requested": len(updated_questions),
                    "delivered": len(updated_questions),
                    "short": False,
                    "note": f"Removed question {slot_num}."
                }
            }

    # 1d. Add/append questions intent: e.g. "add 1 question based on bengaluru", "add 2 questions about history", "add 1 more question"
    add_intent = parse_add_intent(req.prompt)
    if add_intent and req.current_questions:
        add_cnt = min(add_intent["count"], 20)
        add_topic = add_intent.get("topic")

        curr_ids = {q["id"] for q in req.current_questions if "id" in q}
        effective_exclude = curr_ids | set(req.exclude_ids or [])

        add_options = _inherit_brief_options(req)
        prev_b = req.previous_brief or {}

        search_prompt = add_topic if add_topic else (prev_b.get("query") or "general knowledge")
        brief_to_pass = None if add_topic else req.previous_brief

        sub_res = engine.build_draft(
            db,
            search_prompt,
            count=add_cnt,
            options=add_options,
            previous_brief=brief_to_pass,
            exclude_ids=effective_exclude,
        )

        fresh_questions = sub_res.get("questions") or []
        bank = engine._load_bank(db, engine.ALL_SLOTS)
        bank_by_id = {b["id"]: engine._view(b) for b in bank}
        existing_qs = [bank_by_id.get(q["id"], q) for q in req.current_questions if "id" in q]

        if not fresh_questions:
            topic_desc = f" matching '{add_topic}'" if add_topic else ""
            return {
                "brief": prev_b or {"query": search_prompt, "count": len(existing_qs)},
                "questions": existing_qs,
                "message": f"Could not find additional questions{topic_desc}. Kept your current {len(existing_qs)} questions.",
                "bank_size": len(bank),
                "stages": ["Interpreter", "Librarian", "Guardian", "Editor", "Host"],
                "fulfillment": {
                    "requested": len(existing_qs) + add_cnt,
                    "delivered": len(existing_qs),
                    "short": True,
                    "note": f"No additional questions found{topic_desc}."
                }
            }

        final_questions = existing_qs + fresh_questions
        new_brief = dict(prev_b or sub_res.get("brief") or {})
        new_brief["count"] = len(final_questions)

        topic_desc = f" based on {add_topic}" if add_topic else ""
        return {
            "brief": new_brief,
            "questions": final_questions,
            "message": f"Added {len(fresh_questions)} question{'s' if len(fresh_questions) != 1 else ''}{topic_desc} while keeping the rest of your quiz.",
            "bank_size": len(bank),
            "stages": ["Interpreter", "Librarian", "Guardian", "Editor", "Host"],
            "fulfillment": {
                "requested": len(final_questions),
                "delivered": len(final_questions),
                "short": len(fresh_questions) < add_cnt,
                "note": f"Added {len(fresh_questions)} question{'s' if len(fresh_questions) != 1 else ''}{topic_desc}."
            }
        }

    # 2. General refinement or new draft:
    effective_exclude = set(req.keep_ids) | set(req.exclude_ids or [])
    res = engine.build_draft(
        db, req.prompt, req.count, req.options, req.previous_brief,
        exclude_ids=effective_exclude,
        current_questions=req.current_questions
    )

    # 3. Merging logic:
    target_count = res["brief"]["count"]
    bank = engine._load_bank(db, engine.ALL_SLOTS)
    bank_by_id = {b["id"]: engine._view(b) for b in bank}
    new_candidates = res["questions"]

    if req.keep_ids:
        # User explicitly locked certain questions
        kept_set = set(req.keep_ids)
        if req.current_questions:
            # Preserve locked questions in their exact slots
            result_slots = [None] * min(len(req.current_questions), target_count)
            cand_idx = 0
            for i, q in enumerate(req.current_questions):
                if i < len(result_slots) and q.get("id") in kept_set:
                    qid = q.get("id")
                    result_slots[i] = bank_by_id.get(qid, q)
            for i in range(len(result_slots)):
                if result_slots[i] is None:
                    if cand_idx < len(new_candidates):
                        result_slots[i] = new_candidates[cand_idx]
                        cand_idx += 1
            combined = [q for q in result_slots if q is not None]
            while len(combined) < target_count and cand_idx < len(new_candidates):
                combined.append(new_candidates[cand_idx])
                cand_idx += 1
        else:
            kept = [bank_by_id[qid] for qid in req.keep_ids if qid in bank_by_id]
            kept_ids = {q["id"] for q in kept}
            remaining = [q for q in new_candidates if q["id"] not in kept_ids]
            combined = (kept + remaining)[:target_count]

        res["questions"] = combined
        kept_len = len([q for q in combined if q["id"] in kept_set])
        replaced_len = len(combined) - kept_len
        res["message"] = f"Refined quiz: kept {kept_len} locked question{'s' if kept_len != 1 else ''} and updated {replaced_len}."

    elif req.previous_brief:
        res["message"] = f"Refined quiz: updated questions based on '{req.prompt}'."

    if "fulfillment" in res:
        res["fulfillment"]["delivered"] = len(res["questions"])
        res["fulfillment"]["short"] = len(res["questions"]) < target_count

    return res


@router.post("/swap")
def swap(req: SwapReq, db: Session = Depends(get_db)):
    q = engine.swap_one(db, req.prompt, req.question_id, req.keep_ids, req.options)
    if not q:
        raise HTTPException(status_code=404, detail="No other matching question found")
    return q


@router.post("/save")
def save(req: SaveReq, db: Session = Depends(get_db)):
    if not req.question_ids:
        raise HTTPException(status_code=400, detail="Must select at least 1 question")
    bank = engine._load_bank(db, engine.ALL_SLOTS)
    bank_by_id = {b["id"]: b for b in bank}
    ordered = [bank_by_id[qid] for qid in req.question_ids if qid in bank_by_id]
    if not ordered:
        raise HTTPException(status_code=404, detail="None of the selected questions exist")

    # Title: explicit > brief.framing > cleaned prompt
    title = (req.title or "").strip()
    if not title:
        framing = ((req.brief or {}).get("framing") or "").strip()
        title = framing if framing else req.prompt[:80]

    topic = (req.brief or {}).get("query") or "General"
    if isinstance(topic, list):
        topic = ", ".join(str(t) for t in topic)

    # The quiz is built from the stored question rows (the prompt-quiz bank view carries only a subset of their
    # fields), so options, explanation and every kind of media come across, as they do for generated quizzes.
    originals = {q.id: q for q in db.query(Question).filter(Question.id.in_([b["id"] for b in ordered])).all()}
    doc_ids = {q.document_id for q in originals.values() if q.document_id}
    doc_titles = {}
    if doc_ids:
        from backend.app.models.document import Document
        doc_titles = {d.id: (d.title or d.filename) for d in db.query(Document).filter(Document.id.in_(doc_ids)).all()}

    quiz = Quiz(
        title=title[:255],
        topic=str(topic)[:100],
        difficulty=str((req.brief or {}).get("hard_bias") or "Medium")[:50],
        raw_prompt=req.prompt,
        question_count=len(ordered),
        status="READY",
    )
    db.add(quiz)
    db.flush()

    for idx, b in enumerate(ordered):
        orig_q = originals.get(b["id"])

        def field(name, default=None, bank_key=None):
            value = getattr(orig_q, name, None) if orig_q is not None else None
            if value in (None, "", []):
                value = b.get(bank_key or name)
            return default if value in (None, "") else value

        gq = GeneratedQuestion(
            quiz_id=quiz.id,
            order_index=idx,
            question_text=field("question_text", ""),
            options=field("options") or [],
            answer=field("answer", ""),
            explanation=field("explanation", ""),
            difficulty=b.get("difficulty") or field("difficulty", "Medium"),
            grade_min=getattr(orig_q, "grade_min", None) or 3,
            grade_max=getattr(orig_q, "grade_max", None) or 12,
            topic=field("topic"),
            question_type=field("question_type", "SLIDE_QA"),
            format_payload=getattr(orig_q, "format_payload", None),
            image_refs=list(getattr(orig_q, "image_refs", None) or []),
            question_images=list(getattr(orig_q, "question_images", None) or []),
            answer_images=list(getattr(orig_q, "answer_images", None) or []),
            audio_refs=list(getattr(orig_q, "audio_refs", None) or []),
            question_audio=list(getattr(orig_q, "question_audio", None) or []),
            answer_audio=list(getattr(orig_q, "answer_audio", None) or []),
            video_refs=list(getattr(orig_q, "video_refs", None) or []),
            question_video=list(getattr(orig_q, "question_videos", None) or []),
            raw_media_refs=list(getattr(orig_q, "raw_media_refs", None) or []),
            visual_clues=getattr(orig_q, "visual_clues", None),
            is_approved=True,
        )
        db.add(gq)
        db.flush()
        if orig_q is not None:
            db.add(RetrievalSource(
                generated_question_id=gq.id,
                historical_question_id=orig_q.id,
                document_id=orig_q.document_id,
                slide_id=orig_q.slide_id,
                document_title=doc_titles.get(orig_q.document_id),
                relevance_score=1.0,
                rationale="Picked from the vault in the Prompt Quizzer",
            ))

    db.commit()
    return {"quiz_id": quiz.id, "title": quiz.title, "count": len(ordered)}
