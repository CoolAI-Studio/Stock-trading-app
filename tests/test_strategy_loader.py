import pytest
import json
from src.strategy_loader import StrategyLoader

def test_load_pipe_script(tmp_path):
    # 建立一個臨時策略檔案，指定 UTF-8 編碼
    strategy_file = tmp_path / "dummy_strategy.py"
    strategy_file.write_text("def run(): return '策略執行成功'", encoding="utf-8")
    loader = StrategyLoader()
    assert loader.load_pipe_script(str(strategy_file), "DummyStrategy") is True
    assert "DummyStrategy" in loader.list_strategies()
    assert loader.loaded_strategies["DummyStrategy"].run() == "策略執行成功"

def test_load_from_futu():
    loader = StrategyLoader()
    signal = loader.load_from_futu("AAPL")
    assert signal["symbol"] == "AAPL"
    assert signal["action"] == "BUY"
    assert signal["source"] == "FutuAPI"
    assert "futu_AAPL" in loader.list_strategies()

def test_load_from_tradingview():
    loader = StrategyLoader()
    payload = json.dumps({"symbol": "BTCUSD", "action": "SELL"})
    signal = loader.load_from_tradingview(payload)
    assert signal["symbol"] == "BTCUSD"
    assert signal["action"] == "SELL"
    assert signal["source"] == "TradingView"
    assert "tv_BTCUSD" in loader.list_strategies()
