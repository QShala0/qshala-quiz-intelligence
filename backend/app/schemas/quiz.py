from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict, computed_field

class QuizGenerateRequest(BaseModel):
    topic: str = Field(..., example="Australian History")
    subtopic: Optional[str] = None
    quiz_format: Optional[str] = Field(default="TOURNAMENT_PRELIMS", example="TOURNAMENT_PRELIMS")  # TOURNAMENT_PRELIMS, STAGE_FINALS, CLASSROOM_WARMUP, THEMATIC_SPECIAL, GENERAL_CHAMPIONSHIP
    audience_type: Optional[str] = Field(default=None, example="general")
    grades: Optional[List[int]] = Field(default=None)
    age_range: Optional[str] = None
    grade_min: Optional[int] = Field(default=None, ge=1, le=12)
    grade_max: Optional[int] = Field(default=None, ge=1, le=12)
    difficulty: Optional[str] = Field(default="Balanced", example="Balanced")
    difficulty_distribution: Optional[Dict[str, int]] = Field(
        default_factory=lambda: {"easy": 3, "medium": 5, "hard": 2}
    )
    question_count: int = Field(default=10, ge=1, le=50)
    question_types: List[str] = Field(default=["SLIDE_QA"])
    generation_mode: str = Field(default="NEW", example="NEW")  # NEW, REMIX, HISTORICAL, SIMILAR
    tags: Optional[List[str]] = Field(default=None, description="Focus concept tags")
    tag_match_mode: str = Field(default="ANY", description="ANY or ALL")
    personality: Optional[str] = Field(default=None, description="Optional host persona")
    style: str = Field(default="QSHALA_HISTORICAL")
    raw_prompt: Optional[str] = None

class RetrievalSourceResponse(BaseModel):
    id: str
    historical_question_id: Optional[str] = None
    document_id: Optional[str] = None
    slide_id: Optional[str] = None
    document_title: Optional[str] = None
    slide_number: Optional[int] = None
    relevance_score: float = 1.0
    source_quote: Optional[str] = None
    rationale: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class GeneratedQuestionResponse(BaseModel):
    id: str
    quiz_id: str
    order_index: int
    question_text: str
    options: Optional[List[str]] = None
    answer: str
    explanation: Optional[str] = None
    difficulty: Optional[str] = "Balanced"
    grade_min: Optional[int] = None
    grade_max: Optional[int] = None
    topic: Optional[str] = None
    question_type: Optional[str] = "MULTIPLE_CHOICE"
    format_payload: Optional[Dict[str, Any]] = None
    image_refs: List[str] = []
    question_images: List[str] = []
    answer_images: List[str] = []
    audio_refs: List[str] = []
    question_audio: List[str] = []
    answer_audio: List[str] = []
    video_refs: List[str] = []
    question_video: List[str] = []
    raw_media_refs: List[str] = []
    visual_clues: Optional[str] = None
    validation_status: Optional[str] = "APPROVED"
    validation_details: Optional[Dict[str, Any]] = None
    duplicate_score: Optional[float] = 0.0
    is_approved: bool = True
    created_at: datetime
    retrieval_sources: List[RetrievalSourceResponse] = []
    model_config = ConfigDict(from_attributes=True)

    @computed_field
    @property
    def question_format(self) -> Optional[str]:
        """The format the question was read as (from its payload), else the older question_type."""
        p = self.format_payload
        return (p.get("type") if isinstance(p, dict) else None) or self.question_type

class GeneratedQuestionUpdate(BaseModel):
    question_text: Optional[str] = None
    options: Optional[List[str]] = None
    answer: Optional[str] = None
    explanation: Optional[str] = None
    difficulty: Optional[str] = None
    grade_min: Optional[int] = None
    grade_max: Optional[int] = None
    image_refs: Optional[List[str]] = None
    question_images: Optional[List[str]] = None
    answer_images: Optional[List[str]] = None
    audio_refs: Optional[List[str]] = None
    question_audio: Optional[List[str]] = None
    answer_audio: Optional[List[str]] = None
    visual_clues: Optional[str] = None
    is_approved: Optional[bool] = None
    order_index: Optional[int] = None

class QuestionActionRequest(BaseModel):
    action: str  # "regenerate", "make_easier", "make_harder", "generate_similar"
    custom_instruction: Optional[str] = None

class QuizResponse(BaseModel):
    id: str
    title: str
    topic: str
    subtopic: Optional[str] = None
    quiz_format: Optional[str] = "TOURNAMENT_PRELIMS"
    audience_type: Optional[str] = None
    grades: Optional[List[int]] = None
    age_range: Optional[str] = None
    grade_min: Optional[int] = None
    grade_max: Optional[int] = None
    difficulty: Optional[str] = "Balanced"
    difficulty_distribution: Optional[Dict[str, int]] = None
    question_count: int
    question_types: Optional[List[str]] = []
    generation_mode: Optional[str] = "NEW"
    tags: Optional[List[str]] = []
    personality: Optional[str] = None
    style: Optional[str] = "QSHALA_HISTORICAL"
    raw_prompt: Optional[str] = None
    status: Optional[str] = "COMPLETED"
    created_at: datetime
    updated_at: datetime
    questions: List[GeneratedQuestionResponse] = []
    model_config = ConfigDict(from_attributes=True)

class ExportRequest(BaseModel):
    format: str = "json"  # json, csv, printable_html
