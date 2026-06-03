"""
Comprehensive tests for the NLP intelligence layer: normalizer, classifiers,
market mapper, pipeline, signal conversion, providers, ingestion, replay, and storage.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from app.data.models import Market, MarketToken, SignalAction
from app.news.ingestion import NewsIngestionService
from app.news.models import NewsItem
from app.nlp.classifier import (
    EntityExtractor,
    EventTypeClassifier,
    HybridClassifier,
    KeywordClassifier,
    LlmClassifierAdapter,
    LlmOutputValidator,
    LlmValidationError,
    RelevanceClassifier,
    SentimentClassifier,
    UrgencyClassifier,
)
from app.nlp.providers.llm_provider import (
    LocalOpenSourceProvider,
    MockLLMProvider,
    ModelFamily,
    build_llm_classifier,
)
from app.nlp.market_mapper import MarketMapper
from app.nlp.normalizer import TextNormalizer
from app.nlp.pipeline import NlpPipeline, nlp_signal_to_layered
from app.nlp.providers.file_provider import FileProvider
from app.nlp.providers.mock import MockProvider
from app.nlp.replay import NlpReplayEngine
from app.nlp.signals import EventType, NlpSignal, SentimentDirection
from app.utils.helpers import utc_now


def _market(question: str, condition_id: str = "cid-1") -> Market:
    return Market(
        condition_id=condition_id,
        question=question,
        slug="test",
        tokens=[MarketToken(token_id="tok-1", outcome="Yes")],
        active=True,
    )


# ===========================================================================
# TextNormalizer
# ===========================================================================


class TestTextNormalizer:
    def setup_method(self) -> None:
        self.norm = TextNormalizer()

    def test_strips_html(self) -> None:
        r = self.norm.normalize("<p>Hello <b>world</b></p>")
        assert "<" not in r.normalized
        assert "Hello" in r.normalized
        assert "strip_html" in r.steps_applied

    def test_replaces_urls(self) -> None:
        r = self.norm.normalize("Check https://example.com/news for details")
        assert "https://" not in r.normalized
        assert "[URL]" in r.normalized
        assert "replace_urls" in r.steps_applied

    def test_collapses_whitespace(self) -> None:
        r = self.norm.normalize("Too   many    spaces   here")
        assert "  " not in r.normalized

    def test_truncation(self) -> None:
        norm = TextNormalizer(max_length=20)
        r = norm.normalize("This is a very long text that should be truncated")
        assert len(r.normalized) <= 25  # max_length + word boundary + ellipsis
        assert r.normalized.endswith("…")

    def test_unicode_normalization(self) -> None:
        r = self.norm.normalize("Smart\u201cquotes\u201d and \u2018apostrophes\u2019")
        assert r.normalized  # should not crash

    def test_near_duplicate_detection(self) -> None:
        r1 = self.norm.normalize("Bitcoin surges past key resistance level")
        r2 = self.norm.normalize("Bitcoin surges past key resistance level")
        assert r1.is_duplicate is False
        assert r2.is_duplicate is True

    def test_near_duplicate_similar_text(self) -> None:
        norm = TextNormalizer(similarity_threshold=0.7)
        r1 = norm.normalize(
            "Bitcoin surges past key resistance level today as market rallies"
        )
        r2 = norm.normalize(
            "Bitcoin surges past key resistance level today amid market rally"
        )
        assert r2.is_duplicate is True

    def test_different_text_not_duplicate(self) -> None:
        norm = TextNormalizer()
        r1 = norm.normalize("Bitcoin crashes below support")
        r2 = norm.normalize("Federal Reserve raises interest rates by 50 basis points")
        assert r2.is_duplicate is False

    def test_content_hash_stable(self) -> None:
        norm1 = TextNormalizer()
        norm2 = TextNormalizer()
        r1 = norm1.normalize("Same text")
        r2 = norm2.normalize("Same text")
        assert r1.content_hash == r2.content_hash

    def test_reset_cache(self) -> None:
        self.norm.normalize("Text A")
        r1 = self.norm.normalize("Text A")
        assert r1.is_duplicate is True
        self.norm.reset_cache()
        r2 = self.norm.normalize("Text A")
        assert r2.is_duplicate is False

    def test_language_hint_english(self) -> None:
        r = self.norm.normalize("This is an English sentence")
        assert r.language_hint == "en"


# ===========================================================================
# EventTypeClassifier
# ===========================================================================


class TestEventTypeClassifier:
    def setup_method(self) -> None:
        self.clf = EventTypeClassifier()

    def test_detects_election(self) -> None:
        etype, conf = self.clf.detect("New election poll shows candidate leading")
        assert etype == EventType.ELECTION
        assert conf > 0

    def test_detects_crypto(self) -> None:
        etype, conf = self.clf.detect("Bitcoin surges past key level today")
        assert etype == EventType.CRYPTO

    def test_detects_legal(self) -> None:
        etype, conf = self.clf.detect("Judge issues ruling in federal lawsuit")
        assert etype == EventType.LEGAL_RULING

    def test_detects_sports(self) -> None:
        etype, conf = self.clf.detect("Team wins championship game in finals")
        assert etype == EventType.SPORTS

    def test_detects_economic(self) -> None:
        etype, conf = self.clf.detect("GDP growth beats expectations, jobs rise")
        assert etype == EventType.ECONOMIC

    def test_detects_geopolitical(self) -> None:
        etype, conf = self.clf.detect("NATO allies reach diplomatic agreement")
        assert etype == EventType.GEOPOLITICAL

    def test_other_for_irrelevant(self) -> None:
        etype, conf = self.clf.detect("The weather is nice today")
        assert etype == EventType.OTHER
        assert conf == 0.0

    def test_multiple_keywords_boost_confidence(self) -> None:
        _, conf1 = self.clf.detect("court ruling")
        _, conf2 = self.clf.detect("court ruling in a landmark lawsuit by a federal judge")
        assert conf2 > conf1


# ===========================================================================
# SentimentClassifier
# ===========================================================================


class TestSentimentClassifier:
    def setup_method(self) -> None:
        self.clf = SentimentClassifier()

    def test_bullish(self) -> None:
        d, s = self.clf.detect("Stock rally confirmed with gains across sectors")
        assert d == SentimentDirection.BULLISH
        assert s > 0

    def test_bearish(self) -> None:
        d, s = self.clf.detect("Markets crash after negative data, losses mount")
        assert d == SentimentDirection.BEARISH
        assert s < 0

    def test_neutral(self) -> None:
        d, s = self.clf.detect("Weather forecast calls for partly cloudy skies")
        assert d == SentimentDirection.NEUTRAL

    def test_negation_awareness(self) -> None:
        d, s = self.clf.detect("Bitcoin did not crash today despite earlier fears")
        # "not crash" should be treated as bullish-ish
        assert d in {SentimentDirection.BULLISH, SentimentDirection.NEUTRAL}

    def test_mixed_signals(self) -> None:
        d, s = self.clf.detect("Markets rise and fall in volatile session with gains and losses")
        # Mixed, should be roughly neutral
        assert -0.5 <= s <= 0.5


# ===========================================================================
# UrgencyClassifier
# ===========================================================================


class TestUrgencyClassifier:
    def setup_method(self) -> None:
        self.clf = UrgencyClassifier()

    def test_high_urgency(self) -> None:
        u = self.clf.detect("BREAKING: urgent alert, developing situation")
        assert u > 0.5

    def test_no_urgency(self) -> None:
        u = self.clf.detect("A historical overview of market trends")
        assert u == 0.0

    def test_caps_boost(self) -> None:
        u1 = self.clf.detect("something happened")
        u2 = self.clf.detect("ALERT: SOMETHING IMPORTANT HAPPENED")
        assert u2 > u1

    def test_exclamation_boost(self) -> None:
        u1 = self.clf.detect("breaking news")
        u2 = self.clf.detect("breaking news!!!")
        assert u2 >= u1


# ===========================================================================
# RelevanceClassifier
# ===========================================================================


class TestRelevanceClassifier:
    def setup_method(self) -> None:
        self.clf = RelevanceClassifier()

    def test_relevant_text(self) -> None:
        s = self.clf.score(
            "Bitcoin price surges past resistance",
            "Will Bitcoin reach $100k by 2025?",
        )
        assert s > 0.1

    def test_irrelevant_text(self) -> None:
        s = self.clf.score(
            "New semiconductor chip announced",
            "Will the Lakers win the NBA championship?",
        )
        assert s < 0.1

    def test_empty_question(self) -> None:
        assert self.clf.score("some text", "") == 0.0

    def test_empty_text(self) -> None:
        assert self.clf.score("", "Some question?") == 0.0


# ===========================================================================
# EntityExtractor
# ===========================================================================


class TestEntityExtractor:
    def setup_method(self) -> None:
        self.ext = EntityExtractor()

    def test_extracts_names(self) -> None:
        entities = self.ext.extract("Joe Biden met with Elon Musk in Washington")
        names = [e for e in entities if "Biden" in e or "Musk" in e]
        assert len(names) >= 1

    def test_extracts_acronyms(self) -> None:
        entities = self.ext.extract("SEC and FBI investigate BTC exchange")
        acrs = [e for e in entities if e in {"SEC", "FBI", "BTC"}]
        assert len(acrs) >= 1

    def test_no_common_words(self) -> None:
        entities = self.ext.extract("The quick brown fox jumps over")
        # "The" should be filtered out
        assert "The" not in entities


# ===========================================================================
# KeywordClassifier (composite)
# ===========================================================================


class TestKeywordClassifier:
    def setup_method(self) -> None:
        self.clf = KeywordClassifier()

    def test_produces_complete_result(self) -> None:
        r = self.clf.classify("Bitcoin surges past resistance in crypto rally")
        assert r.event_type == EventType.CRYPTO
        assert r.sentiment == SentimentDirection.BULLISH
        assert 0.0 <= r.confidence <= 1.0
        assert r.rationale

    def test_with_market_context(self) -> None:
        ctx = {"question": "Will Bitcoin reach $100k?"}
        r = self.clf.classify("Bitcoin price surges past resistance", ctx)
        assert r.relevance > 0.0

    def test_without_context(self) -> None:
        r = self.clf.classify("Some random text")
        assert r.relevance == pytest.approx(0.3)

    def test_entities_extracted(self) -> None:
        r = self.clf.classify("Joe Biden met with Elon Musk at the White House")
        assert len(r.entities) > 0


# ===========================================================================
# LlmClassifierAdapter
# ===========================================================================


class TestLlmClassifierAdapter:
    def test_no_api_key_returns_default(self) -> None:
        clf = LlmClassifierAdapter(api_key=None)
        r = clf.classify("test headline")
        assert r.confidence == 0.0
        assert "not configured" in r.rationale

    def test_with_api_key_calls_stub(self) -> None:
        clf = LlmClassifierAdapter(api_key="fake-key")
        r = clf.classify("test headline")
        assert "stub" in r.rationale.lower() or r.confidence == 0.0

    def test_parse_response(self) -> None:
        raw = json.dumps({
            "event_type": "election",
            "sentiment": "bullish",
            "sentiment_score": 0.7,
            "urgency": 0.5,
            "relevance": 0.8,
            "confidence": 0.9,
            "rationale": "test",
            "entities": ["Test"],
        })
        result = LlmClassifierAdapter._parse_response(raw)
        assert result.event_type == EventType.ELECTION
        assert result.sentiment == SentimentDirection.BULLISH
        assert result.confidence == 0.9


# ===========================================================================
# HybridClassifier
# ===========================================================================


class TestHybridClassifier:
    def test_falls_back_to_keyword_when_no_llm(self) -> None:
        hybrid = HybridClassifier(llm=None)
        r = hybrid.classify("Bitcoin surges past resistance")
        assert r.event_type == EventType.CRYPTO

    def test_falls_back_when_llm_low_confidence(self) -> None:
        hybrid = HybridClassifier(
            llm=LlmClassifierAdapter(api_key=None),
            llm_confidence_threshold=0.5,
        )
        r = hybrid.classify("Bitcoin surges")
        assert r.confidence > 0


# ===========================================================================
# MarketMapper
# ===========================================================================


class TestMarketMapper:
    def setup_method(self) -> None:
        self.mapper = MarketMapper(min_relevance=0.1)

    def test_matches_relevant_market(self) -> None:
        markets = [_market("Will Bitcoin reach $100k by 2025?")]
        matches = self.mapper.find_matches(
            "Bitcoin price surges past key resistance",
            entities=["Bitcoin"],
            markets=markets,
        )
        assert len(matches) >= 1
        assert matches[0].relevance_score > 0

    def test_no_match_for_irrelevant(self) -> None:
        markets = [_market("Will the Lakers win the NBA championship?")]
        matches = self.mapper.find_matches(
            "New semiconductor chip breakthrough announced",
            entities=["Intel"],
            markets=markets,
        )
        assert len(matches) == 0

    def test_ranked_by_relevance(self) -> None:
        markets = [
            _market("Will Bitcoin reach $100k?", "cid-btc"),
            _market("Will Ethereum merge succeed?", "cid-eth"),
        ]
        matches = self.mapper.find_matches(
            "Bitcoin and Ethereum both surging today",
            entities=["Bitcoin", "Ethereum"],
            markets=markets,
        )
        if len(matches) >= 2:
            assert matches[0].relevance_score >= matches[1].relevance_score

    def test_inactive_excluded(self) -> None:
        m = _market("Will X happen?")
        m.active = False
        assert self.mapper.find_matches("X happened", [], [m]) == []

    def test_manual_override(self) -> None:
        mapper = MarketMapper(
            min_relevance=0.05,
            manual_overrides={"cid-special": ["magic_keyword"]},
        )
        markets = [_market("Completely unrelated question", "cid-special")]
        matches = mapper.find_matches(
            "The magic_keyword appears in this text",
            entities=[],
            markets=markets,
        )
        assert len(matches) >= 1

    def test_ambiguity_detection(self) -> None:
        markets = [
            _market("Will Bitcoin surge?", "cid-1"),
            _market("Will Bitcoin rise?", "cid-2"),
        ]
        matches = self.mapper.find_matches(
            "Bitcoin price surges and rises",
            entities=["Bitcoin"],
            markets=markets,
        )
        if len(matches) >= 2:
            # Close scores should be flagged as ambiguous
            assert any(m.ambiguous for m in matches)

    def test_entity_score_populated(self) -> None:
        markets = [_market("Will Biden win the election?")]
        matches = self.mapper.find_matches(
            "Biden leads in polls",
            entities=["Biden"],
            markets=markets,
        )
        if matches:
            assert matches[0].entity_score > 0


# ===========================================================================
# NlpPipeline
# ===========================================================================


class TestNlpPipeline:
    def test_end_to_end(self) -> None:
        pipeline = NlpPipeline()
        item = NewsItem(
            item_id="n1", source="test",
            text="Bitcoin surges past key resistance level in crypto rally",
        )
        markets = [_market("Will Bitcoin reach $100k by 2025?")]
        signals = pipeline.process_item(item, markets)
        assert len(signals) >= 1
        assert signals[0].source_text_id == "n1"
        assert signals[0].event_type == EventType.CRYPTO

    def test_irrelevant_produces_nothing(self) -> None:
        pipeline = NlpPipeline()
        item = NewsItem(item_id="n2", source="test", text="The weather is nice today")
        signals = pipeline.process_item(item, [_market("Will the Lakers win?")])
        assert len(signals) == 0

    def test_duplicate_text_dropped(self) -> None:
        pipeline = NlpPipeline()
        item1 = NewsItem(item_id="n3", source="test", text="Bitcoin crashes hard today")
        item2 = NewsItem(item_id="n4", source="test", text="Bitcoin crashes hard today")
        markets = [_market("Will Bitcoin reach $100k?")]
        s1 = pipeline.process_item(item1, markets)
        s2 = pipeline.process_item(item2, markets)
        # Second identical text should be detected as near-duplicate
        assert len(s2) == 0

    def test_process_batch(self) -> None:
        pipeline = NlpPipeline()
        items = [
            NewsItem(item_id="b1", source="test",
                     text="Election poll shows candidate winning the vote"),
            NewsItem(item_id="b2", source="test",
                     text="Random content about gardening tips"),
        ]
        markets = [_market("Will candidate win the election?")]
        signals = pipeline.process_batch(items, markets)
        assert isinstance(signals, list)

    def test_html_text_cleaned(self) -> None:
        pipeline = NlpPipeline()
        item = NewsItem(
            item_id="html1", source="test",
            text="<p>Bitcoin <b>surges</b> past resistance</p>",
        )
        markets = [_market("Will Bitcoin reach $100k?")]
        signals = pipeline.process_item(item, markets)
        for sig in signals:
            assert "<" not in sig.text_snippet

    def test_signal_metadata_populated(self) -> None:
        pipeline = NlpPipeline()
        item = NewsItem(
            item_id="m1", source="test",
            text="Bitcoin surges past key resistance level in major rally",
        )
        markets = [_market("Will Bitcoin reach $100k?")]
        signals = pipeline.process_item(item, markets)
        if signals:
            assert "token_overlap_score" in signals[0].metadata
            assert "normalization_steps" in signals[0].metadata


# ===========================================================================
# NlpSignal → NormalizedSignal conversion
# ===========================================================================


class TestNlpSignalToLayered:
    def test_bullish_to_buy(self) -> None:
        sig = NlpSignal(
            source_text_id="t1", source_provider="test",
            market_ids=["mkt-1"],
            sentiment=SentimentDirection.BULLISH,
            sentiment_score=0.8, confidence=0.7,
        )
        ns = nlp_signal_to_layered(sig, "tok-1")
        assert ns.action == SignalAction.BUY_YES
        assert ns.direction == 1
        assert ns.layer.value == "nlp"
        assert ns.normalized_confidence > 0

    def test_bearish_to_sell(self) -> None:
        sig = NlpSignal(
            source_text_id="t2", source_provider="test",
            market_ids=["mkt-1"],
            sentiment=SentimentDirection.BEARISH,
            sentiment_score=-0.6, confidence=0.5,
        )
        ns = nlp_signal_to_layered(sig, "tok-1")
        assert ns.action == SignalAction.SELL_YES
        assert ns.direction == -1

    def test_neutral_to_hold(self) -> None:
        sig = NlpSignal(
            source_text_id="t3", source_provider="test",
            market_ids=["mkt-1"],
            sentiment=SentimentDirection.NEUTRAL,
            confidence=0.3,
        )
        ns = nlp_signal_to_layered(sig, "tok-1")
        assert ns.action == SignalAction.HOLD
        assert ns.direction == 0

    def test_metadata_includes_text_snippet(self) -> None:
        sig = NlpSignal(
            source_text_id="t4", source_provider="test",
            market_ids=["mkt-1"],
            sentiment=SentimentDirection.BULLISH,
            confidence=0.5,
            text_snippet="Bitcoin surges",
        )
        ns = nlp_signal_to_layered(sig, "tok-1")
        assert "text_snippet" in ns.metadata


# ===========================================================================
# MockProvider
# ===========================================================================


class TestMockProvider:
    @pytest.mark.asyncio
    async def test_returns_items(self) -> None:
        items = await MockProvider().fetch_items()
        assert len(items) == 1
        assert isinstance(items[0], NewsItem)
        assert items[0].source == "mock"

    def test_is_available(self) -> None:
        assert MockProvider().is_available() is True

    @pytest.mark.asyncio
    async def test_rotates_headlines(self) -> None:
        provider = MockProvider()
        texts = set()
        for _ in range(10):
            items = await provider.fetch_items()
            texts.add(items[0].text)
        assert len(texts) > 1


# ===========================================================================
# FileProvider
# ===========================================================================


class TestFileProvider:
    @pytest.mark.asyncio
    async def test_reads_json(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            data = [{"text": "Headline one"}, {"text": "Headline two"}]
            (Path(d) / "batch.json").write_text(json.dumps(data))
            items = await FileProvider(directory=d).fetch_items()
            assert len(items) == 2

    @pytest.mark.asyncio
    async def test_skips_seen_files(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.json").write_text(json.dumps([{"text": "h"}]))
            provider = FileProvider(directory=d)
            first = await provider.fetch_items()
            second = await provider.fetch_items()
            assert len(first) == 1
            assert len(second) == 0

    @pytest.mark.asyncio
    async def test_missing_dir(self) -> None:
        assert await FileProvider(directory="/nonexistent").fetch_items() == []

    def test_is_available(self) -> None:
        assert FileProvider(directory="/nonexistent").is_available() is False


# ===========================================================================
# NewsIngestionService
# ===========================================================================


class TestNewsIngestionService:
    @pytest.mark.asyncio
    async def test_poll_once(self) -> None:
        svc = NewsIngestionService(providers=[MockProvider()], pipeline=NlpPipeline())
        svc.set_market_provider(lambda: [_market("Will Bitcoin reach $100k?")])
        signals = await svc.poll_once()
        assert isinstance(signals, list)

    @pytest.mark.asyncio
    async def test_deduplication(self) -> None:
        svc = NewsIngestionService(providers=[MockProvider()], pipeline=NlpPipeline())
        svc.set_market_provider(lambda: [])
        item = NewsItem(item_id="dup", source="test", text="same text")
        d1 = svc._deduplicate([item])
        d2 = svc._deduplicate([item])
        assert len(d1) == 1
        assert len(d2) == 0

    @pytest.mark.asyncio
    async def test_buffers_signals_without_draining(self) -> None:
        # Signals are intentionally NOT drained on read: they are buffered so
        # every market gets a chance to match across multiple intelligence-loop
        # iterations, and are pruned by age inside ``_poll_cycle`` instead.
        svc = NewsIngestionService(providers=[MockProvider()], pipeline=NlpPipeline())
        svc.set_market_provider(lambda: [_market("Will crypto regulation pass?")])
        await svc.poll_once()
        first = svc.get_latest_signals()
        second = svc.get_latest_signals()
        assert len(second) == len(first)

    @pytest.mark.asyncio
    async def test_unavailable_provider(self) -> None:
        class Unavail(MockProvider):
            def is_available(self) -> bool:
                return False
        svc = NewsIngestionService(providers=[Unavail()], pipeline=NlpPipeline())
        svc.set_market_provider(lambda: [])
        assert await svc.poll_once() == []


# ===========================================================================
# NlpReplayEngine
# ===========================================================================


class TestNlpReplayEngine:
    def test_replay_items(self) -> None:
        engine = NlpReplayEngine(
            active_markets=[_market("Will Bitcoin reach $100k?")],
        )
        items = [
            NewsItem(item_id="r1", source="test", text="Bitcoin surges past resistance"),
            NewsItem(item_id="r2", source="test", text="The weather is nice"),
        ]
        result = engine.replay_items(items)
        assert result.total_items == 2
        assert result.items_with_signals >= 0
        assert len(result.per_item) == 2

    def test_replay_from_json(self) -> None:
        engine = NlpReplayEngine(
            active_markets=[_market("Will Bitcoin reach $100k?")],
        )
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump([
                {"id": "j1", "text": "Bitcoin surges past key resistance", "source": "file"},
            ], f)
            f.flush()
            result = engine.replay_from_json(f.name)
        assert result.total_items == 1

    def test_replay_from_json_with_items_key(self) -> None:
        engine = NlpReplayEngine(
            active_markets=[_market("Will Bitcoin reach $100k?")],
        )
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump({"items": [
                {"id": "j2", "text": "BTC rally continues", "source": "file"},
            ]}, f)
            f.flush()
            result = engine.replay_from_json(f.name)
        assert result.total_items == 1

    def test_replay_from_db_rows(self) -> None:
        engine = NlpReplayEngine(
            active_markets=[_market("Will Bitcoin reach $100k?")],
        )
        rows = [
            {"item_id": "db1", "source": "mock", "text": "Bitcoin price rises", "url": ""},
        ]
        result = engine.replay_from_db_rows(rows)
        assert result.total_items == 1

    def test_save_result(self) -> None:
        engine = NlpReplayEngine()
        result = engine.replay_items([])
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "result.json"
            engine.save_result(result, path)
            assert path.exists()
            data = json.loads(path.read_text())
            assert "total_items" in data

    def test_replay_summary_stats(self) -> None:
        engine = NlpReplayEngine(
            active_markets=[_market("Will Bitcoin reach $100k?")],
        )
        items = [
            NewsItem(item_id="s1", source="test",
                     text="Bitcoin surges past key resistance in crypto rally"),
            NewsItem(item_id="s2", source="test",
                     text="Bitcoin crashes after exchange hack"),
        ]
        result = engine.replay_items(items)
        summary = result.summary()
        assert "signal_rate" in summary
        assert "signals_by_sentiment" in summary
        assert "signals_by_event_type" in summary


# ===========================================================================
# NLP Storage (Repository integration)
# ===========================================================================


class TestNlpStorage:
    @pytest.mark.asyncio
    async def test_save_and_get_nlp_event(self) -> None:
        from app.storage.repository import Repository
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            repo = Repository(f.name)
            await repo.initialize()

            await repo.save_nlp_event(
                item_id="ev-1",
                source="test",
                text="Bitcoin surges past resistance",
                content_hash="abc123",
                event_type="crypto",
                sentiment="bullish",
                sentiment_score=0.8,
                confidence=0.7,
                entities=["Bitcoin"],
            )
            rows = await repo.get_nlp_events()
            assert len(rows) == 1
            assert rows[0]["item_id"] == "ev-1"
            assert rows[0]["event_type"] == "crypto"
            await repo.close()

    @pytest.mark.asyncio
    async def test_save_and_get_nlp_signal(self) -> None:
        from app.storage.repository import Repository
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            repo = Repository(f.name)
            await repo.initialize()

            await repo.save_nlp_signal(
                source_text_id="ev-1",
                source_provider="test",
                market_id="mkt-1",
                sentiment="bullish",
                sentiment_score=0.8,
                event_type="crypto",
                confidence=0.7,
                text_snippet="Bitcoin surges",
            )
            rows = await repo.get_nlp_signals(market_id="mkt-1")
            assert len(rows) == 1
            assert rows[0]["sentiment"] == "bullish"
            await repo.close()

    @pytest.mark.asyncio
    async def test_filter_nlp_events_by_type(self) -> None:
        from app.storage.repository import Repository
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            repo = Repository(f.name)
            await repo.initialize()

            await repo.save_nlp_event(
                item_id="a", source="t", text="t", content_hash="h1",
                event_type="crypto",
            )
            await repo.save_nlp_event(
                item_id="b", source="t", text="t2", content_hash="h2",
                event_type="election",
            )
            crypto = await repo.get_nlp_events(event_type="crypto")
            assert len(crypto) == 1
            assert crypto[0]["item_id"] == "a"
            await repo.close()


# ===========================================================================
# Examples.json replay
# ===========================================================================


class TestExamplesReplay:
    def test_examples_file_is_valid_json(self) -> None:
        p = Path("data/news/examples.json")
        if not p.exists():
            pytest.skip("examples.json not found")
        data = json.loads(p.read_text())
        assert "items" in data
        assert len(data["items"]) >= 10

    def test_replay_examples(self) -> None:
        p = Path("data/news/examples.json")
        if not p.exists():
            pytest.skip("examples.json not found")
        engine = NlpReplayEngine(
            active_markets=[
                _market("Will Bitcoin reach $100k by 2025?", "cid-btc"),
                _market("Will the candidate win the presidential election?", "cid-election"),
                _market("Will crypto regulation pass this year?", "cid-regulation"),
                _market("Will the team win the NBA championship?", "cid-nba"),
                _market("Will GDP growth exceed 3% this quarter?", "cid-gdp"),
            ],
        )
        result = engine.replay_from_json(p)
        assert result.total_items >= 10
        assert result.items_with_signals >= 1
        assert result.total_signals >= 1


# ===========================================================================
# LLM Output Validator
# ===========================================================================


class TestLlmOutputValidator:
    """Tests for LlmOutputValidator: the core defense against malformed LLM output."""

    def test_valid_json(self) -> None:
        raw = json.dumps({
            "event_type": "crypto",
            "sentiment": "bullish",
            "sentiment_score": 0.8,
            "urgency": 0.5,
            "relevance": 0.7,
            "confidence": 0.9,
            "rationale": "BTC surge",
            "entities": ["Bitcoin"],
        })
        result, errors = LlmOutputValidator.validate(raw)
        assert len(errors) == 0
        assert result.event_type == EventType.CRYPTO
        assert result.sentiment == SentimentDirection.BULLISH
        assert result.sentiment_score == 0.8
        assert result.urgency == 0.5
        assert result.relevance == 0.7
        assert result.confidence == 0.9
        assert "BTC surge" in result.rationale
        assert result.entities == ["Bitcoin"]

    def test_strips_markdown_json_fences(self) -> None:
        raw = '```json\n{"event_type":"election","sentiment":"neutral","sentiment_score":0,"urgency":0,"relevance":0.5,"confidence":0.4,"rationale":"test","entities":[]}\n```'
        result, errors = LlmOutputValidator.validate(raw)
        assert result.event_type == EventType.ELECTION
        assert len([e for e in errors if e.field == "_json"]) == 0

    def test_strips_plain_markdown_fences(self) -> None:
        raw = '```\n{"event_type":"sports","sentiment":"bullish","sentiment_score":0.5,"urgency":0.3,"relevance":0.6,"confidence":0.7,"rationale":"win","entities":["Team"]}\n```'
        result, errors = LlmOutputValidator.validate(raw)
        assert result.event_type == EventType.SPORTS

    def test_extracts_json_from_surrounding_text(self) -> None:
        raw = 'Here is my analysis:\n{"event_type":"crypto","sentiment":"bearish","sentiment_score":-0.6,"urgency":0.2,"relevance":0.5,"confidence":0.6,"rationale":"drop","entities":[]}\nThat is my analysis.'
        result, errors = LlmOutputValidator.validate(raw)
        assert result.event_type == EventType.CRYPTO
        assert result.sentiment == SentimentDirection.BEARISH

    def test_invalid_json_returns_errors(self) -> None:
        result, errors = LlmOutputValidator.validate("this is not json at all")
        assert len(errors) > 0
        assert any(e.field == "_json" for e in errors)
        assert result.confidence == 0.0

    def test_empty_string_returns_errors(self) -> None:
        result, errors = LlmOutputValidator.validate("")
        assert len(errors) > 0
        assert result.confidence == 0.0

    def test_json_array_returns_errors(self) -> None:
        result, errors = LlmOutputValidator.validate('[1, 2, 3]')
        assert any(e.field == "_json" or e.field == "_root" for e in errors)

    def test_missing_fields_reported(self) -> None:
        raw = json.dumps({"event_type": "crypto"})
        result, errors = LlmOutputValidator.validate(raw)
        missing_fields = {e.field for e in errors if e.issue == "missing"}
        assert "sentiment" in missing_fields
        assert "sentiment_score" in missing_fields
        assert "urgency" in missing_fields
        assert "relevance" in missing_fields
        assert "confidence" in missing_fields

    def test_wrong_types_reported(self) -> None:
        raw = json.dumps({
            "event_type": "crypto",
            "sentiment": "bullish",
            "sentiment_score": "not_a_number",
            "urgency": "high",
            "relevance": True,
            "confidence": [1],
            "rationale": 42,
            "entities": "not_a_list",
        })
        result, errors = LlmOutputValidator.validate(raw)
        error_fields = {e.field for e in errors}
        assert "sentiment_score" in error_fields
        assert "urgency" in error_fields
        assert "entities" in error_fields

    def test_unknown_event_type_defaults_to_other(self) -> None:
        raw = json.dumps({
            "event_type": "alien_invasion",
            "sentiment": "neutral",
            "sentiment_score": 0,
            "urgency": 0,
            "relevance": 0,
            "confidence": 0.5,
            "rationale": "test",
            "entities": [],
        })
        result, errors = LlmOutputValidator.validate(raw)
        assert result.event_type == EventType.OTHER
        # event_type is a soft category: unknown labels fall back to 'other'
        # silently (no validation error) to avoid log noise from small models.
        assert not any(e.field == "event_type" for e in errors)

    def test_event_type_synonyms_map_to_known_categories(self) -> None:
        cases = {
            "political": EventType.ELECTION,
            "employment": EventType.ECONOMIC,
            "lawsuit": EventType.LEGAL_RULING,
            "bitcoin": EventType.CRYPTO,
        }
        for raw_event, expected in cases.items():
            raw = json.dumps({
                "event_type": raw_event,
                "sentiment": "neutral",
                "sentiment_score": 0,
                "urgency": 0,
                "relevance": 0,
                "confidence": 0.5,
                "rationale": "test",
                "entities": [],
            })
            result, errors = LlmOutputValidator.validate(raw)
            assert result.event_type == expected, raw_event
            assert not any(e.field == "event_type" for e in errors), raw_event

    def test_unknown_sentiment_defaults_to_neutral(self) -> None:
        raw = json.dumps({
            "event_type": "crypto",
            "sentiment": "very_positive",
            "sentiment_score": 0.5,
            "urgency": 0,
            "relevance": 0,
            "confidence": 0.5,
            "rationale": "test",
            "entities": [],
        })
        result, errors = LlmOutputValidator.validate(raw)
        assert result.sentiment == SentimentDirection.NEUTRAL
        assert any(e.field == "sentiment" for e in errors)

    def test_out_of_range_clamped(self) -> None:
        raw = json.dumps({
            "event_type": "crypto",
            "sentiment": "bullish",
            "sentiment_score": 5.0,
            "urgency": -2.0,
            "relevance": 100.0,
            "confidence": 1.5,
            "rationale": "test",
            "entities": [],
        })
        result, errors = LlmOutputValidator.validate(raw)
        assert result.sentiment_score == 1.0
        assert result.urgency == 0.0
        assert result.relevance == 1.0
        assert result.confidence == 1.0
        assert len([e for e in errors if "out of range" in e.issue]) >= 3

    def test_non_string_entities_filtered(self) -> None:
        raw = json.dumps({
            "event_type": "crypto",
            "sentiment": "neutral",
            "sentiment_score": 0,
            "urgency": 0,
            "relevance": 0.5,
            "confidence": 0.5,
            "rationale": "test",
            "entities": ["Bitcoin", 42, None, "Ethereum", True],
        })
        result, errors = LlmOutputValidator.validate(raw)
        assert result.entities == ["Bitcoin", "Ethereum"]
        assert any(e.field == "entities" for e in errors)

    def test_validation_error_repr(self) -> None:
        err = LlmValidationError("sentiment", "unknown value")
        assert "sentiment" in repr(err)
        assert "sentiment" in str(err)


# ===========================================================================
# LLM Classifier Adapter
# ===========================================================================


class TestLlmClassifierAdapter:
    def test_no_api_key_returns_default(self) -> None:
        adapter = LlmClassifierAdapter()
        result = adapter.classify("some text")
        assert result.confidence == 0.0
        assert "not configured" in result.rationale

    def test_with_api_key_calls_stub(self) -> None:
        adapter = LlmClassifierAdapter(api_key="test")
        result = adapter.classify("Bitcoin crashes")
        assert "llm" in result.rationale.lower()

    def test_parse_response_backward_compat(self) -> None:
        raw = json.dumps({
            "event_type": "election",
            "sentiment": "bullish",
            "sentiment_score": 0.6,
            "urgency": 0.4,
            "relevance": 0.7,
            "confidence": 0.8,
            "rationale": "poll surge",
            "entities": ["Biden"],
        })
        result = LlmClassifierAdapter._parse_response(raw)
        assert result.event_type == EventType.ELECTION
        assert result.sentiment == SentimentDirection.BULLISH

    def test_rejects_output_with_too_many_errors(self) -> None:
        class BadLLM(LlmClassifierAdapter):
            def _call_llm_api(self, prompt: str) -> str:
                return "totally broken not json garbage output"

        adapter = BadLLM(api_key="test", max_validation_errors=3)
        result = adapter.classify("test")
        assert "rejected" in result.rationale or "not valid JSON" in result.rationale

    def test_accepts_output_with_few_errors(self) -> None:
        class SlightlyBadLLM(LlmClassifierAdapter):
            def _call_llm_api(self, prompt: str) -> str:
                return json.dumps({
                    "event_type": "crypto",
                    "sentiment": "bullish",
                    "sentiment_score": 0.5,
                    "urgency": 0.3,
                    "relevance": 0.6,
                    "confidence": 0.7,
                    "rationale": "test",
                    # missing entities field — 1 error
                })

        adapter = SlightlyBadLLM(api_key="test", max_validation_errors=3)
        result = adapter.classify("test")
        assert result.confidence == 0.7
        assert result.event_type == EventType.CRYPTO

    def test_api_exception_returns_fallback(self) -> None:
        class CrashingLLM(LlmClassifierAdapter):
            def _call_llm_api(self, prompt: str) -> str:
                raise ConnectionError("server down")

        adapter = CrashingLLM(api_key="test")
        result = adapter.classify("test")
        assert "failed" in result.rationale.lower()
        assert result.confidence == 0.0

    def test_markdown_wrapped_json_accepted(self) -> None:
        class MarkdownLLM(LlmClassifierAdapter):
            def _call_llm_api(self, prompt: str) -> str:
                return '```json\n{"event_type":"election","sentiment":"bearish","sentiment_score":-0.4,"urgency":0.1,"relevance":0.5,"confidence":0.6,"rationale":"polls down","entities":["Trump"]}\n```'

        adapter = MarkdownLLM(api_key="test")
        result = adapter.classify("test")
        assert result.event_type == EventType.ELECTION
        assert result.sentiment == SentimentDirection.BEARISH
        assert result.entities == ["Trump"]


# ===========================================================================
# MockLLMProvider
# ===========================================================================


class TestMockLLMProvider:
    def test_bullish_classification(self) -> None:
        provider = MockLLMProvider()
        result = provider.classify("Bitcoin surges past $100k")
        assert result.sentiment == SentimentDirection.BULLISH
        assert result.sentiment_score > 0
        assert result.confidence > 0

    def test_bearish_classification(self) -> None:
        provider = MockLLMProvider()
        result = provider.classify("Market crashes after regulatory crackdown")
        assert result.sentiment == SentimentDirection.BEARISH
        assert result.sentiment_score < 0

    def test_neutral_classification(self) -> None:
        provider = MockLLMProvider()
        result = provider.classify("Weather update: sunny skies expected")
        assert result.sentiment == SentimentDirection.NEUTRAL
        assert result.sentiment_score == 0.0

    def test_event_type_election(self) -> None:
        provider = MockLLMProvider()
        result = provider.classify("Election polls show tight race in battleground state")
        assert result.event_type == EventType.ELECTION

    def test_event_type_crypto(self) -> None:
        provider = MockLLMProvider()
        result = provider.classify("Bitcoin ETF approval drives rally")
        assert result.event_type == EventType.CRYPTO

    def test_event_type_regulatory(self) -> None:
        provider = MockLLMProvider()
        result = provider.classify("SEC announces new regulation framework")
        assert result.event_type == EventType.REGULATORY

    def test_extracts_entities(self) -> None:
        provider = MockLLMProvider()
        result = provider.classify("Donald Trump wins key Iowa caucus")
        assert any("Trump" in e or "Donald" in e for e in result.entities)

    def test_market_context_affects_relevance(self) -> None:
        provider = MockLLMProvider()
        r1 = provider.classify(
            "Bitcoin surges past $100k",
            market_context={"question": "Will Bitcoin reach $100k?"},
        )
        r2 = provider.classify(
            "Bitcoin surges past $100k",
            market_context={"question": "Will the team win the championship?"},
        )
        assert r1.relevance >= r2.relevance

    def test_urgency_breaking_news(self) -> None:
        provider = MockLLMProvider()
        result = provider.classify("BREAKING: Federal Reserve raises interest rate")
        assert result.urgency > 0.5


# ===========================================================================
# Model Family Detection
# ===========================================================================


class TestModelFamily:
    def test_detects_llama(self) -> None:
        assert ModelFamily.detect("meta-llama/Llama-3.1-8B-Instruct") == ModelFamily.LLAMA
        assert ModelFamily.detect("llama-3.2-1b") == ModelFamily.LLAMA

    def test_detects_mistral(self) -> None:
        assert ModelFamily.detect("mistralai/Mistral-7B-Instruct-v0.3") == ModelFamily.MISTRAL
        assert ModelFamily.detect("mixtral-8x7b") == ModelFamily.MISTRAL

    def test_detects_qwen(self) -> None:
        assert ModelFamily.detect("Qwen/Qwen2.5-7B-Instruct") == ModelFamily.QWEN

    def test_generic_fallback(self) -> None:
        assert ModelFamily.detect("gpt-4o-mini") == ModelFamily.GENERIC
        assert ModelFamily.detect("claude-3-sonnet") == ModelFamily.GENERIC

    def test_prompt_per_family(self) -> None:
        for family in [ModelFamily.GENERIC, ModelFamily.LLAMA, ModelFamily.MISTRAL, ModelFamily.QWEN]:
            prompt = ModelFamily.get_system_prompt(family)
            assert len(prompt) > 50
            assert "event_type" in prompt
            assert "sentiment" in prompt


# ===========================================================================
# Build LLM Classifier Factory
# ===========================================================================


class TestBuildLlmClassifier:
    def test_none_returns_none(self) -> None:
        result = build_llm_classifier(provider="none")
        assert result is None

    def test_mock_returns_mock(self) -> None:
        result = build_llm_classifier(provider="mock")
        assert isinstance(result, MockLLMProvider)

    def test_local_requires_base_url(self) -> None:
        with pytest.raises(ValueError, match="LLM_BASE_URL"):
            build_llm_classifier(
                provider="local_open_source",
                model_name="test-model",
                base_url="",
            )

    def test_local_requires_model_name(self) -> None:
        with pytest.raises(ValueError, match="LLM_MODEL_NAME"):
            build_llm_classifier(
                provider="local_open_source",
                model_name="",
                base_url="http://localhost:8000/v1",
            )

    def test_local_returns_local_provider(self) -> None:
        result = build_llm_classifier(
            provider="local_open_source",
            model_name="meta-llama/Llama-3.1-8B-Instruct",
            base_url="http://localhost:8000/v1",
        )
        assert isinstance(result, LocalOpenSourceProvider)

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
            build_llm_classifier(provider="invalid_provider")

    def test_hybrid_with_mock_llm(self) -> None:
        llm = build_llm_classifier(provider="mock")
        hybrid = HybridClassifier(keyword=KeywordClassifier(), llm=llm)
        result = hybrid.classify("Bitcoin surges past $100k resistance level")
        assert result.confidence > 0
        assert result.sentiment in (
            SentimentDirection.BULLISH,
            SentimentDirection.BEARISH,
            SentimentDirection.NEUTRAL,
        )

    def test_hybrid_without_llm_uses_keyword(self) -> None:
        hybrid = HybridClassifier(keyword=KeywordClassifier(), llm=None)
        result = hybrid.classify("Election results shock analysts")
        assert "keyword" in result.rationale

    def test_hybrid_prefers_llm_when_confident(self) -> None:
        llm = MockLLMProvider()
        hybrid = HybridClassifier(
            keyword=KeywordClassifier(),
            llm=llm,
            llm_confidence_threshold=0.3,
        )
        result = hybrid.classify("Breaking: Bitcoin surges past $100k")
        assert "llm" in result.rationale.lower()

    def test_hybrid_falls_back_to_keyword_on_low_confidence(self) -> None:
        llm = MockLLMProvider()
        hybrid = HybridClassifier(
            keyword=KeywordClassifier(),
            llm=llm,
            llm_confidence_threshold=0.99,
        )
        result = hybrid.classify("Weather is nice today")
        assert "keyword" in result.rationale
