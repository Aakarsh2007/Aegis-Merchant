"""Rail-health tests.

Most of these are about small samples, because that is where a naive success
rate quietly produces nonsense that costs money: blacklisting a healthy rail
after two unlucky failures, or crowning a rail on the strength of one lucky
success.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.agent.rail_health import (
    MIN_SAMPLE_FOR_CONFIDENCE,
    AttemptRecord,
    RailHealthIndex,
    RailKey,
    RailStats,
    rails_from_rows,
    wilson_bounds,
)
from app.core.clock import FakeClock

NOW = FakeClock.at_ist(2026, 9, 1, 12, 0).now_utc()


def attempts(
    method: str, issuer: str | None, successes: int, failures: int, *, minutes_ago: int = 30
) -> list[AttemptRecord]:
    ts = NOW - timedelta(minutes=minutes_ago)
    return [AttemptRecord(method, issuer, True, ts) for _ in range(successes)] + [
        AttemptRecord(method, issuer, False, ts) for _ in range(failures)
    ]


class TestWilsonBounds:
    def test_no_trials_is_maximum_uncertainty(self) -> None:
        assert wilson_bounds(0, 0) == (0.0, 1.0)

    def test_zero_of_three_is_not_zero_percent(self) -> None:
        """The central point of using Wilson at all.

        The normal approximation gives exactly [0, 0] here -- asserting
        certainty from three data points. That would permanently blacklist a
        healthy rail that had an unlucky afternoon.
        """
        lower, upper = wilson_bounds(0, 3)
        assert lower == 0.0
        assert 0.4 < upper < 0.8

    def test_three_of_three_is_not_one_hundred_percent(self) -> None:
        lower, upper = wilson_bounds(3, 3)
        assert lower < 0.5
        assert upper == pytest.approx(1.0, abs=1e-9)

    def test_more_evidence_narrows_the_interval(self) -> None:
        small = wilson_bounds(9, 10)
        large = wilson_bounds(900, 1000)
        assert (large[1] - large[0]) < (small[1] - small[0])

    def test_converges_on_the_observed_rate(self) -> None:
        lower, upper = wilson_bounds(9000, 10000)
        assert 0.88 < lower < 0.9 < upper < 0.92

    def test_bounds_stay_inside_zero_and_one(self) -> None:
        for successes, trials in [(0, 1), (1, 1), (0, 100), (100, 100), (1, 2)]:
            lower, upper = wilson_bounds(successes, trials)
            assert 0.0 <= lower <= upper <= 1.0

    def test_impossible_input_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            wilson_bounds(5, 3)
        with pytest.raises(ValueError):
            wilson_bounds(-1, 3)


class TestRailStats:
    def test_score_penalises_a_small_sample(self) -> None:
        """One lucky success must not outrank forty solid ones."""
        lucky = RailStats(RailKey("upi", "A"), attempts=1, successes=1)
        proven = RailStats(RailKey("upi", "B"), attempts=40, successes=36)
        assert lucky.raw_rate > proven.raw_rate  # naive ranking would prefer 'lucky'
        assert proven.score > lucky.score  # evidence-weighted ranking does not

    def test_raw_rate_of_an_empty_rail_does_not_divide_by_zero(self) -> None:
        assert RailStats(RailKey("upi"), attempts=0, successes=0).raw_rate == 0.0

    def test_enough_data_threshold(self) -> None:
        below = RailStats(RailKey("upi"), MIN_SAMPLE_FOR_CONFIDENCE - 1, 3)
        at = RailStats(RailKey("upi"), MIN_SAMPLE_FOR_CONFIDENCE, 3)
        assert not below.has_enough_data
        assert at.has_enough_data

    def test_degraded_requires_confidence_not_just_a_low_rate(self) -> None:
        """A rail with 0/3 looks terrible and means nothing."""
        noisy = RailStats(RailKey("upi", "A"), attempts=3, successes=0)
        assert not noisy.is_degraded(baseline=0.8)

        evidenced = RailStats(RailKey("upi", "B"), attempts=60, successes=20)
        assert evidenced.is_degraded(baseline=0.8)


class TestIndexConstruction:
    def test_empty_index_has_a_neutral_baseline(self) -> None:
        """An empty index must not make every rail look broken."""
        index = RailHealthIndex.from_attempts([], now=NOW)
        assert len(index) == 0
        assert index.baseline == 0.5

    def test_attempts_outside_the_window_are_excluded(self) -> None:
        """Bank rails fail in bursts. A 30-day average hides today's outage."""
        old = attempts("upi", "HDFC", 50, 0, minutes_ago=60 * 48)
        recent = attempts("upi", "HDFC", 0, 10, minutes_ago=10)
        index = RailHealthIndex.from_attempts(old + recent, now=NOW, window=timedelta(hours=24))
        stats = index.get("upi", "HDFC")
        assert stats is not None
        assert stats.attempts == 10
        assert stats.successes == 0

    def test_future_attempts_are_excluded(self) -> None:
        """A clock skew or a bad backfill must not poison the index."""
        future = [AttemptRecord("upi", "HDFC", True, NOW + timedelta(hours=1))]
        index = RailHealthIndex.from_attempts(future, now=NOW)
        assert len(index) == 0

    def test_attempts_with_no_method_are_skipped(self) -> None:
        """An abandoned checkout never reached a rail. Counting it as a rail
        failure would defame every rail equally."""
        index = RailHealthIndex.from_attempts(
            [AttemptRecord(None, None, False, NOW - timedelta(minutes=5))], now=NOW
        )
        assert len(index) == 0

    def test_issuers_are_tracked_separately(self) -> None:
        index = RailHealthIndex.from_attempts(
            attempts("upi", "HDFC", 2, 18) + attempts("upi", "SBI", 19, 1), now=NOW
        )
        hdfc = index.get("upi", "HDFC")
        sbi = index.get("upi", "SBI")
        assert hdfc is not None and sbi is not None
        assert sbi.score > hdfc.score

    def test_rows_adapter_counts_only_reached_rails(self) -> None:
        rows = [
            ("upi", "HDFC", "captured", NOW),
            ("upi", "HDFC", "failed", NOW),
            ("upi", None, "abandoned", NOW),  # never reached a rail
            ("upi", "HDFC", "created", NOW),  # not an outcome yet
        ]
        records = rails_from_rows(rows)
        assert len(records) == 2
        assert sum(r.succeeded for r in records) == 1


