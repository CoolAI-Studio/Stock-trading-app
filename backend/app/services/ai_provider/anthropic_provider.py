from app.config import settings
from app.services.ai_provider.base import SYSTEM_PROMPT, AIResult


class AnthropicProvider:
    """Native Claude API. Requires the optional `anthropic` package
    (commented out in requirements.txt -- uncomment it if you select
    AI_PROVIDER=anthropic)."""

    def ask(self, message: str, system: str | None = None) -> AIResult:
        if not settings.AI_API_KEY:
            return AIResult(ok=False, error="AI_API_KEY is not configured")

        try:
            import anthropic
        except ImportError:
            return AIResult(
                ok=False,
                error="AI_PROVIDER=anthropic requires the 'anthropic' package "
                "(uncomment it in requirements.txt and reinstall)",
            )

        try:
            # Timeout and retry bound stated explicitly. The SDK's defaults
            # are ten minutes and two retries, so an unreachable host would
            # hold a threadpool thread for half an hour -- the same shape as
            # pywebpush's timeout=None, which this project has already paid
            # for once (see services/notification/webpush.py). The sibling
            # openai_compatible provider already passes 30s; this one did not.
            client = anthropic.Anthropic(api_key=settings.AI_API_KEY, timeout=30.0, max_retries=1)
            response = client.messages.create(
                model=settings.AI_MODEL or "claude-sonnet-4-5",
                max_tokens=1024,
                system=system or SYSTEM_PROMPT,
                messages=[{"role": "user", "content": message}],
            )
            reply = "".join(block.text for block in response.content if block.type == "text")
            return AIResult(ok=True, reply=reply)
        except anthropic.APIError as exc:
            return AIResult(ok=False, error=str(exc))
