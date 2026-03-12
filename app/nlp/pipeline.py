"""
NLP pipeline: the full text-to-signal flow.

    NewsItem
      │
      ▼
    TextNormalizer        ← clean, deduplicate
      │
      ▼
    Classifier            ← event type, sentiment, urgency, entities
      │
      ▼
    MarketMapper          ← link text to candidate markets
      │
      ▼
    Signal Generator      ← NlpSignal per matched market
      │
      ▼
    NormalizedSignal      ← ready for the decision engine

Every step is logged so you can trace exactly how a piece of text
became (or didn't become) a trading signal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.data.models import Market, SignalAction
from app.decision.ensemble import normalize_confidence
from app.decision.signals import IntelligenceLayer, NormalizedSignal
from app.nlp.classifier import BaseClassifier, KeywordClassifier
from app.nlp.market_mapper import MarketMapper
from app.nlp.normalizer import TextNormalizer
from app.nlp.signals import ClassificationResult, NlpSignal, SentimentDirection
from app.monitoring import get_logger
from app.utils.helpers import utc_now

if TYPE_CHECKING:
    from app.news.models import NewsItem

logger = get_logger(__name__)


class ProcessingTrace:
    """Records every step of the pipeline for one NewsItem."""

    def __init__(self, item_id: str) -> None:
        self.item_id = item_id
        self.normalized: bool = False
        self.was_duplicate: bool = False
        self.classification: ClassificationResult | None = None
        self.markets_matched: int = 0
        self.signals_generated: int = 0
        self.dropped_reason: str = ""
        self.steps: list[str] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "normalized": self.normalized,
            "was_duplicate": self.was_duplicate,
            "classification_sentiment": self.classification.sentiment.value if self.classification else None,
            "classification_event": self.classification.event_type.value if self.classification else None,
            "classification_confidence": self.classification.confidence if self.classification else None,
            "markets_matched": self.markets_matched,
            "signals_generated": self.signals_generated,
            "dropped_reason": self.dropped_reason,
            "steps": self.steps,
        }


class NlpPipeline:
    """Full text → signal pipeline with normalization, classification,
    market mapping, and structured event signal generation."""

    def __init__(
        self,
        classifier: BaseClassifier | None = None,
        market_mapper: MarketMapper | None = None,
        normalizer: TextNormalizer | None = None,
        min_relevance_for_signal: float = 0.1,
        min_confidence_for_signal: float = 0.1,
    ) -> None:
        self._classifier = classifier or KeywordClassifier()
        self._mapper = market_mapper or MarketMapper()
        self._normalizer = normalizer or TextNormalizer()
        self._min_relevance = min_relevance_for_signal
        self._min_confidence = min_confidence_for_signal

    def process_item(
        self,
        item: NewsItem,
        active_markets: list[Market],
    ) -> list[NlpSignal]:
        """Full pipeline for one news item.  Returns NlpSignal per matched market."""
        trace = ProcessingTrace(item.item_id)

        # ── Step 1: Normalize text ──
        norm = self._normalizer.normalize(item.text)
        trace.normalized = True
        trace.steps.append(f"normalized ({', '.join(norm.steps_applied) or 'no changes'})")

        if norm.is_duplicate:
            trace.was_duplicate = True
            trace.dropped_reason = "near_duplicate"
            trace.steps.append("dropped: near-duplicate")
            logger.debug("nlp_item_dropped_duplicate", item_id=item.item_id)
            self._log_trace(trace)
            return []

        clean_text = norm.normalized

        # ── Step 2: Map to markets (before classification so we can pass context) ──
        pre_matches = self._mapper.find_matches(
            text=clean_text,
            entities=[],
            markets=active_markets,
        )

        # ── Step 3: Classify (with market context if available) ──
        market_context = None
        if pre_matches:
            best = max(pre_matches, key=lambda m: m.relevance_score)
            ed = best.market.exchange_data or {}
            question = ed.get("title") or ed.get("question") or best.market.slug or ""
            if question:
                market_context = {"question": question}

        result = self._classifier.classify(clean_text, market_context)
        trace.classification = result
        trace.steps.append(f"classified: {result.event_type.value}/{result.sentiment.value}")

        if result.relevance < self._min_relevance and result.confidence < self._min_confidence:
            trace.dropped_reason = f"low_relevance({result.relevance:.2f})_and_confidence({result.confidence:.2f})"
            trace.steps.append(f"dropped: {trace.dropped_reason}")
            self._log_trace(trace)
            return []

        # ── Step 4: Refine market mapping with entities from classification ──
        matches = self._mapper.find_matches(
            text=clean_text,
            entities=result.entities,
            markets=active_markets,
        )
        trace.markets_matched = len(matches)
        trace.steps.append(f"matched {len(matches)} market(s)")

        if not matches:
            trace.dropped_reason = "no_market_match"
            trace.steps.append("dropped: no market match")
            self._log_trace(trace)
            return []

        # ── Step 5: Generate structured signals ──
        signals: list[NlpSignal] = []
        for m in matches:
            combined_confidence = result.confidence * m.relevance_score
            sig = NlpSignal(
                source_text_id=item.item_id,
                source_provider=item.source,
                source_timestamp=item.timestamp,
                text_snippet=clean_text[:200],
                market_ids=[m.market.market_id or m.market.condition_id],
                relevance=m.relevance_score,
                sentiment=result.sentiment,
                sentiment_score=result.sentiment_score,
                event_type=result.event_type,
                urgency=result.urgency,
                confidence=combined_confidence,
                rationale=result.rationale,
                entities=result.entities,
                timestamp=utc_now(),
                metadata={
                    "token_overlap_score": m.token_overlap_score,
                    "entity_score": m.entity_score,
                    "matched_keywords": m.matched_keywords[:10],
                    "matched_entities": m.matched_entities[:10],
                    "ambiguous": m.ambiguous,
                    "normalization_steps": norm.steps_applied,
                },
            )
            signals.append(sig)

        trace.signals_generated = len(signals)
        trace.steps.append(f"generated {len(signals)} signal(s)")
        self._log_trace(trace)
        return signals

    def process_batch(
        self,
        items: list[NewsItem],
        active_markets: list[Market],
    ) -> list[NlpSignal]:
        all_signals: list[NlpSignal] = []
        for item in items:
            try:
                sigs = self.process_item(item, active_markets)
                all_signals.extend(sigs)
            except Exception:
                logger.exception("nlp_pipeline_item_error", item_id=item.item_id)
        logger.info(
            "nlp_batch_processed",
            items=len(items),
            signals=len(all_signals),
        )
        return all_signals

    def _log_trace(self, trace: ProcessingTrace) -> None:
        logger.debug("nlp_processing_trace", **trace.to_dict())


# ── Signal conversion for decision engine ──────────────────────────────


def nlp_signal_to_layered(
    signal: NlpSignal,
    token_id: str = "",
    *,
    instrument_id: str = "",
    exchange: str = "",
) -> NormalizedSignal:
    """Convert an NlpSignal to a NormalizedSignal for the decision engine.

    The NLP pipeline generates *structured signals*, not trade orders.
    The decision engine evaluates them alongside L1 and L2 signals.
    """
    iid = instrument_id or token_id
    if signal.sentiment == SentimentDirection.BULLISH:
        action = SignalAction.BUY_YES
        direction = 1
    elif signal.sentiment == SentimentDirection.BEARISH:
        action = SignalAction.SELL_YES
        direction = -1
    else:
        action = SignalAction.HOLD
        direction = 0

    raw_conf = signal.confidence
    normed = normalize_confidence(raw_conf, IntelligenceLayer.NLP)

    return NormalizedSignal(
        layer=IntelligenceLayer.NLP,
        source_name=f"nlp:{signal.source_provider}",
        market_id=signal.market_ids[0] if signal.market_ids else "",
        token_id=iid,
        instrument_id=iid,
        exchange=exchange,
        action=action,
        direction=direction,
        raw_confidence=raw_conf,
        normalized_confidence=normed,
        expected_edge=signal.sentiment_score * raw_conf * 0.01,
        rationale=signal.rationale,
        features_used=["sentiment", "relevance", "urgency", "event_type"],
        timestamp=signal.timestamp,
        metadata={
            "event_type": signal.event_type.value,
            "urgency": signal.urgency,
            "relevance": signal.relevance,
            "entities": signal.entities,
            "text_snippet": signal.text_snippet[:80],
        },
    )
