FROM python:3.11-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 botuser \
    && useradd --uid 1000 --gid botuser --shell /bin/bash --create-home botuser

WORKDIR /app

COPY pyproject.toml .
COPY app/ app/
COPY scripts/ scripts/
RUN pip install --no-cache-dir "." \
    && rm -rf /app/app/

COPY docker/entrypoint.sh /entrypoint.sh
COPY .env.example .env.example
RUN chmod +x /entrypoint.sh

RUN mkdir -p data logs model_artifacts reports \
    && chown -R botuser:botuser /app

ENV PROJECT_ROOT=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DRY_RUN=true \
    ENABLE_LIVE_TRADING=false \
    LIVE_TRADING_ACKNOWLEDGED=false \
    BOT_MODE=dry-run \
    HEALTH_PORT=8880

EXPOSE ${HEALTH_PORT}

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -sf http://127.0.0.1:${HEALTH_PORT}/health || exit 1

USER botuser

ENTRYPOINT ["/entrypoint.sh"]
CMD ["bot"]
