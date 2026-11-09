import os
import time
import asyncio
import logging
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from backend.app.models.document import Document, Slide
from backend.app.models.question import Question, generate_uuid
from backend.app.models.qshala_tag import QshalaTag
from backend.app.database import short_session
from backend.app.config import settings
from backend.app.services.ingestion.pptx_parser import PPTXParser
from backend.app.services.ingestion.pdf_parser import PDFParser
from backend.app.services.ingestion.cancellation import (
    IngestionCancelled,
    doc_cancel_requested,
    raise_if_cancelled,
    clear_doc_cancel,
    purge_document,
)
from backend.app.services.ingestion.slide_classifier import SlideClassifier
from backend.app.services.ingestion.question_extractor import QuestionExtractor
from backend.app.services.ingestion.deck_metadata import annotate_questions
from backend.app.models.question_meta import QuestionEditHistory
from backend.app.services.ingestion import progress
from backend.app.services.ingestion.vision_resize import shrink_for_vision
from backend.app.services.tagging.qshala_tagger import safe_error
from backend.app.services.question_meta_service import (
    upsert_deck_metadata, deck_row_to_info, ensure_question_metadata, apply_tag_to_meta,
)
from backend.app.services.tagging import topic_paths
from backend.app.services.tagging.qshala_rules import classify_taxonomy
from backend.app.services.tagging.auto_tagger import AutoTagger  # Kept for backward compatibility
from backend.app.services.tagging.qshala_tagger import QshalaTagger
from backend.app.services.tagging.difficulty_engine import DifficultyEngine

# Feature toggle: set to False to revert to legacy AutoTagger writing topic tags on upload
USE_QSHALA_TAGGER: bool = True
VISION_CONCURRENCY: int = int(os.getenv("VISION_CONCURRENCY", "12"))   # bounded parallel image-description calls (images are shrunk to <=1600px first, so 12 in flight fits the 2048 MB Cloud Run budget; set 6 on a 512 MB-1 GB host)
# Safety caps on image-description text. A description is normally ~60-80 words; these only ever bite on a
# runaway answer (one stored description was 69,000 characters). The tagger reads just the first 400-600
# characters, and the embedding input uses at most EMBED_VISUAL_CLUES_CHARS of it.
MAX_DESC_CHARS: int = int(os.getenv("MAX_DESC_CHARS", "1200"))             # per image
MAX_CLUES_CHARS: int = int(os.getenv("MAX_CLUES_CHARS", "2400"))           # per question, all images together
EMBED_VISUAL_CLUES_CHARS: int = int(os.getenv("EMBED_VISUAL_CLUES_CHARS", "500"))
TAG_CONCURRENCY: int = int(os.getenv("TAG_CONCURRENCY", "16"))   # parallel Gemini tagging calls per deck
# Questions that are exact repeats (same canonical question+answer hash) of a question already in the vault, or of
# an earlier question in this deck, are dropped by the deduplicator anyway. With this on (default) they skip the
# vision/enrichment/embedding stages instead of paying for results nobody reads. Set 0 to restore the old order.
# The parser writes every extracted picture to disk (SLIDES_DIR/<doc>/media/) before uploading it to R2, so the
# vision stage can read that local file instead of downloading the same bytes back over HTTP. Set 0 to always fetch.
VISION_PREFER_LOCAL: bool = os.getenv("VISION_PREFER_LOCAL", "1").strip().lower() not in ("0", "false", "no", "off")


def _env_tristate(name: str) -> Optional[bool]:
    v = os.getenv(name, "auto").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return None


def running_in_job_worker() -> bool:
    """True inside a Cloud Run Job container (Cloud Run sets CLOUD_RUN_JOB there). Its disk is RAM, so disk is memory."""
    return bool(os.environ.get("CLOUD_RUN_JOB"))


def keep_local_media(r2_configured: bool) -> bool:
    """Whether the parser keeps its extracted pictures on local disk (SLIDES_DIR/<doc>/media/) as well as uploading
    them. KEEP_LOCAL_MEDIA=1/0 forces it. auto (default): keep, except in a Job worker that has R2, where a second
    copy of every picture would sit in RAM; the parser then stages one file at a time and vision downloads from R2."""
    forced = _env_tristate("KEEP_LOCAL_MEDIA")
    if forced is not None:
        return forced
    return not (running_in_job_worker() and r2_configured)


def delete_raw_deck_after_parse() -> bool:
    """Whether the local copy of the uploaded deck is deleted once parsing is done (the copy in R2 stays, so a retried
    task downloads it again). DELETE_RAW_DECK_AFTER_PARSE=1/0 forces it; auto (default): only in a Job worker."""
    forced = _env_tristate("DELETE_RAW_DECK_AFTER_PARSE")
    return running_in_job_worker() if forced is None else forced
# What to do when an uploaded deck file is byte-identical (SHA-256) to a deck already ingested to completion.
#   process (default): ingest it as usual (its questions become "Repeated" entries on the originals).
#   skip: mark it completed straight away with no questions and no AI/R2 work. NOTE: skipping means those
#         repeat records are NOT written for the re-upload.
IDENTICAL_REUPLOAD_ACTION: str = os.getenv("IDENTICAL_REUPLOAD_ACTION", "process").strip().lower()
DEDUP_BEFORE_AI: bool = os.getenv("DEDUP_BEFORE_AI", "1").strip().lower() not in ("0", "false", "no", "off")
from backend.app.services.deduplication.deduplicator import Deduplicator
from backend.app.services.ai.factory import get_embedding_provider, get_llm_provider

logger = logging.getLogger(__name__)

# Global tracker for real-time progress polling.
# Entries are evicted after _REGISTRY_TTL_S seconds (completed/failed) or _REGISTRY_MAX entries.
INGESTION_STATUS_REGISTRY: Dict[str, Dict[str, Any]] = {}
_REGISTRY_TTL_S: float = float(os.getenv("INGESTION_REGISTRY_TTL_S", "3600"))
_REGISTRY_MAX: int = 200


