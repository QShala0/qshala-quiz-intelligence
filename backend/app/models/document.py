import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, BigInteger, Text, DateTime, Boolean, ForeignKey, JSON
from sqlalchemy.orm import relationship
from backend.app.database import Base, get_vector_column_type
from backend.app.config import settings

def generate_uuid():
    return str(uuid.uuid4())

class Document(Base):
    __tablename__ = "documents"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    filename = Column(String(255), nullable=False)
    title = Column(String(255), nullable=False)
    year = Column(Integer, nullable=True)
    uploaded_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    file_type = Column(String(50), nullable=False)  # pptx, ppt, pdf
    storage_path = Column(Text, nullable=False)
    blob_url = Column(Text, nullable=True)
    source = Column(String(50), default="MANUAL")  # MANUAL, SHAREPOINT
    sharepoint_file_id = Column(String(255), nullable=True)
    file_size_bytes = Column(BigInteger, default=0)
    slide_count = Column(Integer, default=0)
    question_count = Column(Integer, default=0)
    processing_status = Column(String(50), default="PENDING")  # PENDING, CLAIMED, PROCESSING, COMPLETED, FAILED, CANCELLED
    processing_error = Column(Text, nullable=True)
    # Cross-process ingestion state: the pipeline may run in a separate worker
    # process from the one serving this API, so progress and cancellation are
    # mirrored through these columns rather than the in-memory registry/set
    # that work when everything runs in one process.
    progress_percentage = Column(Integer, default=0)
    current_step = Column(String(255), nullable=True)
    cancel_requested = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    slides = relationship("Slide", back_populates="document", cascade="all, delete-orphan", order_by="Slide.slide_number")
    questions = relationship("Question", back_populates="document", cascade="all, delete-orphan")

class Slide(Base):
    __tablename__ = "slides"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    slide_number = Column(Integer, nullable=False)
    slide_type = Column(String(50), default="CONTENT")  # QUESTION, ANSWER, TITLE, CONTENT, SCOREBOARD, RULES
    title = Column(Text, nullable=True)
    extracted_text = Column(Text, default="")
    speaker_notes = Column(Text, nullable=True)
    image_paths = Column(JSON, default=list)
    has_images = Column(Boolean, default=False)
    metadata_json = Column(JSON, default=dict)
    embedding = Column(get_vector_column_type(settings.EMBEDDING_DIM), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    document = relationship("Document", back_populates="slides")
    questions = relationship("Question", back_populates="slide", foreign_keys="Question.slide_id")
