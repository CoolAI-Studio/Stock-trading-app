"""Getting a push all the way to an iPhone, and failing usefully when it cannot.

Three separate things were wrong on this path, and each of them ends with the
owner not receiving an alert:

1. THE SEND HAD NO TIMEOUT. pywebpush calls requests with whatever it is given,
   and requests with no timeout waits forever. This runs on the market-loop
   thread -- the same thread that polls prices and checks every stop-loss -- so
   one hung TCP connection to web.push.apple.com does not delay a notification,
   it stops the entire alerting system, indefinitely, with a healthy-looking
   process. That is the worst failure this product has.

2. APPLE'S SERVER-SIDE REJECTIONS WERE READ AS DEAD DEVICES. 404 and 410 mean
   the subscription is gone and will never work again. 400, 401 and 403 mean
   the VAPID credentials this SERVER is sending are wrong -- a mismatched key
   pair, a malformed JWT, a bad subject. Both were treated identically: the
   channel was switched off and the owner told to delete it and set it up again
   on their phone, which cannot possibly help and destroys a working
   subscription while they try.

3. NOTHING CAPPED THE PAYLOAD. Apple rejects a push whose encrypted payload
   exceeds 4 KB. A strategy exception pasted into an alert body clears that
   easily, and 413 was in neither the retry list nor the permanent list, so the
   alert was retried five times and then dropped without a word.
"""

import json
from unittest.mock import patch

import pytest
from pywebpush import WebPushException

from app.services.notification.webpush import MAX_BODY_CHARS, WebPushSender

SUBSCRIPTION = {
    "endpoint": "https://web.push.apple.com/abc",
    "p256dh": "p256dh-value",
    "auth": "auth-value",
}


@pytest.fixture(autouse=True)
def _vapid(monkeypatch):
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "private-key")
    monkeypatch.setattr("app.config.settings.VAPID_SUBJECT", "mailto:owner@example.com")


def _send(message: str = "hello"):
    """Sends, and hands back the kwargs pywebpush was actually called with."""
    with patch("app.services.notification.webpush.webpush", return_value=None) as mock:
        result = WebPushSender().send(SUBSCRIPTION, message)
    return result, (mock.call_args.kwargs if mock.call_args else {})


# --- the timeout ------------------------------------------------------------


def test_a_push_cannot_hang_the_worker_forever():
    """The single most damaging bug on this path: no timeout means requests
    waits indefinitely, on the thread that also runs every stop-loss check."""
    _result, kwargs = _send()

    assert kwargs.get("timeout") is not None, "a send with no timeout can wedge the market loop"


def test_the_timeout_is_short_enough_to_matter():
    """A timeout longer than the poll interval still stalls the loop; this is a
    single small HTTPS POST to a CDN, not a slow query."""
    _result, kwargs = _send()

    assert 0 < kwargs["timeout"] <= 15


def test_a_timeout_is_reported_as_retryable_rather_than_as_a_dead_device():
    """A slow network is the most ordinary failure there is. Reading it as a
    dead subscription would switch off a perfectly good channel."""
    import requests

    with patch(
        "app.services.notification.webpush.webpush",
        side_effect=requests.exceptions.ReadTimeout("too slow"),
    ):
        result = WebPushSender().send(SUBSCRIPTION, "hello")

    assert not result.ok
    assert "HTTP" not in (result.error or ""), "no status code was received; do not invent one"
    from app.services.notification.retry import _is_permanent

    assert not _is_permanent(result.error)


def test_an_unexpected_exception_does_not_escape_into_the_market_loop():
    """dispatcher.py calls this from inside the poll. Anything that escapes
    takes the loop down, and a dead loop is a dead product."""
    with patch("app.services.notification.webpush.webpush", side_effect=RuntimeError("boom")):
        result = WebPushSender().send(SUBSCRIPTION, "hello")

    assert not result.ok
    assert result.error


# --- how big the message may be ---------------------------------------------


def test_a_long_body_is_trimmed_rather_than_rejected_by_apple():
    """Apple refuses a payload over 4 KB. An alert carrying a strategy
    traceback clears that, and 413 was neither retried usefully nor explained
    -- the owner simply never heard about the thing that went wrong."""
    _result, kwargs = _send("x" * 10_000)

    payload = json.loads(kwargs["data"])
    assert len(payload["body"]) <= MAX_BODY_CHARS
    assert len(kwargs["data"].encode("utf-8")) < 3500, "must leave room for encryption overhead"


def test_a_trimmed_body_says_it_was_trimmed():
    """Silently truncating an alert mid-sentence reads as a bug in the alert."""
    _result, kwargs = _send("x" * 10_000)

    assert "…" in json.loads(kwargs["data"])["body"]


def test_a_normal_message_is_left_exactly_as_it_is():
    _result, kwargs = _send("2330.TW 跌破 950")

    assert json.loads(kwargs["data"])["body"] == "2330.TW 跌破 950"


def test_the_title_is_not_a_placeholder_in_english():
    """Every notification arrived titled "Trading App". On a lock screen the
    title is most of what gets read, and it said nothing about what happened."""
    _result, kwargs = _send("2330.TW 跌破 950")

    assert json.loads(kwargs["data"])["title"] != "Trading App"
    assert any("一" <= ch <= "鿿" for ch in json.loads(kwargs["data"])["title"])


# --- telling a dead device from a broken server -----------------------------


@pytest.mark.parametrize("code", [404, 410])
def test_a_gone_subscription_is_reported_as_the_device_being_gone(code):
    result = _fail_with(code)

    from app.services.notification.retry import _is_permanent

    assert _is_permanent(result.error)


@pytest.mark.parametrize("code", [400, 401, 403])
def test_a_vapid_fault_is_not_blamed_on_the_device(code):
    """Apple answers 403 VapidPkHashMismatch when the server's key pair does not
    match the one the subscription was created with, and 400 for a malformed
    JWT. Neither is fixable on the phone -- and the advice given was to delete
    the subscription and make a new one, which throws away the only working
    thing in the picture."""
    from app.models.enums import ChannelType
    from app.services.notification.retry import _permanent_explanation

    result = _fail_with(code)
    explanation = _permanent_explanation(ChannelType.WEB_PUSH, result.error or "")

    assert "重新建立" not in explanation, explanation
    assert "VAPID" in explanation or "伺服器" in explanation, explanation


def test_a_server_side_fault_still_stops_retrying():
    """Retrying a mismatched key pair five times a minute changes nothing; it
    just delays the owner finding out."""
    from app.services.notification.retry import _is_permanent

    assert _is_permanent(_fail_with(403).error)


@pytest.mark.parametrize("code", [429, 500, 502, 503])
def test_a_temporary_refusal_is_left_to_the_retry_sweep(code):
    from app.services.notification.retry import _is_permanent

    assert not _is_permanent(_fail_with(code).error)


def test_413_is_recognised_even_though_the_body_is_now_capped():
    """The cap is a belt; this is the braces. An oversized payload that still
    gets through must not be retried five times in silence."""
    from app.services.notification.retry import _is_permanent

    assert _is_permanent(_fail_with(413).error)


def _fail_with(code: int):
    class _Response:
        status_code = code
        text = ""

    exc = WebPushException(f"Push failed: {code}")
    exc.response = _Response()
    with patch("app.services.notification.webpush.webpush", side_effect=exc):
        return WebPushSender().send(SUBSCRIPTION, "hello")
