"""The REST surface the dashboard consumes (§20).

Driven against the real seeded corpus rather than hand-built rows, so these
tests exercise the same data the demo shows. If a query is wrong the numbers
here move, which is the point.

Two tests carry more weight than the rest:

* ``test_no_money_field_escapes_without_provenance`` walks every metrics
  response looking for anything money-shaped and asserts a badge. It fails when
  someone *adds* a tile, rather than when someone remembers to update a list.
* ``test_overview_cannot_return_gross_without_net`` pins the rule that a caller
  cannot request the flattering half of the story.
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

TOKEN = "rvp_test_token_0123456789abcdef"

METRICS_ENDPOINTS = [
    "/api/v1/metrics/overview",
    "/api/v1/metrics/attribution",
    "/api/v1/metrics/cost",
    "/api/v1/metrics/stopping-rules",
]


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


@pytest_asyncio.fixture
async def client(seeded_engine: AsyncEngine) -> AsyncIterator[TestClient]:
    """A client over the real 420-transaction corpus."""
    clock = FakeClock(ANCHOR_IST)
    factory = async_sessionmaker(seeded_engine, expire_on_commit=False, autoflush=False)

    async def _db() -> AsyncIterator[Any]:
        async with factory() as session:
            yield session

    settings = _settings()
    app = create_app(settings)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_clock] = lambda: clock
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


# ===========================================================================
class TestAuthCoversEverything:
    """A money-adjacent endpoint that forgot its dependency is the failure
    this catches. Parametrised over the real route table, so a new endpoint is
    covered the moment it is registered."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/cases",
            "/api/v1/approvals",
            "/api/v1/audit/verify",
            "/api/v1/audit/ledger",
            "/api/v1/dlq",
            *METRICS_ENDPOINTS,
        ],
    )
    def test_every_read_endpoint_requires_a_bearer_token(
        self, client: TestClient, path: str
    ) -> None:
        assert client.get(path).status_code == 401, f"{path} is unauthenticated"

    def test_the_route_table_has_no_unprotected_api_endpoint(self, client: TestClient) -> None:
        """Belt and braces against the list above going stale.

        Webhooks are exempt: they authenticate by HMAC signature, because
        Razorpay cannot present a bearer token.
        """
        exempt = {"/api/v1/webhooks/razorpay", "/api/v1/health/deep"}
        app = client.app
        paths = {
            r.path  # type: ignore[attr-defined]
            for r in app.routes  # type: ignore[attr-defined]
            if getattr(r, "path", "").startswith("/api/v1/")
        }
        unchecked = sorted(paths - exempt)
        assert unchecked, "route discovery found nothing; the test would be vacuous"
        for path in unchecked:
            if "{" in path:
                continue
            assert client.get(path).status_code in {401, 405}, f"{path} answered unauthenticated"


# ===========================================================================
class TestProvenance:
    def _money_nodes(self, node: Any, trail: str = "") -> list[tuple[str, Any]]:
        """Find every dict that looks like a rupee figure."""
        found: list[tuple[str, Any]] = []
        if isinstance(node, dict):
            if "paise" in node:
                found.append((trail, node))
            for key, value in node.items():
                found.extend(self._money_nodes(value, f"{trail}.{key}"))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                found.extend(self._money_nodes(item, f"{trail}[{i}]"))
        return found

    @pytest.mark.parametrize("path", METRICS_ENDPOINTS)
    def test_no_money_field_escapes_without_provenance(self, client: TestClient, path: str) -> None:
        """The §14.5 rule, enforced against the actual response body.

        This fails when a tile is ADDED without a badge, which is the failure
        mode a hand-maintained list of fields cannot catch.
        """
        body = client.get(path, headers=_auth()).json()
        for trail, figure in self._money_nodes(body):
            assert "provenance" in figure, f"{path}{trail} is a rupee figure with no badge"
            assert figure["basis"].strip(), f"{path}{trail} has a badge with no basis"
            assert figure["provenance"] in {"RAZORPAY_VERIFIED", "SIMULATED", "ESTIMATED"}

    def test_the_sweep_actually_finds_figures(self, client: TestClient) -> None:
        """Guards the test above from being vacuous: if the walker found
        nothing, it would pass on a response with no badges at all."""
        body = client.get("/api/v1/metrics/overview", headers=_auth()).json()
        assert len(self._money_nodes(body)) >= 3


# ===========================================================================
class TestOverview:
    def test_overview_cannot_return_gross_without_net(self, client: TestClient) -> None:
        """A viewer given only the larger number will take it. Both are true;
        they answer different questions."""
        body = client.get("/api/v1/metrics/overview", headers=_auth()).json()
        assert "gross_recovered" in body
        assert "net_incremental" in body

    def test_gross_is_razorpay_verified_because_the_schema_enforces_it(
        self, client: TestClient
    ) -> None:
        """`recovery_requires_proof` is a CHECK constraint: a recovered amount
        cannot exist without a verifying webhook. The badge restates a
        guarantee the database already makes."""
        body = client.get("/api/v1/metrics/overview", headers=_auth()).json()
        assert body["gross_recovered"]["provenance"] == "RAZORPAY_VERIFIED"

    def test_simulated_recoveries_never_reach_the_verified_tile(self, client: TestClient) -> None:
        """The load-bearing separation (INC-018 / DEC-031).

        The schema forces *an* event id onto every recovery, so the batch
        simulator has to write one. If those ids were summed into the verified
        figure, seeded outcomes would be reported as webhook-proven — the exact
        overclaim the badge exists to prevent, and invisible from the outside.
        """
        body = client.get("/api/v1/metrics/overview", headers=_auth()).json()
        assert body["gross_simulated"]["provenance"] == "SIMULATED"
        # Two figures, never summed: §14.5 forbids a tile mixing provenance.
        assert body["gross_recovered"]["paise"] != body["gross_simulated"]["paise"] or (
            body["gross_recovered"]["paise"] == 0
        )

    def test_the_verified_figure_is_zero_before_live_traffic(self, client: TestClient) -> None:
        """Nothing has run against production, so the honest verified figure is
        zero — and it is shown rather than hidden, because a missing tile reads
        as "not measured" while a zero reads as "measured, and none"."""
        body = client.get("/api/v1/metrics/overview", headers=_auth()).json()
        assert body["gross_recovered"]["paise"] == 0

    def test_at_risk_is_simulated_not_verified(self, client: TestClient) -> None:
        """Money that has not moved cannot be verified by anything."""
        body = client.get("/api/v1/metrics/overview", headers=_auth()).json()
        assert body["at_risk"]["provenance"] == "SIMULATED"

    def test_counts_are_badged_too(self, client: TestClient) -> None:
        body = client.get("/api/v1/metrics/overview", headers=_auth()).json()
        for key in ("open_cases", "control_cases", "interceptions", "pending_approvals"):
            assert body[key]["provenance"], key
            assert body[key]["basis"].strip(), key


