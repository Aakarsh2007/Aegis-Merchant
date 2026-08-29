"""Provider tests.

The critical one is :class:`TestReferenceIdIdempotency`. The entire two-phase
outbox design rests on the provider rejecting a duplicate ``reference_id`` --
that rejection is what makes a post-crash retry safe. If the mock did not model
it, the Phase 8 crash-recovery tests would pass against a false model of the
world and the bug would only surface against real Razorpay.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import respx

from app.core.clock import FakeClock
from app.tools.mock_provider import MockRazorpayProvider
from app.tools.provider import (
    DuplicateReference,
    PaymentDetails,
    PaymentLinkRequest,
    ProviderPermanent,
    ProviderRetryable,
)
from app.tools.razorpay_client import RAZORPAY_API_BASE, RazorpayProvider

FIXTURES = Path(__file__).parent / "fixtures" / "razorpay"


@pytest.fixture
def mock() -> MockRazorpayProvider:
    return MockRazorpayProvider(latency_s=0.0)


def link_request(reference_id: str = "rvp_RC-0142_1", amount: int = 429_900) -> PaymentLinkRequest:
    return PaymentLinkRequest(
        amount_paise=amount,
        reference_id=reference_id,
        description="GlowKart - complete your order",
        customer_name="Ananya",
    )


# ---------------------------------------------------------------------------
class TestRequestValidation:
    def test_rejects_non_positive_amount(self) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            link_request(amount=0)

    def test_rejects_missing_reference_id(self) -> None:
        """Without it there is no idempotency key, so a retry could double-charge."""
        with pytest.raises(ValueError, match="idempotency key"):
            link_request(reference_id="")


# ---------------------------------------------------------------------------
class TestReferenceIdIdempotency:
    async def test_duplicate_reference_raises(self, mock: MockRazorpayProvider) -> None:
        await mock.create_payment_link(link_request())
        with pytest.raises(DuplicateReference):
            await mock.create_payment_link(link_request())

    async def test_duplicate_creates_no_second_link(self, mock: MockRazorpayProvider) -> None:
        """The property that matters: one cart, one live link, ever."""
        await mock.create_payment_link(link_request())
        for _ in range(4):
            with pytest.raises(DuplicateReference):
                await mock.create_payment_link(link_request())
        assert mock.link_count() == 1

    async def test_existing_link_is_retrievable(self, mock: MockRazorpayProvider) -> None:
        """The recovery path after a duplicate rejection."""
        created = await mock.create_payment_link(link_request())
        found = await mock.get_payment_link_by_reference("rvp_RC-0142_1")
        assert found is not None
        assert found.link_id == created.link_id
        assert found.was_existing is True

    async def test_unknown_reference_returns_none(self, mock: MockRazorpayProvider) -> None:
        assert await mock.get_payment_link_by_reference("rvp_never_created_1") is None

    async def test_different_attempts_are_different_links(self, mock: MockRazorpayProvider) -> None:
        """reference_id embeds the attempt number, so attempt 2 is legitimately
        a new link rather than a duplicate."""
        await mock.create_payment_link(link_request("rvp_RC-0142_1"))
        await mock.create_payment_link(link_request("rvp_RC-0142_2"))
        assert mock.link_count() == 2


# ---------------------------------------------------------------------------
class TestFaultInjection:
    async def test_timeout_is_retryable(self, mock: MockRazorpayProvider) -> None:
        mock.inject_fault("TIMEOUT")
        with pytest.raises(ProviderRetryable):
            await mock.create_payment_link(link_request())

    async def test_bad_request_is_permanent(self, mock: MockRazorpayProvider) -> None:
        mock.inject_fault("BAD_REQUEST")
        with pytest.raises(ProviderPermanent):
            await mock.create_payment_link(link_request())

    async def test_fault_expires_after_count(self, mock: MockRazorpayProvider) -> None:
        """Models a transient outage: fails twice, then succeeds -- which is
        what the backoff policy must survive."""
        mock.inject_fault("SERVER_ERROR", count=2)
        for _ in range(2):
            with pytest.raises(ProviderRetryable):
                await mock.create_payment_link(link_request())
        result = await mock.create_payment_link(link_request())
        assert result.link_id

    async def test_failed_creation_leaves_no_link(self, mock: MockRazorpayProvider) -> None:
        """A failure must not half-create. Otherwise the retry would hit a
        duplicate for a link the customer never received."""
        mock.inject_fault("TIMEOUT")
        with pytest.raises(ProviderRetryable):
            await mock.create_payment_link(link_request())
        assert mock.link_count() == 0


# ---------------------------------------------------------------------------
class TestMockReads:
    async def test_order_status_defaults_to_created(self, mock: MockRazorpayProvider) -> None:
        assert await mock.get_order_status("order_unknown") == "created"

    async def test_registered_order_status_returned(self, mock: MockRazorpayProvider) -> None:
        """Backs stopping rule S-01: abort if the order was already paid."""
        mock.register_order("order_x", "paid")
        assert await mock.get_order_status("order_x") == "paid"

    async def test_unknown_payment_is_permanent_error(self, mock: MockRazorpayProvider) -> None:
        with pytest.raises(ProviderPermanent):
            await mock.get_payment("pay_nope")

    async def test_registered_payment_returned(self, mock: MockRazorpayProvider) -> None:
        mock.register_payment(
            PaymentDetails(
                payment_id="pay_1",
                order_id="order_1",
                amount_paise=429_900,
                status="failed",
                error_source="bank",
            )
        )
        assert (await mock.get_payment("pay_1")).error_source == "bank"


# ---------------------------------------------------------------------------
class TestRazorpayProviderHTTP:
    """The real client, with the network mocked at the transport layer."""

    def provider(self) -> RazorpayProvider:
        return RazorpayProvider("rzp_test_key", "secret", timeout_s=1.0)

    def test_requires_credentials(self) -> None:
        with pytest.raises(ValueError, match="key_id and key_secret"):
            RazorpayProvider("", "")

    @respx.mock
    async def test_create_link_maps_response(self) -> None:
        respx.post(f"{RAZORPAY_API_BASE}/payment_links").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "plink_ABC",
                    "short_url": "https://rzp.io/i/ABC",
                    "reference_id": "rvp_RC-0142_1",
                    "amount": 429900,
                    "status": "created",
                },
            )
        )
        result = await self.provider().create_payment_link(link_request())
        assert (result.link_id, result.amount_paise, result.was_existing) == (
            "plink_ABC",
            429900,
            False,
        )

    @respx.mock
    async def test_notifications_are_disabled(self) -> None:
        """We dispatch messaging ourselves through the consent-aware adapter.
        Letting Razorpay also notify would send two messages for one recovery
        and breach the contact cap the agent just checked."""
        route = respx.post(f"{RAZORPAY_API_BASE}/payment_links").mock(
            return_value=httpx.Response(200, json={"id": "p", "amount": 1, "status": "created"})
        )
        await self.provider().create_payment_link(link_request())
        sent = json.loads(route.calls[0].request.content)
        assert sent["notify"] == {"sms": False, "email": False}
        assert sent["reminder_enable"] is False

    @respx.mock
    async def test_expiry_sent_as_unix_seconds(self) -> None:
        route = respx.post(f"{RAZORPAY_API_BASE}/payment_links").mock(
            return_value=httpx.Response(200, json={"id": "p", "amount": 1, "status": "created"})
        )
        clock = FakeClock.at_ist(2026, 9, 1, 12, 0)
        expiry = clock.now_utc() + timedelta(minutes=30)
        await self.provider().create_payment_link(
            PaymentLinkRequest(
                amount_paise=1000,
                reference_id="rvp_RC-1_1",
                description="d",
                customer_name="n",
                expire_by=expiry,
            )
        )
        sent = json.loads(route.calls[0].request.content)
        assert sent["expire_by"] == int(expiry.timestamp())

    @respx.mock
    @pytest.mark.parametrize("code", [408, 429, 500, 502, 503, 504])
    async def test_transient_statuses_are_retryable(self, code: int) -> None:
        respx.post(f"{RAZORPAY_API_BASE}/payment_links").mock(
            return_value=httpx.Response(code, json={"error": {"description": "later"}})
        )
        with pytest.raises(ProviderRetryable):
            await self.provider().create_payment_link(link_request())

    @respx.mock
    @pytest.mark.parametrize("code", [401, 403, 404])
    async def test_client_errors_are_permanent(self, code: int) -> None:
        """Retrying these produces the identical failure -- straight to the DLQ."""
        respx.post(f"{RAZORPAY_API_BASE}/payment_links").mock(
            return_value=httpx.Response(code, json={"error": {"description": "nope"}})
        )
        with pytest.raises(ProviderPermanent):
            await self.provider().create_payment_link(link_request())

    @respx.mock
    async def test_duplicate_reference_detected(self) -> None:
        respx.post(f"{RAZORPAY_API_BASE}/payment_links").mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": {
                        "code": "BAD_REQUEST_ERROR",
                        "description": "Payment link with reference_id rvp_RC-0142_1 already exists",
                    }
                },
            )
        )
        with pytest.raises(DuplicateReference):
            await self.provider().create_payment_link(link_request())

    @respx.mock
    async def test_timeout_is_retryable(self) -> None:
        """No response means we cannot know whether the side effect happened --
        which is exactly why reference_id is committed before the call."""
        respx.post(f"{RAZORPAY_API_BASE}/payment_links").mock(
            side_effect=httpx.ReadTimeout("timed out")
        )
        with pytest.raises(ProviderRetryable):
            await self.provider().create_payment_link(link_request())

    @respx.mock
    async def test_payment_failure_telemetry_mapped(self) -> None:
        """The fields the deterministic classifier reads must survive mapping."""
        entity = json.loads((FIXTURES / "payment.failed.json").read_text(encoding="utf-8"))
        payment = entity["payload"]["payment"]["entity"]
        respx.get(f"{RAZORPAY_API_BASE}/payments/pay_glowkart_ananya01").mock(
            return_value=httpx.Response(200, json=payment)
        )
        details = await self.provider().get_payment("pay_glowkart_ananya01")
        assert details.error_source == "bank"
        assert details.error_step == "payment_authorization"
        assert details.error_reason == "payment_failed_due_to_bank_timeout"
        assert details.method == "upi"
        assert details.issuer == "HDFC"
        assert details.amount_paise == 429900
