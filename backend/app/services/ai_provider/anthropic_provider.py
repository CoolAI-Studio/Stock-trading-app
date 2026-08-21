from app.config import settings
from app.services.ai_provider.base import SYSTEM_PROMPT, AIResult, AISettings


class AnthropicProvider:
    """Native Claude API. Requires the optional `anthropic` package
    (commented out in requirements.txt -- uncomment it if you select
    AI_PROVIDER=anthropic)."""

    def __init__(self, config: AISettings | None = None) -> None:
        # None means 「read the environment」, which keeps every existing caller
        # and every existing test working unchanged.
        self._config = config

    @property
    def _settings(self) -> AISettings:
        return self._config or AISettings(
            base_url=settings.AI_BASE_URL,
            api_key=settings.AI_API_KEY,
            model=settings.AI_MODEL,
        )

    def ask(self, message: str, system: str | None = None) -> AIResult:
        if not self._settings.api_key:
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
            client = anthropic.Anthropic(
                api_key=self._settings.api_key, timeout=30.0, max_retries=1
            )
            response = client.messages.create(
                model=self._settings.model or "claude-sonnet-4-5",
                max_tokens=1024,
                system=system or SYSTEM_PROMPT,
                messages=[{"role": "user", "content": message}],
            )
            reply = "".join(block.text for block in response.content if block.type == "text")
            return AIResult(ok=True, reply=reply)
        except anthropic.APIError as exc:
            return AIResult(ok=False, error=str(exc))
