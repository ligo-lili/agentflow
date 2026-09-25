"""Bounded background run queue (Phase 6.2).

Runs execute on a small worker pool instead of inside the HTTP request:
``POST /api/runs`` accepts with 202 and clients poll the session endpoints.
The queue is bounded — when every worker is busy, a new run is rejected
immediately (429 ``RUN_QUEUE_FULL``) instead of silently piling up. Each run
owns its SQLite connections (opened before submit, closed by the worker), so
workers never share connections.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

ENV_RUN_WORKERS = "AGENTFLOW_RUN_WORKERS"
DEFAULT_RUN_WORKERS = 2


class RunQueue:
    """Worker pool with a strict in-flight capacity of ``max_workers``."""

    def __init__(self, max_workers: int = DEFAULT_RUN_WORKERS) -> None:
        self._capacity = max(1, int(max_workers))
        self._executor = ThreadPoolExecutor(
            max_workers=self._capacity, thread_name_prefix="agentflow-run"
        )
        self._lock = threading.Lock()
        self._in_flight = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    def submit(self, work: Callable[[], None]) -> bool:
        """Execute ``work`` on a worker; ``False`` when at capacity."""
        with self._lock:
            if self._in_flight >= self._capacity:
                return False
            self._in_flight += 1
        try:
            self._executor.submit(self._guarded, work)
        except RuntimeError:  # executor already shut down
            with self._lock:
                self._in_flight -= 1
            return False
        return True

    def _guarded(self, work: Callable[[], None]) -> None:
        try:
            work()
        finally:
            with self._lock:
                self._in_flight -= 1

    def close(self) -> None:
        """Wait for in-flight runs; reject nothing afterwards."""
        self._executor.shutdown(wait=True)
