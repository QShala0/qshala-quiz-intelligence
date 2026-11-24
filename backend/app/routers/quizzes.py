import logging
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Response, Body
from sqlalchemy.orm import Session
from backend.app.database import get_db
from backend.app.models.quiz import Quiz, GeneratedQuestion
from backend.app.schemas.quiz import (
    QuizGenerateRequest,
    QuizResponse,
    QuizRenameRequest,
    GeneratedQuestionResponse,
    GeneratedQuestionUpdate,
    QuestionActionRequest,
)
from backend.app.services.generator.quiz_generator import QuizGenerator
from backend.app.services.export.exporter import QuizExporter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quizzes", tags=["Quizzes"])


def _safe_to_pptx(*args, **kwargs):
    """Build a PPTX; turn any builder crash into a clean JSON error, not a raw 500."""
    try:
        return QuizExporter.to_pptx(*args, **kwargs)
    except HTTPException:
        raise
    except Exception:
        logger.exception("PPTX export failed")
        raise HTTPException(
            status_code=500,
            detail="Could not build the PowerPoint for this quiz. Try the JSON export or contact support.",
        )

@router.post("/generate", response_model=QuizResponse)
async def generate_quiz(
    req: QuizGenerateRequest,
    db: Session = Depends(get_db)
):
    try:
        generator = QuizGenerator(db)
        quiz = await generator.generate_quiz(req)
        return quiz
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        logger.exception("Quiz generation failed")
        raise HTTPException(
            status_code=500,
            detail="Quiz generation failed. Please try again in a moment.",
        )

@router.get("", response_model=List[QuizResponse])
def list_quizzes(db: Session = Depends(get_db)):
    return db.query(Quiz).order_by(Quiz.created_at.desc()).limit(500).all()

@router.get("/{quiz_id}", response_model=QuizResponse)
def get_quiz(quiz_id: str, db: Session = Depends(get_db)):
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")
    return quiz

