#!/usr/bin/env bash
# Setup script for the Salazar-Trader Jetson Orin Nano build.
#
# Tested on JetPack 6 (Ubuntu 22.04 / 24.04 base) and NVIDIA L4T images.
# Run from the repo root:
#
#     bash scripts/setup_jetson.sh
#
# What it does:
#   1. Verifies you're on a Jetson (warns otherwise).
#   2. Installs system dependencies (python venv, build tools, sqlite, git LFS).
#   3. Creates a Python venv under .venv and installs project requirements.
#   4. Optionally installs llama-cpp-python with CUDA support.
#   5. Optionally pulls Ollama and the recommended Qwen2.5-3B model.
#   6. Sets the Jetson power mode to MAXN (max performance) if available.
#   7. Prints next-step instructions.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[setup_jetson]${NC} $*"; }
warn() { echo -e "${YELLOW}[setup_jetson]${NC} $*"; }
err()  { echo -e "${RED}[setup_jetson]${NC} $*" >&2; }

# --- Detect Jetson ----------------------------------------------------------

if [ -f /etc/nv_tegra_release ]; then
  log "Detected Jetson platform:"
  head -n 1 /etc/nv_tegra_release || true
else
  warn "This does not appear to be a Jetson device. Continuing — but the GPU"
  warn "and TensorRT optimizations will be unavailable on non-Jetson hosts."
fi

# --- System dependencies ----------------------------------------------------

log "Installing apt packages (sudo required) …"
sudo apt-get update
sudo apt-get install -y \
  build-essential \
  cmake \
  git \
  git-lfs \
  pkg-config \
  python3 \
  python3-venv \
  python3-pip \
  libssl-dev \
  libffi-dev \
  sqlite3 \
  libsqlite3-dev \
  htop \
  nvme-cli || true

# --- Python venv ------------------------------------------------------------

if [ ! -d .venv ]; then
  log "Creating Python venv (.venv) …"
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
log "Upgrading pip / wheel / setuptools …"
pip install --upgrade pip wheel setuptools

log "Installing project (with dev extras) …"
pip install -e ".[dev]"

# --- Optional: llama-cpp-python with CUDA -----------------------------------

read -r -p "Install llama-cpp-python with CUDA for Jetson? [y/N] " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
  log "Installing llama-cpp-python (CUDA build). This may take several minutes."
  CMAKE_ARGS="-DGGML_CUDA=on" pip install --no-cache-dir llama-cpp-python || {
    warn "CUDA build failed — falling back to CPU-only build."
    pip install --no-cache-dir llama-cpp-python
  }
fi

# --- Optional: Ollama -------------------------------------------------------

read -r -p "Install Ollama and pull Qwen2.5-3B? [y/N] " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
  if ! command -v ollama >/dev/null 2>&1; then
    log "Installing Ollama …"
    curl -fsSL https://ollama.com/install.sh | sh
  fi
  log "Pulling qwen2.5:3b-instruct-q4_K_M (small, fast on Jetson) …"
  ollama pull qwen2.5:3b-instruct-q4_K_M || warn "ollama pull failed"
fi

# --- Performance tuning -----------------------------------------------------

if command -v nvpmodel >/dev/null 2>&1; then
  log "Setting Jetson power mode to MAXN (mode 0). Sudo required."
  sudo nvpmodel -m 0 || warn "could not set nvpmodel"
fi
if command -v jetson_clocks >/dev/null 2>&1; then
  log "Locking clocks to max with jetson_clocks …"
  sudo jetson_clocks || warn "could not run jetson_clocks"
fi

# --- Reports / models dirs --------------------------------------------------

mkdir -p models reports model_artifacts data/bars

log "Setup complete!"
echo
echo "Next steps:"
echo "  1. Copy .env.example → .env and fill ALPACA_API_KEY / SECRET."
echo "  2. (Optional) Download a GGUF model into ./models, e.g.:"
echo "       huggingface-cli download Qwen/Qwen2.5-3B-Instruct-GGUF \\"
echo "         qwen2.5-3b-instruct-q4_k_m.gguf --local-dir models"
echo "  3. Activate the venv:    source .venv/bin/activate"
echo "  4. Run the API:          python -m app.api"
echo "  5. Smoke-test backtest:  python scripts/backtest_stock_strategy.py \\"
echo "                             --strategy stock_momentum --tickers SPY \\"
echo "                             --synthetic"
echo "  6. Open the dashboard at http://<jetson-ip>:3000"
