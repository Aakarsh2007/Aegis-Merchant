"""The seven nodes (workflow.md §6.1).

Each is an async function from state to state. None of them decides what runs
next — the runner in ``graph.py`` does, by inspecting typed state. That is what
makes termination provable and what stops a model from steering control flow.

The ordering that matters: **TRIAGE runs before DIAGNOSE.** Stopping rules,
consent, contact caps and the kill switch are all evaluated before a single
token is spent. An LLM call on a case we are not permitted to act on is pure
waste, and an earlier revision of the plan had exactly that bug.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Protocol

from app.agent.playbooks import select_strategy, violations
from app.agent.state import NodeTrace, RecoveryState
from app.core.clock import Clock
from app.db.enums import (
    CaseStatus,
    ExperimentArm,
    FailureCategory,
    LLMTask,
    PolicyVerdict,
    RecoveryStrategy,
)
from app.guardrails.policy_engine import (
    PolicyContext,
    PolicyLimitsFull,
    RecoveryProposal,
    evaluate_policy,
)
from app.guardrails.stopping_rules import Decision, PolicyLimits, StoppingContext, evaluate
from app.llm.adapter import LLMAdapter
from app.llm.routing import diagnose as routed_diagnose
from app.llm.schemas import ProposalOutput
from app.services.experiments import assign_arm

__all__ = [
    "AgentDeps",
    "Executor",
    "audit_node",
    "diagnose_node",
    "enrich_node",
    "escalate_node",
    "execute_node",
    "policy_node",
    "strategise_node",
    "triage_node",
]


class Executor(Protocol):
    """The seam Phase 8 fills with the transactional outbox.

    Typed as taking a token rather than an action: a write path that cannot be
    called without a capability is a write path that cannot be called by
    accident (§7).
    """

    async def execute(self, state: RecoveryState) -> RecoveryState: ...


class AgentDeps:
    """Everything the nodes need from the outside world.

    Injected rather than imported so a test can substitute a fake clock, a mock
    provider and a deterministic adapter without patching anything global.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        adapter: LLMAdapter | None = None,
        executor: Executor | None = None,
        stopping_limits: PolicyLimits | None = None,
        money_limits: PolicyLimitsFull | None = None,
        control_arm_fraction: float = 0.18,
        experiment_key: str = "revpilot_recovery_v1",
        max_llm_calls: int = 3,
    ) -> None:
        self.clock = clock
        self.adapter = adapter
        self.executor = executor
        self.stopping_limits = stopping_limits or PolicyLimits()
        self.money_limits = money_limits or PolicyLimitsFull()
        self.control_arm_fraction = control_arm_fraction
        self.experiment_key = experiment_key
        self.max_llm_calls = max_llm_calls


def _stopping_context(
    state: RecoveryState, deps: AgentDeps, **overrides: object
) -> StoppingContext:
    base = {
        "now_utc": deps.clock.now_utc(),
        "policy": deps.stopping_limits,
        "case_status": state.status,
        "attempt_no": state.attempt_no,
        "discount_bearing_attempts": state.discount_bearing_attempts,
        "window_expires_at": state.window_expires_at,
        "order_status": state.order_status,
        "opted_out": state.consent_opted_out,
        "dnd_registered": state.consent_dnd,
        "marketing_consent": state.consent_marketing,
        "transactional_consent": state.consent_transactional,
        "contacts_24h": state.contacts_24h,
        "contacts_48h": state.contacts_48h,
        "last_contact_at": state.last_contact_at,
    }
    base.update(overrides)
    return StoppingContext(**base)  # type: ignore[arg-type]


def _llm_context(state: RecoveryState) -> dict[str, object]:
    """The redacted view the model is allowed to see (§13.1).

    A first name and amount bands. No phone, no email, no address, no full
    order history. PII is masked at the enrichment boundary and never crosses
    this line.
    """
    return {
        "playbook": state.playbook.value,
        "error_source": state.error_source,
        "error_step": state.error_step,
        "error_reason": state.error_reason,
        "method": state.method,
        "customer_ltv_paise": state.customer_ltv_paise,
        "customer_prior_orders": state.customer_prior_orders,
        "amount_paise": state.amount_paise,
    }


