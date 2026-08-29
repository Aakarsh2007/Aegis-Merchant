"""Stopping-rule tests.

Two things get proven here. The individual rules behave correctly at their
boundaries — and boundaries are where these fail, since every one of them is a
comparison against a clock or a counter. And the *system* terminates: no case,
under any combination of inputs, can run forever.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.clock import FakeClock, to_ist
from app.db.enums import CaseStatus, MessageClass, StoppingRule
from app.guardrails.stopping_rules import (
    Decision,
    PolicyLimits,
    StoppingContext,
    apply_degradations,
    evaluate,
    in_quiet_hours,
    next_quiet_hours_release,
)

POLICY = PolicyLimits()


def ctx(**overrides: object) -> StoppingContext:
    """A context that PROCEEDs, so each test changes exactly one thing."""
    now = FakeClock.at_ist(2026, 9, 1, 11, 30).now_utc()  # a quiet weekday morning
    base = {
        "now_utc": now,
        "policy": POLICY,
        "window_expires_at": now + timedelta(hours=24),
        "order_status": "created",
    }
    base.update(overrides)
    return StoppingContext(**base)  # type: ignore[arg-type]


# ===========================================================================
# Quiet hours -- the window wraps midnight, which is where this goes wrong
# ===========================================================================
class TestQuietHours:
    @pytest.mark.parametrize("hour", [21, 22, 23, 0, 3, 8])
    def test_inside_the_wrapping_window(self, hour: int) -> None:
        """A naive `start <= h < end` reports 23:00 as allowed and messages
        someone at 11 PM."""
        moment = FakeClock.at_ist(2026, 9, 1, hour, 0).now_utc()
        assert in_quiet_hours(moment, start_ist=21, end_ist=9)

    @pytest.mark.parametrize("hour", [9, 10, 14, 18, 20])
    def test_outside_the_window(self, hour: int) -> None:
        moment = FakeClock.at_ist(2026, 9, 1, hour, 0).now_utc()
        assert not in_quiet_hours(moment, start_ist=21, end_ist=9)

    def test_boundaries_are_inclusive_of_start_exclusive_of_end(self) -> None:
        assert in_quiet_hours(
            FakeClock.at_ist(2026, 9, 1, 21, 0).now_utc(), start_ist=21, end_ist=9
        )
        assert not in_quiet_hours(
            FakeClock.at_ist(2026, 9, 1, 9, 0).now_utc(), start_ist=21, end_ist=9
        )

    def test_non_wrapping_window_also_works(self) -> None:
        """Config is merchant-editable, so a same-day window must not break."""
        moment = FakeClock.at_ist(2026, 9, 1, 13, 0).now_utc()
        assert in_quiet_hours(moment, start_ist=12, end_ist=14)
        assert not in_quiet_hours(moment, start_ist=14, end_ist=16)

    def test_equal_bounds_disable_quiet_hours(self) -> None:
        for hour in (0, 12, 23):
            moment = FakeClock.at_ist(2026, 9, 1, hour, 0).now_utc()
            assert not in_quiet_hours(moment, start_ist=9, end_ist=9)

    def test_quiet_hours_are_evaluated_in_ist_not_utc(self) -> None:
        """22:00 IST is 16:30 UTC. Evaluating in UTC would consider it daytime
        and message an Indian customer at 10 PM."""
        moment = datetime(2026, 9, 1, 16, 30, tzinfo=UTC)
        assert to_ist(moment).hour == 22
        assert in_quiet_hours(moment, start_ist=21, end_ist=9)

    def test_evening_release_is_the_next_morning(self) -> None:
        release = next_quiet_hours_release(
            FakeClock.at_ist(2026, 9, 1, 22, 30).now_utc(), start_ist=21, end_ist=9
        )
        local = to_ist(release)
        assert (local.day, local.hour, local.minute) == (2, 9, 5)

    def test_small_hours_release_is_the_same_morning(self) -> None:
        release = next_quiet_hours_release(
            FakeClock.at_ist(2026, 9, 2, 3, 0).now_utc(), start_ist=21, end_ist=9
        )
        local = to_ist(release)
        assert (local.day, local.hour, local.minute) == (2, 9, 5)

    def test_release_is_a_no_op_when_already_allowed(self) -> None:
        moment = FakeClock.at_ist(2026, 9, 1, 14, 0).now_utc()
        assert next_quiet_hours_release(moment, start_ist=21, end_ist=9) == moment

    def test_month_boundary_rolls_correctly(self) -> None:
        release = next_quiet_hours_release(
            FakeClock.at_ist(2026, 9, 30, 23, 30).now_utc(), start_ist=21, end_ist=9
        )
        local = to_ist(release)
        assert (local.month, local.day, local.hour) == (10, 1, 9)


# ===========================================================================
# Individual rules
# ===========================================================================
class TestS01AlreadyResolved:
    def test_paid_order_stops_as_organic(self) -> None:
        """Being too late is a success. Counting it as recovery would be the
        double-count §14.1 exists to prevent."""
        v = evaluate(ctx(order_status="paid"))
        assert v.decision is Decision.STOP
        assert v.terminal_status is CaseStatus.RESOLVED_ORGANIC
        assert v.blocking_rule is StoppingRule.S01_ALREADY_RESOLVED

    def test_captured_also_counts(self) -> None:
        assert evaluate(ctx(order_status="captured")).terminal_status is CaseStatus.RESOLVED_ORGANIC

    def test_status_comparison_is_case_insensitive(self) -> None:
        assert evaluate(ctx(order_status="PAID")).decision is Decision.STOP

    def test_unpaid_proceeds(self) -> None:
        assert evaluate(ctx(order_status="created")).decision is Decision.PROCEED

    def test_missing_status_does_not_block(self) -> None:
        """Absent evidence of payment is not evidence of payment."""
        assert evaluate(ctx(order_status=None)).decision is Decision.PROCEED


class TestS02AttemptBudget:
    @pytest.mark.parametrize(
        ("attempt", "expected"), [(0, Decision.PROCEED), (1, Decision.PROCEED), (2, Decision.STOP)]
    )
    def test_budget_boundary(self, attempt: int, expected: Decision) -> None:
        assert evaluate(ctx(attempt_no=attempt)).decision is expected

    def test_exhausted_budget_expires_the_case(self) -> None:
        assert evaluate(ctx(attempt_no=5)).terminal_status is CaseStatus.EXPIRED


class TestS03DiscountBudget:
    def test_second_discount_is_stripped_not_stopped(self) -> None:
        """Degrade, not stop: the recovery still goes out, at 0%."""
        v = evaluate(ctx(discount_bearing_attempts=1, proposed_discount_pct=5.0))
        assert v.decision is Decision.DEGRADE
        assert v.degradations["discount_pct"] == 0.0
        assert v.may_act

    def test_first_discount_is_allowed(self) -> None:
        v = evaluate(ctx(discount_bearing_attempts=0, proposed_discount_pct=5.0))
        assert v.decision is Decision.PROCEED

    def test_zero_discount_never_touches_the_budget(self) -> None:
        v = evaluate(ctx(discount_bearing_attempts=9, proposed_discount_pct=0.0))
        assert v.decision is Decision.PROCEED


class TestContactCaps:
    def test_24h_cap_defers_to_the_anniversary(self) -> None:
        last = FakeClock.at_ist(2026, 9, 1, 10, 0).now_utc()
        v = evaluate(
            ctx(contacts_24h=1, last_contact_at=last, window_expires_at=last + timedelta(days=5))
        )
        assert v.decision is Decision.DEFER
        assert v.defer_until == last + timedelta(hours=24)

    def test_24h_cap_stops_when_the_timestamp_is_missing(self) -> None:
        """A cap we cannot schedule around is a cap."""
        v = evaluate(ctx(contacts_24h=1, last_contact_at=None))
        assert v.decision is Decision.STOP
        assert v.terminal_status is CaseStatus.SUPPRESSED

    def test_48h_cap_stops_rather_than_defers(self) -> None:
        """Deferring would mean planning a third message -- the thing the cap
        exists to prevent."""
        v = evaluate(ctx(contacts_48h=2))
        assert v.decision is Decision.STOP
        assert v.blocking_rule is StoppingRule.S05_CONTACT_CAP_48H

    @pytest.mark.parametrize(
        ("count", "expected"), [(0, Decision.PROCEED), (1, Decision.PROCEED), (2, Decision.STOP)]
    )
    def test_48h_boundary(self, count: int, expected: Decision) -> None:
        assert evaluate(ctx(contacts_48h=count)).decision is expected

    def test_caps_do_not_block_non_contact_actions(self) -> None:
        """Creating a human task or writing an audit block touches no customer."""
        v = evaluate(ctx(contacts_48h=9, contacts_24h=9, is_outbound_contact=False))
        assert v.decision is Decision.PROCEED


class TestS06RecoveryWindow:
    def test_expired_window_stops(self) -> None:
        now = FakeClock.at_ist(2026, 9, 2, 12, 0).now_utc()
        v = evaluate(ctx(now_utc=now, window_expires_at=now - timedelta(minutes=1)))
        assert v.decision is Decision.STOP
        assert v.terminal_status is CaseStatus.EXPIRED

    def test_the_boundary_instant_is_closed(self) -> None:
        now = FakeClock.at_ist(2026, 9, 2, 12, 0).now_utc()
        assert evaluate(ctx(now_utc=now, window_expires_at=now)).decision is Decision.STOP

    def test_absent_window_does_not_stop(self) -> None:
        assert evaluate(ctx(window_expires_at=None)).decision is Decision.PROCEED


class TestS07OptOut:
    def test_opt_out_is_absolute(self) -> None:
        """Nothing overrides it -- not value, not novelty, not a merchant."""
        v = evaluate(ctx(opted_out=True, marketing_consent=True, transactional_consent=True))
        assert v.decision is Decision.STOP
        assert v.terminal_status is CaseStatus.SUPPRESSED
        assert v.blocking_rule is StoppingRule.S07_OPT_OUT

    def test_opt_out_still_permits_internal_actions(self) -> None:
        """We may still record an audit block about someone who opted out."""
        assert evaluate(ctx(opted_out=True, is_outbound_contact=False)).decision is Decision.PROCEED


class TestS08ConsentClass:
    def test_marketing_without_consent_downgrades(self) -> None:
        """The rule that makes Ananya's recovery discount-free."""
        v = evaluate(
            ctx(
                proposed_message_class=MessageClass.MARKETING,
                proposed_discount_pct=5.0,
                marketing_consent=False,
                transactional_consent=True,
            )
        )
        assert v.decision is Decision.DEGRADE
        assert v.degradations["message_class"] is MessageClass.TRANSACTIONAL
        assert v.degradations["discount_pct"] == 0.0

    def test_dnd_blocks_marketing_even_with_consent(self) -> None:
        v = evaluate(
            ctx(
                proposed_message_class=MessageClass.MARKETING,
                marketing_consent=True,
                dnd_registered=True,
            )
        )
        assert v.decision is Decision.DEGRADE
        detail = next(r.detail for r in v.fired if r.rule is StoppingRule.S08_CONSENT_CLASS)
        assert "DND" in detail

    def test_marketing_with_consent_proceeds(self) -> None:
        v = evaluate(
            ctx(
                proposed_message_class=MessageClass.MARKETING,
                proposed_discount_pct=5.0,
                marketing_consent=True,
            )
        )
        assert v.decision is Decision.PROCEED

    def test_no_transactional_consent_stops_everything(self) -> None:
        v = evaluate(ctx(transactional_consent=False))
        assert v.decision is Decision.STOP

    def test_dnd_does_not_block_transactional(self) -> None:
        """DND restricts promotional contact, not a payment-retry link."""
        v = evaluate(ctx(dnd_registered=True, proposed_message_class=MessageClass.TRANSACTIONAL))
        assert v.decision is Decision.PROCEED


