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


# 協定管線的真身。
#
# 這條一行一個 JSON 的管線就是這個行程的 stdout ／ stdin，而**使用者的策略也寫得到
# 它**：沙箱的安全 builtins 白名單裡有 print。策略印一行字，父行程讀到的那一行就不是
# JSON，_read_line 判成 WorkerUnavailable 並殺掉整個子行程——同一格上其他策略累積的暖
# 身狀態一起歸零，而 WorkerUnavailable 被歸類成基礎設施問題（不累積、不停用），所以這
# 件事每五秒重演一次。
#
# 不把 print 從白名單拿掉：那又是一次列舉式的拒絕（strategy_runtime 的檔頭自己就把兩
# 層叫做 "denial by enumeration"，而這個缺口存在的原因正是那份列舉漏了 print 一個名
# 字），而且會讓一支 print 過的策略變成 NameError——使用者的程式碼沒有錯，錯的是我們把
# 協定放在他寫得到的地方。所以換掉問題的形狀，讓路的盡頭沒有管線。
_pipe = sys.stdout
_commands = sys.stdin


def _take_the_protocol_pipe() -> None:
    """把協定的兩端從 sys.stdout ／ sys.stdin 手上接管過來。

    **要在回報 ready 之前做完**：ready 之後的下一行就可能是一支會 print 的策略，而
    「編譯就是執行」——類別主體和 __init__ 也是使用者的程式碼。

    stdout 交出去之後指到 stderr，而父行程把 stderr 接到 DEVNULL（見
    strategy_worker.py 的 Popen），所以策略印再多都只是掉進地上的洞：不阻塞、不漲記憶
    體、更不會撞壞協定。指到 stderr 而不是丟進 os.devnull，是因為手動跑這個模組除錯的
    時候那些字還看得到。

    stdin 一併接管，因為協定有兩端：只接管 stdout 的話，「路的盡頭沒有管線」只成立一
    半，另一半仍然靠列舉式的拒絕撐著。input 和 open 今天兩層都擋（AST 黑名單 ＋ 不在
    安全 builtins 裡），所以這不是現行漏洞；但將來萬一 input 又回到白名單，它拿到的會
    是 RuntimeError("input(): lost sys.stdin")——一個乾淨的策略錯誤，而不是協定被吃掉一
    行指令（那個症狀比 print 更難查，因為壞掉的是父行程剛送出去的那個指令，看起來像子
    行程無故沉默）。

    這一句不可以拋，跟 apply_limits 同一條規則：子行程起不來等於那一格上的策略全面停
    擺，比一支策略印不出東西糟得多。所以連 stderr 都沒有（被關掉的執行環境）也不用另外
    處理——sys.stdout 變成 None 的時候 print 是一個安靜的 no-op，那正是要的結果。
    """
    global _pipe, _commands
    if sys.stdout is sys.stderr:
        # 已經接管過了。再做一次會讓 _pipe 指到 stderr，協定整條寫進 DEVNULL——子行程
        # 從此一句話都不回，而那是這張票要修掉的全面停擺換了個入口。
        return
    _pipe = sys.stdout
    _commands = sys.stdin
    sys.stdout = sys.stderr
    sys.stdin = None


def _reply(ok: bool, **fields) -> None:
    """回一行。

    每一個回覆都要送得出去，包括「我失敗了」——父行程在等一行，讀不到就會把這裡當
    成死掉並殺掉重建，而那要花三百毫秒。一個講得出原因的失敗便宜得多。

    寫的是 _pipe 不是 sys.stdout：那條管線已經在 main() 裡被接管走了，因為使用者的策略
    print 得到 sys.stdout。
    """
    _pipe.write(json.dumps({"ok": ok, **fields}) + "\n")
    _pipe.flush()


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

    if command == "sweep":
        # 一整個參數網格，**一次往返**。
        #
        # K 棒送一次，所有組共用。這不只是省頻寬：每組各送一次的話，就得有人保證
        # 那幾份是同一批，而「保證兩份資料一樣」是一個沒有人會去檢查的條件。送一
        # 次就沒有那個條件——它們是同一個 list。
        from app.services.strategy_runtime import effective_warmup

        compile_strategy = _sandbox()
        bars = [_bar_from_wire(wire) for wire in message["bars"]]

        def _emit(row):
            """跑完一組就送回去，不等整個網格跑完。

            **一組卡死不可以毀掉整張表。** 一次往返在延遲上是對的（K 棒只序列化一
            次），但如果連結果也等到最後才送，那整個網格就共用一個期限——而使用者
            送掃描的原因，往往正是他還不知道哪組參數是合理的，所以裡面有一組跑不
            完是**預期中**的事，不是例外。

            邊跑邊回讓卡住只損失還沒跑的那幾組。父行程收到多少算多少。
            """
            _reply(True, row=row)

        for params in message["param_sets"]:
            try:
                loaded = compile_strategy(message["source_code"], params=params)
            except Exception as exc:  # noqa: BLE001
                _emit({"params": params, "error": f"{type(exc).__name__}: {exc}"})
                continue

            # warmup_override 是**呼叫端自己畫的線**，用在滾動前進上。
            #
            # 平常由 effective_warmup 決定，而它的規則是「原始碼宣告的贏」——那對
            # 單次回測是對的（暖身根數是指標的性質，作者最清楚）。但滾動前進要的
            # 是「被測的剛好是那一段，不多不少」：一支宣告 warmup_bars = 0 的策
            # 略會讓那幾根本該當暖身的 K 棒被算進成績，而它們來自訓練期——分數就
            # 混進了樣本內的資料，看起來卻完全正常。
            override = message.get("warmup_override")
            if override is not None:
                warmup = int(override)
            elif loaded.entry_point == "on_bar":
                warmup = effective_warmup(loaded, message["stored_warmup_bars"])
            else:
                warmup = 0

            instance = loaded.instance
            call = instance.on_bar if loaded.entry_point == "on_bar" else None

            def _one(bar, call=call, instance=instance):
                return call(bar) if call else instance.on_tick(bar.close)

            try:
                for bar in bars[:warmup]:
                    _one(bar)
                signals = [str(_one(bar)) for bar in bars[warmup:]]
            except Exception as exc:  # noqa: BLE001
                # 一組壞掉只壞那一組。掃描是 N 倍的曝險：跑一次的時候一支壞策略
                # 只毀掉一次回測，掃描的時候它會毀掉整張表——而使用者送掃描的原
                # 因，往往正是他還不知道哪組參數是合理的。
                _emit({"params": params, "error": f"{type(exc).__name__}: {exc}"})
                continue

            _emit(
                {
                    "params": params,
                    "error": None,
                    "warmup": warmup,
                    "signals": signals,
                    "name": loaded.name,
                    "symbol": loaded.symbol,
                    "entry_point": loaded.entry_point,
                    "timeframe": loaded.timeframe.value,
                }
            )

        return {"done": True}

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
    # 第一件事，比上限還早：使用者的策略跟這條協定管線共用一個 stdout，而 ready 之後
    # 的下一行就可能是一支會 print 的策略。見 _take_the_protocol_pipe。
    _take_the_protocol_pipe()
    # 在回報 ready **之前**設下去，所以父行程一收到 ready，這個行程就已經被限制
    # 住了。晚一步的話，中間那個縫裡跑的第一支策略是沒有上限的。
    limit_mb = int(os.environ.get("STRATEGY_MEMORY_LIMIT_MB") or 0)
    if limit_mb > 0:
        _limits = apply_limits(limit_mb * 1024 * 1024)

    _reply(True, ready=True)

    for line in _commands:
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
