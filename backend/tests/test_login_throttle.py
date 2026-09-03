import time

import pytest

from app.core import login_throttle


@pytest.fixture(autouse=True)
def _clean_throttle_state():
    login_throttle.reset_all()


def test_failures_below_the_threshold_do_not_lock():
    for _ in range(2):
        login_throttle.register_failure("a@example.com", max_attempts=3, lockout_seconds=60)
    assert login_throttle.seconds_until_unlocked("a@example.com") == 0


def test_reaching_the_threshold_locks():
    for _ in range(3):
        login_throttle.register_failure("a@example.com", max_attempts=3, lockout_seconds=60)
    assert login_throttle.seconds_until_unlocked("a@example.com") > 0


def test_lockout_is_per_key():
    for _ in range(3):
        login_throttle.register_failure("a@example.com", max_attempts=3, lockout_seconds=60)
    assert login_throttle.seconds_until_unlocked("b@example.com") == 0


def test_expired_lockout_no_longer_blocks():
    for _ in range(3):
        login_throttle.register_failure("a@example.com", max_attempts=3, lockout_seconds=-1)
    assert login_throttle.seconds_until_unlocked("a@example.com") == 0


def test_clear_resets_the_counter():
    for _ in range(2):
        login_throttle.register_failure("a@example.com", max_attempts=3, lockout_seconds=60)
    login_throttle.clear("a@example.com")

    for _ in range(2):
        login_throttle.register_failure("a@example.com", max_attempts=3, lockout_seconds=60)
    assert login_throttle.seconds_until_unlocked("a@example.com") == 0


def test_old_failures_decay_instead_of_accumulating_forever():
    login_throttle.register_failure("a@example.com", max_attempts=2, lockout_seconds=0.05)
    time.sleep(0.06)
    login_throttle.register_failure("a@example.com", max_attempts=2, lockout_seconds=0.05)
    assert login_throttle.seconds_until_unlocked("a@example.com") == 0


# --- 這張表的大小是外面的人決定的 ---------------------------------------------
#
# 節流的鍵是 `form_data.username.strip().lower()`（auth.py），也就是**任何人送得進來
# 的一段字串**——登入端點本來就必須是公開的。每一個沒見過的字串就多一筆，而移除只有
# 兩條路：登入成功（clear）和測試（reset_all）。
#
# 所以一個不需要任何憑證的人，可以用不重複的帳號名把這個行程的記憶體撐大。免費方案
# 是 512 MB，而策略池自己就佔 60 MB；行程被 OOM 殺掉的意思是**每一則提醒都停了**，
# 那是這個產品唯一不能發生的事。
#
# 上限的取捨要講清楚：淘汰的時候不可以讓「正在被鎖住」的那一筆被洗掉，否則這道上限
# 本身就變成解鎖的方法，比沒有上限更糟。

_OVERFLOW = login_throttle.MAX_TRACKED_KEYS + 2_000


def test_the_table_does_not_grow_without_a_ceiling():
    """不重複的帳號名撐不大這個行程。"""
    for i in range(_OVERFLOW):
        login_throttle.register_failure(
            f"flood-{i}@example.com", max_attempts=5, lockout_seconds=60
        )

    assert login_throttle.tracked_keys() <= login_throttle.MAX_TRACKED_KEYS


def test_a_flood_cannot_wash_out_a_lockout_that_is_still_in_force():
    """上限不可以變成解鎖的方法。

    如果淘汰只看「誰最舊」，攻擊者被鎖住之後只要再送幾萬個不重複的帳號名，就能把自己
    那一筆擠掉然後重新開始猜——那樣這道上限反而讓事情變糟。所以還在鎖的最後才丟。
    """
    for _ in range(5):
        login_throttle.register_failure("victim@example.com", max_attempts=5, lockout_seconds=600)
    assert login_throttle.seconds_until_unlocked("victim@example.com") > 0

    for i in range(_OVERFLOW):
        login_throttle.register_failure(
            f"flood-{i}@example.com", max_attempts=5, lockout_seconds=600
        )

    assert login_throttle.seconds_until_unlocked("victim@example.com") > 0, (
        "被鎖住的那一筆被沖掉了：只要再灌一批不重複的帳號名就能解鎖，上限反而變成後門。"
    )


def test_the_ceiling_drops_the_entries_that_mean_the_least_first():
    """先丟證據最少的那些。

    一筆「失敗過四次」比一筆「失敗過一次」值錢：前者再錯一次就要鎖了。淘汰只看時間的
    話，一個接近上鎖的攻擊者只要換著名字灌一陣子，就能把自己那筆快滿的計數洗掉。
    """
    for _ in range(4):
        login_throttle.register_failure("almost@example.com", max_attempts=5, lockout_seconds=600)

    for i in range(_OVERFLOW):
        login_throttle.register_failure(
            f"flood-{i}@example.com", max_attempts=5, lockout_seconds=600
        )

    login_throttle.register_failure("almost@example.com", max_attempts=5, lockout_seconds=600)
    assert login_throttle.seconds_until_unlocked("almost@example.com") > 0, (
        "累積了四次的那一筆被沖掉了，所以第五次又從頭算起——鎖永遠不會關上。"
    )