class TestS10PromiseFreeze:
    def test_active_promise_freezes_outreach(self) -> None:
        now = FakeClock.at_ist(2026, 9, 1, 11, 30).now_utc()
        promised = now + timedelta(days=3)
        v = evaluate(
            ctx(
                promise_active=True,
                promised_at=promised,
                window_expires_at=now + timedelta(days=30),
            )
        )
        assert v.decision is Decision.DEFER
        assert v.defer_until == promised + timedelta(hours=24)

    def test_lapsed_promise_thaws(self) -> None:
        now = FakeClock.at_ist(2026, 9, 5, 11, 30).now_utc()
        v = evaluate(
            ctx(
                now_utc=now,
                promise_active=True,
                promised_at=now - timedelta(days=2),
                window_expires_at=now + timedelta(days=10),
            )
        )
        assert v.decision is Decision.PROCEED

    def test_promise_flag_without_a_date_is_ignored(self) -> None:
        assert evaluate(ctx(promise_active=True, promised_at=None)).decision is Decision.PROCEED


class TestS11MerchantBudget:
    def test_daily_action_budget_stops(self) -> None:
        """Bounds the blast radius of a bad deploy."""
        v = evaluate(ctx(actions_today=50))
        assert v.decision is Decision.STOP
        assert v.blocking_rule is StoppingRule.S11_MERCHANT_BUDGET

    def test_monthly_discount_exposure_stops(self) -> None:
        assert evaluate(ctx(discount_exposure_mtd_paise=20_000_000)).decision is Decision.STOP

    def test_below_budget_proceeds(self) -> None:
        assert evaluate(ctx(actions_today=49, discount_exposure_mtd_paise=19_999_999)).decision is (
            Decision.PROCEED
        )


