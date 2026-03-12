#!/usr/bin/env python3
"""Diagnose why 'No active markets found' occurs.

Traces the full pipeline: fetch → category enrichment → category filter → hard filters.
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app.config.settings import get_settings
from app.exchanges.kalshi.market_data import KalshiMarketDataClient
from app.universe.categories import CategoryPreferences
from app.universe.filters import FilterConfig, MarketFilter


async def main():
    settings = get_settings()
    print(f"=== Market Scanning Diagnostic ===")
    print(f"Exchange: kalshi")
    print(f"Base URL: {settings.kalshi_base_url}")
    print(f"Demo mode: {settings.kalshi_demo_mode}")
    print(f"Has credentials: {settings.has_kalshi_credentials}")
    print(f"Include categories: '{settings.include_categories}'")
    print(f"Exclude categories: '{settings.exclude_categories}'")
    print()

    client = KalshiMarketDataClient(settings)
    try:
        # Step 1: Fetch raw markets (just first page)
        print("--- Step 1: Fetch first page of markets ---")
        try:
            page, cursor = await client.get_markets()
            print(f"  First page: {len(page)} non-parlay markets returned")
            if cursor:
                print(f"  Has more pages (cursor present)")
            else:
                print(f"  No more pages")
            if page:
                m = page[0]
                print(f"  Sample market: id={m.market_id}, active={m.active}, "
                      f"question='{m.question[:60]}', category='{m.category}'")
                ed = m.exchange_data or {}
                print(f"    exchange_data keys: {list(ed.keys())}")
                print(f"    event_ticker: {ed.get('event_ticker', 'MISSING')}")
                print(f"    category (from exchange_data): {ed.get('category', 'MISSING')}")
                print(f"    volume: {ed.get('volume')}, volume_24h: {ed.get('volume_24h')}")
                print(f"    open_interest: {ed.get('open_interest')}")
                print(f"    status: {ed.get('status')}")
            else:
                print("  First page was all parlays — paging will continue in step 2")
        except Exception as e:
            print(f"  *** FETCH FAILED: {e} ***")
            return

        # Step 2: Fetch all markets with category enrichment
        print()
        print("--- Step 2: Fetch ALL markets (with category enrichment) ---")
        all_markets = await client.get_all_markets()
        print(f"  Total markets: {len(all_markets)}")

        active_count = sum(1 for m in all_markets if m.active)
        print(f"  Active markets: {active_count}")

        cats = {}
        no_cat_count = 0
        for m in all_markets:
            cat = m.category or ""
            if not cat:
                cat_from_ed = (m.exchange_data or {}).get("category", "")
                if cat_from_ed:
                    cat = cat_from_ed
            if cat:
                cats[cat] = cats.get(cat, 0) + 1
            else:
                no_cat_count += 1
        print(f"  Markets with category: {sum(cats.values())}")
        print(f"  Markets WITHOUT category: {no_cat_count}")
        print(f"  Category distribution:")
        for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
            print(f"    {cat}: {count}")

        # Step 3: Test category filter
        print()
        print("--- Step 3: Category filter test ---")
        cat_prefs = CategoryPreferences.from_settings(settings)
        print(f"  Mode: {cat_prefs.config.mode}")
        print(f"  Include set: {cat_prefs.config.include_categories or '(empty = all)'}")
        print(f"  Exclude set: {cat_prefs.config.exclude_categories or '(empty = none)'}")

        cat_passed = 0
        cat_failed = 0
        cat_failed_reasons = {}
        for m in all_markets:
            if cat_prefs.is_allowed(m):
                cat_passed += 1
            else:
                cat_failed += 1
                cat = m.category or (m.exchange_data or {}).get("category", "") or "uncategorized"
                cat_failed_reasons[cat] = cat_failed_reasons.get(cat, 0) + 1

        print(f"  Passed category filter: {cat_passed}")
        print(f"  Failed category filter: {cat_failed}")
        if cat_failed_reasons:
            print(f"  Failed categories: {cat_failed_reasons}")

        # Step 4: Test hard filters
        print()
        print("--- Step 4: Hard filter test (on category-passed markets) ---")
        filter_config = FilterConfig(
            min_liquidity=settings.min_liquidity_threshold,
            max_spread=settings.max_spread_filter,
            min_volume=settings.min_volume_threshold,
            min_orderbook_depth=settings.min_orderbook_depth,
            min_time_to_resolution_hours=settings.min_time_to_resolution_hours,
            max_time_to_resolution_hours=settings.max_time_to_resolution_hours,
            allowed_categories=cat_prefs.config.include_categories,
            excluded_categories=cat_prefs.config.exclude_categories,
        )
        print(f"  Filter config:")
        print(f"    min_liquidity: {filter_config.min_liquidity}")
        print(f"    max_spread: {filter_config.max_spread}")
        print(f"    min_volume: {filter_config.min_volume}")
        print(f"    min_orderbook_depth: {filter_config.min_orderbook_depth}")
        print(f"    min_time_to_resolution_hours: {filter_config.min_time_to_resolution_hours}")
        print(f"    max_time_to_resolution_hours: {filter_config.max_time_to_resolution_hours}")

        market_filter = MarketFilter(filter_config)
        filter_passed = 0
        filter_reasons = {}

        for m in all_markets:
            if not cat_prefs.is_allowed(m):
                continue

            metadata = {
                "volume": (m.exchange_data or {}).get("volume", 0),
                "volume_24h": (m.exchange_data or {}).get("volume_24h", 0),
                "open_interest": (m.exchange_data or {}).get("open_interest", 0),
                "liquidity": (m.exchange_data or {}).get("liquidity", 0),
                "spread": (m.exchange_data or {}).get("spread"),
                "category": (m.exchange_data or {}).get("category", ""),
            }
            result = market_filter.apply_all(m, None, metadata)
            if result.passed:
                filter_passed += 1
            else:
                reason = result.reason.split(":")[0]
                filter_reasons[reason] = filter_reasons.get(reason, 0) + 1

        print(f"  Passed all filters: {filter_passed}")
        print(f"  Failed filter breakdown:")
        for reason, count in sorted(filter_reasons.items(), key=lambda x: -x[1]):
            print(f"    {reason}: {count}")

        # Step 5: Show some sample markets that pass/fail
        print()
        print("--- Step 5: Sample results ---")
        passed_samples = []
        failed_samples = []
        for m in all_markets:
            if not cat_prefs.is_allowed(m):
                continue
            metadata = {
                "volume": (m.exchange_data or {}).get("volume", 0),
                "volume_24h": (m.exchange_data or {}).get("volume_24h", 0),
                "open_interest": (m.exchange_data or {}).get("open_interest", 0),
                "liquidity": (m.exchange_data or {}).get("liquidity", 0),
                "spread": (m.exchange_data or {}).get("spread"),
                "category": (m.exchange_data or {}).get("category", ""),
            }
            result = market_filter.apply_all(m, None, metadata)
            if result.passed and len(passed_samples) < 3:
                passed_samples.append(m)
            elif not result.passed and len(failed_samples) < 5:
                failed_samples.append((m, result.reason))

        if passed_samples:
            print(f"  Markets that PASS (first {len(passed_samples)}):")
            for m in passed_samples:
                ed = m.exchange_data or {}
                print(f"    {m.market_id}: cat={m.category}, vol={ed.get('volume')}, "
                      f"end={m.end_date}, q='{m.question[:50]}'")
        else:
            print("  *** NO MARKETS PASS ALL FILTERS ***")

        if failed_samples:
            print(f"  Markets that FAIL (first {len(failed_samples)}):")
            for m, reason in failed_samples:
                ed = m.exchange_data or {}
                print(f"    {m.market_id}: reason={reason}, cat={m.category}, "
                      f"vol={ed.get('volume')}, liq={ed.get('liquidity')}, "
                      f"end={m.end_date}")

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
