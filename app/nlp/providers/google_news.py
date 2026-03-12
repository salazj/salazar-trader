"""
Google News RSS provider — free, no API key required.

Fetches headlines from Google News RSS for prediction-market-relevant
topics (politics, economics, sports, crypto, legal, geopolitics).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from urllib.parse import quote

from app.monitoring import get_logger
from app.news.models import NewsItem
from app.nlp.providers.base import BaseNlpProvider

logger = get_logger(__name__)

_GNEWS_RSS = "https://news.google.com/rss/search"

_SEARCH_QUERIES = [
    "election OR president OR congress OR senate",
    "federal reserve OR inflation OR GDP OR recession",
    "bitcoin OR crypto OR SEC regulation",
    "NBA OR NFL OR MLB OR FIFA OR championship",
    "court ruling OR indictment OR verdict OR lawsuit",
    "NATO OR war OR sanctions OR diplomacy",
]

_MAX_ENTRIES_PER_QUERY = 10
_TIMEOUT = 15.0


class GoogleNewsProvider(BaseNlpProvider):
    """Fetches headlines from Google News RSS (no API key needed)."""

    name: str = "google_news"  # type: ignore[assignment]

    def __init__(self) -> None:
        self._seen: set[str] = set()

    async def fetch_items(self) -> list[NewsItem]:
        try:
            import feedparser  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("google_news_missing_feedparser")
            return []

        items: list[NewsItem] = []

        for query in _SEARCH_QUERIES:
            url = f"{_GNEWS_RSS}?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:_MAX_ENTRIES_PER_QUERY]:
                    title = getattr(entry, "title", "").strip()
                    if not title:
                        continue

                    content_hash = hashlib.sha256(title.encode()).hexdigest()[:16]
                    if content_hash in self._seen:
                        continue
                    self._seen.add(content_hash)

                    published = getattr(entry, "published", "")
                    try:
                        ts = datetime.strptime(
                            published, "%a, %d %b %Y %H:%M:%S %Z"
                        ).replace(tzinfo=timezone.utc)
                    except (ValueError, AttributeError):
                        ts = datetime.now(timezone.utc)

                    items.append(
                        NewsItem(
                            item_id=f"gnews-{content_hash}",
                            source="google_news",
                            text=title,
                            url=getattr(entry, "link", ""),
                            timestamp=ts,
                            raw_metadata={"query": query},
                        )
                    )
            except Exception:
                logger.exception("google_news_feed_error", query=query)

        logger.info("google_news_fetched", new=len(items))
        return items

    def is_available(self) -> bool:
        try:
            import feedparser  # noqa: F401  # type: ignore[import-untyped]
            return True
        except ImportError:
            return False
