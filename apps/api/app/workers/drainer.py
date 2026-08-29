"""Outbox drainer and startup reconciler (workflow.md §10.4).

Two jobs, one mechanism.

**The drainer** picks up rows whose backoff has elapsed and retries them with
the *same* ``reference_id``. That is the whole point: a retry is safe because
the provider will refuse a duplicate, verified against live Razorpay Test Mode
before this was written.

**The reconciler** runs at startup and looks for rows left ``PENDING`` for
longer than they should be. Those are the crashes — the process died between
committing the intent and recording the outcome. It is the answer to failure
scenario #9, *"the API call succeeded and the local write did not"*, and it is
what makes `KILL_PROCESS_MID_EXECUTE` survivable in the demo.

The earlier design answered that scenario with an "in-memory recovery ledger",
which cannot survive the crash it exists to handle. This is the fix (§30
ADL-006).
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock
from app.db.enums import CaseStatus, OutboxStatus
from app.db.models import Outbox, RecoveryCase
from app.tools.outbox import OutboxExecutor

__all__ = ["DrainReport", "OutboxDrainer"]


@dataclass
class DrainReport:
    """What one pass did. Returned so callers assert on facts, not on hope."""

    considered: int = 0
    retried: int = 0
    recovered: int = 0
    failed: int = 0
    skipped_terminal: int = 0
    details: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"considered={self.considered} retried={self.retried} "
            f"recovered={self.recovered} failed={self.failed} "
            f"skipped_terminal={self.skipped_terminal}"
        )


@dataclass
class OutboxDrainer:
    """Retries due outbox rows, and resumes ones a crash left behind."""

    sessionmaker: async_sessionmaker[AsyncSession]
    executor: OutboxExecutor
    clock: Clock
    #: A row PENDING longer than this was almost certainly interrupted rather
    #: than merely waiting: the longest scheduled backoff is 30 s.
    stale_after_s: int = 60
    _task: asyncio.Task[None] | None = None

    # -- queries -----------------------------------------------------------
    async def due(self) -> list[Outbox]:
        """Rows whose backoff has elapsed."""
        async with self.sessionmaker() as session:
            rows = (
                await session.execute(
                    select(Outbox)
                    .where(
                        Outbox.status == OutboxStatus.PENDING,
                        Outbox.next_attempt_at <= self.clock.now_utc(),
                    )
                    .order_by(Outbox.next_attempt_at)
                )
            ).scalars()
            return list(rows)

    async def stale(self) -> list[Outbox]:
        """Rows a crash left mid-flight."""
        cutoff = self.clock.now_utc() - timedelta(seconds=self.stale_after_s)
        async with self.sessionmaker() as session:
            rows = (
                await session.execute(
                    select(Outbox).where(
                        Outbox.status.in_([OutboxStatus.PENDING, OutboxStatus.SENDING]),
                        Outbox.created_at <= cutoff,
                    )
                )
            ).scalars()
            return list(rows)

    # -- work --------------------------------------------------------------
    async def drain_once(self, *, include_stale: bool = False) -> DrainReport:
        """One pass. Idempotent, and safe to run concurrently with itself.

        Concurrency safety comes from the same place as everything else here:
        two workers picking up the same row both retry with the same
        ``reference_id``, and the provider refuses the second. Nothing depends
        on the workers coordinating.
        """
        report = DrainReport()
        rows = await self.due()
        if include_stale:
            seen = {r.id for r in rows}
            rows.extend(r for r in await self.stale() if r.id not in seen)

        for row in rows:
            report.considered += 1
            state = await self._rebuild_state(row)
            if state is None:
                report.skipped_terminal += 1
                report.details.append(f"{row.reference_id}: case is terminal, dropping")
                await self._mark_dead(row.id, "case reached a terminal state")
                continue

            before = row.status
            result = await self.executor.execute(state)
            if result.status is CaseStatus.MONITORING:
                report.recovered += 1
                report.details.append(f"{row.reference_id}: resumed -> {result.status.value}")
            elif result.status is CaseStatus.FAILED_PERMANENT:
                report.failed += 1
                report.details.append(f"{row.reference_id}: dead-lettered")
            else:
                report.retried += 1
                report.details.append(f"{row.reference_id}: still {before.value}")
        return report

    async def _rebuild_state(self, row: Outbox):  # type: ignore[no-untyped-def]
        """Reconstruct enough state to re-execute a committed intent.

        Deliberately re-reads the case rather than trusting the serialised
        payload: if the case reached a terminal state while the row was
        waiting -- the customer paid, an approval expired -- the retry must not
        happen at all. This is the same reason approval does not resume a
        frozen graph (DEC-019).
        """
        from app.agent.state import RecoveryState
        from app.db.enums import TERMINAL_STATUSES, Playbook
        from app.guardrails.token import reissue_from_committed_intent

        async with self.sessionmaker() as session:
            case = await session.get(RecoveryCase, row.case_id)
            if case is None or case.status in TERMINAL_STATUSES:
                return None

            # The outbox row is durable evidence that the firewall already
            # authorised this exact action; the crash destroyed the in-memory
            # token, not the authorisation. reissue_from_committed_intent takes
            # a *committed payload* rather than an arbitrary action, so it
            # cannot authorise anything new -- which is why the reconciler is
            # not a second way past the firewall.
            applied, token = reissue_from_committed_intent(
                row.payload_json, minted_at=self.clock.now_utc()
            )

            return RecoveryState(
                case_id=case.id,
                merchant_id=case.merchant_id,
                customer_id=case.customer_id,
                playbook=Playbook(case.playbook),
                status=case.status,
                amount_paise=case.amount_paise,
                attempt_no=case.attempt_no,
                policy_applied=applied,
                # Re-materialised rather than persisted: a capability is for one
                # immediate execution and must not be storable (§7).
                policy_token=token,
            )

    async def _mark_dead(self, outbox_id: str, reason: str) -> None:
        async with self.sessionmaker() as session:
            row = await session.get(Outbox, outbox_id)
            if row is None:  # pragma: no cover
                return
            row.status = OutboxStatus.DEAD
            row.last_error = reason[:500]
            await session.commit()

    # -- lifecycle ---------------------------------------------------------
    async def reconcile_on_startup(self) -> DrainReport:
        """Resume anything a crash interrupted.

        Called once at boot. This is what makes the chaos demo work: kill the
        process between the provider call and the local commit, restart, and
        the case resolves with one link rather than two.
        """
        return await self.drain_once(include_stale=True)

    async def run_forever(self, *, interval_s: float = 5.0) -> None:
        """Background loop. Never dies on a single bad row."""
        while True:
            with contextlib.suppress(Exception):
                await self.drain_once()
            await asyncio.sleep(interval_s)

    def start(self, *, interval_s: float = 5.0) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_forever(interval_s=interval_s))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
