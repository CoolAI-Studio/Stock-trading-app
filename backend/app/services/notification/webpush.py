import json

import requests
from pywebpush import WebPushException, webpush

from app.config import settings, vapid_keys, vapid_subject
from app.services.notification.base import SendResult

# The alert's own title on a lock screen. Every push used to arrive titled
# "Trading App", in English, which is most of what actually gets read on a
# phone and said nothing about what had happened.
TITLE = "交易提醒"

# THE LIMIT IS THE PLATFORM'S, AND IT IS COUNTED IN BYTES. Apple refuses a push
# whose encrypted payload exceeds 4096 bytes (RFC 8030 §7.2 asks every push
# service to accept at least that much), and aes128gcm spends 103 of them: a
# 21-byte header, the 65-byte sender key, a 16-byte tag, one padding delimiter.
# So the JSON handed to pywebpush has to stay under 3993 bytes; this leaves
# about 600 of those spare for whatever a future pywebpush adds.
#
# It used to be 600 CHARACTERS -- sized for a three-byte Chinese character in
# every position -- and the case it was written for was a strategy traceback,
# which produced a 413 that was retried five times and dropped without a word.
# But the close summary (#117) is a list, one line per stock, and 600 characters
# cut it after about eighteen while more than half of the real budget was
# unused. Counting the actual bytes of the actual payload fits about fifty.
MAX_PAYLOAD_BYTES = 3400

# pywebpush hands `timeout` straight to requests, and its default is None --
# which means requests waits forever. Sends run inside the market loop's tick,
# so a single push to an unresponsive endpoint stalled the loop that also polls
# prices and checks every stop-loss. Ten seconds is generous for one small
# HTTPS POST to a CDN, and a push not delivered in ten seconds is not going to
# be useful anyway.
TIMEOUT_SECONDS = 10.0

# RFC 8030 §5.3. With no Urgency header the message is `normal`, and a push
# service is explicitly allowed to hold a `normal` message while the device is
# in a power-saving state -- Android Doze, iOS background scheduling -- and
# deliver it whenever the phone next wakes up on its own.
#
# That default is written for a chat app's read receipts. Here the entire value
# of a message is that it arrives WHEN THE THING HAPPENED: 「2330 跌破 900」
# delivered forty minutes later is not a late alert, it is a wrong one, because
# the owner reads it as current. `high` is the level that asks for immediate
# delivery to a sleeping device, and it is the honest description of every
# alert this app sends.
#
# Safe to use: what push services throttle is a high-urgency push that displays
# nothing, and the service worker always shows a notification.
URGENCY = "high"


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

    def send(self, config: dict, message: str, receipt_token: str | None = None) -> SendResult:
        """`receipt_token` travels inside the encrypted payload so the service
        worker can report back that it displayed the notification.

        RFC 8030 §5 is explicit that a 2xx from the push service "does not
        indicate that the message was delivered to the user agent", so without
        this there is no way to tell a delivered alert from one the phone never
        saw. Optional because the retry sweep re-sends alerts that were never
        about confirming delivery.
        """
        endpoint = config.get("endpoint")
        p256dh = config.get("p256dh")
        auth = config.get("auth")
        if not endpoint or not p256dh or not auth:
            return SendResult(ok=False, error="missing endpoint/p256dh/auth")

        # 走 config.vapid_keys，不要直接讀 settings：兩個都沒設的時候那一對是從
        # SECRET_ENCRYPTION_KEY 推導出來的（見那個函式的說明）。直接讀的話，一份完全
        # 沒填推播金鑰的部署會在這裡默默地不送——而那正是這個產品最不能有的那種失效。
        _, private_key = vapid_keys(settings)
        if not private_key:
            return SendResult(ok=False, error="VAPID_PRIVATE_KEY is not configured")

        subscription_info = {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}
        body: dict[str, str] = {"title": TITLE, "body": message}
        if receipt_token:
            body["receipt"] = receipt_token
        payload = _within_budget(body)
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=private_key,
                # Built inline on EVERY send, and it has to stay that way.
                # pywebpush mutates this dict in place, writing `aud` (derived
                # from this endpoint's origin) and `exp` into it. Hoisting it
                # to a module constant would look like a harmless tidy-up and
                # would sign the first push service's audience into every push
                # to every other one -- 401 from all of them, forever, while
                # the first carried on working. Pinned by
                # tests/test_push_urgency_and_vapid_freshness.py.
                vapid_claims={"sub": vapid_subject(settings)},
                ttl=self.TTL_SECONDS,
                timeout=TIMEOUT_SECONDS,
                # pywebpush copies this dict before adding the VAPID and TTL
                # headers of its own, so passing one does not displace those.
                headers={"Urgency": URGENCY},
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


def _encoded(body: dict[str, str]) -> str:
    return json.dumps(body, ensure_ascii=False)


def _fits(body: dict[str, str]) -> bool:
    return len(_encoded(body).encode("utf-8")) <= MAX_PAYLOAD_BYTES


def _within_budget(body: dict[str, str]) -> str:
    """The payload, trimmed to what a push service will accept -- and saying so.

    Measured on the whole serialized payload, receipt token and JSON escaping
    included, because that is what Apple counts. Silently cutting an alert
    mid-sentence reads as a bug in the alert itself, which is the wrong thing
    for the owner to go and investigate.

    WHOLE LINES FIRST. The long message this app actually sends is the close
    summary, one line per stock: half a stock's line is worse than no line, and
    a list that is quietly shorter than the watchlist reads as the whole
    watchlist. So it keeps as many whole lines as fit and says how many are
    left. Only a single line too long on its own (a strategy's exception text)
    is cut by characters.
    """
    if _fits(body):
        return _encoded(body)

    lines = body["body"].split("\n")
    for keep in range(len(lines) - 1, 0, -1):
        trimmed = {
            **body,
            "body": "\n".join(lines[:keep]) + f"\n…還有 {len(lines) - keep} 行沒放進這則通知",
        }
        if _fits(trimmed):
            return _encoded(trimmed)

    # Not even the first line fits. Largest prefix that does, found by halving:
    # a few serializations rather than one per character of a traceback.
    text = body["body"]
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _fits({**body, "body": text[:middle] + "…"}):
            low = middle
        else:
            high = middle - 1
    return _encoded({**body, "body": text[:low] + "…"})


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
