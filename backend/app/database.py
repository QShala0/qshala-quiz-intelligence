import json
import logging
import os
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
    # Disable Postgres' idle-in-transaction killer for this app's connections. It is a backstop only:
    # the ingestion pipeline is structured so no transaction is ever held open across a slow AI stage
    # (see IngestionPipeline.run). NOTE: through Neon's pooled (-pooler) endpoint PgBouncer may ignore
    # or reject this startup option, so we never let it drop us to SQLite — if the option is refused we
    # retry the engine without it.
    # NOTE: Neon's pooled (-pooler) endpoint runs PgBouncer in transaction mode, which strictly
    # rejects startup options like idle_in_transaction_session_timeout. Attempting it causes psycopg
    # to fail sequentially across all resolved IPv6 and IPv4 addresses (6+ attempts), taking 5-15s
    # and causing Cloud Run startup TCP probes to fail with DEADLINE_EXCEEDED.
    # Therefore, skip startup options if connecting through a pooler.
    is_pooled = "-pooler" in raw_db_url or "neon.tech" in raw_db_url
    _pg_base = dict(
        echo=False,
        pool_pre_ping=True,
        pool_recycle=280,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
    )
    _pg_connect_args = None if is_pooled else {"options": "-c idle_in_transaction_session_timeout=0"}
    _attempts = (_pg_connect_args, None) if _pg_connect_args else (None,)
    for _attempt_args in _attempts:
        try:
            _kwargs = dict(_pg_base)
            if _attempt_args:
                _kwargs["connect_args"] = _attempt_args
            engine = create_engine(raw_db_url, **_kwargs)
            with engine.connect() as conn:
                pass
            if _attempt_args:
                logger.info("PostgreSQL engine ready (idle-in-transaction timeout disabled)")
            else:
                logger.info("PostgreSQL engine ready (plain; startup option skipped or not accepted by endpoint)")
            break
        except Exception as exc:
            engine = None
            if _attempt_args:
                logger.warning(f"PostgreSQL connect with idle-timeout option failed ({exc}); retrying without it")
                continue
            logger.warning(f"PostgreSQL connection failed ({exc}), falling back to SQLite")
            is_postgres = False

