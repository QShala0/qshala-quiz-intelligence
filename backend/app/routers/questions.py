import asyncio
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func, desc, asc, String
from backend.app.database import get_db
from backend.app.models.question import Question
from backend.app.models.document import Document, Slide
from backend.app.schemas.question import QuestionResponse, QuestionUpdate, DuplicateResolveRequest, ArrangeOrderUpdate
from backend.app.services.retrieval.hybrid_retriever import HybridRetriever

router = APIRouter(prefix="/questions", tags=["Knowledge Base Questions"])


def _enrich(db: Session, responses: List[QuestionResponse]) -> List[QuestionResponse]:
    """Attach the v6 list-row fields (number, topic paths, Repeated) with three batched queries."""
    if not responses:
        return responses
    from backend.app.models.qshala_tag import QshalaQuestionMetadata
    from backend.app.services.ingestion.deck_metadata import question_label
    from backend.app.services.question_meta_service import repeats_for_questions
    from backend.app.services.tagging.topic_paths import paths_for_questions
    ids = [r.id for r in responses]
    metas = {m.question_id: m for m in db.query(QshalaQuestionMetadata)
             .filter(QshalaQuestionMetadata.question_id.in_(ids)).all()}
    paths = paths_for_questions(db, ids)
    reps = repeats_for_questions(db, ids)
    for r in responses:
        m = metas.get(r.id)
        if m is not None:
            r.question_number = m.question_number
            r.question_label = question_label(m.question_number, m.round_number, m.question_in_round) or None
            r.star_marked = bool(m.star_marked)
            if m.question_format:
                r.question_format = m.question_format
        r.topic_paths = paths.get(r.id, [])
        r.repeated = reps.get(r.id, [])
        r.repeated_count = len(r.repeated)

    dup_ids = list({r.duplicate_of_id for r in responses if r.duplicate_of_id and not r.duplicate_of_text})
    if dup_ids:
        orig_map = {
            q[0]: (q[1], q[2]) for q in db.query(Question.id, Question.question_text, Question.answer)
            .filter(Question.id.in_(dup_ids)).all()
        }
        for r in responses:
            if r.duplicate_of_id and r.duplicate_of_id in orig_map:
                txt, ans = orig_map[r.duplicate_of_id]
                r.duplicate_of_text = txt
                r.duplicate_of_answer = ans

    return responses


def _enrich_one(db: Session, response: QuestionResponse) -> QuestionResponse:
    return _enrich(db, [response])[0]

def to_question_response(q: Question, doc: Optional[Document], slide: Optional[Slide]) -> QuestionResponse:
    dup_text = None
    dup_ans = None
    if getattr(q, "duplicate_of", None):
        dup_text = q.duplicate_of.question_text
        dup_ans = q.duplicate_of.answer

    # Resolve video refs: check column or fallback to raw_media_refs with .mp4
    raw_refs = getattr(q, "raw_media_refs", []) or []
    vids = getattr(q, "video_refs", []) or []
    if not vids and raw_refs:
        vids = [r for r in raw_refs if r.endswith((".mp4", ".mov", ".webm", ".m4v")) or "/media/media" in r]
    
    q_vids = getattr(q, "question_videos", []) or []
    if not q_vids and vids:
        q_vids = vids
    ans_vids = getattr(q, "answer_videos", []) or []

    # Filter out placeholder video camera icons from image_refs / question_images
    placeholder_filenames = {
        "slide_41_img_1.png", "slide_40_img_1.png", "slide_49_img_1.png",
        "slide_53_img_1.png", "slide_120_img_1.png", "slide_122_img_1.png",
        "image16.png"
    }
    def _is_placeholder(p: str) -> bool:
        if not p:
            return False
        fname = p.replace("\\", "/").rstrip("/").split("/")[-1].lower()
        return fname in placeholder_filenames

    raw_imgs = getattr(q, "image_refs", []) or []
    clean_imgs = [img for img in raw_imgs if not _is_placeholder(img)]
    raw_q_imgs = getattr(q, "question_images", []) or []
    clean_q_imgs = [img for img in raw_q_imgs if not _is_placeholder(img)]
    raw_ans_imgs = getattr(q, "answer_images", []) or []
    clean_ans_imgs = [img for img in raw_ans_imgs if not _is_placeholder(img)]

    return QuestionResponse(
        id=q.id,
        content_hash=getattr(q, "content_hash", None),
        document_id=q.document_id,
        slide_id=q.slide_id,
        answer_slide_id=q.answer_slide_id,
        question_text=q.question_text,
        answer=q.answer,
        options=q.options,
        explanation=q.explanation,
        topic=q.topic,
        subtopic=q.subtopic,
        topics=getattr(q, "topics", []) or [q.topic],
        tags=getattr(q, "tags", []) or [],
        difficulty=q.difficulty or "Medium",
        difficulty_score=getattr(q, "difficulty_score", 0.50) or 0.50,
        cognitive_level=getattr(q, "cognitive_level", "Recall / Remember") or "Recall / Remember",
        grade_min=q.grade_min or 3,
        grade_max=q.grade_max or 12,
        audience_suitability=getattr(q, "audience_suitability", []) or [],
        question_hook=getattr(q, "question_hook", "DIRECT_TRIVIA") or "DIRECT_TRIVIA",
        curiosity_score=getattr(q, "curiosity_score", 7) or 7,
        temporal_nature=getattr(q, "temporal_nature", "EVERGREEN") or "EVERGREEN",
        occurrence_count=getattr(q, "occurrence_count", 1) or 1,
        provenance_decks=getattr(q, "provenance_decks", []) or [],
        round_number=getattr(q, "round_number", None),
        question_type=q.question_type or "SLIDE_QA",
        question_format=getattr(q, "question_format", None) or getattr(q, "question_type", "STANDARD_QA") or "STANDARD_QA",
        format_data=getattr(q, "format_data", None) or getattr(q, "format_payload", None),
        format_payload=getattr(q, "format_payload", None) or getattr(q, "format_data", None),
        play_mode=getattr(q, "play_mode", None),
        slot=getattr(q, "slot", None) or "LIVE",
        topic_label=getattr(q, "topic_label", None),
        format_needs_review=bool(getattr(q, "format_needs_review", False)),
        image_refs=clean_imgs,
        question_images=clean_q_imgs,
        answer_images=clean_ans_imgs,
        audio_refs=getattr(q, "audio_refs", []) or [],
        question_audio=getattr(q, "question_audio", []) or [],
        answer_audio=getattr(q, "answer_audio", []) or [],
        video_refs=vids,
        question_videos=q_vids,
        question_video=q_vids,
        answer_videos=ans_vids,
        visual_clues=getattr(q, "visual_clues", None),
        audio_transcript=getattr(q, "audio_transcript", None),
        video_transcript=getattr(q, "video_transcript", None),
        raw_media_refs=raw_refs,
        source_slide_range=getattr(q, "source_slide_range", None),
        duplicate_status=getattr(q, "duplicate_status", "UNIQUE") or "UNIQUE",
        duplicate_similarity=getattr(q, "duplicate_similarity", None),
        duplicate_of_id=getattr(q, "duplicate_of_id", None),
        duplicate_of_text=dup_text,
        duplicate_of_answer=dup_ans,
        created_at=q.created_at,
        document_title=doc.title if doc else None,
        slide_number=slide.slide_number if slide else None
    )

