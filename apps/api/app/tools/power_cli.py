"""`python tasks.py power` — the causal question, priced in cases.

Prints the arithmetic behind `docs/PRE-REGISTRATION.md` §5, so the figures in
that document are reproducible by anyone who clones the repo rather than taken
on trust. The pre-registration also carries the SHA-256 of the plan it commits
to; this command prints the file's current digest so a reader can confirm the
document has not been edited since it was registered.

Deliberately prints no p-value and no significance verdict. §6 of the
pre-registration commits to analysing once, at the full sample; a significance
number available on demand while data accumulates is an invitation to stop when
it looks good.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.core.power import Z_ALPHA_05, Z_POWER_80, required_per_arm, sample_size_plan
from app.db.enums import ExperimentArm
from app.db.models import ExperimentAssignment
from app.db.session import create_engine
from app.routers.metrics import (
    PREREG_CONTROL_FRACTION,
    PREREG_P_CONTROL,
    PREREG_P_TREATMENT,
)

PREREG = Path(__file__).resolve().parents[4] / "docs" / "PRE-REGISTRATION.md"


def _digest() -> str:
    if not PREREG.is_file():
        return "MISSING"
    return hashlib.sha256(PREREG.read_bytes()).hexdigest()


async def _arms(database_url: str) -> tuple[int, int]:
    engine = create_engine(database_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            counts = {}
            for arm in ExperimentArm:
                counts[arm] = int(
                    await session.scalar(
                        select(func.count(ExperimentAssignment.case_id)).where(
                            ExperimentAssignment.arm == arm
                        )
                    )
                    or 0
                )
    finally:
        await engine.dispose()
    return counts[ExperimentArm.CONTROL], counts[ExperimentArm.TREATMENT]


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    settings = get_settings()
    database_url = args[0] if args else settings.database_url

    try:
        control_now, treatment_now = asyncio.run(_arms(database_url))
    except Exception as exc:
        print(f"could not read arm sizes from {database_url}: {exc}")
        print("reporting the design requirement only.\n")
        control_now = treatment_now = 0

    plan = sample_size_plan(
        control_now=control_now,
        treatment_now=treatment_now,
        p_control=PREREG_P_CONTROL,
        p_treatment=PREREG_P_TREATMENT,
        control_fraction=PREREG_CONTROL_FRACTION,
    )

    line = "=" * 70
    print(line)
    print("  DID REVPILOT CAUSE ADDITIONAL CUSTOMERS TO PAY?")
    print("  Not proven. This is what proving it would cost.")
    print(line)
    print()
    print(f"  registered   {PREREG.name}  sha256 {_digest()[:16]}")
    print("  design       two-proportion z-test, alpha 0.05 two-sided, power 0.80")
    print(f"               z_alpha {Z_ALPHA_05:.6f}   z_power {Z_POWER_80:.6f}")
    print(f"  allocation   {PREREG_CONTROL_FRACTION:.0%} control (balanced)")
    print(f"  effect size  control {PREREG_P_CONTROL:.2%} -> treated {PREREG_P_TREATMENT:.2%}")
    print(
        f"               lift {PREREG_P_TREATMENT - PREREG_P_CONTROL:.2%} absolute"
        "  -- DECLARED, from the simulation, not observed"
    )
    print()
    print(f"  {'':13}{'have':>9}{'need':>9}{'short':>9}")
    print(
        f"  {'control':13}{plan.control_now:>9}{plan.control_required:>9}"
        f"{max(0, plan.control_required - plan.control_now):>9}"
    )
    print(
        f"  {'treated':13}{plan.treatment_now:>9}{plan.treatment_required:>9}"
        f"{max(0, plan.treatment_required - plan.treatment_now):>9}"
    )
    print(
        f"  {'total':13}{plan.control_now + plan.treatment_now:>9}"
        f"{plan.control_required + plan.treatment_required:>9}{plan.cases_remaining:>9}"
    )
    print()
    print(f"  COMPLETION   {plan.completion:.1%}   powered: {plan.is_powered}")
    print("               the fraction of the BINDING arm, not of the total:")
    print("               power is governed by the smaller arm, and 5,000")
    print("               treated with 12 control is not 99% of an answer.")
    print()
    print("  Payment attempts still needed, by failure rate:")
    for rate in (0.08, 0.12, 0.20):
        print(f"    {rate:.0%} of attempts fail  ->  {plan.attempts_needed(rate):>8,} attempts")
    print()
    print("  Why balanced (PRE-REGISTRATION.md section 4):")
    for fraction, label in ((PREREG_CONTROL_FRACTION, "50/50"), (39 / 210, "81/19 demo")):
        c, t = required_per_arm(
            p_control=PREREG_P_CONTROL,
            p_treatment=PREREG_P_TREATMENT,
            control_fraction=fraction,
        )
        print(f"    {label:12} control {c:>5}  treated {t:>5}  total {c + t:>5}")
    bal_c, bal_t = required_per_arm(p_control=PREREG_P_CONTROL, p_treatment=PREREG_P_TREATMENT)
    unb_c, unb_t = required_per_arm(
        p_control=PREREG_P_CONTROL, p_treatment=PREREG_P_TREATMENT, control_fraction=39 / 210
    )
    print(
        f"    balancing saves {1 - (bal_c + bal_t) / (unb_c + unb_t):.1%} of the cases, and costs"
    )
    print("    the merchant recovery on half of all recoverable cases.")
    print()
    print("  BLOCKED ON, and neither is about our code:")
    print("    1. a merchant with the traffic above, and written consent to")
    print("       contact their customers, plus a data processing agreement.")
    print("    2. DLT/TRAI registration in the MERCHANT's name with approved")
    print("       templates. Weeks of lead time. The binding external gate.")
    print()
    print("  No p-value is printed here, by design. Section 6 commits to")
    print("  analysing once at the full sample; a significance number available")
    print("  on demand while data accumulates is an invitation to stop when it")
    print("  looks good, and that yields a spurious p < 0.05 about one time in")
    print("  three.")
    print(line)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
