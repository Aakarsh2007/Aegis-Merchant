"""The capability token that unlocks a write tool.

workflow.md §7 claims enforcement is *structural, not conventional*: there is no
code path from an LLM proposal to a money-moving API call that does not pass
through the policy firewall. This module is what makes that more than a naming
convention.

**How.** ``AppliedAction`` — the numbers that will actually execute — is signed
with an HMAC under a secret generated at import time and held module-private.
Only :func:`mint` can produce a valid signature; a token constructed directly
carries a wrong one and :meth:`PolicyToken.verify` raises. So a developer who
skips the firewall does not get a silent bypass, they get a loud failure at the
call site.

**What this does and does not prove.** Python has no true private state: code
that deliberately reaches for ``_SIGNING_KEY`` can forge a token. That is
subversion, not an accident, and the honest claim is the narrower one — *no
accidental path* reaches a write tool unauthorised, and every deliberate one is
visible in a diff. ``tests/test_no_unauthorised_writes.py`` walks the import
graph to check the second half.

**Why the key is per-process and ephemeral.** A token is a capability for one
immediate execution, not a durable grant. It must not survive a restart, be
replayed tomorrow, or be moved between processes. Human approvals *do* need to
persist, and they use a different mechanism: ``policy_applied_hash``, a content
hash of the exact action a person approved, checked again at execution so that
approving one action and executing another is impossible (§13.5).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.db.enums import Channel, EscalationRung, MessageClass, RecoveryStrategy

__all__ = [
    "AppliedAction",
    "PolicyToken",
    "PolicyTokenInvalid",
    "canonical_json",
    "mint",
]

#: Generated once per process. Never logged, never persisted, never exported.
_SIGNING_KEY: bytes = secrets.token_bytes(32)


class PolicyTokenInvalid(RuntimeError):
    """A write tool was handed a token it cannot trust."""


def canonical_json(payload: dict[str, Any]) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8.

    Used for both the token signature and the approval hash. Non-canonical
    serialisation is how hashes silently stop matching across processes — the
    same dict can serialise two ways, and then an approved action fails to
    verify for no visible reason.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class AppliedAction:
    """What will actually execute — *after* every clamp.

    The execution node reads this and never the model's proposal. Keeping them
    as separate types means "execute the LLM's suggestion" is not something you
    can write by accident: the proposal has no ``reference_id``, so it cannot
    reach the provider.
    """

    case_id: str
    strategy: RecoveryStrategy
    amount_paise: int
    discount_pct: float
    discount_amount_paise: int
    charge_amount_paise: int
    link_expiry_minutes: int
    channel: Channel
    message_class: MessageClass
    escalation_rung: EscalationRung
    #: The idempotency key, committed to the outbox before the provider call.
    reference_id: str
    attempt_no: int
    #: Set when a stopping rule deferred the send (quiet hours, contact cap).
    send_after: datetime | None = None

    def as_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["strategy"] = self.strategy.value
        payload["channel"] = self.channel.value
        payload["message_class"] = self.message_class.value
        payload["escalation_rung"] = self.escalation_rung.value
        payload["send_after"] = self.send_after.isoformat() if self.send_after else None
        return payload

    def canonical(self) -> str:
        return canonical_json(self.as_payload())

    def content_hash(self) -> str:
        """Stable hash of the exact action.

        Persisted on an approval request so a human approves *this* action.
        If anything changes between display and execution the hash mismatches
        and execution refuses — the difference between a real approval gate and
        a button labelled "approve".
        """
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PolicyToken:
    """Proof that the firewall authorised this exact action.

    Cannot be constructed usefully outside :func:`mint`: the signature covers
    the applied action, and a hand-built token fails :meth:`verify`.
    """

    applied: AppliedAction
    minted_at: datetime
    signature: str = field(repr=False)

    def verify(self) -> None:
        """Raise unless this token was minted by the policy firewall.

        Called by every write tool before it touches the provider.
        """
        expected = _sign(self.applied, self.minted_at)
        if not hmac.compare_digest(expected, self.signature):
            raise PolicyTokenInvalid(
                f"policy token for case {self.applied.case_id} failed verification: "
                "it was not minted by the policy firewall, or the action was "
                "modified after authorisation"
            )

    @property
    def is_valid(self) -> bool:
        try:
            self.verify()
        except PolicyTokenInvalid:
            return False
        return True


def _sign(applied: AppliedAction, minted_at: datetime) -> str:
    message = f"{applied.canonical()}|{minted_at.isoformat()}".encode()
    return hmac.new(_SIGNING_KEY, message, hashlib.sha256).hexdigest()


def mint(applied: AppliedAction, *, minted_at: datetime) -> PolicyToken:
    """Issue a capability token. **Only the policy firewall may call this.**

    Enforced by `tests/test_no_unauthorised_writes.py`, which walks the import
    graph and fails if any module outside ``guardrails`` imports it.
    """
    return PolicyToken(applied=applied, minted_at=minted_at, signature=_sign(applied, minted_at))
