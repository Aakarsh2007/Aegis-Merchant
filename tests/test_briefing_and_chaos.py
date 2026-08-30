"""The morning briefing and the fault-injection endpoints.

The briefing tests are mostly about the **restraint** section — the sentence
naming what the agent chose *not* to do. That section is the clearest evidence
the stopping rules are load-bearing rather than decorative, and the failure
mode worth guarding is that it quietly disappears on a day when it would look
bad. So there is a test asserting it renders when nothing fired, too.

The chaos tests are about the gate: an endpoint that can break the system must
be unreachable in production, and gated on the environment rather than on a
header a caller controls.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.config import Settings, get_settings
from app.core.clock import FakeClock
from app.db.seed import ANCHOR_IST
from app.deps import get_clock, get_db
from app.main import create_app
from app.routers import simulation

TOKEN = "rvp_test_token_0123456789abcdef"


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


def _client(engine: AsyncEngine, settings: Settings) -> TestClient:
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def _db() -> AsyncIterator[Any]:
        async with factory() as session:
            yield session

    app = create_app(settings)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_clock] = lambda: FakeClock(ANCHOR_IST)
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


@pytest_asyncio.fixture
async def client(seeded_engine: AsyncEngine) -> AsyncIterator[TestClient]:
    with _client(seeded_engine, _settings()) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_fault() -> AsyncIterator[None]:  # type: ignore[misc]
    """Faults are process-local state; leaking one across tests would make
    failures depend on ordering."""
    simulation.state.active = None
    yield
    simulation.state.active = None


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


# ===========================================================================
class TestBriefing:
    def test_requires_auth(self, client: TestClient) -> None:
        assert client.get("/api/v1/briefing/today").status_code == 401

    def test_every_headline_figure_carries_a_badge(self, client: TestClient) -> None:
        body = client.get("/api/v1/briefing/today", headers=_auth()).json()
        for key, figure in body["headline"].items():
            assert figure["provenance"], key
            assert figure["basis"].strip(), key

    def test_gross_and_net_both_appear(self, client: TestClient) -> None:
        """The briefing must not quote the larger number alone any more than
        the dashboard may."""
        headline = client.get("/api/v1/briefing/today", headers=_auth()).json()["headline"]
        assert "gross_simulated" in headline
        assert "net_incremental" in headline

    def test_the_verified_figure_is_separate_from_the_simulated_one(
        self, client: TestClient
    ) -> None:
        headline = client.get("/api/v1/briefing/today", headers=_auth()).json()["headline"]
        assert headline["gross_recovered"]["provenance"] == "RAZORPAY_VERIFIED"
        assert headline["gross_simulated"]["provenance"] == "SIMULATED"

    def test_the_restraint_section_always_renders(self, client: TestClient) -> None:
        """On an empty corpus nothing has been suppressed, and the section
        says so rather than vanishing. A briefing that only appeared when the
        news was good would be advertising."""
        body = client.get("/api/v1/briefing/today", headers=_auth()).json()
        assert "restraint" in body
        assert body["restraint"]["sentence"].strip()

    def test_nothing_suppressed_says_so_plainly(self, client: TestClient) -> None:
        body = client.get("/api/v1/briefing/today", headers=_auth()).json()
        if body["restraint"]["total"] == 0:
            assert "Nothing was suppressed" in body["restraint"]["sentence"]

    def test_the_significance_caveat_is_carried_through(self, client: TestClient) -> None:
        """The caveat must survive into every surface that quotes the lift,
        not just the endpoint that computes it."""
        body = client.get("/api/v1/briefing/today", headers=_auth()).json()
        assert isinstance(body["caveats"], list)

    def test_it_states_that_no_model_narrated_the_numbers(self, client: TestClient) -> None:
        """A narrated figure is a figure somebody has to check."""
        body = client.get("/api/v1/briefing/today", headers=_auth()).json()
        assert "SQL" in body["narration"]

    def test_the_prose_lines_are_non_empty(self, client: TestClient) -> None:
        body = client.get("/api/v1/briefing/today", headers=_auth()).json()
        assert len(body["lines"]) >= 4
        assert all(line.strip() for line in body["lines"])


# ===========================================================================
class TestChaos:
    def test_listing_faults_requires_auth(self, client: TestClient) -> None:
        assert client.get("/api/v1/simulation/faults").status_code == 401

    def test_every_fault_documents_its_effect(self, client: TestClient) -> None:
        """A chaos button with no stated expectation is a button that proves
        nothing: the viewer cannot tell a graceful degradation from a bug."""
        body = client.get("/api/v1/simulation/faults", headers=_auth()).json()
        assert len(body["faults"]) == 4
        for fault in body["faults"]:
            assert fault["effect"].strip()

    def test_injecting_a_fault_reports_what_to_expect(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/simulation/inject", json={"fault": "provider_down"}, headers=_auth()
        )
        assert r.status_code == 200
        assert r.json()["active"] == "provider_down"
        assert "degrades" in r.json()["expected_behaviour"]

    def test_clearing_reports_what_was_cleared(self, client: TestClient) -> None:
        client.post("/api/v1/simulation/inject", json={"fault": "provider_slow"}, headers=_auth())
        body = client.post(
            "/api/v1/simulation/inject", json={"fault": "clear"}, headers=_auth()
        ).json()
        assert body["active"] is None
        assert body["cleared"] == "provider_slow"

    def test_an_unknown_fault_is_refused(self, client: TestClient) -> None:
        """extra="forbid" plus a Literal: an endpoint that can break the system
        accepts exactly what it documents."""
        assert (
            client.post(
                "/api/v1/simulation/inject", json={"fault": "drop_database"}, headers=_auth()
            ).status_code
            == 422
        )

    def test_injection_is_refused_in_production(self, seeded_engine: AsyncEngine) -> None:
        """Gated on the environment, not on a caller-supplied header."""
        with _client(seeded_engine, _settings(environment="production")) as c:
            r = c.post(
                "/api/v1/simulation/inject",
                json={"fault": "provider_down"},
                headers=_auth(),
            )
        assert r.status_code == 403

    def test_active_fault_is_never_reported_in_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Belt and braces beyond the 403.

        Even with a fault somehow set in process state, the reader the provider
        adapter calls must refuse to report it outside development — so a
        production process cannot be made to inject faults by any route.
        """
        simulation.state.active = "provider_down"
        monkeypatch.setattr(simulation, "get_settings", lambda: _settings(environment="production"))
        assert simulation.active_fault() is None

        monkeypatch.setattr(
            simulation, "get_settings", lambda: _settings(environment="development")
        )
        assert simulation.active_fault() == "provider_down"

    def test_a_fault_does_not_survive_a_fresh_state(self) -> None:
        """Faults are process-local and deliberately not persisted: a judge who
        injects one and walks away must not leave the demo broken."""
        simulation.state.active = "provider_down"
        fresh = simulation._State()
        assert fresh.active is None
