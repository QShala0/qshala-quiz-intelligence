export interface Slide {
  id: string;
  document_id: string;
  slide_number: number;
  slide_type: string;
  title: string | null;
  extracted_text: string;
  speaker_notes?: string | null;
  has_images: boolean;
  image_paths: string[];
  created_at: string;
}

export interface DocumentItem {
  id: string;
  filename: string;
  title: string;
  year?: number | null;
  file_type: string;
  file_size_bytes: number;
  slide_count: number;
  question_count: number;
  processing_status: 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';
  processing_error?: string | null;
  uploaded_at: string;
}

export interface DocumentDetail extends DocumentItem {
  slides?: Slide[];
}

export interface SourceSeriesItem {
  key: string;
  label: string;
  document_ids: string[];
  deck_count: number;
  question_count: number;
  years: number[];
}

export interface SourceDeckItem {
  id: string;
  title: string;
  filename: string;
  series: string;
  series_key: string;
  year?: number | null;
  question_count: number;
}

export interface SourceCatalog {
  series: SourceSeriesItem[];
  decks: SourceDeckItem[];
}

export interface HistoricalQuestion {
  id: string;
  document_id: string;
  slide_id?: string | null;
  answer_slide_id?: string | null;
  question_text: string;
  answer: string;
  options?: string[] | null;
  explanation?: string | null;
  topic: string;
  subtopic?: string | null;
  topics?: string[];
  tags?: string[];
  difficulty: string;
  difficulty_score?: number;
  cognitive_level?: string;
  grade_min: number;
  grade_max: number;
  audience_suitability?: string[];
  question_hook?: string;
  curiosity_score?: number;
  temporal_nature?: string;
  occurrence_count?: number;
  provenance_decks?: { document_id: string; slide_id?: string }[];
  round_number?: number | null;
  content_hash?: string;
  question_type: string;
  source_year?: number | null;
  document_title?: string | null;
  slide_number?: number | null;
  duplicate_status?: string;
  duplicate_similarity?: number | null;
  duplicate_of_id?: string | null;
  duplicate_of_text?: string | null;
  duplicate_of_answer?: string | null;
  image_refs?: string[];
  question_images?: string[];
  answer_images?: string[];
  visual_clues?: string | null;
  audio_refs?: string[];
  question_audio?: string[];
  answer_audio?: string[];
  video_refs?: string[];
  question_videos?: string[];
  question_video?: string[];
  answer_videos?: string[];
  audio_transcript?: string | null;
  video_transcript?: string | null;
  raw_media_refs?: string[];
  source_slide_range?: string | null;
  created_at: string;
  // v6 tags shown in the list row (everything else is behind "Edit tags")
  question_number?: number | null;
  question_label?: string | null;
  question_format?: string | null;
  format_data?: any;
  format_payload?: any;
  // format-aware ingestion: how the question is played, whether it is part of the live quiz, the deck's own topic label
  play_mode?: string | null;
  slot?: 'LIVE' | 'RESERVE' | 'EXAMPLE' | string;
  topic_label?: string | null;
  format_needs_review?: boolean;
  star_marked?: boolean;
  topic_paths?: TopicPath[];
  repeated?: RepeatEntry[];
  repeated_count?: number;
}

/** One Theme > Subtopic > keywords path. A question can have several. */
export interface TopicPath {
  id?: string;
  position?: number;
  l1: string;
  l2?: string | null;
  keywords: string[];
  source?: string;
  is_custom?: boolean;
}

/** The "Repeated" tag: the same question found again in another quiz. */
export interface RepeatEntry {
  id: string;
  document_id: string;
  deck_title?: string | null;
  quiz_name?: string | null;
  round_number?: number | null;
  question_in_round?: number | null;
  question_number?: number | null;
  slide_range?: string | null;
  match_type?: string;
  label: string;
  found_at?: string | null;
}

export interface QuestionMeta {
  question_number?: number | null;
  round_number?: number | null;
  question_in_round?: number | null;
  question_label?: string;
  question_format?: string | null;
  star_marked: boolean;
  answer_type?: string | null;
  media_type?: string | null;
  language?: string | null;
  quiz_name?: string | null;
  college_institution?: string | null;
  city_name?: string | null;
  round_stage?: string | null;
  deck_year?: number | null;
  primary_audience?: string | null;
  secondary_audience?: string | null;
  is_varsity: boolean;
  relevant_year?: number | null;
  geo_region?: string | null;
  india_international?: string | null;
  source_presentation?: string | null;
  manual_fields: string[];
}

