import json

from pywebpush import WebPushException, webpush

from app.config import settings
from app.services.notification.base import SendResult


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
        payload = json.dumps({"title": "Trading App", "body": message})
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.VAPID_SUBJECT},
                ttl=self.TTL_SECONDS,
            )
            return SendResult(ok=True)
        except WebPushException as exc:
            return SendResult(ok=False, error=_describe(exc))


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
