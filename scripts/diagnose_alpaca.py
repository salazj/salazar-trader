#!/usr/bin/env python3
"""Smoke-test Alpaca paper-trading credentials and connectivity.

Standalone script — only needs `alpaca-py` and `python-dotenv`. Run with:

    .venv/bin/python scripts/diagnose_alpaca.py
"""

import os
import sys
import traceback


def banner(title: str) -> None:
    print("=" * 60)
    print(title)
    print("=" * 60)


def mask(value: str) -> str:
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def load_env():
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception as exc:
        print(f"  WARNING: could not load .env via python-dotenv ({exc}); "
              "relying on existing process env")


def test_credentials_loaded():
    banner("TEST 1: Credentials loaded from .env")
    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    paper_flag = os.environ.get("ALPACA_PAPER", "true").lower() in {"1", "true", "yes"}

    print(f"  ALPACA_API_KEY:    {mask(api_key)}")
    print(f"  ALPACA_SECRET_KEY: {mask(secret_key)}")
    print(f"  ALPACA_PAPER:      {paper_flag}")
    if not api_key or not secret_key:
        print("  FAIL: missing ALPACA_API_KEY or ALPACA_SECRET_KEY in .env")
        return None
    if not paper_flag:
        print("  WARNING: ALPACA_PAPER=false — this would hit LIVE trading!")
    print("  PASS")
    return api_key, secret_key, paper_flag


def test_sdk_installed():
    banner("TEST 2: alpaca-py SDK installed")
    try:
        import alpaca
        from alpaca.trading.client import TradingClient  # noqa: F401
        from alpaca.data.historical import StockHistoricalDataClient  # noqa: F401

        version = getattr(alpaca, "__version__", "unknown")
        print(f"  alpaca-py version: {version}")
        print("  PASS")
        return True
    except Exception as exc:
        print(f"  FAIL: {exc}")
        print("  Install with: pip install 'alpaca-py>=0.33' python-dotenv")
        return False


def test_account(api_key, secret_key, paper):
    banner("TEST 3: GET /v2/account (auth + connectivity)")
    try:
        from alpaca.trading.client import TradingClient

        client = TradingClient(api_key, secret_key, paper=paper)
        account = client.get_account()
        print(f"  Account ID:         {account.id}")
        print(f"  Status:             {account.status}")
        print(f"  Pattern day trader: {account.pattern_day_trader}")
        print(f"  Trading blocked:    {account.trading_blocked}")
        print(f"  Cash:               ${account.cash}")
        print(f"  Buying power:       ${account.buying_power}")
        print(f"  Equity:             ${account.equity}")
        print("  PASS")
        return client
    except Exception as exc:
        print(f"  FAIL: {exc}")
        traceback.print_exc()
        return None


def test_clock(client):
    banner("TEST 4: GET /v2/clock (market hours)")
    try:
        clock = client.get_clock()
        print(f"  Server time: {clock.timestamp}")
        print(f"  Market open: {clock.is_open}")
        print(f"  Next open:   {clock.next_open}")
        print(f"  Next close:  {clock.next_close}")
        print("  PASS")
        return True
    except Exception as exc:
        print(f"  FAIL: {exc}")
        return False


def test_market_data(api_key, secret_key):
    banner("TEST 5: Historical market data (AAPL latest quote)")
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest

        data_client = StockHistoricalDataClient(api_key, secret_key)
        request = StockLatestQuoteRequest(symbol_or_symbols="AAPL")
        quotes = data_client.get_stock_latest_quote(request)
        quote = quotes["AAPL"]
        print(f"  AAPL bid: {quote.bid_price} x {quote.bid_size}")
        print(f"  AAPL ask: {quote.ask_price} x {quote.ask_size}")
        print(f"  Timestamp: {quote.timestamp}")
        print("  PASS")
        return True
    except Exception as exc:
        print(f"  FAIL: {exc}")
        return False


def main():
    load_env()

    results = {}
    creds = test_credentials_loaded()
    print()
    if creds is None:
        return 1
    api_key, secret_key, paper = creds
    results["credentials"] = True

    results["sdk"] = test_sdk_installed()
    print()
    if not results["sdk"]:
        return 1

    client = test_account(api_key, secret_key, paper)
    results["account"] = client is not None
    print()
    if client is None:
        return 1

    results["clock"] = test_clock(client)
    print()
    results["market_data"] = test_market_data(api_key, secret_key)
    print()

    banner("SUMMARY")
    for name, ok in results.items():
        print(f"  {name:<15} {'PASS' if ok else 'FAIL'}")

    if all(results.values()):
        print()
        print("  Alpaca paper-trading connection is fully operational.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
