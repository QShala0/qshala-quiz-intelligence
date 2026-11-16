import asyncio
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional, Dict, Any
from urllib.parse import unquote
from fastapi import APIRouter, Depends, Query, HTTPException, Response, UploadFile, File
from sqlalchemy.orm import Session, selectinload, defer, load_only
from sqlalchemy import or_, and_, func, desc, asc, String
from backend.app.database import get_db
from backend.app.models.question import Question
from backend.app.models.document import Document, Slide
from backend.app.schemas.question import QuestionResponse, QuestionUpdate, DuplicateResolveRequest, ArrangeOrderUpdate
from backend.app.services.retrieval.hybrid_retriever import HybridRetriever, apply_question_scope_filters, apply_keyword_filter
from backend.app.services import media_cleanup

logger = logging.getLogger(__name__)
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

# The search box's deck-name resolver needs every deck's title and every topic word. They change only when a deck is
# ingested, edited or removed, so they are kept for a short while instead of being re-read on every search. Each
# container keeps its own copy; invalidate_search_scope_cache() (called with the stats cache) empties this one.
SEARCH_SCOPE_CACHE_SECONDS: float = float(os.getenv("SEARCH_SCOPE_CACHE_SECONDS", "60"))
_scope_cache: Dict[str, Any] = {"at": 0.0, "docs": None, "topic_words": None}
_scope_lock = threading.Lock()


def invalidate_search_scope_cache() -> None:
    with _scope_lock:
        _scope_cache.update({"at": 0.0, "docs": None, "topic_words": None})


def _search_scope_lists(db: Session):
    """(all decks as id/title/filename dicts, set of topic and subtopic words), from the cache when fresh."""
    now = time.monotonic()
    with _scope_lock:
        if (SEARCH_SCOPE_CACHE_SECONDS > 0 and _scope_cache["docs"] is not None
                and now - _scope_cache["at"] < SEARCH_SCOPE_CACHE_SECONDS):
            return _scope_cache["docs"], _scope_cache["topic_words"]
    from backend.app.services.prompt_quiz.retrieval import tokenize as pq_tokenize
    docs = [{"id": d.id, "title": d.title or "", "filename": d.filename or ""}
            for d in db.query(Document.id, Document.title, Document.filename).all()]
    topic_words = set()
    for t_val, st_val in db.query(Question.topic, Question.subtopic).distinct().all():
        topic_words.update(pq_tokenize(t_val or ""))
        topic_words.update(pq_tokenize(st_val or ""))
    with _scope_lock:
        _scope_cache.update({"at": time.monotonic(), "docs": docs, "topic_words": topic_words})
    return docs, topic_words


