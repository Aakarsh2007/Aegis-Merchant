"""Payment provider boundary.

One protocol, two implementations (real Razorpay Test Mode, and a mock), so
the entire system runs with zero credentials in Judge Mode (workflow.md §22)
and the agent never knows which is behind it.

The error taxonomy is the load-bearing part. The outbox drainer needs to know
whether a failure is worth retrying, and that decision cannot be made by
string-matching an exception message:

* :class:`ProviderRetryable` — timeout, 429, 5xx. Back off and try again with
  the *same* ``reference_id``.
* :class:`ProviderPermanent` — 400/401/404. Retrying will fail identically;
  straight to the dead-letter queue with the provider's own error preserved.
* :class:`DuplicateReference` — we already created this. **Not an error.**
  It is the provider enforcing our idempotency key for us, which is exactly
  what the two-phase outbox is designed to lean on (§10.3). The caller fetches
  the existing object and continues.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

__all__ = [
    "DuplicateReference",
    "PaymentDetails",
    "PaymentLinkRequest",
    "PaymentLinkResult",
    "PaymentProvider",
    "ProviderError",
    "ProviderPermanent",
    "ProviderRetryable",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class ProviderError(Exception):
    """Base class. Carries the provider's own response for the audit trail."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        provider_code: str | None = None,
        raw: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider_code = provider_code
        self.raw = raw or {}


class ProviderRetryable(ProviderError):
    """Transient. Retry with the same reference_id."""


class ProviderPermanent(ProviderError):
    """Terminal. Retrying produces the identical failure -> dead-letter queue."""


class DuplicateReference(ProviderError):
    """The reference_id already exists at the provider.

    This is the happy path of a retry, not a failure: it means a previous
    attempt got further than we recorded, and the provider just prevented us
    from creating a second payment link for the same cart.
    """

    def __init__(self, reference_id: str, *, raw: dict[str, Any] | None = None) -> None:
        super().__init__(f"reference_id already exists: {reference_id}", raw=raw)
        self.reference_id = reference_id


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PaymentLinkRequest:
    """A recovery payment link.

    ``reference_id`` is generated and committed to the outbox *before* this
    request is made. Razorpay enforces its uniqueness per merchant, which is
    what makes a retry after a crash idempotent (§10.3), and it is the exact
    string the attribution matcher later looks for in the confirming webhook —
    so recovery is never attributed by guesswork.
    """

    amount_paise: int
    reference_id: str
    description: str
    customer_name: str
    customer_contact: str | None = None
    customer_email: str | None = None
    expire_by: datetime | None = None
    #: Round-trips through the provider and comes back on the webhook.
    notes: dict[str, str] = field(default_factory=dict)
    currency: str = "INR"

    def __post_init__(self) -> None:
        if self.amount_paise <= 0:
            raise ValueError(f"amount_paise must be positive, got {self.amount_paise}")
        if not self.reference_id:
            raise ValueError("reference_id is required: it is the idempotency key")


@dataclass(frozen=True)
class PaymentLinkResult:
    link_id: str
    short_url: str
    reference_id: str
    amount_paise: int
    status: str
    #: True when this came back from a duplicate-reference lookup rather than
    #: a fresh creation. Recorded so the audit trail distinguishes "created"
    #: from "recovered an earlier attempt".
    was_existing: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentDetails:
    """A payment as the provider reports it.

    The four ``error_*`` fields are the deterministic diagnostic substrate.
    Razorpay states whose fault a failure was; asking a model to infer it
    would be a hallucination surface with no upside (§4.2 item 1).
    """

    payment_id: str
    order_id: str | None
    amount_paise: int
    status: str
    method: str | None = None
    issuer: str | None = None
    error_code: str | None = None
    error_source: str | None = None
    error_step: str | None = None
    error_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------
class PaymentProvider(Protocol):
    """What the agent is allowed to ask a payment provider to do.

    Deliberately small. There is no generic ``request()`` escape hatch, so the
    set of things that can happen to a merchant's money is enumerable by
    reading this class.
    """

    name: str

    async def create_payment_link(self, request: PaymentLinkRequest) -> PaymentLinkResult:
        """Create a link. Raises :class:`DuplicateReference` if it exists."""
        ...

    async def get_payment_link_by_reference(self, reference_id: str) -> PaymentLinkResult | None:
        """Recovery path after a duplicate rejection."""
        ...

    async def get_payment(self, payment_id: str) -> PaymentDetails:
        """Read failure telemetry for a payment."""
        ...

    async def get_order_status(self, order_id: str) -> str:
        """Read an order's status.

        Called immediately before every action so stopping rule S-01 can abort
        on an order that has already been paid organically (§8.1).
        """
        ...

    async def health(self) -> bool:
        """Cheap reachability check for /api/v1/health/deep."""
        ...
