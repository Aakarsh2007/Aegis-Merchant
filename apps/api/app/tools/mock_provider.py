"""Mock payment provider — the reason Judge Mode works with zero credentials.

**The one property this mock must not get wrong: `reference_id` uniqueness.**
The entire two-phase outbox design (§10.3) rests on the provider rejecting a
duplicate reference, because that rejection is what makes a post-crash retry
idempotent. A mock that quietly created a second payment link would let the
Phase 8 crash-recovery tests pass against a model of the world that is false,
and the bug would only appear against real Razorpay. So this mock enforces
uniqueness exactly as Razorpay does.

It also models what a real integration actually feels like — non-zero latency,
and a configurable fault injector for the chaos endpoint (§16.1) — because a
mock that always succeeds instantly tests nothing about the retry path.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any

from app.tools.provider import (
    DuplicateReference,
    PaymentDetails,
    PaymentLinkRequest,
    PaymentLinkResult,
    ProviderPermanent,
    ProviderRetryable,
)

__all__ = ["Fault", "MockRazorpayProvider"]


@dataclass
class Fault:
    """A fault to inject on the next N calls (workflow.md §16.1)."""

    kind: str  # TIMEOUT | SERVER_ERROR | BAD_REQUEST
    remaining: int = 1


@dataclass
class MockRazorpayProvider:
    """In-memory provider with Razorpay-shaped behaviour."""

    name: str = "mock"
    #: Simulated round-trip. Small but non-zero: a provider that returns
    #: instantly hides ordering bugs that only appear under real latency.
    latency_s: float = 0.02
    seed: int = 20260905

    _links_by_reference: dict[str, PaymentLinkResult] = field(default_factory=dict)
    _payments: dict[str, PaymentDetails] = field(default_factory=dict)
    _orders: dict[str, str] = field(default_factory=dict)
    _fault: Fault | None = None
    _rng: random.Random = field(init=False)
    call_count: int = 0

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    # -- test / chaos controls --------------------------------------------
    def inject_fault(self, kind: str, count: int = 1) -> None:
        self._fault = Fault(kind=kind, remaining=count)

    def clear_faults(self) -> None:
        self._fault = None

    def register_payment(self, payment: PaymentDetails) -> None:
        self._payments[payment.payment_id] = payment

    def register_order(self, order_id: str, status: str) -> None:
        self._orders[order_id] = status

    # -- internals ---------------------------------------------------------
    async def _tick(self) -> None:
        self.call_count += 1
        if self.latency_s:
            await asyncio.sleep(self.latency_s * self._rng.uniform(0.7, 1.4))

        fault = self._fault
        if fault is None or fault.remaining <= 0:
            return
        fault.remaining -= 1
        if fault.remaining <= 0:
            self._fault = None

        if fault.kind == "TIMEOUT":
            raise ProviderRetryable("mock: request timed out", status_code=None)
        if fault.kind == "SERVER_ERROR":
            raise ProviderRetryable("mock: 502 Bad Gateway", status_code=502)
        if fault.kind == "BAD_REQUEST":
            raise ProviderPermanent(
                "mock: 400 invalid request", status_code=400, provider_code="BAD_REQUEST_ERROR"
            )
        raise ValueError(f"unknown fault kind: {fault.kind}")

    # -- operations --------------------------------------------------------
    async def create_payment_link(self, request: PaymentLinkRequest) -> PaymentLinkResult:
        await self._tick()

        # The property the outbox depends on. Razorpay rejects a duplicate
        # reference_id per merchant; so do we, or the crash-recovery tests
        # would be validating a fiction.
        if request.reference_id in self._links_by_reference:
            raise DuplicateReference(
                request.reference_id,
                raw={
                    "error": {
                        "code": "BAD_REQUEST_ERROR",
                        "description": (
                            f"Payment link with reference_id {request.reference_id} already exists"
                        ),
                    }
                },
            )

        link_id = f"plink_mock{self._rng.randrange(16**12):012x}"
        result = PaymentLinkResult(
            link_id=link_id,
            short_url=f"https://rzp.io/i/mock{link_id[-8:]}",
            reference_id=request.reference_id,
            amount_paise=request.amount_paise,
            status="created",
            was_existing=False,
            raw={
                "id": link_id,
                "amount": request.amount_paise,
                "reference_id": request.reference_id,
                "notes": dict(request.notes),
            },
        )
        self._links_by_reference[request.reference_id] = result
        return result

    async def get_payment_link_by_reference(self, reference_id: str) -> PaymentLinkResult | None:
        await self._tick()
        existing = self._links_by_reference.get(reference_id)
        if existing is None:
            return None
        # Flagged as pre-existing so the audit trail can distinguish a fresh
        # creation from the recovery of an earlier attempt.
        return PaymentLinkResult(
            link_id=existing.link_id,
            short_url=existing.short_url,
            reference_id=existing.reference_id,
            amount_paise=existing.amount_paise,
            status=existing.status,
            was_existing=True,
            raw=existing.raw,
        )

    async def get_payment(self, payment_id: str) -> PaymentDetails:
        await self._tick()
        found = self._payments.get(payment_id)
        if found is None:
            raise ProviderPermanent(f"mock: payment {payment_id} not found", status_code=404)
        return found

    async def get_order_status(self, order_id: str) -> str:
        await self._tick()
        return self._orders.get(order_id, "created")

    async def health(self) -> bool:
        return True

    # -- introspection for tests ------------------------------------------
    @property
    def links(self) -> dict[str, PaymentLinkResult]:
        return dict(self._links_by_reference)

    def link_count(self) -> int:
        return len(self._links_by_reference)

    def snapshot(self) -> dict[str, Any]:
        return {
            "calls": self.call_count,
            "links": self.link_count(),
            "fault": self._fault.kind if self._fault else None,
        }
