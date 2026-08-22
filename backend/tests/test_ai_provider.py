from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services.ai_provider import get_ai_provider
from app.services.ai_provider.openai_compatible import OpenAICompatibleProvider


def _mock_response(status: int = 200, reply: str = "hello there") -> MagicMock:
    """A stand-in for httpx's Response: raises HTTPStatusError on >=400 the
    same way raise_for_status() really would, so the provider's status-code
    branching is exercised for real rather than against a bare Exception."""
    response = MagicMock()
    response.status_code = status
    if status >= 400:
        request = httpx.Request("POST", "https://example.com/chat/completions")
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"error {status}",
            request=request,
            response=httpx.Response(status_code=status, request=request),
        )
    else:
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": reply}}]}
    return response


@pytest.fixture
def ai_configured(monkeypatch):
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "test-key")
    monkeypatch.setattr("app.config.settings.AI_MODEL", "model-a")


def test_openai_compatible_success(ai_configured):
    with patch("httpx.post", return_value=_mock_response()) as mock_post:
        result = OpenAICompatibleProvider().ask("hi")

    assert result.ok is True
    assert result.reply == "hello there"
    mock_post.assert_called_once()
    assert mock_post.call_args.kwargs["json"]["model"] == "model-a"


def test_a_caller_supplied_system_prompt_replaces_the_default(ai_configured):
    """The strategy generator needs its own contract prompt, but must keep the
    multi-model fallback that lives in this class."""
    with patch("httpx.post", return_value=_mock_response()) as mock_post:
        result = OpenAICompatibleProvider().ask("hi", system="you generate strategies")

    assert result.ok is True
    messages = mock_post.call_args.kwargs["json"]["messages"]
    assert messages[0] == {"role": "system", "content": "you generate strategies"}


def test_openai_compatible_missing_api_key(monkeypatch):
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "")
    result = OpenAICompatibleProvider().ask("hi")
    assert result.ok is False
    assert "AI_API_KEY" in result.error


def test_openai_compatible_missing_model(monkeypatch):
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "test-key")
    monkeypatch.setattr("app.config.settings.AI_MODEL", "")
    result = OpenAICompatibleProvider().ask("hi")
    assert result.ok is False
    assert "AI_MODEL" in result.error


def test_openai_compatible_connection_failure(ai_configured):
    with patch("httpx.post", side_effect=httpx.ConnectError("boom")):
        result = OpenAICompatibleProvider().ask("hi")
    assert result.ok is False


def test_falls_back_to_the_next_model_on_429(monkeypatch):
    """The reason multi-model support exists: OpenRouter's :free models share
    one donated upstream pool across all its users, so a 429 is common even
    when this account's own quota is untouched."""
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "test-key")
    monkeypatch.setattr("app.config.settings.AI_MODEL", "busy-model, working-model")

    with patch(
        "httpx.post", side_effect=[_mock_response(429), _mock_response(200, "answer from backup")]
    ) as mock_post:
        result = OpenAICompatibleProvider().ask("hi")

    assert result.ok is True
    assert result.reply == "answer from backup"
    assert mock_post.call_count == 2
    assert mock_post.call_args_list[0].kwargs["json"]["model"] == "busy-model"
    assert mock_post.call_args_list[1].kwargs["json"]["model"] == "working-model"


def test_reports_a_readable_error_when_every_model_is_rate_limited(monkeypatch):
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "test-key")
    monkeypatch.setattr("app.config.settings.AI_MODEL", "a,b")

    with patch("httpx.post", side_effect=[_mock_response(429), _mock_response(429)]) as mock_post:
        result = OpenAICompatibleProvider().ask("hi")

    assert result.ok is False
    assert mock_post.call_count == 2
    assert "429" in result.error
    # Must not surface httpx's raw "Client error '429 Too Many Requests' for
    # url ..." -- that told the owner nothing actionable.
    assert "繁忙" in result.error


def test_does_not_burn_quota_walking_the_list_on_a_bad_api_key(monkeypatch):
    """401 fails identically on every model, so trying the rest would only
    spend more of a 50-requests-per-day free allowance for nothing."""
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "bad-key")
    monkeypatch.setattr("app.config.settings.AI_MODEL", "a,b,c")

    with patch("httpx.post", return_value=_mock_response(401)) as mock_post:
        result = OpenAICompatibleProvider().ask("hi")

    assert result.ok is False
    assert mock_post.call_count == 1
    assert "AI_API_KEY" in result.error


def test_unknown_model_error_names_the_model(monkeypatch):
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "test-key")
    monkeypatch.setattr("app.config.settings.AI_MODEL", "typo/model-name")

    with patch("httpx.post", return_value=_mock_response(404)):
        result = OpenAICompatibleProvider().ask("hi")

    assert result.ok is False
    assert "typo/model-name" in result.error


def test_a_retired_model_is_stepped_over_rather_than_failing_outright(monkeypatch):
    """Free model line-ups get retired/renamed without notice, so a stale name
    in the list must not take the whole assistant down."""
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "test-key")
    monkeypatch.setattr("app.config.settings.AI_MODEL", "retired-model, current-model")

    with patch(
        "httpx.post", side_effect=[_mock_response(404), _mock_response(200, "still working")]
    ) as mock_post:
        result = OpenAICompatibleProvider().ask("hi")

    assert result.ok is True
    assert result.reply == "still working"
    assert mock_post.call_count == 2


def test_paid_model_402_explains_it_is_not_free(monkeypatch):
    """openrouter/auto routes to paid models and bills at their rate -- the
    owner explicitly wants a zero-cost setup, so this needs to say why."""
    monkeypatch.setattr("app.config.settings.AI_API_KEY", "test-key")
    monkeypatch.setattr("app.config.settings.AI_MODEL", "openrouter/auto")

    with patch("httpx.post", return_value=_mock_response(402)):
        result = OpenAICompatibleProvider().ask("hi")

    assert result.ok is False
    assert ":free" in result.error


def test_unexpected_response_shape_is_reported_not_raised(ai_configured):
    response = MagicMock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = {"unexpected": "shape"}

    with patch("httpx.post", return_value=response):
        result = OpenAICompatibleProvider().ask("hi")

    assert result.ok is False


# 這兩條原本呼叫 get_ai_provider() 不帶參數。那個無參數的呼叫方式已經拿掉了：
# 它的意思是「讀環境變數」，也就是部署者自己的金鑰，而兩支路由就是靠它安靜地花
# 了別人的錢（tests/test_ai_credit_belongs_to_the_owner.py）。
#
# 它們原本要守的東西還在，只是改成用 resolved 表達：不認得的 provider 名稱要有
# 一個 fallback，而不是丟例外。


def _resolved(provider: str):
    from app.services.ai_settings import ResolvedAI

    return ResolvedAI(
        provider=provider,
        base_url="https://example.invalid/v1",
        api_key="k",
        model="m",
        source="env",
    )


def test_get_ai_provider_defaults_to_openai_compatible():
    assert isinstance(get_ai_provider(_resolved("openai_compatible")), OpenAICompatibleProvider)


def test_get_ai_provider_unknown_falls_back_to_default():
    """一個沒見過的名字不該讓整支端點 500——回一個講得出話的 client 才有機會把
    「這個 provider 不認得」變成一句訊息。"""
    assert isinstance(
        get_ai_provider(_resolved("something-unrecognized")), OpenAICompatibleProvider
    )
