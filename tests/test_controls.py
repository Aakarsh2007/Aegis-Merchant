"""The kill switch and the live policy bounds.

INC-022 is the reason this file exists. Three of the twelve stopping rules
could not fire, because `StoppingContext` supplied safe-looking defaults —
autopilot on, no promise, zero spend — and nothing ever overrode them. The
rules were correct and proven to terminate; they were simply never fed.

So the tests here are not "does the endpoint return 200". They are: **does
turning the switch off actually stop the agent**, end to end, through the same
graph a real case goes through. A control that cannot be shown to stop
something is not a control.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.agent.graph import run_case
from app.agent.nodes import AgentDeps
from app.agent.state import RecoveryState
from app.config import Settings, get_settings
from app.core.clock import FakeClock
from app.db.enums import CaseStatus, Playbook, StoppingRule
from app.db.models import Merchant
from app.deps import get_clock, get_db
from app.llm.cache import CachedAdapter, ResponseCache
from app.main import create_app

TOKEN = "rvp_test_token_0123456789abcdef"
NOW = datetime(2026, 9, 1, 11, 30, tzinfo=UTC)


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "razorpay_key_id": "",
        "razorpay_key_secret": "",
        "gemini_api_key": "",
        "api_token": TOKEN,
        "environment": "development",
    }
    base.update(over)
    return Settings(**base)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _deps(clock: FakeClock) -> AgentDeps:
    return AgentDeps(
        clock=clock,
        adapter=CachedAdapter(cache=ResponseCache.load(), live=None, model="test"),
        control_arm_fraction=0.0,  # every case treated, so a stop is unambiguous
        experiment_key="test",
    )


def _state(**over: Any) -> RecoveryState:
    base: dict[str, Any] = {
        "case_id": "RC-KILL01",
        "merchant_id": "mch_glowkart",
        "customer_id": "cus_1",
        "playbook": Playbook.PAYMENT_FAILURE,
        "amount_paise": 429900,
        "order_id": "order_1",
        "error_source": "customer",
        "error_step": "payment_authentication",
        "error_reason": "payment_cancelled",
        "method": "card",
        "customer_first_name": "Ananya",
        "consent_transactional": True,
        "order_status": "created",
        "window_expires_at": NOW + timedelta(hours=24),
    }
    base.update(over)
    return RecoveryState(**base)


@pytest_asyncio.fixture
async def client(seeded_engine: AsyncEngine) -> AsyncIterator[TestClient]:
    factory = async_sessionmaker(seeded_engine, expire_on_commit=False, autoflush=False)

    async def _db() -> AsyncIterator[Any]:
        async with factory() as session:
            yield session

    settings = _settings()
    app = create_app(settings)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_clock] = lambda: FakeClock(NOW)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c


# ===========================================================================
class TestTheKillSwitchActuallyStops:
    """The test INC-022 would have failed. Everything else here is plumbing."""

    @pytest.mark.asyncio
    async def test_autopilot_off_stops_the_agent(self) -> None:
        clock = FakeClock(NOW)
        stopped = await run_case(_state(autopilot_enabled=False), _deps(clock))
        assert stopped.status is CaseStatus.SUPPRESSED
        assert stopped.stopping_rule_fired is StoppingRule.S12_KILL_SWITCH

    @pytest.mark.asyncio
    async def test_autopilot_on_lets_the_same_case_through(self) -> None:
        """The control. Without it, a graph that suppressed everything would
        pass the test above."""
        clock = FakeClock(NOW)
        allowed = await run_case(_state(autopilot_enabled=True), _deps(clock))
        assert allowed.status is not CaseStatus.SUPPRESSED
        assert allowed.stopping_rule_fired is not StoppingRule.S12_KILL_SWITCH

    @pytest.mark.asyncio
    async def test_the_budget_guard_can_fire(self) -> None:
        """S-11 was equally dead: `actions_today` never left zero."""
        clock = FakeClock(NOW)
        result = await run_case(_state(actions_today=10_000), _deps(clock))
        assert result.stopping_rule_fired is StoppingRule.S11_MERCHANT_BUDGET

    @pytest.mark.asyncio
    async def test_a_promise_to_pay_freezes_outreach(self) -> None:
        """S-10. An agent that keeps chasing someone who already said Friday
        is worse than no agent."""
        clock = FakeClock(NOW)
        result = await run_case(
            _state(promise_active=True, promised_at=NOW - timedelta(hours=2)),
            _deps(clock),
        )
        assert result.stopping_rule_fired is StoppingRule.S10_PROMISE_FREEZE

    @pytest.mark.asyncio
    async def test_every_stopping_rule_input_is_reachable_from_state(self) -> None:
        """The systematic version: a field on StoppingContext that the agent
        never populates is a rule that cannot fire, and it fails silently.

        This asserts the wiring rather than any single rule, so a future field
        added to the context without being plumbed through is caught here
        instead of by an incident.
        """
        import dataclasses
        import inspect

        from app.agent import nodes
        from app.guardrails.stopping_rules import StoppingContext

        source = inspect.getsource(nodes._stopping_context)
        # `policy` is the limits object; the proposal fields are supplied by the
        # policy engine at evaluation time rather than by the agent.
        supplied_elsewhere = {
            "policy",
            "proposed_message_class",
            "proposed_discount_pct",
            "is_outbound_contact",
        }
        missing = [
            f.name
            for f in dataclasses.fields(StoppingContext)
            if f.name not in supplied_elsewhere and f'"{f.name}"' not in source
        ]
        assert not missing, (
            f"never populated, so the rules reading them cannot fire: {missing} (INC-022)"
        )


# ===========================================================================
class TestToggleEndpoint:
    def test_requires_auth(self, client: TestClient) -> None:
        assert client.post("/api/v1/autopilot/toggle", json={"enabled": False}).status_code == 401

    def test_turning_it_off_persists(self, client: TestClient) -> None:
        r = client.post("/api/v1/autopilot/toggle", json={"enabled": False}, headers=_auth())
        assert r.status_code == 200
        assert r.json()["autopilot_enabled"] is False
        state = client.get("/api/v1/autopilot", headers=_auth()).json()
        assert state["all_enabled"] is False

    def test_toggling_twice_is_idempotent(self, client: TestClient) -> None:
        """A double-clicked dashboard toggle must not error."""
        client.post("/api/v1/autopilot/toggle", json={"enabled": False}, headers=_auth())
        second = client.post("/api/v1/autopilot/toggle", json={"enabled": False}, headers=_auth())
        assert second.status_code == 200
        assert second.json()["merchants_changed"] == 0

    def test_the_toggle_is_audited(self, client: TestClient) -> None:
        """ "Who turned the agent off, and when" is the question asked after an
        incident."""
        before = client.get("/api/v1/audit/verify", headers=_auth()).json()["blocks"]
        client.post(
            "/api/v1/autopilot/toggle",
            json={"enabled": False, "reason": "investigating a spike"},
            headers=_auth(),
        )
        after = client.get("/api/v1/audit/verify", headers=_auth()).json()
        assert after["blocks"] > before
        assert after["valid"] is True

    def test_an_unknown_field_is_refused(self, client: TestClient) -> None:
        assert (
            client.post(
                "/api/v1/autopilot/toggle",
                json={"enabled": False, "merchant_id": "someone_else"},
                headers=_auth(),
            ).status_code
            == 422
        )


# ===========================================================================
class TestPolicyTightening:
    def test_tightening_is_allowed(self, client: TestClient) -> None:
        r = client.post("/api/v1/policy", json={"max_discount_pct": 3.0}, headers=_auth())
        assert r.status_code == 200
        assert r.json()["applied"]["max_discount_pct"] == 3.0

    def test_loosening_is_refused_with_409(self, client: TestClient) -> None:
        """An endpoint that could raise the discount ceiling is an endpoint
        worth attacking. Loosening costs a config edit and a restart."""
        r = client.post("/api/v1/policy", json={"max_discount_pct": 90.0}, headers=_auth())
        assert r.status_code == 409
        assert "only tightens" in r.json()["detail"]

    def test_a_mixed_request_is_refused_entirely(self, client: TestClient) -> None:
        """One tighten and one loosen must not half-apply: partial application
        would leave the caller unsure which bound is in force."""
        r = client.post(
            "/api/v1/policy",
            json={"max_discount_pct": 3.0, "max_contacts_48h": 99},
            headers=_auth(),
        )
        assert r.status_code == 409
        assert client.get("/api/v1/health/deep").json()["policy"]["max_discount_pct"] == 7.0

    def test_an_empty_request_is_refused(self, client: TestClient) -> None:
        assert client.post("/api/v1/policy", json={}, headers=_auth()).status_code == 400

    def test_out_of_range_values_are_refused(self, client: TestClient) -> None:
        assert (
            client.post(
                "/api/v1/policy", json={"max_discount_pct": -1}, headers=_auth()
            ).status_code
            == 422
        )

    def test_tightening_is_audited(self, client: TestClient) -> None:
        before = client.get("/api/v1/audit/verify", headers=_auth()).json()["blocks"]
        client.post("/api/v1/policy", json={"max_contacts_48h": 1}, headers=_auth())
        after = client.get("/api/v1/audit/verify", headers=_auth()).json()
        assert after["blocks"] > before
        assert after["valid"] is True


class TestSimulationBatchEndpoint:
    def test_requires_auth(self, client: TestClient) -> None:
        assert client.post("/api/v1/simulation/batch").status_code == 401

    @pytest.mark.asyncio
    async def test_it_is_refused_in_production(self, seeded_engine: AsyncEngine) -> None:
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False, autoflush=False)

        async def _db() -> AsyncIterator[Any]:
            async with factory() as session:
                yield session

        settings = _settings(environment="production")
        app = create_app(settings)
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_settings] = lambda: settings
        with TestClient(app) as c:
            assert c.post("/api/v1/simulation/batch", headers=_auth()).status_code == 403


class TestSeededMerchantIsControllable:
    @pytest.mark.asyncio
    async def test_the_seeded_merchant_starts_enabled(self, seeded_engine: AsyncEngine) -> None:
        """If it shipped disabled, the demo would do nothing and look broken."""
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as s:
            merchants = (await s.execute(select(Merchant))).scalars().all()
        assert merchants
        assert all(m.autopilot_enabled for m in merchants)
