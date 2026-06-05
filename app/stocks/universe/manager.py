"""Stock universe manager — selects and maintains the active stock list.

Supports five modes (``settings.stock_universe_mode``):

* ``manual``           — fixed list from ``STOCK_TICKERS``
* ``auto``             — broker catalog scan (legacy, weak filters)
* ``news_driven``      — core list ∪ tickers flagged by news (phi3 entities)
* ``top_movers``       — core list ∪ Alpaca-screened top movers
* ``news_plus_movers`` — core list ∪ hot tickers ∪ top movers

Dynamic modes always keep the core ``STOCK_TICKERS`` (so the bot has a
baseline universe even with no news) and union additional candidates
that pass the ``APPROVED_STOCK_TICKERS`` allow-list and the broker's
overall cap (``max_stock_symbols``).
"""

from __future__ import annotations

from typing import Any

from app.brokers.base import BaseBrokerMarketData
from app.config.settings import Settings
from app.monitoring import get_logger
from app.stocks.universe.filters import StockFilter
from app.stocks.universe.hot_tickers import HotTickerTracker
from app.stocks.universe.movers import TopMoversScreener
from app.stocks.universe.name_resolver import (
    ALL_KNOWN_TICKERS,
    NameResolver,
    SP_100_TICKERS,
)
from app.stocks.universe.scanner import StockUniverseScanner

logger = get_logger(__name__)


_DYNAMIC_MODES = {"news_driven", "top_movers", "news_plus_movers"}


class StockUniverseManager:
    """Manages the active set of stock symbols for trading."""

    def __init__(
        self,
        settings: Settings,
        market_data: BaseBrokerMarketData,
        name_resolver: NameResolver | None = None,
        hot_tracker: HotTickerTracker | None = None,
        movers_screener: TopMoversScreener | None = None,
    ) -> None:
        self._settings = settings
        self._scanner = StockUniverseScanner(market_data)
        self._filter = StockFilter(
            min_price=settings.stock_min_price,
            max_price=settings.stock_max_price,
            min_volume=settings.stock_min_volume,
            sectors=(
                [s.strip() for s in settings.stock_sector_include.split(",") if s.strip()]
                if settings.stock_sector_include
                else None
            ),
        )
        self._active_symbols: list[str] = []
        self._max_symbols = settings.max_stock_symbols

        approved = settings.approved_ticker_set
        self._resolver = name_resolver or NameResolver(
            allowed_tickers=approved or ALL_KNOWN_TICKERS
        )
        self._hot_tracker = hot_tracker or HotTickerTracker(
            resolver=self._resolver,
            ttl_minutes=settings.stock_hot_ticker_ttl_minutes,
            max_tickers=settings.stock_max_hot_tickers,
            min_confidence=settings.stock_hot_min_confidence,
            min_relevance=settings.stock_hot_min_relevance,
        )

        candidates_setting = (settings.stock_universe_candidates or "").strip()
        if candidates_setting:
            candidate_pool = {
                t.strip().upper() for t in candidates_setting.split(",") if t.strip()
            }
        else:
            candidate_pool = set(SP_100_TICKERS) | {"SPY", "QQQ", "IWM", "DIA"}
        if approved:
            candidate_pool &= approved
        self._movers_screener = movers_screener or TopMoversScreener(
            settings=settings,
            candidates=candidate_pool,
            top_n=settings.stock_movers_top_n,
            min_price=settings.stock_min_price,
            max_price=settings.stock_max_price,
            min_dollar_volume=settings.stock_movers_min_dollar_volume,
        )

    # ── Accessors ──────────────────────────────────────────────────────────

    @property
    def active_symbols(self) -> list[str]:
        return list(self._active_symbols)

    @property
    def resolver(self) -> NameResolver:
        return self._resolver

    @property
    def hot_tracker(self) -> HotTickerTracker:
        return self._hot_tracker

    @property
    def movers_screener(self) -> TopMoversScreener:
        return self._movers_screener

    @property
    def mode(self) -> str:
        return self._settings.stock_universe_mode.lower()

    @property
    def is_dynamic(self) -> bool:
        return self.mode in _DYNAMIC_MODES

    # ── Selection entry points ─────────────────────────────────────────────

    async def initial_selection(self) -> list[str]:
        """Build the very first active universe at bot startup."""
        self._active_symbols = await self._select()
        logger.info(
            "stock_universe_initial",
            mode=self.mode,
            symbols=self._active_symbols,
            count=len(self._active_symbols),
        )
        return self._active_symbols

    async def refresh(self) -> list[str]:
        """Re-run universe selection (used by the periodic refresh loop)."""
        self._active_symbols = await self._select()
        logger.info(
            "stock_universe_refreshed",
            mode=self.mode,
            symbols=self._active_symbols,
            count=len(self._active_symbols),
        )
        return self._active_symbols

    # ── Implementation ─────────────────────────────────────────────────────

    async def _select(self) -> list[str]:
        mode = self.mode
        core = self._core_symbols()
        approved = self._settings.approved_ticker_set

        if mode == "manual":
            return self._apply_caps(core, approved)

        if mode == "auto":
            return await self._select_auto(approved)

        candidates: list[str] = []
        if mode in {"news_driven", "news_plus_movers"}:
            candidates.extend(self._select_news_driven())
        if mode in {"top_movers", "news_plus_movers"}:
            candidates.extend(await self._select_top_movers())

        if mode not in _DYNAMIC_MODES and mode != "manual" and mode != "auto":
            logger.warning("stock_universe_unknown_mode", mode=mode)

        merged = list(core) + [c for c in candidates if c not in core]
        return self._apply_caps(merged, approved)

    def _core_symbols(self) -> list[str]:
        tickers_str = (self._settings.stock_tickers or "").strip()
        if not tickers_str:
            return []
        seen: set[str] = set()
        out: list[str] = []
        for raw in tickers_str.split(","):
            sym = raw.strip().upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            out.append(sym)
        return out

    async def _select_auto(self, approved: set[str]) -> list[str]:
        assets = await self._scanner.scan(
            min_price=self._settings.stock_min_price,
            max_price=self._settings.stock_max_price,
            min_volume=self._settings.stock_min_volume,
        )
        filtered = self._filter.apply(assets)
        symbols = [a["symbol"] for a in filtered if "symbol" in a]
        return self._apply_caps(symbols, approved)

    def _select_news_driven(self) -> list[str]:
        hot = self._hot_tracker.get_hot_tickers(
            limit=self._settings.stock_max_hot_tickers
        )
        if not hot:
            return []
        logger.info("stock_universe_news_hot", tickers=hot)
        return hot

    async def _select_top_movers(self) -> list[str]:
        try:
            movers = await self._movers_screener.scan_tickers()
        except Exception as exc:
            logger.warning("stock_universe_movers_error", error=str(exc))
            return []
        if movers:
            logger.info("stock_universe_movers", tickers=movers)
        return movers

    def _apply_caps(
        self, symbols: list[str], approved: set[str]
    ) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for sym in symbols:
            up = sym.upper().strip()
            if not up or up in seen:
                continue
            if approved and up not in approved:
                continue
            seen.add(up)
            out.append(up)
            if len(out) >= self._max_symbols:
                break
        return out
