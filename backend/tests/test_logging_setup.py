"""Logs that exist when something goes wrong.

Nothing configured logging at all. Python's default sends WARNING and above to
stderr with no timestamp and no level name, and drops INFO entirely -- so
every `logger.info` the worker writes went nowhere, and the warnings that did
survive could not be placed in time. When a strategy should have signalled and
did not, the line that would have said what the loop actually saw had never
been written.

The pieces that matter here are unglamorous and each one is a thing that was
missing: a timestamp, a level, the logger's name, and INFO actually reaching
the stream.
"""

import logging

import pytest

from app.logging_setup import _MARKER, configure_logging


@pytest.fixture(autouse=True)
def _detach_handler_between_tests():
    """Remove the handler this module installs once each test is done.

    It is deliberately long-lived in production -- the process owns one stderr
    for its whole life -- but capsys hands each test a *different* stderr and
    closes it afterwards, so a handler surviving the test is left holding a
    closed file and blows up when pytest flushes it during teardown. That is a
    property of the harness, not of the code under test.
    """
    yield
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, "_name_tag", None) == _MARKER]:
        root.removeHandler(handler)


def test_info_actually_gets_through(capsys):
    """The default drops it, which is why the worker's own account of what it
    did was unavailable exactly when it was wanted."""
    configure_logging(level="INFO")
    logging.getLogger("app.market_loop").info("polled 3 symbols")

    assert "polled 3 symbols" in capsys.readouterr().err


def test_a_line_carries_a_timestamp_a_level_and_who_wrote_it(capsys):
    configure_logging(level="INFO")
    logging.getLogger("app.market_data").warning("yfinance returned nothing")

    line = capsys.readouterr().err
    assert "WARNING" in line
    assert "app.market_data" in line
    # ISO-ish date, so lines can be lined up against when the owner noticed.
    assert "-" in line and ":" in line


def test_the_level_can_be_turned_down_without_a_code_change(capsys):
    configure_logging(level="WARNING")
    logging.getLogger("app.market_loop").info("chatty")
    logging.getLogger("app.market_loop").warning("worth knowing")

    err = capsys.readouterr().err
    assert "chatty" not in err
    assert "worth knowing" in err


def test_configuring_twice_does_not_duplicate_every_line(capsys):
    """The lifespan runs once per process, but a reload or an import cycle can
    call this again, and doubled lines make a log much harder to read."""
    configure_logging(level="INFO")
    configure_logging(level="INFO")
    logging.getLogger("app.test").info("once please")

    assert capsys.readouterr().err.count("once please") == 1


def test_an_exception_keeps_its_traceback(capsys):
    """logger.exception is how every swallowed failure in this app reports
    itself; without the traceback it says only that something happened."""
    configure_logging(level="INFO")
    try:
        raise ValueError("the actual cause")
    except ValueError:
        logging.getLogger("app.test").exception("something failed")

    err = capsys.readouterr().err
    assert "the actual cause" in err
    assert "Traceback" in err


def test_the_noisy_libraries_are_turned_down(capsys):
    """At INFO, urllib3 and friends narrate every request. Their volume is
    what makes people stop reading logs, and the app's own lines are what
    matter here."""
    configure_logging(level="INFO")
    logging.getLogger("urllib3.connectionpool").info("Starting new HTTPS connection")

    assert "Starting new HTTPS connection" not in capsys.readouterr().err


def test_an_unknown_level_falls_back_rather_than_crashing_the_process(capsys):
    """A typo in an environment variable must not stop the app booting -- the
    cost of a wrong level is some noise, and the cost of not booting is
    everything."""
    configure_logging(level="NOTALEVEL")
    logging.getLogger("app.test").warning("still here")

    assert "still here" in capsys.readouterr().err
