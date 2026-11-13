import os
import sys
from pathlib import Path

# Ensure backend directory and project root are on sys.path in all runtimes
import types
_current_file = Path(__file__).resolve()
_backend_dir = _current_file.parent.parent
_project_root = _backend_dir.parent

if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

if "backend" not in sys.modules:
    backend_pkg = types.ModuleType("backend")
    backend_pkg.__path__ = [str(_backend_dir)]
    sys.modules["backend"] = backend_pkg

import time as _boot_time
_BOOT_T0 = _boot_time.monotonic()   # for the '[startup]' timing logs
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from backend.app.config import settings
from backend.app.database import engine, Base
from backend.app.routers import documents, questions, quizzes, stats, jobs, sharepoint, qshala_tagging, question_meta, prompt_quiz

# Register ALL SQLAlchemy models here so Base.metadata.create_all sees them before
# the first create_all call below. Importing the module is enough; the class
# definitions attach themselves to Base.metadata.
from backend.app.models.question import Question  # noqa: F401
from backend.app.models.upload_batch import UploadBatch  # noqa: F401
from backend.app.models.qshala_tag import (  # noqa: F401
    QshalaQuestionMetadata, QshalaTag, QshalaTagChange,
)
from backend.app.models.question_meta import (  # noqa: F401
    DeckMetadata, QuestionTopicPath, QuestionRepeat, QuestionEditHistory,
)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Error tracking (QShala priority plan P2-1). Optional and off by default -- sentry_sdk.init is
# only called when SENTRY_DSN is set, so this is a no-op until that env var is added.
if settings.SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            integrations=[FastApiIntegration()],
            traces_sample_rate=0.1,
            send_default_pii=False,
        )
        logger.info("Sentry error tracking enabled")
    except Exception as e:
        logger.warning(f"Sentry init failed (continuing without it): {e}")

