"""The model names, fetched from the provider rather than typed from memory.

The settings page asked for a model with a free-text box and an example in the
placeholder. Nobody knows what to put there. 「anthropic/claude-sonnet-4.5」 is
not a thing anyone remembers, the spelling differs per provider, and a typo
produces a 404 from the provider that reads as 「the key is wrong」.

The owner's words, twice: 「使用者不會知道正確的模型名字，這之前說過了，要由你這邊
提供出來」.

A HARDCODED LIST IS THE WRONG ANSWER and that is why this is an endpoint.
Models are added and retired every few weeks; a list baked into this repo is
wrong within a month, and wrong in the direction that matters -- it would
confidently offer a name that no longer resolves. The providers publish their
own list, so the app asks.

IT MUST WORK BEFORE A KEY IS SAVED. That is the whole point: somebody who has
just pasted a key still has to choose a model, and somebody who has not even
got that far should be able to see what is on offer. OpenRouter's model list
needs no credentials at all, so the common path works from an empty form. The
ones that do need a key say so rather than returning an empty list.

AND IT MUST DEGRADE. If the list cannot be fetched -- an unreachable host, a
provider that has no such endpoint, a shape this does not recognise -- the page
falls back to the text box it has always had. A picker that blocks somebody
from typing a model they know is worse than no picker.
"""

from unittest.mock import patch

import httpx

from app.services import ai_model_list


def _response(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("GET", "https://x/models"))


# --- the shapes the providers actually return ---------------------------------


def test_an_openrouter_style_list_is_understood():
    payload = {
        "data": [
            {
                "id": "anthropic/claude-opus-5",
                "name": "Anthropic: Claude Opus 5",
                "pricing": {"prompt": "0.000015", "completion": "0.000075"},
            }
        ]
    }
    with patch.object(httpx, "get", return_value=_response(payload)):
        models = ai_model_list.fetch("openai_compatible", "https://openrouter.ai/api/v1", "")

    assert models.models[0].id == "anthropic/claude-opus-5"
    assert "Claude Opus 5" in models.models[0].name


def test_a_free_model_is_marked_as_free():
    """The single most useful thing to know when picking. A deployer choosing
    their first model is choosing whether this costs them anything."""
    payload = {
        "data": [
            {
                "id": "some/model:free",
                "name": "Some Model",
                "pricing": {"prompt": "0", "completion": "0"},
            },
            {
                "id": "paid/model",
                "name": "Paid",
                "pricing": {"prompt": "0.001", "completion": "0.002"},
            },
        ]
    }
    with patch.object(httpx, "get", return_value=_response(payload)):
        models = ai_model_list.fetch("openai_compatible", "https://openrouter.ai/api/v1", "")

    by_id = {m.id: m for m in models.models}
    assert by_id["some/model:free"].free is True
    assert by_id["paid/model"].free is False


def test_a_plain_openai_style_list_has_no_pricing_and_that_is_fine():
    """OpenAI's own /models returns ids and nothing else. Claiming those are
    free because no price came back would be the expensive kind of wrong."""
    payload = {"data": [{"id": "gpt-5.6-sol"}]}
    with patch.object(httpx, "get", return_value=_response(payload)):
        models = ai_model_list.fetch("openai_compatible", "https://api.openai.com/v1", "sk-x")

    assert models.models[0].id == "gpt-5.6-sol"
    assert models.models[0].free is False
    assert models.models[0].name == "gpt-5.6-sol"


def test_anthropics_own_shape_is_understood():
    payload = {"data": [{"id": "claude-opus-5", "display_name": "Claude Opus 5"}]}
    with patch.object(httpx, "get", return_value=_response(payload)):
        models = ai_model_list.fetch("anthropic", "https://api.anthropic.com", "sk-ant-x")

    assert models.models[0].id == "claude-opus-5"
    assert models.models[0].name == "Claude Opus 5"


# --- it works before there is a key --------------------------------------------


