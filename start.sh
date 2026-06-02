#!/usr/bin/env bash
#
# Salazar Trader launcher.
#
# Two install paths, both with the React GUI:
#   1) Containerized  -> Docker compose (backend + frontend + ollama).
#                        Pull prebuilt images from GHCR, or build from source.
#   2) Native         -> Python venv + systemd service running the API+GUI
#                        server (python -m app.api). Trading is started from
#                        the dashboard.
#
# Smart start: running ./start.sh with no arguments detects whether Salazar
# Trader is already installed (native and/or containerized). If it's already
# set up, it just STARTS it. If nothing is installed yet, it shows the install
# menu.
#
# Non-interactive:
#   ./start.sh                   # detect existing install and start it
#   ./start.sh docker            # containerized; build/pull only if needed
#   ./start.sh docker --pull     # containerized, force pull prebuilt from GHCR
#   ./start.sh docker --build    # containerized, force build images from source
#   ./start.sh native            # native; (re)install only if needed, then start
#   ./start.sh native --reinstall# native, force venv reinstall + unit refresh
#   ./start.sh install           # always show the install menu
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

API_PORT="${API_PORT:-8000}"
GUI_PORT="${GUI_PORT:-3000}"
SERVICE_NAME="salazar-trader"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
BACKEND_IMAGE="ghcr.io/salazj/salazar-trader:latest"
FRONTEND_IMAGE="ghcr.io/salazj/salazar-trader-frontend:latest"

# ── helpers ────────────────────────────────────────────────────────
c_blue()  { printf '\033[1;34m%s\033[0m\n' "$*"; }
c_green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
c_red()   { printf '\033[1;31m%s\033[0m\n' "$*"; }

require_env() {
  if [ ! -f .env ]; then
    c_red "ERROR: .env not found."
    echo  "  Run: cp .env.example .env   and fill in your keys."
    exit 1
  fi
}

local_ip() {
  # Best-effort LAN IP for printing a reachable GUI URL.
  if command -v hostname >/dev/null 2>&1 && hostname -I >/dev/null 2>&1; then
    hostname -I 2>/dev/null | awk '{print $1}'
  elif command -v ipconfig >/dev/null 2>&1; then
    ipconfig getifaddr en0 2>/dev/null || echo "localhost"
  else
    echo "localhost"
  fi
}

# ── install detection ──────────────────────────────────────────────
native_installed() {
  [ -f "$UNIT_PATH" ] && [ -x "${SCRIPT_DIR}/.venv/bin/python" ]
}

docker_available() { command -v docker >/dev/null 2>&1; }

docker_installed() {
  docker_available || return 1
  # Considered "installed" if our images exist, or any compose container exists.
  if docker image inspect "$BACKEND_IMAGE" >/dev/null 2>&1; then return 0; fi
  if [ -n "$(docker compose ps -aq 2>/dev/null)" ]; then return 0; fi
  return 1
}

compose_files() {
  # Echo the -f flags to use, adding the GPU override when the nvidia runtime
  # is registered. Pass "build" as $1 to also include the build override.
  local out="-f docker-compose.yml"
  if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"nvidia"'; then
    out="$out -f docker-compose.gpu.yml"
  fi
  if [ "${1:-}" = "build" ]; then
    out="$out -f docker-compose.build.yml"
  fi
  echo "$out"
}

# ── containerized path ─────────────────────────────────────────────
start_docker() {
  local mode="${1:-auto}"   # auto | pull | build
  require_env

  if ! docker_available; then
    c_red "ERROR: docker is not installed."
    exit 1
  fi

  if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"nvidia"'; then
    c_blue "==> NVIDIA runtime detected — enabling GPU for ollama."
  else
    c_blue "==> No NVIDIA runtime — ollama will run on CPU."
  fi

  local -a cf
  case "$mode" in
    build)
      read -r -a cf <<< "$(compose_files build)"
      c_blue "==> Building images from source..."
      docker compose "${cf[@]}" build
      ;;
    pull)
      read -r -a cf <<< "$(compose_files)"
      c_blue "==> Pulling prebuilt images from GHCR..."
      docker compose "${cf[@]}" pull backend frontend ollama || true
      ;;
    auto)
      read -r -a cf <<< "$(compose_files)"
      if docker image inspect "$BACKEND_IMAGE" >/dev/null 2>&1 \
         && docker image inspect "$FRONTEND_IMAGE" >/dev/null 2>&1; then
        c_blue "==> Images already present — starting without rebuild/pull."
      else
        c_blue "==> Images missing — pulling prebuilt from GHCR..."
        docker compose "${cf[@]}" pull backend frontend ollama || true
      fi
      ;;
  esac

  c_blue "==> Starting stack (ollama + backend + frontend)..."
  docker compose "${cf[@]}" up -d ollama
  c_blue "==> Ensuring LLM model (${OLLAMA_MODEL:-phi3}) is present..."
  docker compose "${cf[@]}" up ollama-pull || \
    c_red "    (model pull failed/skipped; you can retry later)"
  docker compose "${cf[@]}" up -d backend frontend

  print_docker_urls
}

print_docker_urls() {
  local ip; ip="$(local_ip)"
  echo ""
  c_green "============================================"
  c_green "  GUI:       http://${ip}:${GUI_PORT}"
  c_green "  API:       http://${ip}:${API_PORT}"
  c_green "============================================"
  echo ""
  echo "  Start/stop trading from the dashboard."
  echo "  Logs:  docker compose logs -f backend"
  echo "  Stop:  ./stop.sh"
}

