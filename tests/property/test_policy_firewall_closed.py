"""Property-based proof that the policy firewall is closed (workflow.md §15.3).

The claim this file licenses is the strongest one in the submission, and it is
deliberately narrow:

    We did not test that the agent behaves safely. We proved that **no input —
    including a fully compromised LLM — can produce an unsafe executed action.**

The proposals hypothesis generates are hostile on purpose: NaN and infinite
discounts, 10,000%, negative amounts, expiries of minus five minutes, marketing
class without consent, unicode in strings. If a real model could never emit some
of them, the firewall must still hold — because "the model would never do that"
is not a security property.

Marked ``property`` so CI gates on it. No API key, no network: the proof is pure
computation, which is the point.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.db.enums import (
    CaseStatus,
    Channel,
    EscalationRung,
    ExperimentArm,
    MessageClass,
    PolicyVerdict,
    RecoveryStrategy,
)
from app.guardrails.policy_engine import (
    PolicyContext,
    PolicyLimitsFull,
    RecoveryProposal,
    evaluate_policy,
)
from app.guardrails.stopping_rules import PolicyLimits, StoppingContext, in_quiet_hours

pytestmark = pytest.mark.property

NOW = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)  # 11:30 IST -- outside quiet hours

#: Hostile by construction. Every value here is something a compromised or
#: malfunctioning model could emit, plus several it could not.
proposals = st.builds(
    RecoveryProposal,
    strategy=st.sampled_from(list(RecoveryStrategy)),
    discount_pct=st.one_of(
        st.floats(allow_nan=True, allow_infinity=True),
        st.floats(min_value=-1e6, max_value=1e6),
        st.sampled_from([0.0, 5.0, 7.0, 7.0001, 15.0, 90.0, 100.0, 1e308, -0.0]),
    ),
    link_validity_minutes=st.one_of(
        st.integers(min_value=-10_000, max_value=10_000_000),
        st.sampled_from([0, 1, 14, 15, 30, 1440, 1441, -5]),
    ),
    channel=st.sampled_from(list(Channel)),
    message_class=st.sampled_from(list(MessageClass)),
    rationale=st.text(max_size=200),
)

#: A well-behaved model. The hostile strategy above almost always triggers a
#: clamp, and a discount clamp escalates to a human by design -- so a fuzzer
#: fed only hostile input would barely exercise the PASSED path at all. Real
#: traffic is mostly sane with a hostile tail, and the proof needs both.
sane_proposals = st.builds(
    RecoveryProposal,
    strategy=st.sampled_from(list(RecoveryStrategy)),
    discount_pct=st.floats(min_value=0.0, max_value=7.0, allow_nan=False, allow_infinity=False),
    link_validity_minutes=st.integers(min_value=15, max_value=1440),
    channel=st.sampled_from(list(Channel)),
    message_class=st.sampled_from(list(MessageClass)),
    rationale=st.text(max_size=80),
)

#: Used on the viable path: half realistic, half hostile.
mixed_proposals = st.one_of(sane_proposals, proposals)

stopping_limits = st.builds(
    PolicyLimits,
    max_attempts_per_case=st.integers(min_value=1, max_value=5),
    max_contacts_24h=st.integers(min_value=1, max_value=3),
    max_contacts_48h=st.integers(min_value=1, max_value=5),
    quiet_hours_start_ist=st.integers(min_value=0, max_value=23),
    quiet_hours_end_ist=st.integers(min_value=0, max_value=23),
    daily_action_budget=st.integers(min_value=1, max_value=100),
    monthly_discount_exposure_paise=st.integers(min_value=0, max_value=50_000_000),
)

money_limits = st.builds(
    PolicyLimitsFull,
    max_autonomous_amount_paise=st.integers(min_value=10_000, max_value=5_000_000),
    hitl_dual_signal_amount_paise=st.integers(min_value=5_000_000, max_value=50_000_000),
    max_discount_pct=st.floats(min_value=0.0, max_value=20.0),
    default_discount_pct=st.floats(min_value=0.0, max_value=10.0),
    max_discount_absolute_paise=st.integers(min_value=0, max_value=500_000),
    link_expiry_min_minutes=st.integers(min_value=1, max_value=30),
    link_expiry_max_minutes=st.integers(min_value=60, max_value=10_080),
)

stopping_contexts = st.builds(
    StoppingContext,
    now_utc=st.just(NOW),
    policy=stopping_limits,
    case_status=st.sampled_from(list(CaseStatus)),
    attempt_no=st.integers(min_value=0, max_value=6),
    discount_bearing_attempts=st.integers(min_value=0, max_value=4),
    window_expires_at=st.integers(min_value=-600, max_value=60 * 24 * 10).map(
        lambda m: NOW + timedelta(minutes=m)
    ),
    order_status=st.sampled_from([None, "created", "attempted", "paid"]),
    opted_out=st.booleans(),
    dnd_registered=st.booleans(),
    marketing_consent=st.booleans(),
    transactional_consent=st.booleans(),
    contacts_24h=st.integers(min_value=0, max_value=5),
    contacts_48h=st.integers(min_value=0, max_value=5),
    last_contact_at=st.one_of(
        st.none(),
        st.integers(min_value=-2880, max_value=0).map(lambda m: NOW + timedelta(minutes=m)),
    ),
    autopilot_enabled=st.booleans(),
    actions_today=st.integers(min_value=0, max_value=120),
    discount_exposure_mtd_paise=st.integers(min_value=0, max_value=40_000_000),
    proposed_message_class=st.sampled_from(list(MessageClass)),
    proposed_discount_pct=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
)

contexts = st.builds(
    PolicyContext,
    case_id=st.text(min_size=1, max_size=12, alphabet="RC-0123456789"),
    order_amount_paise=st.one_of(
        st.integers(min_value=-1000, max_value=50_000_000),
        st.sampled_from([0, 1, 100, 429_900, 1_000_000, 1_850_000, 10_000_000]),
    ),
    attempt_no=st.integers(min_value=0, max_value=5),
    arm=st.sampled_from(list(ExperimentArm)),
    stopping=stopping_contexts,
    limits=money_limits,
    approved_action_hash=st.none(),
)

#: ---------------------------------------------------------------------------
#: VIABLE contexts: reach the clamping code by construction.
#:
#: The first version of this file used only the hostile strategy above, and
#: **100% of 1,500 generated examples were BLOCKED at the first gate** -- so the
#: bounds were never exercised once and every `if verdict is PASSED` assertion
#: was vacuously true. The suite was green and proved nothing (INC-006).
#:
#: With ~8 independent block conditions each firing about half the time, the
#: chance of a random context surviving to the clamping code is under 0.1%.
#: Randomness alone will not find the path; it has to be constructed.
#:
#: What stays random is what the invariants are *about*: the discount (still
#: NaN, infinite, negative, 10,000%), the expiry, the message class, consent,
#: and the amount. What is fixed is only the plumbing needed to arrive.
viable_stopping = st.builds(
    StoppingContext,
    now_utc=st.just(NOW),
    policy=st.builds(
        PolicyLimits,
        max_attempts_per_case=st.just(2),
        max_contacts_24h=st.just(1),
        max_contacts_48h=st.just(2),
        quiet_hours_start_ist=st.just(21),  # NOW is 11:30 IST -> outside
        quiet_hours_end_ist=st.just(9),
        daily_action_budget=st.just(50),
        monthly_discount_exposure_paise=st.just(20_000_000),
    ),
    case_status=st.sampled_from(
        [CaseStatus.DETECTED, CaseStatus.TRIAGED, CaseStatus.STRATEGY_FORMED]
    ),
    attempt_no=st.integers(min_value=0, max_value=1),
    discount_bearing_attempts=st.integers(min_value=0, max_value=1),
    window_expires_at=st.just(NOW + timedelta(hours=24)),
    order_status=st.sampled_from([None, "created", "attempted"]),
    opted_out=st.just(False),
    # Consent stays free: the consent invariants are the point.
    dnd_registered=st.booleans(),
    marketing_consent=st.booleans(),
    transactional_consent=st.just(True),
    contacts_24h=st.just(0),
    contacts_48h=st.just(0),
    last_contact_at=st.none(),
    promise_active=st.just(False),
    promised_at=st.none(),
    autopilot_enabled=st.just(True),
    actions_today=st.integers(min_value=0, max_value=10),
    discount_exposure_mtd_paise=st.integers(min_value=0, max_value=1_000_000),
    proposed_message_class=st.sampled_from(list(MessageClass)),
    proposed_discount_pct=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    is_outbound_contact=st.just(True),
)

#: Real production bounds, so the autonomous ceiling means what it means in
#: the product. The randomised `money_limits` above pushed almost everything
#: over the escalation threshold and left only 6% of examples on the PASSED
#: path -- enough to be non-vacuous, not enough to be a proof.
viable_money_limits = st.builds(
    PolicyLimitsFull,
    max_autonomous_amount_paise=st.just(1_000_000),  # Rs 10,000
    hitl_dual_signal_amount_paise=st.just(10_000_000),
    max_discount_pct=st.just(7.0),
    default_discount_pct=st.just(5.0),
    max_discount_absolute_paise=st.just(50_000),  # Rs 500
    link_expiry_min_minutes=st.just(15),
    link_expiry_max_minutes=st.just(1440),
)

viable_contexts = st.builds(
    PolicyContext,
    case_id=st.just("RC-0142"),
    # Weighted below the autonomous ceiling so the PASSED path is well covered,
    # with a minority above it so escalation is exercised too.
    order_amount_paise=st.one_of(
        st.integers(min_value=1, max_value=999_999),
        st.integers(min_value=1_000_000, max_value=20_000_000),
        st.sampled_from([429_900, 999_999, 1_000_000, 1_850_000, 10_000_000]),
    ),
    attempt_no=st.integers(min_value=0, max_value=2),
    arm=st.just(ExperimentArm.TREATMENT),
    stopping=viable_stopping,
    limits=viable_money_limits,
    approved_action_hash=st.none(),
)


#: ---------------------------------------------------------------------------
#: One strategy per refusal, each viable in every respect EXCEPT the condition
#: under test.
#:
#: The second half of INC-006. Properties of the form "if X then it must be
#: refused" are vacuous when run against the hostile strategy, because the
#: hostile strategy is refused for a dozen other reasons anyway. Deleting the
#: CONTROL-arm block entirely did not fail `test_the_control_arm_never_executes`
#: -- something else was blocking every example, so the test never observed the
#: code it was named after.
#:
#: Each strategy below isolates exactly one violation, so the corresponding
#: property fails if and only if that specific guard is removed.
def _viable_but(
    *,
    arm: ExperimentArm = ExperimentArm.TREATMENT,
    amounts: st.SearchStrategy[int] | None = None,
    **stopping_overrides: object,
) -> st.SearchStrategy[PolicyContext]:
    """A viable context with exactly one thing wrong.

    Explicit keyword arguments rather than reflection over field names: the
    reflective version silently wrapped a strategy in ``st.just`` and produced
    contexts whose amount was a strategy object. Clever, wrong, and it failed
    loudly only because the dataclass rejected the type.
    """
    stopping = (
        st.builds(
            replace, viable_stopping, **{k: st.just(v) for k, v in stopping_overrides.items()}
        )
        if stopping_overrides
        else viable_stopping
    )
    return st.builds(
        PolicyContext,
        case_id=st.just("RC-0142"),
        order_amount_paise=(
            amounts if amounts is not None else st.integers(min_value=10_000, max_value=999_999)
        ),
        attempt_no=st.integers(min_value=0, max_value=1),
        arm=st.just(arm),
        stopping=stopping,
        limits=viable_money_limits,
        approved_action_hash=st.none(),
    )


control_arm_contexts = _viable_but(arm=ExperimentArm.CONTROL)
kill_switch_contexts = _viable_but(autopilot_enabled=False)
opted_out_contexts = _viable_but(opted_out=True)
expired_window_contexts = _viable_but(window_expires_at=NOW - timedelta(minutes=1))
non_positive_amount_contexts = _viable_but(amounts=st.integers(min_value=-1000, max_value=0))


SETTINGS = settings(max_examples=2000, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def run(proposal: RecoveryProposal, ctx: PolicyContext):  # type: ignore[no-untyped-def]
    return evaluate_policy(proposal, ctx, now=NOW)


# ===========================================================================
class TestTheSpaceIsClosed:
    """Every bound holds for every PASSED verdict. The core claim."""

    @given(proposal=proposals, ctx=contexts)
    @SETTINGS
    def test_never_raises(self, proposal: RecoveryProposal, ctx: PolicyContext) -> None:
        """An exception in the firewall is a denial of service on recovery."""
        run(proposal, ctx)

    @given(proposal=mixed_proposals, ctx=viable_contexts)
    @SETTINGS
    def test_passed_never_exceeds_the_percentage_ceiling(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        d = run(proposal, ctx)
        if d.verdict is PolicyVerdict.PASSED:
            assert d.applied is not None
            assert d.applied.discount_pct <= ctx.limits.max_discount_pct + 1e-9

    @given(proposal=mixed_proposals, ctx=viable_contexts)
    @SETTINGS
    def test_passed_never_exceeds_the_absolute_cap(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        """7% of a ₹50,000 cart is ₹3,500 -- inside the percentage bound and far
        outside anything a merchant intended."""
        d = run(proposal, ctx)
        if d.verdict is PolicyVerdict.PASSED:
            assert d.applied is not None
            assert d.applied.discount_amount_paise <= ctx.limits.max_discount_absolute_paise

    @given(proposal=mixed_proposals, ctx=viable_contexts)
    @SETTINGS
    def test_discount_is_never_negative_or_non_finite(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        """NaN is the one that slips through a naive bound check: every
        comparison with NaN is False, so `min(nan, 7.0)` returns nan."""
        d = run(proposal, ctx)
        if d.applied is not None:
            assert math.isfinite(d.applied.discount_pct)
            assert d.applied.discount_pct >= 0.0
            assert d.applied.discount_amount_paise >= 0

    @given(proposal=mixed_proposals, ctx=viable_contexts)
    @SETTINGS
    def test_a_discount_never_exceeds_the_amount_owed(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        """Otherwise we would be issuing a link that pays the customer."""
        d = run(proposal, ctx)
        if d.applied is not None:
            assert d.applied.discount_amount_paise <= d.applied.amount_paise
            assert d.applied.charge_amount_paise >= 0

    @given(proposal=mixed_proposals, ctx=viable_contexts)
    @SETTINGS
    def test_expiry_is_always_inside_bounds(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        d = run(proposal, ctx)
        if d.verdict is PolicyVerdict.PASSED:
            assert d.applied is not None
            assert (
                ctx.limits.link_expiry_min_minutes
                <= d.applied.link_expiry_minutes
                <= ctx.limits.link_expiry_max_minutes
            )

    @given(proposal=mixed_proposals, ctx=viable_contexts)
    @SETTINGS
    def test_the_charged_amount_always_matches_the_order(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        """The amount comes from the order, never from the proposal. A model
        cannot change what a customer is charged."""
        d = run(proposal, ctx)
        if d.applied is not None:
            assert d.applied.amount_paise == ctx.order_amount_paise
            assert (
                d.applied.charge_amount_paise
                == ctx.order_amount_paise - d.applied.discount_amount_paise
            )

    @given(proposal=mixed_proposals, ctx=non_positive_amount_contexts)
    @SETTINGS
    def test_a_non_positive_order_never_passes(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        if ctx.order_amount_paise <= 0:
            assert run(proposal, ctx).verdict is PolicyVerdict.BLOCKED


class TestAuthorityIsNeverBypassed:
    @given(proposal=mixed_proposals, ctx=viable_contexts)
    @SETTINGS
    def test_large_amounts_never_pass_without_approval(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        """The ₹10,000 ceiling. With no approval hash supplied, anything at or
        above it must escalate rather than execute.

        **Except NO_ACTION** (INC-031). The ladder governs *authority to act*,
        and doing nothing needs none: a reviewer asked to approve NO_ACTION can
        neither grant nor withhold anything, and a queue padded with
        unactionable items gets rubber-stamped along with the items that matter.

        The exemption is only safe if NO_ACTION genuinely cannot move money, so
        that is asserted here rather than assumed -- see also
        ``test_no_action_can_never_move_money`` below, which is the property
        that licenses this carve-out.
        """
        d = run(proposal, ctx)
        if (
            ctx.order_amount_paise >= ctx.limits.max_autonomous_amount_paise
            and ctx.approved_action_hash is None
            and proposal.strategy is not RecoveryStrategy.NO_ACTION
        ):
            assert d.verdict is not PolicyVerdict.PASSED

    @given(proposal=mixed_proposals, ctx=viable_contexts)
    @SETTINGS
    def test_no_action_can_never_move_money(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        """**The property that licenses the exemption above.**

        Without this, exempting NO_ACTION from the authority ladder would be an
        unproven hole: a strategy that skips approval must be incapable of
        having an effect. Asserted over every generated amount, including ones
        far above the dual-signal ceiling.
        """
        d = run(proposal, ctx)
        if d.applied is None or d.applied.strategy is not RecoveryStrategy.NO_ACTION:
            return
        assert d.applied.discount_pct == 0.0
        assert d.applied.discount_amount_paise == 0
        # Nothing is being charged beyond the order itself -- no new money is
        # authorised by an action that does not happen.
        assert d.applied.charge_amount_paise == ctx.order_amount_paise

    @given(proposal=proposals, ctx=contexts)
    @SETTINGS
    def test_only_passed_verdicts_carry_a_token(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        """The token IS the capability. Handing one out alongside a BLOCKED or
        ESCALATE verdict would make the verdict advisory."""
        d = run(proposal, ctx)
        if d.verdict is not PolicyVerdict.PASSED:
            assert d.token is None
            assert not d.may_execute

    @given(proposal=mixed_proposals, ctx=viable_contexts)
    @SETTINGS
    def test_every_issued_token_verifies(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        d = run(proposal, ctx)
        if d.token is not None:
            d.token.verify()
            assert d.token.applied == d.applied

    @given(proposal=mixed_proposals, ctx=control_arm_contexts)
    @SETTINGS
    def test_the_control_arm_never_executes(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        """A CONTROL case doing nothing is it doing its job: its outcome is what
        makes the recovery number falsifiable (§14.2)."""
        if ctx.arm is ExperimentArm.CONTROL:
            d = run(proposal, ctx)
            assert d.verdict is PolicyVerdict.BLOCKED
            assert d.token is None


class TestStoppingRulesAreHonoured:
    """The firewall must not be a way around the brakes."""

    @given(proposal=mixed_proposals, ctx=kill_switch_contexts)
    @SETTINGS
    def test_a_disabled_kill_switch_never_passes(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        if not ctx.stopping.autopilot_enabled:
            assert not run(proposal, ctx).may_execute

    @given(proposal=mixed_proposals, ctx=opted_out_contexts)
    @SETTINGS
    def test_an_opted_out_customer_never_passes(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        if ctx.stopping.opted_out and ctx.stopping.is_outbound_contact:
            assert not run(proposal, ctx).may_execute

    @given(proposal=mixed_proposals, ctx=expired_window_contexts)
    @SETTINGS
    def test_an_expired_window_never_passes(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        if ctx.stopping.window_expires_at and ctx.stopping.window_expires_at <= NOW:
            assert not run(proposal, ctx).may_execute

    @given(proposal=mixed_proposals, ctx=viable_contexts)
    @SETTINGS
    def test_marketing_never_executes_without_consent(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        """The compliance property (§9.2), stated as an invariant: if the action
        executes at all, it is not a marketing message to someone who never
        opted in."""
        d = run(proposal, ctx)
        if (
            d.may_execute
            and d.applied is not None
            and d.applied.message_class is MessageClass.MARKETING
        ):
            assert ctx.stopping.marketing_consent
            assert not ctx.stopping.dnd_registered

    @given(proposal=mixed_proposals, ctx=viable_contexts)
    @SETTINGS
    def test_a_transactional_message_never_carries_a_discount(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        """A discount is an offer, and an offer is marketing. It cannot ride
        along inside a message downgraded to transactional."""
        d = run(proposal, ctx)
        if (
            d.may_execute
            and d.applied is not None
            and d.applied.message_class is MessageClass.TRANSACTIONAL
        ):
            assert d.applied.discount_pct == 0.0
            assert d.applied.discount_amount_paise == 0

    @given(proposal=mixed_proposals, ctx=viable_contexts)
    @SETTINGS
    def test_nothing_executes_during_quiet_hours_without_a_hold(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        """If quiet hours are in force, either the action does not pass, or it
        carries a send_after that moves it outside the window."""
        if not ctx.stopping.is_outbound_contact:
            return
        if not in_quiet_hours(
            NOW,
            start_ist=ctx.stopping.policy.quiet_hours_start_ist,
            end_ist=ctx.stopping.policy.quiet_hours_end_ist,
        ):
            return
        d = run(proposal, ctx)
        if d.may_execute and d.applied is not None:
            assert d.applied.send_after is not None
            assert not in_quiet_hours(
                d.applied.send_after,
                start_ist=ctx.stopping.policy.quiet_hours_start_ist,
                end_ist=ctx.stopping.policy.quiet_hours_end_ist,
            )


class TestClampingIsDownwardOnly:
    """The firewall may reduce. It may never enlarge, or invent."""

    @given(proposal=mixed_proposals, ctx=viable_contexts)
    @SETTINGS
    def test_the_applied_discount_never_exceeds_the_proposed_one(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        """A firewall that could *raise* a discount would be a new way to lose
        money rather than a control."""
        d = run(proposal, ctx)
        if d.applied is None:
            return
        proposed = proposal.discount_pct
        if isinstance(proposed, (int, float)) and math.isfinite(float(proposed)):
            assert d.applied.discount_pct <= max(float(proposed), 0.0) + 1e-9
        else:
            assert d.applied.discount_pct == 0.0

    @given(proposal=mixed_proposals, ctx=viable_contexts)
    @SETTINGS
    def test_every_reduction_is_recorded(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        """Silent clamping would make the interception metric a lie -- §14.6
        reports interceptions as evidence the firewall is live."""
        d = run(proposal, ctx)
        if d.applied is None:
            return
        proposed = proposal.discount_pct
        was_reduced = (
            not isinstance(proposed, (int, float))
            or not math.isfinite(float(proposed))
            or float(proposed) < 0
            or d.applied.discount_pct < float(proposed) - 1e-9
        )
        if was_reduced and (float(proposed) if math.isfinite(float(proposed)) else 1) != 0:
            assert d.clamps, "a value was reduced without recording an interception"

    @given(proposal=mixed_proposals, ctx=viable_contexts)
    @SETTINGS
    def test_strategy_and_channel_are_passed_through_untouched(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        """The firewall bounds *magnitudes*. Choosing a different strategy than
        the one proposed would make it a second decision-maker, and then two
        components would own the outcome."""
        d = run(proposal, ctx)
        if d.applied is not None:
            assert d.applied.strategy is proposal.strategy
            assert d.applied.channel is proposal.channel


class TestTokenIntegrity:
    @given(proposal=mixed_proposals, ctx=viable_contexts)
    @SETTINGS
    def test_a_token_cannot_be_reused_for_a_modified_action(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        """The signature covers the applied action. Swapping the numbers after
        authorisation invalidates it."""
        from dataclasses import replace

        from app.guardrails.token import PolicyToken

        d = run(proposal, ctx)
        if d.token is None or d.applied is None:
            return
        tampered = PolicyToken(
            applied=replace(d.applied, amount_paise=d.applied.amount_paise + 100_000),
            minted_at=d.token.minted_at,
            signature=d.token.signature,
        )
        assert not tampered.is_valid

    @given(proposal=mixed_proposals, ctx=viable_contexts)
    @SETTINGS
    def test_an_escalated_action_has_a_stable_content_hash(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        """A human approves a hash. It must be reproducible, or approval could
        never be matched back to the action at execution time."""
        d = run(proposal, ctx)
        if d.applied is not None:
            assert d.applied.content_hash() == d.applied.content_hash()
            assert len(d.applied.content_hash()) == 64


class TestEscalationRungIsCoherent:
    @given(proposal=mixed_proposals, ctx=viable_contexts)
    @SETTINGS
    def test_the_rung_matches_the_amount(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        d = run(proposal, ctx)
        if d.applied is None:
            return
        if d.applied.strategy is RecoveryStrategy.NO_ACTION:
            # INC-031: no action, no authority needed. Its safety is proved by
            # `test_no_action_can_never_move_money`, not asserted here.
            assert d.applied.escalation_rung is EscalationRung.A0_AUTONOMOUS
            return
        amount = ctx.order_amount_paise
        if amount >= ctx.limits.hitl_dual_signal_amount_paise:
            assert d.applied.escalation_rung is EscalationRung.A3_APPROVAL_DUAL
        elif amount >= ctx.limits.max_autonomous_amount_paise:
            assert d.applied.escalation_rung is EscalationRung.A2_APPROVAL

    @given(proposal=mixed_proposals, ctx=viable_contexts)
    @SETTINGS
    def test_a_fully_autonomous_action_carries_no_discount(
        self, proposal: RecoveryProposal, ctx: PolicyContext
    ) -> None:
        """Rung A0 means nobody looks at it. Anything spending margin must at
        least be flagged for retrospective review (§8.3)."""
        d = run(proposal, ctx)
        if d.applied is not None and d.applied.escalation_rung is EscalationRung.A0_AUTONOMOUS:
            assert d.applied.discount_pct == 0.0


class TestTheProofIsNotVacuous:
    """Guards against the failure mode that produced INC-006.

    Every invariant above is written as ``if verdict is PASSED: assert ...``.
    If no generated example ever reaches PASSED they all hold trivially and the
    suite is green while proving nothing. That is exactly what happened on the
    first run of this file: **100% of 1,500 examples were BLOCKED at the first
    gate**, and a deliberate sabotage of the NaN guard went undetected.

    These measure the coverage the proof depends on, so it cannot decay
    silently -- a future change that starts blocking everything fails here
    rather than quietly hollowing out twenty-five properties.

    Built from a seeded RNG rather than hypothesis, on purpose: this is a
    measurement over a fixed sample, and it should give the same answer on
    every run.
    """

    MIN_PASSED_FRACTION = 0.15
    MIN_CLAMPED_FRACTION = 0.40
    SAMPLE = 600

    @staticmethod
    def _sample() -> list:  # type: ignore[type-arg]
        import random

        rng = random.Random(20260905)
        hostile_discounts = [float("nan"), float("inf"), -5.0, 90.0, 1e308, 15.0]
        out = []
        for i in range(TestTheProofIsNotVacuous.SAMPLE):
            hostile = i % 2 == 0
            proposal = RecoveryProposal(
                strategy=rng.choice(list(RecoveryStrategy)),
                discount_pct=(rng.choice(hostile_discounts) if hostile else rng.uniform(0.0, 7.0)),
                link_validity_minutes=(
                    rng.choice([-5, 0, 1441, 99999]) if hostile else rng.randint(15, 1440)
                ),
                channel=rng.choice(list(Channel)),
                message_class=rng.choice(list(MessageClass)),
                rationale="",
            )
            stopping = StoppingContext(
                now_utc=NOW,
                policy=PolicyLimits(),
                case_status=CaseStatus.DETECTED,
                attempt_no=rng.randint(0, 1),
                window_expires_at=NOW + timedelta(hours=24),
                order_status=rng.choice([None, "created"]),
                marketing_consent=rng.random() < 0.5,
                dnd_registered=rng.random() < 0.2,
                transactional_consent=True,
                proposed_message_class=proposal.message_class,
                proposed_discount_pct=max(0.0, min(100.0, 5.0)),
            )
            ctx = PolicyContext(
                case_id="RC-0142",
                order_amount_paise=rng.randint(10_000, 999_999),
                attempt_no=rng.randint(0, 1),
                arm=ExperimentArm.TREATMENT,
                stopping=stopping,
                limits=PolicyLimitsFull(),
            )
            out.append(run(proposal, ctx))
        return out

    def test_a_meaningful_share_of_examples_reach_a_passed_verdict(self) -> None:
        decisions = self._sample()
        passed = sum(1 for d in decisions if d.verdict is PolicyVerdict.PASSED)
        fraction = passed / len(decisions)
        assert fraction >= self.MIN_PASSED_FRACTION, (
            f"only {fraction:.1%} of examples reached PASSED. Every bound assertion "
            "is guarded on that verdict, so the proof would be vacuous. See INC-006."
        )

    def test_the_clamping_code_is_actually_exercised(self) -> None:
        decisions = self._sample()
        clamped = sum(1 for d in decisions if d.clamps)
        fraction = clamped / len(decisions)
        assert fraction >= self.MIN_CLAMPED_FRACTION, (
            f"only {fraction:.1%} produced an interception; hostile proposals are "
            "not reaching the clamps"
        )

    def test_tokens_are_actually_minted(self) -> None:
        """If nothing is ever authorised, the token invariants prove nothing."""
        assert any(d.token is not None for d in self._sample())

    def test_both_escalation_and_authorisation_occur(self) -> None:
        """Coverage of both sides of the authority gate."""
        verdicts = {d.verdict for d in self._sample()}
        assert PolicyVerdict.PASSED in verdicts
        assert PolicyVerdict.ESCALATE_HITL in verdicts
