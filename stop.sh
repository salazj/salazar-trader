#!/usr/bin/env bash
#
# Stop Salazar Trader, detecting whichever install path is active:
#   - Containerized -> docker compose down
#   - Native        -> systemctl stop salazar-trader
#
# Stops both if both happen to be running. Safe to run repeatedly.
#
set -euo pipefail

cd "$(dirname "$0")"
SERVICE_NAME="salazar-trader"

c_blue()  { printf '\033[1;34m%s\033[0m\n' "$*"; }
c_green() { printf '\033[1;32m%s\033[0m\n' "$*"; }

stopped_something=false

# ── Containerized ──────────────────────────────────────────────────
if command -v docker >/dev/null 2>&1; then
  running="$(docker compose ps -q 2>/dev/null || true)"
  strays="$(docker ps -q --filter 'name=salazar-' 2>/dev/null || true)"
  if [ -n "$running" ] || [ -n "$strays" ]; then
    c_blue "==> Stopping Docker stack..."
    docker compose down 2>/dev/null || true
    # Clean up any stray named containers from older/standalone runs.
    docker rm -f salazar-backend salazar-frontend salazar-ollama \
                  salazar-ollama-pull salazar-trader 2>/dev/null || true
    stopped_something=true
  fi
fi

# ── Native systemd service ─────────────────────────────────────────
if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
    c_blue "==> Stopping native systemd service (${SERVICE_NAME})..."
    sudo systemctl stop "${SERVICE_NAME}" || true
    stopped_something=true
  fi
fi

if $stopped_something; then
  c_green "    Stopped. Run ./start.sh to start again."
else
  c_green "    Nothing was running. Run ./start.sh to start."
fi
