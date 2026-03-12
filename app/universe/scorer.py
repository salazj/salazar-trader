"""
OpportunityScorer — computes a composite quality score per market.

The score reflects the quality of a trading opportunity, factoring in:
  - Spread quality (tighter is better)
  - Liquidity depth
  - Recent price momentum
  - Trade flow / activity
  - Volatility regime (moderate is best)
  - Market activity (more recent trades = better)
  - NLP/news relevance (optional)
  - ML model confidence (optional)
  - Estimated edge after fees/slippage
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.data.models import Market, OrderbookSnapshot
from app.monitoring import get_logger

logger = get_logger(__name__)


@dataclass
class ScoredMarket:
    market: Market
    score: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    filter_reason: str = ""

    @property
    def market_id(self) -> str:
        return self.market.market_id


@dataclass
class ScorerWeights:
    spread_quality: float = 0.20
    liquidity_depth: float = 0.20
    momentum: float = 0.10
    trade_flow: float = 0.15
    volatility_regime: float = 0.10
    market_activity: float = 0.15
    category_bonus: float = 0.05
    edge_estimate: float = 0.05


class OpportunityScorer:
    """Scores markets by trade opportunity quality."""

    def __init__(
        self,
        weights: ScorerWeights | None = None,
        category_weights: dict[str, float] | None = None,
    ) -> None:
        self._weights = weights or ScorerWeights()
        self._category_weights = category_weights or {}

    @property
    def weights(self) -> ScorerWeights:
        return self._weights

    def score(
        self,
        market: Market,
        book: OrderbookSnapshot | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ScoredMarket:
        """Compute a [0, 1] opportunity score for a single market."""
        meta = metadata or {}
        components: dict[str, float] = {}

        components["spread_quality"] = self._score_spread(book, meta)
        components["liquidity_depth"] = self._score_liquidity(book, meta)
        components["momentum"] = self._score_momentum(meta)
        components["trade_flow"] = self._score_trade_flow(meta)
        components["volatility_regime"] = self._score_volatility(meta)
        components["market_activity"] = self._score_activity(meta)
        components["category_bonus"] = self._score_category(market)
        components["edge_estimate"] = self._score_edge(meta)

        w = self._weights
        total = (
            components["spread_quality"] * w.spread_quality
            + components["liquidity_depth"] * w.liquidity_depth
            + components["momentum"] * w.momentum
            + components["trade_flow"] * w.trade_flow
            + components["volatility_regime"] * w.volatility_regime
            + components["market_activity"] * w.market_activity
            + components["category_bonus"] * w.category_bonus
            + components["edge_estimate"] * w.edge_estimate
        )

        total = max(0.0, min(1.0, total))

        return ScoredMarket(market=market, score=total, components=components)

    def score_batch(
        self,
        markets: list[Market],
        books: dict[str, OrderbookSnapshot] | None = None,
        metadata_map: dict[str, dict[str, Any]] | None = None,
    ) -> list[ScoredMarket]:
        """Score and rank a list of markets. Returns sorted by score descending."""
        books = books or {}
        metadata_map = metadata_map or {}
        scored = []
        for market in markets:
            mid = market.market_id
            book = books.get(mid)
            meta = metadata_map.get(mid)
            scored.append(self.score(market, book, meta))
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored

    def _score_spread(self, book: OrderbookSnapshot | None, meta: dict[str, Any]) -> float:
        spread = meta.get("spread")
        if book is not None and book.bids and book.asks:
            spread = book.asks[0].price - book.bids[0].price
        if spread is None:
            return 0.3
        if spread <= 0:
            return 1.0
        return max(0.0, 1.0 - spread * 5.0)

    def _score_liquidity(self, book: OrderbookSnapshot | None, meta: dict[str, Any]) -> float:
        liquidity = meta.get("liquidity", 0.0)
        if book is not None:
            liquidity = max(liquidity, sum(l.size for l in book.bids) + sum(l.size for l in book.asks))
        if liquidity <= 0:
            return 0.0
        return min(1.0, liquidity / 500.0)

    def _score_momentum(self, meta: dict[str, Any]) -> float:
        momentum = abs(meta.get("momentum", 0.0))
        return min(1.0, momentum * 10.0)

    def _score_trade_flow(self, meta: dict[str, Any]) -> float:
        flow = abs(meta.get("trade_flow", 0.0))
        return min(1.0, flow / 100.0)

    def _score_volatility(self, meta: dict[str, Any]) -> float:
        vol = meta.get("volatility", 0.0)
        if vol <= 0:
            return 0.3
        if vol < 0.01:
            return 0.5
        if vol < 0.05:
            return 1.0
        if vol < 0.10:
            return 0.6
        return 0.2

    def _score_activity(self, meta: dict[str, Any]) -> float:
        trade_count = meta.get("trade_count", meta.get("trade_count_1m", 0))
        volume = meta.get("volume_24h", meta.get("volume", 0)) or 0
        activity = float(trade_count) + float(volume) / 1000.0
        return min(1.0, activity / 20.0)

    def _score_category(self, market: Market) -> float:
        cat = _get_category(market)
        if not cat or not self._category_weights:
            return 0.5
        return self._category_weights.get(cat, 0.5)

    def _score_edge(self, meta: dict[str, Any]) -> float:
        edge = meta.get("estimated_edge", meta.get("edge", 0.0))
        if edge <= 0:
            return 0.3
        return min(1.0, edge * 20.0)


def _get_category(market: Market) -> str:
    cat = getattr(market, "category", "")
    if cat:
        return cat.lower()
    exchange_data = getattr(market, "exchange_data", {}) or {}
    return (exchange_data.get("category", "") or "").lower()
