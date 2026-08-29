"""Outbox tests — exactly-once execution against a payment provider.

The scenario that matters is failure #9: **the provider call succeeded and the
local commit did not.** Everything here is about making that survivable rather
than expensive.
"""

from __future__ import annotations

import random
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.agent.state import RecoveryState
from app.core.clock import FakeClock
from app.db.enums import (
    ActionType,
    CaseStatus,
    Channel,
    DLQStatus,
    EscalationRung,
    ExperimentArm,
    MessageClass,
    OutboxStatus,
    Playbook,
    RecoveryStrategy,
)
from app.db.models import (
    Consent,
    ContactLedger,
    Customer,
    DeadLetter,
    Merchant,
    Outbox,
    RecoveryAction,
    RecoveryCase,
)
from app.guardrails.token import AppliedAction, mint
from app.tools.mock_provider import MockRazorpayProvider
from app.tools.outbox import BACKOFF_SCHEDULE, OutboxExecutor, next_attempt_delay
from app.workers.drainer import OutboxDrainer

CLOCK = FakeClock.at_ist(2026, 9, 1, 11, 30)
CASE_ID = "RC-0142"
REF = "rvp_rc-0142_1"


def applied_action(**overrides: object) -> AppliedAction:
    base: dict[str, object] = {
        "case_id": CASE_ID,
        "strategy": RecoveryStrategy.FRESH_LINK_ALT_RAIL,
        "amount_paise": 429_900,
        "discount_pct": 0.0,
        "discount_amount_paise": 0,
        "charge_amount_paise": 429_900,
        "link_expiry_minutes": 30,
        "channel": Channel.WHATSAPP,
        "message_class": MessageClass.TRANSACTIONAL,
        "escalation_rung": EscalationRung.A0_AUTONOMOUS,
        "reference_id": REF,
        "attempt_no": 1,
    }
    base.update(overrides)
    return AppliedAction(**base)  # type: ignore[arg-type]


def state_for(applied: AppliedAction) -> RecoveryState:
    return RecoveryState(
        case_id=CASE_ID,
        merchant_id="mch_glowkart",
        customer_id="cus_0001",
        playbook=Playbook.PAYMENT_FAILURE,
        amount_paise=applied.amount_paise,
        customer_first_name="Ananya",
        experiment_arm=ExperimentArm.TREATMENT,
        policy_applied=applied,
        policy_token=mint(applied, minted_at=CLOCK.now_utc()),
    )


@pytest.fixture
async def wired(engine: AsyncEngine):  # type: ignore[no-untyped-def]
    """A seeded merchant, customer and case, plus a working executor."""
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        session.add(
            Merchant(id="mch_glowkart", business_name="GlowKart", created_at=CLOCK.now_utc())
        )
        await session.flush()
        session.add(
            Customer(
                id="cus_0001",
                merchant_id="mch_glowkart",
                first_name="Ananya",
                phone_masked="+91 90****1234",
                phone_hash="h",
                first_seen_at=CLOCK.now_utc(),
            )
        )
        await session.flush()
        session.add(Consent(customer_id="cus_0001", updated_at=CLOCK.now_utc()))
        session.add(
            RecoveryCase(
                id=CASE_ID,
                merchant_id="mch_glowkart",
                customer_id="cus_0001",
                playbook=Playbook.PAYMENT_FAILURE,
                status=CaseStatus.EXECUTING,
                amount_paise=429_900,
                idempotency_hash="h1",
                window_expires_at=CLOCK.now_utc() + timedelta(hours=24),
                created_at=CLOCK.now_utc(),
            )
        )
        await session.commit()

    provider = MockRazorpayProvider(latency_s=0.0)
    executor = OutboxExecutor(
        sessionmaker=factory, provider=provider, clock=CLOCK, rng=random.Random(1)
    )
    drainer = OutboxDrainer(sessionmaker=factory, executor=executor, clock=CLOCK)
    return factory, provider, executor, drainer


async def count(factory, model) -> int:  # type: ignore[no-untyped-def]
    async with factory() as s:
        return int(await s.scalar(select(func.count()).select_from(model)) or 0)


