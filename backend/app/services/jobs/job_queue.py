import asyncio
import os
import time
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Awaitable
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.models.job import IngestionJob
from backend.app.models.document import Document, Slide
from backend.app.models.question import Question
from backend.app.models.sharepoint import SharePointFile
from backend.app.services.storage.blob_storage import get_blob_service, VercelBlobService
from backend.app.services.ingestion.pptx_parser import PPTXParser
from backend.app.services.ingestion.pdf_parser import PDFParser
from backend.app.services.ingestion.slide_classifier import SlideClassifier
from backend.app.services.ingestion.question_extractor import QuestionExtractor
from backend.app.services.tagging.auto_tagger import AutoTagger
from backend.app.services.tagging.difficulty_engine import DifficultyEngine
from backend.app.services.deduplication.deduplicator import Deduplicator
from backend.app.services.ai.factory import get_embedding_provider, get_llm_provider

logger = logging.getLogger(__name__)

class JobQueueService:
    """
    Idempotent background job queue service designed for Vercel Serverless Function execution limits.
    Executes deck processing through 8 checkpointed steps with intra-step cursor timeout protection (<8s):
    1. DOWNLOAD
    2. EXTRACT_SLIDES
    3. EXTRACT_MEDIA
    4. TRANSCRIBE_OCR
    5. EMBED
    6. DEDUP_CHECK
    7. TAG
    8. INSERT
    """

    TIME_BUDGET_SECONDS = 7.5  # Yield before 8.0s guard

    def __init__(self, blob_service: Optional[VercelBlobService] = None):
        self.blob_service = blob_service or get_blob_service()
        self.classifier = SlideClassifier()
        self.extractor = QuestionExtractor()
        self.auto_tagger = AutoTagger()
        self.difficulty_engine = DifficultyEngine()
        self.deduplicator = Deduplicator()
        self.tmp_dir = settings.STORAGE_DIR / "tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    async def step_download(self, job: IngestionJob, db: Session):
        """Step 1: Download raw file from Vercel Blob into temporary storage."""
        if not job.blob_url:
            raise ValueError(f"Job {job.id} has no blob_url")

        local_filename = f"{job.id}_{job.filename}"
        local_path = self.tmp_dir / local_filename

        await self.blob_service.download_to_file(job.blob_url, local_path)

        step_data = dict(job.step_data or {})
        step_data["local_path"] = str(local_path)
        job.step_data = step_data
        job.current_step = "EXTRACT_SLIDES"
        job.updated_at = datetime.now(timezone.utc)
        db.commit()

    async def step_extract_slides(self, job: IngestionJob, db: Session):
        """Step 2: Parse presentation slides, classify slide types, and persist Document & Slide records."""
        step_data = dict(job.step_data or {})
        local_path = step_data.get("local_path")
        if not local_path or not os.path.exists(local_path):
            # Fallback if tmp file was lost across cold invocations
            local_filename = f"{job.id}_{job.filename}"
            local_path = str(self.tmp_dir / local_filename)
            await self.blob_service.download_to_file(job.blob_url, Path(local_path))
            step_data["local_path"] = local_path

        # Find or create Document
        doc = None
        if job.document_id:
            doc = db.query(Document).filter(Document.id == job.document_id).first()

        if not doc:
            doc_id = str(uuid.uuid4())
            file_ext = Path(job.filename).suffix.lower().lstrip(".")
            doc = Document(
                id=doc_id,
                filename=job.filename,
                title=Path(job.filename).stem.replace("_", " "),
                file_type=file_ext,
                storage_path=local_path,
                blob_url=job.blob_url,
                source=job.source_type or "SHAREPOINT",
                sharepoint_file_id=job.source_file_id,
                file_size_bytes=os.path.getsize(local_path) if os.path.exists(local_path) else 0,
                processing_status="PROCESSING"
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)
            job.document_id = doc.id

        ext = Path(job.filename).suffix.lower()
        if ext in [".pptx", ".ppt"]:
            parser = PPTXParser(local_path, doc.id, write_to_disk=False)
            raw_slides = parser.parse()
        elif ext == ".pdf":
            parser = PDFParser(local_path, doc.id)
            raw_slides = parser.parse()
        else:
            raise ValueError(f"Unsupported file extension: {ext}")

        deck_types = self.classifier.classify_deck(raw_slides)
        slide_records = []

        # Clear any partial slides for this doc
        db.query(Slide).filter(Slide.document_id == doc.id).delete()
        db.commit()

        for s, stype in zip(raw_slides, deck_types):
            s["slide_type"] = stype
            slide_obj = Slide(
                document_id=doc.id,
                slide_number=s["slide_number"],
                slide_type=stype,
                title=s.get("title"),
                extracted_text=s.get("extracted_text", ""),
                speaker_notes=s.get("speaker_notes", ""),
                image_paths=s.get("image_paths", []),
                has_images=s.get("has_images", False),
                metadata_json=s.get("metadata_json", {})
            )
            db.add(slide_obj)
            slide_records.append(slide_obj)

        db.commit()
        for s_obj in slide_records:
            db.refresh(s_obj)

        doc.slide_count = len(slide_records)
        db.commit()

        # Serialized slides for next steps (omit raw_bytes for JSON safety)
        step_data["slides"] = [
            {
                "id": s.id,
                "slide_number": s.slide_number,
                "slide_type": s.slide_type,
                "title": s.title,
                "extracted_text": s.extracted_text,
                "speaker_notes": s.speaker_notes,
                "image_paths": s.image_paths or [],
                "has_images": s.has_images,
            }
            for s in slide_records
        ]

        job.step_data = step_data
        job.current_step = "EXTRACT_MEDIA"
        job.updated_at = datetime.now(timezone.utc)
        db.commit()

    async def step_extract_media(self, job: IngestionJob, db: Session):
        """Step 3: Extract embedded images, audio, and video clips and upload to Vercel Blob."""
        step_data = dict(job.step_data or {})
        local_path = step_data.get("local_path")
        doc_id = job.document_id or str(uuid.uuid4())

        # Fallback if local scratch file was cleared across serverless cold starts
        if not local_path or not os.path.exists(local_path):
            local_filename = f"{job.id}_{job.filename}"
            local_path = str(self.tmp_dir / local_filename)
            await self.blob_service.download_to_file(job.blob_url, Path(local_path))
            step_data["local_path"] = local_path

        ext = Path(job.filename).suffix.lower()
        all_media = []
        if ext in [".pptx", ".ppt"] and local_path and os.path.exists(local_path):
            parser = PPTXParser(local_path, doc_id, write_to_disk=False)
            all_media = parser.extract_all_media()

        media_manifest = []
        for item in all_media:
            raw_bytes = item.get("raw_bytes")
            if not raw_bytes:
                continue

            fname = item["filename"]
            blob_path = f"media/{doc_id}/{fname}"
            blob_res = await self.blob_service.put(blob_path, raw_bytes, item.get("mime_type"))

            manifest_entry = {
                "media_id": str(uuid.uuid4()),
                "filename": fname,
                "slide_number": item["slide_number"],
                "media_type": item["media_type"],
                "blob_url": blob_res["url"],
                "mime_type": item["mime_type"],
                "local_path": item.get("local_path")
            }
            media_manifest.append(manifest_entry)

        step_data["media_manifest"] = media_manifest
        step_data["media_cursor"] = 0
        step_data["media_transcripts"] = {}
        job.step_data = step_data
        job.current_step = "TRANSCRIBE_OCR"
        job.updated_at = datetime.now(timezone.utc)
        db.commit()

    async def step_transcribe_ocr(self, job: IngestionJob, db: Session):
        """
        Step 4: Transcribe spoken clues from audio/video and perform Vision OCR on images.
        Uses intra-step cursor timeout protection (<8s) to yield before serverless timeout.
        """
        start_time = time.time()
        step_data = dict(job.step_data or {})
        manifest = step_data.get("media_manifest", [])
        cursor = step_data.get("media_cursor", 0)
        transcripts = dict(step_data.get("media_transcripts", {}))
        llm = get_llm_provider()

        batch_concurrency = 5
        while cursor < len(manifest):
            # Time-budget execution guard
            if (time.time() - start_time) > self.TIME_BUDGET_SECONDS:
                logger.info(f"Time budget reached for Job {job.id} at media cursor {cursor}/{len(manifest)}. Yielding.")
                step_data["media_cursor"] = cursor
                step_data["media_transcripts"] = transcripts
                job.step_data = step_data
                job.status = "PROCESSING"
                job.updated_at = datetime.now(timezone.utc)
                db.commit()
                return

            batch_slice = manifest[cursor : cursor + batch_concurrency]

            async def _transcribe_media_item(item):
                m_id = item["media_id"]
                m_type = item["media_type"]
                blob_url = item["blob_url"]
                mime = item.get("mime_type", "application/octet-stream")

                if m_id in transcripts:
                    return m_id, transcripts[m_id]

                try:
                    media_bytes = await self.blob_service.download(blob_url)
                    if m_type == "image":
                        desc = await llm.describe_image(media_bytes, mime)
                        return m_id, desc or f"[Visual clue: {item['filename']}]"
                    elif m_type in ["audio", "video"]:
                        transcript = await llm.transcribe_audio(media_bytes, mime)
                        return m_id, transcript or f"[{m_type.capitalize()} clue: {item['filename']}]"
                    else:
                        return m_id, ""
                except Exception as e:
                    logger.warning(f"Failed to transcribe media item {item['filename']}: {e}")
                    return m_id, ""

            results = await asyncio.gather(*[_transcribe_media_item(it) for it in batch_slice])
            for m_id, val in results:
                transcripts[m_id] = val

            cursor += len(batch_slice)

        step_data["media_cursor"] = len(manifest)
        step_data["media_transcripts"] = transcripts

        # Build canonical questions
        slides = step_data.get("slides", [])
        raw_questions = self.extractor.extract_from_slides(
            slides,
            doc_title=Path(job.filename).stem,
            doc_year=2024
        )

        canonical_questions = []
        for idx, q in enumerate(raw_questions):
            q_slide_num = q.get("slide_number")
            ans_slide_num = q.get("answer_slide_number")
            if q_slide_num is not None and ans_slide_num is not None and ans_slide_num >= q_slide_num:
                relevant_slides = set(range(q_slide_num, ans_slide_num + 1))
            elif q_slide_num is not None:
                relevant_slides = {q_slide_num}
            else:
                relevant_slides = set()

            # Map matching media items
            q_images = []
            q_visual_clues = []
            q_audios = []
            q_videos = []
            q_raw_media = []

            for m in manifest:
                if m.get("slide_number") in relevant_slides:
                    q_raw_media.append(m["blob_url"])
                    if m["media_type"] == "image":
                        q_images.append(m["blob_url"])
                        v_text = transcripts.get(m["media_id"], "")
                        if v_text:
                            q_visual_clues.append(v_text)
                    elif m["media_type"] == "audio":
                        t_text = transcripts.get(m["media_id"], "")
                        if t_text:
                            q_audios.append(t_text)
                    elif m["media_type"] == "video":
                        t_text = transcripts.get(m["media_id"], "")
                        if t_text:
                            q_videos.append(t_text)

            canonical_questions.append({
                "id": str(uuid.uuid4()),
                "canonical_idx": idx,
                "slide_id": q.get("slide_id"),
                "answer_slide_id": q.get("answer_slide_id"),
                "question_text": q["question_text"],
                "answer": q["answer"],
                "options": q.get("options"),
                "explanation": q.get("explanation", ""),
                "subtopic": q.get("subtopic"),
                "round_number": q.get("round_number"),
                "question_type": q.get("question_type", "SLIDE_QA"),
                "source_year": q.get("source_year", 2024),
                "source_slide_range": q.get("source_slide_range", f"Slide {q_slide_num}"),
                "image_refs": q_images or q.get("image_refs", []),
                "visual_clues": " | ".join(q_visual_clues) if q_visual_clues else None,
                "audio_transcript": " | ".join(q_audios) if q_audios else None,
                "video_transcript": " | ".join(q_videos) if q_videos else None,
                "raw_media_refs": q_raw_media,
                "speaker_notes": q.get("speaker_notes", "")
            })

        step_data["canonical_questions"] = canonical_questions
        job.step_data = step_data
        job.current_step = "EMBED"
        job.updated_at = datetime.now(timezone.utc)
        db.commit()

    async def step_embed(self, job: IngestionJob, db: Session):
        """Step 5: Generate semantic vector embeddings for canonical questions."""
        step_data = dict(job.step_data or {})
        canonical_questions = step_data.get("canonical_questions", [])
        embedding_provider = get_embedding_provider()

        texts_to_embed = [
            f"{q['question_text']} {q['answer']} {q.get('explanation') or ''} {q.get('visual_clues') or ''} {q.get('audio_transcript') or ''} {q.get('video_transcript') or ''}".strip()
            for q in canonical_questions
        ]

        embeddings = []
        if texts_to_embed:
            try:
                embeddings = [e if e is not None else [0.0] * 768
                              for e in await embedding_provider.get_embeddings(texts_to_embed)]
            except Exception as e:
                logger.warning(f"Job {job.id} embedding generation warning ({e}), fallback zero vectors")
                embeddings = [[0.0] * 768 for _ in texts_to_embed]

        step_data["embeddings"] = embeddings
        job.step_data = step_data
        job.current_step = "DEDUP_CHECK"
        job.updated_at = datetime.now(timezone.utc)
        db.commit()

    async def step_dedup_check(self, job: IngestionJob, db: Session):
        """
        Step 6: Two-tier deduplication check (Tier 1 hash pre-filter + Tier 2 vector lookup).
        Questions >= 0.92 similarity are flagged as 'POSSIBLE_DUPLICATE' for human review, NOT discarded.
        """
        step_data = dict(job.step_data or {})
        canonical_questions = step_data.get("canonical_questions", [])
        embeddings = step_data.get("embeddings", [])
        doc_id = job.document_id or str(uuid.uuid4())

        final_candidates, exact_duplicates = self.deduplicator.deduplicate_batch(
            db=db,
            candidates=canonical_questions,
            candidate_embeddings=embeddings,
            doc_id=doc_id,
            threshold=0.92
        )

        step_data["dedup_candidates"] = final_candidates
        step_data["exact_duplicates_count"] = len(exact_duplicates)
        job.step_data = step_data
        job.current_step = "TAG"
        job.updated_at = datetime.now(timezone.utc)
        db.commit()

    async def step_tag(self, job: IngestionJob, db: Session):
        """Step 7: Auto-tagging with multi-modal context (OCR clues + audio transcripts)."""
        step_data = dict(job.step_data or {})
        candidates = step_data.get("dedup_candidates", [])
        doc_title = Path(job.filename).stem

        tagged_questions = []
        for q in candidates:
            # 1. Multi-topic tagging with AI / rule-based fallback
            tags_data = await self.auto_tagger.tag_question_with_ai(
                question_text=q["question_text"],
                answer_text=q["answer"],
                explanation=q.get("explanation", ""),
                doc_title=doc_title,
                visual_clues=q.get("visual_clues") or "",
                audio_transcript=q.get("audio_transcript", ""),
                video_transcript=q.get("video_transcript", "")
            )

            # 2. Difficulty & Grade evaluation (multimodal questions set to 'Unrated')
            is_multimodal = bool(
                q.get("image_refs") or
                q.get("raw_media_refs") or
                q.get("audio_transcript") or
                q.get("video_transcript") or
                q.get("visual_clues")
            )
            diff_data = self.difficulty_engine.evaluate(
                question_text=q["question_text"],
                answer_text=q["answer"],
                explanation=q.get("explanation", ""),
                notes=q.get("speaker_notes", ""),
                is_multimodal=is_multimodal
            )

            q.update({
                "primary_topic": tags_data["primary_topic"],
                "topics": tags_data["topics"],
                "tags": tags_data["tags"],
                "question_hook": tags_data["question_hook"],
                "curiosity_score": tags_data["curiosity_score"],
                "temporal_nature": tags_data["temporal_nature"],
                "difficulty": diff_data["difficulty"],
                "difficulty_score": diff_data["difficulty_score"],
                "cognitive_level": diff_data["cognitive_level"],
                "grade_min": diff_data["grade_min"],
                "grade_max": diff_data["grade_max"],
                "audience_suitability": diff_data["audience_suitability"],
                "topic": tags_data["primary_topic"]
            })
            tagged_questions.append(q)

        step_data["tagged_questions"] = tagged_questions
        job.step_data = step_data
        job.current_step = "INSERT"
        job.updated_at = datetime.now(timezone.utc)
        db.commit()

    async def step_insert(self, job: IngestionJob, db: Session):
        """Step 8: Commit canonical normalized questions and complete the job."""
        step_data = dict(job.step_data or {})
        tagged_questions = step_data.get("tagged_questions", [])
        embeddings = step_data.get("embeddings", [])
        doc_id = job.document_id

        doc = db.query(Document).filter(Document.id == doc_id).first() if doc_id else None

        question_models = []
        for q_data in tagged_questions:
            c_idx = q_data.get("canonical_idx")
            emb = None
            if c_idx is not None and 0 <= c_idx < len(embeddings):
                emb = embeddings[c_idx]

            q_obj = Question(
                id=q_data.get("id") or str(uuid.uuid4()),
                content_hash=q_data.get("content_hash"),
                document_id=doc_id,
                slide_id=q_data.get("slide_id"),
                answer_slide_id=q_data.get("answer_slide_id"),
                question_text=q_data["question_text"],
                answer=q_data["answer"],
                options=q_data.get("options"),
                explanation=q_data.get("explanation"),
                topic=q_data["primary_topic"],
                subtopic=q_data.get("subtopic"),
                topics=q_data.get("topics", []),
                tags=q_data.get("tags", []),
                difficulty=q_data.get("difficulty", "Medium"),
                difficulty_score=q_data.get("difficulty_score", 0.50),
                cognitive_level=q_data.get("cognitive_level", "Recall / Remember"),
                grade_min=q_data.get("grade_min", 3),
                grade_max=q_data.get("grade_max", 12),
                audience_suitability=q_data.get("audience_suitability", []),
                question_hook=q_data.get("question_hook", "DIRECT_TRIVIA"),
                curiosity_score=q_data.get("curiosity_score", 7),
                temporal_nature=q_data.get("temporal_nature", "EVERGREEN"),
                provenance_decks=[{"document_id": doc_id, "slide_id": q_data.get("slide_id")}],
                occurrence_count=1,
                question_type=q_data.get("question_type", "SLIDE_QA"),
                question_format=q_data.get("question_format") or "STANDARD_QA",
                format_payload=q_data.get("format_payload"),
                play_mode=q_data.get("play_mode"),
                slot=q_data.get("slot") or "LIVE",
                topic_label=q_data.get("topic_label"),
                source_year=q_data.get("source_year", 2024),
                round_number=q_data.get("round_number"),
                # Multi-modal & duplicate fields
                image_refs=q_data.get("image_refs", []),
                visual_clues=q_data.get("visual_clues"),
                audio_transcript=q_data.get("audio_transcript"),
                video_transcript=q_data.get("video_transcript"),
                raw_media_refs=q_data.get("raw_media_refs", []),
                source_slide_range=q_data.get("source_slide_range"),
                duplicate_status=q_data.get("duplicate_status", "UNIQUE"),
                duplicate_similarity=q_data.get("duplicate_similarity"),
                duplicate_of_id=q_data.get("duplicate_of_id"),
                embedding=emb
            )
            db.add(q_obj)
            question_models.append(q_obj)

        if doc:
            doc.question_count = len(question_models)
            doc.processing_status = "COMPLETED"

        # Update SharePointFile if linked
        if job.source_file_id:
            sp_file = db.query(SharePointFile).filter(SharePointFile.id == job.source_file_id).first()
            if sp_file:
                sp_file.sync_status = "COMPLETED"
                sp_file.error_message = None

        # Clean up temporary scratch file
        local_path = step_data.get("local_path")
        if local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
            except Exception:
                pass

        job.status = "COMPLETED"
        job.current_step = "COMPLETED"
        job.completed_at = datetime.now(timezone.utc)
        job.updated_at = datetime.now(timezone.utc)
        db.commit()

    async def execute_step(self, job: IngestionJob, db: Session):
        """Dispatches execution to the handler for job.current_step."""
        step_handlers: Dict[str, Callable[[IngestionJob, Session], Awaitable[None]]] = {
            "DOWNLOAD": self.step_download,
            "EXTRACT_SLIDES": self.step_extract_slides,
            "EXTRACT_MEDIA": self.step_extract_media,
            "TRANSCRIBE_OCR": self.step_transcribe_ocr,
            "EMBED": self.step_embed,
            "DEDUP_CHECK": self.step_dedup_check,
            "TAG": self.step_tag,
            "INSERT": self.step_insert,
        }

        if job.current_step not in step_handlers:
            if job.current_step == "COMPLETED":
                return
            raise ValueError(f"Unknown step {job.current_step} for Job {job.id}")

        job.status = "PROCESSING"
        db.commit()

        try:
            handler = step_handlers[job.current_step]
            await handler(job, db)
        except Exception as e:
            logger.exception(f"Error executing step {job.current_step} on Job {job.id}: {e}")
            job.retry_count += 1
            job.error_message = str(e)
            if job.retry_count >= job.max_retries:
                job.status = "FAILED"
                if job.source_file_id:
                    sp_file = db.query(SharePointFile).filter(SharePointFile.id == job.source_file_id).first()
                    if sp_file:
                        sp_file.sync_status = "FAILED"
                        sp_file.error_message = str(e)
                if job.document_id:
                    doc = db.query(Document).filter(Document.id == job.document_id).first()
                    if doc:
                        doc.processing_status = "FAILED"
                        doc.processing_error = str(e)
            else:
                job.status = "PENDING"
            job.updated_at = datetime.now(timezone.utc)
            db.commit()
            raise

    async def process_next_job(self, db: Session) -> Optional[IngestionJob]:
        """Picks up the oldest pending or processing job and runs its next step."""
        job = (
            db.query(IngestionJob)
            .filter(IngestionJob.status.in_(["PENDING", "PROCESSING"]))
            .order_by(IngestionJob.created_at.asc())
            .first()
        )
        if not job:
            return None

        await self.execute_step(job, db)
        db.refresh(job)
        return job


_global_job_queue_service: Optional[JobQueueService] = None

def get_job_queue_service() -> JobQueueService:
    global _global_job_queue_service
    if _global_job_queue_service is None:
        _global_job_queue_service = JobQueueService()
    return _global_job_queue_service