def to_question_response(q: Question, doc: Optional[Document], slide: Optional[Slide]) -> QuestionResponse:
    dup_text = None
    dup_ans = None
    # Only use the duplicate's row when it was loaded with the question. Touching an unloaded relationship here would
    # run one extra query per row; _enrich fills the text for the whole page in one query instead.
    dup_row = q.__dict__.get("duplicate_of")
    if dup_row is not None:
        dup_text = dup_row.question_text
        dup_ans = dup_row.answer

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
    placeholder_filenames = media_cleanup.PLACEHOLDER_FILENAMES
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
    document_ids: Optional[str] = Query(None, description="Comma-separated document IDs for multi-deck or series filtering"),
    document_title: Optional[str] = Query(None),
    question_format: Optional[str] = Query(None, description="Filter by format: CONNECT_4, STEPPED_CLUE, ARRANGE_ORDER, BUZZER, QA, VISUAL_CLUE, AUDIO_CLUE, VIDEO_CLUE"),
    format: Optional[str] = Query(None, description="Alias for question_format"),
    slot: Optional[str] = Query(None, description="LIVE | RESERVE | EXAMPLE"),
    play_mode: Optional[str] = Query(None, description="BUZZER | DIRECTED | PASS | AUDIENCE | TIEBREAK | ALL_ON_SCREEN | BUFFER"),
    match: Optional[str] = Query("exact", description="exact: every typed word must appear in the question (default). smart: semantic ranking with synonyms"),
    sort_by: Optional[str] = Query("created_at"),
    sort_order: Optional[str] = Query("desc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    response: Response = None,
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
    doc_ids_list: List[str] = []
    if doc_id_str:
        doc_ids_list.append(doc_id_str)
    if isinstance(document_ids, str) and document_ids.strip():
        for part in document_ids.split(","):
            did = part.strip()
            if did and did not in doc_ids_list:
                doc_ids_list.append(did)
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

    page_info: Dict[str, Any] = {}

    def _send_total() -> None:
        """Expose how many rows match the filters (not just this page) so the UI can page and show a total. The body
        stays a plain list. Only the filtered path knows the exact total; the semantic-search path sends none."""
        if response is not None and page_info.get("total") is not None:
            response.headers["X-Total-Count"] = str(page_info["total"])

    smart_match = isinstance(match, str) and match.strip().lower() == "smart"

    def _filtered_search(override_doc_ids: Optional[List[str]] = None, keywords: Optional[str] = None):
        target_doc_ids = override_doc_ids if override_doc_ids is not None else doc_ids_list
        # A list row only shows the deck title and slide number, and never the vector, so only those are loaded
        # (the deck's progress log and the slide's text and vector made each row about 5 KB). The duplicate's text
        # is filled for the whole page by _enrich in one query.
        q_builder = db.query(Question, Document, Slide).\
            options(defer(Question.embedding),
                    load_only(Document.id, Document.title),
                    load_only(Slide.id, Slide.slide_number)).\
            outerjoin(Document, Question.document_id == Document.id).\
            outerjoin(Slide, Question.slide_id == Slide.id)

        q_builder = apply_question_scope_filters(
            q_builder,
            document_ids=target_doc_ids or None,
            document_title=doc_title_str,
            duplicate_status=dup_status_str,
            subtopic=subtopic_str,
            tag=tag_str,
            difficulty=diff_str,
            slot=slot_str,
            play_mode=play_mode_str,
            question_format=fmt_str,
        )
        if keywords:
            q_builder = apply_keyword_filter(q_builder, keywords)
        if topic_str:
            q_builder = q_builder.filter(
                or_(
                    Question.topic.ilike(f"%{topic_str}%"),
                    Question.subtopic.ilike(f"%{topic_str}%")
                )
            )
        if grade_min_val is not None:
            q_builder = q_builder.filter(Question.grade_min >= grade_min_val)
        if grade_max_val is not None:
            q_builder = q_builder.filter(Question.grade_max <= grade_max_val)

        page_info["total"] = int(q_builder.with_entities(func.count(Question.id)).order_by(None).scalar() or 0)

        # Sorting (id breaks ties so a row can never appear on two pages or be skipped between them)
        sort_col = getattr(Question, sort_by_str, Question.created_at)
        if sort_order_str == "asc":
            q_builder = q_builder.order_by(asc(sort_col), asc(Question.id))
        else:
            q_builder = q_builder.order_by(desc(sort_col), asc(Question.id))

        items = q_builder.offset(offset_int).limit(limit_int).all()

        output = []
        for q, doc, slide in items:
            output.append(to_question_response(q, doc, slide))
        return _enrich(db, output)

    if query_str:
        # 1. Resolve explicit or fuzzy deck/source references in query_str (e.g. "Agastya", "aghtya", "aghtya radio")
        def _resolve_source_scope():
            from backend.app.services.source_grouping import resolve_deck_query
            from backend.app.services.prompt_quiz.retrieval import SYNONYMS, tokenize as pq_tokenize
            from backend.app.services.prompt_quiz.interpreter_llm import V6_THEMES

            all_docs, topic_words = _search_scope_lists(db)
            if doc_ids_list:
                wanted = set(doc_ids_list)
                docs_rows = [d for d in all_docs if d["id"] in wanted]
            else:
                docs_rows = list(all_docs)
            if not docs_rows:
                return None

            extra_excl = set(SYNONYMS.keys()) | {w for t in V6_THEMES for w in pq_tokenize(t)}
            # Don't hijack single-word global searches when the word is an actual Question topic/subtopic outside matched decks

            resolved = resolve_deck_query(
                query_str,
                docs_rows,
                extra_excluded=(extra_excl | (topic_words if not doc_ids_list else set())),
                is_prompt=False,
            )
            return resolved

        resolved_scope = await asyncio.to_thread(_resolve_source_scope)
        effective_doc_ids = list(doc_ids_list)
        effective_query = query_str

        if resolved_scope and resolved_scope["doc_ids"]:
            effective_doc_ids = resolved_scope["doc_ids"]
            effective_query = resolved_scope["remaining_query"]
            # Pure source/deck lookup (e.g. "Agastya" or "aghtya" or "Agastya Set 2 Prelims") -> strict SQL listing
            if not effective_query:
                _res = await asyncio.to_thread(lambda: _filtered_search(override_doc_ids=effective_doc_ids))
                _send_total()
                return _res

        if not smart_match:
            # Exact words: only questions that actually contain every typed word, paged with a true total.
            _res = await asyncio.to_thread(
                lambda: _filtered_search(override_doc_ids=effective_doc_ids, keywords=effective_query))
            _send_total()
            return _res

        # 2. Perform semantic/hybrid search strictly scoped to active deck/source and vault filters
        retriever = HybridRetriever(db)
        has_hard_scope = bool(
            effective_doc_ids or doc_title_str or dup_status_str or subtopic_str
            or tag_str or diff_str or slot_str or play_mode_str or fmt_str
        )
        results = await retriever.search(
            query=effective_query,
            topic=topic_str,
            tags=[tag_str] if tag_str else None,
            grade_min=grade_min_val,
            grade_max=grade_max_val,
            difficulty=diff_str,
            limit=limit_int,
            document_ids=effective_doc_ids or None,
            document_title=doc_title_str,
            subtopic=subtopic_str,
            duplicate_status=dup_status_str,
            slot=slot_str,
            play_mode=play_mode_str,
            question_format=fmt_str,
            difficulty_strict=bool(diff_str),
            require_tag_match=bool(tag_str),
            allow_relax=not has_hard_scope,
        )

        def _assemble():
            output = []
            seen_q_ids = set()
            for r in results:
                if r["question"].id not in seen_q_ids:
                    seen_q_ids.add(r["question"].id)
                    output.append(to_question_response(r["question"], r["document"], r["slide"]))
            return _enrich(db, output[:limit_int])

        return await asyncio.to_thread(_assemble)

    _res = await asyncio.to_thread(_filtered_search)
    _send_total()
    return _res

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
        options(selectinload(Question.duplicate_of)).\
        outerjoin(Document, Question.document_id == Document.id).\
        outerjoin(Slide, Question.slide_id == Slide.id).\
        filter(Question.duplicate_status == "POSSIBLE_DUPLICATE").\
        order_by(desc(Question.duplicate_similarity)).\
        offset(offset).limit(limit).all()

    return _enrich(db, [to_question_response(q, doc, slide) for q, doc, slide in items])


@router.get("/duplicates/count", response_model=Dict[str, int])
def duplicate_candidates_count(db: Session = Depends(get_db)):
    """Count questions flagged with duplicate_status == 'POSSIBLE_DUPLICATE' in one query."""
    n = db.query(func.count(Question.id)).filter(
        Question.duplicate_status == "POSSIBLE_DUPLICATE"
    ).scalar()
    return {"count": int(n or 0)}


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
        options(selectinload(Question.duplicate_of)).\
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

    touches_images = (payload.question_images is not None or payload.answer_images is not None
                      or payload.clue_images is not None)
    before_urls = _picture_urls(q) if touches_images else set()
    if touches_images:
        if payload.clue_images is not None:
            # Connect: these ARE the question's numbered pictures, so they are written into the question itself
            # (format_payload items, renumbered 1..n in this order) as well as the flat list below.
            clue = _clean_image_urls(payload.clue_images)
            _set_connect_items(q, clue)
            q_imgs = clue
        elif payload.question_images is not None:
            q_imgs = _clean_image_urls(payload.question_images)
        else:
            q_imgs = list(q.question_images or [])
        a_imgs = _clean_image_urls(payload.answer_images) if payload.answer_images is not None else list(q.answer_images or [])
        a_imgs = [u for u in a_imgs if u not in q_imgs]     # a picture has one role
        q.question_images = q_imgs
        q.answer_images = a_imgs
        # image_refs is the catch-all (question + answer pictures). Keep it in step so a removed picture cannot come
        # back through the display fallback; with no question pictures it is left empty, as that fallback would
        # otherwise show the answer pictures as question pictures.
        q.image_refs = list(dict.fromkeys(q_imgs + a_imgs)) if q_imgs else []

    db.commit()
    db.refresh(q)
    response = _enrich_one(db, to_question_response(q, doc, slide))
    if touches_images:
        # Pictures taken off this question are deleted from storage too, unless anything else still uses them
        # (see services/media_cleanup.py). Runs after the commit; a failure there never fails the save.
        removed = before_urls - _picture_urls(q)
        if removed:
            media_cleanup.delete_unreferenced(db, removed)
    return response

MAX_IMAGES_PER_QUESTION = 50


def _picture_urls(q: Question) -> set:
    """Every picture URL this question points at: the flat lists plus a Connect question's numbered items."""
    urls = set()
    for col in ("question_images", "answer_images", "image_refs"):
        urls.update(u for u in (getattr(q, col, None) or []) if isinstance(u, str) and u)
    payload = getattr(q, "format_payload", None)
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        urls.update(it["image"] for it in payload["items"] if isinstance(it, dict) and isinstance(it.get("image"), str) and it["image"])
    return urls


def _set_connect_items(q: Question, urls: List[str]) -> None:
    """Replace a Connect question's numbered pictures. Items keep any extra fields they had; order is renumbered 1..n.
    Everything else in the payload (hint, speaker-note labels, scoring, answer) is left exactly as it was."""
    payload = q.format_payload
    if not (isinstance(payload, dict) and payload.get("type") == "CONNECT" and isinstance(payload.get("items"), list)):
        raise HTTPException(status_code=400, detail="clue_images can only be set on a Connect question")
    old = {it.get("image"): it for it in payload["items"] if isinstance(it, dict)}
    items = [{**old.get(u, {}), "order": i, "image": u} for i, u in enumerate(urls, start=1)]
    q.format_payload = {**payload, "items": items}


def _clean_image_urls(urls: List[str]) -> List[str]:
    """Trim, de-duplicate (order kept) and validate image URLs: http(s) or a server path such as /static/slides/..."""
    out: List[str] = []
    for u in urls:
        if not isinstance(u, str):
            raise HTTPException(status_code=400, detail="Image entries must be strings")
        u = u.strip()
        if not u:
            continue
        ok = u.lower().startswith(("http://", "https://")) or (u.startswith("/") and not u.startswith("//") and ".." not in u)
        if not ok:
            raise HTTPException(status_code=400, detail=f"Not an allowed image URL: {u[:80]}")
        if u not in out:
            out.append(u)
    if len(out) > MAX_IMAGES_PER_QUESTION:
        raise HTTPException(status_code=400, detail=f"At most {MAX_IMAGES_PER_QUESTION} images per list")
    return out


def _sniff_image(data: bytes):
    """(extension, content type) from the file's own bytes, never from the filename or the client's header."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def _max_image_bytes() -> int:
    try:
        return int(os.getenv("QUESTION_IMAGE_MAX_BYTES", str(4 * 1024 * 1024)))
    except ValueError:
        return 4 * 1024 * 1024


@router.post("/{question_id}/images")
def upload_question_images(question_id: str, files: List[UploadFile] = File(...), db: Session = Depends(get_db)):
    """Store new pictures for a question and return their URLs. Does not attach them: the caller saves the list it
    wants with PATCH /questions/{id}. Nothing here touches the AI, tags or embeddings."""
    if not db.query(Question.id).filter(Question.id == question_id).first():
        raise HTTPException(status_code=404, detail="Question not found")
    try:
        max_files = int(os.getenv("QUESTION_IMAGE_MAX_FILES", "10"))
    except ValueError:
        max_files = 10
    if not files or len(files) > max_files:
        raise HTTPException(status_code=400, detail=f"Send between 1 and {max_files} images at a time")
    limit = _max_image_bytes()

    staged = []
    for f in files:                                    # validate everything first, so a bad file stores nothing
        data = f.file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(status_code=413, detail=f"{f.filename or 'image'} is larger than {limit // (1024 * 1024)} MB")
        kind = _sniff_image(data)
        if kind is None:
            raise HTTPException(status_code=400, detail=f"{f.filename or 'file'} is not a PNG, JPEG or WebP image")
        staged.append((data, kind))

    from backend.app.config import settings
    from backend.app.services.storage.r2_storage import get_media_storage
    r2 = get_media_storage()
    urls: List[str] = []
    for data, (ext, ctype) in staged:
        name = f"{uuid.uuid4().hex}.{ext}"
        if r2 is not None:
            try:
                urls.append(r2.upload_bytes(f"questions/{question_id}/{name}", data, ctype))
                continue
            except Exception as e:  # fall back to local disk, like the rest of the media code
                logger.warning("[question-images] R2 upload failed, using local disk: %s", e)
        dest_dir = settings.SLIDES_DIR / "questions" / question_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / name).write_bytes(data)
        urls.append(f"/static/slides/questions/{question_id}/{name}")
    return {"urls": urls}


def _source_max_bytes() -> int:
    try:
        return int(os.getenv("QUESTION_IMAGE_SOURCE_MAX_BYTES", str(25 * 1024 * 1024)))
    except ValueError:
        return 25 * 1024 * 1024


def _local_media_path(url: str):
    """The file on this server's disk for a /static/... picture path, or None. Never leaves the media folders."""
    from backend.app.config import settings
    path = url.split("?")[0]
    if path.startswith("/api/v1/static/"):
        path = path[len("/api/v1"):]
    for prefix, root in (("/static/slides/", settings.SLIDES_DIR), ("/static/", settings.STORAGE_DIR)):
        if path.startswith(prefix):
            root = Path(root).resolve()
            cand = (root / unquote(path[len(prefix):])).resolve()
            try:
                cand.relative_to(root)
            except ValueError:
                return None
            return cand if cand.is_file() else None
    return None


@router.get("/{question_id}/image-source")
def question_image_source(question_id: str, url: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    """The bytes of one of THIS question's pictures, served from the API's own address. The picture editor needs them:
    a browser refuses to export a canvas that drew a picture from another site (the R2 CDN), so the editor loads the
    picture through here instead. Only URLs the question itself lists are served, so this cannot fetch anything else."""
    q = db.query(Question).filter(Question.id == question_id).first()
    if q is None:
        raise HTTPException(status_code=404, detail="Question not found")
    if url not in _picture_urls(q):
        raise HTTPException(status_code=400, detail="That picture is not on this question")
    limit = _source_max_bytes()
    data: Optional[bytes] = None

    local = _local_media_path(url) if url.startswith("/") else None
    if local is not None:
        data = local.read_bytes()[: limit + 1]
    if data is None:
        from backend.app.services.storage.r2_storage import get_media_storage
        r2 = get_media_storage()
        base = getattr(r2, "public_base_url", "") if r2 is not None else ""
        key = None
        if base and url.startswith(base + "/"):
            key = unquote(url[len(base) + 1:].split("?")[0])
        elif url.startswith("/static/") and r2 is not None:
            key = media_cleanup.storage_key(url)
        if key:
            try:
                data = r2.download(key)
            except Exception as e:
                logger.warning("[image-source] R2 read failed for %s: %s", key, type(e).__name__)
    if data is None and url.startswith(("http://", "https://")):
        import httpx
        try:
            with httpx.Client(timeout=20.0, follow_redirects=True,
                              headers={"User-Agent": "Mozilla/5.0 (compatible; QShalaImageEditor/1.0)"}) as http:
                with http.stream("GET", url) as resp:
                    resp.raise_for_status()
                    buf = bytearray()
                    for chunk in resp.iter_bytes():
                        buf.extend(chunk)
                        if len(buf) > limit:
                            break
                    data = bytes(buf)
        except Exception as e:
            logger.warning("[image-source] download failed: %s", type(e).__name__)
    if data is None:
        raise HTTPException(status_code=404, detail="The picture file could not be found")
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"The picture is larger than {limit // (1024 * 1024)} MB")
    kind = _sniff_image(data)
    if kind is None:
        raise HTTPException(status_code=415, detail="Only PNG, JPEG or WebP pictures can be edited")
    return Response(content=data, media_type=kind[1], headers={"Cache-Control": "private, max-age=300"})


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

    # Keep a recoverable copy first (same transaction: if the delete fails, the copy is rolled back with it).
    from backend.app.services import trash as _trash
    trash_id = _trash.snapshot(db, "deck" if document_id else "all_questions",
                               f"questions of deck {document_id}" if document_id else "entire question bank", document_id)

    # Cascade deletions
    db.query(QshalaQuestionMetadata).filter(QshalaQuestionMetadata.question_id.in_(question_ids)).delete(synchronize_session=False)
    db.query(QuestionTopicPath).filter(QuestionTopicPath.question_id.in_(question_ids)).delete(synchronize_session=False)
    db.query(QuestionEditHistory).filter(QuestionEditHistory.question_id.in_(question_ids)).delete(synchronize_session=False)
    db.query(QshalaTag).filter(QshalaTag.question_id.in_(question_ids)).delete(synchronize_session=False)
    db.query(QshalaTagChange).filter(QshalaTagChange.question_id.in_(question_ids)).delete(synchronize_session=False)
    db.query(QuestionRepeat).filter(QuestionRepeat.question_id.in_(question_ids)).delete(synchronize_session=False)
    
    deleted_n = db.query(Question).filter(Question.id.in_(question_ids)).delete(synchronize_session=False)
    db.commit()
    try:
        _trash.purge_expired(db)
        db.commit()
    except Exception:
        db.rollback()

    return {
        "status": "ok",
        "deleted_count": deleted_n,
        "trash_id": trash_id,
        "message": f"Successfully removed {deleted_n} questions from the question bank. "
                   f"Undo within {_trash.retention_days()} days with POST /trash/{trash_id}/restore"
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