class TestS12KillSwitch:
    def test_disabled_autopilot_stops_everything(self) -> None:
        v = evaluate(ctx(autopilot_enabled=False))
        assert v.decision is Decision.STOP
        assert v.blocking_rule is StoppingRule.S12_KILL_SWITCH

    def test_kill_switch_outranks_every_other_reason(self) -> None:
        """Turning autopilot off must not be outvoted by anything."""
        v = evaluate(ctx(autopilot_enabled=False, order_status="paid", opted_out=True))
        assert v.blocking_rule is StoppingRule.S12_KILL_SWITCH


# ===========================================================================
# Combination behaviour
# ===========================================================================
class TestCombinations:
    def test_all_twelve_rules_always_run(self) -> None:
        """Never short-circuited: the dashboard counts firings per rule, and
        'S-05 fired 4 times today' is the evidence the brakes work."""
        v = evaluate(ctx(autopilot_enabled=False))
        assert len(v.results) == 12
        assert len({r.rule for r in v.results}) == 12

    def test_stop_beats_defer(self) -> None:
        now = FakeClock.at_ist(2026, 9, 1, 22, 0).now_utc()  # quiet hours -> DEFER
        v = evaluate(ctx(now_utc=now, contacts_48h=2, window_expires_at=now + timedelta(days=2)))
        assert v.decision is Decision.STOP

    def test_defer_beats_degrade(self) -> None:
        now = FakeClock.at_ist(2026, 9, 1, 22, 0).now_utc()
        v = evaluate(
            ctx(
                now_utc=now,
                window_expires_at=now + timedelta(days=2),
                discount_bearing_attempts=1,
                proposed_discount_pct=5.0,
            )
        )
        assert v.decision is Decision.DEFER

    def test_degradations_merge_across_rules(self) -> None:
        """Stripping a discount and downgrading a class are independent."""
        v = evaluate(
            ctx(
                proposed_message_class=MessageClass.MARKETING,
                proposed_discount_pct=5.0,
                marketing_consent=False,
                discount_bearing_attempts=1,
            )
        )
        assert v.degradations["discount_pct"] == 0.0
        assert v.degradations["message_class"] is MessageClass.TRANSACTIONAL

    def test_the_latest_deferral_wins(self) -> None:
        """Sending while another hold is still in force would breach it."""
        now = FakeClock.at_ist(2026, 9, 1, 22, 0).now_utc()  # quiet -> ~09:05 tomorrow
        far = now + timedelta(days=3)
        v = evaluate(
            ctx(
                now_utc=now,
                contacts_24h=1,
                last_contact_at=far - timedelta(hours=24),
                window_expires_at=now + timedelta(days=10),
            )
        )
        assert v.decision is Decision.DEFER
        assert v.defer_until == far

    def test_deferral_past_the_window_becomes_a_stop(self) -> None:
        """Holding until after expiry is a drop with extra steps."""
        now = FakeClock.at_ist(2026, 9, 1, 22, 0).now_utc()
        v = evaluate(ctx(now_utc=now, window_expires_at=now + timedelta(hours=2)))
        assert v.decision is Decision.STOP
        assert v.terminal_status is CaseStatus.EXPIRED

    def test_applying_degradations_changes_the_context(self) -> None:
        original = ctx(
            proposed_message_class=MessageClass.MARKETING,
            proposed_discount_pct=5.0,
            marketing_consent=False,
        )
        reduced = apply_degradations(original, evaluate(original))
        assert reduced.proposed_discount_pct == 0.0
        assert reduced.proposed_message_class is MessageClass.TRANSACTIONAL
        # And the reduced action now passes cleanly.
        assert evaluate(reduced).decision is Decision.PROCEED

    def test_a_clean_context_proceeds(self) -> None:
        v = evaluate(ctx())
        assert v.decision is Decision.PROCEED
        assert v.fired == ()
        assert v.blocking_rule is None


