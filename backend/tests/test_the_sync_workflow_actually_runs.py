"""同步那支工作流程的 shell 真的跑得起來嗎。

＊ 為什麼這一個檔案存在。

`sync-from-upstream.yml` 上面有一行：

    if: github.repository != 'CoolAI-Studio/Stock-trading-app'

**所以它永遠不會在我們自己的 CI 上跑。** 它只在使用者那份副本裡執行，而那個人不在
場、不會看 Actions、也不知道我們改了什麼。一個打錯字的 shell 在我們這邊是全綠的，
在他那邊是「更新從此不會來」——而那正是這支工作流程存在要防的那件事。

其他測試問的是「那個檔案裡有沒有寫這幾個字」。那擋得住「忘了寫」，擋不住「寫錯」。
這裡把那段 shell 抽出來，在一個真的 git repo 上跑，用一個假的 `gh` 記下它被怎麼呼
叫——問的是行為。

＊ 假的 `gh`，不是假的 git。

git 是真的：快轉能不能推、分岔算不算分岔，那些是 git 自己的語意，替身只會替我們把
答案編出來。`gh` 是假的，因為它要打 GitHub——而它做了什麼（有沒有開 PR、開幾個）正
是要驗的東西，所以它只負責把呼叫記下來。

＊ 本地的 bare repo 不會拒絕 workflow 檔。

GitHub 會（#114，見下面那一節），這裡的假 origin 不會。所以「動到 workflow 的更新不
可以推」這件事，只能驗成「它沒有去推」，驗不出「推了會被拒」——後者是 GitHub 的規則，
寫在那一節的說明裡。
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "sync-from-upstream.yml"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="需要 bash 和 git 才問得出這段 shell 的行為",
)


def _fast_forward_script() -> str:
    """把「快轉，不覆蓋」那一步的 shell 抽出來。

    自己找而不是用 YAML 解析器：PyYAML 在 requirements.lock 裡只是因為 bandit 把它拉
    進來的（CI 的測試那一格裝的是 lock），拿它當相依是一條會在別人重新產生 lock 的時
    候安靜斷掉的線。
    """
    lines = WORKFLOW.read_text(encoding="utf-8").split("\n")
    start = next(i for i, line in enumerate(lines) if line.strip() == "run: |")
    start = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "run: |")
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith("          "):
            break
        body.append(line[10:])
    script = "\n".join(body).strip()
    assert "git rev-parse HEAD" in script, f"抽錯段落了：{script[:120]}"
    return script


def _run(script: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 -- 跑的是這個 repo 自己的工作流程
        [shutil.which("bash") or "bash", "-euo", "pipefail", "-c", script],
        cwd=cwd,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        # 這段 shell 印的是中文。Windows 上 subprocess 預設用 cp950 解，會在讀輸出的
        # 執行緒裡丟 UnicodeDecodeError——測試照樣綠，只是多了一堆看不懂的警告。
        encoding="utf-8",
        errors="replace",
    )


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603
        [shutil.which("git") or "git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def world(tmp_path: Path):
    """一個上游、一份他的副本，還有一個會把呼叫記下來的假 `gh`。"""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "--quiet", "--initial-branch=main")
    _git(upstream, "config", "user.email", "u@example.com")
    _git(upstream, "config", "user.name", "upstream")
    (upstream / "README.md").write_text("v1\n", encoding="utf-8")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "--quiet", "-m", "v1")
    _git(upstream, "branch", "stable")

    # 他的副本，加上一個真的 remote 可以推（--bare，因為推到 checked-out 分支會被拒）
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "--bare", "--quiet", str(upstream), str(origin))
    copy = tmp_path / "copy"
    _git(tmp_path, "clone", "--quiet", str(origin), str(copy))
    _git(copy, "config", "user.email", "c@example.com")
    _git(copy, "config", "user.name", "copy")

    fake_gh = tmp_path / "bin"
    fake_gh.mkdir()
    calls = tmp_path / "gh-calls.txt"
    pr_flag = (tmp_path / "PR_EXISTS").as_posix()
    issue_flag = (tmp_path / "ISSUE_EXISTS").as_posix()
    (fake_gh / "gh").write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{calls.as_posix()}"\n'
        # `gh pr list`／`gh issue list` 的空輸出代表「還沒有」。要模擬「已經有一個」的
        # 時候，測試自己在旁邊放一個檔案。
        f'if [ "$1" = "pr" ] && [ "$2" = "list" ] && [ -f "{pr_flag}" ]; then\n'
        "  echo 7\n"
        "fi\n"
        f'if [ "$1" = "issue" ] && [ "$2" = "list" ] && [ -f "{issue_flag}" ]; then\n'
        "  echo 9\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_gh / "gh").chmod(0o755)

    env = {
        "PATH": f"{fake_gh.as_posix()}{os.pathsep}{os.environ.get('PATH', '')}",
        "GITHUB_REF_NAME": "main",
        "SYNC_BRANCH": "upstream-sync",
        "GH_TOKEN": "not-a-real-token",
    }
    return {
        "upstream": upstream,
        "origin": origin,
        "copy": copy,
        "env": env,
        "calls": calls,
        "tmp": tmp_path,
    }


def _calls(world) -> str:
    return world["calls"].read_text(encoding="utf-8") if world["calls"].exists() else ""


def _point_upstream_at(world, script_cwd: Path) -> str:
    """把工作流程裡寫死的 GitHub 網址換成測試裡那個本地 repo。"""
    return _fast_forward_script().replace(
        "https://github.com/CoolAI-Studio/Stock-trading-app.git",
        world["upstream"].as_posix(),
    )


def _add_upstream_remote(world) -> str:
    where = world["upstream"].as_posix()
    return f'git remote add upstream "{where}"\ngit fetch --quiet upstream stable\n'


def _upstream_moves_on(world, *, touching_a_workflow: bool = False) -> None:
    upstream = world["upstream"]
    (upstream / "README.md").write_text("v2\n", encoding="utf-8")
    if touching_a_workflow:
        workflows = upstream / ".github" / "workflows"
        workflows.mkdir(parents=True, exist_ok=True)
        (workflows / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "--quiet", "-m", "v2")
    _git(upstream, "branch", "--force", "stable", "HEAD")


def _he_changed_the_code(world) -> None:
    copy = world["copy"]
    (copy / "mine.txt").write_text("我自己加的\n", encoding="utf-8")
    _git(copy, "add", "-A")
    _git(copy, "commit", "--quiet", "-m", "我改的")


def test_a_copy_that_only_follows_upstream_gets_fast_forwarded(world):
    """沒有改過的副本：照舊快轉，不開 PR。

    這一條先跑，因為它是常見的那一條路——加了 PR 那一段之後最容易弄壞的就是它。
    """
    _upstream_moves_on(world)

    copy = world["copy"]
    script = _add_upstream_remote(world) + _point_upstream_at(world, copy)
    done = _run(script, copy, world["env"])

    assert done.returncode == 0, done.stderr
    assert not world["calls"].exists(), f"沒有分岔卻開了 PR：{_calls(world)}"
    pushed = _git(world["origin"], "rev-parse", "main")
    assert pushed == _git(world["upstream"], "rev-parse", "stable"), "快轉沒有真的推上去"


def test_a_diverged_copy_gets_a_pull_request(world):
    """他改過程式碼：不能快轉，但要開一個 PR，而且不可以失敗離開。"""
    _upstream_moves_on(world)
    _he_changed_the_code(world)

    copy = world["copy"]
    script = _add_upstream_remote(world) + _point_upstream_at(world, copy)
    done = _run(script, copy, world["env"])

    assert done.returncode == 0, f"分岔就整支失敗了，那份副本從此拿不到更新：{done.stderr}"
    calls = _calls(world)
    assert "pr create" in calls, f"沒有開 PR，只留下：{calls}"

    # PR 的來源分支要真的存在，而且指著上游的 stable——這樣他按下去就是最新版。
    head = _git(world["origin"], "rev-parse", "upstream-sync")
    assert head == _git(world["upstream"], "rev-parse", "stable")

    # 而且他自己的東西還在。
    assert (copy / "mine.txt").exists()
    assert _git(world["origin"], "rev-parse", "main") != head, "分岔的時候不可以覆蓋他的 main"


def test_it_does_not_open_a_second_pull_request(world):
    """已經有一個等著的 PR 就更新它，不要每天再開一個。

    每天一個 PR 就是每天一封信，而那會讓他把 Actions 關掉——那樣連這個 PR 都不會再有。
    """
    _upstream_moves_on(world)
    _he_changed_the_code(world)

    (world["tmp"] / "PR_EXISTS").write_text("yes", encoding="utf-8")
    copy = world["copy"]
    script = _add_upstream_remote(world) + _point_upstream_at(world, copy)
    done = _run(script, copy, world["env"])

    assert done.returncode == 0, done.stderr
    calls = _calls(world)
    assert "pr create" not in calls, f"已經有一個 PR 了還再開一個：{calls}"
    assert "pr list" in calls


def test_nothing_to_do_is_not_an_error(world):
    """已經是最新的時候安靜結束——不推、不開 PR、不失敗。"""
    copy = world["copy"]
    script = _add_upstream_remote(world) + _point_upstream_at(world, copy)
    done = _run(script, copy, world["env"])

    assert done.returncode == 0, done.stderr
    assert not world["calls"].exists()


# --- 動到 workflow 檔的更新（#114） ---------------------------------------------------
#
# GitHub 的規則，不是推論：`GITHUB_TOKEN` 不能新增或修改 `.github/workflows/` 底下的檔
# 案，而且沒有任何 `permissions:` 給得了它——刻意不讓一個自動發的 token 改寫「這個 repo
# 要跑什麼」。只要這一段更新裡有一個 commit 動到那個目錄，push 一定被拒：
#
#     refusing to allow a GitHub App to create or update workflow ... without
#     `workflows` permission
#
# 而這個 repo 幾乎每一版都會動到。他看到的是 Actions 分頁上一個看不懂的紅叉，前端停在
# 舊版。分岔時開 PR 那條路要先推一個分支，是同一條規則。
#
# 所以：看得出一定會被拒的，就不推，改成開一則說明怎麼更新的 issue。


def test_an_update_that_touches_a_workflow_opens_an_issue_instead_of_a_doomed_push(world):
    _upstream_moves_on(world, touching_a_workflow=True)
    before = _git(world["origin"], "rev-parse", "main")

    copy = world["copy"]
    script = _add_upstream_remote(world) + _point_upstream_at(world, copy)
    done = _run(script, copy, world["env"])

    assert done.returncode == 0, f"一個他解決不了的狀況不可以變成每天一個紅叉：{done.stderr}"
    calls = _calls(world)
    assert "issue create" in calls, f"沒有留下任何說明：{calls}"
    assert "pr create" not in calls
    assert _git(world["origin"], "rev-parse", "main") == before, "推了一個 GitHub 一定會拒絕的 push"
    # 說明裡要有兩條路：重新部署一份，或者他自己選擇給一把 token。
    assert "SYNC_TOKEN" in calls
    assert "DEPLOYMENT.md" in calls


def test_a_diverged_copy_with_a_workflow_change_gets_the_issue_not_a_doomed_branch(world):
    """分岔時開 PR 要先推 upstream-sync 分支——那一次推送一樣會被拒。"""
    _upstream_moves_on(world, touching_a_workflow=True)
    _he_changed_the_code(world)

    copy = world["copy"]
    script = _add_upstream_remote(world) + _point_upstream_at(world, copy)
    done = _run(script, copy, world["env"])

    assert done.returncode == 0, done.stderr
    calls = _calls(world)
    assert "issue create" in calls
    assert "pr create" not in calls
    assert _git(world["origin"], "branch", "--list", "upstream-sync") == ""


def test_the_issue_is_not_opened_twice(world):
    """每天同步一次，每天一則一樣的 issue 就是每天一封信。已經有了就不再開。"""
    _upstream_moves_on(world, touching_a_workflow=True)
    (world["tmp"] / "ISSUE_EXISTS").write_text("yes", encoding="utf-8")

    copy = world["copy"]
    script = _add_upstream_remote(world) + _point_upstream_at(world, copy)
    done = _run(script, copy, world["env"])

    assert done.returncode == 0, done.stderr
    calls = _calls(world)
    assert "issue list" in calls
    assert "issue create" not in calls, f"已經有一則了還再開：{calls}"


def test_with_his_own_sync_token_a_workflow_change_is_pushed_like_any_other(world):
    """他自己選擇在 repo 的 secret 裡放了一把有 workflow 權限的 token：那就照常快轉。

    這一條是選擇，不是預設——不設的人照樣走上面那幾條。
    """
    _upstream_moves_on(world, touching_a_workflow=True)

    copy = world["copy"]
    script = _add_upstream_remote(world) + _point_upstream_at(world, copy)
    done = _run(script, copy, {**world["env"], "HAS_SYNC_TOKEN": "true"})

    assert done.returncode == 0, done.stderr
    assert "issue create" not in _calls(world)
    assert _git(world["origin"], "rev-parse", "main") == _git(
        world["upstream"], "rev-parse", "stable"
    )
