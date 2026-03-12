#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "==> Stopping compose services..."
docker compose down 2>/dev/null || true

echo "==> Cleaning up standalone containers (if any)..."
docker stop salazar-backend salazar-frontend 2>/dev/null || true
docker rm   salazar-backend salazar-frontend 2>/dev/null || true
docker network rm salazar-net 2>/dev/null || true

echo "    Done. Run ./start.sh to rebuild and restart."
