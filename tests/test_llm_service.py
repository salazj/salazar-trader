"""Tests for the LocalLLMService timeout / failure / cache behavior."""

from __future__ import annotations

import asyncio

import pytest

from app.config.settings import Settings
from app.llm import LocalLLMService
from app.llm.provider import BaseLocalLLMProvider
from app.llm.schema import LLMSentiment
from app.llm.service import LLMRequest


class _DummyProvider(BaseLocalLLMProvider):
    name = "llama_cpp"
    model_name = "dummy"

    def __init__(self, *, response: str | None = None, raise_exc: Exception | None = None,
                 hang_seconds: float = 0.0) -> None:
        self._response = response
        self._raise = raise_exc
        self._hang = hang_seconds
        self.calls = 0

    async def complete(self, prompt: str, *, max_tokens: int = 256) -> str:
        self.calls += 1
        if self._hang:
            await asyncio.sleep(self._hang)
        if self._raise is not None:
            raise self._raise
        return self._response or ""

    def info(self) -> dict:
        return {"provider": self.name, "model_name": self.model_name, "available": True}


def _settings() -> Settings:
    return Settings(local_llm_provider="llama_cpp", local_llm_timeout_seconds=0.2,
                    local_llm_cache_ttl_seconds=300)


class TestLLMServiceTimeoutAndErrors:
    @pytest.mark.asyncio
    async def test_timeout_returns_safe_default(self) -> None:
        provider = _DummyProvider(hang_seconds=1.0)
        svc = LocalLLMService(_settings(), provider=provider)
        v = await svc.evaluate(LLMRequest(ticker="NVDA", technical_context="t", news_context="n"))
        assert v.sentiment == LLMSentiment.NEUTRAL
        assert v.confidence_adjustment == 0.0
        assert v.should_gate_trade is False
        assert v.ticker == "NVDA"

    @pytest.mark.asyncio
    async def test_provider_exception_returns_safe_default(self) -> None:
        provider = _DummyProvider(raise_exc=RuntimeError("boom"))
        svc = LocalLLMService(_settings(), provider=provider)
        v = await svc.evaluate(LLMRequest(ticker="SPY", technical_context="", news_context=""))
        assert v.sentiment == LLMSentiment.NEUTRAL

    @pytest.mark.asyncio
    async def test_malformed_json_returns_safe_default(self) -> None:
        provider = _DummyProvider(response="this is not json")
        svc = LocalLLMService(_settings(), provider=provider)
        v = await svc.evaluate(LLMRequest(ticker="QQQ", technical_context="", news_context=""))
        assert v.sentiment == LLMSentiment.NEUTRAL

    @pytest.mark.asyncio
    async def test_bearish_gate_propagates(self) -> None:
        raw = (
            '{"ticker":"AAPL","sentiment":"bearish","relevance_score":0.9,'
            '"confidence_adjustment":-0.25,"risk_flags":["antitrust"],'
            '"summary":"x","should_gate_trade":true}'
        )
        provider = _DummyProvider(response=raw)
        svc = LocalLLMService(_settings(), provider=provider)
        v = await svc.evaluate(LLMRequest(ticker="AAPL", technical_context="", news_context="news A"))
        assert v.sentiment == LLMSentiment.BEARISH
        assert v.should_gate_trade is True

    @pytest.mark.asyncio
    async def test_cache_avoids_repeated_provider_calls(self) -> None:
        raw = (
            '{"ticker":"NVDA","sentiment":"bullish","relevance_score":0.6,'
            '"confidence_adjustment":0.1}'
        )
        provider = _DummyProvider(response=raw)
        svc = LocalLLMService(_settings(), provider=provider)
        req = LLMRequest(ticker="NVDA", technical_context="", news_context="big news")
        await svc.evaluate(req)
        await svc.evaluate(req)
        assert provider.calls == 1


class TestNoopProviderShortCircuit:
    @pytest.mark.asyncio
    async def test_disabled_returns_safe_default_without_calling_provider(self) -> None:
        from app.llm.provider import NoopLLMProvider
        provider = NoopLLMProvider()
        svc = LocalLLMService(Settings(local_llm_provider="none"), provider=provider)
        v = await svc.evaluate(LLMRequest(ticker="X", technical_context="", news_context=""))
        assert v.sentiment == LLMSentiment.NEUTRAL
        assert "disabled" in v.summary.lower() or "fallback" in v.summary.lower()
