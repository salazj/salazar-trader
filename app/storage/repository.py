"""
SQLite Repository Layer

Persists all system data: markets, events, features, signals, orders,
fills, positions, and PnL snapshots.

Schema is explicit and created on first run. Abstracted behind an async
interface for future migration to Postgres or other backends.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from app.config.settings import Settings
from app.data.models import Market, MarketFeatures, Order, Signal
from app.monitoring import get_logger

logger = get_logger(__name__)


def _to_iso(value: Any) -> str | None:
    """Safely convert a datetime or string to an ISO-format string."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS markets (
    condition_id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    slug TEXT,
    tokens_json TEXT,
    end_date TEXT,
    active INTEGER DEFAULT 1,
    minimum_order_size REAL DEFAULT 1.0,
    minimum_tick_size REAL DEFAULT 0.01,
    exchange TEXT DEFAULT 'polymarket',
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    token_id TEXT,
    exchange TEXT DEFAULT 'polymarket',
    payload TEXT NOT NULL,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    exchange TEXT DEFAULT 'polymarket',
    timestamp TEXT NOT NULL,
    data_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name TEXT NOT NULL,
    market_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    exchange TEXT DEFAULT 'polymarket',
    action TEXT NOT NULL,
    confidence REAL,
    suggested_price REAL,
    suggested_size REAL,
    rationale TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    exchange TEXT DEFAULT 'polymarket',
    side TEXT NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    filled_size REAL DEFAULT 0,
    status TEXT NOT NULL,
    exchange_order_id TEXT,
    signal_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    pnl REAL DEFAULT 0,
    filled_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_fills_order_id ON fills (order_id);

CREATE TABLE IF NOT EXISTS positions (
    token_id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    token_side TEXT NOT NULL,
    exchange TEXT DEFAULT 'polymarket',
    size REAL DEFAULT 0,
    avg_entry_price REAL DEFAULT 0,
    realized_pnl REAL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pnl_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    cash REAL NOT NULL,
    total_exposure REAL NOT NULL,
    total_unrealized_pnl REAL NOT NULL,
    total_realized_pnl REAL NOT NULL,
    daily_pnl REAL NOT NULL,
    positions_json TEXT
);

