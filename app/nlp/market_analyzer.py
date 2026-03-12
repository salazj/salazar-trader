"""
LLM Market Analyzer — uses GPT to evaluate market mispricing.

Instead of just classifying news headlines, this module asks the LLM
to reason about whether a prediction market is correctly priced given
recent news, the market question, and the current YES price.

The LLM acts as an intelligent analyst: it estimates the true probability
of the event, compares it to the market price, and produces a trading
signal only when it identifies a genuine edge.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.data.models import Market
from app.monitoring import get_logger
from app.nlp.signals import NlpSignal, SentimentDirection
from app.utils.helpers import utc_now

logger = get_logger(__name__)


@dataclass
class MarketAnalysis:
    market_id: str
    question: str
    current_price: float
    estimated_probability: float
    edge: float
    direction: str  # "buy_yes", "buy_no", "hold"
    confidence: float
    rationale: str
    key_factors: list[str]


_ANALYSIS_SYSTEM_PROMPT = """\
You are an expert prediction market analyst. You evaluate whether prediction \
markets are correctly priced by reasoning about the true probability of events.

Given a market question, its current YES price (probability), and recent \
relevant news, you must:

1. Reason carefully about the TRUE probability of the event occurring
2. Compare your estimated probability to the current market price
3. Determine if there is a meaningful edge (mispricing)

CRITICAL RULES:
- A YES price of $0.05 means the market thinks there is a 5% chance. This is \
USUALLY correct — do NOT assume cheap = underpriced.
- Only identify an edge when you have SPECIFIC reasoning based on news or \
factual analysis that the probability should be MATERIALLY different.
- Be skeptical. Markets are usually efficient. Most of the time, HOLD is correct.
- Never suggest a trade with less than 5% edge (your estimated probability \
minus the market price).
- Consider timing: how far away is the event? Closer events have more certain probabilities.

Output ONLY a valid JSON object with these fields:
{
  "estimated_probability": float 0.0 to 1.0 (your estimate of the TRUE probability),
  "direction": one of ["buy_yes", "buy_no", "hold"],
  "confidence": float 0.0 to 1.0 (how confident you are in your analysis),
  "rationale": 1-2 sentences explaining your reasoning,
  "key_factors": list of 2-4 key factors you considered
}

