"""Schema, pragma and constraint tests.

The theme here is *proving* rather than assuming. A pragma that was set but did
not take, or a UNIQUE that exists in the model but not in the database, both
look fine in code review and fail in production. So every one is exercised.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.clock import FakeClock
from app.db.enums import (
    ActionType,
    AttemptKind,
    CaseStatus,
    ErrorSource,
    PaymentStatus,
    Playbook,
)
from app.db.models import (
    ALL_MODELS,
    AuditBlock,
    Customer,
    Merchant,
    Outbox,
    PaymentAttempt,
    PolicyConfig,
    RecoveryCase,
    WebhookEvent,
)
from app.db.session import PRAGMAS, read_pragmas

# asyncio_mode = "auto" in pyproject.toml collects async tests automatically.

EXPECTED_TABLES = {
    "merchants",
    "policy_configs",
    "customers",
    "consents",
    "contact_ledger",
    "message_templates",
    "payment_attempts",
    "webhook_events",
    "recovery_cases",
    "experiment_assignments",
    "outbox",
    "recovery_actions",
    "dlq",
    "approval_requests",
    "promises_to_pay",
    "audit_blocks",
    "llm_calls",
    "llm_cache",
}


# ---------------------------------------------------------------------------
# Pragmas — read back from a live connection, never assumed
# ---------------------------------------------------------------------------
class TestPragmas:
    async def test_wal_mode_is_active(self, engine: AsyncEngine) -> None:
        """WAL lets the API read while a worker writes. Without it the
        dashboard blocks whenever the outbox drainer holds the write lock."""
        pragmas = await read_pragmas(engine)
        assert str(pragmas["journal_mode"]).lower() == "wal"

    async def test_foreign_keys_are_enforced(self, engine: AsyncEngine) -> None:
        """SQLite enforces FKs only if asked, per connection, and is off by
        default -- silently. Every ondelete in models.py is inert without this."""
        pragmas = await read_pragmas(engine)
        assert pragmas["foreign_keys"] == 1

    async def test_busy_timeout_is_set(self, engine: AsyncEngine) -> None:
        """WAL permits one writer; concurrent writers need to be told to wait."""
        pragmas = await read_pragmas(engine)
        assert pragmas["busy_timeout"] == int(PRAGMAS["busy_timeout"])

    async def test_synchronous_is_normal(self, engine: AsyncEngine) -> None:
        """NORMAL survives process crash -- the case the reconciler exists for."""
        pragmas = await read_pragmas(engine)
        assert pragmas["synchronous"] == 1  # 1 == NORMAL


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
class TestTables:
    async def test_all_eighteen_tables_exist(self, engine: AsyncEngine) -> None:
        async with engine.connect() as conn:
            names = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
        assert names == EXPECTED_TABLES

    def test_model_count_matches_expected(self) -> None:
        assert len(ALL_MODELS) == 18
        assert {m.__tablename__ for m in ALL_MODELS} == EXPECTED_TABLES

    async def test_constraints_are_named(self, engine: AsyncEngine) -> None:
        """Unnamed constraints cannot be referenced or asserted about later."""
        async with engine.connect() as conn:
            uniques = await conn.run_sync(
                lambda c: inspect(c).get_unique_constraints("recovery_cases")
            )
        assert any(u["name"] and "idempotency" in u["name"] for u in uniques)


# ---------------------------------------------------------------------------
# Foreign keys actually bite
# ---------------------------------------------------------------------------
class TestForeignKeys:
    async def test_orphan_customer_is_rejected(
        self, session: AsyncSession, clock: FakeClock
    ) -> None:
        session.add(
            Customer(
                id="cus_orphan",
                merchant_id="mch_does_not_exist",
                first_name="Ghost",
                phone_masked="+91 90****0000",
                phone_hash="deadbeef",
                first_seen_at=clock.now_utc(),
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()

    async def test_cascade_delete_removes_children(
        self, session: AsyncSession, clock: FakeClock
    ) -> None:
        session.add(Merchant(id="mch_x", business_name="X", created_at=clock.now_utc()))
        await session.flush()
        session.add(
            Customer(
                id="cus_x",
                merchant_id="mch_x",
                first_name="A",
                phone_masked="+91 90****1111",
                phone_hash="aa",
                first_seen_at=clock.now_utc(),
            )
        )
        await session.commit()

        await session.execute(text("DELETE FROM merchants WHERE id = 'mch_x'"))
        await session.commit()
        remaining = await session.scalar(select(Customer).where(Customer.id == "cus_x"))
        assert remaining is None


# ---------------------------------------------------------------------------
# Unique constraints — each one is a defence, so each is exercised
# ---------------------------------------------------------------------------
class TestUniqueConstraints:
    async def test_duplicate_webhook_event_id_rejected(
        self, session: AsyncSession, clock: FakeClock
    ) -> None:
        """This single UNIQUE is the entire duplicate-webhook defence."""
        for _ in range(2):
            session.add(
                WebhookEvent(
                    id=f"evt_row_{_}",
                    event_id="evt_razorpay_same",
                    event_type="payment.failed",
                    payload_json="{}",
                    received_at=clock.now_utc(),
                )
            )
        with pytest.raises(IntegrityError):
            await session.commit()

    async def test_duplicate_case_idempotency_hash_rejected(
        self, session: AsyncSession, clock: FakeClock
    ) -> None:
        """Two workers racing the same order: exactly one wins the INSERT."""
        await _merchant_and_customer(session, clock)
        for i in range(2):
            session.add(_case(f"RC-{i:04d}", clock, idempotency_hash="same-hash"))
        with pytest.raises(IntegrityError):
            await session.commit()

    async def test_duplicate_outbox_reference_id_rejected(
        self, session: AsyncSession, clock: FakeClock
    ) -> None:
        """reference_id is the idempotency key; a duplicate locally would mean
        two live payment links for one cart."""
        await _merchant_and_customer(session, clock)
        session.add(_case("RC-0001", clock, idempotency_hash="h1"))
        await session.flush()
        for i in range(2):
            session.add(
                Outbox(
                    id=f"obx_{i}",
                    case_id="RC-0001",
                    action_type=ActionType.CREATE_PAYMENT_LINK,
                    reference_id="rvp_RC-0001_1",
                    payload_json="{}",
                    attempt=i,
                    next_attempt_at=clock.now_utc(),
                    created_at=clock.now_utc(),
                )
            )
        with pytest.raises(IntegrityError):
            await session.commit()

    async def test_duplicate_audit_block_index_rejected(
        self, session: AsyncSession, clock: FakeClock
    ) -> None:
        for i in range(2):
            session.add(
                AuditBlock(
                    id=f"blk_{i}",
                    block_index=0,
                    prev_hash="0" * 64,
                    current_hash=f"{i:064d}",
                    event_name="TEST",
                    actor="test",
                    payload_canonical="{}",
                    payload_hash="x",
                    created_at=clock.now_utc(),
                )
            )
        with pytest.raises(IntegrityError):
            await session.commit()


# ---------------------------------------------------------------------------
# CHECK constraints — the policy invariants that live in the schema
# ---------------------------------------------------------------------------
class TestCheckConstraints:
    async def test_recovery_without_proof_is_rejected(
        self, session: AsyncSession, clock: FakeClock
    ) -> None:
        """The most important CHECK in the schema.

        A recovered amount with no verifying webhook is exactly the
        unverifiable claim the attribution rule exists to prevent (§14.1) --
        so the database itself refuses to store one.
        """
        await _merchant_and_customer(session, clock)
        case = _case("RC-0009", clock, idempotency_hash="h9")
        case.recovered_amount_paise = 429_900
        case.recovery_verified_by = None
        session.add(case)
        with pytest.raises(IntegrityError):
            await session.commit()

    async def test_recovery_with_proof_is_accepted(
        self, session: AsyncSession, clock: FakeClock
    ) -> None:
        await _merchant_and_customer(session, clock)
        case = _case("RC-0010", clock, idempotency_hash="h10")
        case.recovered_amount_paise = 429_900
        case.recovery_verified_by = "evt_signed_webhook_123"
        session.add(case)
        await session.commit()
        assert case.recovered_amount_paise == 429_900

    async def test_zero_recovery_without_proof_is_fine(
        self, session: AsyncSession, clock: FakeClock
    ) -> None:
        await _merchant_and_customer(session, clock)
        session.add(_case("RC-0011", clock, idempotency_hash="h11"))
        await session.commit()

    async def test_clamp_target_above_ceiling_is_rejected(
        self, session: AsyncSession, clock: FakeClock
    ) -> None:
        """Clamping to a value above the ceiling would defeat the ceiling."""
        session.add(Merchant(id="mch_p", business_name="P", created_at=clock.now_utc()))
        await session.flush()
        session.add(_policy("mch_p", max_discount_pct=5.0, default_discount_pct=9.0))
        with pytest.raises(IntegrityError):
            await session.commit()

    async def test_control_fraction_of_one_is_rejected(
        self, session: AsyncSession, clock: FakeClock
    ) -> None:
        """A control fraction of 1.0 would mean never acting at all."""
        session.add(Merchant(id="mch_q", business_name="Q", created_at=clock.now_utc()))
        await session.flush()
        session.add(_policy("mch_q", control_arm_fraction=1.0))
        with pytest.raises(IntegrityError):
            await session.commit()

    async def test_contact_caps_must_be_ordered(
        self, session: AsyncSession, clock: FakeClock
    ) -> None:
        session.add(Merchant(id="mch_r", business_name="R", created_at=clock.now_utc()))
        await session.flush()
        session.add(_policy("mch_r", max_contacts_24h=5, max_contacts_48h=2))
        with pytest.raises(IntegrityError):
            await session.commit()

    async def test_zero_amount_attempt_is_rejected(
        self, session: AsyncSession, clock: FakeClock
    ) -> None:
        await _merchant_and_customer(session, clock)
        session.add(
            PaymentAttempt(
                id="atp_bad",
                merchant_id="mch_t",
                customer_id="cus_t",
                kind=AttemptKind.CHECKOUT,
                status=PaymentStatus.FAILED,
                amount_paise=0,
                attempted_at=clock.now_utc(),
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


# ---------------------------------------------------------------------------
# Enum storage
# ---------------------------------------------------------------------------
class TestEnumStorage:
    async def test_razorpay_error_source_stored_lowercase(
        self, session: AsyncSession, clock: FakeClock
    ) -> None:
        """error_source holds Razorpay's own 'bank', not our 'BANK'.

        The classifier keys on the API's values, so storing the member name
        would quietly break every lookup.
        """
        await _merchant_and_customer(session, clock)
        session.add(
            PaymentAttempt(
                id="atp_e",
                merchant_id="mch_t",
                customer_id="cus_t",
                kind=AttemptKind.CHECKOUT,
                status=PaymentStatus.FAILED,
                amount_paise=100,
                error_source=ErrorSource.BANK,
                attempted_at=clock.now_utc(),
            )
        )
        await session.commit()
        raw = await session.scalar(
            text("SELECT error_source FROM payment_attempts WHERE id = 'atp_e'")
        )
        assert raw == "bank"

    async def test_invalid_enum_value_is_rejected(
        self, session: AsyncSession, clock: FakeClock
    ) -> None:
        await _merchant_and_customer(session, clock)
        session.add(_case("RC-0020", clock, idempotency_hash="h20"))
        await session.commit()
        with pytest.raises(Exception):  # noqa: B017 - CHECK violation or LookupError
            await session.execute(
                text("UPDATE recovery_cases SET status = 'NOT_A_STATUS' WHERE id = 'RC-0020'")
            )
            await session.commit()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
async def _merchant_and_customer(session: AsyncSession, clock: FakeClock) -> None:
    session.add(Merchant(id="mch_t", business_name="T", created_at=clock.now_utc()))
    await session.flush()
    session.add(
        Customer(
            id="cus_t",
            merchant_id="mch_t",
            first_name="T",
            phone_masked="+91 90****2222",
            phone_hash="bb",
            first_seen_at=clock.now_utc(),
        )
    )
    await session.flush()


def _case(case_id: str, clock: FakeClock, *, idempotency_hash: str) -> RecoveryCase:
    return RecoveryCase(
        id=case_id,
        merchant_id="mch_t",
        customer_id="cus_t",
        playbook=Playbook.PAYMENT_FAILURE,
        status=CaseStatus.DETECTED,
        amount_paise=429_900,
        idempotency_hash=idempotency_hash,
        window_expires_at=clock.now_utc() + timedelta(hours=24),
        created_at=clock.now_utc(),
    )


def _policy(merchant_id: str, **overrides: float | int) -> PolicyConfig:
    defaults: dict[str, float | int] = {
        "max_autonomous_amount_paise": 1_000_000,
        "hitl_dual_signal_amount_paise": 10_000_000,
        "max_discount_pct": 7.0,
        "default_discount_pct": 5.0,
        "max_discount_absolute_paise": 50_000,
        "max_contacts_24h": 1,
        "max_contacts_48h": 2,
        "max_attempts_per_case": 2,
        "max_discount_bearing_attempts": 1,
        "link_expiry_minutes": 30,
        "quiet_hours_start_ist": 21,
        "quiet_hours_end_ist": 9,
        "approval_ttl_minutes": 240,
        "daily_action_budget": 50,
        "monthly_discount_exposure_paise": 20_000_000,
        "pre_debit_notice_hours": 24,
        "max_representations": 3,
        "control_arm_fraction": 0.18,
    }
    defaults.update(overrides)
    return PolicyConfig(merchant_id=merchant_id, **defaults)  # type: ignore[arg-type]
