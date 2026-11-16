"""Give a search vector to every question that has none, so smart search can find it.

A question is stored without a vector when the embedding call failed during its upload (the deck still ingests). Such
questions are invisible to smart search. The vector is built from exactly the same text as at upload
(pipeline.embedding_text).

    python -m backend.scripts.backfill_embeddings            # dry run: counts only, no Gemini call, no write
    python -m backend.scripts.backfill_embeddings --apply    # embed and save, in batches of --batch (default 96)

Set REQUIRE_POSTGRES=1 to run it against the production database (it refuses to run on SQLite then). Each batch is
committed on its own, so a stop part-way keeps what was done; running it again only picks up what is still missing.
A batch whose embedding call fails is left untouched (no fake or zero vectors are written).
"""
import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
os.environ.setdefault("STARTUP_MAINTENANCE_MODE", "off")

logger = logging.getLogger("backfill_embeddings")


def _question_dict(q, use_qshala_tagger: bool) -> dict:
    # At upload, with the QShala tagger on (the default), the vector text has no topic prefix: the legacy tagger's
    # primary_topic/topics are not set then. Mirror that so a backfilled vector matches its neighbours.
    return {
        "primary_topic": None if use_qshala_tagger else q.topic,
        "topics": [] if use_qshala_tagger else (q.topics or []),
        "question_text": q.question_text or "",
        "answer": q.answer or "",
        "visual_clues": q.visual_clues,
        "explanation": q.explanation,
    }


async def backfill(db, apply: bool, batch: int = 96, limit: int = 0, provider=None) -> dict:
    from backend.app.models.question import Question
    from backend.app.services.ingestion import pipeline

    ids = [r[0] for r in db.query(Question.id).filter(Question.embedding.is_(None)).order_by(Question.created_at).all()]
    if limit:
        ids = ids[:limit]
    stats = {"missing": len(ids), "embedded": 0, "failed": 0, "batches": 0}
    if not apply or not ids:
        return stats
    if provider is None:
        from backend.app.services.ai.factory import get_embedding_provider
        provider = get_embedding_provider()
    if getattr(provider, "may_substitute_local", False):
        # A provider that may quietly fall back to local vectors would store vectors that don't match the rest.
        logger.warning("embedding provider may substitute local vectors; failed items are skipped, not substituted")
    label = pipeline.embedding_model_label(provider)

    for start in range(0, len(ids), max(1, batch)):
        chunk_ids = ids[start:start + batch]
        rows = db.query(Question).filter(Question.id.in_(chunk_ids)).all()
        texts = [pipeline.embedding_text(_question_dict(q, pipeline.USE_QSHALA_TAGGER)) for q in rows]
        stats["batches"] += 1
        try:
            vectors = await provider.get_embeddings(texts)
        except Exception as e:
            logger.warning("batch %d failed (%s); left without vectors", stats["batches"], type(e).__name__)
            stats["failed"] += len(rows)
            db.rollback()
            continue
        for q, vec in zip(rows, vectors):
            if vec:
                q.embedding = vec
                q.embedding_model = label
                stats["embedded"] += 1
            else:
                stats["failed"] += 1
        db.commit()
        logger.info("batch %d: %d/%d embedded so far", stats["batches"], stats["embedded"], len(ids))
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="call Gemini and save the vectors (default: dry run)")
    ap.add_argument("--batch", type=int, default=96)
    ap.add_argument("--limit", type=int, default=0, help="at most this many questions (0 = all)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from backend.app.database import SessionLocal, engine
    if os.getenv("REQUIRE_POSTGRES") == "1" and engine.dialect.name != "postgresql":
        print("REQUIRE_POSTGRES=1 but the database is not Postgres; stopping.")
        return 1
    db = SessionLocal()
    try:
        stats = asyncio.run(backfill(db, apply=args.apply, batch=args.batch, limit=args.limit))
    finally:
        db.close()
    mode = "applied" if args.apply else "dry run"
    print(f"{mode} on {engine.dialect.name}: {stats}")
    return 0 if stats["failed"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
