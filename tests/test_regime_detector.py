"""Tests for the market regime classifier."""

from __future__ import annotations

from app.regime import MarketRegime, MarketRegimeDetector, classify_regime


def _trending_up(n: int = 100, slope: float = 0.001) -> list[float]:
    return [100.0 * (1 + slope) ** i for i in range(n)]


def _trending_down(n: int = 100, slope: float = 0.001) -> list[float]:
    return [100.0 * (1 - slope) ** i for i in range(n)]


def _flat(n: int = 100, value: float = 100.0) -> list[float]:
    return [value for _ in range(n)]


class TestRegimeClassifier:
    def test_trending_bullish(self) -> None:
        r = classify_regime(_trending_up(80, 0.005), atr_pct=0.01)
        assert r.regime == MarketRegime.TRENDING_BULLISH
        assert r.allows_long is True
        assert r.prefers_momentum is True

    def test_trending_bearish(self) -> None:
        r = classify_regime(_trending_down(80, 0.005), atr_pct=0.01)
        assert r.regime == MarketRegime.TRENDING_BEARISH
        assert r.allows_long is False

    def test_range_bound(self) -> None:
        r = classify_regime(_flat(80), atr_pct=0.012)
        assert r.regime == MarketRegime.RANGE_BOUND
        assert r.prefers_mean_reversion is True

    def test_low_volatility(self) -> None:
        r = classify_regime(_flat(80), atr_pct=0.001)
        assert r.regime == MarketRegime.LOW_VOLATILITY
        assert r.prefers_mean_reversion is True

    def test_high_volatility(self) -> None:
        r = classify_regime(_trending_up(80, 0.001), atr_pct=0.03)
        assert r.regime == MarketRegime.HIGH_VOLATILITY

    def test_risk_off_via_vix(self) -> None:
        r = classify_regime(_trending_up(80, 0.001), atr_pct=0.01, vix=42.0)
        assert r.regime == MarketRegime.RISK_OFF
        assert r.allows_long is False

    def test_risk_off_via_atr(self) -> None:
        r = classify_regime(_trending_up(80, 0.001), atr_pct=0.06)
        assert r.regime == MarketRegime.RISK_OFF
        assert r.allows_long is False


class TestStatefulDetector:
    def test_update_close_and_evaluate(self) -> None:
        det = MarketRegimeDetector(lookback=20)
        for i, c in enumerate(_trending_up(40, 0.005)):
            det.update_close("SPY", c)
        r = det.evaluate(atr_pct=0.01)
        assert r.regime == MarketRegime.TRENDING_BULLISH
        assert det.last is r

    def test_qqq_blends_into_score(self) -> None:
        det = MarketRegimeDetector(lookback=20)
        for c in _trending_up(40, 0.004):
            det.update_close("SPY", c)
        for c in _trending_up(40, 0.004):
            det.update_close("QQQ", c)
        r = det.evaluate(atr_pct=0.01)
        assert r.regime == MarketRegime.TRENDING_BULLISH
