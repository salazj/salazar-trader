"""News-gated watchlist strategy — only allow entries with bullish NLP signals."""

from __future__ import annotations

from app.data.models import PortfolioSnapshot
from app.models.enums import OrderType, StockAction
from app.stocks.models import StockFeatures, StockSignal
from app.stocks.strategies.base import BaseStockStrategy


class NewsGatedWatchlist(BaseStockStrategy):
    """News-driven entries: requires bullish NLP sentiment AND a technical
    confirmation (price holding above VWAP with non-negative momentum) so we
    don't chase a headline into a falling knife. Emits a fully protected signal
    (ATR stop + reward target).
    """

    name = "stock_news_gated"

    ATR_STOP_MULTIPLIER = 1.5
    REWARD_RISK = 2.0

    def __init__(self) -> None:
        self._bullish_symbols: set[str] = set()

    def update_sentiment(self, symbol: str, bullish: bool) -> None:
        """Called by the main loop when NLP signals arrive for a stock symbol."""
        if bullish:
            self._bullish_symbols.add(symbol.upper())
        else:
            self._bullish_symbols.discard(symbol.upper())

    def generate_signal(
        self, features: StockFeatures, portfolio: PortfolioSnapshot
    ) -> StockSignal | None:
        if features.symbol.upper() not in self._bullish_symbols:
            return None
        if features.last_price <= 0:
            return None

        # Technical confirmation: price above (or at) VWAP and not falling.
        above_vwap = features.vwap <= 0 or features.last_price >= features.vwap
        not_falling = features.momentum_1m >= 0
        if not (above_vwap and not_falling):
            return None

        last = features.last_price
        atr = features.atr_14 if features.atr_14 > 0 else last * 0.01
        stop = last - self.ATR_STOP_MULTIPLIER * atr
        target = last + self.REWARD_RISK * (last - stop)
        # Confidence scales with how cleanly price is trending up post-news.
        confidence = min(0.7, 0.45 + max(0.0, features.momentum_5m) * 8)
        return StockSignal(
            strategy_name=self.name,
            symbol=features.symbol,
            action=StockAction.BUY,
            confidence=confidence,
            suggested_price=last,
            order_type=OrderType.LIMIT,
            stop_price=round(stop, 2),
            target_price=round(target, 2),
            rationale="Bullish news + price holding above VWAP",
        )
