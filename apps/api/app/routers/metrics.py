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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.provenance import Figure, Provenance
from app.db.enums import CaseStatus, ExperimentArm
from app.db.models import ExperimentAssignment, RecoveryCase
from app.deps import get_clock, get_db
from app.security.auth import Principal, require_api_token
from app.services import metrics as metrics_service
from app.services.attribution import CaseOutcome, recovery_report

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


@router.get("/stopping-rules", summary="Firing counts by rule id, including zeroes")
async def stopping_rules(
    session: Annotated[AsyncSession, Depends(get_db)],
    _principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    return await metrics_service.stopping_rule_counts(session)
