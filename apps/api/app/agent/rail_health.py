"""Rail health — rolling success rate per (method, issuer).

workflow.md §4.2 item 7 claims choosing the retry rail is "a statistics query,
not reasoning". This is that query. It is also where a naive implementation
quietly produces nonsense, in a way that would cost real money:

**A rail with 2 attempts and 0 successes is not a 0% rail.** It is a rail we
know nothing about. Ranking by raw success rate would send every recovery to
whichever rail happened to have one lucky success, and would blacklist a
perfectly healthy rail that had two unlucky ones. So rails are ranked by the
**Wilson score lower bound** at 95%: the pessimistic end of the confidence
interval, which is small when the sample is small and converges on the observed
rate as evidence accumulates. It is the standard answer to "rank things by a
success ratio when the sample sizes differ", and it needs no tuning parameter.

**"Degraded" must mean confident-worse, not merely low.** A rail is flagged
degraded only when its Wilson *upper* bound is below the baseline — i.e. even
the optimistic reading of the data says it is underperforming. Otherwise a
quiet rail gets declared broken on three data points.

**Recency matters more than volume.** Bank rails fail in bursts lasting hours.
A 30-day success rate would hide the outage we are currently living through, so
the window is short and configurable, and the caller states which instant
"now" is (the clock is injected — §21).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

__all__ = [
    "MIN_SAMPLE_FOR_CONFIDENCE",
    "AttemptRecord",
    "RailHealthIndex",
    "RailKey",
    "RailStats",
    "wilson_bounds",
]

#: Below this, a rail is reported as `has_enough_data = False`. Not a cutoff
#: for *use* -- the Wilson bound already handles small samples -- but the point
#: below which we refuse to make a *claim* about a rail in the UI.
MIN_SAMPLE_FOR_CONFIDENCE: Final = 8

#: 95% two-sided.
_Z: Final = 1.96


@dataclass(frozen=True)
class RailKey:
    """A payment rail. ``issuer`` may be absent — not every method has one."""

    method: str
    issuer: str | None = None

    def __str__(self) -> str:
        return f"{self.method}/{self.issuer}" if self.issuer else self.method


@dataclass(frozen=True)
class AttemptRecord:
    """One payment attempt. The unit of evidence."""

    method: str | None
    issuer: str | None
    succeeded: bool
    attempted_at: datetime


def wilson_bounds(successes: int, trials: int, z: float = _Z) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Chosen over the normal approximation because that one is actively wrong at
    the extremes this system lives in: with 0 successes in 3 trials it produces
    an interval of exactly [0, 0], asserting certainty from three data points.
    Wilson gives roughly [0, 0.56] — correctly uncertain.
    """
    if trials <= 0:
        return 0.0, 1.0
    if successes < 0 or successes > trials:
        raise ValueError(f"successes={successes} out of range for trials={trials}")

    p = successes / trials
    z2 = z * z
    denom = 1 + z2 / trials
    centre = p + z2 / (2 * trials)
    margin = z * math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials))
    lower = (centre - margin) / denom
    upper = (centre + margin) / denom
    return max(0.0, lower), min(1.0, upper)


@dataclass(frozen=True)
class RailStats:
    key: RailKey
    attempts: int
    successes: int

    @property
    def failures(self) -> int:
        return self.attempts - self.successes

    @property
    def raw_rate(self) -> float:
        """Observed rate. Shown to humans; never used for ranking."""
        return self.successes / self.attempts if self.attempts else 0.0

    @property
    def bounds(self) -> tuple[float, float]:
        return wilson_bounds(self.successes, self.attempts)

    @property
    def score(self) -> float:
        """Ranking score: the pessimistic end of the interval.

        A rail must *earn* its ranking with evidence. One lucky success does
        not beat forty solid ones.
        """
        return self.bounds[0]

    @property
    def has_enough_data(self) -> bool:
        return self.attempts >= MIN_SAMPLE_FOR_CONFIDENCE

    def is_degraded(self, baseline: float) -> bool:
        """Confidently worse than baseline, not merely unlucky."""
        return self.has_enough_data and self.bounds[1] < baseline


