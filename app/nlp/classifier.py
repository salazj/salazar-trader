"""
Text classifiers for Level 3 intelligence.

Architecture:
  BaseClassifier      – abstract interface for any classifier
  EventTypeClassifier – detects what kind of event the text describes
  SentimentClassifier – directional impact (bullish / bearish / neutral)
  UrgencyClassifier   – how time-sensitive the information is
  RelevanceClassifier – how relevant text is to a specific market
  CompositeClassifier – chains all sub-classifiers into one result
  LlmClassifierAdapter – pluggable adapter for future LLM-based classification

Each sub-classifier can be tested and improved independently.  The
CompositeClassifier is what the pipeline uses by default.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

from app.nlp.signals import (
    ClassificationResult,
    EventType,
    SentimentDirection,
)
from app.monitoring import get_logger

logger = get_logger(__name__)


# ── Abstract base ──────────────────────────────────────────────────────


class BaseClassifier(ABC):
    """Abstract text classifier interface."""

    @abstractmethod
    def classify(
        self,
        text: str,
        market_context: dict[str, Any] | None = None,
    ) -> ClassificationResult:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...


# ── Event-type detection ───────────────────────────────────────────────


class EventTypeClassifier:
    """Detects what kind of real-world event the text describes."""

    _PATTERNS: list[tuple[re.Pattern[str], EventType, float]] = [
        (re.compile(r"\b(court|ruling|judge|lawsuit|legal|indictment|verdict|prosecution|acquit|convict)\b", re.I),
         EventType.LEGAL_RULING, 0.8),
        (re.compile(r"\b(SEC|regulation|regulatory|FCC|FDA|ban|sanction|compliance|enforcement|fine|penalty)\b", re.I),
         EventType.REGULATORY, 0.8),
        (re.compile(r"\b(election|poll|vote|ballot|primary|caucus|candidate|campaign|gubernatorial|senate|congress)\b", re.I),
         EventType.ELECTION, 0.85),
        (re.compile(r"\b(GDP|inflation|unemployment|jobs|CPI|interest rate|fed|federal reserve|rate cut|rate hike|payroll|treasury)\b", re.I),
         EventType.ECONOMIC, 0.8),
        (re.compile(r"\b(celebrity|star|actor|musician|influencer|scandal|arrest|controversy)\b", re.I),
         EventType.CELEBRITY, 0.6),
        (re.compile(r"\b(game|match|tournament|championship|finals|playoff|season|team|league|coach|score|win|victory)\b", re.I),
         EventType.SPORTS, 0.7),
        (re.compile(r"\b(war|conflict|treaty|NATO|UN|diplomacy|geopolitical|invasion|military|ceasefire|sanctions?)\b", re.I),
         EventType.GEOPOLITICAL, 0.85),
        (re.compile(r"\b(bitcoin|BTC|crypto|ethereum|ETH|blockchain|token|defi|exchange hack|mining|halving)\b", re.I),
         EventType.CRYPTO, 0.8),
    ]

    def detect(self, text: str) -> tuple[EventType, float]:
        """Return (event_type, pattern_confidence)."""
        best_type = EventType.OTHER
        best_conf = 0.0
        text_lower = text.lower()
        for pattern, etype, base_conf in self._PATTERNS:
            matches = pattern.findall(text_lower)
            if matches:
                # More keyword hits = higher confidence
                conf = min(base_conf + len(matches) * 0.05, 1.0)
                if conf > best_conf:
                    best_type = etype
                    best_conf = conf
        return best_type, best_conf


# ── Directional impact (sentiment) ─────────────────────────────────────


class SentimentClassifier:
    """Estimates directional impact: bullish, bearish, or neutral.

    Uses keyword counting with negation awareness.
    """

    _BULLISH = {
        "win", "wins", "won", "winning", "pass", "passes", "passed",
        "approve", "approved", "approval", "surge", "surges", "surging",
        "rally", "rise", "rising", "increase", "positive", "success",
        "confirm", "confirmed", "gain", "gains", "advance", "advancing",
        "breakthrough", "bullish", "soar", "soaring", "upgrade", "beat",
        "beats", "exceed", "exceeds", "outperform", "record high",
        "accept", "accepted", "agree", "agreed", "settle", "settled",
    }

    _BEARISH = {
        "lose", "loses", "lost", "losing", "fail", "fails", "failed",
        "reject", "rejected", "rejection", "crash", "crashes", "crashing",
        "drop", "drops", "dropping", "decline", "declining", "fall",
        "falling", "negative", "defeat", "defeated", "deny", "denied",
        "loss", "losses", "collapse", "collapsing", "bearish", "downgrade",
        "plunge", "plunging", "scandal", "miss", "misses", "underperform",
        "bankrupt", "bankruptcy", "default", "suspend", "suspended",
        "cancel", "cancelled", "withdraw", "withdrawn",
    }

    _NEGATION = {"not", "no", "never", "neither", "nor", "don't", "doesn't", "won't", "isn't", "wasn't", "aren't"}

    def detect(self, text: str) -> tuple[SentimentDirection, float]:
        """Return (direction, sentiment_score in [-1, 1])."""
        words = re.findall(r"\b\w+\b", text.lower())

        bull_score = 0.0
        bear_score = 0.0

        for i, word in enumerate(words):
            negated = i > 0 and words[i - 1] in self._NEGATION
            if word in self._BULLISH:
                if negated:
                    bear_score += 1.0
                else:
                    bull_score += 1.0
            elif word in self._BEARISH:
                if negated:
                    bull_score += 1.0
                else:
                    bear_score += 1.0

        total = bull_score + bear_score
        if total == 0:
            return SentimentDirection.NEUTRAL, 0.0

        raw = (bull_score - bear_score) / total
        if raw > 0.1:
            return SentimentDirection.BULLISH, min(raw, 1.0)
        if raw < -0.1:
            return SentimentDirection.BEARISH, max(raw, -1.0)
        return SentimentDirection.NEUTRAL, raw


# ── Urgency detection ──────────────────────────────────────────────────


class UrgencyClassifier:
    """Estimates time-sensitivity of the text.

    Uses keyword presence, all-caps detection, and exclamation patterns.
    """

    _URGENCY_PHRASES = [
        ("breaking", 0.40),
        ("urgent", 0.35),
        ("just in", 0.30),
        ("developing", 0.25),
        ("alert", 0.30),
        ("happening now", 0.35),
        ("imminent", 0.30),
        ("emergency", 0.35),
        ("live", 0.15),
        ("confirmed", 0.10),
        ("moments ago", 0.25),
        ("flash", 0.20),
        ("exclusive", 0.15),
    ]

    def detect(self, text: str) -> float:
        """Return urgency score in [0, 1]."""
        text_lower = text.lower()
        score = 0.0

        for phrase, weight in self._URGENCY_PHRASES:
            if phrase in text_lower:
                score += weight

        # All-caps words boost (common in breaking news)
        caps_words = re.findall(r"\b[A-Z]{3,}\b", text)
        score += min(len(caps_words) * 0.05, 0.15)

        # Exclamation marks
        excl = text.count("!")
        score += min(excl * 0.05, 0.10)

        return min(score, 1.0)


# ── Relevance to a specific market ─────────────────────────────────────


class RelevanceClassifier:
    """Scores how relevant text is to a specific market question.

    Uses weighted token overlap with IDF-like term weighting:
    rare shared words contribute more than common ones.
    """

    _STOP_WORDS = {
        "the", "and", "for", "will", "this", "that", "with", "from",
        "are", "was", "has", "have", "been", "not", "but", "its",
        "who", "what", "when", "where", "how", "can", "does", "did",
        "would", "could", "should", "they", "their", "there", "than",
        "then", "into", "also", "just", "about", "more", "some",
        "any", "each", "every", "all", "most", "other", "after",
        "before", "between", "over", "under", "only",
    }

    def score(self, text: str, market_question: str) -> float:
        """Return relevance in [0, 1]."""
        text_tokens = self._meaningful_tokens(text.lower())
        question_tokens = self._meaningful_tokens(market_question.lower())

        if not question_tokens or not text_tokens:
            return 0.0

        # Weighted overlap: each shared token contributes inversely to its
        # frequency in the question (rarer shared words matter more)
        overlap = text_tokens & question_tokens
        if not overlap:
            return 0.0

        # Simple IDF approximation: weight = 1 / (1 + count_in_question)
        q_counts: dict[str, int] = {}
        for tok in re.findall(r"\b\w{3,}\b", market_question.lower()):
            if tok not in self._STOP_WORDS:
                q_counts[tok] = q_counts.get(tok, 0) + 1

        weighted_score = sum(1.0 / (1 + q_counts.get(tok, 1)) for tok in overlap)
        max_possible = sum(1.0 / (1 + q_counts.get(tok, 1)) for tok in question_tokens)

        if max_possible == 0:
            return 0.0

        return min(weighted_score / max_possible + 0.05, 1.0)

    def _meaningful_tokens(self, text: str) -> set[str]:
        tokens = set(re.findall(r"\b\w{3,}\b", text))
        return tokens - self._STOP_WORDS


# ── Entity extraction ──────────────────────────────────────────────────


class EntityExtractor:
    """Simple entity extraction based on capitalization patterns."""

    _TITLE_CASE_RE = re.compile(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\b")
    _ACRONYM_RE = re.compile(r"\b[A-Z]{2,6}\b")

    # Common words that happen to be capitalized at sentence starts
    _COMMON_STARTS = {
        "The", "This", "That", "There", "These", "Those", "It", "He",
        "She", "They", "We", "You", "New", "Just", "Also", "But",
        "And", "However", "Meanwhile", "After", "Before", "Breaking",
    }
    _COMMON_ACRONYMS = {"US", "UK", "EU", "AM", "PM", "CEO", "AI", "IT"}

    def extract(self, text: str) -> list[str]:
        entities: list[str] = []

        # Title-case phrases (likely proper nouns)
        for match in self._TITLE_CASE_RE.finditer(text):
            name = match.group()
            if name not in self._COMMON_STARTS:
                entities.append(name)

        # Acronyms (likely organizations, tickers)
        for match in self._ACRONYM_RE.finditer(text):
            acr = match.group()
            if acr not in self._COMMON_ACRONYMS:
                entities.append(acr)

        # Deduplicate preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for e in entities:
            if e not in seen:
                seen.add(e)
                unique.append(e)
        return unique


# ── Composite classifier (default) ────────────────────────────────────


class KeywordClassifier(BaseClassifier):
    """Composes all sub-classifiers into one ClassificationResult.

    This is the default classifier used by the NLP pipeline.  It requires
    zero external dependencies and always works.
    """

    name: str = "keyword"  # type: ignore[assignment]

    def __init__(self) -> None:
        self._event = EventTypeClassifier()
        self._sentiment = SentimentClassifier()
        self._urgency = UrgencyClassifier()
        self._relevance = RelevanceClassifier()
        self._entities = EntityExtractor()

    def classify(
        self,
        text: str,
        market_context: dict[str, Any] | None = None,
    ) -> ClassificationResult:
        event_type, event_conf = self._event.detect(text)
        sentiment, sentiment_score = self._sentiment.detect(text)
        urgency = self._urgency.detect(text)
        entities = self._entities.extract(text)

        # Relevance depends on market context
        if market_context:
            question = str(market_context.get("question", ""))
            relevance = self._relevance.score(text, question) if question else 0.3
        else:
            relevance = 0.3

        # Confidence is a weighted combination of sub-classifier outputs
        confidence = min(
            0.20
            + relevance * 0.30
            + abs(sentiment_score) * 0.25
            + event_conf * 0.15
            + urgency * 0.10,
            1.0,
        )

        rationale_parts = [
            f"event={event_type.value}({event_conf:.2f})",
            f"sentiment={sentiment.value}({sentiment_score:+.2f})",
            f"urgency={urgency:.2f}",
            f"relevance={relevance:.2f}",
        ]
        if entities:
            rationale_parts.append(f"entities=[{', '.join(entities[:5])}]")

        return ClassificationResult(
            relevance=relevance,
            sentiment=sentiment,
            sentiment_score=sentiment_score,
            event_type=event_type,
            urgency=urgency,
            confidence=confidence,
            rationale="keyword: " + " | ".join(rationale_parts),
            entities=entities,
        )


# ── LLM output validation ──────────────────────────────────────────────


_VALID_EVENT_TYPES = {e.value for e in EventType}
_VALID_SENTIMENTS = {s.value for s in SentimentDirection}

_EVENT_MAP: dict[str, EventType] = {e.value: e for e in EventType}
_SENT_MAP: dict[str, SentimentDirection] = {s.value: s for s in SentimentDirection}

# Small local models (e.g. phi3) frequently emit event-type labels outside our
# enum. Fold the common ones into the closest supported category so they map
# correctly instead of spamming validation warnings and collapsing to 'other'.
_EVENT_SYNONYMS: dict[str, EventType] = {
    "politics": EventType.ELECTION,
    "political": EventType.ELECTION,
    "vote": EventType.ELECTION,
    "voting": EventType.ELECTION,
    "policy": EventType.ELECTION,
    "government": EventType.GEOPOLITICAL,
    "geopolitics": EventType.GEOPOLITICAL,
    "international": EventType.GEOPOLITICAL,
    "war": EventType.GEOPOLITICAL,
    "military": EventType.GEOPOLITICAL,
    "conflict": EventType.GEOPOLITICAL,
    "security": EventType.GEOPOLITICAL,
    "diplomacy": EventType.GEOPOLITICAL,
    "terrorism": EventType.GEOPOLITICAL,
    "economy": EventType.ECONOMIC,
    "employment": EventType.ECONOMIC,
    "jobs": EventType.ECONOMIC,
    "labor": EventType.ECONOMIC,
    "inflation": EventType.ECONOMIC,
    "investment": EventType.ECONOMIC,
    "business": EventType.ECONOMIC,
    "finance": EventType.ECONOMIC,
    "financial": EventType.ECONOMIC,
    "market": EventType.ECONOMIC,
    "markets": EventType.ECONOMIC,
    "stocks": EventType.ECONOMIC,
    "earnings": EventType.ECONOMIC,
    "trade": EventType.ECONOMIC,
    "tariff": EventType.ECONOMIC,
    "real_estate": EventType.ECONOMIC,
    "housing": EventType.ECONOMIC,
    "energy": EventType.ECONOMIC,
    "legal": EventType.LEGAL_RULING,
    "law": EventType.LEGAL_RULING,
    "lawsuit": EventType.LEGAL_RULING,
    "court": EventType.LEGAL_RULING,
    "ruling": EventType.LEGAL_RULING,
    "verdict": EventType.LEGAL_RULING,
    "indictment": EventType.LEGAL_RULING,
    "crime": EventType.LEGAL_RULING,
    "criminal": EventType.LEGAL_RULING,
    "regulation": EventType.REGULATORY,
    "regulatory": EventType.REGULATORY,
    "sec": EventType.REGULATORY,
    "fda": EventType.REGULATORY,
    "antitrust": EventType.REGULATORY,
    "cryptocurrency": EventType.CRYPTO,
    "bitcoin": EventType.CRYPTO,
    "blockchain": EventType.CRYPTO,
    "sport": EventType.SPORTS,
    "entertainment": EventType.CELEBRITY,
    "celebrity": EventType.CELEBRITY,
    "media": EventType.CELEBRITY,
}

_REQUIRED_FIELDS = {
    "event_type": str,
    "sentiment": str,
    "sentiment_score": (int, float),
    "urgency": (int, float),
    "relevance": (int, float),
    "confidence": (int, float),
    "rationale": str,
    "entities": list,
}

_MARKDOWN_FENCE_RE = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```",
    re.DOTALL,
)


class LlmValidationError:
    """Describes a single validation failure in LLM output."""

    __slots__ = ("field", "issue", "value")

    def __init__(self, field: str, issue: str, value: Any = None) -> None:
        self.field = field
        self.issue = issue
        self.value = value

    def __repr__(self) -> str:
        return f"LlmValidationError({self.field!r}, {self.issue!r})"

    def __str__(self) -> str:
        return f"{self.field}: {self.issue}"


class LlmOutputValidator:
    """Validates and sanitises raw LLM output into a ClassificationResult.

    Steps:
      1. Strip markdown code fences (```json ... ```)
      2. Extract the first JSON object from the response
      3. Validate required fields, types, ranges, and enum values
      4. Clamp numeric values to valid ranges
      5. Collect all validation errors (non-fatal where possible)
    """

    @staticmethod
    def strip_fences(raw: str) -> str:
        """Remove markdown code fences that many LLMs wrap around JSON."""
        match = _MARKDOWN_FENCE_RE.search(raw)
        if match:
            return match.group(1).strip()
        return raw.strip()

    @staticmethod
    def extract_json(raw: str) -> str:
        """Extract the first JSON object from a string.

        Handles cases where the LLM adds explanation text before or
        after the JSON.
        """
        depth = 0
        start = -1
        for i, ch in enumerate(raw):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start >= 0:
                    return raw[start : i + 1]
        if start >= 0:
            return raw[start:]
        return raw

    @classmethod
    def validate(cls, raw: str) -> tuple[ClassificationResult, list[LlmValidationError]]:
        """Parse, validate, and return (result, errors).

        Returns the best-effort ClassificationResult even when
        validation errors are present — fields that fail validation
        get safe defaults.  The error list lets the caller decide
        whether to trust the result.
        """
        errors: list[LlmValidationError] = []

        cleaned = cls.strip_fences(raw)
        cleaned = cls.extract_json(cleaned)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            errors.append(LlmValidationError("_json", f"invalid JSON: {exc}"))
            return ClassificationResult(rationale="LLM output was not valid JSON"), errors

        if not isinstance(data, dict):
            errors.append(LlmValidationError("_root", f"expected object, got {type(data).__name__}"))
            return ClassificationResult(rationale="LLM output was not a JSON object"), errors

        # ── Required fields & types ────────────────────────────────
        for field, expected_type in _REQUIRED_FIELDS.items():
            if field not in data:
                errors.append(LlmValidationError(field, "missing"))
            elif not isinstance(data[field], expected_type):
                errors.append(LlmValidationError(
                    field,
                    f"wrong type: expected {expected_type}, got {type(data[field]).__name__}",
                    value=data[field],
                ))

        # ── Enum validation ────────────────────────────────────────
        # event_type is a soft category: map exact values and common synonyms;
        # anything else falls back to 'other' silently (non-fatal, avoids noise).
        raw_event = str(data.get("event_type", "")).lower().strip().replace(" ", "_")
        event_type = (
            _EVENT_MAP.get(raw_event)
            or _EVENT_SYNONYMS.get(raw_event)
            or EventType.OTHER
        )

        raw_sent = str(data.get("sentiment", "")).lower().strip()
        if raw_sent and raw_sent not in _VALID_SENTIMENTS:
            errors.append(LlmValidationError(
                "sentiment",
                f"unknown value {raw_sent!r}; using 'neutral'",
                value=raw_sent,
            ))
        sentiment = _SENT_MAP.get(raw_sent, SentimentDirection.NEUTRAL)

        # ── Numeric fields: safe parse + clamp ─────────────────────
        def _safe_float(key: str, lo: float, hi: float, default: float) -> float:
            val = data.get(key, default)
            try:
                f = float(val)
            except (TypeError, ValueError):
                errors.append(LlmValidationError(
                    key, f"not a number: {val!r}; using {default}", value=val,
                ))
                return default
            if f < lo or f > hi:
                errors.append(LlmValidationError(
                    key, f"out of range [{lo}, {hi}]: {f}; clamping", value=f,
                ))
            return max(lo, min(f, hi))

        sentiment_score = _safe_float("sentiment_score", -1.0, 1.0, 0.0)
        urgency = _safe_float("urgency", 0.0, 1.0, 0.0)
        relevance = _safe_float("relevance", 0.0, 1.0, 0.0)
        confidence = _safe_float("confidence", 0.0, 1.0, 0.0)

        # ── Entities validation ────────────────────────────────────
        raw_entities = data.get("entities", [])
        if isinstance(raw_entities, list):
            entities = [str(e) for e in raw_entities if isinstance(e, str)]
            if len(entities) != len(raw_entities):
                errors.append(LlmValidationError(
                    "entities",
                    f"filtered {len(raw_entities) - len(entities)} non-string items",
                ))
        else:
            errors.append(LlmValidationError(
                "entities", f"expected list, got {type(raw_entities).__name__}",
            ))
            entities = []

        rationale = str(data.get("rationale", "llm"))

        result = ClassificationResult(
            relevance=relevance,
            sentiment=sentiment,
            sentiment_score=sentiment_score,
            event_type=event_type,
            urgency=urgency,
            confidence=confidence,
            rationale=f"llm: {rationale}",
            entities=entities,
        )

        return result, errors


# ── LLM classifier adapter ────────────────────────────────────────────


class LlmClassifierAdapter(BaseClassifier):
    """Adapter for external LLM-based classification.

    To use: subclass and implement ``_call_llm_api`` to call your chosen
    provider (OpenAI, Anthropic, local vLLM, etc.).  The base class
    handles prompt construction, output parsing, and validation.

    The adapter **never** places trades or bypasses risk controls.
    It only produces ``ClassificationResult`` objects with structured
    fields: relevance, sentiment, urgency, entities, rationale.
    """

    name: str = "llm"  # type: ignore[assignment]

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
        max_validation_errors: int = 3,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_validation_errors = max_validation_errors

    def classify(
        self,
        text: str,
        market_context: dict[str, Any] | None = None,
    ) -> ClassificationResult:
        if not self._api_key:
            logger.debug("llm_classifier_no_api_key")
            return ClassificationResult(
                rationale="LLM adapter not configured (no API key)",
            )

        prompt = self._build_prompt(text, market_context)
        try:
            raw_response = self._call_llm_api(prompt)
        except Exception:
            logger.exception("llm_api_call_failed")
            return ClassificationResult(
                rationale="LLM API call failed — falling back to default",
            )

        result, errors = LlmOutputValidator.validate(raw_response)
        if errors:
            error_msgs = [str(e) for e in errors]
            logger.warning(
                "llm_output_validation_errors",
                model=self._model,
                error_count=len(errors),
                errors=error_msgs[:5],
                raw_length=len(raw_response),
            )
            if len(errors) > self._max_validation_errors:
                logger.warning(
                    "llm_output_rejected",
                    model=self._model,
                    error_count=len(errors),
                    threshold=self._max_validation_errors,
                )
                return ClassificationResult(
                    rationale=(
                        f"LLM output rejected: {len(errors)} validation errors "
                        f"(max {self._max_validation_errors})"
                    ),
                )

        return result

    def _build_prompt(
        self,
        text: str,
        market_context: dict[str, Any] | None,
    ) -> str:
        parts = [f"Headline: {text}"]
        if market_context:
            question = market_context.get("question", "")
            if question:
                parts.append(f"Market question: {question}")
        return "\n".join(parts)

    def _call_llm_api(self, user_prompt: str) -> str:
        """Override in a concrete subclass to call the actual LLM API.

        Must return a raw string.  The base class handles JSON parsing
        and validation — the subclass just needs to return the LLM's
        text output.
        """
        logger.warning("llm_classifier_stub_call")
        return json.dumps({
            "event_type": "other",
            "sentiment": "neutral",
            "sentiment_score": 0.0,
            "urgency": 0.0,
            "relevance": 0.0,
            "confidence": 0.0,
            "rationale": "LLM stub — not implemented",
            "entities": [],
        })

    @staticmethod
    def _parse_response(raw: str) -> ClassificationResult:
        """Parse and validate LLM output.  Kept for backward compatibility."""
        result, _ = LlmOutputValidator.validate(raw)
        return result


# ── Classifier that chains keyword + optional LLM ─────────────────────


class HybridClassifier(BaseClassifier):
    """Uses the keyword classifier as baseline, optionally enhanced by LLM.

    When an LLM is available: runs keyword first, then LLM.  Merges by
    taking the LLM result when its confidence exceeds a threshold,
    otherwise falls back to keyword.
    """

    name: str = "hybrid"  # type: ignore[assignment]

    def __init__(
        self,
        keyword: KeywordClassifier | None = None,
        llm: LlmClassifierAdapter | None = None,
        llm_confidence_threshold: float = 0.5,
    ) -> None:
        self._keyword = keyword or KeywordClassifier()
        self._llm = llm
        self._llm_threshold = llm_confidence_threshold

    def classify(
        self,
        text: str,
        market_context: dict[str, Any] | None = None,
    ) -> ClassificationResult:
        kw_result = self._keyword.classify(text, market_context)

        if self._llm is None:
            return kw_result

        llm_result = self._llm.classify(text, market_context)
        if llm_result.confidence >= self._llm_threshold:
            logger.debug(
                "hybrid_using_llm",
                llm_confidence=llm_result.confidence,
                kw_confidence=kw_result.confidence,
            )
            return llm_result

        logger.debug(
            "hybrid_using_keyword",
            llm_confidence=llm_result.confidence,
            kw_confidence=kw_result.confidence,
        )
        return kw_result
