"""Webhook signature verification.

This is the outermost security boundary in the system: everything downstream
trusts that a verified event genuinely came from Razorpay. Four details, each
of which has silently broken this exact check in real systems:

**Verify the raw bytes.** The signature covers the exact body Razorpay sent.
Any framework that parses JSON and re-serialises before verifying will produce
a different byte string for identical data — unicode escaping and key ordering
both differ — and every signature will fail. FastAPI's ``await request.body()``
is therefore called *before* any parsing, and the parsed form is derived from
those same bytes.

**Compare in constant time.** ``==`` on a hex digest leaks timing information
that can be used to forge a signature byte by byte. ``hmac.compare_digest``
does not.

**A valid signature does not prevent replay.** An attacker who captures one
valid request can send it again forever. Signature validity answers "did
Razorpay send this", not "did Razorpay send this *recently*", so the event
timestamp is bounded separately.

**A missing secret must fail closed.** If ``RAZORPAY_WEBHOOK_SECRET`` is unset,
verification must reject rather than skip — an empty secret that quietly
accepts everything is worse than no verification at all, because it looks
verified.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta

__all__ = [
    "SignatureResult",
    "compute_signature",
    "verify_signature",
    "verify_timestamp",
]


@dataclass(frozen=True)
class SignatureResult:
    valid: bool
    reason: str = "ok"

    def __bool__(self) -> bool:
        return self.valid


def compute_signature(raw_body: bytes, secret: str) -> str:
    """HMAC-SHA256 hex digest of the raw body, as Razorpay computes it."""
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def verify_signature(raw_body: bytes, signature: str | None, secret: str) -> SignatureResult:
    """Constant-time verification of the ``X-Razorpay-Signature`` header."""
    if not secret:
        # Fail closed. An unset secret must never mean "accept everything".
        return SignatureResult(False, "webhook_secret_not_configured")
    if not signature:
        return SignatureResult(False, "signature_header_missing")

    expected = compute_signature(raw_body, secret)
    if not hmac.compare_digest(expected, signature):
        return SignatureResult(False, "signature_mismatch")
    return SignatureResult(True)


def verify_timestamp(
    event_ts: datetime | None,
    now: datetime,
    tolerance_s: int,
) -> SignatureResult:
    """Bound how old a signed payload may be.

    Rejects both directions. A far-future timestamp is as suspicious as an old
    one — it would otherwise let an attacker mint a payload that stays
    replayable indefinitely.
    """
    if event_ts is None:
        return SignatureResult(False, "event_timestamp_missing")

    tolerance = timedelta(seconds=tolerance_s)
    age = now - event_ts
    if age > tolerance:
        return SignatureResult(False, f"event_too_old:{int(age.total_seconds())}s")
    if -age > tolerance:
        return SignatureResult(False, f"event_in_future:{int(-age.total_seconds())}s")
    return SignatureResult(True)
