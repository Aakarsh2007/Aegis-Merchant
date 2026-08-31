"""The last mile: a settling webhook becoming a verified recovery.

Two bugs live here, both found by paying a real ₹1 Razorpay link, and neither
findable from the seeded corpus:

**INC-024** — the webhook handler stored the event and dropped it.
`_process_event` was a Phase-2 stub, so every claim about attribution was about
code nothing called on the live path. `RAZORPAY_VERIFIED` could not move no
matter what Razorpay sent.

**INC-025** — a real `payment_link.paid` carries *three* entities
(`payment_link`, `order`, `payment`) and only `payment_link` has the
`reference_id`. The parser chose by a fixed priority list with `payment` first,
so it took the entity without the reference and a genuine recovery became
unattributable. Our own fixture had one entity, which is why every test passed.

The fixture these tests are pinned to is the actual delivery, not a
reconstruction.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.clock import FakeClock
from app.db.enums import ActionType, CaseStatus, OutboxStatus, Playbook
from app.db.models import Consent, Customer, Merchant, Outbox, RecoveryCase
from app.ingest.normalise import normalise
from app.ingest.settle import process_settlement

MULTI = (
    pathlib.Path(__file__).parent / "fixtures" / "razorpay" / "payment_link.paid.multi_entity.json"
)

CASE = "RC-SETTLE1"
REFERENCE = "rvp_rc-settle1_1"
NOW = datetime(2026, 9, 1, 11, 30, tzinfo=UTC)


def _load() -> dict[str, object] | None:
    if not MULTI.exists():
        return None
    doc = json.loads(MULTI.read_text(encoding="utf-8"))
    doc.pop("_fixture_meta", None)
    return doc


def _payload(reference: str = REFERENCE, amount: int = 100) -> dict[str, object]:
    """Razorpay's real three-entity shape, with the reference we choose."""
    return {
        "entity": "event",
        "event": "payment_link.paid",
        "contains": ["payment_link", "order", "payment"],
        "created_at": int(NOW.timestamp()),
        "payload": {
            "order": {
                "entity": {"id": "order_x", "entity": "order", "status": "paid", "amount": amount}
            },
            "payment": {
                "entity": {
                    "id": "pay_x",
                    "entity": "payment",
                    "status": "captured",
                    "amount": amount,
                }
            },
            "payment_link": {
                "entity": {
                    "id": "plink_x",
                    "entity": "payment_link",
                    "reference_id": reference,
                    "status": "paid",
                    "amount": amount,
                    "amount_paid": amount,
                }
            },
        },
    }


async def _seed(
    factory, *, status: CaseStatus = CaseStatus.MONITORING, window_hours: float = 24
) -> None:  # type: ignore[no-untyped-def]
    async with factory() as s:
        s.add(Merchant(id="mch_s", business_name="GlowKart", created_at=NOW))
        s.add(
            Customer(
                id="cus_s",
                merchant_id="mch_s",
                first_name="Ananya",
                phone_masked="+91 ***** 43210",
                phone_hash="h" * 64,
                ltv_paise=0,
                success_orders_count=0,
                first_seen_at=NOW,
            )
        )
        s.add(Consent(customer_id="cus_s", transactional=True, updated_at=NOW))
        await s.flush()
        s.add(
            RecoveryCase(
                id=CASE,
                merchant_id="mch_s",
                customer_id="cus_s",
                playbook=Playbook.PAYMENT_FAILURE,
                status=status,
                amount_paise=100,
                idempotency_hash="s" * 64,
                window_expires_at=NOW + timedelta(hours=window_hours),
                created_at=NOW,
            )
        )
        # Flush before the outbox: Outbox.case_id is a plain FK with no
        # relationship(), so the unit of work has no edge to order on.
        await s.flush()
        s.add(
            Outbox(
                id="obx_s",
                case_id=CASE,
                action_type=ActionType.CREATE_PAYMENT_LINK,
                reference_id=REFERENCE,
                payload_json="{}",
                status=OutboxStatus.SENT,
                attempt=1,
                next_attempt_at=NOW,
                created_at=NOW,
            )
        )
        await s.commit()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


# ===========================================================================
class TestTheMultiEntityPayload:
    """INC-025. The bug a real payload found and a fixture could not."""

    def test_the_reference_is_found_in_a_three_entity_payload(self) -> None:
        event = normalise(_payload(), event_id="evt_x")
        assert event.reference_id == REFERENCE, (
            "the reference lives on payment_link; picking `payment` loses it"
        )

    def test_the_entity_is_chosen_by_the_event_name(self) -> None:
        """`payment_link.paid` is about the payment link, whatever else is
        bundled alongside it as context."""
        event = normalise(_payload(), event_id="evt_x")
        assert event.payment_link_id == "plink_x"

    def test_a_reference_only_in_notes_is_still_found(self) -> None:
        """Razorpay round-trips `notes`, and some flows put it there instead."""
        payload = _payload()
        entity = payload["payload"]["payment_link"]["entity"]  # type: ignore[index]
        del entity["reference_id"]
        entity["notes"] = {"reference_id": REFERENCE}
        assert normalise(payload, event_id="evt_x").reference_id == REFERENCE

    def test_a_reference_on_a_sibling_entity_is_found(self) -> None:
        """The backstop. The reference is the attribution key, so it is worth
        looking everywhere rather than in one place and being wrong."""
        payload = _payload()
        del payload["payload"]["payment_link"]["entity"]["reference_id"]  # type: ignore[index]
        payload["payload"]["order"]["entity"]["reference_id"] = REFERENCE  # type: ignore[index]
        assert normalise(payload, event_id="evt_x").reference_id == REFERENCE

    def test_the_captured_fixture_is_a_real_multi_entity_delivery(self) -> None:
        """Pinned to the actual delivery. A fixture that reverted to a
        single-entity reconstruction would make these tests vacuous while
        staying green (INC-006)."""
        doc = json.loads(MULTI.read_text(encoding="utf-8")) if MULTI.exists() else None
        if doc is None:
            pytest.skip("no captured multi-entity webhook")
        meta = doc["_fixture_meta"]
        assert meta["provenance"] == "captured_live_webhook"
        assert set(doc["contains"]) == {"payment_link", "order", "payment"}
        assert doc["payload"]["payment_link"]["entity"]["reference_id"].startswith("rvp_")

    def test_the_real_payload_parses(self) -> None:
        doc = _load()
        if doc is None:
            pytest.skip("no captured multi-entity webhook")
        event = normalise(doc, event_id="TWK4SYivi78jL4")
        assert event.event_type == "payment_link.paid"
        assert event.reference_id
        assert event.amount_paise == 100