@router.patch("/{quiz_id}", response_model=QuizResponse)
def rename_quiz(
    quiz_id: str,
    update_data: QuizRenameRequest,
    db: Session = Depends(get_db)
):
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")

    title = (update_data.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    if len(title) > 255:
        raise HTTPException(status_code=400, detail="Title cannot exceed 255 characters")

    quiz.title = title
    db.commit()
    db.refresh(quiz)
    return quiz

def _generated_picture_urls(questions) -> set:
    """Every picture a quiz's questions point at (they normally share the bank question's own pictures)."""
    urls = set()
    for q in questions:
        for col in ("question_images", "answer_images", "image_refs"):
            urls.update(u for u in (getattr(q, col, None) or []) if isinstance(u, str) and u)
        payload = getattr(q, "format_payload", None)
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            urls.update(it["image"] for it in payload["items"]
                        if isinstance(it, dict) and isinstance(it.get("image"), str) and it["image"])
    return urls


def _cleanup_pictures(db: Session, urls: set) -> None:
    """After a delete has been committed: remove a stored picture only if nothing else uses it any more. A picture a
    bank question, slide or other quiz still shows is kept; this only catches files the quiz was the last user of
    (for example a picture removed from its bank question earlier while this quiz still showed it)."""
    if not urls:
        return
    try:
        from backend.app.services import media_cleanup
        media_cleanup.delete_unreferenced(db, urls)
    except Exception:
        pass


@router.delete("/{quiz_id}")
def delete_quiz(quiz_id: str, db: Session = Depends(get_db)):
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")
    urls = _generated_picture_urls(quiz.questions)
    db.delete(quiz)
    db.commit()
    _cleanup_pictures(db, urls)
    return {"message": "Quiz deleted successfully"}

@router.patch("/questions/{question_id}", response_model=GeneratedQuestionResponse)
def update_question(
    question_id: str,
    update_data: GeneratedQuestionUpdate,
    db: Session = Depends(get_db)
):
    gen_q = db.query(GeneratedQuestion).filter(GeneratedQuestion.id == question_id).first()
    if not gen_q:
        raise HTTPException(status_code=404, detail="Question not found")

    data_dict = update_data.model_dump(exclude_unset=True)
    for field, value in data_dict.items():
        setattr(gen_q, field, value)

    db.commit()
    db.refresh(gen_q)
    return gen_q

@router.post("/questions/{question_id}/action", response_model=GeneratedQuestionResponse)
async def execute_question_action(
    question_id: str,
    req: QuestionActionRequest,
    db: Session = Depends(get_db)
):
    try:
        generator = QuizGenerator(db)
        updated_q = await generator.execute_question_action(
            question_id=question_id,
            action=req.action,
            custom_instruction=req.custom_instruction
        )
        return updated_q
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        logger.exception("Question action failed")
        raise HTTPException(status_code=500, detail="Question action failed. Please try again.")

@router.delete("/questions/{question_id}")
def delete_question(question_id: str, db: Session = Depends(get_db)):
    gen_q = db.query(GeneratedQuestion).filter(GeneratedQuestion.id == question_id).first()
    if not gen_q:
        raise HTTPException(status_code=404, detail="Question not found")
    urls = _generated_picture_urls([gen_q])
    db.delete(gen_q)
    db.commit()
    _cleanup_pictures(db, urls)
    return {"message": "Question deleted successfully"}

@router.get("/{quiz_id}/export")
def export_quiz(
    quiz_id: str,
    format: str = Query("json", pattern="^(json|csv|pptx)$"),
    quiz_type: str = Query("school", pattern="^(school|corporate|minimalist|qshala_school|qshala|zvq|zvq_school|zvq-school|zsv|zsv_school|zvq_corporate|zsv_corporate)$"),
    campus_edition: Optional[str] = Query("Bangalore Campus Edition", max_length=255),
    prize_money: Optional[str] = Query("3.5 lakh", max_length=100),
    custom_title: Optional[str] = Query(None, max_length=255),
    include_social_slides: bool = Query(True),
    theme: Optional[str] = Query(None),
    custom_subtitle: Optional[str] = Query(None),
    show_answer_divider: Optional[bool] = Query(None),
    template_id: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")

    if format == "json":
        json_content = QuizExporter.to_json(quiz)
        return Response(
            content=json_content,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=quiz_{quiz.id[:8]}.json"}
        )
    elif format == "csv":
        csv_content = QuizExporter.to_csv(quiz)
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=quiz_{quiz.id[:8]}.csv"}
        )
    elif format == "pptx":
        effective_quiz_type = quiz_type
        if theme and theme in ("corporate", "corp"):
            effective_quiz_type = "corporate"
        elif theme and theme in ("minimalist", "clean"):
            effective_quiz_type = "minimalist"
        elif theme and theme in ("qshala_school", "qshala", "qshala-school"):
            effective_quiz_type = "qshala_school"
        elif effective_quiz_type in ("zvq_corporate", "zsv_corporate"):
            effective_quiz_type = "corporate"
        elif effective_quiz_type in ("zvq", "zvq_school", "zvq-school", "zsv", "zsv_school"):
            effective_quiz_type = "school"
        pptx_bytes = _safe_to_pptx(
            quiz,
            quiz_type=effective_quiz_type,
            campus_edition=campus_edition,
            prize_money=prize_money,
            custom_title=custom_title,
            include_social_slides=include_social_slides,
            custom_subtitle=custom_subtitle,
            show_answer_divider=show_answer_divider,
            template_id=template_id,
            db=db,
        )
        return Response(
            content=pptx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={"Content-Disposition": f"attachment; filename=qshala_quiz_{quiz.id[:8]}.pptx"}
        )

@router.post("/export")
def export_quiz_payload(
    payload: Dict[str, Any] = Body(...),
    format: str = Query("pptx", pattern="^(json|csv|pptx)$"),
    quiz_type: str = Query("school", pattern="^(school|corporate|minimalist|qshala_school|qshala|zvq|zvq_school|zvq-school|zsv|zsv_school|zvq_corporate|zsv_corporate)$"),
    campus_edition: Optional[str] = Query("Bangalore Campus Edition", max_length=255),
    prize_money: Optional[str] = Query("3.5 lakh", max_length=100),
    custom_title: Optional[str] = Query(None, max_length=255),
    include_social_slides: bool = Query(True),
    theme: Optional[str] = Query(None),
    custom_subtitle: Optional[str] = Query(None),
    show_answer_divider: Optional[bool] = Query(None),
    template_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    if not isinstance(payload, dict) or not isinstance(payload.get("questions", []), list):
        raise HTTPException(status_code=422, detail="Quiz payload must be an object with a 'questions' list")
    quiz_id = payload.get("id", "custom")
    clean_id = str(quiz_id)[:8] if quiz_id else "custom"
    if format == "json":
        json_content = QuizExporter.to_json(payload)
        return Response(
            content=json_content,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=quiz_{clean_id}.json"}
        )
    elif format == "csv":
        csv_content = QuizExporter.to_csv(payload)
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=quiz_{clean_id}.csv"}
        )
    elif format == "pptx":
        effective_quiz_type = quiz_type
        if theme and theme in ("corporate", "corp"):
            effective_quiz_type = "corporate"
        elif theme and theme in ("minimalist", "clean"):
            effective_quiz_type = "minimalist"
        elif theme and theme in ("qshala_school", "qshala", "qshala-school"):
            effective_quiz_type = "qshala_school"
        elif effective_quiz_type in ("zvq_corporate", "zsv_corporate"):
            effective_quiz_type = "corporate"
        elif effective_quiz_type in ("zvq", "zvq_school", "zvq-school", "zsv", "zsv_school"):
            effective_quiz_type = "school"
        pptx_bytes = _safe_to_pptx(
            payload,
            quiz_type=effective_quiz_type,
            campus_edition=campus_edition,
            prize_money=prize_money,
            custom_title=custom_title,
            include_social_slides=include_social_slides,
            custom_subtitle=custom_subtitle,
            show_answer_divider=show_answer_divider,
            template_id=template_id,
            db=db,
        )
        return Response(
            content=pptx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={"Content-Disposition": f"attachment; filename=qshala_quiz_{clean_id}.pptx"}
        )

