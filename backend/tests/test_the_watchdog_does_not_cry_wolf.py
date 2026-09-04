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


def test_the_health_window_clears_whatever_the_loop_is_sleeping():
    """門檻要蓋得過迴圈自己剛剛決定要睡的那一段，**不論那是多長**。

    原本這裡比的是一個固定的常數，而那條不變式只在「輪詢間隔不會變」的前提下成立。
    間隔實際上差兩百倍（開市 5 秒、關市半小時），所以固定門檻只能對其中一邊是對的
    ——訂大了盤中反應慢半小時，訂小了每天半夜亂叫。現在門檻跟著迴圈走。
    """
    from app.api.routers.health import _max_age

    for gap in (
        settings.MARKET_DATA_POLL_INTERVAL_SEC,
        market_loop.CLOSED_POLL_INTERVAL_SEC,
        market_loop.CLOSED_POLL_INTERVAL_SEC * 10,  # 以後再放慢也要成立
    ):
        assert _max_age(gap) > gap + settings.HEALTH_TICK_BUDGET_SEC - 1


def test_the_window_does_not_go_slack_while_the_market_is_open():
    """盤中不可以跟著關市那一段一起放寬。

    放寬的代價是「一支卡死的迴圈要多久才被發現」，而盤中正是那件事最貴的時候：停損
    穿過去、訊號沒發出，每一秒都算數。
    """
    from app.api.routers.health import _max_age

    open_window = _max_age(settings.MARKET_DATA_POLL_INTERVAL_SEC)

    assert open_window <= settings.HEALTH_MAX_AGE_SEC


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

    # 迴圈在睡之前會說一聲（run_forever 裡的 expect_next_within），健康檢查就是照那
    # 個數字放寬門檻的。不說的話這裡量到的是「剛開機的預設門檻」，那不是關市的樣子。
    worker_health.heartbeat.expect_next_within(market_loop.CLOSED_POLL_INTERVAL_SEC)
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

    worker_health.heartbeat.expect_next_within(market_loop.CLOSED_POLL_INTERVAL_SEC)
    clock.advance((market_loop.CLOSED_POLL_INTERVAL_SEC + settings.HEALTH_MAX_AGE_SEC) * 3)

    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json()["checks"]["worker"]["status"] == "fail"


def test_a_loop_that_wedges_during_the_session_is_caught_quickly(client, monkeypatch):
    """而盤中卡死要在幾分鐘內被抓到，不是半小時。

    這正是「門檻跟著迴圈走」買回來的東西。門檻寫死成蓋得過關市那一段的話，盤中一支
    卡死的迴圈要半小時才被發現——停損穿過去、訊號沒發出，而每一項探測都是綠的。
    """
    monkeypatch.setattr("app.config.settings.WORKER_ENABLED", True)
    monkeypatch.setattr("app.config.settings.NOTIFICATIONS_ENABLED", True)
    clock = _FakeClock()
    monkeypatch.setattr(worker_health, "heartbeat", worker_health.WorkerHeartbeat(clock=clock))
    worker_health.heartbeat.mark_loop()
    worker_health.heartbeat.mark_poll_success()

    # 開市：迴圈說它幾秒後就回來。
    worker_health.heartbeat.expect_next_within(settings.MARKET_DATA_POLL_INTERVAL_SEC)
    clock.advance(settings.HEALTH_MAX_AGE_SEC + 1)

    assert client.get("/healthz").status_code == 503