@router.get("/topics", response_model=List[Dict[str, Any]])
def list_vault_topics(db: Session = Depends(get_db)):
    """
    Returns all distinct topics currently stored in the Question Vault along with their counts.
    Used for predictive topic autocomplete and topic browsing.
    """
    results = (
        db.query(Question.topic, func.count(Question.id).label("count"))
        .filter(Question.topic.isnot(None))
        .group_by(Question.topic)
        .order_by(desc("count"))
        .all()
    )
    return [{"topic": r[0], "count": r[1]} for r in results]

@router.get("/topics/{topic}/summary", response_model=Dict[str, Any])
def get_topic_summary(topic: str, db: Session = Depends(get_db)):
    """
    Returns deep pedagogical metadata for a given topic in the vault:
    total questions, difficulty breakdown, subtopics, grade coverage, and previews.
    """
    questions = (
        db.query(Question)
        .filter(or_(Question.topic.ilike(f"%{topic}%"), Question.subtopic.ilike(f"%{topic}%")))
        .all()
    )

    if not questions:
        return {
            "topic": topic,
            "total_questions": 0,
            "difficulty_breakdown": {"Easy": 0, "Medium": 0, "Hard": 0},
            "subtopics": [],
            "grade_min": None,
            "grade_max": None,
            "sample_questions": []
        }

    diff_counts = {"Easy": 0, "Medium": 0, "Hard": 0}
    subtopics = set()
    grade_mins = []
    grade_maxs = []

    for q in questions:
        d = q.difficulty or "Medium"
        diff_counts[d] = diff_counts.get(d, 0) + 1
        if q.subtopic:
            subtopics.add(q.subtopic)
        if q.grade_min:
            grade_mins.append(q.grade_min)
        if q.grade_max:
            grade_maxs.append(q.grade_max)

    samples = [
        {"question_text": q.question_text, "difficulty": q.difficulty, "answer": q.answer}
        for q in questions[:3]
    ]

    return {
        "topic": topic,
        "total_questions": len(questions),
        "difficulty_breakdown": diff_counts,
        "subtopics": sorted(list(subtopics)),
        "grade_min": min(grade_mins) if grade_mins else 3,
        "grade_max": max(grade_maxs) if grade_maxs else 12,
        "sample_questions": samples
    }

@router.get("/tags", response_model=List[Dict[str, Any]])
def list_vault_tags(
    topic: Optional[str] = Query(None, description="Optional topic filter"),
    db: Session = Depends(get_db)
):
    """
    Returns an inverted tag index of all unique entity tags in the Question Vault,
    their frequency counts, linked macro-topics, and co-occurring tags.
    """
    topic_str = topic if isinstance(topic, str) and topic.strip() else None
    q_query = db.query(Question.tags, Question.topic)
    if topic_str:
        q_query = q_query.filter(Question.topic.ilike(f"%{topic_str}%"))
    questions = q_query.all()

    tag_counts: Dict[str, int] = {}
    tag_topics: Dict[str, Set[str]] = {}
    tag_co_occur: Dict[str, Dict[str, int]] = {}

    for q in questions:
        q_tags = [t.strip().lstrip("#").strip() for t in (q.tags or []) if isinstance(t, str) and t.strip()]
        for t in q_tags:
            tag_counts[t] = tag_counts.get(t, 0) + 1
            if q.topic:
                tag_topics.setdefault(t, set()).add(q.topic)
            if t not in tag_co_occur:
                tag_co_occur[t] = {}
            for other_t in q_tags:
                if other_t.lower() != t.lower():
                    tag_co_occur[t][other_t] = tag_co_occur[t].get(other_t, 0) + 1

    sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
    results = []
    for t_name, count in sorted_tags:
        co_sorted = sorted(tag_co_occur.get(t_name, {}).items(), key=lambda x: x[1], reverse=True)
        top_co = [c[0] for c in co_sorted[:3]]
        results.append({
            "tag": t_name,
            "count": count,
            "topics": sorted(list(tag_topics.get(t_name, set()))),
            "co_occurring_tags": top_co
        })
    return results

