import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, Boolean, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from backend.app.database import Base

def generate_uuid():
    return str(uuid.uuid4())

class Quiz(Base):
    __tablename__ = "quizzes"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    title = Column(String(255), nullable=False)
    topic = Column(String(100), nullable=False, index=True)
    subtopic = Column(String(100), nullable=True)
    quiz_format = Column(String(50), default="TOURNAMENT_PRELIMS")  # TOURNAMENT_PRELIMS, STAGE_FINALS, CLASSROOM_WARMUP, THEMATIC_SPECIAL, GENERAL_CHAMPIONSHIP
    audience_type = Column(String(50), nullable=True, default=None)  # Deprecated in favor of quiz_format
    grades = Column(JSON, default=list)  # list of ints, e.g. [3, 4, 5]
    age_range = Column(String(50), nullable=True)
    grade_min = Column(Integer, nullable=True, default=None)
    grade_max = Column(Integer, nullable=True, default=None)
    difficulty = Column(String(50), default="Balanced")  # Summary or preset name
    difficulty_distribution = Column(JSON, default=dict)  # {"easy": 5, "medium": 10, "hard": 5}
    question_count = Column(Integer, default=10)
    question_types = Column(JSON, default=lambda: ["SLIDE_QA"])
    generation_mode: str = Column(String(50), default="NEW")  # NEW, REMIX, HISTORICAL, SIMILAR
    tags = Column(JSON, default=list)  # Target focus concept tags
    personality = Column(String(50), nullable=True, default=None)  # Optional host persona
    style = Column(String(50), default="QSHALA_HISTORICAL")
    raw_prompt = Column(Text, nullable=True)
    status = Column(String(50), default="READY")  # DRAFT, GENERATING, READY, ARCHIVED
    created_by_user_id = Column(String(36), ForeignKey("auth_users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_name = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    questions = relationship("GeneratedQuestion", back_populates="quiz", cascade="all, delete-orphan", order_by="GeneratedQuestion.order_index")

class GeneratedQuestion(Base):
    __tablename__ = "generated_questions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    quiz_id = Column(String(36), ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False)
    order_index = Column(Integer, nullable=False, default=1)
    
    question_text = Column(Text, nullable=False)
    options = Column(JSON, nullable=True)  # List of strings for MCQs
    answer = Column(Text, nullable=False)
    explanation = Column(Text, nullable=True)
    difficulty = Column(String(20), default="Medium")
    grade_min = Column(Integer, default=3)
    grade_max = Column(Integer, default=5)
    topic = Column(String(100), nullable=True)
    question_type = Column(String(50), default="SLIDE_QA")
    format_payload = Column(JSON, nullable=True)
    
    # Multimodal media & visual clues
    image_refs = Column(JSON, default=list)            # All image URLs
    question_images = Column(JSON, default=list)       # Clue / Question slide images
    answer_images = Column(JSON, default=list)         # Reveal / Answer slide images
    audio_refs = Column(JSON, default=list)            # All audio URLs
    question_audio = Column(JSON, default=list)        # Clue / Question slide audio clips
    answer_audio = Column(JSON, default=list)          # Reveal / Answer slide audio clips
    video_refs = Column(JSON, default=list)            # All video URLs
    question_video = Column(JSON, default=list)        # Clue / Question slide video clips
    raw_media_refs = Column(JSON, default=list)        # Raw media files
    visual_clues = Column(Text, nullable=True)         # OCR & visual description
    
    # Validation fields
    validation_status = Column(String(50), default="PASSED")  # PASSED, WARNING, FAILED
    validation_details = Column(JSON, default=dict)
    # validation_details schema:
    # {
    #   "answer_consistency": {"passed": true, "score": 1.0, "reason": "..."},
    #   "factual_grounding": {"passed": true, "score": 0.95, "reason": "..."},
    #   "grade_suitability": {"passed": true, "score": 0.90, "grade_range": "3-5", "reason": "..."},
    #   "duplicate_risk": {"passed": true, "score": 0.12, "most_similar_q": "..."},
    #   "internal_consistency": {"passed": true, "score": 1.0, "distractor_quality": "High"},
    #   "difficulty_alignment": {"passed": true, "score": 0.95, "level": "Medium"},
    #   "provenance": {"has_source": true, "doc_title": "...", "slide_num": 12}
    # }
    duplicate_score = Column(Float, default=0.0)
    is_approved = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    quiz = relationship("Quiz", back_populates="questions")
    retrieval_sources = relationship("RetrievalSource", back_populates="generated_question", cascade="all, delete-orphan")

class RetrievalSource(Base):
    __tablename__ = "retrieval_sources"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    generated_question_id = Column(String(36), ForeignKey("generated_questions.id", ondelete="CASCADE"), nullable=False)
    historical_question_id = Column(String(36), ForeignKey("questions.id", ondelete="SET NULL"), nullable=True)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    slide_id = Column(String(36), ForeignKey("slides.id", ondelete="SET NULL"), nullable=True)
    
    document_title = Column(String(255), nullable=True)
    slide_number = Column(Integer, nullable=True)
    relevance_score = Column(Float, default=1.0)
    source_quote = Column(Text, nullable=True)
    rationale = Column(Text, nullable=True)

    # Relationships
    generated_question = relationship("GeneratedQuestion", back_populates="retrieval_sources")
