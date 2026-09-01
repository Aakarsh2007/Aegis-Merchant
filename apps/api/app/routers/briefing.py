"""The morning briefing (§19.3).

Numbers computed in SQL, sentences assembled from them. The LLM does not
compute anything here and is not asked to: a narrated figure is a figure
somebody has to check, and there is no upside to a model restating a number the
database already knows.

The last section is the one that matters
----------------------------------------

*"I did not contact 6 customers who had reached their 48-hour contact limit, 4
who have no marketing consent, and 2 who opted out."*

An agent that reports **what it chose not to do** is one a merchant can trust,
and it is the clearest possible demonstration that the stopping rules are load
bearing rather than decorative. Most agent demos show only the actions taken,
which is exactly the half that cannot be audited by a sceptic.

So the restraint section is not optional, and it is not filtered to look good:
when nothing was suppressed it says so plainly, because a briefing that only
appeared on good days would be advertising.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock, iso_ist
from app.core.provenance import rupees
from app.db.enums import CaseStatus, ExperimentArm, Playbook, StoppingRule
from app.db.models import ExperimentAssignment, RecoveryCase
from app.deps import get_clock, get_db
from app.security.auth import Principal, require_api_token
from app.services import metrics as metrics_service
from app.services.attribution import recovery_report

router = APIRouter(prefix="/api/v1/briefing", tags=["briefing"])

#: Why each rule declined to act, in the merchant's language rather than ours.
_RESTRAINT_WORDING: dict[StoppingRule, str] = {
    StoppingRule.S01_ALREADY_RESOLVED: "had already paid",
    StoppingRule.S02_ATTEMPT_BUDGET: "had used their attempt budget",
    StoppingRule.S03_DISCOUNT_ATTEMPT_BUDGET: "had already been offered a discount",
    StoppingRule.S04_CONTACT_CAP_24H: "were inside their 24-hour contact limit",
    StoppingRule.S05_CONTACT_CAP_48H: "had reached their 48-hour contact limit",
    StoppingRule.S06_RECOVERY_WINDOW: "were past their recovery window",
    StoppingRule.S07_OPT_OUT: "have opted out",
    StoppingRule.S08_CONSENT_CLASS: "have no marketing consent",
    StoppingRule.S09_QUIET_HOURS: "were in quiet hours and are queued for 09:05",
    StoppingRule.S10_PROMISE_FREEZE: "have an active promise to pay",
    StoppingRule.S11_MERCHANT_BUDGET: "were beyond your daily action budget",
    StoppingRule.S12_KILL_SWITCH: "were halted by the autopilot kill switch",
}

_PLAYBOOK_WORDING: dict[Playbook, str] = {
    Playbook.PAYMENT_FAILURE: "failed payments",
    Playbook.CHECKOUT_ABANDON: "abandoned carts",
    Playbook.RECEIVABLE: "overdue invoices",
    Playbook.SUBSCRIPTION: "subscription renewals",
}


def _singular(plural: str, count: int) -> str:
    """`1 failed payment`, not `1 failed payments`.

    Trivial, and worth doing: a grammatical slip in the one paragraph asking a
    merchant to trust a number undermines it out of proportion to the effort.
    """
    if count != 1:
        return plural
    return plural[:-1] if plural.endswith("s") else plural


@router.get("/today", summary="The morning briefing")
async def today(
    session: Annotated[AsyncSession, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    _principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    """Everything a merchant needs before their first coffee.

    Every figure carries a provenance badge, as everywhere else. The narrative
    lines are assembled from those same figures, so the prose and the tiles
    cannot disagree — a briefing that rounded differently from the dashboard
    would undermine both.
    """
    # Imported and computed before the overview, which now requires it.
    from app.routers.metrics import _outcomes

    attribution = recovery_report(await _outcomes(session))
    overview = await metrics_service.overview(session, clock=clock, attribution=attribution)

    recovered_rows = (
        await session.execute(
            select(RecoveryCase.playbook, func.count(RecoveryCase.id))
            .where(RecoveryCase.status == CaseStatus.RECOVERED)
            .group_by(RecoveryCase.playbook)
        )
    ).all()

    restraint_rows = (
        await session.execute(
            select(RecoveryCase.stopping_rule_fired, func.count(RecoveryCase.id))
            .where(RecoveryCase.stopping_rule_fired.is_not(None))
            .group_by(RecoveryCase.stopping_rule_fired)
            .order_by(func.count(RecoveryCase.id).desc())
        )
    ).all()

    held = (
        await session.scalar(
            select(func.count(ExperimentAssignment.case_id)).where(
                ExperimentAssignment.arm == ExperimentArm.CONTROL
            )
        )
    ) or 0

    # Attribution is composed rather than recomputed: one implementation of the
    # lift, so the briefing and /metrics/attribution cannot drift.
    from app.routers.metrics import _outcomes

    attribution = recovery_report(await _outcomes(session))

    # Singular where the count is one. "1 failed payments" reads as a bug in a
    # line a merchant is meant to trust.
    recovered_phrase = " · ".join(
        f"{count} {_singular(_PLAYBOOK_WORDING.get(playbook, playbook.value), count)}"
        for playbook, count in recovered_rows
    )

    restraint = [
        {
            "rule": rule.value,
            "count": int(count),
            "wording": f"{count} who {_RESTRAINT_WORDING.get(rule, 'were held by ' + rule.value)}",
        }
        for rule, count in restraint_rows
        if rule is not None
    ]

    return {
        "greeting": "Good morning, GlowKart.",
        "as_of_ist": iso_ist(clock.now_ist()),
        "headline": {
            "at_risk": overview.at_risk.as_dict(),
            "gross_recovered": overview.gross_recovered.as_dict(),
            "gross_simulated": overview.gross_simulated.as_dict(),
            "net_incremental": {
                "paise": attribution.net_incremental_paise,
                "display": f"Rs {rupees(attribution.net_incremental_paise)}",
                "provenance": "SIMULATED",
                "basis": (
                    f"lift {attribution.absolute_lift:.1%} over a "
                    f"{attribution.control.cases}-case holdout"
                ),
            },
        },
        "lines": [
            f"{overview.at_risk.display} at risk across {overview.open_cases.value} open cases.",
            (
                f"{overview.gross_simulated.display} recovered"
                + (f" — {recovered_phrase}." if recovered_phrase else ".")
                if overview.gross_simulated.paise
                else (
                    f"Nothing recovered yet — {recovered_phrase}."
                    if recovered_phrase
                    else "Nothing recovered yet; run the batch to put the corpus through the agent."
                )
            ),
            (
                f"Rs {rupees(attribution.net_incremental_paise)} net incremental against "
                f"{held} cases held as an untouched control group."
            ),
            (
                f"{overview.interceptions.value} unsafe proposals intercepted · "
                f"{overview.pending_approvals.value} waiting on you."
            ),
        ],
        # The section that matters. Never filtered, never omitted on a quiet
        # day: a briefing that only appeared when the news was good would be
        # advertising rather than reporting.
        "restraint": {
            "total": sum(row["count"] for row in restraint),
            "items": restraint,
            "sentence": (
                "I did not contact " + ", ".join(row["wording"] for row in restraint) + "."
                if restraint
                else "Nothing was suppressed today: no stopping rule fired."
            ),
        },
        "caveats": list(attribution.notes),
        "narration": (
            "Numbers are computed in SQL and assembled into sentences. No model "
            "narrates a figure it did not compute."
        ),
    }
