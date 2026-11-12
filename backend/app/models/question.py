import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, Text, DateTime, ForeignKey, JSON, Boolean, false
from sqlalchemy.orm import relationship
from backend.app.database import Base, get_vector_column_type
from backend.app.config import settings

def generate_uuid():
    return str(uuid.uuid4())

class Question(Base):
    __tablename__ = "questions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    content_hash = Column(String(64), index=True, nullable=True) # Canonical hash for O(1) deduplication
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    slide_id = Column(String(36), ForeignKey("slides.id", ondelete="SET NULL"), nullable=True, index=True)
    answer_slide_id = Column(String(36), ForeignKey("slides.id", ondelete="SET NULL"), nullable=True)
    
    question_text = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    options = Column(JSON, nullable=True)  # List of strings for MCQs, e.g. ["A) ...", "B) ..."]
    explanation = Column(Text, nullable=True)
    
    topic = Column(String(100), nullable=False, index=True)
    subtopic = Column(String(100), nullable=True, index=True)
    topics = Column(JSON, default=list) # Multi-topic tags, e.g. ["History", "Science", "Politics"]
    tags = Column(JSON, default=list)   # Named entities and secondary keywords
    
    difficulty = Column(String(20), default="Medium")  # Easy, Medium, Hard
    difficulty_score = Column(Float, default=0.50)     # Continuous PDI 0.00 - 1.00
    cognitive_level = Column(String(50), default="Recall / Remember")
    grade_min = Column(Integer, default=3)
    grade_max = Column(Integer, default=12)
    audience_suitability = Column(JSON, default=list)  # ["primary", "middle_school", ...]
    
    question_hook = Column(String(50), default="DIRECT_TRIVIA")
    curiosity_score = Column(Integer, default=7)
    temporal_nature = Column(String(20), default="EVERGREEN")
    
    provenance_decks = Column(JSON, default=list)      # List of all document IDs where this appeared
    occurrence_count = Column(Integer, default=1)
    
    question_type = Column(String(50), default="SLIDE_QA")
    question_format = Column(
        String(20),
        nullable=False,
        default="STANDARD_QA",
        server_default="STANDARD_QA",
        index=True,
        comment="STANDARD_QA | CONNECT (alias CONNECT_4) | LIST_IT | IMAGE_REVEAL | STEPPED_CLUE | ARRANGE_ORDER | MCQ"
    )
    format_data = Column(
        JSON,
        nullable=True,
        default=None,
        comment="Structured data for non-standard formats"
    )
    format_payload = Column(JSON, nullable=True)       # Structured payload for CONNECT_4, STEPPED_CLUE, ARRANGE_ORDER
    # How the question is played, and whether it is part of the live quiz. Round name/number and the directed team
    # live in format_payload. `topic` stays the taxonomy topic; `topic_label` is the deck's own Topicator label
    # ("Sustainability 1") and is never used for de-duplication.
    play_mode = Column(String(16), nullable=True,
                       comment="BUZZER | DIRECTED | PASS | AUDIENCE | TIEBREAK | ALL_ON_SCREEN | BUFFER")
    slot = Column(String(10), nullable=False, default="LIVE", server_default="LIVE", comment="LIVE | RESERVE | EXAMPLE")
    topic_label = Column(String(100), nullable=True)
    # True while the format reading (confidence < 0.7, an Arrange order that could not be computed...) waits for a person.
    format_needs_review = Column(Boolean, nullable=False, default=False, server_default=false())
    source_year = Column(Integer, nullable=True)
    round_number = Column(Integer, nullable=True)

    # Multi-modal media & canonical representations
    image_refs = Column(JSON, default=list)            # List of Blob URLs for images (all images)
    question_images = Column(JSON, default=list)       # Specific clue/question images
    answer_images = Column(JSON, default=list)         # Specific reveal/answer images
    audio_refs = Column(JSON, default=list)            # All audio URLs
    question_audio = Column(JSON, default=list)        # Specific clue/question audio clips
    answer_audio = Column(JSON, default=list)          # Specific reveal/answer audio clips
    video_refs = Column(JSON, default=list)            # All video URLs
    question_videos = Column(JSON, default=list)       # Specific clue/question video clips
    answer_videos = Column(JSON, default=list)         # Specific reveal/answer video clips
    visual_clues = Column(Text, nullable=True)         # OCR text & Gemini vision description
    audio_transcript = Column(Text, nullable=True)     # Transcribed audio speech/clue
    video_transcript = Column(Text, nullable=True)     # Transcribed video speech
    raw_media_refs = Column(JSON, default=list)        # Original media files in Blob
    source_slide_range = Column(String(50), nullable=True) # e.g. "Slide 4-5"

    # Deduplication & Human Review
    duplicate_status = Column(String(50), default="UNIQUE", index=True) # UNIQUE, POSSIBLE_DUPLICATE, CONFIRMED_DUPLICATE, RESOLVED
    duplicate_similarity = Column(Float, nullable=True)
    duplicate_of_id = Column(String(36), ForeignKey("questions.id", ondelete="SET NULL"), nullable=True, index=True)
    
    embedding = Column(get_vector_column_type(settings.EMBEDDING_DIM), nullable=True)
    # Which model produced `embedding` (e.g. "gemini-embedding-001"), so vectors from different models can be
    # told apart. Null for rows embedded before this column existed.
    embedding_model = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    # Relationships
    document = relationship("Document", back_populates="questions")
    slide = relationship("Slide", foreign_keys=[slide_id], back_populates="questions")
    answer_slide = relationship("Slide", foreign_keys=[answer_slide_id])
    duplicate_of = relationship("Question", remote_side=[id], foreign_keys=[duplicate_of_id])