export interface DeckMeta {
  document_id: string;
  quiz_name?: string | null;
  college?: string | null;
  city?: string | null;
  round_stage?: string | null;
  deck_date?: string | null;
  deck_year?: number | null;
  language?: string | null;
  primary_audience?: string | null;
  is_varsity: boolean;
  source_presentation?: string | null;
  parsed_ok?: boolean;
  manual_fields: string[];
  questions_updated?: number;
}

export interface QuestionDetail {
  tag_error?: string | null;
  id: string;
  question_text: string;
  answer: string;
  difficulty: string;
  difficulty_score?: number;
  document: { id: string; title?: string | null; filename?: string | null };
  source_slide_range?: string | null;
  question_images: string[];
  answer_images: string[];
  visual_clues?: string | null;
  meta: QuestionMeta;
  deck: DeckMeta;
  topic_paths: TopicPath[];
  hashtags: string[];
  repeated: RepeatEntry[];
  topic: string;
  subtopic?: string | null;
}

export interface EditHistoryItem {
  id: string;
  field: string;
  old_value?: string | null;
  new_value?: string | null;
  kind: string;
  actor?: string | null;
  changed_at?: string | null;
}

export interface TaxonomyV6 {
  version: string;
  themes: { name: string; code: string; subtopics: { name: string; code: string; description: string }[] }[];
  custom: { l1: string; l2?: string | null; count: number }[];
  formats: string[];
  answer_types: string[];
  geographies: string[];
  audiences: string[];
}

export interface SharePointSyncStatus {
  id: string;
  site_id?: string | null;
  drive_id?: string | null;
  status: string;
  last_sync_at?: string | null;
  total_files_tracked: number;
  last_error?: string | null;
}

export interface SharePointFileItem {
  id: string;
  name: string;
  web_url?: string | null;
  file_size_bytes: number;
  last_modified_date_time?: string | null;
  blob_url?: string | null;
  sync_status: string;
  error_message?: string | null;
}

export interface SharePointSyncResponse {
  status: string;
  pages_processed: number;
  discovered_files_count: number;
  enqueued_jobs_count: number;
  deleted_files_count: number;
  enqueued_job_ids: string[];
  delta_token?: string | null;
}

export interface RequeueResponse {
  message: string;
  job_id: string;
}

export interface JobActionResponse {
  status: 'SUCCESS' | 'NO_JOBS_PENDING' | 'IDLE' | 'ERROR';
  job?: IngestionJobItem | null;
  processed?: boolean;
  error?: string;
}

export interface JobRetryResponse {
  message: string;
  job: IngestionJobItem;
}

export interface IngestionJobItem {
  id: string;
  source_type: string;
  source_file_id?: string | null;
  filename: string;
  blob_url?: string | null;
  status: string;
  current_step: string;
  retry_count: number;
  max_retries: number;
  error_message?: string | null;
  document_id?: string | null;
  created_at: string;
  updated_at?: string | null;
  completed_at?: string | null;
  step_summary?: {
    slides_count: number;
    media_count: number;
    media_cursor: number;
    questions_count: number;
    exact_duplicates: number;
  };
}

export interface TopicItem {
  topic: string;
  count: number;
}

export interface TopicSummary {
  topic: string;
  total_questions: number;
  difficulty_breakdown: Record<string, number>;
  subtopics: string[];
  grade_min: number | null;
  grade_max: number | null;
  sample_questions: { question_text: string; difficulty: string; answer: string }[];
}

export interface RetrievalSource {
  id: string;
  historical_question_id?: string | null;
  document_id?: string | null;
  slide_id?: string | null;
  document_title?: string | null;
  slide_number?: number | null;
  relevance_score: number;
  source_quote?: string | null;
  rationale?: string | null;
}

export interface ValidationDetailItem {
  passed: boolean;
  score?: number;
  reason?: string;
  level?: string;
  grade_range?: string;
  distractor_quality?: string;
  most_similar_question?: string;
}

