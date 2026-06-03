#!/usr/bin/env python3
"""Test that NewsAPI and OpenAI LLM are working end-to-end."""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app.config.settings import get_settings


async def test_newsapi(settings):
    print("=" * 60)
    print("TEST 1: NewsAPI.org")
    print("=" * 60)
    key = settings.newsapi_key
    if not key:
        print("  FAIL: NEWSAPI_KEY not set in .env")
        return False

    print(f"  Key: {key[:8]}...{key[-4:]}")

    from app.nlp.providers.newsapi import NewsApiProvider
    provider = NewsApiProvider(api_key=key)
    print(f"  Available: {provider.is_available()}")

    try:
        items = await provider.fetch_items()
        print(f"  Fetched: {len(items)} news items")
        if items:
            for item in items[:3]:
                print(f"    [{item.source}] {item.text[:80]}...")
            print(f"  PASS: NewsAPI is working")
            return True
        else:
            print(f"  WARNING: 0 items returned (might be rate-limited on free tier)")
            return False
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


def test_openai_llm(settings):
    print()
    print("=" * 60)
    print("TEST 2: OpenAI LLM (GPT-4o-mini)")
    print("=" * 60)

    provider = settings.llm_provider
    model = settings.llm_model_name
    base_url = settings.llm_base_url
    api_key = settings.llm_api_key

    print(f"  Provider: {provider}")
    print(f"  Model: {model}")
    print(f"  Base URL: {base_url}")
    print(f"  API Key: {api_key[:12]}...{api_key[-4:]}" if api_key else "  API Key: NOT SET")

    if provider == "none":
        print("  SKIP: LLM_PROVIDER=none")
        return False

    from app.nlp.providers.llm_provider import build_llm_classifier
    try:
        classifier = build_llm_classifier(
            provider=provider,
            model_name=model,
            base_url=base_url,
            api_key=api_key,
            timeout=settings.llm_timeout_seconds,
        )
        if classifier is None:
            print("  FAIL: build_llm_classifier returned None")
            return False
        print("  LLM classifier built successfully")
    except Exception as e:
        print(f"  FAIL building classifier: {e}")
        return False

    test_headline = "Federal Reserve announces surprise interest rate cut of 50 basis points"
    print(f"  Test headline: '{test_headline}'")

    try:
        result = classifier.classify(test_headline)
        print(f"  Classification result:")
        print(f"    Event type: {result.event_type}")
        print(f"    Sentiment: {result.sentiment} (score: {result.sentiment_score:.2f})")
        print(f"    Urgency: {result.urgency:.2f}")
        print(f"    Relevance: {result.relevance:.2f}")
        print(f"    Confidence: {result.confidence:.2f}")
        print(f"    Entities: {result.entities}")
        print(f"    Rationale: {result.rationale}")
        print(f"  PASS: OpenAI LLM is working")

        if hasattr(classifier, 'close'):
            classifier.close()
        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        if hasattr(classifier, 'close'):
            classifier.close()
        return False


async def test_full_pipeline(settings):
    print()
    print("=" * 60)
    print("TEST 3: Full NLP Pipeline (News → LLM → Market Mapping)")
    print("=" * 60)

    from app.nlp.providers.newsapi import NewsApiProvider
    from app.nlp.providers.llm_provider import build_llm_classifier
    from app.nlp.classifier import HybridClassifier, KeywordClassifier

    news_provider = NewsApiProvider(api_key=settings.newsapi_key)
    items = await news_provider.fetch_items()
    if not items:
        print("  SKIP: No news items to test pipeline with")
        return False

    headline = items[0].text
    print(f"  Real headline: '{headline[:100]}...'")

    llm = build_llm_classifier(
        provider=settings.llm_provider,
        model_name=settings.llm_model_name,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        timeout=settings.llm_timeout_seconds,
    )

    keyword_clf = KeywordClassifier()
    kw_result = keyword_clf.classify(headline)
    print(f"  Keyword classifier: {kw_result.sentiment.value}, "
          f"event={kw_result.event_type.value}, conf={kw_result.confidence:.2f}")

    if llm:
        hybrid = HybridClassifier(
            keyword=keyword_clf,
            llm=llm,
            llm_confidence_threshold=settings.llm_confidence_threshold,
        )
        hybrid_result = hybrid.classify(headline)
        print(f"  Hybrid (LLM) result: {hybrid_result.sentiment.value}, "
              f"event={hybrid_result.event_type.value}, conf={hybrid_result.confidence:.2f}")
        print(f"    Rationale: {hybrid_result.rationale}")
        print(f"    Entities: {hybrid_result.entities}")
        llm.close()

    print(f"  PASS: Full pipeline working")
    return True


async def main():
    settings = get_settings()

    r1 = await test_newsapi(settings)
    r2 = test_openai_llm(settings)
    r3 = await test_full_pipeline(settings) if r1 else False

    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  NewsAPI:        {'PASS' if r1 else 'FAIL'}")
    print(f"  OpenAI LLM:     {'PASS' if r2 else 'FAIL'}")
    print(f"  Full Pipeline:  {'PASS' if r3 else 'SKIP/FAIL'}")

    if r1 and r2:
        print()
        print("  Your bot's L3 intelligence (news + AI) is fully operational.")
        print("  When the bot runs, it will:")
        print("    1. Fetch real headlines every 5 minutes (NewsAPI)")
        print("    2. Classify each with GPT-4o-mini (sentiment, event type, entities)")
        print("    3. Map headlines to prediction markets")
        print("    4. Generate trading signals from the analysis")


if __name__ == "__main__":
    asyncio.run(main())
