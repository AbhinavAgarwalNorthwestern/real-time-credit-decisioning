"""In-process rolling-window metrics for the watchdog.

Collects per-request latency, profit, and error counts into a fixed-size
deque. The watchdog reads current vs baseline windows each poll interval.
No external dependencies (Prometheus integration is Day 7).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from .rollback_watchdog import WindowMetrics

WINDOW_SIZE = 500


@dataclass(slots=True)
class _Sample:
    ts: float
    latency_ms: float
    profit: float
    is_error: bool
    segment_id: int = 0
    alias: str = 'champion'


def _to_window(samples: list[_Sample]) -> WindowMetrics | None:
    if len(samples) < 10:
        return None
    lats = [s.latency_ms for s in samples]
    return WindowMetrics(
        p99_latency_ms=float(np.percentile(lats, 99)),
        mean_profit=float(np.mean([s.profit for s in samples])),
        error_rate=sum(1 for s in samples if s.is_error) / len(samples),
    )


class MetricsCollector:
    """Thread-safe rolling metrics buffer with per-segment and per-alias views."""

    def __init__(self, window_size: int = WINDOW_SIZE) -> None:
        self._buf: deque[_Sample] = deque(maxlen=window_size)
        self._lock = threading.Lock()

    def record(
        self,
        latency_ms: float,
        profit: float,
        is_error: bool = False,
        segment_id: int = 0,
        alias: str = 'champion',
    ) -> None:
        with self._lock:
            self._buf.append(
                _Sample(
                    ts=time.monotonic(),
                    latency_ms=latency_ms,
                    profit=profit,
                    is_error=is_error,
                    segment_id=segment_id,
                    alias=alias,
                )
            )

    def current_window(self, last_n: int = 200) -> WindowMetrics | None:
        with self._lock:
            samples = list(self._buf)
        return _to_window(samples[-last_n:])

    def current_window_by_segment(
        self, segment_id: int, last_n: int = 200
    ) -> WindowMetrics | None:
        with self._lock:
            samples = [s for s in self._buf if s.segment_id == segment_id]
        return _to_window(samples[-last_n:])

    def current_window_by_alias(
        self, alias: str, last_n: int = 200
    ) -> WindowMetrics | None:
        with self._lock:
            samples = [s for s in self._buf if s.alias == alias]
        return _to_window(samples[-last_n:])

    def baseline_window(self, last_n: int = 200) -> WindowMetrics | None:
        with self._lock:
            samples = list(self._buf)
        if len(samples) < last_n + 10:
            return None
        baseline = samples[-(2 * last_n) : -last_n]
        return _to_window(baseline)
