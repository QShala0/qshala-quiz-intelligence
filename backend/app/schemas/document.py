from datetime import datetime
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, ConfigDict

class SlideBase(BaseModel):
    slide_number: int
    slide_type: str
    title: Optional[str] = None
    extracted_text: str = ""
    speaker_notes: Optional[str] = None
    has_images: bool = False
    image_paths: List[str] = []
    metadata_json: Optional[Dict[str, Any]] = None

class SlideResponse(SlideBase):
    id: str
    document_id: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class DocumentBase(BaseModel):
    filename: str
    title: str
    year: Optional[int] = None
    file_type: str

class DocumentCreate(DocumentBase):
    pass

class DocumentListItem(DocumentBase):
    id: str
    uploaded_at: datetime
    storage_path: str
    file_size_bytes: int
    slide_count: int
    question_count: int
    processing_status: str
    processing_error: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class DocumentResponse(DocumentListItem):
    slides: Optional[List[SlideResponse]] = None
    model_config = ConfigDict(from_attributes=True)

class IngestionStatusResponse(BaseModel):
    document_id: str
    status: str
    progress_percentage: int
    current_step: str
    slides_processed: int
    questions_extracted: int
    error: Optional[str] = None
    detail: Optional[str] = None
    events: Optional[List[Dict[str, Any]]] = None
    memory_mb: Optional[float] = None
    peak_memory_mb: Optional[float] = None
    memory_limit_mb: Optional[float] = None
