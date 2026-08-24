"""一格要貼兩個值的時候，標題要說兩個。

實際走過一遍全空部署時看到的：設定頁上那一項的標題是

    VAPID_PUBLIC_KEY

而內文說「按下面的『產生』會一次給你完整的一對，**兩個值都要貼回**」。標題只有一
個名字，讀的人照著標題走就會只貼一個——而只貼一半的下場是「每一則推播都失敗」，
那正是這一項下一個分支在講的事。

`name` 不能直接改成兩個名字：它同時是識別碼（畫面用它當 key，CORS_ORIGINS 那一格
靠它做特判），而且它就是環境變數在平台上的欄位名。所以多一個欄位說「這一格還要
一起貼哪幾個」，標題把它們一起顯示出來。
"""

import pytest

from app.config import Settings
from app.services import setup_state


@pytest.fixture(autouse=True)
def _no_boot_error(monkeypatch):
    monkeypatch.delenv("DATABASE_MIGRATION_ERROR", raising=False)


def _item(name: str, s: Settings):
    for item in setup_state.missing_settings(s):
        if item.name == name:
            return item
    return None


def _blank_vapid(monkeypatch) -> Settings:
    for name in ("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY"):
        monkeypatch.delenv(name, raising=False)
    return Settings(VAPID_PUBLIC_KEY="", VAPID_PRIVATE_KEY="")


def test_the_push_key_item_names_both_halves(monkeypatch):
    item = _item("VAPID_PUBLIC_KEY", _blank_vapid(monkeypatch))

    assert item is not None
    assert "VAPID_PRIVATE_KEY" in item.also, "標題只說了一半，而漏掉另一半會讓每一則推播都失敗"


def test_a_setting_that_needs_one_value_does_not_invent_a_second(monkeypatch):
    """多數格子就是一個值。多一個空欄位不該讓它們看起來像成對的。"""
    item = _item("SECRET_ENCRYPTION_KEY", Settings(SECRET_ENCRYPTION_KEY=""))

    assert item is not None
    assert item.also == ()
