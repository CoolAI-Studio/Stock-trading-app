"""每週稽查的相依套件關卡，不可以卡在一個永遠不會修的公告上。

＊ 量到的（`.github/workflows/audit.yml` 的排程紀錄）。

    2026-08-24  相依套件的已知漏洞   紅   ecdsa 0.19.2  PYSEC-2026-1325
    2026-08-31  相依套件的已知漏洞   紅   同上
    2026-09-07  （前一步先紅了，這一步被跳過——但它還在）

PYSEC-2026-1325 是 python-ecdsa 在 P-256 上的 Minerva timing attack，而公告自己寫著
「The python-ecdsa project considers side channel attacks out of scope for the
project and there is no planned fix.」——**不會有修好的版本可以升**。

它是 python-jose 帶進來的（`pip show ecdsa` → Required-by: python-jose），而這個 app
從來不用它：JWT 是 HMAC 簽的，jose 的 EC 金鑰也走 cryptography 後端。可是 pip-audit
看的是裝了什麼，不是用了什麼。

所以只剩兩條路：在關卡上寫一條「這一項不算」，或者不要裝它。前者要永遠記得那條例外
的理由還成立，而且讓一個每週都在叫的關卡從此少叫一種東西；後者只要換掉一個套件——這
個 app 用到 jose 的只有 `core/security.py` 的 encode／decode，PyJWT 是同一個 API，而
且不帶 ecdsa、rsa、pyasn1 這三個只為 jose 存在的傳遞相依。

＊ 為什麼這件事值得一條測試。

一個每週都紅的關卡等於沒有關卡。它紅了三週都沒有人處理，正是因為那封信每週都一樣。
這幾條讓 jose 不會被某一次「順手加回來」，而加回來的症狀就是每週一封沒有人會讀的信。
"""

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _lines(name: str) -> list[str]:
    """去掉註解：requirements.txt 裡解釋為什麼不用 jose 的那一行不算「裝了 jose」。"""
    text = (BACKEND / name).read_text(encoding="utf-8")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _pinned() -> dict[str, str]:
    pins = {}
    for line in _lines("requirements.lock"):
        if "==" in line:
            name, version = line.split("==", 1)
            pins[name.lower().replace("_", "-")] = version
    return pins


def test_the_package_that_will_never_be_fixed_is_not_installed():
    assert "ecdsa" not in _pinned(), (
        "ecdsa 又回到 lock 裡了——PYSEC-2026-1325 不會有修好的版本，每週稽查會永遠紅"
    )


def test_nothing_declares_the_package_that_pulled_it_in():
    declared = " ".join(_lines("requirements.txt")).lower()

    assert "python-jose" not in declared
    assert "python-jose" not in _pinned()


def test_the_replacement_is_actually_there():
    """拿掉 jose 而沒有補上 PyJWT，症狀是容器起不來（`core/security.py` 的 import 期例外）。"""
    declared = " ".join(_lines("requirements.txt")).lower()

    assert "pyjwt" in declared
    assert "pyjwt" in _pinned()