# ===========================================================================
class TestHappyPath:
    async def test_a_link_is_created_and_recorded(self, wired) -> None:  # type: ignore[no-untyped-def]
        factory, provider, executor, _ = wired
        result = await executor.execute(state_for(applied_action()))

        assert result.status is CaseStatus.MONITORING
        assert result.payment_link_url
        assert provider.link_count() == 1
        assert await count(factory, RecoveryAction) == 1

    async def test_the_key_is_committed_before_the_call(self, wired) -> None:  # type: ignore[no-untyped-def]
        """The ordering the whole design rests on.

        If the reference were generated at call time, a crash before the commit
        would leave a live link with no local record, and the retry would mint
        a *different* key and create a second one.
        """
        factory, _provider, executor, _ = wired

        class Recorder(MockRazorpayProvider):
            outbox_at_call_time: int = -1

            async def create_payment_link(self, request):  # type: ignore[no-untyped-def]
                async with factory() as s:
                    row = await s.scalar(
                        select(Outbox).where(Outbox.reference_id == request.reference_id)
                    )
                    Recorder.outbox_at_call_time = 1 if row else 0
                return await super().create_payment_link(request)

        executor.provider = Recorder(latency_s=0.0)
        await executor.execute(state_for(applied_action()))
        assert Recorder.outbox_at_call_time == 1, (
            "the outbox row must be committed BEFORE the provider call"
        )

    async def test_the_contact_ledger_is_written_with_the_action(self, wired) -> None:  # type: ignore[no-untyped-def]
        """Same transaction as the dispatch. A cap check reading a ledger the
        dispatch has not been written to would let a second message through."""
        factory, _, executor, _ = wired
        await executor.execute(state_for(applied_action()))
        assert await count(factory, ContactLedger) == 1

    async def test_no_contact_row_when_no_channel_is_used(self, wired) -> None:  # type: ignore[no-untyped-def]
        factory, _, executor, _ = wired
        await executor.execute(state_for(applied_action(channel=Channel.NONE)))
        assert await count(factory, ContactLedger) == 0


# ===========================================================================
class TestIdempotency:
    async def test_running_twice_creates_one_link(self, wired) -> None:  # type: ignore[no-untyped-def]
        """One cart, one live link, ever."""
        factory, provider, executor, _ = wired
        await executor.execute(state_for(applied_action()))
        await executor.execute(state_for(applied_action()))

        assert provider.link_count() == 1
        assert await count(factory, Outbox) == 1
        assert await count(factory, RecoveryAction) == 1

    async def test_a_duplicate_reference_is_a_success_not_a_failure(self, wired) -> None:  # type: ignore[no-untyped-def]
        """Verified against live Razorpay Test Mode before this was written:
        the provider rejects the duplicate and the existing link is
        retrievable. It means a previous attempt got further than we recorded,
        and the provider just prevented a double-charge."""
        _factory, provider, executor, _ = wired
        # A link already exists at the provider, with no local record of it --
        # exactly the crash-between-phases state.
        from app.tools.provider import PaymentLinkRequest

        await provider.create_payment_link(
            PaymentLinkRequest(
                amount_paise=429_900, reference_id=REF, description="d", customer_name="Ananya"
            )
        )
        result = await executor.execute(state_for(applied_action()))

        assert result.status is CaseStatus.MONITORING
        assert provider.link_count() == 1
        assert result.trace[-1].detail["was_existing"] is True

    async def test_phase_two_is_safe_to_repeat(self, wired) -> None:  # type: ignore[no-untyped-def]
        factory, _, executor, _ = wired
        state = state_for(applied_action())
        await executor.execute(state)
        await executor._phase_two(
            state,
            (await _first_outbox(factory)).id,
            await _stub_result(),
        )
        assert await count(factory, RecoveryAction) == 1


async def _first_outbox(factory):  # type: ignore[no-untyped-def]
    async with factory() as s:
        return (await s.execute(select(Outbox))).scalars().first()


async def _stub_result():  # type: ignore[no-untyped-def]
    from app.tools.provider import PaymentLinkResult

    return PaymentLinkResult(
        link_id="plink_x", short_url="u", reference_id=REF, amount_paise=429_900, status="created"
    )


