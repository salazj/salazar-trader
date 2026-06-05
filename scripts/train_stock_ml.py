#!/usr/bin/env python3
"""Train the L2 stock ML model from historical bars (with calibration).

Pipeline:
  1. Replay ``data/bars/<TICKER>.csv`` through ``StockFeatureEngine``.
  2. Build the production feature vector at each bar (after warmup).
  3. Label each sample by the forward return at ``--horizon`` bars:
        y = 1 if (close[t+H] - close[t]) / close[t] > --up-threshold else 0
  4. Chronological train / calibration / test split (no look-ahead).
  5. Fit a gradient-boosted classifier, then probability-calibrate it.
  6. Report holdout metrics and save to ``--out`` (default = settings path).

Examples:
    python scripts/train_stock_ml.py --tickers SPY,QQQ,NVDA --data-dir data/bars
    python scripts/train_stock_ml.py --tickers NVDA --horizon 10 --up-threshold 0.001
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import Settings  # noqa: E402
from app.stocks.backtesting import load_csv_bars  # noqa: E402
from app.stocks.features import StockFeatureEngine  # noqa: E402
from app.stocks.ml.predictor import (  # noqa: E402
    StockMLPredictor,
    extract_feature_vector,
    train_baseline_model,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tickers", required=True, help="comma separated tickers")
    p.add_argument("--data-dir", default="data/bars")
    p.add_argument("--out", default=None, help="output pickle (default: settings.stock_ml_model_path)")
    p.add_argument("--horizon", type=int, default=10, help="forward bars for labeling")
    p.add_argument("--up-threshold", type=float, default=0.0005, help="forward return for a 'up' label")
    p.add_argument("--warmup", type=int, default=50, help="skip this many leading bars per ticker")
    p.add_argument("--test-frac", type=float, default=0.2)
    p.add_argument("--no-calibration", action="store_true")
    return p.parse_args()


def build_dataset(tickers, data_dir, horizon, up_threshold, warmup):
    X_rows: list[np.ndarray] = []
    y_rows: list[int] = []
    for ticker in tickers:
        path = Path(data_dir) / f"{ticker}.csv"
        if not path.exists():
            print(f"  WARNING: {path} not found, skipping", file=sys.stderr)
            continue
        bars = load_csv_bars(path, symbol=ticker)
        if len(bars) <= warmup + horizon:
            print(f"  {ticker}: not enough bars ({len(bars)})", file=sys.stderr)
            continue
        engine = StockFeatureEngine(ticker)
        feats: list = []
        closes: list[float] = []
        for b in bars:
            engine.add_bar(b)
            feats.append(extract_feature_vector(engine.compute()))
            closes.append(b.close)
        # Label with forward return; stop `horizon` short of the end.
        for i in range(warmup, len(closes) - horizon):
            fwd = (closes[i + horizon] - closes[i]) / closes[i] if closes[i] else 0.0
            X_rows.append(feats[i])
            y_rows.append(1 if fwd > up_threshold else 0)
    if not X_rows:
        raise SystemExit("ERROR: no training samples built (need bar CSVs)")
    return np.vstack(X_rows), np.asarray(y_rows, dtype=int)


def _metrics(model, X, y) -> dict:
    try:
        proba = model.predict_proba(X)[:, -1]
    except Exception:
        proba = model.predict(X).astype(float)
    pred = (proba >= 0.5).astype(int)
    acc = float((pred == y).mean()) if len(y) else 0.0
    eps = 1e-9
    logloss = float(
        -np.mean(y * np.log(proba + eps) + (1 - y) * np.log(1 - proba + eps))
    ) if len(y) else 0.0
    return {"accuracy": round(acc, 4), "logloss": round(logloss, 4), "n": int(len(y))}


def _calibrate(model, X_cal, y_cal):
    """Probability-calibrate an already-fit model (sklearn version tolerant)."""
    from sklearn.calibration import CalibratedClassifierCV

    # sklearn >= 1.6: wrap in FrozenEstimator; older: cv="prefit".
    try:
        from sklearn.frozen import FrozenEstimator

        cal = CalibratedClassifierCV(FrozenEstimator(model), method="isotonic")
        cal.fit(X_cal, y_cal)
        return cal
    except Exception:
        pass
    try:
        cal = CalibratedClassifierCV(model, method="isotonic", cv="prefit")
        cal.fit(X_cal, y_cal)
        return cal
    except Exception:
        return None


def main() -> int:
    args = parse_args()
    settings = Settings()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    out_path = args.out or settings.stock_ml_model_path

    print(f"Building dataset for {tickers} (horizon={args.horizon}, "
          f"up>{args.up_threshold})...")
    X, y = build_dataset(tickers, args.data_dir, args.horizon, args.up_threshold, args.warmup)
    print(f"  samples={len(y):,}  up-rate={y.mean():.3f}")

    # Chronological split (the rows are already in time order per ticker; for a
    # single ticker this is a clean walk-forward holdout).
    n = len(y)
    n_test = max(1, int(n * args.test_frac))
    n_cal = max(1, int((n - n_test) * 0.2))
    n_train = n - n_test - n_cal
    X_train, y_train = X[:n_train], y[:n_train]
    X_cal, y_cal = X[n_train:n_train + n_cal], y[n_train:n_train + n_cal]
    X_test, y_test = X[n_train + n_cal:], y[n_train + n_cal:]

    print(f"  train={n_train}  cal={n_cal}  test={n_test}")
    predictor = train_baseline_model(X_train, y_train, version="stock_l2")
    model = predictor._model  # noqa: SLF001 - trusted internal

    print(f"  base model: {predictor.version}")
    print(f"  base test : {_metrics(model, X_test, y_test)}")

    final_model = model
    version = predictor.version
    if not args.no_calibration and len(np.unique(y_cal)) > 1:
        calibrated = _calibrate(model, X_cal, y_cal)
        if calibrated is not None:
            final_model = calibrated
            version = f"{predictor.version}_calibrated"
            print(f"  calibrated test : {_metrics(final_model, X_test, y_test)}")
        else:
            print("  calibration skipped (unsupported sklearn API)", file=sys.stderr)

    out = StockMLPredictor(model=final_model, version=version)
    out.save(out_path)
    print(f"Saved model → {out_path}  (version={version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
