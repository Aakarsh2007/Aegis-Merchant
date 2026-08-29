"""Agent graph tests.

The Phase 7 Definition of Done, plus the edge cases that would leave a case
stuck. A stuck case is the one failure the stopping rules cannot rescue: it sits
in a non-terminal status with nothing scheduled to move it, and it is invisible
in every aggregate.
"""

from __future__ import annotations

import ast
import inspect
from datetime import timedelta
from pathlib import Path

import pytest

from app.agent import nodes as nodes_module
from app.agent.graph import MAX_NODE_VISITS, GraphBoundExceeded, next_node, run_case
from app.agent.nodes import AgentDeps
from app.agent.state import RecoveryState
from app.core.clock import FakeClock
from app.db.enums import (
    CaseStatus,
    Channel,
    ExperimentArm,
    FailureCategory,
    LLMSource,
    MessageClass,
    Playbook,
    PolicyVerdict,
    RecoveryStrategy,
)
from app.guardrails.policy_engine import PolicyLimitsFull
from app.guardrails.stopping_rules import PolicyLimits
from app.llm.adapter import StructuredResult
from app.llm.deterministic import DeterministicAdapter
from app.llm.schemas import ProposalOutput

CLOCK = FakeClock.at_ist(2026, 9, 1, 11, 30)


def deps(**overrides: object) -> AgentDeps:
    base: dict[str, object] = {
        "clock": CLOCK,
        "adapter": None,
        "stopping_limits": PolicyLimits(),
        "money_limits": PolicyLimitsFull(),
        "control_arm_fraction": 0.0,  # deterministic TREATMENT unless a test asks
    }
    base.update(overrides)
    return AgentDeps(**base)  # type: ignore[arg-type]


def ananya(**overrides: object) -> RecoveryState:
    """The hero case: Rs 4,299, bank-side UPI timeout, no marketing consent."""
    base: dict[str, object] = {
        "case_id": "RC-0142",
        "merchant_id": "mch_glowkart",
        "customer_id": "cus_0001",
        "playbook": Playbook.PAYMENT_FAILURE,
        "amount_paise": 429_900,
        "order_id": "order_glowkart_ananya01",
        "error_source": "bank",
        "error_step": "payment_authorization",
        "error_reason": "payment_failed_due_to_bank_timeout",
        "method": "upi",
        "issuer": "HDFC",
        "customer_first_name": "Ananya",
        "customer_ltv_paise": 1_480_000,
        "customer_prior_orders": 4,
        "consent_marketing": False,
        "consent_transactional": True,
        "order_status": "created",
        "window_expires_at": CLOCK.now_utc() + timedelta(hours=24),
        "rail_degraded": True,
        "rail_alternative": "upi/ICICI",
    }
    base.update(overrides)
    return RecoveryState(**base)  # type: ignore[arg-type]


# ===========================================================================
class TestDefinitionOfDone:
    """The three things Phase 7 must prove."""

    def test_execute_never_reads_the_proposal(self) -> None:
        """**The Phase 7 DoD.**

        The execution node must read only ``policy_applied``. If it could read
        the model's proposal, an unbounded number could reach a payment API --
        every clamp in the firewall would be advisory.

        Checked by walking the function's AST rather than by convention, so it
        cannot be reintroduced by someone who has not read the docstring.
        """
        source = inspect.getsource(nodes_module.execute_node)
        tree = ast.parse(source.lstrip())
        referenced = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert "proposal" not in referenced, (
            "execute_node references `proposal`. Only `policy_applied` may be "
            "executed -- the proposal is advisory (workflow.md §6.2)."
        )
        assert "policy_applied" in referenced

    async def test_the_node_budget_trips_on_a_synthetic_loop(self) -> None:
        """The graph is acyclic by construction; this catches the case where
        someone later adds an edge that makes it not so."""
        looping = ananya(node_visits=MAX_NODE_VISITS)
        with pytest.raises(GraphBoundExceeded, match="node visits"):
            await run_case(looping, deps())

    async def test_the_model_call_budget_is_enforced(self) -> None:
        """A case that somehow accumulates calls beyond the budget must stop,
        not keep spending."""

        class AlwaysLive:
            """Reports every answer as a live call, so the counter climbs."""

            name = "always-live"

            async def complete_structured(self, *, task, context, timeout_s=None):  # type: ignore[no-untyped-def]
                return StructuredResult(
                    task=task,
                    output=ProposalOutput(
                        strategy=RecoveryStrategy.FRESH_LINK_SAME_RAIL,
                        discount_pct=0.0,
                        link_validity_minutes=30,
                        channel=Channel.WHATSAPP,
                        message_class=MessageClass.TRANSACTIONAL,
                        rationale="proposal",
                    ),
                    source=LLMSource.LIVE,
                )

            async def health(self) -> bool:
                return True

        # Starts already over budget: the guard must fire rather than continue.
        state = ananya(llm_calls=5)
        with pytest.raises(GraphBoundExceeded, match="model calls"):
            await run_case(state, deps(adapter=AlwaysLive(), max_llm_calls=1))


