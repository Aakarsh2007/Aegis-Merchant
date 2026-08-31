"""A genuinely randomised holdout against real Razorpay Test Mode.

Why this exists
---------------

The dashboard's three numbers answer three questions, and until now only two
had any real-provider evidence behind them:

* **₹1.00 `RAZORPAY_VERIFIED`** — *can this execute and verify a recovery
  through Razorpay?* Yes. Proven on one treated case.
* **₹60,217 `SIMULATED`** — *what might it recover at scale?* A declared
  response model. No provider involved.
* **"Did RevPilot cause additional customers to pay?"** — unproven, and
  `docs/PRE-REGISTRATION.md` says exactly what would settle it: 1,592 cases and
  a DLT-registered merchant.

What was missing sat between the first and the third. The **holdout** — the
mechanism the entire incremental figure rests on — had never touched real
provider data at all. It worked on a seeded corpus, where our own code decides
who pays.

This module runs both arms against the real API. Treated cases get real payment
links. Control cases get **nothing sent** and are settleable only through the
merchant's own checkout, so a control customer who pays is recorded as the
counterfactual rather than credited to us.

What it proves, and what it does not
------------------------------------

**Proves, on real provider data:** the arm assignment is deterministic and
recomputable; a treated settlement matches on a reference we issued and counts;
a control settlement finds no issued reference and resolves *organically*; the
arm-level rates and the lift arithmetic run on `RAZORPAY_VERIFIED` events only;
and no outreach artefact of any kind exists for a control case.

**Does not prove anything about customer behaviour.** The payments are made by
the developer. There is no independent population, n is two orders of magnitude
below §5 of the pre-registration, and the interval is uselessly wide. This is a
test of the instrument. Reporting it as a test of the effect would be precisely
the error the pre-registration exists to prevent, so the API response and the
dashboard tile both say so in the payload rather than in a caption.

Why the control arm gets a link at all
--------------------------------------

Because the counterfactual the holdout needs is *"the merchant's ordinary
checkout stays open to this customer"* (pre-registration §9.4) — not *"this
customer has no way to pay"*. Without a settleable path, control conversion is
pinned at 0% by construction and the measured lift is inflated to whatever the
treated arm did. That link is created with `notify` off, is never messaged, has
no outbox row and no `RecoveryAction`, and carries an `rvpo_` reference in a
namespace disjoint from the `rvp_` one the recovery path uses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.graph import run_case
from app.agent.nodes import AgentDeps
from app.agent.state import RecoveryState
from app.config import Settings
from app.core.clock import Clock
from app.db.enums import (
    ActionType,
    CaseStatus,
    ExperimentArm,
    OutboxStatus,
    Playbook,
)
from app.db.ids import idempotency_hash, new_id, observed_reference_id
from app.db.models import (
    Customer,
    ExperimentAssignment,
    Merchant,
    Outbox,
    RecoveryAction,
    RecoveryCase,
)
from app.llm.cache import CachedAdapter, ResponseCache
from app.services.experiments import assign_arm
from app.tools.audit import AuditChain
from app.tools.razorpay_client import RazorpayProvider

log = logging.getLogger(__name__)

#: Its own key, so these cases can never be pooled with the seeded corpus's
#: arms. Two experiments sharing a key would make one population out of two,
#: which is the fastest way to turn a real measurement into a meaningless one.
EXPERIMENT_KEY = "revpilot_testmode_holdout_v1"

#: Balanced, matching `docs/PRE-REGISTRATION.md` §4 rather than the demo's 19%.
#: The document's reasoning applies here even though n is tiny: it is the design
#: being exercised, and exercising a different one would prove the wrong thing.
CONTROL_FRACTION = 0.5

#: ₹1. The amount is irrelevant to what is being demonstrated, and a large one
#: would invite the misreading this module's docstring exists to prevent.
AMOUNT_PAISE = 100


@dataclass
class ArmResult:
    """One arm's cases, and what happened to each."""

    arm: ExperimentArm
    case_ids: list[str] = field(default_factory=list)
    links: dict[str, str] = field(default_factory=dict)
    stopped: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.case_ids)