class TestFallback:
    def test_unknown_issuer_falls_back_to_the_method(self) -> None:
        """During an issuer outage we may have plenty of evidence about UPI
        generally and almost none about that one bank in the last hour."""
        index = RailHealthIndex.from_attempts(
            attempts("upi", "HDFC", 18, 2) + attempts("upi", "SBI", 15, 5), now=NOW
        )
        merged = index.get("upi", "AXIS")
        assert merged is not None
        assert merged.attempts == 40
        assert merged.key.issuer is None

    def test_unknown_method_returns_none(self) -> None:
        index = RailHealthIndex.from_attempts(attempts("upi", "HDFC", 10, 0), now=NOW)
        assert index.get("carrier_billing") is None

    def test_no_data_is_not_degraded(self) -> None:
        """Absence of evidence is not evidence of failure."""
        index = RailHealthIndex.from_attempts(attempts("upi", "HDFC", 10, 0), now=NOW)
        assert not index.is_degraded("netbanking", "BOB")


class TestAlternativeSelection:
    def test_picks_the_healthier_rail(self) -> None:
        index = RailHealthIndex.from_attempts(
            attempts("upi", "HDFC", 4, 26) + attempts("card", "ICICI", 28, 2), now=NOW
        )
        alt = index.best_alternative(failed_method="upi", failed_issuer="HDFC")
        assert alt is not None
        assert alt.key == RailKey("card", "ICICI")

    def test_never_returns_the_rail_that_just_failed(self) -> None:
        index = RailHealthIndex.from_attempts(
            attempts("upi", "HDFC", 28, 2) + attempts("card", "ICICI", 4, 26), now=NOW
        )
        alt = index.best_alternative(failed_method="upi", failed_issuer="HDFC")
        assert alt is None or alt.key != RailKey("upi", "HDFC")

    def test_returns_none_when_nothing_is_confidently_better(self) -> None:
        """`None` is a real answer meaning 'reissue on the same rail'.

        Guessing here would spend one of only two attempts on a hunch, and
        churning rails on noise makes the recovery message harder to explain.
        """
        index = RailHealthIndex.from_attempts(
            attempts("upi", "HDFC", 15, 5) + attempts("card", "ICICI", 15, 5), now=NOW
        )
        assert index.best_alternative(failed_method="upi", failed_issuer="HDFC") is None

    def test_ignores_rails_with_too_little_evidence(self) -> None:
        index = RailHealthIndex.from_attempts(
            attempts("upi", "HDFC", 2, 28) + attempts("wallet", "PAYTM", 2, 0), now=NOW
        )
        assert index.best_alternative(failed_method="upi", failed_issuer="HDFC") is None

    def test_never_suggests_emandate_as_an_alternative(self) -> None:
        """A mandate rail is not a substitute for a one-off payment: it needs
        an authorisation that does not exist for this transaction."""
        index = RailHealthIndex.from_attempts(
            attempts("upi", "HDFC", 2, 28) + attempts("emandate", "HDFC", 30, 0), now=NOW
        )
        alt = index.best_alternative(failed_method="upi", failed_issuer="HDFC")
        assert alt is None or alt.key.method != "emandate"

    def test_returns_none_with_no_data_at_all(self) -> None:
        index = RailHealthIndex.from_attempts([], now=NOW)
        assert index.best_alternative(failed_method="upi", failed_issuer="HDFC") is None


