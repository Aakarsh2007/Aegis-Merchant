"""Audit chain: does the verifier actually do work?

A hash chain is trivial to implement and trivial to implement *uselessly*. The
failure mode is a verifier that returns ``valid: true`` because nothing ever
made it return anything else — and it passes every test written by someone who
only feeds it untampered chains.

So the bulk of this file constructs specific tampers and asserts each one is
caught, at the right index, with a reason naming the right failure. The
untampered-chain test is the least interesting one here.

The honest-limitation test at the bottom asserts what the chain *cannot* do.
It exists so that nobody later reads `valid: true` as "nothing was removed".
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.clock import FakeClock
from app.db.models import AuditBlock
from app.db.types import to_db_iso
from app.tools.audit import (
    GENESIS_HASH,
    AuditChain,
    canonical_json,
    compute_block_hash,
    payload_digest,
    verify_blocks,
)


async def _append_n(chain: AuditChain, session: AsyncSession, n: int) -> None:
    for i in range(n):
        await chain.append(
            session,
            event_name="case.transitioned",
            actor="agent",
            payload={"step": i, "note": "ordinary activity"},
            case_id=None,
        )
    await session.commit()


async def _blocks(session: AsyncSession) -> list[AuditBlock]:
    result = await session.execute(select(AuditBlock).order_by(AuditBlock.block_index))
    return list(result.scalars().all())


# ===========================================================================
# canonical_json — determinism is the whole game
# ===========================================================================
class TestCanonicalJson:
    def test_key_order_does_not_change_the_bytes(self) -> None:
        """Two processes building the same dict differently must hash the same.

        Python preserves insertion order, so without sort_keys this passes in
        the process that wrote the block and fails in the one that verifies it.
        """
        a = canonical_json({"b": 1, "a": 2, "c": {"z": 1, "y": 2}})
        b = canonical_json({"c": {"y": 2, "z": 1}, "a": 2, "b": 1})
        assert a == b

    def test_no_incidental_whitespace(self) -> None:
        assert canonical_json({"a": 1, "b": [1, 2]}) == '{"a":1,"b":[1,2]}'

    def test_unicode_survives_a_round_trip(self) -> None:
        """Template text is Hindi and Tamil in production. A serialiser that
        mangled it would corrupt the ledger for exactly the merchants the
        product is for."""
        payload = {"template": "आपका भुगतान विफल", "tamil": "பணம் செலுத்த"}
        canonical = canonical_json(payload)
        assert "आपका" in canonical
        assert payload_digest(canonical) == payload_digest(canonical_json(payload))

    def test_a_datetime_in_the_payload_does_not_raise(self) -> None:
        """Losing an audit block to a serialisation error is worse than
        storing a stringified datetime."""
        out = canonical_json({"at": datetime(2026, 9, 1, tzinfo=UTC)})
        assert "2026-09-01" in out


# ===========================================================================
# The hash function
# ===========================================================================
class TestBlockHash:
    def test_field_boundaries_are_unambiguous(self) -> None:
        """Bare concatenation makes ("ab","c") and ("a","bc") collide. The
        unit separator is what stops two different blocks hashing alike."""
        at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        h1 = compute_block_hash(
            prev_hash="a" * 64, payload_canonical="bc", block_index=1, created_at=at
        )
        h2 = compute_block_hash(
            prev_hash="a" * 63 + "b", payload_canonical="c", block_index=1, created_at=at
        )
        assert h1 != h2

    def test_every_hashed_field_changes_the_hash(self) -> None:
        base = {
            "prev_hash": "a" * 64,
            "payload_canonical": '{"x":1}',
            "block_index": 3,
            "created_at": datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        }
        original = compute_block_hash(**base)  # type: ignore[arg-type]
        for field, value in (
            ("prev_hash", "b" * 64),
            ("payload_canonical", '{"x":2}'),
            ("block_index", 4),
            ("created_at", datetime(2026, 9, 1, 12, 0, 1, tzinfo=UTC)),
        ):
            assert compute_block_hash(**{**base, field: value}) != original, (  # type: ignore[arg-type]
                f"{field} is not actually part of the hash"
            )


# ===========================================================================
# Round-trip — the INC-013 shape
# ===========================================================================
class TestDatabaseRoundTrip:
    async def test_microsecond_timestamps_still_verify(self, engine: AsyncEngine) -> None:
        """`to_db_iso` truncates microseconds to milliseconds.

        If the hash were computed over `datetime.isoformat()`, every block
        written with sub-millisecond precision would verify as CORRUPT the
        moment it was read back — a value that survives in memory and changes
        on round-trip, which is exactly INC-013. A clock landing on a whole
        second would hide this, so the timestamp here deliberately does not.
        """
        clock = FakeClock(datetime(2026, 9, 1, 11, 30, 15, 123456, tzinfo=UTC))
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        chain = AuditChain(clock)

        async with factory() as s:
            await _append_n(chain, s, 3)

        # A *different* session, so nothing is served from the identity map.
        async with factory() as s:
            assert (await chain.verify(s)).valid, (
                "blocks written with microsecond precision must verify after reload"
            )

    async def test_the_stored_timestamp_is_what_was_hashed(self, engine: AsyncEngine) -> None:
        clock = FakeClock(datetime(2026, 9, 1, 11, 30, 15, 999999, tzinfo=UTC))
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            await _append_n(AuditChain(clock), s, 1)
        async with factory() as s:
            block = (await _blocks(s))[0]
            assert (
                compute_block_hash(
                    prev_hash=block.prev_hash,
                    payload_canonical=block.payload_canonical,
                    block_index=block.block_index,
                    created_at=block.created_at,
                )
                == block.current_hash
            )
            assert to_db_iso(block.created_at).endswith("Z")


# ===========================================================================
# Structure
# ===========================================================================
class TestChainStructure:
    async def test_genesis_block_links_to_zeroes(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            await _append_n(AuditChain(clock), s, 1)
            block = (await _blocks(s))[0]
            assert block.block_index == 0
            assert block.prev_hash == GENESIS_HASH

    async def test_each_block_links_to_its_predecessor(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            await _append_n(AuditChain(clock), s, 6)
            blocks = await _blocks(s)
            for prev, nxt in pairwise(blocks):
                assert nxt.prev_hash == prev.current_hash

    async def test_an_untampered_chain_verifies(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        chain = AuditChain(clock)
        async with factory() as s:
            await _append_n(chain, s, 8)
            result = await chain.verify(s)
        assert result.valid
        assert result.blocks == 8
        assert result.head_hash is not None
        assert result.first_divergence_index is None

    async def test_concurrent_appends_do_not_collide(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        """Two coroutines appending at once must not mint the same index.

        The lock is the fast path; UNIQUE(block_index) is the guarantee. If
        both were absent this test would produce a chain with a duplicate
        index, which the verifier would then reject.
        """
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        chain = AuditChain(clock)
        async with factory() as s:
            await asyncio.gather(
                *(
                    chain.append(s, event_name="e", actor="agent", payload={"i": i})
                    for i in range(10)
                )
            )
            await s.commit()
            blocks = await _blocks(s)
            assert [b.block_index for b in blocks] == list(range(10))
            assert (await chain.verify(s)).valid


# ===========================================================================
# THE POINT OF THE FILE: tampers must be caught
# ===========================================================================
class TestTamperDetection:
    """Each test edits the database the way an attacker with SQL access would,
    then asserts the verifier catches it at the right index for the right
    reason. A verifier that hardcoded `valid: true` fails every one."""

    async def test_edited_payload_is_caught(self, engine: AsyncEngine, clock: FakeClock) -> None:
        """The headline demo: change a recorded amount, watch it break."""
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        chain = AuditChain(clock)
        async with factory() as s:
            await _append_n(chain, s, 5)

        async with factory() as s:
            block = (await _blocks(s))[2]
            block.payload_canonical = '{"note":"ordinary activity","step":999}'
            await s.commit()

        async with factory() as s:
            result = await chain.verify(s)
        assert not result.valid
        assert result.first_divergence_index == 2
        assert "payload" in (result.reason or "")

    async def test_recomputed_payload_hash_still_breaks_the_chain(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        """A smarter attacker edits the payload AND fixes payload_hash. The
        block's own current_hash must still catch it — otherwise payload_hash
        would be the only check and one UPDATE too many would defeat it."""
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        chain = AuditChain(clock)
        async with factory() as s:
            await _append_n(chain, s, 5)

        async with factory() as s:
            block = (await _blocks(s))[3]
            forged = '{"note":"ordinary activity","step":1000}'
            block.payload_canonical = forged
            block.payload_hash = payload_digest(forged)
            await s.commit()

        async with factory() as s:
            result = await chain.verify(s)
        assert not result.valid
        assert result.first_divergence_index == 3
        assert "recomputed" in (result.reason or "")

    async def test_edited_current_hash_is_caught(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        chain = AuditChain(clock)
        async with factory() as s:
            await _append_n(chain, s, 5)
        async with factory() as s:
            (await _blocks(s))[1].current_hash = "f" * 64
            await s.commit()
        async with factory() as s:
            result = await chain.verify(s)
        assert not result.valid
        assert result.first_divergence_index == 1

    async def test_edited_timestamp_is_caught(self, engine: AsyncEngine, clock: FakeClock) -> None:
        """Backdating a block is a plausible attack — it moves an action out
        of a quiet-hours window after the fact."""
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        chain = AuditChain(clock)
        async with factory() as s:
            await _append_n(chain, s, 4)
        async with factory() as s:
            block = (await _blocks(s))[2]
            block.created_at = block.created_at - timedelta(hours=3)
            await s.commit()
        async with factory() as s:
            result = await chain.verify(s)
        assert not result.valid
        assert result.first_divergence_index == 2

    async def test_deleted_middle_block_is_caught(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        chain = AuditChain(clock)
        async with factory() as s:
            await _append_n(chain, s, 6)
        async with factory() as s:
            await s.delete((await _blocks(s))[3])
            await s.commit()
        async with factory() as s:
            result = await chain.verify(s)
        assert not result.valid
        assert result.first_divergence_index == 3
        assert "deleted" in (result.reason or "")

    async def test_swapped_blocks_are_caught(self, engine: AsyncEngine, clock: FakeClock) -> None:
        """Reordering changes which action came first, which is the whole
        question in a dispute. The index is inside the hash so it cannot be
        swapped silently."""
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        chain = AuditChain(clock)
        async with factory() as s:
            await _append_n(chain, s, 5)
        async with factory() as s:
            blocks = await _blocks(s)
            blocks[1].payload_canonical, blocks[2].payload_canonical = (
                blocks[2].payload_canonical,
                blocks[1].payload_canonical,
            )
            await s.commit()
        async with factory() as s:
            result = await chain.verify(s)
        assert not result.valid
        assert result.first_divergence_index == 1


# ===========================================================================
# What the chain honestly cannot do
# ===========================================================================
class TestStatedLimitations:
    async def test_tail_truncation_is_not_detected_and_we_say_so(
        self, engine: AsyncEngine, clock: FakeClock
    ) -> None:
        """Deleting the last k blocks leaves a shorter, perfectly valid chain.

        No construction that lives entirely inside the database can detect
        this. This test pins the limitation so it is impossible to later read
        `valid: true` as "nothing was removed" — and asserts the two fields an
        external observer needs (`blocks`, `head_hash`) actually change, since
        comparing them against a previously recorded value is the only real
        defence.
        """
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        chain = AuditChain(clock)
        async with factory() as s:
            await _append_n(chain, s, 6)
            before = await chain.verify(s)

        async with factory() as s:
            for block in (await _blocks(s))[4:]:
                await s.delete(block)
            await s.commit()

        async with factory() as s:
            after = await chain.verify(s)

        # The uncomfortable part, asserted rather than hidden:
        assert after.valid, "a truncated chain is still internally consistent"
        # ...and the part that makes it survivable:
        assert after.blocks == 4 and before.blocks == 6
        assert after.head_hash != before.head_hash
        assert "truncation" in (after.reason or "")

    def test_an_empty_chain_does_not_silently_pass(self) -> None:
        """`valid: true` with no explanation for a wiped table would be
        actively misleading, so the reason states it."""
        result = verify_blocks([])
        assert result.valid
        assert result.blocks == 0
        assert "empty" in (result.reason or "")