@dataclass
class ExperimentResult:
    treatment: ArmResult
    control: ArmResult
    experiment_key: str
    control_fraction: float

    def render(self) -> str:
        line = "=" * 70
        out = [
            line,
            "  RANDOMISED HOLDOUT, REAL RAZORPAY TEST MODE",
            "  An apparatus test. NOT a measurement of customer behaviour.",
            line,
            "",
            f"  experiment key    {self.experiment_key}",
            f"  control fraction  {self.control_fraction:.0%}  (balanced, per PRE-REGISTRATION section 4)",
            f"  arms              treated {self.treatment.size}, control {self.control.size}",
            "",
            "  TREATED -- real payment links, created through the live API:",
        ]
        for case_id in self.treatment.case_ids:
            if case_id in self.treatment.links:
                out.append(f"    {case_id}  {self.treatment.links[case_id]}")
            elif case_id in self.treatment.stopped:
                out.append(f"    {case_id}  STOPPED: {self.treatment.stopped[case_id]}")
            else:
                out.append(f"    {case_id}  ERROR: {self.treatment.errors.get(case_id, '?')}")
        out += [
            "",
            "  CONTROL -- never contacted. No message, no outbox row, no action.",
            "  The link below is the MERCHANT'S OWN CHECKOUT, which stays open to",
            "  these customers by design. Paying it is recorded as ORGANIC, not as",
            "  a recovery of ours. That is the guard the whole lift figure rests on.",
        ]
        for case_id in self.control.case_ids:
            if case_id in self.control.links:
                out.append(f"    {case_id}  {self.control.links[case_id]}")
            else:
                out.append(f"    {case_id}  ERROR: {self.control.errors.get(case_id, '?')}")
        out += [
            "",
            "  NEXT: pay some of each with card 4111 1111 1111 1111, any future",
            "  expiry, any CVV. Razorpay POSTs the webhook; signatures are",
            "  verified; treated payments become RAZORPAY_VERIFIED recoveries and",
            "  control payments become RESOLVED_ORGANIC. Then:",
            "",
            "    curl localhost:8000/api/v1/metrics/holdout",
            "",
            "  WHAT THIS CANNOT SHOW: the payments are made by the developer, so",
            f"  there is no independent population. n = {self.treatment.size + self.control.size}"
            f" against the {1592} the",
            "  pre-registration requires. The interval will be uselessly wide and",
            "  the endpoint reports no significance verdict at all.",
            line,
        ]
        return "\n".join(out)


async def _fixtures(session: AsyncSession) -> tuple[Merchant, Customer]:
    merchant = (await session.execute(select(Merchant))).scalars().first()
    customer = (await session.execute(select(Customer))).scalars().first()
    if merchant is None or customer is None:
        raise RuntimeError("no merchant or customer in the database; run `python tasks.py seed`")
    return merchant, customer


def _state(
    case_id: str,
    *,
    merchant: Merchant,
    customer: Customer,
    now: Any,
) -> RecoveryState:
    """A case shaped like the live-verified one, so the path is the same one.

    `payment_cancelled` with `error_source: customer` is what Razorpay actually
    sent for the real Test Mode failure (INC-015), rather than a spelling we
    invented.
    """
    return RecoveryState(
        case_id=case_id,
        merchant_id=merchant.id,
        customer_id=customer.id,
        playbook=Playbook.PAYMENT_FAILURE,
        amount_paise=AMOUNT_PAISE,
        order_id=f"order_xp_{case_id.lower()}",
        error_source="customer",
        error_step="payment_authentication",
        error_reason="payment_cancelled",
        method="card",
        issuer="HDFC",
        customer_first_name=customer.first_name,
        consent_transactional=True,
        autopilot_enabled=merchant.autopilot_enabled,
        order_status="created",
        window_expires_at=now + timedelta(hours=24),
    )


async def _create_link(
    provider: RazorpayProvider,
    *,
    case_id: str,
    reference: str,
    description: str,
    customer_name: str,
    now: Any,
) -> dict[str, Any]:
    """One real Razorpay payment link.

    `notify` is off for **both** arms. For the treated arm because we dispatch
    messaging ourselves through the consent-aware channel (two messages for one
    recovery would breach the contact cap the agent just checked); for the
    control arm because notifying would be contact, and a contacted control case
    is not a control case.
    """
    result = await provider._request(
        "POST",
        "/payment_links",
        json={
            "amount": AMOUNT_PAISE,
            "currency": "INR",
            "accept_partial": False,
            "reference_id": reference,
            "description": description,
            "customer": {
                "name": customer_name,
                "contact": "+919000000000",
                "email": "testmode@example.com",
            },
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
            "expire_by": int(now.timestamp()) + 6 * 24 * 3600,
            "notes": {"revpilot_case": case_id, "revpilot_experiment": EXPERIMENT_KEY},
        },
    )
    return dict(result)


