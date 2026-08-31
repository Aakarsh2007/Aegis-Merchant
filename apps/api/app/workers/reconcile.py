"""Ask Razorpay directly whether our links were paid (§10.3).

A webhook is a *notification*, not a source of truth. It can be lost, delayed,
delivered to a URL that has since died, or — as happened here — rejected by our
own replay window while carrying a valid signature. A recovery system whose only
knowledge of a settlement arrives by webhook will eventually miss money it
actually recovered.

So this polls. For every reference we issued and have not yet settled, it asks
Razorpay for the link's current state and attributes anything paid. Production
needs this regardless of how good the webhook path is, and it has a second
benefit worth stating plainly: **it removes the tunnel from the demo's critical
path.** A judge can pay a link and run one command; no public URL, no webhook
registration, nothing to go wrong on stage.

Is a polled settlement as trustworthy as a webhook?
---------------------------------------------------

Yes, and arguably more so. Both come from Razorpay over TLS, authenticated with
our own API keys. A webhook proves Razorpay *sent* something and that the HMAC
matched; a direct read proves Razorpay *currently believes* the link is paid,
which is the fact we actually want. There is no signature to verify because
there is no third party in between — the authentication is the API key.

What is **not** the same is provenance, so the two are distinguishable in the
ledger. A webhook settlement records the Razorpay event id; a reconciled one
records the payment id, and the audit block names the source. Both are
RAZORPAY_VERIFIED, because in both cases Razorpay is the one asserting it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock
from app.db.enums import CaseStatus, OutboxStatus, RecoveryVerifier
from app.db.models import Outbox, RecoveryCase
from app.tools.audit import AuditChain
from app.tools.provider import PaymentLinkResult, PaymentProvider

log = logging.getLogger(__name__)

__all__ = ["ReconcileResult", "reconcile_outstanding"]


@dataclass
class ReconcileResult:
    """What the poll found."""

    checked: int = 0
    settled: int = 0
    recovered_paise: int = 0
    still_open: int = 0
    errors: int = 0
    details: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "=" * 68,
            "RECONCILIATION",
            "=" * 68,
            "",
            f"  references checked  {self.checked}",
            f"  newly settled       {self.settled}",
            f"  still unpaid        {self.still_open}",
            f"  errors              {self.errors}",
            "",
            f"  recovered           Rs {self.recovered_paise / 100:,.2f}   [RAZORPAY VERIFIED]",
        ]
        if self.details:
            lines += ["", "  detail:"]
            lines += [f"    {d}" for d in self.details]
        lines += [
            "",
            "  Asked Razorpay directly rather than waiting for a webhook. Both are",
            "  Razorpay asserting the payment; a poll needs no public URL, which is",
            "  why a lost or undeliverable webhook cannot cost us a real recovery.",
            "=" * 68,
        ]
        return "\n".join(lines)


def _payment_id(link: PaymentLinkResult) -> str | None:
    """The payment that settled this link, from Razorpay's own response.

    `payments` is a list because a partial-payment link can be settled by
    several. We take the first captured one: partial payments are disabled on
    every link we create (`accept_partial: false`), so there is at most one, and
    reaching for `[0]` blindly would break the day that changes.
    """
    payments = link.raw.get("payments")
    if isinstance(payments, list):
        for payment in payments:
            if isinstance(payment, dict) and payment.get("status") == "captured":
                identifier = payment.get("payment_id") or payment.get("id")
                if isinstance(identifier, str):
                    return identifier
    return None


async def _outstanding(session: AsyncSession) -> list[tuple[Outbox, RecoveryCase]]:
    """References we issued for cases that have not settled yet.

    Only `MONITORING` cases: a case we never acted on has no reference of ours
    to reconcile, and a case already RECOVERED must not be counted twice.
    """
    rows = (
        await session.execute(
            select(Outbox, RecoveryCase)
            .join(RecoveryCase, RecoveryCase.id == Outbox.case_id)
            .where(
                Outbox.status.in_((OutboxStatus.SENT, OutboxStatus.SENDING)),
                RecoveryCase.status == CaseStatus.MONITORING,
                RecoveryCase.recovery_verified_by.is_(None),
            )
        )
    ).all()
    return [(entry, case) for entry, case in rows]


async def reconcile_outstanding(
    factory: async_sessionmaker[AsyncSession],
    *,
    provider: PaymentProvider,
    clock: Clock,
    limit: int = 50,
) -> ReconcileResult:
    """Poll Razorpay for every outstanding reference and settle what is paid."""
    result = ReconcileResult()

    async with factory() as session:
        outstanding = await _outstanding(session)

    for entry, case in outstanding[:limit]:
        result.checked += 1
        try:
            link = await provider.get_payment_link_by_reference(entry.reference_id)
        except Exception as exc:
            result.errors += 1
            result.details.append(f"{case.id}: lookup failed - {str(exc)[:70]}")
            log.warning("reconcile lookup failed for %s: %s", entry.reference_id, exc)
            continue

        if link is None:
            result.errors += 1
            result.details.append(f"{case.id}: Razorpay does not know {entry.reference_id}")
            continue

        if link.status != "paid":
            result.still_open += 1
            continue

        # The amount comes from the CASE, never from the provider response --
        # the same rule the webhook path follows. A provider reporting a larger
        # figure must not inflate the metric.
        async with factory() as session:
            fresh = await session.get(RecoveryCase, case.id)
            if fresh is None or fresh.recovery_verified_by is not None:
                # Someone else settled it between our read and now -- most
                # likely the webhook arriving. Not an error: exactly one of the
                # two paths should win, and it does not matter which.
                result.still_open += 1
                continue

            fresh.status = CaseStatus.RECOVERED
            fresh.recovered_amount_paise = fresh.amount_paise
            # The PAYMENT id, not an event id: this settlement came from a
            # direct read, and the ledger should say so rather than imply a
            # webhook we never received.
            # Razorpay's LIST endpoint returns `payments: []` even for a paid
            # link -- the array is only populated on the single-link read. So
            # the payment id is usually unavailable here, and the link id is
            # recorded instead.
            #
            # That is sufficient provenance: `plink_...` is the Razorpay object
            # asserting `status: paid`, which is the fact being claimed. Losing
            # a real recovery over a field the list endpoint does not populate
            # would be the wrong trade.
            payment_id = _payment_id(link)
            fresh.recovery_verified_by = payment_id or link.link_id
            fresh.recovery_verified_via = RecoveryVerifier.API_RECONCILIATION
            fresh.resolved_at = clock.now_utc()

            await AuditChain(clock).append(
                session,
                event_name="recovery.verified",
                actor="reconciler",
                payload={
                    "case_id": fresh.id,
                    "reference_id": entry.reference_id,
                    "razorpay_link_id": link.link_id,
                    "payment_id": payment_id,
                    "amount_paise": fresh.amount_paise,
                    "provenance": "RAZORPAY_VERIFIED",
                    "source": "razorpay_api_reconciliation",
                    "verified_by": payment_id or link.link_id,
                    "verifier_kind": "payment_id" if payment_id else "payment_link_id",
                    "note": (
                        "read directly from Razorpay rather than received as a webhook; "
                        "authenticated by our API key, so no signature is involved. "
                        "The link id is recorded when the list endpoint does not "
                        "populate `payments`."
                    ),
                },
                case_id=fresh.id,
            )
            await session.commit()

        result.settled += 1
        result.recovered_paise += case.amount_paise
        result.details.append(
            f"{case.id}: paid - Rs {case.amount_paise / 100:,.2f} verified by "
            f"{_payment_id(link) or link.link_id}"
        )
        log.info(
            "reconciled: case=%s amount=%s payment=%s",
            case.id,
            case.amount_paise,
            _payment_id(link),
        )

    return result
