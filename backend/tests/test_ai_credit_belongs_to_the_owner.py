"""AI 的錢是部署者的，不是「任何一個登入的人」的。

`services/ai_settings.py` 的 `_is_deployment_owner()` 閘門是對的：非擁有者呼叫
`resolve()` 拿到的是空金鑰。問題是有兩支路由根本沒經過 `resolve()`：

    strategies.py         provider = get_ai_provider()
    broker_credentials.py get_ai_provider().ask(payload.message)

兩者連 `db` 都沒注入，所以 `user.id` 從頭到尾沒有參與金鑰的選擇，最後回退到
`settings.AI_API_KEY`——也就是部署者環境變數裡那一把。同一個 codebase 裡
`system.py` 寫的是 `get_ai_provider(ai_settings.resolve(db, user.id))`，對的寫法
和錯的寫法並存，而錯的那個是「什麼都不做」就會得到的預設值。

所以這裡除了把兩支路由改對，也把那個預設拿掉：`get_ai_provider()` 不再接受
「沒有 resolved」這種呼叫。忘記傳的下一個人會拿到一個明確的錯誤，而不是安靜地
花掉別人的錢。
"""

import pytest

from app.services import ai_settings
from app.services.ai_provider import get_ai_provider


def test_forgetting_to_resolve_is_now_an_error_not_a_silent_fallback():
    """這是這一組修正的核心：把「沒傳就用擁有者的金鑰」這個預設拿掉。

    留著它的話，下一支忘記傳的路由會重現同一個 bug，而且一樣不會有人發現——
    它不會壞，只會用錯人的錢。
    """
    with pytest.raises(TypeError):
        get_ai_provider()  # type: ignore[call-arg]


def test_a_second_account_gets_no_key(client, db_session):
    """`resolve()` 對非擁有者回空金鑰，這是既有的閘門，這裡只是釘住它。"""
    from app.models.user import User

    owner = User(email="owner@example.com", hashed_password="x")
    intruder = User(email="intruder@example.com", hashed_password="x")
    db_session.add_all([owner, intruder])
    db_session.commit()
    db_session.refresh(owner)
    db_session.refresh(intruder)

    resolved = ai_settings.resolve(db_session, intruder.id)

    assert not resolved.api_key


def test_the_owner_still_gets_theirs(client, db_session, monkeypatch):
    from app.models.user import User

    monkeypatch.setattr("app.config.settings.AI_API_KEY", "owner-key")
    monkeypatch.setattr("app.config.settings.AI_MODEL", "some/model")
    owner = User(email="owner@example.com", hashed_password="x")
    db_session.add(owner)
    db_session.commit()
    db_session.refresh(owner)

    resolved = ai_settings.resolve(db_session, owner.id)

    assert resolved.api_key == "owner-key"


def test_every_route_that_asks_an_ai_resolves_first():
    """讀原始碼的斷言，而不是打端點：一支忘記傳的路由不會壞，它只會用錯人的錢，
    所以沒有任何一個端點測試會抓到它。這一條抓的是「有沒有人又寫成無參數」。"""
    import pathlib

    routers = pathlib.Path(__file__).resolve().parents[1] / "app" / "api" / "routers"
    offenders = []
    for path in routers.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "get_ai_provider()" in text:
            offenders.append(path.name)

    assert offenders == []
