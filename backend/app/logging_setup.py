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
import re
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

# A personal TradingView URL is a credential (#115): whoever holds it can send
# signals as that account. uvicorn's access log prints every request path, so
# without this each alert would write the credential into the hosting
# platform's log. The account and version stay readable -- a log that cannot
# say which account was being called is barely a log -- and only the MAC goes.
_PERSONAL_WEBHOOK_MAC = re.compile(r"(/api/webhooks/tradingview/\d+\.\d+\.)[A-Za-z0-9_-]+")


class RedactWebhookUrls(logging.Filter):
    """Masks the MAC of a personal TradingView URL wherever a record carries one.

    uvicorn's access records keep the path in `args` rather than in the
    message, so the arguments are rewritten as well as the format string.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _PERSONAL_WEBHOOK_MAC.sub(r"\1***", record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                _PERSONAL_WEBHOOK_MAC.sub(r"\1***", arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True


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

    # On uvicorn's access logger itself, not on the handler above: uvicorn gives
    # that logger a handler of its own and stops it propagating, so a filter on
    # ours would never see a single access line. Checked by type so a second
    # call does not stack another copy.
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(installed, RedactWebhookUrls) for installed in access.filters):
        access.addFilter(RedactWebhookUrls())

    if fell_back:
        root.warning("unknown LOG_LEVEL %r; using INFO", level)