# ===========================================================================
class TestFailureHandling:
    async def test_a_timeout_schedules_a_retry(self, wired) -> None:  # type: ignore[no-untyped-def]
        factory, provider, executor, _ = wired
        provider.inject_fault("TIMEOUT")
        result = await executor.execute(state_for(applied_action()))

        assert result.status is CaseStatus.EXECUTING
        row = await _first_outbox(factory)
        assert row.status is OutboxStatus.PENDING
        assert row.attempt == 1
        assert row.next_attempt_at > CLOCK.now_utc()

    async def test_a_bad_request_goes_straight_to_the_dlq(self, wired) -> None:  # type: ignore[no-untyped-def]
        """Retrying a 400 produces the identical failure."""
        factory, provider, executor, _ = wired
        provider.inject_fault("BAD_REQUEST")
        result = await executor.execute(state_for(applied_action()))

        assert result.status is CaseStatus.FAILED_PERMANENT
        assert await count(factory, DeadLetter) == 1
        row = await _first_outbox(factory)
        assert row.status is OutboxStatus.DEAD

    async def test_the_retry_budget_ends_in_the_dlq(self, wired) -> None:  # type: ignore[no-untyped-def]
        factory, provider, executor, _ = wired
        provider.inject_fault("TIMEOUT", count=10)
        for _ in range(executor.max_attempts):
            await executor.execute(state_for(applied_action()))

        row = await _first_outbox(factory)
        assert row.status is OutboxStatus.DEAD
        assert await count(factory, DeadLetter) == 1

    async def test_a_dead_letter_preserves_the_provider_error(self, wired) -> None:  # type: ignore[no-untyped-def]
        """Never silently discarded: the merchant sees why."""
        factory, provider, executor, _ = wired
        provider.inject_fault("BAD_REQUEST")
        await executor.execute(state_for(applied_action()))
        async with factory() as s:
            dlq = (await s.execute(select(DeadLetter))).scalars().first()
        assert "400" in dlq.reason or "invalid" in dlq.reason.lower()
        assert dlq.status is DLQStatus.OPEN

    async def test_a_failure_leaves_no_action_row(self, wired) -> None:  # type: ignore[no-untyped-def]
        factory, provider, executor, _ = wired
        provider.inject_fault("TIMEOUT")
        await executor.execute(state_for(applied_action()))
        assert await count(factory, RecoveryAction) == 0
        assert await count(factory, ContactLedger) == 0


# ===========================================================================
class TestBackoff:
    def test_delays_grow(self) -> None:
        rng = random.Random(7)
        delays = [next_attempt_delay(i, rng) for i in range(len(BACKOFF_SCHEDULE))]
        assert delays == sorted(delays)

    def test_jitter_spreads_a_thundering_herd(self) -> None:
        """Without jitter, a provider blip that fails many cases at once makes
        them all retry in lockstep and hit the recovering provider as a spike."""
        rng = random.Random(11)
        samples = {round(next_attempt_delay(0, rng), 6) for _ in range(50)}
        assert len(samples) > 40

    def test_jitter_stays_within_a_quarter(self) -> None:
        rng = random.Random(3)
        base = BACKOFF_SCHEDULE[0]
        for _ in range(200):
            delay = next_attempt_delay(0, rng)
            assert base * 0.75 <= delay <= base * 1.25

    def test_the_schedule_does_not_run_off_the_end(self) -> None:
        assert next_attempt_delay(99, random.Random(1)) > 0


