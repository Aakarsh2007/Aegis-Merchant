"""Normalise Razorpay webhooks into one canonical event.

Razorpay nests the interesting entity differently per event type
(``payload.payment.entity``, ``payload.order.entity``, ``payload.invoice.entity``,
``payload.subscription.entity``, ``payload.payment_link.entity``). Doing that
unwrapping at every call site would spread provider-shaped assumptions through
the whole agent; doing it once here means the state machine only ever sees a
:class:`RevenueRiskEvent`.

Everything in this module is a pure function over a parsed payload — no I/O, no
clock, no database — so the whole surface is testable from a fixture file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.db.enums import Playbook

__all__ = [
    "EVENT_PLAYBOOKS",
    "RESOLUTION_EVENTS",
    "RISK_EVENTS",
    "RevenueRiskEvent",
    "extract_event_ts",
    "normalise",
    "route_to_playbook",
]

#: Events that may open a recovery case.
RISK_EVENTS: dict[str, Playbook] = {
    "payment.failed": Playbook.PAYMENT_FAILURE,
    "order.created": Playbook.CHECKOUT_ABANDON,
    "invoice.expired": Playbook.RECEIVABLE,
    "subscription.pending": Playbook.SUBSCRIPTION,
    "subscription.halted": Playbook.SUBSCRIPTION,
}

#: Events that *close* exposure. These never open a case; they resolve one, or
#: they verify a recovery. Handling them as risk would be the classic
#: double-count bug: counting the payment that proves a recovery as a new
#: opportunity to recover.
RESOLUTION_EVENTS: frozenset[str] = frozenset(
    {
        "payment.captured",
        "order.paid",
        "payment_link.paid",
        "invoice.paid",
        "subscription.charged",
    }
)

EVENT_PLAYBOOKS = dict(RISK_EVENTS)

#: Where the meaningful entity lives, per event family.
_ENTITY_KEYS = ("payment", "order", "invoice", "subscription", "payment_link", "refund")

#: Fallback order only. The authoritative entity is chosen from the EVENT NAME
#: (INC-025).
#:
#: A real `payment_link.paid` from Razorpay carries THREE entities --
#: `payment_link`, `order` and `payment` -- and only `payment_link` has the
#: `reference_id`. Picking by a fixed priority list put `payment` first, so the
#: reference was invisible and a genuine recovery could not be attributed to
#: the action that caused it.
#:
#: Our own fixture had a single entity, which is why every test passed.


@dataclass(frozen=True)
class RevenueRiskEvent:
    """One canonical event, whatever Razorpay called it."""

    event_id: str
    event_type: str
    event_ts: datetime | None

    playbook: Playbook | None = None
    is_resolution: bool = False

    order_id: str | None = None
    payment_id: str | None = None
    invoice_id: str | None = None
    subscription_id: str | None = None
    payment_link_id: str | None = None
    #: Our idempotency key, echoed back by the provider. The attribution
    #: matcher requires an exact match on this before counting any recovery.
    reference_id: str | None = None

    amount_paise: int | None = None
    currency: str = "INR"
    status: str | None = None
    method: str | None = None
    issuer: str | None = None

    # Razorpay's own failure telemetry -- read, never inferred (§4.2 item 1).
    error_code: str | None = None
    error_source: str | None = None
    error_step: str | None = None
    error_reason: str | None = None

    contact: str | None = None
    email: str | None = None
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        """Whether this event can open a case."""
        return self.playbook is not None


def extract_event_ts(payload: dict[str, Any]) -> datetime | None:
    """Read the top-level ``created_at`` (unix seconds) as aware UTC.

    Returns ``None`` rather than guessing when absent or malformed: the replay
    window fails closed on a missing timestamp, and inventing one here would
    quietly disable that defence.
    """
    raw = payload.get("created_at")
    if raw is None:
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _first_entity(payload: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """Unwrap the entity the EVENT is about, not merely the first one present.

    `payment_link.paid` is about the payment link. Razorpay includes the order
    and the payment alongside it as context, and those do not carry the
    `reference_id` — so choosing by a fixed priority list silently discarded
    the attribution key (INC-025).

    The event name is the authority: everything before the first dot names the
    entity. The fixed list remains only as a fallback for event names we do not
    recognise, because a webhook Razorpay adds next year must still parse.
    """
    container = payload.get("payload")
    if not isinstance(container, dict):
        return None, {}

    def _unwrap(key: str) -> dict[str, Any] | None:
        wrapper = container.get(key)
        if isinstance(wrapper, dict):
            entity = wrapper.get("entity")
            if isinstance(entity, dict):
                return entity
        return None

    preferred = str(payload.get("event", "")).split(".", 1)[0]
    if preferred:
        entity = _unwrap(preferred)
        if entity is not None:
            return preferred, entity

    for key in _ENTITY_KEYS:
        entity = _unwrap(key)
        if entity is not None:
            return key, entity
    return None, {}


def _find_reference(payload: dict[str, Any]) -> str | None:
    """Search every entity for a reference we issued.

    Belt and braces on top of the entity choice above. The reference is *the*
    attribution key — condition 3 of six, the line between attribution and
    coincidence — so losing it costs a recovery that actually happened. Cheap
    to look everywhere; expensive to look in one place and be wrong.
    """
    container = payload.get("payload")
    if not isinstance(container, dict):
        return None
    for wrapper in container.values():
        if not isinstance(wrapper, dict):
            continue
        entity = wrapper.get("entity")
        if not isinstance(entity, dict):
            continue
        candidate = entity.get("reference_id")
        if not candidate:
            notes = entity.get("notes")
            if isinstance(notes, dict):
                candidate = notes.get("reference_id")
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return None


def route_to_playbook(event_type: str) -> Playbook | None:
    return RISK_EVENTS.get(event_type)


def normalise(payload: dict[str, Any], *, event_id: str) -> RevenueRiskEvent:
    """Turn a Razorpay webhook payload into a :class:`RevenueRiskEvent`.

    Tolerant by design: an unknown event type produces a valid event with no
    playbook rather than an exception. Razorpay can add event types at any
    time, and a 500 on an unrecognised webhook would make Razorpay retry it
    forever.
    """
    event_type = str(payload.get("event", ""))
    kind, entity = _first_entity(payload)

    # `notes` is the round-trip channel we set on the payment link, so it is
    # where the reference tends to survive across entity types. Bound once and
    # narrowed once: calling .get() twice defeats type narrowing, and a
    # non-dict `notes` from a malformed payload would then crash ingestion.
    raw_notes = entity.get("notes")
    notes: dict[str, Any] = raw_notes if isinstance(raw_notes, dict) else {}

    # Chosen entity first, then every other entity in the payload. Razorpay
    # puts the reference on the entity the event is about, and a multi-entity
    # payload is the normal case rather than the exception.
    reference_id = (
        entity.get("reference_id") or notes.get("reference_id") or _find_reference(payload)
    )

    # A payment_link.paid event carries its own id under `id`; a payment that
    # settled a link carries the link under `payment_link_id`.
    payment_link_id = entity.get("payment_link_id")
    if kind == "payment_link":
        payment_link_id = entity.get("id")

    amount = entity.get("amount")
    try:
        amount_paise = int(amount) if amount is not None else None
    except (TypeError, ValueError):
        amount_paise = None

    issuer = entity.get("bank") or entity.get("wallet") or entity.get("vpa")

    return RevenueRiskEvent(
        event_id=event_id,
        event_type=event_type,
        event_ts=extract_event_ts(payload),
        playbook=route_to_playbook(event_type),
        is_resolution=event_type in RESOLUTION_EVENTS,
        order_id=entity.get("order_id") or (entity.get("id") if kind == "order" else None),
        payment_id=entity.get("id") if kind == "payment" else entity.get("payment_id"),
        invoice_id=entity.get("invoice_id") or (entity.get("id") if kind == "invoice" else None),
        subscription_id=(
            entity.get("subscription_id") or (entity.get("id") if kind == "subscription" else None)
        ),
        payment_link_id=payment_link_id,
        reference_id=reference_id,
        amount_paise=amount_paise,
        currency=str(entity.get("currency", "INR")),
        status=entity.get("status"),
        method=entity.get("method"),
        issuer=str(issuer) if issuer else None,
        error_code=entity.get("error_code"),
        error_source=entity.get("error_source"),
        error_step=entity.get("error_step"),
        error_reason=entity.get("error_reason"),
        contact=entity.get("contact"),
        email=entity.get("email"),
        notes=notes,
    )
