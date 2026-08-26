"""A data feed that stopped working has to be visible.

Every failure mode here is silent by construction. The providers catch
`Exception` and `continue`, so a blocked IP, a changed API shape and a market
that is simply closed all produce the same empty dict. Nothing logged it, so
there was not even a line to find afterwards. And /healthz derived its
market_data check from "did tick_once return", which it always does -- because
the providers never raise -- so the probe stayed green through an outage and
UptimeRobot never mailed anyone.

The owner's first clue was noticing, days later, that no orders had appeared.
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

from app.enums import DataSource
from app.services.market_data.base import Quote
from app.services.market_data.service import MarketDataService


class _DeadProvider:
    """What a blocked or broken upstream actually looks like from here: not an
    exception, just nothing."""

    def get_quotes(self, symbols):
        return {}

    def get_bars(self, symbol, timeframe, limit):
        return []


class _LiveProvider:
    def get_quotes(self, symbols):
        return {
            s: Quote(
                symbol=s,
                data_source=DataSource.YFINANCE,
                price=Decimal(100),
                quote_time=datetime.now(UTC),
            )
            for s in symbols
        }

    def get_bars(self, symbol, timeframe, limit):
        return []


def test_a_fetch_that_returns_nothing_is_logged(caplog):
    """The one line that would have told the owner where to look."""
    service = MarketDataService(providers={DataSource.YFINANCE: _DeadProvider()})

    with caplog.at_level(logging.WARNING):
        service.get_quotes(["AAPL", "2330.TW"], DataSource.YFINANCE)

    assert any("AAPL" in record.message or "2330.TW" in record.message for record in caplog.records)


def test_a_partial_fetch_names_the_symbols_that_did_not_come_back(caplog):
    """Providers omit what they cannot resolve rather than saying so, which is
    how a mistyped ticker becomes a strategy that warms up forever."""

    class _Partial:
        def get_quotes(self, symbols):
            return {
                "AAPL": Quote(
                    symbol="AAPL",
                    data_source=DataSource.YFINANCE,
                    price=Decimal(100),
                    quote_time=datetime.now(UTC),
                )
            }

        def get_bars(self, symbol, timeframe, limit):
            return []

    service = MarketDataService(providers={DataSource.YFINANCE: _Partial()})

    with caplog.at_level(logging.WARNING):
        service.get_quotes(["AAPL", "NOSUCH.TW"], DataSource.YFINANCE)

    assert any("NOSUCH.TW" in record.message for record in caplog.records)


def test_a_working_fetch_says_nothing(caplog):
    """A log line on every successful poll is a log nobody reads."""
    service = MarketDataService(providers={DataSource.YFINANCE: _LiveProvider()})

    with caplog.at_level(logging.WARNING):
        service.get_quotes(["AAPL"], DataSource.YFINANCE)

    assert caplog.records == []


def test_the_health_check_fails_once_the_feed_has_been_empty_long_enough():
    """The probe used to go green on "the loop ran", which it does whether or
    not a single price came back. UptimeRobot therefore stayed quiet through
    an outage that had stopped every alert in the system."""
    from app.services import worker_health

    beat = worker_health.WorkerHeartbeat()
    beat.mark_loop()
    beat.mark_poll_success()

    # The loop keeps turning; every fetch comes back empty.
    for _ in range(3):
        beat.mark_poll_success()
        beat.mark_quotes_empty()
    assert beat.snapshot().consecutive_empty_polls == 3
    assert beat.snapshot().last_poll_age_sec is not None, (
        "the loop really is running -- which is why the age alone said healthy"
    )

    beat.mark_quotes_fetched()
    assert beat.snapshot().consecutive_empty_polls == 0, "one good fetch clears the run"


def test_a_provider_exception_is_logged_rather_than_swallowed(caplog):
    """`except Exception: continue` is right -- one bad symbol must not stop
    the poll -- but it was also the only record that anything went wrong."""
    from app.services.market_data.providers.yfinance_provider import YFinanceProvider

    with (
        patch(
            "app.services.market_data.providers.yfinance_provider.yf.Ticker",
            side_effect=RuntimeError("429 Too Many Requests"),
        ),
        caplog.at_level(logging.WARNING),
    ):
        result = YFinanceProvider().get_quotes(["AAPL"])

    assert result == {}
    assert any("AAPL" in record.message for record in caplog.records)
    assert any("429" in str(record.exc_info or record.message) for record in caplog.records)


def test_running_a_migration_does_not_silence_the_application_logs():
    """alembic's env.py calls logging.config.fileConfig, which defaults to
    disable_existing_loggers=True -- it walks every logger created so far and
    sets .disabled = True. Every app logger built at import time is therefore
    dead for the rest of the process.

    Caught because the whole test suite runs a migration and the logging
    assertions above then failed, while passing alone. The same shape reaches
    production the moment anything runs alembic in-process, and it would take
    out precisely the logs added to make a dead data feed visible -- a silent
    failure whose detection is itself silenced.
    """
    from logging.config import fileConfig

    logger = logging.getLogger("app.market_data")
    assert not logger.disabled

    fileConfig("alembic.ini", disable_existing_loggers=False)
    assert not logger.disabled, "the app's loggers have to survive a migration"

    # Asserted through a handler of our own rather than caplog: fileConfig
    # legitimately replaces the root handlers, pytest's capture handler
    # included, so caplog going quiet here says nothing about the app.
    captured: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    handler = _Collect(level=logging.WARNING)
    logger.addHandler(handler)
    try:
        service = MarketDataService(providers={DataSource.YFINANCE: _DeadProvider()})
        service.get_quotes(["AAPL"], DataSource.YFINANCE)
    finally:
        logger.removeHandler(handler)

    assert any("AAPL" in message for message in captured)
