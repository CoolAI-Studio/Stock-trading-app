import httpx

from app.config import settings
from app.services.ai_provider.base import SYSTEM_PROMPT, AIResult


class OpenAICompatibleProvider:
    """Works with any OpenAI Chat Completions-compatible endpoint --
    OpenRouter, NVIDIA NIM, etc. (many have free-tier models)."""

    def ask(self, message: str) -> AIResult:
        if not settings.AI_API_KEY:
            return AIResult(ok=False, error="AI_API_KEY is not configured")

        try:
            response = httpx.post(
                f"{settings.AI_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {settings.AI_API_KEY}"},
                json={
                    "model": settings.AI_MODEL,
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
            return AIResult(ok=True, reply=reply)
        except httpx.HTTPError as exc:
            return AIResult(ok=False, error=str(exc))
        except (KeyError, IndexError) as exc:
            return AIResult(ok=False, error=f"unexpected response shape: {exc}")