# ===========================================================================
class TestCrashRecovery:
    """Failure scenario #9, and the live chaos demo."""

    async def test_the_reconciler_resumes_a_crash_between_phases(self, wired) -> None:  # type: ignore[no-untyped-def]
        """The process died after the provider call and before the commit.

        The link exists at the provider; we have an intent and no outcome. The
        reconciler must finish the job with ONE link, not two.
        """
        factory, provider, executor, drainer = wired
        state = state_for(applied_action())

        # Phase one commits the intent...
        outbox_id = await executor._phase_one(state)
        # ...the provider call succeeds...
        await executor._call_provider(state)
        # ...and the process dies here. No phase two.
        assert provider.link_count() == 1
        assert await count(factory, RecoveryAction) == 0

        # Restart.
        CLOCK.advance(seconds=120)
        report = await drainer.reconcile_on_startup()

        assert report.recovered == 1, str(report)
        assert provider.link_count() == 1, "a second link was created"
        assert await count(factory, RecoveryAction) == 1
        row = await _first_outbox(factory)
        assert row.status is OutboxStatus.SENT
        assert row.id == outbox_id

    async def test_the_reconciler_ignores_fresh_rows(self, wired) -> None:  # type: ignore[no-untyped-def]
        """A row waiting on its backoff is not a crash."""
        _factory, _provider, executor, drainer = wired
        await executor._phase_one(state_for(applied_action()))
        assert await drainer.stale() == []

    async def test_a_terminal_case_is_not_retried(self, wired) -> None:  # type: ignore[no-untyped-def]
        """The customer may have paid while the row sat waiting. Re-executing
        would message someone about a payment they already made."""
        factory, provider, executor, drainer = wired
        await executor._phase_one(state_for(applied_action()))

        async with factory() as s:
            case = await s.get(RecoveryCase, CASE_ID)
            case.status = CaseStatus.RESOLVED_ORGANIC
            await s.commit()

        CLOCK.advance(seconds=120)
        report = await drainer.reconcile_on_startup()
        assert report.skipped_terminal == 1
        assert provider.link_count() == 0

    async def test_two_concurrent_workers_create_one_link(self, wired) -> None:  # type: ignore[no-untyped-def]
        """Nothing depends on the workers coordinating: both retry with the
        same reference and the provider refuses the second."""
        import asyncio

        factory, provider, executor, _ = wired
        await asyncio.gather(
            executor.execute(state_for(applied_action())),
            executor.execute(state_for(applied_action())),
        )
        assert provider.link_count() == 1
        assert await count(factory, Outbox) == 1

    async def test_the_drainer_survives_a_bad_row(self, wired) -> None:  # type: ignore[no-untyped-def]
        factory, _provider, _executor, drainer = wired
        async with factory() as s:
            s.add(
                Outbox(
                    id="obx_orphan",
                    case_id=CASE_ID,
                    action_type=ActionType.CREATE_PAYMENT_LINK,
                    reference_id="rvp_orphan_1",
                    payload_json="{not json",
                    status=OutboxStatus.PENDING,
                    attempt=0,
                    next_attempt_at=CLOCK.now_utc(),
                    created_at=CLOCK.now_utc(),
                )
            )
            await s.commit()
        with pytest.raises(Exception):  # noqa: B017 - drain_once surfaces it; run_forever suppresses
            await drainer.drain_once()


# ===========================================================================
class TestAuthorisation:
    async def test_execution_without_a_token_is_refused(self, wired) -> None:  # type: ignore[no-untyped-def]
        _factory, provider, executor, _ = wired
        state = state_for(applied_action())
        result = await executor.execute(RecoveryState(**{**state.__dict__, "policy_token": None}))
        assert result.status is CaseStatus.SUPPRESSED
        assert provider.link_count() == 0

    async def test_a_forged_token_raises_at_the_boundary(self, wired) -> None:  # type: ignore[no-untyped-def]
        """Checked here as well as in the graph node: this is the last thing
        before a provider call."""
        from app.guardrails.token import PolicyToken, PolicyTokenInvalid

        _factory, provider, executor, _ = wired
        applied = applied_action()
        state = RecoveryState(
            **{
                **state_for(applied).__dict__,
                "policy_token": PolicyToken(
                    applied=applied, minted_at=CLOCK.now_utc(), signature="0" * 64
                ),
            }
        )
        with pytest.raises(PolicyTokenInvalid):
            await executor.execute(state)
        assert provider.link_count() == 0

    async def test_the_charged_amount_is_the_authorised_one(self, wired) -> None:  # type: ignore[no-untyped-def]
        """A discount reduces what the customer pays, and the provider must be
        asked for exactly that."""
        _factory, provider, executor, _ = wired
        applied = applied_action(
            discount_pct=5.0, discount_amount_paise=21_495, charge_amount_paise=408_405
        )
        await executor.execute(state_for(applied))
        link = next(iter(provider.links.values()))
        assert link.amount_paise == 408_405
