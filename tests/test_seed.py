"""Seed corpus tests.

The load-bearing test here is :class:`TestReproducibility`. The batch demo's
credibility rests on a judge being able to regenerate the corpus and get the
same numbers (workflow.md §4.5), and the classic way that silently breaks is a
wall-clock read creeping into the generator.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.db.enums import AttemptKind, ErrorSource, PaymentMethod, PaymentStatus
from app.db.models import Consent, Customer, MessageTemplate, PaymentAttempt, PolicyConfig
from app.db.seed import ANCHOR_IST, N_DND, N_OPTED_OUT, seed_to_engine
from app.db.session import create_engine

# asyncio_mode = "auto" in pyproject.toml collects async tests automatically.


async def _count(engine: AsyncEngine, stmt) -> int:  # type: ignore[no-untyped-def]
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        return int(await s.scalar(stmt) or 0)


# ---------------------------------------------------------------------------
class TestComposition:
    async def test_exactly_420_transactions(self, seeded_engine: AsyncEngine) -> None:
        total = await _count(seeded_engine, select(func.count()).select_from(PaymentAttempt))
        assert total == 420

    async def test_140_customers(self, seeded_engine: AsyncEngine) -> None:
        assert await _count(seeded_engine, select(func.count()).select_from(Customer)) == 140

    @pytest.mark.parametrize(
        ("status", "kind", "expected"),
        [
            (PaymentStatus.CAPTURED, AttemptKind.CHECKOUT, 210),
            (PaymentStatus.FAILED, AttemptKind.CHECKOUT, 96),
            (PaymentStatus.ABANDONED, AttemptKind.CHECKOUT, 62),
        ],
    )
    async def test_checkout_segments(
        self,
        seeded_engine: AsyncEngine,
        status: PaymentStatus,
        kind: AttemptKind,
        expected: int,
    ) -> None:
        got = await _count(
            seeded_engine,
            select(func.count())
            .select_from(PaymentAttempt)
            .where(PaymentAttempt.status == status, PaymentAttempt.kind == kind),
        )
        assert got == expected

    async def test_28_invoices_and_24_subscriptions(self, seeded_engine: AsyncEngine) -> None:
        inv = await _count(
            seeded_engine,
            select(func.count())
            .select_from(PaymentAttempt)
            .where(PaymentAttempt.kind == AttemptKind.INVOICE),
        )
        sub = await _count(
            seeded_engine,
            select(func.count())
            .select_from(PaymentAttempt)
            .where(PaymentAttempt.kind == AttemptKind.SUBSCRIPTION),
        )
        assert (inv, sub) == (28, 24)

    async def test_policy_config_exists_for_merchant(self, seeded_engine: AsyncEngine) -> None:
        assert await _count(seeded_engine, select(func.count()).select_from(PolicyConfig)) == 1

    async def test_templates_cover_both_message_classes(self, seeded_engine: AsyncEngine) -> None:
        """Without a marketing template AND a transactional one, the
        consent-class rule has nothing to select between."""
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as s:
            classes = set((await s.execute(select(MessageTemplate.message_class))).scalars())
        assert len(classes) == 2


# ---------------------------------------------------------------------------
class TestFailureStratification:
    async def test_all_failures_carry_razorpay_telemetry(self, seeded_engine: AsyncEngine) -> None:
        """The deterministic classifier reads (error_source, error_step). A
        failure without them would have to be diagnosed by guesswork."""
        missing = await _count(
            seeded_engine,
            select(func.count())
            .select_from(PaymentAttempt)
            .where(
                PaymentAttempt.status == PaymentStatus.FAILED,
                PaymentAttempt.error_source.is_(None),
            ),
        )
        assert missing == 0

    async def test_failures_span_multiple_error_sources(self, seeded_engine: AsyncEngine) -> None:
        """A corpus of one failure type would make the classifier untestable."""
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as s:
            sources = set(
                (
                    await s.execute(
                        select(PaymentAttempt.error_source).where(
                            PaymentAttempt.status == PaymentStatus.FAILED
                        )
                    )
                ).scalars()
            )
        assert {ErrorSource.BANK, ErrorSource.CUSTOMER, ErrorSource.GATEWAY} <= sources

    async def test_subscription_failures_split_balance_vs_mandate(
        self, seeded_engine: AsyncEngine
    ) -> None:
        """The distinction playbook 4 must get right: insufficient balance means
        reschedule and retry; a dead mandate means retrying cannot succeed and
        burns a re-presentation."""
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as s:
            reasons = list(
                (
                    await s.execute(
                        select(PaymentAttempt.error_reason).where(
                            PaymentAttempt.kind == AttemptKind.SUBSCRIPTION
                        )
                    )
                ).scalars()
            )
        balance = sum(1 for r in reasons if r and "insufficient_funds" in r)
        mandate = sum(1 for r in reasons if r and "mandate" in r)
        assert balance > 0 and mandate > 0
        assert balance + mandate == 24

    async def test_abandoned_checkouts_have_no_payment_id(self, seeded_engine: AsyncEngine) -> None:
        """An abandoned cart never reached a payment, so a payment_id would be
        a fabrication."""
        wrong = await _count(
            seeded_engine,
            select(func.count())
            .select_from(PaymentAttempt)
            .where(
                PaymentAttempt.status == PaymentStatus.ABANDONED,
                PaymentAttempt.payment_id.is_not(None),
            ),
        )
        assert wrong == 0


# ---------------------------------------------------------------------------
class TestConsentProfile:
    async def test_opted_out_customers_exist(self, seeded_engine: AsyncEngine) -> None:
        """Stopping rule S-07 needs someone to stop for."""
        n = await _count(
            seeded_engine,
            select(func.count()).select_from(Consent).where(Consent.opted_out.is_(True)),
        )
        assert n == N_OPTED_OUT

    async def test_dnd_registered_customers_exist(self, seeded_engine: AsyncEngine) -> None:
        n = await _count(
            seeded_engine,
            select(func.count()).select_from(Consent).where(Consent.dnd_registered.is_(True)),
        )
        assert n == N_DND

    async def test_some_customers_lack_marketing_consent(self, seeded_engine: AsyncEngine) -> None:
        """If everyone consented to marketing, the consent-class rule (§9.2)
        would never fire and the discount path would go untested."""
        n = await _count(
            seeded_engine,
            select(func.count()).select_from(Consent).where(Consent.marketing.is_(False)),
        )
        assert n >= 22

    async def test_every_customer_has_a_consent_row(self, seeded_engine: AsyncEngine) -> None:
        """A missing consent row must never be readable as implied consent."""
        customers = await _count(seeded_engine, select(func.count()).select_from(Customer))
        consents = await _count(seeded_engine, select(func.count()).select_from(Consent))
        assert customers == consents

    async def test_opted_out_rows_record_when_and_where(self, seeded_engine: AsyncEngine) -> None:
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as s:
            rows = list(
                (await s.execute(select(Consent).where(Consent.opted_out.is_(True)))).scalars()
            )
        assert all(r.opted_out_at is not None and r.opt_out_source for r in rows)


# ---------------------------------------------------------------------------
class TestHeroCases:
    """The three demo cases, planted inside a realistic distribution."""

    async def test_ananya_upi_timeout(self, seeded_engine: AsyncEngine) -> None:
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as s:
            attempt = await s.scalar(
                select(PaymentAttempt).where(PaymentAttempt.order_id == "order_glowkart_ananya01")
            )
            assert attempt is not None
            assert attempt.amount_paise == 429_900  # ₹4,299
            assert attempt.method is PaymentMethod.UPI
            assert attempt.issuer == "HDFC"
            # Bank-side fault, read from Razorpay rather than inferred.
            assert attempt.error_source is ErrorSource.BANK

            customer = await s.get(Customer, attempt.customer_id)
            assert customer is not None
            assert customer.first_name == "Ananya"
            assert customer.ltv_paise == 1_480_000  # ₹14,800
            assert customer.success_orders_count == 4

    async def test_ananya_has_no_marketing_consent(self, seeded_engine: AsyncEngine) -> None:
        """This is why her recovery must be a zero-discount transactional link:
        a discount is a marketing-class message she has not opted in to."""
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as s:
            attempt = await s.scalar(
                select(PaymentAttempt).where(PaymentAttempt.order_id == "order_glowkart_ananya01")
            )
            assert attempt is not None
            consent = await s.get(Consent, attempt.customer_id)
            assert consent is not None
            assert consent.marketing is False
            assert consent.transactional is True

    async def test_rahul_invoice_exceeds_autonomous_ceiling(
        self, seeded_engine: AsyncEngine
    ) -> None:
        """₹18,500 > ₹10,000, so this case must escalate to a human (rung A2)."""
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as s:
            attempt = await s.scalar(
                select(PaymentAttempt).where(PaymentAttempt.invoice_id == "inv_glowkart_rahul01")
            )
            assert attempt is not None
            assert attempt.amount_paise == 1_850_000
            assert attempt.kind is AttemptKind.INVOICE

            policy = await s.scalar(select(PolicyConfig))
            assert policy is not None
            assert attempt.amount_paise >= policy.max_autonomous_amount_paise

            customer = await s.get(Customer, attempt.customer_id)
            assert customer is not None
            assert customer.is_business is True

    async def test_vikram_mandate_is_revoked_not_underfunded(
        self, seeded_engine: AsyncEngine
    ) -> None:
        """Retrying a revoked mandate is guaranteed to fail and burns a
        re-presentation attempt -- it needs re-authorisation instead."""
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as s:
            attempt = await s.scalar(
                select(PaymentAttempt).where(
                    PaymentAttempt.subscription_id == "sub_glowkart_vikram01"
                )
            )
            assert attempt is not None
            assert attempt.method is PaymentMethod.EMANDATE
            assert attempt.error_reason is not None
            assert "mandate" in attempt.error_reason
            assert "insufficient" not in attempt.error_reason

    async def test_hero_cases_are_not_the_first_rows(self, seeded_engine: AsyncEngine) -> None:
        """A demo whose showcase rows are the first rows demonstrates nothing.

        The heroes sit inside the ordinary distribution; the agent finds them
        the same way it finds everything else.
        """
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as s:
            first_ten = list(
                (
                    await s.execute(
                        select(PaymentAttempt.order_id).order_by(PaymentAttempt.id).limit(10)
                    )
                ).scalars()
            )
        assert "order_glowkart_ananya01" not in first_ten


# ---------------------------------------------------------------------------
class TestNoFabricatedOutcomes:
    async def test_seed_writes_no_recovery_cases(self, seeded_engine: AsyncEngine) -> None:
        """The seed provides inputs only. Outcomes are produced by the agent at
        run time -- a pre-seeded 'recovered' row would be a fabricated result.
        """
        from app.db.models import RecoveryAction, RecoveryCase

        assert await _count(seeded_engine, select(func.count()).select_from(RecoveryCase)) == 0
        assert await _count(seeded_engine, select(func.count()).select_from(RecoveryAction)) == 0

    async def test_seed_writes_no_experiment_assignments(self, seeded_engine: AsyncEngine) -> None:
        from app.db.models import ExperimentAssignment

        n = await _count(seeded_engine, select(func.count()).select_from(ExperimentAssignment))
        assert n == 0


# ---------------------------------------------------------------------------
class TestReproducibility:
    """The batch demo's credibility depends on this."""

    async def test_same_seed_produces_identical_corpus(self, tmp_path: Path) -> None:
        digests = []
        for name in ("a.db", "b.db"):
            eng = create_engine(f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}")
            await seed_to_engine(eng, seed=20260905)
            digests.append(await _corpus_digest(eng))
            await eng.dispose()
        assert digests[0] == digests[1], "identical seeds produced different corpora"

    async def test_different_seed_produces_different_corpus(self, tmp_path: Path) -> None:
        """Guards against the digest being computed over something constant --
        a reproducibility test that cannot fail proves nothing."""
        digests = []
        for name, seed in (("c.db", 20260905), ("d.db", 111)):
            eng = create_engine(f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}")
            await seed_to_engine(eng, seed=seed)
            digests.append(await _corpus_digest(eng))
            await eng.dispose()
        assert digests[0] != digests[1]

    async def test_anchor_instant_is_fixed_not_now(self) -> None:
        """Deriving timestamps from the wall clock would make the committed
        database differ on every run -- silently destroying the reproducibility
        claim in §4.5."""
        assert ANCHOR_IST.year == 2026
        assert ANCHOR_IST.tzinfo is not None
        assert ANCHOR_IST.isoformat() == "2026-09-01T09:00:00+05:30"

    async def test_all_timestamps_precede_the_anchor(self, seeded_engine: AsyncEngine) -> None:
        """Nothing in a historical corpus should be in the anchor's future."""
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False)
        async with factory() as s:
            latest = await s.scalar(select(func.max(PaymentAttempt.attempted_at)))
        assert latest is not None
        assert latest <= ANCHOR_IST


async def _corpus_digest(engine: AsyncEngine) -> str:
    """Hash the corpus content (not row order or file bytes)."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        rows = list(
            (
                await s.execute(
                    select(
                        PaymentAttempt.id,
                        PaymentAttempt.customer_id,
                        PaymentAttempt.amount_paise,
                        PaymentAttempt.status,
                        PaymentAttempt.error_reason,
                        PaymentAttempt.attempted_at,
                    ).order_by(PaymentAttempt.id)
                )
            ).all()
        )
        custs = list(
            (
                await s.execute(
                    select(Customer.id, Customer.first_name, Customer.ltv_paise).order_by(
                        Customer.id
                    )
                )
            ).all()
        )
    payload = "|".join(str(r) for r in rows) + "||" + "|".join(str(c) for c in custs)
    return hashlib.sha256(payload.encode()).hexdigest()