# Initialize DB tables at import time outside Vercel serverless cold starts
# (Vercel initializes schema lazily via _ensure_runtime_ready on first non-diagnostic request).
# ensure_schema_columns() (run when backend.app.database was imported) already creates any missing table, in one
# inspection; a second create_all would re-check every table one query at a time, which is slow over a remote database.
if not (os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV")):
    from backend.app.database import schema_is_ensured
    if not schema_is_ensured():
        try:
            Base.metadata.create_all(bind=engine)
        except Exception as e:
            logger.warning(f"DB table creation notice: {e}")

# State of the whole-vault maintenance passes (repair + heal), shown in /health.
STARTUP_MAINTENANCE = {"mode": None, "state": "not_started", "seconds": None, "error": None}


def _run_startup_maintenance(include_repairs: bool) -> None:
    """The slow, whole-vault, idempotent passes. They scan every question, so on a remote database they take a while;
    on Cloud Run they run in a background thread so the app answers requests straight away."""
    import time as _time
    t0 = _time.monotonic()
    STARTUP_MAINTENANCE["state"] = "running"
    try:
        if include_repairs:
            from backend.app.database import run_startup_repairs
            run_startup_repairs()
        try:
            from backend.app.services.ingestion.heal_specialized import heal_specialized_questions
            _db_heal = SessionLocal()
            try:
                heal_specialized_questions(_db_heal)
            finally:
                _db_heal.close()
        except Exception as e_heal:
            logger.warning(f"Specialized questions startup heal skipped: {e_heal}")
        STARTUP_MAINTENANCE["state"] = "done"
    except Exception as e:
        STARTUP_MAINTENANCE["state"] = "failed"
        STARTUP_MAINTENANCE["error"] = type(e).__name__
        logger.warning(f"Startup maintenance failed (the app keeps serving): {e}")
    finally:
        STARTUP_MAINTENANCE["seconds"] = round(_time.monotonic() - t0, 1)
        logger.info(f"Startup maintenance {STARTUP_MAINTENANCE['state']} in {STARTUP_MAINTENANCE['seconds']}s")

from contextlib import asynccontextmanager
from backend.app.database import SessionLocal
from backend.app.models.document import Document

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB tables and schema columns
    try:
        from backend.app.database import ensure_schema_columns
        ensure_schema_columns()
    except Exception as e:
        logger.warning(f"DB schema migration notice: {e}")

    try:
        db = SessionLocal()
        doc_count = db.query(Document).count()
        # In cloud_run_job mode, the worker is a separate container that survives API restarts/deploys,
        # so skip marking PROCESSING as FAILED on startup (stale detection via heartbeat is the safety net).
        if settings.INGESTION_MODE != "cloud_run_job":
            stuck = db.query(Document).filter(Document.processing_status == "PROCESSING").all()
            for d in stuck:
                d.processing_status = "FAILED"
                d.processing_error = ("Processing stopped because the server restarted, most likely because the deck is "
                                      "too large for the server's memory. Compress the pictures and upload it again.")
            if stuck:
                db.commit()
                logger.warning(f"Marked {len(stuck)} interrupted upload(s) as FAILED")
        else:
            logger.info("INGESTION_MODE=cloud_run_job: skipping startup PROCESSING -> FAILED mark for documents; worker container runs independently")
        db.close()
        if doc_count == 0:
            logger.info("Knowledge base is empty. Running automatic seeding of sample decks...")
            from backend.app.utils.seed_knowledge_base import seed
            await seed(force=False)
            logger.info("Auto-seeding completed.")

        from backend.app.database import startup_maintenance_mode
        mode = startup_maintenance_mode()
        STARTUP_MAINTENANCE["mode"] = mode
        if mode == "background":
            import threading
            threading.Thread(target=_run_startup_maintenance, args=(True,), name="startup-maintenance", daemon=True).start()
        else:
            _run_startup_maintenance(False)   # repairs already ran when the database module was imported
    except Exception as e:
        logger.warning(f"Lifespan seed check exception: {e}")

    logger.info("[startup] ready to serve %.1fs after the process started (maintenance mode: %s)",
                _boot_time.monotonic() - _BOOT_T0, STARTUP_MAINTENANCE.get("mode"))

    # Upload queue: reconcile jobs interrupted by a restart, then start the single serial worker.
    try:
        from backend.app.config import settings as _settings
        if _settings.UPLOAD_QUEUE_ENABLED:
            from backend.app.services.ingestion import upload_queue
            if _settings.INGESTION_MODE != "cloud_run_job":
                _db = SessionLocal()
                try:
                    upload_queue.reconcile_on_startup(_db)
                finally:
                    _db.close()
            else:
                logger.info("INGESTION_MODE=cloud_run_job: skipping startup reconcile for upload queue jobs")
            upload_queue.start_worker()
            logger.info("Upload queue worker started")
    except Exception as e:
        logger.warning(f"Upload queue startup skipped: {e}")
    yield

import logging as _logging
_logging.getLogger("httpx").setLevel(_logging.WARNING)   # its INFO lines print the API key in the URL

app = FastAPI(
    title=settings.PROJECT_NAME or "QShala Quiz Intelligence Platform",
    openapi_url=f"{settings.API_V1_STR or '/api/v1'}/openapi.json",
    docs_url=f"{settings.API_V1_STR or '/api/v1'}/docs",
    lifespan=lifespan
)

# CORS setup. CORS_ORIGINS is a comma-separated allowlist (e.g. the real Vercel domain(s));
# unset/empty falls back to localhost-only rather than "*" -- an open CORS policy combined with
# the optional X-API-Key auth above would let any website relay authenticated requests once a
# key is in use, so this never defaults to wildcard.
_cors_origins = (
    [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
    if settings.CORS_ORIGINS
    else ["http://localhost:3000", "http://127.0.0.1:3000"]
)
_cors_origin_regex = (
    r"^https://qshala[a-z0-9-]*\.vercel\.app$|^https://qshala[a-z0-9-]*\.run\.app$|^http://localhost(:\d+)?$|^http://127\.0\.0\.1(:\d+)?$"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)

# Performance backlog item 1 (QSHALA_PERFORMANCE_IDEAS.md): gzip JSON responses over 500 bytes.
# Search results carry topic_paths, image arrays, transcripts -- easy to shrink, free bandwidth win.
app.add_middleware(GZipMiddleware, minimum_size=500)

# Optional shared-secret gate. Off by default (API_KEY_SECRET unset) so this deploys with zero
# behavior change; set API_KEY_SECRET on the host once the frontend is sending X-API-Key to start
# requiring it. This is not user auth / multi-tenancy -- it only stops an anonymous visitor with the
# URL from uploading decks, deleting quizzes, or burning the Gemini/R2 quota.
_PUBLIC_PREFIXES = (
    "/static", f"{settings.API_V1_STR}/static", "/slides",
    "/docs", f"{settings.API_V1_STR}/docs",
    "/openapi.json", f"{settings.API_V1_STR}/openapi.json",
    "/redoc",
)
_PUBLIC_EXACT = {"/", "/api", settings.API_V1_STR,
                 "/health", "/api/health", f"{settings.API_V1_STR}/health"}

def _api_key_required() -> bool:
    """REQUIRE_API_KEY=1 makes the gate fail closed: with no API_KEY_SECRET configured, protected routes answer 503
    instead of being open. Off by default so an existing deployment without the secret keeps working."""
    return os.getenv("REQUIRE_API_KEY", "").strip().lower() in ("1", "true", "yes")


def _api_key_matches(provided: str, secret: str) -> bool:
    import hmac
    return hmac.compare_digest(provided.encode("utf-8"), secret.encode("utf-8"))


if os.getenv("K_SERVICE") and not settings.API_KEY_SECRET:
    logging.getLogger(__name__).warning(
        "Running on Cloud Run with API_KEY_SECRET unset: every API route is open to anonymous callers. "
        "Set API_KEY_SECRET (and REQUIRE_API_KEY=1 to refuse to run open).")


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.middleware("http")
async def require_api_key(request, call_next):
    from fastapi.responses import JSONResponse
    if request.method == "OPTIONS":
        return await call_next(request)
    path = request.url.path
    if path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIXES):
        return await call_next(request)
    if not settings.API_KEY_SECRET:
        if _api_key_required():
            return JSONResponse(status_code=503, content={
                "detail": "API key auth is required (REQUIRE_API_KEY) but API_KEY_SECRET is not configured"})
        return await call_next(request)
    if not _api_key_matches(request.headers.get("x-api-key", ""), settings.API_KEY_SECRET):
        return JSONResponse(status_code=401, content={"detail": "Missing or invalid X-API-Key header"})
    return await call_next(request)

class MultiDirStaticFiles(StaticFiles):
    """Static file handler that falls back to bundled repository storage when running in serverless (/tmp)."""
    def __init__(self, *args, fallback_dirs=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fallback_dirs = [Path(d) for d in (fallback_dirs or []) if Path(d).exists()]

    def lookup_path(self, path: str):
        full_path, stat_result = super().lookup_path(path)
        if stat_result is not None:
            return full_path, stat_result
        for fb in self.fallback_dirs:
            fb_path = fb / path
            # Containment check: Starlette rejects ".." for the primary directory, but this fallback
            # used to join the raw path itself, so "/static/%2e%2e/<file>" could read files outside
            # the fallback folder. Resolve first and require the result to stay inside it.
            try:
                resolved = fb_path.resolve()
                fb_root = fb.resolve()
            except (OSError, RuntimeError):
                continue
            if not (resolved == fb_root or fb_root in resolved.parents):
                continue
            if resolved.exists() and resolved.is_file():
                return str(resolved), resolved.stat()
        return full_path, stat_result

# Mount static file directory for extracted slide assets with fallback
settings.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
static_fallbacks = [
    _backend_dir / "storage",
    _project_root / "backend" / "storage",
    _project_root / "storage",
    _project_root / "frontend" / "public",
    _project_root / "frontend" / "public" / "static",
    _backend_dir.parent / "frontend" / "public" / "static",
]
slide_fallbacks = [
    _backend_dir / "storage" / "slides",
    _project_root / "backend" / "storage" / "slides",
    _project_root / "storage" / "slides",
    _project_root / "frontend" / "public" / "static" / "slides",
    _backend_dir.parent / "frontend" / "public" / "static" / "slides",
    settings.SLIDES_DIR,
]
app.mount("/static", MultiDirStaticFiles(directory=str(settings.STORAGE_DIR), fallback_dirs=static_fallbacks), name="static")
app.mount(f"{settings.API_V1_STR}/static", MultiDirStaticFiles(directory=str(settings.STORAGE_DIR), fallback_dirs=static_fallbacks), name="api_v1_static")
app.mount("/slides", MultiDirStaticFiles(directory=str(settings.SLIDES_DIR), fallback_dirs=slide_fallbacks), name="slides")

# Mount API routers
app.include_router(stats.router, prefix=settings.API_V1_STR)
app.include_router(documents.router, prefix=settings.API_V1_STR)
app.include_router(questions.router, prefix=settings.API_V1_STR)
from backend.app.routers import trash as _trash_router
app.include_router(_trash_router.router, prefix=settings.API_V1_STR)
app.include_router(question_meta.router, prefix=settings.API_V1_STR)
app.include_router(quizzes.router, prefix=settings.API_V1_STR)
app.include_router(jobs.router, prefix=settings.API_V1_STR)
app.include_router(sharepoint.router, prefix=settings.API_V1_STR)
app.include_router(qshala_tagging.router, prefix=settings.API_V1_STR)
app.include_router(qshala_tagging.router)  # Also mount at /qshala/* directly
app.include_router(prompt_quiz.router, prefix=settings.API_V1_STR)

@app.get("/")
@app.get("/api")
@app.get(f"{settings.API_V1_STR}")
def root():
    return {
        "service": settings.PROJECT_NAME,
        "status": "online",
        "docs": f"{settings.API_V1_STR}/docs"
    }

def _gemini_models_status():
    """Informational only: never affects the healthy/unhealthy verdict and never blocks the health check."""
    try:
        from backend.app.services.ai.model_check import get_status
        return get_status()
    except Exception:
        return None


@app.get("/health")
@app.get("/api/health")
@app.get(f"{settings.API_V1_STR}/health")
def health():
    """Live health check: actually pings the database, and checks that the Gemini key and (if
    configured) R2 credentials are present -- not just whether the process is up. Returns 503 with
    the specific failing check(s) so monitoring (and whoever is staring at a cold-start timeout) can
    tell what's actually wrong, instead of a blind 200 OK that doesn't mean anything."""
    from fastapi.responses import JSONResponse
    from sqlalchemy import text as _sql_text
    from backend.app.database import DB_BACKEND, DB_PERSISTENT, raw_db_url, engine

    host = "local file"
    if DB_PERSISTENT:
        try:
            host = raw_db_url.split("@", 1)[1].split("/", 1)[0]   # host only, never credentials
        except Exception:
            host = "postgres"

    problems = []
    try:
        with engine.connect() as conn:
            conn.execute(_sql_text("SELECT 1"))
        db_reachable = True
    except Exception as e:
        db_reachable = False
        problems.append(f"database unreachable: {e}")

    gemini_configured = bool(settings.GEMINI_API_KEY)
    if not gemini_configured:
        problems.append("GEMINI_API_KEY is not set")

    r2_fields = [settings.R2_ACCOUNT_ID, settings.R2_ACCESS_KEY_ID, settings.R2_SECRET_ACCESS_KEY,
                 settings.R2_BUCKET_NAME, settings.R2_PUBLIC_BASE_URL]
    r2_touched = any(r2_fields)
    r2_complete = all(r2_fields)
    if r2_touched and not r2_complete:
        problems.append("R2 credentials are partially set (some vars missing)")

    if not DB_PERSISTENT:
        problems.append("Data is NOT persistent (SQLite on ephemeral disk). Set DATABASE_URL to Neon.")

    healthy = db_reachable and DB_PERSISTENT and gemini_configured and (not r2_touched or r2_complete)

    body = {
        "status": "healthy" if healthy else "unhealthy",
        "database": DB_BACKEND,
        "persistent": DB_PERSISTENT,
        "db_host": host,
        "checks": {
            "database_reachable": db_reachable,
            "gemini_key_configured": gemini_configured,
            "r2_configured": r2_complete if r2_touched else None,
        },
        "problems": problems or None,
        "gemini_models": _gemini_models_status(),
        "startup_maintenance": dict(STARTUP_MAINTENANCE),
        "warning": None if DB_PERSISTENT else
                   "Data is NOT persistent (SQLite on ephemeral disk). Set DATABASE_URL to Neon.",
    }
    return JSONResponse(content=body, status_code=200 if healthy else 503)

