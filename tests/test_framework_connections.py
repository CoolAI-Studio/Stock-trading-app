import pytest
from src.framework import FutuAPI, TradingViewAPI

def test_futu_api_connect():
    api = FutuAPI()
    assert api.connect() is True

def test_futu_api_get_price():
    api = FutuAPI()
    price = api.get_price("AAPL")
    assert isinstance(price, float)
    assert price == 100.0

def test_futu_api_place_order():
    api = FutuAPI()
    result = api.place_order("AAPL", 10, "BUY")
    assert result is True

def test_tradingview_api_connect():
    api = TradingViewAPI()
    assert api.connect() is True

def test_tradingview_api_get_price():
    api = TradingViewAPI()
    price = api.get_price("BTCUSD")
    assert isinstance(price, float)
    assert price == 200.0

def test_tradingview_api_place_order():
    api = TradingViewAPI()
    result = api.place_order("BTCUSD", 1, "SELL")
    assert result is True
