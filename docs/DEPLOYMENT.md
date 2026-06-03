# Deployment Guide

## Local Development

### Prerequisites
- Docker and Docker Compose
- Node.js 20+ (for frontend development only)
- Python 3.9+ (for backend development only)

### One-Command Startup

```bash
docker compose up --build
```

This starts:
- **Backend** on port 8000 (FastAPI + bot manager)
- **Frontend** on port 3000 (React GUI via nginx)

Open `http://localhost:3000` in your browser.

### Development Mode (Hot Reload)

For backend development:
```bash
cd /path/to/salazar-trader
pip install -e ".[dev]"
uvicorn app.api.app:create_app --factory --reload --port 8000
```

For frontend development:
```bash
cd frontend
npm install
npm run dev
```

The Vite dev server proxies `/api/*` and `/ws/*` to `localhost:8000`.

## Cloud VM Deployment

### 1. Provision a VM

Any Linux VM with Docker installed (Ubuntu 22.04+ recommended).

### 2. Clone and Configure

```bash
git clone https://github.com/salazj/salazar-trader.git
cd salazar-trader
cp .env.example .env
nano .env  # Add your API keys
```

### 3. Launch

```bash
docker compose up -d --build
```

### 4. Access the GUI

Open `http://<your-vm-ip>:3000` in your browser.

### 5. Firewall

Ensure port 3000 is open for GUI access:
```bash
sudo ufw allow 3000/tcp
```

For production, consider:
- Using a reverse proxy (nginx/Caddy) with HTTPS
- Restricting access by IP
- Using a VPN

### HTTPS with Caddy (Optional)

```bash
sudo apt install caddy
```

Create `/etc/caddy/Caddyfile`:
```
your-domain.com {
    reverse_proxy localhost:3000
}
```

```bash
sudo systemctl restart caddy
```

## Standalone Bot (No GUI)

For headless operation without the web interface:

```bash
docker compose run --rm bot
```

Or directly:
```bash
docker run -d --name salazar-trader \
    --env-file .env \
    -v ./data:/app/data \
    -v ./logs:/app/logs \
    ghcr.io/salazj/salazar-trader:latest bot
```

## Environment Variables

All configuration is done through the `.env` file. See `.env.example` for all available options with documentation.

Key variables:
| Variable | Default | Description |
|----------|---------|-------------|
| `ASSET_CLASS` | `prediction_markets` | `prediction_markets` or `equities` |
| `EXCHANGE` | `polymarket` | `polymarket` |
| `BROKER` | `alpaca` | `alpaca` (for equities) |
| `DRY_RUN` | `true` | Simulate trades |
| `ENABLE_LIVE_TRADING` | `false` | Second safety gate |
| `LIVE_TRADING_ACKNOWLEDGED` | `false` | Third safety gate |

## Monitoring

- **Health check**: `GET http://localhost:8000/api/health`
- **Bot status**: `GET http://localhost:8000/api/status`
- **Logs**: WebSocket `ws://localhost:8000/ws/logs`
- **GUI**: `http://localhost:3000`
