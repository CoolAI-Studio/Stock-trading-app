"""Two things a push service reads before it decides how hard to try.

## Urgency (RFC 8030 §5.3)

Every push this app sends went out with no Urgency header, which means the
default: `normal`. A push service is explicitly permitted to hold a `normal`
message while the device is in a power-saving state -- Android Doze, iOS's
background scheduling -- and deliver it whenever the device next wakes up on
its own.

That default is written for a chat app's read receipts. For this product the
entire value of a message is that it arrives WHEN THE THING HAPPENED: 「2330 跌
破 900」 delivered forty minutes later, after the phone came out of Doze, is not
a late alert, it is a wrong one. The owner reads it as current.

`high` is the level that asks for immediate delivery to a sleeping device. It
is the honest description of what every alert this app sends actually is, and
it is safe to use because the service worker always shows a notification --
what push services throttle is a high-urgency push that displays nothing.

## The VAPID claims dict

pywebpush MUTATES the claims dict it is handed: it writes `aud` (derived from
this endpoint's origin) and `exp` into it, in place. A dict built fresh on
every send is therefore correct, and a module-level constant would be a bug
that only shows up on the SECOND push service the owner subscribes from -- the
first endpoint's `aud` would be signed into every push to every other one, and
every one of them would answer 401.

Pinned here because the fix for it is invisible: 「build the dict inline」 looks
like a style choice and reads as safe to hoist.
"""

from unittest.mock import patch

import pytest

from app.services.notification.webpush import WebPushSender

APPLE = {
    "endpoint": "https://web.push.apple.com/abc",
    "p256dh": "p256dh-value",
    "auth": "auth-value",
}
GOOGLE = {
    "endpoint": "https://fcm.googleapis.com/fcm/send/xyz",
    "p256dh": "p256dh-value",
    "auth": "auth-value",
}


@pytest.fixture(autouse=True)
def _vapid(monkeypatch):
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "private-key")
    monkeypatch.setattr("app.config.settings.VAPID_SUBJECT", "mailto:owner@example.com")


def _calls(*subscriptions):
    """Sends to each subscription in turn; hands back the kwargs pywebpush saw.

    The claims dict is copied at call time because pywebpush mutates it -- the
    real thing does, and reading mock.call_args afterwards would otherwise show
    the mutated state rather than what was passed.
    """
    seen = []
    with patch("app.services.notification.webpush.webpush", return_value=None) as mock:

        def _record(**kwargs):
            seen.append({**kwargs, "vapid_claims": dict(kwargs.get("vapid_claims") or {})})

        mock.side_effect = _record
        for subscription in subscriptions:
            WebPushSender().send(subscription, "2330.TW 跌破 900")
    return seen


# --- urgency ----------------------------------------------------------------


def test_an_alert_asks_to_be_delivered_now_rather_than_at_the_phones_convenience():
    headers = _calls(APPLE)[0].get("headers") or {}

    assert headers.get("Urgency") == "high", headers


def test_the_urgency_survives_alongside_the_ttl():
    """Both are headers the push service reads, and pywebpush builds the TTL
    one itself -- passing our own header dict must not displace it."""
    call = _calls(APPLE)[0]

    assert call["ttl"] == WebPushSender.TTL_SECONDS
    assert (call.get("headers") or {}).get("Urgency") == "high"


# --- the claims dict --------------------------------------------------------


def test_each_push_is_signed_for_its_own_push_service():
    """pywebpush writes `aud` into the claims dict in place. Reusing one dict
    would sign Apple's audience into a push aimed at Google, and Google would
    answer 401 -- for every push, forever, while Apple carried on working."""
    apple_claims, google_claims = (call["vapid_claims"] for call in _calls(APPLE, GOOGLE))

    assert "aud" not in apple_claims and "aud" not in google_claims


def test_each_push_carries_a_freshly_built_claim_set():
    """Same reason as `aud`, for `exp`: a stale expiry signed into a JWT is a
    401 from every push service at once, which is every alert stopping."""
    apple_claims, google_claims = (call["vapid_claims"] for call in _calls(APPLE, GOOGLE))

    assert "exp" not in apple_claims and "exp" not in google_claims
    assert apple_claims == {"sub": "mailto:owner@example.com"}


def test_the_subject_still_comes_from_configuration():
    assert _calls(APPLE)[0]["vapid_claims"]["sub"] == "mailto:owner@example.com"
