"""Make the application's logs exist.

Nothing configured logging, so Python's default applied: WARNING and above to
stderr, no timestamp, no level name, and INFO dropped entirely. Every
`logger.info` the worker wrote went nowhere, and the warnings that survived
could not be placed in time. When a strategy should have signalled and did
not, the line that would have said what the loop actually saw had never been
written at all.

Deliberately plain text rather than JSON: the only place these are read is
Render's log viewer and the owner's own eyes, and neither benefits from
quoting every field.
"""

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Libraries that narrate every HTTP request at INFO. Their volume is what makes
# people stop reading logs, and the app's own lines are the point.
_QUIET = (
    "urllib3",
    "httpx",
    "httpcore",
    "asyncio",
    "peewee",
    "yfinance",
)

_MARKER = "app-logging"


def configure_logging(level: str = "INFO") -> None:
    """Called once from the app's lifespan. Safe to call again."""
    root = logging.getLogger()

    existing = next((h for h in root.handlers if getattr(h, "_name_tag", None) == _MARKER), None)
    if existing is None:
        handler = logging.StreamHandler(sys.stderr)
        handler._name_tag = _MARKER  # type: ignore[attr-defined]
        root.addHandler(handler)
    else:
        # Reconfigured rather than added again: a reload or an import cycle
        # calling this twice would otherwise double every line, which makes a
        # log harder to read than no configuration at all.
        handler = existing

    # Re-pointed at the current stderr before anything is logged, not just
    # reused. A handler holds the stream it was built with, so anything that
    # replaces sys.stderr after startup -- a test harness capturing output, a
    # supervisor reopening the pipe on rotation -- leaves it writing to a
    # closed file. Doing this first also matters because the level check below
    # may itself log.
    handler.setStream(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT))

    resolved = getattr(logging, str(level).upper(), None)
    fell_back = not isinstance(resolved, int)
    if fell_back:
        # A typo in an environment variable must not stop the process booting.
        # Some noise is cheap; not starting is not.
        resolved = logging.INFO

    handler.setLevel(resolved)
    root.setLevel(resolved)

    for name in _QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)

    if fell_back:
        root.warning("unknown LOG_LEVEL %r; using INFO", level)
