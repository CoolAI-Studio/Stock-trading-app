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

import contextlib
import json
import os
import sys


def apply_limits(memory_bytes: int) -> dict:
    """把這個行程的記憶體上限設下去。回報設成什麼樣，**絕對不拋例外**。

    設不起來只能是「沒設成」，不可以是「行程起不來」。在唯讀的容器、已經被外層
    cgroup 限制過的環境、或 setrlimit 被 seccomp 擋掉的沙箱裡，這一句都可能失
    敗——而正確的反應是繼續跑。**警告不能停擺，優先於一個沒設成的上限。**

    Windows 沒有 resource 模組，所以那裡永遠是 applied=False。那不是退化：開發機
    上起不來的話，等於在每一個貢獻者寫程式的地方把整個策略功能關掉。
    """
    try:
        import resource  # noqa: PLC0415 -- 只有 POSIX 有，不能放在檔頭
    except ImportError:
        return {"applied": False, "memory_bytes": 0, "why": "這個平台沒有 resource 模組"}

    try:
        # RLIMIT_AS 是位址空間，不是 RSS。選它是因為它是唯一在 Linux 上真的會讓
        # 配置失敗（丟 MemoryError）的那一個——RLIMIT_DATA 在現代 glibc 的 mmap
        # 配置上根本不生效，而那正是 list 變大時走的路。
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        # 不要把 hard limit 調高（調不動，而且會拋）。取小的那個。
        ceiling = memory_bytes if hard == resource.RLIM_INFINITY else min(memory_bytes, hard)
        resource.setrlimit(resource.RLIMIT_AS, (ceiling, hard))
    except Exception as exc:  # noqa: BLE001
        return {"applied": False, "memory_bytes": 0, "why": f"{type(exc).__name__}: {exc}"}

    return {"applied": True, "memory_bytes": ceiling, "why": ""}


_limits: dict = {"applied": False, "memory_bytes": 0, "why": "還沒設"}


def lift_limits() -> None:
    """把記憶體上限拿掉。**只有在上限緊到連沙箱都載不起來的時候才走這條。**

    這是這個功能唯一可以接受的失敗方式。RLIMIT_AS 限的是位址空間不是 RSS，而
    CPython 光是啟動就會 mmap 一大片——所以「256 MB 夠不夠」不是一個看得出來的數
    字，它取決於 glibc 的版本、執行緒堆疊、和容器的設定。

    設得太緊的後果不是「策略被擋下來」，是**每一支策略都永遠載不起來**：呼叫端會
    一直收到子行程不可用，而那被歸類成基礎設施問題（不停用策略，只留一句話），於
    是畫面上每一支都寫著「行程暫時不可用」，而它永遠不會好。**警告全面停擺**——那
    比沒有上限糟得多。

    所以寧可沒有上限也要活著，並且把這件事記下來讓 limits 指令說得出來。
    """
    global _limits
    # **先記錄再嘗試解除。** 這件事發生過的紀錄，比解除本身更重要：解除可能失敗
    # （沒有 resource 模組、setrlimit 被擋），但「載不起來所以退讓過」是使用者和
    # /system 都應該看得到的事實。記在解除成功之後，就會在最需要它的那些環境裡剛
    # 好消失。
    _limits = {
        "applied": False,
        "memory_bytes": 0,
        "why": "上限緊到連沙箱都載不起來，已經拿掉——警告不能停擺優先",
    }
    try:
        import resource  # noqa: PLC0415
    except ImportError:
        return
    with contextlib.suppress(Exception):
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        resource.setrlimit(resource.RLIMIT_AS, (hard, hard))


