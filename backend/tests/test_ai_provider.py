from unittest.mock import MagicMock, patch

from app.services.ai_provider import get_ai_provider
from app.services.ai_provider.openai_compatible import OpenAICompatibleProvider


def test_openai_compatible_success(monkeypatch):
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "test-key")
    fake_response = MagicMock(status_code=200)
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {"choices": [{"message": {"content": "hello there"}}]}
    with patch("httpx.post", return_value=fake_response) as mock_post:
        result = OpenAICompatibleProvider().ask("hi")

    assert result.ok is True
    assert result.reply == "hello there"
    mock_post.assert_called_once()


def test_openai_compatible_missing_api_key(monkeypatch):
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "")
    result = OpenAICompatibleProvider().ask("hi")
    assert result.ok is False
    assert "AI_API_KEY" in result.error


def test_openai_compatible_http_failure(monkeypatch):
    import httpx

    monkeypatch.setattr("app.config.settings.AI_API_KEY", "test-key")
    with patch("httpx.post", side_effect=httpx.ConnectError("boom")):
        result = OpenAICompatibleProvider().ask("hi")
    assert result.ok is False


def test_get_ai_provider_defaults_to_openai_compatible(monkeypatch):
    monkeypatch.setattr("app.config.settings.AI_PROVIDER", "openai_compatible")
    assert isinstance(get_ai_provider(), OpenAICompatibleProvider)


def test_get_ai_provider_unknown_falls_back_to_default(monkeypatch):
    monkeypatch.setattr("app.config.settings.AI_PROVIDER", "something-unrecognized")
    assert isinstance(get_ai_provider(), OpenAICompatibleProvider)
