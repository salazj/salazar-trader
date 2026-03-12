"""
Finnhub news provider — free tier covers financial/market news.

Free tier: 60 calls/minute. Uses the general news endpoint.
Requires FINNHUB_API_KEY in .env.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import httpx

from app.monitoring import get_logger
from app.news.models import NewsItem
from app.nlp.providers.base import BaseNlpProvider

logger = get_logger(__name__)

_BASE_URL = "https://finnhub.io/api/v1/news"
_TIMEOUT = 15.0


class FinnhubProvider(BaseNlpProvider):
    """Fetches general news from Finnhub free tier."""

    name: str = "finnhub"  # type: ignore[assignment]

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._seen: set[str] = set()

    async def fetch_items(self) -> list[NewsItem]:
        params = {
            "category": "general",
            "token": self._api_key,
        }

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(_BASE_URL, params=params)
                resp.raise_for_status()
                articles = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("finnhub_http_error", status=exc.response.status_code)
            return []
        except Exception as exc:
            logger.warning("finnhub_fetch_error", error=str(exc))
            return []

        if not isinstance(articles, list):
            logger.warning("finnhub_unexpected_response")
            return []

        items: list[NewsItem] = []

        for article in articles:
            headline = (article.get("headline") or "").strip()
            summary = (article.get("summary") or "").strip()
            text = f"{headline}. {summary}" if summary else headline
            if not text:
                continue

            content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
            if content_hash in self._seen:
                continue
            self._seen.add(content_hash)

            epoch = article.get("datetime", 0)
            try:
                ts = datetime.fromtimestamp(epoch, tz=timezone.utc)
            except (ValueError, OSError, OverflowError):
                ts = datetime.now(timezone.utc)

            items.append(
                NewsItem(
                    item_id=f"finnhub-{content_hash}",
                    source=article.get("source", "finnhub"),
                    text=text,
                    url=article.get("url", ""),
                    timestamp=ts,
                    raw_metadata={
                        "category": article.get("category"),
                        "related": article.get("related"),
                    },
                )
            )

        logger.info("finnhub_fetched", new=len(items))
        return items

    def is_available(self) -> bool:
        return bool(self._api_key)