# ── native path ────────────────────────────────────────────────────
ensure_venv() {
  if [ ! -d .venv ]; then
    c_blue "==> Creating virtualenv (.venv)..."
    python3 -m venv .venv
  fi
  c_blue "==> Installing the package into .venv..."
  ./.venv/bin/pip install --upgrade pip >/dev/null
  ./.venv/bin/pip install -e . >/dev/null
}

install_service() {
  local user; user="$(id -un)"

  c_blue "==> Installing systemd service (${SERVICE_NAME})..."
  # Render a unit from the current path/user so it works on any host.
  local tmp; tmp="$(mktemp)"
  cat > "$tmp" <<EOF
[Unit]
Description=Salazar Trader API + GUI (equities, Alpaca paper, local LLM)
After=network-online.target ollama.service
Wants=network-online.target ollama.service

[Service]
Type=simple
User=${user}
WorkingDirectory=${SCRIPT_DIR}
EnvironmentFile=${SCRIPT_DIR}/.env
ExecStart=${SCRIPT_DIR}/.venv/bin/python -m app.api
Restart=on-failure
RestartSec=10
TimeoutStartSec=60
KillSignal=SIGINT
StandardOutput=append:/var/log/salazar-trader.log
StandardError=append:/var/log/salazar-trader.log

[Install]
WantedBy=multi-user.target
EOF

  sudo cp "$tmp" "$UNIT_PATH"
  rm -f "$tmp"
  sudo touch /var/log/salazar-trader.log
  sudo chown "${user}:${user}" /var/log/salazar-trader.log 2>/dev/null || true
  sudo systemctl daemon-reload
  sudo systemctl enable --now "${SERVICE_NAME}"
}

start_native() {
  local mode="${1:-auto}"   # auto | reinstall
  require_env

  if [ "$(uname -s)" != "Linux" ] || ! command -v systemctl >/dev/null 2>&1; then
    c_red "Native (systemd) install requires Linux with systemd."
    echo  "On macOS, run the server directly for testing:"
    echo  "    .venv/bin/python -m app.api"
    echo  "Or use the containerized path: ./start.sh docker"
    exit 1
  fi

  if [ "$mode" != "reinstall" ] && native_installed; then
    c_blue "==> Existing native install detected — starting service..."
    sudo systemctl enable "${SERVICE_NAME}" >/dev/null 2>&1 || true
    sudo systemctl start "${SERVICE_NAME}"
  else
    ensure_venv
    install_service
  fi

  print_native_urls
}

print_native_urls() {
  local ip; ip="$(local_ip)"
  echo ""
  c_green "============================================"
  c_green "  GUI + API:  http://${ip}:${API_PORT}"
  c_green "============================================"
  echo ""
  echo "  Trading is started from the dashboard (no autostart)."
  echo "  After a reboot, open the dashboard and press Start."
  echo "  Logs:  ./scripts/logs.sh live    (or journalctl -u ${SERVICE_NAME} -f)"
  echo "  Stop:  ./stop.sh"
}

# ── smart no-arg start ─────────────────────────────────────────────
auto_start() {
  local have_native=false have_docker=false
  native_installed && have_native=true
  docker_installed && have_docker=true

  if $have_native && ! $have_docker; then
    c_green "Detected existing NATIVE install."
    start_native auto
  elif $have_docker && ! $have_native; then
    c_green "Detected existing CONTAINERIZED install."
    start_docker auto
  elif $have_native && $have_docker; then
    c_blue "Both native and containerized installs detected. Which to start?"
    echo "  1) Native        (systemd, http://<ip>:${API_PORT})"
    echo "  2) Containerized (docker,  http://<ip>:${GUI_PORT})"
    printf "Select [1/2]: "
    read -r choice
    case "$choice" in
      1) start_native auto ;;
      2) start_docker auto ;;
      *) c_red "Invalid choice."; exit 1 ;;
    esac
  else
    install_menu
  fi
}

# ── fresh-install menu ─────────────────────────────────────────────
install_menu() {
  c_blue "Salazar Trader — how do you want to install/run it?"
  echo "  1) Containerized (Docker: backend + frontend + ollama, with GUI)"
  echo "  2) Native        (systemd service running API+GUI on this host)"
  printf "Select [1/2]: "
  read -r choice
  case "$choice" in
    1)
      echo ""
      c_blue "Image source?"
      echo "  a) Pull prebuilt images from GHCR (fastest)"
      echo "  b) Build images from source (uses local Dockerfiles)"
      printf "Select [a/b]: "
      read -r src
      case "$src" in
        a|A) start_docker pull ;;
        b|B) start_docker build ;;
        *)   c_red "Invalid choice."; exit 1 ;;
      esac
      ;;
    2)
      start_native auto
      ;;
    *)
      c_red "Invalid choice."; exit 1 ;;
  esac
}

# ── arg dispatch ───────────────────────────────────────────────────
case "${1:-}" in
  docker|container|containerized)
    case "${2:-}" in
      --build) start_docker build ;;
      --pull)  start_docker pull ;;
      *)       start_docker auto ;;
    esac
    ;;
  native|systemd)
    if [ "${2:-}" = "--reinstall" ]; then start_native reinstall; else start_native auto; fi
    ;;
  ""|start)
    auto_start
    ;;
  install|menu)
    install_menu
    ;;
  -h|--help)
    sed -n '2,31p' "$0"
    ;;
  *)
    c_red "Unknown argument: $1"
    echo  "Usage: ./start.sh [docker [--build|--pull] | native [--reinstall] | install]"
    exit 1
    ;;
esac
