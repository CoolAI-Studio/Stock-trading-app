"""TradingView 的訊號屬於誰，不能用「誰的策略剛好也叫這個代號」來猜。

MEASURED: `_resolve_user` 上半段的查詢是

    db.query(Strategy).filter(Strategy.symbol == symbol)

沒有 user_id 條件，然後依 name 或 symbol 取 `.first()`——連 ORDER BY 都沒有。
`Strategy` 的唯一鍵是 `(user_id, name)`，所以第二個帳號可以建一筆同代號、甚至
同名的策略。

攻擊者不需要知道 `TV_WEBHOOK_SECRET`：密鑰是**擁有者自己的 TradingView** 帶上來
的。最糟的情境是確定性的——TradingView 的警報是在 TV 那邊設定的，app 裡未必有
對應的 Strategy 列，那時對方那一筆就是唯一匹配，於是每一則警報都必定落進他的
帳本，而擁有者這邊完全靜默。

下半段的 fallback（有兩個以上帳號就回 None）已經修好了。這個檔案守的是上半段。
"""

from decimal import Decimal

import pytest

from app.api.routers.webhooks import _resolve_user
from app.models.enums import DataSource
from app.models.strategy import Strategy
from app.models.user import User


def _user(db_session, email: str) -> User:
    user = User(email=email, hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _strategy(db_session, user: User, name: str, symbol: str) -> Strategy:
    strategy = Strategy(
        user_id=user.id,
        name=name,
        symbol=symbol,
        data_source=DataSource.YFINANCE,
        source_code="class Strategy:\n    pass\n",
        code_hash=f"hash-{user.id}-{name}",
        default_quantity=Decimal(1),
    )
    db_session.add(strategy)
    db_session.commit()
    return strategy


def test_an_alert_goes_to_the_owner_when_only_the_owner_has_that_symbol(db_session):
    """The ordinary case must keep working: one account, one strategy."""
    owner = _user(db_session, "owner@example.com")
    _strategy(db_session, owner, "台積電", "2330.TW")

    resolved = _resolve_user(db_session, "2330.TW", "台積電")

    assert resolved is not None
    assert resolved.id == owner.id


def test_a_second_account_cannot_take_the_alert_by_naming_a_strategy_the_same(db_session):
    """MEASURED: `(user_id, name)` 是唯一鍵，所以「同名」在別的帳號底下完全合法。
    而查詢沒有 user_id 條件，`.first()` 拿到誰是資料庫的事。"""
    owner = _user(db_session, "owner@example.com")
    intruder = _user(db_session, "intruder@example.com")
    _strategy(db_session, owner, "台積電", "2330.TW")
    _strategy(db_session, intruder, "台積電", "2330.TW")

    resolved = _resolve_user(db_session, "2330.TW", "台積電")

    assert resolved is None or resolved.id == owner.id


def test_and_cannot_take_it_by_watching_the_same_symbol(db_session):
    owner = _user(db_session, "owner@example.com")
    intruder = _user(db_session, "intruder@example.com")
    _strategy(db_session, owner, "我的台積電", "2330.TW")
    _strategy(db_session, intruder, "他的台積電", "2330.TW")

    resolved = _resolve_user(db_session, "2330.TW", None)

    assert resolved is None or resolved.id == owner.id


def test_the_worst_case_is_the_one_that_used_to_be_certain(db_session):
    """擁有者在 app 裡**沒有**對應的策略列（警報是在 TradingView 那邊設的），
    而入侵者有一筆。原本那是唯一匹配，所以每一則警報都會落進他的帳本。"""
    owner = _user(db_session, "owner@example.com")
    intruder = _user(db_session, "intruder@example.com")
    _strategy(db_session, intruder, "劫走", "2330.TW")

    resolved = _resolve_user(db_session, "2330.TW", None)

    assert resolved is None or resolved.id == owner.id


def test_nothing_matching_and_two_accounts_still_refuses(db_session):
    """已經修好的那半，這裡一起釘住免得回頭。"""
    _user(db_session, "owner@example.com")
    _user(db_session, "intruder@example.com")

    assert _resolve_user(db_session, "0050.TW", None) is None


@pytest.mark.parametrize("name", ["台積電", None])
def test_a_single_owner_deployment_is_unaffected(db_session, name):
    """這一組修正不可以讓正常的一人部署變得更難用：唯一的帳號照樣收得到。"""
    owner = _user(db_session, "owner@example.com")
    _strategy(db_session, owner, "台積電", "2330.TW")

    resolved = _resolve_user(db_session, "2330.TW", name)

    assert resolved is not None
    assert resolved.id == owner.id
