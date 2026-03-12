"""
NewsAPI.org provider — fetches real headlines from 80k+ sources.

Requires a NEWSAPI_KEY in .env (free tier: 100 req/day).
Queries prediction-market-relevant topics: politics, economics,
crypto, sports, legal, and geopolitics.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import httpx

from app.monitoring import get_logger
from app.news.models import NewsItem
from app.nlp.providers.base import BaseNlpProvider

logger = get_logger(__name__)

_BASE_URL = "https://newsapi.org/v2/everything"

_QUERY_KEYWORDS = (
    "(election OR vote OR poll OR congress OR senate OR president)"
    " OR (federal reserve OR inflation OR GDP OR unemployment OR recession)"
    " OR (bitcoin OR crypto OR ethereum OR SEC OR regulation)"
    " OR (championship OR playoff OR FIFA OR NBA OR NFL OR MLB)"
    " OR (court ruling OR lawsuit OR indictment OR verdict)"
    " OR (NATO OR war OR sanctions OR treaty OR diplomacy)"
)

_PAGE_SIZE = 30
_TIMEOUT = 15.0


class NewsApiProvider(BaseNlpProvider):
    """Fetches headlines from NewsAPI.org."""

    name: str = "newsapi"  # type: ignore[assignment]

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._seen: set[str] = set()

    async def fetch_items(self) -> list[NewsItem]:
        params = {
            "q": _QUERY_KEYWORDS,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": _PAGE_SIZE,
            "apiKey": self._api_key,
        }

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(_BASE_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("newsapi_http_error", status=exc.response.status_code)
            return []
        except Exception as exc:
            logger.warning("newsapi_fetch_error", error=str(exc))
            return []

        articles = data.get("articles") or []
        items: list[NewsItem] = []

        for article in articles:
            title = (article.get("title") or "").strip()
            description = (article.get("description") or "").strip()
            text = f"{title}. {description}" if description else title
            if not text or text == "[Removed].":
                continue

            content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
            if content_hash in self._seen:
                continue
            self._seen.add(content_hash)

            published = article.get("publishedAt") or ""
            try:
                ts = datetime.fromisoformat(published.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                ts = datetime.now(timezone.utc)

            items.append(
                NewsItem(
                    item_id=f"newsapi-{content_hash}",
                    source=article.get("source", {}).get("name", "newsapi"),
                    text=text,
                    url=article.get("url") or "",
                    timestamp=ts,
                    raw_metadata={
                        "author": article.get("author"),
                        "source_id": article.get("source", {}).get("id"),
                    },
                )
            )

        logger.info("newsapi_fetched", total=len(articles), new=len(items))
        return items

    def is_available(self) -> bool:
        return bool(self._api_key)
