"""The batch runner, and the control arm it must not corrupt.

The measurement this project reports rests on one number being right: the
conversion rate of the untouched holdout. INC-018 was a case where it was
wrong by a factor of four — 89.7% instead of 23.1% — because
``RESOLVED_ORGANIC`` meant "held as control" in one module and "settled without
us" in another.

Treatment was correct throughout. Only control was wrong, and it was wrong in
the direction that makes the product look worse, which is the only reason it
was noticed rather than shipped. So the tests here are about the control arm
specifically, and they assert *rates*, not signs — a test asserting only "lift
is positive" would pass on the broken code the moment the sign flipped back.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.agent.nodes import AgentDeps
from app.core.clock import FakeClock
from app.db.enums import CaseStatus, ExperimentArm
from app.db.models import ExperimentAssignment, RecoveryCase
from app.llm.cache import CachedAdapter, ResponseCache
from app.services.metrics import SIMULATED_EVENT_PREFIX
from app.tools.audit import AuditChain
from app.workers.batch import BASELINE_SELF_RECOVERY, run_batch

SETTLED = (CaseStatus.RECOVERED, CaseStatus.RESOLVED_ORGANIC)


@pytest.fixture
def deps() -> AgentDeps:
    return AgentDeps(
        clock=FakeClock(datetime(2026, 9, 1, 6, 0, tzinfo=UTC)),
        adapter=CachedAdapter(cache=ResponseCache.load(), live=None, model="test"),
        control_arm_fraction=0.18,
        experiment_key="revpilot_recovery_v1",
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 9, 1, 6, 0, tzinfo=UTC))


async def _run(engine: AsyncEngine, clock: FakeClock, deps: AgentDeps, limit: int = 210):
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    result = await run_batch(factory, clock=clock, deps=deps, limit=limit)
    return factory, result


async def _rates(factory) -> dict[str, tuple[int, int]]:  # type: ignore[no-untyped-def]
    """(settled, total) per arm, read back from the database."""
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    ExperimentAssignment.arm,
                    func.count(RecoveryCase.id),
                    func.sum(case((RecoveryCase.status.in_(SETTLED), 1), else_=0)),
                )
                .join(RecoveryCase, RecoveryCase.id == ExperimentAssignment.case_id)
                .group_by(ExperimentAssignment.arm)
            )
        ).all()
    return {arm.value: (int(settled or 0), int(total)) for arm, total, settled in rows}


class TestTheControlArm:
    @pytest.mark.asyncio
    async def test_control_conversion_matches_the_declared_baseline(
        self, seeded_engine: AsyncEngine, clock: FakeClock, deps: AgentDeps
    ) -> None:
        """The INC-018 regression.

        Control cases are never contacted, so their settlement rate must sit
        near the declared 21% baseline. The bug put it at 89.7%, because every
        case merely *held* as control was being counted as one that *paid*.
        """
        factory, _ = await _run(seeded_engine, clock, deps)
        rates = await _rates(factory)
        settled, total = rates[ExperimentArm.CONTROL.value]
        assert total > 20, "too few control cases for this assertion to mean anything"
        rate = settled / total
        assert BASELINE_SELF_RECOVERY - 0.15 < rate < BASELINE_SELF_RECOVERY + 0.15, (
            f"control settled at {rate:.1%}; the declared baseline is "
            f"{BASELINE_SELF_RECOVERY:.0%}. A rate near 90% means case status is "
            "being read as payment (INC-018)."
        )

    @pytest.mark.asyncio
    async def test_a_control_case_that_did_not_pay_is_not_resolved_organic(
        self, seeded_engine: AsyncEngine, clock: FakeClock, deps: AgentDeps
    ) -> None:
        """RESOLVED_ORGANIC must mean "settled without our involvement" and
        nothing else.

        A control case that did not pay is OBSERVED_NO_ACTION — we deliberately
        did nothing and nothing happened. That is a different fact from "money
        arrived without us", and conflating them is what inverted the lift.
        """
        factory, _ = await _run(seeded_engine, clock, deps)
        async with factory() as session:
            observed = await session.scalar(
                select(func.count(RecoveryCase.id))
                .join(ExperimentAssignment, ExperimentAssignment.case_id == RecoveryCase.id)
                .where(
                    ExperimentAssignment.arm == ExperimentArm.CONTROL,
                    RecoveryCase.status == CaseStatus.OBSERVED_NO_ACTION,
                )
            )
        assert (observed or 0) > 0, (
            "no control case is OBSERVED_NO_ACTION — every one is being recorded "
            "as settled (INC-018)"
        )

    @pytest.mark.asyncio
    async def test_observed_and_organic_are_never_the_same_value(
        self, seeded_engine: AsyncEngine, clock: FakeClock, deps: AgentDeps
    ) -> None:
        """The INC-018 fix at source: the two states must both occur and must
        not be interchangeable. If a refactor collapsed them, this fails."""
        assert CaseStatus.OBSERVED_NO_ACTION is not CaseStatus.RESOLVED_ORGANIC
        factory, _ = await _run(seeded_engine, clock, deps)
        async with factory() as session:
            rows = dict(
                (
                    await session.execute(
                        select(RecoveryCase.status, func.count(RecoveryCase.id)).group_by(
                            RecoveryCase.status
                        )
                    )
                ).all()
            )
        assert rows.get(CaseStatus.OBSERVED_NO_ACTION, 0) > 0
        assert rows.get(CaseStatus.RESOLVED_ORGANIC, 0) > 0

    @pytest.mark.asyncio
    async def test_no_control_case_is_ever_recorded_as_recovered(
        self, seeded_engine: AsyncEngine, clock: FakeClock, deps: AgentDeps
    ) -> None:
        """We never acted, so there is nothing of ours to credit. A control
        case marked RECOVERED would be the measurement destroying itself."""
        factory, _ = await _run(seeded_engine, clock, deps)
        async with factory() as session:
            recovered = await session.scalar(
                select(func.count(RecoveryCase.id))
                .join(ExperimentAssignment, ExperimentAssignment.case_id == RecoveryCase.id)
                .where(
                    ExperimentAssignment.arm == ExperimentArm.CONTROL,
                    RecoveryCase.status == CaseStatus.RECOVERED,
                )
            )
        assert (recovered or 0) == 0

    @pytest.mark.asyncio
    async def test_no_control_case_carries_a_recovered_amount(
        self, seeded_engine: AsyncEngine, clock: FakeClock, deps: AgentDeps
    ) -> None:
        factory, _ = await _run(seeded_engine, clock, deps)
        async with factory() as session:
            total = await session.scalar(
                select(func.coalesce(func.sum(RecoveryCase.recovered_amount_paise), 0))
                .join(ExperimentAssignment, ExperimentAssignment.case_id == RecoveryCase.id)
                .where(ExperimentAssignment.arm == ExperimentArm.CONTROL)
            )
        assert (total or 0) == 0, "control money is not ours and must never be counted"


class TestSimulatedProvenance:
    @pytest.mark.asyncio
    async def test_every_recovery_is_marked_simulated(
        self, seeded_engine: AsyncEngine, clock: FakeClock, deps: AgentDeps
    ) -> None:
        """DEC-031. The schema forces an event id onto every recovery, so the
        runner must write one — and a realistic-looking id would silently
        promote seeded outcomes into the RAZORPAY_VERIFIED column."""
        factory, _ = await _run(seeded_engine, clock, deps)
        async with factory() as session:
            verifiers = (
                (
                    await session.execute(
                        select(RecoveryCase.recovery_verified_by).where(
                            RecoveryCase.recovery_verified_by.is_not(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert verifiers, "the batch settled nothing; this test would be vacuous"
        assert all(v.startswith(SIMULATED_EVENT_PREFIX) for v in verifiers)

    @pytest.mark.asyncio
    async def test_the_recovery_proof_constraint_still_holds(
        self, seeded_engine: AsyncEngine, clock: FakeClock, deps: AgentDeps
    ) -> None:
        """A recovered amount with no verifier cannot exist. The batch must not
        be the thing that finds a way around it."""
        factory, _ = await _run(seeded_engine, clock, deps)
        async with factory() as session:
            unproven = await session.scalar(
                select(func.count(RecoveryCase.id)).where(
                    RecoveryCase.recovered_amount_paise > 0,
                    RecoveryCase.recovery_verified_by.is_(None),
                )
            )
        assert (unproven or 0) == 0


class TestReproducibility:
    @pytest.mark.asyncio
    async def test_running_twice_does_not_double_the_numbers(
        self, seeded_engine: AsyncEngine, clock: FakeClock, deps: AgentDeps
    ) -> None:
        """A judge who runs the batch twice must see the same figures, not
        doubled ones."""
        _factory, first = await _run(seeded_engine, clock, deps, limit=40)
        _, second = await _run(seeded_engine, clock, deps, limit=40)
        assert first.cases_created == second.cases_created
        assert first.simulated_recovered_paise == second.simulated_recovered_paise
        assert first.control == second.control

    @pytest.mark.asyncio
    async def test_the_audit_chain_survives_a_batch(
        self, seeded_engine: AsyncEngine, clock: FakeClock, deps: AgentDeps
    ) -> None:
        factory, _ = await _run(seeded_engine, clock, deps, limit=40)
        async with factory() as session:
            assert (await AuditChain(clock).verify(session)).valid
