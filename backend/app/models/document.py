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
    processing_status = Column(String(50), default="PENDING")  # PENDING, PROCESSING, COMPLETED, FAILED
    processing_error = Column(Text, nullable=True)
    # Cross-process cancellation flag. In-process ingestion (default) uses the in-memory
    # CANCELLED_DOCS set in cancellation.py and never touches this column. The Cloud Run Job
    # worker (INGESTION_MODE=cloud_run_job) runs in a separate process/container that can't see
    # that in-memory set, so it polls this column instead -- see worker.py's cancel poller.
    cancel_requested = Column(Boolean, default=False)
    # Periodic snapshot of the live in-process progress registry (progress.py), written by
    # worker.py every few seconds under INGESTION_MODE=cloud_run_job so /status's DB fallback can
    # show the same detail/activity-log/server-memory telemetry the upload page already renders
    # for in-process ingestion -- that telemetry otherwise lives only in the worker container's
    # own memory, same cross-process visibility gap as cancel_requested above. Unused (stays
    # null) for in_process/upload_queue modes, which already have it live via the registry.
    progress_detail = Column(JSON, nullable=True)
    # SHA-256 of the uploaded deck file, recorded at the start of ingestion so an identical re-upload can be
    # recognised (see IDENTICAL_REUPLOAD_ACTION in pipeline.py). Null for decks ingested before this existed.
    file_sha256 = Column(String(64), nullable=True)
    # Per-deck duplicate/reconciliation report from the last ingestion run (see
    # deduplicator.build_duplicate_report): already-in-library repeats, same-deck intra-batch
    # repeats, possible-duplicates kept for review, and extracted/saved/dropped counts. Written
    # directly here (not just to progress_detail) so GET /documents/{id}/upload-report can read it
    # back after the run, in every ingestion mode -- progress_detail itself is only persisted by
    # the Cloud Run Job worker's cross-process mirror and stays null for in-process ingestion.
    duplicate_report = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    slides = relationship("Slide", back_populates="document", cascade="all, delete-orphan", order_by="Slide.slide_number")
    questions = relationship("Question", back_populates="document", cascade="all, delete-orphan")

class Slide(Base):
    __tablename__ = "slides"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
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
