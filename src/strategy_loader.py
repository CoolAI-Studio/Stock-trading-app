import importlib.util
import json

class StrategyLoader:
    """策略載入器，支援 Pipe script、富途 API、TradingView Webhook (模擬版)"""

    def __init__(self):
        self.loaded_strategies = {}

    def load_pipe_script(self, file_path: str, strategy_name: str):
        """載入本地 Python 策略檔案"""
        spec = importlib.util.spec_from_file_location(strategy_name, file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.loaded_strategies[strategy_name] = module
        return True

    def load_from_futu(self, symbol: str):
        """模擬從富途 API 載入策略訊號"""
        signal = {"symbol": symbol, "action": "BUY", "source": "FutuAPI"}
        self.loaded_strategies[f"futu_{symbol}"] = signal
        return signal

    def load_from_tradingview(self, webhook_payload: str):
        """模擬從 TradingView Webhook 載入策略訊號"""
        data = json.loads(webhook_payload)
        signal = {"symbol": data.get("symbol"), "action": data.get("action"), "source": "TradingView"}
        self.loaded_strategies[f"tv_{signal['symbol']}"] = signal
        return signal

    def list_strategies(self):
        return list(self.loaded_strategies.keys())
