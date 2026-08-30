"""The real Razorpay delivery, and the two bugs finding it exposed.

`tests/fixtures/razorpay/payment_link.paid.captured.json` is not a fixture we
wrote. It is the payload Razorpay's own infrastructure delivered over a public
tunnel, HMAC-verified against the configured secret and acknowledged 200. Every
other webhook fixture in that directory is `documented_shape` or a captured API
*response*; this is the first captured *delivery*.

Pinning tests to it matters because a constructed payload shares an author with
the parser, and INC-015 was exactly that failure — four invented spellings of an
error reason and not the one Razorpay sends.

The rest of the file guards INC-020 and INC-021: application logging that went
nowhere, and two 401s that meant opposite things.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import pathlib
import re

import pytest

FIXTURE = (
    pathlib.Path(__file__).parent / "fixtures" / "razorpay" / "payment_link.paid.captured.json"
)


def _load() -> dict[str, object] | None:
    if not FIXTURE.exists():
        return None
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TestTheRealDelivery:
    def test_the_fixture_is_a_real_capture(self) -> None:
        """A fixture that quietly reverted to a hand-written one would make
        every test below vacuous while staying green (INC-006)."""
        doc = _load()
        if doc is None:
            pytest.skip("no captured live webhook; see docs/webhooks.md")
        meta = doc["_fixture_meta"]
        assert isinstance(meta, dict)
        assert meta["provenance"] == "captured_live_webhook"
        assert meta["signature_verified"] is True

    def test_it_is_a_settling_event(self) -> None:
        """`payment_link.paid` is in SETTLING_EVENTS; `payment.captured` is
        deliberately not, because it also fires for organic completions."""
        from app.services.attribution import SETTLING_EVENTS

        doc = _load()
        if doc is None:
            pytest.skip("no captured live webhook")
        assert doc["event"] in SETTLING_EVENTS

    def test_it_carries_a_reference_we_issued(self) -> None:
        """Attribution condition 3 — the line between attribution and
        coincidence — satisfied by a real Razorpay event rather than a
        constructed one."""
        doc = _load()
        if doc is None:
            pytest.skip("no captured live webhook")
        entity = doc["payload"]["payment_link"]["entity"]  # type: ignore[index]
        assert entity["reference_id"].startswith("rvp_")

    def test_the_amount_is_paise_not_rupees(self) -> None:
        """429900, not 4299. A float rupee is how payment systems lose half a
        paisa a million times — and reading the wrong unit off a real payload
        is how the headline number ends up 100x wrong."""
        doc = _load()
        if doc is None:
            pytest.skip("no captured live webhook")
        entity = doc["payload"]["payment_link"]["entity"]  # type: ignore[index]
        assert entity["amount"] == entity["amount_paid"]
        assert isinstance(entity["amount"], int)
        assert entity["amount"] >= 100

    def test_no_contact_details_were_committed(self) -> None:
        """The fixture is public. A redaction that silently stopped working
        would put a real customer's phone number in the repository."""
        doc = _load()
        if doc is None:
            pytest.skip("no captured live webhook")
        blob = json.dumps(doc)
        assert not re.findall(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}", blob)
        assert not re.findall(r"\b(?:\+91)?[6-9]\d{9}\b", blob)

    def test_our_verifier_accepts_it_when_signed(self) -> None:
        """End to end over the real bytes: sign the captured payload with a
        known secret and confirm the same verifier Razorpay's delivery passed
        through accepts it."""
        from app.security.webhook import verify_signature

        doc = _load()
        if doc is None:
            pytest.skip("no captured live webhook")
        payload = {k: v for k, v in doc.items() if k != "_fixture_meta"}
        raw = json.dumps(payload, separators=(",", ":")).encode()
        secret = "whsec_test_only"
        signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()

        assert verify_signature(raw, signature, secret).valid
        # And the classic failure: same object, different bytes.
        assert not verify_signature(json.dumps(payload, indent=2).encode(), signature, secret).valid


class TestLoggingActuallyReachesSomewhere:
    """INC-020. Every `log.warning` in the application was being discarded at
    runtime because uvicorn leaves the root logger without a handler."""

    def test_create_app_attaches_a_root_handler(self) -> None:
        from app.config import Settings
        from app.main import create_app

        root = logging.getLogger()
        original = list(root.handlers)
        try:
            root.handlers.clear()
            create_app(
                Settings(
                    razorpay_key_id="",
                    razorpay_key_secret="",
                    gemini_api_key="",
                    api_token="",
                    environment="development",
                )
            )
            assert root.handlers, (
                "no root handler: every application log record is discarded (INC-020)"
            )
        finally:
            root.handlers[:] = original

    def test_an_existing_configuration_is_not_clobbered(self) -> None:
        """A real deployment configures its own logging. Adding a second
        handler would duplicate every line."""
        from app.config import Settings
        from app.main import create_app

        root = logging.getLogger()
        original = list(root.handlers)
        try:
            sentinel = logging.NullHandler()
            root.handlers[:] = [sentinel]
            create_app(
                Settings(
                    razorpay_key_id="",
                    razorpay_key_secret="",
                    gemini_api_key="",
                    api_token="",
                    environment="development",
                )
            )
            assert root.handlers == [sentinel]
        finally:
            root.handlers[:] = original


class TestTheTwo401sAreDistinguishable:
    """INC-021. A bad signature and a stale event both return 401, and they
    mean opposite things: 'your secret is wrong' versus 'your secret is fine
    and this event is old'."""

    def test_the_signature_diagnostic_names_both_signatures(self) -> None:
        import inspect

        from app.routers.webhooks import _log_rejected_signature

        source = inspect.getsource(_log_rejected_signature)
        assert "received=%s" in source
        assert "expected=%s" in source

    def test_the_stale_diagnostic_says_the_signature_was_valid(self) -> None:
        """The sentence that would have saved a debugging session."""
        import inspect

        from app.routers import webhooks

        source = inspect.getsource(webhooks.razorpay_webhook)
        assert "rejected as stale" in source
        assert "signature was VALID" in source

    def test_diagnostics_are_development_only(self) -> None:
        """They print request bodies. That must never happen in production."""
        import inspect

        from app.routers.webhooks import _log_rejected_signature

        source = inspect.getsource(_log_rejected_signature)
        assert "simulation_allowed" in source
