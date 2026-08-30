"""Fault injection (§19.2 ChaosPanel, §16).

A resilience claim nobody has watched fail is indistinguishable from an absent
one — the same argument as the audit tamper button. These endpoints let a judge
break the system on their own machine and watch it recover, rather than read a
paragraph asserting that it would.

Every fault here corresponds to something the design already claims to survive:

* ``provider_down`` — the payment API is unreachable. Actions should retry with
  backoff and dead-letter rather than be lost.
* ``provider_slow`` — worse than down, because it consumes the timeout budget
  and ties up the caller. Distinguished deliberately: a system tested only
  against hard failures often hangs on soft ones.
* ``provider_duplicate`` — the provider reports our ``reference_id`` as already
  used. This is the *success* path of the outbox design: it means a previous
  attempt actually succeeded, and the correct response is to record that, not
  to retry.
* ``llm_quota_exhausted`` — the free tier is spent. The agent must degrade to
  the deterministic classifier, not stop.

Refused outside development, gated on the environment rather than a header a
caller controls.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.security.auth import Principal, require_api_token

router = APIRouter(prefix="/api/v1/simulation", tags=["simulation"])

log = logging.getLogger(__name__)

Fault = Literal[
    "provider_down",
    "provider_slow",
    "provider_duplicate",
    "llm_quota_exhausted",
    "clear",
]

FAULT_EFFECTS: dict[str, str] = {
    "provider_down": (
        "Every provider call raises a connection error. Actions retry with "
        "jittered backoff and dead-letter after the budget; none are lost."
    ),
    "provider_slow": (
        "Every provider call takes longer than the timeout. Worse than down, "
        "because it consumes the budget rather than failing fast."
    ),
    "provider_duplicate": (
        "The provider reports our reference_id as already used. This is the "
        "outbox working: a previous attempt succeeded, so we record that "
        "outcome instead of charging twice."
    ),
    "llm_quota_exhausted": (
        "The free tier is spent. The agent falls back to the deterministic "
        "classifier, which scores 96.5% on the golden set at zero cost."
    ),
}


class _State:
    """Process-local fault state.

    A module-level singleton because the fault must be visible to the provider
    adapter without threading a parameter through every call site. Explicitly
    not persisted: a fault should not survive a restart, or a judge who injects
    one and walks away leaves the demo broken for the next person.
    """

    active: str | None = None


state = _State()


class InjectRequest(BaseModel):
    model_config = {"extra": "forbid"}

    fault: Fault
    #: Kept for symmetry with the UI; faults currently persist until cleared.
    note: str | None = Field(default=None, max_length=200)


def _guard(settings: Settings) -> None:
    if not settings.simulation_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fault injection is a development-only demonstration and is disabled here.",
        )


@router.get("/faults", summary="Available faults and the current one")
async def list_faults(
    settings: Annotated[Settings, Depends(get_settings)],
    _principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    return {
        "active": state.active,
        "enabled": settings.simulation_allowed,
        "faults": [{"fault": name, "effect": effect} for name, effect in FAULT_EFFECTS.items()],
    }


@router.post("/inject", summary="[dev only] Inject a fault, or clear the active one")
async def inject(
    body: InjectRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    _guard(settings)

    if body.fault == "clear":
        previous, state.active = state.active, None
        log.warning("fault cleared (was %s) by %s", previous, principal.audit_actor)
        return {"active": None, "cleared": previous}

    state.active = body.fault
    log.warning("fault injected: %s by %s", body.fault, principal.audit_actor)
    return {
        "active": state.active,
        "effect": FAULT_EFFECTS[body.fault],
        "expected_behaviour": (
            "The system degrades rather than losing work. Watch the outbox depth "
            "and the DLQ; nothing should disappear."
        ),
    }


@router.post("/batch", summary="[dev only] Run the corpus through the agent")
async def run_batch_endpoint(
    settings: Annotated[Settings, Depends(get_settings)],
    _principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    """Kick off a batch without a terminal.

    Deliberately synchronous and deliberately capped. The batch takes about
    two seconds against the committed cache, so streaming progress would be
    more machinery than the wait justifies -- and a fire-and-forget version
    would need a job table to report failures honestly.
    """
    _guard(settings)

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.agent.nodes import AgentDeps
    from app.core.clock import SystemClock
    from app.db.session import get_sessionmaker
    from app.llm.cache import CachedAdapter, ResponseCache
    from app.workers.batch import run_batch

    clock = SystemClock()
    factory: async_sessionmaker[Any] = get_sessionmaker()
    result = await run_batch(
        factory,
        clock=clock,
        deps=AgentDeps(
            clock=clock,
            adapter=CachedAdapter(
                cache=ResponseCache.load(), live=None, model=settings.gemini_model
            ),
            control_arm_fraction=settings.control_arm_fraction,
            experiment_key="revpilot_recovery_v1",
        ),
    )
    return {
        "cases": result.cases_created,
        "by_status": result.by_status,
        "treated": result.treated,
        "control": result.control,
        "simulated_recovered_paise": result.simulated_recovered_paise,
        "note": (
            "Every settled case carries a sim_evt_ verifier, so it reports as "
            "SIMULATED and can never reach the RAZORPAY VERIFIED tile."
        ),
    }


def active_fault() -> str | None:
    """Read by the provider adapter. Returns None in production, always."""
    if not get_settings().simulation_allowed:
        return None
    return state.active