def _evict_stale_registry() -> None:
    """Remove completed/failed entries older than _REGISTRY_TTL_S, and cap at _REGISTRY_MAX."""
    if len(INGESTION_STATUS_REGISTRY) <= _REGISTRY_MAX // 2:
        return
    now = time.time()
    to_remove = []
    for doc_id, entry in INGESTION_STATUS_REGISTRY.items():
        status = entry.get("status", "")
        ts = entry.get("_ts", 0)
        if status in ("COMPLETED", "FAILED", "CANCELLED") and (now - ts) > _REGISTRY_TTL_S:
            to_remove.append(doc_id)
    for doc_id in to_remove:
        INGESTION_STATUS_REGISTRY.pop(doc_id, None)
    if len(INGESTION_STATUS_REGISTRY) > _REGISTRY_MAX:
        sorted_entries = sorted(INGESTION_STATUS_REGISTRY.items(), key=lambda x: x[1].get("_ts", 0))
        for doc_id, _ in sorted_entries[:len(INGESTION_STATUS_REGISTRY) - _REGISTRY_MAX]:
            INGESTION_STATUS_REGISTRY.pop(doc_id, None)


def _ensure_clean(db: Session, where: str) -> None:
    """Enforce the ingestion invariant: no DB transaction may stay open across a slow AI/network
    stage. If one is open at a phase boundary, commit it (ending the idle window Neon would kill)
    and log where — so a regression is visible instead of silently reintroducing the
    IdleInTransactionSessionTimeout crash.
    """
    try:
        if db.in_transaction():
            logger.info("ingestion: committing open transaction before %s", where)
            db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def embedding_model_label(provider) -> Optional[str]:
    """Short name stored in questions.embedding_model for vectors this provider produced, or None when the provider
    may have substituted local vectors (so the label would not be trustworthy)."""
    if getattr(provider, "may_substitute_local", False):
        return None
    model = getattr(provider, "model", None)
    return str(model or type(provider).__name__)[:64]


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    """Streaming SHA-256 of a file (1 MB at a time, so a 700 MB deck never sits in memory)."""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def find_identical_deck(db: Session, file_hash: str, exclude_id: str) -> Optional[Document]:
    """A different deck that finished ingesting from byte-identical file content, if any."""
    return (db.query(Document)
            .filter(Document.file_sha256 == file_hash, Document.id != exclude_id,
                    Document.processing_status == "COMPLETED")
            .order_by(Document.created_at.asc()).first())


def local_media_for_url(url: str, document_id: str) -> Optional["Path"]:
    """The on-disk copy of a media URL this deck's own parser stored, or None.

    Only URLs of the form <base>/slides/<document_id>/media/<file> map to SLIDES_DIR/<document_id>/media/<file>,
    and <file> must be a single plain name (no separators, no '..'), so a crafted URL can never point elsewhere."""
    from pathlib import Path
    marker = f"/slides/{document_id}/media/"
    pos = url.find(marker)
    if pos < 0:
        return None
    name = url[pos + len(marker):].split("?", 1)[0].split("#", 1)[0]
    if not name or name != Path(name).name or name in (".", "..") or "\\" in name or "/" in name:
        return None
    candidate = settings.SLIDES_DIR / document_id / "media" / name
    return candidate if candidate.is_file() else None


# A retried run (Cloud Run task retry, reaper requeue) starts on a document that may already hold the slides and
# questions its failed predecessor committed; without clearing them the retry would add a second copy of everything.
RESET_PARTIAL_ON_RETRY: bool = os.getenv("RESET_PARTIAL_ON_RETRY", "1").strip().lower() not in ("0", "false", "no", "off")


def clear_partial_ingestion(db: Session, doc: Document) -> int:
    """Delete the slides and questions left on `doc` by an earlier, unfinished attempt. Returns how many questions went.
    A COMPLETED document is never touched. Commits."""
    if doc.processing_status == "COMPLETED":
        return 0
    from backend.app.services.question_meta_service import remove_repeats_for_document
    n_q = db.query(Question).filter(Question.document_id == doc.id).count()
    n_s = db.query(Slide).filter(Slide.document_id == doc.id).count()
    if not n_q and not n_s:
        return 0
    remove_repeats_for_document(db, doc.id)          # other questions stop listing this deck as a repeat
    for q in list(doc.questions):
        db.delete(q)
    for s in list(doc.slides):
        db.delete(s)
    doc.question_count = 0
    doc.slide_count = 0
    db.commit()
    return n_q


def exact_duplicate_indices(db: Session, questions: list) -> set:
    """Indices of questions the deduplicator's tier-1 check would drop as exact repeats: the content hash
    already exists in the vault, or the same hash appeared earlier in this batch. Uses the same hash and the
    same rule (Deduplicator.tier1_exact_indices) as Deduplicator.deduplicate_batch. deduplicate_batch still does its
    own vault lookup afterwards on purpose: other decks can land while this one is in its AI stages."""
    hashes = [Deduplicator.compute_hash(q.get("question_text") or "", q.get("answer") or "") for q in questions]
    unique = list(dict.fromkeys(hashes))
    in_vault: set = set()
    for i in range(0, len(unique), 500):
        rows = db.query(Question.content_hash).filter(Question.content_hash.in_(unique[i:i + 500])).all()
        in_vault.update(r[0] for r in rows)
    return Deduplicator.tier1_exact_indices(hashes, in_vault)