if engine is None:
    # About to use SQLite. On a deployed host this is a DATA-LOSS trap: the file lives on the
    # container's ephemeral disk and is wiped on every restart/sleep. Refuse to start if the operator
    # declared this environment must be persistent (REQUIRE_POSTGRES=1); otherwise run but warn loudly.
    if getattr(settings, "REQUIRE_POSTGRES", False) and not ((os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV")) and not os.environ.get("DATABASE_URL")):
        raise RuntimeError(
            "REQUIRE_POSTGRES is set but no PostgreSQL connection is available. "
            "DATABASE_URL is missing/empty or Neon was unreachable at startup, so the app would fall "
            "back to SQLite on an ephemeral disk and LOSE all data on the next restart. "
            "Set DATABASE_URL to your Neon connection string (…?sslmode=require) and redeploy."
        )
    from backend.app.config import default_db_url
    sqlite_url = raw_db_url if (raw_db_url and raw_db_url.startswith("sqlite")) else default_db_url
    connect_args = {"check_same_thread": False}
    engine = create_engine(sqlite_url, connect_args=connect_args, echo=False)
    logger.warning(
        "=" * 78 + "\n"
        "  DATA IS NOT PERSISTENT: running on SQLite (%s).\n"
        "  On Google Cloud Run/Render this file is deleted on restart or sleep — uploads will vanish.\n"
        "  Set DATABASE_URL (Neon) and REQUIRE_POSTGRES=1 for a persistent database.\n"
        + "=" * 78, sqlite_url)

# What database are we actually on? Read by the /health endpoint so the operator can verify
# persistence without guessing.
DB_BACKEND = "postgres" if is_postgres else "sqlite"
DB_PERSISTENT = is_postgres

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


from contextlib import contextmanager


@contextmanager
def short_session():
    """A short-lived DB session that commits on success, rolls back on error, and always closes.

    Use this for a bounded unit of DB work (insert a batch, run a query) — never hold one open
    across a slow AI/network call. Keeping sessions short is what prevents the idle-in-transaction
    connection drops that Neon enforces on long-running ingestion jobs.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

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
            if is_postgres:
                try:
                    conn.exec_driver_sql("SET lock_timeout = '2s';")
                except Exception:
                    pass
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
                    ("duplicate_of_id", "VARCHAR(36)"),
                    ("format_payload", "JSON"),
                    ("question_format", "VARCHAR(20) DEFAULT 'STANDARD_QA'"),
                    ("format_data", "JSON"),
                    ("embedding_model", "VARCHAR(64)"),
                    ("play_mode", "VARCHAR(16)"),
                    ("slot", "VARCHAR(10) NOT NULL DEFAULT 'LIVE'"),
                    ("topic_label", "VARCHAR(100)"),
                    ("format_needs_review", "BOOLEAN NOT NULL DEFAULT FALSE"),
                ]
                for col_name, col_type in new_q_cols:
                    try:
                        if is_postgres:
                            conn.exec_driver_sql(f"ALTER TABLE questions ADD COLUMN IF NOT EXISTS {col_name} {col_type};")
                        else:
                            conn.exec_driver_sql(f"ALTER TABLE questions ADD COLUMN {col_name} {col_type};")
                    except Exception:
                        pass # Column already exists

            # Check if ingestion_jobs table exists
            job_table_check = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name='ingestion_jobs';").fetchone() if not is_postgres else True
            if job_table_check:
                job_cols = [
                    ("batch_id", "VARCHAR(36)"),
                    ("client_item_id", "VARCHAR(64)"),
                    ("execution_name", "VARCHAR(255)")
                ]
                for col_name, col_type in job_cols:
                    try:
                        if is_postgres:
                            conn.exec_driver_sql(f"ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS {col_name} {col_type};")
                        else:
                            conn.exec_driver_sql(f"ALTER TABLE ingestion_jobs ADD COLUMN {col_name} {col_type};")
                    except Exception:
                        pass

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
                    ("visual_clues", "TEXT"),
                    ("format_payload", "JSON")
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

            # Check if upload_batches table exists
            batch_table_check = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name='upload_batches';").fetchone() if not is_postgres else True
            if batch_table_check:
                try:
                    if is_postgres:
                        conn.exec_driver_sql("ALTER TABLE upload_batches ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP WITH TIME ZONE;")
                    else:
                        conn.exec_driver_sql("ALTER TABLE upload_batches ADD COLUMN last_seen_at DATETIME;")
                except Exception:
                    pass

            # Ensure upload_sessions table exists
            try:
                if is_postgres:
                    conn.exec_driver_sql("""
                        CREATE TABLE IF NOT EXISTS upload_sessions (
                            upload_id VARCHAR(64) PRIMARY KEY,
                            doc_id VARCHAR(64) NOT NULL,
                            raw_key TEXT NOT NULL,
                            r2_upload_id TEXT NOT NULL,
                            parts JSON,
                            total_bytes BIGINT DEFAULT 0,
                            filename VARCHAR(255) NOT NULL,
                            title VARCHAR(255),
                            year INTEGER,
                            batch_id VARCHAR(64),
                            client_item_id VARCHAR(64),
                            status VARCHAR(32) DEFAULT 'OPEN',
                            document_id VARCHAR(64),
                            created_at TIMESTAMP WITH TIME ZONE,
                            updated_at TIMESTAMP WITH TIME ZONE
                        );
                        CREATE INDEX IF NOT EXISTS ix_upload_sessions_batch_id ON upload_sessions (batch_id);
                        CREATE INDEX IF NOT EXISTS ix_upload_sessions_status ON upload_sessions (status);
                    """)
                    try:
                        conn.exec_driver_sql('ALTER TABLE upload_sessions ALTER COLUMN r2_upload_id TYPE TEXT;')
                        conn.exec_driver_sql('ALTER TABLE upload_sessions ALTER COLUMN raw_key TYPE TEXT;')
                    except Exception:
                        pass
                else:
                    conn.exec_driver_sql("""
                        CREATE TABLE IF NOT EXISTS upload_sessions (
                            upload_id VARCHAR(64) PRIMARY KEY,
                            doc_id VARCHAR(64) NOT NULL,
                            raw_key TEXT NOT NULL,
                            r2_upload_id TEXT NOT NULL,
                            parts JSON,
                            total_bytes BIGINT DEFAULT 0,
                            filename VARCHAR(255) NOT NULL,
                            title VARCHAR(255),
                            year INTEGER,
                            batch_id VARCHAR(64),
                            client_item_id VARCHAR(64),
                            status VARCHAR(32) DEFAULT 'OPEN',
                            document_id VARCHAR(64),
                            created_at DATETIME,
                            updated_at DATETIME
                        );
                        CREATE INDEX IF NOT EXISTS ix_upload_sessions_batch_id ON upload_sessions (batch_id);
                        CREATE INDEX IF NOT EXISTS ix_upload_sessions_status ON upload_sessions (status);
                    """)
            except Exception as e:
                logger.debug(f"upload_sessions table creation notice: {e}")

            # Check if documents table exists
            doc_table_check = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name='documents';").fetchone() if not is_postgres else True
            if doc_table_check:
                new_doc_cols = [
                    ("blob_url", "TEXT"),
                    ("source", "VARCHAR(50) DEFAULT 'MANUAL'"),
                    ("sharepoint_file_id", "VARCHAR(255)"),
                    ("cancel_requested", "BOOLEAN DEFAULT FALSE"),
                    ("progress_detail", "JSON"),
                    ("file_sha256", "VARCHAR(64)"),
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
                    ("cached_tokens", "INTEGER"),
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

            _BACKFILL_BATCH = 200
            sess = SessionLocal()
            try:
                total = sess.query(Question).filter(Question.content_hash.is_(None)).count()
                if total:
                    tagger = AutoTagger()
                    diff_eng = DifficultyEngine()
                    offset = 0
                    while offset < total:
                        batch = (sess.query(Question)
                                 .filter(Question.content_hash.is_(None))
                                 .limit(_BACKFILL_BATCH).all())
                        if not batch:
                            break
                        for q in batch:
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
                        offset += len(batch)
            finally:
                sess.close()
        except Exception as be:
            logger.debug(f"Legacy backfill notice: {be}")

    except Exception as e:
        logger.debug(f"Schema auto-check notice: {e}")

# Ensure schema columns are initialized immediately across all database types
# (SQLite, PostgreSQL/Neon, Cloud SQL) so local and serverless workers never fail on missing columns.
try:
    ensure_schema_columns()
except Exception as e:
    logger.warning(f"Database auto-migration notice: {e}")

def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

