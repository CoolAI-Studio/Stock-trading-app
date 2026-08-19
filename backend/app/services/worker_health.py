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
    # Polls in a row that asked for prices and got none back. Separate from
    # last_poll_age_sec because the two failures are different: the loop can
    # keep turning perfectly while every fetch comes back empty, and that is
    # precisely the state that used to read as healthy -- the providers catch
    # everything and return {}, so nothing raised and the probe stayed green
    # through an outage that had silenced every alert in the system.
    consecutive_empty_polls: int


class WorkerHeartbeat:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started_at = clock()
        self._last_loop_at: float | None = None
        self._last_poll_at: float | None = None
        self._consecutive_empty_polls = 0

    def mark_loop(self) -> None:
        """The loop reached the top of an iteration, so it is not wedged."""
        self._last_loop_at = self._clock()

    def mark_poll_success(self) -> None:
        """A whole poll cycle finished without raising."""
        self._last_poll_at = self._clock()

    def mark_quotes_fetched(self) -> None:
        """Prices actually came back. One good fetch clears the run."""
        self._consecutive_empty_polls = 0

    def mark_quotes_empty(self) -> None:
        """We asked for prices and got none.

        Only called when symbols were actually requested -- an account with no
        strategies and no positions asks for nothing, and that is not an
        outage.
        """
        self._consecutive_empty_polls += 1

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
            consecutive_empty_polls=self._consecutive_empty_polls,
        )


# Built at import, i.e. at process start -- which is exactly what the /healthz
# startup grace measures against, since a Render spin-down restarts the process.
heartbeat = WorkerHeartbeat()
