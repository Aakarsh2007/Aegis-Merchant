"""One button, one real Razorpay Test Mode recovery, end to end.

The distinction this endpoint exists to make, and it matters:

**Test Mode proves the execution path.** A real case, a real diagnosis, a real
policy decision, a real Razorpay payment link, a real signed webhook, real
attribution, a real audit block. Every component on the money path is the
production one.

**Test Mode does not prove that customers change their behaviour.** Paying our
own link demonstrates integration, not lift. The 210-case lift experiment stays
labelled SIMULATED, because it measures a response model we declared rather
than a population we observed.

Conflating those two would be the single most damaging thing this project could
do, so the response says which is which in the payload itself rather than
leaving it to a caption.

What this is not
----------------

It is not a shortcut to a bigger number. The link it creates is for ₹1 by
default — the amount is irrelevant to what is being demonstrated, and a large
figure would invite exactly the misreading above. Whatever is paid lands in the
RAZORPAY_VERIFIED column, which is correct: a signed webhook did prove it.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import run_case
from app.agent.nodes import AgentDeps
from app.agent.state import RecoveryState
from app.config import Settings, get_settings
from app.core.clock import Clock
from app.db.enums import ActionType, CaseStatus, OutboxStatus, Playbook
from app.db.ids import idempotency_hash, new_id
from app.db.models import Consent, Customer, Merchant, Outbox, RecoveryCase
from app.deps import get_clock, get_db
from app.llm.cache import CachedAdapter, ResponseCache
from app.security.auth import Principal, require_api_token
from app.tools.audit import AuditChain
from app.tools.razorpay_client import RazorpayProvider

router = APIRouter(prefix="/api/v1/testmode", tags=["test mode"])

log = logging.getLogger(__name__)

#: Deliberately trivial. The amount is irrelevant to what is being
#: demonstrated, and a large figure would invite the misreading this endpoint
#: exists to prevent.
DEFAULT_AMOUNT_PAISE = 100


class TestRecoveryRequest(BaseModel):
    model_config = {"extra": "forbid"}

    amount_paise: int = Field(default=DEFAULT_AMOUNT_PAISE, ge=100, le=100_000)


@router.get("/status", summary="Whether a real Test Mode run is possible")
async def testmode_status(
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_db)],
    _principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    """What is configured, and what each missing piece costs.

    A button that fails with "something went wrong" is worse than one that is
    disabled with a reason, so the UI reads this first.
    """
    verified = (
        (
            await session.execute(
                select(RecoveryCase).where(
                    RecoveryCase.recovery_verified_by.is_not(None),
                    ~RecoveryCase.recovery_verified_by.startswith("sim_evt_"),
                )
            )
        )
        .scalars()
        .all()
    )

    return {
        "razorpay_configured": settings.razorpay_live,
        "webhook_secret_configured": bool(settings.razorpay_webhook_secret),
        "ready": settings.razorpay_live,
        "missing": [
            *([] if settings.razorpay_live else ["RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET"]),
            *(
                []
                if settings.razorpay_webhook_secret
                else ["RAZORPAY_WEBHOOK_SECRET (needed for the webhook half)"]
            ),
        ],
        "webhook_note": (
            "Creating the link needs only the API keys. Razorpay can only DELIVER "
            "the webhook to a public HTTPS URL -- run `python tasks.py tunnel` and "
            "register it (docs/webhooks.md)."
        ),
        "verified_recoveries": [
            {
                "case_id": c.id,
                "amount_paise": c.recovered_amount_paise,
                "verified_by": c.recovery_verified_by,
            }
            for c in verified
        ],
        "verified_count": len(verified),
    }


@router.post("/reconcile", summary="Ask Razorpay which of our links were paid")
async def reconcile(
    session: Annotated[AsyncSession, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
    _principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    """Settle outstanding references by reading Razorpay directly.

    This is what makes the demo tunnel-free. A webhook is a notification and
    can be lost, delayed, or sent to a URL that has since died; a direct read
    asks Razorpay what it currently believes, which is the fact we want.
    """
    if not settings.razorpay_live:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Razorpay credentials, so there is nothing to reconcile against.",
        )

    from app.db.session import get_sessionmaker
    from app.deps import get_provider
    from app.workers.reconcile import reconcile_outstanding

    result = await reconcile_outstanding(
        get_sessionmaker(), provider=get_provider(settings), clock=clock
    )
    return {
        "checked": result.checked,
        "settled": result.settled,
        "recovered_paise": result.recovered_paise,
        "still_open": result.still_open,
        "errors": result.errors,
        "details": result.details,
        "note": (
            "Read directly from Razorpay, authenticated by our API key. Both this "
            "and the webhook path are Razorpay asserting the payment; this one "
            "needs no public URL, so a lost webhook cannot cost us a recovery."
        ),
    }


@router.post("/recover", summary="Create one real Test Mode recovery, end to end")
async def test_recovery(
    body: TestRecoveryRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
    principal: Annotated[Principal, Depends(require_api_token)],
) -> dict[str, Any]:
    """Detect → diagnose → policy → real Razorpay link, then hand back the URL.

    Everything up to the link is the production path, including the policy
    firewall and the capability token. The link is a genuine Razorpay Test Mode
    payment link with a `reference_id` **we** issued, committed to the outbox
    *before* the provider call — so paying it produces a webhook that
    attribution can match, and a crash between the two is recoverable.
    """
    if not settings.simulation_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The Test Mode demonstration is disabled outside development.",
        )
    if not settings.razorpay_live:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No Razorpay credentials. Set RAZORPAY_KEY_ID and "
                "RAZORPAY_KEY_SECRET in .env (Test Mode keys are free)."
            ),
        )

    now = clock.now_utc()

    merchant = (await session.execute(select(Merchant))).scalars().first()
    customer = (await session.execute(select(Customer))).scalars().first()
    if merchant is None or customer is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No seeded merchant or customer. Run `python tasks.py seed`.",
        )
    consent = await session.get(Consent, customer.id)

    # 1. A real case, in the real table.
    #
    # The id comes from the INJECTED clock, not `time.time()`. The lint rule in
    # tests/test_no_wall_clock_reads.py caught the latter, correctly: this
    # module had two notions of "now" -- one from the clock and one from the
    # system -- and a test that froze the clock would have produced a
    # non-deterministic id. That is precisely the INC-023 shape.
    case_id = f"RC-TM{int(now.timestamp()) % 100000:05d}"
    state = RecoveryState(
        case_id=case_id,
        merchant_id=merchant.id,
        customer_id=customer.id,
        playbook=Playbook.PAYMENT_FAILURE,
        amount_paise=body.amount_paise,
        order_id=f"order_tm_{case_id.lower()}",
        error_source="customer",
        error_step="payment_authentication",
        error_reason="payment_cancelled",
        method="card",
        issuer="HDFC",
        customer_first_name=customer.first_name,
        consent_transactional=consent.transactional if consent else True,
        consent_marketing=consent.marketing if consent else False,
        consent_dnd=consent.dnd_registered if consent else False,
        consent_opted_out=consent.opted_out if consent else False,
        autopilot_enabled=merchant.autopilot_enabled,
        order_status="created",
        window_expires_at=now + timedelta(hours=24),
    )

    # 2. The real agent, the real firewall.
    deps = AgentDeps(
        clock=clock,
        adapter=CachedAdapter(cache=ResponseCache.load(), live=None, model=settings.gemini_model),
        control_arm_fraction=0.0,  # a demo case must be treated, not held out
        experiment_key="revpilot_testmode",
    )
    final = await run_case(state, deps)

    if final.status is not CaseStatus.MONITORING or final.reference_id is None:
        # The firewall refused, which is a legitimate outcome and is reported as
        # one rather than worked around.
        return {
            "stopped_before_execution": True,
            "case_id": case_id,
            "status": final.status.value,
            "stopping_rule": (
                final.stopping_rule_fired.value if final.stopping_rule_fired else None
            ),
            "block_reasons": list(final.policy_block_reasons),
            "note": "The policy firewall did not authorise this action. Nothing was sent.",
        }

    session.add(
        RecoveryCase(
            id=case_id,
            merchant_id=merchant.id,
            customer_id=customer.id,
            playbook=Playbook.PAYMENT_FAILURE,
            status=CaseStatus.MONITORING,
            order_id=state.order_id,
            amount_paise=body.amount_paise,
            diagnosis_category=final.diagnosis.category if final.diagnosis else None,
            diagnosis_source=final.diagnosis.source if final.diagnosis else None,
            confidence=final.diagnosis.confidence if final.diagnosis else None,
            attempt_no=1,
            idempotency_hash=idempotency_hash(merchant.id, case_id, "PAYMENT_FAILURE"),
            is_demo=True,
            window_expires_at=state.window_expires_at,
            created_at=now,
        )
    )
    await session.flush()

    # 3. The reference is committed BEFORE the provider call. That ordering is
    #    the entire exactly-once guarantee: a crash after this point is
    #    recoverable, because a retry reuses the key and Razorpay rejects the
    #    duplicate.
    outbox = Outbox(
        id=new_id("outbox"),
        case_id=case_id,
        action_type=ActionType.CREATE_PAYMENT_LINK,
        reference_id=final.reference_id,
        payload_json="{}",
        status=OutboxStatus.SENDING,
        attempt=1,
        next_attempt_at=now,
        created_at=now,
    )
    session.add(outbox)
    await session.commit()

    # 4. The real Razorpay call.
    provider = RazorpayProvider(
        settings.razorpay_key_id, settings.razorpay_key_secret, timeout_s=25.0
    )
    try:
        link = await provider._request(
            "POST",
            "/payment_links",
            json={
                "amount": body.amount_paise,
                "currency": "INR",
                "accept_partial": False,
                "reference_id": final.reference_id,
                "description": f"RevPilot Test Mode recovery - {case_id}",
                "customer": {
                    "name": customer.first_name,
                    "contact": "+919000000000",
                    "email": "testmode@example.com",
                },
                "notify": {"sms": False, "email": False},
                "reminder_enable": False,
                "expire_by": int(now.timestamp()) + 6 * 24 * 3600,
            },
        )
    except Exception as exc:
        outbox.status = OutboxStatus.PENDING
        outbox.last_error = str(exc)[:200]
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Razorpay refused the link: {str(exc)[:200]}",
        ) from exc

    outbox.status = OutboxStatus.SENT
    outbox.provider_ref = str(link.get("id"))
    await AuditChain(clock).append(
        session,
        event_name="action.dispatched",
        actor=principal.audit_actor,
        payload={
            "case_id": case_id,
            "reference_id": final.reference_id,
            "razorpay_link_id": link.get("id"),
            "amount_paise": body.amount_paise,
            "mode": "razorpay_test_mode",
        },
        case_id=case_id,
    )
    await session.commit()

    return {
        "stopped_before_execution": False,
        "case_id": case_id,
        "diagnosis": final.diagnosis.category.value if final.diagnosis else None,
        "diagnosis_source": final.diagnosis.source.value if final.diagnosis else None,
        "strategy": final.proposal.strategy.value if final.proposal else None,
        "reference_id": final.reference_id,
        "razorpay_link_id": link.get("id"),
        "pay_url": link.get("short_url"),
        "amount_paise": body.amount_paise,
        "next_step": (
            "Pay the link with card 4111 1111 1111 1111, any future expiry, any CVV, "
            "then click Success. Razorpay will POST payment_link.paid to the webhook "
            "URL; the signature is verified, the reference is matched, and this case "
            "moves to RAZORPAY_VERIFIED."
        ),
        "what_this_proves": (
            "The execution path is real: agent, policy firewall, capability token, "
            "Razorpay link, signed webhook, attribution, audit block."
        ),
        "what_this_does_not_prove": (
            "That customers change their behaviour because of the agent. Paying our "
            "own link demonstrates integration, not lift -- which is why the "
            "210-case experiment stays labelled SIMULATED."
        ),
    }
