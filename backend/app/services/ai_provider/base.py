from dataclasses import dataclass
from typing import Protocol

SYSTEM_PROMPT = (
    "You are a setup assistant embedded in a personal trading dashboard. Your only "
    "job is helping the user figure out how to obtain and correctly format API "
    "credentials for a broker/exchange they want to connect later (e.g. where to find "
    "an API key in their broker's dashboard, what a field is called, what format it's "
    "in). You cannot place trades, execute code, or access the user's accounts -- you "
    "only produce text guidance. If asked to do anything else, say this is outside "
    "what you can help with here."
)


@dataclass
class AIResult:
    ok: bool
    reply: str | None = None
    error: str | None = None


class AIProvider(Protocol):
    # `system` overrides SYSTEM_PROMPT for callers with a different contract to
    # teach the model (strategy generation), so they get the multi-model
    # fallback and the error phrasing without reimplementing either.
    def ask(self, message: str, system: str | None = None) -> AIResult: ...