# ===========================================================================
async def enrich_node(state: RecoveryState, deps: AgentDeps) -> RecoveryState:
    """Gather context. No decisions, no spend.

    In Phase 7 the caller supplies enrichment on the initial state; Phase 12
    wires the database reads. Kept as a node anyway so the trace has a place to
    record *what the agent knew* before it decided anything.
    """
    started = time.perf_counter()
    ltv = state.customer_ltv_paise / 100
    return state.with_trace(
        NodeTrace(
            node="ENRICH",
            summary=(
                f"LTV Rs {ltv:,.0f} - {state.customer_prior_orders} prior orders - "
                f"{state.contacts_48h} contacts/48h"
            ),
            provenance="database",
            at=deps.clock.now_utc(),
            duration_ms=int((time.perf_counter() - started) * 1000),
            detail={"rail_degraded": state.rail_degraded, "alternative": state.rail_alternative},
        )
    )


async def triage_node(state: RecoveryState, deps: AgentDeps) -> RecoveryState:
    """Stopping rules and arm assignment — **before any token is spent.**

    Two jobs, both cheap and both decisive. If the case must stop, it stops
    here and no model is consulted. If it is a CONTROL case, it is observed
    without intervention and its outcome is what makes the headline recovery
    figure falsifiable (§14.2).
    """
    started = time.perf_counter()
    verdict = evaluate(_stopping_context(state, deps))

    # One implementation, in app.services.experiments -- the arm must be
    # identical wherever it is computed, and a second copy here would be a
    # second thing to keep in sync (the INC-007 shape).
    assignment = assign_arm(
        state.case_id,
        experiment_key=deps.experiment_key,
        control_fraction=deps.control_arm_fraction,
    )
    arm = assignment.arm

    if verdict.decision is Decision.STOP:
        return state.with_trace(
            NodeTrace(
                node="TRIAGE",
                summary=f"stopped by {verdict.blocking_rule.value if verdict.blocking_rule else '?'}",
                provenance="policy",
                at=deps.clock.now_utc(),
                duration_ms=int((time.perf_counter() - started) * 1000),
                detail={"fired": [r.rule.value for r in verdict.fired]},
            ),
            status=verdict.terminal_status or CaseStatus.SUPPRESSED,
            stopping_rule_fired=verdict.blocking_rule,
            experiment_arm=arm,
            assignment_hash=assignment.assignment_hash,
        )

    return state.with_trace(
        NodeTrace(
            node="TRIAGE",
            summary=f"{len(verdict.results)}/12 stopping rules clear - arm {arm.value}",
            provenance="policy",
            at=deps.clock.now_utc(),
            duration_ms=int((time.perf_counter() - started) * 1000),
            detail={"arm": arm.value, "fired": [r.rule.value for r in verdict.fired]},
        ),
        status=CaseStatus.TRIAGED,
        experiment_arm=arm,
        assignment_hash=assignment.assignment_hash,
    )


