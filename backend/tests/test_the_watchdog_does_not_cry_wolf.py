"""半夜不要亂叫。

MEASURED: `HEALTH_MAX_AGE_SEC` 是 300 秒，而市場關著的時候 `next_poll_delay()`
回的 `CLOSED_POLL_INTERVAL_SEC` 也是 300 秒。`run_forever` 的順序是

    mark_loop → 跑一輪 tick（耗時 T）→ 睡 300 秒

所以 `last_loop_age_sec` 的最大值是 300 + T，而判斷式是 `age > 300`。**每一個
循環都有長度 T 的一段時間，探測打進來就是 fail。** 台股使用者從 13:30 到隔天
09:00 都在這個狀態，每天半夜都會收到一封「背景 worker 沒有在跑，提醒不會發出」，
而 worker 好得很。

WHY THIS IS NOT A COSMETIC BUG. 一個每天亂叫的警報器，人會學會忽略它——然後真的
停擺的那一次，那封信長得跟前面三十封一模一樣。這個產品唯一不能失效的東西就是
「事情發生時會有人知道」，而把警報器訓練成噪音，是弄壞它最有效的方法。

兩種寫法各守一半：不變式測試讓「有人調整了其中一個間隔」立刻變紅；行為測試讓
「門檻本身被誰改小」立刻變紅。
"""

from app.config import settings
from app.services import market_loop, worker_health


class _FakeClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- 不變式：門檻必須容得下最長的那一種輪詢，再加一輪 tick ----------------------------


def test_the_health_window_clears_the_closed_market_interval():
    """關市時的輪詢間隔是這兩個裡面大的那一個，也是原本撞在一起的那一個。"""
    floor = market_loop.CLOSED_POLL_INTERVAL_SEC + settings.HEALTH_TICK_BUDGET_SEC

    assert settings.HEALTH_MAX_AGE_SEC > floor


def test_and_the_open_market_one_too():
    floor = settings.MARKET_DATA_POLL_INTERVAL_SEC + settings.HEALTH_TICK_BUDGET_SEC

    assert settings.HEALTH_MAX_AGE_SEC > floor


def test_the_budget_is_big_enough_for_the_slowest_thing_inside_one_tick():
    """一輪 tick 裡最慢的是通知重送掃描，它自己的上限是 20 秒
    （notification/retry.py::_MAX_SWEEP_SEC）。抓報價和 K 棒還要再加上去。"""
    from app.services.notification.retry import _MAX_SWEEP_SEC

    assert settings.HEALTH_TICK_BUDGET_SEC > _MAX_SWEEP_SEC


# --- 行為：關市時的正常循環不能報警 ---------------------------------------------------


def test_a_normal_closed_market_cycle_does_not_page_anybody(client, monkeypatch):
    """心跳停在「一個關市週期 ＋ 一輪 tick」之後——也就是探測最不巧的那一刻。"""
    monkeypatch.setattr("app.config.settings.WORKER_ENABLED", True)
    monkeypatch.setattr("app.config.settings.NOTIFICATIONS_ENABLED", True)
    clock = _FakeClock()
    monkeypatch.setattr(worker_health, "heartbeat", worker_health.WorkerHeartbeat(clock=clock))
    worker_health.heartbeat.mark_loop()
    worker_health.heartbeat.mark_poll_success()

    clock.advance(market_loop.CLOSED_POLL_INTERVAL_SEC + 30.0)

    assert client.get("/healthz").status_code == 200


def test_but_a_worker_that_really_stopped_still_does(client, monkeypatch):
    """把門檻放大到不會誤報，不可以順手把警報器關掉。"""
    monkeypatch.setattr("app.config.settings.WORKER_ENABLED", True)
    monkeypatch.setattr("app.config.settings.NOTIFICATIONS_ENABLED", True)
    clock = _FakeClock()
    monkeypatch.setattr(worker_health, "heartbeat", worker_health.WorkerHeartbeat(clock=clock))
    worker_health.heartbeat.mark_loop()
    worker_health.heartbeat.mark_poll_success()

    clock.advance(settings.HEALTH_MAX_AGE_SEC * 3)

    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json()["checks"]["worker"]["status"] == "fail"
