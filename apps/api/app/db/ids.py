"""Identifier generation.

Human-readable prefixed IDs rather than raw UUIDs. A judge reads case IDs in
the decision trace, the audit chain and the demo narration ("RC-0142"), and
``RC-0142`` is legible where ``f47ac10b-58cc-...`` is not. The prefix also
makes a foreign key obvious at a glance when browsing the SQLite file.

Two distinct generators, for two distinct needs:

* :func:`new_id` — random, for runtime rows. Uses ``secrets`` rather than
  ``random`` so IDs are not predictable from a seeded RNG.
* :func:`seq_id` — sequential, for the seeded corpus, so the committed demo
  database is byte-for-byte reproducible.
"""

from __future__ import annotations

import hashlib
import secrets

__all__ = ["PREFIXES", "idempotency_hash", "new_id", "reference_id", "seq_id"]

#: Row-type prefixes. Kept in one place so nothing invents its own.
PREFIXES: dict[str, str] = {
    "merchant": "mch",
    "customer": "cus",
    "attempt": "atp",
    "webhook": "evt",
    "case": "RC",
    "outbox": "obx",
    "action": "act",
    "approval": "apr",
    "promise": "prm",
    "audit": "blk",
    "llm_call": "llm",
    "contact": "cnt",
    "dlq": "dlq",
    "template": "tpl",
}


def new_id(kind: str, *, nbytes: int = 5) -> str:
    """Random identifier for a runtime row: ``cus_a1b2c3d4e5``."""
    prefix = PREFIXES.get(kind, kind)
    return f"{prefix}_{secrets.token_hex(nbytes)}"


def seq_id(kind: str, n: int, *, width: int = 4) -> str:
    """Deterministic sequential identifier for the seeded corpus.

    Case IDs are rendered as ``RC-0142`` (dash) because that is the form used
    in the demo script and the dashboard; everything else uses an underscore.
    """
    prefix = PREFIXES.get(kind, kind)
    sep = "-" if prefix.isupper() else "_"
    return f"{prefix}{sep}{n:0{width}d}"


def reference_id(case_id: str, attempt_no: int) -> str:
    """The idempotency key sent to Razorpay as ``reference_id``.

    Razorpay enforces uniqueness of ``reference_id`` per merchant, which is
    what makes a retry safe: we commit this string to the outbox *before* the
    provider call, so if we crash and retry, the provider itself rejects the
    duplicate (workflow.md §10.3). An idempotency key generated at call time
    is not an idempotency key.

    It is also the exact string the attribution matcher looks for in the
    confirming webhook, so recovery can never be attributed by guesswork.

    **Lowercased deliberately.** Verified against live Razorpay Test Mode
    (INC-012): the provider *stores* the reference with its original case, but
    treats uniqueness and lookup **case-insensitively** -- while our own
    ``UNIQUE(reference_id)`` in SQLite is case-*sensitive*. Two references
    differing only in case would therefore pass our constraint and be rejected
    by the provider, a confusing failure with no local trace. Emitting
    lowercase makes both uniqueness domains identical, which removes the
    asymmetry at the source instead of handling it at every comparison site.
    """
    return f"rvp_{case_id}_{attempt_no}".lower()


def idempotency_hash(merchant_id: str, order_ref: str, playbook: str) -> str:
    """Case-level dedupe key, backing ``UNIQUE(idempotency_hash)``.

    Two workers racing on the same order produce the same hash, so exactly one
    wins the INSERT and the loser exits cleanly on IntegrityError (§12.4).
    """
    return hashlib.sha256(f"{merchant_id}|{order_ref}|{playbook}".encode()).hexdigest()
