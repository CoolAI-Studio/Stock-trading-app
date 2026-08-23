"""資料庫這一格要給方案，不是給一段文字。

使用者的話：「render 只是其一的解法不是嗎？你要提供方案給他們選。」

而實測走過一遍之後才看得出這一格為什麼特別：**雲端使用者能做這個選擇的地方只
有這裡。** 在 Render 上、資料庫還是容器裡那個檔案的時候，DATABASE_URL 是擋住的
項目，`is_configured` 為 false，整個 app 在 setup mode——他連帳號都還沒有，進不
到登入之後的設定引導。等他走到那一頁，資料庫**一定已經是 Postgres 了**。

所以「本機還是雲端、雲端要用誰家的」這個決定，發生在登入之前的這一頁，而這一頁
原本只有一段散文：「你需要一個 Postgres 連線字串…免費的例如 Neon、Supabase；要
更穩的可以用付費方案，或是自己架的也可以。」一段話裡塞了四個方案，而讀它的人按
照 CLAUDE.md 的定義**不是工程師**。
"""

import pytest

from app.config import Settings
from app.services import setup_state


@pytest.fixture(autouse=True)
def _no_boot_error(monkeypatch):
    monkeypatch.delenv("DATABASE_MIGRATION_ERROR", raising=False)


def _on_render(monkeypatch):
    monkeypatch.setenv("RENDER", "true")


def _database_item(s: Settings):
    for item in setup_state.missing_settings(s):
        if item.name == "DATABASE_URL":
            return item
    return None


def test_the_cloud_case_offers_named_options_not_one_paragraph(monkeypatch):
    _on_render(monkeypatch)
    item = _database_item(Settings(DATABASE_URL="sqlite:///./x.db"))

    assert item is not None and item.blocking
    assert item.options, "這一格要給得出方案清單，不是只有一段文字"
    labels = [option.label for option in item.options]
    assert any("Neon" in label for label in labels)
    assert any("自己架" in label or "自架" in label for label in labels)


def test_every_option_says_what_it_costs_and_where_to_go(monkeypatch):
    # 「免費的例如 Neon」對一個不寫程式的人少了兩件事：免費到什麼程度，以及點哪裡。
    _on_render(monkeypatch)
    item = _database_item(Settings(DATABASE_URL="sqlite:///./x.db"))

    for option in item.options:
        assert option.detail.strip(), f"{option.label} 沒有說明"
        assert option.url is None or option.url.startswith("https://")


def test_running_it_on_your_own_machine_is_one_of_the_options(monkeypatch):
    """本機不是「還沒設定完」，是一個選項——只是在雲端平台上它會被清空。

    兩種情況都要在清單裡看得到它，差別在說明：在自己機器上是「不用做任何事」，
    在雲端平台上是「這個平台會清空它」。看不到的選項等於不存在，而使用者要的是
    看得到再自己決定。
    """
    _on_render(monkeypatch)
    on_platform = _database_item(Settings(DATABASE_URL="sqlite:///./x.db"))

    local = [o for o in on_platform.options if o.kind == "local"]
    assert local, "雲端平台上也要看得到「跑在自己機器上」這個選項"
    assert "清空" in local[0].detail


def test_on_your_own_machine_the_local_option_is_the_finished_one(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    item = _database_item(Settings(DATABASE_URL="sqlite:///./x.db"))

    assert item is not None and not item.blocking
    local = [o for o in item.options if o.kind == "local"]
    assert local and "不用做任何事" in local[0].detail