class TestCost:
    def test_actual_spend_is_zero_and_says_so(self, client: TestClient) -> None:
        body = client.get("/api/v1/metrics/cost", headers=_auth()).json()
        assert body["actual_spend"]["paise"] == 0

    def test_the_projection_is_estimated_not_measured(self, client: TestClient) -> None:
        """A published price list is not a bill."""
        body = client.get("/api/v1/metrics/cost", headers=_auth()).json()
        assert body["projected_spend"]["provenance"] == "ESTIMATED"


class TestStoppingRules:
    def test_every_rule_is_listed_including_the_ones_that_never_fired(
        self, client: TestClient
    ) -> None:
        """Returning only non-zero rules makes an inactive rule
        indistinguishable from an absent one, and "which brakes exist" is the
        question this panel answers."""
        body = client.get("/api/v1/metrics/stopping-rules", headers=_auth()).json()
        assert len(body["rules"]) == 12
        assert all("fired" in r for r in body["rules"])


# ===========================================================================
class TestCases:
    def test_the_corpus_is_visible(self, client: TestClient) -> None:
        body = client.get("/api/v1/cases", headers=_auth()).json()
        assert body["total"] >= 0
        assert body["limit"] == 50

    def test_pagination_bounds_are_enforced(self, client: TestClient) -> None:
        assert client.get("/api/v1/cases?limit=500", headers=_auth()).status_code == 422
        assert client.get("/api/v1/cases?offset=-1", headers=_auth()).status_code == 422

    def test_an_unknown_status_filter_is_refused(self, client: TestClient) -> None:
        """A typo'd filter must not silently return everything — that would
        show a judge an unfiltered list while the UI claimed it was filtered."""
        assert client.get("/api/v1/cases?status=NONSENSE", headers=_auth()).status_code == 422

    def test_the_control_arm_is_inspectable(self, client: TestClient) -> None:
        """A control arm nobody can look at is indistinguishable from one that
        does not exist."""
        r = client.get("/api/v1/cases?arm=CONTROL", headers=_auth())
        assert r.status_code == 200
        assert all(c["arm"] == "CONTROL" for c in r.json()["cases"])

    def test_unknown_case_is_404(self, client: TestClient) -> None:
        assert client.get("/api/v1/cases/RC-nope", headers=_auth()).status_code == 404


# ===========================================================================
class TestHealthDeep:
    def test_the_database_probe_is_real_and_reports_wal(self, client: TestClient) -> None:
        """Most of the concurrency design assumes WAL. A database silently in
        journal mode would be a slow, confusing failure."""
        checks = client.get("/api/v1/health/deep").json()["checks"]
        assert checks["database"]["ok"] is True
        assert checks["database"]["wal"] is True

    def test_queue_depths_are_numbers_now_not_placeholders(self, client: TestClient) -> None:
        checks = client.get("/api/v1/health/deep").json()["checks"]
        assert isinstance(checks["outbox_depth"], int)
        assert isinstance(checks["dlq_depth"], int)

    def test_the_audit_chain_is_probed(self, client: TestClient) -> None:
        checks = client.get("/api/v1/health/deep").json()["checks"]
        assert checks["audit_chain"]["valid"] is True

    def test_the_scheduler_admits_it_is_not_running(self, client: TestClient) -> None:
        """Claiming a sweeper is alive when nothing starts it is exactly the
        lie this endpoint must not tell."""
        checks = client.get("/api/v1/health/deep").json()["checks"]
        assert "not_running" in checks["scheduler"]


# ===========================================================================
class TestOpenAPIContract:
    """The frontend generates its types from this, so it must be complete."""

    def test_every_endpoint_is_documented(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        for path in (
            "/api/v1/cases",
            "/api/v1/cases/{case_id}",
            "/api/v1/metrics/overview",
            "/api/v1/metrics/attribution",
            "/api/v1/metrics/cost",
            "/api/v1/metrics/stopping-rules",
            "/api/v1/approvals",
            "/api/v1/audit/verify",
            "/api/v1/audit/ledger",
            "/api/v1/dlq",
            "/api/v1/stream/events",
        ):
            assert path in schema["paths"], f"{path} missing from OpenAPI"

    def test_every_operation_has_a_summary(self, client: TestClient) -> None:
        """Generated client method names come from these."""
        schema = client.get("/openapi.json").json()
        for path, operations in schema["paths"].items():
            for verb, op in operations.items():
                if verb in {"get", "post", "put", "delete", "patch"}:
                    assert op.get("summary"), f"{verb.upper()} {path} has no summary"
