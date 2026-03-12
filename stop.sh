#!/usr/bin/env bash
set -euo pipefail

echo "==> Stopping containers..."
docker stop salazar-backend salazar-frontend 2>/dev/null || true
echo "    Containers stopped."
echo "    Run ./start.sh to restart, or ./remove.sh to remove them entirely."