def _stage_log(document_id: str, stage: str, t0: float) -> float:
    """Log wall-clock + process memory for a finished stage, warn once (soft, non-blocking) if
    memory is getting close to the container's limit, and return a new start time."""
    rss = None
    try:
        from backend.app.services.ingestion import progress as _p
        mem = _p.memory_mb() or {}
        rss = mem.get("memory_mb")
        _p.maybe_warn_memory(INGESTION_STATUS_REGISTRY.get(document_id), mem)
    except Exception:
        pass
    dt = time.perf_counter() - t0
    logger.info("stage[%s] %-22s %6.1fs  mem=%s MB", document_id[:8], stage, dt, rss)
    return time.perf_counter()

class IngestionPipeline:
    def __init__(self, db: Optional[Session] = None):
        self._external_db = db
        self.classifier = SlideClassifier()
        self.extractor = QuestionExtractor()
        self.auto_tagger = AutoTagger()
        self.qshala_tagger = QshalaTagger()
        self.difficulty_engine = DifficultyEngine()
        self.deduplicator = Deduplicator()
        self.embedding_provider = get_embedding_provider()
        self.llm_provider = get_llm_provider()

    async def run(self, document_id: str):
        _evict_stale_registry()
        INGESTION_STATUS_REGISTRY[document_id] = {
            "document_id": document_id,
            "status": "PROCESSING",
            "progress_percentage": 5,
            "current_step": "1. Extracting content from presentation",
            "slides_processed": 0,
            "questions_extracted": 0,
            "error": None,
            "detail": "Upload received. Starting to process the deck.",
            "events": [],
            "_ts": time.time(),
        }
        progress.event(INGESTION_STATUS_REGISTRY[document_id], "Upload received, starting to process the deck")

        own_session = False
        db = self._external_db
        if db is None:
            from backend.app.database import SessionLocal
            db = SessionLocal()
            own_session = True

        try:
            t0 = time.perf_counter()
            doc = db.query(Document).filter(Document.id == document_id).first()
            if not doc:
                INGESTION_STATUS_REGISTRY[document_id]["status"] = "FAILED"
                INGESTION_STATUS_REGISTRY[document_id]["error"] = "Document not found"
                return

            if RESET_PARTIAL_ON_RETRY:
                try:
                    _cleared = clear_partial_ingestion(db, doc)
                    if _cleared:
                        progress.event(INGESTION_STATUS_REGISTRY[document_id],
                                       f"Cleared {_cleared} questions left by an earlier unfinished attempt")
                except Exception as _clr_err:
                    db.rollback()
                    logger.warning("could not clear a previous attempt's rows: %s", safe_error(_clr_err))
                    doc = db.query(Document).filter(Document.id == document_id).first()

            doc.processing_status = "PROCESSING"
            db.commit()

            file_ext = os.path.splitext(doc.filename)[1].lower()

            # Fingerprint the file so an identical re-upload can be recognised. Never blocks ingestion.
            try:
                if doc.storage_path and os.path.exists(doc.storage_path):
                    file_hash = await asyncio.to_thread(sha256_file, doc.storage_path)
                    twin = find_identical_deck(db, file_hash, doc.id)
                    doc.file_sha256 = file_hash
                    db.commit()
                    if twin is not None:
                        progress.event(INGESTION_STATUS_REGISTRY[document_id],
                                       f"This file is byte-identical to '{twin.title}', which is already ingested")
                        if IDENTICAL_REUPLOAD_ACTION == "skip":
                            doc.question_count = 0
                            doc.slide_count = 0
                            doc.processing_status = "COMPLETED"
                            doc.processing_error = None
                            db.commit()
                            INGESTION_STATUS_REGISTRY[document_id].update({
                                "progress_percentage": 100, "status": "COMPLETED",
                                "current_step": f"Skipped: identical to '{twin.title}', already ingested",
                                "duplicates_filtered": 0,
                            })
                            try:
                                from backend.app.routers.stats import invalidate_stats_cache
                                invalidate_stats_cache()
                            except Exception:
                                pass
                            return
            except Exception as hash_err:
                logger.warning("deck fingerprint check failed (continuing normally): %s", safe_error(hash_err))
                try:
                    db.rollback()
                except Exception:
                    pass

            # Step 1 & 2: Extracting and Processing Slides
            INGESTION_STATUS_REGISTRY[document_id].update({
                "progress_percentage": 20,
                "current_step": "2. Processing slides and extracting visual assets"
            })

            if file_ext in [".pptx", ".ppt"]:
                try:
                    from backend.app.services.storage.r2_storage import get_media_storage
                    _r2_on = get_media_storage() is not None
                except Exception:
                    _r2_on = False
                parser = PPTXParser(doc.storage_path, doc.id, write_to_disk=keep_local_media(_r2_on))
                parser.should_cancel = lambda: doc_cancel_requested(document_id)
                parser.on_progress = lambda detail, pct=None: progress.update(
                    INGESTION_STATUS_REGISTRY[document_id], detail=detail, pct=pct)
                try:
                    _mb = os.path.getsize(doc.storage_path) / 1048576.0
                    progress.event(INGESTION_STATUS_REGISTRY[document_id], f"Opening the deck ({_mb:.0f} MB). Large decks take a while to load.")
                except Exception:
                    pass
                # Parse in a worker thread: it is blocking, and on a big deck it would otherwise
                # freeze the event loop so a Cancel request couldn't even be received.
                raw_slides = await asyncio.to_thread(parser.parse)
            elif file_ext == ".pdf":
                parser = PDFParser(doc.storage_path, doc.id)
                raw_slides = await asyncio.to_thread(parser.parse)
            else:
                raise ValueError(f"Unsupported file format: {file_ext}")

            if delete_raw_deck_after_parse() and doc.storage_path:
                # Everything the later stages need is now in raw_slides, the database and R2. Free the deck's disk
                # (RAM, on Cloud Run) before the memory-hungry vision stage; a retried task downloads it again.
                try:
                    os.remove(doc.storage_path)
                    progress.event(INGESTION_STATUS_REGISTRY[document_id], "Freed the temporary copy of the deck")
                except FileNotFoundError:
                    pass
                except OSError as _rm_err:
                    logger.warning("could not delete the local deck copy: %s", type(_rm_err).__name__)

            raise_if_cancelled(document_id)
            progress.event(INGESTION_STATUS_REGISTRY[document_id], f"Read {len(raw_slides)} slides and saved their pictures")

            # Classify each slide using 2-pass contextual deck classifier
            classified_slides = []
            slide_models = []
            deck_types = self.classifier.classify_deck(raw_slides)

            for s, slide_type in zip(raw_slides, deck_types):
                s["slide_type"] = slide_type
                classified_slides.append(s)

                slide_obj = Slide(
                    document_id=doc.id,
                    slide_number=s["slide_number"],
                    slide_type=slide_type,
                    title=s["title"],
                    extracted_text=s["extracted_text"],
                    speaker_notes=s["speaker_notes"],
                    image_paths=s["image_paths"],
                    has_images=s["has_images"],
                    metadata_json=s["metadata_json"]
                )
                db.add(slide_obj)
                slide_models.append(slide_obj)

            db.flush()  # Assign slide IDs
            _kinds: Dict[str, int] = {}
            for _t in deck_types:
                _kinds[_t] = _kinds.get(_t, 0) + 1
            _other = len(deck_types) - _kinds.get("QUESTION", 0) - _kinds.get("ANSWER", 0)
            progress.event(INGESTION_STATUS_REGISTRY[document_id],
                           f"Sorted slides: {_kinds.get('QUESTION', 0)} question, {_kinds.get('ANSWER', 0)} answer, "
                           f"{_other} other (titles, rounds, scoreboards: not sent to the AI)")
            doc.slide_count = len(slide_models)

            INGESTION_STATUS_REGISTRY[document_id].update({
                "progress_percentage": 45,
                "slides_processed": len(slide_models),
                "current_step": "3. Detecting questions and quiz patterns"
            })

            # Map slide objects back to classified slides with IDs, then CLOSE phase A:
            # slides are durable and no transaction stays open into the slow AI stages below.
            for i, s_model in enumerate(slide_models):
                classified_slides[i]["id"] = s_model.id
            db.commit()
            t0 = _stage_log(document_id, "A:parse+slides", t0)

            # Step 3, 4, 5: Question Extraction & Classification
            INGESTION_STATUS_REGISTRY[document_id].update({
                "progress_percentage": 60,
                "current_step": "4. Detecting answers, options, and difficulty levels"
            })

            raw_questions = self.extractor.extract_from_slides(
                classified_slides,
                doc_title=doc.title,
                doc_year=doc.year
            )

            progress.event(INGESTION_STATUS_REGISTRY[document_id], f"Found {len(raw_questions)} questions")

            # Deck details from the file name, then the no-AI per-question fields
            # (question number, format, star, answer type, language).
            deck_row = upsert_deck_metadata(db, doc)
            annotate_questions(raw_questions, classified_slides, deck_row_to_info(deck_row))
            # Mid-confidence format readings (0.7-0.9) get one optional Gemini look (FORMAT_VISION_CHECK=1); the rest
            # are accepted (>= 0.9) or left flagged for review (< 0.7) without any AI call.
            try:
                from backend.app.services.ingestion.format_vision import vision_check_enabled, verify_band
                if vision_check_enabled():
                    _fv = await verify_band(raw_questions, self.llm_provider)
                    if _fv.get('checked') or _fv.get('skipped'):
                        progress.event(INGESTION_STATUS_REGISTRY[document_id], f"Format check: {_fv}")
            except Exception as _fv_err:
                logger.warning('format vision check skipped: %s', safe_error(_fv_err))
            # Show a clean quiz name in Recent Uploads instead of the raw ".pptx" filename: the deck
            # parser already pulled a quiz_name out of the filename. Only override an auto-derived
            # title (the filename), never a title the user typed in.
            if getattr(deck_row, "quiz_name", None) and (doc.title or "") in ("", doc.filename,
                    os.path.splitext(doc.filename)[0].replace("_", " ").title()):
                doc.title = deck_row.quiz_name
            # Values needed later, captured BEFORE the commit expires the ORM rows.
            deck_year = deck_row.deck_year or doc.year
            doc_title = doc.title
            # CLOSE phase A/B DB work: the vision + embedding stages below touch no DB, so no
            # transaction is held while they run (minutes). This is the fix for the recurring
            # IdleInTransactionSessionTimeout — not the engine flag, which Neon's pooler may ignore.
            db.commit()
            _ensure_clean(db, "vision/embedding stage")

            # Step 5: Multi-Topic Auto-Tagging & Pedagogical Difficulty Scoring
            INGESTION_STATUS_REGISTRY[document_id].update({
                "progress_percentage": 75,
                "questions_extracted": len(raw_questions),
                "current_step": "5. Multimodal visual inspection & intelligent tagging"
            })

            # Describe question images in parallel (each is a separate vision call; they used to run one by one).
            # A picture reused across several questions (a round's title image, a repeated
            # logo or answer slide) is only described once: callers for the same path share
            # one in-flight future rather than each firing their own Gemini call.
            # Exact repeats are dropped by the deduplicator later (their provenance is still recorded there), so
            # they skip vision, enrichment and embedding. Everything kept sees exactly the same inputs as before.
            skip_idx: set = set()
            if DEDUP_BEFORE_AI and raw_questions:
                try:
                    skip_idx = exact_duplicate_indices(db, raw_questions)
                except Exception as pre_err:
                    logger.warning("pre-AI duplicate check failed, processing every question: %s", safe_error(pre_err))
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    skip_idx = set()
                _ensure_clean(db, "vision/embedding stage")
                if skip_idx:
                    progress.event(INGESTION_STATUS_REGISTRY[document_id],
                                   f"{len(skip_idx)} repeated questions skip the AI stages (exact matches of questions already held)")
            work_questions = [q for i, q in enumerate(raw_questions) if i not in skip_idx]
            for _i, _q in enumerate(raw_questions):
                _q["_emb_idx"] = _i
            import httpx as _httpx
            _http = _httpx.AsyncClient(
                timeout=30.0, headers={"User-Agent": "Mozilla/5.0 (compatible; QShalaIngestion/1.0)"},
                limits=_httpx.Limits(max_connections=VISION_CONCURRENCY * 2, max_keepalive_connections=VISION_CONCURRENCY))
            _vision_cache: Dict[str, asyncio.Future] = {}
            _vision_cache_lock = asyncio.Lock()
            _vision_reused = {"n": 0}
            # Bounds the images being fetched/shrunk/described at once (each holds its bytes in memory).
            vision_sem = asyncio.Semaphore(VISION_CONCURRENCY)
            from backend.app.services.ai.gemini_provider import usage_snapshot
            _usage_before = usage_snapshot()

            async def _describe_one_image(rel_img_path: str) -> Optional[str]:
                async with _vision_cache_lock:
                    fut = _vision_cache.get(rel_img_path)
                    if fut is None:
                        fut = asyncio.get_event_loop().create_future()
                        _vision_cache[rel_img_path] = fut
                        owner = True
                    else:
                        _vision_reused["n"] += 1
                        owner = False
                if not owner:
                    # Wait for the owner OUTSIDE the lock, so one slow shared picture never blocks
                    # unrelated images from starting.
                    return await fut

                desc: Optional[str] = None
                _holding = False
                try:
                    await vision_sem.acquire()
                    _holding = True
                    disk_path = None
                    img_bytes = None
                    ext = None

                    if rel_img_path.startswith("http://") or rel_img_path.startswith("https://"):
                        # Media stored in Cloudflare R2 (or any other remote object store). The parser just
                        # wrote this very file to local disk before uploading it, so read that copy when it is
                        # there (same bytes, no download); otherwise fetch it over HTTP.
                        if VISION_PREFER_LOCAL:
                            local_copy = local_media_for_url(rel_img_path, document_id)
                            if local_copy is not None:
                                try:
                                    img_bytes = await asyncio.to_thread(local_copy.read_bytes)
                                    ext = local_copy.suffix.lower()
                                except Exception as local_err:
                                    img_bytes = None
                                    logger.warning(f"Failed to read local copy of {rel_img_path}: {local_err}")
                        if img_bytes is None:
                            try:
                                # Same fix as exporter.py's _download_url_to_file: a default client
                                # User-Agent can get a blanket 403 from CDN/edge bot-protection on R2's public
                                # pub-*.r2.dev URLs. One pooled client serves the whole deck.
                                resp = await _http.get(rel_img_path)
                                resp.raise_for_status()
                                img_bytes = resp.content
                                ext = os.path.splitext(rel_img_path)[1].lower()
                            except Exception as fetch_err:
                                logger.warning(f"Failed to fetch remote image {rel_img_path}: {fetch_err}")
                    elif rel_img_path.startswith("/static/slides/"):
                        sub = rel_img_path[len("/static/slides/"):]
                        cand = settings.SLIDES_DIR / sub
                        if cand.exists():
                            disk_path = cand
                    elif rel_img_path.startswith("/static/"):
                        cand = settings.STORAGE_DIR / rel_img_path.lstrip("/static/")
                        if cand.exists():
                            disk_path = cand

                    if img_bytes is None and disk_path and disk_path.exists():
                        try:
                            with open(disk_path, "rb") as f:
                                img_bytes = f.read()
                            ext = disk_path.suffix.lower()
                        except Exception as read_err:
                            logger.warning(f"Failed to read local image {rel_img_path}: {read_err}")

                    if img_bytes:
                        try:
                            mime = "image/png" if ext == ".png" else ("image/jpeg" if ext in [".jpg", ".jpeg"] else "image/webp")
                            # Shrink in memory only -- not touching the stored file -- so a deck
                            # with many large pictures doesn't hold full-res bytes for every
                            # concurrent vision call at once.
                            vision_bytes, mime = await asyncio.to_thread(shrink_for_vision, img_bytes, mime)
                            raw_desc = await self.llm_provider.describe_image(vision_bytes, mime_type=mime)
                            if raw_desc and raw_desc.strip():
                                desc = raw_desc.strip()[:MAX_DESC_CHARS]
                        except Exception as vision_err:
                            logger.warning(f"Failed to describe image {rel_img_path}: {vision_err}")
                finally:
                    if _holding:
                        vision_sem.release()
                    if not fut.done():
                        fut.set_result(desc)
                return desc

            async def _describe_images(q):
                target_imgs = list(q.get("question_images") or q.get("image_refs") or [])
                # A question's pictures are described in parallel (the semaphore above bounds the total);
                # gather keeps them in their original order.
                descs = await asyncio.gather(*[_describe_one_image(p) for p in target_imgs])
                visual_clues_list = [d for d in descs if d]

                visual_clues_str = "\n".join(visual_clues_list)[:MAX_CLUES_CHARS] if visual_clues_list else None
                q["visual_clues"] = visual_clues_str
                return visual_clues_str

            _img_total = sum(1 for _q in work_questions if (_q.get("question_images") or _q.get("image_refs")))
            _img_done = {"n": 0}

            async def _describe_guarded(q):
                raise_if_cancelled(document_id)
                out = await _describe_images(q)
                if q.get("question_images") or q.get("image_refs"):
                    _img_done["n"] += 1
                    progress.update(INGESTION_STATUS_REGISTRY[document_id],
                                    detail=f"Describing question images with AI ({_img_done['n']}/{_img_total})")
                return out

            INGESTION_STATUS_REGISTRY[document_id]["current_step"] = "5. Reading question images"
            if _img_total:
                progress.event(INGESTION_STATUS_REGISTRY[document_id], f"Describing images for {_img_total} questions with AI")
            try:
                await asyncio.gather(*[_describe_guarded(q) for q in work_questions])
            finally:
                await _http.aclose()
            if _vision_reused["n"]:
                progress.event(INGESTION_STATUS_REGISTRY[document_id],
                               f"{_vision_reused['n']} repeated pictures reused their earlier description (skipped the AI call)")
            _vu = {k: usage_snapshot().get("vision", {}).get(k, 0) - _usage_before.get("vision", {}).get(k, 0)
                   for k in ("calls", "in", "out", "think", "cached")}
            if _vu["calls"]:
                try:
                    from backend.app.services.ai.usage_cost import save_usage
                    with short_session() as _udb:
                        save_usage(_udb, document_id, "vision", getattr(self.llm_provider, "model", None), _vu)
                        _udb.commit()
                except Exception as _ue:
                    logger.warning("vision usage row not saved: %s", type(_ue).__name__)
                logger.info("vision usage for %s: %s", document_id, _vu)
                progress.event(INGESTION_STATUS_REGISTRY[document_id],
                               f"Image AI usage: {_vu['calls']} calls, {_vu['in']:,} input / {_vu['out']:,} output / "
                               f"{_vu['think']:,} thinking tokens")

            enriched_questions = []
            for q in work_questions:
                raise_if_cancelled(document_id)
                visual_clues_str = q.get("visual_clues")

                # 1. Multi-topic and entity tagging (including visual clues)
                is_multimodal = bool(
                    q.get("image_refs") or
                    q.get("question_images") or
                    q.get("audio_refs") or
                    q.get("question_audio") or
                    q.get("video_refs") or
                    q.get("question_videos") or
                    q.get("media_items") or
                    q.get("audio_transcript") or
                    q.get("video_transcript") or
                    q.get("raw_media_refs")
                )

                if not USE_QSHALA_TAGGER:
                    tags_data = await self.auto_tagger.tag_question_with_ai(
                        question_text=q["question_text"],
                        answer_text=q["answer"],
                        explanation=q.get("explanation", ""),
                        doc_title=doc_title,
                        visual_clues=visual_clues_str or q.get("visual_clues", "") or "",
                        audio_transcript=q.get("audio_transcript", ""),
                        video_transcript=q.get("video_transcript", ""),
                        subtopic=q.get("subtopic")
                    )
                else:
                    # Legacy AutoTagger bypassed: QshalaTagger tags will be populated below
                    tags_data = {
                        "primary_topic": None,
                        "topics": [],
                        "subtopic": q.get("subtopic"),
                        "tags": [],
                        "question_hook": "DIRECT_TRIVIA",
                        "curiosity_score": 7,
                        "temporal_nature": "EVERGREEN"
                    }
                # 2. Difficulty & Grade evaluation (multimodal questions set to 'Unrated')
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
                    "subtopic": q.get("subtopic") or tags_data.get("subtopic"),
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
                enriched_questions.append(q)

            raise_if_cancelled(document_id)

            # Step 6: Generate Semantic Embeddings
            INGESTION_STATUS_REGISTRY[document_id].update({
                "progress_percentage": 85,
                "current_step": "6. Generating semantic vector embeddings"
            })

            texts_to_embed = [
                f"{q.get('primary_topic') or ''} {' '.join(q.get('topics') or [])} {q['question_text']} {q['answer']} {(q.get('visual_clues') or '')[:EMBED_VISUAL_CLUES_CHARS]} {q.get('explanation') or ''}".strip()
                for q in enriched_questions
            ]

            embeddings = []
            if texts_to_embed:
                try:
                    embeddings = await self.embedding_provider.get_embeddings(texts_to_embed)
                except Exception as e:
                    # No vector is better than a fake one: zero vectors have an undefined cosine distance and
                    # never rank. The questions are stored without an embedding and can be re-embedded later.
                    logger.warning(f"Embedding generation error ({e}); storing these questions without an embedding")
                    embeddings = [None for _ in texts_to_embed]

            # Re-align to the full question list: every question keeps its original position (_emb_idx), and the
            # skipped exact repeats get an empty vector (the deduplicator never reads it for an exact match).
            emb_by_idx = {q["_emb_idx"]: emb for q, emb in zip(enriched_questions, embeddings)}
            embeddings = [emb_by_idx.get(i, []) for i in range(len(raw_questions))]
            enriched_by_idx = {q["_emb_idx"]: q for q in enriched_questions}
            enriched_questions = [enriched_by_idx.get(i, raw_questions[i]) for i in range(len(raw_questions))]

            raise_if_cancelled(document_id)

            # Step 7: Deduplication Check (Tier 1 O(1) Hash + Tier 2 Semantic ANN)
            INGESTION_STATUS_REGISTRY[document_id].update({
                "progress_percentage": 92,
                "current_step": "7. Deduplicating questions against knowledge base"
            })

            t0 = _stage_log(document_id, "B:vision+embed+enrich", t0)

            # Phase C begins. The connection has been idle through the AI stages, so the first
            # query here must run on a fresh, pre-pinged connection. _ensure_clean guarantees no
            # stale transaction is open; the retry covers the case where the pooled connection was
            # dropped server-side during the idle window.
            _ensure_clean(db, "dedup")
            from sqlalchemy.exc import OperationalError, DBAPIError
            try:
                unique_candidates, duplicates = self.deduplicator.deduplicate_batch(
                    db=db, candidates=enriched_questions,
                    candidate_embeddings=embeddings, doc_id=doc.id)
            except (OperationalError, DBAPIError) as db_err:
                logger.warning("dedup hit a stale connection, retrying once: %s", safe_error(db_err))
                try:
                    db.rollback()
                except Exception:
                    pass
                unique_candidates, duplicates = self.deduplicator.deduplicate_batch(
                    db=db, candidates=enriched_questions,
                    candidate_embeddings=embeddings, doc_id=doc.id)

            raise_if_cancelled(document_id)

            # Step 8: Indexing Unique Records
            INGESTION_STATUS_REGISTRY[document_id].update({
                "progress_percentage": 96,
                "current_step": f"8. Storing {len(unique_candidates)} unique questions ({len(duplicates)} replicas linked)"
            })

            question_models = []
            for q_data in unique_candidates:
                # Find matching embedding
                orig_idx = q_data.get("_emb_idx")
                emb = embeddings[orig_idx] if orig_idx is not None and 0 <= orig_idx < len(embeddings) else None

                q_fmt = (q_data.get("_meta") or {}).get("question_format") or q_data.get("question_type") or "QA"
                rule_l1, rule_l2, rule_keywords = classify_taxonomy(
                    q_data.get("question_text") or "",
                    q_data.get("answer") or "",
                    q_data.get("subtopic") or "",
                    q_fmt,
                )
                final_topic = q_data.get("primary_topic") or rule_l1
                raw_sub = (q_data.get("subtopic") or "").strip()
                if not raw_sub or any(w in raw_sub.lower() for w in ["round", "closing bell", "market indicators", "ticker tangle", "portfolio builder", "opening bell"]):
                    final_subtopic = rule_l2
                else:
                    final_subtopic = raw_sub
                final_tags = list(dict.fromkeys((q_data.get("tags") or []) + rule_keywords))

                # deduplicator.py pre-assigns a stable id to every candidate (tier1_passed loop,
                # and again for any brand-new intra-batch match target) and uses THAT id as the
                # duplicate_of_id value when one question in this batch is flagged as a likely
                # duplicate of another question *also in this batch*. If we let SQLAlchemy's
                # Question.id column default (generate_uuid, applied at flush when id is unset)
                # mint a fresh id here instead of reusing q_data["id"], the two ids diverge: the
                # row actually gets a brand-new random id, while the *other* row's duplicate_of_id
                # still points at the id the deduplicator invented and never persisted anywhere --
                # a dangling FK the INSERT violates immediately. Passing id=q_data["id"] here makes
                # the persisted primary key match exactly what the deduplicator already decided on,
                # so an intra-batch duplicate_of_id always resolves. (Confirmed in production,
                # Nov 2 2026: psycopg.errors.ForeignKeyViolation on questions_duplicate_of_id_fkey,
                # "Key (duplicate_of_id)=(...) is not present in table questions" -- that id was
                # exactly a sibling candidate in the same deck's batch.)
                q_obj = Question(
                    id=q_data.get("id") or generate_uuid(),
                    content_hash=q_data["content_hash"],
                    document_id=doc.id,
                    slide_id=q_data.get("slide_id"),
                    answer_slide_id=q_data.get("answer_slide_id"),
                    question_text=q_data["question_text"],
                    answer=q_data["answer"],
                    options=q_data.get("options"),
                    explanation=q_data.get("explanation"),
                    topic=final_topic,
                    subtopic=final_subtopic,
                    topics=q_data.get("topics") or [final_topic],
                    tags=final_tags,
                    question_format=q_fmt,
                    difficulty=q_data.get("difficulty", "Medium"),
                    difficulty_score=q_data.get("difficulty_score", 0.50),
                    cognitive_level=q_data.get("cognitive_level", "Recall / Remember"),
                    grade_min=q_data.get("grade_min", 3),
                    grade_max=q_data.get("grade_max", 12),
                    audience_suitability=q_data.get("audience_suitability", []),
                    question_hook=q_data.get("question_hook", "DIRECT_TRIVIA"),
                    curiosity_score=q_data.get("curiosity_score", 7),
                    temporal_nature=q_data.get("temporal_nature", "EVERGREEN"),
                    provenance_decks=[{"document_id": doc.id, "slide_id": q_data.get("slide_id")}],
                    occurrence_count=1,
                    question_type=q_data.get("question_type", "SLIDE_QA"),
                    format_payload=q_data.get("format_payload"),
                    play_mode=q_data.get("play_mode"),
                    slot=q_data.get("slot") or "LIVE",
                    topic_label=q_data.get("topic_label"),
                    format_needs_review=bool((q_data.get("_ingest") or {}).get("needs_review")),
                    source_year=q_data.get("source_year", doc.year),
                    round_number=(q_data.get("_meta") or {}).get("round_number"),
                    image_refs=q_data.get("image_refs", []),
                    question_images=q_data.get("question_images", []),
                    answer_images=q_data.get("answer_images", []),
                    audio_refs=q_data.get("audio_refs", []),
                    question_audio=q_data.get("question_audio", []),
                    answer_audio=q_data.get("answer_audio", []),
                    video_refs=q_data.get("video_refs", []),
                    question_videos=q_data.get("question_videos", []),
                    answer_videos=q_data.get("answer_videos", []),
                    visual_clues=q_data.get("visual_clues"),
                    audio_transcript=q_data.get("audio_transcript"),
                    video_transcript=q_data.get("video_transcript"),
                    raw_media_refs=q_data.get("raw_media_refs", []),
                    source_slide_range=q_data.get("source_slide_range"),
                    duplicate_status=q_data.get("duplicate_status", "UNIQUE"),
                    duplicate_similarity=q_data.get("duplicate_similarity"),
                    duplicate_of_id=q_data.get("duplicate_of_id"),
                    embedding=emb or None,
                    embedding_model=embedding_model_label(self.embedding_provider) if emb else None,
                )
                db.add(q_obj)
                question_models.append(q_obj)

            # Assign question ids now: the metadata rows and the tagger both key on questions.id.
            db.flush()

            for q_m, q_data in zip(question_models, unique_candidates):
                ensure_question_metadata(db, q_m, q_data.get("_meta"), deck_row, doc)
                try:
                    topic_paths.replace_paths(
                        db,
                        q_m.id,
                        [{"l1": q_m.topic, "l2": q_m.subtopic, "keywords": q_m.tags or []}],
                        source="RULES",
                        record_history=False,
                        question=q_m,
                    )
                except Exception as tp_err:
                    logger.warning("replace_paths rule seeding error: %s", tp_err)

            # Phase C1 done: questions + metadata are durable BEFORE tagging runs. A tagging hiccup
            # can no longer roll back the whole ingested deck, and the connection is released.
            db.commit()
            t0 = _stage_log(document_id, "C:dedup+persist", t0)

            # Tagging (phase C2). Chunked batch calls (one Gemini call per ~12 questions) instead of
            # one call per question: fast, and small enough that no single response overflows the
            # output-token ceiling. Each result is routed through the same draft/confirmed gate, so
            # low-confidence questions still land in the review queue. A failed chunk parks its
            # questions in review rather than taking the deck down.
            tag_stats = {"confirmed": 0, "draft": 0, "failed_chunks": 0}
            if USE_QSHALA_TAGGER and question_models:
                raise_if_cancelled(document_id)
                INGESTION_STATUS_REGISTRY[document_id]["current_step"] = "8. Tagging questions"
                progress.update(INGESTION_STATUS_REGISTRY[document_id],
                                detail=f"AI tagging {len(question_models)} questions in batches")
                try:
                    from backend.app.services.tagging.qshala_batch import tag_questions_batched
                    tag_stats = await tag_questions_batched(
                        db, question_models,
                        provider=self.qshala_tagger.provider,
                        model=self.qshala_tagger.model,
                        actor="deck_upload",
                    )
                    if tag_stats.get("calls"):
                        from backend.app.services.ai.usage_cost import save_usage
                        save_usage(db, document_id, "tagging", self.qshala_tagger.model,
                                   {"calls": tag_stats["calls"], "in": tag_stats["tokens_in"],
                                    "out": tag_stats["tokens_out"], "think": tag_stats.get("tokens_think", 0),
                                    "cached": tag_stats.get("tokens_cached", 0)})
                        progress.event(INGESTION_STATUS_REGISTRY[document_id],
                                       f"Tagging AI usage: {tag_stats['calls']} calls, {tag_stats['tokens_in']:,} input / "
                                       f"{tag_stats['tokens_out']:,} output / {tag_stats.get('tokens_think', 0):,} thinking tokens")
                    # Mirror each question's resulting tag onto its metadata row (geography, year, ...).
                    for q_m in question_models:
                        latest = (db.query(QshalaTag)
                                  .filter(QshalaTag.question_id == q_m.id)
                                  .order_by(QshalaTag.created_at.desc()).first())
                        if latest is not None:
                            try:
                                apply_tag_to_meta(db, q_m.id, latest)
                            except Exception as meta_err:
                                logger.warning("apply_tag_to_meta failed for %s: %s", q_m.id, safe_error(meta_err))
                    db.commit()
                except IngestionCancelled:
                    raise
                except Exception as tag_err:
                    # Tagging is best-effort: questions are already stored. Park the deck's tags for
                    # review rather than failing the whole ingestion.
                    db.rollback()
                    logger.warning("batch tagging failed, questions left untagged for review: %s",
                                   safe_error(tag_err))
                t0 = _stage_log(document_id, "C2:tagging", t0)

            with short_session() as fin:
                fin_doc = fin.query(Document).filter(Document.id == document_id).first()
                if fin_doc:
                    fin_doc.question_count = len(question_models)
                    fin_doc.processing_status = "COMPLETED"

            _review_n = tag_stats.get("draft", 0)
            _summary = (f"Saved {len(question_models)} questions from {len(slide_models)} slides"
                        + (f", {len(duplicates)} duplicate(s) skipped" if duplicates else "")
                        + (f", {_review_n} flagged for review" if _review_n else ""))
            progress.event(INGESTION_STATUS_REGISTRY[document_id], _summary, detail="Finishing up")
            _tag_note = ""
            if USE_QSHALA_TAGGER and question_models and tag_stats.get("draft"):
                _tag_note = (f" ({tag_stats['draft']} of {len(question_models)} questions flagged for "
                             f"review; {tag_stats.get('failed_chunks', 0)} chunk(s) failed)")
            INGESTION_STATUS_REGISTRY[document_id].update({
                "progress_percentage": 100,
                "status": "COMPLETED",
                "current_step": "Ingestion completed successfully" + _tag_note,
                "slides_processed": len(slide_models),
                "questions_extracted": len(question_models),
                "duplicates_filtered": len(duplicates),
                "_ts": time.time(),
            })
            try:
                from backend.app.routers.stats import invalidate_stats_cache
                invalidate_stats_cache()
            except Exception:
                pass

        except IngestionCancelled:
            logger.info(f"[CANCEL] ingestion of {document_id} cancelled by user, cleaning up")
            try:
                db.rollback()
            except Exception:
                pass
            purge_document(db, document_id)
            clear_doc_cancel(document_id)
            INGESTION_STATUS_REGISTRY[document_id].update({
                "status": "CANCELLED",
                "error": None,
                "current_step": "Upload cancelled",
            })
        except Exception as e:
            logger.exception(f"Error processing document {document_id}: {e}")
            try:
                db.rollback()
                doc.processing_status = "FAILED"
                doc.processing_error = str(e)
                db.commit()
            except Exception:
                pass

            INGESTION_STATUS_REGISTRY[document_id].update({
                "status": "FAILED",
                "error": str(e),
                "current_step": f"Error: {str(e)}",
                "_ts": time.time(),
            })
        finally:
            if own_session:
                try:
                    db.close()
                except Exception:
                    pass
