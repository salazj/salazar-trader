#!/usr/bin/env bash
set -euo pipefail

BACKEND_IMAGE="salazj16/salazar-trader:latest"
FRONTEND_IMAGE="ghcr.io/salazj/salazar-trader-frontend:latest"
NETWORK="salazar-net"
ENV_FILE=".env"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_PATH="$SCRIPT_DIR/$ENV_FILE"

if [ ! -f "$ENV_PATH" ]; then
  echo "ERROR: $ENV_PATH not found. Copy .env.example to .env and fill in your keys."
  exit 1
fi

echo "==> Pulling latest images..."
docker pull "$BACKEND_IMAGE"
docker pull "$FRONTEND_IMAGE"

echo "==> Stopping old containers (if any)..."
docker stop salazar-backend salazar-frontend 2>/dev/null || true
docker rm   salazar-backend salazar-frontend 2>/dev/null || true

echo "==> Creating network '$NETWORK' (if needed)..."
docker network create "$NETWORK" 2>/dev/null || true

echo "==> Starting backend..."
docker run -d \
  --name salazar-backend \
  --network "$NETWORK" \
  --network-alias backend \
  --env-file "$ENV_PATH" \
  --restart unless-stopped \
  -p 8000:8000 \
  "$BACKEND_IMAGE"

echo "==> Waiting for backend health check..."
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/api/health >/dev/null 2>&1; then
    echo "    Backend is healthy."
    break
  fi
  sleep 1
done

echo "==> Starting frontend..."
docker run -d \
  --name salazar-frontend \
  --network "$NETWORK" \
  --restart unless-stopped \
  -p 3000:80 \
  "$FRONTEND_IMAGE"

echo ""
echo "============================================"
echo "  Backend:   http://localhost:8000"
echo "  Frontend:  http://localhost:3000"
echo "============================================"
echo ""
echo "==> Tailing backend logs (Ctrl+C to detach)..."
docker logs -f salazar-backend
