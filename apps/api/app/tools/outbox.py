"""Two-phase execution against a payment provider (workflow.md §10.3).

The problem this solves is the one that quietly loses money: **the provider call
succeeded and our commit did not.** A naive executor calls the API, then writes
the result. Crash in between and the customer has a live payment link that we
have no record of — so the retry creates a second one, and now there are two
live links for one cart.

The shape of the fix:

    TXN 1 (atomic)   INSERT outbox(PENDING, reference_id)  <- key committed FIRST
                     UPDATE case -> EXECUTING
                            |
    side effect      POST /payment_links with that reference_id
                            |
    TXN 2 (atomic)   UPDATE outbox -> SENT, provider_ref
                     INSERT recovery_action
                     INSERT contact_ledger        <- same txn as the dispatch

The load-bearing detail is the *ordering*: ``reference_id`` is generated and
committed **before** the call. Crash anywhere after TXN 1 and the reconciler
retries with the identical key, and **Razorpay's own uniqueness constraint
rejects the duplicate.** We do not need a distributed transaction; we need the
provider to refuse our second attempt, which it does.

That last claim is not taken on faith. It was verified against live Razorpay
Test Mode before this module was written: sending the same ``reference_id``
twice produced ``payment link with given reference_id ... already exists``, and
the existing link was then retrievable. The mock models the same behaviour
(DEC-009), so the crash-recovery tests are validating something true.

``DuplicateReference`` is therefore **not an error path**. It is the happy path
of a retry: it means a previous attempt got further than we recorded, and the
provider just prevented the double-charge. We fetch the existing link and carry
on.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.state import NodeTrace, RecoveryState
from app.core.clock import Clock
from app.db.enums import (
    ActionType,
    CaseStatus,
    Channel,
    DLQStatus,
    MessageClass,
    OutboxStatus,
)
from app.db.ids import new_id
from app.db.models import ContactLedger, DeadLetter, Outbox, RecoveryAction
from app.tools.provider import (
    DuplicateReference,
    PaymentLinkRequest,
    PaymentLinkResult,
    PaymentProvider,
    ProviderError,
    ProviderPermanent,
    ProviderRetryable,
)

__all__ = ["BACKOFF_SCHEDULE", "OutboxExecutor", "next_attempt_delay"]

#: Seconds before each retry. Four attempts total, then the dead-letter queue.
BACKOFF_SCHEDULE = (0.5, 2.0, 8.0, 30.0)

#: ±25%. Without jitter, a provider blip that fails many cases at once makes
#: them all retry in lockstep and hit the recovering provider as one spike.
_JITTER = 0.25


def next_attempt_delay(attempt: int, rng: random.Random | None = None) -> float:
    """Backoff with jitter for the given attempt number (0-based)."""
    rng = rng or random.Random()
    base = BACKOFF_SCHEDULE[min(attempt, len(BACKOFF_SCHEDULE) - 1)]
    return base * (1 + rng.uniform(-_JITTER, _JITTER))


@dataclass
class OutboxExecutor:
    """Executes an authorised action, exactly once, or not at all."""

    sessionmaker: async_sessionmaker[AsyncSession]
    provider: PaymentProvider
    clock: Clock
    max_attempts: int = 4
    rng: random.Random | None = None

    # -- the Executor protocol the graph calls -----------------------------
    async def execute(self, state: RecoveryState) -> RecoveryState:
        """Run both phases for one authorised action.

        Requires a verified capability. The token is checked here as well as in
        the graph node: this module is the last thing before a provider call,
        and a check at the boundary is worth more than one further upstream.
        """
        applied = state.policy_applied
        token = state.policy_token
        if applied is None or token is None:
            return self._trace(state, "refused: no authorised action", CaseStatus.SUPPRESSED)
        token.verify()

        outbox_id = await self._phase_one(state)
        try:
            result, was_existing = await self._call_provider(state)
        except ProviderRetryable as exc:
            await self._schedule_retry(outbox_id, exc)
            return self._trace(
                state,
                f"transient failure, retry scheduled: {exc}",
                CaseStatus.EXECUTING,
                detail={"outbox_id": outbox_id, "retryable": True},
            )
        except ProviderPermanent as exc:
            await self._dead_letter(outbox_id, exc)
            return self._trace(
                state,
                f"permanent failure, dead-lettered: {exc}",
                CaseStatus.FAILED_PERMANENT,
                detail={"outbox_id": outbox_id, "retryable": False},
            )

        await self._phase_two(state, outbox_id, result)
        return self._trace(
            state,
            (
                f"{'recovered existing' if was_existing else 'created'} link "
                f"{result.link_id} for Rs {result.amount_paise / 100:,.0f} "
                f"(ref {result.reference_id})"
            ),
            CaseStatus.MONITORING,
            detail={
                "link_url": result.short_url,
                "was_existing": was_existing,
                "outbox_id": outbox_id,
            },
            payment_link_url=result.short_url,
            attempt_no=state.attempt_no + 1,
            discount_bearing_attempts=state.discount_bearing_attempts
            + (1 if applied.discount_pct > 0 else 0),
        )

    # -- phase one ---------------------------------------------------------
    async def _phase_one(self, state: RecoveryState) -> str:
        """Commit the intent, **including the idempotency key**, before calling.

        If an outbox row already exists for this reference the previous attempt
        got at least this far; we reuse it rather than creating a second intent.
        That is what makes the whole thing resumable.
        """
        applied = state.policy_applied
        assert applied is not None
        async with self.sessionmaker() as session:
            existing = await session.scalar(
                select(Outbox).where(Outbox.reference_id == applied.reference_id)
            )
            if existing is not None:
                return existing.id

            row = Outbox(
                id=new_id("outbox"),
                case_id=state.case_id,
                action_type=ActionType.CREATE_PAYMENT_LINK,
                reference_id=applied.reference_id,
                payload_json=json.dumps(applied.as_payload(), sort_keys=True, default=str),
                status=OutboxStatus.PENDING,
                attempt=0,
                next_attempt_at=self.clock.now_utc(),
                created_at=self.clock.now_utc(),
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                # A concurrent worker won the race. Its row is authoritative --
                # the UNIQUE on reference_id is doing exactly its job.
                await session.rollback()
                won = await session.scalar(
                    select(Outbox).where(Outbox.reference_id == applied.reference_id)
                )
                if won is None:  # pragma: no cover - would mean the UNIQUE lied
                    raise
                return won.id
            return row.id

    # -- side effect -------------------------------------------------------
    async def _call_provider(self, state: RecoveryState) -> tuple[PaymentLinkResult, bool]:
        """Create the link, or recover the one a previous attempt created."""
        applied = state.policy_applied
        assert applied is not None
        request = PaymentLinkRequest(
            amount_paise=applied.charge_amount_paise,
            reference_id=applied.reference_id,
            description=f"Complete your order - {state.merchant_id}",
            customer_name=state.customer_first_name or "Customer",
            expire_by=self.clock.now_utc() + timedelta(minutes=applied.link_expiry_minutes),
            notes={
                "case_id": state.case_id,
                "playbook": state.playbook.value,
                "arm": state.experiment_arm.value if state.experiment_arm else "TREATMENT",
            },
        )
        try:
            return await self.provider.create_payment_link(request), False
        except DuplicateReference:
            # Not a failure. A previous attempt got further than we recorded,
            # and the provider just prevented a second live link for one cart.
            existing = await self.provider.get_payment_link_by_reference(applied.reference_id)
            if existing is None:
                # The provider says it exists but will not return it. Treating
                # this as retryable is the safe reading: creating another link
                # is the one thing we must not do.
                raise ProviderRetryable(
                    f"reference {applied.reference_id} reported as duplicate but not retrievable"
                ) from None
            return existing, True

    # -- phase two ---------------------------------------------------------
    async def _phase_two(
        self, state: RecoveryState, outbox_id: str, result: PaymentLinkResult
    ) -> None:
        """Record the outcome. Idempotent: safe to run twice.

        The contact-ledger row is written in the **same transaction** as the
        action. A cap check that reads a ledger the dispatch has not yet been
        written to would let a second message through.
        """
        applied = state.policy_applied
        assert applied is not None
        now = self.clock.now_utc()

        async with self.sessionmaker() as session:
            row = await session.get(Outbox, outbox_id)
            if row is None:  # pragma: no cover - phase one just wrote it
                return
            if row.status is OutboxStatus.SENT:
                return  # already recorded; a replay must not double-write

            row.status = OutboxStatus.SENT
            row.provider_ref = result.link_id
            row.last_error = None

            session.add(
                RecoveryAction(
                    id=new_id("action"),
                    case_id=state.case_id,
                    outbox_id=outbox_id,
                    attempt_no=state.attempt_no + 1,
                    action_type=ActionType.CREATE_PAYMENT_LINK,
                    strategy=applied.strategy,
                    escalation_rung=applied.escalation_rung,
                    message_class=applied.message_class,
                    discount_pct_applied=applied.discount_pct,
                    discount_amount_paise=applied.discount_amount_paise,
                    razorpay_link_id=result.link_id,
                    razorpay_link_url=result.short_url,
                    reference_id=applied.reference_id,
                    channel=applied.channel,
                    status="EXECUTED",
                    executed_at=now,
                )
            )
            if applied.channel is not Channel.NONE:
                session.add(
                    ContactLedger(
                        id=new_id("contact"),
                        customer_id=state.customer_id,
                        case_id=state.case_id,
                        channel=applied.channel,
                        message_class=applied.message_class or MessageClass.TRANSACTIONAL,
                        sent_at=now,
                    )
                )
            await session.commit()

    # -- failure handling --------------------------------------------------
    async def _schedule_retry(self, outbox_id: str, exc: ProviderError) -> None:
        """Back off, or dead-letter once the budget is spent."""
        async with self.sessionmaker() as session:
            row = await session.get(Outbox, outbox_id)
            if row is None:  # pragma: no cover
                return
            row.attempt += 1
            row.last_error = f"{type(exc).__name__}: {exc}"[:500]
            if row.attempt >= self.max_attempts:
                row.status = OutboxStatus.DEAD
                session.add(self._dlq_row(row, "retry budget exhausted"))
            else:
                row.status = OutboxStatus.PENDING
                row.next_attempt_at = self.clock.now_utc() + timedelta(
                    seconds=next_attempt_delay(row.attempt - 1, self.rng)
                )
            await session.commit()

    async def _dead_letter(self, outbox_id: str, exc: ProviderError) -> None:
        """Terminal provider refusal. Retrying produces the identical failure."""
        async with self.sessionmaker() as session:
            row = await session.get(Outbox, outbox_id)
            if row is None:  # pragma: no cover
                return
            row.status = OutboxStatus.DEAD
            row.attempt += 1
            row.last_error = f"{type(exc).__name__}: {exc}"[:500]
            session.add(self._dlq_row(row, f"provider refused: {exc}"))
            await session.commit()

    def _dlq_row(self, row: Outbox, reason: str) -> DeadLetter:
        """Never silently discarded: visible in the dashboard, replayable."""
        return DeadLetter(
            id=new_id("dlq"),
            outbox_id=row.id,
            reason=reason[:120],
            error_chain_json=json.dumps(
                {"attempts": row.attempt, "last_error": row.last_error}, default=str
            ),
            attempts=row.attempt,
            status=DLQStatus.OPEN,
            created_at=self.clock.now_utc(),
        )

    # -- helper ------------------------------------------------------------
    def _trace(
        self,
        state: RecoveryState,
        summary: str,
        status: CaseStatus,
        *,
        detail: dict[str, Any] | None = None,
        **changes: Any,
    ) -> RecoveryState:
        return state.with_trace(
            NodeTrace(
                node="EXECUTE",
                summary=summary,
                provenance="razorpay",
                at=self.clock.now_utc(),
                detail=detail or {},
            ),
            status=status,
            **changes,
        )
