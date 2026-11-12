import time
from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, select, literal, union_all, cast, String, Integer, desc
from backend.app.database import get_db
from backend.app.models.document import Document, Slide
from backend.app.models.question import Question
from backend.app.models.quiz import Quiz
from backend.app.schemas.stats import DashboardStatsResponse

router = APIRouter(prefix="/stats", tags=["Dashboard Stats"])

_stats_cache: Optional[DashboardStatsResponse] = None
_stats_cached_at: float = 0.0
_STATS_TTL_SECONDS: float = 45.0  # 30-60s TTL cache

def invalidate_stats_cache() -> None:
    """Invalidates the process-local stats cache (e.g. when a deck finishes ingesting)."""
    global _stats_cache, _stats_cached_at
    _stats_cache = None
    _stats_cached_at = 0.0

@router.get("", response_model=DashboardStatsResponse)
def get_stats(db: Session = Depends(get_db)):
    global _stats_cache, _stats_cached_at
    now = time.monotonic()
    if _stats_cache is not None and (now - _stats_cached_at) < _STATS_TTL_SECONDS:
        return _stats_cache

    # 1. Combined scalar counts (1 query)
    counts_stmt = select(
        select(func.count(Document.id)).scalar_subquery().label("doc_count"),
        select(func.count(Slide.id)).scalar_subquery().label("slide_count"),
        select(func.count(Question.id)).scalar_subquery().label("question_count"),
        select(func.count(Quiz.id)).scalar_subquery().label("quiz_count"),
        select(func.count(func.distinct(Question.topic))).scalar_subquery().label("distinct_topics"),
    )
    counts = db.execute(counts_stmt).first()

    # 2. Combined recent documents and recent quizzes (1 query via typed union)
    doc_sub = select(
        literal("doc").label("kind"),
        Document.id.label("id"),
        Document.title.label("title"),
        Document.filename.label("col_str1"),
        Document.processing_status.label("col_str2"),
        cast(literal(None), String).label("col_str3"),
        Document.slide_count.label("col_int1"),
        Document.question_count.label("col_int2"),
        cast(literal(None), Integer).label("col_int3"),
        cast(literal(None), Integer).label("col_int4"),
        Document.created_at.label("created_at"),
    ).order_by(Document.created_at.desc()).limit(5).subquery()

    quiz_sub = select(
        literal("quiz").label("kind"),
        Quiz.id.label("id"),
        Quiz.title.label("title"),
        Quiz.topic.label("col_str1"),
        Quiz.difficulty.label("col_str2"),
        Quiz.audience_type.label("col_str3"),
        cast(literal(0), Integer).label("col_int1"),
        Quiz.question_count.label("col_int2"),
        Quiz.grade_min.label("col_int3"),
        Quiz.grade_max.label("col_int4"),
        Quiz.created_at.label("created_at"),
    ).order_by(Quiz.created_at.desc()).limit(5).subquery()

    recent_rows = db.execute(union_all(select(doc_sub), select(quiz_sub))).all()

    # Preserve order by created_at desc within each group
    doc_rows = sorted([r for r in recent_rows if r.kind == "doc"], key=lambda r: r.created_at or "", reverse=True)
    quiz_rows = sorted([r for r in recent_rows if r.kind == "quiz"], key=lambda r: r.created_at or "", reverse=True)

    recent_documents = [
        {
            "id": r.id,
            "title": r.title,
            "filename": r.col_str1,
            "slide_count": r.col_int1,
            "question_count": r.col_int2,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "status": r.col_str2,
        }
        for r in doc_rows
    ]

    recent_quizzes = [
        {
            "id": r.id,
            "title": r.title,
            "topic": r.col_str1,
            "question_count": r.col_int2,
            "difficulty": r.col_str2,
            "grade_range": f"Grades {r.col_int3}\u2013{r.col_int4}" if (r.col_int3 and r.col_int4) else ((r.col_str3 or "general").replace("_", " ").title()),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in quiz_rows
    ]

    # 3. Top topics with question counts (1 query)
    top_topics_query = db.query(Question.topic, func.count(Question.id).label("count")).\
        group_by(Question.topic).\
        order_by(desc("count")).\
        limit(6).all()
    top_topics = [{"topic": t[0], "count": t[1]} for t in top_topics_query]

    response = DashboardStatsResponse(
        total_documents=counts.doc_count if counts else 0,
        total_slides=counts.slide_count if counts else 0,
        total_questions=counts.question_count if counts else 0,
        total_topics=counts.distinct_topics if counts else 0,
        total_quizzes=counts.quiz_count if counts else 0,
        recent_documents=recent_documents,
        recent_quizzes=recent_quizzes,
        top_topics=top_topics,
    )

    _stats_cache = response
    _stats_cached_at = now
    return response
