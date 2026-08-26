"""子行程的那一端：使用者的策略真正執行的地方。

由 strategy_worker.py 用 `python -m app.services.strategy_worker_main` 啟動，環境
是父行程明確給的一份白名單（見那個檔案的 _KEEP_FROM_PARENT）。

＊ 這裡不可以 import 重的東西。

量到的（#18 第 1 步）：

    裸 Python                 187 ms
    ＋ SQLAlchemy 模型層      2171 ms
    ＋ indicators（要用的）     285 ms

子行程被殺掉之後要重建，而重建期間它負責的策略是瞎的——輪詢週期才五秒。所以這個
檔案的 import 清單本身就是一條規則，而
tests/test_the_sandbox_does_not_drag_the_orm_along.py 在有人破壞它的時候會紅。

＊ 一行一個 JSON，不用 pickle。

pickle 會反序列化成任意物件，而這條管線的另一端跑的正是不受信任的程式碼。JSON 只
有資料，沒有行為。
"""

import json
import os
import sys


def _reply(ok: bool, **fields) -> None:
    """回一行。

    每一個回覆都要送得出去，包括「我失敗了」——父行程在等一行，讀不到就會把這裡當
    成死掉並殺掉重建，而那要花三百毫秒。一個講得出原因的失敗便宜得多。
    """
    sys.stdout.write(json.dumps({"ok": ok, **fields}) + "\n")
    sys.stdout.flush()


# 這個行程裡活著的策略，key -> LoadedStrategy。
#
# **狀態住在這裡，這就是常駐子行程的全部意義。** 一支 MA20 策略的 self.prices 要
# 跨輪累積，每輪重建就永遠只看得到一個價——而那不會有任何東西變紅，只會讓它永遠
# 不發訊號。
_loaded: dict = {}


def _get(key: str):
    """那支策略的 LoadedStrategy。

    底下每一個指令都直接叫 `.instance.on_tick(...)`，**不是** `loaded.on_tick(...)`
    ——後者會走 strategy_runtime._guarded，也就是那個開一條執行緒再 join 逾時的守
    衛。那個守衛的檔頭自己寫著它的極限：Python 殺不掉執行緒，逾時之後那條 while
    True 會一直燒著一顆核心直到行程重啟。

    這裡的逾時改由父行程做，做法是**殺掉這整個行程**。留著內層那一層不但多花一
    條執行緒，還會把真正的期限吃掉：內層先逾時就變成一個普通的策略錯誤，父行程
    永遠等不到該殺行程的那一刻，於是那條迴圈又活下來了——等於這張票什麼都沒換到。
    """
    strategy = _loaded.get(key)
    if strategy is None:
        # 父行程負責在呼叫之前確保載入過。走到這裡表示子行程被重建過而父行程還沒
        # 察覺——說清楚比回一個假的 HOLD 好得多。
        raise KeyError(f"策略 {key!r} 不在這個行程裡（可能剛被重建）")
    return strategy


def _bar_from_wire(wire: dict):
    from datetime import datetime

    from app.services.market_data.base import Bar, Timeframe

    return Bar(
        symbol=wire["symbol"],
        timeframe=Timeframe(wire["timeframe"]),
        timestamp=datetime.fromisoformat(wire["timestamp"]),
        open=wire["open"],
        high=wire["high"],
        low=wire["low"],
        close=wire["close"],
        volume=wire["volume"],
    )


