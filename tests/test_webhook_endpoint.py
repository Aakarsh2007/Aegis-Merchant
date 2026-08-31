"""End-to-end webhook ingestion tests, driven from the real fixtures."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.config import Settings, get_settings
from app.core.clock import FakeClock
from app.db.models import WebhookEvent
from app.deps import get_clock, get_db
from app.main import create_app

FIXTURES = Path(__file__).parent / "fixtures" / "razorpay"
SECRET = "whsec_glowkart_test"


def load_fixture(name: str) -> dict[str, Any]:
    payload = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    payload.pop("_fixture_meta", None)
    return payload


def body_and_signature(payload: dict[str, Any], secret: str = SECRET) -> tuple[bytes, str]:
    """Serialise once and sign those exact bytes.

    Mirrors reality: Razorpay signs the bytes it puts on the wire, so the test
    must never re-serialise between signing and sending.
    """
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return raw, hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        razorpay_webhook_secret=SECRET,
        razorpay_key_id="",
        razorpay_key_secret="",
        gemini_api_key="",
    )


@pytest.fixture
def clock() -> FakeClock:
    """Positioned just after the fixtures' `created_at`, so they are fresh."""
    return FakeClock(datetime.fromtimestamp(1788240900, tz=UTC))


@pytest_asyncio.fixture
async def client(
    engine: AsyncEngine, settings: Settings, clock: FakeClock
) -> AsyncIterator[TestClient]:
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def _db() -> AsyncIterator[Any]:
        async with factory() as session:
            yield session

    app = create_app(settings)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_clock] = lambda: clock
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c


async def count_events(engine: AsyncEngine) -> int:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        return int(await s.scalar(select(func.count()).select_from(WebhookEvent)) or 0)


def post(
    client: TestClient,
    payload: dict[str, Any],
    *,
    event_id: str,
    secret: str = SECRET,
    now: FakeClock | None = None,
):
    """POST a signed webhook.

    ``now`` re-stamps ``created_at`` so the event is fresh relative to the test
    clock. The fixtures carry realistic timestamps spanning ~40 minutes, which
    is wider than the 300s replay window -- so a test about *routing* must say
    that it wants a fresh event, and a test about *replay* must deliberately
    omit this. Making that explicit is the point: the first run of these tests
    returned 401 for three routing cases, and the replay defence was right.
    """
    if now is not None:
        payload = {**payload, "created_at": int(now.now_utc().timestamp())}
    raw, signature = body_and_signature(payload, secret)
    return client.post(
        "/api/v1/webhooks/razorpay",
        content=raw,
        headers={
            "X-Razorpay-Signature": signature,
            "X-Razorpay-Event-Id": event_id,
            "Content-Type": "application/json",
        },
    )


# ---------------------------------------------------------------------------
class TestAcceptance:
    def test_valid_payment_failed_accepted(self, client: TestClient) -> None:
        r = post(client, load_fixture("payment.failed"), event_id="evt_001")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "accepted"
        assert body["event_type"] == "payment.failed"
        assert body["playbook"] == "PAYMENT_FAILURE"
        assert body["actionable"] is True

    async def test_event_is_persisted(self, client: TestClient, engine: AsyncEngine) -> None:
        post(client, load_fixture("payment.failed"), event_id="evt_002")
        assert await count_events(engine) == 1

    def test_subscription_routes_to_playbook_four(
        self, client: TestClient, clock: FakeClock
    ) -> None:
        r = post(client, load_fixture("subscription.pending"), event_id="evt_003", now=clock)
        assert r.json()["playbook"] == "SUBSCRIPTION"

    def test_resolution_event_opens_no_case(self, client: TestClient, clock: FakeClock) -> None:
        """order.paid closes exposure. Treating it as risk would be the
        classic double-count: the payment that proves a recovery counted as a
        fresh opportunity to recover."""
        r = post(client, load_fixture("order.paid"), event_id="evt_004", now=clock)
        assert r.status_code == 200
        assert r.json()["playbook"] is None
        assert r.json()["actionable"] is False

    def test_unknown_event_accepted_not_500(self, client: TestClient, clock: FakeClock) -> None:
        """Razorpay retries non-2xx, so an exception on an unrecognised event
        type would become an infinite retry loop."""
        r = post(client, load_fixture("unknown.event"), event_id="evt_005", now=clock)
        assert r.status_code == 200
        assert r.json()["actionable"] is False


