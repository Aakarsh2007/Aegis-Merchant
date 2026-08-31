"""Human-in-the-loop approvals (§8.3 A2/A3, §13.5).

The endpoint that makes an approval gate real rather than a button labelled
"approve":

* ``reviewed_by`` comes from the **authenticated principal**, never from the
  request body. A caller who could name their own reviewer could approve a
  ₹1,00,000 action as "the CFO".
* The presented ``policy_applied_hash`` must match what is stored, or the
  action is refused with 409. A human approves a *specific* action with
  specific numbers; if anything changed between the screen they read and this
  request, their approval no longer refers to what would happen.
* Every action appends an audit block naming the principal and the exact
  ``policy_applied`` they approved, which is what rung A3 requires.

Ordering is deliberate
----------------------

Expiry is checked before the hash, and the hash before the state transition.
An expired approval must not be actionable even with a correct hash — the TTL
exists because a decision made against four-hour-old information is not the
decision the reviewer thought they were making.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.db.enums import ApprovalStatus, CaseStatus
from app.db.models import ApprovalRequest, RecoveryCase
from app.deps import get_clock, get_db
from app.security.auth import Principal, require_api_token, verify_approval_hash
from app.tools.audit import AuditChain

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


def _audit(clock: Clock) -> AuditChain:
    return AuditChain(clock)


class ActionRequest(BaseModel):
    """What a reviewer submits.

    Note what is *absent*: any field naming the reviewer. That comes from the
    bearer token. Accepting it here would let a caller authorise a six-figure
    action under someone else's name.
    """

    model_config = {"extra": "forbid"}

    action: Literal["approve", "reject"]
    #: The hash of the action as it was displayed. Required — not optional with
    #: a skip-if-absent fallback, which would make the whole guard bypassable
    #: by omitting a field.
    policy_applied_hash: str = Field(min_length=64, max_length=64)
    notes: str | None = Field(default=None, max_length=1000)


@router.get("", summary="Pending approvals, with the hash to present back")
async def list_pending(
    session: Annotated[AsyncSession, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    _principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    """Approvals awaiting a human.

    Rows already past their TTL are reported as ``expired: true`` rather than
    hidden or silently omitted. A reviewer looking at a queue needs to see that
    something expired unactioned — that is a signal about their own response
    time, and hiding it would make the queue look healthier than it is.
    """
    now = clock.now_utc()
    rows = (
        (
            await session.execute(
                select(ApprovalRequest)
                .where(ApprovalRequest.status == ApprovalStatus.PENDING)
                .order_by(ApprovalRequest.expires_at)
            )
        )
        .scalars()
        .all()
    )
    return {
        "approvals": [
            {
                "id": a.id,
                "case_id": a.case_id,
                "trigger_rung": a.trigger_rung.value,
                "trigger_reason": a.trigger_reason,
                "amount_paise": a.amount_paise,
                "policy_applied": a.policy_applied_json,
                "policy_applied_hash": a.policy_applied_hash,
                "expires_at": a.expires_at.isoformat(),
                "seconds_remaining": max(0, int((a.expires_at - now).total_seconds())),
                "expired": a.expires_at <= now,
            }
            for a in rows
        ],
        "count": len(rows),
    }


@router.post("/{approval_id}/action", summary="Approve or reject a pending action")
async def action_approval(
    approval_id: str,
    body: ActionRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    """Record a human decision.

    Returns 404 for an unknown approval, 409 for one that is already actioned
    or past its TTL, and 409 for a hash mismatch.
    """
    approval = await session.get(ApprovalRequest, approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such approval.")

    now = clock.now_utc()

    # 1. Already decided. Idempotency matters here: a double-clicked Approve
    #    must not produce two audit blocks claiming two separate decisions.
    if approval.status is not ApprovalStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This approval is already {approval.status.value}.",
        )

    # 2. Expired, checked BEFORE the hash. A decision made against four-hour-old
    #    information is not the decision the reviewer thinks they are making,
    #    and a correct hash does not make stale information fresh.
    if approval.expires_at <= now:
        approval.status = ApprovalStatus.EXPIRED
        approval.reviewed_at = now
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This approval expired at {approval.expires_at.isoformat()} and has been "
                "marked EXPIRED. Re-run the case to produce a fresh proposal."
            ),
        )

    # 3. The hash. Raises 409 on mismatch.
    verify_approval_hash(presented=body.policy_applied_hash, current=approval.policy_applied_hash)

    approved = body.action == "approve"
    approval.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
    approval.reviewed_at = now
    approval.reviewed_by = principal.audit_actor
    approval.review_notes = body.notes

    case = await session.get(RecoveryCase, approval.case_id)
    if case is not None and case.status is CaseStatus.AWAITING_APPROVAL:
        if approved:
            case.status = CaseStatus.STRATEGY_FORMED
        else:
            case.status = CaseStatus.REJECTED
            case.resolved_at = now

    await _audit(clock).append(
        session,
        event_name="approval.approved" if approved else "approval.rejected",
        actor=principal.audit_actor,
        payload={
            "approval_id": approval.id,
            "case_id": approval.case_id,
            "amount_paise": approval.amount_paise,
            "trigger_rung": approval.trigger_rung.value,
            # The exact action approved, not a summary. Rung A3 requires the
            # principal be recorded WITH the policy_applied they approved, so
            # a later dispute can compare what was authorised against what ran.
            "policy_applied": approval.policy_applied_json,
            "policy_applied_hash": approval.policy_applied_hash,
            "notes": body.notes,
            "unauthenticated_principal": principal.unauthenticated,
        },
        case_id=approval.case_id,
    )
    await session.commit()

    return {
        "id": approval.id,
        "status": approval.status.value,
        "reviewed_by": approval.reviewed_by,
        "reviewed_at": now.isoformat(),
        "case_status": case.status.value if case else None,
        # Stated because it would otherwise be inferred, and inferred wrongly.
        # Approving records an authorisation and moves the case to
        # STRATEGY_FORMED. **Nothing in this build then executes it** -- there is
        # no continuous worker consuming STRATEGY_FORMED, by design: dispatching
        # a real provider call for a seeded demo customer would be worse than
        # doing nothing. A reviewer who clicked approve and saw 200 would
        # reasonably assume a message went out, so the response says plainly
        # that one did not.
        "what_happens_next": (
            "The authorisation is recorded and hash-pinned in the audit ledger; "
            "the case moved to STRATEGY_FORMED. Nothing was dispatched: this "
            "build has no worker that executes approved actions, because the "
            "seeded corpus has no real customers to contact. In a deployment "
            "the outbox drainer would pick this up. To watch a real dispatch "
            "end to end, use the Test Mode panel -- that path calls Razorpay."
            if approved
            else "The case is closed as REJECTED. Nothing was dispatched, and "
            "the rejection is recorded in the audit ledger."
        ),
        "dispatched": False,
    }
