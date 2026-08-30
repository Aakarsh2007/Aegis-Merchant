"""Audit endpoints (§13.4).

``GET /api/v1/audit/verify`` recomputes the entire chain and reports what it
found. The demo calls it, tampers with a row, and calls it again — because a
verifier nobody has watched fail is indistinguishable from one that returns
``true`` unconditionally.

``POST /api/v1/audit/tamper`` is the button that makes that demonstration
possible. It is **refused outside development**, refuses to run against a chain
it did not just read, and writes nothing that the verifier is not designed to
catch. It exists so a judge can watch the check work on their own machine
rather than take our word for it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import AuditBlock
from app.deps import get_db
from app.security.auth import Principal, require_api_token
from app.tools.audit import verify_blocks

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


class TamperRequest(BaseModel):
    """Which block to corrupt, and how."""

    model_config = {"extra": "forbid"}

    block_index: int = Field(ge=0)
    #: What to corrupt. Each maps to a different verifier check, so the demo
    #: can show more than one kind of detection.
    mode: str = Field(default="payload", pattern="^(payload|hash|timestamp)$")


@router.get(
    "/verify",
    summary="Recompute the audit chain and report any divergence",
)
async def verify_chain(
    session: Annotated[AsyncSession, Depends(get_db)],
    _principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    """Recompute every block from its own stored fields.

    Nothing stored is trusted — not ``current_hash``, not ``payload_hash``, not
    the link. The response names the specific failure and the index it occurred
    at, rather than a bare boolean, because "invalid" is not actionable.
    """
    result = await session.execute(select(AuditBlock).order_by(AuditBlock.block_index))
    return verify_blocks(list(result.scalars().all())).as_dict()


@router.get(
    "/blocks",
    summary="Read the ledger",
)
async def list_blocks(
    session: Annotated[AsyncSession, Depends(get_db)],
    _principal: Annotated[Principal, Depends(require_api_token)],
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Page through the chain. Read-only; no endpoint updates or deletes."""
    limit = max(1, min(limit, 200))
    result = await session.execute(
        select(AuditBlock).order_by(AuditBlock.block_index).limit(limit).offset(max(0, offset))
    )
    blocks = list(result.scalars().all())
    return {
        "blocks": [
            {
                "block_index": b.block_index,
                "event_name": b.event_name,
                "actor": b.actor,
                "case_id": b.case_id,
                "created_at": b.created_at.isoformat(),
                "prev_hash": b.prev_hash,
                "current_hash": b.current_hash,
                "payload": b.payload_canonical,
            }
            for b in blocks
        ],
        "limit": limit,
        "offset": offset,
    }


@router.post(
    "/tamper",
    summary="[dev only] Corrupt a block so the verifier can be seen catching it",
    status_code=status.HTTP_200_OK,
)
async def tamper(
    body: TamperRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    _principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    """Deliberately corrupt one block.

    Gated on ``settings.simulation_allowed`` — an endpoint that damages the
    audit log must be unreachable in production, and the check is on the
    environment rather than on a header a caller controls.
    """
    if not settings.simulation_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tampering is a development-only demonstration and is disabled here.",
        )

    block = (
        (
            await session.execute(
                select(AuditBlock).where(AuditBlock.block_index == body.block_index)
            )
        )
        .scalars()
        .first()
    )
    if block is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No block at index {body.block_index}.",
        )

    before = {"payload": block.payload_canonical, "hash": block.current_hash}
    if body.mode == "payload":
        block.payload_canonical = '{"tampered":true}'
    elif body.mode == "hash":
        block.current_hash = "0" * 63 + "1"
    else:
        block.created_at = block.created_at.replace(year=block.created_at.year - 1)
    await session.commit()

    return {
        "tampered_block_index": body.block_index,
        "mode": body.mode,
        "before": before,
        "next_step": "GET /api/v1/audit/verify — it should now report valid: false",
    }
