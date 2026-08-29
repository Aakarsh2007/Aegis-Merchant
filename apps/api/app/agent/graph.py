"""The agent runner (workflow.md §6.1).

Seven nodes, and **no LLM-controlled edges**. Every transition is decided here,
by deterministic code inspecting typed state. That is the property the whole
safety argument rests on: a model can influence *what a case is diagnosed as*,
and can never influence *what happens next*.

---

**On not using LangGraph.**

ADL-008 chose LangGraph, for one stated reason: checkpointed pause/resume, so a
case escalated to a human could suspend for hours and resume mid-graph. That
justification was written in the planning phase and does not survive contact
with what actually got built.

*The checkpoint already exists.* A case awaiting approval is a row in
``recovery_cases`` with ``status = AWAITING_APPROVAL``. That row is the
authoritative state — the dashboard reads it, the audit chain hashes it, the
attribution matcher queries it. A graph checkpointer would store the same case
state a second time, and the two would have to agree. Duplicated state that must
agree is precisely the defect INC-007 produced one phase earlier, where the
proposed message class lived in two places and diverged.

*And resuming is the wrong semantics anyway.* When a human approves, the correct
behaviour is **not** to continue a frozen graph. It is to reload the case and
re-run the policy firewall, because §6.1 requires the stopping rules to be
evaluated again immediately before acting — the customer may have paid in the
intervening four hours. Resuming a checkpoint would skip exactly the check that
exists to catch that.

So the graph is an explicit async state machine over the case row. The honest
trade: "we use LangGraph" is a more recognisable sentence, and a dependency
whose main feature would duplicate our own state is not a good reason to add
`langchain-core` and `langsmith` to a project whose Definition of Done is that a
judge clones it and it runs. Recorded as DEC-019; ADL-008 is superseded.

---

**Bounds.** Two, both hard:

* ``MAX_NODE_VISITS`` — a ceiling on steps, so no arrangement of transitions
  can loop. The graph is acyclic by construction; this catches the case where
  someone later adds an edge that makes it not so.
* ``max_llm_calls`` — a ceiling on spend per case, checked at every node that
  could make one.
"""

from __future__ import annotations

from dataclasses import replace

from app.agent.nodes import (
    AgentDeps,
    audit_node,
    diagnose_node,
    enrich_node,
    escalate_node,
    execute_node,
    policy_node,
    strategise_node,
    triage_node,
)
from app.agent.state import NodeTrace, RecoveryState
from app.db.enums import CaseStatus, PolicyVerdict

__all__ = ["MAX_NODE_VISITS", "NODES", "GraphBoundExceeded", "run_case"]

#: Seven nodes plus headroom. A well-behaved run visits at most 7; anything
#: approaching this means a transition rule is wrong.
MAX_NODE_VISITS = 9

NODES = {
    "ENRICH": enrich_node,
    "TRIAGE": triage_node,
    "DIAGNOSE": diagnose_node,
    "STRATEGISE": strategise_node,
    "POLICY": policy_node,
    "EXECUTE": execute_node,
    "ESCALATE": escalate_node,
    "AUDIT": audit_node,
}


class GraphBoundExceeded(RuntimeError):
    """The run exceeded its step budget. Should be unreachable."""


def next_node(current: str, state: RecoveryState) -> str | None:
    """The whole control flow, in one pure function.

    Deliberately a single readable function rather than edges scattered across
    node bodies: the set of things that can happen to a case is enumerable by
    reading twenty lines, and nothing a model returns appears in it.
    """
    if current == "ENRICH":
        return "TRIAGE"

    if current == "TRIAGE":
        # A stopped case skips diagnosis entirely -- no token is spent on a
        # case we are not permitted to act on.
        return "AUDIT" if state.is_terminal else "DIAGNOSE"

    if current == "DIAGNOSE":
        return "STRATEGISE"

    if current == "STRATEGISE":
        return "POLICY"

    if current == "POLICY":
        if state.policy_verdict is PolicyVerdict.PASSED:
            return "EXECUTE"
        if state.policy_verdict is PolicyVerdict.ESCALATE_HITL:
            return "ESCALATE"
        return "AUDIT"

    if current in ("EXECUTE", "ESCALATE"):
        return "AUDIT"

    return None  # AUDIT is terminal


async def run_case(state: RecoveryState, deps: AgentDeps) -> RecoveryState:
    """Run one case through the graph.

    Never raises for ordinary trouble. A node that throws is recorded on the
    state and the case is finalised as ``FAILED_PERMANENT`` rather than being
    left mid-flight — an exception escaping here would leave a case stuck in a
    non-terminal status with nothing scheduled to move it, which is the one
    outcome the stopping rules cannot rescue.
    """
    if state.is_terminal:
        # Re-entry on a finished case is a no-op, not an error. Duplicate
        # webhook deliveries make this a normal occurrence.
        return state

    current: str | None = "ENRICH"
    while current is not None:
        if state.node_visits >= MAX_NODE_VISITS:
            raise GraphBoundExceeded(
                f"case {state.case_id} exceeded {MAX_NODE_VISITS} node visits at {current}; "
                "a transition rule is wrong"
            )

        node = NODES[current]
        try:
            state = await node(state, deps)
        except Exception as exc:
            state = replace(
                state,
                status=CaseStatus.FAILED_PERMANENT,
                error=f"{current}: {type(exc).__name__}: {exc}",
                node_visits=state.node_visits + 1,
                trace=(
                    *state.trace,
                    NodeTrace(
                        node=current,
                        summary=f"unhandled {type(exc).__name__}",
                        provenance="error",
                        at=deps.clock.now_utc(),
                        detail={"error": str(exc)[:300]},
                    ),
                ),
            )
            return await audit_node(state, deps)

        if state.llm_calls > deps.max_llm_calls:
            raise GraphBoundExceeded(
                f"case {state.case_id} made {state.llm_calls} model calls, "
                f"over the budget of {deps.max_llm_calls}"
            )

        current = next_node(current, state)

    return state
