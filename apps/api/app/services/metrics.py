"""Dashboard metrics, each figure carrying its provenance (§14.5, §19.2).

Every query here answers one tile. They are separated from the router so the
arithmetic can be tested without HTTP, and so the *same* function produces the
number for the API, the briefing and the CLI — three surfaces reporting three
slightly different totals is how a demo loses an argument it should win.

The rule this module exists to enforce: **gross and net are always adjacent,
and neither is ever returned alone.** ``/metrics/overview`` cannot emit gross
recovery without also emitting net incremental, because a viewer given only
the larger number will take it, and both are true answers to different
questions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.provenance import Count, Figure, Provenance
from app.db.enums import (
    TERMINAL_STATUSES,
    ApprovalStatus,
    CaseStatus,
    DLQStatus,
    ExperimentArm,
    LLMSource,
    OutboxStatus,
    RecoveryVerifier,
    StoppingRule,
)
from app.db.models import (
    ApprovalRequest,
    DeadLetter,
    ExperimentAssignment,
    LLMCall,
    Outbox,
    RecoveryCase,
)

__all__ = ["CostReport", "OverviewReport", "cost_report", "overview", "stopping_rule_counts"]

#: Published paid-tier rate, for the projection only. Actual spend is zero and
#: every figure derived from this is ESTIMATED, never SIMULATED — it is a price
#: list, not a measurement.
_INR_PER_MILLION_INPUT_TOKENS = 7.0
_INR_PER_MILLION_OUTPUT_TOKENS = 28.0


#: A verifying event id starting with this was produced by the batch
#: simulator, not by Razorpay. The distinction is load-bearing: the schema's
#: `recovery_requires_proof` CHECK forces *an* id, and a simulator that wrote
#: a realistic-looking one would silently promote seeded outcomes to
#: RAZORPAY_VERIFIED -- precisely the overclaim the badge exists to prevent.
SIMULATED_EVENT_PREFIX: Final = "sim_evt_"


@dataclass(frozen=True)
class OverviewReport:
    """The five tiles. Gross and net travel together, always.

    Gross is **two** figures, not one. Recoveries proven by a real signed
    webhook and recoveries produced by the batch simulator carry different
    badges, and §14.5 forbids a tile mixing provenance: a figure that would
    need two badges is two figures. Summing them would produce a number that
    is neither, labelled as whichever the author preferred.
    """

    at_risk: Figure
    gross_recovered: Figure
    gross_simulated: Figure
    net_incremental: Figure
    open_cases: Count
    control_cases: Count
    interceptions: Count
    pending_approvals: Count

    def as_dict(self) -> dict[str, Any]:
        return {
            "at_risk": self.at_risk.as_dict(),
            # Adjacent in the payload as well as on screen. A client that
            # renders the first key it finds still gets both.
            "gross_recovered": self.gross_recovered.as_dict(),
            "gross_simulated": self.gross_simulated.as_dict(),
            "net_incremental": self.net_incremental.as_dict(),
            "open_cases": self.open_cases.as_dict(),
            "control_cases": self.control_cases.as_dict(),
            "interceptions": self.interceptions.as_dict(),
            "pending_approvals": self.pending_approvals.as_dict(),
        }


async def overview(session: AsyncSession, *, clock: Clock) -> OverviewReport:
    """The headline tiles.

    ``gross_recovered`` is RAZORPAY_VERIFIED because the schema will not let it
    be anything else: ``recovery_requires_proof`` is a CHECK constraint, so a
    recovered amount cannot exist in the database without an id that verified
    it. The badge is not a claim we make about the number, it is a claim the
    database already enforces.

    That id is **not always a webhook event id**, and this docstring used to say
    it was. A recovery proven by ``workers/reconcile`` carries a payment or
    payment-link id from a direct API read. ``recovery_verified_via`` records
    which, and the basis string reports the split rather than asserting a
    webhook -- the first live recovery of 2026-08-31 was a poll, because the
    webhook was lost to a dead tunnel.
    """
    open_statuses = [s for s in CaseStatus if s not in TERMINAL_STATUSES]

    at_risk_paise = (
        await session.scalar(
            select(func.coalesce(func.sum(RecoveryCase.amount_paise), 0)).where(
                RecoveryCase.status.in_(open_statuses)
            )
        )
    ) or 0
    open_count = (
        await session.scalar(
            select(func.count(RecoveryCase.id)).where(RecoveryCase.status.in_(open_statuses))
        )
    ) or 0
    # Split by who proved it. A real Razorpay event id and a simulator's id
    # are different claims and cannot share a tile.
    # Filtered on the typed column, with the id-prefix check retained as a
    # belt-and-braces second condition rather than as the primary test. Either
    # alone is enough to exclude a simulated row; requiring BOTH means a bug in
    # one cannot promote seeded outcomes onto the verified tile.
    verified_paise = (
        await session.scalar(
            select(func.coalesce(func.sum(RecoveryCase.recovered_amount_paise), 0)).where(
                RecoveryCase.recovery_verified_by.is_not(None),
                RecoveryCase.recovery_verified_via.in_(
                    [RecoveryVerifier.WEBHOOK, RecoveryVerifier.API_RECONCILIATION]
                ),
                ~RecoveryCase.recovery_verified_by.startswith(SIMULATED_EVENT_PREFIX),
            )
        )
    ) or 0
    # How it was proven, per mechanism, so the basis can name what actually
    # happened instead of asserting a webhook every time.
    by_verifier = {
        verifier: int(count or 0)
        for verifier, count in (
            await session.execute(
                select(
                    RecoveryCase.recovery_verified_via,
                    func.count(RecoveryCase.id),
                )
                .where(
                    RecoveryCase.recovery_verified_via.in_(
                        [RecoveryVerifier.WEBHOOK, RecoveryVerifier.API_RECONCILIATION]
                    )
                )
                .group_by(RecoveryCase.recovery_verified_via)
            )
        ).all()
    }
    webhook_count = by_verifier.get(RecoveryVerifier.WEBHOOK, 0)
    poll_count = by_verifier.get(RecoveryVerifier.API_RECONCILIATION, 0)
    simulated_paise = (
        await session.scalar(
            select(func.coalesce(func.sum(RecoveryCase.recovered_amount_paise), 0)).where(
                RecoveryCase.recovery_verified_by.startswith(SIMULATED_EVENT_PREFIX)
            )
        )
    ) or 0
    control_count = (
        await session.scalar(
            select(func.count(ExperimentAssignment.case_id)).where(
                ExperimentAssignment.arm == ExperimentArm.CONTROL
            )
        )
    ) or 0
    intercepted = (
        await session.scalar(
            select(func.count(RecoveryCase.id)).where(RecoveryCase.stopping_rule_fired.is_not(None))
        )
    ) or 0
    pending = (
        await session.scalar(
            select(func.count(ApprovalRequest.id)).where(
                ApprovalRequest.status == ApprovalStatus.PENDING,
                ApprovalRequest.expires_at > clock.now_utc(),
            )
        )
    ) or 0

    # Net incremental needs the control arm, which lives in the attribution
    # service. Rather than recompute the lift here -- two implementations of
    # one number is the INC-007 shape -- the router composes them. Overview
    # reports gross with an explicit pointer, so a client that renders only
    # this endpoint still cannot show gross as though it were the whole story.
    net = Figure(
        paise=0,
        provenance=Provenance.SIMULATED,
        basis=(
            "computed by /metrics/attribution against the holdout control arm; "
            "gross alone overstates our contribution roughly threefold"
        ),
    )

    return OverviewReport(
        at_risk=Figure(
            paise=int(at_risk_paise),
            provenance=Provenance.SIMULATED,
            basis=f"sum of amount over {open_count} non-terminal cases in the seeded corpus",
        ),
        gross_recovered=Figure(
            paise=int(verified_paise),
            provenance=Provenance.RAZORPAY_VERIFIED,
            basis=(
                (
                    f"proven by Razorpay itself: {webhook_count} by signed webhook, "
                    f"{poll_count} by direct API reconciliation. Both are Razorpay "
                    "asserting the payment -- a poll needs no public URL, which is "
                    "why a lost webhook cannot cost a real recovery (DEC-037). The "
                    "recovery_requires_proof CHECK makes an unverified recovery "
                    "unrepresentable."
                )
                if (webhook_count or poll_count)
                else (
                    "nothing has been proven by Razorpay yet, so this is zero. It "
                    "counts only cases Razorpay itself confirmed, by signed webhook "
                    "or by direct API reconciliation."
                )
            ),
        ),
        gross_simulated=Figure(
            paise=int(simulated_paise),
            provenance=Provenance.SIMULATED,
            basis=(
                "sum of recovered_amount over cases settled by the batch simulator. "
                "The attribution machinery is real and unmodified; the customer "
                "responses are a declared parameter. Kept separate from the verified "
                "figure because they are different claims (§14.5)."
            ),
        ),
        net_incremental=net,
        open_cases=Count(
            value=int(open_count),
            provenance=Provenance.SIMULATED,
            basis="cases not in a terminal state",
        ),
        control_cases=Count(
            value=int(control_count),
            provenance=Provenance.SIMULATED,
            basis="cases held in the control arm and deliberately not acted on",
        ),
        interceptions=Count(
            value=int(intercepted),
            provenance=Provenance.SIMULATED,
            basis="cases where a stopping rule fired and blocked or degraded an action",
        ),
        pending_approvals=Count(
            value=int(pending),
            provenance=Provenance.SIMULATED,
            basis="approvals awaiting a human, excluding those past their TTL",
        ),
    )


async def stopping_rule_counts(session: AsyncSession) -> dict[str, Any]:
    """Firing counts by rule id. Makes the brakes visible (§19.2).

    Every one of the twelve rules is listed, including those that fired zero
    times. Returning only the non-zero ones would make an inactive rule
    indistinguishable from an absent one, and "which brakes exist" is the
    question this panel answers.
    """
    rows = (
        await session.execute(
            select(RecoveryCase.stopping_rule_fired, func.count(RecoveryCase.id))
            .where(RecoveryCase.stopping_rule_fired.is_not(None))
            .group_by(RecoveryCase.stopping_rule_fired)
        )
    ).all()
    fired = {rule.value: int(count) for rule, count in rows if rule is not None}

    return {
        "rules": [
            {
                "rule": rule.value,
                "fired": fired.get(rule.value, 0),
            }
            for rule in StoppingRule
        ],
        "total_interceptions": sum(fired.values()),
        "provenance": Provenance.SIMULATED.value,
        "basis": "counted over the seeded corpus; every rule is listed, including zeroes",
    }


@dataclass(frozen=True)
class CostReport:
    """What the agent spent, and what it would cost at published rates."""

    llm_calls: int
    by_source: dict[str, int]
    input_tokens: int
    output_tokens: int
    actual_spend: Figure
    projected_spend: Figure
    cache_hit_rate: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "llm_calls": self.llm_calls,
            "by_source": self.by_source,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "actual_spend": self.actual_spend.as_dict(),
            "projected_spend": self.projected_spend.as_dict(),
            "cache_hit_rate": round(self.cache_hit_rate, 4),
        }


async def cost_report(session: AsyncSession) -> CostReport:
    """Free-tier consumption and the paid-rate projection.

    Actual spend is ₹0 and says so as a *verified* figure, not an estimate:
    zero is exactly what was spent, and hedging it would be false modesty in
    the wrong direction. The projection is ESTIMATED because a published price
    list is not a measurement.
    """
    rows = (
        await session.execute(
            select(LLMCall.source, func.count(LLMCall.id)).group_by(LLMCall.source)
        )
    ).all()
    by_source = {source.value: int(count) for source, count in rows if source is not None}
    total = sum(by_source.values())

    input_tokens = int(
        (await session.scalar(select(func.coalesce(func.sum(LLMCall.input_tokens), 0)))) or 0
    )
    output_tokens = int(
        (await session.scalar(select(func.coalesce(func.sum(LLMCall.output_tokens), 0)))) or 0
    )

    projected_inr = (
        input_tokens / 1_000_000 * _INR_PER_MILLION_INPUT_TOKENS
        + output_tokens / 1_000_000 * _INR_PER_MILLION_OUTPUT_TOKENS
    )
    cached = by_source.get(LLMSource.CACHED.value, 0)

    return CostReport(
        llm_calls=total,
        by_source=by_source,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        actual_spend=Figure(
            paise=0,
            # NOT RAZORPAY_VERIFIED. Razorpay has no view on what we spent with
            # Google, and the badge means "a signed webhook proves this". The
            # old code reasoned that zero is exactly what was spent and so
            # deserved the strongest badge -- true about the number, wrong about
            # the badge, which names a source rather than a confidence level.
            # Spending it here devalues it on the tiles where it is load-bearing.
            provenance=Provenance.SIMULATED,
            basis=(
                "counted from our own llm_calls ledger: Gemini free tier plus a "
                "committed response cache, so nothing was billed. Razorpay is "
                "not involved in this figure and does not verify it."
            ),
        ),
        projected_spend=Figure(
            paise=round(projected_inr * 100),
            provenance=Provenance.ESTIMATED,
            basis=(
                f"{input_tokens} input + {output_tokens} output tokens at published paid "
                # :.2f, not the bare float. These rendered as `Rs 7.0` and
                # `Rs 28.0`, which breaks the project's own rule that money
                # carries two decimals -- the same defect as the `Rs 20,055.6`
                # in the approvals queue, two files over.
                f"rates (Rs {_INR_PER_MILLION_INPUT_TOKENS:.2f}/Rs "
                f"{_INR_PER_MILLION_OUTPUT_TOKENS:.2f} per million); a price list, not a bill"
            ),
        ),
        cache_hit_rate=(cached / total) if total else 0.0,
    )


async def queue_depths(session: AsyncSession) -> dict[str, int]:
    """Outbox and DLQ depth, for /health/deep.

    Reported as plain integers rather than Figures: these are operational
    gauges for a health probe, not numbers anyone will mistake for revenue.
    """
    outbox = int(
        (
            await session.scalar(
                select(func.count(Outbox.id)).where(Outbox.status == OutboxStatus.PENDING)
            )
        )
        or 0
    )
    sending = int(
        (
            await session.scalar(
                select(func.count(Outbox.id)).where(Outbox.status == OutboxStatus.SENDING)
            )
        )
        or 0
    )
    dlq = int(
        (
            await session.scalar(
                select(func.count(DeadLetter.id)).where(DeadLetter.status == DLQStatus.OPEN)
            )
        )
        or 0
    )
    return {"outbox_pending": outbox, "outbox_sending": sending, "dlq_pending": dlq}
