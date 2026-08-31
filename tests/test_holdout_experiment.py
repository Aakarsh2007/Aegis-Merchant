"""The randomised holdout, and the guard the whole lift figure rests on.

No network in this file. The Razorpay call is stubbed; what is tested is
everything around it — the randomisation, the two reference namespaces, the
organic resolution of a control settlement, and above all the **absence** of
any outreach artefact on a control case.

The last one is the assertion that matters. Every rupee of the incremental
figure depends on the control arm being genuinely untouched, and "we didn't
contact them" is the kind of claim that stays true right up until someone adds
a well-meaning notification. So it is asserted as an absence, over every table
that could record a contact, rather than trusted to the code's intent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.clock import FakeClock
from app.db.enums import CaseStatus, ExperimentArm
from app.db.ids import observed_reference_id, reference_id
from app.db.models import (
    ContactLedger,
    ExperimentAssignment,
    Outbox,
    RecoveryAction,
    RecoveryCase,
)
from app.ingest.settle import process_settlement
from app.services.attribution import attribute
from app.services.experiments import assign_arm
from app.workers.experiment import (
    CONTROL_FRACTION,
    EXPERIMENT_KEY,
    holdout_report,
    run_testmode_experiment,
)

MOMENT = datetime(2026, 9, 1, 11, 0, tzinfo=UTC)


class _StubProvider:
    """Stands in for Razorpay. Records what was asked of it.

    Recording the requests is the point: several assertions below are about what
    we *sent* to the provider — that `notify` is off, that the reference is in
    the right namespace — and those are invisible from the database alone.
    """

    # ClassVar deliberately: the stub is instantiated inside the code under
    # test, so the recorded calls have to live on the class for the test to
    # reach them. The fixture resets it before every test.
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        body = dict(kwargs.get("json") or {})
        _StubProvider.calls.append({"method": method, "path": path, **body})
        index = len(_StubProvider.calls)
        return {
            "id": f"plink_stub{index:04d}",
            "short_url": f"https://rzp.io/i/stub{index:04d}",
            "order_id": f"order_stub{index:04d}",
            "reference_id": body.get("reference_id"),
        }


@pytest.fixture
def stub_provider(monkeypatch: pytest.MonkeyPatch) -> type[_StubProvider]:
    _StubProvider.calls = []
    monkeypatch.setattr("app.workers.experiment.RazorpayProvider", _StubProvider)
    return _StubProvider


class _Settings:
    razorpay_key_id = "rzp_test_stub"
    razorpay_key_secret = "stubsecret"
    gemini_model = "gemini-3.1-flash-lite"


async def _run(engine: AsyncEngine, n: int = 8) -> Any:
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    return await run_testmode_experiment(
        factory,
        clock=FakeClock(MOMENT),
        settings=_Settings(),  # type: ignore[arg-type]
        n=n,
    )


# ===========================================================================
class TestRandomisation:
    """Assignment must be recomputable by someone who does not trust us."""

    def test_the_arm_is_a_function_of_the_case_id(self) -> None:
        """Same id, same key, same arm. Every time.

        Post-hoc arm assignment is the easiest way to fabricate a lift and the
        hardest to detect from summary statistics, so the defence is that
        anyone can recompute it.
        """
        first = assign_arm(
            "RC-XP0001", experiment_key=EXPERIMENT_KEY, control_fraction=CONTROL_FRACTION
        )
        second = assign_arm(
            "RC-XP0001", experiment_key=EXPERIMENT_KEY, control_fraction=CONTROL_FRACTION
        )
        assert first.arm is second.arm
        assert first.assignment_hash == second.assignment_hash

    def test_the_experiment_key_isolates_the_population(self) -> None:
        """A different key must be able to give a different arm.

        Two experiments sharing a key would pool two populations into one. The
        assertion is on the *hash* rather than the arm, because two arms
        agreeing by chance is likely at any single id.
        """
        ours = assign_arm(
            "RC-XP0001", experiment_key=EXPERIMENT_KEY, control_fraction=CONTROL_FRACTION
        )
        theirs = assign_arm(
            "RC-XP0001", experiment_key="revpilot_recovery_v1", control_fraction=CONTROL_FRACTION
        )
        assert ours.assignment_hash != theirs.assignment_hash

    def test_balanced_allocation_over_many_ids(self) -> None:
        """Not a test of the hash's quality -- a test that we passed 0.5 and not
        the demo's 0.18. A drifted default here would silently unbalance the
        experiment the pre-registration commits to."""
        arms = [
            assign_arm(
                f"RC-XP{i:04d}",
                experiment_key=EXPERIMENT_KEY,
                control_fraction=CONTROL_FRACTION,
            ).arm
            for i in range(2000)
        ]
        control = sum(1 for a in arms if a is ExperimentArm.CONTROL)
        assert 0.45 <= control / len(arms) <= 0.55

    def test_the_pre_registered_fraction_is_balanced(self) -> None:
        assert CONTROL_FRACTION == 0.5


# ===========================================================================
class TestReferenceNamespaces:
    """`rvp_` and `rvpo_` must not be able to collide."""

    def test_the_two_forms_differ(self) -> None:
        assert reference_id("RC-XP0001", 1) != observed_reference_id("RC-XP0001")

    def test_the_observed_form_carries_no_attempt(self) -> None:
        """There is no attempt. Nothing was sent."""
        assert observed_reference_id("RC-XP0001") == "rvpo_rc-xp0001"

    def test_neither_is_a_prefix_of_the_other_after_the_underscore(self) -> None:
        """A collision would credit a control payment to us as a recovery --
        the one error the holdout exists to prevent -- so disjointness is
        asserted rather than assumed from looking at the format strings."""
        outreach = reference_id("RC-XP0001", 1)
        observed = observed_reference_id("RC-XP0001")
        assert not outreach.startswith(observed)
        assert not observed.startswith(outreach)

    def test_both_are_lowercase(self) -> None:
        """INC-012: Razorpay treats reference uniqueness case-insensitively
        while SQLite's UNIQUE does not."""
        assert observed_reference_id("RC-XP0001") == observed_reference_id("RC-XP0001").lower()


