"""Tests for the Alpaca streaming subscription path.

Regression guard for a deadlock: calling alpaca-py's live ``subscribe_*`` /
``unsubscribe_*`` on an already-running websocket from the event-loop thread
freezes the whole bot. The client must instead bounce the socket (stop_ws) so
the reconnect loop rebuilds with the updated symbol set.
"""

from __future__ import annotations

import pytest

from app.brokers.alpaca.streaming import AlpacaStreaming
from app.config.settings import Settings


class _FakeStream:
    def __init__(self) -> None:
        self.stopped = 0
        self.live_sub_calls = 0

    # These would deadlock on a live socket — they must never be called while running.
    def subscribe_bars(self, *a, **k) -> None:
        self.live_sub_calls += 1

    def unsubscribe_bars(self, *a, **k) -> None:
        self.live_sub_calls += 1

    async def stop_ws(self) -> None:
        self.stopped += 1


def _streaming() -> AlpacaStreaming:
    return AlpacaStreaming(Settings())


@pytest.mark.asyncio
async def test_pre_connect_subscribe_only_populates_pending() -> None:
    s = _streaming()
    await s.subscribe_bars(["SPY", "QQQ"])
    assert s._pending_bars == ["SPY", "QQQ"]


@pytest.mark.asyncio
async def test_live_subscription_change_bounces_socket_not_live_subscribe() -> None:
    s = _streaming()
    await s.subscribe_bars(["SPY", "QQQ"])
    fake = _FakeStream()
    s._stream = fake
    s._connected = True

    await s.subscribe_bars(["NVDA"])        # add → bounce
    await s.unsubscribe_bars(["QQQ"])       # drop → bounce
    await s.subscribe_bars(["NVDA"])        # already pending → no-op, no bounce

    assert fake.live_sub_calls == 0          # never touch the deadlock-prone path
    assert fake.stopped == 2                 # exactly one bounce per real change
    assert s._pending_bars == ["SPY", "NVDA"]


@pytest.mark.asyncio
async def test_noop_change_does_not_bounce() -> None:
    s = _streaming()
    await s.subscribe_bars(["SPY"])
    fake = _FakeStream()
    s._stream = fake
    s._connected = True
    await s.subscribe_bars(["SPY"])          # already subscribed
    await s.unsubscribe_bars(["TSLA"])       # not subscribed
    assert fake.stopped == 0
