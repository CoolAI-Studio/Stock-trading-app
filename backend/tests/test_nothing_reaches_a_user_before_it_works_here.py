"""使用者拿到的每一版，都要先在我們自己的實例上跑起來而且活著。

＊ `stable` 是這整條線上唯一真正對使用者生效的東西。

他的後端追 `stable` ＋ autoDeploy，他的前端每天從 `stable` 快轉（#52）。所以 **`stable`
往前一步，就是別人的機器上跑起一份新的程式**——而他不在場、沒有 CI、也不知道我們改了
什麼。

ci.yml 的 deploy job 把那一步排在最後，理由寫在它自己的註解裡：放在測試綠燈之後就移
的話，`stable` 只代表「測試過」；放在健康檢查之後，它代表「我們自己的實例已經跑起來
而且活著」——使用者拿到的每一版，我們都先當過白老鼠。

＊ 這個檔案守的是那個順序，不是那段程式碼。

那些保證全部寫在一份 YAML 裡，而 YAML 不會被任何東西檢查。刪掉一個 `needs`、把移動
`stable` 那一步往上搬、或者哪天為了「讓卡住的部署動一下」加一個 `--force`——每一種都
不會讓任何東西變紅，而後果是一版沒有驗證過的程式直接送到別人的機器上。

＊ 為什麼 chart 和 first-run **刻意**不在 needs 裡。

那兩關開真的瀏覽器，守的是畫面。CLAUDE.md 的工程標準那一列寫著「兩關都不擋
deploy：警告不能停擺優先於畫面」——圖表壞掉不值得讓一個修好通知路徑的 hotfix 卡住。

這件事寫成測試，是因為它看起來像遺漏。下一個讀 ci.yml 的人會很想「順手補上」，而那
會把一個刻意的取捨變成一次意外的回歸。
"""

from pathlib import Path

import pytest

CI = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _deploy_job() -> str:
    """deploy job 那一段，**去掉註解**。

    去掉註解，是因為這個 repo 已經被咬過一次：一條 `"pip-audit" in ci` 的斷言命中的是
    另一步的註解，於是那條測試守的其實是一段說明文字。而這一份的註解特別長。
    """
    text = CI.read_text(encoding="utf-8")
    body = text[text.index("  deploy:") :]
    return "\n".join(line for line in body.split("\n") if not line.lstrip().startswith("#"))


def _steps() -> list[str]:
    return [
        line.split("- name:", 1)[1].strip()
        for line in _deploy_job().split("\n")
        if line.strip().startswith("- name:")
    ]


@pytest.mark.parametrize("job", ["backend", "frontend", "first-deploy"])
def test_a_version_only_ships_after_this_gate(job: str):
    """三關全綠才允許部署。

    少一關，那一類的失效就會直接送到他的機器上：backend 是邏輯、frontend 是型別和
    建置、first-deploy 是「這個映像檔在一個全空的環境裡真的起得來」。
    """
    needs = next(line for line in _deploy_job().split("\n") if line.strip().startswith("needs:"))

    assert job in needs, f"deploy 不再等 {job} 了：那一類的失效會直接送到使用者的機器上"


def test_the_browser_gates_deliberately_do_not_block_a_deploy():
    """chart 和 first-run 不擋 deploy——**這是刻意的，不是漏掉的。**

    CLAUDE.md：「兩關都不擋 deploy：警告不能停擺優先於畫面。」圖表壞掉不值得讓一個修
    好通知路徑的 hotfix 卡住。

    寫成測試是因為它看起來像遺漏，而下一個人會很想順手補上。
    """
    needs = next(line for line in _deploy_job().split("\n") if line.strip().startswith("needs:"))

    for gate in ("chart", "first-run"):
        assert gate not in needs, (
            f"{gate} 被加進 deploy 的 needs 了。那一關開真的瀏覽器、守的是畫面，"
            "而警告不能停擺優先於畫面——一個修通知路徑的 hotfix 不該被圖表卡住。"
            "如果這個取捨要改，改的是 CLAUDE.md 那一列，不是這裡。"
        )


