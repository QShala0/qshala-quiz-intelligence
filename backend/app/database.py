import json
import logging
from typing import List, Optional
from sqlalchemy import create_engine, TypeDecorator, Text
from sqlalchemy.orm import sessionmaker, declarative_base
from backend.app.config import settings

logger = logging.getLogger(__name__)

# Check if using PostgreSQL and normalize URL scheme
raw_db_url = settings.DATABASE_URL
if raw_db_url.startswith("postgres://"):
    raw_db_url = raw_db_url.replace("postgres://", "postgresql://", 1)

is_postgres = raw_db_url.startswith("postgresql")

# Custom JSON-backed Vector type for SQLite and fallback
class VectorType(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return json.dumps([float(x) for x in value])
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return []
        return value

# Try importing pgvector if postgres
PG_VECTOR_AVAILABLE = False
if is_postgres:
    try:
        from pgvector.sqlalchemy import Vector
        PG_VECTOR_AVAILABLE = True
    except (ImportError, Exception):
        logger.warning("pgvector package not loaded, using custom VectorType fallback")

def get_vector_column_type(dim: int = 768):
    if is_postgres and PG_VECTOR_AVAILABLE:
        try:
            from pgvector.sqlalchemy import Vector
            return Vector(dim)
        except Exception:
            return VectorType()
    return VectorType()

def _ensure_vector_index(engine) -> None:
    """Enables pgvector and creates an ANN index on questions.embedding.

    Search ranks every candidate by vector distance on this column, on every
    request. Without an index Postgres has to sequentially scan the whole
    table to do that -- fine for a handful of rows, but it gets slower (and
    pulls more into memory) as the question bank grows. HNSW is tried first
    (better recall, no retraining needed as data grows); if the pgvector
    version installed on the server is too old to support it, IVFFlat is used
    instead. Either way this is best-effort: if it fails (missing privileges,
    extension unavailable, etc.) dense search simply falls back to scoring
    candidates in Python, same as it always has.
    """
    if not (is_postgres and PG_VECTOR_AVAILABLE):
        return

    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector;")
            conn.commit()
    except Exception as e:
        logger.warning(f"Could not enable the pgvector extension ({e}); dense search will fall back to in-app scoring.")
        return

    try:
        with engine.connect() as conn:
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_questions_embedding_hnsw "
                "ON questions USING hnsw (embedding vector_cosine_ops) "
                "WITH (m = 16, ef_construction = 64);"
            )
            conn.commit()
        logger.info("pgvector HNSW index ready on questions.embedding")
        return
    except Exception as e:
        logger.debug(f"HNSW index creation failed ({e}), trying IVFFlat instead.")

    try:
        with engine.connect() as conn:
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_questions_embedding_ivfflat "
                "ON questions USING ivfflat (embedding vector_cosine_ops) "
                "WITH (lists = 100);"
            )
            conn.commit()
        logger.info("pgvector IVFFlat index ready on questions.embedding")
    except Exception as e:
        logger.warning(f"Could not create a vector index on questions.embedding ({e}); dense search will fall back to in-app scoring.")

# Database engine with automatic fallback
engine = None
if is_postgres:
    try:
        engine = create_engine(
            raw_db_url,
            echo=False,
            pool_pre_ping=True,   # test each connection before use; Neon pooler can drop idle SSL conns
            pool_recycle=280,     # recycle connections before Neon/pgbouncer idle timeout kicks in
        )
        with engine.connect() as conn:
            pass
    except Exception as exc:
        logger.warning(f"PostgreSQL connection failed ({exc}), falling back to SQLite")
        is_postgres = False
        engine = None