# ---------------------------------------------------------------------------
class TestRejection:
    async def test_forged_signature_rejected_and_not_stored(
        self, client: TestClient, engine: AsyncEngine
    ) -> None:
        raw, _ = body_and_signature(load_fixture("payment.failed"))
        r = client.post(
            "/api/v1/webhooks/razorpay",
            content=raw,
            headers={"X-Razorpay-Signature": "0" * 64, "X-Razorpay-Event-Id": "evt_forged"},
        )
        assert r.status_code == 401
        # A rejected event must leave no trace that could later be mistaken
        # for a verified one.
        assert await count_events(engine) == 0

    def test_missing_signature_rejected(self, client: TestClient) -> None:
        raw, _ = body_and_signature(load_fixture("payment.failed"))
        r = client.post(
            "/api/v1/webhooks/razorpay",
            content=raw,
            headers={"X-Razorpay-Event-Id": "evt_nosig"},
        )
        assert r.status_code == 401

    def test_body_tampered_after_signing_rejected(self, client: TestClient) -> None:
        payload = load_fixture("payment.failed")
        _original, signature = body_and_signature(payload)
        # Attacker inflates the amount but keeps the original signature.
        payload["payload"]["payment"]["entity"]["amount"] = 99999999
        tampered = json.dumps(payload, separators=(",", ":")).encode()
        r = client.post(
            "/api/v1/webhooks/razorpay",
            content=tampered,
            headers={"X-Razorpay-Signature": signature, "X-Razorpay-Event-Id": "evt_tamper"},
        )
        assert r.status_code == 401

    def test_absurdly_old_event_rejected(self, client: TestClient) -> None:
        """A week old. Beyond any plausible provider retry.

        The window was 300 seconds until INC-024, which rejected Razorpay's own
        retries -- by the second attempt a legitimate event was "stale" and
        every delivery was refused with a valid signature. It is now 24 hours,
        and this asserts the remaining purpose: discarding events so old that
        no retry explains them.
        """
        payload = load_fixture("payment.failed")
        payload["created_at"] = 1788240900 - 7 * 24 * 3600
        r = post(client, payload, event_id="evt_ancient")
        assert r.status_code == 401
        assert "too_old" in r.json()["reason"]

    def test_a_retry_hours_later_is_accepted(self, client: TestClient) -> None:
        """The INC-024 regression, stated as a requirement.

        Razorpay retries a failed delivery for hours. An event eleven hours old
        is a normal retry, not an attack, and rejecting it loses a real
        recovery. Replay is prevented by UNIQUE(event_id) -- see below -- which
        is strictly stronger than a clock, since a timestamp is
        attacker-controlled data inside a signed payload.
        """
        payload = load_fixture("payment.failed")
        payload["created_at"] = 1788240900 - 11 * 3600
        r = post(client, payload, event_id="evt_legit_retry")
        assert r.status_code == 200, r.text

    def test_missing_event_id_rejected(self, client: TestClient) -> None:
        """Without an event id we cannot deduplicate, so accepting one risks
        processing the same event twice."""
        raw, signature = body_and_signature(load_fixture("payment.failed"))
        r = client.post(
            "/api/v1/webhooks/razorpay",
            content=raw,
            headers={"X-Razorpay-Signature": signature},
        )
        assert r.status_code == 400
        assert r.json()["reason"] == "event_id_header_missing"

    def test_malformed_json_with_valid_signature_rejected(self, client: TestClient) -> None:
        raw = b"{not json at all"
        signature = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        r = client.post(
            "/api/v1/webhooks/razorpay",
            content=raw,
            headers={"X-Razorpay-Signature": signature, "X-Razorpay-Event-Id": "evt_bad"},
        )
        assert r.status_code == 400

    def test_rejection_does_not_disclose_which_check_failed(self, client: TestClient) -> None:
        """A probe learns that it was rejected, not how to get closer."""
        raw, _ = body_and_signature(load_fixture("payment.failed"))
        r = client.post(
            "/api/v1/webhooks/razorpay",
            content=raw,
            headers={"X-Razorpay-Signature": "deadbeef", "X-Razorpay-Event-Id": "evt_probe"},
        )
        assert r.json()["reason"] == "signature_mismatch"
        assert "expected" not in r.text.lower()
        assert SECRET not in r.text


