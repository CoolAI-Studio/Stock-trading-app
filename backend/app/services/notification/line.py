import httpx

from app.services.notification.base import SendResult

_PUSH_URL = "https://api.line.me/v2/bot/message/push"


class LineSender:
    """Uses the LINE Messaging API's push endpoint (channel access token +
    target user id). LINE Notify -- the simpler, single-bearer-token
    service the legacy app's config implied -- was discontinued by LINE
    in March 2025, so that integration is no longer possible to build."""

    def send(self, config: dict, message: str) -> SendResult:
        access_token = config.get("access_token")
        to = config.get("to")
        if not access_token or not to:
            return SendResult(ok=False, error="missing access_token/to")

        headers = {"Authorization": f"Bearer {access_token}"}
        payload = {"to": to, "messages": [{"type": "text", "text": message}]}
        try:
            response = httpx.post(_PUSH_URL, json=payload, headers=headers, timeout=10.0)
            response.raise_for_status()
            return SendResult(ok=True)
        except httpx.HTTPError as exc:
            return SendResult(ok=False, error=str(exc))