async def diagnose_node(state: RecoveryState, deps: AgentDeps) -> RecoveryState:
    """Why the payment failed.

    The rule table answers; the model is consulted only where the rule table
    declares itself unsure. That routing is not a hedge — it is what the Phase 6
    measurement produced (DEC-017): the model scored 90.6% against the rule
    table's 96.5% overall, while matching it exactly on the conflicting-signal
    cases it exists to handle.
    """
    started = time.perf_counter()
    adapter = deps.adapter if state.llm_calls < deps.max_llm_calls else None
    routed = await routed_diagnose(_llm_context(state), adapter=adapter)
    diagnosis = routed.diagnosis

    provenance = "model" if routed.consulted_model else "rule table (no model call)"
    note = ""
    if routed.consulted_model and routed.model_disagreed:
        note = f" - model disagreed, said {routed.model_category.value if routed.model_category else '?'}"

    return state.with_trace(
        NodeTrace(
            node="DIAGNOSE",
            summary=f"{diagnosis.category.value} (confidence {diagnosis.confidence:.0%}){note}",
            provenance=provenance,
            at=deps.clock.now_utc(),
            duration_ms=int((time.perf_counter() - started) * 1000),
            detail={
                "reasoning": diagnosis.reasoning,
                "retry_same_rail": diagnosis.retry_same_rail,
                "requires_reauth": diagnosis.requires_reauth,
                "signals_conflict": diagnosis.signals_conflict,
            },
        ),
        status=CaseStatus.DIAGNOSING,
        diagnosis=diagnosis,
        consulted_model=routed.consulted_model,
        model_disagreed=routed.model_disagreed,
        llm_calls=state.llm_calls + (1 if routed.consulted_model else 0),
    )


async def strategise_node(state: RecoveryState, deps: AgentDeps) -> RecoveryState:
    """What to do about it. Produces a *proposal*, which is a request.

    Every number here may be reduced by the firewall and none of it reaches a
    payment API unchanged.
    """
    started = time.perf_counter()
    diagnosis = state.diagnosis
    consulted = False

    if diagnosis is None:
        proposal = RecoveryProposal(strategy=RecoveryStrategy.NO_ACTION)
    elif diagnosis.requires_reauth:
        # Retrying a dead mandate cannot succeed and burns a scheme
        # re-presentation, so this is decided deterministically rather than
        # asked about. Routed through the playbook module so there is one
        # place that knows what a dead mandate implies.
        proposal = _fallback_proposal(diagnosis, state)
    elif diagnosis.category is FailureCategory.RISK_BLOCKED:
        proposal = RecoveryProposal(
            strategy=RecoveryStrategy.NO_ACTION,
            rationale="blocked by the merchant's own risk controls",
        )
    elif deps.adapter is not None and state.llm_calls < deps.max_llm_calls:
        result = await deps.adapter.complete_structured(
            task=LLMTask.STRATEGISE,
            context={
                **_llm_context(state),
                "diagnosis_category": diagnosis.category.value,
                "retry_same_rail": diagnosis.retry_same_rail,
                "requires_reauth": diagnosis.requires_reauth,
                "discount_could_help": diagnosis.discount_could_help,
                "rail_alternative": state.rail_alternative,
            },
        )
        output = result.output
        consulted = result.source.value == "LIVE"
        if isinstance(output, ProposalOutput):
            # The model may ARGUE for an action; it may not select one the
            # playbook forbids. A plausible-sounding rationale is precisely
            # what a model produces for a wrong action, so the check is on the
            # action itself rather than on how well it was justified.
            problems = violations(
                state.playbook,
                output.strategy,
                output.discount_pct,
                requires_reauth=diagnosis.requires_reauth,
            )
            if problems:
                proposal = _fallback_proposal(diagnosis, state)
                proposal = RecoveryProposal(
                    strategy=proposal.strategy,
                    discount_pct=proposal.discount_pct,
                    link_validity_minutes=proposal.link_validity_minutes,
                    channel=proposal.channel,
                    message_class=proposal.message_class,
                    rationale=(
                        f"model proposed {output.strategy.value}, rejected by the "
                        f"{state.playbook.value} playbook ({problems[0]}); "
                        f"using {proposal.strategy.value}"
                    ),
                )
            else:
                proposal = RecoveryProposal(
                    strategy=output.strategy,
                    discount_pct=output.discount_pct,
                    link_validity_minutes=output.link_validity_minutes,
                    channel=output.channel,
                    message_class=output.message_class,
                    rationale=output.rationale,
                )
        else:
            proposal = _fallback_proposal(diagnosis, state)
    else:
        proposal = _fallback_proposal(diagnosis, state)

    return state.with_trace(
        NodeTrace(
            node="STRATEGISE",
            summary=(
                f"{proposal.strategy.value} - {proposal.discount_pct}% proposed - "
                f"{proposal.message_class.value.lower()}"
            ),
            provenance="model proposal" if consulted else "deterministic strategy",
            at=deps.clock.now_utc(),
            duration_ms=int((time.perf_counter() - started) * 1000),
            detail={"rationale": proposal.rationale},
        ),
        status=CaseStatus.STRATEGY_FORMED,
        proposal=proposal,
        llm_calls=state.llm_calls + (1 if consulted else 0),
    )


