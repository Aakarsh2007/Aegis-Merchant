"""Tamper-evident audit chain (workflow.md §13.4).

``H_n = SHA256(H_{n-1} || canonical_json(payload) || block_index || created_at)``

Every state transition, policy clamp, approval, dispatch and verification
appends a block. ``GET /api/v1/audit/verify`` recomputes the whole chain, and
the demo shows it *catching* a deliberate tamper — a verifier that has never
been seen to fail is indistinguishable from one that returns ``true``.

What this construction actually guarantees
------------------------------------------

Stated precisely, because a hash chain is easy to oversell:

* **Editing a block in place is detected.** Changing ``payload_canonical``
  breaks ``payload_hash``; changing any hashed field breaks ``current_hash``;
  changing ``current_hash`` breaks the next block's ``prev_hash``. One edit
  therefore requires rewriting every subsequent block.
* **Deleting a block from the middle is detected**, as a gap in
  ``block_index`` and a broken link.
* **Reordering is detected**, because the index is inside the hash.

And what it does **not** guarantee, which matters more:

* **Truncation of the tail is not detected by the chain alone.** Removing the
  last *k* blocks leaves a shorter but perfectly valid chain. Nothing inside
  the data can prevent this; detection requires an anchor kept outside the
  database. :func:`verify_blocks` therefore reports ``head_hash`` and
  ``blocks``, so an external observer who recorded them earlier can detect a
  rollback we cannot detect ourselves.
* **An attacker with write access and our code can rewrite the whole chain.**
  The chain proves the log is internally consistent, not that it is complete.
  It raises the cost of a silent edit from one UPDATE to a full rewrite, and
  makes a partial edit loudly detectable. That is the honest claim.

Determinism is the whole game
-----------------------------

A chain that cannot be recomputed byte-for-byte in another process is not a
chain, it is decoration. Two specific traps are handled here:

* :func:`canonical_json` fixes key order, separators and unicode handling.
  Python's default ``json.dumps`` inserts spaces and preserves insertion
  order, so two processes could serialise the same dict differently.
* The timestamp is hashed as :func:`to_db_iso` renders it, **not** as
  ``datetime.isoformat()``. ``to_db_iso`` truncates microseconds to
  milliseconds; hashing the untruncated value would produce a hash the
  database can never reproduce, and every block would verify as corrupt the
  moment it was read back. This is the INC-013 failure shape — a value that
  survives in memory and changes on round-trip.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.db.ids import new_id
from app.db.models import AuditBlock
from app.db.types import to_db_iso

__all__ = [
    "GENESIS_HASH",
    "AuditChain",
    "ChainVerification",
    "canonical_json",
    "compute_block_hash",
    "payload_digest",
    "verify_blocks",
]

#: ``prev_hash`` of block 0. Sixty-four zeroes, so the genesis block is the
#: same shape as every other block and needs no special case in the verifier.
GENESIS_HASH: Final = "0" * 64

#: No whitespace. Two processes must produce identical bytes.
_SEPARATORS: Final = (",", ":")

#: Field separator inside the hashed material. ASCII unit separator, chosen
#: because it cannot appear in a hex hash, a decimal index or an ISO timestamp.
_FS: Final = "\x1f"

_APPEND_RETRIES: Final = 3


def canonical_json(payload: Mapping[str, Any]) -> str:
    """Serialise deterministically: sorted keys, no whitespace, UTF-8.

    ``ensure_ascii=False`` keeps Indian-language template text readable in the
    ledger rather than escaping it; the string is hashed as UTF-8 either way,
    so this is a legibility choice, not a semantic one.

    ``default=str`` is deliberate and narrow: a payload carrying a datetime or
    Decimal must not raise *at the point of writing an audit record*. Losing
    the audit block is worse than storing a stringified value, and the
    stringification is stable.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=_SEPARATORS,
        ensure_ascii=False,
        default=str,
    )


def payload_digest(payload_canonical: str) -> str:
    """SHA-256 of the canonical payload, stored alongside it.

    Redundant with ``current_hash`` by construction, and kept anyway: it lets
    the verifier say *which* part diverged — a mutated payload versus a
    mutated link — instead of only that something did.
    """
    return hashlib.sha256(payload_canonical.encode("utf-8")).hexdigest()


