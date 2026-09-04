"""每十五分鐘跑一次的排程，會在他的副本上把 GitHub Actions 的免費額度吃光。

＊ 數字（GitHub 自己的文件，2026-09 讀的）。

    公開 repo：「The use of standard GitHub-hosted runners is free: In public
               repositories.」
    私人 repo（GitHub Free）：一個月 **2,000 分鐘**，而且
               「usage is blocked once you use up your quota」。

這個看門狗是 `*/30 * * * *`，也就是一個月 1,440 次。每一次都要 checkout 加
setup-python，所以就算 `HEALTH_URL` 沒設、腳本第一行就 `exit 0`，那一分鐘照樣算。

    1,440 次 × 至少 1 分鐘 = 1,440 分鐘，佔 2,000 的 72%

（間隔原本是 `*/15`，那時候是 2,880 分鐘、直接超過額度。改成半小時是為了另一件事——
深的那一條會查資料庫，問得比休眠門檻密就把免費方案的運算時數吃光——但即使數字掉到門
檻底下，底下那個 `if:` 還是要留著：剩下的 560 分鐘要留給
`sync-from-upstream.yml`，而那是他拿到更新的唯一管道。）

＊ 為什麼這件事會咬到「更新」。

Vercel 的 clone 會在他自己的帳號下開一個**新的 repo**（不是 fork，所以排程一開始就
會跑），而那個 repo 很可能是私人的——這是一個他自己的股票提醒系統。

額度用完之後 GitHub 擋掉的是**所有**的 Actions，包括
`.github/workflows/sync-from-upstream.yml`——那條每天從上游快轉的線，正是他拿到更新
（含安全修補）的唯一管道。

也就是：一個為了「看得到提醒有沒有在跑」而存在的排程，每個月大約第二十天會把「拿得
到更新」關掉。而兩件事都是安靜的。

＊ 為什麼可以直接關掉而不是想辦法省。

被跳過的 job **不計分鐘**（GitHub 只算真的跑起來的）。而沒設 `HEALTH_URL` 的人本來
就沒有在用這個看門狗——它每十五分鐘印一次「沒設定，略過」給沒有人看的 Actions 分頁。

而且對他來說有更好的東西：安裝頁那一步（#93）教他設一個每 5 分鐘檢查一次、壞掉就寄
信的外部監控。那比這個排程快三倍，而且不吃他任何額度。
"""

from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "watchdog.yml"


@pytest.fixture
def text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture
def without_comments(text: str) -> str:
    """註解裡提到什麼都不算數。

    這個 repo 已經因為 `"pip-audit" in ci` 命中一行註解而誤判過一次。
    """
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_it_does_not_run_when_nobody_asked_for_it(without_comments: str):
    """沒設 HEALTH_URL 就不要起這個 job。

    被跳過的 job 不計分鐘，而起來只為了印一行「沒設定，略過」的 job 要算一分鐘——
    一個月 1,440 分鐘，佔掉私人 repo 那 2,000 的四分之三，而剩下的要留給每天同步更新
    的那條工作流程。
    """
    assert "vars.HEALTH_URL" in without_comments, (
        "job 層沒有用 vars.HEALTH_URL 擋——沒設定的副本會每半小時燒掉一分鐘額度"
    )
    # 擋在 job 層（if:），不是只在 step 裡面 exit 0——後者照樣起了 runner。
    guarded = [
        line
        for line in without_comments.splitlines()
        if line.lstrip().startswith("if:") and "HEALTH_URL" in line
    ]
    assert guarded, "那個判斷不在 job 的 if: 上，起來了才跳過等於沒省到"


def test_the_schedule_is_still_there(without_comments: str):
    """省額度不可以順手把看門狗關掉。

    設好 HEALTH_URL 的人（維護者自己那一份）照樣要被定期問一次。
    """
    assert "schedule:" in without_comments
    assert "cron:" in without_comments


def test_it_does_not_ask_more_often_than_the_database_can_sleep():
    """這個 job 打的是 `?deep=1`，而深的那一條會查資料庫。

    Neon 免費方案閒置五分鐘才休眠、而且關不掉，所以問得比那個門檻密就等於把那顆運算單
    元釘在醒著的狀態——一個月 730 小時，而額度只有 400 小時（量出來的：維護者的主控台
    2026-09-04 是 24.12/100，用量從 9/1 起算）。

    盯盤迴圈收盤後已經拉到半小時一次，這裡不可以比它密——否則省下來的那一半又被這個排
    程吃回去。
    """
    import re

    from app.services.market_loop import CLOSED_POLL_INTERVAL_SEC

    found = re.search(r'cron:\s*"\*/(\d+) \* \* \* \*"', WORKFLOW.read_text(encoding="utf-8"))

    assert found, "看不懂這個排程的間隔"
    assert int(found.group(1)) * 60 >= CLOSED_POLL_INTERVAL_SEC


def test_the_reason_is_written_down_where_the_next_person_looks(text: str):
    """把 if: 拿掉不會有任何東西變紅，而症狀是別人每個月第二十天開始收不到更新。"""
    assert "2,000" in text or "2000" in text, "檔頭沒有寫下額度這件事"
    assert "sync" in text or "更新" in text, "檔頭沒有寫下它會連帶關掉什麼"
