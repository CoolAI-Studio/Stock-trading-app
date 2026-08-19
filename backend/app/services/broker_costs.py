"""What each broker actually charges, as a list to pick from.

Which commission rate applies is not this app's decision. It is whatever the
owner's broker gives *them*, which varies by firm, by branch, by monthly
volume and by whatever promotion they opened the account under -- the published
台股 discounts run from about 1.68折 to 7.5折 and the per-trade floor from 1 元
to 20 元. Asking someone to type a rate they would have to go and look up, into
a box with a number I invented as its default, is how a backtest ends up priced
under conditions nobody has.

So: a list of the common cases with a source and a date, every one of them
editable, and 自訂 for anyone whose deal is not on it. The rates below were
checked against public 2026 rate cards; the `note` on each says what it
assumes, because the discount tier is the part most likely to be wrong for any
given person.

Sources (checked 2026-08-19):
- 台股 board rate 0.1425% and the 0.3% sell tax: standard, set by regulation.
- Per-broker discounts and floors: chihyun.com.tw/stock-fee/ and money101.com.tw.
- 現股當沖 sell tax halved to 0.15%, extended to 2027-12-31 (證券交易稅條例
  第2條之2, 立法院三讀 2024-12-31, effective 2025-01-04).
- Firstrade: US$0 commission on stocks and ETFs; the SEC fee on sales is
  US$0.00002060 per dollar as of 2026-04-06.
- Binance spot: 0.1% per side, no transaction tax.
"""

from dataclasses import dataclass
from decimal import Decimal

# The regulated Taiwan numbers, named rather than repeated, because a typo in
# one copy of 0.001425 would be invisible.
_TW_BOARD_RATE = Decimal("0.001425")
_TW_SELL_TAX = Decimal("0.003")
_TW_DAY_TRADE_SELL_TAX = Decimal("0.0015")


def _discount(tenths: str) -> Decimal:
    """折 to a rate. 2.8折 means 28% of the board rate, not 2.8% off it --
    worth spelling out, because reading it the other way is a factor of three
    error in the direction that flatters the strategy."""
    return _TW_BOARD_RATE * Decimal(tenths) / Decimal(10)


@dataclass(frozen=True)
class BrokerCostPreset:
    id: str
    label: str
    market: str
    commission_rate: Decimal
    minimum_fee: Decimal
    sell_tax_rate: Decimal
    note: str


_PRESETS: tuple[BrokerCostPreset, ...] = (
    BrokerCostPreset(
        id="tw-board",
        label="台股牌告費率（不打折）",
        market="TW",
        commission_rate=_TW_BOARD_RATE,
        minimum_fee=Decimal(20),
        sell_tax_rate=_TW_SELL_TAX,
        note="0.1425% 原價，最低 20 元。沒談過折扣、或想看最保守的結果就用這個。",
    ),
    BrokerCostPreset(
        id="tw-cathay",
        label="國泰證券（電子下單 2.8 折）",
        market="TW",
        commission_rate=_discount("2.8"),
        minimum_fee=Decimal(1),
        sell_tax_rate=_TW_SELL_TAX,
        note="2.8 折、最低 1 元。零股與整股都是 1 元下限。",
    ),
    BrokerCostPreset(
        id="tw-sinopac",
        label="永豐金證券（電子下單 2.8 折）",
        market="TW",
        commission_rate=_discount("2.8"),
        minimum_fee=Decimal(20),
        sell_tax_rate=_TW_SELL_TAX,
        note="2.8 折、最低 20 元（盤中零股最低 1 元）。",
    ),
    BrokerCostPreset(
        id="tw-shinkong",
        label="新光證券（電子下單 2.8 折）",
        market="TW",
        commission_rate=_discount("2.8"),
        minimum_fee=Decimal(20),
        sell_tax_rate=_TW_SELL_TAX,
        note="2.8 折、最低 20 元（盤中零股最低 1 元）。",
    ),
    BrokerCostPreset(
        id="tw-esun-fugle",
        label="玉山證券 富果帳戶（3.8 折）",
        market="TW",
        commission_rate=_discount("3.8"),
        minimum_fee=Decimal(12),
        sell_tax_rate=_TW_SELL_TAX,
        note="3.8 折、最低 12 元，是少數把下限壓在 20 元以下的。實際折數依方案 3.8～6 折。",
    ),
    BrokerCostPreset(
        id="tw-yuanta",
        label="元大證券（電子下單 5 折）",
        market="TW",
        commission_rate=_discount("5"),
        minimum_fee=Decimal(20),
        sell_tax_rate=_TW_SELL_TAX,
        note="牌告 5～6 折、最低 20 元。大戶或專案可能更低，以你實際拿到的為準。",
    ),
    BrokerCostPreset(
        id="tw-fubon",
        label="富邦證券（電子下單 5 折）",
        market="TW",
        commission_rate=_discount("5"),
        minimum_fee=Decimal(20),
        sell_tax_rate=_TW_SELL_TAX,
        note="牌告 4～6 折、最低 20 元（盤中零股最低 1 元）。這裡取中間值 5 折。",
    ),
    BrokerCostPreset(
        id="tw-kgi",
        label="凱基證券（電子下單 6 折）",
        market="TW",
        commission_rate=_discount("6"),
        minimum_fee=Decimal(20),
        sell_tax_rate=_TW_SELL_TAX,
        note="6 折、最低 20 元（盤中零股最低 1 元）。",
    ),
    BrokerCostPreset(
        id="tw-day-trade",
        label="台股現股當沖（2.8 折，證交稅減半）",
        market="TW",
        commission_rate=_discount("2.8"),
        minimum_fee=Decimal(20),
        sell_tax_rate=_TW_DAY_TRADE_SELL_TAX,
        note="當沖賣出證交稅為 0.15%（減半優惠施行至 2027/12/31），手續費仍兩邊都收。",
    ),
    BrokerCostPreset(
        id="us-firstrade",
        label="Firstrade（美股 0 佣金）",
        market="US",
        commission_rate=Decimal(0),
        minimum_fee=Decimal(0),
        # Not zero, and not a tax in the Taiwan sense -- it is the SEC's fee on
        # sales. It sits in this field because it behaves identically: charged
        # on the way out, proportional to value.
        sell_tax_rate=Decimal("0.0000206"),
        note="股票與 ETF 免佣金；賣出時仍有 SEC 規費約 0.00206%。金額極小但不是零。",
    ),
    BrokerCostPreset(
        id="us-zero-commission",
        label="美股 0 佣金券商（一般）",
        market="US",
        commission_rate=Decimal(0),
        minimum_fee=Decimal(0),
        sell_tax_rate=Decimal("0.0000206"),
        note="Robinhood、Schwab 這類 0 佣金券商的一般情況，賣出含 SEC 規費。",
    ),
    BrokerCostPreset(
        id="crypto-binance",
        label="幣安現貨（0.1%）",
        market="CRYPTO",
        commission_rate=Decimal("0.001"),
        minimum_fee=Decimal(0),
        sell_tax_rate=Decimal(0),
        note="現貨掛單與吃單皆 0.1%，沒有交易稅，也沒有最低手續費。",
    ),
)

_BY_ID = {preset.id: preset for preset in _PRESETS}


def catalogue() -> list[BrokerCostPreset]:
    return list(_PRESETS)


def get(preset_id: str) -> BrokerCostPreset:
    """Raises rather than falling back.

    A silent default would price a backtest under a broker the owner did not
    choose, and the result would look exactly as authoritative as a correct
    one.
    """
    return _BY_ID[preset_id]
