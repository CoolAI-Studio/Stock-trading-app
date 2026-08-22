"""這個部署跑在哪一家的平台上，以及那一家把「環境變數」那一頁叫做什麼。

WHY THIS IS NOT A CONSTANT. Two rules in CLAUDE.md collide here, and both are
load-bearing:

  「永遠不要叫他去別的地方拿一個值。」 The audience is not an engineer.
  「把它填進環境變數」 is not an instruction to them, it is where the process
  stops. The setup page has to name the screen.

  使用者不會都用 Render。The maintainer picked a free tier; the next person
  will pay for something steadier, or self-host. A product that says
  「Render 後台 → Environment」 to somebody on Fly.io has sent them looking
  for a page that does not exist -- worse than saying nothing, because they
  will believe it.

Every platform of this kind announces itself in the environment, so the app
can simply ask. Exact when it knows, honestly generic when it does not.
Adding one costs a line, and nothing anywhere else needs to change.

DELIBERATELY NOT A SETTING. Making this configurable would put an eighth blank
on the deploy form to tell the app something the platform already told it --
and that form is the whole reason the setup page exists.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Host:
    """一家部署平台，以及它自己的說法。"""

    name: str
    # The literal path through THAT platform's interface. Written in its own
    # words -- Environment, Variables, Config Vars, secrets -- because the
    # person is reading their screen, not this repository.
    env_where: str


GENERIC = Host(
    name="你的部署平台",
    env_where=(
        "你的部署平台上「環境變數」那一頁（每一家的叫法不一樣：Environment、Variables、"
        "Config Vars、Secrets，指的都是同一件事）→ 新增一個同名的變數，把值貼上去 → "
        "存檔之後服務通常會自己重新啟動。自己用 Docker 跑的話，就是 `-e 名稱=值` "
        "或 compose 檔裡的 environment。"
    ),
)

# The environment variable each platform sets on every one of its own
# containers, and what to say when it is there. Order only matters if two ever
# appear at once, which would mean one is running inside the other.
_KNOWN: tuple[tuple[str, Host], ...] = (
    (
        "RENDER",
        Host(
            name="Render",
            env_where=(
                "Render 後台 → 你的服務 → 左邊選單 Environment → 找到同名的欄位貼上去 → "
                "存檔之後 Render 會自動重新部署，大約一兩分鐘。"
            ),
        ),
    ),
    (
        "RAILWAY_ENVIRONMENT",
        Host(
            name="Railway",
            env_where=(
                "Railway 後台 → 你的服務 → Variables → New Variable，名稱一樣、把值貼上去 → "
                "存檔之後會自動重新部署。"
            ),
        ),
    ),
    (
        "FLY_APP_NAME",
        Host(
            name="Fly.io",
            env_where=(
                "Fly.io 把這種值叫 secrets：`fly secrets set 名稱=值`，"
                "或在 fly.io 後台你的 app → Secrets。設定完會自動重啟。"
            ),
        ),
    ),
    (
        "DYNO",
        Host(
            name="Heroku",
            env_where=(
                "Heroku 後台 → 你的 app → Settings → Config Vars → Reveal Config Vars → "
                "新增一個同名的變數貼上去。"
            ),
        ),
    ),
    (
        "KOYEB_APP_NAME",
        Host(
            name="Koyeb",
            env_where=(
                "Koyeb 後台 → 你的服務 → Settings → Environment variables → "
                "新增一個同名的變數貼上去 → Save 之後會重新部署。"
            ),
        ),
    ),
)


def detect() -> Host:
    """Which platform this process is on, or the generic answer.

    Read at call time rather than at import: the app is imported long before
    anybody asks, and a value frozen then is a value from the wrong moment --
    the same reason build_info reads the commit when asked.
    """
    for marker, host in _KNOWN:
        if (os.environ.get(marker) or "").strip():
            return host
    return GENERIC


def paste_target() -> str:
    """「貼回哪裡」的短說法，給文案句尾用。

    Long enough to be an instruction, short enough to sit at the end of a
    sentence that is about something else.
    """
    host = detect()
    return f"{host.name}的環境變數" if host is not GENERIC else "你的部署平台的環境變數"


# What each platform calls the address it gave this service. Some hand over a
# whole URL, some only the host part; the shape is recorded next to the name
# so the caller never has to guess.
_PUBLIC_URL_ENV: tuple[tuple[str, bool], ...] = (
    ("APP_PUBLIC_URL", True),  # 這個 app 自己的名字，任何平台都能設
    ("RENDER_EXTERNAL_URL", True),
    ("RAILWAY_PUBLIC_DOMAIN", False),
    ("KOYEB_PUBLIC_DOMAIN", False),
)


def public_url() -> str:
    """The address this deployment answers on, if the platform said so.

    WHY THIS MATTERS MORE THAN IT LOOKS. It has exactly one reader -- the
    TradingView setup page, which tells the owner what URL to paste into
    TradingView. When it is wrong it is wrong SILENTLY: the webhook goes to
    localhost, no signal ever arrives, and nothing on any screen says why.

    Asking somebody to copy their own service's URL back into that same
    service is a step that exists for no reason, and it is a step a first-time
    deployer skips. So it is derived wherever the platform makes that possible
    -- and 「wherever」 is the point of this function rather than one company's
    variable: the same silent failure was waiting for everybody else.

    Empty string when nothing is knowable, which is the honest answer and lets
    the caller keep its own explicit value.
    """
    for name, is_full_url in _PUBLIC_URL_ENV:
        value = (os.environ.get(name) or "").strip()
        if not value:
            continue
        if is_full_url:
            return value
        return value if value.startswith(("http://", "https://")) else f"https://{value}"

    # Fly.io publishes no URL variable, but it names the app -- and its
    # default hostname is derived from that name by rule, not by chance.
    fly_app = (os.environ.get("FLY_APP_NAME") or "").strip()
    if fly_app:
        return f"https://{fly_app}.fly.dev"
    return ""
