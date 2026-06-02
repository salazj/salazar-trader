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

# Unload any models the host Ollama is holding in memory (frees RAM/GPU).
# The models stay on disk and the ollama service keeps running; start.sh
# re-warms the model next time. (Containerized stop frees memory by bringing
# the ollama container down, so this is only needed for the native path.)
unload_ollama_models() {
  command -v ollama >/dev/null 2>&1 || return 0
  curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 || return 0
  local loaded
  loaded="$(ollama ps 2>/dev/null | awk 'NR>1 && $1!="" {print $1}')"
  [ -z "$loaded" ] && return 0
  local m
  for m in $loaded; do
    c_blue "==> Unloading Ollama model '${m}' from memory..."
    ollama stop "$m" >/dev/null 2>&1 || \
      curl -sf http://127.0.0.1:11434/api/generate \
        -d "{\"model\":\"${m}\",\"keep_alive\":0}" >/dev/null 2>&1 || true
  done
}

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
    # Surface the sudo password prompt so it doesn't look like a freeze.
    if ! sudo -n true 2>/dev/null; then
      c_blue "==> This step needs sudo — enter your password if prompted:"
    fi
    sudo systemctl stop "${SERVICE_NAME}" || true
    unload_ollama_models
    stopped_something=true
  fi
fi

if $stopped_something; then
  c_green "    Stopped. Run ./start.sh to start again."
else
  c_green "    Nothing was running. Run ./start.sh to start."
fi
