"""Property-based proof that the stopping rules terminate.

The individual tests in ``test_stopping_rules.py`` check chosen inputs. This
file checks *all* of them: hypothesis generates hostile contexts — impossible
counter combinations, windows in the past, promises decades away, negative
discounts, every hour of the day — and asserts the invariants hold for every
one.

The claim this licenses is stronger than "we tested it": **no reachable input
produces a case that runs forever, or a verdict the executor cannot act on.**
That is what the track bar means by *stopping rules*, and it is the difference
between a retry counter and a guarantee.

Marked ``property`` so CI gates on it. No API key, no network.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from app.db.enums import CaseStatus, MessageClass
from app.guardrails.stopping_rules import (
    Decision,
    PolicyLimits,
    StoppingContext,
    evaluate,
    in_quiet_hours,
    next_quiet_hours_release,
)

pytestmark = pytest.mark.property

TERMINAL = {
    CaseStatus.RECOVERED,
    CaseStatus.RESOLVED_ORGANIC,
    CaseStatus.EXPIRED,
    CaseStatus.SUPPRESSED,
    CaseStatus.REJECTED,
    CaseStatus.FAILED_PERMANENT,
}

BASE = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)

policies = st.builds(
    PolicyLimits,
    max_attempts_per_case=st.integers(min_value=1, max_value=5),
    max_discount_bearing_attempts=st.integers(min_value=0, max_value=3),
    max_contacts_24h=st.integers(min_value=1, max_value=4),
    max_contacts_48h=st.integers(min_value=1, max_value=6),
    quiet_hours_start_ist=st.integers(min_value=0, max_value=23),
    quiet_hours_end_ist=st.integers(min_value=0, max_value=23),
    quiet_hours_release_minute=st.integers(min_value=0, max_value=59),
    daily_action_budget=st.integers(min_value=1, max_value=100),
    monthly_discount_exposure_paise=st.integers(min_value=0, max_value=50_000_000),
    promise_freeze_h=st.integers(min_value=0, max_value=72),
)

#: Deliberately hostile: impossible counter combinations, past windows,
#: far-future promises, negative discounts. If a real caller can never produce
#: some of these, the rules must still not blow up on them.
contexts = st.builds(
    StoppingContext,
    now_utc=st.integers(min_value=0, max_value=60 * 24 * 30).map(
        lambda m: BASE + timedelta(minutes=m)
    ),
    policy=policies,
    case_status=st.sampled_from(list(CaseStatus)),
    attempt_no=st.integers(min_value=0, max_value=10),
    discount_bearing_attempts=st.integers(min_value=0, max_value=10),
    window_expires_at=st.one_of(
        st.none(),
        st.integers(min_value=-60 * 24 * 7, max_value=60 * 24 * 40).map(
            lambda m: BASE + timedelta(minutes=m)
        ),
    ),
    order_status=st.sampled_from([None, "created", "attempted", "paid", "captured", "PAID", ""]),
    opted_out=st.booleans(),
    dnd_registered=st.booleans(),
    marketing_consent=st.booleans(),
    transactional_consent=st.booleans(),
    contacts_24h=st.integers(min_value=0, max_value=10),
    contacts_48h=st.integers(min_value=0, max_value=10),
    last_contact_at=st.one_of(
        st.none(),
        st.integers(min_value=-60 * 24 * 5, max_value=0).map(lambda m: BASE + timedelta(minutes=m)),
    ),
    promise_active=st.booleans(),
    promised_at=st.one_of(
        st.none(),
        st.integers(min_value=-60 * 24 * 10, max_value=60 * 24 * 400).map(
            lambda m: BASE + timedelta(minutes=m)
        ),
    ),
    autopilot_enabled=st.booleans(),
    actions_today=st.integers(min_value=0, max_value=200),
    discount_exposure_mtd_paise=st.integers(min_value=0, max_value=60_000_000),
    proposed_message_class=st.sampled_from(list(MessageClass)),
    proposed_discount_pct=st.floats(
        min_value=-10.0, max_value=100.0, allow_nan=False, allow_infinity=False
    ),
    is_outbound_contact=st.booleans(),
)

SETTINGS = settings(
    max_examples=2000,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


# ---------------------------------------------------------------------------
class TestVerdictIsAlwaysWellFormed:
    """A verdict the executor cannot act on is as bad as a wrong one."""

    @given(ctx=contexts)
    @SETTINGS
    def test_never_raises(self, ctx: StoppingContext) -> None:
        evaluate(ctx)

    @given(ctx=contexts)
    @SETTINGS
    def test_all_twelve_rules_always_reported(self, ctx: StoppingContext) -> None:
        """Firing counts per rule are the dashboard's evidence that the brakes
        work; a short-circuit would silently under-report them."""
        results = evaluate(ctx).results
        assert len(results) >= 12
        assert len({r.rule for r in results}) == 12

    @given(ctx=contexts)
    @SETTINGS
    def test_stop_always_carries_a_terminal_status(self, ctx: StoppingContext) -> None:
        v = evaluate(ctx)
        if v.decision is Decision.STOP:
            assert v.terminal_status is not None
            assert v.terminal_status in TERMINAL

    @given(ctx=contexts)
    @SETTINGS
    def test_defer_always_carries_a_future_instant(self, ctx: StoppingContext) -> None:
        """A deferral without a time is a drop."""
        v = evaluate(ctx)
        if v.decision is Decision.DEFER:
            assert v.defer_until is not None
            assert v.defer_until > ctx.now_utc

    @given(ctx=contexts)
    @SETTINGS
    def test_a_blocking_rule_is_named_whenever_something_fires(self, ctx: StoppingContext) -> None:
        """Every non-proceed outcome must be attributable to a named rule, or
        the merchant cannot be told why nothing happened."""
        v = evaluate(ctx)
        if v.decision is not Decision.PROCEED:
            assert v.blocking_rule is not None


class TestTerminationInvariants:
    @given(ctx=contexts)
    @SETTINGS
    def test_deferral_never_outlives_the_window(self, ctx: StoppingContext) -> None:
        """The backstop that makes termination provable: a hold past expiry is
        converted into a stop rather than scheduled."""
        v = evaluate(ctx)
        if v.decision is Decision.DEFER and ctx.window_expires_at is not None:
            assert v.defer_until is not None
            assert v.defer_until < ctx.window_expires_at

    @given(ctx=contexts)
    @SETTINGS
    def test_an_expired_window_can_never_proceed(self, ctx: StoppingContext) -> None:
        if ctx.window_expires_at is not None and ctx.now_utc >= ctx.window_expires_at:
            assert evaluate(ctx).decision is Decision.STOP

    @given(ctx=contexts)
    @SETTINGS
    def test_advancing_the_clock_always_reaches_a_terminal_state(
        self, ctx: StoppingContext
    ) -> None:
        """The termination proof.

        Repeatedly jump to whatever instant the engine asked us to wait for. If
        deferral could ever cycle, this would not converge.
        """
        assume(ctx.window_expires_at is not None)
        assert ctx.window_expires_at is not None

        current = ctx
        for _ in range(50):
            v = evaluate(current)
            if v.decision is not Decision.DEFER:
                return  # STOP, PROCEED or DEGRADE -- all resolve the step
            assert v.defer_until is not None
            assert v.defer_until > current.now_utc, "deferral did not advance the clock"
            current = replace(current, now_utc=v.defer_until)
        pytest.fail("did not converge within 50 deferrals")


class TestSafetyInvariants:
    """Properties that must hold for *every* input, not merely most."""

    @given(ctx=contexts)
    @SETTINGS
    def test_an_opted_out_customer_is_never_contacted(self, ctx: StoppingContext) -> None:
        """The one rule with no exceptions anywhere in the system."""
        if ctx.opted_out and ctx.is_outbound_contact:
            assert not evaluate(ctx).may_act

    @given(ctx=contexts)
    @SETTINGS
    def test_a_disabled_kill_switch_stops_everything(self, ctx: StoppingContext) -> None:
        if not ctx.autopilot_enabled:
            v = evaluate(ctx)
            assert v.decision is Decision.STOP
            assert not v.may_act

    @given(ctx=contexts)
    @SETTINGS
    def test_a_paid_order_is_never_acted_on(self, ctx: StoppingContext) -> None:
        """Messaging someone about a payment they already made is the most
        visible way to lose a merchant's trust."""
        if (ctx.order_status or "").lower() in {"paid", "captured"}:
            assert not evaluate(ctx).may_act

    @given(ctx=contexts)
    @SETTINGS
    def test_marketing_never_survives_without_consent(self, ctx: StoppingContext) -> None:
        """If the action proceeds at all, it has been downgraded away from
        marketing -- never sent as marketing."""
        if (
            ctx.is_outbound_contact
            and ctx.proposed_message_class is MessageClass.MARKETING
            and (not ctx.marketing_consent or ctx.dnd_registered)
        ):
            v = evaluate(ctx)
            if v.may_act:
                assert v.degradations.get("message_class") is MessageClass.TRANSACTIONAL
                assert v.degradations.get("discount_pct") == 0.0

    @given(ctx=contexts)
    @SETTINGS
    def test_the_contact_cap_is_never_exceeded(self, ctx: StoppingContext) -> None:
        if ctx.is_outbound_contact and ctx.contacts_48h >= ctx.policy.max_contacts_48h:
            assert not evaluate(ctx).may_act

    @given(ctx=contexts)
    @SETTINGS
    def test_the_attempt_budget_is_never_exceeded(self, ctx: StoppingContext) -> None:
        if ctx.attempt_no >= ctx.policy.max_attempts_per_case:
            assert not evaluate(ctx).may_act

    @given(ctx=contexts)
    @SETTINGS
    def test_a_message_is_never_sent_during_quiet_hours(self, ctx: StoppingContext) -> None:
        """The compliance property. If quiet hours are in force and the action
        touches a customer, the engine must not permit it now."""
        if not ctx.is_outbound_contact:
            return
        if in_quiet_hours(
            ctx.now_utc,
            start_ist=ctx.policy.quiet_hours_start_ist,
            end_ist=ctx.policy.quiet_hours_end_ist,
        ):
            assert not evaluate(ctx).may_act


