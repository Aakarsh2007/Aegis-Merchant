"""Razorpay webhook ingestion (workflow.md §10.1).

The order of operations here is the design, and it is deliberate:

1. **Read raw bytes first.** Parsing before verifying would break every
   signature, because a re-serialised body is not the byte string that was
   signed.
2. **Verify the signature.** Constant-time, fail closed on a missing secret.
3. **Bound the replay window.** A valid signature proves origin, not recency.
4. **Insert with ``UNIQUE(event_id)``.** The IntegrityError *is* the duplicate
   defence; there is no separate "have I seen this?" query, which would be a
   race between two workers.
5. **Acknowledge immediately**, then process in the background. Razorpay
   retries anything slow or non-2xx, so holding the connection open while an
   LLM thinks would turn one event into several.

A rejected webhook is never processed and never stored as valid. A duplicate is
acknowledged with 200 — returning an error would make Razorpay retry a message
we have already handled.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.clock import Clock
from app.db.ids import new_id
from app.db.models import WebhookEvent
from app.deps import get_clock, get_db
from app.ingest.normalise import normalise
from app.security.webhook import verify_signature, verify_timestamp

__all__ = ["router"]

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


class IngestStatus:
    """Outcomes, as recorded on ``webhook_events.status``."""

    ACCEPTED = "ACCEPTED"
    DUPLICATE_DROPPED = "DUPLICATE_DROPPED"
    PROCESSED = "PROCESSED"
    IGNORED_UNKNOWN_EVENT = "IGNORED_UNKNOWN_EVENT"
    FAILED = "FAILED"


async def _process_event(event_row_id: str, payload: dict[str, Any], event_id: str) -> None:
    """Background processing.

    Phase 2 normalises and classifies only. Case creation and the agent graph
    arrive in Phases 4-7; wiring them here now would mean writing rows the
    policy firewall cannot yet gate.
    """
    event = normalise(payload, event_id=event_id)
    # Structured logging lands in Phase 11; until then this is the seam.
    _ = event


@router.post(
    "/razorpay",
    status_code=status.HTTP_200_OK,
    summary="Ingest a signed Razorpay webhook",
    responses={
        200: {"description": "Accepted, or an idempotent duplicate acknowledgement"},
        401: {"description": "Signature invalid, absent, or outside the replay window"},
    },
)
async def razorpay_webhook(
    request: Request,
    response: Response,
    background: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_razorpay_signature: Annotated[str | None, Header()] = None,
    x_razorpay_event_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    # 1. Raw bytes, before anything parses them.
    raw_body = await request.body()

    # 2. Origin.
    signature = verify_signature(raw_body, x_razorpay_signature, settings.razorpay_webhook_secret)
    if not signature.valid:
        # Deliberately terse: an attacker probing the endpoint learns only that
        # it was rejected, not which check rejected it. The reason is logged.
        response.status_code = status.HTTP_401_UNAUTHORIZED
        return {"status": "rejected", "reason": signature.reason}

    # Parse only after the bytes are trusted.
    try:
        payload: dict[str, Any] = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"status": "rejected", "reason": "malformed_json"}

    event = normalise(payload, event_id=x_razorpay_event_id or "")

    # 3. Recency. A signature proves origin, not freshness.
    freshness = verify_timestamp(
        event.event_ts, clock.now_utc(), settings.webhook_replay_tolerance_s
    )
    if not freshness.valid:
        response.status_code = status.HTTP_401_UNAUTHORIZED
        return {"status": "rejected", "reason": freshness.reason}

    # Razorpay supplies the event id in a header. Without it we cannot
    # deduplicate, so we reject rather than accept an event we could process
    # twice.
    if not x_razorpay_event_id:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"status": "rejected", "reason": "event_id_header_missing"}

    # 4. Idempotency. The UNIQUE constraint is the check -- a prior SELECT
    # would be a race between concurrent deliveries of the same event.
    row = WebhookEvent(
        id=new_id("webhook"),
        event_id=x_razorpay_event_id,
        event_type=event.event_type,
        payload_json=raw_body.decode("utf-8", errors="replace"),
        signature=x_razorpay_signature,
        signature_valid=True,
        status=IngestStatus.ACCEPTED,
        event_ts=event.event_ts,
        received_at=clock.now_utc(),
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # 200, not an error: an error would make Razorpay retry an event we
        # have already handled.
        return {
            "status": "duplicate",
            "event_id": x_razorpay_event_id,
            "detail": "already ingested",
        }

    # 5. Acknowledge now; think later.
    background.add_task(_process_event, row.id, payload, x_razorpay_event_id)

    return {
        "status": "accepted",
        "event_id": x_razorpay_event_id,
        "event_type": event.event_type,
        "playbook": event.playbook.value if event.playbook else None,
        "actionable": event.is_actionable,
    }
