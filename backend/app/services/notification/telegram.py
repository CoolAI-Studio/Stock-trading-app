import httpx

from app.services.notification.base import SendResult

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

# Telegram authenticates by putting the bot token in the request *path*, so any
# httpx error string that names the URL names the token with it. Whatever a
# sender returns in SendResult.error is written verbatim into
# NotificationLog.error and NotificationChannel.last_error -- plain Text
# columns, sitting right next to the deliberately Fernet-encrypted config --
# and both are handed back by the notifications API. So str(exc) must never
# reach a SendResult from here: it would publish in the clear exactly the
# credential the encrypted column exists to protect.


def _describe_status_error(exc: httpx.HTTPStatusError) -> str:
    """The status code plus whatever Telegram said, and nothing else.

    Telegram's error body is JSON like
    {"ok": false, "error_code": 401, "description": "Unauthorized"} -- it
    carries no credential, and it is the only part of the failure with any
    diagnostic value to the owner. The URL, which is the part that leaks,
    tells them nothing they don't already know.
    """
    try:
        body = exc.response.json()
    except ValueError:
        body = None
    description = body.get("description") if isinstance(body, dict) else None
    if description:
        return f"HTTP {exc.response.status_code}: {description}"
    return f"HTTP {exc.response.status_code}"


def _redact(text: str, token: str) -> str:
    """Scrub the token out of an error we did not compose ourselves.

    Failures that never produced a response (DNS, TLS, timeouts) come with
    messages httpx builds from the request, which may or may not include the
    URL depending on the failure -- there is no list of the ones that do, so
    the token is removed by name instead of the message being trusted.
    """
    if not token:
        return text
    return text.replace(token, "***")


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
                # 2xx with ok:false -- the status code adds nothing here, so
                # report Telegram's description alone.
                return SendResult(ok=False, error=str(body.get("description")))
            return SendResult(ok=True)
        except httpx.HTTPStatusError as exc:
            return SendResult(ok=False, error=_describe_status_error(exc))
        except httpx.HTTPError as exc:
            return SendResult(ok=False, error=_redact(f"{type(exc).__name__}: {exc}", token))
