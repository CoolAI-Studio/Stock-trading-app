from app.config import settings
from app.services.ai_provider.anthropic_provider import AnthropicProvider
from app.services.ai_provider.base import AIProvider, AIResult
from app.services.ai_provider.openai_compatible import OpenAICompatibleProvider

__all__ = ["AIProvider", "AIResult", "get_ai_provider"]

_PROVIDERS: dict[str, AIProvider] = {
    "openai_compatible": OpenAICompatibleProvider(),
    "anthropic": AnthropicProvider(),
}


def get_ai_provider() -> AIProvider:
    return _PROVIDERS.get(settings.AI_PROVIDER, _PROVIDERS["openai_compatible"])
