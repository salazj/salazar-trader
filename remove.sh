#!/usr/bin/env bash
set -euo pipefail

echo "==> Stopping and removing containers..."
docker stop salazar-backend salazar-frontend 2>/dev/null || true
docker rm   salazar-backend salazar-frontend 2>/dev/null || true

echo "==> Removing network..."
docker network rm salazar-net 2>/dev/null || true

echo "    Done. Containers and network removed."
echo ""

read -rp "Also remove the Docker images? [y/N] " answer
if [[ "${answer,,}" == "y" ]]; then
  echo "==> Removing images..."
  docker rmi salazj16/salazar-trader:latest 2>/dev/null || true
  docker rmi ghcr.io/salazj/salazar-trader-frontend:latest 2>/dev/null || true
  echo "    Images removed."
fi

echo "    Run ./start.sh to pull fresh images and start again."
