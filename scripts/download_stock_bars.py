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
from datetime import datetime, timedelta, timezone
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
    p.add_argument(
        "--append",
        action="store_true",
        help=(
            "Accumulate: if a ticker CSV already exists, fetch only bars newer "
            "than the last stored one and append (dedup), growing the history "
            "instead of overwriting. Missing files are seeded from --start."
        ),
    )
    return p.parse_args()


_TIMEFRAME_MINUTES = {"1Min": 1, "5Min": 5, "15Min": 15, "1Hour": 60, "1Day": 1440}


def _tail_timestamps(path: "Path", n: int = 6) -> list[datetime]:
    """Read up to the last ``n`` data-row timestamps from a bar CSV."""
    out: list[datetime] = []
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = min(size, 16384)
            f.seek(size - block)
            tail = f.read().decode("utf-8", "replace").strip().splitlines()
        for line in reversed(tail):
            cell = line.split(",", 1)[0].strip()
            if not cell or cell.lower() == "timestamp":
                continue
            try:
                out.append(datetime.fromisoformat(cell.replace("Z", "+00:00")))
            except ValueError:
                continue
            if len(out) >= n:
                break
    except Exception:
        return out
    return out


def _last_timestamp(path: "Path") -> datetime | None:
    ts = _tail_timestamps(path, n=1)
    return ts[0] if ts else None


def _stored_spacing_minutes(path: "Path") -> float | None:
    """Infer the bar spacing (minutes) of an existing store from its tail.

    Used to refuse appending a different timeframe into the same file (which
    would silently corrupt the dataset). Uses the smallest positive gap among
    recent rows so intraday spacing — not the overnight gap — is detected.
    """
    ts = _tail_timestamps(path, n=6)
    if len(ts) < 2:
        return None
    ts = sorted(ts)
    gaps = [
        (ts[i] - ts[i - 1]).total_seconds() / 60.0
        for i in range(1, len(ts))
        if ts[i] > ts[i - 1]
    ]
    return min(gaps) if gaps else None


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
        path = out_dir / f"{ticker}.csv"
        # Append mode: only fetch bars newer than the last stored one so the
        # history accumulates instead of being overwritten.
        appending = False
        last_ts = None
        fetch_start = start
        if args.append and path.exists() and path.stat().st_size > 0:
            tf_minutes = _TIMEFRAME_MINUTES.get(args.timeframe, 1)
            spacing = _stored_spacing_minutes(path)
            if spacing is not None and abs(spacing - tf_minutes) > 0.01:
                print(
                    f"  {ticker}: SKIP — existing store is ~{spacing:g}min bars "
                    f"but --timeframe={args.timeframe}. Clear data-dir to reseed.",
                    file=sys.stderr,
                )
                continue
            last_ts = _last_timestamp(path)
            if last_ts is not None:
                appending = True
                # Re-fetch from one bar after the last stored timestamp.
                step = timedelta(minutes=tf_minutes)
                fetch_start = last_ts + step

        if fetch_start >= end:
            print(f"  {ticker}: already up to date ({last_ts})")
            continue

        req_kwargs = dict(
            symbol_or_symbols=ticker,
            timeframe=_timeframe(args.timeframe),
            start=fetch_start,
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
        # Guard against dup/overlap rows when appending.
        if appending and last_ts is not None:
            bars = [b for b in bars if b.timestamp > last_ts]
        if not bars:
            print(f"  {ticker}: no new bars")
            continue

        mode = "a" if appending else "w"
        with path.open(mode, newline="") as f:
            w = csv.writer(f)
            if not appending:
                w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for b in bars:
                w.writerow([
                    b.timestamp.isoformat(),
                    b.open, b.high, b.low, b.close, int(b.volume),
                ])
        total += len(bars)
        verb = "appended" if appending else "wrote"
        print(f"  {ticker}: {verb} {len(bars):,} bars → {path}")

    print(f"Done. {total:,} bars across {len(tickers)} tickers in {out_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
