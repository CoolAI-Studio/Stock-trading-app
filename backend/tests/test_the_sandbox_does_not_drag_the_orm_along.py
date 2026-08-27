"""策略沙箱要載入的東西，不可以把整個 ORM 拉進來。

#18 要把使用者的策略移到獨立子行程執行，而那件事的成本被一個 import 決定：

    裸 Python                              187 ms
    app.models.enums（搬家前，觸發套件 __init__）  2171 ms   ← 整個 SQLAlchemy 模型層
    sqlalchemy 本身                        1209 ms
    indicators（沙箱真正需要的）             285 ms

沙箱只是為了一個 `DataSource` enum，就把整個 ORM 拖進子行程。後果不是「慢一點」：
子行程被逾時殺掉之後要重建，而重建 2.3 秒的時候，那個 worker 負責的策略是瞎的——
輪詢週期才 5 秒。**這個產品唯一不能停的東西就是盯盤。**

而且記憶體從 54 MB 降到 20 MB 上下，讓「池要開幾個 worker」從一個緊繃的取捨變成
不用煩惱的事。

這一條守的是**性質**不是名字：不管將來誰在 market_data.base 或 strategy_runtime
裡加了什麼 import，只要它把 SQLAlchemy 拉進來，這裡就紅。
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent

# 子行程要載入的東西。沙箱在 worker 裡需要的就是這幾個，不多。
SANDBOX_IMPORTS = [
    "app.enums",
    "app.services.indicators",
    "app.services.market_data.base",
]


def _modules_after_importing(target: str) -> set[str]:
    """在一個乾淨的子行程裡 import，回傳它拉進來的頂層模組名。

    非得開子行程不可：這一條問的是「import 這個東西會連帶載入什麼」，而測試自己
    的行程早就把整個 app 載進來了，在裡面問這個問題永遠得到「全部都在」。
    """
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(BACKEND)!r})
        import {target}
        print(",".join(sorted({{name.split(".")[0] for name in sys.modules}})))
    """)
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return set(done.stdout.strip().split(","))


@pytest.mark.parametrize("target", SANDBOX_IMPORTS)
def test_no_sqlalchemy(target: str):
    loaded = _modules_after_importing(target)

    assert "sqlalchemy" not in loaded, (
        f"import {target} 把 SQLAlchemy 拉進來了。子行程重建會因此多花兩秒，而那兩秒裡策略是瞎的。"
    )


@pytest.mark.parametrize("target", SANDBOX_IMPORTS)
def test_no_orm_models(target: str):
    """連 app.models 都不該被碰到。

    分開一條，因為原因不同：SQLAlchemy 是那兩秒的來源，而 app.models 是**觸發**
    它的那一步——套件的 __init__ 會把每一個模型都 import 一遍。擋住前者而放過後
    者，下一個人加一行 `from app.models.strategy import Strategy` 就又回去了。
    """
    loaded = _modules_after_importing(target)

    assert "app" in loaded  # 確認真的 import 到東西了，不是量到一個空殼
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent(f"""
                import sys
                sys.path.insert(0, {str(BACKEND)!r})
                import {target}
                print("app.models" in sys.modules)
            """),
        ],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert done.stdout.strip() == "False", f"import {target} 觸發了 app.models 套件"
