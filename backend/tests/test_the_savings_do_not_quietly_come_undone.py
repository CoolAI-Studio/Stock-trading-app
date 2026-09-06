"""省下來的那些喚醒，不可以因為別的地方壞掉就靜靜地還回去。

#98 到 #101 花了三輪才讓免費方案的資料庫真的睡得著（量出來：每分鐘 15.4 句 SQL → 12 分
鐘 0 句，閒置後第一次查詢從 0.97 秒變成 2.43 秒，也就是真的付了一次冷啟動）。

那三輪的成果全部靠同一個機制撐著：**盯盤迴圈在它自己那一輪裡把答案算好，探測只讀記憶
體。** 而這個檔案守的是那個機制的兩條退路——兩條都不會讓任何測試變紅，只會讓用量在下個
月中把資料庫關掉。
"""

import pytest

from app.services import market_loop, worker_health
from app.services.notification import retry as notification_retry


def test_a_failed_retry_sweep_does_not_stop_the_count(db_session, monkeypatch):
    """重送掃描炸了，那個計數還是要更新。

    ＊ 為什麼這條會發生。

    `retry_pending` 會真的送出通知——它打的是 Telegram、SMTP、推播，也就是這一輪裡最可
    能丟例外的一段。而迴圈刻意把它包在 try 裡（一次重送失敗不該讓盯盤停掉）。

    ＊ 為什麼它一炸就會把三輪的成果還回去。

    `mark_undelivered` 本來寫在同一個 try 的**後面**。前面一炸，這一句就跳過了 →
    心跳裡那個數字愈來愈舊 → 超過 `_max_age` → `/healthz?deep=1` 判定證據過期 → **每一
    次探測又自己去查一次資料庫**，而監控每 5 分鐘打一次，於是運算單元回到永遠不休眠。

    整條路上不會有任何東西變紅：通知照送、健康檢查照綠，只有月底的帳單知道。
    """
    beat = worker_health.WorkerHeartbeat()
    monkeypatch.setattr(worker_health, "heartbeat", beat)

    def _blows_up(_session):
        raise RuntimeError("Telegram 不理我")

    monkeypatch.setattr(notification_retry, "retry_pending", _blows_up)

    market_loop.tick_once(db=db_session)

    assert beat.snapshot().undelivered is not None, (
        "重送掃描失敗就沒有人數「放棄掉的提醒」了——深層探測會退回每次自己查資料庫"
    )


def test_the_count_is_still_taken_when_everything_works(db_session, monkeypatch):
    """正常那條路也要真的數到，否則上面那條是自己跟自己比。"""
    beat = worker_health.WorkerHeartbeat()
    monkeypatch.setattr(worker_health, "heartbeat", beat)

    market_loop.tick_once(db=db_session)

    assert beat.snapshot().undelivered == 0


@pytest.mark.anyio
async def test_a_socket_whose_snapshot_fails_is_not_left_registered(monkeypatch):
    """初始快照查不到東西的時候，那條連線不可以留在推播名單上。

    ＊ 為什麼這條現在才變得會發生。

    因為資料庫**這下真的會睡了**（#99、#101）。閒置之後的第一次查詢要付一次冷啟動——量
    到的是 2.43 秒——而在那段時間裡連線逾時、連線被對面關掉都是真的會遇到的。

    快照那一句原本寫在 try 的**外面**，所以它一丟例外：socket 已經 accept、已經登記進
    `manager`，而 `finally` 裡的 disconnect 收不到。留下的是一筆指向死掉 socket 的登
    記，之後每一次推播都會試著送給它。

    這一條問的是行為（「名單上還有沒有它」），不是實作。
    """
    from app.ws import routes
    from app.ws.manager import ConnectionManager

    manager = ConnectionManager()
    monkeypatch.setattr(routes, "manager", manager)
    monkeypatch.setattr(routes, "redeem_ticket", lambda _ticket: 7)

    def _database_is_waking_up(*_args, **_kwargs):
        raise RuntimeError("connection timed out")

    monkeypatch.setattr(routes, "_initial_snapshot", _database_is_waking_up)

    socket = _FakeSocket()
    with pytest.raises(RuntimeError):
        await routes.ws_endpoint(socket, ticket="anything", db=None)

    assert manager.count_for(7) == 0, "快照失敗之後，死掉的 socket 還留在推播名單上"


class _FakeSocket:
    """只回答 ws_endpoint 在快照失敗之前會用到的那幾件事。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def accept(self) -> None:
        return None

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, code: int | None = None) -> None:
        return None
