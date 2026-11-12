# Cloud Run deploys this by building from the repo root (gcloud run deploy --source .
# or via a Cloud Build GitHub trigger). Only backend/ is needed in the image -- the
# frontend stays on Vercel and is never built here.
FROM python:3.12-slim

WORKDIR /app

# build-essential + libpq-dev are a safety net in case any dependency needs to compile
# from source on this platform; psycopg2-binary/Pillow normally ship prebuilt wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install the exact versions in the lock file (built for Python 3.12) so a rebuild never silently changes a package.
# To upgrade: see backend/DEPENDENCIES.md.
COPY backend/requirements.txt backend/requirements.lock.txt backend/
RUN pip install --no-cache-dir -r backend/requirements.lock.txt

COPY backend backend

# Run as non-root for defense in depth
RUN groupadd -r qshala && useradd -r -g qshala -d /app -s /sbin/nologin qshala \
    && mkdir -p /tmp/storage /tmp/storage/uploads /tmp/storage/slides /tmp/storage/exports /tmp/storage/blobs \
    && chown -R qshala:qshala /app /tmp/storage
USER qshala

ENV PYTHONUNBUFFERED=1
ENV SERVER_MEMORY_LIMIT_MB=2048
ENV UPLOAD_CONCURRENCY=32
ENV UPLOAD_SLOTS=64
ENV STREAM_COPY_CHUNK=2097152
ENV VISION_CONCURRENCY=12
ENV TAG_CONCURRENCY=8
ENV CHUNK_CONCURRENCY=8
ENV BIG_DECODE_CONCURRENCY=6
ENV MALLOC_ARENA_MAX=4
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:${PORT:-8080}/health || exit 1

# Cloud Run injects $PORT (defaults to 8080); single worker -- see .env.example, the
# upload queue's advisory lock and in-process state assume one process per container.
CMD exec uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8080}