async def run_testmode_experiment(
    factory: async_sessionmaker[AsyncSession],
    *,
    clock: Clock,
    settings: Settings,
    n: int = 10,
    actor: str = "cli:experiment",
) -> ExperimentResult:
    """Create `n` real cases, randomised, and links for both arms."""
    if n < 2:
        raise ValueError("an experiment with fewer than two cases has no arms")
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise RuntimeError("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are required")

    treatment = ArmResult(arm=ExperimentArm.TREATMENT)
    control = ArmResult(arm=ExperimentArm.CONTROL)
    provider = RazorpayProvider(
        settings.razorpay_key_id, settings.razorpay_key_secret, timeout_s=25.0
    )

    async with factory() as session:
        merchant, customer = await _fixtures(session)
        merchant_id, customer_id, first_name = merchant.id, customer.id, customer.first_name
        existing = int(
            await session.scalar(
                select(func.count(RecoveryCase.id)).where(RecoveryCase.id.like("RC-XP%"))
            )
            or 0
        )

    for index in range(n):
        # Sequential and stable, so a re-run does not collide and an auditor can
        # recompute every assignment from the id alone.
        case_id = f"RC-XP{existing + index + 1:04d}"
        assignment = assign_arm(
            case_id, experiment_key=EXPERIMENT_KEY, control_fraction=CONTROL_FRACTION
        )

        if assignment.arm is ExperimentArm.CONTROL:
            await _run_control(
                factory,
                case_id=case_id,
                merchant_id=merchant_id,
                customer_id=customer_id,
                first_name=first_name,
                assignment_hash=assignment.assignment_hash,
                provider=provider,
                clock=clock,
                actor=actor,
                result=control,
            )
        else:
            await _run_treated(
                factory,
                case_id=case_id,
                merchant_id=merchant_id,
                customer_id=customer_id,
                first_name=first_name,
                assignment_hash=assignment.assignment_hash,
                provider=provider,
                settings=settings,
                clock=clock,
                actor=actor,
                result=treatment,
            )

    return ExperimentResult(
        treatment=treatment,
        control=control,
        experiment_key=EXPERIMENT_KEY,
        control_fraction=CONTROL_FRACTION,
    )


async def _persist(
    session: AsyncSession,
    *,
    case_id: str,
    merchant_id: str,
    customer_id: str,
    arm: ExperimentArm,
    assignment_hash: str,
    now: Any,
    window_expires_at: Any,
    status: CaseStatus = CaseStatus.MONITORING,
    **case_fields: Any,
) -> RecoveryCase:
    """The case and its assignment, together, in one transaction.

    Written before any provider call. A case with a link and no arm assignment
    would be unanalysable, and an assignment recorded after the outcome is the
    single easiest way to fabricate a lift.
    """
    unknown = set(case_fields) - {c.key for c in RecoveryCase.__mapper__.column_attrs}
    if unknown:
        # `**case_fields: Any` means mypy cannot catch a misspelled column, and
        # SQLAlchemy's own error arrives at flush time with a confusing message.
        # An earlier version passed `status_override=None` here and would have
        # raised on the first stopped treated case, in a code path no test
        # covered.
        raise TypeError(f"not columns on RecoveryCase: {sorted(unknown)}")

    case = RecoveryCase(
        id=case_id,
        merchant_id=merchant_id,
        customer_id=customer_id,
        playbook=Playbook.PAYMENT_FAILURE,
        status=status,
        amount_paise=AMOUNT_PAISE,
        attempt_no=1,
        idempotency_hash=idempotency_hash(merchant_id, case_id, "PAYMENT_FAILURE"),
        # NOT a demo case. `is_demo` excludes a row from the attribution
        # population, and these are the only rows in the database whose
        # outcomes are real provider events.
        is_demo=False,
        window_expires_at=window_expires_at,
        created_at=now,
        **case_fields,
    )
    session.add(case)
    await session.flush()
    session.add(
        ExperimentAssignment(
            case_id=case_id,
            experiment_key=EXPERIMENT_KEY,
            arm=arm,
            assignment_hash=assignment_hash,
            assigned_at=now,
        )
    )
    return case


