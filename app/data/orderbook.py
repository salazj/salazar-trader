"""
Orderbook Manager

Maintains in-memory orderbook snapshots per instrument.
Supports both full snapshot replacement and incremental delta updates.
"""

from __future__ import annotations

from typing import Any

from app.data.models import OrderbookSnapshot, PriceLevel
from app.monitoring import get_logger
from app.utils.helpers import utc_now

logger = get_logger(__name__)


class OrderbookManager:
    """Thread-safe orderbook state per instrument."""

    def __init__(self) -> None:
        self._books: dict[str, OrderbookSnapshot] = {}

    def apply_snapshot(
        self,
        market_id: str = "",
        instrument_id: str = "",
        bids: list[Any] | None = None,
        asks: list[Any] | None = None,
        *,
        token_id: str = "",
    ) -> None:
        iid = instrument_id or token_id
        parsed_bids = _parse_levels(bids or [])
        parsed_asks = _parse_levels(asks or [])
        parsed_bids.sort(key=lambda l: l.price, reverse=True)
        parsed_asks.sort(key=lambda l: l.price)
        self._books[iid] = OrderbookSnapshot(
            market_id=market_id,
            token_id=iid,
            instrument_id=iid,
            bids=parsed_bids,
            asks=parsed_asks,
            timestamp=utc_now(),
        )

    def apply_delta(
        self,
        instrument_id: str = "",
        bid_updates: list[Any] | None = None,
        ask_updates: list[Any] | None = None,
        *,
        token_id: str = "",
    ) -> None:
        iid = instrument_id or token_id
        book = self._books.get(iid)
        if book is None:
            return

        if bid_updates:
            bid_map = {l.price: l.size for l in book.bids}
            for upd in _parse_levels(bid_updates):
                if upd.size <= 0:
                    bid_map.pop(upd.price, None)
                else:
                    bid_map[upd.price] = upd.size
            book.bids = sorted(
                [PriceLevel(price=p, size=s) for p, s in bid_map.items()],
                key=lambda l: l.price, reverse=True,
            )

        if ask_updates:
            ask_map = {l.price: l.size for l in book.asks}
            for upd in _parse_levels(ask_updates):
                if upd.size <= 0:
                    ask_map.pop(upd.price, None)
                else:
                    ask_map[upd.price] = upd.size
            book.asks = sorted(
                [PriceLevel(price=p, size=s) for p, s in ask_map.items()],
                key=lambda l: l.price,
            )

        book.timestamp = utc_now()

    def get_snapshot(self, instrument_id: str) -> OrderbookSnapshot | None:
        book = self._books.get(instrument_id)
        if book is None:
            return None
        return book.model_copy(deep=True)

    def remove(self, instrument_id: str) -> None:
        self._books.pop(instrument_id, None)

    @property
    def instruments(self) -> list[str]:
        return list(self._books.keys())

    def get_all_instrument_ids(self) -> list[str]:
        return list(self._books.keys())


def _parse_levels(raw: list[Any]) -> list[PriceLevel]:
    levels: list[PriceLevel] = []
    for item in raw:
        if isinstance(item, PriceLevel):
            levels.append(item)
        elif isinstance(item, dict):
            levels.append(
                PriceLevel(
                    price=float(item.get("price", item.get("p", 0))),
                    size=float(item.get("size", item.get("s", item.get("quantity", 0)))),
                )
            )
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            levels.append(PriceLevel(price=float(item[0]), size=float(item[1])))
    return levels
