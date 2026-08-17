import httpx

from app.config import settings
from app.services.ai_provider.base import SYSTEM_PROMPT, AIResult

# Statuses where moving on to the *next* configured model is worth a try: the
# request itself was well-formed, this particular model just couldn't serve it.
# Anything else (a bad key above all) would fail identically on every model, so
# walking the list would only burn the daily quota for nothing.
#
# 404 belongs here deliberately: free model line-ups get retired and renamed
# without notice, so a stale name in the middle of the list should be stepped
# over rather than taking the whole assistant down with it.
_TRY_NEXT_MODEL_STATUSES = frozenset({402, 404, 408, 429, 500, 502, 503, 504})


def _describe(status: int, model: str) -> str:
    """User-facing text for the assistant panel -- the raw httpx message
    ("Client error '429 Too Many Requests' for url ...") tells a non-developer
    nothing about what to actually do next."""
    if status in (401, 403):
        return f"AI 服務拒絕存取（HTTP {status}）：請確認 AI_API_KEY 是否正確。"
    if status == 402:
        return (
            f"AI 服務回報額度不足（HTTP {status}）：模型「{model}」不是免費的，"
            "請改用名稱結尾為 :free 的模型。"
        )
    if status == 404:
        return f"找不到模型「{model}」（HTTP {status}）：請確認 AI_MODEL 名稱是否正確。"
    if status == 429:
        return (
            f"AI 服務目前繁忙（HTTP {status}）：免費模型是所有使用者共用的，"
            "尖峰時段容易額滿。請稍後再試，或在 AI_MODEL 用逗號多設定幾個備援模型。"
        )
    return f"AI 服務錯誤（HTTP {status}）：模型「{model}」。"


class OpenAICompatibleProvider:
    """Works with any OpenAI Chat Completions-compatible endpoint --
    OpenRouter, NVIDIA NIM, etc. (many have free-tier models).

    AI_MODEL may name several models separated by commas; they're tried in
    order until one answers. That fallback is what makes a free-tier setup
    usable at all: OpenRouter's `:free` models run on donated upstream
    capacity shared across every OpenRouter user, so any single free model
    returns 429 fairly often even when this account is nowhere near its own
    quota. Doing the fallback here rather than via OpenRouter's `models`
    routing parameter keeps this class provider-agnostic.
    """

    def ask(self, message: str) -> AIResult:
        if not settings.AI_API_KEY:
            return AIResult(ok=False, error="尚未設定 AI_API_KEY。")

        models = [m.strip() for m in settings.AI_MODEL.split(",") if m.strip()]
        if not models:
            return AIResult(ok=False, error="尚未設定 AI_MODEL。")

        last_error = "沒有可用的模型。"
        for model in models:
            result, try_next = self._ask_one(model, message)
            if result.ok:
                return result
            last_error = result.error or "未知錯誤。"
            if not try_next:
                break

        return AIResult(ok=False, error=last_error)

    def _ask_one(self, model: str, message: str) -> tuple[AIResult, bool]:
        """Returns (result, whether trying another model could still help)."""
        try:
            response = httpx.post(
                f"{settings.AI_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {settings.AI_API_KEY}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": message},
                    ],
                },
                timeout=30.0,
            )
            response.raise_for_status()
            body = response.json()
            reply = body["choices"][0]["message"]["content"]
            return AIResult(ok=True, reply=reply), False
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            try_next = status in _TRY_NEXT_MODEL_STATUSES
            return AIResult(ok=False, error=_describe(status, model)), try_next
        except httpx.HTTPError as exc:
            # Connection/timeout: the host itself is unreachable, so a
            # different model on that same host won't fare any better.
            return AIResult(ok=False, error=f"無法連線至 AI 服務：{exc}"), False
        except (KeyError, IndexError) as exc:
            return AIResult(ok=False, error=f"AI 服務回應格式不符：{exc}"), False
