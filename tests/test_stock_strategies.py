"""Tests for stock strategy signal generation."""

from __future__ import annotations

from datetime import datetime, timezone

from app.data.models import PortfolioSnapshot
from app.stocks.models import StockFeatures
from app.stocks.strategies.momentum import StockMomentum
from app.stocks.strategies.mean_reversion import StockMeanReversion
from app.stocks.strategies.breakout import StockBreakout
from app.stocks.strategies.news_gated import NewsGatedWatchlist


def _make_features(**overrides) -> StockFeatures:
    defaults = dict(
        symbol="AAPL",
        timestamp=datetime.now(timezone.utc),
        last_price=150.0,
        bid=149.8,
        ask=150.2,
        spread=0.4,
        volume_today=1000000,
        vwap=149.5,
        rsi_14=50.0,
        sma_20=148.0,
        ema_9=149.0,
        atr_14=2.5,
        momentum_5m=0.0,
        high_of_day=151.0,
        low_of_day=148.0,
        relative_volume=1.0,
    )
    defaults.update(overrides)
    return StockFeatures(**defaults)


def _make_portfolio(**overrides) -> PortfolioSnapshot:
    defaults = dict(cash=10000.0, total_exposure=0.0)
    defaults.update(overrides)
    return PortfolioSnapshot(**defaults)


class TestMomentumStrategy:
    def test_buy_signal_on_strong_momentum(self):
        strat = StockMomentum()
        strat._bar_count = 10
        features = _make_features(
            last_price=152.0,
            ema_9=149.0,
            momentum_5m=0.01,
            rsi_14=55.0,
            relative_volume=1.5,
            volume_surge_ratio=1.5,
        )
        sig = strat.generate_signal(features, _make_portfolio())
        assert sig is not None
        assert sig.action.value == "BUY"

    def test_no_signal_on_neutral(self):
        strat = StockMomentum()
        features = _make_features(momentum_5m=0.0, last_price=149.0, ema_9=149.0)
        sig = strat.generate_signal(features, _make_portfolio())
        assert sig is None

    def test_sell_signal_on_reversal(self):
        strat = StockMomentum()
        for _ in range(6):
            strat._bar_count += 1
        features = _make_features(
            last_price=147.0,
            ema_9=149.0,
            momentum_5m=-0.01,
        )
        sig = strat.generate_signal(features, _make_portfolio())
        assert sig is not None
        assert sig.action.value == "SELL"


class TestMeanReversionStrategy:
    def test_buy_below_vwap_low_rsi(self):
        strat = StockMeanReversion()
        features = _make_features(
            last_price=145.0,
            vwap=150.0,
            price_vs_vwap=-5.0,
            rsi_14=25.0,
        )
        sig = strat.generate_signal(features, _make_portfolio())
        assert sig is not None
        assert sig.action.value == "BUY"

    def test_no_signal_at_vwap(self):
        strat = StockMeanReversion()
        features = _make_features(
            last_price=150.0,
            vwap=150.0,
            price_vs_vwap=0.0,
            rsi_14=50.0,
        )
        sig = strat.generate_signal(features, _make_portfolio())
        assert sig is None


class TestBreakoutStrategy:
    def test_buy_on_high_of_day_breakout(self):
        strat = StockBreakout()
        features = _make_features(
            last_price=151.0,
            high_of_day=151.0,
            low_of_day=148.0,
            relative_volume=2.0,
            atr_14=2.0,
        )
        sig = strat.generate_signal(features, _make_portfolio())
        assert sig is not None
        assert sig.action.value == "BUY"

    def test_no_signal_without_volume(self):
        strat = StockBreakout()
        features = _make_features(
            last_price=151.0,
            high_of_day=151.0,
            low_of_day=148.0,
            relative_volume=0.8,
        )
        sig = strat.generate_signal(features, _make_portfolio())
        assert sig is None


class TestNewsGatedWatchlist:
    def test_no_signal_without_sentiment(self):
        strat = NewsGatedWatchlist()
        features = _make_features()
        sig = strat.generate_signal(features, _make_portfolio())
        assert sig is None

    def test_signal_with_bullish_sentiment(self):
        strat = NewsGatedWatchlist()
        strat.update_sentiment("AAPL", True)
        features = _make_features()
        sig = strat.generate_signal(features, _make_portfolio())
        assert sig is not None
        assert sig.action.value == "BUY"

    def test_no_signal_after_bearish_update(self):
        strat = NewsGatedWatchlist()
        strat.update_sentiment("AAPL", True)
        strat.update_sentiment("AAPL", False)
        features = _make_features()
        sig = strat.generate_signal(features, _make_portfolio())
        assert sig is None
