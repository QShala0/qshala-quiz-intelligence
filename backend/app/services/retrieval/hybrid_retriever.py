import json
import re
import logging
from collections import OrderedDict
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
import numpy as np
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import or_, and_, not_, func, String
from backend.app.database import is_postgres, PG_VECTOR_AVAILABLE
from backend.app.models.question import Question
from backend.app.models.document import Document, Slide
from backend.app.services.ai.factory import get_embedding_provider
from backend.app.services.retrieval.vector_search import batch_cosine_similarities

logger = logging.getLogger(__name__)

TAXONOMY_FILE = Path(__file__).resolve().parent.parent / "tagging" / "taxonomy.json"
# Search-query synonym expansions, externalized to its own editable file (QShala priority plan
# P3-3): this used to be a dict hardcoded into this module with one customer's domain
# (Australian quiz content) baked in, which silently polluted search for every other topic
# domain. A missing/unreadable file now falls back to {} -- no synonym expansion at all -- rather
# than silently reintroducing that single-customer bias as a code-level default.
SYNONYM_EXPANSIONS_FILE = Path(__file__).resolve().parent / "synonym_expansions.json"

def apply_question_scope_filters(
    query_builder,
    *,
    document_id: Optional[str] = None,
    document_ids: Optional[List[str]] = None,
    document_title: Optional[str] = None,
    duplicate_status: Optional[str] = None,
    subtopic: Optional[str] = None,
    tag: Optional[str] = None,
    difficulty: Optional[str] = None,
    slot: Optional[str] = None,
    play_mode: Optional[str] = None,
    question_format: Optional[str] = None,
):
    """Apply hard Question/Document scope filters to a SQLAlchemy query with Document joined."""
    effective_doc_ids: List[str] = []
    if document_id and isinstance(document_id, str) and document_id.strip():
        effective_doc_ids.append(document_id.strip())
    if document_ids:
        for did in document_ids:
            if isinstance(did, str) and did.strip() and did.strip() not in effective_doc_ids:
                effective_doc_ids.append(did.strip())

    if effective_doc_ids:
        query_builder = query_builder.filter(Question.document_id.in_(effective_doc_ids))
    if document_title and isinstance(document_title, str) and document_title.strip():
        dt = document_title.strip()
        query_builder = query_builder.filter(
            or_(Document.title.ilike(f"%{dt}%"), Document.filename.ilike(f"%{dt}%"))
        )
    if duplicate_status:
        query_builder = query_builder.filter(Question.duplicate_status == duplicate_status)
    if subtopic:
        query_builder = query_builder.filter(Question.subtopic.ilike(f"%{subtopic}%"))
    if tag:
        clean_tag = tag.strip().lstrip("#").strip()
        if clean_tag:
            query_builder = query_builder.filter(func.cast(Question.tags, String).ilike(f"%{clean_tag}%"))
    if difficulty:
        query_builder = query_builder.filter(Question.difficulty.ilike(difficulty))
    if slot:
        query_builder = query_builder.filter(Question.slot == slot)
    if play_mode:
        query_builder = query_builder.filter(Question.play_mode == play_mode)
    if question_format:
        fmt_str = question_format.strip().upper()
        target_fmts = [fmt_str]
        if fmt_str in ("CONNECT_4", "CONNECT"):
            target_fmts = ["CONNECT_4", "CONNECT"]
        elif fmt_str in ("STEPPED_CLUE", "THREE_CLUE"):
            target_fmts = ["STEPPED_CLUE", "THREE_CLUE"]
        elif fmt_str in ("STANDARD_QA", "QA"):
            target_fmts.extend(["STANDARD_QA", "QA", "SLIDE_QA"])
        from backend.app.models.qshala_tag import QshalaQuestionMetadata
        query_builder = query_builder.outerjoin(
            QshalaQuestionMetadata, Question.id == QshalaQuestionMetadata.question_id
        ).filter(
            or_(
                Question.question_format.in_(target_fmts),
                Question.question_type.in_(target_fmts),
                QshalaQuestionMetadata.question_format.in_(target_fmts),
            )
        )
    return query_builder


