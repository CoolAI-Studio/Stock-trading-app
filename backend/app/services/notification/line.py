import httpx

from app.services.notification.base import SendResult

_PUSH_URL = "https://api.line.me/v2/bot/message/push"


class LineSender:
    """Uses the LINE Messaging API's push endpoint (channel access token +
    target user id). LINE Notify -- the simpler, single-bearer-token
    service the legacy app's config implied -- was discontinued by LINE
    in March 2025, so that integration is no longer possible to build."""

    # Telegram 的上限是 4096 字元，LINE 是 5000。取小的那一個當共用上限：同一則提醒
    # 會走每一個管道，而截斷點不一致只會讓比對變難。
    MAX_MESSAGE_CHARS = 4096

    @classmethod
    def _fit(cls, message: str) -> str:
        """截到對方收得下，而且要讓他看得出來被截過。

        沒有上限的話，一則過長的提醒（策略的錯誤訊息可以是一整串 Python 例外）會拿到
        HTTP 400——而 400 同時是「權杖不對」的意思，所以它會把這個管道永久停用，並叫他
        去檢查一把好好的權杖。分類那一邊已經修好不再誤停，這裡是讓那則提醒真的送到。

        安靜地從句子中間切掉，讀起來像提醒本身有 bug——那會讓他去查錯的東西
        （跟 webpush 的 _fit 同一個判斷）。
        """
        if len(message) <= cls.MAX_MESSAGE_CHARS:
            return message
        return message[: cls.MAX_MESSAGE_CHARS - 1] + "…"

    def send(self, config: dict, message: str) -> SendResult:
        access_token = config.get("access_token")
        to = config.get("to")
        if not access_token or not to:
            return SendResult(ok=False, error="missing access_token/to")

        headers = {"Authorization": f"Bearer {access_token}"}
        payload = {"to": to, "messages": [{"type": "text", "text": self._fit(message)}]}
        try:
            response = httpx.post(_PUSH_URL, json=payload, headers=headers, timeout=10.0)
            response.raise_for_status()
            return SendResult(ok=True)
        except httpx.HTTPError as exc:
            return SendResult(ok=False, error=self._describe(exc))

    @staticmethod
    def _describe(exc: Exception) -> str:
        """錯誤字串要讓 retry._is_permanent 判得出來。

        這裡原本是 `str(exc)`，而 httpx 給的是「Client error '401 Unauthorized' for
        url '…'」——不以「HTTP <code>」開頭，所以**憑證失效永遠不會被判成永久失敗**。
        後果是靜默的、而且方向最壞：管道不停用、不透過別的管道告訴他、畫面上永遠寫著
        「啟用中」，而每一則提醒都送進黑洞。

        retry.py 的註解說「the 'HTTP <code>' prefix every sender now emits」——那句話
        對 telegram 和 webpush 成立，對這裡一直不成立。

        網址不帶進來：它會洩漏，而且對他沒有診斷價值（跟 telegram 那邊同一個判斷）。
        """
        if isinstance(exc, httpx.HTTPStatusError):
            return f"HTTP {exc.response.status_code}"
        return f"{type(exc).__name__}"
