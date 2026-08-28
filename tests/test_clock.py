"""Clock tests.

Quiet hours, TTLs and recovery windows all depend on these semantics being
exact, so they are pinned here rather than assumed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.clock import IST, Clock, FakeClock, SystemClock, iso_ist, to_ist


class TestIST:
    def test_ist_is_utc_plus_530(self) -> None:
        assert IST.utcoffset(None) == timedelta(hours=5, minutes=30)

    def test_ist_has_no_dst(self) -> None:
        """India has never observed DST, which is why a fixed offset is exact."""
        january = datetime(2026, 1, 15, 12, 0, tzinfo=IST)
        july = datetime(2026, 7, 15, 12, 0, tzinfo=IST)
        assert january.utcoffset() == july.utcoffset()


class TestSystemClock:
    def test_satisfies_protocol(self) -> None:
        assert isinstance(SystemClock(), Clock)

    def test_now_utc_is_aware(self) -> None:
        assert SystemClock().now_utc().tzinfo is not None

    def test_now_ist_is_aware_and_ist(self) -> None:
        now = SystemClock().now_ist()
        assert now.utcoffset() == timedelta(hours=5, minutes=30)

    def test_utc_and_ist_are_the_same_instant(self) -> None:
        clock = SystemClock()
        utc, ist = clock.now_utc(), clock.now_ist()
        assert abs((utc - ist).total_seconds()) < 1.0

    def test_monotonic_never_decreases(self) -> None:
        clock = SystemClock()
        assert clock.monotonic() <= clock.monotonic()


class TestFakeClock:
    def test_satisfies_protocol(self) -> None:
        assert isinstance(FakeClock.at_ist(2026, 9, 1), Clock)

    def test_time_does_not_move_on_its_own(self) -> None:
        """The whole point: an unadvanced FakeClock is frozen."""
        clock = FakeClock.at_ist(2026, 9, 1, 14, 30)
        first = clock.now_ist()
        for _ in range(1000):
            pass
        assert clock.now_ist() == first

    def test_at_ist_roundtrips(self) -> None:
        clock = FakeClock.at_ist(2026, 9, 1, 22, 30)
        ist = clock.now_ist()
        assert (ist.year, ist.month, ist.day, ist.hour, ist.minute) == (2026, 9, 1, 22, 30)

    def test_at_ist_converts_to_correct_utc(self) -> None:
        # 22:30 IST == 17:00 UTC same day
        clock = FakeClock.at_ist(2026, 9, 1, 22, 30)
        utc = clock.now_utc()
        assert (utc.hour, utc.minute) == (17, 0)
        assert utc.day == 1

    def test_ist_date_rolls_before_utc_date(self) -> None:
        """00:30 IST on the 2nd is 19:00 UTC on the 1st.

        This is exactly the boundary that breaks naive 'today' queries for the
        daily action budget and the quota counter.
        """
        clock = FakeClock.at_ist(2026, 9, 2, 0, 30)
        assert clock.now_ist().day == 2
        assert clock.now_utc().day == 1
        assert clock.now_utc().hour == 19

    def test_advance_moves_both_wall_and_monotonic(self) -> None:
        clock = FakeClock.at_ist(2026, 9, 1, 20, 0)
        before_mono = clock.monotonic()
        clock.advance(hours=2)
        assert clock.now_ist().hour == 22
        assert clock.monotonic() == before_mono + 7200

    def test_advance_across_quiet_hours_boundary(self) -> None:
        """21:00-09:00 IST is the quiet window; this is the release path."""
        clock = FakeClock.at_ist(2026, 9, 1, 22, 30)
        assert clock.now_ist().hour >= 21  # in quiet hours
        clock.advance(hours=10, minutes=35)
        released = clock.now_ist()
        assert (released.day, released.hour, released.minute) == (2, 9, 5)

    def test_advance_rejects_negative(self) -> None:
        clock = FakeClock.at_ist(2026, 9, 1)
        with pytest.raises(ValueError, match="backwards"):
            clock.advance(hours=-1)

    def test_advance_is_chainable(self) -> None:
        clock = FakeClock.at_ist(2026, 9, 1, 0, 0).advance(days=1).advance(hours=6)
        assert (clock.now_ist().day, clock.now_ist().hour) == (2, 6)

    def test_set_ist_may_move_backwards(self) -> None:
        clock = FakeClock.at_ist(2026, 9, 10)
        clock.set_ist(2026, 9, 1)
        assert clock.now_ist().day == 1

    def test_rejects_naive_datetime(self) -> None:
        with pytest.raises(ValueError, match="aware"):
            FakeClock(datetime(2026, 9, 1, 12, 0))

    def test_accepts_utc_construction(self) -> None:
        clock = FakeClock.at_utc(2026, 9, 1, 17, 0)
        assert clock.now_ist().hour == 22
        assert clock.now_ist().minute == 30


class TestHelpers:
    def test_to_ist_converts_from_utc(self) -> None:
        utc = datetime(2026, 9, 1, 17, 0, tzinfo=UTC)
        assert to_ist(utc).hour == 22

    def test_to_ist_rejects_naive(self) -> None:
        """A naive datetime is a latent quiet-hours violation, so it raises."""
        with pytest.raises(ValueError, match="naive"):
            to_ist(datetime(2026, 9, 1, 12, 0))

    def test_to_ist_is_idempotent(self) -> None:
        ist = datetime(2026, 9, 1, 22, 30, tzinfo=IST)
        assert to_ist(to_ist(ist)) == ist

    def test_iso_ist_format_carries_offset_and_millis(self) -> None:
        s = iso_ist(datetime(2026, 9, 1, 22, 30, 15, 123456, tzinfo=IST))
        assert s == "2026-09-01T22:30:15.123+05:30"

    def test_iso_ist_of_utc_input_renders_in_ist(self) -> None:
        s = iso_ist(datetime(2026, 9, 1, 17, 0, tzinfo=UTC))
        assert s.startswith("2026-09-01T22:30:00")
        assert s.endswith("+05:30")
