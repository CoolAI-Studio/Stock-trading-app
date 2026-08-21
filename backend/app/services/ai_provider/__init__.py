from app.config import settings
from app.services.ai_provider.anthropic_provider import AnthropicProvider
from app.services.ai_provider.base import AIProvider, AIResult, AISettings
from app.services.ai_provider.openai_compatible import OpenAICompatibleProvider

__all__ = ["AIProvider", "AIResult", "AISettings", "get_ai_provider"]

_CLASSES = {
    "openai_compatible": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
}


def get_ai_provider(resolved=None) -> AIProvider:
    """The client for whichever provider this deployment (or this user) uses.

    `resolved` is a services.ai_settings.ResolvedAI. Without it the provider
    reads the environment, which is what every caller did before the settings
    moved into the database and what keeps the existing tests meaningful.

    A NEW INSTANCE each time rather than a shared singleton: the config is now
    per user, and a cached client would answer the second user with the first
    one's key. The clients hold no connection state -- both build their request
    per call -- so this costs nothing.
    """
    if resolved is None:
        return _CLASSES.get(settings.AI_PROVIDER, OpenAICompatibleProvider)()

    cls = _CLASSES.get(resolved.provider, OpenAICompatibleProvider)
    return cls(
        AISettings(
            base_url=resolved.base_url,
            api_key=resolved.api_key,
            model=resolved.model,
        )
    )
