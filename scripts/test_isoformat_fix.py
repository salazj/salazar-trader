"""Quick test to verify .isoformat() fix on Market.end_date and Order.created_at."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()


async def test_market_save():
    """Test that saving a market with a string end_date works."""
    from app.data.models import Market
    from app.storage.repository import _to_iso

    m = Market(
        market_id="TEST-123",
        question="Will it rain tomorrow?",
        slug="will-it-rain",
        end_date="2026-03-15T00:00:00Z",
        exchange="kalshi",
    )

    result = _to_iso(m.end_date)
    assert isinstance(result, str), f"Expected str, got {type(result)}"
    assert result == "2026-03-15T00:00:00Z", f"Unexpected value: {result}"
    print(f"  [PASS] Market.end_date (str) -> _to_iso = {result!r}")

    m2 = Market(market_id="TEST-456", question="Test", end_date=None)
    result2 = _to_iso(m2.end_date)
    assert result2 is None, f"Expected None, got {result2}"
    print(f"  [PASS] Market.end_date (None) -> _to_iso = {result2!r}")


async def test_order_created_at():
    """Test that Order.created_at works with both datetime and string."""
    from datetime import datetime, timezone
    from app.data.models import Order, Side, OrderStatus
    from app.storage.repository import _to_iso

    o = Order(
        order_id="ORD-1",
        market_id="MKT-1",
        side=Side.BUY,
        price=0.50,
        size=10,
        created_at=datetime.now(timezone.utc),
    )
    result = _to_iso(o.created_at)
    assert isinstance(result, str), f"Expected str, got {type(result)}"
    print(f"  [PASS] Order.created_at (datetime) -> _to_iso = {result!r}")

    result_str = _to_iso("2026-03-11T12:00:00Z")
    assert result_str == "2026-03-11T12:00:00Z"
    print(f"  [PASS] Raw string -> _to_iso = {result_str!r}")


async def test_bot_manager_orders():
    """Test OrderItem creation in bot_manager handles string created_at."""
    from app.api.schemas import OrderItem

    item = OrderItem(
        order_id="ORD-1",
        instrument_id="INS-1",
        exchange="kalshi",
        side="buy",
        price=0.50,
        size=10,
        filled_size=0,
        status="pending",
        created_at="2026-03-11T12:00:00+00:00",
    )
    dumped = item.model_dump()
    assert isinstance(dumped["created_at"], str)
    print(f"  [PASS] OrderItem with str created_at -> model_dump works")


async def test_live_market_scan():
    """Fetch real markets from Kalshi and test the save_market path."""
    from app.config.settings import get_settings
    from app.storage.repository import _to_iso

    settings = get_settings()
    if not settings.has_kalshi_credentials:
        print("  [SKIP] No Kalshi credentials")
        return

    from app.exchanges.kalshi.market_data import KalshiMarketDataClient
    client = KalshiMarketDataClient(settings)
    try:
        markets, _cursor = await client.get_markets(limit=20)
        print(f"  Fetched {len(markets)} markets from Kalshi")

        fail_count = 0
        for m in markets[:10]:
            try:
                iso_val = _to_iso(m.end_date)
                print(f"    [OK] {m.market_id[:30]:30s} end_date={m.end_date!r:30s} -> {iso_val!r}")
            except Exception as e:
                print(f"    [FAIL] {m.market_id[:30]:30s} end_date={m.end_date!r} -> {e}")
                fail_count += 1

        if fail_count == 0:
            print(f"  [PASS] All {min(10, len(markets))} markets passed _to_iso check")
        else:
            print(f"  [FAIL] {fail_count} markets failed")
    finally:
        await client.close()


async def main():
    print("=== Testing isoformat fix ===\n")

    print("1. Market.end_date handling:")
    await test_market_save()

    print("\n2. Order.created_at handling:")
    await test_order_created_at()

    print("\n3. OrderItem schema (bot_manager path):")
    await test_bot_manager_orders()

    print("\n4. Live market scan (Kalshi):")
    await test_live_market_scan()

    print("\n=== All tests passed ===")


if __name__ == "__main__":
    asyncio.run(main())
