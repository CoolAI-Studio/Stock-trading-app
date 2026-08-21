"""The model names, fetched from the provider rather than typed from memory.

The settings page asked for a model with a free-text box and an example in the
placeholder. Nobody knows what to put there: 「anthropic/claude-sonnet-4.5」 is
not a thing anyone remembers, the spelling differs per provider, and a typo
comes back as a 404 that reads as 「the key is wrong」.

A HARDCODED LIST IS THE WRONG ANSWER, which is why this makes a request.
Models are added and retired every few weeks, so a list baked into this repo is
wrong within a month -- and wrong in the direction that matters, because it
would confidently offer a name that no longer resolves. The providers publish
their own list; the app asks.

IT WORKS BEFORE A KEY IS SAVED, which is the whole point. Somebody who has just
pasted a key still has to choose a model, and OpenRouter's list needs no
credentials at all, so the common path works from an empty form.

AND IT DEGRADES. Every failure comes back as (no models, a reason) rather than
an exception: the page keeps the free-text box it has always had, because a
picker that stops somebody typing a model they know is worse than no picker.
"""

from dataclasses import dataclass, field

import httpx

# Long enough for a provider having a slow minute, short enough that a settings
# page does not appear to hang. Nothing depends on this succeeding.
_TIMEOUT_SEC = 15.0


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    # Whether asking it costs anything. The single most useful thing to know
    # when picking: a deployer choosing their first model is choosing whether
    # this feature costs them money at all.
    free: bool = False


@dataclass(frozen=True)
class ModelList:
    models: list[Model] = field(default_factory=list)
    # Why the list is empty, when it is. An empty list with no reason reads as
    # 「this provider has no models」, which is never true and stops somebody
    # who only needed to paste a key.
    error: str | None = None


def _is_free(entry: dict) -> bool:
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict):
        # OpenAI's own /models returns ids and nothing else. Claiming those are
        # free because no price came back would be the expensive kind of wrong.
        return False
    try:
        return all(float(pricing.get(k, 1) or 0) == 0 for k in ("prompt", "completion"))
    except (TypeError, ValueError):
        return False


def fetch(provider: str, base_url: str, api_key: str) -> ModelList:
    url = f"{base_url.rstrip('/')}/models"
    headers: dict[str, str] = {}

    if provider == "anthropic":
        if not api_key:
            # Named rather than returned empty: 「needs a key」 is the
            # difference between somebody pasting one and giving up.
            return ModelList(error="Anthropic 的模型清單需要金鑰。請先填入 API 金鑰再按重新整理。")
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    elif api_key:
        headers = {"Authorization": f"Bearer {api_key}"}

    try:
        response = httpx.get(url, headers=headers, timeout=_TIMEOUT_SEC)
    except Exception as exc:
        return ModelList(error=f"連不到 {url}：{type(exc).__name__}")

    if response.status_code >= 400:
        return ModelList(error=f"供應者回了 HTTP {response.status_code}，抓不到模型清單。")

    try:
        payload = response.json()
    except Exception:
        return ModelList(error="供應者的回應不是 JSON，抓不到模型清單。")

    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return ModelList(error="供應者的回應格式看不懂，抓不到模型清單。")

    models = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("id") or "").strip()
        if not model_id:
            # A blank row in a picker is unpickable and unexplainable.
            continue
        name = str(entry.get("name") or entry.get("display_name") or model_id).strip()
        models.append(Model(id=model_id, name=name, free=_is_free(entry)))

    # Free first, then by name. The free ones are what make this feature
    # reachable for somebody who has not decided to pay for it yet.
    models.sort(key=lambda m: (not m.free, m.name.lower()))
    return ModelList(models=models)
