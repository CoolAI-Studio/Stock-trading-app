"""改壞了要有路可以回去。

＊ 為什麼這件事對**這個**使用者特別重要。

他不會寫 Python。他最可能的操作是「讓 AI 改一版試試看」——而 AI 改出來的東西可能編
得過、跑得動、然後在半夜給出一個他不想要的訊號。

而 `source_code` 改了就沒了。沒有版本歷史的話，他唯一的路是再叫 AI 改回來，而那不
是同一份程式碼。

＊ 現在還多了一個來源：我們自己的更新。

#50 讓被更新弄壞的策略不會被永久停用，但它還是不會發訊號。而如果他當初的程式碼用
到了一個我們後來收掉的名字，「回到上一版」也救不了他——**那正是要驗的一件事**：還原
要走跟儲存一樣的驗證，不可以因為「這是舊的、以前能用」就放行。放行的話他會得到一支
存得進去、但每一輪都在報錯的策略。

＊ 最容易安靜壞掉的地方：還原了，但跑的還是舊的。

策略的實例活在子行程裡，由 id 快取（#18）。只改資料庫而不叫 release_strategy，畫面
上會顯示新的程式碼，而盯盤跑的還是還原前的那一份——**兩者可以差好幾個月，而沒有任何
東西會說**。

＊ 還原是新增一版，不是刪掉中間的。

否則「還原之後又想還原回來」就沒路了。這一條是票上明寫的。
"""

import pytest

WORKS = """
class Strategy:
    def __init__(self):
        self.name = "v1"
        self.symbol = "AAPL"

    def on_tick(self, current_price: float) -> str:
        return "HOLD"
"""

V2 = WORKS.replace('"v1"', '"v2"')
V3 = WORKS.replace('"v1"', '"v3"')