# ===========================================================================
class TestControlArmIsUntouched:
    """**The assertion this file exists for.**

    Asserted as an absence over every table that could record a contact. "We
    did not contact them" is the kind of claim that stays true until someone
    adds a helpful notification, and every rupee of the incremental figure
    depends on it.
    """

    async def test_no_outbox_row_for_any_control_case(
        self, seeded_engine: AsyncEngine, stub_provider: type[_StubProvider]
    ) -> None:
        result = await _run(seeded_engine)
        assert result.control.size > 0, "no control cases: this test would prove nothing"

        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as session:
            rows = int(
                await session.scalar(
                    select(func.count(Outbox.id)).where(Outbox.case_id.in_(result.control.case_ids))
                )
                or 0
            )
        assert rows == 0, "a control case has an outbox row: something was queued to send"

    async def test_no_recovery_action_for_any_control_case(
        self, seeded_engine: AsyncEngine, stub_provider: type[_StubProvider]
    ) -> None:
        result = await _run(seeded_engine)
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as session:
            rows = int(
                await session.scalar(
                    select(func.count(RecoveryAction.id)).where(
                        RecoveryAction.case_id.in_(result.control.case_ids)
                    )
                )
                or 0
            )
        assert rows == 0, "a control case has a recorded action"

    async def test_no_contact_ledger_entry_for_any_control_case(
        self, seeded_engine: AsyncEngine, stub_provider: type[_StubProvider]
    ) -> None:
        result = await _run(seeded_engine)
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as session:
            rows = int(
                await session.scalar(
                    select(func.count(ContactLedger.id)).where(
                        ContactLedger.case_id.in_(result.control.case_ids)
                    )
                )
                or 0
            )
        assert rows == 0, "a control case was contacted"

    async def test_no_control_case_has_an_outreach_reference(
        self, seeded_engine: AsyncEngine, stub_provider: type[_StubProvider]
    ) -> None:
        """Belt and braces: even if a row appeared, it must not carry an
        `rvp_` reference, because that is what attribution matches on."""
        result = await _run(seeded_engine)
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as session:
            for case_id in result.control.case_ids:
                case = await session.get(RecoveryCase, case_id)
                assert case is not None
                assert case.observed_reference_id == observed_reference_id(case_id)

    async def test_the_control_link_does_not_notify(
        self, seeded_engine: AsyncEngine, stub_provider: type[_StubProvider]
    ) -> None:
        """Razorpay must not message on our behalf either.

        Checked against what we actually sent the provider, not against our
        intent: `notify: {sms: true}` would make Razorpay contact a control
        customer, and no assertion on our own tables would ever see it.
        """
        await _run(seeded_engine)
        assert stub_provider.calls, "the provider was never called"
        for call in stub_provider.calls:
            assert call["notify"] == {"sms": False, "email": False}
            assert call["reminder_enable"] is False