async def _run_control(
    factory: async_sessionmaker[AsyncSession],
    *,
    case_id: str,
    merchant_id: str,
    customer_id: str,
    first_name: str,
    assignment_hash: str,
    provider: RazorpayProvider,
    clock: Clock,
    actor: str,
    result: ArmResult,
) -> None:
    """A held-out case. Nothing is sent, and nothing is diagnosed.

    The agent is not run at all. That is stricter than the batch, which runs the
    graph and lets the stopping rules block the action, and it is the right
    choice here: with n this small, a single accidental outreach artefact on a
    control case would invalidate the only claim this experiment makes.
    """
    now = clock.now_utc()
    result.case_ids.append(case_id)

    async with factory() as session:
        case = await _persist(
            session,
            case_id=case_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            arm=ExperimentArm.CONTROL,
            assignment_hash=assignment_hash,
            now=now,
            window_expires_at=now + timedelta(hours=24),
            order_id=f"order_xp_{case_id.lower()}",
            error_source="customer",
            error_step="payment_authentication",
            error_reason="payment_cancelled",
            observed_reference_id=observed_reference_id(case_id),
        )
        await AuditChain(clock).append(
            session,
            event_name="experiment.held_out",
            actor=actor,
            payload={
                "case_id": case_id,
                "arm": "CONTROL",
                "experiment_key": EXPERIMENT_KEY,
                "assignment_hash": assignment_hash,
                "note": (
                    "deliberately not contacted. No diagnosis, no action, no "
                    "message. The merchant's own checkout stays open, and a "
                    "payment on it resolves organically."
                ),
            },
            case_id=case_id,
        )
        await session.commit()
        reference = case.observed_reference_id

    assert reference is not None
    try:
        link = await _create_link(
            provider,
            case_id=case_id,
            reference=reference,
            description=f"{first_name} - order checkout (control arm, not an outreach)",
            customer_name=first_name,
            now=now,
        )
    except Exception as exc:
        log.warning("control link failed for %s: %s", case_id, exc)
        result.errors[case_id] = str(exc)[:160]
        return

    result.links[case_id] = str(link.get("short_url") or "")
    async with factory() as session:
        # Recorded on the case, NOT in the outbox: the outbox is the record of
        # things we sent, and we sent nothing.
        stored = await session.get(RecoveryCase, case_id)
        if stored is not None:
            stored.order_id = str(link.get("order_id") or stored.order_id)
        await session.commit()


async def _run_treated(
    factory: async_sessionmaker[AsyncSession],
    *,
    case_id: str,
    merchant_id: str,
    customer_id: str,
    first_name: str,
    assignment_hash: str,
    provider: RazorpayProvider,
    settings: Settings,
    clock: Clock,
    actor: str,
    result: ArmResult,
) -> None:
    """A treated case: the real agent, the real firewall, a real link."""
    now = clock.now_utc()
    result.case_ids.append(case_id)

    async with factory() as session:
        merchant, customer = await _fixtures(session)
        state = _state(case_id, merchant=merchant, customer=customer, now=now)

    deps = AgentDeps(
        clock=clock,
        adapter=CachedAdapter(cache=ResponseCache.load(), live=None, model=settings.gemini_model),
        # Zero here, not CONTROL_FRACTION: the arm was already decided by
        # `assign_arm` above and this case is in the treated arm. Letting the
        # graph re-randomise would be a second draw on an already-assigned case,
        # and the pre-registration forbids re-randomisation for exactly this
        # reason.
        control_arm_fraction=0.0,
        experiment_key=EXPERIMENT_KEY,
    )
    final = await run_case(state, deps)

    if final.status is not CaseStatus.MONITORING or final.reference_id is None:
        reason = (
            final.stopping_rule_fired.value if final.stopping_rule_fired else final.status.value
        )
        result.stopped[case_id] = reason
        async with factory() as session:
            await _persist(
                session,
                case_id=case_id,
                merchant_id=merchant_id,
                customer_id=customer_id,
                arm=ExperimentArm.TREATMENT,
                assignment_hash=assignment_hash,
                now=now,
                window_expires_at=state.window_expires_at,
                status=final.status,
                order_id=state.order_id,
                error_source=state.error_source,
                error_step=state.error_step,
                error_reason=state.error_reason,
                stopping_rule_fired=final.stopping_rule_fired,
            )
            await session.commit()
        return

    async with factory() as session:
        await _persist(
            session,
            case_id=case_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            arm=ExperimentArm.TREATMENT,
            assignment_hash=assignment_hash,
            now=now,
            window_expires_at=state.window_expires_at,
            order_id=state.order_id,
            error_source=state.error_source,
            error_step=state.error_step,
            error_reason=state.error_reason,
            diagnosis_category=final.diagnosis.category if final.diagnosis else None,
            diagnosis_source=final.diagnosis.source if final.diagnosis else None,
            confidence=final.diagnosis.confidence if final.diagnosis else None,
        )
        # The reference is committed BEFORE the provider call. That ordering is
        # the exactly-once guarantee: a crash here is recoverable, because a
        # retry reuses the key and Razorpay rejects the duplicate.
        session.add(
            Outbox(
                id=new_id("outbox"),
                case_id=case_id,
                action_type=ActionType.CREATE_PAYMENT_LINK,
                reference_id=final.reference_id,
                payload_json="{}",
                status=OutboxStatus.SENDING,
                attempt=1,
                next_attempt_at=now,
                created_at=now,
            )
        )
        await session.commit()

    try:
        link = await _create_link(
            provider,
            case_id=case_id,
            reference=final.reference_id,
            description=f"RevPilot recovery - {case_id}",
            customer_name=first_name,
            now=now,
        )
    except Exception as exc:
        log.warning("treated link failed for %s: %s", case_id, exc)
        result.errors[case_id] = str(exc)[:160]
        async with factory() as session:
            entry = (
                (
                    await session.execute(
                        select(Outbox).where(Outbox.reference_id == final.reference_id)
                    )
                )
                .scalars()
                .first()
            )
            if entry is not None:
                entry.status = OutboxStatus.PENDING
                entry.last_error = str(exc)[:200]
            await session.commit()
        return

    result.links[case_id] = str(link.get("short_url") or "")
    async with factory() as session:
        entry = (
            (await session.execute(select(Outbox).where(Outbox.reference_id == final.reference_id)))
            .scalars()
            .first()
        )
        if entry is not None:
            entry.status = OutboxStatus.SENT
            entry.provider_ref = str(link.get("id"))
        session.add(
            RecoveryAction(
                id=new_id("action"),
                case_id=case_id,
                outbox_id=entry.id if entry is not None else None,
                attempt_no=1,
                action_type=ActionType.CREATE_PAYMENT_LINK,
                reference_id=final.reference_id,
                # Post-clamp, and zero: the firewall authorised no discount on
                # this case. Recording the proposal here instead of the applied
                # value would make the ledger a record of what was asked for.
                discount_pct_applied=0.0,
                discount_amount_paise=0,
                razorpay_link_id=str(link.get("id")),
                razorpay_link_url=str(link.get("short_url") or ""),
                status="EXECUTED",
                executed_at=now,
            )
        )
        await AuditChain(clock).append(
            session,
            event_name="action.dispatched",
            actor=actor,
            payload={
                "case_id": case_id,
                "arm": "TREATMENT",
                "experiment_key": EXPERIMENT_KEY,
                "assignment_hash": assignment_hash,
                "reference_id": final.reference_id,
                "razorpay_link_id": link.get("id"),
                "mode": "razorpay_test_mode",
            },
            case_id=case_id,
        )
        await session.commit()


