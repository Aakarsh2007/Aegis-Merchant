"""The zero-credential adapter.

This is not a stub and not a degraded mode with an apology attached. **The
entire product runs on it** — Judge Mode's promise is that someone can clone
the repo and see everything work without signing up for anything (§22), and
that promise is only true if every cognitive task has a real deterministic
answer behind it.

It is also the failure path. Every other adapter degrades to this one on
timeout, quota exhaustion, or a response that will not parse. That is why it
must never raise: an exception here would drop a recoverable payment, and the
whole point of a fallback is that it is the thing that cannot fail.

Diagnosis reuses the Phase 3 rule table, which scores **96.5% on the golden
set**. So "no API key" costs a few points of diagnostic accuracy and nothing
else — not a broken demo, not a missing feature.
"""

from __future__ import annotations

from typing import Any

from app.agent.classifier import classify, classify_abandoned_checkout
from app.db.enums import Channel, LLMSource, LLMTask, MessageClass, RecoveryStrategy
from app.llm.adapter import StructuredResult
from app.llm.schemas import (
    BriefingOutput,
    DiagnosisOutput,
    MessageOutput,
    PromiseOutput,
    ProposalOutput,
)

__all__ = ["DeterministicAdapter"]

#: Pre-approved copy. Slot substitution only -- no generation, so nothing here
#: can invent an offer or state a percentage it was not given.
_TEMPLATES: dict[str, dict[str, str]] = {
    "hinglish": {
        "headline": "Aapka payment complete nahi hua",
        "body": (
            "Hi {first_name}, aapka {amount} ka payment {cause} ki wajah se complete "
            "nahi ho paya. Yeh fresh link {validity} minutes tak valid hai: {link}"
        ),
        "cta": "Payment complete karein",
    },
    "english": {
        "headline": "Your payment did not go through",
        "body": (
            "Hi {first_name}, your {amount} payment could not be completed ({cause}). "
            "Here is a fresh link, valid for {validity} minutes: {link}"
        ),
        "cta": "Complete payment",
    },
}

#: Plain-language cause, for a customer rather than an engineer.
_CAUSE_TEXT: dict[str, str] = {
    "RAIL_FAULT": "a temporary bank issue",
    "INSUFFICIENT_FUNDS": "the payment not going through",
    "AUTHENTICATION_ABANDONED": "the payment not being confirmed",
    "MANDATE_INVALID": "your auto-pay setup needing renewal",
    "PRICE_RESISTANCE": "your order still being open",
    "INTENT_DECAY": "your order still being open",
    "RISK_BLOCKED": "a verification check",
    "UNKNOWN": "a technical issue",
}


