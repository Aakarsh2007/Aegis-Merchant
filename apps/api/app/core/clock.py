"""Injected clock.

Everything in RevPilot that reads the current time goes through a ``Clock``.

Why this exists (workflow.md §21):

Quiet hours (21:00-09:00 IST), approval TTLs, recovery windows, rolling
48-hour contact caps and the daily free-tier quota counter are *all*
time-dependent. Testing them by monkey-patching the global clock
(``freezegun`` and friends) fights with APScheduler and the asyncio event
loop, and produces slow, flaky tests. Injecting a clock instead makes every
window test deterministic, fast, and explicit about which instant it means.

``tests/test_no_wall_clock_reads.py`` enforces this: no module under
``apps/api/app`` may call ``datetime.now()``, ``datetime.utcnow()`` or
``time.time()`` except this one.

Note on IST: India Standard Time is UTC+05:30 and has never observed DST, so
a fixed offset is *exact*. Using a fixed offset rather than
``ZoneInfo("Asia/Kolkata")`` also avoids depending on the ``tzdata`` package,
which Windows requires because it ships no IANA time zone database.
"""

from __future__ import annotations

import time as _time
from datetime import UTC, datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

__all__ = ["IST", "Clock", "FakeClock", "SystemClock", "iso_ist", "to_ist"]

#: India Standard Time. UTC+05:30, no DST, ever.
IST = timezone(timedelta(hours=5, minutes=30), name="IST")


@runtime_checkable
class Clock(Protocol):
    """The only sanctioned source of 'now' in the application."""

    def now_utc(self) -> datetime:
        """Current instant as an aware UTC datetime."""
        ...

    def now_ist(self) -> datetime:
        """Current instant as an aware IST datetime."""
        ...

    def monotonic(self) -> float:
        """Monotonic seconds, for measuring durations (never for wall time)."""
        ...


class SystemClock:
    """Real time. The only place in the app allowed to read the wall clock."""

    __slots__ = ()

    def now_utc(self) -> datetime:
        return datetime.now(UTC)

    def now_ist(self) -> datetime:
        return self.now_utc().astimezone(IST)

    def monotonic(self) -> float:
        return _time.monotonic()


class FakeClock:
    """Deterministic clock for tests.

    Time only moves when a test moves it, which is what makes quiet-hours and
    window-expiry assertions exact rather than probabilistic.

        clock = FakeClock.at_ist(2026, 9, 1, 22, 30)   # inside quiet hours
        clock.advance(hours=11)                        # -> 09:30 IST, allowed
    """

    __slots__ = ("_mono", "_now")

    def __init__(self, now: datetime, monotonic_start: float = 0.0) -> None:
        if now.tzinfo is None:
            raise ValueError("FakeClock requires an aware datetime (got naive)")
        self._now = now.astimezone(UTC)
        self._mono = monotonic_start

    # -- constructors ------------------------------------------------------
    @classmethod
    def at_ist(
        cls,
        year: int,
        month: int,
        day: int,
        hour: int = 0,
        minute: int = 0,
        second: int = 0,
    ) -> FakeClock:
        return cls(datetime(year, month, day, hour, minute, second, tzinfo=IST))

    @classmethod
    def at_utc(
        cls,
        year: int,
        month: int,
        day: int,
        hour: int = 0,
        minute: int = 0,
        second: int = 0,
    ) -> FakeClock:
        return cls(datetime(year, month, day, hour, minute, second, tzinfo=UTC))

    # -- Clock protocol ----------------------------------------------------
    def now_utc(self) -> datetime:
        return self._now

    def now_ist(self) -> datetime:
        return self._now.astimezone(IST)

    def monotonic(self) -> float:
        return self._mono

    # -- test controls -----------------------------------------------------
    def advance(
        self,
        *,
        days: int = 0,
        hours: int = 0,
        minutes: int = 0,
        seconds: float = 0,
    ) -> FakeClock:
        delta = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        if delta.total_seconds() < 0:
            raise ValueError("FakeClock.advance cannot move time backwards")
        self._now += delta
        self._mono += delta.total_seconds()
        return self

    def set_ist(
        self,
        year: int,
        month: int,
        day: int,
        hour: int = 0,
        minute: int = 0,
        second: int = 0,
    ) -> FakeClock:
        """Jump to an absolute IST instant. Allowed to move backwards.

        Explicit parameters rather than ``*args``: unpacking positionally into
        ``datetime()`` can spill into the ``tzinfo`` slot and produce a
        duplicate-keyword error at runtime.
        """
        self._now = datetime(year, month, day, hour, minute, second, tzinfo=IST).astimezone(UTC)
        return self


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def to_ist(dt: datetime) -> datetime:
    """Convert an aware datetime to IST. Rejects naive datetimes loudly.

    A naive datetime in a payments system is a bug waiting to be a quiet-hours
    violation, so this raises rather than assuming a timezone.
    """
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime not allowed: {dt!r}")
    return dt.astimezone(IST)


def iso_ist(dt: datetime) -> str:
    """Canonical IST ISO-8601 string, used in logs and the audit chain."""
    return to_ist(dt).isoformat(timespec="milliseconds")
