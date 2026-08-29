"""How diagnosis is actually decided — the Phase 6 verdict, in code.

**The rule table ships. The model is consulted only where the rule table
declares itself unsure.**

That was not the expected outcome and it is not a hedge; it is what the
measurement said. Both runs are recorded in `docs/DECISIONS.md` DEC-017:

| system | overall | conflicting_signals |
|---|---|---|
| deterministic rule table | **96.5%** (82/85) | 10/10 |
| gemini-3.1-flash-lite, prompt v1 | 82.4% (70/85) | 10/10 |
| gemini-3.1-flash-lite, prompt v2 | 90.6% (77/85) | 10/10 |

§15.1 committed to this before the model existed: *if the model does not beat
the rule table, we ship the rule table and say so.* It did not, so we do.

The sub-result is the interesting one. On `conflicting_signals` — the band the
architecture says the model is *for*, where Razorpay's own fields disagree with
each other — the model scored **10/10 in both runs**, matching the rule table
exactly. It loses overall on cases the rule table already answers well, not on
the cases it was designed to be asked about.

So the routing here is not a compromise between two systems. It is the split
§4.2 specified before either was built, now supported by measurement instead of
assertion: deterministic code handles the unambiguous majority, and the model is
spent only on genuine ambiguity. Roughly **13% of real failures** reach it
(measured over the corpus in Phase 3), which is also what makes the free-tier
quota survivable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent.classifier import Diagnosis, classify, classify_abandoned_checkout
from app.db.enums import DiagnosisSource, FailureCategory, LLMTask
from app.llm.adapter import LLMAdapter
from app.llm.schemas import DiagnosisOutput

__all__ = ["RoutedDiagnosis", "diagnose"]


@dataclass(frozen=True)
class RoutedDiagnosis:
    """A diagnosis plus an honest account of where it came from."""

    diagnosis: Diagnosis
    #: True when the model was actually consulted. Drives the ⓘ annotations in
    #: the decision trace, which say which facts came from Razorpay, which from
    #: statistics, and which from a model (§19.1).
    consulted_model: bool
    #: Set when the model was asked and disagreed with the rule table. Recorded
    #: rather than resolved: a disagreement between two systems is information,
    #: and burying it would remove the only signal that the routing threshold
    #: might be wrong.
    model_disagreed: bool = False
    model_category: FailureCategory | None = None
    llm_source: str | None = None
    latency_ms: int = 0


async def diagnose(
    context: dict[str, Any],
    *,
    adapter: LLMAdapter | None = None,
) -> RoutedDiagnosis:
    """Diagnose a failure. Rule table first, model only where it is unsure.

    The classifier runs unconditionally — it is free and instant — and its
    ``needs_llm_review`` flag decides whether a token is spent. That ordering
    also means a model outage costs nothing: there is always an answer already
    in hand before the network is touched.
    """
    if context.get("playbook") == "CHECKOUT_ABANDON":
        rule = classify_abandoned_checkout(
            ltv_paise=int(context.get("customer_ltv_paise") or 0),
            prior_orders=int(context.get("customer_prior_orders") or 0),
            cart_amount_paise=int(context.get("amount_paise") or 0),
        )
    else:
        rule = classify(
            error_source=context.get("error_source"),
            error_step=context.get("error_step"),
            error_reason=context.get("error_reason"),
            method=context.get("method"),
        )

    if adapter is None or not rule.needs_llm_review:
        return RoutedDiagnosis(diagnosis=rule, consulted_model=False)

    result = await adapter.complete_structured(task=LLMTask.DIAGNOSE, context=context)
    output = result.output
    if not isinstance(output, DiagnosisOutput):
        return RoutedDiagnosis(diagnosis=rule, consulted_model=False)

    disagreed = output.category is not rule.category
    merged = Diagnosis(
        category=output.category,
        # Recovery implications stay with the rule table's mapping. The model
        # names the category; what that category *means* for retry, re-auth and
        # discount is policy, and policy is not something a model gets to
        # decide (§4.1).
        is_recoverable=output.is_recoverable and rule.category is not FailureCategory.RISK_BLOCKED,
        retry_same_rail=rule.retry_same_rail,
        requires_reauth=rule.requires_reauth or output.category is FailureCategory.MANDATE_INVALID,
        discount_could_help=rule.discount_could_help,
        confidence=output.confidence,
        reasoning=output.reasoning,
        source=DiagnosisSource.LLM,
        signals_conflict=rule.signals_conflict,
        missing_fields=rule.missing_fields,
    )
    return RoutedDiagnosis(
        diagnosis=merged,
        consulted_model=True,
        model_disagreed=disagreed,
        model_category=output.category,
        llm_source=result.source.value,
        latency_ms=result.latency_ms,
    )