# ===========================================================================
# Termination -- the guarantee the track bar asks for
# ===========================================================================
class TestTermination:
    """No case can run forever, under any combination of inputs."""

    def test_a_deferred_case_terminates_when_the_clock_advances(self) -> None:
        clock = FakeClock.at_ist(2026, 9, 1, 22, 0)
        window = clock.now_utc() + timedelta(hours=24)

        decisions = []
        for _ in range(200):  # far more steps than the window allows
            v = evaluate(ctx(now_utc=clock.now_utc(), window_expires_at=window))
            decisions.append(v.decision)
            if v.decision is Decision.STOP:
                break
            clock.advance(hours=1)

        assert Decision.STOP in decisions, "case never reached a terminal state"
        assert decisions.index(Decision.STOP) <= 25, "took longer than the 24h window"

    def test_every_attempt_budget_terminates(self) -> None:
        for attempt in range(0, 10):
            v = evaluate(ctx(attempt_no=attempt))
            if attempt >= POLICY.max_attempts_per_case:
                assert v.decision is Decision.STOP

    def test_no_rule_can_defer_indefinitely(self) -> None:
        """S-06 is the backstop: any deferral past the window becomes a stop,
        so the bound is min(attempt budget, wall clock) and both are finite."""
        now = FakeClock.at_ist(2026, 9, 1, 12, 0).now_utc()
        window = now + timedelta(hours=1)
        for hours_ahead in range(0, 48):
            v = evaluate(ctx(now_utc=now + timedelta(hours=hours_ahead), window_expires_at=window))
            if v.decision is Decision.DEFER:
                assert v.defer_until is not None
                assert v.defer_until < window, "deferred beyond the window"

    @pytest.mark.parametrize("start_hour", [0, 6, 12, 18, 21, 23])
    def test_terminates_from_any_starting_hour(self, start_hour: int) -> None:
        """Quiet-hours deferral must not create a loop at any time of day."""
        clock = FakeClock.at_ist(2026, 9, 1, start_hour, 0)
        window = clock.now_utc() + timedelta(hours=24)
        for step in range(60):
            v = evaluate(ctx(now_utc=clock.now_utc(), window_expires_at=window))
            if v.decision is Decision.STOP:
                return
            clock.advance(hours=1)
            assert step < 30, "no terminal state within the window"
        pytest.fail("never terminated")
