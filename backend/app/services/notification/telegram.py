import httpx

from app.services.notification.base import SendResult

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramSender:
    def send(self, config: dict, message: str) -> SendResult:
        token = config.get("bot_token")
        chat_id = config.get("chat_id")
        if not token or not chat_id:
            return SendResult(ok=False, error="missing bot_token/chat_id")

        try:
            response = httpx.post(
                _API_URL.format(token=token),
                json={"chat_id": chat_id, "text": message},
                timeout=10.0,
            )
            response.raise_for_status()
            body = response.json()
            if not body.get("ok"):
                return SendResult(ok=False, error=str(body.get("description")))
            return SendResult(ok=True)
        except httpx.HTTPError as exc:
            return SendResult(ok=False, error=str(exc))
