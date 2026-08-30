"""Playbook routing, and the strategies each playbook forbids.

The expensive mistake this file guards is the subscription one. Both arrive as
"subscription payment failed":

* the mandate is **alive** and the account was empty — re-present it;
* the mandate is **dead** — re-presenting cannot succeed and burns a scheme
  re-presentation, of which NPCI allows only a few before penalties.

Treating them alike costs money in both directions: a fresh link for the first
throws away every future collection, and a retry on the second is a wasted
re-presentation.

The other half of the file is about the model. `violations()` is applied to the
LLM's proposal, not just our own, because a plausible rationale is exactly what
a model produces for a wrong action — so the check is on the action rather than
on how well it was argued for.
"""

from __future__ import annotations

import pytest

from app.agent.playbooks import (
    DISCOUNT_PERMITTED,
    select_strategy,
    violations,
)
from app.db.enums import Channel, FailureCategory, Playbook, RecoveryStrategy


def _choose(playbook: Playbook, **kw):  # type: ignore[no-untyped-def]
    return select_strategy(
        playbook,
        category=kw.get("category"),
        requires_reauth=kw.get("requires_reauth", False),
        retry_same_rail=kw.get("retry_same_rail", True),
        rail_alternative=kw.get("rail_alternative"),
    )


class TestSubscriptionIsTheExpensiveOne:
    def test_empty_account_re_presents_the_mandate(self) -> None:
        """The mandate is alive. Issuing a fresh link would work once and lose
        every future collection."""
        choice = _choose(Playbook.SUBSCRIPTION, category=FailureCategory.INSUFFICIENT_FUNDS)
        assert choice.strategy is RecoveryStrategy.MANDATE_RETRY

    def test_dead_mandate_asks_for_re_authorisation(self) -> None:
        """Re-presenting cannot succeed and burns a scheme re-presentation."""
        choice = _choose(Playbook.SUBSCRIPTION, category=FailureCategory.MANDATE_INVALID)
        assert choice.strategy is RecoveryStrategy.MANDATE_REAUTH

    def test_requires_reauth_overrides_the_category(self) -> None:
        """The classifier's `requires_reauth` is authoritative: if the mandate
        is dead, nothing else about the failure matters."""
        choice = _choose(
            Playbook.SUBSCRIPTION,
            category=FailureCategory.INSUFFICIENT_FUNDS,
            requires_reauth=True,
        )
        assert choice.strategy is RecoveryStrategy.MANDATE_REAUTH

    def test_the_two_paths_are_actually_different(self) -> None:
        """Guards against a refactor collapsing them — which would look fine
        in every individual test above."""
        alive = _choose(Playbook.SUBSCRIPTION, category=FailureCategory.INSUFFICIENT_FUNDS)
        dead = _choose(Playbook.SUBSCRIPTION, category=FailureCategory.MANDATE_INVALID)
        assert alive.strategy is not dead.strategy

    def test_a_subscription_is_never_converted_to_a_one_off_link(self) -> None:
        for strategy in (
            RecoveryStrategy.FRESH_LINK_SAME_RAIL,
            RecoveryStrategy.FRESH_LINK_ALT_RAIL,
            RecoveryStrategy.INCENTIVISED_LINK,
        ):
            problems = violations(Playbook.SUBSCRIPTION, strategy, 0.0, requires_reauth=False)
            assert problems, f"{strategy.value} should be rejected for a subscription"

    def test_retrying_a_dead_mandate_is_rejected_with_the_reason(self) -> None:
        """The single most expensive action available in this playbook."""
        problems = violations(
            Playbook.SUBSCRIPTION,
            RecoveryStrategy.MANDATE_RETRY,
            0.0,
            requires_reauth=True,
        )
        assert problems
        assert "re-presentation" in " ".join(problems)


