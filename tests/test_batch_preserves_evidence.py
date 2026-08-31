"""The batch must not delete a payment Razorpay confirmed.

INC-032. ``_clear`` was an unfiltered ``delete(RecoveryCase)``, so
``python tasks.py batch`` -- and therefore ``demo`` -- destroyed every
RAZORPAY_VERIFIED recovery in the database.

This is not a hypothetical. It is how the first live Test Mode verification of
this project was lost, and it would have happened to anyone who ran the demo
after making a real payment: the one figure in the system meant to be beyond
argument, deleted by a routine command, silently.

The invariant, stated once: **the batch owns simulated data and may clear it; a
payment Razorpay confirmed is not the batch's to delete.**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.agent.nodes import AgentDeps
from app.core.clock import FakeClock
from app.db.enums import CaseStatus, Playbook, RecoveryVerifier
from app.db.ids import idempotency_hash
from app.db.models import AuditBlock, RecoveryCase
from app.workers.batch import run_batch

MOMENT = datetime(2026, 9, 1, 11, 0, tzinfo=UTC)

#: One of each real mechanism, plus a simulated row that MUST be cleared.
FIXTURES = [
    ("RC-REAL-WEBHOOK", RecoveryVerifier.WEBHOOK, "TWSSP5BW90Y89E", 100),
    ("RC-REAL-POLL", RecoveryVerifier.API_RECONCILIATION, "plink_TWPwcbsfrYnIQQ", 100),
    ("RC-FAKE-SIM", RecoveryVerifier.SIMULATOR, "sim_evt_abc123", 500_000),
]


async def _plant(engine: AsyncEngine) -> None:
    """Insert the three cases before the batch runs."""
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        merchant_id = (await session.scalar(select(func.min(RecoveryCase.merchant_id)))) or None
        if merchant_id is None:
            from app.db.models import Merchant

            merchant_id = (await session.scalar(select(func.min(Merchant.id)))) or ""
        customer_id = await session.scalar(select(func.min(RecoveryCase.customer_id)))
        if customer_id is None:
            from app.db.models import Customer

            customer_id = await session.scalar(select(func.min(Customer.id)))

        for case_id, verifier, verified_by, paise in FIXTURES:
            session.add(
                RecoveryCase(
                    id=case_id,
                    merchant_id=merchant_id,
                    customer_id=customer_id,
                    playbook=Playbook.PAYMENT_FAILURE,
                    status=CaseStatus.RECOVERED,
                    amount_paise=paise,
                    attempt_no=1,
                    recovered_amount_paise=paise,
                    recovery_verified_by=verified_by,
                    recovery_verified_via=verifier,
                    idempotency_hash=idempotency_hash(str(merchant_id), case_id, "PAYMENT_FAILURE"),
                    is_demo=False,
                    window_expires_at=MOMENT + timedelta(hours=24),
                    created_at=MOMENT,
                )
            )
        await session.commit()


async def _run(engine: AsyncEngine) -> object:
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    return await run_batch(
        factory,
        clock=FakeClock(MOMENT),
        deps=AgentDeps(clock=FakeClock(MOMENT), adapter=None),
        limit=6,
    )


async def _ids(engine: AsyncEngine) -> set[str]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        return set((await session.execute(select(RecoveryCase.id))).scalars().all())


# ===========================================================================
class TestVerifiedCasesSurvive:
    @pytest.mark.parametrize("case_id", ["RC-REAL-WEBHOOK", "RC-REAL-POLL"])
    async def test_a_razorpay_verified_case_is_not_deleted(
        self, seeded_engine: AsyncEngine, case_id: str
    ) -> None:
        await _plant(seeded_engine)
        await _run(seeded_engine)
        assert case_id in await _ids(seeded_engine), (
            f"{case_id} was deleted by a batch run. This is INC-032: a payment "
            "Razorpay confirmed is not the batch's to delete."
        )

    async def test_the_amount_survives_too(self, seeded_engine: AsyncEngine) -> None:
        """Not just the row. A preserved case with a zeroed amount would keep
        the id and lose the evidence, which is worse than deleting it -- the
        totals would silently drop while the case still looked present."""
        await _plant(seeded_engine)
        await _run(seeded_engine)

        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as session:
            case = await session.get(RecoveryCase, "RC-REAL-WEBHOOK")
            assert case is not None
            assert case.recovered_amount_paise == 100
            assert case.recovery_verified_by == "TWSSP5BW90Y89E"
            assert case.recovery_verified_via is RecoveryVerifier.WEBHOOK

    async def test_a_simulated_case_IS_cleared(self, seeded_engine: AsyncEngine) -> None:
        """The other half of the invariant.

        Without this the fix could be "never delete anything", which would make
        the batch non-re-runnable and double every figure on a second run. The
        rule is *verified* cases survive, not *all* cases.
        """
        await _plant(seeded_engine)
        await _run(seeded_engine)
        assert "RC-FAKE-SIM" not in await _ids(seeded_engine)

    async def test_repeated_runs_do_not_accumulate(self, seeded_engine: AsyncEngine) -> None:
        """A judge who runs it three times sees the same numbers."""
        await _plant(seeded_engine)
        await _run(seeded_engine)
        first = len(await _ids(seeded_engine))
        await _run(seeded_engine)
        await _run(seeded_engine)
        assert len(await _ids(seeded_engine)) == first


# ===========================================================================
class TestTheCarryOverIsRecorded:
    """A preserved case must not appear in the totals with no ledger entry."""

    async def test_each_preserved_case_gets_a_block(self, seeded_engine: AsyncEngine) -> None:
        await _plant(seeded_engine)
        await _run(seeded_engine)

        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        select(AuditBlock.case_id).where(
                            AuditBlock.event_name == "case.carried_over"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert set(rows) == {"RC-REAL-WEBHOOK", "RC-REAL-POLL"}, (
            "a RAZORPAY_VERIFIED case present in the totals with no entry in the "
            "rebuilt chain is exactly the shape an auditor should distrust"
        )

    async def test_the_chain_still_verifies(self, seeded_engine: AsyncEngine) -> None:
        """The blocks are a hash chain. Adding carry-over entries must not break
        it, or the fix trades a data-loss bug for a verification failure."""
        from app.tools.audit import AuditChain

        await _plant(seeded_engine)
        await _run(seeded_engine)

        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as session:
            result = await AuditChain(FakeClock(MOMENT)).verify(session)
        assert result.valid, f"chain broken at block {result.first_divergence_index}"

    async def test_the_result_reports_what_it_preserved(self, seeded_engine: AsyncEngine) -> None:
        """Reported, not merely logged: the operator has to be able to see that
        their real evidence survived a routine command."""
        await _plant(seeded_engine)
        result = await _run(seeded_engine)
        carried = getattr(result, "carried_over", ())
        assert set(carried) == {"RC-REAL-WEBHOOK", "RC-REAL-POLL"}
        rendered = result.render()  # type: ignore[attr-defined]
        assert "PRESERVED" in rendered
        assert "RC-REAL-WEBHOOK" in rendered
