"""Metrics endpoints (§20).

Four tiles' worth of data, every rupee figure carrying a provenance badge and
a one-line basis. The arithmetic lives in ``services/metrics.py`` and
``services/attribution.py``; this module composes and serialises.

``/metrics/overview`` deliberately returns gross **and** net together and will
not return one without the other. A caller cannot request the flattering half.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.power import sample_size_plan
from app.core.provenance import Figure, Provenance
from app.db.enums import CaseStatus, ExperimentArm
from app.db.models import ExperimentAssignment, RecoveryCase
from app.deps import get_clock, get_db
from app.security.auth import Principal, require_api_token
from app.services import metrics as metrics_service
from app.services.attribution import CaseOutcome, recovery_report
from app.workers.experiment import holdout_report

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])

_SETTLED = frozenset({CaseStatus.RECOVERED, CaseStatus.RESOLVED_ORGANIC})


async def _outcomes(session: AsyncSession) -> list[CaseOutcome]:
    """Build the attribution population from the database.

    One place, used by both ``/attribution`` and ``/overview``, so the two
    endpoints cannot disagree about the lift — two implementations of one
    number is the INC-007 shape.
    """
    rows = (
        await session.execute(
            select(RecoveryCase, ExperimentAssignment.arm).outerjoin(
                ExperimentAssignment, ExperimentAssignment.case_id == RecoveryCase.id
            )
        )
    ).all()
    return [
        CaseOutcome(
            case_id=case.id,
            arm=arm or ExperimentArm.TREATMENT,
            paid=case.status in _SETTLED,
            amount_paise=case.amount_paise,
            # Only a case with a verifying webhook counts, which is the
            # database's own constraint rather than a rule applied here.
            recovered=case.recovery_verified_by is not None,
            is_demo=case.is_demo,
        )
        for case, arm in rows
    ]


@router.get("/overview", summary="Headline tiles; gross and net always together")
async def overview(
    session: Annotated[AsyncSession, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    _principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    report = await metrics_service.overview(session, clock=clock)
    body = report.as_dict()

    # Fill in net incremental from the attribution service rather than
    # recomputing it. If there is no control arm the figure stays zero and the
    # basis says why, which is the honest answer -- claiming gross as
    # incremental is exactly the overstatement the control group prevents.
    attribution = recovery_report(await _outcomes(session))
    body["net_incremental"] = Figure(
        paise=attribution.net_incremental_paise,
        provenance=Provenance.SIMULATED,
        basis=(
            f"lift {attribution.absolute_lift:.1%} over a {attribution.control.cases}-case "
            "holdout, less discounts and inference"
            if attribution.has_control
            else "no control arm in this population: incremental cannot be computed "
            "and is not claimed"
        ),
    ).as_dict()
    body["lift_is_significant"] = attribution.lift_is_significant
    body["notes"] = list(attribution.notes)
    return body


@router.get("/attribution", summary="Treatment vs control, lift, confidence intervals")
async def attribution(
    session: Annotated[AsyncSession, Depends(get_db)],
    _principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    """The measurement, with its caveats attached to the payload.

    ``notes`` carries the significance warning as a sentence, not only as the
    ``lift_is_significant`` boolean — a boolean is something a client can
    forget to render, and an unqualified 6% on a dashboard reads as a result
    rather than as noise.
    """
    return recovery_report(await _outcomes(session)).as_dict()


@router.get("/cost", summary="Free-tier consumption and the paid-rate projection")
async def cost(
    session: Annotated[AsyncSession, Depends(get_db)],
    _principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    return (await metrics_service.cost_report(session)).as_dict()


#: The effect size `docs/PRE-REGISTRATION.md` §5 declares. A **declared
#: assumption**, not a measurement: these are the rates the simulation produces,
#: and the pre-registration says so in the same breath. Named constants rather
#: than inline literals because `tests/test_power.py` asserts the document and
#: the code agree, and it needs one place to compare against.
PREREG_P_CONTROL = 0.2308
PREREG_P_TREATMENT = 0.2924
#: §4: balanced, because power is governed by the smaller arm.
PREREG_CONTROL_FRACTION = 0.5


@router.get("/power", summary="How far the corpus is from a powered causal test")
async def power(
    session: Annotated[AsyncSession, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    _principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    """The gap between what we have and what would settle the causal question.

    This endpoint exists because "not statistically significant" is a
    disclaimer, and a disclaimer invites the reader to guess how close we are.
    A completion percentage and a case count do not.

    Two things it deliberately does **not** return:

    * **No p-value, and no significance verdict.** §6 of the pre-registration
      commits to analysing once, at the full sample. A p-value on a dashboard
      while data accumulates is an invitation to stop when it looks good, and
      repeated testing of a growing sample yields a spurious p < 0.05 roughly
      one time in three. The one place significance is reported is
      ``/attribution``, over the completed seeded corpus, where it says *not*
      significant.
    * **No projected date unless there is real arrival data.** ``eta`` is null
      here rather than extrapolated from the seeded corpus, whose cases all
      arrive at once. A countdown computed from fabricated velocity is fiction.
    """
    control_now = int(
        await session.scalar(
            select(func.count(ExperimentAssignment.case_id)).where(
                ExperimentAssignment.arm == ExperimentArm.CONTROL
            )
        )
        or 0
    )
    treatment_now = int(
        await session.scalar(
            select(func.count(ExperimentAssignment.case_id)).where(
                ExperimentAssignment.arm == ExperimentArm.TREATMENT
            )
        )
        or 0
    )

    plan = sample_size_plan(
        control_now=control_now,
        treatment_now=treatment_now,
        p_control=PREREG_P_CONTROL,
        p_treatment=PREREG_P_TREATMENT,
        control_fraction=PREREG_CONTROL_FRACTION,
    )

    return {
        "registered": "docs/PRE-REGISTRATION.md",
        "design": {
            "alpha": 0.05,
            "power": 0.80,
            "control_fraction": PREREG_CONTROL_FRACTION,
            "assumed_control_rate": PREREG_P_CONTROL,
            "assumed_treatment_rate": PREREG_P_TREATMENT,
            "assumption_basis": (
                "the rates the seeded simulation produces -- a declared "
                "parameter, not an observation of customer behaviour"
            ),
        },
        "have": {"control": plan.control_now, "treatment": plan.treatment_now},
        "need": {
            "control": plan.control_required,
            "treatment": plan.treatment_required,
        },
        "completion": round(plan.completion, 4),
        "completion_basis": (
            "the fraction of the BINDING arm, not of the total: power is "
            "governed by the smaller arm, and a study with 5,000 treated and "
            "12 control cases is not 99% of the way to an answer"
        ),
        "cases_remaining": plan.cases_remaining,
        "attempts_remaining": {
            "at_8pc_failure_rate": plan.attempts_needed(0.08),
            "at_12pc_failure_rate": plan.attempts_needed(0.12),
            "at_20pc_failure_rate": plan.attempts_needed(0.20),
        },
        "is_powered": plan.is_powered,
        # Null by construction on the seeded corpus. See the docstring.
        "eta": None,
        "eta_basis": (
            "no real arrival rate exists: the seeded corpus arrives at once. A "
            "date will appear here only when live traffic provides a velocity."
        ),
        "blocked_on": [
            "a merchant with the traffic volume above, and written consent to "
            "contact their customers",
            "DLT/TRAI registration in the merchant's name, with approved "
            "templates -- weeks of lead time, and the binding external gate",
        ],
        "today": clock.now_utc().date().isoformat(),
    }


@router.get("/holdout", summary="The real-provider randomised holdout, arm by arm")
async def holdout(
    session: Annotated[AsyncSession, Depends(get_db)],
    _principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    """Outcomes of the Test Mode holdout, from RAZORPAY_VERIFIED events only.

    Separate from ``/attribution`` on purpose. That endpoint reports the seeded
    corpus, where our own code decides who pays; this one reports cases whose
    outcomes are real signed webhooks. Merging them would produce a single rate
    that is neither, labelled as whichever the reader preferred.

    Returns ``significance: null`` at every sample size. See
    ``workers/experiment.holdout_report``.
    """
    return await holdout_report(session)


@router.get("/stopping-rules", summary="Firing counts by rule id, including zeroes")
async def stopping_rules(
    session: Annotated[AsyncSession, Depends(get_db)],
    _principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    return await metrics_service.stopping_rule_counts(session)