class HybridRetriever:
    """
    State-of-the-art hybrid retriever for the QShala Knowledge Vault.
    Combines:
    - Direct pedagogical metadata filtering (for exact topic/difficulty compilations)
    - Dense vector cosine semantic search
    - Sparse token lexical matching with dynamic taxonomy query expansions
    - Reciprocal Rank Fusion (RRF)
    """

    # Shared across requests in this process -- HybridRetriever is re-built
    # per request, so the cache lives on the class rather than the instance.
    # Repeated search terms then skip the live embedding API call entirely.
    _embedding_cache: "OrderedDict[str, List[float]]" = OrderedDict()
    _EMBED_CACHE_MAX = 256
    _DENSE_POOL_SIZE = 60

    def __init__(self, db: Session):
        self.db = db
        self.embedding_provider = get_embedding_provider()
        self.expansions = self._load_synonym_expansions()

    async def _get_query_embedding(self, query: str) -> Optional[List[float]]:
        """Embeds the query, reusing a recent result for the same text.

        Search is expected to be the dominant, latency-sensitive traffic on
        this service, and the same handful of queries get typed a lot -- this
        avoids hitting the embedding API (and its latency/cost) more than
        once per distinct query within the cache window.
        """
        cache_key = query.strip().lower()
        cached = HybridRetriever._embedding_cache.get(cache_key)
        if cached is not None:
            HybridRetriever._embedding_cache.move_to_end(cache_key)
            return cached

        try:
            embedding = await self.embedding_provider.get_embedding(query)
        except Exception as e:
            # No real query vector: skip dense ranking for this request instead of comparing a made-up vector
            # against the stored ones. Nothing is cached, so the next search tries again.
            logger.warning("query embedding unavailable (%s); using keyword ranking only for this search",
                           type(e).__name__)
            return None

        HybridRetriever._embedding_cache[cache_key] = embedding
        HybridRetriever._embedding_cache.move_to_end(cache_key)
        while len(HybridRetriever._embedding_cache) > HybridRetriever._EMBED_CACHE_MAX:
            HybridRetriever._embedding_cache.popitem(last=False)

        return embedding

    def _load_synonym_expansions(self) -> Dict[str, List[str]]:
        expansions: Dict[str, List[str]] = {}
        if SYNONYM_EXPANSIONS_FILE.exists():
            try:
                with open(SYNONYM_EXPANSIONS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    expansions.update({
                        k.lower(): v for k, v in data.items() if not k.startswith("_")
                    })
            except Exception:
                logger.warning(
                    "Failed to load %s; continuing with no synonym expansions",
                    SYNONYM_EXPANSIONS_FILE,
                )
        # Separately, the taxonomy file's own categories (if it defines any) also expand
        # queries -- this part isn't domain-specific, it's just reusing whatever taxonomy the
        # deployment is already configured with.
        if TAXONOMY_FILE.exists():
            try:
                with open(TAXONOMY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for cat, keywords in data.get("categories", {}).items():
                        expansions[cat.lower()] = keywords[:10]
            except Exception:
                pass
        return expansions

    def _expand_query(self, query: str) -> List[str]:
        tokens = re.sub(r"[^\w\s]", " ", query.lower()).split()
        expanded_terms = set(tokens)
        q_lower = query.lower()
        for term, synonyms in self.expansions.items():
            if term in q_lower:
                expanded_terms.update(synonyms)
            for tok in tokens:
                if tok in term:
                    expanded_terms.update(synonyms)
        return list(expanded_terms)

    def fetch_by_filters(
        self,
        topic: Optional[str] = None,
        difficulty: Optional[str] = None,
        grade_min: Optional[int] = None,
        grade_max: Optional[int] = None,
        subtopic: Optional[str] = None,
        tags: Optional[List[str]] = None,
        has_visual: Optional[bool] = None,
        has_audio: Optional[bool] = None,
        exclude_ids: Optional[List[str]] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Direct SQL retrieval by pedagogical filters and tags for compiling quizzes straight from the vault.
        Prioritizes tag matches and exact topic alignments without vector fuzzy drift.
        """
        query_builder = self.db.query(Question, Document, Slide).\
            options(selectinload(Question.duplicate_of)).\
            join(Document, Question.document_id == Document.id).\
            outerjoin(Slide, Question.slide_id == Slide.id)

        # Topic filter (if provided)
        if topic:
            query_builder = query_builder.filter(
                or_(
                    Question.topic.ilike(f"%{topic}%"),
                    Question.subtopic.ilike(f"%{topic}%"),
                    Question.visual_clues.ilike(f"%{topic}%")
                )
            )

        if subtopic:
            query_builder = query_builder.filter(Question.subtopic.ilike(f"%{subtopic}%"))

        if difficulty:
            query_builder = query_builder.filter(Question.difficulty.ilike(difficulty))

        if has_visual is True:
            query_builder = query_builder.filter(
                or_(
                    and_(Question.visual_clues.isnot(None), Question.visual_clues != ""),
                    and_(Question.question_images.isnot(None), Question.question_images != "[]")
                )
            )
        elif has_visual is False:
            query_builder = query_builder.filter(
                or_(Question.visual_clues.is_(None), Question.visual_clues == "")
            )

        if has_audio is True:
            query_builder = query_builder.filter(
                or_(
                    and_(Question.audio_refs.isnot(None), Question.audio_refs != "[]"),
                    and_(Question.question_audio.isnot(None), Question.question_audio != "[]")
                )
            )
        elif has_audio is False:
            query_builder = query_builder.filter(
                or_(Question.audio_refs.is_(None), Question.audio_refs == "[]")
            )

        if grade_min is not None and grade_max is not None:
            query_builder = query_builder.filter(
                and_(
                    Question.grade_min <= (grade_max + 1),
                    Question.grade_max >= (grade_min - 1)
                )
            )

        if exclude_ids:
            query_builder = query_builder.filter(not_(Question.id.in_(exclude_ids)))

        candidates = query_builder.all()
        if not candidates:
            return []

        clean_tags = [t.strip().lstrip("#").lower() for t in (tags or []) if isinstance(t, str) and t.strip()]

        # Score candidates with tag-overlap priority
        scored_candidates = []
        for q, doc, slide in candidates:
            q_tags_lower = [t.strip().lstrip("#").lower() for t in (q.tags or []) if isinstance(t, str)]
            tag_overlap = sum(1 for t in clean_tags if t in q_tags_lower)
            relevance = 100.0 + (tag_overlap * 50.0)
            scored_candidates.append((relevance, tag_overlap, q, doc, slide))

        # Sort by tag_overlap descending, then relevance
        scored_candidates.sort(key=lambda x: (x[1], x[0]), reverse=True)

        results = []
        for relevance, tag_overlap, q, doc, slide in scored_candidates[:limit]:
            results.append({
                "question": q,
                "document": doc,
                "slide": slide,
                "relevance_score": relevance,
                "tag_overlap": tag_overlap,
                "dense_similarity": 1.0,
                "quote": slide.extracted_text if slide else q.question_text
            })

        return results

    def _join_query(self):
        return self.db.query(Question, Document, Slide).\
            options(selectinload(Question.duplicate_of)).\
            join(Document, Question.document_id == Document.id).\
            outerjoin(Slide, Question.slide_id == Slide.id)

    def _apply_grade_filter(self, query_builder, grade_min: Optional[int], grade_max: Optional[int]):
        if grade_min is not None and grade_max is not None:
            query_builder = query_builder.filter(
                and_(
                    Question.grade_min <= (grade_max + 1),
                    Question.grade_max >= (grade_min - 1)
                )
            )
        elif grade_min is not None:
            query_builder = query_builder.filter(Question.grade_min >= grade_min)
        elif grade_max is not None:
            query_builder = query_builder.filter(Question.grade_max <= grade_max)
        return query_builder

    def _apply_topic_filter(self, query_builder, topic: Optional[str]):
        if topic:
            query_builder = query_builder.filter(
                or_(
                    Question.topic.ilike(f"%{topic}%"),
                    Question.subtopic.ilike(f"%{topic}%"),
                    Question.question_text.ilike(f"%{topic}%"),
                    Question.visual_clues.ilike(f"%{topic}%")
                )
            )
        return query_builder

    async def search(
        self,
        query: str,
        topic: Optional[str] = None,
        tags: Optional[List[str]] = None,
        grade_min: Optional[int] = None,
        grade_max: Optional[int] = None,
        difficulty: Optional[str] = None,
        limit: int = 15,
        document_id: Optional[str] = None,
        document_ids: Optional[List[str]] = None,
        document_title: Optional[str] = None,
        subtopic: Optional[str] = None,
        duplicate_status: Optional[str] = None,
        slot: Optional[str] = None,
        play_mode: Optional[str] = None,
        question_format: Optional[str] = None,
        difficulty_strict: bool = False,
        require_tag_match: bool = False,
        allow_relax: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Executes hybrid dense + sparse retrieval with Reciprocal Rank Fusion (RRF) and Tag-Overlap Boost.

        The dense candidate pool is ranked by Postgres itself (pgvector's
        cosine_distance, backed by the HNSW/IVFFlat index on
        questions.embedding) when running on Postgres, so only the nearest
        _DENSE_POOL_SIZE rows are ever pulled into this process -- memory and
        latency stay flat regardless of how many questions are in the vault.
        On SQLite (local/dev, no pgvector) it falls back to the original
        in-process scan over the filtered candidate set.
        """
        pool_size = max(self._DENSE_POOL_SIZE, limit)

        # Step 1: Dense Query Embedding (cached -- a repeated search term
        # skips the live embedding API call).
        query_emb = await self._get_query_embedding(query)

        clean_tags = [t.strip().lstrip("#").lower() for t in (tags or []) if isinstance(t, str) and t.strip()]

        def _scoped_query():
            return apply_question_scope_filters(
                self._join_query(),
                document_id=document_id,
                document_ids=document_ids,
                document_title=document_title,
                duplicate_status=duplicate_status,
                subtopic=subtopic,
                tag=(clean_tags[0] if require_tag_match and clean_tags else None),
                difficulty=(difficulty if difficulty_strict else None),
                slot=slot,
                play_mode=play_mode,
                question_format=question_format,
            )

        use_pg_ann = is_postgres and PG_VECTOR_AVAILABLE and query_emb
        candidate_distances: Dict[str, float] = {}

        if use_pg_ann:
            # Step 2 (Postgres): let the database find the nearest questions --
            # nothing else is loaded into this process.
            distance_col = Question.embedding.cosine_distance(query_emb)
            query_builder = self._apply_topic_filter(
                self._apply_grade_filter(_scoped_query(), grade_min, grade_max),
                topic
            ).add_columns(distance_col.label("_distance")).order_by(distance_col).limit(pool_size)

            rows = query_builder.all()
            candidates = [(q, doc, slide) for (q, doc, slide, _dist) in rows]
            candidate_distances = {q.id: float(_dist) for (q, doc, slide, _dist) in rows if _dist is not None}

            # If topic/grade filters matched nothing, relax only topic/grade while
            # strictly preserving source/deck and vault scope filters.
            if not candidates and allow_relax:
                rows = _scoped_query().add_columns(distance_col.label("_distance")).\
                    order_by(distance_col).limit(pool_size).all()
                candidates = [(q, doc, slide) for (q, doc, slide, _dist) in rows]
                candidate_distances = {q.id: float(_dist) for (q, doc, slide, _dist) in rows if _dist is not None}
        else:
            # Step 2 (fallback): no pgvector available -- score matching rows
            # in Python. Cap at 2000 to avoid OOM on large vaults.
            _FALLBACK_CAP = 2000
            query_builder = self._apply_topic_filter(
                self._apply_grade_filter(_scoped_query(), grade_min, grade_max),
                topic
            )
            candidates = query_builder.limit(_FALLBACK_CAP).all()

            if not candidates and allow_relax:
                candidates = _scoped_query().limit(_FALLBACK_CAP).all()

        if not candidates:
            return []

        tag_query_str = " ".join(clean_tags)
        expanded_keywords = self._expand_query(f"{query} {topic or ''} {tag_query_str}")

        # Path A: Dense Vector similarities
        if not query_emb:
            # Query embedding unavailable: every candidate gets the same dense rank, so the order comes from
            # the keyword/tag path alone.
            dense_scores = np.zeros(len(candidates))
        elif candidate_distances:
            # pgvector's cosine_distance is (1 - cosine_similarity); convert back.
            # A candidate with no stored embedding (no distance returned) scores
            # as fully dissimilar rather than being dropped -- it can still
            # surface via the keyword/tag path below.
            dense_scores = np.array([1.0 - candidate_distances.get(q.id, 1.0) for q, doc, slide in candidates])
        else:
            candidate_embs = []
            for q, doc, slide in candidates:
                emb = q.embedding
                if isinstance(emb, list) and len(emb) == len(query_emb):
                    candidate_embs.append(emb)
                else:
                    candidate_embs.append([0.0] * len(query_emb))
            dense_scores = batch_cosine_similarities(query_emb, candidate_embs)

        if query_emb:
            dense_ranked_indices = list(np.argsort(-dense_scores))
            dense_rank_map = {idx: rank + 1 for rank, idx in enumerate(dense_ranked_indices)}
        else:
            dense_rank_map = {idx: 1 for idx in range(len(candidates))}

        # Path B: Sparse Lexical & Keyword Matching + Tag Overlap
        sparse_scores = []
        tag_overlaps = []
        for i, (q, doc, slide) in enumerate(candidates):
            content_text = f"{q.question_text} {q.answer} {q.topic} {q.subtopic or ''} {q.visual_clues or ''} {slide.extracted_text if slide else ''}".lower()
            q_tags_lower = [t.strip().lstrip("#").lower() for t in (q.tags or []) if isinstance(t, str)]
            tag_overlap = sum(1 for t in clean_tags if t in q_tags_lower)
            tag_overlaps.append(tag_overlap)

            score = 0.0
            for kw in expanded_keywords:
                if kw in content_text:
                    score += 1.0
            if difficulty and q.difficulty and q.difficulty.lower() == (difficulty or "").lower():
                score += 0.5
            # Heavy bonus for tag match
            score += tag_overlap * 5.0
            sparse_scores.append(score)

        sparse_ranked_indices = list(np.argsort(-np.array(sparse_scores)))
        sparse_rank_map = {idx: rank + 1 for rank, idx in enumerate(sparse_ranked_indices)}

        # Step 3: Reciprocal Rank Fusion (RRF) with Tag Multiplier
        rrf_scores = []
        k_rrf = 60.0
        for idx in range(len(candidates)):
            r_dense = dense_rank_map[idx]
            r_sparse = sparse_rank_map[idx]
            overlap_boost = 1.0 + (0.5 * tag_overlaps[idx])
            score = ((1.0 / (k_rrf + r_dense)) + (1.0 / (k_rrf + r_sparse))) * overlap_boost
            rrf_scores.append((score, idx))

        rrf_scores.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, idx in rrf_scores[:limit]:
            q, doc, slide = candidates[idx]
            results.append({
                "question": q,
                "document": doc,
                "slide": slide,
                "relevance_score": round(score * 100, 3),
                "dense_similarity": round(float(dense_scores[idx]), 3),
                "tag_overlap": tag_overlaps[idx],
                "quote": slide.extracted_text if slide else q.question_text
            })

        return results

