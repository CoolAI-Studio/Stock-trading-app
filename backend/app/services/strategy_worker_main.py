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
