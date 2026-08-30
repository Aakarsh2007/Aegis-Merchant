"""Approval expiry and stale-deferral sweeps (§8.1 S-09, §8.3 A2).

Two time-driven jobs, both of which exist because *nothing happening* is a
state the system has to handle explicitly.

**The TTL sweeper.** An approval nobody actions is the common case, not the
edge case: a merchant goes home at 7pm and a ₹18,500 approval sits until
morning. Without a sweeper the case is frozen indefinitely while its recovery
window closes around it. Expiry has to run on a timer rather than on the next
request, because the whole problem is that no request is coming.

**The stale-deferral sweeper**, which is the one that found a real gap.

S-09 defers a quiet-hours message by setting ``outbox.next_attempt_at`` to
09:05 IST, and the drainer collects anything whose time has come. That much
already worked. What did not: the drainer's query is
``status = PENDING AND next_attempt_at <= now`` and **nothing checked whether
the case was still alive**. A message deferred at 22:00 for a case whose
24-hour recovery window closed at 03:00 would be sent at 09:05 — a fresh
payment link for a case that was over, six hours after the fact.

That is worse than a dropped message. It spends a contact from a budget of two,
it messages someone about a payment we are no longer trying to recover, and the
resulting link is attributable to nothing. The sweep marks those DEAD before
the drainer can reach them.

Both sweeps are idempotent and safe to run alongside request handling. They
select by state and deadline and re-assert the expected state per row, so two
overlapping sweeps cannot double-expire a row or overwrite a decision a human
just made.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.db.enums import (
    TERMINAL_STATUSES,
    ApprovalStatus,
    CaseStatus,
    OutboxStatus,
)
from app.db.models import ApprovalRequest, Outbox, RecoveryCase
from app.tools.audit import AuditChain

__all__ = ["Scheduler", "SweepResult", "approval_expires_at"]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SweepResult:
    """What one sweep did. Returned rather than only logged, so it is testable."""

    expired_approvals: int = 0
    #: Deferred sends killed because their case's window closed while they
    #: waited. Counted separately from approvals because this number is a
    #: *loss*: money we held a message for and then could not pursue.
    stale_deferrals: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.expired_approvals or self.stale_deferrals)


class Scheduler:
    """Time-driven maintenance. One instance per process."""

    def __init__(self, clock: Clock, audit: AuditChain) -> None:
        self._clock = clock
        self._audit = audit

    async def sweep(self, session: AsyncSession) -> SweepResult:
        """Run every job once, then commit."""
        expired = await self._expire_approvals(session)
        stale = await self._kill_stale_deferrals(session)
        await session.commit()
        return SweepResult(expired_approvals=expired, stale_deferrals=stale)

    # -------------------------------------------------------------- approvals
    async def _expire_approvals(self, session: AsyncSession) -> int:
        """Expire approvals past their TTL.

        The status guard is re-asserted per row after the select, so an
        approval a human actioned in between is not clobbered. The human's
        decision wins: they were there, the timer was not.
        """
        now = self._clock.now_utc()
        rows = (
            (
                await session.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.status == ApprovalStatus.PENDING,
                        ApprovalRequest.expires_at <= now,
                    )
                )
            )
            .scalars()
            .all()
        )

        count = 0
        for approval in rows:
            if approval.status is not ApprovalStatus.PENDING:
                continue
            approval.status = ApprovalStatus.EXPIRED
            approval.reviewed_at = now
            # reviewed_by stays NULL. Nobody reviewed this, and writing
            # "system" into a column that means "the human who decided" would
            # make the audit trail claim a review that never happened.
            count += 1

            case = await session.get(RecoveryCase, approval.case_id)
            if case is not None and case.status is CaseStatus.AWAITING_APPROVAL:
                case.status = CaseStatus.EXPIRED
                case.resolved_at = now

            await self._audit.append(
                session,
                event_name="approval.expired",
                actor="scheduler",
                payload={
                    "approval_id": approval.id,
                    "case_id": approval.case_id,
                    "amount_paise": approval.amount_paise,
                    "expired_at": approval.expires_at.isoformat(),
                    "note": "no human actioned this within the TTL",
                },
                case_id=approval.case_id,
            )
        if count:
            log.info("expired %d approval(s) past TTL", count)
        return count

    # ------------------------------------------------------ stale deferrals
    async def _kill_stale_deferrals(self, session: AsyncSession) -> int:
        """Kill queued sends whose case is no longer worth sending for.

        Two conditions, either of which is disqualifying:

        * the case reached a terminal state (paid organically, suppressed by a
          stopping rule, expired) while the message waited;
        * the recovery window closed before the release time.

        Both are reachable specifically because of quiet hours: a message
        deferred at 22:00 waits eleven hours, and a 24-hour window can close
        inside that gap.
        """
        now = self._clock.now_utc()
        rows = (
            (
                await session.execute(
                    select(Outbox).where(
                        Outbox.status == OutboxStatus.PENDING,
                        Outbox.next_attempt_at > now,
                    )
                )
            )
            .scalars()
            .all()
        )

        count = 0
        for entry in rows:
            if entry.status is not OutboxStatus.PENDING:
                continue
            case = await session.get(RecoveryCase, entry.case_id)
            if case is None:
                continue

            if case.status in TERMINAL_STATUSES:
                reason = f"case reached {case.status.value} while the send was deferred"
            elif entry.next_attempt_at > case.window_expires_at:
                reason = (
                    f"deferred to {entry.next_attempt_at.isoformat()}, after the recovery "
                    f"window closes at {case.window_expires_at.isoformat()}"
                )
            else:
                continue

            entry.status = OutboxStatus.DEAD
            entry.last_error = f"cancelled before send: {reason}"
            count += 1

            await self._audit.append(
                session,
                event_name="outbox.deferral_cancelled",
                actor="scheduler",
                payload={
                    "outbox_id": entry.id,
                    "case_id": entry.case_id,
                    "reference_id": entry.reference_id,
                    "reason": reason,
                    "note": (
                        "a held message must not be sent for a case that is over; "
                        "it would spend a contact from a budget of two and be "
                        "attributable to nothing"
                    ),
                },
                case_id=entry.case_id,
            )
        if count:
            log.info("cancelled %d stale deferred send(s)", count)
        return count


def approval_expires_at(now: datetime, *, ttl_minutes: int) -> datetime:
    """When an approval minted at ``now`` expires.

    A free function so the router and the sweeper cannot disagree about the
    TTL. An approval whose creator and expirer used different arithmetic would
    expire early or never, and that is the INC-007 shape.
    """
    return now + timedelta(minutes=ttl_minutes)
