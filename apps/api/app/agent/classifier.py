"""Deterministic failure classifier.

**This module is the reason 40% of the planned LLM calls were deleted**
(workflow.md §4.2 item 1). Razorpay *states* whose fault a failure was, in
``error_source`` and ``error_step``. Asking a model to infer a fact the API
already reports is a hallucination surface with no upside — so we read it.

What that buys, concretely: the category decides the *recovery strategy*, and
getting it wrong costs real money in ways that are not symmetrical.

* ``INSUFFICIENT_FUNDS`` -> retry on the **same** rail, later. Switching rails
  is the intuitive move and it is wrong: a different rail does not put money in
  the customer's account, and it burns an attempt from a budget of two.
* ``RAIL_FAULT`` -> switch to a **healthier** rail. Retrying the rail that just
  timed out is the classic dumb-rule-engine behaviour §0.1 calls out.
* ``MANDATE_INVALID`` -> **do not retry at all.** The debit cannot succeed and
  each attempt consumes a scheme re-presentation. Needs re-authorisation.
* ``RISK_BLOCKED`` -> **do not act autonomously.** The business blocked this
  deliberately; an agent quietly re-issuing a link would be working around a
  fraud control.

Where this classifier is *not* confident — missing fields, or signals that
disagree with each other — it says so, and that is precisely the input the LLM
diagnostic node is for (§4.3 task 1). The split is deliberate: deterministic
code handles the unambiguous majority; the model is spent only on genuine
ambiguity.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, TypeVar

from app.db.enums import DiagnosisSource, ErrorSource, ErrorStep, FailureCategory

E = TypeVar("E", bound=Enum)

__all__ = [
    "Diagnosis",
    "classify",
    "classify_abandoned_checkout",
]


@dataclass(frozen=True)
class Diagnosis:
    """A classification, plus what it implies for recovery.

    ``confidence`` reflects *how specific the matching evidence was*, not a
    model's feeling. An exact (source, step, reason) match scores higher than a
    source-only match because it is grounded in more of what Razorpay told us.
    """

    category: FailureCategory
    is_recoverable: bool
    #: True when the same rail should be retried. False means switch rails.
    #: Distinguishing these is the difference between a recovery and a wasted
    #: attempt from a budget of two.
    retry_same_rail: bool
    #: The mandate is dead; retrying cannot succeed and burns a re-presentation.
    requires_reauth: bool
    #: A discount cannot fix this. Guards against the agent spending margin on
    #: a bank outage.
    discount_could_help: bool
    confidence: float
    reasoning: str
    source: DiagnosisSource = DiagnosisSource.DETERMINISTIC_FALLBACK
    #: Razorpay's fields disagree with each other. These are exactly the cases
    #: worth spending an LLM call on.
    signals_conflict: bool = False
    #: Fields that were absent. An empty diagnosis built from nothing should
    #: never look as trustworthy as one built from complete telemetry.
    missing_fields: tuple[str, ...] = ()

    @property
    def needs_llm_review(self) -> bool:
        """Whether a cognitive second opinion is worth its cost."""
        return self.signals_conflict or self.confidence < 0.6


# ---------------------------------------------------------------------------
# Confidence, by strength of evidence.
# ---------------------------------------------------------------------------
CONF_EXACT: Final = 0.95  # source + step + a recognised reason
CONF_SOURCE_STEP: Final = 0.85  # source + step
CONF_SOURCE_ONLY: Final = 0.65  # source alone
CONF_REASON_ONLY: Final = 0.60  # reason alone, no source
CONF_CONFLICT: Final = 0.45  # fields disagree -> hand to the LLM
CONF_NONE: Final = 0.25  # nothing usable

#: Substrings of ``error_reason``. Razorpay's reason strings are more specific
#: than (source, step), so they refine the verdict — and when they *contradict*
#: the source, that disagreement is itself signal.
_REASON_MARKERS: Final[tuple[tuple[str, FailureCategory], ...]] = (
    # Ordered by SPECIFICITY, deliberately, because these are substring matches
    # and a generic mechanism word must never outrank a phrase naming the actor.
    # `otp_entry_timed_out_by_user` is a person giving up, not a rail outage;
    # matching "timeout" first got that backwards (INC-003).
    #
    # 1. Mandate -- the costliest category to miss: every retry of a dead
    #    mandate burns a scheme re-presentation and cannot succeed.
    ("mandate_revoked", FailureCategory.MANDATE_INVALID),
    ("mandate_not_active", FailureCategory.MANDATE_INVALID),
    ("mandate_cancelled", FailureCategory.MANDATE_INVALID),
    ("mandate_expired", FailureCategory.MANDATE_INVALID),
    ("mandate_not_found", FailureCategory.MANDATE_INVALID),
    ("mandate", FailureCategory.MANDATE_INVALID),
    # 2. Explicit customer agency. A phrase naming the user is more specific
    #    than a word describing the mechanism they abandoned.
    #
    #    CAPTURED (INC-015): `payment_cancelled` is the string Razorpay Test
    #    Mode actually returns when a customer abandons the checkout. This
    #    table originally carried four *guessed* spellings of that event and
    #    not the real one, so every genuine cancellation fell through to the
    #    (source, step) fallback. The verdict happened to survive; the stated
    #    reasoning did not.
    ("payment_cancelled", FailureCategory.AUTHENTICATION_ABANDONED),  # observed live
    ("by_user", FailureCategory.AUTHENTICATION_ABANDONED),
    ("user_cancel", FailureCategory.AUTHENTICATION_ABANDONED),
    ("cancelled_by", FailureCategory.AUTHENTICATION_ABANDONED),
    ("canceled_by", FailureCategory.AUTHENTICATION_ABANDONED),
    ("user_closed", FailureCategory.AUTHENTICATION_ABANDONED),
    ("collect_request_expired", FailureCategory.AUTHENTICATION_ABANDONED),
    ("session_expired", FailureCategory.AUTHENTICATION_ABANDONED),
    ("incorrect_otp", FailureCategory.AUTHENTICATION_ABANDONED),
    ("otp", FailureCategory.AUTHENTICATION_ABANDONED),
    ("authentication_failed", FailureCategory.AUTHENTICATION_ABANDONED),
    ("authentication_cancelled", FailureCategory.AUTHENTICATION_ABANDONED),
    # 3. Funds. Checked before risk, because "declined_by_issuer_insufficient_
    #    funds" is a balance problem, not a fraud block -- and the two demand
    #    opposite actions.
    ("insufficient_funds", FailureCategory.INSUFFICIENT_FUNDS),
    ("insufficient_balance", FailureCategory.INSUFFICIENT_FUNDS),
    ("credit_limit", FailureCategory.INSUFFICIENT_FUNDS),
    ("exceeds_limit", FailureCategory.INSUFFICIENT_FUNDS),
    ("limit_exceed", FailureCategory.INSUFFICIENT_FUNDS),
    ("limit_or_funds", FailureCategory.INSUFFICIENT_FUNDS),
    # 4. Risk. Note `declined_by_issuer` is deliberately NOT here: an issuer
    #    decline is not a merchant risk block, and conflating them would stop
    #    us recovering ordinary declines.
    ("suspected_fraud", FailureCategory.RISK_BLOCKED),
    ("fraud", FailureCategory.RISK_BLOCKED),
    ("blocked", FailureCategory.RISK_BLOCKED),
    ("risk", FailureCategory.RISK_BLOCKED),
    # 5. Generic rail mechanics -- last, because they describe how something
    #    failed rather than who or why.
    ("timed_out", FailureCategory.RAIL_FAULT),
    ("timeout", FailureCategory.RAIL_FAULT),
    ("downtime", FailureCategory.RAIL_FAULT),
    ("unavailable", FailureCategory.RAIL_FAULT),
    ("unreachable", FailureCategory.RAIL_FAULT),
    ("gateway", FailureCategory.RAIL_FAULT),
    ("response_not_received", FailureCategory.RAIL_FAULT),
    ("service_error", FailureCategory.RAIL_FAULT),
    ("reconciliation_mismatch", FailureCategory.RAIL_FAULT),
)


#: (error_source, error_step) -> category. Razorpay's own taxonomy, mapped to
#: ours. Unlisted combinations fall back to source-level classification rather
#: than raising: Razorpay may add a step at any time, and a crash on an
#: unrecognised combination would drop a recoverable payment.
_SOURCE_STEP: Final[dict[tuple[ErrorSource, ErrorStep], FailureCategory]] = {
    (ErrorSource.BANK, ErrorStep.PAYMENT_AUTHORIZATION): FailureCategory.RAIL_FAULT,
    (ErrorSource.BANK, ErrorStep.PAYMENT_AUTHENTICATION): FailureCategory.RAIL_FAULT,
    (ErrorSource.BANK, ErrorStep.PAYMENT_INITIATION): FailureCategory.RAIL_FAULT,
    (ErrorSource.BANK, ErrorStep.PAYMENT_RESPONSE): FailureCategory.RAIL_FAULT,
    (ErrorSource.GATEWAY, ErrorStep.PAYMENT_AUTHORIZATION): FailureCategory.RAIL_FAULT,
    (ErrorSource.GATEWAY, ErrorStep.PAYMENT_AUTHENTICATION): FailureCategory.RAIL_FAULT,
    (ErrorSource.GATEWAY, ErrorStep.PAYMENT_INITIATION): FailureCategory.RAIL_FAULT,
    (ErrorSource.GATEWAY, ErrorStep.PAYMENT_RESPONSE): FailureCategory.RAIL_FAULT,
    (ErrorSource.INTERNAL, ErrorStep.PAYMENT_RESPONSE): FailureCategory.RAIL_FAULT,
    (ErrorSource.INTERNAL, ErrorStep.PAYMENT_INITIATION): FailureCategory.RAIL_FAULT,
    (ErrorSource.INTERNAL, ErrorStep.PAYMENT_AUTHORIZATION): FailureCategory.RAIL_FAULT,
    (ErrorSource.INTERNAL, ErrorStep.PAYMENT_AUTHENTICATION): FailureCategory.RAIL_FAULT,
    # Authorization is where money moves, so a customer-side failure there is
    # usually funds; authentication is where they walk away.
    (ErrorSource.CUSTOMER, ErrorStep.PAYMENT_AUTHORIZATION): FailureCategory.INSUFFICIENT_FUNDS,
    (
        ErrorSource.CUSTOMER,
        ErrorStep.PAYMENT_AUTHENTICATION,
    ): FailureCategory.AUTHENTICATION_ABANDONED,
    (
        ErrorSource.CUSTOMER,
        ErrorStep.PAYMENT_INITIATION,
    ): FailureCategory.AUTHENTICATION_ABANDONED,
    (ErrorSource.CUSTOMER, ErrorStep.PAYMENT_RESPONSE): FailureCategory.AUTHENTICATION_ABANDONED,
    # 'business' means our own risk rules rejected it. Never work around that.
    (ErrorSource.BUSINESS, ErrorStep.PAYMENT_INITIATION): FailureCategory.RISK_BLOCKED,
    (ErrorSource.BUSINESS, ErrorStep.PAYMENT_AUTHORIZATION): FailureCategory.RISK_BLOCKED,
    (ErrorSource.BUSINESS, ErrorStep.PAYMENT_AUTHENTICATION): FailureCategory.RISK_BLOCKED,
    (ErrorSource.BUSINESS, ErrorStep.PAYMENT_RESPONSE): FailureCategory.RISK_BLOCKED,
    (ErrorSource.NBFC, ErrorStep.PAYMENT_AUTHORIZATION): FailureCategory.INSUFFICIENT_FUNDS,
    (ErrorSource.NBFC, ErrorStep.PAYMENT_INITIATION): FailureCategory.RAIL_FAULT,
    (ErrorSource.NBFC, ErrorStep.PAYMENT_AUTHENTICATION): FailureCategory.RAIL_FAULT,
    (ErrorSource.NBFC, ErrorStep.PAYMENT_RESPONSE): FailureCategory.RAIL_FAULT,
}

#: source alone, when the step is missing or unrecognised.
_SOURCE_ONLY: Final[dict[ErrorSource, FailureCategory]] = {
    ErrorSource.BANK: FailureCategory.RAIL_FAULT,
    ErrorSource.GATEWAY: FailureCategory.RAIL_FAULT,
    ErrorSource.INTERNAL: FailureCategory.RAIL_FAULT,
    ErrorSource.NBFC: FailureCategory.RAIL_FAULT,
    ErrorSource.CUSTOMER: FailureCategory.AUTHENTICATION_ABANDONED,
    ErrorSource.BUSINESS: FailureCategory.RISK_BLOCKED,
}

#: What each category implies. Kept as data so the mapping is inspectable, and
#: so a test can assert the whole policy rather than sampling it.
#: (is_recoverable, retry_same_rail, requires_reauth, discount_could_help)
_IMPLICATIONS: Final[dict[FailureCategory, tuple[bool, bool, bool, bool]]] = {
    # A healthier rail is the whole point.
    FailureCategory.RAIL_FAULT: (True, False, False, False),
    # Same rail, later. A different rail does not create money, and a discount
    # genuinely might close a small gap -- but policy still has to allow it.
    FailureCategory.INSUFFICIENT_FUNDS: (True, True, False, True),
    # They were present and stopped. A fresh link on the same rail is the
    # cheapest thing that could work.
    FailureCategory.AUTHENTICATION_ABANDONED: (True, True, False, False),
    # Retrying is guaranteed to fail AND consumes a re-presentation.
    FailureCategory.MANDATE_INVALID: (True, False, True, False),
    FailureCategory.PRICE_RESISTANCE: (True, True, False, True),
    FailureCategory.INTENT_DECAY: (True, True, False, True),
    # Deliberately blocked. Escalate to a human; never route around it.
    FailureCategory.RISK_BLOCKED: (False, False, False, False),
    FailureCategory.UNKNOWN: (True, True, False, False),
}


def _coerce(value: str | None, enum_cls: type[E]) -> tuple[E | None, bool]:
    """Parse a provider string into an enum.

    Returns ``(member_or_None, was_unrecognised)``. An unrecognised *value* is
    different from a *missing* one: Razorpay adding a new ``error_source`` must
    degrade this classifier's confidence, not crash it or silently look like
    absent data.
    """
    if value is None:
        return None, False
    cleaned = str(value).strip().lower()
    if not cleaned:
        return None, False
    try:
        return enum_cls(cleaned), False
    except ValueError:
        return None, True


def _match_reason(reason: str | None) -> FailureCategory | None:
    if not reason:
        return None
    text = str(reason).strip().lower()
    if not text:
        return None
    for marker, category in _REASON_MARKERS:
        if marker in text:
            return category
    return None


def _build(
    category: FailureCategory,
    confidence: float,
    reasoning: str,
    *,
    conflict: bool = False,
    missing: tuple[str, ...] = (),
) -> Diagnosis:
    recoverable, same_rail, reauth, discount = _IMPLICATIONS[category]
    return Diagnosis(
        category=category,
        is_recoverable=recoverable,
        retry_same_rail=same_rail,
        requires_reauth=reauth,
        discount_could_help=discount,
        confidence=confidence,
        reasoning=reasoning,
        signals_conflict=conflict,
        missing_fields=missing,
    )


def classify(
    *,
    error_source: str | None,
    error_step: str | None = None,
    error_reason: str | None = None,
    method: str | None = None,
) -> Diagnosis:
    """Classify a payment failure from Razorpay's own telemetry.

    ``method`` is accepted but **deliberately unused**. Three golden-set cases
    (G-M001..003) show that payment method genuinely changes the right answer --
    a bank-side failure at initiation means a rail outage on UPI but an
    unregistered mandate on e-mandate. Encoding that as more rules would be
    guessing at a combinatorial table; it is exactly the kind of multi-signal
    judgement the LLM diagnostic node is for. The parameter stays in the
    signature so the handoff is visible rather than hidden.

    Never raises. Every input — including nonsense, empty strings, and values
    Razorpay invents next quarter — produces a diagnosis, because an exception
    here would drop a recoverable payment on the floor.
    """
    source, source_unknown = _coerce(error_source, ErrorSource)
    step, step_unknown = _coerce(error_step, ErrorStep)
    reason_category = _match_reason(error_reason)

    missing: list[str] = []
    if source is None:
        missing.append("error_source")
    if step is None:
        missing.append("error_step")
    if not error_reason:
        missing.append("error_reason")

    # --- nothing usable -----------------------------------------------------
    if source is None and reason_category is None:
        note = (
            "no recognised error_source or error_reason"
            if not source_unknown
            else f"unrecognised error_source {error_source!r}"
        )
        return _build(
            FailureCategory.UNKNOWN,
            CONF_NONE,
            f"Insufficient telemetry to classify: {note}.",
            missing=tuple(missing),
        )

    # --- source absent or unrecognised, but the reason is specific ----------
    if source is None and reason_category is not None:
        return _build(
            reason_category,
            CONF_REASON_ONLY,
            f"Classified from error_reason alone ({error_reason!r}); "
            f"error_source {'unrecognised' if source_unknown else 'absent'}.",
            missing=tuple(missing),
        )

    assert isinstance(source, ErrorSource)  # narrowed by the branches above

    # SAFETY GATE, checked before anything else can reverse it.
    # `business` means the merchant's own risk rules rejected this payment. That
    # is a decision already taken, not a signal to weigh against a substring.
    # Found by test_never_acts_autonomously_on_a_risk_block (INC-003): a reason
    # containing "timeout" or "mandate" was overriding the business source, so
    # the classifier marked a deliberately-blocked payment recoverable -- the
    # agent would have routed around a fraud control. No error_reason may
    # unblock a business block.
    if source is ErrorSource.BUSINESS:
        return _build(
            FailureCategory.RISK_BLOCKED,
            CONF_EXACT if error_reason else CONF_SOURCE_ONLY,
            f"error_source=business: the merchant's own risk controls rejected this "
            f"payment{f' ({error_reason!r})' if error_reason else ''}. Not recoverable "
            "autonomously under any reason string.",
        )

    # A dead mandate on an e-mandate rail is unambiguous regardless of who
    # Razorpay blames, and it is the costliest thing to get wrong: every retry
    # burns a scheme re-presentation that cannot succeed.
    if reason_category is FailureCategory.MANDATE_INVALID:
        return _build(
            FailureCategory.MANDATE_INVALID,
            CONF_EXACT,
            f"Mandate is not active ({error_reason!r}); retrying cannot succeed "
            "and would consume a re-presentation. Re-authorisation required.",
        )

    step_category = _SOURCE_STEP.get((source, step)) if step is not None else None
    source_category = _SOURCE_ONLY.get(source)

    # --- source + step + reason all available -------------------------------
    if step_category is not None and reason_category is not None:
        if step_category is reason_category:
            return _build(
                reason_category,
                CONF_EXACT,
                f"Razorpay reports error_source={source.value}, "
                f"error_step={step.value if step else '?'}, reason={error_reason!r}. "
                "All three agree.",
            )
        # They disagree. The reason string is the more specific evidence, so it
        # wins -- but the disagreement lowers confidence and flags the case for
        # a cognitive second opinion (§4.3 task 1). This is the honest handoff
        # point between deterministic code and the model.
        return _build(
            reason_category,
            CONF_CONFLICT,
            f"Conflicting signals: (source={source.value}, step={step.value if step else '?'}) "
            f"implies {step_category.value}, but reason {error_reason!r} implies "
            f"{reason_category.value}. Taking the more specific reason; flagged for review.",
            conflict=True,
        )

    # --- source + step ------------------------------------------------------
    if step_category is not None:
        return _build(
            step_category,
            CONF_SOURCE_STEP,
            f"Razorpay reports error_source={source.value}, "
            f"error_step={step.value if step else '?'}. No usable error_reason.",
            missing=tuple(missing),
        )

    # --- reason only, source recognised but the pair is not ------------------
    if reason_category is not None:
        return _build(
            reason_category,
            CONF_SOURCE_ONLY,
            f"Classified from error_reason {error_reason!r}; "
            f"(source={source.value}, step={error_step!r}) is not a recognised pair.",
            missing=tuple(missing),
        )

    # --- source alone -------------------------------------------------------
    if source_category is not None:
        detail = (
            f"unrecognised error_step {error_step!r}" if step_unknown else "no error_step reported"
        )
        return _build(
            source_category,
            CONF_SOURCE_ONLY,
            f"Razorpay reports error_source={source.value}; {detail}.",
            missing=tuple(missing),
        )

    return _build(
        FailureCategory.UNKNOWN,
        CONF_NONE,
        f"error_source={source.value} has no mapping.",
        missing=tuple(missing),
    )


def classify_abandoned_checkout(
    *,
    ltv_paise: int,
    prior_orders: int,
    cart_amount_paise: int,
) -> Diagnosis:
    """Classify an abandoned checkout.

    Different problem: there is no failure and no error telemetry, because
    nothing failed — the customer simply left. So the only signals are
    behavioural, and the deterministic split is deliberately coarse.

    A repeat buyer abandoning is unlikely to be price-shopping; a first-timer
    on a cart well above their history plausibly is. Both are weak inferences,
    which is exactly why confidence stays low and the LLM gets asked: *"is this
    price resistance or a distraction?"* is a judgement call, and pretending a
    rule table settles it would be the wrong tool in the right place.
    """
    if prior_orders >= 2 and ltv_paise > 0:
        avg = ltv_paise // max(prior_orders, 1)
        if cart_amount_paise > avg * 2:
            return _build(
                FailureCategory.PRICE_RESISTANCE,
                CONF_SOURCE_ONLY,
                f"Repeat customer ({prior_orders} orders) abandoned a cart "
                f"{cart_amount_paise / max(avg, 1):.1f}x their average order value.",
            )
        return _build(
            FailureCategory.INTENT_DECAY,
            CONF_SOURCE_ONLY,
            f"Repeat customer ({prior_orders} orders) abandoned a cart in their "
            "normal range; more likely a distraction than a pricing objection.",
        )

    return _build(
        FailureCategory.INTENT_DECAY,
        CONF_CONFLICT,
        "First-time or low-history customer; no behavioural baseline to compare "
        "against. Intent unclear from behaviour alone.",
        missing=("purchase_history",),
    )