# ===========================================================================
class TestTreatedArm:
    """The other half: treated cases do get a real link."""

    async def test_treated_cases_get_an_outbox_row_and_an_action(
        self, seeded_engine: AsyncEngine, stub_provider: type[_StubProvider]
    ) -> None:
        result = await _run(seeded_engine)
        acted = [c for c in result.treatment.case_ids if c in result.treatment.links]
        assert acted, "no treated case produced a link: the arm did nothing"

        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as session:
            for case_id in acted:
                outbox = (
                    (await session.execute(select(Outbox).where(Outbox.case_id == case_id)))
                    .scalars()
                    .first()
                )
                assert outbox is not None, f"{case_id} has a link and no outbox row"
                assert outbox.reference_id.startswith("rvp_")
                assert not outbox.reference_id.startswith("rvpo_")

    async def test_every_case_has_an_arm_assignment(
        self, seeded_engine: AsyncEngine, stub_provider: type[_StubProvider]
    ) -> None:
        """A case with a link and no assignment is unanalysable."""
        result = await _run(seeded_engine)
        all_ids = result.treatment.case_ids + result.control.case_ids
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as session:
            rows = int(
                await session.scalar(
                    select(func.count(ExperimentAssignment.case_id)).where(
                        ExperimentAssignment.case_id.in_(all_ids)
                    )
                )
                or 0
            )
        assert rows == len(all_ids)

    async def test_cases_are_not_marked_demo(
        self, seeded_engine: AsyncEngine, stub_provider: type[_StubProvider]
    ) -> None:
        """`is_demo` excludes a row from attribution, and these are the only
        rows whose outcomes are real provider events."""
        result = await _run(seeded_engine)
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as session:
            for case_id in result.treatment.case_ids + result.control.case_ids:
                case = await session.get(RecoveryCase, case_id)
                assert case is not None and not case.is_demo

    async def test_a_rerun_does_not_collide(
        self, seeded_engine: AsyncEngine, stub_provider: type[_StubProvider]
    ) -> None:
        """Case ids continue from what exists rather than restarting at 1."""
        first = await _run(seeded_engine, n=4)
        second = await _run(seeded_engine, n=4)
        overlap = set(first.treatment.case_ids + first.control.case_ids) & set(
            second.treatment.case_ids + second.control.case_ids
        )
        assert not overlap


# ===========================================================================
class TestControlSettlementResolvesOrganically:
    """A control customer who pays must NOT be credited to us.

    This is the guard the entire incremental figure rests on. Tested at the
    attribution level and then end-to-end through `process_settlement`, because
    the unit-level guarantee is worthless if the wiring does not reach it — that
    is INC-024.
    """

    def test_attribution_refuses_without_an_issued_reference(self) -> None:
        verdict = attribute(
            event_type="payment_link.paid",
            signature_valid=True,
            event_id="evt_stub",
            reference_id=observed_reference_id("RC-XP0002"),
            webhook_amount_paise=100,
            # None: nothing of ours was issued for this case.
            issued_reference_id=None,
            case_status=CaseStatus.MONITORING,
            case_amount_paise=100,
            already_counted=False,
            now=MOMENT,
            window_expires_at=MOMENT + timedelta(hours=24),
            grace=timedelta(hours=1),
        )
        assert not verdict.counted
        assert verdict.resolves_organically

    async def test_end_to_end_a_control_payment_becomes_organic(
        self, seeded_engine: AsyncEngine, stub_provider: type[_StubProvider]
    ) -> None:
        """Driven through the real settlement path, on a real payload shape.

        Three entities, because that is what Razorpay actually sends (INC-025),
        and the reference lives only on `payment_link`.
        """
        result = await _run(seeded_engine)
        assert result.control.case_ids
        case_id = result.control.case_ids[0]
        reference = observed_reference_id(case_id)

        factory = async_sessionmaker(seeded_engine, expire_on_commit=False, autoflush=False)
        payload = {
            "event": "payment_link.paid",
            "payload": {
                "order": {"entity": {"id": "order_x", "amount": 100}},
                "payment": {"entity": {"id": "pay_x", "amount": 100}},
                "payment_link": {
                    "entity": {"id": "plink_x", "reference_id": reference, "amount": 100}
                },
            },
        }
        outcome = await process_settlement(
            factory, payload=payload, event_id="evt_control_1", clock=FakeClock(MOMENT)
        )

        assert not outcome.counted, "a control payment was counted as our recovery"
        async with factory() as session:
            case = await session.get(RecoveryCase, case_id)
            assert case is not None
            assert case.status is CaseStatus.RESOLVED_ORGANIC
            assert case.recovery_verified_by is None, "an organic payment must not be verified-by"
            assert case.recovered_amount_paise in (0, None)

    async def test_a_treated_payment_does_count(
        self, seeded_engine: AsyncEngine, stub_provider: type[_StubProvider]
    ) -> None:
        """The mirror image. Without this, a settlement path that refused
        *everything* would pass the test above."""
        result = await _run(seeded_engine)
        acted = [c for c in result.treatment.case_ids if c in result.treatment.links]
        assert acted
        case_id = acted[0]

        factory = async_sessionmaker(seeded_engine, expire_on_commit=False, autoflush=False)
        async with factory() as session:
            outbox = (
                (await session.execute(select(Outbox).where(Outbox.case_id == case_id)))
                .scalars()
                .first()
            )
            assert outbox is not None
            reference = outbox.reference_id

        payload = {
            "event": "payment_link.paid",
            "payload": {
                "order": {"entity": {"id": "order_y", "amount": 100}},
                "payment": {"entity": {"id": "pay_y", "amount": 100}},
                "payment_link": {
                    "entity": {"id": "plink_y", "reference_id": reference, "amount": 100}
                },
            },
        }
        outcome = await process_settlement(
            factory, payload=payload, event_id="evt_treated_1", clock=FakeClock(MOMENT)
        )
        assert outcome.counted, f"a treated payment was not counted: {outcome.reason}"
        async with factory() as session:
            case = await session.get(RecoveryCase, case_id)
            assert case is not None
            assert case.status is CaseStatus.RECOVERED
            assert case.recovery_verified_by == "evt_treated_1"


