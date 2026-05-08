"""Strict JSON schema and validation for local LLM verdicts.

The LLM is allowed to return only the following structure:

    {
        "ticker": "NVDA",
        "sentiment": "bullish | neutral | bearish",
        "relevance_score": 0.0,
        "confidence_adjustment": 0.0,
        "risk_flags": [],
        "summary": "short explanation",
        "should_gate_trade": false
    }

If parsing or validation fails — for any reason — the system returns a
``safe_default_verdict``: neutral sentiment, zero confidence adjustment,
``should_gate_trade=False``. This guarantees the LLM cannot accidentally
green-light reckless behavior.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator


class LLMSentiment(str, Enum):
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"


class LLMVerdict(BaseModel):
    """Validated LLM verdict for a single ticker / news context."""

    ticker: str = ""
    sentiment: LLMSentiment = LLMSentiment.NEUTRAL
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_adjustment: float = Field(default=0.0, ge=-0.25, le=0.25)
    risk_flags: list[str] = Field(default_factory=list)
    summary: str = ""
    should_gate_trade: bool = False

    @field_validator("ticker")
    @classmethod
    def upper_ticker(cls, v: str) -> str:
        return (v or "").strip().upper()

    @field_validator("risk_flags", mode="before")
    @classmethod
    def coerce_risk_flags(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        return [str(x).strip() for x in v if str(x).strip()]


def safe_default_verdict(ticker: str = "", reason: str = "") -> LLMVerdict:
    """Conservative fallback when LLM output cannot be trusted.

    Defaults to neutral sentiment, no confidence boost, and — importantly —
    ``should_gate_trade=False``. The L3 layer will simply contribute zero
    weight to the decision, never reckless approval.
    """
    return LLMVerdict(
        ticker=ticker,
        sentiment=LLMSentiment.NEUTRAL,
        relevance_score=0.0,
        confidence_adjustment=0.0,
        risk_flags=[reason] if reason else [],
        summary=reason or "fallback: invalid or missing LLM response",
        should_gate_trade=False,
    )


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json_blob(text: str) -> str | None:
    """Find the largest balanced JSON object inside a free-form response."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("{") and text.rstrip().endswith("}"):
        return text
    match = _JSON_BLOCK_RE.search(text)
    return match.group(0) if match else None


def parse_llm_verdict(raw: str, *, ticker: str = "") -> LLMVerdict:
    """Parse the raw model output into an ``LLMVerdict``.

    Any failure (non-JSON, missing keys, out-of-range values) yields the
    safe-default verdict. The parser always returns a valid object so the
    decision engine never needs to handle exceptions from the LLM path.
    """
    blob = _extract_json_blob(raw or "")
    if blob is None:
        return safe_default_verdict(ticker, "no JSON found")

    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return safe_default_verdict(ticker, "malformed JSON")

    if not isinstance(data, dict):
        return safe_default_verdict(ticker, "JSON not an object")

    if "ticker" not in data and ticker:
        data["ticker"] = ticker

    raw_sentiment = str(data.get("sentiment", "neutral")).lower().strip()
    if raw_sentiment not in {"bullish", "neutral", "bearish"}:
        raw_sentiment = "neutral"
    data["sentiment"] = raw_sentiment

    try:
        data["relevance_score"] = float(data.get("relevance_score", 0.0))
        data["confidence_adjustment"] = float(data.get("confidence_adjustment", 0.0))
    except (TypeError, ValueError):
        return safe_default_verdict(ticker, "non-numeric scores")

    data["relevance_score"] = max(0.0, min(1.0, data["relevance_score"]))
    data["confidence_adjustment"] = max(
        -0.25, min(0.25, data["confidence_adjustment"])
    )

    try:
        return LLMVerdict(**data)
    except ValidationError:
        return safe_default_verdict(ticker, "validation error")
