from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, ConfigDict

class QuestionBase(BaseModel):
    question_text: str
    answer: str
    options: Optional[List[str]] = None
    explanation: Optional[str] = None
    topic: str
    subtopic: Optional[str] = None
    topics: List[str] = []
    tags: List[str] = []
    difficulty: str = "Medium"
    difficulty_score: float = 0.50
    cognitive_level: str = "Recall / Remember"
    grade_min: int = 3
    grade_max: int = 12
    audience_suitability: List[str] = []
    question_hook: str = "DIRECT_TRIVIA"
    curiosity_score: int = 7
    temporal_nature: str = "EVERGREEN"
    occurrence_count: int = 1
    provenance_decks: List[Dict[str, Any]] = []
    round_number: Optional[int] = None
    question_type: str = "SLIDE_QA"
    question_format: Optional[str] = "STANDARD_QA"
    format_data: Optional[Dict[str, Any]] = None
    format_payload: Optional[Dict[str, Any]] = None
    play_mode: Optional[str] = None      # BUZZER | DIRECTED | PASS | AUDIENCE | TIEBREAK | ALL_ON_SCREEN | BUFFER
    slot: str = "LIVE"                   # LIVE | RESERVE | EXAMPLE
    topic_label: Optional[str] = None
    format_needs_review: bool = False
    source_year: Optional[int] = None
    # Multi-modal media & canonical representations
    image_refs: List[str] = []
    question_images: List[str] = []
    answer_images: List[str] = []
    audio_refs: List[str] = []
    question_audio: List[str] = []
    answer_audio: List[str] = []
    video_refs: List[str] = []
    question_videos: List[str] = []
    question_video: List[str] = []
    answer_videos: List[str] = []
    visual_clues: Optional[str] = None
    audio_transcript: Optional[str] = None
    video_transcript: Optional[str] = None
    raw_media_refs: List[str] = []
    source_slide_range: Optional[str] = None
    # Deduplication & human review
    duplicate_status: str = "UNIQUE"
    duplicate_similarity: Optional[float] = None
    duplicate_of_id: Optional[str] = None

class QuestionCreate(QuestionBase):
    document_id: str
    slide_id: Optional[str] = None
    answer_slide_id: Optional[str] = None

class QuestionResponse(QuestionBase):
    id: str
    content_hash: Optional[str] = None
    document_id: str
    slide_id: Optional[str] = None
    answer_slide_id: Optional[str] = None
    created_at: datetime
    document_title: Optional[str] = None
    slide_number: Optional[int] = None
    duplicate_of_text: Optional[str] = None
    duplicate_of_answer: Optional[str] = None
    # v6 tags shown in the list row (the full set is in GET /questions/{id}/detail)
    question_number: Optional[int] = None
    question_label: Optional[str] = None
    star_marked: bool = False
    topic_paths: List[Dict[str, Any]] = []
    repeated: List[Dict[str, Any]] = []
    repeated_count: int = 0
    model_config = ConfigDict(from_attributes=True)

class DuplicateResolveRequest(BaseModel):
    action: str  # "CONFIRM_DUPLICATE", "DISMISS_UNIQUE", "MERGE"
    notes: Optional[str] = None

class QuestionUpdate(BaseModel):
    tags: Optional[List[str]] = None
    topics: Optional[List[str]] = None
    topic: Optional[str] = None
    subtopic: Optional[str] = None
    difficulty: Optional[str] = None
    question_text: Optional[str] = None
    answer: Optional[str] = None
    explanation: Optional[str] = None
    visual_clues: Optional[str] = None
    slot: Optional[str] = None
    play_mode: Optional[str] = None


class ArrangeOrderUpdate(BaseModel):
    """The correct order of an ARRANGE_ORDER question, as the item labels (1-4) in order. [] clears it."""
    correct_order: List[int]


class QuestionSearchParams(BaseModel):
    query: Optional[str] = None
    topic: Optional[str] = None
    subtopic: Optional[str] = None
    tag: Optional[str] = None
    grade_min: Optional[int] = None
    grade_max: Optional[int] = None
    difficulty: Optional[str] = None
    year: Optional[int] = None
    limit: int = 20
    offset: int = 0

