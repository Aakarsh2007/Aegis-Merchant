"""The audit endpoints and the bearer-auth contract (§13.5).

The demonstration this file protects is the one a judge will actually run:
call `/audit/verify` (valid), tamper with a block, call it again (invalid, at
the right index, with a reason that names what broke). If that sequence ever
stops working the verifier has become decoration.

The auth tests are mostly about the *unset token* case, because that is where
a convenience default turns into an open API.
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
from app.deps import get_clock, get_db
from app.main import create_app
from app.security.auth import verify_approval_hash
from app.tools.audit import AuditChain

TOKEN = "rvp_test_token_0123456789abcdef"


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "razorpay_key_id": "",
        "razorpay_key_secret": "",
        "gemini_api_key": "",
        "api_token": "",
        "environment": "development",
    }
    base.update(over)
    return Settings(**base)


async def _seed_chain(engine: AsyncEngine, clock: FakeClock, n: int = 5) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    chain = AuditChain(clock)
    async with factory() as s:
        for i in range(n):
            await chain.append(
                s, event_name="case.transitioned", actor="agent", payload={"step": i}
            )
        await s.commit()


def _client(engine: AsyncEngine, clock: FakeClock, settings: Settings) -> TestClient:
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def _db() -> AsyncIterator[Any]:
        async with factory() as session:
            yield session

    app = create_app(settings)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_clock] = lambda: clock
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


@pytest_asyncio.fixture
async def open_client(engine: AsyncEngine, clock: FakeClock) -> AsyncIterator[TestClient]:
    """No token configured, development: the Judge Mode posture."""
    await _seed_chain(engine, clock)
    with _client(engine, clock, _settings()) as c:
        yield c


@pytest_asyncio.fixture
async def secured_client(engine: AsyncEngine, clock: FakeClock) -> AsyncIterator[TestClient]:
    await _seed_chain(engine, clock)
    with _client(engine, clock, _settings(api_token=TOKEN)) as c:
        yield c


# ===========================================================================
# The demonstration
# ===========================================================================
class TestTheDemo:
    def test_verify_then_tamper_then_verify(self, open_client: TestClient) -> None:
        """The exact sequence a judge runs. If this passes, the verifier has
        been *seen* to fail, which is the only way to know it can."""
        before = open_client.get("/api/v1/audit/verify").json()
        assert before["valid"] is True
        assert before["blocks"] == 5
        assert before["head_hash"]

        tampered = open_client.post(
            "/api/v1/audit/tamper", json={"block_index": 2, "mode": "payload"}
        )
        assert tampered.status_code == 200

        after = open_client.get("/api/v1/audit/verify").json()
        assert after["valid"] is False
        assert after["first_divergence_index"] == 2
        assert "payload" in after["reason"]

    @pytest.mark.parametrize("mode", ["payload", "hash", "timestamp"])
    def test_every_tamper_mode_is_caught(self, open_client: TestClient, mode: str) -> None:
        """Three different corruptions exercise three different checks. A
        verifier that only caught one would pass a single-mode test."""
        open_client.post("/api/v1/audit/tamper", json={"block_index": 1, "mode": mode})
        result = open_client.get("/api/v1/audit/verify").json()
        assert result["valid"] is False, f"{mode} tamper went undetected"
        assert result["first_divergence_index"] == 1

    def test_tamper_rejects_an_unknown_block(self, open_client: TestClient) -> None:
        assert (
            open_client.post(
                "/api/v1/audit/tamper", json={"block_index": 999, "mode": "payload"}
            ).status_code
            == 404
        )

    def test_tamper_rejects_an_unknown_mode(self, open_client: TestClient) -> None:
        """`extra="forbid"` plus a pattern: an endpoint that damages the audit
        log should accept exactly what it documents and nothing else."""
        assert (
            open_client.post(
                "/api/v1/audit/tamper", json={"block_index": 0, "mode": "drop_table"}
            ).status_code
            == 422
        )

    def test_blocks_are_readable(self, open_client: TestClient) -> None:
        body = open_client.get("/api/v1/audit/ledger").json()
        assert len(body["blocks"]) == 5
        assert body["blocks"][0]["prev_hash"] == "0" * 64


# ===========================================================================
# Auth
# ===========================================================================
class TestBearerAuth:
    def test_no_token_is_rejected(self, secured_client: TestClient) -> None:
        r = secured_client.get("/api/v1/audit/verify")
        assert r.status_code == 401
        assert r.headers.get("WWW-Authenticate") == "Bearer"

    def test_correct_token_is_accepted(self, secured_client: TestClient) -> None:
        r = secured_client.get("/api/v1/audit/verify", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200
        assert r.json()["valid"] is True

    @pytest.mark.parametrize(
        "header",
        [
            "Bearer wrong-token",
            f"Basic {TOKEN}",
            f"{TOKEN}",
            "Bearer ",
            "Bearer",
            "",
            f"bearer {TOKEN[:-1]}",
        ],
    )
    def test_malformed_or_wrong_credentials_are_rejected(
        self, secured_client: TestClient, header: str
    ) -> None:
        assert (
            secured_client.get(
                "/api/v1/audit/verify", headers={"Authorization": header}
            ).status_code
            == 401
        )

    def test_lowercase_scheme_is_accepted(self, secured_client: TestClient) -> None:
        """RFC 7235 makes the scheme case-insensitive. Rejecting `bearer`
        would be a spec violation that looks like an auth bug in the field."""
        assert (
            secured_client.get(
                "/api/v1/audit/verify", headers={"Authorization": f"bearer {TOKEN}"}
            ).status_code
            == 200
        )

    def test_wrong_and_missing_give_the_same_message(self, secured_client: TestClient) -> None:
        """Distinguishing them tells an attacker which half of the problem
        they have already solved."""
        missing = secured_client.get("/api/v1/audit/verify").json()
        wrong = secured_client.get(
            "/api/v1/audit/verify", headers={"Authorization": "Bearer nope"}
        ).json()
        assert missing["detail"] == wrong["detail"]

    def test_prefix_of_a_valid_token_is_rejected(self, secured_client: TestClient) -> None:
        """The shape a naive `startswith` or truncating comparison would let
        through."""
        assert (
            secured_client.get(
                "/api/v1/audit/verify", headers={"Authorization": f"Bearer {TOKEN[:10]}"}
            ).status_code
            == 401
        )


class TestAuthPosture:
    def test_open_mode_says_so_on_every_response(self, open_client: TestClient) -> None:
        """An open API that looks identical to a secured one is the part
        worth preventing."""
        r = open_client.get("/api/v1/audit/verify")
        assert r.status_code == 200
        assert r.headers["X-Auth-Mode"] == "disabled"

    def test_secured_mode_says_so_too(self, secured_client: TestClient) -> None:
        r = secured_client.get("/api/v1/audit/verify", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.headers["X-Auth-Mode"] == "enforced"

    def test_health_deep_reports_the_posture(self, open_client: TestClient) -> None:
        body = open_client.get("/api/v1/health/deep").json()
        assert body["auth"] == "disabled"
        # Phase 12a turned this from a string naming the endpoint into a live
        # probe that recomputes the chain. This client has a seeded chain, so
        # the probe should find it valid.
        chain = body["checks"]["audit_chain"]
        assert chain["endpoint"] == "GET /api/v1/audit/verify"
        assert chain["valid"] is True
        assert chain["blocks"] == 5

    def test_production_without_a_token_refuses_to_start(self) -> None:
        """The one that matters.

        The realistic failure is not a weak token, it is a service that boots
        happily with authentication silently disabled because nobody set one.
        Failing at startup is loud; failing at request time would leave every
        endpoint open until somebody noticed.
        """
        with pytest.raises(RuntimeError, match="API_TOKEN"):
            create_app(_settings(environment="production", api_token=""))

    def test_production_with_a_token_starts(self) -> None:
        assert create_app(_settings(environment="production", api_token=TOKEN)) is not None

    def test_tamper_is_refused_in_production(self, engine: AsyncEngine, clock: FakeClock) -> None:
        """An endpoint that damages the audit log must be unreachable in
        production, gated on the environment rather than a caller header."""
        settings = _settings(environment="production", api_token=TOKEN)
        with _client(engine, clock, settings) as c:
            r = c.post(
                "/api/v1/audit/tamper",
                json={"block_index": 0, "mode": "payload"},
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
        assert r.status_code == 403


# ===========================================================================
# The approval-hash guard (wired to the approvals endpoint in Phase 11)
# ===========================================================================
class TestApprovalHashGuard:
    def test_matching_hash_passes(self) -> None:
        verify_approval_hash(presented="a" * 64, current="a" * 64)

    def test_mismatch_is_409_not_400(self) -> None:
        """409, because the request was well-formed and the state moved
        underneath it. That tells the operator to re-read and re-approve
        rather than to fix their request."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            verify_approval_hash(presented="a" * 64, current="b" * 64)
        assert exc.value.status_code == 409

    def test_a_single_changed_character_is_caught(self) -> None:
        """A human approved a specific discount for a specific customer. If
        anything about that action changed between display and execution,
        their approval no longer refers to what will happen."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            verify_approval_hash(presented="a" * 63 + "b", current="a" * 64)
