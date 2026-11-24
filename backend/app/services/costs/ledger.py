"""Append-only cost ledger service and attribution scope management.

Rules:
- Append-only. Nothing is overwritten.
- Cost bookkeeping must never break ingestion or quizzes: every ledger write is wrapped,
  runs in its own short session, and errors are logged and swallowed.
- Scoped aggregation: accumulate units per (operation, model) in memory and flush once on scope exit.
- Unattributed calls accumulate in a memory bucket and flush periodically.
"""
from __future__ import annotations

import atexit
import contextvars
import logging
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models.cost import CostEvent
from backend.app.services.costs.rate_card import calculate_event_cost, get_active_rate_card

logger = logging.getLogger(__name__)

# ContextVar for active CostScope in asyncio tasks and threads
_CURRENT_COST_SCOPE: contextvars.ContextVar[Optional[CostScope]] = contextvars.ContextVar(
    "current_cost_scope", default=None
)


def get_current_cost_scope() -> Optional[CostScope]:
    """Return the active CostScope for the current context (or None)."""
    return _CURRENT_COST_SCOPE.get()


def record_event(
    *,
    category: str,
    operation: str,
    units: Optional[Dict[str, Any]] = None,
    rates: Optional[Dict[str, Any]] = None,
    rate_card_version: Optional[int] = None,
    document_id: Optional[str] = None,
    quiz_id: Optional[str] = None,
    job_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    model: Optional[str] = None,
    source: str = "measured",
    occurred_at: Optional[datetime] = None,
    period: Optional[str] = None,
    dedupe_key: Optional[str] = None,
    note: Optional[str] = None,
    cost_usd: Optional[float] = None,
    fx_rate: Optional[float] = None,
    cost_inr: Optional[float] = None,
    tax_inr: float = 0.0,
    is_batch: bool = False,
    db_session: Optional[Session] = None,
) -> Optional[CostEvent]:
    """Build and insert a CostEvent in an isolated short session with ON CONFLICT DO NOTHING.

    Never raises: any failure is logged and swallowed so cost tracking never breaks app flows.
    """
    try:
        dt = occurred_at or datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        p = period or dt.strftime("%Y-%m")
        units_dict = dict(units or {})

        # Compute cost if not provided
        if cost_usd is None or cost_inr is None or rates is None:
            calc_usd, calc_inr, calc_rates, calc_ver, calc_fx = calculate_event_cost(
                category=category,
                operation=operation,
                units=units_dict,
                model=model,
                fx_rate=fx_rate,
                is_batch=is_batch,
            )
            cost_usd = calc_usd if cost_usd is None else float(cost_usd)
            cost_inr = calc_inr if cost_inr is None else float(cost_inr)
            rates = calc_rates if rates is None else rates
            rate_card_version = calc_ver if rate_card_version is None else rate_card_version
            fx_rate = calc_fx if fx_rate is None else float(fx_rate)
        else:
            rate_card_version = rate_card_version or 1
            fx_rate = fx_rate or 95.0

        event_data = {
            "id": str(uuid.uuid4()),
            "occurred_at": dt,
            "category": category,
            "operation": operation,
            "document_id": document_id,
            "quiz_id": quiz_id,
            "job_id": job_id,
            "batch_id": batch_id,
            "model": model,
            "units": units_dict,
            "rates": dict(rates or {}),
            "rate_card_version": int(rate_card_version),
            "cost_usd": round(float(cost_usd), 6),
            "fx_rate": round(float(fx_rate), 4),
            "cost_inr": round(float(cost_inr), 4),
            "tax_inr": round(float(tax_inr), 4),
            "source": source,
            "period": p,
            "dedupe_key": dedupe_key.strip() if dedupe_key else None,
            "note": note,
        }

        # If a session was passed, use it; otherwise open an isolated session
        if db_session is not None:
            return _execute_insert(db_session, event_data)

        from backend.app.database import SessionLocal
        sess = SessionLocal()
        try:
            res = _execute_insert(sess, event_data)
            sess.commit()
            return res
        except Exception as e:
            sess.rollback()
            logger.warning("Failed to commit CostEvent (%s): %s", operation, e)
            return None
        finally:
            sess.close()

    except Exception as e:
        logger.warning("record_event failed for %s/%s: %s", category, operation, e)
        return None


