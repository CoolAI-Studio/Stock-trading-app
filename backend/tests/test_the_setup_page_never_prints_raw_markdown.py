"""設定頁把字串原樣印出來，所以字串裡的 `**` 會變成畫面上的星號。

＊ 這件事已經真的發生過一次。

全新使用者第一次打開的時候，登入頁的第一句話印出來的是

    你現在建立的是**第一個…**帳號

星號原封不動地在畫面上。那是 `firstrun.browser.test.ts` 開頭列的第 1 條——四件事裡的
第一件，而且是第一次真的有人去看那些畫面才發現的。

＊ 為什麼還會再發生。

這幾個檔案裡的說明文字**很像 markdown**：長句、要強調的詞、條列。寫的人（包括我，今
天在寫 Supabase 那一段的時候）順手就打了 `**…**`，而它不會壞掉任何東西——測試照過、
linter 沒話說、型別也沒問題。只是每一個讀到那一句的人都看到星號，而那一句正是他卡住
的時候會讀的那一句。

＊ 這裡守的是「會被印出來的字串」，不是註解。

註解裡寫 `**這一條不可以退**` 是給下一個維護者看的，那沒有問題——這個 repo 到處都是。
所以只看真的會進到回應裡的那些字串。
"""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

# 這些檔案裡的字串會原樣出現在畫面上。
WATCHED = [
    APP / "services" / "setup_state.py",
    APP / "services" / "hosting.py",
    APP / "services" / "backup_schedule.py",
    APP / "services" / "notification" / "dispatcher.py",
    APP / "services" / "notification" / "retry.py",
]


def _string_literals(path: Path) -> list[str]:
    """這個模組裡所有的字串常值，**不含 docstring**。

    docstring 說的是給維護者的話，不會被印出去。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value not in docstrings
    ]


@pytest.mark.parametrize("path", WATCHED, ids=lambda p: p.name)
def test_no_user_facing_string_uses_markdown_bold(path: Path):
    """`**這樣**` 在畫面上就是星號。要強調的話由畫面那一側決定。"""
    offenders = [text for text in _string_literals(path) if "**" in text]

    assert not offenders, (
        f"{path.name} 裡有會被原樣印出去的 markdown 粗體：{offenders}。"
        "設定頁不會渲染 markdown——這件事在登入頁上已經真的發生過一次。"
    )
