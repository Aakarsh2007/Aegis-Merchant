"""The deterministic policy firewall (workflow.md §26.2).

Every LLM proposal passes through here, and nothing reaches a payment API that
did not. The module is a pure function: context and proposal in, verdict out,
no I/O and no clock read.

The framing that matters: **a proposal is a description, a verdict is a
decision.** The model describes what it would like to happen; this module
decides what happens. They are separate types on purpose — ``RecoveryProposal``
has no ``reference_id``, so it physically cannot be handed to the provider, and
"execute the model's suggestion" is not something you can write by accident.

Three properties this is built to have, all checked by the fuzzer in
``tests/property/test_policy_firewall_closed.py`` over generated hostile input:

* **Closed.** No proposal — including one from a fully compromised model —
  produces a PASSED verdict whose applied numbers violate a bound.
* **Clamping is downward only.** The firewall may reduce an action. It may
  never enlarge one, and it may never invent one the model did not propose.
* **Interceptions are recorded, not silenced.** Every clamp is returned as a
  :class:`Clamp` with the proposed value, the applied value and the reason.
  §14.6 reports these as a positive metric: fourteen interceptions means the
  model tried something unsafe fourteen times and was stopped fourteen times,
  which is stronger evidence than zero.

Clamp semantics worth stating, because the obvious choice is wrong: a discount
above the ceiling is clamped to the **safe default**, not to the ceiling.
Clamping to the ceiling would teach a model that asking for 90% reliably yields
the maximum permitted, which is precisely the wrong incentive.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime

from app.db.enums import (
    Channel,
    EscalationRung,
    ExperimentArm,
    MessageClass,
    PolicyVerdict,
    RecoveryStrategy,
)
from app.db.ids import reference_id as make_reference_id
from app.guardrails.stopping_rules import (
    Decision,
    StoppingContext,
    StoppingVerdict,
    apply_degradations,
)
from app.guardrails.stopping_rules import (
    evaluate as evaluate_stopping,
)
from app.guardrails.token import AppliedAction, PolicyToken, mint

__all__ = [
    "Clamp",
    "PolicyContext",
    "PolicyDecision",
    "PolicyLimitsFull",
    "RecoveryProposal",
    "evaluate_policy",
]


@dataclass(frozen=True)
class PolicyLimitsFull:
    """Money and expiry bounds. Complements ``stopping_rules.PolicyLimits``."""

    max_autonomous_amount_paise: int = 1_000_000  # ₹10,000
    hitl_dual_signal_amount_paise: int = 10_000_000  # ₹1,00,000
    max_discount_pct: float = 7.0
    default_discount_pct: float = 5.0
    max_discount_absolute_paise: int = 50_000  # ₹500
    link_expiry_minutes: int = 30
    link_expiry_min_minutes: int = 15
    link_expiry_max_minutes: int = 1440


@dataclass(frozen=True)
class RecoveryProposal:
    """What the model suggests. **Untrusted.**

    Deliberately carries no ``reference_id`` and no ``case_id``: it is not
    something the provider layer can be handed, however hard someone tries.
    """

    strategy: RecoveryStrategy = RecoveryStrategy.FRESH_LINK_SAME_RAIL
    discount_pct: float = 0.0
    link_validity_minutes: int = 30
    channel: Channel = Channel.WHATSAPP
    message_class: MessageClass = MessageClass.TRANSACTIONAL
    rationale: str = ""


@dataclass(frozen=True)
class PolicyContext:
    """Ground truth the firewall checks the proposal against."""

    case_id: str
    #: What the customer actually owes, read from the order — never from the
    #: proposal. A model that asks to charge a different amount is refused
    #: rather than clamped.
    order_amount_paise: int
    attempt_no: int
    arm: ExperimentArm
    stopping: StoppingContext
    limits: PolicyLimitsFull = field(default_factory=PolicyLimitsFull)
    #: Set once a human has approved this exact action (rung A2/A3).
    approved_action_hash: str | None = None


@dataclass(frozen=True)
class Clamp:
    """One interception, recorded for the audit trail and the dashboard.

    ``is_violation`` separates two things that look identical as numbers and
    mean opposite things:

    * A **violation** -- the model proposed something outside a hard bound
      (90% discount, NaN, negative). Evidence the model tried to do something
      it is not permitted to do, and worth a human's attention.
    * A **routine reduction** -- a consent downgrade, or the absolute rupee cap
      biting on a large cart. The model proposed something reasonable and
      policy made it *safer*. No human needs to see this.

    Conflating them would send Ananya's recovery to an approval queue: her
    discount is stripped because she has no marketing consent, which is the
    system working exactly as designed (§9.2), not a model misbehaving.
    """

    field_name: str
    proposed: object
    applied: object
    reason: str
    #: True only when the proposal breached a hard bound.
    is_violation: bool = False

    def __str__(self) -> str:
        kind = "VIOLATION" if self.is_violation else "reduction"
        return f"{self.field_name}: {self.proposed!r} -> {self.applied!r} ({kind}: {self.reason})"


@dataclass(frozen=True)
class PolicyDecision:
    verdict: PolicyVerdict
    applied: AppliedAction | None = None
    #: Only present when the verdict is PASSED. This is the capability.
    token: PolicyToken | None = None
    clamps: tuple[Clamp, ...] = ()
    block_reasons: tuple[str, ...] = ()
    escalation_rung: EscalationRung = EscalationRung.A0_AUTONOMOUS
    stopping: StoppingVerdict | None = None

    @property
    def may_execute(self) -> bool:
        return self.verdict is PolicyVerdict.PASSED and self.token is not None

    @property
    def intercepted(self) -> bool:
        """Whether the firewall reduced the proposal at all."""
        return bool(self.clamps)

    @property
    def violations(self) -> tuple[Clamp, ...]:
        """Clamps where the model breached a hard bound.

        This is the number §14.6 reports as *unsafe proposals intercepted*, and
        it is deliberately narrower than `clamps`: routine consent downgrades
        are policy working, not the model misbehaving, and counting them would
        inflate the metric with the system's own good behaviour.
        """
        return tuple(c for c in self.clamps if c.is_violation)


# ---------------------------------------------------------------------------
# Sanitisers. Every one exists because the naive version has a hole.
# ---------------------------------------------------------------------------
def _sanitise_discount(proposed: float, limits: PolicyLimitsFull, clamps: list[Clamp]) -> float:
    """Reduce a proposed discount to something permitted.

    Handles the case a `min()` silently gets wrong: **NaN**. Every comparison
    with NaN is False, so ``min(nan, 7.0)`` returns ``nan`` and a NaN discount
    would sail through a naive bound check and then be multiplied into an
    amount. Infinity and negatives are equally non-negotiable.
    """
    if not isinstance(proposed, (int, float)) or not math.isfinite(float(proposed)):
        clamps.append(
            Clamp(
                "discount_pct",
                proposed,
                0.0,
                "non-finite value (NaN or infinity)",
                is_violation=True,
            )
        )
        return 0.0

    value = float(proposed)
    if value < 0:
        clamps.append(Clamp("discount_pct", value, 0.0, "negative discount", is_violation=True))
        return 0.0

    if value > limits.max_discount_pct:
        # Clamped to the SAFE DEFAULT, not the ceiling. Clamping to the ceiling
        # would teach a model that asking for 90% reliably yields the maximum.
        applied = min(limits.default_discount_pct, limits.max_discount_pct)
        clamps.append(
            Clamp(
                "discount_pct",
                value,
                applied,
                f"exceeds ceiling {limits.max_discount_pct}%; reset to the safe default",
                is_violation=True,
            )
        )
        return applied
    return value


def _apply_absolute_cap(
    pct: float, amount_paise: int, limits: PolicyLimitsFull, clamps: list[Clamp]
) -> tuple[float, int]:
    """Enforce the rupee ceiling as well as the percentage ceiling.

    7% of a ₹50,000 cart is ₹3,500, which is inside the percentage bound and
    far outside anything a merchant intended. The absolute cap is what makes
    the bound meaningful on large carts.
    """
    if pct <= 0 or amount_paise <= 0:
        return 0.0, 0

    discount_paise = int(amount_paise * pct / 100)
    if discount_paise <= limits.max_discount_absolute_paise:
        return pct, discount_paise

    capped_paise = limits.max_discount_absolute_paise
    capped_pct = round(capped_paise * 100 / amount_paise, 4)
    clamps.append(
        Clamp(
            "discount_amount_paise",
            discount_paise,
            capped_paise,
            f"exceeds absolute cap ₹{limits.max_discount_absolute_paise / 100:,.0f}; "
            f"percentage reduced {pct}% -> {capped_pct}%",
        )
    )
    return capped_pct, capped_paise


def _sanitise_expiry(proposed: int, limits: PolicyLimitsFull, clamps: list[Clamp]) -> int:
    """Bound link validity into [15 min, 24 h].

    A negative or zero expiry would create a link dead on arrival; a 30-day one
    would outlive the recovery window and let a customer pay at a price the
    merchant no longer offers.
    """
    try:
        value = int(proposed)
    except (TypeError, ValueError, OverflowError):
        clamps.append(
            Clamp("link_expiry_minutes", proposed, limits.link_expiry_minutes, "not an integer")
        )
        return limits.link_expiry_minutes

    if value < limits.link_expiry_min_minutes:
        clamps.append(
            Clamp(
                "link_expiry_minutes",
                value,
                limits.link_expiry_min_minutes,
                f"below the {limits.link_expiry_min_minutes}-minute floor",
            )
        )
        return limits.link_expiry_min_minutes
    if value > limits.link_expiry_max_minutes:
        clamps.append(
            Clamp(
                "link_expiry_minutes",
                value,
                limits.link_expiry_max_minutes,
                f"above the {limits.link_expiry_max_minutes}-minute ceiling",
            )
        )
        return limits.link_expiry_max_minutes
    return value


def _escalation_rung(
    amount_paise: int,
    discount_pct: float,
    limits: PolicyLimitsFull,
    clamps: list[Clamp],
    *,
    strategy: RecoveryStrategy,
) -> EscalationRung:
    """Authority ladder (§8.3, dimension A).

    INC-031. The ladder is about **authority to act**, and it used to be
    computed from the amount alone -- so a ``NO_ACTION`` on a large order was
    escalated, and a reviewer was asked to approve doing nothing. They can
    neither grant nor withhold anything.

    RC-0023 was the case that showed it: RISK_BLOCKED, correctly left alone,
    sitting in a queue of twenty demanding human attention. A queue padded with
    unactionable items gets rubber-stamped, and the items that *do* matter get
    rubber-stamped with them.

    The exemption is only sound because NO_ACTION cannot move money, which
    ``test_no_action_can_never_move_money`` proves rather than assumes.
    Restraint is still reported -- the morning briefing's "what I chose not to
    do" section is where a decision a human should know about but cannot act on
    belongs.
    """
    if strategy is RecoveryStrategy.NO_ACTION:
        return EscalationRung.A0_AUTONOMOUS
    if amount_paise >= limits.hitl_dual_signal_amount_paise:
        return EscalationRung.A3_APPROVAL_DUAL
    if amount_paise >= limits.max_autonomous_amount_paise:
        return EscalationRung.A2_APPROVAL
    # A *violation* means the model wanted something outside a hard bound --
    # worth a human's eyes even below the amount threshold. A routine reduction
    # (consent downgrade, absolute cap) is policy working, and escalating it
    # would send every no-marketing-consent recovery to an approval queue.
    if any(c.is_violation for c in clamps):
        return EscalationRung.A2_APPROVAL
    if discount_pct > 0:
        return EscalationRung.A1_FLAGGED
    return EscalationRung.A0_AUTONOMOUS


# ---------------------------------------------------------------------------
def evaluate_policy(
    proposal: RecoveryProposal,
    ctx: PolicyContext,
    *,
    now: datetime,
) -> PolicyDecision:
    """The firewall. Pure; the only place a :class:`PolicyToken` is minted.

    Order is deliberate. Hard refusals come before clamping, because clamping
    an action that must not happen at all wastes work and — worse — could
    produce a plausible-looking applied action for a case that should have been
    refused outright.
    """
    clamps: list[Clamp] = []
    blocks: list[str] = []

    # -- 0. Single source of truth for the proposed action ------------------
    # The stopping rules need to know what is being proposed (a marketing
    # message needs consent; a discount consumes the discount budget), and the
    # proposal carries those fields too. Rather than trust the caller to keep
    # two copies in sync, the proposal is copied into the stopping context
    # here. They previously could disagree, and did: a MARKETING proposal
    # evaluated against a stopping context still defaulting to TRANSACTIONAL
    # had its discount stripped for the wrong reason (INC-007).
    stopping_ctx = replace(
        ctx.stopping,
        proposed_message_class=proposal.message_class,
        proposed_discount_pct=(
            float(proposal.discount_pct)
            if isinstance(proposal.discount_pct, (int, float))
            and math.isfinite(float(proposal.discount_pct))
            else 0.0
        ),
    )

    # -- 1. Hard refusals: things no clamp can rescue -----------------------
    if ctx.order_amount_paise <= 0:
        blocks.append(f"order amount is not positive ({ctx.order_amount_paise})")

    # -- 2. Stopping rules, re-checked immediately before acting ------------
    stopping = evaluate_stopping(stopping_ctx)
    if stopping.decision is Decision.STOP:
        rule = stopping.blocking_rule.value if stopping.blocking_rule else "unknown"
        blocks.append(f"stopping rule {rule}: {_blocking_detail(stopping)}")

    # Degradations from the stopping engine are inputs to the clamping below:
    # a consent downgrade must reduce the discount before it is bounded, or we
    # would bound a discount that should not exist.
    degraded_ctx = apply_degradations(stopping_ctx, stopping)
    effective_class = degraded_ctx.proposed_message_class
    if effective_class is not proposal.message_class:
        clamps.append(
            Clamp(
                "message_class",
                proposal.message_class.value,
                effective_class.value,
                "consent class downgrade",
            )
        )

    # -- 3. The control arm never acts --------------------------------------
    # Not a failure: a CONTROL case is doing its job by doing nothing, and its
    # outcome is what makes the recovery number falsifiable (§14.2).
    if ctx.arm is ExperimentArm.CONTROL:
        blocks.append("assigned to the CONTROL arm; observed without intervention")

    if blocks:
        return PolicyDecision(
            verdict=PolicyVerdict.BLOCKED,
            clamps=tuple(clamps),
            block_reasons=tuple(blocks),
            stopping=stopping,
        )

    # -- 4. Clamp the proposal down to something permitted ------------------
    discount_pct = _sanitise_discount(proposal.discount_pct, ctx.limits, clamps)
    if effective_class is MessageClass.TRANSACTIONAL and discount_pct > 0:
        # A discount is a marketing offer. If the message has been downgraded
        # to transactional, the discount cannot ride along inside it.
        clamps.append(
            Clamp("discount_pct", discount_pct, 0.0, "transactional message cannot carry an offer")
        )
        discount_pct = 0.0

    discount_pct, discount_paise = _apply_absolute_cap(
        discount_pct, ctx.order_amount_paise, ctx.limits, clamps
    )
    if proposal.strategy is RecoveryStrategy.NO_ACTION and (discount_pct or discount_paise):
        # There is nothing to discount. An applied action reading "NO_ACTION at
        # 15% off" is incoherent, and it would put a discount figure into the
        # audit payload and the approval hash for an action that never happens.
        #
        # Recorded as a clamp rather than zeroed silently: "every reduction is
        # recorded" is a property this file is proved against, and the property
        # suite caught the first version of this fix for violating it. Not a
        # violation though -- a discount alongside NO_ACTION is incoherent
        # rather than dangerous, and flagging it as one would re-escalate the
        # case the fix exists to stop escalating.
        clamps.append(
            Clamp(
                "discount_pct",
                discount_pct,
                0.0,
                "NO_ACTION: nothing to discount",
                is_violation=False,
            )
        )
        discount_pct, discount_paise = 0.0, 0
    expiry = _sanitise_expiry(proposal.link_validity_minutes, ctx.limits, clamps)
    charge_amount = ctx.order_amount_paise - discount_paise
    rung = _escalation_rung(
        ctx.order_amount_paise,
        discount_pct,
        ctx.limits,
        clamps,
        strategy=proposal.strategy,
    )

    applied = AppliedAction(
        case_id=ctx.case_id,
        strategy=proposal.strategy,
        amount_paise=ctx.order_amount_paise,
        discount_pct=discount_pct,
        discount_amount_paise=discount_paise,
        charge_amount_paise=charge_amount,
        link_expiry_minutes=expiry,
        channel=proposal.channel,
        message_class=effective_class,
        escalation_rung=rung,
        reference_id=make_reference_id(ctx.case_id, ctx.attempt_no),
        attempt_no=ctx.attempt_no,
        send_after=stopping.defer_until if stopping.decision is Decision.DEFER else None,
    )

    # -- 5. Escalate to a human where authority requires it -----------------
    needs_human = rung in (EscalationRung.A2_APPROVAL, EscalationRung.A3_APPROVAL_DUAL)
    if needs_human and ctx.approved_action_hash != applied.content_hash():
        # Either not yet approved, or approved for a *different* action. The
        # second case is the one worth guarding: approving one action and
        # executing another is what an approval gate exists to prevent (§13.5).
        return PolicyDecision(
            verdict=PolicyVerdict.ESCALATE_HITL,
            applied=applied,
            clamps=tuple(clamps),
            block_reasons=(
                (
                    f"amount ₹{ctx.order_amount_paise / 100:,.0f} requires approval at rung "
                    f"{rung.value}"
                )
                if ctx.approved_action_hash is None
                else "the approved action does not match the action about to execute",
            ),
            escalation_rung=rung,
            stopping=stopping,
        )

    # -- 6. Mint the capability ---------------------------------------------
    return PolicyDecision(
        verdict=PolicyVerdict.PASSED,
        applied=applied,
        token=mint(applied, minted_at=now),
        clamps=tuple(clamps),
        escalation_rung=rung,
        stopping=stopping,
    )


def _blocking_detail(stopping: StoppingVerdict) -> str:
    for result in stopping.results:
        if result.rule is stopping.blocking_rule:
            return result.detail
    return "blocked"
