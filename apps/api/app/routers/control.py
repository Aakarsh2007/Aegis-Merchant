"""Merchant controls: the kill switch and the live policy bounds (§8.2, §20).

Two endpoints that were specified and missing. Their absence was not cosmetic:
`autopilot_enabled` and the policy bounds are *inputs to the stopping rules*,
and a control nobody can operate is a control that does not exist.

The kill switch
---------------

One toggle, checked at TRIAGE and again at POLICY, evaluated **first** so that
turning autopilot off cannot be outvoted by any other consideration. It halts
new actions; it deliberately does **not** cancel work already committed to the
outbox, because a payment link that has already been created at the provider is
not un-created by us changing our mind — the honest behaviour is to stop
issuing new ones and let the reconciler finish what is in flight.

Every toggle appends an audit block naming the principal. "Who turned the agent
off, and when" is exactly the question asked after an incident.

Live policy bounds
------------------

§12.3 says every policy bound is a config value rather than a literal, so that
a merchant can tighten one and have it take effect immediately. This endpoint
is what makes that true at runtime rather than at restart.

**Bounds may only be tightened here, never loosened.** Raising a discount
ceiling or a contact cap through an API is a decision with a cost attached, and
an endpoint that could do it is an endpoint an attacker — or a bad deploy —
could use to unlock the firewall. Loosening requires editing configuration and
restarting, which is deliberately more friction than a POST.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.clock import Clock
from app.db.models import Merchant
from app.deps import get_clock, get_db
from app.security.auth import Principal, require_api_token
from app.tools.audit import AuditChain

router = APIRouter(prefix="/api/v1", tags=["control"])


class ToggleRequest(BaseModel):
    model_config = {"extra": "forbid"}

    enabled: bool
    #: Recorded in the audit block. Optional, because requiring a reason to
    #: turn something OFF would add friction to the safe direction.
    reason: str | None = Field(default=None, max_length=200)


class PolicyRequest(BaseModel):
    """Tightenable bounds. Every field optional; omitted fields are unchanged."""

    model_config = {"extra": "forbid"}

    max_discount_pct: float | None = Field(default=None, ge=0, le=100)
    max_autonomous_amount_paise: int | None = Field(default=None, ge=0)
    max_contacts_48h: int | None = Field(default=None, ge=0)
    daily_action_budget: int | None = Field(default=None, ge=0)


@router.post("/autopilot/toggle", summary="The kill switch")
async def toggle_autopilot(
    body: ToggleRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    """Halt or resume autonomous action for every merchant on this instance.

    Idempotent: setting it to its current value is a no-op that still returns
    200, because a dashboard toggle double-clicked must not error.
    """
    merchants = (await session.execute(select(Merchant))).scalars().all()
    if not merchants:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No merchant configured. Run `python tasks.py seed` first.",
        )

    changed = [m.id for m in merchants if m.autopilot_enabled != body.enabled]
    for merchant in merchants:
        merchant.autopilot_enabled = body.enabled

    if changed:
        await AuditChain(clock).append(
            session,
            event_name="autopilot.enabled" if body.enabled else "autopilot.disabled",
            actor=principal.audit_actor,
            payload={
                "enabled": body.enabled,
                "merchants": changed,
                "reason": body.reason,
                "note": (
                    "halts NEW actions; work already committed to the outbox is "
                    "finished by the reconciler, because a link already created at "
                    "the provider is not un-created by us changing our mind"
                ),
            },
        )
    await session.commit()

    return {
        "autopilot_enabled": body.enabled,
        "merchants_changed": len(changed),
        "effective": "at the next stopping-rule evaluation (S-12, evaluated first)",
    }


@router.get("/autopilot", summary="Current kill-switch state")
async def autopilot_state(
    session: Annotated[AsyncSession, Depends(get_db)],
    _principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    merchants = (await session.execute(select(Merchant))).scalars().all()
    return {
        "merchants": [
            {"id": m.id, "name": m.business_name, "autopilot_enabled": m.autopilot_enabled}
            for m in merchants
        ],
        "all_enabled": all(m.autopilot_enabled for m in merchants) if merchants else None,
    }


@router.post("/policy", summary="Tighten a policy bound at runtime")
async def tighten_policy(
    body: PolicyRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    clock: Annotated[Clock, Depends(get_clock)],
    session: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    """Tighten a bound. Loosening is refused with 409.

    The asymmetry is the point. Tightening reduces what the agent may do and is
    safe to expose; loosening unlocks the firewall and must cost more than a
    POST. An endpoint that could raise the discount ceiling is an endpoint worth
    attacking.
    """
    proposed = body.model_dump(exclude_none=True)
    if not proposed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No bound supplied. Send at least one field.",
        )

    current = {
        "max_discount_pct": settings.max_discount_pct,
        "max_autonomous_amount_paise": settings.max_autonomous_amount_paise,
        "max_contacts_48h": settings.max_contacts_48h,
        "daily_action_budget": settings.daily_action_budget,
    }

    loosened = {
        key: (current[key], value) for key, value in proposed.items() if value > current[key]
    }
    if loosened:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This endpoint only tightens. "
                + "; ".join(
                    f"{k} would go from {was} to {now}" for k, (was, now) in loosened.items()
                )
                + ". Loosening a bound is a decision with a cost attached: edit the "
                "configuration and restart."
            ),
        )

    applied = {k: v for k, v in proposed.items() if v < current[k]}
    for key, value in applied.items():
        object.__setattr__(settings, key, value)

    if applied:
        await AuditChain(clock).append(
            session,
            event_name="policy.tightened",
            actor=principal.audit_actor,
            payload={
                "changes": {k: {"from": current[k], "to": v} for k, v in applied.items()},
                "note": "runtime tightening; effective at the next policy evaluation",
            },
        )
        await session.commit()

    return {
        "applied": applied,
        "unchanged": {k: v for k, v in proposed.items() if k not in applied},
        "effective_bounds": {**current, **applied},
    }
