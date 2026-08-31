"""What happens when a settling webhook arrives (§14.1).

This is the last mile of the whole system, and it was missing. The webhook
handler verified the signature, stored the event and acknowledged in 7 ms —
and then did nothing with it. `_process_event` was a Phase-2 stub that
normalised the payload and dropped it, so a genuine Razorpay
`payment_link.paid` could arrive, verify, be stored, and never reach
attribution. The `RAZORPAY_VERIFIED` figure could not move no matter what
Razorpay sent.

Which is worth stating plainly: **every earlier claim about attribution was
about code that nothing called on the live path.** The rules were tested and
correct; the wire was not connected.

What this does
--------------

Applies :func:`app.services.attribution.attribute` — the same six conditions,
unchanged — and on a pass marks the case RECOVERED with the **real** Razorpay
event id. Not a ``sim_evt_`` prefix, so the amount lands in the
RAZORPAY_VERIFIED column rather than the SIMULATED one. That column moving is
the only thing in this project that constitutes proof of a real recovery.

Why it runs in its own session
------------------------------

FastAPI's ``BackgroundTasks`` runs after the response is sent, by which time
the request-scoped session is closed. Reusing it would raise on first use, so
this opens its own. It also means a failure here cannot roll back the webhook
row that was already committed — which is correct: the event genuinely arrived,
and losing that record because attribution failed would be worse than a missed
attribution.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock
from app.db.enums import CaseStatus
from app.db.models import Outbox, RecoveryAction, RecoveryCase, WebhookEvent
from app.ingest.normalise import normalise
from app.services.attribution import SETTLING_EVENTS, attribute
from app.tools.audit import AuditChain

log = logging.getLogger(__name__)

__all__ = ["SettlementOutcome", "process_settlement"]

#: How long after the window closes a payment still counts. Razorpay can
#: deliver late, and a customer who paid inside the window but whose webhook
#: was retried for an hour should not be discarded.
ATTRIBUTION_GRACE = timedelta(hours=24)


class SettlementOutcome:
    """What the settlement did, so the caller can log something specific."""

    def __init__(self, *, counted: bool, reason: str, case_id: str | None = None) -> None:
        self.counted = counted
        self.reason = reason
        self.case_id = case_id

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"SettlementOutcome(counted={self.counted}, reason={self.reason!r})"


async def _find_case(
    session: AsyncSession, reference_id: str
) -> tuple[RecoveryCase | None, str | None]:
    """The case whose reference we issued, and the reference as we stored it.

    The reference lives on the **outbox row**, because that is where it is
    committed before the provider call — which is the whole basis of the
    exactly-once guarantee. `RecoveryAction` carries a copy for the trace, and
    is checked as a fallback for actions written outside the outbox path.

    Matched case-insensitively: we emit lowercase (INC-012) but Razorpay's own
    error messages echo different casing, and the reference is a high-entropy
    string we generated, so leniency costs nothing here while strictness costs
    an uncounted recovery.
    """
    needle = reference_id.strip().lower()

    entry = (
        (await session.execute(select(Outbox).where(func.lower(Outbox.reference_id) == needle)))
        .scalars()
        .first()
    )
    if entry is not None:
        return await session.get(RecoveryCase, entry.case_id), entry.reference_id

    action = (
        (
            await session.execute(
                select(RecoveryAction).where(func.lower(RecoveryAction.reference_id) == needle)
            )
        )
        .scalars()
        .first()
    )
    if action is not None:
        return await session.get(RecoveryCase, action.case_id), action.reference_id

    # The merchant's own checkout, for a control-arm case. Returns the case with
    # `issued_reference = None` -- and that None is the whole point. It routes
    # straight into attribute()'s "no reference_id to match on" branch, which
    # resolves organically, so a control customer who pays is recorded as the
    # counterfactual rather than credited to an action we never took.
    observed = (
        (
            await session.execute(
                select(RecoveryCase).where(func.lower(RecoveryCase.observed_reference_id) == needle)
            )
        )
        .scalars()
        .first()
    )
    if observed is not None:
        return observed, None

    return None, None


async def process_settlement(
    factory: async_sessionmaker[AsyncSession],
    *,
    payload: dict[str, Any],
    event_id: str,
    clock: Clock,
) -> SettlementOutcome:
    """Attribute one verified webhook, or explain why it does not count.

    Never raises. A background task that throws produces a log line nobody
    reads and a silently unattributed payment; every path here returns an
    outcome instead.
    """
    try:
        event = normalise(payload, event_id=event_id)
    except Exception:
        log.exception("could not normalise webhook %s", event_id)
        return SettlementOutcome(counted=False, reason="unparseable payload")

    if event.event_type not in SETTLING_EVENTS:
        # payment.captured lands here deliberately: it fires for organic
        # checkout completions too, and counting it would credit us with
        # payments we had nothing to do with.
        return SettlementOutcome(
            counted=False, reason=f"{event.event_type} does not settle a payment"
        )

    if not event.reference_id:
        return SettlementOutcome(
            counted=False, reason="no reference_id: not attributable to any action of ours"
        )

    try:
        async with factory() as session:
            case, issued_reference = await _find_case(session, event.reference_id)
            if case is None:
                return SettlementOutcome(
                    counted=False,
                    reason=f"reference {event.reference_id!r} was not issued by us",
                )

            verdict = attribute(
                event_type=event.event_type,
                # The handler already verified the HMAC; reaching here means it
                # passed. Stated rather than assumed, because attribute()
                # treats the signature as a required condition and a caller
                # that fudged this would defeat condition 1.
                signature_valid=True,
                event_id=event_id,
                reference_id=event.reference_id,
                webhook_amount_paise=event.amount_paise,
                issued_reference_id=issued_reference,
                case_status=case.status,
                case_amount_paise=case.amount_paise,
                already_counted=case.recovery_verified_by is not None,
                now=clock.now_utc(),
                window_expires_at=case.window_expires_at,
                grace=ATTRIBUTION_GRACE,
            )

            if not verdict.counted:
                if verdict.resolves_organically and case.status not in {
                    CaseStatus.RECOVERED,
                    CaseStatus.RESOLVED_ORGANIC,
                }:
                    # Real money, and not ours. Recording it as organic is what
                    # keeps the control arm honest.
                    case.status = CaseStatus.RESOLVED_ORGANIC
                    case.resolved_at = clock.now_utc()
                    await session.commit()
                return SettlementOutcome(counted=False, reason=verdict.reason, case_id=case.id)

            case.status = CaseStatus.RECOVERED
            case.recovered_amount_paise = verdict.amount_paise
            # The REAL Razorpay event id. No sim_evt_ prefix, so this amount
            # lands in the RAZORPAY_VERIFIED column -- the only figure in this
            # project that constitutes proof of an actual recovery.
            case.recovery_verified_by = event_id
            case.resolved_at = clock.now_utc()

            await AuditChain(clock).append(
                session,
                event_name="recovery.verified",
                actor="webhook",
                payload={
                    "case_id": case.id,
                    "event_id": event_id,
                    "event_type": event.event_type,
                    "reference_id": event.reference_id,
                    "amount_paise": verdict.amount_paise,
                    "provenance": "RAZORPAY_VERIFIED",
                    "note": (
                        "a signed Razorpay webhook carrying a reference we issued, "
                        "inside the attribution window, for a case we acted on"
                    ),
                },
                case_id=case.id,
            )
            await session.commit()

            log.info(
                "recovery VERIFIED: case=%s amount=%s event=%s",
                case.id,
                verdict.amount_paise,
                event_id,
            )
            return SettlementOutcome(counted=True, reason=verdict.reason, case_id=case.id)
    except Exception:
        log.exception("attribution failed for webhook %s", event_id)
        return SettlementOutcome(counted=False, reason="attribution raised")


async def mark_processed(
    factory: async_sessionmaker[AsyncSession], *, event_row_id: str, outcome: str
) -> None:
    """Record what became of the event, on the event row itself.

    So an operator can answer "did this webhook do anything?" from the ledger
    rather than from a log file that may have rotated.
    """
    try:
        async with factory() as session:
            row = await session.get(WebhookEvent, event_row_id)
            if row is not None:
                row.status = outcome
                await session.commit()
    except Exception:  # pragma: no cover - defensive
        log.exception("could not mark webhook row %s", event_row_id)