def compute_block_hash(
    *,
    prev_hash: str,
    payload_canonical: str,
    block_index: int,
    created_at: datetime,
) -> str:
    """The chaining function. Pure, so it can be tested without a database.

    Fields are joined with a unit separator rather than concatenated bare.
    Bare concatenation is ambiguous: ``("ab", "c")`` and ``("a", "bc")`` hash
    identically, which is a genuine collision between different block
    contents.
    """
    material = _FS.join((prev_hash, payload_canonical, str(block_index), to_db_iso(created_at)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChainVerification:
    """The shape ``GET /api/v1/audit/verify`` returns."""

    valid: bool
    blocks: int
    first_divergence_index: int | None = None
    #: Human-readable statement of what broke. Shown to the judge in the demo,
    #: so it must name the specific failure rather than say "invalid".
    reason: str | None = None
    #: The chain head. An external observer who recorded this earlier can
    #: detect a tail truncation, which the chain cannot detect itself.
    head_hash: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "blocks": self.blocks,
            "first_divergence_index": self.first_divergence_index,
            "reason": self.reason,
            "head_hash": self.head_hash,
        }


def verify_blocks(blocks: Sequence[AuditBlock]) -> ChainVerification:
    """Recompute an ordered chain. Nothing stored is trusted.

    Pure, so the verifier can be tested against hand-constructed tampered
    chains without a database — and so the demo's tamper button exercises
    exactly the code the endpoint runs.
    """
    if not blocks:
        # An empty chain is vacuously valid. Saying so explicitly matters: a
        # verifier that returned a bare `valid: true` for a chain someone had
        # just deleted entirely would be actively misleading.
        return ChainVerification(valid=True, blocks=0, reason="chain is empty")

    prev_hash = GENESIS_HASH
    for position, block in enumerate(blocks):
        # 1. Position. The index is inside the hash, so a reorder or a gap is
        #    a real divergence, not a cosmetic one.
        if block.block_index != position:
            return ChainVerification(
                valid=False,
                blocks=len(blocks),
                first_divergence_index=position,
                reason=(
                    f"block at position {position} claims index {block.block_index}: "
                    "a block was deleted, reordered, or inserted"
                ),
            )

        # 2. Payload integrity, checked before the link so the reason can
        #    distinguish an edited record from a broken chain.
        if payload_digest(block.payload_canonical) != block.payload_hash:
            return ChainVerification(
                valid=False,
                blocks=len(blocks),
                first_divergence_index=block.block_index,
                reason=f"block {block.block_index}: payload does not match its stored hash",
            )

        # 3. The link.
        if block.prev_hash != prev_hash:
            return ChainVerification(
                valid=False,
                blocks=len(blocks),
                first_divergence_index=block.block_index,
                reason=(
                    f"block {block.block_index}: prev_hash does not match the previous "
                    "block's hash — the chain is broken here"
                ),
            )

        # 4. The block's own hash, recomputed from its stored fields. This is
        #    what catches an edit to a hashed field (timestamp, index, payload)
        #    where the attacker forgot to recompute the rest.
        expected = compute_block_hash(
            prev_hash=block.prev_hash,
            payload_canonical=block.payload_canonical,
            block_index=block.block_index,
            created_at=block.created_at,
        )
        if expected != block.current_hash:
            return ChainVerification(
                valid=False,
                blocks=len(blocks),
                first_divergence_index=block.block_index,
                reason=(
                    f"block {block.block_index}: stored hash does not match a hash "
                    "recomputed from the block's own contents"
                ),
            )

        prev_hash = block.current_hash

    return ChainVerification(
        valid=True,
        blocks=len(blocks),
        head_hash=prev_hash,
        reason=(
            "chain is internally consistent. Note that tail truncation cannot be "
            "detected from the chain alone: compare head_hash against a previously "
            "recorded value to rule it out."
        ),
    )


class AuditChain:
    """Appends and verifies. One instance per process.

    Appends are serialised through an ``asyncio.Lock`` because two concurrent
    appends would read the same head and mint the same ``block_index``. The
    lock is the fast path, not the guarantee — ``UNIQUE(block_index)`` is the
    guarantee, and it holds across processes where the lock does not.
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._lock = asyncio.Lock()

    async def append(
        self,
        session: AsyncSession,
        *,
        event_name: str,
        actor: str,
        payload: Mapping[str, Any],
        case_id: str | None = None,
    ) -> AuditBlock:
        """Append one block and flush it.

        The caller commits. An audit block must land in the *same* transaction
        as the change it records — committing it separately would allow a
        state change with no audit block, or an audit block for a change that
        rolled back, and both are worse than either alone.
        """
        canonical = canonical_json(payload)
        digest = payload_digest(canonical)

        async with self._lock:
            for attempt in range(_APPEND_RETRIES):
                head = await self._head(session)
                index = 0 if head is None else head.block_index + 1
                prev = GENESIS_HASH if head is None else head.current_hash
                created = self._clock.now_utc()

                block = AuditBlock(
                    id=new_id("audit"),
                    block_index=index,
                    case_id=case_id,
                    prev_hash=prev,
                    current_hash=compute_block_hash(
                        prev_hash=prev,
                        payload_canonical=canonical,
                        block_index=index,
                        created_at=created,
                    ),
                    event_name=event_name,
                    actor=actor,
                    payload_canonical=canonical,
                    payload_hash=digest,
                    created_at=created,
                )
                session.add(block)
                try:
                    await session.flush()
                except IntegrityError:
                    # Another writer took this index. Re-read the head and
                    # retry rather than failing the caller outright.
                    await session.rollback()
                    if attempt == _APPEND_RETRIES - 1:
                        raise
                    continue
                return block

        raise RuntimeError("unreachable: append loop exhausted without returning")

    async def _head(self, session: AsyncSession) -> AuditBlock | None:
        result = await session.execute(
            select(AuditBlock).order_by(AuditBlock.block_index.desc()).limit(1)
        )
        return result.scalars().first()

    async def verify(self, session: AsyncSession) -> ChainVerification:
        """Recompute the entire chain from stored data."""
        result = await session.execute(select(AuditBlock).order_by(AuditBlock.block_index))
        return verify_blocks(list(result.scalars().all()))
