from app.services.ai_provider.anthropic_provider import AnthropicProvider
from app.services.ai_provider.base import AIProvider, AIResult, AISettings
from app.services.ai_provider.openai_compatible import OpenAICompatibleProvider

__all__ = ["AIProvider", "AIResult", "AISettings", "get_ai_provider"]

_CLASSES = {
    "openai_compatible": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
}


def get_ai_provider(resolved) -> AIProvider:
    """The client for whichever provider THIS USER is entitled to use.

    `resolved` is a services.ai_settings.ResolvedAI and it is REQUIRED. It used
    to default to None, and None meant 「read the environment」 -- that is, the
    deployment owner's own key.

    That default was the whole of a real bug. Two routes called this with no
    arguments and no `db` at all (strategies.py's generator and
    broker_credentials.py's assistant), so `user.id` never took part in
    choosing a key, and any account could spend the owner's AI credit. The
    owner gate in services/ai_settings.py::_is_deployment_owner was correct
    and simply never consulted.

    Nothing about that failure was visible: it did not error, it worked --
    with the wrong person's money. So the fallback is gone rather than fixed
    in place. Forgetting to resolve is now a TypeError at the call site.

    A NEW INSTANCE each time rather than a shared singleton: the config is per
    user, and a cached client would answer the second user with the first
    one's key. The clients hold no connection state, so this costs nothing.
    """
    cls = _CLASSES.get(resolved.provider, OpenAICompatibleProvider)
    return cls(
        AISettings(
            base_url=resolved.base_url,
            api_key=resolved.api_key,
            model=resolved.model,
        )
    )