@pytest.fixture
def strategy_id(auth_client) -> int:
    resp = auth_client.post(
        "/api/strategies",
        json={"name": "versioned", "symbol": "AAPL", "source_code": WORKS},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _versions(auth_client, strategy_id: int) -> list[dict]:
    resp = auth_client.get(f"/api/strategies/{strategy_id}/versions")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_the_very_first_version_is_kept(auth_client, strategy_id):
    """**建立的時候就要存一版。**

    只在「變更」時存的話，他第一次編輯就永久失去了原始的那一份——而那通常正是他
    想回去的那一版。
    """
    versions = _versions(auth_client, strategy_id)

    assert len(versions) == 1
    assert versions[0]["source_code"] == WORKS


def test_every_edit_leaves_a_version_behind(auth_client, strategy_id):
    auth_client.patch(f"/api/strategies/{strategy_id}", json={"source_code": V2})
    auth_client.patch(f"/api/strategies/{strategy_id}", json={"source_code": V3})

    versions = _versions(auth_client, strategy_id)

    # 最新的在前面：他要找的通常是「剛剛那一版」。
    assert [v["source_code"] for v in versions] == [V3, V2, WORKS]


def test_changing_only_the_parameters_also_leaves_a_version(auth_client):
    """參數也是「這支策略是什麼」的一部分。

    調了一個參數之後績效變差，跟改了程式碼之後變差一樣需要退路——而參數改動不會
    動到 source_code，所以只看原始碼的版本歷史會漏掉它。
    """
    tunable = (
        "class Strategy:\n"
        "    def __init__(self):\n"
        "        self.name = 'tunable'\n"
        "        self.symbol = 'AAPL'\n"
        "        self.params = {'window': 5}\n"
        "    def on_tick(self, price):\n"
        "        return 'HOLD'\n"
    )
    created = auth_client.post(
        "/api/strategies",
        json={"name": "params-versioned", "symbol": "AAPL", "source_code": tunable},
    ).json()

    auth_client.patch(f"/api/strategies/{created['id']}", json={"params": {"window": 20}})

    versions = _versions(auth_client, created["id"])
    assert len(versions) == 2
    assert versions[0]["params"] == {"window": 20}


def test_editing_something_that_is_not_the_code_does_not_pile_up_versions(auth_client, strategy_id):
    """改名字、改代號不算一個新版本。

    每一次無關的編輯都存一版的話，他要找的那一版會被埋在幾十筆長得一模一樣的紀錄
    裡——而那等於沒有版本歷史。
    """
    auth_client.patch(f"/api/strategies/{strategy_id}", json={"name": "renamed"})

    assert len(_versions(auth_client, strategy_id)) == 1


def test_restoring_puts_the_old_code_back(auth_client, strategy_id):
    auth_client.patch(f"/api/strategies/{strategy_id}", json={"source_code": V2})
    first = _versions(auth_client, strategy_id)[-1]

    resp = auth_client.post(f"/api/strategies/{strategy_id}/versions/{first['id']}/restore")

    assert resp.status_code == 200, resp.text
    assert auth_client.get(f"/api/strategies/{strategy_id}").json()["source_code"] == WORKS


def test_restoring_adds_a_version_rather_than_deleting_the_ones_after_it(auth_client, strategy_id):
    """票上明寫的一條：還原之後還要能還原回來。

    刪掉中間的版本，「我還原錯了，我要回到剛剛那一版」就沒有路了——而那是使用這個
    功能的人最可能做的第二件事。
    """
    auth_client.patch(f"/api/strategies/{strategy_id}", json={"source_code": V2})
    first = _versions(auth_client, strategy_id)[-1]

    auth_client.post(f"/api/strategies/{strategy_id}/versions/{first['id']}/restore")

    versions = _versions(auth_client, strategy_id)
    assert len(versions) == 3, "還原應該是新增一版"
    assert versions[0]["source_code"] == WORKS
    # V2 還在，所以他還原得回去。
    assert any(v["source_code"] == V2 for v in versions)


def test_restoring_actually_changes_what_is_running(auth_client, strategy_id):
    """**這是最容易安靜壞掉的一條。**

    策略的實例活在子行程裡，由 id 快取（#18）。只改資料庫而不放掉那個實例，畫面上
    會顯示還原後的程式碼，而盯盤跑的還是還原前的那一份——兩者可以差好幾個月，而沒
    有任何東西會說。
    """
    from app.services import market_loop

    auth_client.patch(f"/api/strategies/{strategy_id}", json={"source_code": V2})
    # 讓它真的被載入，這樣「有沒有被放掉」才問得出來。
    market_loop._registry.get_or_load(strategy_id, V2)
    assert market_loop._registry.is_cached(strategy_id)

    first = _versions(auth_client, strategy_id)[-1]
    auth_client.post(f"/api/strategies/{strategy_id}/versions/{first['id']}/restore")

    assert not market_loop._registry.is_cached(strategy_id), "還原了，但正在跑的還是舊的那一份"


def test_restoring_code_that_no_longer_compiles_is_refused(auth_client, db_session, strategy_id):
    """還原要走跟儲存一樣的驗證。

    「這是舊的，以前能用」不是放行的理由——我們可能在這之間收緊了沙箱（#50 就是在
    處理那件事的後果）。放行的話他會得到一支存得進去、但每一輪都在報錯的策略，而
    他以為自己已經修好了。
    """
    from app.models.strategy_version import StrategyVersion

    # 直接塞一筆編不過的版本進去，模擬「當初能用、現在不能用」。
    #
    # 用 db_session（測試那個暫時資料庫），不是 SessionLocal()——後者連的是正式的引
    # 擎。我第一版寫錯了，而它報的是「沒有這張表」而不是斷言失敗，所以一眼看不出問
    # 題在測試而不是在實作。
    version = StrategyVersion(
        strategy_id=strategy_id,
        source_code="import os\n" + WORKS,
        params={},
        code_hash="whatever",
        author="manual",
    )
    db_session.add(version)
    db_session.commit()
    bad_id = version.id

    resp = auth_client.post(f"/api/strategies/{strategy_id}/versions/{bad_id}/restore")

    assert resp.status_code == 422, resp.text
    # 而且原本的程式碼沒有被動到。
    assert auth_client.get(f"/api/strategies/{strategy_id}").json()["source_code"] == WORKS


def test_versions_do_not_grow_without_a_ceiling(auth_client, strategy_id):
    """免費方案的資料庫塞得爆。

    保留上限是票上明寫的。丟最舊的，但**現在正在跑的那一版永遠不丟**——那是他唯一
    真正需要的一版。
    """
    from app.config import settings

    for index in range(settings.STRATEGY_VERSION_LIMIT + 5):
        auth_client.patch(
            f"/api/strategies/{strategy_id}",
            json={"source_code": WORKS.replace('"v1"', f'"v{index}"')},
        )

    versions = _versions(auth_client, strategy_id)
    assert len(versions) <= settings.STRATEGY_VERSION_LIMIT
    current = auth_client.get(f"/api/strategies/{strategy_id}").json()["source_code"]
    assert versions[0]["source_code"] == current, "現在在跑的那一版被丟掉了"


def test_another_account_cannot_read_or_restore_them(second_user_headers, client, strategy_id):
    """版本裡是他的策略程式碼，那是這個 app 裡最私人的東西之一。

    第二個帳號用 conftest 的 fixture 直接建，不走註冊——**這個部署只能有一個帳號**
    （註冊在第一個帳號建立之後永久關閉，見 auth.py 的檔頭）。我第一版試圖用 API 註
    冊第二個人，那是在測一件這個產品裡不會發生的事。

    但跨帳號隔離還是要驗：第二個身分可以由 scripts/create_user.py 建出來，也可能存
    在於修好之前就開著註冊的舊部署上。
    """
    listed = client.get(f"/api/strategies/{strategy_id}/versions", headers=second_user_headers)
    assert listed.status_code == 404, listed.text

    restored = client.post(
        f"/api/strategies/{strategy_id}/versions/1/restore", headers=second_user_headers
    )
    assert restored.status_code == 404, restored.text
