"""In-memory record of recent drafts: how long each stage took and which path produced the result.

Per process (a restart or a second instance starts its own window), so treat it as a live health view, not an audit log.
Nothing here changes what a draft returns.
"""
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List

_MAX = 500
_lock = threading.Lock()
_recent: Deque[Dict[str, Any]] = deque(maxlen=_MAX)


class StageTimer:
    def __init__(self) -> None:
        self._t0 = self._last = time.perf_counter()
        self.stages: Dict[str, float] = {}

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        self.stages[name] = round((now - self._last) * 1000, 1)
        self._last = now

    def total_ms(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000, 1)


def record(entry: Dict[str, Any]) -> None:
    with _lock:
        _recent.append(entry)


def reset() -> None:
    with _lock:
        _recent.clear()


def _pct(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


def summary() -> Dict[str, Any]:
    with _lock:
        rows = list(_recent)
    n = len(rows)
    totals = [r["total_ms"] for r in rows]
    stage_names = sorted({k for r in rows for k in r["stages"]})
    return {
        "drafts_in_window": n,
        "total_ms": {"p50": _pct(totals, 0.5), "p95": _pct(totals, 0.95), "max": max(totals) if totals else 0.0},
        "stage_p50_ms": {k: _pct([r["stages"][k] for r in rows if k in r["stages"]], 0.5) for k in stage_names},
        "stage_p95_ms": {k: _pct([r["stages"][k] for r in rows if k in r["stages"]], 0.95) for k in stage_names},
        "interpreter_regex_rate": round(sum(1 for r in rows if r["interpreter"] == "regex") / n, 3) if n else None,
        "curator_fallback_rate": round(sum(1 for r in rows if r["curator"] == "fallback") / n, 3) if n else None,
        "short_rate": round(sum(1 for r in rows if r["short"]) / n, 3) if n else None,
        "last_bank_size": rows[-1]["bank_size"] if rows else None,
    }