class RailHealthIndex:
    """Success rates per rail over a recent window.

    Built from our own attempt ledger. No external call, no model, no cost —
    which is the point: this was one of the nine places an LLM was rejected.
    """

    def __init__(
        self,
        stats: dict[RailKey, RailStats],
        *,
        window: timedelta,
        computed_at: datetime,
        total_attempts: int,
        total_successes: int,
    ) -> None:
        self._stats = stats
        self.window = window
        self.computed_at = computed_at
        self.total_attempts = total_attempts
        self.total_successes = total_successes

    # -- construction ------------------------------------------------------
    @classmethod
    def from_attempts(
        cls,
        attempts: Iterable[AttemptRecord],
        *,
        now: datetime,
        window: timedelta = timedelta(hours=24),
    ) -> RailHealthIndex:
        """Aggregate attempts inside ``[now - window, now]``.

        Attempts with no method are skipped rather than bucketed under a
        placeholder: an abandoned checkout never reached a rail, so counting it
        as a rail failure would defame every rail equally.
        """
        cutoff = now - window
        buckets: dict[RailKey, list[int]] = {}
        total = success_total = 0

        for record in attempts:
            if record.method is None:
                continue
            if record.attempted_at < cutoff or record.attempted_at > now:
                continue
            key = RailKey(str(record.method), str(record.issuer) if record.issuer else None)
            bucket = buckets.setdefault(key, [0, 0])
            bucket[0] += 1
            total += 1
            if record.succeeded:
                bucket[1] += 1
                success_total += 1

        stats = {
            key: RailStats(key=key, attempts=count, successes=wins)
            for key, (count, wins) in buckets.items()
        }
        return cls(
            stats,
            window=window,
            computed_at=now,
            total_attempts=total,
            total_successes=success_total,
        )

    # -- queries -----------------------------------------------------------
    @property
    def baseline(self) -> float:
        """Overall success rate across every rail in the window.

        The reference point for "degraded". Falls back to a neutral 0.5 with no
        data, so an empty index cannot make every rail look broken.
        """
        if self.total_attempts == 0:
            return 0.5
        return self.total_successes / self.total_attempts

    def get(self, method: str, issuer: str | None = None) -> RailStats | None:
        """Exact rail, then the method as a whole. ``None`` means no evidence.

        Falling back from (upi, HDFC) to all-of-upi matters during an issuer
        outage: we may have plenty of evidence about UPI generally and almost
        none about that one bank in the last hour.
        """
        exact = self._stats.get(RailKey(method, issuer))
        if exact is not None:
            return exact
        if issuer is None:
            return None

        merged_attempts = merged_successes = 0
        for key, stats in self._stats.items():
            if key.method == method:
                merged_attempts += stats.attempts
                merged_successes += stats.successes
        if merged_attempts == 0:
            return None
        return RailStats(
            key=RailKey(method, None), attempts=merged_attempts, successes=merged_successes
        )

    def is_degraded(self, method: str, issuer: str | None = None) -> bool:
        stats = self.get(method, issuer)
        return stats.is_degraded(self.baseline) if stats else False

    def ranked(self, *, exclude: RailKey | None = None) -> list[RailStats]:
        """Rails best-first by Wilson lower bound."""
        candidates = [s for k, s in self._stats.items() if exclude is None or k != exclude]
        return sorted(candidates, key=lambda s: (-s.score, -s.attempts, str(s.key)))

    def best_alternative(
        self,
        *,
        failed_method: str,
        failed_issuer: str | None = None,
        min_attempts: int = MIN_SAMPLE_FOR_CONFIDENCE,
    ) -> RailStats | None:
        """The healthiest rail that is *not* the one that just failed.

        Returns ``None`` when there is no defensible alternative — no evidence,
        or nothing measurably better. ``None`` is a real answer: it means
        "reissue on the same rail", not "pick something at random". Guessing
        here would spend one of only two attempts on a hunch.
        """
        failed = RailKey(failed_method, failed_issuer)
        candidates = [
            s
            for k, s in self._stats.items()
            if k != failed and s.attempts >= min_attempts and k.method != "emandate"
        ]
        if not candidates:
            return None

        best = max(candidates, key=lambda s: (s.score, s.attempts))
        failed_stats = self.get(failed_method, failed_issuer)

        # Only switch if the alternative is *confidently* better: its
        # pessimistic bound must beat the failed rail's optimistic one.
        # Churning rails on noise makes the recovery message harder to explain
        # and gains nothing.
        if (
            failed_stats is not None
            and failed_stats.attempts >= min_attempts
            and best.score <= failed_stats.bounds[1]
        ):
            return None
        return best

    def snapshot(self, limit: int = 10) -> list[dict[str, object]]:
        """Serialisable view for the dashboard and the decision trace."""
        return [
            {
                "rail": str(s.key),
                "attempts": s.attempts,
                "successes": s.successes,
                "raw_rate": round(s.raw_rate, 4),
                "score": round(s.score, 4),
                "lower": round(s.bounds[0], 4),
                "upper": round(s.bounds[1], 4),
                "enough_data": s.has_enough_data,
                "degraded": s.is_degraded(self.baseline),
            }
            for s in self.ranked()[:limit]
        ]

    def __len__(self) -> int:
        return len(self._stats)

    def __repr__(self) -> str:
        return (
            f"RailHealthIndex(rails={len(self._stats)}, attempts={self.total_attempts}, "
            f"baseline={self.baseline:.3f}, window={self.window})"
        )


def rails_from_rows(
    rows: Sequence[tuple[str | None, str | None, str, datetime]],
) -> list[AttemptRecord]:
    """Adapt ``(method, issuer, status, attempted_at)`` rows to attempts.

    Only ``captured`` counts as success. ``abandoned`` is deliberately *not* a
    rail failure — the customer never reached a rail, so blaming one would
    corrupt every rate in the index.
    """
    return [
        AttemptRecord(
            method=method,
            issuer=issuer,
            succeeded=status == "captured",
            attempted_at=attempted_at,
        )
        for method, issuer, status, attempted_at in rows
        if status in ("captured", "failed")
    ]
