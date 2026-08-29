"""The agent's state container (workflow.md §6.2).

One frozen object threaded through seven nodes. Each node returns a *new* state
rather than mutating one, so a node cannot quietly change what a later node
sees, and any step of a run can be reconstructed from the trace.

The field that matters most is the pair ``proposal`` / ``policy_applied``.
They are separate on purpose, and the whole safety argument depends on the
separation holding:

* ``proposal`` — what the model suggested. **Advisory. Never executed.**
* ``policy_applied`` — what the firewall authorised. **The only thing the
  execution node may read.**

``tests/test_agent_graph.py::test_execute_never_reads_the_proposal`` walks the
AST of the execute node and fails if it so much as mentions ``proposal`` — the
Phase 7 Definition of Done, enforced rather than asserted in prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from app.agent.classifier import Diagnosis
from app.db.enums import (
    CaseStatus,
    ExperimentArm,
    Playbook,
    PolicyVerdict,
    StoppingRule,
)
from app.guardrails.policy_engine import Clamp, RecoveryProposal
from app.guardrails.token import AppliedAction, PolicyToken

__all__ = ["NodeTrace", "RecoveryState"]


@dataclass(frozen=True)
class NodeTrace:
    """One step, as the merchant will read it in the decision trace (§19.1).

    ``provenance`` is what makes the trace honest: it says whether a fact came
    from Razorpay, from a statistics query over our own ledger, from policy, or
    from a model. A trace that presented all four the same way would be
    prettier and worth much less.
    """

    node: str
    summary: str
    provenance: str
    at: datetime
    duration_ms: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecoveryState:
    """Everything one case knows about itself, at one instant."""

    # --- identity ---
    case_id: str
    merchant_id: str
    customer_id: str
    playbook: Playbook
    status: CaseStatus = CaseStatus.DETECTED

    # --- provider linkage ---
    order_id: str | None = None
    payment_id: str | None = None
    invoice_id: str | None = None
    subscription_id: str | None = None
    amount_paise: int = 0
    currency: str = "INR"

    # --- raw telemetry, read from Razorpay and never inferred (§4.2 item 1) ---
    error_code: str | None = None
    error_source: str | None = None
    error_step: str | None = None
    error_reason: str | None = None
    method: str | None = None
    issuer: str | None = None

    # --- enrichment ---
    customer_first_name: str = ""
    customer_ltv_paise: int = 0
    customer_prior_orders: int = 0
    language_pref: str = "hinglish"
    contacts_24h: int = 0
    contacts_48h: int = 0
    last_contact_at: datetime | None = None
    consent_transactional: bool = True
    consent_marketing: bool = False
    consent_dnd: bool = False
    consent_opted_out: bool = False
    order_status: str | None = None
    rail_alternative: str | None = None
    rail_degraded: bool = False
    window_expires_at: datetime | None = None

    # --- experiment (assigned once, at TRIAGE, immutably) ---
    experiment_arm: ExperimentArm | None = None
    assignment_hash: str | None = None

    # --- cognitive outputs: advisory, never authoritative ---
    diagnosis: Diagnosis | None = None
    consulted_model: bool = False
    model_disagreed: bool = False
    proposal: RecoveryProposal | None = None

    # --- policy verdict: authoritative ---
    policy_verdict: PolicyVerdict | None = None
    #: The ONLY action the execute node may read.
    policy_applied: AppliedAction | None = None
    policy_token: PolicyToken | None = None
    policy_clamps: tuple[Clamp, ...] = ()
    policy_block_reasons: tuple[str, ...] = ()
    stopping_rule_fired: StoppingRule | None = None

    # --- execution ---
    attempt_no: int = 0
    discount_bearing_attempts: int = 0
    reference_id: str | None = None
    payment_link_url: str | None = None
    approval_request_id: str | None = None

    # --- observability ---
    trace_id: str = ""
    node_visits: int = 0
    llm_calls: int = 0
    trace: tuple[NodeTrace, ...] = ()
    error: str | None = None
    is_demo: bool = False

    # -- helpers -----------------------------------------------------------
    def with_trace(self, entry: NodeTrace, **changes: Any) -> RecoveryState:
        """Advance the state, recording the step that produced it."""
        return replace(
            self,
            trace=(*self.trace, entry),
            node_visits=self.node_visits + 1,
            **changes,
        )

    @property
    def is_terminal(self) -> bool:
        from app.db.enums import TERMINAL_STATUSES

        return self.status in TERMINAL_STATUSES

    @property
    def may_execute(self) -> bool:
        """Execution requires an authorised action *and* its capability token.

        Both, deliberately: the applied action alone is just a dataclass, and
        a token alone does not say what was authorised.
        """
        return (
            self.policy_verdict is PolicyVerdict.PASSED
            and self.policy_applied is not None
            and self.policy_token is not None
        )

    def summary(self) -> dict[str, Any]:
        """A compact view for logs and the API."""
        return {
            "case_id": self.case_id,
            "playbook": self.playbook.value,
            "status": self.status.value,
            "arm": self.experiment_arm.value if self.experiment_arm else None,
            "amount_paise": self.amount_paise,
            "diagnosis": self.diagnosis.category.value if self.diagnosis else None,
            "diagnosis_source": self.diagnosis.source.value if self.diagnosis else None,
            "consulted_model": self.consulted_model,
            "verdict": self.policy_verdict.value if self.policy_verdict else None,
            "stopping_rule": self.stopping_rule_fired.value if self.stopping_rule_fired else None,
            "clamps": len(self.policy_clamps),
            "violations": sum(1 for c in self.policy_clamps if c.is_violation),
            "nodes": self.node_visits,
            "llm_calls": self.llm_calls,
            "error": self.error,
        }
