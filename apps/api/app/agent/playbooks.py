"""Playbook-specific strategy, and the strategies each playbook forbids (§5).

Until now the strategy node routed on the *diagnosis* alone, which is right for
a failed checkout and wrong for everything else. A ₹18,500 invoice that is
merely overdue and a ₹4,299 card payment that timed out are not the same
problem, and sending a "fresh payment link" for the first is a category error.

Two jobs
--------

:func:`select_strategy` picks the deterministic strategy for a
(playbook, diagnosis) pair. :func:`violations` says which strategies a playbook
**forbids**, and is applied to the model's proposal as well as our own — the
LLM may argue for an action, it may not choose one the playbook rules out.

The distinction this module exists for
--------------------------------------

**Subscription failures split two ways, and the split is worth money.**

* ``INSUFFICIENT_FUNDS`` — the mandate is alive and the account was empty. The
  correct action is to **re-present the existing mandate** later, ideally after
  a salary date. A fresh payment link would work but throws away the mandate,
  converting a recurring customer into a one-off payment and losing every
  future collection.
* ``MANDATE_INVALID`` — the mandate is revoked, expired or not active.
  Re-presenting cannot succeed and **burns a scheme re-presentation**, of which
  NPCI allows a small number before penalties. The only action that can work is
  asking the customer to re-authorise.

Both arrive as "subscription payment failed". Treating them alike is the single
most expensive mistake available in this playbook, and it is why the classifier
carries ``requires_reauth`` as a distinct field rather than a confidence score.

**Receivables may never be discounted.** A discount on an invoice the customer
already owes is not an incentive, it is writing off a receivable — and unlike a
cart discount, the customer had already agreed to the price. That is a
commercial decision for a human, never an agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.db.enums import Channel, FailureCategory, MessageClass, Playbook, RecoveryStrategy

__all__ = [
    "DISCOUNT_PERMITTED",
    "PlaybookChoice",
    "select_strategy",
    "violations",
]

#: Playbooks where a discount is a legitimate lever at all.
#:
#: RECEIVABLE is absent deliberately: discounting an invoice already owed is
#: writing off a receivable, and the customer had already agreed the price.
#: SUBSCRIPTION is absent because the lever there is *timing*, not price — the
#: customer's card was empty on the 3rd, not overpriced.
DISCOUNT_PERMITTED: Final[frozenset[Playbook]] = frozenset(
    {Playbook.PAYMENT_FAILURE, Playbook.CHECKOUT_ABANDON}
)

#: Channel of first resort per playbook. A B2B invoice reminder belongs in
#: email where it can be forwarded to accounts payable; a failed consumer
#: checkout belongs where the customer already is.
_PREFERRED_CHANNEL: Final[dict[Playbook, Channel]] = {
    Playbook.PAYMENT_FAILURE: Channel.WHATSAPP,
    Playbook.CHECKOUT_ABANDON: Channel.WHATSAPP,
    Playbook.RECEIVABLE: Channel.EMAIL,
    Playbook.SUBSCRIPTION: Channel.WHATSAPP,
}


@dataclass(frozen=True)
class PlaybookChoice:
    """A deterministic strategy, and why."""

    strategy: RecoveryStrategy
    channel: Channel
    message_class: MessageClass
    rationale: str
    #: False when a discount cannot help or is not ours to give. The policy
    #: firewall clamps the amount; this decides whether the lever exists.
    discount_permitted: bool


def select_strategy(
    playbook: Playbook,
    *,
    category: FailureCategory | None,
    requires_reauth: bool,
    retry_same_rail: bool,
    rail_alternative: str | None = None,
) -> PlaybookChoice:
    """The deterministic strategy for this playbook and diagnosis.

    Pure and total: every (playbook, category) pair returns something, because
    an unrecognised combination must degrade to the cheapest safe action rather
    than raise inside the agent loop.
    """
    channel = _PREFERRED_CHANNEL[playbook]
    discount_ok = playbook in DISCOUNT_PERMITTED

    # --- absolute, before anything else -----------------------------------
    if category is FailureCategory.RISK_BLOCKED:
        return PlaybookChoice(
            RecoveryStrategy.NO_ACTION,
            channel,
            MessageClass.TRANSACTIONAL,
            "blocked by the merchant's own risk controls; not ours to route around",
            discount_permitted=False,
        )

    # --- subscription: the distinction that is worth money ----------------
    if playbook is Playbook.SUBSCRIPTION:
        if requires_reauth or category is FailureCategory.MANDATE_INVALID:
            return PlaybookChoice(
                RecoveryStrategy.MANDATE_REAUTH,
                channel,
                MessageClass.TRANSACTIONAL,
                (
                    "mandate is not active: re-presenting cannot succeed and burns a "
                    "scheme re-presentation, so the customer must re-authorise"
                ),
                discount_permitted=False,
            )
        if category is FailureCategory.INSUFFICIENT_FUNDS:
            return PlaybookChoice(
                RecoveryStrategy.MANDATE_RETRY,
                channel,
                MessageClass.TRANSACTIONAL,
                (
                    "mandate is alive and the account was empty: re-present it rather "
                    "than issue a link, which would throw away every future collection"
                ),
                discount_permitted=False,
            )
        return PlaybookChoice(
            RecoveryStrategy.MANDATE_RETRY,
            channel,
            MessageClass.TRANSACTIONAL,
            "subscription failure with a live mandate: re-present",
            discount_permitted=False,
        )

    # --- receivables: formal, never discounted ----------------------------
    if playbook is Playbook.RECEIVABLE:
        return PlaybookChoice(
            RecoveryStrategy.INVOICE_REMINDER,
            channel,
            MessageClass.TRANSACTIONAL,
            (
                "an overdue invoice is a reminder, not an offer: the price was already "
                "agreed, so a discount here writes off a receivable"
            ),
            discount_permitted=False,
        )

    # --- mandate trouble outside the subscription playbook ----------------
    if requires_reauth or category is FailureCategory.MANDATE_INVALID:
        return PlaybookChoice(
            RecoveryStrategy.MANDATE_REAUTH,
            channel,
            MessageClass.TRANSACTIONAL,
            "mandate is not active; re-authorisation required",
            discount_permitted=False,
        )

    # --- checkout abandonment ---------------------------------------------
    if playbook is Playbook.CHECKOUT_ABANDON:
        return PlaybookChoice(
            RecoveryStrategy.FRESH_LINK_SAME_RAIL,
            channel,
            MessageClass.TRANSACTIONAL,
            (
                "the cart is intact and nothing failed: a plain link at zero discount "
                "before any incentive is considered"
            ),
            discount_permitted=discount_ok,
        )

    # --- payment failure: rail health decides the rail --------------------
    if category is FailureCategory.RAIL_FAULT and rail_alternative is not None:
        return PlaybookChoice(
            RecoveryStrategy.FRESH_LINK_ALT_RAIL,
            channel,
            MessageClass.TRANSACTIONAL,
            f"the original rail is degraded; routing to {rail_alternative}",
            discount_permitted=discount_ok,
        )
    if not retry_same_rail and rail_alternative is not None:
        return PlaybookChoice(
            RecoveryStrategy.FRESH_LINK_ALT_RAIL,
            channel,
            MessageClass.TRANSACTIONAL,
            "the same rail is unlikely to succeed; switching",
            discount_permitted=discount_ok,
        )
    return PlaybookChoice(
        RecoveryStrategy.FRESH_LINK_SAME_RAIL,
        channel,
        MessageClass.TRANSACTIONAL,
        "cheapest action that could work, at zero discount",
        discount_permitted=discount_ok,
    )


#: Strategies each playbook rules out, whatever the model proposes.
_FORBIDDEN: Final[dict[Playbook, frozenset[RecoveryStrategy]]] = {
    # A recurring collection must not be converted into a one-off link: it
    # throws away the mandate and every future collection with it.
    Playbook.SUBSCRIPTION: frozenset(
        {
            RecoveryStrategy.FRESH_LINK_SAME_RAIL,
            RecoveryStrategy.FRESH_LINK_ALT_RAIL,
            RecoveryStrategy.INCENTIVISED_LINK,
        }
    ),
    # An invoice reminder is not a sales channel, and it is not a checkout
    # recovery either. A B2B invoice is settled by accounts payable, who need
    # the invoice number, the amount and the due date restated -- not "here is
    # a fresh payment link", which is consumer-checkout language and gives them
    # nothing to reconcile against. INVOICE_REMINDER carries a link too; the
    # difference is what surrounds it.
    Playbook.RECEIVABLE: frozenset(
        {
            RecoveryStrategy.INCENTIVISED_LINK,
            RecoveryStrategy.FRESH_LINK_SAME_RAIL,
            RecoveryStrategy.FRESH_LINK_ALT_RAIL,
        }
    ),
}


def violations(
    playbook: Playbook,
    strategy: RecoveryStrategy,
    discount_pct: float,
    *,
    requires_reauth: bool,
) -> tuple[str, ...]:
    """What is wrong with this proposal for this playbook.

    Applied to the **model's** proposal as well as our own. The LLM may argue
    for an action; it may not select one the playbook forbids, and the
    reasoning is that a plausible-sounding rationale is exactly what a model
    produces for a wrong action.

    Returns an empty tuple when the proposal is acceptable.
    """
    problems: list[str] = []

    if strategy in _FORBIDDEN.get(playbook, frozenset()):
        problems.append(
            f"{strategy.value} is not permitted for {playbook.value}: "
            + (
                "converting a mandate into a one-off link throws away every future collection"
                if playbook is Playbook.SUBSCRIPTION
                else "an overdue invoice needs the invoice number, amount and due "
                "date restated for accounts payable, not consumer-checkout language"
            )
        )

    if discount_pct > 0 and playbook not in DISCOUNT_PERMITTED:
        problems.append(
            f"a discount is not a lever for {playbook.value}: "
            + (
                "the price was already agreed, so this writes off a receivable"
                if playbook is Playbook.RECEIVABLE
                else "the card was empty, not overpriced; the lever is timing"
            )
        )

    # The expensive one. A dead mandate cannot be re-presented, and each
    # attempt consumes a scheme re-presentation.
    if requires_reauth and strategy is RecoveryStrategy.MANDATE_RETRY:
        problems.append(
            "MANDATE_RETRY on an inactive mandate cannot succeed and burns a "
            "scheme re-presentation; re-authorisation is the only action that works"
        )

    return tuple(problems)
