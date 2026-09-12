"""CI 用的 action 版本不可以各自漂移。

＊ 量到的（run 34633530540，2026-09-11）。

    Node.js 20 is deprecated. The following actions target Node.js 20 but are
    being forced to run on Node.js 24: actions/checkout@v4 / actions/cache@v4

四個 job 都印了這一行。當時 `checkout` 在這個 repo 裡同時有兩種版本：多數 job 是
`@v7`，而 `first-deploy`、`upgrade` 和使用者副本的同步還停在 `@v4`。沒有人刻意選擇
留在舊版——它只是沒有跟著一起改。

＊ 為什麼這件事值得一條測試（#113）。

`first-deploy` 和 `upgrade` **擋 `deploy`**。哪天 GitHub 真的把 Node 20 拿掉，這兩關
一紅，送不出去的不只是這一次改動，而是**所有**修正，包括修通知路徑的 hotfix——那就
是警告全面停擺本身。而在那之前，唯一的徵兆是一行沒有人會讀的黃色警告。

`sync-from-upstream.yml` 更遠：它跑在**別人的 repo 裡**，壞了我們連紅燈都看不到。

判準是「同一個 action 只能有一種版本」，不是「要用哪一版」：版本要不要往前推是人的
決定，而這條只擋「有一半忘了跟上」——那正是這次發生的事，也是下次會再發生的事。
"""

import re
from collections import defaultdict
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
USES = re.compile(r"uses:\s*(actions/[a-z-]+)@(v\d+)")


def _versions() -> dict[str, dict[str, list[str]]]:
    found: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                continue
            match = USES.search(line)
            if match:
                found[match.group(1)][match.group(2)].append(path.name)
    return found


def test_every_action_is_pinned_to_one_version_everywhere():
    split = {
        action: dict(versions) for action, versions in _versions().items() if len(versions) > 1
    }

    assert not split, (
        f"同一個 action 有兩種版本，代表有一半忘了跟上：{split}。"
        "擋 deploy 的那幾個 job 停在舊版，症狀是哪天所有修正都送不出去"
    )


def test_the_workflows_actually_use_actions():
    """上面那條在檔案讀不到、或 regex 沒對上的時候會空手通過。"""
    assert _versions(), "一個 actions/… 都沒找到，這條檢查等於沒有在檢查"
