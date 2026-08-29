"""Free-tier quota control.

On a free tier the scarce resource is **requests, not rupees** (workflow.md
§4.6), so the budget is a quota budget and it is enforced in code rather than
hoped for.

Two limits, two mechanisms, for a reason:

* **Per minute** — an in-process token bucket. Requests *queue* rather than
  fail, because a burst of webhook arrivals is normal and rejecting them would
  turn a rate limit into lost recoveries.
* **Per day** — a counter that must be **persisted**. An in-memory daily count
  resets on every restart, and a process that restarts a few times would sail
  past a daily allowance while believing it had barely started. That is exactly
  the class of bug the outbox exists to prevent one layer down, and it applies
  here too.

The day boundary is **IST**, not UTC. A quota that rolled over at 05:30 IST
would reset in the middle of the Indian evening, which is when the merchant's
traffic actually is.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime

from app.core.clock import Clock, to_ist

__all__ = ["InMemoryQuotaStore", "QuotaState", "QuotaStore", "RateLimiter"]


class QuotaStore:
    """Where the daily count lives. Implemented against SQLite in Phase 7."""

    async def get(self, day: date) -> int:  # pragma: no cover - interface
        raise NotImplementedError

    async def increment(self, day: date) -> int:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class InMemoryQuotaStore(QuotaStore):
    """Non-durable store.

    Correct for tests and for a single short-lived process. **Not** correct for
    a long-running deployment -- see the module docstring. The class name says
    so out loud rather than leaving a caller to discover it.
    """

    counts: dict[date, int] = field(default_factory=dict)

    async def get(self, day: date) -> int:
        return self.counts.get(day, 0)

    async def increment(self, day: date) -> int:
        self.counts[day] = self.counts.get(day, 0) + 1
        return self.counts[day]


@dataclass(frozen=True)
class QuotaState:
    day: date
    used_today: int
    daily_limit: int

    @property
    def remaining(self) -> int:
        return max(0, self.daily_limit - self.used_today)

    @property
    def exhausted(self) -> bool:
        return self.used_today >= self.daily_limit


class RateLimiter:
    """Token bucket for RPM, persisted counter for RPD."""

    def __init__(
        self,
        *,
        clock: Clock,
        rpm_limit: int,
        rpd_limit: int,
        store: QuotaStore | None = None,
    ) -> None:
        if rpm_limit < 1 or rpd_limit < 1:
            raise ValueError("rate limits must be at least 1")
        self._clock = clock
        self._rpm = rpm_limit
        self._rpd = rpd_limit
        self._store = store or InMemoryQuotaStore()
        self._tokens = float(rpm_limit)
        self._last_refill = clock.monotonic()
        self._lock = asyncio.Lock()

    # -- helpers -----------------------------------------------------------
    def _today(self) -> date:
        """The IST calendar day. A UTC day would roll at 05:30 IST."""
        return to_ist(self._clock.now_utc()).date()

    def _refill(self) -> None:
        now = self._clock.monotonic()
        elapsed = max(0.0, now - self._last_refill)
        self._last_refill = now
        self._tokens = min(float(self._rpm), self._tokens + elapsed * (self._rpm / 60.0))

    # -- api ---------------------------------------------------------------
    async def state(self) -> QuotaState:
        day = self._today()
        return QuotaState(day=day, used_today=await self._store.get(day), daily_limit=self._rpd)

    async def try_acquire(self) -> tuple[bool, str]:
        """Take one request slot if the budget allows.

        Returns ``(allowed, reason)``. Never sleeps and never raises: the caller
        decides whether to wait or degrade, because those are different
        decisions in a webhook handler and in a batch warm-up.
        """
        async with self._lock:
            day = self._today()
            used = await self._store.get(day)
            if used >= self._rpd:
                return False, f"daily quota exhausted ({used}/{self._rpd} on {day.isoformat()})"

            self._refill()
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) * (60.0 / self._rpm)
                return False, f"per-minute limit reached; {wait:.1f}s until the next slot"

            self._tokens -= 1.0
            await self._store.increment(day)
            return True, "ok"

    def seconds_until_slot(self) -> float:
        """How long until a token is available. For the batch warm-up pacer."""
        self._refill()
        if self._tokens >= 1.0:
            return 0.0
        return (1.0 - self._tokens) * (60.0 / self._rpm)

    async def acquire(self, *, max_wait_s: float) -> tuple[bool, str]:
        """Wait for a slot, up to a bound.

        Used by ``warm-cache``, which is allowed to be slow. Never used on the
        webhook path, where waiting a minute for a token would be worse than
        answering deterministically.
        """
        waited = 0.0
        while True:
            allowed, reason = await self.try_acquire()
            if allowed:
                return True, "ok"
            if "daily quota" in reason:
                return False, reason
            delay = min(self.seconds_until_slot() + 0.05, max_wait_s - waited)
            if delay <= 0:
                return False, f"{reason} (waited {waited:.1f}s)"
            await asyncio.sleep(delay)
            waited += delay


def utc_day_would_differ(moment: datetime) -> bool:
    """True when the IST and UTC calendar dates disagree at this instant.

    Exists so a test can assert the quota rolls on the IST boundary rather
    than the UTC one -- between 18:30 and 24:00 UTC they are different days.
    """
    return to_ist(moment).date() != moment.date()
