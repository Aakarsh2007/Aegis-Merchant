"""The 18 tables (workflow.md §12.2).

Every table here is load-bearing; §12.3 records what breaks without each one.
Three that a conventional design would omit, and why they are not optional:

* ``outbox`` — two-phase execution. Without it, "the Razorpay call succeeded
  but our commit crashed" loses money silently.
* ``contact_ledger`` — an append-only ledger, *not* a counter on ``customers``.
  A counter cannot express a rolling 48-hour window, cannot be audited, and
  drifts under concurrent writes.
* ``experiment_assignments`` — the holdout arm. Without it the headline
  recovery number has no counterfactual and is unfalsifiable.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import (
    ActionType,
    ApprovalStatus,
    AttemptKind,
    CaseStatus,
    Channel,
    DiagnosisSource,
    DLQStatus,
    ErrorSource,
    ErrorStep,
    EscalationRung,
    ExperimentArm,
    FailureCategory,
    LLMSource,
    LLMTask,
    MessageClass,
    OutboxStatus,
    PaymentMethod,
    PaymentStatus,
    Playbook,
    PromiseStatus,
    RecoveryStrategy,
    StoppingRule,
)
from app.db.types import PaiseInt, UtcDateTime


def _enum(py_enum: type, name: str) -> SAEnum:
    """String-valued enum column with a CHECK constraint.

    ``native_enum=False`` keeps it a VARCHAR + CHECK, which SQLite supports and
    which stays readable to anyone browsing the file. ``values_callable``
    stores the enum *value* rather than the member name, so ``error_source``
    holds Razorpay's own lowercase ``"bank"`` instead of ``"BANK"``.
    """
    return SAEnum(
        py_enum,
        name=name,
        native_enum=False,
        # SQLAlchemy defaults this to False, which means NO constraint is
        # emitted and the database silently accepts any string. Found by
        # test_invalid_enum_value_is_rejected (see docs/INCIDENTS.md INC-001):
        # a bad `status` value would put a case in a state the state machine
        # has no branch for.
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


# ===========================================================================
# 1. Merchant & policy
# ===========================================================================
class Merchant(Base):
    __tablename__ = "merchants"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    business_name: Mapped[str] = mapped_column(String(200), nullable=False)

    razorpay_key_id: Mapped[str | None] = mapped_column(String(80))
    razorpay_key_secret_enc: Mapped[str | None] = mapped_column(String(200))
    webhook_secret_enc: Mapped[str | None] = mapped_column(String(200))

    #: The kill switch (stopping rule S-12). One toggle, effective immediately.
    autopilot_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Bearer token hash. Every money-moving endpoint requires it, and
    #: ``reviewed_by`` is taken from the authenticated principal, never the body.
    api_token_hash: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    policy: Mapped[PolicyConfig] = relationship(back_populates="merchant", uselist=False)
    customers: Mapped[list[Customer]] = relationship(back_populates="merchant")


class PolicyConfig(Base):
    """Every bound the agent cannot cross, as data.

    This is what makes the live demo possible — tightening a bound in the
    dashboard takes effect on the next case, with no deploy — and what lets
    each merchant carry its own limits.
    """

    __tablename__ = "policy_configs"

    merchant_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("merchants.id", ondelete="CASCADE"), primary_key=True
    )

    max_autonomous_amount_paise: Mapped[int] = mapped_column(PaiseInt, nullable=False)
    hitl_dual_signal_amount_paise: Mapped[int] = mapped_column(PaiseInt, nullable=False)

    max_discount_pct: Mapped[float] = mapped_column(Float, nullable=False)
    default_discount_pct: Mapped[float] = mapped_column(Float, nullable=False)
    max_discount_absolute_paise: Mapped[int] = mapped_column(PaiseInt, nullable=False)

    max_contacts_24h: Mapped[int] = mapped_column(Integer, nullable=False)
    max_contacts_48h: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts_per_case: Mapped[int] = mapped_column(Integer, nullable=False)
    max_discount_bearing_attempts: Mapped[int] = mapped_column(Integer, nullable=False)

    link_expiry_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    quiet_hours_start_ist: Mapped[int] = mapped_column(Integer, nullable=False)
    quiet_hours_end_ist: Mapped[int] = mapped_column(Integer, nullable=False)

    approval_ttl_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_action_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_discount_exposure_paise: Mapped[int] = mapped_column(PaiseInt, nullable=False)

    pre_debit_notice_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    max_representations: Mapped[int] = mapped_column(Integer, nullable=False)

    control_arm_fraction: Mapped[float] = mapped_column(Float, nullable=False)

    merchant: Mapped[Merchant] = relationship(back_populates="policy")

    __table_args__ = (
        # The clamp target must sit below the ceiling. Clamping *to* the
        # ceiling would reward a model for asking high (§26.2).
        CheckConstraint(
            "default_discount_pct <= max_discount_pct", name="default_discount_within_ceiling"
        ),
        CheckConstraint("max_discount_pct >= 0", name="discount_non_negative"),
        # A control fraction of 1.0 would mean never acting at all.
        CheckConstraint(
            "control_arm_fraction >= 0 AND control_arm_fraction < 1", name="control_fraction_range"
        ),
        CheckConstraint("max_contacts_24h <= max_contacts_48h", name="contact_caps_ordered"),
    )


# ===========================================================================
# 2. Customer, consent, contact ledger
# ===========================================================================
class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )

    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    #: PII is masked at this boundary and never leaves it. The LLM sees a first
    #: name and an amount band — never a phone number or email (§13.1).
    phone_masked: Mapped[str] = mapped_column(String(24), nullable=False)
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    email_masked: Mapped[str | None] = mapped_column(String(120))

    ltv_paise: Mapped[int] = mapped_column(PaiseInt, nullable=False, default=0)
    success_orders_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    language_pref: Mapped[str] = mapped_column(String(12), nullable=False, default="hinglish")
    is_business: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    first_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    merchant: Mapped[Merchant] = relationship(back_populates="customers")
    consent: Mapped[Consent | None] = relationship(back_populates="customer", uselist=False)

    __table_args__ = (
        Index("ix_customers_merchant", "merchant_id"),
        CheckConstraint("ltv_paise >= 0", name="ltv_non_negative"),
    )


class Consent(Base):
    """Consent as a stored legal fact, not an inference (§4.2 item 9)."""

    __tablename__ = "consents"

    customer_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("customers.id", ondelete="CASCADE"), primary_key=True
    )

    transactional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    marketing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: DND / NCPR registry. Blocks marketing class; transactional still allowed.
    dnd_registered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Stopping rule S-07. Permanent, across every case, forever.
    opted_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    opted_out_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    opt_out_source: Mapped[str | None] = mapped_column(String(40))

    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="consent")


class ContactLedger(Base):
    """Append-only record of every outbound contact.

    Deliberately not a counter on ``customers``: a counter cannot expire
    entries out of a rolling window, cannot be audited, and drifts under
    concurrent writes. The cap check and the insert happen in the *same*
    transaction as the dispatch (§12.3).
    """

    __tablename__ = "contact_ledger"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    customer_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("recovery_cases.id", ondelete="SET NULL")
    )

    channel: Mapped[Channel] = mapped_column(_enum(Channel, "channel"), nullable=False)
    message_class: Mapped[MessageClass] = mapped_column(
        _enum(MessageClass, "message_class"), nullable=False
    )
    template_id: Mapped[str | None] = mapped_column(String(40))
    sent_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (
        # The rolling-window query: WHERE customer_id = ? AND sent_at > ?
        Index("ix_contact_ledger_customer_sent", "customer_id", "sent_at"),
    )


class MessageTemplate(Base):
    """DLT-registered templates with slots.

    On the SMS channel the model fills *slots*, never free text — unregistered
    commercial content is blocked by carriers under TRAI TCCCPR (§9.1).
    """

    __tablename__ = "message_templates"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )

    channel: Mapped[Channel] = mapped_column(_enum(Channel, "tpl_channel"), nullable=False)
    message_class: Mapped[MessageClass] = mapped_column(
        _enum(MessageClass, "tpl_message_class"), nullable=False
    )
    dlt_template_id: Mapped[str | None] = mapped_column(String(60))
    language: Mapped[str] = mapped_column(String(12), nullable=False, default="hinglish")
    body_with_slots: Mapped[str] = mapped_column(Text, nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# ===========================================================================
# 3. Transaction ledger  (added in v3.2 — see ADL-012)
# ===========================================================================
class PaymentAttempt(Base):
    """Every payment attempt, successful or not.

    Two things need this table. The rail-health index is "a rolling success
    rate per (method, issuer) from our own event log" (§4.2 item 7) — a success
    rate needs a denominator, so failures alone are not enough. And the 210
    successful payments in the seed corpus are not recovery cases, so they had
    nowhere else to live. It is also the honest basis for "revenue at risk".
    """

    __tablename__ = "payment_attempts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )

    order_id: Mapped[str | None] = mapped_column(String(60))
    payment_id: Mapped[str | None] = mapped_column(String(60))
    invoice_id: Mapped[str | None] = mapped_column(String(60))
    subscription_id: Mapped[str | None] = mapped_column(String(60))

    kind: Mapped[AttemptKind] = mapped_column(_enum(AttemptKind, "attempt_kind"), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        _enum(PaymentStatus, "payment_status"), nullable=False
    )
    amount_paise: Mapped[int] = mapped_column(PaiseInt, nullable=False)
    method: Mapped[PaymentMethod | None] = mapped_column(_enum(PaymentMethod, "payment_method"))
    issuer: Mapped[str | None] = mapped_column(String(60))

    # Razorpay's own failure telemetry. Read, never inferred (§4.2 item 1).
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_source: Mapped[ErrorSource | None] = mapped_column(_enum(ErrorSource, "error_source"))
    error_step: Mapped[ErrorStep | None] = mapped_column(_enum(ErrorStep, "error_step"))
    error_reason: Mapped[str | None] = mapped_column(String(120))

    attempted_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (
        Index("ix_attempts_rail_health", "method", "issuer", "attempted_at"),
        Index("ix_attempts_customer", "customer_id"),
        Index("ix_attempts_order", "order_id"),
        CheckConstraint("amount_paise > 0", name="amount_positive"),
    )


# ===========================================================================
# 4. Ingestion
# ===========================================================================
class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    #: Razorpay's event id. The UNIQUE here is the entire duplicate defence:
    #: a replayed POST hits an IntegrityError and is acked without processing.
    event_id: Mapped[str] = mapped_column(String(80), nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)

    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    signature: Mapped[str | None] = mapped_column(String(200))
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    status: Mapped[str] = mapped_column(String(30), nullable=False, default="RECEIVED")
    #: The event's own timestamp, used for the replay window (§10.1).
    event_ts: Mapped[datetime | None] = mapped_column(UtcDateTime)
    received_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("event_id", name="event_id"),
        Index("ix_webhook_events_type", "event_type"),
    )


# ===========================================================================
# 5. Recovery case & experiment
# ===========================================================================
class RecoveryCase(Base):
    __tablename__ = "recovery_cases"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("payment_attempts.id", ondelete="SET NULL")
    )

    playbook: Mapped[Playbook] = mapped_column(_enum(Playbook, "playbook"), nullable=False)
    status: Mapped[CaseStatus] = mapped_column(_enum(CaseStatus, "case_status"), nullable=False)

    order_id: Mapped[str | None] = mapped_column(String(60))
    payment_id: Mapped[str | None] = mapped_column(String(60))
    invoice_id: Mapped[str | None] = mapped_column(String(60))
    subscription_id: Mapped[str | None] = mapped_column(String(60))
    amount_paise: Mapped[int] = mapped_column(PaiseInt, nullable=False)

    error_source: Mapped[ErrorSource | None] = mapped_column(_enum(ErrorSource, "case_err_source"))
    error_step: Mapped[ErrorStep | None] = mapped_column(_enum(ErrorStep, "case_err_step"))
    error_reason: Mapped[str | None] = mapped_column(String(120))

    diagnosis_category: Mapped[FailureCategory | None] = mapped_column(
        _enum(FailureCategory, "failure_category")
    )
    diagnosis_source: Mapped[DiagnosisSource | None] = mapped_column(
        _enum(DiagnosisSource, "diagnosis_source")
    )
    confidence: Mapped[float | None] = mapped_column(Float)

    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discount_bearing_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    recovered_amount_paise: Mapped[int] = mapped_column(PaiseInt, nullable=False, default=0)
    #: The webhook event_id that proved the recovery. Null means unproven, and
    #: an unproven case is never counted (§14.1).
    recovery_verified_by: Mapped[str | None] = mapped_column(String(80))

    #: Two workers racing on the same order produce the same hash; exactly one
    #: wins the INSERT (§12.4).
    idempotency_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    stopping_rule_fired: Mapped[StoppingRule | None] = mapped_column(
        _enum(StoppingRule, "stopping_rule")
    )

    #: Demo injections are demonstrations of mechanism, not data points, and
    #: are excluded from every lift computation (§14.4).
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    window_expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    assignment: Mapped[ExperimentAssignment | None] = relationship(
        back_populates="case", uselist=False
    )
    actions: Mapped[list[RecoveryAction]] = relationship(back_populates="case")

    __table_args__ = (
        UniqueConstraint("idempotency_hash", name="idempotency_hash"),
        Index("ix_cases_status", "status"),
        Index("ix_cases_playbook_status", "playbook", "status"),
        Index("ix_cases_window", "window_expires_at"),
        CheckConstraint("amount_paise > 0", name="amount_positive"),
        CheckConstraint("recovered_amount_paise >= 0", name="recovered_non_negative"),
        # Recovery must be provable: an amount without a verifying webhook is
        # exactly the unverifiable claim §14.1 exists to prevent.
        CheckConstraint(
            "recovered_amount_paise = 0 OR recovery_verified_by IS NOT NULL",
            name="recovery_requires_proof",
        ),
    )


class ExperimentAssignment(Base):
    """Immutable arm assignment, written once at TRIAGE.

    Deterministic from the case identity, so it is stable across restarts and
    replays, and independent of amount or LTV — anything correlated with
    outcome would bias the measurement (§14.2).
    """

    __tablename__ = "experiment_assignments"

    case_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("recovery_cases.id", ondelete="CASCADE"), primary_key=True
    )
    experiment_key: Mapped[str] = mapped_column(String(60), nullable=False)
    arm: Mapped[ExperimentArm] = mapped_column(
        _enum(ExperimentArm, "experiment_arm"), nullable=False
    )
    assignment_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    case: Mapped[RecoveryCase] = relationship(back_populates="assignment")

    __table_args__ = (Index("ix_assignments_arm", "experiment_key", "arm"),)


# ===========================================================================
# 6. Execution: outbox, actions, DLQ
# ===========================================================================
class Outbox(Base):
    """Two-phase execution intent (§10.3).

    The ``reference_id`` is generated and committed here *before* the provider
    call. If we crash anywhere after that commit, the drainer retries with the
    identical key and Razorpay's own uniqueness constraint on ``reference_id``
    makes the retry idempotent. No distributed transaction required — we just
    need the provider to reject our duplicate, which it does.
    """

    __tablename__ = "outbox"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("recovery_cases.id", ondelete="CASCADE"), nullable=False
    )

    action_type: Mapped[ActionType] = mapped_column(
        _enum(ActionType, "action_type"), nullable=False
    )
    #: The idempotency key. UNIQUE locally as well as at the provider.
    reference_id: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[OutboxStatus] = mapped_column(
        _enum(OutboxStatus, "outbox_status"), nullable=False, default=OutboxStatus.PENDING
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_ref: Mapped[str | None] = mapped_column(String(80))
    last_error: Mapped[str | None] = mapped_column(Text)

    next_attempt_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("reference_id", name="reference_id"),
        # Belt and braces: one attempt of one action type per case, once.
        UniqueConstraint("case_id", "action_type", "attempt", name="case_action_attempt"),
        # The drainer's query: WHERE status = 'PENDING' AND next_attempt_at <= ?
        Index("ix_outbox_due", "status", "next_attempt_at"),
        CheckConstraint("attempt >= 0", name="attempt_non_negative"),
    )


class RecoveryAction(Base):
    __tablename__ = "recovery_actions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("recovery_cases.id", ondelete="CASCADE"), nullable=False
    )
    outbox_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("outbox.id", ondelete="SET NULL")
    )

    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[ActionType] = mapped_column(
        _enum(ActionType, "ra_action_type"), nullable=False
    )
    strategy: Mapped[RecoveryStrategy | None] = mapped_column(
        _enum(RecoveryStrategy, "recovery_strategy")
    )
    escalation_rung: Mapped[EscalationRung | None] = mapped_column(
        _enum(EscalationRung, "escalation_rung")
    )
    message_class: Mapped[MessageClass | None] = mapped_column(_enum(MessageClass, "ra_msg_class"))

    #: The discount that was actually applied — i.e. post-clamp. This is what
    #: executed, not what the model proposed.
    discount_pct_applied: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    discount_amount_paise: Mapped[int] = mapped_column(PaiseInt, nullable=False, default=0)

    razorpay_link_id: Mapped[str | None] = mapped_column(String(80))
    razorpay_link_url: Mapped[str | None] = mapped_column(String(300))
    reference_id: Mapped[str | None] = mapped_column(String(80))

    channel: Mapped[Channel | None] = mapped_column(_enum(Channel, "ra_channel"))
    message_body: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(30), nullable=False, default="EXECUTED")
    executed_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    case: Mapped[RecoveryCase] = relationship(back_populates="actions")

    __table_args__ = (
        Index("ix_actions_case", "case_id"),
        Index("ix_actions_reference", "reference_id"),
        CheckConstraint("discount_pct_applied >= 0", name="discount_non_negative"),
        CheckConstraint("discount_amount_paise >= 0", name="discount_amount_non_negative"),
    )


class DeadLetter(Base):
    """Persistent terminal execution failures.

    A dead-letter queue that lives in memory cannot survive the crash it
    exists to handle — which is what an earlier revision of the design got
    wrong (§30 ADL-006). Visible in the dashboard, replayable by one
    authenticated POST, never silently discarded.
    """

    __tablename__ = "dlq"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    outbox_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("outbox.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(120), nullable=False)
    error_chain_json: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[DLQStatus] = mapped_column(
        _enum(DLQStatus, "dlq_status"), nullable=False, default=DLQStatus.OPEN
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    replayed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    __table_args__ = (Index("ix_dlq_status", "status"),)


# ===========================================================================
# 7. Human-in-the-loop
# ===========================================================================
class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("recovery_cases.id", ondelete="CASCADE"), nullable=False
    )

    trigger_rung: Mapped[EscalationRung] = mapped_column(
        _enum(EscalationRung, "ar_trigger_rung"), nullable=False
    )
    trigger_reason: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_paise: Mapped[int] = mapped_column(PaiseInt, nullable=False)

    #: The exact action being approved, and its hash. A human approves a
    #: *specific* action: if anything changes between display and execution the
    #: hash mismatches and execution refuses (§13.5). That is the difference
    #: between a real approval gate and a button labelled "approve".
    policy_applied_json: Mapped[str] = mapped_column(Text, nullable=False)
    policy_applied_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[ApprovalStatus] = mapped_column(
        _enum(ApprovalStatus, "approval_status"), nullable=False, default=ApprovalStatus.PENDING
    )
    #: Taken from the authenticated principal, never from the request body.
    reviewed_by: Mapped[str | None] = mapped_column(String(80))
    review_notes: Mapped[str | None] = mapped_column(Text)

    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    __table_args__ = (
        Index("ix_approvals_status_expiry", "status", "expires_at"),
        Index("ix_approvals_case", "case_id"),
    )


class PromiseToPay(Base):
    """A commitment extracted from a free-text reply.

    While a promise is active the reminder cadence is frozen until
    ``promised_at + 24h`` (stopping rule S-10). An agent that keeps chasing
    someone who already said "Friday" is worse than no agent.
    """

    __tablename__ = "promises_to_pay"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("recovery_cases.id", ondelete="CASCADE"), nullable=False
    )
    invoice_id: Mapped[str | None] = mapped_column(String(60))

    promised_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    promised_amount_paise: Mapped[int | None] = mapped_column(PaiseInt)
    #: Untrusted input, stored verbatim for the audit trail. Wrapped in
    #: <untrusted_customer_text> before it ever reaches a prompt (§13.1).
    customer_raw_reply: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_confidence: Mapped[float | None] = mapped_column(Float)

    status: Mapped[PromiseStatus] = mapped_column(
        _enum(PromiseStatus, "promise_status"), nullable=False, default=PromiseStatus.ACTIVE
    )
    recorded_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (Index("ix_promises_status_date", "status", "promised_at"),)


# ===========================================================================
# 8. Audit chain
# ===========================================================================
class AuditBlock(Base):
    """Tamper-evident ledger: ``H_n = SHA256(H_{n-1} || canonical || n || ts)``.

    Append-only by design — no code path issues an UPDATE or DELETE against
    this table. ``GET /api/v1/audit/verify`` recomputes the whole chain, and is
    demonstrated catching a deliberate tamper, so it is visibly a real verifier
    rather than one that always returns true (§13.4).
    """

    __tablename__ = "audit_blocks"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    block_index: Mapped[int] = mapped_column(Integer, nullable=False)
    case_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("recovery_cases.id", ondelete="SET NULL")
    )

    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    current_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    event_name: Mapped[str] = mapped_column(String(60), nullable=False)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    #: Canonical JSON: sorted keys, no whitespace, fixed separators.
    #: Non-canonical serialisation is how hash chains silently become
    #: unverifiable across processes.
    payload_canonical: Mapped[str] = mapped_column(Text, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("block_index", name="block_index"),
        UniqueConstraint("current_hash", name="current_hash"),
        Index("ix_audit_case", "case_id"),
        CheckConstraint("block_index >= 0", name="block_index_non_negative"),
    )


# ===========================================================================
# 9. LLM accounting & response cache
# ===========================================================================
class LLMCall(Base):
    """One row per model call, with provenance.

    ``source`` is why this table exists in this shape: without it a cached
    response could be silently presented as a live one, and the cost and
    latency claims in §4.6 would be assertions rather than measurements.
    """

    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("recovery_cases.id", ondelete="CASCADE")
    )

    task: Mapped[LLMTask] = mapped_column(_enum(LLMTask, "llm_task"), nullable=False)
    source: Mapped[LLMSource] = mapped_column(_enum(LLMSource, "llm_source"), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(60))
    prompt_version: Mapped[str | None] = mapped_column(String(20))
    cache_key: Mapped[str | None] = mapped_column(String(64))

    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Actual spend on a free tier is zero. This is the *projected* cost at
    #: published paid rates, which is the number that answers "would this work
    #: in production" (§4.6).
    projected_cost_micro_inr: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    schema_valid_first_try: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    fell_back: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (
        Index("ix_llm_calls_task_source", "task", "source"),
        Index("ix_llm_calls_case", "case_id"),
    )


class LLMCache(Base):
    """Content-addressed model response cache (§4.5).

    Key = SHA256(task || model || prompt_version || canonical_json(context)).
    Committed to the repo, so a 420-case batch runs in seconds with zero API
    calls and produces byte-for-byte reproducible numbers. Bumping
    ``prompt_version`` invalidates every key, so a stale cache cannot silently
    pass CI.
    """

    __tablename__ = "llm_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    task: Mapped[LLMTask] = mapped_column(_enum(LLMTask, "cache_task"), nullable=False)
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False)
    context_canonical: Mapped[str] = mapped_column(Text, nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (Index("ix_llm_cache_task", "task", "prompt_version"),)


#: Every table, for schema assertions and the seed script.
ALL_MODELS = (
    Merchant,
    PolicyConfig,
    Customer,
    Consent,
    ContactLedger,
    MessageTemplate,
    PaymentAttempt,
    WebhookEvent,
    RecoveryCase,
    ExperimentAssignment,
    Outbox,
    RecoveryAction,
    DeadLetter,
    ApprovalRequest,
    PromiseToPay,
    AuditBlock,
    LLMCall,
    LLMCache,
)