class TestReporting:
    def test_ranked_is_ordered_by_score(self) -> None:
        index = RailHealthIndex.from_attempts(
            attempts("upi", "HDFC", 5, 15)
            + attempts("card", "ICICI", 18, 2)
            + attempts("netbanking", "SBI", 12, 8),
            now=NOW,
        )
        scores = [s.score for s in index.ranked()]
        assert scores == sorted(scores, reverse=True)

    def test_snapshot_exposes_the_sample_size(self) -> None:
        """A rate without an n is not a fact. The dashboard shows both."""
        index = RailHealthIndex.from_attempts(attempts("upi", "HDFC", 5, 15), now=NOW)
        row = index.snapshot()[0]
        assert row["attempts"] == 20
        assert "raw_rate" in row and "score" in row
        assert row["enough_data"] is True

    def test_baseline_is_the_overall_rate(self) -> None:
        index = RailHealthIndex.from_attempts(
            attempts("upi", "HDFC", 10, 10) + attempts("card", "ICICI", 20, 0), now=NOW
        )
        assert index.baseline == pytest.approx(30 / 40)


class TestAgainstTheSeededCorpus:
    """The index must work on the real committed corpus, not just fixtures."""

    async def test_computes_from_the_seeded_ledger(self, seeded_engine) -> None:  # type: ignore[no-untyped-def]
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.db.models import PaymentAttempt
        from app.db.seed import ANCHOR_IST

        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as session:
            rows = (
                await session.execute(
                    select(
                        PaymentAttempt.method,
                        PaymentAttempt.issuer,
                        PaymentAttempt.status,
                        PaymentAttempt.attempted_at,
                    )
                )
            ).all()

        records = rails_from_rows([(m, i, str(s), t) for m, i, s, t in rows])
        index = RailHealthIndex.from_attempts(records, now=ANCHOR_IST, window=timedelta(days=14))
        assert len(index) > 0
        assert 0.0 < index.baseline < 1.0
        # Every rail in the corpus is a real (method, issuer) pair.
        for stats in index.ranked():
            assert stats.attempts > 0
            assert 0.0 <= stats.score <= stats.raw_rate + 1e-9
