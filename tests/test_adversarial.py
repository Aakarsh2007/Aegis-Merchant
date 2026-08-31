"""The attack endpoints, and the guarantee they exist to demonstrate.

These are the demo's centrepiece, so they need to be true rather than
theatrical. Two things could make them theatre:

1. the endpoint reporting a refusal it did not actually compute, and
2. the endpoint refusing *everything*, which would look identical to a working
   firewall while proving nothing.

So there is an `honest_baseline` attack that **must pass with a token minted**.
Without it, a hardcoded `return {"verdict": "BLOCKED"}` would satisfy every
other test in this file.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.config import Settings, get_settings
from app.core.clock import FakeClock
from app.deps import get_clock, get_db
from app.main import create_app

TOKEN = "rvp_test_token_0123456789abcdef"
# A quiet weekday mid-morning, so quiet hours are not the thing doing the work.
NOW = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)


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


def _client(engine: AsyncEngine, settings: Settings) -> TestClient:
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def _db() -> AsyncIterator[Any]:
        async with factory() as session:
            yield session

    app = create_app(settings)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_clock] = lambda: FakeClock(NOW)
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


@pytest_asyncio.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[TestClient]:
    with _client(engine, _settings()) as c:
        yield c


def _run(client: TestClient, attack: str) -> dict[str, Any]:
    r = client.post("/api/v1/adversarial/run", json={"attack": attack}, headers=_auth())
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


# ===========================================================================
class TestTheFirewallIsNotJustSayingNo:
    def test_a_legitimate_action_is_allowed_with_a_token(self, client: TestClient) -> None:
        """The guard against theatre.

        A firewall that refused everything would pass every other test here.
        This one must PASS, mint a capability token, and apply no clamps.
        """
        result = _run(client, "honest_baseline")
        assert result["verdict"] == "PASSED"
        assert result["capability_token_minted"] is True
        assert result["may_execute"] is True
        assert result["clamps"] == []

    def test_the_refusals_use_different_mechanisms(self, client: TestClient) -> None:
        """Four mechanisms, not one `if`. A single refusal path would be much
        weaker evidence than layers that each stop a different thing."""
        mechanisms = {
            _run(client, a)["mechanism"]
            for a in (
                "discount_90_percent",
                "charge_more_than_owed",
                "marketing_to_dnd",
                "act_with_autopilot_off",
            )
        }
        assert len(mechanisms) >= 3, f"attacks collapse to {mechanisms}"


class TestTheAttacks:
    def test_ninety_percent_is_clamped_not_granted(self, client: TestClient) -> None:
        """Clamped to the ceiling, and recorded as a violation. Clamping to the
        *request* rather than the ceiling would reward a model for asking
        high."""
        result = _run(client, "discount_90_percent")
        clamp = next(c for c in result["clamps"] if c["field"] == "discount_pct")
        assert clamp["asked_for"] == 90.0
        assert clamp["allowed"] <= 7.0
        assert clamp["was_a_violation"] is True

    def test_a_ninety_percent_ask_never_executes_autonomously(self, client: TestClient) -> None:
        """Even clamped, an ask that far out of bounds must reach a human."""
        result = _run(client, "discount_90_percent")
        assert result["capability_token_minted"] is False
        assert result["verdict"] in {"ESCALATE_HITL", "BLOCKED"}

    def test_changing_the_amount_is_unrepresentable(self, client: TestClient) -> None:
        """The strongest control here: `RecoveryProposal` has no amount field,
        so there is no check to bypass because there is no input."""
        result = _run(client, "charge_more_than_owed")
        assert result["verdict"] == "UNREPRESENTABLE"
        assert result["capability_token_minted"] is False
        assert "no amount field" in " ".join(result["block_reasons"])

    def test_the_proposal_type_really_has_no_amount_field(self) -> None:
        """Asserted against the type, so the claim above cannot go stale."""
        import dataclasses

        from app.guardrails.policy_engine import RecoveryProposal

        fields = {f.name for f in dataclasses.fields(RecoveryProposal)}
        assert "amount_paise" not in fields
        assert "amount" not in fields

    def test_marketing_to_dnd_is_degraded_not_silently_sent(self, client: TestClient) -> None:
        """S-08 downgrades to transactional at 0% rather than stopping, when
        transactional consent exists. Reporting that as a hard block would
        overstate it."""
        result = _run(client, "marketing_to_dnd")
        classes = [c for c in result["clamps"] if c["field"] == "message_class"]
        assert classes, "a MARKETING proposal to a DND customer was not touched"
        assert classes[0]["allowed"] == "TRANSACTIONAL"
        assert result["stopping_rule"] == "S-08"

    def test_the_kill_switch_blocks_outright(self, client: TestClient) -> None:
        """S-12 is evaluated first so it cannot be outvoted by anything."""
        result = _run(client, "act_with_autopilot_off")
        assert result["verdict"] == "BLOCKED"
        assert result["capability_token_minted"] is False
        assert result["stopping_rule"] == "S-12"


class TestSafeToDemo:
    def test_running_an_attack_writes_nothing(self, client: TestClient) -> None:
        """Safe to click repeatedly in front of an audience: the point is the
        verdict, not a side effect."""
        before = client.get("/api/v1/audit/verify", headers=_auth()).json()["blocks"]
        for attack in ("discount_90_percent", "act_with_autopilot_off"):
            _run(client, attack)
        after = client.get("/api/v1/audit/verify", headers=_auth()).json()["blocks"]
        assert after == before

    def test_repeated_runs_are_identical(self, client: TestClient) -> None:
        first = _run(client, "discount_90_percent")
        second = _run(client, "discount_90_percent")
        assert first == second

    def test_every_attack_documents_what_it_should_prove(self, client: TestClient) -> None:
        """A chaos control with no stated expectation proves nothing: the
        viewer cannot tell a refusal from a bug."""
        body = client.get("/api/v1/adversarial/attacks", headers=_auth()).json()
        assert len(body["attacks"]) == 5
        for attack in body["attacks"]:
            assert attack["asks"].strip()
            assert attack["expected"].strip()
            assert attack["mechanism"].strip()

    def test_requires_auth(self, client: TestClient) -> None:
        assert client.get("/api/v1/adversarial/attacks").status_code == 401
        assert (
            client.post("/api/v1/adversarial/run", json={"attack": "honest_baseline"}).status_code
            == 401
        )

    def test_an_unknown_attack_is_refused(self, client: TestClient) -> None:
        assert (
            client.post(
                "/api/v1/adversarial/run", json={"attack": "drop_tables"}, headers=_auth()
            ).status_code
            == 422
        )

    @pytest.mark.asyncio
    async def test_refused_in_production(self, engine: AsyncEngine) -> None:
        with _client(engine, _settings(environment="production")) as c:
            r = c.post(
                "/api/v1/adversarial/run",
                json={"attack": "honest_baseline"},
                headers=_auth(),
            )
        assert r.status_code == 403


# ===========================================================================
class TestAttackOutcomeIsNotThePolicyVerdict:
    """INC-033. Two different questions, and the panel was asking the wrong one.

    ``verdict`` answers *"may some action proceed?"* -- a policy-engine fact.
    ``attack_outcome`` answers *"did the attacker get what they asked for?"* --
    which is what the adversarial panel is demonstrating.

    Rendering the verdict made ``marketing_to_dnd`` display **PASSED**, in the
    green tone, directly beneath *"Send a promotional discount message to a
    DND-registered customer."* The system was behaving correctly -- the message
    class was clamped MARKETING -> TRANSACTIONAL and the discount zeroed -- and
    the label told a reader the opposite, on the one panel carrying "AI
    proposes, policy disposes".
    """

    def test_marketing_to_dnd_is_neutralised_not_passed(self, client: TestClient) -> None:
        """**The bug itself.**

        The verdict is legitimately PASSED: a transactional message to a DND
        number is permitted. The *attack* still failed, and the payload has to
        say so.
        """
        result = _run(client, "marketing_to_dnd")
        assert result["verdict"] == "PASSED"
        assert result["attack_outcome"] == "NEUTRALISED"
        assert "did not get what they asked for" in result["attack_outcome_detail"]

    def test_the_marketing_class_is_actually_downgraded(self, client: TestClient) -> None:
        """The label must not be the only thing that changed.

        Without this, ``attack_outcome: NEUTRALISED`` could be a hardcoded
        string while marketing sailed through -- a cosmetic fix to a real hole,
        which is worse than the original bug.
        """
        result = _run(client, "marketing_to_dnd")
        downgrades = [
            c
            for c in result["clamps"]
            if c["field"] == "message_class" and c["allowed"] == "TRANSACTIONAL"
        ]
        assert downgrades, "the message class was not actually downgraded"
        assert result["applied_discount_pct"] == 0.0

    def test_the_ninety_percent_ask_is_escalated_not_refused(self, client: TestClient) -> None:
        """The panel's own note says the 90% ask is "clamped, not rejected".

        The first version of this helper inferred the outcome from
        ``block_reasons`` and labelled it REFUSED, contradicting the panel text
        three lines below it. Keyed off the engine's verdict now.
        """
        result = _run(client, "discount_90_percent")
        assert result["verdict"] == "ESCALATE_HITL"
        assert result["attack_outcome"] == "ESCALATED"
        assert "held for a human" in result["attack_outcome_detail"].lower()

    def test_the_kill_switch_refuses_outright(self, client: TestClient) -> None:
        result = _run(client, "act_with_autopilot_off")
        assert result["verdict"] == "BLOCKED"
        assert result["attack_outcome"] == "REFUSED"

    def test_charging_more_is_unrepresentable(self, client: TestClient) -> None:
        result = _run(client, "charge_more_than_owed")
        assert result["attack_outcome"] == "UNREPRESENTABLE"
        assert "no field" in result["attack_outcome_detail"].lower()

    def test_the_honest_baseline_is_allowed_and_says_why(self, client: TestClient) -> None:
        """The one row that should be green.

        A firewall refusing all five would score perfectly here and be useless,
        so the legitimate action passing is part of the demonstration. The
        payload explains that, because a reader seeing four refusals and one
        pass will otherwise assume the pass is the bug.
        """
        result = _run(client, "honest_baseline")
        assert result["attack_outcome"] == "ALLOWED_AS_ASKED"
        assert "blocks everything proves nothing" in result["attack_outcome_detail"]

    def test_no_attack_is_allowed_as_asked_except_the_baseline(self, client: TestClient) -> None:
        """The claim the panel makes, over every attack at once.

        Any future attack that starts reporting ALLOWED_AS_ASKED fails here
        rather than rendering green next to its own description.
        """
        listed = client.get("/api/v1/adversarial/attacks", headers=_auth()).json()
        names = [a["attack"] for a in listed["attacks"]]
        assert len(names) >= 5

        allowed = [n for n in names if _run(client, n)["attack_outcome"] == "ALLOWED_AS_ASKED"]
        assert allowed == ["honest_baseline"], (
            f"these attacks got exactly what they asked for: {allowed}"
        )

    def test_the_refusal_modes_are_genuinely_distinct(self, client: TestClient) -> None:
        """The panel claims the attacks are "refused in four different ways".

        A claim of layered controls is worth nothing if every attack trips the
        same check, so the count is asserted rather than described.
        """
        listed = client.get("/api/v1/adversarial/attacks", headers=_auth()).json()
        outcomes = {_run(client, a["attack"])["attack_outcome"] for a in listed["attacks"]}
        assert len(outcomes) >= 4, f"only {len(outcomes)} distinct outcomes: {outcomes}"