# ===========================================================================
class TestSettlement:
    """INC-024. The handler used to store the event and drop it."""

    @pytest.mark.asyncio
    async def test_a_verified_webhook_marks_the_case_recovered(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory)

        outcome = await process_settlement(
            factory, payload=_payload(), event_id="evt_real", clock=clock
        )
        assert outcome.counted, outcome.reason

        async with factory() as s:
            case = await s.get(RecoveryCase, CASE)
        assert case is not None
        assert case.status is CaseStatus.RECOVERED
        assert case.recovered_amount_paise == 100

    @pytest.mark.asyncio
    async def test_the_verifier_is_the_real_event_id_not_a_sim_prefix(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        """This is what puts the amount in the RAZORPAY_VERIFIED column rather
        than the SIMULATED one — the only figure here that constitutes proof."""
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory)
        await process_settlement(factory, payload=_payload(), event_id="evt_real", clock=clock)
        async with factory() as s:
            case = await s.get(RecoveryCase, CASE)
        assert case is not None
        assert case.recovery_verified_by == "evt_real"
        assert not case.recovery_verified_by.startswith("sim_evt_")

    @pytest.mark.asyncio
    async def test_it_writes_an_audit_block(self, engine: AsyncEngine, clock: FakeClock) -> None:
        from app.db.models import AuditBlock
        from app.tools.audit import AuditChain

        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory)
        await process_settlement(factory, payload=_payload(), event_id="evt_real", clock=clock)
        async with factory() as s:
            names = [b.event_name for b in (await s.execute(select(AuditBlock))).scalars().all()]
            assert "recovery.verified" in names
            assert (await AuditChain(clock).verify(s)).valid


class TestSettlementRefusals:
    """Each of these is money that arrived and is not ours to claim."""

    @pytest.mark.asyncio
    async def test_a_reference_we_did_not_issue_is_not_counted(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        """The line between attribution and coincidence."""
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory)
        outcome = await process_settlement(
            factory,
            payload=_payload(reference="rvp_someone_elses_1"),
            event_id="evt_x",
            clock=clock,
        )
        assert not outcome.counted
        assert "not issued by us" in outcome.reason

    @pytest.mark.asyncio
    async def test_payment_captured_does_not_settle(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        """Deliberately excluded: it fires for organic checkout completions
        too, and counting it would credit us with payments we never touched."""
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory)
        payload = _payload()
        payload["event"] = "payment.captured"
        outcome = await process_settlement(factory, payload=payload, event_id="evt_x", clock=clock)
        assert not outcome.counted
        assert "does not settle" in outcome.reason

    @pytest.mark.asyncio
    async def test_a_case_not_in_monitoring_resolves_organic(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        """A control-arm case that pays is the counterfactual, not a recovery.
        Counting it would destroy the measurement it exists to provide."""
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory, status=CaseStatus.OBSERVED_NO_ACTION)
        outcome = await process_settlement(
            factory, payload=_payload(), event_id="evt_x", clock=clock
        )
        assert not outcome.counted
        async with factory() as s:
            case = await s.get(RecoveryCase, CASE)
        assert case is not None
        assert case.status is CaseStatus.RESOLVED_ORGANIC
        assert case.recovered_amount_paise == 0

    @pytest.mark.asyncio
    async def test_a_second_delivery_does_not_double_count(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        """Razorpay retries. At-least-once delivery must not become
        at-least-once revenue."""
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory)
        first = await process_settlement(factory, payload=_payload(), event_id="evt_a", clock=clock)
        second = await process_settlement(
            factory, payload=_payload(), event_id="evt_b", clock=clock
        )
        assert first.counted
        assert not second.counted
        async with factory() as s:
            case = await s.get(RecoveryCase, CASE)
        assert case is not None
        assert case.recovered_amount_paise == 100

    @pytest.mark.asyncio
    async def test_a_webhook_claiming_more_cannot_inflate_the_figure(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        """The amount comes from the case, never the webhook."""
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory)
        await process_settlement(
            factory, payload=_payload(amount=9_999_999), event_id="evt_x", clock=clock
        )
        async with factory() as s:
            case = await s.get(RecoveryCase, CASE)
        assert case is not None
        assert case.recovered_amount_paise == 100

    @pytest.mark.asyncio
    async def test_settlement_never_raises(self, engine: AsyncEngine, clock: FakeClock) -> None:
        """It runs as a background task after the response has gone. An
        exception there is a log line nobody reads and a silently unattributed
        payment."""
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _seed(factory)
        for payload in ({}, {"event": "payment_link.paid"}, {"payload": "nonsense"}):
            outcome = await process_settlement(
                factory,
                payload=payload,  # type: ignore[arg-type]
                event_id="evt_x",
                clock=clock,
            )
            assert not outcome.counted