@router.get("/tag-preview", response_model=Dict[str, Any])
def preview_tag_quiz_availability(
    topic: Optional[str] = Query(None),
    tags: Optional[str] = Query(None, description="Comma-separated tags"),
    grade_min: Optional[int] = Query(None),
    grade_max: Optional[int] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Returns live compilation preview based on selected topic and/or concept tags.
    Identifies exact topic+tag matches and cross-topic tag synthesis matches.
    """
    topic_str = topic if isinstance(topic, str) and topic.strip() else None
    tags_str = tags if isinstance(tags, str) and tags.strip() else None
    grade_min_val = grade_min if isinstance(grade_min, int) else None
    grade_max_val = grade_max if isinstance(grade_max, int) else None

    tag_list = [t.strip().lstrip("#").strip().lower() for t in (tags_str.split(",") if tags_str else []) if t.strip()]

    all_questions = db.query(
        Question.id, Question.question_text, Question.answer, Question.difficulty,
        Question.topic, Question.tags, Question.grade_min, Question.grade_max,
    ).all()

    exact_matches = []
    cross_topic_tag_matches = []
    topic_only_matches = []

    for q in all_questions:
        if grade_min_val and q.grade_max and q.grade_max < grade_min_val:
            continue
        if grade_max_val and q.grade_min and q.grade_min > grade_max_val:
            continue

        q_tags_lower = [t.strip().lstrip("#").strip().lower() for t in (q.tags or []) if isinstance(t, str)]
        matches_topic = bool(topic_str and topic_str.lower() in (q.topic or "").lower())
        matches_tags = bool(
            tag_list and any(
                any(t == qt or t in qt or qt in t for t in tag_list)
                for qt in q_tags_lower
            )
        )

        if matches_topic and matches_tags:
            exact_matches.append(q)
        elif matches_tags:
            cross_topic_tag_matches.append(q)
        elif matches_topic:
            topic_only_matches.append(q)

    combined_pool = exact_matches + cross_topic_tag_matches
    if not tag_list:
        combined_pool = topic_only_matches

    diff_counts = {"Easy": 0, "Medium": 0, "Hard": 0}
    for q in combined_pool:
        d = q.difficulty or "Medium"
        diff_counts[d] = diff_counts.get(d, 0) + 1

    sample_previews = [
        {
            "id": q.id,
            "question_text": q.question_text,
            "answer": q.answer,
            "difficulty": q.difficulty,
            "topic": q.topic,
            "tags": q.tags or []
        }
        for q in combined_pool[:3]
    ]

    total = len(combined_pool)
    suggested_mode = "HISTORICAL" if total >= 5 else "NEW"

    return {
        "topic": topic,
        "tags": tag_list,
        "total_available": total,
        "exact_matches_count": len(exact_matches),
        "cross_topic_tag_matches_count": len(cross_topic_tag_matches),
        "difficulty_breakdown": diff_counts,
        "suggested_mode": suggested_mode,
        "sample_questions": sample_previews
    }


@router.get("", response_model=List[QuestionResponse])
async def search_questions(
    query: Optional[str] = Query(None),
    topic: Optional[str] = Query(None),
    subtopic: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    grade_min: Optional[int] = Query(None),
    grade_max: Optional[int] = Query(None),
    difficulty: Optional[str] = Query(None),
    duplicate_status: Optional[str] = Query(None),
    document_id: Optional[str] = Query(None),
    document_title: Optional[str] = Query(None),
    question_format: Optional[str] = Query(None, description="Filter by format: CONNECT_4, STEPPED_CLUE, ARRANGE_ORDER, BUZZER, QA, VISUAL_CLUE, AUDIO_CLUE, VIDEO_CLUE"),
    format: Optional[str] = Query(None, description="Alias for question_format"),
    slot: Optional[str] = Query(None, description="LIVE | RESERVE | EXAMPLE"),
    play_mode: Optional[str] = Query(None, description="BUZZER | DIRECTED | PASS | AUDIENCE | TIEBREAK | ALL_ON_SCREEN | BUFFER"),
    sort_by: Optional[str] = Query("created_at"),
    sort_order: Optional[str] = Query("desc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    # Normalize inputs in case of direct Python function calls
    query_str = query if isinstance(query, str) and query.strip() else None
    topic_str = topic if isinstance(topic, str) and topic.strip() else None
    subtopic_str = subtopic if isinstance(subtopic, str) and subtopic.strip() else None
    tag_str = tag if isinstance(tag, str) and tag.strip() else None
    diff_str = difficulty if isinstance(difficulty, str) and difficulty.strip() else None
    dup_status_str = duplicate_status if isinstance(duplicate_status, str) and duplicate_status.strip() else None
    doc_id_str = document_id if isinstance(document_id, str) and document_id.strip() else None
    doc_title_str = document_title if isinstance(document_title, str) and document_title.strip() else None
    fmt_raw = question_format or format
    fmt_str = fmt_raw.strip().upper() if isinstance(fmt_raw, str) and fmt_raw.strip() else None
    slot_str = slot.strip().upper() if isinstance(slot, str) and slot.strip() else None
    play_mode_str = play_mode.strip().upper() if isinstance(play_mode, str) and play_mode.strip() else None
    grade_min_val = grade_min if isinstance(grade_min, int) else None
    grade_max_val = grade_max if isinstance(grade_max, int) else None
    _allowed_sort = {"created_at", "question_text", "topic", "subtopic", "difficulty",
                     "difficulty_score", "curiosity_score", "grade_min", "grade_max"}
    sort_by_str = sort_by if isinstance(sort_by, str) and sort_by in _allowed_sort else "created_at"
    sort_order_str = sort_order if isinstance(sort_order, str) else "desc"
    limit_int = limit if isinstance(limit, int) else 50
    offset_int = offset if isinstance(offset, int) else 0

    if query_str:
        # 1. Check if query matches a deck / document title directly
        # (Database work in this endpoint runs in a worker thread so one slow query cannot stall the whole server.)
        def _title_matches():
            return [d.id for d in db.query(Document).filter(Document.title.ilike(f"%{query_str}%")).all()]

        doc_ids = await asyncio.to_thread(_title_matches)

        # 2. Perform semantic/hybrid search
        retriever = HybridRetriever(db)
        results = await retriever.search(
            query=query_str,
            topic=topic_str,
            grade_min=grade_min_val,
            grade_max=grade_max_val,
            difficulty=diff_str,
            limit=limit_int
        )
        def _assemble():
            output = []
            seen_q_ids = set()

            # If any document matches the query string by title, prioritize and include its questions
            if doc_ids:
                deck_questions = db.query(Question, Document, Slide).\
                    outerjoin(Document, Question.document_id == Document.id).\
                    outerjoin(Slide, Question.slide_id == Slide.id).\
                    filter(Question.document_id.in_(doc_ids)).limit(limit_int).all()
                for q, doc, slide in deck_questions:
                    if q.id not in seen_q_ids:
                        seen_q_ids.add(q.id)
                        output.append(to_question_response(q, doc, slide))

            for r in results:
                if r["question"].id not in seen_q_ids:
                    seen_q_ids.add(r["question"].id)
                    output.append(to_question_response(r["question"], r["document"], r["slide"]))
            return _enrich(db, output[:limit_int])

        return await asyncio.to_thread(_assemble)

    def _filtered_search():
        # Direct query with rich metadata filters
        q_builder = db.query(Question, Document, Slide).\
            outerjoin(Document, Question.document_id == Document.id).\
            outerjoin(Slide, Question.slide_id == Slide.id)

        if dup_status_str:
            q_builder = q_builder.filter(Question.duplicate_status == dup_status_str)
        if doc_id_str:
            q_builder = q_builder.filter(Question.document_id == doc_id_str)
        if doc_title_str:
            q_builder = q_builder.filter(Document.title.ilike(f"%{doc_title_str}%"))
        if topic_str:
            q_builder = q_builder.filter(
                or_(
                    Question.topic.ilike(f"%{topic_str}%"),
                    Question.subtopic.ilike(f"%{topic_str}%")
                )
            )
        if subtopic_str:
            q_builder = q_builder.filter(Question.subtopic.ilike(f"%{subtopic_str}%"))
        if tag_str:
            clean_tag = tag_str.strip().lstrip("#").strip()
            q_builder = q_builder.filter(func.cast(Question.tags, String).ilike(f"%{clean_tag}%"))
        if diff_str:
            q_builder = q_builder.filter(Question.difficulty.ilike(diff_str))
        if grade_min_val is not None:
            q_builder = q_builder.filter(Question.grade_min >= grade_min_val)
        if grade_max_val is not None:
            q_builder = q_builder.filter(Question.grade_max <= grade_max_val)
        if slot_str:
            q_builder = q_builder.filter(Question.slot == slot_str)
        if play_mode_str:
            q_builder = q_builder.filter(Question.play_mode == play_mode_str)
        if fmt_str:
            target_fmts = [fmt_str]
            if fmt_str in ("CONNECT_4", "CONNECT"):
                target_fmts = ["CONNECT_4", "CONNECT"]
            elif fmt_str in ("STEPPED_CLUE", "THREE_CLUE"):
                target_fmts = ["STEPPED_CLUE", "THREE_CLUE"]
            from backend.app.models.qshala_tag import QshalaQuestionMetadata
            if fmt_str in ("STANDARD_QA", "QA"):
                target_fmts.extend(["STANDARD_QA", "QA", "SLIDE_QA"])
            q_builder = q_builder.outerjoin(
                QshalaQuestionMetadata, Question.id == QshalaQuestionMetadata.question_id
            ).filter(
                or_(
                    Question.question_format.in_(target_fmts),
                    Question.question_type.in_(target_fmts),
                    QshalaQuestionMetadata.question_format.in_(target_fmts)
                )
            )

        # Sorting
        sort_col = getattr(Question, sort_by_str, Question.created_at)
        if sort_order_str == "asc":
            q_builder = q_builder.order_by(asc(sort_col))
        else:
            q_builder = q_builder.order_by(desc(sort_col))

        items = q_builder.offset(offset_int).limit(limit_int).all()

        output = []
        for q, doc, slide in items:
            output.append(to_question_response(q, doc, slide))
        return _enrich(db, output)

    return await asyncio.to_thread(_filtered_search)

@router.get("/duplicates", response_model=List[QuestionResponse])
def list_duplicate_candidates(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Lists all questions flagged with duplicate_status == 'POSSIBLE_DUPLICATE' for human review.
    """
    items = db.query(Question, Document, Slide).\
        outerjoin(Document, Question.document_id == Document.id).\
        outerjoin(Slide, Question.slide_id == Slide.id).\
        filter(Question.duplicate_status == "POSSIBLE_DUPLICATE").\
        order_by(desc(Question.duplicate_similarity)).\
        offset(offset).limit(limit).all()

    return _enrich(db, [to_question_response(q, doc, slide) for q, doc, slide in items])


def _needs_review_ids(db: Session):
    """Determine which questions belong in the review queue vs well-tagged.
    
    A question needs review if:
    1. Truly uncategorized: No QshalaTag at all, or subtopic_l2 is missing/empty/Uncategorized.
    2. Confused / low-confidence: Model marked fit='weak' or fit='none', confidence < 0.65, or chunk failed.
    
    A question is well-tagged (excluded from review) if:
    - It has been reviewed/confirmed by a human (source='HUMAN' or verified_by is set), OR
    - It has a high-confidence, valid canonical taxonomy subtopic tagged by AI.
    """
    from backend.app.models.qshala_tag import QshalaTag
    from sqlalchemy import select, not_, or_, and_, func

    notes = func.coalesce(QshalaTag.verification_notes, "")
    well_tagged = select(QshalaTag.question_id).filter(
        or_(
            and_(
                QshalaTag.status == "confirmed",
                or_(QshalaTag.source == "HUMAN", QshalaTag.verified_by.isnot(None)),
            ),
            and_(
                QshalaTag.subtopic_l2.isnot(None),
                QshalaTag.subtopic_l2 != "",
                not_(QshalaTag.subtopic_l2.in_(["Uncategorized", "None", "Unknown"])),
                or_(
                    QshalaTag.status == "confirmed",
                    and_(
                        QshalaTag.fit == "good",
                        QshalaTag.confidence >= 0.65,
                        ~notes.like("%mismatch%"),
                        ~notes.like("%failed%"),
                    ),
                ),
            ),
        )
    ).scalar_subquery()

    needs_review = select(Question.id).filter(~Question.id.in_(well_tagged)).scalar_subquery()
    return needs_review, well_tagged


def _confirm_format_reading(q: Question) -> bool:
    """Mark a flagged format reading as checked by a person (it leaves the review queue). True if it changed."""
    import copy
    fp = q.format_payload
    ex = fp.get("extraction") if isinstance(fp, dict) else None
    if not isinstance(ex, dict) or not (ex.get("needs_review") or q.format_needs_review):
        return False
    new = copy.deepcopy(fp)
    new["extraction"].update({"needs_review": False, "reviewed": True, "route": "accept"})
    q.format_payload = new
    q.format_needs_review = False
    return True


def _format_flagged_clause():
    """Questions whose format reading was flagged at ingestion (confidence < 0.7, an unreadable Arrange order, a List It
    that does not add up...) and that nobody has confirmed yet."""
    return Question.format_needs_review.is_(True)


@router.get("/needs-review", response_model=List[QuestionResponse])
def list_review_candidates(
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Questions that need human review: truly uncategorized questions or low-confidence/confused tags.
    Well-tagged questions with valid canonical taxonomy subtopics bypass this queue."""
    needs_review, well_tagged = _needs_review_ids(db)
    items = db.query(Question, Document, Slide).\
        outerjoin(Document, Question.document_id == Document.id).\
        outerjoin(Slide, Question.slide_id == Slide.id).\
        filter(or_(and_(Question.id.in_(needs_review), ~Question.id.in_(well_tagged)), _format_flagged_clause())).\
        order_by(desc(Question.created_at)).\
        offset(offset).limit(limit).all()
    return _enrich(db, [to_question_response(q, doc, slide) for q, doc, slide in items])


@router.get("/needs-review/count", response_model=Dict[str, int])
def review_candidates_count(db: Session = Depends(get_db)):
    needs_review, well_tagged = _needs_review_ids(db)
    n = db.query(func.count(Question.id)).filter(
        or_(and_(Question.id.in_(needs_review), ~Question.id.in_(well_tagged)), _format_flagged_clause())
    ).scalar()
    return {"count": int(n or 0)}


@router.post("/{question_id}/accept-review", response_model=QuestionResponse)
def accept_review(question_id: str, db: Session = Depends(get_db)):
    """Confirm a question's draft or uncategorized status.
    Promotes the latest QshalaTag to 'confirmed' with verified_by='quizmaster'
    so it leaves the review queue. If the question was completely untagged,
    creates a confirmed human tag."""
    from datetime import datetime, timezone
    from backend.app.models.qshala_tag import QshalaTag, QshalaTagChange
    item = db.query(Question, Document, Slide).\
        outerjoin(Document, Question.document_id == Document.id).\
        outerjoin(Slide, Question.slide_id == Slide.id).\
        filter(Question.id == question_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Question not found")
    _confirm_format_reading(item[0])

    tag = db.query(QshalaTag).filter(
        QshalaTag.question_id == question_id).\
        order_by(desc(QshalaTag.created_at)).first()
    if tag is not None:
        for prev in db.query(QshalaTag).filter(
                QshalaTag.question_id == question_id, QshalaTag.status == "confirmed").all():
            prev.status = "superseded"
        tag.status = "confirmed"
        tag.verified_by = "quizmaster"
        tag.verified_at = datetime.now(timezone.utc)
        if not tag.subtopic_l2 or tag.subtopic_l2 in ("Uncategorized", "General Knowledge", "None", "Unknown"):
            q, doc, slide = item
            tag.subtopic_l2 = q.subtopic or tag.topic_l1 or q.topic or "General Knowledge"
        if tag.fit in ("weak", "none", None):
            tag.fit = "good"
        if (tag.confidence or 0.0) < 0.70:
            tag.confidence = 0.90
        db.add(QshalaTagChange(question_id=question_id, from_tag_id=None, to_tag_id=tag.id,
                               change_type="PROMOTE", actor="quizmaster", reason="accepted from review"))
        try:
            from backend.app.services.question_meta_service import apply_tag_to_meta
            apply_tag_to_meta(db, question_id, tag)
        except Exception:
            pass
        db.commit()
    else:
        q, doc, slide = item
        topic_l1 = q.topic or "General Knowledge"
        subtopic_l2 = q.subtopic or topic_l1
        tag = QshalaTag(
            question_id=question_id,
            taxonomy_version="v6",
            model_version="manual",
            source="HUMAN",
            topic_l1=topic_l1,
            subtopic_l2=subtopic_l2,
            fit="good",
            confidence=0.90,
            status="confirmed",
            verified_by="quizmaster",
            verified_at=datetime.now(timezone.utc),
            verification_notes="accepted from review"
        )
        db.add(tag)
        try:
            from backend.app.services.question_meta_service import apply_tag_to_meta
            apply_tag_to_meta(db, question_id, tag)
        except Exception:
            pass
        db.commit()

    q, doc, slide = item
    return _enrich_one(db, to_question_response(q, doc, slide))


@router.get("/{question_id}", response_model=QuestionResponse)
def get_question(question_id: str, db: Session = Depends(get_db)):
    item = db.query(Question, Document, Slide).\
        outerjoin(Document, Question.document_id == Document.id).\
        outerjoin(Slide, Question.slide_id == Slide.id).\
        filter(Question.id == question_id).first()

    if not item:
        raise HTTPException(status_code=404, detail="Question not found")

    q, doc, slide = item
    return _enrich_one(db, to_question_response(q, doc, slide))

@router.post("/{question_id}/resolve-duplicate", response_model=QuestionResponse)
def resolve_question_duplicate(
    question_id: str,
    req: DuplicateResolveRequest,
    db: Session = Depends(get_db)
):
    """
    Resolves a question flagged as POSSIBLE_DUPLICATE:
    - CONFIRM_DUPLICATE: marks question as confirmed duplicate and merges provenance with original.
    - DISMISS_UNIQUE: marks question as verified UNIQUE / RESOLVED.
    - MERGE: updates occurrence count and merges source deck references.
    """
    item = db.query(Question, Document, Slide).\
        outerjoin(Document, Question.document_id == Document.id).\
        outerjoin(Slide, Question.slide_id == Slide.id).\
        filter(Question.id == question_id).first()

    if not item:
        raise HTTPException(status_code=404, detail="Question not found")

    q, doc, slide = item
    action = req.action.upper()

    if action in ["CONFIRM_DUPLICATE", "MERGE"]:
        q.duplicate_status = "CONFIRMED_DUPLICATE"
        if q.duplicate_of_id:
            original = db.query(Question).filter(Question.id == q.duplicate_of_id).first()
            if original:
                from backend.app.models.qshala_tag import QshalaQuestionMetadata
                from backend.app.services.question_meta_service import add_repeat, meta_to_dict
                dup_meta = db.query(QshalaQuestionMetadata).filter(QshalaQuestionMetadata.question_id == q.id).first()
                if q.document_id == original.document_id:
                    # Same deck: it is not "found again in another quiz", so just count it and keep the row.
                    original.occurrence_count = (original.occurrence_count or 1) + 1
                else:
                    rep = add_repeat(db, original, q.document_id, meta_to_dict(dup_meta), q.source_slide_range,
                                     match_type="CONFIRMED")
                    if rep is not None:
                        original.occurrence_count = (original.occurrence_count or 1) + 1
                    prov = list(original.provenance_decks or [])
                    if not any(p.get("document_id") == q.document_id for p in prov):
                        prov.append({"document_id": q.document_id, "slide_id": q.slide_id})
                        original.provenance_decks = prov
                    # The question is stored once: the extra copy goes, and the original carries the Repeated entry.
                    db.delete(q)
                    db.commit()
                    o_item = db.query(Question, Document, Slide).\
                        outerjoin(Document, Question.document_id == Document.id).\
                        outerjoin(Slide, Question.slide_id == Slide.id).\
                        filter(Question.id == original.id).first()
                    return _enrich_one(db, to_question_response(*o_item))
    elif action in ["DISMISS_UNIQUE", "RESOLVE"]:
        q.duplicate_status = "RESOLVED"
    else:
        raise HTTPException(status_code=400, detail=f"Unknown resolution action: {req.action}")

    db.commit()
    db.refresh(q)
    return _enrich_one(db, to_question_response(q, doc, slide))

@router.patch("/{question_id}", response_model=QuestionResponse)
def update_question(
    question_id: str,
    payload: QuestionUpdate,
    db: Session = Depends(get_db)
):
    """
    Update tags, topics, or content fields for a question in the Question Vault.
    Provides full curation control to teachers and quizmasters.
    """
    item = db.query(Question, Document, Slide).\
        outerjoin(Document, Question.document_id == Document.id).\
        outerjoin(Slide, Question.slide_id == Slide.id).\
        filter(Question.id == question_id).first()

    if not item:
        raise HTTPException(status_code=404, detail="Question not found")

    q, doc, slide = item

    if payload.tags is not None:
        # Clean, trim, strip leading '#', and deduplicate case-insensitively while preserving order
        cleaned_tags = []
        seen = set()
        for t in payload.tags:
            if not isinstance(t, str):
                continue
            clean = t.strip()
            if clean.startswith("#"):
                clean = clean[1:].strip()
            if clean and clean.lower() not in seen:
                seen.add(clean.lower())
                cleaned_tags.append(clean)
        q.tags = cleaned_tags

    if payload.topics is not None:
        cleaned_topics = []
        seen_topics = set()
        for top in payload.topics:
            if not isinstance(top, str):
                continue
            c = top.strip()
            if c and c.lower() not in seen_topics:
                seen_topics.add(c.lower())
                cleaned_topics.append(c)
        q.topics = cleaned_topics
        if cleaned_topics and not payload.topic:
            q.topic = cleaned_topics[0]

    if payload.topic is not None:
        q.topic = payload.topic.strip()
    if payload.subtopic is not None:
        q.subtopic = payload.subtopic.strip()
    if payload.difficulty is not None:
        q.difficulty = payload.difficulty.strip()
    if payload.question_text is not None:
        q.question_text = payload.question_text.strip()
    if payload.answer is not None:
        q.answer = payload.answer.strip()
    if payload.explanation is not None:
        q.explanation = payload.explanation.strip()
    if payload.visual_clues is not None:
        q.visual_clues = payload.visual_clues.strip() or None
    if payload.slot is not None:
        slot_v = payload.slot.strip().upper()
        if slot_v not in ALLOWED_SLOTS:
            raise HTTPException(status_code=400, detail=f"slot must be one of {sorted(ALLOWED_SLOTS)}")
        q.slot = slot_v
    if payload.play_mode is not None:
        pm = payload.play_mode.strip().upper()
        if pm and pm not in ALLOWED_PLAY_MODES:
            raise HTTPException(status_code=400, detail=f"play_mode must be one of {sorted(ALLOWED_PLAY_MODES)}")
        q.play_mode = pm or None

    db.commit()
    db.refresh(q)
    return _enrich_one(db, to_question_response(q, doc, slide))

ALLOWED_SLOTS = {"LIVE", "RESERVE", "EXAMPLE"}
ALLOWED_PLAY_MODES = {"BUZZER", "DIRECTED", "PASS", "AUDIENCE", "TIEBREAK", "ALL_ON_SCREEN", "BUFFER"}


@router.put("/{question_id}/arrange-order", response_model=QuestionResponse)
def set_arrange_order(question_id: str, body: ArrangeOrderUpdate, db: Session = Depends(get_db)):
    """Enter (or clear) the correct order of an Arrange the Order question by hand.

    The deck never states the order; it is computed only when the reveal values parse. When it could not be, a
    person enters it here (order_source becomes "human") and the question leaves the format review queue."""
    import copy
    item = db.query(Question, Document, Slide).\
        outerjoin(Document, Question.document_id == Document.id).\
        outerjoin(Slide, Question.slide_id == Slide.id).\
        filter(Question.id == question_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Question not found")
    q, doc, slide = item
    fp = q.format_payload
    if (q.question_format or "") != "ARRANGE_ORDER" or not isinstance(fp, dict) or not fp.get("items"):
        raise HTTPException(status_code=400, detail="Not an Arrange the Order question")
    labels = sorted(int(i["label"]) for i in fp["items"])
    order = list(body.correct_order)
    new = copy.deepcopy(fp)
    ex = new.setdefault("extraction", {})
    names = {int(i["label"]): i.get("text") or str(i["label"]) for i in fp["items"]}
    if not order:
        new["correct_order"], new["order_source"] = [], "none"
        ex.update({"needs_review": True, "reviewed": False, "route": "review"})
        q.format_needs_review = True
        q.answer = "Correct order not recorded"
        ordered = [names[l] for l in labels]
    else:
        if sorted(order) != labels:
            raise HTTPException(status_code=400, detail=f"correct_order must list each label exactly once: {labels}")
        new["correct_order"], new["order_source"] = order, "human"
        ex.update({"needs_review": False, "reviewed": True, "route": "accept"})
        q.format_needs_review = False
        reasons = [r for r in (ex.get("reasons") or []) if "order" not in r.lower()]
        ex["reasons"] = reasons + ["order entered by hand"]
        ordered = [names[l] for l in order]
        q.answer = " \u2192 ".join(ordered)
    q.question_text = (str(fp.get("prompt") or "") + " " + ", ".join(ordered)).strip()
    q.format_payload = new
    db.commit()
    db.refresh(q)
    return _enrich_one(db, to_question_response(q, doc, slide))


@router.post("/{question_id}/accept-format-review", response_model=QuestionResponse)
def accept_format_review(question_id: str, db: Session = Depends(get_db)):
    """A person has checked the format reading of a flagged question: it leaves the review queue."""
    item = db.query(Question, Document, Slide).\
        outerjoin(Document, Question.document_id == Document.id).\
        outerjoin(Slide, Question.slide_id == Slide.id).\
        filter(Question.id == question_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Question not found")
    q, doc, slide = item
    if _confirm_format_reading(q):
        db.commit()
        db.refresh(q)
    return _enrich_one(db, to_question_response(q, doc, slide))


@router.post("/{question_id}/tags", response_model=QuestionResponse)
def add_question_tag(
    question_id: str,
    tag: str = Query(..., description="Tag name to append"),
    db: Session = Depends(get_db)
):
    """
    Directly append a new tag to a question.
    """
    item = db.query(Question, Document, Slide).\
        outerjoin(Document, Question.document_id == Document.id).\
        outerjoin(Slide, Question.slide_id == Slide.id).\
        filter(Question.id == question_id).first()

    if not item:
        raise HTTPException(status_code=404, detail="Question not found")

    q, doc, slide = item
    current_tags = list(q.tags or [])
    clean_tag = tag.strip().lstrip("#").strip()
    if clean_tag and not any(clean_tag.lower() == t.lower() for t in current_tags):
        current_tags.append(clean_tag)
        q.tags = current_tags
        db.commit()
        db.refresh(q)

    return _enrich_one(db, to_question_response(q, doc, slide))

@router.delete("/{question_id}/tags/{tag_name}", response_model=QuestionResponse)
def delete_question_tag(
    question_id: str,
    tag_name: str,
    db: Session = Depends(get_db)
):
    """
    Delete a specific tag from a question.
    """
    item = db.query(Question, Document, Slide).\
        outerjoin(Document, Question.document_id == Document.id).\
        outerjoin(Slide, Question.slide_id == Slide.id).\
        filter(Question.id == question_id).first()

    if not item:
        raise HTTPException(status_code=404, detail="Question not found")

    q, doc, slide = item
    current_tags = list(q.tags or [])
    clean_target = tag_name.strip().lstrip("#").strip().lower()
    updated_tags = [t for t in current_tags if t.strip().lstrip("#").strip().lower() != clean_target]
    q.tags = updated_tags
    db.commit()
    db.refresh(q)
    return _enrich_one(db, to_question_response(q, doc, slide))



@router.delete("", response_model=dict)
def delete_all_questions_endpoint(
    document_id: Optional[str] = None,
    all: bool = False,
    db: Session = Depends(get_db)
):
    """
    Remove questions from the question bank.
    If document_id is specified, removes all questions from that deck.
    Purging the whole bank requires an explicit all=true: a bare DELETE with no parameters is refused.
    """
    from backend.app.models.qshala_tag import QshalaTag, QshalaTagChange, QshalaQuestionMetadata
    from backend.app.models.question_meta import QuestionTopicPath, QuestionEditHistory, QuestionRepeat

    if not document_id and not all:
        raise HTTPException(
            status_code=400,
            detail="Refusing to delete the whole question bank: pass document_id=<deck> to remove one deck, "
                   "or all=true to purge everything.",
        )

    q_query = db.query(Question)
    if document_id:
        q_query = q_query.filter(Question.document_id == document_id)
    
    question_ids = [q.id for q in q_query.all()]
    if not question_ids:
        return {"status": "ok", "deleted_count": 0, "message": "No matching questions found"}

    # Cascade deletions
    db.query(QshalaQuestionMetadata).filter(QshalaQuestionMetadata.question_id.in_(question_ids)).delete(synchronize_session=False)
    db.query(QuestionTopicPath).filter(QuestionTopicPath.question_id.in_(question_ids)).delete(synchronize_session=False)
    db.query(QuestionEditHistory).filter(QuestionEditHistory.question_id.in_(question_ids)).delete(synchronize_session=False)
    db.query(QshalaTag).filter(QshalaTag.question_id.in_(question_ids)).delete(synchronize_session=False)
    db.query(QshalaTagChange).filter(QshalaTagChange.question_id.in_(question_ids)).delete(synchronize_session=False)
    db.query(QuestionRepeat).filter(QuestionRepeat.question_id.in_(question_ids)).delete(synchronize_session=False)
    
    deleted_n = db.query(Question).filter(Question.id.in_(question_ids)).delete(synchronize_session=False)
    db.commit()

    return {
        "status": "ok",
        "deleted_count": deleted_n,
        "message": f"Successfully removed {deleted_n} questions from the question bank"
    }


@router.delete("/{question_id}", response_model=dict)
def delete_question_endpoint(
    question_id: str,
    db: Session = Depends(get_db)
):
    """Delete a single question from the question bank."""
    from backend.app.models.qshala_tag import QshalaTag, QshalaTagChange, QshalaQuestionMetadata
    from backend.app.models.question_meta import QuestionTopicPath, QuestionEditHistory, QuestionRepeat

    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    db.query(QshalaQuestionMetadata).filter(QshalaQuestionMetadata.question_id == question_id).delete(synchronize_session=False)
    db.query(QuestionTopicPath).filter(QuestionTopicPath.question_id == question_id).delete(synchronize_session=False)
    db.query(QuestionEditHistory).filter(QuestionEditHistory.question_id == question_id).delete(synchronize_session=False)
    db.query(QshalaTag).filter(QshalaTag.question_id == question_id).delete(synchronize_session=False)
    db.query(QshalaTagChange).filter(QshalaTagChange.question_id == question_id).delete(synchronize_session=False)
    db.query(QuestionRepeat).filter(QuestionRepeat.question_id == question_id).delete(synchronize_session=False)

    db.delete(q)
    db.commit()

    return {"status": "ok", "message": f"Question {question_id} deleted successfully"}
