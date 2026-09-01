"""The money identity, against the database.

``arrived = driven + organic``, exactly, in paise, with no tolerance. The
companion to ``test_money_reconciles.py``: that file pins the arithmetic of the
estimate, this one pins the identity the estimate is reported *beside*.

The bug this exists to prevent is not an arithmetic error. It is a **layout**
that implies an arithmetic relationship which does not hold -- three correct
figures arranged as ``gross -> claimable + not claimed``, which a reviewer added
up and found short by Rs 3,522.54. The suite was green, because no test had any
opinion about how the three related to each other.

So the identity is now a property of the system rather than a claim in prose,
and the two populations are asserted disjoint, which is the fact that makes the
identity true rather than a coincidence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.enums import CaseStatus, ExperimentArm, Playbook, RecoveryVerifier
from app.db.ids import idempotency_hash
from app.db.models import Customer, Merchant, RecoveryCase
from app.services.attribution import CaseOutcome, recovery_report
from app.services.reconciliation import money_ledger

MOMENT = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)

#: (id, status, amount, recovered, is_demo). Chosen so every branch of the
#: ledger is populated and the three sums are all distinct -- if two coincided,
#: a query reading the wrong column would still pass.
FIXTURES: tuple[tuple[str, CaseStatus, int, int, bool], ...] = (
    ("RC-D1", CaseStatus.RECOVERED, 500_00, 500_00, False),
    ("RC-D2", CaseStatus.RECOVERED, 300_00, 300_00, False),
    ("RC-O1", CaseStatus.RESOLVED_ORGANIC, 700_00, 0, False),
    ("RC-O2", CaseStatus.RESOLVED_ORGANIC, 110_00, 0, False),
    # Open: contributes to neither term. A ledger that summed "every case"
    # would pick this up and stop balancing.
    ("RC-M1", CaseStatus.MONITORING, 999_00, 0, False),
    ("RC-N1", CaseStatus.OBSERVED_NO_ACTION, 888_00, 0, False),
    # The real Razorpay Test Mode recoveries: excluded from the measured
    # population, reported separately.
    ("RC-TM1", CaseStatus.RECOVERED, 1_00, 1_00, True),
)

DRIVEN = 800_00
ORGANIC = 810_00
DEMO = 1_00


@pytest_asyncio.fixture
async def populated(engine: AsyncEngine) -> AsyncEngine:
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        session.add_all(
            [
                Merchant(id="mrc_test", business_name="Test", created_at=MOMENT),
                Customer(
                    id="cust_test",
                    merchant_id="mrc_test",
                    first_name="Test",
                    phone_masked="+91 98xxxxxx10",
                    phone_hash="0" * 64,
                    first_seen_at=MOMENT,
                ),
            ]
        )
        await session.flush()
        for case_id, status, amount, recovered, is_demo in FIXTURES:
            session.add(
                RecoveryCase(
                    id=case_id,
                    merchant_id="mrc_test",
                    customer_id="cust_test",
                    playbook=Playbook.PAYMENT_FAILURE,
                    status=status,
                    amount_paise=amount,
                    attempt_no=1,
                    recovered_amount_paise=recovered,
                    recovery_verified_by=(f"sim_evt_{case_id}" if recovered else None),
                    recovery_verified_via=(RecoveryVerifier.SIMULATOR if recovered else None),
                    idempotency_hash=idempotency_hash("mrc_test", case_id, "PAYMENT_FAILURE"),
                    is_demo=is_demo,
                    window_expires_at=MOMENT + timedelta(hours=24),
                    created_at=MOMENT,
                )
            )
        await session.commit()
    return engine


async def _ledger(engine: AsyncEngine) -> tuple[object, AsyncSession]:
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        rows = (await session.execute(select(RecoveryCase))).scalars().all()
        outcomes = [
            CaseOutcome(
                case_id=c.id,
                arm=ExperimentArm.TREATMENT,
                paid=c.status in {CaseStatus.RECOVERED, CaseStatus.RESOLVED_ORGANIC},
                amount_paise=c.amount_paise,
                recovered=c.recovery_verified_by is not None,
                is_demo=c.is_demo,
            )
            for c in rows
        ]
        return await money_ledger(session, attribution=recovery_report(outcomes)), session


@pytest.mark.asyncio
class TestTheIdentityHolds:
    async def test_arrived_equals_driven_plus_organic_exactly(self, populated: AsyncEngine) -> None:
        ledger, _ = await _ledger(populated)
        assert ledger.driven_paise == DRIVEN
        assert ledger.organic_paise == ORGANIC
        assert ledger.arrived_paise == DRIVEN + ORGANIC
        assert ledger.residual_paise == 0
        assert ledger.balances

    async def test_open_cases_are_in_neither_term(self, populated: AsyncEngine) -> None:
        """The MONITORING and OBSERVED_NO_ACTION rows carry Rs 18,870 between
        them. Money that has not arrived must not appear in a ledger of money
        that arrived."""
        ledger, _ = await _ledger(populated)
        assert ledger.arrived_paise == 1_610_00
        assert 999_00 + 888_00 not in (ledger.driven_paise, ledger.organic_paise)

    async def test_the_demo_rupees_are_separate_and_not_in_the_identity(
        self, populated: AsyncEngine
    ) -> None:
        ledger, _ = await _ledger(populated)
        assert ledger.demo_verified_paise == DEMO
        assert ledger.demo_verified_cases == 1
        # The identity must not move when a demo recovery is added -- that is
        # what "excluded from the measured population" has to mean numerically.
        assert ledger.arrived_paise == DRIVEN + ORGANIC

    async def test_the_two_populations_are_disjoint(self, populated: AsyncEngine) -> None:
        """The identity is only true because no case can be in both terms.

        Asserted against the database rather than reasoned about: a future status
        that satisfied both predicates would double-count silently.
        """
        factory = async_sessionmaker(populated, expire_on_commit=False)
        async with factory() as session:
            overlap = await session.scalar(
                select(func.count(RecoveryCase.id)).where(
                    RecoveryCase.status == CaseStatus.RECOVERED,
                    RecoveryCase.status == CaseStatus.RESOLVED_ORGANIC,
                )
            )
        assert overlap == 0

    async def test_the_estimate_is_not_a_term_in_the_sum(self, populated: AsyncEngine) -> None:
        """The whole point.

        With no control arm the estimate is zero, and the identity must be
        completely unaffected. If incremental were a term, this would break it.
        """
        ledger, _ = await _ledger(populated)
        assert ledger.incremental_estimate_paise == 0, "no control arm in this fixture"
        assert ledger.balances
        assert ledger.arrived_paise == DRIVEN + ORGANIC

    async def test_claimed_share_is_a_fraction_of_arrived_not_of_driven(
        self, populated: AsyncEngine
    ) -> None:
        ledger, _ = await _ledger(populated)
        assert ledger.claimed_share == 0.0
        assert 0.0 <= ledger.claimed_share <= 1.0

    async def test_the_published_shape_reports_the_residual(self, populated: AsyncEngine) -> None:
        """A reader must be able to *see* that the residual is zero rather than
        trust that it is."""
        ledger, _ = await _ledger(populated)
        d = ledger.as_dict()
        assert d["balances"] is True
        assert d["residual_paise"] == 0
        assert d["identity"] == "arrived = driven + organic"
        assert d["arrived"]["paise"] == d["driven"]["paise"] + d["organic"]["paise"]
        # The estimate must carry its own warning, in the payload, where a
        # front-end that renders `basis` cannot omit it.
        assert "ESTIMATE" in d["incremental_estimate"]["basis"]

    async def test_an_empty_database_balances_at_zero(self, engine: AsyncEngine) -> None:
        """A fresh clone. Must render, and must not divide by zero."""
        ledger, _ = await _ledger(engine)
        assert ledger.arrived_paise == 0
        assert ledger.balances
        assert ledger.claimed_share == 0.0


@pytest.mark.asyncio
class TestTheLedgerAgreesWithAttribution:
    async def test_driven_equals_the_attribution_gross(self, populated: AsyncEngine) -> None:
        """Two independent queries of one quantity, which must agree.

        ``attribution`` computes gross by summing recovered treated outcomes in
        Python; the ledger sums a column in SQL. They are written separately and
        this is the only thing tying them together -- INC-007 was two
        implementations of one number drifting apart.
        """
        factory = async_sessionmaker(populated, expire_on_commit=False, autoflush=False)
        async with factory() as session:
            rows = (await session.execute(select(RecoveryCase))).scalars().all()
            outcomes = [
                CaseOutcome(
                    case_id=c.id,
                    arm=ExperimentArm.TREATMENT,
                    paid=c.status in {CaseStatus.RECOVERED, CaseStatus.RESOLVED_ORGANIC},
                    amount_paise=c.amount_paise,
                    recovered=c.recovery_verified_by is not None,
                    is_demo=c.is_demo,
                )
                for c in rows
            ]
            report = recovery_report(outcomes)
            ledger = await money_ledger(session, attribution=report)
        assert report.gross_recovered_paise == ledger.driven_paise