export interface ValidationDetails {
  answer_consistency?: ValidationDetailItem;
  factual_grounding?: ValidationDetailItem;
  grade_suitability?: ValidationDetailItem;
  duplicate_risk?: ValidationDetailItem;
  internal_consistency?: ValidationDetailItem;
  difficulty_alignment?: ValidationDetailItem;
  source_provenance?: ValidationDetailItem;
}

export interface GeneratedQuestion {
  id: string;
  quiz_id: string;
  order_index: number;
  question_text: string;
  options?: string[] | null;
  answer: string;
  explanation?: string | null;
  difficulty: string;
  grade_min?: number;
  grade_max?: number;
  topic?: string;
  question_type: string;
  // present once the quiz API returns the format reading (see formatPayloads.ts for the shapes)
  question_format?: string | null;
  format_payload?: unknown;
  image_refs?: string[];
  question_images?: string[];
  answer_images?: string[];
  visual_clues?: string | null;
  audio_refs?: string[];
  question_audio?: string[];
  answer_audio?: string[];
  video_refs?: string[];
  question_video?: string[];
  raw_media_refs?: string[];
  validation_status: 'PASSED' | 'WARNING' | 'FAILED';
  validation_details?: ValidationDetails;
  duplicate_score: number;
  is_approved: boolean;
  created_at: string;
  retrieval_sources: RetrievalSource[];
}

export type QuizFormat =
  | 'TOURNAMENT_PRELIMS'
  | 'STAGE_FINALS'
  | 'CLASSROOM_WARMUP'
  | 'THEMATIC_SPECIAL'
  | 'GENERAL_CHAMPIONSHIP';

export interface Quiz {
  id: string;
  title: string;
  topic: string;
  subtopic?: string | null;
  quiz_format?: QuizFormat | string;
  audience_type?: string;
  grades?: number[];
  age_range?: string | null;
  grade_min?: number | null;
  grade_max?: number | null;
  difficulty: string;
  difficulty_distribution?: Record<string, number>;
  question_count: number;
  question_types: string[];
  generation_mode: string;
  tags?: string[];
  personality?: string;
  style: string;
  raw_prompt?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  questions: GeneratedQuestion[];
}

export interface TagItem {
  tag: string;
  count: number;
  topics: string[];
  co_occurring_tags: string[];
}

export interface TagPreviewResponse {
  topic?: string | null;
  tags: string[];
  total_available: number;
  exact_matches_count: number;
  cross_topic_tag_matches_count: number;
  difficulty_breakdown: Record<string, number>;
  suggested_mode: 'HISTORICAL' | 'NEW';
  sample_questions: {
    id: string;
    question_text: string;
    answer: string;
    difficulty: string;
    topic: string;
    tags: string[];
  }[];
}

export interface DashboardStats {
  years_of_knowledge?: number;
  total_documents: number;
  total_slides: number;
  total_questions: number;
  total_topics: number;
  total_quizzes: number;
  recent_documents: {
    id: string;
    title: string;
    filename: string;
    slide_count: number;
    question_count: number;
    created_at: string;
    status: string;
  }[];
  recent_quizzes: {
    id: string;
    title: string;
    topic: string;
    question_count: number;
    difficulty: string;
    grade_range: string;
    created_at: string;
  }[];
  top_topics: {
    topic: string;
    count: number;
  }[];
}

export interface IngestionStatus {
  document_id: string;
  status: string;
  progress_percentage: number;
  current_step: string;
  slides_processed: number;
  questions_extracted: number;
  error?: string | null;
  detail?: string | null;
  events?: { ts: number; msg: string }[] | null;
  memory_mb?: number | null;
  peak_memory_mb?: number | null;
  memory_limit_mb?: number | null;
}

// ---------------------------------------------------------------- QShala tagging
// Mirrors backend/app/models/qshala_tag.py (QshalaTag). One row per tagging run.
// `source` is RULE | LLM | NEIGHBOUR | HUMAN; `status` is draft | confirmed | superseded.
export interface QshalaTag {
  id: string;
  question_id: string;
  source: 'RULE' | 'LLM' | 'NEIGHBOUR' | 'HUMAN';
  status: 'draft' | 'confirmed' | 'superseded';
  model_version: string | null;
  taxonomy_version: string;

