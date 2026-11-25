"""Strategy-agnostic, safe-by-default execution layer for Polymarket (specialist 07).

paper by default; live requires config mode=live + TRADING_MODE=live + I_UNDERSTAND_REAL_MONEY=yes.
"""
from .models import BUY, SELL, Level, OrderBook, MarketInfo, MarketSnapshot, OrderIntent, Order, Fill, Position  # noqa: F401
from .risk import RiskLimits, RiskManager, AccountView, Decision  # noqa: F401
from .broker import Broker, PaperBroker, PolymarketLiveBroker, LiveTradingRefused, assert_live_allowed  # noqa: F401
from .strategy_api import Strategy, ExampleWiringStrategy, load_strategy  # noqa: F401
