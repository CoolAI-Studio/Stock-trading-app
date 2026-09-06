"""他貼進 DATABASE_URL 的那一串，長什麼樣子我們控制不了。

`DATABASE_URL` 是整張部署表單上**唯一一格 app 生不出來**的值——只能去別人家的服務複製
貼上（CLAUDE.md：使用者不是工程師，而「請在你的電腦上跑這支腳本」等於流程到此結束）。
所以那一格拿到的東西是各家主控台的「複製」按鈕給什麼、他就貼什麼。

而貼錯的代價**比其他每一格都嚴重**，因為它不是「這個功能壞掉」，是**整個網址是死
的**：

    app/db/session.py 在**模組層**建引擎（`engine = make_engine(settings.DATABASE_URL)`）
    → 建不起來就是 import 期例外
    → uvicorn 起不來
    → 沒有任何一個埠被綁起來

而 `scripts/start.py` 整支的存在理由就是「遷移跑不動也要把服務起起來，好讓設定頁說得出
原因」。那段話再有用，也要行程活著才送得出去。**這條路繞過了它。**

＊ 量出來的（2026-09-06）：

    make_engine("postgres://u:p@h/db")
    → NoSuchModuleError: Can't load plugin: sqlalchemy.dialects:postgres

`postgres://` 不是打錯字，是 SQLAlchemy 1.4 拿掉的舊別名，而**外面到處都還是這個
scheme**：Heroku 的 DATABASE_URL 一直是它，網路上大半教學也是。他照著貼，拿到一個打不
開的網址和零行說明。

＊ 所以這裡修的是「把貼進來的東西修好」，不是「壞掉之後撐住」。

差別是刻意的。**壞掉之後撐住是危險的**：一份已經在跑的部署如果連線字串被改壞，現在的
行為是行程死掉 → 平台的健康檢查不通過 → 部署被判失敗 → **上一版繼續服務**，他的提醒沒
有斷。改成「起得來但連到一個空資料庫」反而會讓流量切過去，變成一份沒有任何資料、而且
worker 不跑的部署。

修好貼進來的字串沒有這個代價：修得動的就是他本來想要的那一個，修不動的照樣死掉。
"""

import pytest

from app.config import Settings
from app.db.session import make_engine

# 各家主控台真的會給的形狀。
PASTED = [
    # Heroku 風格的舊 scheme，以及大半教學裡的那一個。
    ("postgres://u:p@h.example/db", "postgresql://u:p@h.example/db"),
    # Neon 主控台的 psql 分頁：整行連指令一起複製。
    (
        "psql 'postgresql://u:p@h.example/db?sslmode=require'",
        "postgresql://u:p@h.example/db?sslmode=require",
    ),
    # 兩者一起（照著舊教學貼）。
    ('psql "postgres://u:p@h.example/db"', "postgresql://u:p@h.example/db"),
    # 引號和前後空白：從網頁上選取時很容易一起帶到。
    ('  "postgresql://u:p@h.example/db"  ', "postgresql://u:p@h.example/db"),
]


@pytest.mark.parametrize(("pasted", "meant"), PASTED)
def test_what_he_pasted_becomes_what_he_meant(pasted: str, meant: str):
    assert Settings(DATABASE_URL=pasted).DATABASE_URL == meant


@pytest.mark.parametrize(("pasted", "_meant"), PASTED)
def test_and_the_repaired_one_actually_builds_an_engine(pasted: str, _meant: str):
    """修好了要真的能用。

    上一條只比對字串，而字串對了不代表 SQLAlchemy 收得下——那正是這個檔案要防的那一種
    失敗（import 期就炸，整個網址是死的）。
    """
    make_engine(Settings(DATABASE_URL=pasted).DATABASE_URL)


def test_a_string_that_was_already_right_is_left_alone():
    """已經對的不要動。修東西的程式最常見的壞法，是把本來好的也一起改了。"""
    url = "postgresql+psycopg2://u:p@h.example:5432/db?sslmode=require"

    assert Settings(DATABASE_URL=url).DATABASE_URL == url


def test_sqlite_is_left_alone():
    """開發機和整個測試套件都跑在這條路上。"""
    url = "sqlite:///./trading_app_dev.db"

    assert Settings(DATABASE_URL=url).DATABASE_URL == url


def test_a_password_that_looks_like_a_quote_is_not_eaten():
    """密碼裡的引號不可以被當成「貼多了」的引號拿掉。

    只有**整串**被引號包住才算，不是「出現過引號」。搞錯的話他會拿到一個密碼被改過的
    連線字串，而錯誤訊息會是「密碼錯誤」——那會讓他去改 Neon 上的密碼，愈修愈遠。
    """
    url = "postgresql://u:pa'ss@h.example/db"

    assert Settings(DATABASE_URL=url).DATABASE_URL == url
