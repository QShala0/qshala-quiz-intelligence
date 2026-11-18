import uuid
import hashlib
import re
import logging
from typing import Dict, Any, List, Optional, Tuple, Set
import numpy as np
from sqlalchemy.orm import Session
from backend.app.models.question import Question
from backend.app.services.retrieval.vector_search import batch_cosine_similarities
from backend.app.config import settings
from backend.app.database import is_postgres, PG_VECTOR_AVAILABLE

logger = logging.getLogger(__name__)

class Deduplicator:
    """
    Two-Tier High-Scale Deduplication Engine:
    - Tier 1: O(1) Canonical Normalized SHA-256 Hash Pre-Filter
    - Tier 2: Semantic Vector Cosine Similarity Nearest-Neighbor Lookup (0.92 Threshold)
    
    Candidates >= 0.92 similarity are flagged as 'POSSIBLE_DUPLICATE' for human review
    in the Questions UI (NOT auto-rejected or discarded).
    """

    DEFAULT_SEMANTIC_THRESHOLD = getattr(settings, "DUPLICATE_SIMILARITY_THRESHOLD", 0.92)

    @staticmethod
    def canonicalize_text(text: str) -> str:
        if not text:
            return ""
        t = text.lower().strip()
        t = re.sub(r"[^\w\s]", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    @classmethod
    def compute_hash(cls, question_text: str, answer_text: str) -> str:
        q_norm = cls.canonicalize_text(question_text)
        a_norm = cls.canonicalize_text(answer_text)
        payload = f"{q_norm}|||{a_norm}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def tier1_exact_indices(hashes: List[str], in_vault: Set[str]) -> Set[int]:
        """Positions whose content hash is an exact repeat: already in the vault, or seen earlier in this batch.
        The single definition of the tier-1 rule, used by deduplicate_batch and by the ingestion pipeline's
        pre-AI check so the two can never disagree."""
        repeats: Set[int] = set()
        seen: Set[str] = set()
        for i, h in enumerate(hashes):
            if h in in_vault or h in seen:
                repeats.add(i)
            else:
                seen.add(h)
        return repeats

    @classmethod
    def is_plausible_semantic_duplicate(
        cls,
        cand_text: str,
        cand_ans: str,
        match_text: str,
        match_ans: str,
        similarity: float,
        threshold: float
    ) -> bool:
        """
        Guards against false positives in semantic vector duplicate detection:
        1. Cosine similarity must meet threshold.
        2. Match text must not be empty (an image-only slide with no extracted question
           text cannot be a semantic duplicate of a text question).
        3. Short / symbolic questions (< 15 chars or < 3 substantive words, such as math
           puzzle grids '_ _ _ _ L+2 L-2 A+1 F+2') collapse into generic vector space and
           must not be flagged unless the question text itself matches.
        4. Answer divergence guard: If both candidate and match have answers, they must share
           at least some semantic/lexical overlap or cross question-answer overlap.
           If answers are completely disjoint (e.g. 'SKIN' vs "Zohran Mamdani - New York City's mayor"),
           they cannot be duplicates unless question text similarity is virtually identical (> 0.97).
        """
        if similarity < threshold:
            return False

        c_q = (cand_text or "").strip()
        m_q = (match_text or "").strip()
        c_a = (cand_ans or "").strip()
        m_a = (match_ans or "").strip()

        # Matched record has no question text (e.g. image-only slide)
        if not m_q:
            return False

        # Sparse or symbolic candidate (puzzles, math symbols, single words)
        c_words = re.findall(r"[a-zA-Z]{3,}", c_q)
        if len(c_q) < 15 or len(c_words) < 3:
            q_norm_c = cls.canonicalize_text(c_q)
            q_norm_m = cls.canonicalize_text(m_q)
            if q_norm_c != q_norm_m:
                return False

        # Answer divergence check
        if c_a and m_a:
            a_norm_c = cls.canonicalize_text(c_a)
            a_norm_m = cls.canonicalize_text(m_a)

            # Direct equality or substring containment (e.g. 'koala' in 'the koala bear')
            if a_norm_c == a_norm_m or a_norm_c in a_norm_m or a_norm_m in a_norm_c:
                return True

            tokens_c = set(re.findall(r"\w+", a_norm_c))
            tokens_m = set(re.findall(r"\w+", a_norm_m))
            stopwords = {"the", "a", "an", "of", "and", "in", "on", "at", "to", "for", "is", "by", "or"}
            meaningful_c = tokens_c - stopwords
            meaningful_m = tokens_m - stopwords

            # Shared meaningful tokens in answers
            if meaningful_c and meaningful_m and (meaningful_c & meaningful_m):
                return True

            # Cross question-answer overlap
            q_norm_c = cls.canonicalize_text(c_q)
            q_norm_m = cls.canonicalize_text(m_q)
            if (a_norm_c and a_norm_c in q_norm_m) or (a_norm_m and a_norm_m in q_norm_c):
                return True

            # If answers are completely disjoint, reject duplicate unless similarity is near-exact (> 0.97)
            if similarity < 0.97:
                return False

        return True


    def deduplicate_batch(
        self,
        db: Session,
        candidates: List[Dict[str, Any]],
        candidate_embeddings: List[List[float]],
        doc_id: str,
        threshold: Optional[float] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
        """
        Deduplicates candidate questions:
        - Tier 1: Exact hash match updates provenance on existing record and skips vector search.
        - Tier 2: Vector nearest-neighbor check. Candidates >= threshold (0.92) are flagged as
          'POSSIBLE_DUPLICATE' with matched ID and similarity score, but KEPT for insertion
          and human UI review.
        """
        if not candidates:
            return [], [], []

        thresh = threshold if threshold is not None else self.DEFAULT_SEMANTIC_THRESHOLD

        # 1. Compute canonical hashes
        for c in candidates:
            c["content_hash"] = self.compute_hash(c["question_text"], c["answer"])

        candidate_hashes = [c["content_hash"] for c in candidates]

        # 2. Tier 1: Exact Hash Pre-filter
        existing_hash_map: Dict[str, Question] = {}
        matched_records = db.query(Question).filter(Question.content_hash.in_(candidate_hashes)).all()
        for rec in matched_records:
            existing_hash_map[rec.content_hash] = rec

        tier1_passed: List[Dict[str, Any]] = []
        tier1_passed_embeddings: List[List[float]] = []
        exact_duplicates: List[Dict[str, Any]] = []
        provenance_updates: List[Tuple[Question, Optional[str], Dict[str, Any]]] = []
        exact_idx = self.tier1_exact_indices(candidate_hashes, set(existing_hash_map))

        for i, c in enumerate(candidates):
            chash = c["content_hash"]
            if i in exact_idx and chash in existing_hash_map:
                existing = existing_hash_map[chash]
                provenance_updates.append((existing, c.get("slide_id"), c))
                exact_duplicates.append({
                    "candidate_question": c["question_text"],
                    "matched_id": existing.id,
                    "duplicate_type": "EXACT_HASH",
                    "similarity": 1.0,
                    # Which deck already holds this question, for the upload report -- independent
                    # of whether the repeat-provenance write below succeeds.
                    "already_in_deck_title": getattr(existing.document, "title", None),
                    "question_number": (c.get("_meta") or {}).get("question_number"),
                })
            elif i in exact_idx:
                exact_duplicates.append({
                    "candidate_question": c["question_text"],
                    "duplicate_type": "INTRA_BATCH_EXACT",
                    "similarity": 1.0
                })
            else:
                tier1_passed.append(c)
                if i < len(candidate_embeddings):
                    tier1_passed_embeddings.append(candidate_embeddings[i])
                else:
                    tier1_passed_embeddings.append([])

        if not tier1_passed:
            _, warnings = self._batch_commit_provenance(db, provenance_updates, doc_id)
            return [], exact_duplicates, warnings

        # 3. Tier 2: Nearest-Neighbor Semantic Vector Similarity
        batch_topics = set()
        for c in tier1_passed:
            c["id"] = c.get("id") or str(uuid.uuid4())
            pt = c.get("primary_topic") or c.get("topic")
            if pt:
                batch_topics.add(pt)

        existing_ids = []
        existing_matrix = []

        # Only load in-memory matrix if not running on native pgvector
        existing_records_cache = {}
        # Only load in-memory matrix if not running on native pgvector
        if not (is_postgres and PG_VECTOR_AVAILABLE):
            query = db.query(Question.id, Question.question_text, Question.answer, Question.embedding).filter(Question.embedding.isnot(None))
            if batch_topics and len(batch_topics) <= 4:
                query = query.filter(Question.topic.in_(list(batch_topics)))

            existing_vec_records = query.all()
            if not existing_vec_records:
                existing_vec_records = db.query(Question.id, Question.question_text, Question.answer, Question.embedding).filter(Question.embedding.isnot(None)).limit(1000).all()

            existing_ids = [r[0] for r in existing_vec_records if r[3] and len(r[3]) > 0]
            existing_matrix = [r[3] for r in existing_vec_records if r[3] and len(r[3]) > 0]
            for r in existing_vec_records:
                existing_records_cache[r[0]] = (r[1] or "", r[2] or "")

        final_candidates: List[Dict[str, Any]] = []
        final_candidates_embeddings: List[List[float]] = []
        # final_candidates_embeddings only holds candidates that HAVE a vector, so position k in it is not position k
        # in final_candidates. This maps one to the other.
        final_candidates_emb_idx: List[int] = []

        for idx, item in enumerate(tier1_passed):
            item_emb = tier1_passed_embeddings[idx] if idx < len(tier1_passed_embeddings) else None
            best_sim = 0.0
            best_matched_id: Optional[str] = None
            is_possible_dup = False

            # Check against existing DB embeddings
            if item_emb:
                if is_postgres and PG_VECTOR_AVAILABLE:
                    try:
                        q_query = db.query(
                            Question.id,
                            Question.question_text,
                            Question.answer,
                            Question.embedding.cosine_distance(item_emb).label("distance")
                        ).filter(Question.embedding.isnot(None))
                        if batch_topics and len(batch_topics) <= 4:
                            q_query = q_query.filter(Question.topic.in_(list(batch_topics)))
                        nn_recs = q_query.order_by("distance").limit(5).all()
                        for nn_rec in nn_recs:
                            if nn_rec and nn_rec.distance is not None:
                                pg_sim = float(1.0 - nn_rec.distance)
                                if self.is_plausible_semantic_duplicate(
                                    item.get("question_text", ""),
                                    item.get("answer", ""),
                                    nn_rec.question_text or "",
                                    nn_rec.answer or "",
                                    pg_sim,
                                    thresh
                                ):
                                    if pg_sim > best_sim:
                                        best_sim = pg_sim
                                        best_matched_id = nn_rec.id
                                        is_possible_dup = True
                                        break
                    except Exception as e:
                        logger.warning(f"pgvector query error ({e}), falling back")

                if not is_possible_dup and existing_matrix:
                    sims = batch_cosine_similarities(item_emb, existing_matrix)
                    if len(sims) > 0:
                        above_indices = np.where(sims >= thresh)[0]
                        if len(above_indices) > 0:
                            sorted_indices = above_indices[np.argsort(sims[above_indices])[::-1]]
                            for match_idx in sorted_indices:
                                match_id = existing_ids[match_idx]
                                match_q, match_a = existing_records_cache.get(match_id, ("", ""))
                                sim = float(sims[match_idx])
                                if self.is_plausible_semantic_duplicate(
                                    item.get("question_text", ""),
                                    item.get("answer", ""),
                                    match_q,
                                    match_a,
                                    sim,
                                    thresh
                                ):
                                    best_sim = sim
                                    best_matched_id = match_id
                                    is_possible_dup = True
                                    break

            # Intra-batch comparison against previously accepted items in this batch
            if not is_possible_dup and item_emb and final_candidates_embeddings:
                intra_sims = batch_cosine_similarities(item_emb, final_candidates_embeddings)
                if len(intra_sims) > 0:
                    above_indices = np.where(intra_sims >= thresh)[0]
                    if len(above_indices) > 0:
                        sorted_indices = above_indices[np.argsort(intra_sims[above_indices])[::-1]]
                        for b_idx in sorted_indices:
                            matched_cand = final_candidates[final_candidates_emb_idx[b_idx]]
                            b_sim = float(intra_sims[b_idx])
                            if self.is_plausible_semantic_duplicate(
                                item.get("question_text", ""),
                                item.get("answer", ""),
                                matched_cand.get("question_text", ""),
                                matched_cand.get("answer", ""),
                                b_sim,
                                thresh
                            ):
                                if not matched_cand.get("id"):
                                    matched_cand["id"] = str(uuid.uuid4())
                                best_matched_id = matched_cand["id"]
                                best_sim = b_sim
                                is_possible_dup = True
                                break

            if is_possible_dup:
                item["duplicate_status"] = "POSSIBLE_DUPLICATE"
                item["duplicate_similarity"] = round(best_sim, 4)
                item["duplicate_of_id"] = best_matched_id
            else:
                item["duplicate_status"] = "UNIQUE"
                item["duplicate_similarity"] = round(best_sim, 4) if best_sim > 0 else None
                item["duplicate_of_id"] = None

            final_candidates.append(item)
            if item_emb:
                final_candidates_embeddings.append(item_emb)
                final_candidates_emb_idx.append(len(final_candidates) - 1)

        # Commit provenance for exact hash matches
        _, provenance_warnings = self._batch_commit_provenance(db, provenance_updates, doc_id)

        # Explain the final count: raw candidates in, how many collapsed and why, how many kept.
        n_exact_db = sum(1 for d in exact_duplicates if d.get("duplicate_type") == "EXACT_HASH")
        n_exact_intra = sum(1 for d in exact_duplicates if d.get("duplicate_type") == "INTRA_BATCH_EXACT")
        n_possible = sum(1 for c in final_candidates if c.get("duplicate_status") == "POSSIBLE_DUPLICATE")
        logger.info(
            "dedup[doc=%s]: raw=%d -> kept=%d | dropped_exact_in_vault=%d dropped_exact_in_batch=%d "
            "| flagged_possible_dup=%d (threshold=%.2f)",
            doc_id, len(candidates), len(final_candidates),
            n_exact_db, n_exact_intra, n_possible, thresh,
        )

        return final_candidates, exact_duplicates, provenance_warnings

    def _batch_commit_provenance(
        self,
        db: Session,
        updates: List[Tuple[Question, Optional[str], Dict[str, Any]]],
        doc_id: str
    ) -> Tuple[int, List[str]]:
        """Records each exact repeat on the question we already hold.

        Instead of storing the question again, the original gets a Repeated entry saying which quiz it was
        found in again and at which question number, plus the provenance link and occurrence count.

        Each update runs in its own SAVEPOINT: one bad row (a write error, a stale FK) rolls back only
        that row and is reported as a warning, instead of the old single try/except around the whole
        loop losing every repeat record for the deck because one of them raised.
        """
        if not updates:
            return 0, []
        from backend.app.services.question_meta_service import add_repeat
        ok = 0
        warnings: List[str] = []
        for existing, slide_id, cand in updates:
            if existing.document_id == doc_id:
                continue  # the same deck cannot repeat a question it already contributed
            try:
                sp = db.begin_nested()
                rep = add_repeat(db, existing, doc_id, cand.get("_meta"), cand.get("source_slide_range"),
                                 match_type="EXACT")
                provenance = list(existing.provenance_decks or [])
                if not any(p.get("document_id") == doc_id for p in provenance):
                    provenance.append({"document_id": doc_id, "slide_id": slide_id})
                    existing.provenance_decks = provenance
                if rep is not None:
                    existing.occurrence_count = (existing.occurrence_count or 1) + 1
                sp.commit()
                ok += 1
            except Exception as e:
                try:
                    sp.rollback()
                except Exception:
                    pass
                msg = f"repeat record for question {existing.id} in deck {doc_id} failed: {e}"
                logger.warning("duplicate provenance savepoint failed: %s", msg)
                warnings.append(msg)
        try:
            db.commit()
        except Exception as e:
            logger.warning(f"Failed to commit duplicate provenance batch: {e}")
            db.rollback()
            warnings.append(f"batch commit failed, {ok} repeat record(s) not saved: {e}")
            ok = 0
        return ok, warnings


def build_duplicate_report(
    deck_title: str,
    extracted: int,
    saved: int,
    exact_duplicates: List[Dict[str, Any]],
    final_candidates: List[Dict[str, Any]],
    provenance_warnings: Optional[List[str]] = None,
    skipped_media_slides: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """The per-deck duplicate/reconciliation report shown on the upload page: what deduplicate_batch
    found, in plain categories, plus extracted/saved/dropped so the numbers visibly add up.

    `skipped_media_slides` is an empty list for now -- Workstream 4 (audio-only questions) fills it
    in with slides that carried a real clue but produced no question, so this shape is ready for it
    without another schema change.
    """
    already_in_library = [
        {"already_in_deck_title": d.get("already_in_deck_title"),
         "question_number": d.get("question_number")}
        for d in exact_duplicates if d.get("duplicate_type") == "EXACT_HASH"
    ]
    repeated_in_this_deck = sum(1 for d in exact_duplicates if d.get("duplicate_type") == "INTRA_BATCH_EXACT")
    possible_duplicates_kept = [
        {"question_text": (c.get("question_text") or "")[:200],
         "similarity": c.get("duplicate_similarity"), "matched_id": c.get("duplicate_of_id")}
        for c in final_candidates if c.get("duplicate_status") == "POSSIBLE_DUPLICATE"
    ]
    dropped = len(already_in_library) + repeated_in_this_deck
    if extracted != saved + dropped:
        # Not fatal (the report is informational), but worth knowing about if the two ever drift.
        logger.warning("duplicate report reconciliation mismatch: extracted=%d saved=%d dropped=%d",
                       extracted, saved, dropped)
    return {
        "deck_title": deck_title,
        "reconciliation": {"extracted": extracted, "saved": saved, "dropped": dropped},
        "already_in_library": already_in_library,
        "repeated_in_this_deck": repeated_in_this_deck,
        "possible_duplicates_kept": possible_duplicates_kept,
        "skipped_media_slides": skipped_media_slides or [],
        "provenance_warnings": provenance_warnings or [],
    }