Output ONLY valid JSON. No markdown fences, no explanation outside the JSON."""


def _build_market_prompt(
    question: str,
    yes_price: float,
    news_context: list[str],
    end_date: str | None = None,
) -> str:
    parts = [
        f"Market question: {question}",
        f"Current YES price: ${yes_price:.2f} (implies {yes_price*100:.0f}% probability)",
    ]
    if end_date:
        parts.append(f"Resolution date: {end_date}")

    if news_context:
        parts.append("\nRecent relevant news:")
        for i, headline in enumerate(news_context[:5], 1):
            parts.append(f"  {i}. {headline[:200]}")
    else:
        parts.append("\nNo specific recent news available for this market.")

    parts.append(
        "\nAnalyze whether this market is correctly priced. "
        "If you don't have strong evidence of mispricing, output direction: \"hold\"."
    )
    return "\n".join(parts)


class LLMMarketAnalyzer:
    """Uses GPT to evaluate whether prediction markets are mispriced."""

    COST_PER_CALL = 0.005

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout: int = 45,
        min_edge: float = 0.05,
        max_markets_per_cycle: int = 15,
    ) -> None:
        self._model = model
        self._min_edge = min_edge
        self._max_markets = max_markets_per_cycle
        self.api_calls: int = 0
        self.errors: int = 0
        self.last_call_at: str | None = None

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(timeout),
        )
        logger.info(
            "llm_market_analyzer_initialized",
            model=model,
            min_edge=min_edge,
            max_markets=max_markets_per_cycle,
        )

    def get_stats(self) -> dict[str, Any]:
        return {
            "api_calls": self.api_calls,
            "errors": self.errors,
            "estimated_cost": round(self.api_calls * self.COST_PER_CALL, 4),
            "last_call_at": self.last_call_at,
        }

    async def analyze_markets(
        self,
        markets: list[Market],
        news_headlines: list[str] | None = None,
    ) -> list[NlpSignal]:
        """Analyze a batch of markets and return trading signals."""
        signals: list[NlpSignal] = []
        headlines = news_headlines or []

        candidates = self._select_candidates(markets)

        for market in candidates:
            try:
                analysis = await self._analyze_single(market, headlines)
                if analysis and analysis.direction != "hold":
                    sig = self._analysis_to_signal(analysis, market)
                    if sig:
                        signals.append(sig)
            except Exception:
                logger.exception(
                    "llm_market_analysis_error",
                    market_id=market.market_id,
                )

        logger.info(
            "llm_market_analysis_complete",
            markets_analyzed=len(candidates),
            signals_generated=len(signals),
        )
        return signals

    def _select_candidates(self, markets: list[Market]) -> list[Market]:
        """Pick the most promising markets to analyze (save API calls)."""
        scored: list[tuple[float, Market]] = []
        for m in markets:
            ed = m.exchange_data or {}
            price = ed.get("yes_price")
            volume = float(ed.get("volume", 0) or 0)
            if price is None or price <= 0:
                continue
            if volume < 50:
                continue
            # Prefer markets in the 0.20-0.80 range with decent volume
            price_quality = 1.0 - abs(price - 0.50) * 2
            vol_score = min(volume / 500.0, 1.0)
            score = price_quality * 0.6 + vol_score * 0.4
            scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[: self._max_markets]]

    async def _analyze_single(
        self,
        market: Market,
        headlines: list[str],
    ) -> MarketAnalysis | None:
        ed = market.exchange_data or {}
        yes_price = ed.get("yes_price")
        if yes_price is None or yes_price <= 0:
            return None

        question = _extract_question(market)
        if not question:
            return None

        end_date = market.end_date if hasattr(market, "end_date") else None
        end_str = str(end_date) if end_date else None

        relevant_news = _filter_relevant_headlines(question, headlines)

        prompt = _build_market_prompt(question, yes_price, relevant_news, end_str)

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 400,
        }

        try:
            self.api_calls += 1
            self.last_call_at = utc_now().isoformat()
            resp = await self._client.post("/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            analysis = self._parse_response(content, market.market_id, question, yes_price)

            if analysis:
                logger.info(
                    "llm_market_analyzed",
                    market_id=market.market_id,
                    question=question[:80],
                    yes_price=yes_price,
                    estimated_prob=analysis.estimated_probability,
                    edge=analysis.edge,
                    direction=analysis.direction,
                    confidence=analysis.confidence,
                    rationale=analysis.rationale[:100],
                )
            return analysis

        except httpx.TimeoutException:
            self.errors += 1
            logger.warning("llm_analysis_timeout", market_id=market.market_id)
            return None
        except httpx.HTTPStatusError as e:
            self.errors += 1
            logger.warning(
                "llm_analysis_http_error",
                market_id=market.market_id,
                status=e.response.status_code,
            )
            return None

    def _parse_response(
        self,
        raw: str,
        market_id: str,
        question: str,
        current_price: float,
    ) -> MarketAnalysis | None:
        return _parse_analysis_response(
            raw, market_id, question, current_price, self._min_edge
        )

    def _analysis_to_signal(
        self,
        analysis: MarketAnalysis,
        market: Market,
    ) -> NlpSignal | None:
        if analysis.direction == "hold":
            return None

        if analysis.direction == "buy_yes":
            sentiment = SentimentDirection.BULLISH
            score = analysis.edge
        elif analysis.direction == "buy_no":
            sentiment = SentimentDirection.BEARISH
            score = -analysis.edge
        else:
            return None

        return NlpSignal(
            source_text_id=f"llm_analysis:{analysis.market_id}",
            source_provider="llm_market_analyzer",
            source_timestamp=utc_now(),
            text_snippet=f"LLM analysis: {analysis.rationale[:200]}",
            market_ids=[market.market_id],
            relevance=1.0,
            sentiment=sentiment,
            sentiment_score=score,
            urgency=min(analysis.edge * 5, 1.0),
            confidence=analysis.confidence,
            rationale=analysis.rationale,
            entities=[],
            timestamp=utc_now(),
            metadata={
                "source": "llm_market_analyzer",
                "estimated_probability": analysis.estimated_probability,
                "current_price": analysis.current_price,
                "edge": analysis.edge,
                "key_factors": analysis.key_factors,
            },
        )

    async def close(self) -> None:
        await self._client.aclose()


class ClaudeMarketAnalyzer:
    """Uses Claude to evaluate whether prediction markets are mispriced.

    Same analysis prompt and response parsing as LLMMarketAnalyzer but
    targets the Anthropic Messages API instead of OpenAI chat completions.
    """

    COST_PER_CALL = 0.003

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        timeout: int = 45,
        min_edge: float = 0.05,
        max_markets_per_cycle: int = 15,
    ) -> None:
        self._model = model
        self._min_edge = min_edge
        self._max_markets = max_markets_per_cycle
        self.api_calls: int = 0
        self.errors: int = 0
        self.last_call_at: str | None = None

        self._client = httpx.AsyncClient(
            base_url="https://api.anthropic.com",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=httpx.Timeout(timeout),
        )
        logger.info(
            "claude_market_analyzer_initialized",
            model=model,
            min_edge=min_edge,
            max_markets=max_markets_per_cycle,
        )

    def get_stats(self) -> dict[str, Any]:
        return {
            "api_calls": self.api_calls,
            "errors": self.errors,
            "estimated_cost": round(self.api_calls * self.COST_PER_CALL, 4),
            "last_call_at": self.last_call_at,
        }

    async def analyze_markets(
        self,
        markets: list[Market],
        news_headlines: list[str] | None = None,
    ) -> list[NlpSignal]:
        signals: list[NlpSignal] = []
        headlines = news_headlines or []

        candidates = self._select_candidates(markets)

        for market in candidates:
            try:
                analysis = await self._analyze_single(market, headlines)
                if analysis and analysis.direction != "hold":
                    sig = self._analysis_to_signal(analysis, market)
                    if sig:
                        signals.append(sig)
            except Exception:
                logger.exception(
                    "claude_market_analysis_error",
                    market_id=market.market_id,
                )

        logger.info(
            "claude_market_analysis_complete",
            markets_analyzed=len(candidates),
            signals_generated=len(signals),
        )
        return signals

    def _select_candidates(self, markets: list[Market]) -> list[Market]:
        scored: list[tuple[float, Market]] = []
        for m in markets:
            ed = m.exchange_data or {}
            price = ed.get("yes_price")
            volume = float(ed.get("volume", 0) or 0)
            if price is None or price <= 0:
                continue
            if volume < 50:
                continue
            price_quality = 1.0 - abs(price - 0.50) * 2
            vol_score = min(volume / 500.0, 1.0)
            score = price_quality * 0.6 + vol_score * 0.4
            scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[: self._max_markets]]

    async def _analyze_single(
        self,
        market: Market,
        headlines: list[str],
    ) -> MarketAnalysis | None:
        ed = market.exchange_data or {}
        yes_price = ed.get("yes_price")
        if yes_price is None or yes_price <= 0:
            return None

        question = _extract_question(market)
        if not question:
            return None

        end_date = market.end_date if hasattr(market, "end_date") else None
        end_str = str(end_date) if end_date else None

        relevant_news = _filter_relevant_headlines(question, headlines)
        prompt = _build_market_prompt(question, yes_price, relevant_news, end_str)

        payload = {
            "model": self._model,
            "system": _ANALYSIS_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 400,
        }

        try:
            self.api_calls += 1
            self.last_call_at = utc_now().isoformat()
            resp = await self._client.post("/v1/messages", json=payload)
            resp.raise_for_status()
            data = resp.json()
            content_blocks = data.get("content", [])
            text_content = ""
            for block in content_blocks:
                if block.get("type") == "text":
                    text_content += block.get("text", "")

            analysis = _parse_analysis_response(
                text_content, market.market_id, question, yes_price, self._min_edge
            )

            if analysis:
                logger.info(
                    "claude_market_analyzed",
                    market_id=market.market_id,
                    question=question[:80],
                    yes_price=yes_price,
                    estimated_prob=analysis.estimated_probability,
                    edge=analysis.edge,
                    direction=analysis.direction,
                    confidence=analysis.confidence,
                    rationale=analysis.rationale[:100],
                )
            return analysis

        except httpx.TimeoutException:
            self.errors += 1
            logger.warning("claude_analysis_timeout", market_id=market.market_id)
            return None
        except httpx.HTTPStatusError as e:
            self.errors += 1
            body = ""
            try:
                body = e.response.text[:300]
            except Exception:
                pass
            logger.warning(
                "claude_analysis_http_error",
                market_id=market.market_id,
                status=e.response.status_code,
                body=body,
            )
            return None

    def _analysis_to_signal(
        self,
        analysis: MarketAnalysis,
        market: Market,
    ) -> NlpSignal | None:
        if analysis.direction == "hold":
            return None

        if analysis.direction == "buy_yes":
            sentiment = SentimentDirection.BULLISH
            score = analysis.edge
        elif analysis.direction == "buy_no":
            sentiment = SentimentDirection.BEARISH
            score = -analysis.edge
        else:
            return None

        return NlpSignal(
            source_text_id=f"claude_analysis:{analysis.market_id}",
            source_provider="claude_market_analyzer",
            source_timestamp=utc_now(),
            text_snippet=f"Claude analysis: {analysis.rationale[:200]}",
            market_ids=[market.market_id],
            relevance=1.0,
            sentiment=sentiment,
            sentiment_score=score,
            urgency=min(analysis.edge * 5, 1.0),
            confidence=analysis.confidence,
            rationale=analysis.rationale,
            entities=[],
            timestamp=utc_now(),
            metadata={
                "source": "claude_market_analyzer",
                "estimated_probability": analysis.estimated_probability,
                "current_price": analysis.current_price,
                "edge": analysis.edge,
                "key_factors": analysis.key_factors,
            },
        )

    async def close(self) -> None:
        await self._client.aclose()


def _filter_relevant_headlines(
    question: str,
    headlines: list[str],
) -> list[str]:
    """Quick keyword overlap to pick the most relevant headlines."""
    q_tokens = set(re.findall(r"\w+", question.lower()))
    q_tokens -= {"the", "a", "an", "is", "will", "be", "to", "of", "in", "by", "on", "at", "for", "and", "or"}

    scored: list[tuple[int, str]] = []
    for h in headlines:
        h_tokens = set(re.findall(r"\w+", h.lower()))
        overlap = len(q_tokens & h_tokens)
        if overlap >= 1:
            scored.append((overlap, h))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [h for _, h in scored[:5]]


def _parse_analysis_response(
    raw: str,
    market_id: str,
    question: str,
    current_price: float,
    min_edge: float,
) -> MarketAnalysis | None:
    """Shared response parser for both GPT and Claude analyzers."""
    cleaned = _strip_markdown_fences(raw).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("llm_analysis_invalid_json", market_id=market_id, raw=raw[:200])
        return None

    est_prob = _clamp(float(data.get("estimated_probability", 0.5)), 0.0, 1.0)
    direction = data.get("direction", "hold").lower().strip()
    confidence = _clamp(float(data.get("confidence", 0.0)), 0.0, 1.0)
    rationale = str(data.get("rationale", ""))
    key_factors = data.get("key_factors", [])

    if direction == "buy_yes":
        edge = est_prob - current_price
    elif direction == "buy_no":
        edge = current_price - est_prob
    else:
        edge = abs(est_prob - current_price)
        direction = "hold"

    if abs(edge) < min_edge and direction != "hold":
        logger.debug(
            "llm_analysis_insufficient_edge",
            market_id=market_id,
            edge=edge,
            min_edge=min_edge,
        )
        direction = "hold"
        edge = 0.0

    return MarketAnalysis(
        market_id=market_id,
        question=question,
        current_price=current_price,
        estimated_probability=est_prob,
        edge=edge,
        direction=direction,
        confidence=confidence,
        rationale=rationale,
        key_factors=key_factors if isinstance(key_factors, list) else [],
    )


def _extract_question(market: Market) -> str:
    """Get the market question/title from the Market object."""
    if market.question:
        return market.question
    ed = market.exchange_data or {}
    for key in ("title", "question", "subtitle"):
        val = ed.get(key)
        if val:
            return str(val)
    if market.slug:
        return market.slug.replace("-", " ").title()
    return market.market_id


def _strip_markdown_fences(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    text = re.sub(r"\n?```\s*$", "", text)
    return text


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))
