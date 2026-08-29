"""Classifier edge cases and safety properties.

The golden set measures *accuracy*. This file pins *behaviour* — especially the
properties that must hold regardless of accuracy, because they are the ones
that cost money or route around a control when violated.
"""

from __future__ import annotations

import pytest

from app.agent.classifier import (
    CONF_CONFLICT,
    CONF_EXACT,
    CONF_NONE,
    CONF_SOURCE_ONLY,
    CONF_SOURCE_STEP,
    Diagnosis,
    classify,
    classify_abandoned_checkout,
)
from app.db.enums import DiagnosisSource, FailureCategory


class TestNeverRaises:
    """An exception here drops a recoverable payment on the floor."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"error_source": None},
            {"error_source": ""},
            {"error_source": "   "},
            {"error_source": "bank", "error_step": ""},
            {"error_source": "not_a_source", "error_step": "not_a_step"},
            {"error_source": "bank", "error_reason": ""},
            {"error_source": None, "error_step": None, "error_reason": None, "method": None},
            {"error_source": "BANK", "error_step": "PAYMENT_AUTHORIZATION"},
            {"error_source": "  bank  ", "error_step": " payment_authorization "},
            {"error_source": "bank", "method": "quantum_teleport"},
            {"error_source": "bank", "error_reason": "x" * 5000},
            {"error_source": "bank", "error_reason": "मैंडेट रद्द"},
            {"error_source": "bank", "error_reason": "\x00\x01binary"},
        ],
    )
    def test_survives_hostile_input(self, kwargs: dict[str, str | None]) -> None:
        result = classify(**kwargs)  # type: ignore[arg-type]
        assert isinstance(result, Diagnosis)
        assert 0.0 <= result.confidence <= 1.0

    def test_empty_string_is_treated_as_absent(self) -> None:
        """`""` is not a value. Treating it as one would make an empty field
        look like a recognised source."""
        assert classify(error_source="").category is FailureCategory.UNKNOWN

    def test_case_and_whitespace_are_not_semantics(self) -> None:
        upper = classify(error_source="BANK", error_step="PAYMENT_AUTHORIZATION")
        padded = classify(error_source="  bank ", error_step=" payment_authorization  ")
        lower = classify(error_source="bank", error_step="payment_authorization")
        assert upper.category is lower.category is padded.category


class TestSafetyProperties:
    """Properties that must hold whatever the accuracy number says."""

    def test_business_source_is_never_recoverable(self) -> None:
        """A merchant risk block is a decision already taken."""
        for reason in [
            None,
            "payment_failed_due_to_risk_check",
            "payment_blocked_after_risk_check_timeout",
            "mandate_presented_but_blocked_by_risk",
            "insufficient_funds",
            "bank_timeout",
            "cancelled_by_user",
        ]:
            result = classify(
                error_source="business", error_step="payment_initiation", error_reason=reason
            )
            assert result.category is FailureCategory.RISK_BLOCKED, reason
            assert not result.is_recoverable, reason

    def test_no_reason_string_can_unblock_a_business_block(self) -> None:
        """The specific defect INC-003 was about: a substring in a free-text
        field must not be able to reverse a fraud control."""
        crafted = classify(
            error_source="business",
            error_step="payment_authorization",
            error_reason="gateway timeout downtime unavailable insufficient_funds mandate",
        )
        assert crafted.category is FailureCategory.RISK_BLOCKED
        assert not crafted.is_recoverable

    def test_mandate_never_retries_and_always_reauths(self) -> None:
        """Each retry of a dead mandate burns a scheme re-presentation."""
        for reason in [
            "payment_failed_mandate_revoked_by_customer",
            "mandate_not_active",
            "mandate_expired",
            "emandate_debit_mandate_not_found",
        ]:
            result = classify(error_source="bank", error_reason=reason, method="emandate")
            assert result.category is FailureCategory.MANDATE_INVALID
            assert result.requires_reauth
            assert not result.retry_same_rail

    def test_insufficient_funds_stays_on_the_same_rail(self) -> None:
        """Switching rails does not put money in someone's account, and it
        spends one of only two attempts."""
        result = classify(
            error_source="customer",
            error_step="payment_authorization",
            error_reason="payment_failed_due_to_insufficient_funds",
        )
        assert result.category is FailureCategory.INSUFFICIENT_FUNDS
        assert result.retry_same_rail

    def test_rail_fault_switches_rails_and_forbids_a_discount(self) -> None:
        """Spending margin on a bank outage is pure waste."""
        result = classify(
            error_source="bank",
            error_step="payment_authorization",
            error_reason="payment_failed_due_to_bank_timeout",
        )
        assert result.category is FailureCategory.RAIL_FAULT
        assert not result.retry_same_rail
        assert not result.discount_could_help

    def test_every_diagnosis_is_marked_deterministic(self) -> None:
        """Provenance matters: a rule-table answer must never be displayed as
        model reasoning (§19.2)."""
        assert classify(error_source="bank").source is DiagnosisSource.DETERMINISTIC_FALLBACK


class TestConfidenceTracksEvidence:
    def test_more_evidence_scores_higher(self) -> None:
        full = classify(
            error_source="bank",
            error_step="payment_authorization",
            error_reason="payment_failed_due_to_bank_timeout",
        )
        pair = classify(error_source="bank", error_step="payment_authorization")
        alone = classify(error_source="bank")
        nothing = classify(error_source=None)
        assert full.confidence == CONF_EXACT
        assert pair.confidence == CONF_SOURCE_STEP
        assert alone.confidence == CONF_SOURCE_ONLY
        assert nothing.confidence == CONF_NONE
        assert full.confidence > pair.confidence > alone.confidence > nothing.confidence

    def test_missing_fields_are_reported(self) -> None:
        result = classify(error_source="bank")
        assert "error_step" in result.missing_fields
        assert "error_reason" in result.missing_fields

    def test_no_telemetry_does_not_look_confident(self) -> None:
        result = classify(error_source=None, error_step=None, error_reason=None)
        assert result.category is FailureCategory.UNKNOWN
        assert result.confidence <= 0.3
        assert result.needs_llm_review


class TestConflictHandoff:
    """The boundary between deterministic code and the model."""

    def test_disagreeing_signals_lower_confidence_and_flag(self) -> None:
        result = classify(
            error_source="customer",
            error_step="payment_authorization",
            error_reason="payment_failed_due_to_bank_timeout",
        )
        assert result.signals_conflict
        assert result.confidence == CONF_CONFLICT
        assert result.needs_llm_review

    def test_the_more_specific_reason_wins_a_conflict(self) -> None:
        result = classify(
            error_source="customer",
            error_step="payment_authorization",
            error_reason="payment_failed_due_to_bank_timeout",
        )
        assert result.category is FailureCategory.RAIL_FAULT

    def test_agreeing_signals_are_not_flagged(self) -> None:
        result = classify(
            error_source="bank",
            error_step="payment_authorization",
            error_reason="payment_failed_due_to_bank_timeout",
        )
        assert not result.signals_conflict
        assert not result.needs_llm_review

    def test_reasoning_names_the_conflict(self) -> None:
        """The glass-box trace has to explain itself to a merchant."""
        text = classify(
            error_source="customer",
            error_step="payment_authorization",
            error_reason="payment_failed_due_to_bank_timeout",
        ).reasoning.lower()
        assert "conflict" in text
        assert "customer" in text and "bank" in text


class TestMarkerPrecedence:
    """Substring matching is a real technique with real ordering hazards."""

    def test_customer_agency_outranks_a_mechanism_word(self) -> None:
        """`otp_entry_timed_out_by_user` is a person giving up, not an outage.
        Matching 'timeout' first got this backwards (INC-003)."""
        result = classify(
            error_source="customer",
            error_step="payment_authentication",
            error_reason="otp_entry_timed_out_by_user",
        )
        assert result.category is FailureCategory.AUTHENTICATION_ABANDONED

    def test_funds_outrank_an_issuer_decline(self) -> None:
        """`card_declined_by_issuer_insufficient_funds` is a balance problem.
        Treating it as a risk block would stop us recovering an ordinary
        decline."""
        result = classify(
            error_source="customer",
            error_step="payment_authorization",
            error_reason="card_declined_by_issuer_insufficient_funds",
        )
        assert result.category is FailureCategory.INSUFFICIENT_FUNDS
        assert result.is_recoverable

    def test_mandate_outranks_everything_except_a_business_block(self) -> None:
        assert (
            classify(
                error_source="bank", error_reason="mandate_revoked_after_gateway_timeout"
            ).category
            is FailureCategory.MANDATE_INVALID
        )
        assert (
            classify(
                error_source="business", error_reason="mandate_revoked_after_gateway_timeout"
            ).category
            is FailureCategory.RISK_BLOCKED
        )


class TestUnrecognisedValues:
    """Razorpay will add values. Degrade, never crash."""

    def test_unknown_source_falls_back_to_the_reason(self) -> None:
        result = classify(
            error_source="acquirer", error_reason="payment_failed_due_to_bank_timeout"
        )
        assert result.category is FailureCategory.RAIL_FAULT
        assert "unrecognised" in result.reasoning.lower()

    def test_unknown_step_falls_back_to_the_source(self) -> None:
        result = classify(error_source="bank", error_step="payment_settlement")
        assert result.category is FailureCategory.RAIL_FAULT

    def test_nothing_recognised_is_honestly_unknown(self) -> None:
        result = classify(error_source="acquirer", error_step="payment_settlement")
        assert result.category is FailureCategory.UNKNOWN
        assert result.needs_llm_review


class TestAbandonedCheckout:
    """No failure, no telemetry -- only behaviour, so confidence stays low."""

    def test_repeat_customer_on_a_large_cart_reads_as_price_resistance(self) -> None:
        result = classify_abandoned_checkout(
            ltv_paise=1_480_000, prior_orders=4, cart_amount_paise=1_200_000
        )
        assert result.category is FailureCategory.PRICE_RESISTANCE
        assert result.discount_could_help

    def test_repeat_customer_on_a_normal_cart_reads_as_distraction(self) -> None:
        result = classify_abandoned_checkout(
            ltv_paise=1_480_000, prior_orders=4, cart_amount_paise=380_000
        )
        assert result.category is FailureCategory.INTENT_DECAY

    def test_first_time_buyer_is_explicitly_uncertain(self) -> None:
        """No baseline to compare against. Pretending a rule settles this
        would be the wrong tool in the right place."""
        result = classify_abandoned_checkout(ltv_paise=0, prior_orders=0, cart_amount_paise=429_900)
        assert result.needs_llm_review
        assert "purchase_history" in result.missing_fields

    def test_no_division_by_zero_on_an_empty_history(self) -> None:
        assert classify_abandoned_checkout(
            ltv_paise=0, prior_orders=0, cart_amount_paise=1
        ).category

    def test_abandonment_is_always_recoverable(self) -> None:
        """Nothing failed -- the cart is still winnable."""
        for orders, ltv in [(0, 0), (1, 100_000), (9, 4_120_000)]:
            assert classify_abandoned_checkout(
                ltv_paise=ltv, prior_orders=orders, cart_amount_paise=500_000
            ).is_recoverable
