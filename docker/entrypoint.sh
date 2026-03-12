#!/usr/bin/env bash
set -euo pipefail

# ── Banner ─────────────────────────────────────────────────────────────
cat <<'BANNER'

  ____        _                       _____              _
 / ___|  __ _| | __ _ ______ _ _ __  |_   _| __ __ _  __| | ___ _ __
 \___ \ / _` | |/ _` |_  / _` | '__|   | || '__/ _` |/ _` |/ _ \ '__|
  ___) | (_| | | (_| |/ / (_| | |      | || | | (_| | (_| |  __/ |
 |____/ \__,_|_|\__,_/___\__,_|_|      |_||_|  \__,_|\__,_|\___|_|
                          $alazar-Trader

BANNER

# ── Mode detection ─────────────────────────────────────────────────────
BOT_MODE="${BOT_MODE:-dry-run}"
DRY_RUN="${DRY_RUN:-true}"
ENABLE_LIVE_TRADING="${ENABLE_LIVE_TRADING:-false}"
LIVE_TRADING_ACKNOWLEDGED="${LIVE_TRADING_ACKNOWLEDGED:-false}"

EXCHANGE="${EXCHANGE:-polymarket}"
ASSET_CLASS="${ASSET_CLASS:-prediction_markets}"
BROKER="${BROKER:-alpaca}"

echo "=============================================="
echo "  Asset:    ${ASSET_CLASS}"
echo "  Exchange: ${EXCHANGE}"
echo "  Broker:   ${BROKER}"
echo "  Mode:     ${BOT_MODE}"
echo "  Dry Run:  ${DRY_RUN}"
echo "  Live:     ${ENABLE_LIVE_TRADING}"
echo "  Ack:      ${LIVE_TRADING_ACKNOWLEDGED}"
echo "  LLM:      ${LLM_PROVIDER:-none}"
echo "  NLP:      ${NLP_PROVIDERS:-${NLP_PROVIDER:-mock}}"
echo "  Log:      ${LOG_LEVEL:-INFO}"
echo "  Time:     $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "=============================================="

# ── Safety checks ──────────────────────────────────────────────────────
if [ "${DRY_RUN}" = "false" ] && [ "${ENABLE_LIVE_TRADING}" = "true" ] && [ "${LIVE_TRADING_ACKNOWLEDGED}" = "true" ]; then
    echo ""
    echo "  *** LIVE TRADING IS ENABLED ***"
    echo "  All three safety gates are open."
    echo "  Real orders WILL be submitted."
    echo ""

    if [ "${ASSET_CLASS}" = "equities" ]; then
        if [ -z "${ALPACA_API_KEY:-}" ] || [ -z "${ALPACA_SECRET_KEY:-}" ]; then
            echo "ERROR: Equities live trading requires ALPACA_API_KEY and ALPACA_SECRET_KEY."
            echo "       Set them in your .env file or environment."
            exit 1
        fi
    elif [ "${EXCHANGE}" = "kalshi" ]; then
        if [ -z "${KALSHI_API_KEY:-}" ]; then
            echo "ERROR: Kalshi live trading requires KALSHI_API_KEY."
            echo "       Set it in your .env file or environment."
            exit 1
        fi
        if [ -z "${KALSHI_PRIVATE_KEY:-}" ] && [ -z "${KALSHI_PRIVATE_KEY_PATH:-}" ]; then
            echo "ERROR: Kalshi live trading requires KALSHI_PRIVATE_KEY or KALSHI_PRIVATE_KEY_PATH."
            echo "       Set one of them in your .env file or environment."
            exit 1
        fi
    else
        if [ -z "${PRIVATE_KEY:-}" ] || [ -z "${POLY_API_KEY:-}" ]; then
            echo "ERROR: Polymarket live trading requires PRIVATE_KEY and POLY_API_KEY."
            echo "       Set them in your .env file or environment."
            exit 1
        fi
    fi
else
    echo ""
    echo "  Running in SAFE mode — no real orders will be placed."
    echo ""
fi

# ── Ensure volume dirs exist ───────────────────────────────────────────
mkdir -p /app/data /app/logs /app/model_artifacts /app/reports

# ── Route to the right command ─────────────────────────────────────────
CMD="${1:-bot}"
shift || true

# uvicorn requires lowercase log level
UV_LOG_LEVEL=$(echo "${LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')

case "${CMD}" in
    api)
        echo "Starting API server..."
        exec uvicorn app.api.app:create_app --factory \
            --host 0.0.0.0 \
            --port ${API_PORT:-8000} \
            --log-level "${UV_LOG_LEVEL}" \
            --access-log
        ;;
    bot)
        echo "Starting bot (mode=${BOT_MODE})..."
        exec python -m app.main "$@"
        ;;
    backtest)
        echo "Starting backtest runner..."
        exec python /app/scripts/backtest_strategy.py "$@"
        ;;
    replay)
        echo "Starting replay runner..."
        exec python /app/scripts/replay_session.py "$@"
        ;;
    train)
        echo "Starting model training..."
        exec python /app/scripts/train_model.py "$@"
        ;;
    evaluate)
        echo "Starting model evaluation..."
        exec python /app/scripts/evaluate_model.py "$@"
        ;;
    nlp-replay)
        echo "Starting NLP replay..."
        exec python -c "
from app.nlp.replay import NlpReplayEngine
from app.nlp.pipeline import NlpPipeline
engine = NlpReplayEngine(pipeline=NlpPipeline())
result = engine.replay_from_json('${2:-data/news/examples.json}')
print(f'Processed {result.total_items} items, generated {result.total_signals} signals')
for item in result.per_item:
    print(f'  {item[\"item_id\"]}: {item[\"signal_count\"]} signals')
"
        ;;
    shell)
        echo "Dropping to shell..."
        exec /bin/bash "$@"
        ;;
    health)
        exec curl -sf "http://127.0.0.1:${HEALTH_PORT:-8880}/health"
        ;;
    *)
        echo "Unknown command: ${CMD}"
        echo "Available: api, bot, backtest, replay, train, evaluate, nlp-replay, shell, health"
        exit 1
        ;;
esac
