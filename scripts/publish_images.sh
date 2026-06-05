#!/usr/bin/env bash
#
# Build and push Salazar Trader images to GitHub Container Registry (GHCR)
# for linux/arm64 (Jetson / Apple Silicon target).
#
# Prerequisites:
#   - Docker with buildx (Docker Desktop has it; on Linux: docker buildx).
#   - A GitHub Personal Access Token (classic) with `write:packages` scope.
#
# Usage:
#   export GHCR_USER=salazj
#   export GHCR_TOKEN=ghp_xxxxxxxx        # token with write:packages
#   ./scripts/publish_images.sh           # builds + pushes :latest
#   TAG=v3.0.0 ./scripts/publish_images.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

GHCR_USER="${GHCR_USER:-salazj}"
TAG="${TAG:-latest}"
PLATFORM="${PLATFORM:-linux/arm64}"

BACKEND_IMAGE="ghcr.io/${GHCR_USER}/salazar-trader:${TAG}"
FRONTEND_IMAGE="ghcr.io/${GHCR_USER}/salazar-trader-frontend:${TAG}"

c_blue()  { printf '\033[1;34m%s\033[0m\n' "$*"; }
c_red()   { printf '\033[1;31m%s\033[0m\n' "$*"; }

if ! command -v docker >/dev/null 2>&1; then
  c_red "ERROR: docker is not installed."
  exit 1
fi

if ! docker buildx version >/dev/null 2>&1; then
  c_red "ERROR: docker buildx is required for cross-platform builds."
  exit 1
fi

# ── login ──────────────────────────────────────────────────────────
if [ -n "${GHCR_TOKEN:-}" ]; then
  c_blue "==> Logging in to ghcr.io as ${GHCR_USER}..."
  echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
else
  c_blue "==> GHCR_TOKEN not set; assuming you are already 'docker login ghcr.io'."
fi

# ── ensure a buildx builder exists ─────────────────────────────────
if ! docker buildx inspect salazar-builder >/dev/null 2>&1; then
  c_blue "==> Creating buildx builder 'salazar-builder'..."
  docker buildx create --name salazar-builder --use >/dev/null
else
  docker buildx use salazar-builder
fi

# ── build + push backend ───────────────────────────────────────────
c_blue "==> Building + pushing backend (${PLATFORM}): ${BACKEND_IMAGE}"
docker buildx build \
  --platform "$PLATFORM" \
  -t "$BACKEND_IMAGE" \
  --push \
  .

# ── build + push frontend ──────────────────────────────────────────
c_blue "==> Building + pushing frontend (${PLATFORM}): ${FRONTEND_IMAGE}"
docker buildx build \
  --platform "$PLATFORM" \
  -t "$FRONTEND_IMAGE" \
  --push \
  ./frontend

echo ""
c_blue "Done. Published:"
echo "  ${BACKEND_IMAGE}"
echo "  ${FRONTEND_IMAGE}"