def _fallback_proposal(diagnosis: object, state: RecoveryState) -> RecoveryProposal:
    """The playbook's own answer, at zero discount.

    Playbook-aware since Phase 13. The previous version issued a fresh payment
    link for everything, which is right for a failed checkout and a category
    error for an overdue invoice or a live subscription mandate.
    """
    choice = select_strategy(
        state.playbook,
        category=getattr(diagnosis, "category", None),
        requires_reauth=bool(getattr(diagnosis, "requires_reauth", False)),
        retry_same_rail=bool(getattr(diagnosis, "retry_same_rail", True)),
        rail_alternative=state.rail_alternative,
    )
    return RecoveryProposal(
        strategy=choice.strategy,
        discount_pct=0.0,
        channel=choice.channel,
        message_class=choice.message_class,
        rationale=f"deterministic: {choice.rationale}",
    )


async def policy_node(state: RecoveryState, deps: AgentDeps) -> RecoveryState:
    """The firewall. The only place a capability token is minted.

    Stopping rules are re-evaluated here, not reused from TRIAGE: state changes
    in between, and the expensive mistake is discovering at execution time that
    the customer paid organically ten seconds ago.
    """
    started = time.perf_counter()
    proposal = state.proposal or RecoveryProposal(strategy=RecoveryStrategy.NO_ACTION)

    decision = evaluate_policy(
        proposal,
        PolicyContext(
            case_id=state.case_id,
            order_amount_paise=state.amount_paise,
            attempt_no=state.attempt_no + 1,
            arm=state.experiment_arm or ExperimentArm.TREATMENT,
            stopping=_stopping_context(state, deps),
            limits=deps.money_limits,
        ),
        now=deps.clock.now_utc(),
    )

    applied = decision.applied
    violations = len([c for c in decision.clamps if c.is_violation])
    if decision.verdict is PolicyVerdict.PASSED and applied is not None:
        summary = (
            f"PASSED - {applied.discount_pct}% applied - "
            f"{applied.escalation_rung.value} - {len(decision.clamps)} clamp(s)"
        )
        status = CaseStatus.EXECUTING
    elif decision.verdict is PolicyVerdict.ESCALATE_HITL:
        summary = f"ESCALATE - {decision.escalation_rung.value}"
        status = CaseStatus.AWAITING_APPROVAL
    else:
        summary = f"BLOCKED - {'; '.join(decision.block_reasons)[:120]}"
        status = _blocked_status(decision.block_reasons)

    return state.with_trace(
        NodeTrace(
            node="POLICY",
            summary=summary,
            provenance="policy firewall (deterministic)",
            at=deps.clock.now_utc(),
            duration_ms=int((time.perf_counter() - started) * 1000),
            detail={
                "clamps": [str(c) for c in decision.clamps],
                "violations": violations,
                "applied_hash": applied.content_hash() if applied else None,
            },
        ),
        status=status,
        policy_verdict=decision.verdict,
        policy_applied=applied,
        policy_token=decision.token,
        policy_clamps=decision.clamps,
        policy_block_reasons=decision.block_reasons,
        stopping_rule_fired=(
            decision.stopping.blocking_rule if decision.stopping else state.stopping_rule_fired
        ),
        reference_id=applied.reference_id if applied else None,
    )