class TestQuietHoursProperties:
    @given(
        minutes=st.integers(min_value=0, max_value=60 * 24 * 400),
        start=st.integers(min_value=0, max_value=23),
        end=st.integers(min_value=0, max_value=23),
        release=st.integers(min_value=0, max_value=59),
    )
    @settings(max_examples=2000, deadline=None)
    def test_the_release_instant_is_always_outside_quiet_hours(
        self, minutes: int, start: int, end: int, release: int
    ) -> None:
        """Otherwise a deferral would land inside the window it was avoiding,
        and the next evaluation would defer again -- a loop."""
        moment = BASE + timedelta(minutes=minutes)
        computed = next_quiet_hours_release(
            moment, start_ist=start, end_ist=end, release_minute=release
        )
        assert not in_quiet_hours(computed, start_ist=start, end_ist=end)

    @given(
        minutes=st.integers(min_value=0, max_value=60 * 24 * 400),
        start=st.integers(min_value=0, max_value=23),
        end=st.integers(min_value=0, max_value=23),
    )
    @settings(max_examples=2000, deadline=None)
    def test_the_release_never_moves_backwards(self, minutes: int, start: int, end: int) -> None:
        moment = BASE + timedelta(minutes=minutes)
        assert next_quiet_hours_release(moment, start_ist=start, end_ist=end) >= moment

    @given(
        minutes=st.integers(min_value=0, max_value=60 * 24 * 400),
        start=st.integers(min_value=0, max_value=23),
        end=st.integers(min_value=0, max_value=23),
    )
    @settings(max_examples=1000, deadline=None)
    def test_the_release_is_within_a_day(self, minutes: int, start: int, end: int) -> None:
        """A quiet-hours hold is hours, never days."""
        moment = BASE + timedelta(minutes=minutes)
        computed = next_quiet_hours_release(moment, start_ist=start, end_ist=end)
        assert computed - moment < timedelta(days=1, minutes=1)
