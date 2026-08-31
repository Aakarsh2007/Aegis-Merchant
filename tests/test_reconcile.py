"""Reconciliation: asking Razorpay instead of waiting to be told.

A webhook is a notification and can be lost, delayed, or delivered to a URL
that has since died — which happened repeatedly while building this. A recovery
system whose only knowledge of a settlement arrives by webhook will eventually
miss money it actually recovered.

The tests that matter here are the ones about **not** counting: a link that was
not paid, a case already settled, a reference Razorpay does not recognise, and
a second run over the same data. Reconciliation writes to the
RAZORPAY_VERIFIED column, so a bug here inflates the one figure in this project
that is supposed to be beyond argument.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.clock import FakeClock
from app.db.enums import ActionType, CaseStatus, OutboxStatus, Playbook
from app.db.models import Consent, Customer, Merchant, Outbox, RecoveryCase
from app.tools.provider import PaymentLinkResult
from app.workers.reconcile import reconcile_outstanding

NOW = datetime(2026, 9, 1, 11, 30, tzinfo=UTC)
CASE = "RC-REC1"
REFERENCE = "rvp_rc-rec1_1"


class FakeProvider:
    """Just enough provider to answer the one question reconciliation asks."""

    def __init__(self, links: dict[str, PaymentLinkResult | None], *, raises: bool = False):
        self.links = links
        self.raises = raises
        self.calls: list[str] = []

    async def get_payment_link_by_reference(self, reference_id: str) -> PaymentLinkResult | None:
        self.calls.append(reference_id)
        if self.raises:
            raise RuntimeError("razorpay unreachable")
        return self.links.get(reference_id)


def _link(
    *, status: str = "paid", payments: list[dict[str, Any]] | None = None
) -> PaymentLinkResult:
    return PaymentLinkResult(
        link_id="plink_abc",
        short_url="https://rzp.io/x",
        reference_id=REFERENCE,
        amount_paise=100,
        status=status,
        was_existing=True,
        raw={"payments": payments if payments is not None else []},
    )


async def _seed(
    factory,  # type: ignore[no-untyped-def]
    *,
    case_status: CaseStatus = CaseStatus.MONITORING,
    verified: str | None = None,
    outbox_status: OutboxStatus = OutboxStatus.SENT,
    amount: int = 100,
) -> None:
    async with factory() as s:
        s.add(Merchant(id="mch_r", business_name="GlowKart", created_at=NOW))
        s.add(
            Customer(
                id="cus_r",
                merchant_id="mch_r",
                first_name="Ananya",
                phone_masked="x",
                phone_hash="h" * 64,
                ltv_paise=0,
                success_orders_count=0,
                first_seen_at=NOW,
            )
        )
        s.add(Consent(customer_id="cus_r", transactional=True, updated_at=NOW))
        await s.flush()
        s.add(
            RecoveryCase(
                id=CASE,
                merchant_id="mch_r",
                customer_id="cus_r",
                playbook=Playbook.PAYMENT_FAILURE,
                status=case_status,
                amount_paise=amount,
                recovered_amount_paise=100 if verified else 0,
                recovery_verified_by=verified,
                idempotency_hash="r" * 64,
                window_expires_at=NOW + timedelta(hours=24),
                created_at=NOW,
            )
        )
        await s.flush()
        s.add(
            Outbox(
                id="obx_r",
                case_id=CASE,
                action_type=ActionType.CREATE_PAYMENT_LINK,
                reference_id=REFERENCE,
                payload_json="{}",
                status=outbox_status,
                attempt=1,
                next_attempt_at=NOW,
                created_at=NOW,
            )
        )
        await s.commit()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


async def _run(engine: AsyncEngine, provider: Any, clock: FakeClock):  # type: ignore[no-untyped-def]
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    return factory, await reconcile_outstanding(factory, provider=provider, clock=clock)


# ===========================================================================
class TestItSettlesWhatWasPaid:
    @pytest.mark.asyncio
    async def test_a_paid_link_becomes_a_verified_recovery(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory)
        _, result = await _run(engine, FakeProvider({REFERENCE: _link()}), clock)

        assert result.settled == 1
        assert result.recovered_paise == 100
        async with factory() as s:
            case = await s.get(RecoveryCase, CASE)
        assert case is not None
        assert case.status is CaseStatus.RECOVERED
        assert case.recovery_verified_by == "plink_abc"

    @pytest.mark.asyncio
    async def test_a_payment_id_is_preferred_when_available(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        """Stronger evidence than the link id, when Razorpay populates it."""
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory)
        link = _link(payments=[{"payment_id": "pay_xyz", "status": "captured"}])
        await _run(engine, FakeProvider({REFERENCE: link}), clock)
        async with factory() as s:
            case = await s.get(RecoveryCase, CASE)
        assert case is not None
        assert case.recovery_verified_by == "pay_xyz"

    @pytest.mark.asyncio
    async def test_an_uncaptured_payment_is_not_used_as_proof(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        """An authorised-but-not-captured payment has not settled."""
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory)
        link = _link(payments=[{"payment_id": "pay_pending", "status": "authorized"}])
        await _run(engine, FakeProvider({REFERENCE: link}), clock)
        async with factory() as s:
            case = await s.get(RecoveryCase, CASE)
        assert case is not None
        assert case.recovery_verified_by == "plink_abc"

    @pytest.mark.asyncio
    async def test_it_writes_an_audit_block_naming_the_source(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        """A polled settlement must not be indistinguishable from a webhook
        one in the ledger. Both are Razorpay asserting the payment; only one
        involved a signature."""
        import json

        from app.db.models import AuditBlock
        from app.tools.audit import AuditChain

        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory)
        await _run(engine, FakeProvider({REFERENCE: _link()}), clock)

        async with factory() as s:
            block = (
                (await s.execute(select(AuditBlock).order_by(AuditBlock.block_index.desc())))
                .scalars()
                .first()
            )
            assert (await AuditChain(clock).verify(s)).valid
        assert block is not None
        payload = json.loads(block.payload_canonical)
        assert payload["source"] == "razorpay_api_reconciliation"
        assert payload["provenance"] == "RAZORPAY_VERIFIED"
        assert payload["verifier_kind"] in {"payment_id", "payment_link_id"}


# ===========================================================================
class TestItDoesNotCountWhatItShouldNot:
    """Reconciliation writes to the RAZORPAY_VERIFIED column, so a bug here
    inflates the one figure that is meant to be beyond argument."""

    @pytest.mark.asyncio
    async def test_an_unpaid_link_is_left_alone(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory)
        _, result = await _run(engine, FakeProvider({REFERENCE: _link(status="created")}), clock)
        assert result.settled == 0
        assert result.still_open == 1
        async with factory() as s:
            case = await s.get(RecoveryCase, CASE)
        assert case is not None
        assert case.status is CaseStatus.MONITORING
        assert case.recovery_verified_by is None

    @pytest.mark.asyncio
    async def test_an_already_verified_case_is_not_touched(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        """The webhook may have won the race. Exactly one path should settle a
        case, and it does not matter which."""
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory, verified="evt_webhook_got_there_first")
        _, result = await _run(engine, FakeProvider({REFERENCE: _link()}), clock)
        assert result.settled == 0
        async with factory() as s:
            case = await s.get(RecoveryCase, CASE)
        assert case is not None
        assert case.recovery_verified_by == "evt_webhook_got_there_first"

    @pytest.mark.asyncio
    async def test_running_twice_does_not_double_count(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        """It is meant to be run on a timer."""
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory)
        provider = FakeProvider({REFERENCE: _link()})
        _, first = await _run(engine, provider, clock)
        _, second = await _run(engine, provider, clock)
        assert first.settled == 1
        assert second.settled == 0
        async with factory() as s:
            case = await s.get(RecoveryCase, CASE)
        assert case is not None
        assert case.recovered_amount_paise == 100

    @pytest.mark.asyncio
    async def test_a_reference_razorpay_does_not_know_is_an_error_not_a_recovery(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory)
        _, result = await _run(engine, FakeProvider({}), clock)
        assert result.settled == 0
        assert result.errors == 1

    @pytest.mark.asyncio
    async def test_a_provider_failure_does_not_settle_anything(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        """An unreachable provider must not be read as "not paid" *or* as
        paid. It is an error, and it says so."""
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory)
        _, result = await _run(engine, FakeProvider({}, raises=True), clock)
        assert result.settled == 0
        assert result.errors == 1
        async with factory() as s:
            case = await s.get(RecoveryCase, CASE)
        assert case is not None
        assert case.status is CaseStatus.MONITORING

    @pytest.mark.asyncio
    async def test_the_amount_comes_from_the_case_not_the_provider(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        """Same rule as the webhook path: a provider reporting a larger figure
        must not inflate the metric."""
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory, amount=100)
        inflated = PaymentLinkResult(
            link_id="plink_abc",
            short_url="u",
            reference_id=REFERENCE,
            amount_paise=9_999_999,
            status="paid",
            raw={},
        )
        await _run(engine, FakeProvider({REFERENCE: inflated}), clock)
        async with factory() as s:
            case = await s.get(RecoveryCase, CASE)
        assert case is not None
        assert case.recovered_amount_paise == 100

    @pytest.mark.asyncio
    async def test_a_control_arm_case_is_never_reconciled(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        """We issued no reference for it, so there is nothing of ours to
        credit — and counting it would destroy the holdout measurement."""
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory, case_status=CaseStatus.OBSERVED_NO_ACTION)
        _, result = await _run(engine, FakeProvider({REFERENCE: _link()}), clock)
        assert result.checked == 0
        assert result.settled == 0
