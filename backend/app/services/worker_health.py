"""Liveness bookkeeping for the background market-data worker, read by /healthz.

Kept in process memory rather than a table: the worker would otherwise write a
row several times a minute, forever, to record something only the health probe
reads -- and the app runs with --workers 1 (see run_forever), so the process
answering /healthz is the same process running the loop.

Timestamps are monotonic, not wall-clock: an NTP correction on the host must not
be able to make a healthy worker look hours stale, or a dead one look fresh.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class HeartbeatSnapshot:
    uptime_sec: float
    last_loop_age_sec: float | None
    last_poll_age_sec: float | None


class WorkerHeartbeat:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started_at = clock()
        self._last_loop_at: float | None = None
        self._last_poll_at: float | None = None

    def mark_loop(self) -> None:
        """The loop reached the top of an iteration, so it is not wedged."""
        self._last_loop_at = self._clock()

    def mark_poll_success(self) -> None:
        """A whole poll cycle finished without raising."""
        self._last_poll_at = self._clock()

    def snapshot(self) -> HeartbeatSnapshot:
        # The marks are written from the event loop thread and read from the
        # threadpool thread serving /healthz. Single float attribute reads and
        # writes are atomic under the GIL, so no lock is needed; a snapshot may
        # simply be a few milliseconds behind, which no threshold here cares
        # about.
        now = self._clock()
        return HeartbeatSnapshot(
            uptime_sec=now - self._started_at,
            last_loop_age_sec=None if self._last_loop_at is None else now - self._last_loop_at,
            last_poll_age_sec=None if self._last_poll_at is None else now - self._last_poll_at,
        )


# Built at import, i.e. at process start -- which is exactly what the /healthz
# startup grace measures against, since a Render spin-down restarts the process.
heartbeat = WorkerHeartbeat()