# ===========================================================================
class TestTransitions:
    """The control flow is a pure function, so it can be tested directly."""

    def test_a_stopped_case_skips_diagnosis(self) -> None:
        """No token is spent on a case we are not permitted to act on."""
        stopped = ananya(status=CaseStatus.SUPPRESSED)
        assert next_node("TRIAGE", stopped) == "AUDIT"

    def test_a_live_case_proceeds_to_diagnosis(self) -> None:
        assert next_node("TRIAGE", ananya(status=CaseStatus.TRIAGED)) == "DIAGNOSE"

    @pytest.mark.parametrize(
        ("verdict", "expected"),
        [
            (PolicyVerdict.PASSED, "EXECUTE"),
            (PolicyVerdict.ESCALATE_HITL, "ESCALATE"),
            (PolicyVerdict.BLOCKED, "AUDIT"),
        ],
    )
    def test_policy_routes_by_verdict(self, verdict: PolicyVerdict, expected: str) -> None:
        assert next_node("POLICY", ananya(policy_verdict=verdict)) == expected

    def test_every_path_ends_at_audit(self) -> None:
        """So "every case is accounted for" is checkable rather than hopeful."""
        for node in ("EXECUTE", "ESCALATE", "TRIAGE", "POLICY"):
            state = ananya(status=CaseStatus.SUPPRESSED, policy_verdict=PolicyVerdict.BLOCKED)
            assert next_node(node, state) in ("AUDIT", "DIAGNOSE", "EXECUTE", "ESCALATE")
        assert next_node("AUDIT", ananya()) is None


# ===========================================================================
class TestEndToEnd:
    async def test_ananya_recovers_autonomously_at_zero_discount(self) -> None:
        """The flagship case, produced by the design rather than narrated.

        Bank-side rail fault, high LTV, no marketing consent. A discount is a
        marketing offer she never opted in to, so policy declines it -- and it
        must happen *without* a human, or every no-consent recovery would need
        one.
        """
        final = await run_case(ananya(), deps())

        assert final.status is CaseStatus.MONITORING
        assert final.policy_verdict is PolicyVerdict.PASSED
        assert final.diagnosis is not None
        assert final.diagnosis.category is FailureCategory.RAIL_FAULT
        assert final.policy_applied is not None
        assert final.policy_applied.discount_pct == 0.0
        assert final.policy_applied.escalation_rung.value == "A0_AUTONOMOUS"
        assert final.reference_id == "rvp_rc-0142_1"
        assert final.trace[-1].node == "AUDIT"

    async def test_the_trace_records_provenance_for_every_step(self) -> None:
        """A trace that presented Razorpay's fields, a SQL query and a model
        the same way would be prettier and worth much less (§19.1)."""
        final = await run_case(ananya(), deps())
        provenances = {t.provenance for t in final.trace}
        assert "policy firewall (deterministic)" in provenances
        assert any("rule table" in p or "model" in p for p in provenances)

    async def test_rahul_escalates_and_does_not_execute(self) -> None:
        final = await run_case(ananya(case_id="RC-0155", amount_paise=1_850_000), deps())
        assert final.status is CaseStatus.AWAITING_APPROVAL
        assert final.policy_verdict is PolicyVerdict.ESCALATE_HITL
        assert final.policy_token is None

    async def test_a_control_arm_case_is_observed_not_acted_on(self) -> None:
        """A CONTROL case doing nothing is it doing its job (§14.2)."""
        final = await run_case(ananya(), deps(control_arm_fraction=1.0))
        assert final.experiment_arm is ExperimentArm.CONTROL
        assert final.policy_verdict is PolicyVerdict.BLOCKED
        assert final.policy_token is None

    async def test_an_opted_out_customer_costs_nothing(self) -> None:
        """Stopped at TRIAGE, before diagnosis: zero model calls."""
        final = await run_case(ananya(consent_opted_out=True), deps(adapter=DeterministicAdapter()))
        assert final.status is CaseStatus.SUPPRESSED
        assert final.llm_calls == 0
        assert final.diagnosis is None
        assert [t.node for t in final.trace] == ["ENRICH", "TRIAGE", "AUDIT"]

    async def test_a_dead_mandate_never_proposes_a_retry(self) -> None:
        final = await run_case(
            ananya(
                playbook=Playbook.SUBSCRIPTION,
                method="emandate",
                error_reason="payment_failed_mandate_revoked_by_customer",
                amount_paise=99_900,
            ),
            deps(),
        )
        assert final.diagnosis is not None
        assert final.diagnosis.requires_reauth
        assert final.proposal is not None
        assert final.proposal.strategy is RecoveryStrategy.MANDATE_REAUTH

    async def test_a_risk_blocked_payment_is_never_acted_on(self) -> None:
        final = await run_case(
            ananya(
                error_source="business",
                error_step="payment_initiation",
                error_reason="payment_failed_due_to_risk_check",
            ),
            deps(),
        )
        assert final.diagnosis is not None
        assert final.diagnosis.category is FailureCategory.RISK_BLOCKED
        assert final.proposal is not None
        assert final.proposal.strategy is RecoveryStrategy.NO_ACTION