async def holdout_report(session: AsyncSession) -> dict[str, Any]:
    """Arm-level outcomes for the real-provider holdout, and nothing more.

    No p-value and no significance verdict, at any n. §6 of the pre-registration
    commits to a single analysis at the full sample; and at this n a
    significance claim either way would be noise dressed as a finding.
    """
    rows = (
        await session.execute(
            select(
                ExperimentAssignment.arm,
                RecoveryCase.status,
                RecoveryCase.recovery_verified_by,
                RecoveryCase.recovered_amount_paise,
            )
            .join(RecoveryCase, RecoveryCase.id == ExperimentAssignment.case_id)
            .where(ExperimentAssignment.experiment_key == EXPERIMENT_KEY)
        )
    ).all()

    arms: dict[str, dict[str, Any]] = {
        arm.value: {"cases": 0, "razorpay_verified_recoveries": 0, "organic": 0, "paise": 0}
        for arm in ExperimentArm
    }
    for arm, status, verified_by, paise in rows:
        bucket = arms[arm.value]
        bucket["cases"] += 1
        if status is CaseStatus.RECOVERED and verified_by:
            bucket["razorpay_verified_recoveries"] += 1
            bucket["paise"] += int(paise or 0)
        elif status is CaseStatus.RESOLVED_ORGANIC:
            bucket["organic"] += 1

    treated = arms[ExperimentArm.TREATMENT.value]
    held = arms[ExperimentArm.CONTROL.value]

    return {
        "experiment_key": EXPERIMENT_KEY,
        "control_fraction": CONTROL_FRACTION,
        "arms": arms,
        "what_this_proves": [
            "the arm assignment is deterministic and recomputable from the case id",
            "a treated settlement matches a reference we issued and counts as a recovery",
            "a control settlement finds no issued reference and resolves ORGANIC",
            "no outreach artefact of any kind exists for a control case",
        ],
        "what_this_does_not_prove": (
            "anything about customer behaviour. The payments are made by the "
            "developer, so the arms are not independent observations of a "
            f"population. n = {treated['cases'] + held['cases']} against the 1,592 "
            "docs/PRE-REGISTRATION.md section 5 requires."
        ),
        "significance": None,
        "significance_basis": (
            "not reported at any n. Section 6 commits to a single analysis at the "
            "full sample, and at this n a verdict either way would be noise "
            "presented as a finding."
        ),
    }
