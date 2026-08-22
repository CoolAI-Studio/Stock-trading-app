"""這台後端不把自己的端點清單交給路過的人。

WHAT WAS MEASURED. On the live deployment:

    GET https://<後端>/openapi.json  -> 200
    GET https://<後端>/docs          -> 200

那是 FastAPI 內建的，沒有人打開它，它自己就在那裡。裡面沒有任何一筆使用者資料
——所以這不是資料外洩——但它是一份完整的地圖：82 個操作、每一個的參數、每一個
回應的結構，交給任何一個知道後端網址的人。而那個網址不是秘密：前端每一個請求
都帶著它。

擁有者不需要這份地圖（他不是工程師，也不會打開 /docs），所以它現在唯一的讀者
就是想知道這台機器有哪些端點的人。關掉。

BUT NOT AT THE COST OF A BLANK ON THE DEPLOY FORM. Local development does want
the docs page, so there is a flag -- and the flag is deliberately absent from
render.yaml, because CLAUDE.md's rule about the setup form is that every extra
box is a place a non-engineer stops. A developer who wants /docs sets it in
their own .env; nobody deploying a copy ever sees it.
"""

import pathlib

import pytest

from app.config import Settings
from app.main import docs_urls


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
def test_the_map_is_not_served_to_anybody(client, path):
    """404, not 401: 「這裡沒有這個東西」 tells a stranger less than
    「這裡有這個東西，但你不能看」, and FastAPI's own way of switching these
    off produces exactly that."""
    assert client.get(path).status_code == 404


def test_the_app_itself_is_unaffected(client, monkeypatch):
    """Turning the schema route off must not turn the API off with it.

    Notifications back on first: conftest mutes them for every test, and
    /healthz calls a muted notifier a failure on purpose (a system that sends
    no alerts is not healthy for this product), so a 503 here would be that
    rather than anything to do with the schema."""
    monkeypatch.setattr("app.config.settings.NOTIFICATIONS_ENABLED", True)

    assert client.get("/healthz").status_code == 200


def test_a_developer_can_switch_them_back_on():
    """Local work wants the docs page. The flag exists for that and only that."""
    urls = docs_urls(Settings(ENABLE_API_DOCS=True))

    assert urls["openapi_url"] == "/openapi.json"
    assert urls["docs_url"] == "/docs"
    assert urls["redoc_url"] == "/redoc"


def test_and_off_is_what_a_deployment_gets_without_asking():
    """Default off, because the person deploying a copy will never set it and
    should never have to think about it."""
    urls = docs_urls(Settings())

    assert urls == {"openapi_url": None, "docs_url": None, "redoc_url": None}


def test_the_flag_is_not_on_the_deploy_form():
    """A flag that lands in render.yaml becomes an eighth blank on the form
    somebody is filling in for the first time -- and CLAUDE.md is explicit that
    each of those is a place they stop. This one is for developers, so it
    stays out of the blueprint."""
    blueprint = pathlib.Path(__file__).resolve().parents[2] / "render.yaml"

    assert "ENABLE_API_DOCS" not in blueprint.read_text(encoding="utf-8")