def _execute_insert(session: Session, data: Dict[str, Any]) -> Optional[CostEvent]:
    """Execute dialect-aware ON CONFLICT DO NOTHING insert."""
    dedupe_key = data.get("dedupe_key")
    dialect_name = session.get_bind().dialect.name if session.get_bind() else "sqlite"

    if dialect_name == "postgresql" and dedupe_key:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        stmt = (
            pg_insert(CostEvent)
            .values(**data)
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
        )
        res = session.execute(stmt)
        if res.rowcount == 0:
            return None
        return CostEvent(**data)

    elif dialect_name == "sqlite" and dedupe_key:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        try:
            stmt = (
                sqlite_insert(CostEvent)
                .values(**data)
                .on_conflict_do_nothing(index_elements=["dedupe_key"])
            )
            res = session.execute(stmt)
            if res.rowcount == 0:
                return None
            return CostEvent(**data)
        except IntegrityError:
            session.rollback()
            return None

    # Fallback standard add
    try:
        obj = CostEvent(**data)
        session.add(obj)
        session.flush()
        return obj
    except IntegrityError:
        session.rollback()
        return None


class CostScope:
    """Attribution scope using contextvars.

    Accumulates units per (category, operation, model) in memory to avoid writing
    individual database rows on high-frequency calls (e.g. 76 vision calls in one deck).
    Flushes all accumulated usage on scope exit (even if an exception or cancellation occurs).
    Dedupe keys include run_id so re-ingestions of the same deck create new ledger rows
    while retries within the same run are protected against duplicates.
    """

    def __init__(
        self,
        document_id: Optional[str] = None,
        quiz_id: Optional[str] = None,
        job_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ):
        self.document_id = document_id
        self.quiz_id = quiz_id
        self.job_id = job_id
        self.batch_id = batch_id
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self._lock = threading.Lock()
        # Key: (category, operation, model, is_batch) -> Dict of units
        self._accumulated: Dict[Tuple[str, str, Optional[str], bool], Dict[str, Any]] = defaultdict(
            lambda: {
                "tokens_in": 0,
                "tokens_out": 0,
                "thinking_tokens": 0,
                "cached_tokens": 0,
                "calls": 0,
                "objects": 0,
                "bytes": 0,
            }
        )
        self._token: Optional[contextvars.Token] = None
        self._flushed = False
        self._depth = 0

    def add_gemini_usage(
        self,
        operation: str,
        model: Optional[str],
        tokens_in: int = 0,
        tokens_out: int = 0,
        thinking_tokens: int = 0,
        cached_tokens: int = 0,
        calls: int = 1,
        is_batch: bool = False,
    ) -> None:
        """Thread-safe accumulation of Gemini token usage."""
        key = ("gemini", operation, model, is_batch)
        with self._lock:
            acc = self._accumulated[key]
            acc["tokens_in"] += tokens_in
            acc["tokens_out"] += tokens_out
            acc["thinking_tokens"] += thinking_tokens
            acc["cached_tokens"] += cached_tokens
            acc["calls"] += calls

    def add_r2_usage(
        self,
        operation: str = "r2_put",
        objects: int = 1,
        bytes_uploaded: int = 0,
    ) -> None:
        """Thread-safe accumulation of R2 upload stats."""
        key = ("r2", operation, None, False)
        with self._lock:
            acc = self._accumulated[key]
            acc["objects"] += objects
            acc["bytes"] += bytes_uploaded
            acc["calls"] += 1

    def add_custom_usage(
        self,
        category: str,
        operation: str,
        units: Dict[str, Any],
        model: Optional[str] = None,
    ) -> None:
        """Generic unit accumulator."""
        key = (category, operation, model, False)
        with self._lock:
            acc = self._accumulated[key]
            for k, v in units.items():
                if isinstance(v, (int, float)):
                    acc[k] = acc.get(k, 0) + v
                else:
                    acc[k] = v
            acc["calls"] += 1

    def flush(self) -> List[CostEvent]:
        """Flush all accumulated usage into cost_events ledger rows."""
        with self._lock:
            if self._flushed:
                return []
            self._flushed = True
            items = list(self._accumulated.items())

        events: List[CostEvent] = []
        for (category, operation, model, is_batch), units in items:
            if not units.get("calls", 0) and not units.get("bytes", 0) and not units.get("objects", 0):
                continue

            # Generate deterministic dedupe key for scoped flush per run (including run_id)
            dedupe_key = None
            if self.document_id and category == "gemini":
                dedupe_key = f"scope:{self.document_id}:{self.run_id}:{operation}:{model or 'default'}"
            elif self.document_id and category == "r2":
                dedupe_key = f"scope:{self.document_id}:{self.run_id}:{operation}"

            ev = record_event(
                category=category,
                operation=operation,
                model=model,
                units=units,
                document_id=self.document_id,
                quiz_id=self.quiz_id,
                job_id=self.job_id,
                batch_id=self.batch_id,
                source="measured",
                dedupe_key=dedupe_key,
                is_batch=is_batch,
            )
            if ev:
                events.append(ev)

        return events

    def __enter__(self) -> CostScope:
        self._depth += 1
        if self._token is None:
            self._token = _CURRENT_COST_SCOPE.set(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._depth -= 1
        if self._depth <= 0:
            try:
                self.flush()
            finally:
                if self._token is not None:
                    _CURRENT_COST_SCOPE.reset(self._token)
                    self._token = None


def cost_scope(
    document_id: Optional[str] = None,
    quiz_id: Optional[str] = None,
    job_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> CostScope:
    """Context manager for cost attribution. Re-entrant if matching scope active."""
    current = get_current_cost_scope()
    if current is not None and (document_id is None or current.document_id == document_id):
        if job_id and not current.job_id:
            current.job_id = job_id
        if batch_id and not current.batch_id:
            current.batch_id = batch_id
        return current

    return CostScope(
        document_id=document_id,
        quiz_id=quiz_id,
        job_id=job_id,
        batch_id=batch_id,
        run_id=run_id,
    )


# Unattributed buffer for calls happening outside any active cost_scope
_UNATTRIBUTED_LOCK = threading.Lock()
_UNATTRIBUTED_ACCUMULATED: Dict[Tuple[str, str, Optional[str]], Dict[str, Any]] = defaultdict(
    lambda: {
        "tokens_in": 0,
        "tokens_out": 0,
        "thinking_tokens": 0,
        "cached_tokens": 0,
        "calls": 0,
    }
)
UNATTRIBUTED_FLUSH_THRESHOLD = 10


def record_unattributed_call(
    category: str,
    operation: str,
    model: Optional[str],
    units: Dict[str, Any],
) -> None:
    """Record a call that happened outside an active cost_scope."""
    should_flush = False
    with _UNATTRIBUTED_LOCK:
        acc = _UNATTRIBUTED_ACCUMULATED[(category, operation, model)]
        for k in ("tokens_in", "tokens_out", "thinking_tokens", "cached_tokens"):
            acc[k] += int(units.get(k, 0) or 0)
        acc["calls"] += int(units.get("calls", 1) or 1)

        total_calls = sum(v["calls"] for v in _UNATTRIBUTED_ACCUMULATED.values())
        if total_calls >= UNATTRIBUTED_FLUSH_THRESHOLD:
            should_flush = True

    if should_flush:
        flush_unattributed()


def flush_unattributed() -> None:
    """Flush pending unattributed usage to the ledger."""
    with _UNATTRIBUTED_LOCK:
        items = list(_UNATTRIBUTED_ACCUMULATED.items())
        _UNATTRIBUTED_ACCUMULATED.clear()

    for (category, operation, model), units in items:
        if not units.get("calls", 0):
            continue
        record_event(
            category=category,
            operation=operation,
            model=model,
            units=units,
            source="measured",
            note="unattributed call aggregate",
        )


# Ensure un-flushed unattributed calls are written at process shutdown
atexit.register(flush_unattributed)
