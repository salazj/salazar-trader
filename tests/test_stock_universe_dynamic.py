"""Tests for the dynamic stock-universe components."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.nlp.signals import (
    ClassificationResult,
    EventType,
    SentimentDirection,
)
from app.stocks.universe.hot_tickers import HotTickerTracker
from app.stocks.universe.movers import MoverEntry, TopMoversScreener
from app.stocks.universe.name_resolver import (
    ALL_KNOWN_TICKERS,
    NameResolver,
    SP_100_TICKERS,
)


# ── NameResolver ──────────────────────────────────────────────────────────


def test_resolver_maps_company_name_to_ticker():
    r = NameResolver()
    assert r.resolve(["Tesla"]) == {"TSLA"}
    assert r.resolve(["Apple Inc"]) == {"AAPL"}
    assert r.resolve(["nvidia"]) == {"NVDA"}


def test_resolver_accepts_raw_ticker_symbol():
    r = NameResolver()
    assert r.resolve(["AAPL"]) == {"AAPL"}
    assert r.resolve(["spy"]) == {"SPY"}


def test_resolver_drops_unknown_entities():
    r = NameResolver()
    assert r.resolve(["Random Person", "FAKETICKER", ""]) == set()


def test_resolver_respects_allowed_set():
    r = NameResolver(allowed_tickers={"AAPL"})
    assert r.resolve(["Apple"]) == {"AAPL"}
    assert r.resolve(["Tesla"]) == set()


def test_resolver_resolves_text_phrases():
    r = NameResolver()
    out = r.resolve_text("Apple just announced a new iPhone, big news for Tesla too.")
    assert "AAPL" in out
    assert "TSLA" in out


def test_sp100_subset_of_known_tickers():
    assert SP_100_TICKERS.issubset(ALL_KNOWN_TICKERS)


# ── HotTickerTracker ──────────────────────────────────────────────────────


def _make_classification(
    *,
    entities,
    sentiment=SentimentDirection.BULLISH,
    sentiment_score=0.7,
    confidence=0.8,
    relevance=0.7,
    urgency=0.6,
):
    return ClassificationResult(
        relevance=relevance,
        sentiment=sentiment,
        sentiment_score=sentiment_score,
        event_type=EventType.OTHER,
        urgency=urgency,
        confidence=confidence,
        rationale="test",
        entities=list(entities),
    )


def test_tracker_ingests_bullish_news():
    r = NameResolver()
    t = HotTickerTracker(r, ttl_minutes=60, max_tickers=5)
    touched = t.ingest(
        _make_classification(entities=["Apple"]),
        "Apple posts record earnings.",
    )
    assert touched == {"AAPL"}
    hot = t.get_hot_tickers()
    assert hot == ["AAPL"]
    assert t.latest_headline("AAPL").startswith("Apple posts record earnings")


def test_tracker_rejects_low_confidence():
    r = NameResolver()
    t = HotTickerTracker(r, min_confidence=0.9, min_relevance=0.0)
    touched = t.ingest(
        _make_classification(entities=["Apple"], confidence=0.5),
        "Apple announces something.",
    )
    assert touched == set()
    assert t.get_hot_tickers() == []


def test_tracker_ttl_prunes_old_entries():
    r = NameResolver()
    t = HotTickerTracker(r, ttl_minutes=30, max_tickers=5)
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    t.ingest(
        _make_classification(entities=["Tesla"]),
        "Tesla old news",
        now=old,
    )
    now = datetime.now(timezone.utc)
    # Probing with the present time should drop the stale ticker.
    assert "TSLA" not in t.get_hot_tickers(now=now)


def test_tracker_orders_by_score():
    r = NameResolver()
    t = HotTickerTracker(r, ttl_minutes=120, max_tickers=10)
    t.ingest(
        _make_classification(entities=["Apple"], relevance=0.4, confidence=0.5, urgency=0.2),
        "Apple modest news",
    )
    # Tesla gets multiple high-conviction hits.
    for i in range(3):
        t.ingest(
            _make_classification(
                entities=["Tesla"],
                relevance=0.8,
                confidence=0.9,
                urgency=0.8,
            ),
            f"Tesla huge news {i}",
        )
    hot = t.get_hot_tickers()
    assert hot[0] == "TSLA"
    assert "AAPL" in hot


def test_tracker_falls_back_to_text_resolution():
    r = NameResolver()
    t = HotTickerTracker(r, ttl_minutes=60)
    # Classification has no recognizable entities, but text mentions Tesla.
    touched = t.ingest(
        _make_classification(entities=["UnknownPerson"]),
        "Tesla shares jumped 5% on strong delivery numbers.",
    )
    assert "TSLA" in touched


# ── TopMoversScreener ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_top_movers_ranks_by_score(monkeypatch):
    settings = MagicMock()
    settings.alpaca_api_key = "k"
    settings.alpaca_secret_key = "s"

    screener = TopMoversScreener(
        settings,
        candidates=["AAPL", "TSLA", "NVDA"],
        top_n=2,
        min_price=1.0,
        max_price=10000.0,
        min_dollar_volume=0.0,
    )

    def fake_fetch():
        def snap(price, open_, volume):
            s = MagicMock()
            s.latest_trade = MagicMock(price=price)
            s.daily_bar = MagicMock(open=open_, volume=volume)
            return s

        return {
            "AAPL": snap(110.0, 100.0, 1_000_000),
            "TSLA": snap(200.0, 220.0, 5_000_000),
            "NVDA": snap(50.5, 50.0, 200_000),
        }

    monkeypatch.setattr(screener, "_fetch_snapshots", fake_fetch)

    result = await screener.scan()
    assert [m.ticker for m in result] == ["TSLA", "AAPL"]
    assert result[0].pct_change == pytest.approx((200 - 220) / 220)
    assert result[1].pct_change == pytest.approx(0.10)


@pytest.mark.asyncio
async def test_top_movers_filters_low_dollar_volume(monkeypatch):
    settings = MagicMock()
    settings.alpaca_api_key = "k"
    settings.alpaca_secret_key = "s"

    screener = TopMoversScreener(
        settings,
        candidates=["AAPL"],
        top_n=5,
        min_dollar_volume=1_000_000_000.0,
    )

    def fake_fetch():
        s = MagicMock()
        s.latest_trade = MagicMock(price=100.0)
        s.daily_bar = MagicMock(open=90.0, volume=10)
        return {"AAPL": s}

    monkeypatch.setattr(screener, "_fetch_snapshots", fake_fetch)

    result = await screener.scan()
    assert result == []


@pytest.mark.asyncio
async def test_top_movers_handles_empty_candidates():
    settings = MagicMock()
    settings.alpaca_api_key = "k"
    settings.alpaca_secret_key = "s"

    screener = TopMoversScreener(settings, candidates=[], top_n=5)
    assert await screener.scan() == []