# ---------------------------------------------------------------------------
class TestIdempotency:
    async def test_duplicate_delivery_acknowledged_once(
        self, client: TestClient, engine: AsyncEngine
    ) -> None:
        payload = load_fixture("payment.failed")
        first = post(client, payload, event_id="evt_dup")
        second = post(client, payload, event_id="evt_dup")

        assert first.json()["status"] == "accepted"
        # 200, not an error: an error would make Razorpay retry an event we
        # have already handled.
        assert second.status_code == 200
        assert second.json()["status"] == "duplicate"
        assert await count_events(engine) == 1

    async def test_five_deliveries_store_one_row(
        self, client: TestClient, engine: AsyncEngine
    ) -> None:
        payload = load_fixture("payment.failed")
        results = [post(client, payload, event_id="evt_storm").json() for _ in range(5)]
        assert sum(r["status"] == "accepted" for r in results) == 1
        assert sum(r["status"] == "duplicate" for r in results) == 4
        assert await count_events(engine) == 1

    async def test_distinct_events_both_stored(
        self, client: TestClient, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        post(client, load_fixture("payment.failed"), event_id="evt_a", now=clock)
        post(client, load_fixture("order.paid"), event_id="evt_b", now=clock)
        assert await count_events(engine) == 2


# ---------------------------------------------------------------------------
class TestStoredRecord:
    async def test_raw_body_stored_verbatim(self, client: TestClient, engine: AsyncEngine) -> None:
        """The stored payload must be the bytes we verified.

        Storing a re-serialised copy would make the audit trail unable to
        re-verify its own signature later.
        """
        payload = load_fixture("payment.failed")
        raw, _ = body_and_signature(payload)
        post(client, payload, event_id="evt_raw")

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            row = await s.scalar(select(WebhookEvent).where(WebhookEvent.event_id == "evt_raw"))
        assert row is not None
        assert row.payload_json.encode("utf-8") == raw
        assert row.signature_valid is True
        assert row.event_ts is not None


class TestReplayWindowAtTheEndpoint:
    def test_replay_is_stopped_by_the_event_id_not_the_clock(
        self, client: TestClient, clock: FakeClock
    ) -> None:
        """The real replay defence, asserted directly.

        A captured payload replayed with the SAME event id is refused however
        fresh its timestamp claims to be -- because `UNIQUE(event_id)` is the
        check, and unlike a clock it cannot be defeated by editing a field
        inside the signed body.

        The 300-second window used to stand in for this and was strictly worse:
        it rejected Razorpay's own retries (INC-024) while an attacker replaying
        promptly would have passed it.
        """
        payload = load_fixture("subscription.pending")
        first = post(client, payload, event_id="evt_replay_once", now=clock)
        assert first.status_code == 200

        second = post(client, payload, event_id="evt_replay_once", now=clock)
        assert second.status_code == 200
        assert second.json()["status"] == "duplicate"

    def test_same_fixture_accepted_when_fresh(self, client: TestClient, clock: FakeClock) -> None:
        r = post(client, load_fixture("subscription.pending"), event_id="evt_fresh", now=clock)
        assert r.status_code == 200
