"""Import every model module so Base.metadata is fully populated before
Alembic autogenerate (or Base.metadata.create_all in tests) runs. This file
being complete is load-bearing -- a missing import here means a table is
silently absent from migrations."""

from app.models.ai import AiSettings  # noqa: F401
from app.models.backtest import BacktestRun  # noqa: F401
from app.models.backup import BackupSchedule  # noqa: F401,E402
from app.models.broker import BrokerCredential  # noqa: F401
from app.models.market import MarketBar, MarketQuote  # noqa: F401
from app.models.notification import NotificationChannel, NotificationLog  # noqa: F401
from app.models.order import Order  # noqa: F401
from app.models.position import Position  # noqa: F401
from app.models.risk import RiskSettings  # noqa: F401
from app.models.strategy import Strategy, StrategyAlert  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.watchlist import WatchlistItem  # noqa: F401,E402
from app.models.webhook import TradingViewWebhookLog  # noqa: F401
