"""HITL approvals and the time-driven sweeps.

The approval endpoint is the one place a human authorises money movement, so
the tests are about the ways an approval can be *wrong* rather than the way it
works: a stale one, a replayed one, one for an action that changed after it was
displayed, and one whose reviewer is named by the caller instead of the token.

The scheduler tests cover the case nobody writes: nothing happening. An
approval nobody actions, and a message held for quiet hours whose case dies
while it waits.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.core.clock import FakeClock
from app.db.enums import (
    ActionType,
    ApprovalStatus,
    CaseStatus,
    EscalationRung,
    OutboxStatus,
    Playbook,
)
from app.db.models import ApprovalRequest, Consent, Customer, Merchant, Outbox, RecoveryCase
from app.deps import get_clock, get_db
from app.main import create_app
from app.services.scheduler import Scheduler, approval_expires_at
from app.tools.audit import AuditChain

TOKEN = "rvp_test_token_0123456789abcdef"
MERCHANT = "mch_glowkart"
CUSTOMER = "cus_test_0001"
CASE = "RC-9001"
APPROVAL = "apr_test_0001"

POLICY_APPLIED = {
    "channel": "WHATSAPP",
    "discount_pct": 0.0,
    "message_class": "TRANSACTIONAL",
    "amount_paise": 1850000,
}
POLICY_JSON = json.dumps(POLICY_APPLIED, sort_keys=True, separators=(",", ":"))
POLICY_HASH = hashlib.sha256(POLICY_JSON.encode()).hexdigest()

NOW = datetime(2026, 9, 1, 11, 30, tzinfo=UTC)


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "razorpay_key_id": "",
        "razorpay_key_secret": "",
        "gemini_api_key": "",
        "api_token": TOKEN,
        "environment": "development",
    }
    base.update(over)
    return Settings(**base)


async def _seed(
    session: AsyncSession,
    *,
    clock: FakeClock,
    expires_in_minutes: int = 240,
    case_status: CaseStatus = CaseStatus.AWAITING_APPROVAL,
) -> None:
    now = clock.now_utc()
    session.add(Merchant(id=MERCHANT, business_name="GlowKart", created_at=now))
    session.add(
        Customer(
            id=CUSTOMER,
            merchant_id=MERCHANT,
            first_name="Rahul",
            phone_masked="+91 ***** 43210",
            phone_hash="h" * 64,
            ltv_paise=0,
            success_orders_count=0,
            first_seen_at=now,
        )
    )
    session.add(
        Consent(
            customer_id=CUSTOMER,
            transactional=True,
            marketing=False,
            dnd_registered=False,
            opted_out=False,
            updated_at=now,
        )
    )
    # Same reason as below: parents before children, explicitly.
    await session.flush()
    session.add(
        RecoveryCase(
            id=CASE,
            merchant_id=MERCHANT,
            customer_id=CUSTOMER,
            playbook=Playbook.RECEIVABLE,
            status=case_status,
            amount_paise=1850000,
            idempotency_hash="x" * 64,
            window_expires_at=now + timedelta(days=30),
            created_at=now,
        )
    )
    # Flush before the approval. `ApprovalRequest.case_id` is a plain FK column
    # with no relationship(), so the unit of work has no dependency edge to
    # sort on and would insert the approval ahead of its case.
    await session.flush()
    session.add(
        ApprovalRequest(
            id=APPROVAL,
            case_id=CASE,
            trigger_rung=EscalationRung.A2_APPROVAL,
            trigger_reason="amount >= Rs 10,000",
            amount_paise=1850000,
            policy_applied_json=POLICY_JSON,
            policy_applied_hash=POLICY_HASH,
            status=ApprovalStatus.PENDING,
            expires_at=approval_expires_at(now, ttl_minutes=expires_in_minutes),
            created_at=now,
        )
    )
    await session.commit()


@pytest_asyncio.fixture
async def ctx(engine: AsyncEngine) -> AsyncIterator[tuple[TestClient, FakeClock, Any]]:
    clock = FakeClock(NOW)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        await _seed(s, clock=clock)

    async def _db() -> AsyncIterator[Any]:
        async with factory() as session:
            yield session

    settings = _settings()
    app = create_app(settings)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_clock] = lambda: clock
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c, clock, factory


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _action(client: TestClient, **over: Any) -> Any:
    body: dict[str, Any] = {"action": "approve", "policy_applied_hash": POLICY_HASH}
    body.update(over)
    return client.post(f"/api/v1/approvals/{APPROVAL}/action", json=body, headers=_auth())


# ===========================================================================
class TestListing:
    def test_pending_approvals_carry_the_hash_to_present_back(self, ctx: Any) -> None:
        client, _, _ = ctx
        body = client.get("/api/v1/approvals", headers=_auth()).json()
        assert body["count"] == 1
        row = body["approvals"][0]
        assert row["policy_applied_hash"] == POLICY_HASH
        assert row["seconds_remaining"] == 240 * 60
        assert row["expired"] is False

    def test_listing_requires_auth(self, ctx: Any) -> None:
        client, _, _ = ctx
        assert client.get("/api/v1/approvals").status_code == 401

    def test_an_expired_row_is_shown_as_expired_not_hidden(self, ctx: Any) -> None:
        """Hiding it would make the queue look healthier than it is. A
        reviewer needs to see that something aged out unactioned."""
        client, clock, _ = ctx
        clock.advance(hours=5)
        row = client.get("/api/v1/approvals", headers=_auth()).json()["approvals"][0]
        assert row["expired"] is True
        assert row["seconds_remaining"] == 0


# ===========================================================================
class TestActioning:
    def test_approve_records_the_principal_from_the_token(self, ctx: Any) -> None:
        """Not from the body. A caller who could name their own reviewer could
        authorise Rs 1,00,000 as "the CFO"."""
        client, _, _ = ctx
        body = _action(client).json()
        assert body["status"] == "APPROVED"
        assert body["reviewed_by"].startswith("api:")
        assert body["case_status"] == CaseStatus.STRATEGY_FORMED.value

    def test_the_request_body_cannot_name_a_reviewer(self, ctx: Any) -> None:
        """extra="forbid" means an attempt to inject one is a 422, not a
        silently ignored field."""
        client, _, _ = ctx
        assert _action(client, reviewed_by="the CFO").status_code == 422

    def test_reject_closes_the_case(self, ctx: Any) -> None:
        client, _, _ = ctx
        body = _action(client, action="reject", notes="customer already called").json()
        assert body["status"] == "REJECTED"
        assert body["case_status"] == CaseStatus.REJECTED.value

    def test_an_unknown_action_is_refused(self, ctx: Any) -> None:
        client, _, _ = ctx
        assert _action(client, action="maybe").status_code == 422

    def test_actioning_requires_auth(self, ctx: Any) -> None:
        client, _, _ = ctx
        r = client.post(
            f"/api/v1/approvals/{APPROVAL}/action",
            json={"action": "approve", "policy_applied_hash": POLICY_HASH},
        )
        assert r.status_code == 401

    def test_unknown_approval_is_404(self, ctx: Any) -> None:
        client, _, _ = ctx
        r = client.post(
            "/api/v1/approvals/apr_nope/action",
            json={"action": "approve", "policy_applied_hash": POLICY_HASH},
            headers=_auth(),
        )
        assert r.status_code == 404


# ===========================================================================
class TestTheHashGuard:
    """The difference between an approval gate and a button labelled
    "approve"."""

    def test_a_changed_action_is_refused_with_409(self, ctx: Any) -> None:
        """A human approved 0% on WhatsApp. If the action became 10% between
        display and execution, their approval no longer refers to what would
        happen."""
        client, _, _ = ctx
        r = _action(client, policy_applied_hash="b" * 64)
        assert r.status_code == 409
        assert "changed since it was displayed" in r.json()["detail"]

    def test_the_hash_cannot_be_omitted(self, ctx: Any) -> None:
        """An optional guard with a skip-if-absent fallback is bypassable by
        omitting a field."""
        client, _, _ = ctx
        r = client.post(
            f"/api/v1/approvals/{APPROVAL}/action",
            json={"action": "approve"},
            headers=_auth(),
        )
        assert r.status_code == 422

    def test_a_truncated_hash_is_refused(self, ctx: Any) -> None:
        client, _, _ = ctx
        assert _action(client, policy_applied_hash=POLICY_HASH[:32]).status_code == 422

    def test_a_refused_hash_leaves_the_approval_pending(self, ctx: Any) -> None:
        """A failed guard must not consume the approval — the reviewer needs
        to be able to re-read and approve the real action."""
        client, _, _ = ctx
        _action(client, policy_applied_hash="b" * 64)
        assert client.get("/api/v1/approvals", headers=_auth()).json()["count"] == 1


# ===========================================================================
class TestReplayAndExpiry:
    def test_a_second_action_is_refused(self, ctx: Any) -> None:
        """A double-clicked Approve must not write two audit blocks claiming
        two separate decisions."""
        client, _, _ = ctx
        assert _action(client).status_code == 200
        second = _action(client)
        assert second.status_code == 409
        assert "already APPROVED" in second.json()["detail"]

    def test_an_expired_approval_cannot_be_actioned(self, ctx: Any) -> None:
        client, clock, _ = ctx
        clock.advance(hours=4, minutes=1)
        r = _action(client)
        assert r.status_code == 409
        assert "expired" in r.json()["detail"]

    def test_expiry_is_checked_before_the_hash(self, ctx: Any) -> None:
        """Ordering matters: a correct hash does not make four-hour-old
        information fresh, so the stale approval must lose either way."""
        client, clock, _ = ctx
        clock.advance(hours=5)
        r = _action(client, policy_applied_hash=POLICY_HASH)
        assert r.status_code == 409
        assert "expired" in r.json()["detail"].lower()

    def test_actioning_an_expired_one_marks_it_expired(self, ctx: Any) -> None:
        client, clock, _ = ctx
        clock.advance(hours=5)
        _action(client)
        assert client.get("/api/v1/approvals", headers=_auth()).json()["count"] == 0


# ===========================================================================
class TestAuditTrail:
    @pytest.mark.asyncio
    async def test_an_approval_appends_a_verifiable_block(self, ctx: Any) -> None:
        """Rung A3 requires the principal be recorded WITH the exact
        policy_applied they approved, so a dispute can compare what was
        authorised against what ran."""
        client, clock, factory = ctx
        _action(client)

        async with factory() as s:
            chain = AuditChain(clock)
            result = await chain.verify(s)
            assert result.valid

            from app.db.models import AuditBlock

            block = (
                (await s.execute(select(AuditBlock).order_by(AuditBlock.block_index.desc())))
                .scalars()
                .first()
            )
        assert block is not None
        assert block.event_name == "approval.approved"
        assert block.actor.startswith("api:")
        payload = json.loads(block.payload_canonical)
        assert payload["policy_applied"] == POLICY_JSON
        assert payload["policy_applied_hash"] == POLICY_HASH

    @pytest.mark.asyncio
    async def test_a_rejection_is_recorded_too(self, ctx: Any) -> None:
        client, _clock, factory = ctx
        _action(client, action="reject", notes="spoke to them")
        async with factory() as s:
            from app.db.models import AuditBlock

            block = (
                (await s.execute(select(AuditBlock).order_by(AuditBlock.block_index.desc())))
                .scalars()
                .first()
            )
        assert block is not None and block.event_name == "approval.rejected"
        assert json.loads(block.payload_canonical)["notes"] == "spoke to them"


# ===========================================================================
class TestScheduler:
    """The jobs that handle nothing happening."""

    @pytest.mark.asyncio
    async def test_the_sweeper_expires_an_unactioned_approval(self, engine: AsyncEngine) -> None:
        clock = FakeClock(NOW)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            await _seed(s, clock=clock)

        clock.advance(hours=4, minutes=1)
        async with factory() as s:
            result = await Scheduler(clock, AuditChain(clock)).sweep(s)
        assert result.expired_approvals == 1

        async with factory() as s:
            approval = await s.get(ApprovalRequest, APPROVAL)
            case = await s.get(RecoveryCase, CASE)
        assert approval is not None and approval.status is ApprovalStatus.EXPIRED
        assert case is not None and case.status is CaseStatus.EXPIRED

    @pytest.mark.asyncio
    async def test_expiry_does_not_claim_a_human_reviewed_it(self, engine: AsyncEngine) -> None:
        """reviewed_by stays NULL. Writing "system" into a column meaning
        "the human who decided" would make the audit trail claim a review
        that never happened."""
        clock = FakeClock(NOW)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            await _seed(s, clock=clock)
        clock.advance(hours=5)
        async with factory() as s:
            await Scheduler(clock, AuditChain(clock)).sweep(s)
        async with factory() as s:
            approval = await s.get(ApprovalRequest, APPROVAL)
        assert approval is not None and approval.reviewed_by is None

    @pytest.mark.asyncio
    async def test_the_sweep_is_idempotent(self, engine: AsyncEngine) -> None:
        clock = FakeClock(NOW)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            await _seed(s, clock=clock)
        clock.advance(hours=5)
        scheduler = Scheduler(clock, AuditChain(clock))
        async with factory() as s:
            first = await scheduler.sweep(s)
        async with factory() as s:
            second = await scheduler.sweep(s)
        assert first.expired_approvals == 1
        assert second.expired_approvals == 0

    @pytest.mark.asyncio
    async def test_a_still_valid_approval_is_untouched(self, engine: AsyncEngine) -> None:
        clock = FakeClock(NOW)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            await _seed(s, clock=clock)
        clock.advance(hours=3, minutes=59)
        async with factory() as s:
            result = await Scheduler(clock, AuditChain(clock)).sweep(s)
        assert result.expired_approvals == 0

    @pytest.mark.asyncio
    async def test_the_sweep_leaves_an_audit_trail_that_verifies(self, engine: AsyncEngine) -> None:
        clock = FakeClock(NOW)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            await _seed(s, clock=clock)
        clock.advance(hours=5)
        chain = AuditChain(clock)
        async with factory() as s:
            await Scheduler(clock, chain).sweep(s)
        async with factory() as s:
            assert (await chain.verify(s)).valid


# ===========================================================================
class TestStaleDeferrals:
    """A message held for quiet hours whose case dies while it waits.

    The drainer's query is `status = PENDING AND next_attempt_at <= now` and
    nothing checked whether the case was still alive. A message deferred at
    22:00 for a case whose window closed at 03:00 would be sent at 09:05 — a
    fresh payment link, six hours after the case was over.
    """

    async def _seed_deferred(
        self,
        factory: Any,
        clock: FakeClock,
        *,
        release_in_hours: float,
        window_in_hours: float,
        case_status: CaseStatus = CaseStatus.EXECUTING,
    ) -> None:
        now = clock.now_utc()
        async with factory() as s:
            await _seed(s, clock=clock, case_status=case_status)
            case = await s.get(RecoveryCase, CASE)
            assert case is not None
            case.window_expires_at = now + timedelta(hours=window_in_hours)
            s.add(
                Outbox(
                    id="obx_1",
                    case_id=CASE,
                    action_type=ActionType.CREATE_PAYMENT_LINK,
                    reference_id="rvp_rc-9001_1",
                    payload_json="{}",
                    status=OutboxStatus.PENDING,
                    attempt=0,
                    next_attempt_at=now + timedelta(hours=release_in_hours),
                    created_at=now,
                )
            )
            await s.commit()

    @pytest.mark.asyncio
    async def test_a_deferral_past_the_window_is_cancelled(self, engine: AsyncEngine) -> None:
        """Held at 22:00 for release at 09:05; window closes at 03:00."""
        clock = FakeClock(NOW)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await self._seed_deferred(factory, clock, release_in_hours=11, window_in_hours=5)

        async with factory() as s:
            result = await Scheduler(clock, AuditChain(clock)).sweep(s)
        assert result.stale_deferrals == 1

        async with factory() as s:
            entry = await s.get(Outbox, "obx_1")
        assert entry is not None and entry.status is OutboxStatus.DEAD
        assert "window" in (entry.last_error or "")

    @pytest.mark.asyncio
    async def test_a_deferral_inside_the_window_survives(self, engine: AsyncEngine) -> None:
        """The control. A sweep that killed everything would also pass the
        test above."""
        clock = FakeClock(NOW)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await self._seed_deferred(factory, clock, release_in_hours=11, window_in_hours=24)

        async with factory() as s:
            result = await Scheduler(clock, AuditChain(clock)).sweep(s)
        assert result.stale_deferrals == 0

        async with factory() as s:
            entry = await s.get(Outbox, "obx_1")
        assert entry is not None and entry.status is OutboxStatus.PENDING

    @pytest.mark.asyncio
    async def test_a_deferral_for_a_terminal_case_is_cancelled(self, engine: AsyncEngine) -> None:
        """The customer paid organically at 23:00. The held message must not
        go out at 09:05 asking them to pay again."""
        clock = FakeClock(NOW)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await self._seed_deferred(
            factory,
            clock,
            release_in_hours=11,
            window_in_hours=24,
            case_status=CaseStatus.RESOLVED_ORGANIC,
        )
        async with factory() as s:
            result = await Scheduler(clock, AuditChain(clock)).sweep(s)
        assert result.stale_deferrals == 1
        async with factory() as s:
            entry = await s.get(Outbox, "obx_1")
        assert entry is not None and entry.status is OutboxStatus.DEAD
        assert "RESOLVED_ORGANIC" in (entry.last_error or "")

    @pytest.mark.asyncio
    async def test_cancellation_is_audited(self, engine: AsyncEngine) -> None:
        clock = FakeClock(NOW)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await self._seed_deferred(factory, clock, release_in_hours=11, window_in_hours=5)
        chain = AuditChain(clock)
        async with factory() as s:
            await Scheduler(clock, chain).sweep(s)
        async with factory() as s:
            from app.db.models import AuditBlock

            names = [b.event_name for b in (await s.execute(select(AuditBlock))).scalars().all()]
            assert "outbox.deferral_cancelled" in names
            assert (await chain.verify(s)).valid
