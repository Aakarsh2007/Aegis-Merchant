"""Razorpay Test Mode client.

**Why not the official ``razorpay`` Python SDK.** It is synchronous — it wraps
``requests`` — and every call from this async application would block the event
loop. That matters here specifically: the outbox drainer runs in-process
alongside the API, so one slow provider call would stall webhook acknowledgement
for every other merchant event. It also gives no control over per-request
timeouts, which the retry policy in §10.4 depends on. We therefore call the
same documented REST API directly over ``httpx.AsyncClient`` (recorded as
DEC-008). The SDK's own signature helper is likewise re-implemented in
``app/security/webhook.py`` so the verification is inspectable rather than
delegated.

Auth is HTTP Basic with ``key_id:key_secret``, exactly as the SDK does it.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.tools.provider import (
    DuplicateReference,
    PaymentDetails,
    PaymentLinkRequest,
    PaymentLinkResult,
    ProviderPermanent,
    ProviderRetryable,
)

__all__ = ["RAZORPAY_API_BASE", "RazorpayProvider"]

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"

#: Retry on transport failure, rate limiting, and anything server-side.
#: Everything else is our fault and will fail identically on retry.
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

#: Razorpay signals a duplicate ``reference_id`` in the error description
#: rather than with a dedicated code. Matching on substrings is fragile, so
#: any 400 mentioning the reference is treated as a duplicate *and then
#: verified* by looking the link up — a false positive costs one GET, while a
#: false negative would create a second live payment link for one cart.
_DUPLICATE_HINTS = ("reference_id", "already exists", "duplicate")


class RazorpayProvider:
    """Real Razorpay Test Mode integration."""

    name = "razorpay"

    def __init__(
        self,
        key_id: str,
        key_secret: str,
        *,
        base_url: str = RAZORPAY_API_BASE,
        timeout_s: float = 3.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not key_id or not key_secret:
            raise ValueError("RazorpayProvider requires both key_id and key_secret")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._auth = (key_id, key_secret)
        self._client = client

    # -- transport ---------------------------------------------------------
    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None, **params: Any
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.request(
                method, url, json=json, params=params or None, auth=self._auth
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # No response at all: we cannot know whether the side effect
            # happened, which is precisely why the reference_id is committed
            # before the call.
            raise ProviderRetryable(f"{method} {path}: {exc.__class__.__name__}") from exc
        finally:
            if owns_client:
                await client.aclose()

        return self._handle(response, method, path)

    def _handle(self, response: httpx.Response, method: str, path: str) -> dict[str, Any]:
        if response.is_success:
            body: dict[str, Any] = response.json()
            return body

        try:
            payload = response.json()
        except ValueError:
            payload = {"raw_text": response.text[:500]}

        # Razorpay documents `{"error": {"code": ..., "description": ...}}`, but
        # not every response uses it: a 401 on a product that is not enabled
        # returns `{"error": "Unauthorized"}` -- a plain string (INC-019).
        # Assuming the object shape raised AttributeError here, which is
        # neither ProviderRetryable nor ProviderPermanent, so it escaped the
        # outbox's classification entirely and would have left a row stuck in
        # SENDING. Both shapes are handled, and anything else degrades to text
        # rather than crashing the caller.
        raw_error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(raw_error, dict):
            error: dict[str, Any] = raw_error
        elif isinstance(raw_error, str):
            error = {"description": raw_error}
        else:
            error = {}

        description = str(error.get("description", "")).lower()
        code = error.get("code")
        message = f"{method} {path} -> {response.status_code}: {error.get('description', response.text[:200])}"

        if response.status_code in _RETRYABLE_STATUS:
            raise ProviderRetryable(
                message, status_code=response.status_code, provider_code=code, raw=payload
            )

        if response.status_code == 400 and any(h in description for h in _DUPLICATE_HINTS):
            raise DuplicateReference(description, raw=payload)

        raise ProviderPermanent(
            message, status_code=response.status_code, provider_code=code, raw=payload
        )

    # -- operations --------------------------------------------------------
    async def create_payment_link(self, request: PaymentLinkRequest) -> PaymentLinkResult:
        payload: dict[str, Any] = {
            "amount": request.amount_paise,
            "currency": request.currency,
            "description": request.description,
            "reference_id": request.reference_id,
            "customer": {"name": request.customer_name},
            # We dispatch messaging ourselves through the consent-aware channel
            # adapter, so Razorpay must not also notify. Two messages for one
            # recovery would breach the contact cap the agent just checked.
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
            "notes": dict(request.notes),
        }
        if request.customer_contact:
            payload["customer"]["contact"] = request.customer_contact
        if request.customer_email:
            payload["customer"]["email"] = request.customer_email
        if request.expire_by is not None:
            payload["expire_by"] = int(request.expire_by.timestamp())

        body = await self._request("POST", "/payment_links", json=payload)
        return self._to_link(body, was_existing=False)

    async def get_payment_link_by_reference(self, reference_id: str) -> PaymentLinkResult | None:
        body = await self._request("GET", "/payment_links", reference_id=reference_id)
        items = body.get("payment_links") or []
        if not items:
            return None
        return self._to_link(items[0], was_existing=True)

    async def get_payment(self, payment_id: str) -> PaymentDetails:
        body = await self._request("GET", f"/payments/{payment_id}")
        return self._to_payment(body)

    async def get_order_status(self, order_id: str) -> str:
        body = await self._request("GET", f"/orders/{order_id}")
        return str(body.get("status", "unknown"))

    async def health(self) -> bool:
        try:
            # Cheapest authenticated read available.
            await self._request("GET", "/payments", count=1)
        except ProviderRetryable:
            return False
        except ProviderPermanent:
            # Reachable but rejecting us -- bad credentials, not downtime.
            return False
        return True

    # -- mapping -----------------------------------------------------------
    @staticmethod
    def _to_link(body: dict[str, Any], *, was_existing: bool) -> PaymentLinkResult:
        return PaymentLinkResult(
            link_id=str(body.get("id", "")),
            short_url=str(body.get("short_url", "")),
            reference_id=str(body.get("reference_id", "")),
            amount_paise=int(body.get("amount", 0)),
            status=str(body.get("status", "created")),
            was_existing=was_existing,
            raw=body,
        )

    @staticmethod
    def _to_payment(body: dict[str, Any]) -> PaymentDetails:
        # `bank`, `wallet` and `vpa` all describe the instrument depending on
        # method; the first present one is the closest thing to an issuer.
        issuer = body.get("bank") or body.get("wallet") or body.get("card_id")
        return PaymentDetails(
            payment_id=str(body.get("id", "")),
            order_id=body.get("order_id"),
            amount_paise=int(body.get("amount", 0)),
            status=str(body.get("status", "")),
            method=body.get("method"),
            issuer=str(issuer) if issuer else None,
            error_code=body.get("error_code"),
            error_source=body.get("error_source"),
            error_step=body.get("error_step"),
            error_reason=body.get("error_reason"),
            raw=body,
        )
