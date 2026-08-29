"""HMAC verification and replay-window tests.

This is the outermost security boundary: everything downstream trusts that a
verified event came from Razorpay. So these tests attack it rather than
confirming the happy path.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta

import pytest

from app.core.clock import FakeClock
from app.security.webhook import compute_signature, verify_signature, verify_timestamp

SECRET = "whsec_glowkart_test"
BODY = b'{"event":"payment.failed","created_at":1788240841}'


def sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestSignature:
    def test_valid_signature_accepted(self) -> None:
        assert verify_signature(BODY, sign(BODY), SECRET).valid

    def test_matches_razorpay_algorithm(self) -> None:
        """Independently recomputed, not just self-consistent with our helper."""
        expected = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()
        assert compute_signature(BODY, SECRET) == expected
        assert len(expected) == 64

    def test_forged_signature_rejected(self) -> None:
        assert not verify_signature(BODY, "0" * 64, SECRET).valid

    def test_signature_from_wrong_secret_rejected(self) -> None:
        assert not verify_signature(BODY, sign(BODY, "wrong_secret"), SECRET).valid

    def test_missing_signature_rejected(self) -> None:
        result = verify_signature(BODY, None, SECRET)
        assert not result.valid
        assert result.reason == "signature_header_missing"

    def test_empty_secret_fails_closed(self) -> None:
        """An unset secret must reject, never skip.

        A blank secret that silently accepts everything is worse than no
        verification, because the events then look verified.
        """
        result = verify_signature(BODY, sign(BODY, ""), "")
        assert not result.valid
        assert result.reason == "webhook_secret_not_configured"

    def test_single_byte_body_change_invalidates(self) -> None:
        signature = sign(BODY)
        tampered = BODY.replace(b"payment.failed", b"payment.paidxx")
        assert len(tampered) == len(BODY)
        assert not verify_signature(tampered, signature, SECRET).valid

    def test_reserialised_json_breaks_the_signature(self) -> None:
        """The reason the endpoint verifies raw bytes before parsing.

        Re-serialising identical data produces a different byte string -- key
        order and separators differ -- so a handler that parsed first would
        reject every genuine webhook.
        """
        original = b'{"event":"payment.failed","created_at":1788240841}'
        signature = sign(original)
        reserialised = json.dumps(json.loads(original)).encode()
        assert reserialised != original
        assert verify_signature(original, signature, SECRET).valid
        assert not verify_signature(reserialised, signature, SECRET).valid

    def test_unicode_body_survives_verification(self) -> None:
        """Customer names carry non-ASCII; byte-level handling must not mangle."""
        body = json.dumps({"name": "अनन्या", "event": "payment.failed"}).encode("utf-8")
        assert verify_signature(body, sign(body), SECRET).valid

    def test_result_is_falsy_when_invalid(self) -> None:
        assert not verify_signature(BODY, "bad", SECRET)
        assert verify_signature(BODY, sign(BODY), SECRET)


class TestReplayWindow:
    """A valid signature proves origin, not recency."""

    def test_fresh_event_accepted(self) -> None:
        clock = FakeClock.at_ist(2026, 9, 1, 12, 0)
        assert verify_timestamp(clock.now_utc(), clock.now_utc(), 300).valid

    def test_event_within_tolerance_accepted(self) -> None:
        clock = FakeClock.at_ist(2026, 9, 1, 12, 0)
        sent = clock.now_utc() - timedelta(seconds=299)
        assert verify_timestamp(sent, clock.now_utc(), 300).valid

    def test_stale_event_rejected(self) -> None:
        """A captured, still-validly-signed payload replayed an hour later."""
        clock = FakeClock.at_ist(2026, 9, 1, 12, 0)
        sent = clock.now_utc() - timedelta(hours=1)
        result = verify_timestamp(sent, clock.now_utc(), 300)
        assert not result.valid
        assert result.reason.startswith("event_too_old")

    def test_future_event_rejected(self) -> None:
        """A far-future timestamp would stay replayable indefinitely."""
        clock = FakeClock.at_ist(2026, 9, 1, 12, 0)
        sent = clock.now_utc() + timedelta(hours=1)
        result = verify_timestamp(sent, clock.now_utc(), 300)
        assert not result.valid
        assert result.reason.startswith("event_in_future")

    def test_missing_timestamp_fails_closed(self) -> None:
        clock = FakeClock.at_ist(2026, 9, 1, 12, 0)
        result = verify_timestamp(None, clock.now_utc(), 300)
        assert not result.valid
        assert result.reason == "event_timestamp_missing"

    @pytest.mark.parametrize("offset_s", [0, 1, 150, 299, 300])
    def test_boundary_inside_window(self, offset_s: int) -> None:
        clock = FakeClock.at_ist(2026, 9, 1, 12, 0)
        sent = clock.now_utc() - timedelta(seconds=offset_s)
        assert verify_timestamp(sent, clock.now_utc(), 300).valid

    @pytest.mark.parametrize("offset_s", [301, 600, 86400])
    def test_boundary_outside_window(self, offset_s: int) -> None:
        clock = FakeClock.at_ist(2026, 9, 1, 12, 0)
        sent = clock.now_utc() - timedelta(seconds=offset_s)
        assert not verify_timestamp(sent, clock.now_utc(), 300).valid
