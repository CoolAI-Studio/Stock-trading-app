"""Feeds a synthetic price sequence through the mock provider and calls
market_loop.tick_once() directly against the app's real configured
database -- so the full strategy -> signal -> pending-order pipeline is
provable end-to-end without waiting for the market to move or waiting
MARKET_DATA_POLL_INTERVAL_SEC between polls.

Requires at least one *active* strategy already registered (via the API)
for the given symbol -- this script only supplies prices, it doesn't
create strategies.

Usage: python scripts/simulate_tick.py <symbol> <price1> [<price2> ...]
Example: python scripts/simulate_tick.py AAPL 100 101 99 102 103 105 104 108
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.enums import DataSource  # noqa: E402
from app.services import market_loop  # noqa: E402
from app.services.market_data.providers.mock_provider import MockProvider  # noqa: E402
from app.services.market_data.service import MarketDataService  # noqa: E402


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        raise SystemExit(1)

    symbol = sys.argv[1].upper()
    prices = [float(p) for p in sys.argv[2:]]

    for price in prices:
        # A fresh MockProvider per tick means each price is used exactly as
        # given (no random-walk drift layered on top).
        provider = MockProvider(base_prices={symbol: price})
        service = MarketDataService(providers={DataSource.YFINANCE: provider})
        events = market_loop.tick_once(market_data_service=service)
        print(f"price={price}: {len(events)} event(s)")
        for event in events:
            print(f"  {event.type} {event.data}")


if __name__ == "__main__":
    main()
