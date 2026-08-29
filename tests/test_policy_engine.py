"""Policy firewall unit tests.

The fuzzer proves the space is closed. This file pins the *specific* behaviours
a merchant or judge would ask about — above all the two hero cases, which have
to come out of the design rather than being narrated.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.db.enums import (
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
from app.guardrails.stopping_rules import PolicyLimits, StoppingContext
from app.guardrails.token import PolicyToken, PolicyTokenInvalid

NOW = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)  # 11:30 IST


def stopping(**overrides: object) -> StoppingContext:
    base: dict[str, object] = {
        "now_utc": NOW,
        "policy": PolicyLimits(),
        "window_expires_at": NOW + timedelta(hours=24),
        "order_status": "created",
        "transactional_consent": True,
    }
    base.update(overrides)
    return StoppingContext(**base)  # type: ignore[arg-type]


def context(amount: int = 429_900, **overrides: object) -> PolicyContext:
    base: dict[str, object] = {
        "case_id": "RC-0142",
        "order_amount_paise": amount,
        "attempt_no": 1,
        "arm": ExperimentArm.TREATMENT,
        "stopping": overrides.pop("stopping", stopping()),
        "limits": PolicyLimitsFull(),
    }
    base.update(overrides)
    return PolicyContext(**base)  # type: ignore[arg-type]


def run(proposal: RecoveryProposal, ctx: PolicyContext):  # type: ignore[no-untyped-def]
    return evaluate_policy(proposal, ctx, now=NOW)


# ===========================================================================
class TestHeroCases:
    """The two cases the demo turns on."""

    def test_ananya_recovers_without_a_discount(self) -> None:
        """Rs 4,299, high LTV, no marketing consent.

        The model proposes 5% to be safe. Policy declines the discount -- not
        because 5% is too much, but because a discount is a marketing offer and
        she never opted in. The compliance constraint and the margin constraint
        point the same way (§9.2), and this must happen *autonomously*: routing
        every no-consent recovery through an approval queue would make the
        product unusable.
        """
        decision = run(
            RecoveryProposal(
                strategy=RecoveryStrategy.FRESH_LINK_ALT_RAIL,
                discount_pct=5.0,
                message_class=MessageClass.MARKETING,
                channel=Channel.WHATSAPP,
            ),
            context(429_900, stopping=stopping(marketing_consent=False)),
        )
        assert decision.verdict is PolicyVerdict.PASSED
        assert decision.applied is not None
        assert decision.applied.discount_pct == 0.0
        assert decision.applied.discount_amount_paise == 0
        assert decision.applied.charge_amount_paise == 429_900
        assert decision.applied.message_class is MessageClass.TRANSACTIONAL
        assert decision.applied.escalation_rung is EscalationRung.A0_AUTONOMOUS
        # A routine consent downgrade is policy working, not the model
        # misbehaving -- so it is an interception, not a violation.
        assert decision.intercepted
        assert decision.violations == ()

    def test_rahul_escalates_to_a_human(self) -> None:
        """Rs 18,500 is above the Rs 10,000 autonomous ceiling."""
        decision = run(
            RecoveryProposal(strategy=RecoveryStrategy.INVOICE_REMINDER),
            context(1_850_000),
        )
        assert decision.verdict is PolicyVerdict.ESCALATE_HITL
        assert decision.escalation_rung is EscalationRung.A2_APPROVAL
        assert decision.token is None
        assert decision.applied is not None  # shown to the human for approval

    def test_an_approved_action_then_executes(self) -> None:
        escalated = run(RecoveryProposal(), context(1_850_000))
        assert escalated.applied is not None

        approved = run(
            RecoveryProposal(),
            context(1_850_000, approved_action_hash=escalated.applied.content_hash()),
        )
        assert approved.verdict is PolicyVerdict.PASSED
        assert approved.may_execute

    def test_approving_one_action_does_not_authorise_another(self) -> None:
        """The difference between a real approval gate and a button labelled
        "approve" (§13.5)."""
        escalated = run(RecoveryProposal(discount_pct=0.0), context(1_850_000))
        assert escalated.applied is not None
        stale_hash = escalated.applied.content_hash()

        # Same case, but now a discount is attached: a different action.
        tampered = run(
            RecoveryProposal(discount_pct=5.0, message_class=MessageClass.MARKETING),
            context(
                1_850_000,
                approved_action_hash=stale_hash,
                stopping=stopping(marketing_consent=True),
            ),
        )
        assert tampered.verdict is PolicyVerdict.ESCALATE_HITL
        assert "does not match" in tampered.block_reasons[0]


class TestClamping:
    def test_an_excessive_discount_resets_to_the_safe_default(self) -> None:
        """Not to the ceiling. Clamping to 7% would teach a model that asking
        for 90% reliably yields the maximum permitted."""
        decision = run(
            RecoveryProposal(discount_pct=90.0, message_class=MessageClass.MARKETING),
            context(200_000, stopping=stopping(marketing_consent=True)),
        )
        assert decision.applied is not None
        assert decision.applied.discount_pct == 5.0
        assert decision.violations
        assert decision.violations[0].is_violation

    def test_a_violation_escalates_even_below_the_amount_ceiling(self) -> None:
        decision = run(
            RecoveryProposal(discount_pct=90.0, message_class=MessageClass.MARKETING),
            context(200_000, stopping=stopping(marketing_consent=True)),
        )
        assert decision.escalation_rung is EscalationRung.A2_APPROVAL
        assert decision.verdict is PolicyVerdict.ESCALATE_HITL

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_discounts_become_zero(self, value: float) -> None:
        """`min(nan, 7.0)` returns nan: every comparison with NaN is False, so a
        naive bound check passes it straight through into an amount."""
        decision = run(
            RecoveryProposal(discount_pct=value, message_class=MessageClass.MARKETING),
            context(200_000, stopping=stopping(marketing_consent=True)),
        )
        assert decision.applied is not None
        assert decision.applied.discount_pct == 0.0

    def test_negative_discounts_become_zero(self) -> None:
        decision = run(RecoveryProposal(discount_pct=-25.0), context(200_000))
        assert decision.applied is not None
        assert decision.applied.discount_pct == 0.0

    def test_the_absolute_cap_bites_on_a_large_cart(self) -> None:
        """7% of Rs 50,000 is Rs 3,500 -- inside the percentage bound and far
        outside anything a merchant intended."""
        decision = run(
            RecoveryProposal(discount_pct=7.0, message_class=MessageClass.MARKETING),
            context(5_000_000, stopping=stopping(marketing_consent=True)),
        )
        assert decision.applied is not None
        assert decision.applied.discount_amount_paise == 50_000  # Rs 500
        assert decision.applied.discount_pct == pytest.approx(1.0, abs=0.01)

    @pytest.mark.parametrize(
        ("proposed", "expected"), [(-5, 15), (0, 15), (14, 15), (15, 15), (30, 30), (1441, 1440)]
    )
    def test_expiry_is_bounded(self, proposed: int, expected: int) -> None:
        decision = run(RecoveryProposal(link_validity_minutes=proposed), context())
        assert decision.applied is not None
        assert decision.applied.link_expiry_minutes == expected

    def test_every_reduction_is_recorded(self) -> None:
        """Silent clamping would make the interception metric a lie."""
        decision = run(
            RecoveryProposal(discount_pct=90.0, link_validity_minutes=99999),
            context(200_000, stopping=stopping(marketing_consent=True)),
        )
        fields = {c.field_name for c in decision.clamps}
        assert "discount_pct" in fields
        assert "link_expiry_minutes" in fields


class TestRefusals:
    def test_the_control_arm_never_executes(self) -> None:
        """Not a failure: a CONTROL case is doing its job by doing nothing, and
        its outcome is what makes the recovery number falsifiable (§14.2)."""
        decision = run(RecoveryProposal(), context(arm=ExperimentArm.CONTROL))
        assert decision.verdict is PolicyVerdict.BLOCKED
        assert decision.token is None
        assert "CONTROL" in decision.block_reasons[0]

    def test_a_stopping_rule_blocks_and_names_itself(self) -> None:
        decision = run(RecoveryProposal(), context(stopping=stopping(opted_out=True)))
        assert decision.verdict is PolicyVerdict.BLOCKED
        assert "S-07" in decision.block_reasons[0]

    def test_a_non_positive_order_is_refused(self) -> None:
        assert run(RecoveryProposal(), context(0)).verdict is PolicyVerdict.BLOCKED
        assert run(RecoveryProposal(), context(-100)).verdict is PolicyVerdict.BLOCKED

    def test_the_kill_switch_blocks(self) -> None:
        decision = run(RecoveryProposal(), context(stopping=stopping(autopilot_enabled=False)))
        assert decision.verdict is PolicyVerdict.BLOCKED


class TestTheAppliedAction:
    def test_the_amount_comes_from_the_order_not_the_proposal(self) -> None:
        """A model cannot change what a customer is charged: the proposal has
        no amount field at all."""
        decision = run(RecoveryProposal(), context(429_900))
        assert decision.applied is not None
        assert decision.applied.amount_paise == 429_900

    def test_the_reference_id_is_deterministic(self) -> None:
        """It is committed to the outbox before the provider call and is the
        exact string the attribution matcher later looks for."""
        decision = run(RecoveryProposal(), context())
        assert decision.applied is not None
        assert decision.applied.reference_id == "rvp_rc-0142_1"

    def test_strategy_and_channel_pass_through(self) -> None:
        """The firewall bounds magnitudes; it does not make a second choice."""
        decision = run(
            RecoveryProposal(strategy=RecoveryStrategy.STATIC_UPI_QR, channel=Channel.SMS),
            context(),
        )
        assert decision.applied is not None
        assert decision.applied.strategy is RecoveryStrategy.STATIC_UPI_QR
        assert decision.applied.channel is Channel.SMS


class TestTokens:
    def test_a_passed_verdict_carries_a_valid_token(self) -> None:
        decision = run(RecoveryProposal(), context())
        assert decision.token is not None
        decision.token.verify()
        assert decision.may_execute

    def test_a_hand_built_token_is_rejected(self) -> None:
        """The capability is unforgeable without the process-private key, so a
        module that skips the firewall fails loudly at the call site rather
        than silently bypassing every bound."""
        decision = run(RecoveryProposal(), context())
        assert decision.applied is not None
        forged = PolicyToken(applied=decision.applied, minted_at=NOW, signature="0" * 64)
        with pytest.raises(PolicyTokenInvalid):
            forged.verify()
        assert not forged.is_valid

    def test_modifying_the_action_invalidates_the_token(self) -> None:
        decision = run(RecoveryProposal(), context())
        assert decision.token is not None and decision.applied is not None
        tampered = PolicyToken(
            applied=replace(decision.applied, amount_paise=99_999_999),
            minted_at=decision.token.minted_at,
            signature=decision.token.signature,
        )
        with pytest.raises(PolicyTokenInvalid):
            tampered.verify()

    def test_the_content_hash_is_stable_and_order_independent(self) -> None:
        """Approvals persist as a hash. Non-canonical JSON is how hashes
        silently stop matching across processes."""
        a = run(RecoveryProposal(), context())
        b = run(RecoveryProposal(), context())
        assert a.applied is not None and b.applied is not None
        assert a.applied.content_hash() == b.applied.content_hash()


class TestSerialisationRoundTrip:
    """The outbox commits an action as JSON and the reconciler reads it back
    after a crash -- the one code path that only runs when something has
    already gone wrong, and therefore the one least likely to be exercised by
    accident. INC-013 was exactly this: enums came back as strings and the
    token signature blew up."""

    def test_an_action_survives_a_round_trip(self) -> None:
        import json

        from app.guardrails.token import AppliedAction

        original = run(RecoveryProposal(), context()).applied
        assert original is not None

        restored = AppliedAction.from_payload(json.loads(json.dumps(original.as_payload())))
        assert restored == original

    def test_the_hash_survives_a_round_trip(self) -> None:
        """A human approves a hash. If deserialisation changed it, an approved
        action could never be matched back at execution time."""
        import json

        from app.guardrails.token import AppliedAction

        original = run(RecoveryProposal(discount_pct=5.0), context()).applied
        assert original is not None
        restored = AppliedAction.from_payload(json.loads(json.dumps(original.as_payload())))
        assert restored.content_hash() == original.content_hash()

    def test_enums_come_back_as_enums_not_strings(self) -> None:
        import json

        from app.db.enums import Channel, EscalationRung, MessageClass, RecoveryStrategy
        from app.guardrails.token import AppliedAction

        original = run(RecoveryProposal(), context()).applied
        assert original is not None
        restored = AppliedAction.from_payload(json.loads(json.dumps(original.as_payload())))
        assert isinstance(restored.strategy, RecoveryStrategy)
        assert isinstance(restored.channel, Channel)
        assert isinstance(restored.message_class, MessageClass)
        assert isinstance(restored.escalation_rung, EscalationRung)
