"""Domain enumerations.

All stored as VARCHAR with a CHECK constraint rather than a native DB enum:
SQLite has no ENUM type, and readable strings mean a judge inspecting
``revpilot.seed.db`` in any SQLite browser can understand the data without our
code (workflow.md §12.1).

``StrEnum`` (Python 3.11+) so a member *is* its value: ``str(ErrorSource.BANK)``
is ``"bank"``, which serialises straight to JSON and to a SQL literal without a
conversion step that could go wrong.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "TERMINAL_STATUSES",
    "ActionType",
    "ApprovalStatus",
    "AttemptKind",
    "CaseStatus",
    "Channel",
    "DLQStatus",
    "DiagnosisSource",
    "ErrorSource",
    "ErrorStep",
    "EscalationRung",
    "ExperimentArm",
    "FailureCategory",
    "LLMSource",
    "LLMTask",
    "MessageClass",
    "OutboxStatus",
    "PaymentMethod",
    "PaymentStatus",
    "Playbook",
    "PolicyVerdict",
    "PromiseStatus",
    "RecoveryStrategy",
    "StoppingRule",
]


class Playbook(StrEnum):
    """The four revenue leaks (workflow.md §5)."""

    PAYMENT_FAILURE = "PAYMENT_FAILURE"
    CHECKOUT_ABANDON = "CHECKOUT_ABANDON"
    RECEIVABLE = "RECEIVABLE"
    SUBSCRIPTION = "SUBSCRIPTION"


class CaseStatus(StrEnum):
    """Case lifecycle (workflow.md §6.2). Terminal states have no outgoing edge."""

    DETECTED = "DETECTED"
    TRIAGED = "TRIAGED"
    DIAGNOSING = "DIAGNOSING"
    STRATEGY_FORMED = "STRATEGY_FORMED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    MONITORING = "MONITORING"
    # --- terminal ---
    RECOVERED = "RECOVERED"
    RESOLVED_ORGANIC = "RESOLVED_ORGANIC"
    #: Held as the control arm and never acted on, and it did not settle.
    #:
    #: Added after INC-018, where a control-arm block was recorded as
    #: RESOLVED_ORGANIC to mean "the holdout is doing its job" while
    #: attribution read the same value as "the customer paid without us". The
    #: two meanings put control conversion at 89.7% and the measured lift at
    #: -60.5%. RESOLVED_ORGANIC now means one thing -- money arrived, and not
    #: because of us -- and this means the other.
    OBSERVED_NO_ACTION = "OBSERVED_NO_ACTION"
    EXPIRED = "EXPIRED"
    SUPPRESSED = "SUPPRESSED"
    REJECTED = "REJECTED"
    FAILED_PERMANENT = "FAILED_PERMANENT"


#: A case in one of these may never transition again. Enforced by the single
#: ``transition()`` helper, so the invariant lives in exactly one place.
TERMINAL_STATUSES: frozenset[CaseStatus] = frozenset(
    {
        CaseStatus.RECOVERED,
        CaseStatus.RESOLVED_ORGANIC,
        CaseStatus.OBSERVED_NO_ACTION,
        CaseStatus.EXPIRED,
        CaseStatus.SUPPRESSED,
        CaseStatus.REJECTED,
        CaseStatus.FAILED_PERMANENT,
    }
)


class ExperimentArm(StrEnum):
    """CONTROL cases receive no intervention at all, so that recovery can be
    reported as incremental lift rather than asserted (workflow.md §14.2)."""

    TREATMENT = "TREATMENT"
    CONTROL = "CONTROL"


# ---------------------------------------------------------------------------
# Razorpay-supplied failure telemetry.
#
# These are read from the API, never inferred by a model — §4.2 item 1. Being
# told whose fault a failure was deleted roughly 40% of the planned LLM calls.
# ---------------------------------------------------------------------------
class ErrorSource(StrEnum):
    CUSTOMER = "customer"
    BUSINESS = "business"
    BANK = "bank"
    GATEWAY = "gateway"
    INTERNAL = "internal"
    NBFC = "nbfc"


class ErrorStep(StrEnum):
    PAYMENT_INITIATION = "payment_initiation"
    PAYMENT_AUTHENTICATION = "payment_authentication"
    PAYMENT_AUTHORIZATION = "payment_authorization"
    PAYMENT_RESPONSE = "payment_response"


class PaymentMethod(StrEnum):
    UPI = "upi"
    CARD = "card"
    NETBANKING = "netbanking"
    WALLET = "wallet"
    EMANDATE = "emandate"


class PaymentStatus(StrEnum):
    CREATED = "created"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    ABANDONED = "abandoned"


class AttemptKind(StrEnum):
    """What produced this row in the transaction ledger."""

    CHECKOUT = "checkout"
    INVOICE = "invoice"
    SUBSCRIPTION = "subscription"
    RECOVERY_LINK = "recovery_link"


# ---------------------------------------------------------------------------
# Cognitive outputs — advisory only. Never authoritative.
# ---------------------------------------------------------------------------
class FailureCategory(StrEnum):
    RAIL_FAULT = "RAIL_FAULT"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    AUTHENTICATION_ABANDONED = "AUTHENTICATION_ABANDONED"
    MANDATE_INVALID = "MANDATE_INVALID"
    PRICE_RESISTANCE = "PRICE_RESISTANCE"
    INTENT_DECAY = "INTENT_DECAY"
    RISK_BLOCKED = "RISK_BLOCKED"
    UNKNOWN = "UNKNOWN"


class DiagnosisSource(StrEnum):
    """Whether a diagnosis came from the model or the rule table. Surfaced in
    the UI: a deterministic fallback is never presented as model reasoning."""

    LLM = "LLM"
    DETERMINISTIC_FALLBACK = "DETERMINISTIC_FALLBACK"


class RecoveryStrategy(StrEnum):
    FRESH_LINK_SAME_RAIL = "FRESH_LINK_SAME_RAIL"
    FRESH_LINK_ALT_RAIL = "FRESH_LINK_ALT_RAIL"
    INCENTIVISED_LINK = "INCENTIVISED_LINK"
    INVOICE_REMINDER = "INVOICE_REMINDER"
    MANDATE_RETRY = "MANDATE_RETRY"
    MANDATE_REAUTH = "MANDATE_REAUTH"
    STATIC_UPI_QR = "STATIC_UPI_QR"
    NO_ACTION = "NO_ACTION"


# ---------------------------------------------------------------------------
# Communication
# ---------------------------------------------------------------------------
class Channel(StrEnum):
    WHATSAPP = "WHATSAPP"
    SMS = "SMS"
    EMAIL = "EMAIL"
    NONE = "NONE"


class MessageClass(StrEnum):
    """The most consequential compliance decision in the system (§9.2).

    A payment-retry link tied to an existing transaction is utility.
    An unsolicited discount offer is marketing and requires opt-in. Because
    marketing consent is often absent, the agent's default and preferred
    action ends up being the zero-discount transactional link — the compliance
    constraint and the margin constraint point the same way.
    """

    TRANSACTIONAL = "TRANSACTIONAL"
    MARKETING = "MARKETING"


class EscalationRung(StrEnum):
    """Two orthogonal ladders (§8.3): A* is authority, B* is contact."""

    A0_AUTONOMOUS = "A0_AUTONOMOUS"
    A1_FLAGGED = "A1_FLAGGED"
    A2_APPROVAL = "A2_APPROVAL"
    A3_APPROVAL_DUAL = "A3_APPROVAL_DUAL"
    B1_FIRST_TOUCH = "B1_FIRST_TOUCH"
    B2_SECOND_TOUCH = "B2_SECOND_TOUCH"
    B3_FORMAL_EMAIL = "B3_FORMAL_EMAIL"
    B4_HUMAN_TASK = "B4_HUMAN_TASK"


# ---------------------------------------------------------------------------
# Policy & stopping rules
# ---------------------------------------------------------------------------
class PolicyVerdict(StrEnum):
    PASSED = "PASSED"
    ESCALATE_HITL = "ESCALATE_HITL"
    BLOCKED = "BLOCKED"


class StoppingRule(StrEnum):
    """The twelve stopping rules (workflow.md §8.1), by ID.

    Named in the enum so that a firing is recorded as data and can be counted
    per rule on the dashboard, rather than buried in a log string.
    """

    S01_ALREADY_RESOLVED = "S-01"
    S02_ATTEMPT_BUDGET = "S-02"
    S03_DISCOUNT_ATTEMPT_BUDGET = "S-03"
    S04_CONTACT_CAP_24H = "S-04"
    S05_CONTACT_CAP_48H = "S-05"
    S06_RECOVERY_WINDOW = "S-06"
    S07_OPT_OUT = "S-07"
    S08_CONSENT_CLASS = "S-08"
    S09_QUIET_HOURS = "S-09"
    S10_PROMISE_FREEZE = "S-10"
    S11_MERCHANT_BUDGET = "S-11"
    S12_KILL_SWITCH = "S-12"


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
class ActionType(StrEnum):
    CREATE_PAYMENT_LINK = "CREATE_PAYMENT_LINK"
    NOTIFY_INVOICE = "NOTIFY_INVOICE"
    DISPATCH_MESSAGE = "DISPATCH_MESSAGE"
    MANDATE_PRE_DEBIT_NOTICE = "MANDATE_PRE_DEBIT_NOTICE"
    MANDATE_RETRY = "MANDATE_RETRY"
    CREATE_HUMAN_TASK = "CREATE_HUMAN_TASK"


class OutboxStatus(StrEnum):
    """Two-phase execution states (§10.3). The reference_id is committed while
    PENDING, before the provider call, which is what makes a retry idempotent."""

    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    DEAD = "DEAD"


class DLQStatus(StrEnum):
    OPEN = "OPEN"
    REPLAYED = "REPLAYED"
    ACKNOWLEDGED = "ACKNOWLEDGED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class PromiseStatus(StrEnum):
    ACTIVE = "ACTIVE"
    HONOURED = "HONOURED"
    BROKEN = "BROKEN"


# ---------------------------------------------------------------------------
# LLM accounting
# ---------------------------------------------------------------------------
class LLMTask(StrEnum):
    DIAGNOSE = "DIAGNOSE"
    STRATEGISE = "STRATEGISE"
    COMPOSE_MESSAGE = "COMPOSE_MESSAGE"
    EXTRACT_PROMISE = "EXTRACT_PROMISE"
    DAILY_BRIEFING = "DAILY_BRIEFING"


class LLMSource(StrEnum):
    """Provenance of every model response. A cached response is never
    presented as a live one (workflow.md §4.5)."""

    LIVE = "LIVE"
    CACHED = "CACHED"
    DETERMINISTIC = "DETERMINISTIC"
