"""NYSE market hours awareness."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    _NYSE_TZ: timezone | "ZoneInfo" = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - fallback if tzdata is unavailable
    # Approximate US Eastern; does NOT handle DST. Only used if zoneinfo fails.
    _NYSE_TZ = timezone(timedelta(hours=-5))

_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)
_PRE_MARKET_OPEN = time(4, 0)
_AFTER_HOURS_CLOSE = time(20, 0)


class MarketHoursManager:
    """Determines whether US equity markets are open."""

    def is_market_open(self) -> bool:
        """Check if regular trading hours are active (Mon-Fri 9:30-16:00 ET)."""
        now_et = self._now_et()
        if now_et.weekday() >= 5:
            return False
        return _MARKET_OPEN <= now_et.time() < _MARKET_CLOSE

    def is_extended_hours(self) -> bool:
        """Check if pre-market or after-hours trading is active."""
        now_et = self._now_et()
        if now_et.weekday() >= 5:
            return False
        t = now_et.time()
        return (
            (_PRE_MARKET_OPEN <= t < _MARKET_OPEN)
            or (_MARKET_CLOSE <= t < _AFTER_HOURS_CLOSE)
        )

    def next_market_open(self) -> datetime:
        now_et = self._now_et()
        candidate = now_et.replace(
            hour=_MARKET_OPEN.hour,
            minute=_MARKET_OPEN.minute,
            second=0,
            microsecond=0,
        )
        if candidate <= now_et:
            candidate += timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)

    def next_market_close(self) -> datetime:
        now_et = self._now_et()
        candidate = now_et.replace(
            hour=_MARKET_CLOSE.hour,
            minute=_MARKET_CLOSE.minute,
            second=0,
            microsecond=0,
        )
        if candidate <= now_et:
            candidate += timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)

    @staticmethod
    def _now_et() -> datetime:
        """Current time in US Eastern, DST-aware (America/New_York)."""
        return datetime.now(_NYSE_TZ)
