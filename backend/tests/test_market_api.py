from app.enums import DataSource
from app.main import app
from app.services.market_data.providers.mock_provider import MockProvider
from app.services.market_data.service import MarketDataService, get_market_data_service


def test_get_quote_returns_prices_for_requested_symbols(auth_client):
    mock_service = MarketDataService(
        providers={
            DataSource.YFINANCE: MockProvider(base_prices={"AAPL": 150.0, "TSLA": 250.0}),
        }
    )
    app.dependency_overrides[get_market_data_service] = lambda: mock_service
    try:
        resp = auth_client.get("/api/market/quote", params={"symbols": "AAPL,TSLA"})
    finally:
        app.dependency_overrides.pop(get_market_data_service, None)

    assert resp.status_code == 200, resp.text
    body = {q["symbol"]: q for q in resp.json()}
    assert set(body) == {"AAPL", "TSLA"}
    assert body["AAPL"]["data_source"] == "yfinance"


def test_get_quote_requires_symbols(auth_client):
    resp = auth_client.get("/api/market/quote", params={"symbols": ""})
    assert resp.status_code == 422


def test_get_quote_requires_auth(client):
    resp = client.get("/api/market/quote", params={"symbols": "AAPL"})
    assert resp.status_code == 401
