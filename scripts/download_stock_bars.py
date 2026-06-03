#!/usr/bin/env python3
"""Download historical minute bars from Alpaca into the backtest bar store.

Writes one CSV per ticker to ``--data-dir`` (default ``data/bars``) with the
columns expected by the backtester: ``timestamp,open,high,low,close,volume``.

Examples:

    python scripts/download_stock_bars.py --tickers SPY,QQQ,NVDA \\
        --start 2025-01-01 --end 2025-03-01 --timeframe 1Min

    # Then backtest the full pipeline on the downloaded data:
    python scripts/backtest_stock_pipeline.py --tickers SPY,QQQ,NVDA \\
        --data-dir data/bars

Credentials are read from the environment / .env (ALPACA_API_KEY,
ALPACA_SECRET_KEY) via Settings.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import Settings  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tickers", required=True, help="comma separated tickers")
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", default=None, help="YYYY-MM-DD (default: now)")
    p.add_argument("--timeframe", default="1Min", choices=["1Min", "5Min", "15Min", "1Hour", "1Day"])
    p.add_argument("--data-dir", default="data/bars")
    p.add_argument("--feed", default=None, help="iex or sip (default: account default)")
    return p.parse_args()


def _timeframe(tf: str):
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    return {
        "1Min": TimeFrame.Minute,
        "5Min": TimeFrame(5, TimeFrameUnit.Minute),
        "15Min": TimeFrame(15, TimeFrameUnit.Minute),
        "1Hour": TimeFrame.Hour,
        "1Day": TimeFrame.Day,
    }[tf]


def main() -> int:
    args = parse_args()
    settings = Settings()
    if not (settings.alpaca_api_key and settings.alpaca_secret_key):
        print("ERROR: ALPACA_API_KEY / ALPACA_SECRET_KEY not configured", file=sys.stderr)
        return 2

    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.enums import DataFeed
    except ImportError:
        print("ERROR: alpaca-py not installed", file=sys.stderr)
        return 2

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = (
        datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
        if args.end
        else datetime.now(timezone.utc)
    )
    out_dir = Path(args.data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    client = StockHistoricalDataClient(settings.alpaca_api_key, settings.alpaca_secret_key)
    feed = None
    if args.feed:
        feed = DataFeed.SIP if args.feed.lower() == "sip" else DataFeed.IEX

    total = 0
    for ticker in tickers:
        req_kwargs = dict(
            symbol_or_symbols=ticker,
            timeframe=_timeframe(args.timeframe),
            start=start,
            end=end,
        )
        if feed is not None:
            req_kwargs["feed"] = feed
        try:
            resp = client.get_stock_bars(StockBarsRequest(**req_kwargs))
        except Exception as exc:
            print(f"  {ticker}: fetch failed: {exc}", file=sys.stderr)
            continue

        bars = resp.data.get(ticker, []) if hasattr(resp, "data") else []
        if not bars:
            print(f"  {ticker}: no bars returned")
            continue

        path = out_dir / f"{ticker}.csv"
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for b in bars:
                w.writerow([
                    b.timestamp.isoformat(),
                    b.open, b.high, b.low, b.close, int(b.volume),
                ])
        total += len(bars)
        print(f"  {ticker}: wrote {len(bars):,} bars → {path}")

    print(f"Done. {total:,} bars across {len(tickers)} tickers in {out_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
