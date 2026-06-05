"""TTL cache for local LLM verdicts.

Cache key = ``ticker + sha1(news) + provider + model_name``.
Default TTL is 30 minutes — repeated evaluations of the same ticker and
news context within that window return the cached verdict.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass

from app.llm.schema import LLMVerdict


@dataclass
class _CacheEntry:
    expires_at: float
    verdict: LLMVerdict


class LLMResponseCache:
    """In-memory TTL cache for ``LLMVerdict`` objects."""

    def __init__(self, default_ttl_seconds: int = 1800, max_entries: int = 1024) -> None:
        self._ttl = default_ttl_seconds
        self._max = max_entries
        self._store: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def make_key(*, ticker: str, news: str, provider: str, model_name: str) -> str:
        digest = hashlib.sha1((news or "").encode("utf-8")).hexdigest()
        return f"{ticker.upper()}|{digest}|{provider}|{model_name}"

    def get(self, key: str) -> LLMVerdict | None:
        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if entry is None or entry.expires_at < now:
                if entry is not None:
                    self._store.pop(key, None)
                self._misses += 1
                return None
            self._hits += 1
            return entry.verdict

    def put(
        self,
        key: str,
        verdict: LLMVerdict,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        with self._lock:
            if len(self._store) >= self._max:
                oldest = min(self._store.items(), key=lambda kv: kv[1].expires_at)[0]
                self._store.pop(oldest, None)
            self._store[key] = _CacheEntry(
                expires_at=time.time() + ttl, verdict=verdict
            )

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._store),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": (self._hits / total) if total else 0.0,
            }

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._hits = 0
            self._misses = 0
