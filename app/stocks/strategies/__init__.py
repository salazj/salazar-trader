"""Stock trading strategies."""

from app.stocks.strategies.base import BaseStockStrategy
from app.stocks.strategies.momentum import StockMomentum
from app.stocks.strategies.mean_reversion import StockMeanReversion
from app.stocks.strategies.breakout import StockBreakout
from app.stocks.strategies.pullback import StockPullback
from app.stocks.strategies.news_gated import NewsGatedWatchlist

ALL_STOCK_STRATEGIES: list[type[BaseStockStrategy]] = [
    StockMomentum,
    StockMeanReversion,
    StockBreakout,
    StockPullback,
    NewsGatedWatchlist,
]

STRATEGY_REGISTRY: dict[str, type[BaseStockStrategy]] = {
    cls.name: cls for cls in ALL_STOCK_STRATEGIES
}

__all__ = [
    "BaseStockStrategy",
    "StockMomentum",
    "StockMeanReversion",
    "StockBreakout",
    "StockPullback",
    "NewsGatedWatchlist",
    "ALL_STOCK_STRATEGIES",
    "STRATEGY_REGISTRY",
]
