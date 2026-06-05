"""Tests for the local LLM JSON schema, parser, and cache."""

from __future__ import annotations

import json
import time

import pytest

from app.llm.cache import LLMResponseCache
from app.llm.schema import (
    LLMSentiment,
    LLMVerdict,
    parse_llm_verdict,
    safe_default_verdict,
)


class TestLLMVerdictValidation:
    def test_valid_payload_round_trips(self) -> None:
        v = LLMVerdict(
            ticker="nvda",
            sentiment="bullish",
            relevance_score=0.7,
            confidence_adjustment=0.1,
            risk_flags=["thin_liquidity"],
            summary="ok",
            should_gate_trade=False,
        )
        assert v.ticker == "NVDA"
        assert v.sentiment == LLMSentiment.BULLISH
        assert v.confidence_adjustment == pytest.approx(0.1)

    def test_relevance_score_out_of_range_rejected(self) -> None:
        with pytest.raises(Exception):
            LLMVerdict(ticker="X", sentiment="neutral", relevance_score=1.5)

    def test_confidence_adjustment_out_of_range_rejected(self) -> None:
        with pytest.raises(Exception):
            LLMVerdict(ticker="X", sentiment="neutral", confidence_adjustment=0.5)

    def test_invalid_sentiment_rejected(self) -> None:
        with pytest.raises(Exception):
            LLMVerdict(ticker="X", sentiment="moon")


class TestParseVerdict:
    def test_parses_pure_json(self) -> None:
        raw = json.dumps(
            {
                "ticker": "nvda",
                "sentiment": "bullish",
                "relevance_score": 0.6,
                "confidence_adjustment": 0.1,
                "risk_flags": ["news"],
                "summary": "...",
                "should_gate_trade": False,
            }
        )
        v = parse_llm_verdict(raw)
        assert v.ticker == "NVDA"
        assert v.sentiment == LLMSentiment.BULLISH
        assert v.confidence_adjustment == pytest.approx(0.1)

    def test_extracts_json_from_prose(self) -> None:
        raw = (
            "Here is your answer:\n"
            '{"ticker":"AAPL","sentiment":"neutral","relevance_score":0.2,'
            '"confidence_adjustment":0.0,"risk_flags":[],'
            '"summary":"meh","should_gate_trade":false}\n'
            "Hope that helps."
        )
        v = parse_llm_verdict(raw)
        assert v.ticker == "AAPL"
        assert v.sentiment == LLMSentiment.NEUTRAL

    def test_malformed_json_returns_safe_default(self) -> None:
        v = parse_llm_verdict("definitely not json", ticker="QQQ")
        assert v.sentiment == LLMSentiment.NEUTRAL
        assert v.confidence_adjustment == 0.0
        assert v.should_gate_trade is False
        assert v.ticker == "QQQ"

    def test_invalid_sentiment_coerced_to_neutral(self) -> None:
        raw = json.dumps(
            {
                "ticker": "SPY",
                "sentiment": "moon",
                "relevance_score": 0.5,
                "confidence_adjustment": 0.0,
            }
        )
        v = parse_llm_verdict(raw)
        assert v.sentiment == LLMSentiment.NEUTRAL

    def test_clipping_confidence_adjustment(self) -> None:
        raw = json.dumps(
            {
                "ticker": "TSLA",
                "sentiment": "bullish",
                "relevance_score": 0.8,
                "confidence_adjustment": 0.99,  # out of bounds
            }
        )
        v = parse_llm_verdict(raw)
        assert v.confidence_adjustment == pytest.approx(0.25)

    def test_should_gate_trade_preserved(self) -> None:
        raw = json.dumps(
            {
                "ticker": "X",
                "sentiment": "bearish",
                "relevance_score": 0.9,
                "confidence_adjustment": -0.2,
                "should_gate_trade": True,
                "risk_flags": ["regulatory_action"],
            }
        )
        v = parse_llm_verdict(raw)
        assert v.should_gate_trade is True
        assert "regulatory_action" in v.risk_flags

    def test_safe_default_is_neutral(self) -> None:
        v = safe_default_verdict("AAPL", reason="timeout")
        assert v.sentiment == LLMSentiment.NEUTRAL
        assert v.confidence_adjustment == 0.0
        assert v.should_gate_trade is False
        assert "timeout" in v.risk_flags[0]

    def test_empty_string_returns_safe_default(self) -> None:
        v = parse_llm_verdict("", ticker="META")
        assert v.sentiment == LLMSentiment.NEUTRAL
        assert v.ticker == "META"


class TestLLMResponseCache:
    def test_cache_miss_returns_none(self) -> None:
        cache = LLMResponseCache()
        assert cache.get("missing") is None

    def test_cache_hit_returns_value(self) -> None:
        cache = LLMResponseCache()
        v = LLMVerdict(ticker="X", sentiment="neutral")
        key = LLMResponseCache.make_key(
            ticker="X", news="news", provider="llama_cpp", model_name="qwen"
        )
        cache.put(key, v)
        got = cache.get(key)
        assert got is not None
        assert got.ticker == "X"

    def test_cache_ttl_expires(self) -> None:
        cache = LLMResponseCache(default_ttl_seconds=1)
        v = LLMVerdict(ticker="X", sentiment="neutral")
        key = "k"
        cache.put(key, v, ttl_seconds=0)
        time.sleep(0.05)
        assert cache.get(key) is None

    def test_cache_key_includes_news_and_provider(self) -> None:
        a = LLMResponseCache.make_key(
            ticker="X", news="A", provider="p", model_name="m"
        )
        b = LLMResponseCache.make_key(
            ticker="X", news="B", provider="p", model_name="m"
        )
        c = LLMResponseCache.make_key(
            ticker="X", news="A", provider="p2", model_name="m"
        )
        assert a != b
        assert a != c
