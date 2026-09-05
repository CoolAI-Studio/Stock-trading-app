"""打開的分頁不可以讓資料庫永遠不休眠。

Neon 免費方案的規則（官方文件 compute-lifecycle）：閒置 5 分鐘就把運算單元收起來，
**而且會把連線關掉**——所以單純掛在那裡的連線不是問題。問題是它明講的那個例外：

    It treats an "idle-in-transaction" connection as active to avoid breaking
    application logic that involves long-running transactions.

卡在交易裡的連線會被當成「還在用」，那顆運算單元就**永遠不休眠**。而免費方案一個月
只有 100 CU-hours（官方換算：0.25 CU 跑 400 小時），永遠醒著是 730 小時——大約月中就
用完，接下來到下一個帳單週期為止，資料庫是停的。

**症狀是每個月後半一則提醒都不會送出**，而畫面上不會有任何東西變紅：策略還寫著啟用
中，只是沒有東西在跑。這是這個產品最不能發生的那件事。

WebSocket 是這個 app 裡唯一長命的請求。它用 `Depends(get_db)` 拿 session，而 FastAPI
的依賴要等到**連線結束**才收——所以查完初始快照之後，那條交易就一路開著，開多久取決
於使用者的分頁開多久。一個下午沒關的分頁 = 一整個下午不休眠。

量出來的（2026-09-05 16:00，台股 13:30 已收、盯盤迴圈 15 分鐘沒碰過資料庫）：
深層探測 0.95 秒 vs 淺層 0.79 秒，差 0.15 秒。冷啟動要多花半秒以上，所以那一刻資料庫
是醒著的——不該醒的時候醒著。
"""


def _ticket(auth_client) -> str:
    resp = auth_client.post("/api/ws/ticket")
    assert resp.status_code == 200, resp.text
    return resp.json()["ticket"]


def test_an_open_socket_leaves_no_transaction_open(auth_client, db_session):
    """連著的 socket 不可以坐在一條開著的交易上面。

    問的是行為（「這條連線在 Neon 眼中算不算還在用」）而不是實作——`in_transaction()`
    正是 `pg_stat_activity` 會把它報成 `idle in transaction` 的那個狀態。
    """
    ticket = _ticket(auth_client)

    # 前提：現在沒有交易開著。沒有這一句，測試會被前面註冊／登入留下的交易汙染，
    # 於是不管有沒有修好都紅，變成一條看不出原因的測試。
    db_session.close()
    assert not db_session.in_transaction()

    with auth_client.websocket_connect(f"/ws?ticket={ticket}") as ws:
        assert ws.receive_json()["type"] == "snapshot"

        assert not db_session.in_transaction(), (
            "socket 還開著的時候有一條交易也開著——Neon 會把它當成『還在用』而永遠不"
            "休眠，一個月的運算額度大約月中就用完，之後不會再有任何提醒送出"
        )


def test_the_snapshot_still_carries_what_it_owes(auth_client):
    """省連線不可以省掉那份快照。

    快照是「剛連上的畫面不要空白」唯一的來源；把 session 收掉的時候順手把查詢也弄丟
    了的話，症狀是畫面一片空白而沒有任何錯誤。
    """
    created = auth_client.post(
        "/api/orders", json={"symbol": "2330.TW", "side": "buy", "quantity": "1"}
    )
    assert created.status_code == 201, created.text

    with auth_client.websocket_connect(f"/ws?ticket={_ticket(auth_client)}") as ws:
        data = ws.receive_json()["data"]

    assert [o["symbol"] for o in data["pending_orders"]] == ["2330.TW"]
