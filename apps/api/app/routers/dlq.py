"""Dead-letter queue: what failed, and replaying it safely (§20).

The replay endpoint is the one that could double-charge a customer, so it does
not create a new action. It re-queues the **original outbox row with its
original ``reference_id``**, which is the whole point of the two-phase outbox:
Razorpay's own uniqueness constraint on ``reference_id`` makes the retry
idempotent at the provider, so a replay of something that actually succeeded
is rejected by Razorpay rather than charged twice.

Minting a fresh reference on replay would look equivalent and would be the bug:
it converts "retry this action" into "perform this action again".
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.db.enums import DLQStatus, OutboxStatus
from app.db.models import DeadLetter, Outbox
from app.deps import get_clock, get_db
from app.security.auth import Principal, require_api_token
from app.tools.audit import AuditChain

router = APIRouter(prefix="/api/v1/dlq", tags=["dlq"])


@router.get("", summary="Dead-lettered actions")
async def list_dead_letters(
    session: Annotated[AsyncSession, Depends(get_db)],
    _principal: Annotated[Principal, Depends(require_api_token)],
    dlq_status: Annotated[DLQStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    stmt = select(DeadLetter, Outbox).join(Outbox, Outbox.id == DeadLetter.outbox_id)
    count_stmt = select(func.count(DeadLetter.id))
    if dlq_status is not None:
        stmt = stmt.where(DeadLetter.status == dlq_status)
        count_stmt = count_stmt.where(DeadLetter.status == dlq_status)

    total = int((await session.scalar(count_stmt)) or 0)
    rows = (
        await session.execute(
            stmt.order_by(DeadLetter.created_at.desc()).limit(limit).offset(offset)
        )
    ).all()

    return {
        "dead_letters": [
            {
                "id": dl.id,
                "outbox_id": dl.outbox_id,
                "case_id": entry.case_id,
                "action_type": entry.action_type.value,
                # Shown so an operator can confirm a replay reuses it.
                "reference_id": entry.reference_id,
                "reason": dl.reason,
                "attempts": dl.attempts,
                "status": dl.status.value,
                "error_chain": json.loads(dl.error_chain_json),
                "created_at": dl.created_at.isoformat(),
                "replayed_at": dl.replayed_at.isoformat() if dl.replayed_at else None,
            }
            for dl, entry in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/{dead_letter_id}/replay", summary="Re-queue with the ORIGINAL reference_id")
async def replay(
    dead_letter_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    """Return a dead-lettered action to the outbox for another attempt.

    Refuses to replay one that is already REPLAYED (409). A replay is an
    operator asserting the failure was transient; doing it twice by accident —
    a double-clicked button — must not queue two attempts.
    """
    dead = await session.get(DeadLetter, dead_letter_id)
    if dead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such dead letter.")
    if dead.status is DLQStatus.REPLAYED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Already replayed at {dead.replayed_at.isoformat() if dead.replayed_at else 'unknown'}.",
        )

    entry = await session.get(Outbox, dead.outbox_id)
    if entry is None:  # pragma: no cover - FK makes this unreachable
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The underlying outbox row is gone; nothing to replay.",
        )

    now = clock.now_utc()
    original_reference = entry.reference_id

    # The reference_id is NOT regenerated. Razorpay rejects a duplicate, so a
    # replay of something that actually succeeded is refused by the provider
    # rather than charged twice.
    entry.status = OutboxStatus.PENDING
    entry.next_attempt_at = now
    entry.last_error = None

    dead.status = DLQStatus.REPLAYED
    dead.replayed_at = now

    await AuditChain(clock).append(
        session,
        event_name="dlq.replayed",
        actor=principal.audit_actor,
        payload={
            "dead_letter_id": dead.id,
            "outbox_id": entry.id,
            "case_id": entry.case_id,
            "reference_id": original_reference,
            "attempts_before_replay": dead.attempts,
            "note": (
                "re-queued with the ORIGINAL reference_id; provider-side uniqueness "
                "makes a replay of a succeeded action a no-op rather than a second charge"
            ),
        },
        case_id=entry.case_id,
    )
    await session.commit()

    return {
        "dead_letter_id": dead.id,
        "outbox_id": entry.id,
        "reference_id": original_reference,
        "status": dead.status.value,
        "requeued_at": now.isoformat(),
    }