def test_stable_moves_last_of_all():
    """`stable` 只在「送達而且活著」都確認過之後才前進。

    往上搬一步，它就退回「測試過」的意思——而使用者拿到的就不再是我們自己跑過的那一
    版。舊版的後端每一項健康檢查都是綠的，所以少了這個順序，部署失敗在我們這邊看起來
    跟成功一模一樣。
    """
    steps = _steps()
    move = next(i for i, name in enumerate(steps) if "stable" in name)

    assert any("alive" in name or "healthy" in name for name in steps[:move]), (
        "移動 stable 之前沒有確認線上是活的"
    )
    assert any("this commit" in name for name in steps[:move]), (
        "移動 stable 之前沒有確認送達的真的是這一個 commit"
    )
    assert move == len(steps) - 1, f"移動 stable 不是最後一步，後面還有：{steps[move + 1 :]}"


def test_stable_is_never_forced():
    """不 force。

    非快轉會被 GitHub 擋下來並回 422，而那正是要的：`stable` 落後於 `main` 是正常的
    （它只在部署成功時前進），`stable` 跑到 `main` 前面則代表有人手動動過它——那要有人
    看到。

    而**回滾就是把 `stable` 移回去**。一個 `--force` 會讓下一次成功的部署安靜地把那次
    回滾蓋掉，使用者則會第二次拿到那個壞掉的版本。
    """
    job = _deploy_job()

    assert "force=false" in job, "stable 的移動不再是非強制的了"
    assert "-f force=true" not in job and "--force" not in job


def test_it_only_ever_runs_on_the_main_branch():
    """分支上的推送不可以動到 `stable`。

    少了這道閘門，任何一個功能分支綠燈之後都會把它自己送到每一個使用者的機器上。
    """
    guard = next(line for line in _deploy_job().split("\n") if line.strip().startswith("if:"))

    assert "refs/heads/main" in guard
    assert "push" in guard


# --- 那個開關是使用者拿得到更新的唯一原因 -------------------------------------

RENDER_YAML = Path(__file__).resolve().parents[2] / "render.yaml"


def test_autodeploy_stays_on_even_though_render_advises_against_it():
    """`autoDeploy: true` 是刻意的，而且**跟 Render 自己的建議相反**。

    Render 的文件對 Deploy to Render 按鈕寫著：

        For a service that is meant to be deployed via a Deploy to Render
        button, it is strongly advised to set `autoDeploy: false` … This
        ensures that code pushes to the repo that contains the Deploy to
        Render button don't trigger an automatic deploy of every instance
        deployed via the Deploy to Render button.

    他們擔心的事情，正是這個專案要的事情：按鈕部署出去的每一份，都會因為我們推的東西
    而重新部署。#52 的整個理由就是「使用者拿不到我們的更新（含安全修補）」。

    而他們警告的那個風險，我們用 `branch: stable` 換掉了：一次 push 不會送到任何人手
    上，只有 `stable` 前進才會——而 `stable` 只在我們自己的實例跑起來而且健康之後才動
    （見這個檔案上面那幾條）。

    **這一條存在，是因為下一個讀 Render 文件的人會想「照著改成 false」。** 改下去不會
    有任何東西變紅，而後果是每一個現有使用者從此拿不到任何更新，包括安全修補——完全
    安靜。

    Deploy to Render 按鈕不會 fork：Render 直接從原始的 repo 部署（同一份文件）。所以
    那些服務追的是**我們這個 repo 的 stable**，這個開關關掉就真的是全部一起停。
    """
    spec = RENDER_YAML.read_text(encoding="utf-8")
    body = "\n".join(line for line in spec.split("\n") if not line.lstrip().startswith("#"))

    assert "autoDeploy: true" in body, (
        "autoDeploy 被關掉了。Render 的文件確實建議這樣做，但這個專案靠它送出安全修補，"
        "而風險是用 branch: stable 換掉的，不是用關掉它換掉的。"
    )
    assert "branch: stable" in body, (
        "不追 stable 了。那樣 autoDeploy 就變成 Render 警告的那一種：一次 push 直接送到"
        "每一個人的機器上，而那一版可能連測試都還沒跑完。"
    )


def test_the_reason_is_written_next_to_the_switch():
    """理由要留在那個檔案裡，不是只在這條測試裡。

    測試會擋下改動，但擋下來的人得知道為什麼——而他當下手上拿的是 Render 的文件。
    """
    spec = RENDER_YAML.read_text(encoding="utf-8")

    assert "Deploy to Render" in spec or "autoDeploy" in spec
    assert "stable" in spec
