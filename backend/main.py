import os
import sys
import types
from pathlib import Path

# Ensure the backend directory is in sys.path
_current_dir = Path(__file__).resolve().parent
if str(_current_dir) not in sys.path:
    sys.path.insert(0, str(_current_dir))

# Alias "backend" in sys.modules so that imports like "from backend.app..."
# resolve correctly both locally and in Vercel's serverless environment (/var/task)
if "backend" not in sys.modules:
    backend_pkg = types.ModuleType("backend")
    backend_pkg.__path__ = [str(_current_dir)]
    sys.modules["backend"] = backend_pkg

# Also add the parent directory to sys.path if it exists
_parent_dir = _current_dir.parent
if str(_parent_dir) not in sys.path:
    sys.path.insert(0, str(_parent_dir))

import threading
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

app = FastAPI(title="QShala Service Root")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_init_error = None
_init_traceback = None
_runtime_ready = False
_runtime_ready_lock = threading.Lock()
_DIAGNOSTIC_PATHS = frozenset({
    "/api/ping",
    "/ping",
    "/api/diagnostics",
    "/diagnostics",
    "/health",
    "/api/health",
    "/api/v1/health",
})


def _ensure_runtime_ready() -> bool:
    """Lazily initialize DB schema and startup repairs once per worker process."""
    global _runtime_ready
    if _runtime_ready or _init_error is not None:
        return _runtime_ready
    with _runtime_ready_lock:
        if _runtime_ready or _init_error is not None:
            return _runtime_ready
        try:
            from backend.app.database import ensure_schema_columns
            if ensure_schema_columns():
                _runtime_ready = True
        except Exception as em:
            print("Schema ensure notice:", em)
        return _runtime_ready


@app.middleware("http")
async def _lazy_runtime_init_middleware(request: Request, call_next):
    normalized_path = request.url.path.rstrip("/") or "/"
    if request.method != "OPTIONS" and normalized_path not in _DIAGNOSTIC_PATHS and not _runtime_ready:
        await run_in_threadpool(_ensure_runtime_ready)
    return await call_next(request)


@app.get("/api/ping")
@app.get("/ping")
def ping():
    return {
        "status": "healthy",
        "service": "qshala-backend",
        "has_init_error": _init_error is not None,
        "schema_ready": _runtime_ready,
    }


@app.get("/api/diagnostics")
@app.get("/diagnostics")
def diagnostics():
    db_test = None
    db_meta = {"schema_ready": _runtime_ready}
    try:
        from backend.app.database import DB_BACKEND, DB_PERSISTENT, raw_db_url
        host = "local file"
        if DB_PERSISTENT:
            try:
                host = raw_db_url.split("@", 1)[1].split("/", 1)[0]
            except Exception:
                host = "postgres"
        db_meta = {
            "db_backend": DB_BACKEND,
            "db_persistent": DB_PERSISTENT,
            "db_host": host,
            "schema_ready": _runtime_ready,
        }
    except Exception:
        pass

    try:
        from backend.app.database import SessionLocal
        from backend.app.models.question import Question
        db = SessionLocal()
        try:
            q_count = db.query(Question).count()
            db_test = {"status": "ok", "question_count": q_count}
        finally:
            db.close()
    except Exception as e:
        import traceback
        db_test = {"status": "error", "error": str(e), "traceback": traceback.format_exc().splitlines()}

    return {
        "init_error": _init_error,
        "traceback": _init_traceback.splitlines() if _init_traceback else None,
        "db_test": db_test,
        **db_meta,
        "sys_path": sys.path,
        "cwd": os.getcwd(),
        "files": os.listdir("."),
        "python_version": sys.version
    }


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    import traceback
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "traceback": traceback.format_exc().splitlines()}
    )


try:
    from backend.app.main import app as qshala_app, MultiDirStaticFiles
    from backend.app.config import settings

    for r in qshala_app.routes:
        app.routes.append(r)

    # Direct root mounts to guarantee static asset resolution on Vercel
    static_dirs = [
        _current_dir / "storage",
        _parent_dir / "backend" / "storage",
        _parent_dir / "frontend" / "public" / "static",
        settings.STORAGE_DIR,
    ]
    slide_dirs = [
        _current_dir / "storage" / "slides",
        _parent_dir / "backend" / "storage" / "slides",
        _parent_dir / "frontend" / "public" / "static" / "slides",
        settings.SLIDES_DIR,
    ]
    app.mount("/static", MultiDirStaticFiles(directory=str(settings.STORAGE_DIR), fallback_dirs=static_dirs), name="root_static")
    app.mount(f"{settings.API_V1_STR}/static", MultiDirStaticFiles(directory=str(settings.STORAGE_DIR), fallback_dirs=static_dirs), name="root_api_static")
    app.mount("/slides", MultiDirStaticFiles(directory=str(settings.SLIDES_DIR), fallback_dirs=slide_dirs), name="root_slides")
except Exception as e:
    import traceback
    _init_error = str(e)
    _init_traceback = traceback.format_exc()

if _init_error is not None:
    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
    def err_handler(path: str):
        return JSONResponse({
            "error": "Backend initialization failed",
            "details": _init_error,
            "traceback": _init_traceback.splitlines() if _init_traceback else []
        }, status_code=500)

__all__ = ["app"]