def _blocked_status(reasons: tuple[str, ...]) -> CaseStatus:
    """A block is not one thing. A control-arm case is doing its job.

    The control branch returns OBSERVED_NO_ACTION, not RESOLVED_ORGANIC
    (INC-018). The two are easy to conflate and mean opposite things to the
    attribution layer: "we deliberately did nothing" versus "money arrived
    without us". Returning the latter for a case that had not settled counted
    every held case as a payment and inverted the measured lift.
    """
    joined = " ".join(reasons).lower()
    if "control" in joined:
        return CaseStatus.OBSERVED_NO_ACTION
    if "s-01" in joined or "resolved" in joined:
        return CaseStatus.RESOLVED_ORGANIC
    if "s-06" in joined or "window" in joined:
        return CaseStatus.EXPIRED
    return CaseStatus.SUPPRESSED


async def execute_node(state: RecoveryState, deps: AgentDeps) -> RecoveryState:
    """Perform the authorised action.

    This function reads ``policy_applied`` and ``policy_token``. It does not
    read the model's proposal, and a test walks this function's AST to prove
    it: mentioning ``proposal`` here would mean an unbounded number could reach
    a payment API.
    """
    started = time.perf_counter()
    applied = state.policy_applied
    token = state.policy_token
    if applied is None or token is None:
        return state.with_trace(
            NodeTrace(
                node="EXECUTE",
                summary="refused: no authorised action",
                provenance="policy firewall",
                at=deps.clock.now_utc(),
            ),
            status=CaseStatus.SUPPRESSED,
        )

    # The capability is verified at the call site, every time. A token that
    # was not minted by the firewall, or an action modified after
    # authorisation, raises here rather than reaching a provider.
    token.verify()

    if deps.executor is None:
        # Phase 8 fills this seam with the transactional outbox. Until then the
        # case reaches MONITORING without a side effect, which is honest: the
        # authorisation is real, the dispatch is not yet built.
        return state.with_trace(
            NodeTrace(
                node="EXECUTE",
                summary=(
                    f"authorised {applied.strategy.value} for Rs "
                    f"{applied.charge_amount_paise / 100:,.0f} (ref {applied.reference_id})"
                ),
                provenance="policy firewall",
                at=deps.clock.now_utc(),
                duration_ms=int((time.perf_counter() - started) * 1000),
                detail={"executor": "not wired until Phase 8"},
            ),
            status=CaseStatus.MONITORING,
            attempt_no=state.attempt_no + 1,
            discount_bearing_attempts=state.discount_bearing_attempts
            + (1 if applied.discount_pct > 0 else 0),
        )

    executed = await deps.executor.execute(state)
    return replace(executed, node_visits=state.node_visits + 1)


async def escalate_node(state: RecoveryState, deps: AgentDeps) -> RecoveryState:
    """Freeze the case and hand it to a human.

    The approval carries the *content hash* of the exact action, so approving
    one action and executing another is impossible (§13.5).
    """
    started = time.perf_counter()
    applied = state.policy_applied
    return state.with_trace(
        NodeTrace(
            node="ESCALATE",
            summary=(
                f"awaiting approval - {'; '.join(state.policy_block_reasons)[:100]}"
                if state.policy_block_reasons
                else "awaiting approval"
            ),
            provenance="policy firewall",
            at=deps.clock.now_utc(),
            duration_ms=int((time.perf_counter() - started) * 1000),
            detail={"applied_hash": applied.content_hash() if applied else None},
        ),
        status=CaseStatus.AWAITING_APPROVAL,
    )


async def audit_node(state: RecoveryState, deps: AgentDeps) -> RecoveryState:
    """Close the run.

    Phase 10 appends the hash-chained block here. For now the node exists so
    every path through the graph ends in the same place, which is what makes
    "every case is accounted for" checkable rather than hopeful.
    """
    started = time.perf_counter()
    return state.with_trace(
        NodeTrace(
            node="AUDIT",
            summary=f"case closed as {state.status.value}",
            provenance="audit ledger",
            at=deps.clock.now_utc(),
            duration_ms=int((time.perf_counter() - started) * 1000),
            detail=state.summary(),
        )
    )