class TestReceivables:
    def test_an_overdue_invoice_gets_a_reminder_not_a_checkout_link(self) -> None:
        """A B2B invoice is settled by accounts payable, who need the invoice
        number, amount and due date restated — not consumer-checkout
        language."""
        assert _choose(Playbook.RECEIVABLE).strategy is RecoveryStrategy.INVOICE_REMINDER

    def test_a_checkout_link_is_rejected_for_a_receivable(self) -> None:
        for strategy in (
            RecoveryStrategy.FRESH_LINK_SAME_RAIL,
            RecoveryStrategy.FRESH_LINK_ALT_RAIL,
            RecoveryStrategy.INCENTIVISED_LINK,
        ):
            assert violations(Playbook.RECEIVABLE, strategy, 0.0, requires_reauth=False)

    def test_receivables_go_to_email(self) -> None:
        """It needs to be forwardable to accounts payable."""
        assert _choose(Playbook.RECEIVABLE).channel is Channel.EMAIL

    def test_a_discount_on_an_owed_invoice_is_rejected(self) -> None:
        """Not an incentive — writing off a receivable. The customer had
        already agreed the price, so it is a commercial decision for a human."""
        problems = violations(
            Playbook.RECEIVABLE, RecoveryStrategy.INVOICE_REMINDER, 5.0, requires_reauth=False
        )
        assert problems
        assert "receivable" in " ".join(problems)

    def test_receivables_are_not_in_the_discount_permitted_set(self) -> None:
        assert Playbook.RECEIVABLE not in DISCOUNT_PERMITTED
        assert Playbook.SUBSCRIPTION not in DISCOUNT_PERMITTED


class TestPaymentFailureAndCheckout:
    def test_a_degraded_rail_routes_to_the_alternative(self) -> None:
        choice = _choose(
            Playbook.PAYMENT_FAILURE,
            category=FailureCategory.RAIL_FAULT,
            rail_alternative="CARD",
        )
        assert choice.strategy is RecoveryStrategy.FRESH_LINK_ALT_RAIL
        assert "CARD" in choice.rationale

    def test_no_alternative_means_the_same_rail(self) -> None:
        """Switching to a rail that does not exist is worse than retrying."""
        choice = _choose(
            Playbook.PAYMENT_FAILURE,
            category=FailureCategory.RAIL_FAULT,
            rail_alternative=None,
        )
        assert choice.strategy is RecoveryStrategy.FRESH_LINK_SAME_RAIL

    def test_abandonment_starts_at_zero_discount(self) -> None:
        """A plain link before any incentive: the cart is intact and nothing
        failed."""
        choice = _choose(Playbook.CHECKOUT_ABANDON, category=FailureCategory.INTENT_DECAY)
        assert choice.strategy is RecoveryStrategy.FRESH_LINK_SAME_RAIL
        assert choice.discount_permitted is True

    def test_a_discount_is_permitted_only_where_it_is_a_real_lever(self) -> None:
        assert _choose(Playbook.CHECKOUT_ABANDON).discount_permitted
        assert not _choose(Playbook.RECEIVABLE).discount_permitted
        assert not _choose(
            Playbook.SUBSCRIPTION, category=FailureCategory.INSUFFICIENT_FUNDS
        ).discount_permitted


class TestRiskBlockIsAbsolute:
    @pytest.mark.parametrize("playbook", list(Playbook))
    def test_no_playbook_routes_around_a_merchant_risk_block(self, playbook: Playbook) -> None:
        """INC-003, re-asserted at the playbook layer. Every new routing rule
        is a new candidate for going around the merchant's own controls."""
        choice = _choose(playbook, category=FailureCategory.RISK_BLOCKED)
        assert choice.strategy is RecoveryStrategy.NO_ACTION
        assert not choice.discount_permitted


class TestTotality:
    @pytest.mark.parametrize("playbook", list(Playbook))
    @pytest.mark.parametrize("category", [*list(FailureCategory), None])
    def test_every_combination_returns_a_strategy(
        self, playbook: Playbook, category: FailureCategory | None
    ) -> None:
        """Pure and total. An unrecognised pair must degrade to a safe action
        rather than raise inside the agent loop, where it would drop a
        recoverable payment."""
        choice = _choose(playbook, category=category)
        assert choice.strategy in set(RecoveryStrategy)
        assert choice.rationale.strip()

    def test_an_acceptable_proposal_reports_no_violations(self) -> None:
        """Guards the check from being vacuously strict — a `violations` that
        rejected everything would pass every test above."""
        assert (
            violations(
                Playbook.PAYMENT_FAILURE,
                RecoveryStrategy.FRESH_LINK_SAME_RAIL,
                5.0,
                requires_reauth=False,
            )
            == ()
        )
        assert (
            violations(
                Playbook.SUBSCRIPTION,
                RecoveryStrategy.MANDATE_REAUTH,
                0.0,
                requires_reauth=True,
            )
            == ()
        )