if engine is None:
    import os
    from backend.app.config import default_db_url
    connect_args = {"check_same_thread": False}
    engine = create_engine(default_db_url, connect_args=connect_args, echo=False)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def ensure_schema_columns():
    """Safely adds newly introduced columns to existing database tables."""
    if engine is None:
        return
    try:
        # Create all tables that do not exist yet (including sharepoint and job tables)
        import backend.app.models  # noqa: F401
        Base.metadata.create_all(bind=engine)
        _ensure_vector_index(engine)

        with engine.connect() as conn:
            # Check if questions table exists
            table_check = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name='questions';").fetchone() if not is_postgres else True
            if table_check:
                new_q_cols = [
                    ("content_hash", "VARCHAR(64)"),
                    ("topics", "TEXT"),
                    ("tags", "TEXT"),
                    ("difficulty_score", "FLOAT DEFAULT 0.50"),
                    ("cognitive_level", "VARCHAR(50) DEFAULT 'Recall / Remember'"),
                    ("audience_suitability", "TEXT"),
                    ("question_hook", "VARCHAR(50) DEFAULT 'DIRECT_TRIVIA'"),
                    ("curiosity_score", "INTEGER DEFAULT 7"),
                    ("temporal_nature", "VARCHAR(20) DEFAULT 'EVERGREEN'"),
                    ("provenance_decks", "TEXT"),
                    ("occurrence_count", "INTEGER DEFAULT 1"),
                    ("round_number", "INTEGER"),
                    ("image_refs", "TEXT DEFAULT '[]'"),
                    ("question_images", "TEXT DEFAULT '[]'"),
                    ("answer_images", "TEXT DEFAULT '[]'"),
                    ("audio_refs", "TEXT DEFAULT '[]'"),
                    ("question_audio", "TEXT DEFAULT '[]'"),
                    ("answer_audio", "TEXT DEFAULT '[]'"),
                    ("video_refs", "TEXT DEFAULT '[]'"),
                    ("question_videos", "TEXT DEFAULT '[]'"),
                    ("answer_videos", "TEXT DEFAULT '[]'"),
                    ("visual_clues", "TEXT"),
                    ("audio_transcript", "TEXT"),
                    ("video_transcript", "TEXT"),
                    ("raw_media_refs", "TEXT DEFAULT '[]'"),
                    ("source_slide_range", "VARCHAR(50)"),
                    ("duplicate_status", "VARCHAR(50) DEFAULT 'UNIQUE'"),
                    ("duplicate_similarity", "FLOAT"),
                    ("duplicate_of_id", "VARCHAR(36)")
                ]
                for col_name, col_type in new_q_cols:
                    try:
                        if is_postgres:
                            conn.exec_driver_sql(f"ALTER TABLE questions ADD COLUMN IF NOT EXISTS {col_name} {col_type};")
                        else:
                            conn.exec_driver_sql(f"ALTER TABLE questions ADD COLUMN {col_name} {col_type};")
                    except Exception:
                        pass # Column already exists

            # Check if generated_questions table exists
            gen_q_table_check = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name='generated_questions';").fetchone() if not is_postgres else True
            if gen_q_table_check:
                gen_q_cols = [
                    ("image_refs", "TEXT DEFAULT '[]'"),
                    ("question_images", "TEXT DEFAULT '[]'"),
                    ("answer_images", "TEXT DEFAULT '[]'"),
                    ("audio_refs", "TEXT DEFAULT '[]'"),
                    ("question_audio", "TEXT DEFAULT '[]'"),
                    ("answer_audio", "TEXT DEFAULT '[]'"),
                    ("visual_clues", "TEXT")
                ]
                for col_name, col_type in gen_q_cols:
                    try:
                        if is_postgres:
                            conn.exec_driver_sql(f"ALTER TABLE generated_questions ADD COLUMN IF NOT EXISTS {col_name} {col_type};")
                        else:
                            conn.exec_driver_sql(f"ALTER TABLE generated_questions ADD COLUMN {col_name} {col_type};")
                    except Exception:
                        pass

            # Check if quizzes table exists
            quiz_table_check = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name='quizzes';").fetchone() if not is_postgres else True
            if quiz_table_check:
                quiz_cols = [
                    ("quiz_format", "VARCHAR(50) DEFAULT 'TOURNAMENT_PRELIMS'"),
                    ("audience_type", "VARCHAR(50) DEFAULT NULL"),
                    ("grades", "TEXT DEFAULT '[]'"),
                    ("age_range", "VARCHAR(50) DEFAULT NULL"),
                    ("grade_min", "INTEGER DEFAULT NULL"),
                    ("grade_max", "INTEGER DEFAULT NULL"),
                    ("tags", "TEXT"),
                    ("personality", "VARCHAR(50) DEFAULT NULL")
                ]
                for col_name, col_type in quiz_cols:
                    try:
                        if is_postgres:
                            conn.exec_driver_sql(f"ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS {col_name} {col_type};")
                        else:
                            conn.exec_driver_sql(f"ALTER TABLE quizzes ADD COLUMN {col_name} {col_type};")
                    except Exception:
                        pass

            # Check if documents table exists
            doc_table_check = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name='documents';").fetchone() if not is_postgres else True
            if doc_table_check:
                new_doc_cols = [
                    ("blob_url", "TEXT"),
                    ("source", "VARCHAR(50) DEFAULT 'MANUAL'"),
                    ("sharepoint_file_id", "VARCHAR(255)"),
                    ("progress_percentage", "INTEGER DEFAULT 0"),
                    ("current_step", "VARCHAR(255)"),
                    ("cancel_requested", "BOOLEAN DEFAULT FALSE" if is_postgres else "BOOLEAN DEFAULT 0")
                ]
                for col_name, col_type in new_doc_cols:
                    try:
                        if is_postgres:
                            conn.exec_driver_sql(f"ALTER TABLE documents ADD COLUMN IF NOT EXISTS {col_name} {col_type};")
                        else:
                            conn.exec_driver_sql(f"ALTER TABLE documents ADD COLUMN {col_name} {col_type};")
                    except Exception:
                        pass
            # v6 tag system: columns added to the QShala tag tables after they first shipped.
            _v6_cols = {
                "qshala_question_metadata": [
                    ("round_number", "INTEGER"),
                    ("question_in_round", "INTEGER"),
                    ("city_name", "VARCHAR(100)"),
                    ("deck_year", "INTEGER"),
                    ("is_varsity", "BOOLEAN DEFAULT FALSE" if is_postgres else "BOOLEAN DEFAULT 0"),
                    ("relevant_year", "INTEGER"),
                    ("geo_region", "VARCHAR(100)"),
                    ("india_international", "VARCHAR(8)"),
                    ("manual_fields", "TEXT"),
                ],
                "qshala_tags": [
                    ("relevant_year", "INTEGER"),
                ],
            }
            for _tbl, _cols in _v6_cols.items():
                for col_name, col_type in _cols:
                    try:
                        if is_postgres:
                            conn.exec_driver_sql(f"ALTER TABLE {_tbl} ADD COLUMN IF NOT EXISTS {col_name} {col_type};")
                        else:
                            conn.exec_driver_sql(f"ALTER TABLE {_tbl} ADD COLUMN {col_name} {col_type};")
                    except Exception:
                        pass  # table not created yet, or column already exists
            conn.commit()

        # One-time backfill of content_hash and topics for legacy questions
        try:
            from backend.app.services.deduplication.deduplicator import Deduplicator
            from backend.app.services.tagging.auto_tagger import AutoTagger
            from backend.app.services.tagging.difficulty_engine import DifficultyEngine
            from backend.app.models.question import Question

            sess = SessionLocal()
            unhashed = sess.query(Question).filter(Question.content_hash.is_(None)).all()
            if unhashed:
                tagger = AutoTagger()
                diff_eng = DifficultyEngine()
                for q in unhashed:
                    q.content_hash = Deduplicator.compute_hash(q.question_text, q.answer)
                    t_data = tagger.tag_question(q.question_text, q.answer, q.explanation or "")
                    d_data = diff_eng.evaluate(q.question_text, q.answer, q.explanation or "")
                    q.topics = t_data["topics"]
                    q.tags = t_data["tags"]
                    q.question_hook = t_data["question_hook"]
                    q.curiosity_score = t_data["curiosity_score"]
                    q.temporal_nature = t_data["temporal_nature"]
                    q.difficulty = d_data["difficulty"]
                    q.difficulty_score = d_data["difficulty_score"]
                    q.cognitive_level = d_data["cognitive_level"]
                    q.audience_suitability = d_data["audience_suitability"]
                    q.provenance_decks = [{"document_id": q.document_id, "slide_id": q.slide_id}]
                    q.occurrence_count = 1
                sess.commit()
            sess.close()
        except Exception as be:
            logger.debug(f"Legacy backfill notice: {be}")

    except Exception as e:
        logger.debug(f"Schema auto-check notice: {e}")

try:
    ensure_schema_columns()
except Exception:
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

