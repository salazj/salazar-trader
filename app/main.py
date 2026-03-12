"""
Main entry point for the trading bot.

Wires together all modules and runs the main trading loop in the
configured mode (dry_run, live, backtest, replay).

Exchange-agnostic: uses the adapter factory to instantiate the
correct exchange adapter based on the EXCHANGE config setting.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

import click

from app.config.settings import Settings, get_settings
from app.data.features import FeatureEngine
from app.data.models import Market, MarketFeatures, Side, Signal, Trade, TradingMode
from app.data.orderbook import OrderbookManager
from app.decision.engine import DecisionEngine, signal_to_normalized
from app.decision.ensemble import DecisionMode, EnsembleConfig
from app.decision.signals import IntelligenceLayer, NormalizedSignal
from app.exchanges import build_exchange_adapter
from app.exchanges.base import BaseExchangeAdapter
from app.execution.engine import ExecutionEngine
from app.monitoring import get_logger, setup_logging
from app.universe.manager import UniverseManager
from app.monitoring.health import HealthServer
from app.monitoring.logger import metrics
from app.news.ingestion import NewsIngestionService
from app.nlp.classifier import HybridClassifier, KeywordClassifier
from app.nlp.pipeline import NlpPipeline, nlp_signal_to_layered
from app.nlp.providers.llm_provider import build_llm_classifier
from app.nlp.providers.mock import MockProvider
from app.nlp.providers.file_provider import FileProvider
from app.nlp.providers.newsapi import NewsApiProvider
from app.portfolio.tracker import PortfolioTracker
from app.risk.manager import RiskManager
from app.storage.repository import Repository
from app.strategies.base import BaseStrategy, StrategyRegistry, _import_all_strategies

logger = get_logger(__name__)


class TradingBot:
    """
    Main orchestrator. Manages the lifecycle of all components and runs
    the core trading loop.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        setup_logging(
            level=self._settings.log_level,
            json_output=self._settings.environment.value == "production",
        )

        self._mode = TradingMode.DRY_RUN if self._settings.dry_run else TradingMode.LIVE
        self._running = False
        self._is_equities = self._settings.asset_class == "equities"

        # Shared components
        self._portfolio = PortfolioTracker(self._settings)
        self._repository = Repository(self._settings.database_url)

        # NLP pipeline + news ingestion (shared across asset classes)
        self._nlp_pipeline = self._build_nlp_pipeline()
        self._news_service = NewsIngestionService(
            pipeline=self._nlp_pipeline,
            poll_interval=float(self._settings.news_poll_interval),
        )
        self._setup_nlp_providers()

        if self._is_equities:
            self._init_equities()
        else:
            self._init_prediction_markets()

    def _init_prediction_markets(self) -> None:
        """Set up all prediction-market-specific components."""
        # Exchange adapter (Polymarket or Kalshi)
        self._adapter: BaseExchangeAdapter = build_exchange_adapter(self._settings)

        self._risk_manager = RiskManager(self._settings)
        self._execution = ExecutionEngine(self._settings, self._adapter.execution, self._risk_manager)
        self._risk_manager.set_cancel_all_callback(self._execution.cancel_all_orders)
        self._orderbook = OrderbookManager()
        self._feature_engines: dict[str, FeatureEngine] = {}

        # Decision engine (three-layer ensemble)
        ensemble_cfg = EnsembleConfig(
            weight_l1=self._settings.ensemble_weight_l1,
            weight_l2=self._settings.ensemble_weight_l2,
            weight_l3=self._settings.ensemble_weight_l3,
            min_confidence=self._settings.min_ensemble_confidence,
            min_layers_agree=self._settings.min_layers_agree,
            mode=DecisionMode(self._settings.decision_mode),
            min_evidence_signals=self._settings.min_evidence_signals,
            large_trade_threshold=self._settings.large_trade_threshold,
            large_trade_min_layers=self._settings.large_trade_min_layers,
            conflict_tolerance=self._settings.conflict_tolerance,
        )
        ensemble_cfg.apply_mode_defaults()
        self._decision_engine = DecisionEngine(config=ensemble_cfg)

        # Level 1: all rule-based strategies
        _import_all_strategies()
        self._l1_strategies: list[BaseStrategy] = []
        for name in StrategyRegistry.available():
            try:
                cls = StrategyRegistry.get(name)
                self._l1_strategies.append(cls(self._settings))
            except Exception:
                logger.warning("strategy_init_failed", name=name)

        # Level 2: ML strategy
        self._ml_strategy: BaseStrategy | None = None
        for s in self._l1_strategies:
            if s.name == "event_probability_model":
                self._ml_strategy = s
                break

        self._news_service.set_market_provider(lambda: self._active_markets)

        # Universe selection (upstream from strategy execution)
        self._universe = UniverseManager(self._settings, self._adapter.market_data)

        # Health endpoint
        self._health_server = HealthServer(
            portfolio_snapshot_fn=self._portfolio.get_snapshot,
            is_halted_fn=lambda: self._risk_manager.is_halted,
            ws_connected_fn=lambda: self._adapter.websocket.is_connected,
        )

        # Markets to trade
        self._active_markets: list[Market] = []
        self._instrument_to_market: dict[str, Market] = {}

    def _init_equities(self) -> None:
        """Set up all equities-specific components."""
        from app.brokers import build_broker_adapter
        from app.stocks.execution import StockExecutionEngine
        from app.stocks.features import StockFeatureEngine
        from app.stocks.risk import StockRiskManager
        from app.stocks.strategies import ALL_STOCK_STRATEGIES
        from app.stocks.universe import StockUniverseManager

        self._broker = build_broker_adapter(self._settings)
        self._stock_risk = StockRiskManager(self._settings)
        self._stock_execution = StockExecutionEngine(
            self._settings, self._broker.execution, self._stock_risk
        )
        self._stock_risk.set_cancel_all_callback(self._stock_execution.cancel_all_orders)
        self._stock_universe = StockUniverseManager(
            self._settings, self._broker.market_data
        )
        self._stock_strategies = [cls() for cls in ALL_STOCK_STRATEGIES]
        self._stock_feature_engines: dict[str, StockFeatureEngine] = {}
        self._active_markets: list[Market] = []

        self._health_server = HealthServer(
            portfolio_snapshot_fn=self._portfolio.get_snapshot,
            is_halted_fn=lambda: self._stock_risk.is_halted,
            ws_connected_fn=lambda: self._broker.streaming.is_connected,
        )

    def _build_nlp_pipeline(self) -> NlpPipeline:
        llm = build_llm_classifier(
            provider=self._settings.llm_provider,
            model_name=self._settings.llm_model_name,
            base_url=self._settings.llm_base_url,
            api_key=self._settings.llm_api_key,
            timeout=self._settings.llm_timeout_seconds,
        )

        if llm is not None:
            classifier = HybridClassifier(
                keyword=KeywordClassifier(),
                llm=llm,
                llm_confidence_threshold=self._settings.llm_confidence_threshold,
            )
            logger.info(
                "nlp_pipeline_hybrid",
                llm_provider=self._settings.llm_provider,
                llm_model=self._settings.llm_model_name,
            )
        else:
            classifier = KeywordClassifier()
            logger.info("nlp_pipeline_keyword_only")

        return NlpPipeline(classifier=classifier)

    def _setup_nlp_providers(self) -> None:
        provider_name = self._settings.nlp_provider.lower()
        if provider_name == "mock":
            self._news_service.register_provider(MockProvider())
        elif provider_name == "file":
            self._news_service.register_provider(
                FileProvider(directory=self._settings.news_file_dir)
            )
        elif provider_name == "newsapi":
            self._news_service.register_provider(
                NewsApiProvider(api_key=self._settings.newsapi_key)
            )
        elif provider_name == "none":
            pass
        else:
            logger.warning("unknown_nlp_provider", name=provider_name)
            self._news_service.register_provider(MockProvider())

    async def start(self, market_slugs: list[str] | None = None) -> None:
        if self._is_equities:
            await self._start_equities()
            return

        strategy_names = [s.name for s in self._l1_strategies]
        logger.info(
            "bot_starting",
            mode=self._mode.value,
            exchange=self._settings.exchange,
            strategies=strategy_names,
            dry_run=self._settings.dry_run,
            llm_provider=self._settings.llm_provider,
        )

        await self._repository.initialize()

        # Fetch actual exchange balance and sync portfolio
        try:
            exchange_balance = await self._adapter.execution.get_balance()
            if exchange_balance > 0:
                self._portfolio._cash = exchange_balance
                self._portfolio._initial_cash = exchange_balance
                logger.info("exchange_balance_synced", balance=exchange_balance)
            else:
                logger.warning(
                    "exchange_balance_zero",
                    balance=exchange_balance,
                    hint="API returned $0. Check that API credentials are valid and KALSHI_DEMO_MODE matches your intended account.",
                )
        except Exception as e:
            logger.warning("exchange_balance_fetch_failed", error=str(e))

        # Strategic market-universe selection with retry
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            self._active_markets = await self._universe.initial_selection(
                market_slugs=market_slugs,
            )
            if self._active_markets:
                break
            if attempt < max_retries:
                logger.warning(
                    "no_markets_found_retrying",
                    attempt=attempt,
                    max_retries=max_retries,
                    next_retry_seconds=30,
                )
                await asyncio.sleep(30)

        if not self._active_markets:
            raise RuntimeError(
                "No active markets found after scanning. "
                "This usually means the exchange API returned no markets, "
                "or your category/filter settings are too restrictive. "
                "Try broadening your categories or leaving filters at defaults."
            )

        instrument_ids = await self._setup_market_instruments(self._active_markets)

        logger.info(
            "markets_loaded",
            count=len(self._active_markets),
            instruments=len(instrument_ids),
            watchlist_stats=self._universe.stats,
        )

        # Fetch initial orderbook snapshots via REST (only overwrite if API returns data)
        for iid in instrument_ids:
            try:
                book_data = await self._adapter.market_data.get_orderbook(iid)
                bids = book_data.get("bids", [])
                asks = book_data.get("asks", [])
                if bids or asks:
                    market = self._instrument_to_market[iid]
                    self._orderbook.apply_snapshot(
                        market_id=market.market_id,
                        instrument_id=iid,
                        bids=bids,
                        asks=asks,
                    )
            except Exception as e:
                logger.warning("initial_book_fetch_failed", instrument_id=iid, error=str(e))

        # Subscribe to WebSocket feeds
        ws = self._adapter.websocket
        ws.subscribe_book(instrument_ids)
        ws.subscribe_trades(instrument_ids)
        ws.subscribe_user()
        ws.on("book", self._on_book_message)
        ws.on("trade", self._on_trade_message)
        ws.on("user", self._on_user_message)

        # Recover positions from previous session
        await self._recover_positions()

        # Start portfolio daily tracking
        self._portfolio.start_new_day()

        # Start health endpoint and main loops
        await self._health_server.start()
        self._running = True
        await asyncio.gather(
            ws.connect(),
            self._intelligence_loop(),
            self._housekeeping_loop(),
            self._universe_refresh_loop(),
            self._news_service.start(),
        )

    async def stop(self) -> None:
        logger.info("bot_stopping")
        self._running = False

        if self._is_equities:
            await self._stop_equities()
            return

        self._universe.stop()
        await self._news_service.stop()
        await self._health_server.stop()
        await self._execution.cancel_all_orders()
        await self._persist_positions()
        await self._adapter.close()

        summary = self._portfolio.export_summary(
            self._settings.reports_dir / "final_portfolio.json"
        )
        logger.info("bot_stopped", **{k: v for k, v in summary.items() if k != "positions"})
        await self._repository.close()

    # ── Equities Mode ──────────────────────────────────────────────────

    async def _start_equities(self) -> None:
        """Start the bot in equities mode with the Alpaca broker."""
        logger.info(
            "bot_starting_equities",
            mode=self._mode.value,
            broker=self._settings.broker,
            dry_run=self._settings.dry_run,
            strategies=[s.name for s in self._stock_strategies],
        )

        await self._repository.initialize()

        # Fetch actual broker balance and sync portfolio
        try:
            acct = await self._broker.execution.get_account()
            broker_balance = float(acct.get("cash", acct.get("buying_power", 0)))
            if broker_balance > 0:
                self._portfolio._cash = broker_balance
                self._portfolio._initial_cash = broker_balance
                logger.info("broker_balance_synced", balance=broker_balance)
        except Exception as e:
            logger.warning("broker_balance_fetch_failed", error=str(e))

        symbols = await self._stock_universe.initial_selection()
        if not symbols:
            raise RuntimeError(
                "No stock symbols selected. "
                "Add ticker symbols in the configuration, "
                "or switch universe mode to 'filtered' to discover stocks automatically."
            )

        from app.stocks.features import StockFeatureEngine

        for sym in symbols:
            self._stock_feature_engines[sym] = StockFeatureEngine(sym)
            self._stock_feature_engines[sym].start_new_day()

        logger.info("stocks_loaded", count=len(symbols), symbols=symbols[:10])

        await self._health_server.start()
        self._running = True
        self._portfolio.start_new_day()

        await asyncio.gather(
            self._broker.streaming.connect(),
            self._stock_intelligence_loop(),
            self._stock_housekeeping_loop(),
            self._news_service.start(),
        )

    async def _stop_equities(self) -> None:
        await self._news_service.stop()
        await self._health_server.stop()
        await self._stock_execution.cancel_all_orders()
        await self._broker.close()
        await self._repository.close()
        logger.info("bot_stopped_equities")

    async def _stock_intelligence_loop(self) -> None:
        """Main loop for equities trading."""
        while self._running:
            await asyncio.sleep(5.0)

            if self._stock_risk.is_halted:
                logger.warning("stock_trading_halted")
                continue

            if not self._settings.allow_extended_hours and not self._broker.is_market_open():
                continue

            for symbol, engine in self._stock_feature_engines.items():
                try:
                    quote_data = await self._broker.market_data.get_quote(symbol)
                    engine.update_quote(
                        bid=quote_data.get("bid", 0),
                        ask=quote_data.get("ask", 0),
                        last=quote_data.get("last", quote_data.get("bid", 0)),
                    )

                    features = engine.compute()
                    portfolio_snap = self._portfolio.get_snapshot()

                    for strat in self._stock_strategies:
                        try:
                            sig = strat.generate_signal(features, portfolio_snap)
                            if sig is not None and sig.action.value != "HOLD":
                                await self._stock_execution.process_signal(
                                    sig, features, portfolio_snap, broker=self._broker
                                )
                        except Exception:
                            logger.exception("stock_strategy_error", strategy=strat.name)

                except Exception as exc:
                    logger.error("stock_loop_error", symbol=symbol, error=str(exc))
                    metrics.increment("stock_loop_errors")

    async def _stock_housekeeping_loop(self) -> None:
        """Periodic maintenance for equities mode."""
        while self._running:
            await asyncio.sleep(60.0)
            try:
                snap = self._portfolio.get_snapshot()
                await self._repository.save_pnl_snapshot(
                    cash=snap.cash,
                    total_exposure=snap.total_exposure,
                    total_unrealized=snap.total_unrealized_pnl,
                    total_realized=snap.total_realized_pnl,
                    daily_pnl=snap.daily_pnl,
                )
                await self._repository.flush()
            except Exception as exc:
                logger.error("stock_housekeeping_error", error=str(exc))

    async def _setup_market_instruments(self, markets: list[Market]) -> list[str]:
        """Register markets: save to DB, build instrument mapping and feature engines."""
        instrument_ids: list[str] = []
        for market in markets:
            await self._repository.save_market(market)
            yes_price = (market.exchange_data or {}).get("yes_price")

            for token in market.tokens:
                iid = token.instrument_id or token.token_id
                if iid not in self._instrument_to_market:
                    instrument_ids.append(iid)
                    self._instrument_to_market[iid] = market
                    self._feature_engines[iid] = FeatureEngine(
                        market.market_id,
                        instrument_id=iid,
                        exchange=self._settings.exchange,
                    )

                    if yes_price is not None:
                        is_no = iid.endswith("-no")
                        price = (1.0 - yes_price) if is_no else yes_price
                        bid = max(price - 0.01, 0.01)
                        ask = min(price + 0.01, 0.99)
                        self._orderbook.apply_snapshot(
                            market_id=market.market_id,
                            instrument_id=iid,
                            bids=[{"price": bid, "size": 10}],
                            asks=[{"price": ask, "size": 10}],
                        )

        return instrument_ids

    async def _universe_refresh_loop(self) -> None:
        """Periodically re-evaluate the market universe and rotate the watchlist."""
        while self._running:
            await asyncio.sleep(float(self._settings.universe_refresh_seconds))
            if not self._running:
                break

            try:
                books = {}
                for iid in self._orderbook.instruments:
                    snap = self._orderbook.get_snapshot(iid)
                    if snap is not None:
                        market = self._instrument_to_market.get(iid)
                        if market:
                            books[market.market_id] = snap

                summary = await self._universe.refresh(books=books)

                new_markets = self._universe.active_markets
                current_ids = {m.market_id for m in self._active_markets}
                new_ids = {m.market_id for m in new_markets}

                added_markets = [m for m in new_markets if m.market_id not in current_ids]
                removed_ids = current_ids - new_ids

                if added_markets:
                    new_instrument_ids = await self._setup_market_instruments(added_markets)
                    if new_instrument_ids:
                        ws = self._adapter.websocket
                        ws.subscribe_book(new_instrument_ids)
                        ws.subscribe_trades(new_instrument_ids)
                        for iid in new_instrument_ids:
                            try:
                                book_data = await self._adapter.market_data.get_orderbook(iid)
                                bids = book_data.get("bids", [])
                                asks = book_data.get("asks", [])
                                if bids or asks:
                                    market = self._instrument_to_market[iid]
                                    self._orderbook.apply_snapshot(
                                        market_id=market.market_id,
                                        instrument_id=iid,
                                        bids=bids,
                                        asks=asks,
                                    )
                            except Exception as e:
                                logger.warning("refresh_book_fetch_failed", instrument_id=iid, error=str(e))

                self._active_markets = new_markets

                logger.info(
                    "universe_refresh_applied",
                    active_markets=len(self._active_markets),
                    added=len(added_markets),
                    removed=len(removed_ids),
                    watchlist_stats=self._universe.stats,
                )

            except Exception as e:
                logger.error("universe_refresh_error", error=str(e))

    async def _persist_positions(self) -> None:
        try:
            positions = self._portfolio.positions
            await self._repository.save_all_positions(positions)
            logger.info("positions_persisted", count=len(positions))
        except Exception as e:
            logger.error("position_persist_failed", error=str(e))

    async def _recover_positions(self) -> None:
        try:
            rows = await self._repository.load_positions()
            for row in rows:
                from app.data.models import OutcomeSide
                self._portfolio.restore_position(
                    token_id=row["token_id"],
                    market_id=row["market_id"],
                    token_side=OutcomeSide(row["token_side"]),
                    size=row["size"],
                    avg_entry_price=row["avg_entry_price"],
                    realized_pnl=row["realized_pnl"],
                )
            if rows:
                logger.info("positions_recovered", count=len(rows))
        except Exception as e:
            logger.error("position_recovery_failed", error=str(e))

    # ── WebSocket Handlers ─────────────────────────────────────────────

    async def _on_book_message(self, msg: dict[str, Any]) -> None:
        assets = msg.get("assets", [])
        for asset in assets if isinstance(assets, list) else [msg]:
            iid = asset.get("instrument_id", asset.get("asset_id", asset.get("token_id", asset.get("market_ticker", ""))))
            if not iid or iid not in self._instrument_to_market:
                continue

            market = self._instrument_to_market[iid]
            bids = asset.get("bids", [])
            asks = asset.get("asks", [])

            if asset.get("type") == "snapshot" or len(bids) > 5:
                self._orderbook.apply_snapshot(market_id=market.market_id, instrument_id=iid, bids=bids, asks=asks)
            else:
                self._orderbook.apply_delta(instrument_id=iid, bid_updates=bids, ask_updates=asks)

            await self._repository.save_raw_event("book", iid, asset)

    async def _on_trade_message(self, msg: dict[str, Any]) -> None:
        trades = msg.get("trades", msg.get("data", []))
        if not isinstance(trades, list):
            trades = [msg]

        for t in trades:
            iid = t.get("instrument_id", t.get("asset_id", t.get("token_id", t.get("market_ticker", ""))))
            if not iid or iid not in self._feature_engines:
                continue

            market = self._instrument_to_market.get(iid)
            trade = Trade(
                market_id=market.market_id if market else "",
                token_id=iid,
                instrument_id=iid,
                exchange=self._settings.exchange,
                price=float(t.get("price", 0)),
                size=float(t.get("size", t.get("count", 0))),
                side=Side.BUY if t.get("side", "").upper() in ("BUY", "YES") else Side.SELL,
            )
            self._feature_engines[iid].add_trade(trade)
            await self._repository.save_raw_event("trade", iid, t)

    async def _on_user_message(self, msg: dict[str, Any]) -> None:
        event = msg.get("event", msg.get("type", ""))

        if event in ("fill", "order_fill", "trade_fill"):
            await self._process_exchange_fill(msg)
        elif event in ("order_update", "order", "order_group_updates"):
            self._process_order_update(msg)

    async def _process_exchange_fill(self, msg: dict[str, Any]) -> None:
        order_id = msg.get("order_id", msg.get("orderId", ""))
        fill_price = float(msg.get("price", 0))
        fill_size = float(msg.get("size", msg.get("matchSize", msg.get("count", 0))))

        if not order_id or fill_price <= 0 or fill_size <= 0:
            logger.warning("invalid_fill_message", msg=str(msg)[:300])
            return

        from app.data.models import OrderStatus

        self._execution.update_order_status(order_id, OrderStatus.FILLED, fill_size)

        with self._execution._lock:
            order = self._execution._active_orders.get(order_id)
        if order is None:
            logger.warning("fill_for_unknown_order", order_id=order_id)
            return

        realized = self._portfolio.on_fill(order, fill_price, fill_size)
        self._risk_manager.record_fill(realized)

        await self._repository.save_fill(order_id, fill_price, fill_size, realized)
        await self._repository.save_order(order)

        logger.info(
            "exchange_fill_processed",
            order_id=order_id,
            price=fill_price,
            size=fill_size,
            realized_pnl=realized,
        )

    def _process_order_update(self, msg: dict[str, Any]) -> None:
        from app.data.models import OrderStatus

        order_id = msg.get("order_id", msg.get("orderId", ""))
        status_str = msg.get("status", "").upper()
        status_map = {s.value: s for s in OrderStatus}
        new_status = status_map.get(status_str)
        if order_id and new_status:
            self._execution.update_order_status(order_id, new_status)

    async def _reconcile_positions(self) -> None:
        try:
            exchange_positions = await self._adapter.execution.get_open_positions()
            if not exchange_positions:
                return

            exchange_map = {
                p.get("instrument_id", p.get("token_id", "")): p
                for p in exchange_positions
            }
            local_positions = self._portfolio.positions

            for pos in local_positions:
                pid = pos.instrument_id or pos.token_id
                ex = exchange_map.pop(pid, None)
                if ex is None:
                    logger.warning(
                        "position_drift_local_only",
                        instrument_id=pid,
                        local_size=pos.size,
                    )
                elif abs(pos.size - float(ex.get("size", 0))) > 0.01:
                    logger.warning(
                        "position_drift_size_mismatch",
                        instrument_id=pid,
                        local_size=pos.size,
                        exchange_size=ex.get("size"),
                    )

            for pid, ex in exchange_map.items():
                logger.warning(
                    "position_drift_exchange_only",
                    instrument_id=pid,
                    exchange_size=ex.get("size"),
                )
        except Exception as e:
            logger.error("reconciliation_error", error=str(e))

    # ── Core Loops ─────────────────────────────────────────────────────

    async def _intelligence_loop(self) -> None:
        while self._running:
            await asyncio.sleep(5.0)

            if self._risk_manager.is_halted:
                logger.warning("trading_halted_by_risk")
                continue

            pending_nlp = self._news_service.get_latest_signals()

            for iid, engine in self._feature_engines.items():
                try:
                    book = self._orderbook.get_snapshot(iid)
                    features = engine.compute(book)
                    portfolio_snap = self._portfolio.get_snapshot()

                    await self._repository.save_features(features)

                    if features.mid_price is not None:
                        self._portfolio.mark_to_market(iid, features.mid_price)

                    market = self._instrument_to_market.get(iid)
                    market_id = market.market_id if market else features.market_id

                    # ── Level 1: Rule strategies ──
                    l1_signals: list[NormalizedSignal] = []
                    for strat in self._l1_strategies:
                        if strat is self._ml_strategy:
                            continue
                        try:
                            sig = strat.generate_signal(features, portfolio_snap)
                            if sig is not None:
                                l1_signals.append(
                                    signal_to_normalized(sig, IntelligenceLayer.RULES)
                                )
                        except Exception:
                            logger.exception("l1_strategy_error", strategy=strat.name)

                    # ── Level 2: ML prediction ──
                    l2_signals: list[NormalizedSignal] = []
                    if self._ml_strategy is not None:
                        try:
                            ml_sig = self._ml_strategy.generate_signal(features, portfolio_snap)
                            if ml_sig is not None:
                                l2_signals.append(
                                    signal_to_normalized(ml_sig, IntelligenceLayer.ML)
                                )
                        except Exception:
                            logger.exception("l2_ml_error")

                    # ── Level 3: NLP/event signals ──
                    l3_signals: list[NormalizedSignal] = []
                    for nlp_sig in pending_nlp:
                        if market_id in nlp_sig.market_ids:
                            l3_signals.append(
                                nlp_signal_to_layered(nlp_sig, instrument_id=iid, exchange=self._settings.exchange)
                            )

                    # ── Decision Engine ──
                    candidate, trace = self._decision_engine.evaluate(
                        market_id=market_id,
                        token_id=iid,
                        features=features,
                        portfolio=portfolio_snap,
                        l1_signals=l1_signals,
                        l2_signals=l2_signals,
                        l3_signals=l3_signals,
                        instrument_id=iid,
                        exchange=self._settings.exchange,
                    )

                    for ls in l1_signals + l2_signals + l3_signals:
                        sig = Signal(
                            strategy_name=ls.source_name,
                            market_id=ls.market_id,
                            token_id=ls.instrument_id or ls.token_id,
                            instrument_id=ls.instrument_id or ls.token_id,
                            exchange=ls.exchange or self._settings.exchange,
                            action=ls.action,
                            confidence=ls.normalized_confidence,
                            suggested_price=ls.suggested_price,
                            suggested_size=ls.suggested_size,
                            rationale=ls.rationale,
                        )
                        await self._repository.save_signal(sig)

                    if candidate.blocked or candidate.action.value == "HOLD":
                        continue

                    exec_signal = Signal(
                        strategy_name="decision_engine",
                        market_id=candidate.market_id,
                        token_id=candidate.token_id,
                        instrument_id=candidate.token_id,
                        exchange=self._settings.exchange,
                        action=candidate.action,
                        confidence=candidate.final_confidence,
                        suggested_price=candidate.suggested_price,
                        suggested_size=candidate.suggested_size,
                        rationale=candidate.rationale,
                    )
                    order = await self._execution.process_signal(
                        exec_signal, features, portfolio_snap
                    )
                    if order is not None:
                        await self._repository.save_order(order)

                except Exception as e:
                    logger.error("intelligence_loop_error", instrument_id=iid, error=str(e))
                    metrics.increment("intelligence_loop_errors")

    async def _housekeeping_loop(self) -> None:
        while self._running:
            await asyncio.sleep(60.0)

            try:
                await self._execution.cancel_stale_orders(max_age_seconds=300)

                snap = self._portfolio.get_snapshot()
                await self._repository.save_pnl_snapshot(
                    cash=snap.cash,
                    total_exposure=snap.total_exposure,
                    total_unrealized=snap.total_unrealized_pnl,
                    total_realized=snap.total_realized_pnl,
                    daily_pnl=snap.daily_pnl,
                )

                await self._repository.flush()
                await self._persist_positions()

                m = metrics.snapshot()
                logger.info(
                    "periodic_metrics",
                    cash=snap.cash,
                    exposure=snap.total_exposure,
                    daily_pnl=snap.daily_pnl,
                    active_orders=len(self._execution.active_orders),
                    **{k: v for k, v in m.items() if v > 0},
                )

                if self._mode == TradingMode.LIVE:
                    await self._reconcile_positions()

                if self._adapter.websocket.is_stale:
                    logger.warning(
                        "ws_data_stale",
                        seconds=self._adapter.websocket.seconds_since_last_message,
                    )

            except Exception as e:
                logger.error("housekeeping_error", error=str(e))


# ── CLI ────────────────────────────────────────────────────────────────────


@click.command()
@click.option("--markets", "-m", multiple=True, help="Market slugs to trade (default: dynamic universe selection)")
@click.option("--strategy", "-s", default=None, help="Strategy name override")
@click.option("--dry-run/--live", default=True, help="Dry run (default) or live trading")
@click.option("--exchange", "-e", default=None, help="Exchange: polymarket or kalshi")
def main(markets: tuple[str, ...], strategy: str | None, dry_run: bool, exchange: str | None) -> None:
    """Start the trading bot."""
    settings = get_settings()

    if strategy:
        settings.strategy = strategy
    if exchange:
        settings.exchange = exchange
    if not dry_run:
        settings.dry_run = False
        settings.require_live_trading()

    bot = TradingBot(settings)

    loop = asyncio.new_event_loop()

    def shutdown_handler(sig: int, frame: Any) -> None:
        logger.info("shutdown_signal_received", signal=sig)
        loop.create_task(bot.stop())

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    try:
        loop.run_until_complete(bot.start(list(markets) if markets else None))
    except KeyboardInterrupt:
        loop.run_until_complete(bot.stop())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