def _handle(message: dict) -> dict:
    command = message.get("cmd")

    if command == "ping":
        return {"pong": True}

    if command == "env":
        # 讓「這裡沒有秘密」驗得到。只回名字不回值——回值就等於在管線上再洩一次，
        # 而這個指令的用途是證明那裡是空的。
        return {"names": sorted(os.environ)}

    if command == "compile":
        # 在函式裡 import，不在檔頭：ping 和 env 是父行程確認這裡活著用的，不該為
        # 了它們付沙箱的載入時間。
        from app.services.strategy_runtime import compile_strategy

        loaded = compile_strategy(message["source_code"], params=message.get("params") or {})
        # 回的是**資料**，不是那個 LoadedStrategy 物件。物件本身留在這裡是重點：
        # 它裡面有使用者寫的程式碼建出來的實例，而把那種東西送過管線就等於把隔離
        # 拆掉。`declared_params` 是原始碼宣告的預設值，不是現在生效的值——表單要
        # 顯示「預設 5，你設了 20」，回生效值會把作者的答案弄丟。
        return {
            "name": loaded.name,
            "symbol": loaded.symbol,
            "entry_point": loaded.entry_point,
            "code_hash": loaded.code_hash,
            "timeframe": loaded.timeframe.value,
            "warmup_bars": loaded.warmup_bars,
            "declared_params": loaded.declared_params,
        }

    if command == "validate":
        # compile ＋ 試跑，**一次往返**。
        #
        # 拆成兩次的話，中間那段時間父行程手上會有一個「編好了但還沒跑」的狀態，
        # 而逾時可能落在那個縫裡——那時候該不該殺行程沒有答案。一次往返就沒有那
        # 個縫：不是整件事做完，就是整個行程被殺掉。
        from app.services.market_data.base import bars_from_closes
        from app.services.strategy_runtime import compile_strategy

        loaded = compile_strategy(message["source_code"])
        detected = {
            "name": loaded.name,
            "symbol": loaded.symbol,
            "entry_point": loaded.entry_point,
            "timeframe": loaded.timeframe.value,
            "declared_params": loaded.declared_params,
        }
        prices = [float(price) for price in message["prices"]]
        try:
            if loaded.entry_point == "on_bar":
                bars = bars_from_closes(loaded.symbol, loaded.timeframe, prices)
                signals = [loaded.instance.on_bar(bar) for bar in bars]
            else:
                signals = [loaded.instance.on_tick(price) for price in prices]
        except Exception as exc:  # noqa: BLE001
            # 編得起來但跑不動，跟編不起來是兩件事：前者要把偵測到的欄位還給表
            # 單（使用者才知道我們讀到了什麼），後者沒有東西可以還。
            return {**detected, "run_error": f"{type(exc).__name__}: {exc}"}
        return {**detected, "signals": [str(signal) for signal in signals]}

    if command == "preload":
        # 只是把沙箱載進來。量到 886 毫秒——那是第一次 load 的成本，而如果讓它落在
        # 第一輪盯盤上，三個 worker 依序付就是 3.4 秒，而輪詢週期只有五秒。父行程
        # 在啟動之後、第一輪之前並行地叫這個，把那筆錢先付掉。
        from app.services.strategy_runtime import compile_strategy  # noqa: F401

        return {}

    if command == "load":
        from app.services.strategy_runtime import compile_strategy

        loaded = compile_strategy(message["source_code"], params=message.get("params") or {})
        _loaded[message["key"]] = loaded
        return {
            "name": loaded.name,
            "symbol": loaded.symbol,
            "entry_point": loaded.entry_point,
            "code_hash": loaded.code_hash,
            "timeframe": loaded.timeframe.value,
            "warmup_bars": loaded.warmup_bars,
            "declared_params": loaded.declared_params,
        }

    if command == "tick":
        return {"signal": _get(message["key"]).instance.on_tick(float(message["price"]))}

    if command == "bar":
        return {"signal": _get(message["key"]).instance.on_bar(_bar_from_wire(message["bar"]))}

    if command == "warm":
        # 重播的訊號全部丟掉：那些 K 棒在這個實例存在之前就收盤了，它們產生的 BUY
        # 是一段對過去的觀察，不是現在的指示。
        instance = _get(message["key"]).instance
        for wire in message["bars"]:
            instance.on_bar(_bar_from_wire(wire))
        return {}

    if command == "drop":
        _loaded.pop(message["key"], None)
        return {}

    raise ValueError(f"不認得的指令：{command!r}")


def main() -> int:
    _reply(True, ready=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            _reply(False, error=f"讀不懂的指令：{exc}")
            continue

        if message.get("cmd") == "bye":
            return 0

        try:
            _reply(True, result=_handle(message))
        except Exception as exc:  # noqa: BLE001
            # 任何失敗都只是「這一個請求失敗」，不是「這個行程該死」。一支寫壞的策
            # 略如果能弄死 worker，「一個人寫錯」就變成「所有策略停擺」——而盯盤不
            # 能停是這個產品唯一的鐵律。
            _reply(False, error=f"{type(exc).__name__}: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
