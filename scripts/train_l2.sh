#!/usr/bin/env bash
# Turnkey L2 (ML) training: download historical bars from Alpaca, then train +
# calibrate the gradient-boosted classifier the live bot loads as its L2 layer.
#
# Run this on a box that has the Alpaca creds in .env (e.g. the Jetson). The
# bot picks the model up on its next restart (logged as `stock_ml_loaded`).
#
# Usage:
#   ./scripts/train_l2.sh                         # defaults below
#   TICKERS="SPY,QQQ,NVDA,AAPL" START=2024-06-01 ./scripts/train_l2.sh
#   HORIZON=10 UP_THRESHOLD=0.0005 ./scripts/train_l2.sh
#
# Env overrides:
#   TICKERS       comma-separated symbols      (default: liquid large-caps + ETFs)
#   START         history start  YYYY-MM-DD    (default: ~1y ago)
#   END           history end    YYYY-MM-DD    (default: today)
#   TIMEFRAME     1Min|5Min|15Min|1Hour|1Day   (default: 1Min)
#   HORIZON       forward bars for the label   (default: 10)
#   UP_THRESHOLD  forward return = "up" label  (default: 0.0005)
#   FEED          iex|sip                       (default: account default)
#   DATA_DIR      where CSVs land               (default: data/bars)
set -euo pipefail

cd "$(dirname "$0")/.."

PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x ".venv/bin/python3" ]]; then PY=".venv/bin/python3"; else PY="python3"; fi
fi

TICKERS="${TICKERS:-SPY,QQQ,AAPL,MSFT,NVDA,TSLA,AMD,META,AMZN,GOOGL,IWM,GLD}"
START="${START:-$($PY -c 'import datetime; print((datetime.date.today()-datetime.timedelta(days=365)).isoformat())')}"
END="${END:-$($PY -c 'import datetime; print(datetime.date.today().isoformat())')}"
TIMEFRAME="${TIMEFRAME:-1Min}"
HORIZON="${HORIZON:-10}"
UP_THRESHOLD="${UP_THRESHOLD:-0.0005}"
DATA_DIR="${DATA_DIR:-data/bars}"

echo "==> Downloading $TIMEFRAME bars for [$TICKERS]  $START → $END"
DL_ARGS=(--tickers "$TICKERS" --start "$START" --end "$END" --timeframe "$TIMEFRAME" --data-dir "$DATA_DIR")
if [[ -n "${FEED:-}" ]]; then DL_ARGS+=(--feed "$FEED"); fi
"$PY" scripts/download_stock_bars.py "${DL_ARGS[@]}"

echo "==> Training L2 model (horizon=$HORIZON, up>$UP_THRESHOLD)"
"$PY" scripts/train_stock_ml.py \
  --tickers "$TICKERS" \
  --data-dir "$DATA_DIR" \
  --horizon "$HORIZON" \
  --up-threshold "$UP_THRESHOLD"

echo "==> Done. Restart the bot to load the new model (look for 'stock_ml_loaded')."