# ===========================================================================
class TestRobustness:
    """A stuck case is the one failure the stopping rules cannot rescue."""

    async def test_a_node_exception_finalises_the_case(self) -> None:
        class Exploding:
            name = "boom"

            async def complete_structured(self, **_: object) -> StructuredResult:
                raise RuntimeError("provider melted")

            async def health(self) -> bool:
                return True

        conflicting = ananya(
            error_source="customer",
            error_reason="payment_failed_due_to_bank_timeout",
        )
        final = await run_case(conflicting, deps(adapter=Exploding()))
        assert final.is_terminal
        assert final.trace[-1].node == "AUDIT"

    async def test_a_finished_case_is_a_no_op_on_re_entry(self) -> None:
        """Duplicate webhook deliveries make re-entry normal, not exceptional."""
        done = ananya(status=CaseStatus.RECOVERED, node_visits=7)
        assert await run_case(done, deps()) is done

    async def test_it_runs_with_no_adapter_at_all(self) -> None:
        """Judge Mode: the whole graph works with zero credentials."""
        final = await run_case(ananya(), deps(adapter=None))
        assert final.status is CaseStatus.MONITORING
        assert final.llm_calls == 0

    async def test_a_case_with_no_telemetry_still_terminates(self) -> None:
        final = await run_case(
            ananya(error_source=None, error_step=None, error_reason=None, method=None),
            deps(),
        )
        assert final.trace[-1].node == "AUDIT"
        assert final.diagnosis is not None

    async def test_a_zero_amount_case_is_blocked_not_executed(self) -> None:
        final = await run_case(ananya(amount_paise=0), deps())
        assert final.policy_verdict is PolicyVerdict.BLOCKED
        assert final.policy_token is None

    async def test_an_expired_window_stops_before_any_spend(self) -> None:
        final = await run_case(
            ananya(window_expires_at=CLOCK.now_utc() - timedelta(minutes=1)),
            deps(adapter=DeterministicAdapter()),
        )
        assert final.status is CaseStatus.EXPIRED
        assert final.llm_calls == 0

    async def test_a_normal_run_stays_well_inside_the_budget(self) -> None:
        final = await run_case(ananya(), deps())
        assert final.node_visits <= 7
        assert final.node_visits < MAX_NODE_VISITS


# ===========================================================================
class TestNoLangGraph:
    """DEC-019, enforced.

    ADL-008 chose LangGraph for checkpointed pause/resume. That justification
    does not survive what got built: a case awaiting approval is already a row
    in `recovery_cases`, and a checkpointer would store the same state twice --
    the duplicated-state defect from INC-007, one layer up. Resuming a frozen
    graph would also skip the policy re-check §6.1 requires immediately before
    acting.
    """

    def test_the_agent_does_not_depend_on_langgraph(self) -> None:
        app_dir = Path(__file__).resolve().parents[1] / "apps" / "api" / "app"
        offenders = []
        for path in app_dir.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                if any(m.split(".")[0] in {"langgraph", "langchain", "langsmith"} for m in mods):
                    offenders.append(f"{path.name}: {mods}")
        assert not offenders, (
            "LangGraph/LangChain imported. DEC-019 records why the graph is an "
            f"explicit state machine instead: {offenders}"
        )

    def test_it_is_not_in_requirements(self) -> None:
        reqs = (
            Path(__file__).resolve().parents[1] / "apps" / "api" / "requirements.txt"
        ).read_text(encoding="utf-8")
        assert "langgraph" not in reqs.lower()
        assert "langchain" not in reqs.lower()
