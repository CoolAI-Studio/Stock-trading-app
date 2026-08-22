"""這個 app 不綁死任何一家部署平台，但它還是要講得出「那一格在哪裡填」。

THE TENSION THIS RESOLVES. Two rules in CLAUDE.md pull in opposite directions
the moment you take them both seriously:

  「永遠不要叫他去別的地方拿一個值。」 The audience is not an engineer, so
  「把它填進環境變數」 is not an instruction, it is a dead end. The setup page
  has to say WHERE, in the words of the screen they are looking at.

  使用者不會都用 Render。The owner chose a free tier; somebody else will pay
  for something steadier. 「Render 後台 → Environment」 written into the
  product makes that person's screen disagree with their app -- which is worse
  than saying nothing, because it sends them looking for a page that does not
  exist.

The way out is that the app asks where it is. Every one of these platforms
announces itself in the environment -- RENDER, DYNO, FLY_APP_NAME -- so the
instruction can be exact when it is knowable and honestly generic when it is
not. Neither rule gets broken, and adding a platform costs one line.
"""

import pytest

from app.config import settings
from app.services import hosting, setup_state

ALL_MARKERS = (
    "RENDER",
    "RAILWAY_ENVIRONMENT",
    "FLY_APP_NAME",
    "DYNO",
    "KOYEB_APP_NAME",
)

COMPANIES = ("Render", "Heroku", "Railway", "Fly.io", "Koyeb")


@pytest.fixture(autouse=True)
def _nowhere_in_particular(monkeypatch):
    """No platform markers set, so each test says what it means to say. CI runs
    on GitHub's runners, which set none of these -- but a laptop might."""
    for marker in ALL_MARKERS:
        monkeypatch.delenv(marker, raising=False)


# --- 認得出平台 -----------------------------------------------------------------------


def test_an_unknown_platform_still_gets_a_usable_instruction():
    """A self-hosted container, a platform nobody here thought of, a laptop.
    The answer cannot be silence: that person still has to find the place where
    their environment variables live."""
    host = hosting.detect()

    assert host.env_where
    assert not any(company in host.env_where for company in COMPANIES)


def test_no_company_name_reaches_somebody_who_is_not_using_it():
    host = hosting.detect()

    assert not any(company in host.name for company in COMPANIES)


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("RENDER", "Render"),
        ("RAILWAY_ENVIRONMENT", "Railway"),
        ("FLY_APP_NAME", "Fly.io"),
        ("DYNO", "Heroku"),
        ("KOYEB_APP_NAME", "Koyeb"),
    ],
)
def test_a_platform_that_announces_itself_is_named_exactly(monkeypatch, marker, expected):
    """Being exact is the whole point of detecting: 「Render 後台 →
    Environment」 is an instruction, 「你的部署平台」 is only a hint."""
    monkeypatch.setenv(marker, "1")

    host = hosting.detect()

    assert host.name == expected
    assert expected in host.env_where


def test_each_platform_is_described_in_its_own_words(monkeypatch):
    """They all call the same screen something different -- Environment,
    Variables, Config Vars, secrets -- and the person is reading their screen,
    not this repository."""
    monkeypatch.setenv("DYNO", "web.1")
    assert "Config Vars" in hosting.detect().env_where

    monkeypatch.delenv("DYNO")
    monkeypatch.setenv("FLY_APP_NAME", "somebodys-app")
    assert "secrets" in hosting.detect().env_where.lower()


def test_detection_reads_the_environment_when_asked_not_at_import(monkeypatch):
    """The app is imported long before anybody asks, and a value frozen at
    import is a value from the wrong moment."""
    assert hosting.detect().name != "Render"

    monkeypatch.setenv("RENDER", "true")

    assert hosting.detect().name == "Render"


def test_an_empty_marker_is_not_a_platform(monkeypatch):
    """A blank variable is what a deploy form leaves behind, not a signal."""
    monkeypatch.setenv("RENDER", "")

    assert hosting.detect().name != "Render"


# --- 設定頁的文案 ---------------------------------------------------------------------


def _database_row():
    return next(
        item for item in setup_state.missing_settings(settings) if item.name == "DATABASE_URL"
    )


def test_the_database_field_asks_for_a_connection_string_not_a_company():
    """MEASURED BEFORE THIS CHANGE: the DATABASE_URL row read 「去 neon.tech
    註冊一個免費帳號…貼進 Render 的這個欄位」. Both halves name one company,
    and the layer underneath (SQLAlchemy) never cared which."""
    row = _database_row()
    text = f"{row.why} {row.how}"

    assert "Postgres" in text
    assert "貼進 Render" not in text


def test_naming_a_provider_is_allowed_only_as_one_example_among_others():
    """Naming Neon is genuinely useful -- somebody has to start somewhere.
    Naming it as THE answer is the part that breaks for everyone else."""
    row = _database_row()
    text = f"{row.why} {row.how}"

    if "Neon" in text:
        assert "例如" in text
        # 至少還有另一家，否則「例如」是騙人的
        assert any(other in text for other in ("Supabase", "Railway", "自架", "Aiven"))


def test_the_free_tier_warning_does_not_assume_which_free_tier():
    """The warning that matters is 「檔案型資料庫每次重新部署都會被清空」, and
    that is true of every container platform, not of one company."""
    row = _database_row()

    assert "在 Render 上" not in row.why


def test_where_to_paste_it_follows_the_platform_this_is_running_on(client, monkeypatch):
    """The `where` field on /api/setup/status used to be the literal string
    「Render 後台 → 你的服務 → 左邊選單 Environment」 for everybody."""
    monkeypatch.setenv("FLY_APP_NAME", "somebodys-app")

    where = client.get("/api/setup/status").json()["where"]

    assert "Fly.io" in where
    assert "Render" not in where


def test_and_says_something_useful_when_it_cannot_tell(client):
    where = client.get("/api/setup/status").json()["where"]

    assert where
    assert not any(company in where for company in COMPANIES)
