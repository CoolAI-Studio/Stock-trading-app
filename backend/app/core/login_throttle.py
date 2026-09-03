import time
from dataclasses import dataclass

# In-memory and process-local, like app/ws/tickets.py -- fine because the app
# already has to run with `--workers 1` (app/services/market_loop.py), so there
# is exactly one process holding this state and no extra dependency (Redis, a
# DB table) is needed for it to be correct.
#
# The trade-off: a restart forgets every counter and lockout. On Render's free
# tier that happens on each deploy and after an idle sleep, so a patient
# attacker could get a fresh set of attempts by waiting for one. That is still
# orders of magnitude away from an unthrottled brute force, and an attacker
# cannot cause a restart -- persisting counters would mean a DB write per failed
# login, which is not worth it for a single-user dashboard.
_attempts: dict[str, "_Failures"] = {}

# **這張表的鍵是任何人送得進來的字串。**
#
# auth.py 用 `form_data.username.strip().lower()` 當鍵，而登入端點本來就必須是公開的。
# 每一個沒見過的名字就多一筆，而移除原本只有兩條路：登入成功（clear）和測試
# （reset_all）。所以一個不需要任何憑證的人，可以用不重複的帳號名把這個行程撐大。
#
# 免費方案是 512 MB，策略池自己就佔 60 MB。行程被 OOM 殺掉的意思是**每一則提醒都停
# 了**——那是這個產品唯一不能發生的事，比「暴力破解慢一點」重要得多。
MAX_TRACKED_KEYS = 10_000

# 撞到上限就一次清到這裡，不是每多一筆就清一筆：清理要排序，而每一次登入失敗都排一次
# 一萬筆會讓這條路自己變成攻擊面。這樣一千次插入才付一次排序的錢。
_PRUNE_TO = MAX_TRACKED_KEYS * 9 // 10


def tracked_keys() -> int:
    """現在記著幾個鍵。給測試和狀態頁看的。"""
    return len(_attempts)


def _prune(now: float, lockout_seconds: float) -> None:
    """把表壓回上限以下。

    **順序是這個函式的全部重點。** 淘汰如果只看「誰最舊」，這道上限就變成解鎖的方法：
    被鎖住的人再灌幾萬個不重複的帳號名，就能把自己那一筆擠掉。那比沒有上限更糟——沒有
    上限至少不會幫攻擊者開門。

    所以照「這一筆值多少」排，最不值錢的先丟：

      1. 已經衰減完、而且沒有在鎖的——它們本來就不代表任何事了
      2. 沒有在鎖的，計數低的先（一筆「失敗過四次」比一筆「失敗過一次」值錢：前者再錯
         一次就要鎖了，洗掉它等於讓鎖永遠關不上）
      3. 還在鎖的最後才丟，一樣是計數低的、舊的先

    第 3 步理論上還是可能丟掉一個生效中的鎖，但要走到那裡，攻擊者得先把一萬個鍵各弄到
    上鎖（五萬次請求），只為了省下等十五分鐘。
    """
    if len(_attempts) <= MAX_TRACKED_KEYS:
        return

    for key in [
        key
        for key, entry in _attempts.items()
        if entry.locked_until <= now and now - entry.last_at > lockout_seconds
    ]:
        del _attempts[key]
    if len(_attempts) <= MAX_TRACKED_KEYS:
        return

    ranked = sorted(
        _attempts.items(),
        key=lambda item: (item[1].locked_until > now, item[1].count, item[1].last_at),
    )
    for key, _ in ranked[: len(_attempts) - _PRUNE_TO]:
        del _attempts[key]


@dataclass
class _Failures:
    count: int
    last_at: float
    locked_until: float


def seconds_until_unlocked(key: str) -> float:
    """Remaining lockout in seconds, or 0.0 when `key` may attempt a login."""
    entry = _attempts.get(key)
    if entry is None:
        return 0.0
    remaining = entry.locked_until - time.monotonic()
    return remaining if remaining > 0 else 0.0


def register_failure(key: str, *, max_attempts: int, lockout_seconds: float) -> None:
    """Count one failed login and lock `key` out once it hits `max_attempts`."""
    now = time.monotonic()
    _prune(now, lockout_seconds)
    entry = _attempts.get(key)
    # Failures decay over the lockout duration so that a handful of typos spread
    # across months never adds up to a lockout; only a burst does.
    if entry is None or now - entry.last_at > lockout_seconds:
        entry = _Failures(count=0, last_at=now, locked_until=0.0)
        _attempts[key] = entry

    entry.count += 1
    entry.last_at = now
    if entry.count >= max_attempts:
        entry.locked_until = now + lockout_seconds


def clear(key: str) -> None:
    """Forget every failure for `key` -- called after a successful login."""
    _attempts.pop(key, None)


def reset_all() -> None:
    """Drop all state. Only used by tests, which share this module-level dict."""
    _attempts.clear()