  topic_l1: string | null;
  subtopic_l2: string | null;
  keywords_l3: string[];
  secondary_l2: string[];
  entity_type: string | null;
  city_name: string | null;
  geo_region: string | null;
  india_international: 'IN' | 'INTL' | 'MIXED' | null;
  shelf_life: 'EVERGREEN' | 'REVIEW_YEARLY' | 'TIME_SENSITIVE' | null;
  sensitivity: 'NONE' | 'POLITICAL' | 'MATURE_REFERENCE' | null;
  fit: 'good' | 'weak' | 'none' | null;
  search_summary: string | null;

  confidence: number | null;
  verified_by: string | null;
  verified_at: string | null;
  verification_notes: string | null;
  fact_check_url: string | null;

  tokens_in: number | null;
  tokens_out: number | null;
  thinking_tokens: number | null;
  latency_ms: number | null;
  created_at: string | null;
}

export interface QshalaTagHistory {
  current: QshalaTag | null;
  history: QshalaTag[];
  changes: {
    id: string;
    change_type: 'PROMOTE' | 'SUPERSEDE' | 'HUMAN_OVERRIDE' | 'ROLLBACK';
    actor: string | null;
    reason: string | null;
    from_tag_id: string | null;
    to_tag_id: string | null;
    changed_at: string | null;
  }[];
}

export interface QshalaHumanTagInput {
  subtopic_l2: string;
  secondary_l2?: string[];
  keywords_l3?: string[];
  entity_type?: string;
  india_international?: 'IN' | 'INTL' | 'MIXED';
  city_name?: string;
  geo_region?: string;
  shelf_life?: 'EVERGREEN' | 'REVIEW_YEARLY' | 'TIME_SENSITIVE';
  sensitivity?: 'NONE' | 'POLITICAL' | 'MATURE_REFERENCE';
  fit?: 'good' | 'weak' | 'none';
  search_summary?: string;
  fact_check_url?: string;
  actor: string;
  notes?: string;
}

export interface QshalaTaxonomyDomain {
  l1: string;
  subtopics: {
    l2: string;
    description: string;
  }[];
}

export interface QshalaTaxonomy {
  version: string;
  domains: QshalaTaxonomyDomain[];
  l2_to_l1: Record<string, string>;
  l2_descriptions: Record<string, string>;
}

export interface QshalaSearchResponse {
  total: number;
  items: {
    question: {
      id: string;
      question_text: string;
      answer: string;
      document_id?: string;
    };
    tag: QshalaTag;
  }[];
}

export interface BatchFileRegisterItem {
  client_item_id: string;
  filename: string;
  size_bytes: number;
}

export interface BatchRegisterPayload {
  files: BatchFileRegisterItem[];
  client_id?: string;
}

export interface BatchRegisterResponse {
  batch_id: string;
}

export interface ActiveBatchResponse {
  batch_id: string | null;
}

export interface BatchTotals {
  total: number;
  completed: number;
  processing: number;
  queued: number;
  waiting: number;
  failed: number;
  cancelled: number;
}

export type BatchItemPhase =
  | 'WAITING'
  | 'UPLOADING'
  | 'QUEUED'
  | 'PROCESSING'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED';

export interface BatchItemStatus {
  client_item_id: string;
  filename: string;
  status: 'WAITING' | 'QUEUED' | 'PROCESSING' | 'COMPLETED' | 'FAILED' | 'CANCELLED' | 'CANCELLING';
  document_id: string | null;
  job_id: string | null;
  queue_position: number | null;
  slides: number;
  questions: number;
  step: string | null;
  percentage: number;
  error: string | null;
  size_bytes: number;
}

export interface BatchStatusResponse {
  batch_id: string;
  created_at: string | null;
  totals: BatchTotals;
  items: BatchItemStatus[];
}

export interface CancelPendingResponse {
  batch_id: string;
  cancelled_count: number;
}

export interface BatchLocalItem {
  clientItemId: string;
  file?: File;
  filename: string;
  size: number;
  phase: BatchItemPhase;
  docId?: string | null;
  jobId?: string | null;
  queuePosition?: number | null;
  slides?: number;
  questions?: number;
  step?: string | null;
  percentage: number;
  error?: string | null;
}