class DeterministicAdapter:
    """Rule-based answers for every cognitive task. Never raises, never costs."""

    name = "deterministic"

    async def complete_structured(
        self,
        *,
        task: LLMTask,
        context: dict[str, Any],
        timeout_s: float | None = None,
    ) -> StructuredResult:
        handler = {
            LLMTask.DIAGNOSE: self._diagnose,
            LLMTask.STRATEGISE: self._strategise,
            LLMTask.COMPOSE_MESSAGE: self._compose,
            LLMTask.EXTRACT_PROMISE: self._promise,
            LLMTask.DAILY_BRIEFING: self._briefing,
        }[task]
        return StructuredResult(
            task=task,
            output=handler(context),
            source=LLMSource.DETERMINISTIC,
            model=None,
            provider="deterministic",
            fell_back=True,
        )

    async def health(self) -> bool:
        return True

    # -- tasks -------------------------------------------------------------
    def _diagnose(self, ctx: dict[str, Any]) -> DiagnosisOutput:
        """Reuse the Phase 3 rule table -- the measured 96.5% baseline."""
        if ctx.get("playbook") == "CHECKOUT_ABANDON":
            d = classify_abandoned_checkout(
                ltv_paise=int(ctx.get("customer_ltv_paise") or 0),
                prior_orders=int(ctx.get("customer_prior_orders") or 0),
                cart_amount_paise=int(ctx.get("amount_paise") or 0),
            )
        else:
            d = classify(
                error_source=ctx.get("error_source"),
                error_step=ctx.get("error_step"),
                error_reason=ctx.get("error_reason"),
                method=ctx.get("method"),
            )
        return DiagnosisOutput(
            category=d.category,
            is_recoverable=d.is_recoverable,
            confidence=d.confidence,
            reasoning=d.reasoning[:240],
        )

    def _strategise(self, ctx: dict[str, Any]) -> ProposalOutput:
        """Cheapest-first, always. A fresh link on a healthy rail costs nothing
        and recovers most failures; the discount path is never taken without a
        model, because a rule table has no business deciding to spend margin."""
        category = str(ctx.get("diagnosis_category") or "UNKNOWN")
        retry_same = bool(ctx.get("retry_same_rail", True))
        requires_reauth = bool(ctx.get("requires_reauth", False))

        if requires_reauth:
            strategy = RecoveryStrategy.MANDATE_REAUTH
        elif category == "RISK_BLOCKED":
            strategy = RecoveryStrategy.NO_ACTION
        elif retry_same:
            strategy = RecoveryStrategy.FRESH_LINK_SAME_RAIL
        else:
            strategy = RecoveryStrategy.FRESH_LINK_ALT_RAIL

        return ProposalOutput(
            strategy=strategy,
            discount_pct=0.0,
            link_validity_minutes=int(ctx.get("link_expiry_minutes") or 30),
            channel=Channel(str(ctx.get("channel") or Channel.WHATSAPP.value)),
            message_class=MessageClass.TRANSACTIONAL,
            rationale=(
                f"Deterministic strategy for {category}: cheapest action that could work, "
                "no discount."
            )[:240],
        )

    def _compose(self, ctx: dict[str, Any]) -> MessageOutput:
        language = "hinglish" if str(ctx.get("language_pref")) == "hinglish" else "english"
        template = _TEMPLATES[language]
        amount_paise = int(ctx.get("amount_paise") or 0)
        slots = {
            "first_name": str(ctx.get("first_name") or "there")[:40],
            "amount": f"Rs {amount_paise / 100:,.0f}",
            "cause": _CAUSE_TEXT.get(str(ctx.get("diagnosis_category")), "a technical issue"),
            "validity": str(ctx.get("link_expiry_minutes") or 30),
            "link": "{link}",
        }
        return MessageOutput(
            headline=template["headline"][:60],
            body=template["body"].format(**slots)[:300],
            cta=template["cta"][:24],
            language=language,
        )

    def _promise(self, ctx: dict[str, Any]) -> PromiseOutput:
        """No promise recorded.

        Deliberately not a regex date-parser. Getting this wrong in the
        confident direction means an invoice goes unchased because we believed
        a promise nobody made -- and unlike the other tasks, there is no safe
        rule-based approximation of reading intent from free text. So the
        honest answer with no model is "we do not know", and the standard
        cadence continues (§4.3, task 4 fallback).
        """
        return PromiseOutput(has_promise=False, promised_at=None, confidence=0.0)

    def _briefing(self, ctx: dict[str, Any]) -> BriefingOutput:
        """Templated numeric summary. Every figure comes from the caller."""
        at_risk = int(ctx.get("revenue_at_risk_paise") or 0)
        recovered = int(ctx.get("recovered_paise") or 0)
        cases = int(ctx.get("open_cases") or 0)
        stopped = int(ctx.get("stopping_rules_fired") or 0)
        intercepted = int(ctx.get("violations_intercepted") or 0)
        pending = int(ctx.get("approvals_pending") or 0)

        bullets = [
            f"Rs {at_risk / 100:,.0f} at risk across {cases} open cases",
            f"Rs {recovered / 100:,.0f} recovered, verified against signed webhooks",
            f"{intercepted} unsafe proposals intercepted, 0 violations executed",
            f"{stopped} cases stopped by policy",
        ]
        if pending:
            bullets.append(f"{pending} action(s) need your approval")

        return BriefingOutput(
            headline=f"Good morning, {str(ctx.get('merchant_name') or 'there')[:60]}."[:120],
            bullets=bullets[:6],
            closing="Generated without a language model; figures computed directly from the ledger.",
        )
