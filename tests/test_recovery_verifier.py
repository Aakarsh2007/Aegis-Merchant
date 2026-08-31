"""How a recovery was proven, as a type rather than a string convention.

INC-030. Two problems, found by reading the dashboard's own hover text after a
real recovery on 2026-08-31:

**The basis named a mechanism that did not happen.** It asserted every
RAZORPAY_VERIFIED rupee was *"proven by a REAL signed Razorpay webhook"*. The
first live recovery of that day was proven by a **poll** — the webhook was lost
to a dead tunnel and ``workers/reconcile`` picked it up. The badge was correct;
the sentence under it was false, on the project's most load-bearing tile.

**The real/simulated split was decided by sniffing an id prefix.** The comment
above ``SIMULATED_EVENT_PREFIX`` already named the hazard: *"a simulator that
wrote a realistic-looking one would silently promote seeded outcomes to
RAZORPAY_VERIFIED"*. A convention that only holds while everyone remembers it
is not a guarantee.

Both are now the same fix: ``recovery_verified_via``, a column. The tests below
are weighted towards the promotion hazard, because a simulated rupee appearing
on the verified tile is the single worst thing this system could do.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import FakeClock
from app.db.enums import CaseStatus, Playbook, RecoveryVerifier
from app.db.ids import idempotency_hash
from app.db.models import Customer, Merchant, RecoveryCase
from app.services.metrics import overview

MOMENT = datetime(2026, 9, 1, 11, 0, tzinfo=UTC)


async def _fixtures(session: AsyncSession) -> tuple[str, str]:
    merchant = Merchant(
        id="mch_t",
        business_name="T",
        razorpay_key_id="rzp_test_x",
        autopilot_enabled=True,
        created_at=MOMENT,
    )
    session.add(merchant)
    await session.flush()
    customer = Customer(
        id="cus_t",
        merchant_id="mch_t",
        first_name="A",
        phone_masked="+91******0000",
        phone_hash="0" * 64,
        first_seen_at=MOMENT,
    )
    session.add(customer)
    await session.flush()
    return merchant.id, customer.id


async def _case(
    session: AsyncSession,
    case_id: str,
    *,
    verified_by: str | None,
    verified_via: RecoveryVerifier | None,
    paise: int = 5000,
) -> None:
    merchant_id, customer_id = "mch_t", "cus_t"
    session.add(
        RecoveryCase(
            id=case_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            playbook=Playbook.PAYMENT_FAILURE,
            status=CaseStatus.RECOVERED if verified_by else CaseStatus.MONITORING,
            amount_paise=paise,
            attempt_no=1,
            recovered_amount_paise=paise if verified_by else 0,
            recovery_verified_by=verified_by,
            recovery_verified_via=verified_via,
            idempotency_hash=idempotency_hash(merchant_id, case_id, "PAYMENT_FAILURE"),
            is_demo=False,
            window_expires_at=MOMENT + timedelta(hours=24),
            created_at=MOMENT,
        )
    )
    await session.flush()


# ===========================================================================
class TestTheEnum:
    def test_three_mechanisms(self) -> None:
        assert {v.value for v in RecoveryVerifier} == {
            "WEBHOOK",
            "API_RECONCILIATION",
            "SIMULATOR",
        }

    def test_a_poll_is_distinguishable_from_a_webhook(self) -> None:
        """The distinction the basis text needed and did not have."""
        assert RecoveryVerifier.WEBHOOK is not RecoveryVerifier.API_RECONCILIATION


# ===========================================================================
class TestSimulatedNeverReachesTheVerifiedTile:
    """The promotion hazard. Weighted heavily on purpose."""

    async def test_a_simulator_row_is_excluded(self, session: AsyncSession) -> None:
        await _fixtures(session)
        await _case(
            session,
            "RC-S1",
            verified_by="sim_evt_abc",
            verified_via=RecoveryVerifier.SIMULATOR,
            paise=999_999,
        )
        await session.commit()

        report = await overview(session, clock=FakeClock(MOMENT))
        assert report.gross_recovered.paise == 0
        assert report.gross_simulated.paise == 999_999

    async def test_a_convincing_id_does_not_promote_a_simulator_row(
        self, session: AsyncSession
    ) -> None:
        """**The exact hazard the old prefix check admitted.**

        A simulator row whose id looks like a real Razorpay event id. Under the
        prefix convention this would have landed on the RAZORPAY_VERIFIED tile.
        The column refuses it regardless of how plausible the id is.
        """
        await _fixtures(session)
        await _case(
            session,
            "RC-S2",
            verified_by="TWSSP5BW90Y89E",  # indistinguishable from a real event id
            verified_via=RecoveryVerifier.SIMULATOR,
            paise=500_000,
        )
        await session.commit()

        report = await overview(session, clock=FakeClock(MOMENT))
        assert report.gross_recovered.paise == 0, (
            "a SIMULATOR row with a realistic id reached the verified tile -- this "
            "is the precise overclaim the badge exists to prevent"
        )

    async def test_a_sim_prefix_is_refused_even_if_the_column_lies(
        self, session: AsyncSession
    ) -> None:
        """Both conditions must hold, so one bug cannot promote a row.

        The mirror of the test above: a row mislabelled WEBHOOK but carrying a
        `sim_evt_` id is still excluded. Belt and braces, deliberately.
        """
        await _fixtures(session)
        await _case(
            session,
            "RC-S3",
            verified_by="sim_evt_deadbeef",
            verified_via=RecoveryVerifier.WEBHOOK,
            paise=750_000,
        )
        await session.commit()

        report = await overview(session, clock=FakeClock(MOMENT))
        assert report.gross_recovered.paise == 0


# ===========================================================================
class TestBothRealMechanismsCount:
    """A poll is Razorpay asserting the payment, exactly as a webhook is."""

    @pytest.mark.parametrize(
        ("verifier", "verified_by"),
        [
            (RecoveryVerifier.WEBHOOK, "TWSSP5BW90Y89E"),
            (RecoveryVerifier.API_RECONCILIATION, "plink_TWPwcbsfrYnIQQ"),
        ],
    )
    async def test_counts_on_the_verified_tile(
        self, session: AsyncSession, verifier: RecoveryVerifier, verified_by: str
    ) -> None:
        await _fixtures(session)
        await _case(session, "RC-R1", verified_by=verified_by, verified_via=verifier, paise=100)
        await session.commit()

        report = await overview(session, clock=FakeClock(MOMENT))
        assert report.gross_recovered.paise == 100

    async def test_a_lost_webhook_does_not_cost_a_recovery(self, session: AsyncSession) -> None:
        """DEC-037's whole point, asserted as a value rather than a doc claim.

        This is not hypothetical: on 2026-08-31 the tunnel died, Razorpay
        POSTed into a Cloudflare 1016, and the poll recovered the rupee.
        """
        await _fixtures(session)
        await _case(
            session,
            "RC-LOST",
            verified_by="plink_TWPwcbsfrYnIQQ",
            verified_via=RecoveryVerifier.API_RECONCILIATION,
            paise=100,
        )
        await session.commit()
        report = await overview(session, clock=FakeClock(MOMENT))
        assert report.gross_recovered.paise == 100


# ===========================================================================
class TestTheBasisTellsTheTruth:
    """The sentence a judge reads on hover."""

    async def test_it_names_both_mechanisms_with_counts(self, session: AsyncSession) -> None:
        await _fixtures(session)
        await _case(
            session,
            "RC-W",
            verified_by="TWSSP5BW90Y89E",
            verified_via=RecoveryVerifier.WEBHOOK,
            paise=100,
        )
        await _case(
            session,
            "RC-P",
            verified_by="plink_TWPwcbsfrYnIQQ",
            verified_via=RecoveryVerifier.API_RECONCILIATION,
            paise=100,
        )
        await session.commit()

        basis = (await overview(session, clock=FakeClock(MOMENT))).gross_recovered.basis
        assert "1 by signed webhook" in basis
        assert "1 by direct API reconciliation" in basis

    async def test_it_does_not_assert_a_webhook_for_a_poll(self, session: AsyncSession) -> None:
        """**INC-030 itself.**

        With only a polled recovery in the population, the basis must not claim
        a signed webhook proved it. This is the assertion the old wording failed.
        """
        await _fixtures(session)
        await _case(
            session,
            "RC-P",
            verified_by="plink_TWPwcbsfrYnIQQ",
            verified_via=RecoveryVerifier.API_RECONCILIATION,
            paise=100,
        )
        await session.commit()

        basis = (await overview(session, clock=FakeClock(MOMENT))).gross_recovered.basis
        assert "0 by signed webhook" in basis
        assert "1 by direct API reconciliation" in basis
        # The old text asserted this unconditionally.
        assert "proven by a REAL signed Razorpay webhook" not in basis

    async def test_the_zero_basis_says_nothing_is_proven(self, session: AsyncSession) -> None:
        """An empty population must not claim counts of zero mechanisms as
        though something had been measured."""
        await _fixtures(session)
        await session.commit()
        basis = (await overview(session, clock=FakeClock(MOMENT))).gross_recovered.basis
        assert "nothing has been proven by Razorpay yet" in basis


# ===========================================================================
class TestWriteSitesSetTheColumn:
    """A column nothing populates is INC-026 again.

    Checked by reading the source, because each write site lives behind a
    provider call or a webhook and the cheapest honest check that they are all
    covered is that each assignment exists.
    """

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("apps/api/app/ingest/settle.py", "RecoveryVerifier.WEBHOOK"),
            ("apps/api/app/workers/reconcile.py", "RecoveryVerifier.API_RECONCILIATION"),
            ("apps/api/app/workers/batch.py", "RecoveryVerifier.SIMULATOR"),
        ],
    )
    def test_each_write_site_sets_a_verifier(self, path: str, expected: str) -> None:
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / path).read_text(encoding="utf-8")
        assert "recovery_verified_via" in source, f"{path} never sets the verifier"
        assert expected in source, f"{path} does not set {expected}"
