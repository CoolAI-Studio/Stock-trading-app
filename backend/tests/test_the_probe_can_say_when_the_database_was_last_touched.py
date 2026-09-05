"""這個行程上一次真的碰資料庫是什麼時候——不查資料庫也要答得出來。

＊ 為什麼需要它。

2026-09-05 花了整整一輪在猜「還有什麼在敲資料庫」。#98 修好淺層探測、#99 修好
WebSocket 的交易和深層探測，而量出來的結果是：盯盤迴圈 7 分鐘沒碰它，資料庫**還是醒
著的**（閒置 7 分鐘後的第一次查詢 0.97 秒，後續兩次 0.98／1.07——沒有冷啟動）。

到這裡為止能用的工具全是外部推論：延遲差、用量頁上的日增量。兩個都要等、都很鈍，而且
分不出「是我們的流量」還是「是別人的流量」。

這一格分得出來，而且免費：

    數字一直很小        → 這個行程自己在敲（瀏覽器開著的分頁、重連中的 socket）
    一路長到接近 1800   → 不是我們，那就是 Neon 自己或別的客戶端

＊ 為什麼放在沒帶參數的那一條上。

因為它必須**不碰資料庫**才問得到——問一次就把答案弄髒了（那一問本身就是一次喚醒）。
資料是引擎層的 `after_cursor_execute` 記下來的，全部在記憶體裡。

＊ 「不知道」不可以顯示成「沒問題」。

行程剛起來、一句 SQL 都還沒送的時候，這一格是 `null` 而不是 0——0 會被讀成「剛剛才碰
過」，那是這個 repo 一路在守的同一條規則。
"""

import pytest
from sqlalchemy import text

from app.services import db_activity


@pytest.fixture
def fresh_recorder(monkeypatch):
    """一個乾淨的紀錄器，時鐘由測試自己推。"""
    clock = _Clock()
    recorder = db_activity.DatabaseActivity(clock=clock)
    monkeypatch.setattr(db_activity, "activity", recorder)
    return recorder, clock


def test_a_process_that_has_not_touched_it_says_so_rather_than_zero(fresh_recorder):
    """剛起來的行程回 None，不是 0。

    0 會被讀成「剛剛才碰過」，剛好是相反的意思。
    """
    recorder, _ = fresh_recorder

    assert recorder.last_statement_age_sec is None
    assert recorder.statements == 0


def test_it_notices_a_real_statement(fresh_recorder, db_session):
    """真的送出去的 SQL 才算數。

    數在引擎那一層（`after_cursor_execute`），也就是資料庫真的被碰到的那一刻——不是
    「哪個函式被呼叫了」。
    """
    recorder, clock = fresh_recorder
    db_activity.watch(db_session.get_bind())

    db_session.execute(text("SELECT 1"))
    clock.now += 30

    assert recorder.statements >= 1
    assert recorder.last_statement_age_sec == pytest.approx(30, abs=1)


def test_the_probe_reports_it_without_asking_the_database(client, counted, fresh_recorder):
    """讀這一格不可以自己再碰一次資料庫——那就把要量的東西弄髒了。"""
    counted.clear()

    body = client.get("/healthz").json()

    assert counted == [], f"讀活動紀錄的時候送出了 SQL：{counted}"
    assert "last_sql_age_sec" in body["checks"]["database"]
    assert "statements" in body["checks"]["database"]


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now