CREATE TABLE IF NOT EXISTS nlp_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    source TEXT NOT NULL,
    text TEXT NOT NULL,
    url TEXT DEFAULT '',
    content_hash TEXT NOT NULL,
    normalized_text TEXT,
    event_type TEXT,
    sentiment TEXT,
    sentiment_score REAL DEFAULT 0,
    urgency REAL DEFAULT 0,
    relevance REAL DEFAULT 0,
    confidence REAL DEFAULT 0,
    entities_json TEXT DEFAULT '[]',
    rationale TEXT DEFAULT '',
    received_at TEXT NOT NULL,
    processed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nlp_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_text_id TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    market_id TEXT NOT NULL,
    sentiment TEXT NOT NULL,
    sentiment_score REAL DEFAULT 0,
    event_type TEXT,
    urgency REAL DEFAULT 0,
    relevance REAL DEFAULT 0,
    confidence REAL DEFAULT 0,
    rationale TEXT DEFAULT '',
    entities_json TEXT DEFAULT '[]',
    text_snippet TEXT DEFAULT '',
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_events_token ON raw_events(token_id);
CREATE INDEX IF NOT EXISTS idx_raw_events_type ON raw_events(event_type);
CREATE INDEX IF NOT EXISTS idx_features_token ON features(token_id);
CREATE INDEX IF NOT EXISTS idx_signals_market ON signals(market_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_id);
CREATE INDEX IF NOT EXISTS idx_nlp_events_item ON nlp_events(item_id);
CREATE INDEX IF NOT EXISTS idx_nlp_events_hash ON nlp_events(content_hash);
CREATE INDEX IF NOT EXISTS idx_nlp_events_type ON nlp_events(event_type);
CREATE INDEX IF NOT EXISTS idx_nlp_signals_market ON nlp_signals(market_id);
CREATE INDEX IF NOT EXISTS idx_nlp_signals_source ON nlp_signals(source_text_id);
"""


class Repository:
    """Async SQLite repository for all persistent data.

    High-frequency writes (raw_events, features) are buffered and flushed
    periodically to amortise SQLite transaction overhead. Call flush() to
    force a write, or rely on flush_if_needed() which auto-flushes when the
    buffer exceeds ``max_buffer_size`` rows.
    """

    def __init__(self, db_path: str | Path, max_buffer_size: int = 200) -> None:
        if isinstance(db_path, str) and db_path.startswith("sqlite:///"):
            db_path = db_path.replace("sqlite:///", "")
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None
        self._max_buffer_size = max_buffer_size
        self._event_buffer: list[tuple[str, str, str, str]] = []
        self._feature_buffer: list[tuple[str, str, str, str]] = []

    async def initialize(self) -> None:
        """Open connection and create schema if needed."""
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()
        logger.info("database_initialized", path=self._db_path)

    async def flush(self) -> None:
        """Flush all buffered writes to disk in a single transaction."""
        assert self._db is not None
        if not self._event_buffer and not self._feature_buffer:
            return
        async with self._db.cursor() as cur:
            if self._event_buffer:
                await cur.executemany(
                    "INSERT INTO raw_events (event_type, token_id, payload, received_at) "
                    "VALUES (?, ?, ?, ?)",
                    self._event_buffer,
                )
                flushed_events = len(self._event_buffer)
                self._event_buffer.clear()
            else:
                flushed_events = 0
            if self._feature_buffer:
                await cur.executemany(
                    "INSERT INTO features (market_id, token_id, timestamp, data_json) "
                    "VALUES (?, ?, ?, ?)",
                    self._feature_buffer,
                )
                flushed_features = len(self._feature_buffer)
                self._feature_buffer.clear()
            else:
                flushed_features = 0
        await self._db.commit()
        if flushed_events + flushed_features > 0:
            logger.debug(
                "buffer_flushed",
                events=flushed_events,
                features=flushed_features,
            )

    async def _flush_if_needed(self) -> None:
        total = len(self._event_buffer) + len(self._feature_buffer)
        if total >= self._max_buffer_size:
            await self.flush()

    async def close(self) -> None:
        if self._db:
            await self.flush()
            await self._db.close()

    # ── Markets ────────────────────────────────────────────────────────

    async def save_market(self, market: Market) -> None:
        assert self._db is not None
        await self._db.execute(
            """INSERT OR REPLACE INTO markets
               (condition_id, question, slug, tokens_json, end_date, active,
                minimum_order_size, minimum_tick_size, exchange, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                market.condition_id or market.market_id,
                market.question,
                market.slug,
                json.dumps([t.model_dump() for t in market.tokens]),
                _to_iso(market.end_date),
                int(market.active),
                market.minimum_order_size,
                market.minimum_tick_size,
                market.exchange,
                _to_iso(datetime.utcnow()),
            ),
        )
        await self._db.commit()

    async def get_markets(self, active_only: bool = True) -> list[dict]:
        assert self._db is not None
        query = "SELECT * FROM markets"
        if active_only:
            query += " WHERE active = 1"
        cursor = await self._db.execute(query)
        rows = await cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    # ── Raw Events ─────────────────────────────────────────────────────

    async def save_raw_event(self, event_type: str, token_id: str, payload: dict) -> None:
        """Buffer a raw event for batch write."""
        self._event_buffer.append(
            (event_type, token_id, json.dumps(payload), _to_iso(datetime.utcnow()))
        )
        await self._flush_if_needed()

    async def get_raw_events(
        self, token_id: str | None = None, event_type: str | None = None, limit: int = 1000
    ) -> list[dict]:
        assert self._db is not None
        query = "SELECT * FROM raw_events WHERE 1=1"
        params: list[Any] = []
        if token_id:
            query += " AND token_id = ?"
            params.append(token_id)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    # ── Features ───────────────────────────────────────────────────────

    async def save_features(self, features: MarketFeatures) -> None:
        """Buffer a feature snapshot for batch write."""
        self._feature_buffer.append((
            features.market_id,
            features.token_id,
            _to_iso(features.timestamp),
            features.model_dump_json(),
        ))
        await self._flush_if_needed()

    async def get_features(self, token_id: str, limit: int = 1000) -> list[dict]:
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT * FROM features WHERE token_id = ? ORDER BY id DESC LIMIT ?",
            (token_id, limit),
        )
        rows = await cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    # ── Signals ────────────────────────────────────────────────────────

    async def save_signal(self, signal: Signal) -> None:
        assert self._db is not None
        await self._db.execute(
            """INSERT INTO signals
               (strategy_name, market_id, token_id, exchange, action,
                confidence, suggested_price, suggested_size, rationale, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                signal.strategy_name,
                signal.market_id,
                signal.instrument_id or signal.token_id,
                signal.exchange,
                signal.action.value,
                signal.confidence,
                signal.suggested_price,
                signal.suggested_size,
                signal.rationale,
                _to_iso(signal.timestamp),
            ),
        )
        await self._db.commit()

    # ── Orders ─────────────────────────────────────────────────────────

    async def save_order(self, order: Order) -> None:
        assert self._db is not None
        await self._db.execute(
            """INSERT OR REPLACE INTO orders
               (order_id, market_id, token_id, exchange, side, price, size,
                filled_size, status, exchange_order_id, signal_id,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order.order_id,
                order.market_id,
                order.instrument_id or order.token_id,
                order.exchange,
                order.side.value,
                order.price,
                order.size,
                order.filled_size,
                order.status.value,
                order.exchange_order_id,
                order.signal_id,
                _to_iso(order.created_at),
                _to_iso(order.updated_at),
            ),
        )
        await self._db.commit()

    async def get_orders(self, status: str | None = None, limit: int = 100) -> list[dict]:
        assert self._db is not None
        query = "SELECT * FROM orders"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    # ── Fills ──────────────────────────────────────────────────────────

    async def save_fill(
        self, order_id: str, price: float, size: float, pnl: float, timestamp: str | None = None,
    ) -> None:
        assert self._db is not None
        filled_at = timestamp if timestamp else _to_iso(datetime.utcnow())
        await self._db.execute(
            "INSERT OR IGNORE INTO fills (order_id, price, size, pnl, filled_at) VALUES (?, ?, ?, ?, ?)",
            (order_id, price, size, pnl, filled_at),
        )
        await self._db.commit()

    # ── Positions ──────────────────────────────────────────────────────

    async def save_position(self, position: Any) -> None:
        """Upsert a position (keyed by token_id)."""
        assert self._db is not None
        from app.data.models import Position

        p: Position = position
        await self._db.execute(
            """INSERT OR REPLACE INTO positions
               (token_id, market_id, token_side, exchange, size,
                avg_entry_price, realized_pnl, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                p.instrument_id or p.token_id,
                p.market_id,
                p.token_side.value,
                p.exchange,
                p.size,
                p.avg_entry_price,
                p.realized_pnl,
                _to_iso(p.updated_at),
            ),
        )
        await self._db.commit()

    async def save_all_positions(self, positions: list[Any]) -> None:
        """Batch-save all positions (used on shutdown)."""
        for pos in positions:
            await self.save_position(pos)

    async def delete_position(self, token_id: str) -> None:
        assert self._db is not None
        await self._db.execute("DELETE FROM positions WHERE token_id = ?", (token_id,))
        await self._db.commit()

    async def load_positions(self) -> list[dict]:
        """Load all saved positions for recovery on startup."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT token_id, market_id, token_side, size, avg_entry_price, "
            "realized_pnl, updated_at FROM positions WHERE size > 0"
        )
        rows = await cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    # ── PnL Snapshots ─────────────────────────────────────────────────

    async def save_pnl_snapshot(
        self,
        cash: float,
        total_exposure: float,
        total_unrealized: float,
        total_realized: float,
        daily_pnl: float,
        positions_json: str = "[]",
    ) -> None:
        assert self._db is not None
        await self._db.execute(
            """INSERT INTO pnl_snapshots
               (timestamp, cash, total_exposure, total_unrealized_pnl,
                total_realized_pnl, daily_pnl, positions_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                _to_iso(datetime.utcnow()),
                cash,
                total_exposure,
                total_unrealized,
                total_realized,
                daily_pnl,
                positions_json,
            ),
        )
        await self._db.commit()

    async def get_pnl_history(self, limit: int = 500) -> list[dict]:
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT * FROM pnl_snapshots ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    # ── NLP Events ────────────────────────────────────────────────────

    async def save_nlp_event(
        self,
        item_id: str,
        source: str,
        text: str,
        content_hash: str,
        normalized_text: str = "",
        url: str = "",
        event_type: str = "",
        sentiment: str = "",
        sentiment_score: float = 0.0,
        urgency: float = 0.0,
        relevance: float = 0.0,
        confidence: float = 0.0,
        entities: list[str] | None = None,
        rationale: str = "",
    ) -> None:
        assert self._db is not None
        await self._db.execute(
            """INSERT INTO nlp_events
               (item_id, source, text, url, content_hash, normalized_text,
                event_type, sentiment, sentiment_score, urgency, relevance,
                confidence, entities_json, rationale, received_at, processed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item_id, source, text, url, content_hash, normalized_text,
                event_type, sentiment, sentiment_score, urgency, relevance,
                confidence, json.dumps(entities or []), rationale,
                _to_iso(datetime.utcnow()), _to_iso(datetime.utcnow()),
            ),
        )
        await self._db.commit()

    async def get_nlp_events(
        self,
        event_type: str | None = None,
        source: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """Retrieve stored NLP events for replay or analysis."""
        assert self._db is not None
        query = "SELECT * FROM nlp_events WHERE 1=1"
        params: list[Any] = []
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        if source:
            query += " AND source = ?"
            params.append(source)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    # ── NLP Signals ───────────────────────────────────────────────────

    async def save_nlp_signal(
        self,
        source_text_id: str,
        source_provider: str,
        market_id: str,
        sentiment: str,
        sentiment_score: float = 0.0,
        event_type: str = "",
        urgency: float = 0.0,
        relevance: float = 0.0,
        confidence: float = 0.0,
        rationale: str = "",
        entities: list[str] | None = None,
        text_snippet: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        assert self._db is not None
        await self._db.execute(
            """INSERT INTO nlp_signals
               (source_text_id, source_provider, market_id, sentiment,
                sentiment_score, event_type, urgency, relevance, confidence,
                rationale, entities_json, text_snippet, metadata_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source_text_id, source_provider, market_id, sentiment,
                sentiment_score, event_type, urgency, relevance, confidence,
                rationale, json.dumps(entities or []), text_snippet,
                json.dumps(metadata or {}), _to_iso(datetime.utcnow()),
            ),
        )
        await self._db.commit()

    async def get_nlp_signals(
        self,
        market_id: str | None = None,
        source_text_id: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        assert self._db is not None
        query = "SELECT * FROM nlp_signals WHERE 1=1"
        params: list[Any] = []
        if market_id:
            query += " AND market_id = ?"
            params.append(market_id)
        if source_text_id:
            query += " AND source_text_id = ?"
            params.append(source_text_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