def _sandbox():
    """沙箱的 compile_strategy，在上限之下載入。

    載不起來（MemoryError）就把上限拿掉再試一次。不是吞掉錯誤：試不起來的第二次
    會照樣往上拋。
    """
    try:
        from app.services.strategy_runtime import compile_strategy
    except MemoryError:
        lift_limits()
        from app.services.strategy_runtime import compile_strategy
    return compile_strategy


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

    if command == "limits":
        # 讓上限**驗得到**。一個宣稱有邊界但沒有辦法檢查的邊界，跟沒有邊界的差別
        # 只在文件上——跟 env 這個指令存在的理由是同一個。
        return dict(_limits)

    if command == "env":
        # 讓「這裡沒有秘密」驗得到。只回名字不回值——回值就等於在管線上再洩一次，
        # 而這個指令的用途是證明那裡是空的。
        return {"names": sorted(os.environ)}

    if command == "compile":
        # 在函式裡 import，不在檔頭：ping 和 env 是父行程確認這裡活著用的，不該為
        # 了它們付沙箱的載入時間。
        loaded = _sandbox()(message["source_code"], params=message.get("params") or {})
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

    if command == "replay":
        # 一場回測的訊號，**一次往返**。
        #
        # 只搬「跑出訊號」，不搬整個 run_backtest：帳戶模擬和計分不碰使用者的程
        # 式碼（_dispatch 只餵一根 K 棒進去，策略看不到帳戶），而把它們也搬過來
        # 要讓 BacktestAssumptions 和 BacktestResult 都過一次 JSON——這個 app 裡
        # 欄位最多的兩個東西。這樣切一樣是一次往返，序列化面小得多。
        from app.services.strategy_runtime import effective_warmup

        loaded = _sandbox()(message["source_code"], params=message.get("params") or {})
        instance = loaded.instance

        # 暖身要幾根，在**這裡**算。呼叫端不知道答案：它取決於原始碼有沒有宣告
        # self.warmup_bars，而那要編過才知道。讓呼叫端先編一次去問，就等於在 API
        # 行程裡又跑了一次使用者的程式碼——那正是這張票要消滅的東西。
        if loaded.entry_point == "on_bar":
            warmup = effective_warmup(loaded, message["stored_warmup_bars"])
        else:
            # 盯盤迴圈對 on_tick 也不做暖身：_run_tick_strategy 拿到第一個報價就
            # 動作，「資料還不夠」留給策略自己的 `if len(self.prices) < 5`。這裡
            # 加了就會讓回測比實際嚴格。
            warmup = 0

        described = {
            "name": loaded.name,
            "symbol": loaded.symbol,
            "entry_point": loaded.entry_point,
            "timeframe": loaded.timeframe.value,
            "warmup": warmup,
        }

        all_bars = message["bars"]
        call = instance.on_bar if loaded.entry_point == "on_bar" else None

        def _one(wire):
            bar = _bar_from_wire(wire)
            return call(bar) if call else instance.on_tick(bar.close)

        # 暖身那幾根的訊號全部丟掉：它們在這個實例存在之前就收盤了。這是
        # market_loop 的規則，回測照抄——兩邊如果不一樣，回測評的就是一支跟實際
        # 跑起來不同時機開始發訊號的策略。
        tested = all_bars[warmup:]
        if warmup and tested:
            try:
                for wire in all_bars[:warmup]:
                    _one(wire)
            except Exception as exc:  # noqa: BLE001
                # -1 是「暖身就爆了」。跟回放中途爆掉分開，因為使用者看到的訊息
                # 不一樣：暖身沒有「哪一根」可以指。
                return {**described, "signals": [], "failed_at": -1, "error": f"{exc}"}

        signals = []
        for index, wire in enumerate(tested):
            try:
                signals.append(str(_one(wire)))
            except Exception as exc:  # noqa: BLE001
                # 是**哪一根**爆的要說出來。例外送不過 JSON 管線，所以這裡只回索
                # 引和文字，訊息由父行程重建——它手上才有那根 K 棒的時間。
                return {**described, "signals": signals, "failed_at": index, "error": f"{exc}"}
        return {**described, "signals": signals, "failed_at": None}

    if command == "validate":
        # compile ＋ 試跑，**一次往返**。
        #
        # 拆成兩次的話，中間那段時間父行程手上會有一個「編好了但還沒跑」的狀態，
        # 而逾時可能落在那個縫裡——那時候該不該殺行程沒有答案。一次往返就沒有那
        # 個縫：不是整件事做完，就是整個行程被殺掉。
        from app.services.market_data.base import bars_from_closes

        loaded = _sandbox()(message["source_code"])
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
        _sandbox()

        return {}

    if command == "load":
        loaded = _sandbox()(message["source_code"], params=message.get("params") or {})
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
    global _limits
    # 在回報 ready **之前**設下去，所以父行程一收到 ready，這個行程就已經被限制
    # 住了。晚一步的話，中間那個縫裡跑的第一支策略是沒有上限的。
    limit_mb = int(os.environ.get("STRATEGY_MEMORY_LIMIT_MB") or 0)
    if limit_mb > 0:
        _limits = apply_limits(limit_mb * 1024 * 1024)

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
