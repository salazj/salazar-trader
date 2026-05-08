"""High-level LLM service used by the stock decision engine.

* Builds a strict, JSON-only prompt from a stock context.
* Calls the local provider with a hard timeout.
* Parses the response into ``LLMVerdict`` (always returns a valid object).
* Caches results for ``cache_ttl_seconds``.
* Logs every call for observability.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from app.config.settings import Settings
from app.llm.cache import LLMResponseCache
from app.llm.provider import BaseLocalLLMProvider, build_local_llm
from app.llm.schema import (
    LLMSentiment,
    LLMVerdict,
    parse_llm_verdict,
    safe_default_verdict,
)

log = logging.getLogger(__name__)


_PROMPT = """You are a careful financial-news classifier for a low-budget,
risk-averse stock trading bot. You will be given a ticker, the current
trading context (technical signal summary), and a short news context.

Return ONLY a single JSON object — no prose, no markdown — that matches
exactly this schema:

{{
  "ticker": "...",
  "sentiment": "bullish" | "neutral" | "bearish",
  "relevance_score": 0.0,        # how relevant is the news to {ticker}, 0..1
  "confidence_adjustment": 0.0,  # -0.25..+0.25 — your suggested adjustment
                                 # to the trade confidence (NEVER outside this range)
  "risk_flags": [],              # any specific concerns, short strings
  "summary": "...",              # one to two sentences
  "should_gate_trade": false     # true ONLY if the news is clearly negative
                                 # and the trade should be blocked outright
}}

Rules:
* If the news is unrelated, return relevance_score=0 and confidence_adjustment=0.
* If you are uncertain, default to neutral with no boost.
* Never recommend a confidence_adjustment > 0.25 or < -0.25.
* Never invent news that wasn't in the context.

Inputs:
ticker: {ticker}
technical_context: {tech}
news_context: {news}

JSON:"""


@dataclass
class LLMRequest:
    ticker: str
    technical_context: str
    news_context: str


class LocalLLMService:
    """Thin orchestration layer over a ``BaseLocalLLMProvider``."""

    def __init__(
        self,
        settings: Settings,
        provider: BaseLocalLLMProvider | None = None,
        cache: LLMResponseCache | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider or build_local_llm(settings)
        self._cache = cache or LLMResponseCache(
            default_ttl_seconds=settings.local_llm_cache_ttl_seconds
        )
        self._timeout = float(settings.local_llm_timeout_seconds)
        self._max_tokens = int(settings.local_llm_max_tokens)
        self._calls_total = 0
        self._calls_failed = 0
        self._last_call_at: float | None = None
        self._last_latency_ms: float | None = None

    @property
    def provider(self) -> BaseLocalLLMProvider:
        return self._provider

    @property
    def cache(self) -> LLMResponseCache:
        return self._cache

    async def evaluate(self, req: LLMRequest) -> LLMVerdict:
        """Return an ``LLMVerdict`` for the request — always succeeds."""
        if self._provider.name == "none":
            return safe_default_verdict(req.ticker, "llm disabled")

        cache_key = LLMResponseCache.make_key(
            ticker=req.ticker,
            news=req.news_context,
            provider=self._provider.name,
            model_name=self._provider.model_name,
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        prompt = _PROMPT.format(
            ticker=req.ticker.upper(),
            tech=(req.technical_context or "").strip()[:1500],
            news=(req.news_context or "").strip()[:2500],
        )

        self._calls_total += 1
        start = time.time()
        try:
            raw = await asyncio.wait_for(
                self._provider.complete(prompt, max_tokens=self._max_tokens),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            self._calls_failed += 1
            verdict = safe_default_verdict(req.ticker, "llm timeout")
            self._cache.put(cache_key, verdict, ttl_seconds=60)
            return verdict
        except Exception as exc:
            self._calls_failed += 1
            log.warning("local_llm_failure: %s", exc)
            verdict = safe_default_verdict(req.ticker, f"llm error: {type(exc).__name__}")
            self._cache.put(cache_key, verdict, ttl_seconds=60)
            return verdict

        self._last_latency_ms = (time.time() - start) * 1000.0
        self._last_call_at = time.time()
        verdict = parse_llm_verdict(raw, ticker=req.ticker)

        # Empty response => degrade safely.
        if not raw.strip():
            verdict = safe_default_verdict(req.ticker, "empty llm response")

        self._cache.put(cache_key, verdict)
        return verdict

    def status(self) -> dict:
        info = dict(self._provider.info())
        info.update(
            {
                "calls_total": self._calls_total,
                "calls_failed": self._calls_failed,
                "cache": self._cache.stats(),
                "last_call_at": self._last_call_at,
                "last_latency_ms": self._last_latency_ms,
                "timeout_seconds": self._timeout,
                "max_tokens": self._max_tokens,
            }
        )
        return info
