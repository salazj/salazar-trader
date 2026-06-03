"""Extra coverage for the upgraded Jetson stock feature engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.stocks.features import StockFeatureEngine
from app.stocks.models import StockBar


def _bars(prices: list[float], symbol: str = "SPY") -> list[StockBar]:
    base = datetime(2024, 6, 3, 13, 30, tzinfo=timezone.utc)
    bars = []
    for i, p in enumerate(prices):
        bars.append(
            StockBar(
                symbol=symbol,
                timestamp=base + timedelta(minutes=i),
                open=p,
                high=p + 0.2,
                low=p - 0.2,
                close=p,
                volume=100_000 + (50_000 if i % 5 == 0 else 0),
            )
        )
    return bars


class TestNewIndicators:
    def test_ema_21_and_50_present(self) -> None:
        engine = StockFeatureEngine("SPY")
        prices = [100.0 + i * 0.1 for i in range(80)]
        for b in _bars(prices):
            engine.add_bar(b)
        engine.update_quote(prices[-1] - 0.05, prices[-1] + 0.05, prices[-1])
        f = engine.compute()
        assert f.ema_21 > 0
        assert f.ema_50 > 0
        assert f.ema_9 >= f.ema_21 > 0

    def test_macd_present(self) -> None:
        engine = StockFeatureEngine("SPY")
        for b in _bars([100.0 + i * 0.05 for i in range(80)]):
            engine.add_bar(b)
        engine.update_quote(103.5, 103.6, 103.55)
        f = engine.compute()
        assert isinstance(f.macd_line, float)
        assert isinstance(f.macd_signal, float)
        assert isinstance(f.macd_hist, float)

    def test_bollinger_bands(self) -> None:
        engine = StockFeatureEngine("SPY")
        for b in _bars([100.0 + (i % 5) * 0.5 for i in range(60)]):
            engine.add_bar(b)
        engine.update_quote(100.5, 100.6, 100.55)
        f = engine.compute()
        assert f.bb_lower <= f.bb_middle <= f.bb_upper
        assert 0.0 <= f.bb_pct_b <= 1.0

    def test_volume_surge_ratio(self) -> None:
        engine = StockFeatureEngine("SPY")
        for i in range(20):
            engine.add_bar(
                StockBar(
                    symbol="SPY",
                    timestamp=datetime(2024, 6, 3, 13, 30 + i, tzinfo=timezone.utc),
                    open=100.0,
                    high=100.5,
                    low=99.5,
                    close=100.0,
                    volume=100_000,
                )
            )
        engine.add_bar(
            StockBar(
                symbol="SPY",
                timestamp=datetime(2024, 6, 3, 14, 0, tzinfo=timezone.utc),
                open=100.0,
                high=100.5,
                low=99.5,
                close=100.0,
                volume=500_000,
            )
        )
        engine.update_quote(99.9, 100.1, 100.0)
        f = engine.compute()
        assert f.volume_surge_ratio > 4.0

    def test_distance_from_vwap_pct(self) -> None:
        engine = StockFeatureEngine("SPY")
        for b in _bars([100.0 for _ in range(20)]):
            engine.add_bar(b)
        engine.update_quote(101.9, 102.1, 102.0)
        f = engine.compute()
        # last_price falls back to closes[-1] when last quote is set;
        # the engine uses self._last_quote['last']=102.0 so distance > 0.
        assert f.last_price == 102.0
        assert abs(f.distance_from_vwap_pct - 2.0) < 0.5

    def test_trend_strength_bullish_stack(self) -> None:
        engine = StockFeatureEngine("SPY")
        prices = [100.0 + i * 0.2 for i in range(100)]
        for b in _bars(prices):
            engine.add_bar(b)
        engine.update_quote(prices[-1] - 0.05, prices[-1] + 0.05, prices[-1])
        f = engine.compute()
        assert f.trend_strength > 0


class TestMultiTimeframe:
    def test_resample_buckets_five_minutes(self) -> None:
        engine = StockFeatureEngine("SPY")
        # 30 one-minute bars -> 6 five-minute buckets.
        for b in _bars([100.0 + i * 0.1 for i in range(30)]):
            engine.add_bar(b)
        closes, highs, lows = engine._resample(5)
        assert len(closes) == 6
        assert len(highs) == len(lows) == 6
        # Each 5m close equals the last 1m close in that bucket.
        assert abs(closes[0] - (100.0 + 4 * 0.1)) < 1e-6

    def test_htf_trend_positive_in_uptrend(self) -> None:
        engine = StockFeatureEngine("SPY")
        prices = [100.0 + i * 0.2 for i in range(150)]
        for b in _bars(prices):
            engine.add_bar(b)
        engine.update_quote(prices[-1] - 0.05, prices[-1] + 0.05, prices[-1])
        f = engine.compute()
        assert f.htf_trend > 0
        assert -1.0 <= f.htf_trend <= 1.0
        assert 0.0 <= f.htf_rsi <= 100.0

    def test_htf_trend_negative_in_downtrend(self) -> None:
        engine = StockFeatureEngine("SPY")
        prices = [130.0 - i * 0.2 for i in range(150)]
        for b in _bars(prices):
            engine.add_bar(b)
        engine.update_quote(prices[-1] - 0.05, prices[-1] + 0.05, prices[-1])
        f = engine.compute()
        assert f.htf_trend < 0

    def test_mtf_alignment_all_up(self) -> None:
        engine = StockFeatureEngine("SPY")
        prices = [100.0 + i * 0.3 for i in range(60)]
        for b in _bars(prices):
            engine.add_bar(b)
        engine.update_quote(prices[-1] - 0.05, prices[-1] + 0.05, prices[-1])
        f = engine.compute()
        # 1m/5m/15m momentum all positive -> alignment == 1.0
        assert f.mtf_alignment == 1.0