def test_openrouter_is_asked_without_credentials():
    """The common path, and the reason this is usable from an empty form:
    somebody who has not saved a key yet still has to choose a model."""
    seen = {}

    def _get(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers") or {}
        return _response({"data": []})

    with patch.object(httpx, "get", _get):
        ai_model_list.fetch("openai_compatible", "https://openrouter.ai/api/v1", "")

    assert seen["url"].endswith("/models")
    assert "Authorization" not in seen["headers"]


def test_a_key_is_sent_when_there_is_one():
    seen = {}

    def _get(url, **kwargs):
        seen["headers"] = kwargs.get("headers") or {}
        return _response({"data": []})

    with patch.object(httpx, "get", _get):
        ai_model_list.fetch("openai_compatible", "https://api.openai.com/v1", "sk-secret")

    assert seen["headers"].get("Authorization") == "Bearer sk-secret"


def test_anthropic_without_a_key_says_so_rather_than_returning_nothing():
    """An empty list reads as 「this provider has no models」. Saying a key is
    needed is the difference between somebody pasting one and giving up."""
    result = ai_model_list.fetch("anthropic", "https://api.anthropic.com", "")

    assert result.models == []
    assert result.error and "金鑰" in result.error


# --- and it degrades rather than blocking --------------------------------------


def test_an_unreachable_provider_is_reported_not_raised():
    """The page falls back to the text box it has always had. A picker that
    stops somebody typing a model they know is worse than no picker."""
    with patch.object(httpx, "get", side_effect=httpx.ConnectError("no route")):
        result = ai_model_list.fetch("openai_compatible", "https://nope.example", "")

    assert result.models == []
    assert result.error


def test_a_rejected_request_reports_the_status():
    with patch.object(httpx, "get", return_value=_response({"error": "nope"}, status=401)):
        result = ai_model_list.fetch("openai_compatible", "https://x/v1", "bad-key")

    assert result.models == []
    assert "401" in (result.error or "")


def test_a_shape_this_does_not_recognise_is_not_a_crash():
    with patch.object(httpx, "get", return_value=_response({"unexpected": True})):
        result = ai_model_list.fetch("openai_compatible", "https://x/v1", "")

    assert result.models == []


def test_entries_without_an_id_are_skipped_rather_than_listed_blank():
    payload = {"data": [{"name": "no id here"}, {"id": "real/model"}]}
    with patch.object(httpx, "get", return_value=_response(payload)):
        result = ai_model_list.fetch("openai_compatible", "https://x/v1", "")

    assert [m.id for m in result.models] == ["real/model"]


def test_free_models_come_first():
    """A deployer choosing their first model is choosing whether this costs
    them anything, and the free ones are what makes the feature reachable."""
    payload = {
        "data": [
            {"id": "paid/a", "pricing": {"prompt": "0.01", "completion": "0.01"}},
            {"id": "free/b", "pricing": {"prompt": "0", "completion": "0"}},
        ]
    }
    with patch.object(httpx, "get", return_value=_response(payload)):
        result = ai_model_list.fetch("openai_compatible", "https://x/v1", "")

    assert result.models[0].id == "free/b"


# --- through the API -------------------------------------------------------------


def test_the_endpoint_needs_a_login(client):
    assert client.get("/api/ai-settings/models").status_code == 401


def test_the_endpoint_lists_for_whatever_the_form_currently_shows(auth_client):
    """Not for what is SAVED. The person is choosing a provider and a model in
    the same visit, and listing the saved provider's models while they look at
    a different one is how a picker offers names that cannot work."""
    seen = {}

    def _get(url, **kwargs):
        seen["url"] = url
        return _response({"data": [{"id": "x/y"}]})

    with patch.object(httpx, "get", _get):
        body = auth_client.get(
            "/api/ai-settings/models?provider=openai_compatible&base_url=https://custom.example/v1"
        ).json()

    assert "custom.example" in seen["url"]
    assert body["models"][0]["id"] == "x/y"
