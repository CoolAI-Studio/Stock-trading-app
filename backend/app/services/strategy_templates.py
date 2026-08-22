"""現成的提醒範本：讓一則提醒可以用表單設定出來，不用寫 Python。

WHY THIS EXISTS. CLAUDE.md 把它寫成核心功能：

    不用寫 Python 就能設定的簡單價格提醒，是**核心功能**，不是加分項。

而在這之前它不存在。想要「台積電跌到 900 塊叫我」的唯一一條路，是到策略頁挑一個
範例、**在一個程式碼編輯器裡**把 `self.buy_below = 950.0` 改掉、存檔、啟用。範例檔
把改動縮小到「改三個數字」，但畫面上仍然是程式碼——而「改三個數字」和「不用寫
程式」對這個 app 的使用者是兩件不同的事。另一條路是 AI，但那需要一把金鑰，而 AI
不能是設定流程的必需品。

WHAT THIS IS BUILT ON, DELIBERATELY. 參數機制早就有了：一支策略宣告 `self.params`，
擁有者不改程式碼就能覆寫（services/strategy_runtime.py::_apply_params）。所以這裡
不做第二套訂閱系統——每一個範本就是一支很短的策略，底下是同一個 worker、同一套
節流、同一條通知重送、同一個沙箱。那條路已經被測過幾百次；一條新的沒有。

WRITING A TEMPLATE. 三條規矩，違反的話使用者看不出來但東西是壞的：

  1. **在做決定的地方讀 `self.params[...]`**，不要在 __init__ 把值抄進別的屬性。
     覆寫是在 __init__ 之後才注入的，抄過去的那一份不會更新。
  2. **平靜的行情要完全安靜。** 每天都叫的規則會被靜音，然後真正那一則也被靜音了。
  3. **表單問的欄位和程式讀的參數必須完全一致。** 少問一個，他拿到的是作者的預設值
     而畫面上不會說；多問一個，他填了一個數字然後什麼都沒發生。
     tests/test_alerts_without_writing_python.py 兩個方向都會檢查。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TemplateField:
    """表單上的一格。

    `help` 不是選填的：這份清單的讀者不是工程師，一個沒有說明的數字欄位就是一個
    他填不下去的欄位。
    """

    key: str
    label: str
    help: str
    kind: str = "number"  # number | text
    default: float | str = 0.0
    minimum: float | None = None


@dataclass(frozen=True)
class StrategyTemplate:
    key: str
    title: str
    summary: str
    good_for: str
    source: str
    fields: tuple[TemplateField, ...]


_PRICE_ALERT = '''class Strategy:
    """跌破或漲過你設定的價位就通知你。

    不判斷趨勢、不算指標，只做這一件事。0 表示那一邊不用管——「跌到 900 叫我」
    是一個完整的需求，不應該為了填滿表格逼人再想一個賣出價。
    """

    def __init__(self):
        self.name = "到價提醒"
        self.symbol = "2330.TW"
        self.params = {"buy_below": 0.0, "sell_above": 0.0}
        self.warmup_bars = 1
        self.timeframe = "1d"

    def on_bar(self, bar) -> str:
        buy_below = self.params["buy_below"]
        sell_above = self.params["sell_above"]
        # 0 是「這一邊關掉」，不是「零元」：沒有這個判斷，空白的那一側會變成
        # 一個每一根 K 棒都成立的條件，然後每天都通知。
        if buy_below > 0 and bar.close <= buy_below:
            return "BUY"
        if sell_above > 0 and bar.close >= sell_above:
            return "SELL"
        return "HOLD"
'''


_MA_BREAK = '''class Strategy:
    """跌破均線就通知你。

    用系統內建的 indicators.sma，不要自己算——自己算出來的數字會跟回測、指標
    清單和一般看盤軟體對不起來，而畫面上不會有任何地方告訴你差在哪。
    """

    def __init__(self):
        self.name = "跌破均線"
        self.symbol = "2330.TW"
        self.params = {"window": 20}
        self.closes = []
        self.was_above = False
        self.warmup_bars = 60
        self.timeframe = "1d"

    def on_bar(self, bar) -> str:
        window = self.params["window"]
        self.closes.append(bar.close)
        if len(self.closes) > window * 5:
            self.closes.pop(0)

        line = indicators.sma(self.closes, window)  # noqa: F821
        average = line[-1] if line else None
        if average is None:
            return "HOLD"

        # >= 而不是 >：一段完全平的行情裡，收盤價會剛好等於均線，用 > 的話
        # 「在均線上方」永遠是 False，接著真的跌下去也不會叫。平盤本身仍然是
        # 安靜的——沒有跌破就沒有交叉。
        above = bar.close >= average
        # 只在「剛剛跌破」那一根叫。持續在均線下方的每一天都叫，是把一則提醒
        # 變成一個每天都響的鬧鐘，而那種鬧鐘會被關掉。
        crossed_down = self.was_above and not above
        self.was_above = above
        if crossed_down:
            return "SELL"
        return "HOLD"
'''


_HIGH_BREAK = '''class Strategy:
    """創下這段期間的新高就通知你。"""

    def __init__(self):
        self.name = "突破新高"
        self.symbol = "2330.TW"
        self.params = {"window": 60}
        self.closes = []
        self.warmup_bars = 90
        self.timeframe = "1d"

    def on_bar(self, bar) -> str:
        window = self.params["window"]
        self.closes.append(bar.close)
        if len(self.closes) > window * 3:
            self.closes.pop(0)

        # 至少要有一段歷史才談得上「新高」：第一根 K 棒永遠是它自己的最高點。
        if len(self.closes) < 2:
            return "HOLD"
        previous = self.closes[-window - 1 : -1] if window < len(self.closes) else self.closes[:-1]
        if not previous:
            return "HOLD"
        if bar.close > max(previous):
            return "BUY"
        return "HOLD"
'''


_DRAWDOWN = '''class Strategy:
    """從最近的高點回落一定幅度就通知你。

    停損的形狀，但它只通知——要不要賣是你的決定，這個系統不下單。
    """

    def __init__(self):
        self.name = "從高點回落"
        self.symbol = "2330.TW"
        self.params = {"lookback": 20, "drop_pct": 10.0}
        self.closes = []
        self.warned = False
        self.warmup_bars = 40
        self.timeframe = "1d"

    def on_bar(self, bar) -> str:
        lookback = self.params["lookback"]
        drop_pct = self.params["drop_pct"]
        self.closes.append(bar.close)
        if len(self.closes) > lookback * 3:
            self.closes.pop(0)

        window = self.closes[-lookback:]
        peak = max(window)
        if peak <= 0:
            return "HOLD"
        fallen = (peak - bar.close) / peak * 100.0

        if fallen >= drop_pct:
            # 一次就好。跌深了之後每天都符合條件，而每天都講一次同一件事，
            # 是讓人把通知關掉最快的方法。回到高點附近才重新武裝。
            if not self.warned:
                self.warned = True
                return "SELL"
            return "HOLD"
        self.warned = False
        return "HOLD"
'''


_RSI_OVERSOLD = '''class Strategy:
    """RSI 跌到你設的水位就通知你（常被當成「可能超賣」）。"""

    def __init__(self):
        self.name = "RSI 超賣"
        self.symbol = "2330.TW"
        self.params = {"period": 14, "threshold": 30.0}
        self.closes = []
        self.was_above = True
        self.warmup_bars = 60
        self.timeframe = "1d"

    def on_bar(self, bar) -> str:
        period = self.params["period"]
        threshold = self.params["threshold"]
        self.closes.append(bar.close)
        if len(self.closes) > period * 6:
            self.closes.pop(0)

        line = indicators.rsi(self.closes, period)  # noqa: F821
        value = line[-1] if line else None
        if value is None:
            return "HOLD"

        above = value > threshold
        crossed_down = self.was_above and not above
        self.was_above = above
        if crossed_down:
            return "BUY"
        return "HOLD"
'''


TEMPLATES: tuple[StrategyTemplate, ...] = (
    StrategyTemplate(
        key="price_alert",
        title="到價提醒",
        summary="跌破或漲過你設的價位，就通知你。",
        good_for="只想要「跌到我想買的價位叫我」的時候。這是最單純的一種。",
        source=_PRICE_ALERT,
        fields=(
            TemplateField(
                key="buy_below",
                label="跌破多少通知我",
                help="例如 900。留 0 表示這一邊不用管。",
                default=0.0,
                minimum=0.0,
            ),
            TemplateField(
                key="sell_above",
                label="漲過多少通知我",
                help="例如 1200。留 0 表示這一邊不用管。",
                default=0.0,
                minimum=0.0,
            ),
        ),
    ),
    StrategyTemplate(
        key="ma_break",
        title="跌破均線",
        summary="收盤價從均線上方掉到下方的那一天，通知你。",
        good_for="想知道「走勢轉弱了」，但不想每天自己看線的時候。",
        source=_MA_BREAK,
        fields=(
            TemplateField(
                key="window",
                label="幾日均線",
                help="常見的是 20（月線）或 60（季線）。",
                default=20,
                minimum=2,
            ),
        ),
    ),
    StrategyTemplate(
        key="high_break",
        title="突破新高",
        summary="收盤價超過這段期間的最高點，就通知你。",
        good_for="想抓「開始強勢」的時候。",
        source=_HIGH_BREAK,
        fields=(
            TemplateField(
                key="window",
                label="看前面幾天",
                help="60 大約是一季。數字愈大，愈少叫、但每一次愈有意義。",
                default=60,
                minimum=2,
            ),
        ),
    ),
    StrategyTemplate(
        key="drawdown",
        title="從高點回落",
        summary="從最近的高點跌掉一定百分比，就通知你一次。",
        good_for="停損的形狀，但它只通知——要不要賣是你決定的。",
        source=_DRAWDOWN,
        fields=(
            TemplateField(
                key="lookback",
                label="高點看多久以內",
                help="20 大約是一個月。",
                default=20,
                minimum=2,
            ),
            TemplateField(
                key="drop_pct",
                label="跌幾 % 通知我",
                help="填 10 就是從高點跌 10%。",
                default=10.0,
                minimum=0.1,
            ),
        ),
    ),
    StrategyTemplate(
        key="rsi_oversold",
        title="RSI 超賣",
        summary="RSI 掉到你設的水位以下的那一天，通知你。",
        good_for="想在「跌深了」的時候被提醒看一眼。",
        source=_RSI_OVERSOLD,
        fields=(
            TemplateField(
                key="period",
                label="RSI 週期",
                help="幾乎所有看盤軟體的預設都是 14。",
                default=14,
                minimum=2,
            ),
            TemplateField(
                key="threshold",
                label="低於多少通知我",
                help="30 是常見的「超賣」水位。",
                default=30.0,
                minimum=1.0,
            ),
        ),
    ),
)


def get_template(key: str) -> StrategyTemplate | None:
    for template in TEMPLATES:
        if template.key == key:
            return template
    return None
