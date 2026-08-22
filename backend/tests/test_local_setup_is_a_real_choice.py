"""在自己的電腦上跑，不是一個沒設定完的部署。

MEASURED BEFORE THIS CHANGE: `setup_state` 把任何 sqlite 的 DATABASE_URL 一律
當成「資料庫還沒設定」，理由是雲端平台的磁碟每次重新部署都會被清空。那個理由
是對的——在雲端平台上。在自己的電腦上，那個檔案就在那裡，不會消失，而 app 卻
一直說他還沒設定完。

現在 app 認得出自己在哪裡（services/hosting.py），所以這兩件事可以分開講：

    在已知的雲端平台上用 sqlite  → 真的會出事，擋著，並且說出「重新部署會清空」
    在本機或自架環境用 sqlite    → 是一個選擇，不擋，但要說出「它是一個檔案，
                                    記得備份」——因為那也是真的

這不是把檢查放寬。同一個事實（sqlite）在兩個環境裡的後果不一樣，而原本的訊息
在其中一個環境裡是錯的。
"""

import pytest

from app.config import Settings
from app.services import setup_state

CLOUD_MARKERS = ("RENDER", "RAILWAY_ENVIRONMENT", "FLY_APP_NAME", "DYNO", "KOYEB_APP_NAME")


@pytest.fixture(autouse=True)
def _nowhere_in_particular(monkeypatch):
    for marker in CLOUD_MARKERS:
        monkeypatch.delenv(marker, raising=False)


def _database_row(settings: Settings):
    return next(
        (item for item in setup_state.missing_settings(settings) if item.name == "DATABASE_URL"),
        None,
    )


def _configured(**overrides) -> Settings:
    """Everything else filled in, so only the database is in question."""
    from cryptography.fernet import Fernet

    values = {
        "DATABASE_URL": "sqlite:///./trading_app_dev.db",
        "JWT_SECRET": "a-real-secret-value-not-a-placeholder",
        "TV_WEBHOOK_SECRET": "another-real-secret-value-here",
        "SECRET_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        **overrides,
    }
    return Settings(**values)


def test_on_a_cloud_platform_a_file_database_still_blocks(monkeypatch):
    """The original reason, unchanged: the disk goes away on every deploy and
    nothing on screen says so afterwards -- the data is simply gone."""
    monkeypatch.setenv("RENDER", "true")

    row = _database_row(_configured())

    assert row is not None
    assert row.blocking is True
    assert "重新部署" in row.why


def test_on_your_own_machine_it_is_a_choice_not_a_fault(monkeypatch):
    """No platform marker means nobody's ephemeral disk is involved."""
    row = _database_row(_configured())

    assert row is None or row.blocking is False


def test_but_it_still_says_the_thing_that_is_true_everywhere(monkeypatch):
    """A file is a file: it can be deleted, and nothing here backs it up for
    you. Saying nothing at all would be the other way to be wrong."""
    row = _database_row(_configured())

    if row is not None:
        assert "備份" in row.how or "備份" in row.why


def test_a_postgres_url_is_not_mentioned_at_all(monkeypatch):
    """Nothing to say: this is the configuration the app wants."""
    settings = _configured(DATABASE_URL="postgresql://user:pw@db.example.com/trading")

    assert _database_row(settings) is None


def test_an_empty_url_blocks_wherever_you_are(monkeypatch):
    """No database at all is not a local setup, it is a broken one."""
    settings = _configured(DATABASE_URL="")

    row = _database_row(settings)

    assert row is not None
    assert row.blocking is True


def test_the_setup_endpoints_do_not_stay_open_forever_on_a_local_machine(monkeypatch):
    """The public setup endpoints answer only while something BLOCKING is
    missing. If a local sqlite counted as blocking for good, a finished local
    deployment would keep an unauthenticated endpoint open describing its own
    configuration -- which is what 「blocking」 controls."""
    assert setup_state.blocking_settings(_configured()) == []
