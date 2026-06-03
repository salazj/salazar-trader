"""Top-movers screener: rank candidate tickers by recent price movement.

Uses the Alpaca historical-data API's snapshot endpoint, which returns
the latest trade plus the most recent daily bar (open / high / low /
close) in one request. We compute::

    pct_change = (latest_price - daily_open) / daily_open
    score      = abs(pct_change) * sqrt(dollar_volume / 1e6)

and return the top N tickers by score.

The screener is asynchronous-friendly but does its network call in a
worker thread because ``alpaca-py`` is sync-only. That keeps the
event loop responsive.

It does **not** add tickers to the universe by itself — it returns
candidates that the universe manager unions with the static and
news-driven sets, subject to the approved-ticker allow-list and the
overall ``max_stock_symbols`` cap.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Iterable

from app.config.settings import Settings
from app.monitoring import get_logger

logger = get_logger(__name__)


@dataclass
class MoverEntry:
    """One screened mover."""

    ticker: str
    last_price: float
    pct_change: float
    volume: float
    dollar_volume: float
    score: float

    def __repr__(self) -> str:
        return (
            f"MoverEntry({self.ticker} "
            f"px={self.last_price:.2f} "
            f"chg={self.pct_change*100:+.2f}% "
            f"vol={self.volume:,.0f} "
            f"score={self.score:.3f})"
        )


class TopMoversScreener:
    """Rank candidates by absolute % move scaled by dollar volume."""

    def __init__(
        self,
        settings: Settings,
        candidates: Iterable[str],
        top_n: int = 10,
        min_price: float = 5.0,
        max_price: float = 1000.0,
        min_dollar_volume: float = 5_000_000.0,
    ) -> None:
        self._settings = settings
        self._candidates = sorted({c.upper() for c in candidates})
        self._top_n = max(1, int(top_n))
        self._min_price = min_price
        self._max_price = max_price
        self._min_dollar_volume = min_dollar_volume

    @property
    def candidates(self) -> list[str]:
        return list(self._candidates)

    async def scan(self) -> list[MoverEntry]:
        """Run a snapshot scan over all candidates and return ranked movers."""
        if not self._candidates:
            return []
        try:
            snapshots = await asyncio.to_thread(self._fetch_snapshots)
        except Exception as exc:
            logger.warning("movers_scan_failed", error=str(exc))
            return []

        ranked: list[MoverEntry] = []
        for ticker, snap in snapshots.items():
            entry = self._snapshot_to_entry(ticker, snap)
            if entry is None:
                continue
            ranked.append(entry)

        ranked.sort(key=lambda m: m.score, reverse=True)
        logger.info(
            "movers_scan_complete",
            candidates=len(self._candidates),
            ranked=len(ranked),
            top=[m.ticker for m in ranked[: self._top_n]],
        )
        return ranked[: self._top_n]

    async def scan_tickers(self) -> list[str]:
        """Convenience wrapper that returns just the top tickers."""
        return [m.ticker for m in await self.scan()]

    def _fetch_snapshots(self) -> dict[str, object]:
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockSnapshotRequest
        except ImportError:
            logger.warning("alpaca_py_missing_for_movers_screener")
            return {}

        if not (
            self._settings.alpaca_api_key
            and self._settings.alpaca_secret_key
        ):
            logger.warning("movers_missing_alpaca_credentials")
            return {}

        client = StockHistoricalDataClient(
            self._settings.alpaca_api_key,
            self._settings.alpaca_secret_key,
        )

        out: dict[str, object] = {}
        for chunk in self._chunk(self._candidates, 100):
            try:
                req = StockSnapshotRequest(symbol_or_symbols=chunk)
                snaps = client.get_stock_snapshot(req)
                if isinstance(snaps, dict):
                    out.update(snaps)
            except Exception as exc:
                logger.warning(
                    "movers_chunk_failed",
                    chunk=chunk[:5],
                    error=str(exc),
                )
        return out

    @staticmethod
    def _chunk(items: list[str], size: int) -> Iterable[list[str]]:
        for i in range(0, len(items), size):
            yield items[i : i + size]

    def _snapshot_to_entry(self, ticker: str, snap) -> MoverEntry | None:
        try:
            latest_trade = getattr(snap, "latest_trade", None)
            daily = getattr(snap, "daily_bar", None) or getattr(snap, "previous_daily_bar", None)
            if latest_trade is None or daily is None:
                return None
            price = float(getattr(latest_trade, "price", 0.0) or 0.0)
            open_ = float(getattr(daily, "open", 0.0) or 0.0)
            volume = float(getattr(daily, "volume", 0.0) or 0.0)
        except Exception:
            return None

        if price <= 0 or open_ <= 0:
            return None
        if price < self._min_price or price > self._max_price:
            return None

        dollar_volume = price * volume
        if dollar_volume < self._min_dollar_volume:
            return None

        # Liquidity gate: skip names with a wide quoted spread (bad fills).
        max_spread_bps = float(getattr(self._settings, "stock_max_spread_bps", 0) or 0)
        if max_spread_bps > 0:
            quote = getattr(snap, "latest_quote", None)
            bid = float(getattr(quote, "bid_price", 0.0) or 0.0) if quote else 0.0
            ask = float(getattr(quote, "ask_price", 0.0) or 0.0) if quote else 0.0
            if bid > 0 and ask > 0:
                mid = (bid + ask) / 2.0
                spread_bps = (ask - bid) / mid * 10_000.0 if mid > 0 else 1e9
                if spread_bps > max_spread_bps:
                    return None

        pct_change = (price - open_) / open_
        # Cap the move term so we rank unusual *liquid* activity rather than
        # only chasing the most parabolic (already-extended) names.
        capped_move = min(abs(pct_change), 0.15)
        score = capped_move * math.sqrt(max(dollar_volume, 1.0) / 1_000_000.0)
        return MoverEntry(
            ticker=ticker,
            last_price=price,
            pct_change=pct_change,
            volume=volume,
            dollar_volume=dollar_volume,
            score=score,
        )
