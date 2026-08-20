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
    # Per symbol, how long it has gone without a price -- seconds since its
    # last good one, or since it was first asked for if it has never had one.
    # Only symbols currently WITHOUT a price appear; a healthy one is absent
    # rather than present with a zero.
    #
    # The count above cannot see this. It clears on any one good price, so
    # nine working symbols hide the tenth that never resolves -- and every
    # threshold on that tenth has silently stopped evaluating.
    symbol_gap_sec: dict[str, float]


class WorkerHeartbeat:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started_at = clock()
        self._last_loop_at: float | None = None
        self._last_poll_at: float | None = None
        self._consecutive_empty_polls = 0
        # symbol -> monotonic time it has been priceless since.
        self._priceless_since: dict[str, float] = {}

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

    def mark_symbols(self, asked: set[str], answered: set[str]) -> None:
        """Which symbols this poll asked for, and which came back with a price.

        Called on every tick, including ticks that asked for nothing: symbols
        the owner has stopped watching are forgotten here, and that is what
        makes 「delete the bad row」 an actual fix rather than a permanently
        angry probe.
        """
        now = self._clock()
        for symbol in asked - answered:
            # setdefault, so an ongoing gap keeps the time it started rather
            # than resetting on every poll -- which would keep it forever
            # under any threshold.
            self._priceless_since.setdefault(symbol, now)
        for symbol in list(self._priceless_since):
            if symbol in answered or symbol not in asked:
                del self._priceless_since[symbol]

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
            symbol_gap_sec={s: now - since for s, since in self._priceless_since.items()},
        )


# Built at import, i.e. at process start -- which is exactly what the /healthz
# startup grace measures against, since a Render spin-down restarts the process.
heartbeat = WorkerHeartbeat()
