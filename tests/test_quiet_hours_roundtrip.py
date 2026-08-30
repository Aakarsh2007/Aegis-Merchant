"""Quiet hours end to end: is the message *held*, or quietly lost?

``tests/test_stopping_rules.py`` already proves the predicate — the wrapping
21:00/09:00 window, IST versus UTC, month rollover, merchant-editable bounds.
None of that is repeated here.

What it does not prove is the property S-09 actually claims, which spans four
components: a message deferred at 22:00 is **still sent** at 09:05. A system
that dropped held messages would pass every predicate test in that file, look
identical in the logs, and lose money silently. So this file follows one
message through stopping rule → outbox → scheduler → drainer eligibility, at
every hour of the day, and asserts nothing disappears.

The distinction that matters throughout: a *deferral* moves a send later, a
*cancellation* stops it. Both leave no message sent right now, and only one of
them is correct at 22:00.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.clock import FakeClock, to_ist
from app.db.enums import ActionType, CaseStatus, OutboxStatus, Playbook
from app.db.models import Consent, Customer, Merchant, Outbox, RecoveryCase
from app.guardrails.stopping_rules import (
    Decision,
    in_quiet_hours,
    next_quiet_hours_release,
)
from app.services.scheduler import Scheduler
from app.tools.audit import AuditChain
from tests.test_stopping_rules import ctx

MERCHANT = "mch_q"
CUSTOMER = "cus_q"
CASE = "RC-Q001"


async def _seed_case(factory, clock: FakeClock, *, window_hours: float) -> None:  # type: ignore[no-untyped-def]
    now = clock.now_utc()
    async with factory() as s:
        s.add(Merchant(id=MERCHANT, business_name="GlowKart", created_at=now))
        s.add(
            Customer(
                id=CUSTOMER,
                merchant_id=MERCHANT,
                first_name="Ananya",
                phone_masked="+91 ***** 43210",
                phone_hash="h" * 64,
                ltv_paise=0,
                success_orders_count=0,
                first_seen_at=now,
            )
        )
        s.add(
            Consent(
                customer_id=CUSTOMER,
                transactional=True,
                marketing=False,
                dnd_registered=False,
                opted_out=False,
                updated_at=now,
            )
        )
        await s.flush()
        s.add(
            RecoveryCase(
                id=CASE,
                merchant_id=MERCHANT,
                customer_id=CUSTOMER,
                playbook=Playbook.PAYMENT_FAILURE,
                status=CaseStatus.EXECUTING,
                amount_paise=429900,
                idempotency_hash="q" * 64,
                window_expires_at=now + timedelta(hours=window_hours),
                created_at=now,
            )
        )
        await s.commit()


async def _defer_send(factory, clock: FakeClock, release: datetime) -> None:  # type: ignore[no-untyped-def]
    async with factory() as s:
        s.add(
            Outbox(
                id="obx_q",
                case_id=CASE,
                action_type=ActionType.CREATE_PAYMENT_LINK,
                reference_id="rvp_rc-q001_1",
                payload_json="{}",
                status=OutboxStatus.PENDING,
                attempt=0,
                next_attempt_at=release,
                created_at=clock.now_utc(),
            )
        )
        await s.commit()


# ===========================================================================
class TestEveryHourOfTheDay:
    """Sweep all 24 hours. A rule that mishandled one hour would otherwise
    hide behind whichever hour a single-example test happened to pick."""

    @pytest.mark.parametrize("hour", list(range(24)))
    def test_a_send_is_deferred_or_allowed_but_never_dropped(self, hour: int) -> None:
        now = FakeClock.at_ist(2026, 9, 1, hour, 30).now_utc()
        verdict = ctx_verdict(now)

        if in_quiet_hours(now, start_ist=21, end_ist=9):
            assert verdict.decision is Decision.DEFER, f"{hour}:30 IST should defer"
            assert verdict.defer_until is not None, "a deferral must carry a release time"
            # The distinction the whole rule rests on: deferred, not stopped.
            assert verdict.terminal_status is None, (
                f"{hour}:30 IST produced a terminal status — that is a drop, not a hold"
            )
        else:
            assert verdict.decision is not Decision.DEFER, f"{hour}:30 IST should proceed"

    @pytest.mark.parametrize("hour", [21, 22, 23, 0, 3, 8])
    def test_the_release_is_always_in_the_future_and_at_0905(self, hour: int) -> None:
        """A release in the past is the INC-005 livelock: the case re-evaluates,
        defers again to the same instant, and never advances."""
        now = FakeClock.at_ist(2026, 9, 1, hour, 30).now_utc()
        release = next_quiet_hours_release(now, start_ist=21, end_ist=9, release_minute=5)
        assert release > now
        local = to_ist(release)
        assert (local.hour, local.minute) == (9, 5)


def ctx_verdict(now: datetime):  # type: ignore[no-untyped-def]
    from app.guardrails.stopping_rules import evaluate

    return evaluate(ctx(now_utc=now, is_outbound_contact=True))


# ===========================================================================
class TestTheHeldMessageIsActuallySent:
    """The property S-09 claims, proven across the components that implement
    it rather than in the rule alone."""

    @pytest.mark.asyncio
    async def test_a_message_held_at_2200_is_due_at_0905(self, engine: AsyncEngine) -> None:
        """The whole point. Defer at 22:00, advance past 09:05, and assert the
        drainer's own predicate now selects it. If the message had been
        dropped this fails; if it had been sent early, the mid-hold assertion
        fails."""
        clock = FakeClock.at_ist(2026, 9, 1, 22, 0)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed_case(factory, clock, window_hours=24)

        release = next_quiet_hours_release(
            clock.now_utc(), start_ist=21, end_ist=9, release_minute=5
        )
        await _defer_send(factory, clock, release)

        # Mid-hold: present, PENDING, and NOT yet due.
        async with factory() as s:
            entry = await s.get(Outbox, "obx_q")
            assert entry is not None
            assert entry.status is OutboxStatus.PENDING
            assert entry.next_attempt_at > clock.now_utc(), "must not be sent during quiet hours"

        # The sweep runs overnight and must not touch a healthy deferral.
        async with factory() as s:
            result = await Scheduler(clock, AuditChain(clock)).sweep(s)
            assert result.stale_deferrals == 0

        clock.advance(hours=11, minutes=10)  # 09:10 IST next morning

        async with factory() as s:
            due = (
                (
                    await s.execute(
                        select(Outbox).where(
                            Outbox.status == OutboxStatus.PENDING,
                            Outbox.next_attempt_at <= clock.now_utc(),
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert [e.id for e in due] == ["obx_q"], "the held message was not released"

    @pytest.mark.asyncio
    async def test_a_hold_that_outlives_the_window_is_cancelled_not_sent(
        self, engine: AsyncEngine
    ) -> None:
        """The counterpart, and the gap this phase found.

        A 5-hour window opened at 22:00 closes at 03:00, inside the hold. The
        drainer's query is `PENDING AND next_attempt_at <= now` and would have
        sent it at 09:05 — a fresh payment link six hours after the case was
        over, spending one of two permitted contacts.
        """
        clock = FakeClock.at_ist(2026, 9, 1, 22, 0)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed_case(factory, clock, window_hours=5)

        release = next_quiet_hours_release(
            clock.now_utc(), start_ist=21, end_ist=9, release_minute=5
        )
        await _defer_send(factory, clock, release)

        async with factory() as s:
            result = await Scheduler(clock, AuditChain(clock)).sweep(s)
        assert result.stale_deferrals == 1

        clock.advance(hours=11, minutes=10)
        async with factory() as s:
            due = (
                (
                    await s.execute(
                        select(Outbox).where(
                            Outbox.status == OutboxStatus.PENDING,
                            Outbox.next_attempt_at <= clock.now_utc(),
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert due == [], "a message for a closed case must not become due"

    @pytest.mark.asyncio
    async def test_cancellation_and_release_are_distinguishable(self, engine: AsyncEngine) -> None:
        """Both leave nothing sent tonight. Only one is correct, so the
        outcomes must not look the same in the data."""
        clock = FakeClock.at_ist(2026, 9, 1, 22, 0)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed_case(factory, clock, window_hours=5)
        release = next_quiet_hours_release(
            clock.now_utc(), start_ist=21, end_ist=9, release_minute=5
        )
        await _defer_send(factory, clock, release)

        async with factory() as s:
            await Scheduler(clock, AuditChain(clock)).sweep(s)
        async with factory() as s:
            entry = await s.get(Outbox, "obx_q")
        assert entry is not None
        assert entry.status is OutboxStatus.DEAD
        assert entry.last_error and "window" in entry.last_error


class TestQuietHoursIsNotABusinessHoursFilter:
    def test_a_transactional_message_at_2200_is_still_deferred(self) -> None:
        """Quiet hours apply to outbound contact regardless of class. A
        utility message is still a notification arriving at 10 PM."""
        now = FakeClock.at_ist(2026, 9, 1, 22, 0).now_utc()
        assert ctx_verdict(now).decision is Decision.DEFER

    def test_non_contact_work_is_not_deferred(self) -> None:
        """Diagnosis, policy evaluation and verification happen at any hour.
        Deferring those would stall the pipeline overnight for no benefit —
        nothing reaches a customer."""
        from app.guardrails.stopping_rules import evaluate

        now = FakeClock.at_ist(2026, 9, 1, 22, 0).now_utc()
        verdict = evaluate(ctx(now_utc=now, is_outbound_contact=False))
        assert verdict.decision is not Decision.DEFER