# ===========================================================================
class TestHoldoutReport:
    """What the endpoint says, and what it refuses to say."""

    async def test_no_significance_verdict_at_any_n(
        self, seeded_engine: AsyncEngine, stub_provider: type[_StubProvider]
    ) -> None:
        """§6 of the pre-registration commits to one analysis at the full
        sample. A verdict here at n=8 would be noise dressed as a finding."""
        await _run(seeded_engine)
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as session:
            report = await holdout_report(session)
        assert report["significance"] is None
        assert "not reported at any n" in report["significance_basis"]

    async def test_the_limitation_is_in_the_payload(
        self, seeded_engine: AsyncEngine, stub_provider: type[_StubProvider]
    ) -> None:
        """Not in a caption a client can forget to render."""
        await _run(seeded_engine)
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as session:
            report = await holdout_report(session)
        text = report["what_this_does_not_prove"]
        assert "customer behaviour" in text
        assert "1,592" in text

    async def test_both_arms_are_reported(
        self, seeded_engine: AsyncEngine, stub_provider: type[_StubProvider]
    ) -> None:
        await _run(seeded_engine)
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as session:
            report = await holdout_report(session)
        assert set(report["arms"]) == {"TREATMENT", "CONTROL"}
        assert report["arms"]["CONTROL"]["cases"] > 0
        assert report["arms"]["TREATMENT"]["cases"] > 0

    async def test_an_organic_settlement_is_not_a_verified_recovery(
        self, seeded_engine: AsyncEngine, stub_provider: type[_StubProvider]
    ) -> None:
        """The report's own columns must keep the distinction the attribution
        path just made."""
        result = await _run(seeded_engine)
        case_id = result.control.case_ids[0]
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False, autoflush=False)
        await process_settlement(
            factory,
            payload={
                "event": "payment_link.paid",
                "payload": {
                    "payment_link": {
                        "entity": {
                            "id": "plink_z",
                            "reference_id": observed_reference_id(case_id),
                            "amount": 100,
                        }
                    }
                },
            },
            event_id="evt_control_2",
            clock=FakeClock(MOMENT),
        )
        async with factory() as session:
            report = await holdout_report(session)
        control = report["arms"]["CONTROL"]
        assert control["organic"] == 1
        assert control["razorpay_verified_recoveries"] == 0
        assert control["paise"] == 0


# ===========================================================================
class TestRefusals:
    """Preconditions, refused loudly."""

    async def test_needs_at_least_two_cases(self, seeded_engine: AsyncEngine) -> None:
        with pytest.raises(ValueError, match="no arms"):
            await _run(seeded_engine, n=1)

    async def test_needs_razorpay_credentials(self, seeded_engine: AsyncEngine) -> None:
        class _NoKeys:
            razorpay_key_id = ""
            razorpay_key_secret = ""
            gemini_model = "x"

        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        with pytest.raises(RuntimeError, match="RAZORPAY_KEY_ID"):
            await run_testmode_experiment(
                factory,
                clock=FakeClock(MOMENT),
                settings=_NoKeys(),  # type: ignore[arg-type]
                n=4,
            )
