import json

import requests
from pywebpush import WebPushException, webpush

from app.config import settings
from app.services.notification.base import SendResult

# The alert's own title on a lock screen. Every push used to arrive titled
# "Trading App", in English, which is most of what actually gets read on a
# phone and said nothing about what had happened.
TITLE = "交易提醒"

# Apple refuses a push whose encrypted payload exceeds 4 KB, and the encryption
# adds about a hundred bytes of overhead on top of whatever is sent. 600
# characters is comfortably inside that even when every one of them is a
# three-byte Chinese character, and is far more than any alert this app
# composes -- the case it exists for is a strategy traceback landing in the
# body, which used to produce a 413 that was retried five times and then
# dropped without a word.
MAX_BODY_CHARS = 600

# pywebpush hands `timeout` straight to requests, and its default is None --
# which means requests waits forever. Sends run inside the market loop's tick,
# so a single push to an unresponsive endpoint stalled the loop that also polls
# prices and checks every stop-loss. Ten seconds is generous for one small
# HTTPS POST to a CDN, and a push not delivered in ten seconds is not going to
# be useful anyway.
TIMEOUT_SECONDS = 10.0


class WebPushSender:
    """Sends to a single browser subscription (one NotificationChannel row
    per subscribed device/browser -- PushManager.subscribe() on the
    frontend produces the endpoint/p256dh/auth this expects)."""

    # A trading alert stale by more than an hour isn't worth redelivering.
    # pywebpush's own default (ttl=0) reads as "don't try to redeliver at
    # all" to most push services -- but Microsoft's WNS (the endpoint Edge
    # subscribes through) outright rejects it with a 400 ("Ttl value
    # conflicts with X-WNS-Cache-Policy"), so every push needs an explicit
    # positive value regardless of provider.
    TTL_SECONDS = 3600

    def send(self, config: dict, message: str) -> SendResult:
        endpoint = config.get("endpoint")
        p256dh = config.get("p256dh")
        auth = config.get("auth")
        if not endpoint or not p256dh or not auth:
            return SendResult(ok=False, error="missing endpoint/p256dh/auth")

        if not settings.VAPID_PRIVATE_KEY:
            return SendResult(ok=False, error="VAPID_PRIVATE_KEY is not configured")

        subscription_info = {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}
        payload = json.dumps(
            {"title": TITLE, "body": _fit(message)},
            ensure_ascii=False,
        )
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.VAPID_SUBJECT},
                ttl=self.TTL_SECONDS,
                timeout=TIMEOUT_SECONDS,
            )
            return SendResult(ok=True)
        except WebPushException as exc:
            return SendResult(ok=False, error=_describe(exc))
        except requests.exceptions.RequestException as exc:
            # A timeout, a DNS failure, a reset connection: no status code was
            # ever received, so none is invented. retry.py keys "permanent" off
            # the "HTTP <code>" prefix, and putting one here would retire a
            # perfectly good channel because the wifi dropped.
            return SendResult(ok=False, error=f"連線失敗：{type(exc).__name__}")
        except Exception as exc:  # pragma: no cover -- defence, not a path
            # dispatcher.py calls this from inside the market loop's tick.
            # Anything that escapes stops the loop, and a stopped loop is the
            # end of every alert, not just this one.
            return SendResult(ok=False, error=f"推播時發生未預期的錯誤：{type(exc).__name__}")


def _fit(message: str) -> str:
    """Trim to what a push service will accept, and say that it was trimmed.

    Silently cutting an alert mid-sentence reads as a bug in the alert itself,
    which is the wrong thing for the owner to go and investigate.
    """
    if len(message) <= MAX_BODY_CHARS:
        return message
    return message[: MAX_BODY_CHARS - 1] + "…"


def _describe(exc: WebPushException) -> str:
    """The status code first, because that is the part that decides what
    happens next: 404 and 410 mean the browser rotated this subscription and
    it will never work again, everything else is worth retrying. pywebpush's
    own str() is prose with the code buried in it or missing entirely, so
    retry.py could not tell the two apart."""
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if code is None:
        return str(exc)
    detail = (getattr(response, "text", "") or "").strip()
    return f"HTTP {code}: {detail}" if detail else f"HTTP {code}"
