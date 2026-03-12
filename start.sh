#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f .env ]; then
  echo "ERROR: .env not found. Run: cp .env.example .env  and fill in your keys."
  exit 1
fi

echo "==> Cleaning up old containers (if any)..."
docker stop salazar-backend salazar-frontend 2>/dev/null || true
docker rm   salazar-backend salazar-frontend 2>/dev/null || true
docker network rm salazar-net 2>/dev/null || true

echo "==> Building images from source..."
docker compose -f docker-compose.yml -f docker-compose.build.yml build

echo "==> Starting backend + frontend..."
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d backend frontend

echo ""
echo "============================================"
echo "  Backend:   http://localhost:8000"
echo "  Frontend:  http://localhost:3000"
echo "============================================"
echo ""
echo "==> Tailing backend logs (Ctrl+C to detach)..."
docker compose logs -f backend
