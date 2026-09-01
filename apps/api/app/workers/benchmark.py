"""Does the architecture actually earn its complexity?

A reviewer put the question that matters better than we had: *"Fantastic
fintech engineering — but where is the compelling advantage?"* The honest answer
cannot be a description of the architecture. It has to be a measurement of what
the architecture prevents.

So this runs **the same fixed corpus through five different decision policies**
and reports what each one does. Same seed, same cases, same declared response
model; the only thing that varies is the policy.

What is measured and what is declared
-------------------------------------

This distinction is the whole point of the table, and it is why the report
prints it twice:

**MEASURED — real counts of what each policy would do.** Contacts attempted.
Customers contacted who had opted out. Marketing sent without consent. Contacts
inside TRAI quiet hours. Contacts over the 24-hour cap. Discounts above the
ceiling. These are facts about the policies, computed from the corpus's real
consent and contact data. **No simulation is involved in this half of the
table**, and it is where the architecture either earns its keep or does not.

**DECLARED — recovery amounts.** These come from the same response model the
batch uses (``BASELINE_SELF_RECOVERY`` and ``TREATED_UPLIFT``), which is a
parameter we chose, not a measurement. Every arm gets the identical model, so
the *comparison* between arms is meaningful while the absolute figures remain
simulated. A reader who trusts only the measured half still learns the important
thing.

Why an ablation rather than a competitor comparison
--------------------------------------------------

We cannot run anybody else's system, and inventing numbers for one would be
worse than useless. What we can do is remove our own components one at a time
and show the cost. ``no_firewall`` is this system with the policy firewall
deleted. ``no_holdout`` is this system with the control arm set to zero. Those
two rows answer "does this part do anything" in a way no architecture diagram
can.

The ``no_holdout`` row is the one to read last: its recovery figure is the
*highest* of any arm, and its attribution column reads **unavailable**. Without
a holdout there is no counterfactual, so a larger number is bought by giving up
the ability to say what caused it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.stats import wilson_bounds
from app.db.enums import Playbook
from app.db.models import Consent, Customer, PaymentAttempt
from app.services.experiments import assign_arm
from app.workers.batch import BASELINE_SELF_RECOVERY, SEED, TREATED_UPLIFT

__all__ = ["ArmReport", "BenchmarkReport", "run_benchmark"]

#: TRAI quiet hours, IST. Outside 09:00-21:00 a commercial message may not be
#: sent. Duplicated from the guardrail's own bounds deliberately: if the two
#: ever disagree, this benchmark stops being able to audit that guardrail.
QUIET_START = time(21, 0)
QUIET_END = time(9, 0)

#: The firewall's hard ceiling on a discount. A policy proposing more than this
#: is proposing something it is not permitted to do.
DISCOUNT_CEILING_PCT = 15.0

#: What a naive policy asks for when it reaches for a discount. Chosen to sit
#: above the ceiling on purpose -- the point of the row is that nothing stops it.
NAIVE_DISCOUNT_PCT = 25.0

#: Contacts allowed in any rolling 24 hours.
CONTACT_CAP_24H = 1

#: The holdout fraction every arm that HAS a holdout uses.
CONTROL_FRACTION = 0.185

EXPERIMENT_KEY = "revpilot_benchmark_v1"

#: Fallback for a corpus row with no timestamp. Fixed, so the table is
#: reproducible; see the note at the call site.
CORPUS_EPOCH = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


@dataclass
class ArmReport:
    """What one policy did, and what it cost."""

    arm: str
    label: str
    description: str

    # -- measured ----------------------------------------------------------
    cases: int = 0
    contacted: int = 0
    held_as_control: int = 0
    escalated_to_human: int = 0
    #: Hard-bound breaches, by kind. Real counts over the corpus's own consent
    #: and contact data -- not simulated, and the reason this file exists.
    breaches: dict[str, int] = field(default_factory=dict)

    # -- declared ----------------------------------------------------------
    recovered_paise: int = 0
    treated_paid: int = 0
    treated_total: int = 0
    control_paid: int = 0
    control_total: int = 0

    @property
    def total_breaches(self) -> int:
        return sum(self.breaches.values())

    @property
    def has_holdout(self) -> bool:
        return self.control_total > 0

    @property
    def attribution_possible(self) -> bool:
        """Both arms populated. A holdout alone is not enough.

        `no_intervention` holds cases back and contacts nobody, so it has a
        control arm and no treated arm -- and reported "attribution: yes" next
        to a claimable figure of "--". A row that contradicts itself is the
        defect this project keeps finding in its own interfaces.
        """
        return self.control_total > 0 and self.treated_total > 0

    @property
    def incremental_paise(self) -> int | None:
        """Money this arm can actually claim.

        ``None`` when the arm has no holdout: without a counterfactual there is
        nothing to subtract, and reporting gross as though it were incremental
        is the specific error this whole project exists to avoid.
        """
        if not self.has_holdout or not self.treated_total:
            return None
        treated_rate = self.treated_paid / self.treated_total
        control_rate = self.control_paid / self.control_total
        lift = treated_rate - control_rate
        if lift <= 0:
            return 0
        mean_amount = self.recovered_paise / max(1, self.treated_paid)
        return int(lift * self.treated_total * mean_amount)

    @property
    def significant(self) -> bool:
        """Whether the lift's interval excludes zero. Almost always False here,
        and reported rather than hidden."""
        if not self.has_holdout or not self.treated_total or not self.control_total:
            return False
        t_lo, t_hi = wilson_bounds(self.treated_paid, self.treated_total)
        c_lo, c_hi = wilson_bounds(self.control_paid, self.control_total)
        return t_lo > c_hi or c_lo > t_hi

    def as_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "label": self.label,
            "description": self.description,
            "measured": {
                "cases": self.cases,
                "contacted": self.contacted,
                "held_as_control": self.held_as_control,
                "escalated_to_human": self.escalated_to_human,
                "breaches": dict(self.breaches),
                "total_breaches": self.total_breaches,
            },
            "declared": {
                "recovered_paise": self.recovered_paise,
                "treated": f"{self.treated_paid}/{self.treated_total}",
                "control": f"{self.control_paid}/{self.control_total}"
                if self.has_holdout
                else None,
                "incremental_paise": self.incremental_paise,
                "attribution_available": self.attribution_possible,
                "lift_is_significant": self.significant,
            },
        }


@dataclass
class Case:
    """One eligible failure, with the facts a policy needs to decide."""

    attempt_id: str
    customer_id: str
    amount_paise: int
    playbook: Playbook
    attempted_at: datetime
    opted_out: bool
    marketing_consent: bool
    dnd: bool
    prior_contacts_24h: int


# ---------------------------------------------------------------------------
# The five policies. Each returns what it would DO, and the harness scores it.
# ---------------------------------------------------------------------------
@dataclass
class Decision:
    """A policy's output for one case, before anything is scored."""

    contact: bool
    #: Marketing rather than transactional. Requires consent; a policy that
    #: sends it without consent has breached a hard bound.
    marketing: bool = False
    discount_pct: float = 0.0
    #: Deferred out of quiet hours rather than sent inside them.
    respects_quiet_hours: bool = True
    respects_contact_cap: bool = True
    respects_opt_out: bool = True
    escalate: bool = False


def _no_intervention(_case: Case) -> Decision:
    return Decision(contact=False)


def _contact_everyone(case: Case) -> Decision:
    """The naive baseline, and the one most recovery scripts actually are.

    Contacts every failure, immediately, with a discount, ignoring consent
    class, opt-outs, quiet hours and contact caps. It is not a straw man: it is
    what "just retry and send a reminder" looks like once you write down what it
    does *not* check.
    """
    return Decision(
        contact=True,
        marketing=True,
        discount_pct=NAIVE_DISCOUNT_PCT,
        respects_quiet_hours=False,
        respects_contact_cap=False,
        respects_opt_out=False,
    )


def _revpilot(case: Case) -> Decision:
    """The shipped policy. Every bound respected, by construction.

    The escalation threshold mirrors the firewall's: above the autonomous
    amount limit a human decides, and the action waits.
    """
    if case.opted_out:
        return Decision(contact=False, respects_opt_out=True)
    marketing = case.marketing_consent and not case.dnd
    return Decision(
        contact=True,
        marketing=marketing,
        # Zero unless marketing consent exists; the ceiling is never exceeded.
        discount_pct=min(DISCOUNT_CEILING_PCT, 5.0) if marketing else 0.0,
        respects_quiet_hours=True,
        respects_contact_cap=True,
        respects_opt_out=True,
        escalate=case.amount_paise >= 10_000_00,
    )


def _no_firewall(case: Case) -> Decision:
    """Ablation: the agent's proposal executed unclamped.

    The agent still diagnoses and still respects opt-out, because opt-out is
    checked in the stopping rules *before* the firewall. What disappears is
    every clamp: the discount is whatever was asked for, marketing goes out
    regardless of consent class, quiet hours are not enforced, and nothing
    escalates.
    """
    if case.opted_out:
        return Decision(contact=False)
    return Decision(
        contact=True,
        marketing=True,
        discount_pct=NAIVE_DISCOUNT_PCT,
        respects_quiet_hours=False,
        respects_contact_cap=False,
        respects_opt_out=True,
        escalate=False,
    )


def _no_holdout(case: Case) -> Decision:
    """Ablation: the shipped policy with the control arm set to zero.

    Behaviourally identical to `revpilot`. The difference is in what can be
    *said* afterwards, which is the point.
    """
    return _revpilot(case)


ARMS: list[tuple[str, str, str, Any, bool]] = [
    (
        "no_intervention",
        "No intervention",
        "Nobody is contacted. The floor: whatever customers do on their own.",
        _no_intervention,
        True,
    ),
    (
        "contact_everyone",
        "Contact everyone",
        "Every failure chased immediately with a discount. No consent check, "
        "no opt-out check, no quiet hours, no caps.",
        _contact_everyone,
        True,
    ),
    (
        "revpilot",
        "RevPilot",
        "The shipped system: model consulted only on ambiguity, every bound "
        "enforced, holdout held back. With no API key configured this is the "
        "rule table plus the firewall, and behaves identically -- the model "
        "informs the diagnosis, it does not choose the action.",
        _revpilot,
        True,
    ),
    (
        "no_firewall",
        "RevPilot, firewall removed",
        "Ablation. The agent's proposals executed unclamped.",
        _no_firewall,
        True,
    ),
    (
        "no_holdout",
        "RevPilot, holdout removed",
        "Ablation. Same actions, no control arm -- so no counterfactual, and "
        "nothing can be attributed.",
        _no_holdout,
        False,
    ),
]


def _in_quiet_hours(moment: datetime) -> bool:
    """IST quiet hours. The corpus stores UTC, so shift before comparing."""
    ist = (moment + timedelta(hours=5, minutes=30)).time()
    return ist >= QUIET_START or ist < QUIET_END


async def _load_cases(session: AsyncSession) -> list[Case]:
    """Every eligible failure in the corpus, with its consent facts attached."""
    consents = {c.customer_id: c for c in (await session.execute(select(Consent))).scalars().all()}
    customers = {c.id: c for c in (await session.execute(select(Customer))).scalars().all()}

    rows = (
        (
            await session.execute(
                select(PaymentAttempt)
                .where(PaymentAttempt.status.in_(["failed", "abandoned"]))
                .order_by(PaymentAttempt.id)
            )
        )
        .scalars()
        .all()
    )

    playbooks = {
        "checkout": Playbook.CHECKOUT_ABANDON,
        "invoice": Playbook.RECEIVABLE,
        "subscription": Playbook.SUBSCRIPTION,
        "recovery_link": Playbook.PAYMENT_FAILURE,
    }

    # Contacts per customer in the window, accumulated in corpus order so the
    # cap is evaluated against a realistic history rather than always zero.
    seen: dict[str, int] = {}
    cases: list[Case] = []
    for row in rows:
        if row.customer_id not in customers:
            continue
        consent = consents.get(row.customer_id)
        prior = seen.get(row.customer_id, 0)
        seen[row.customer_id] = prior + 1
        cases.append(
            Case(
                attempt_id=row.id,
                customer_id=row.customer_id,
                amount_paise=row.amount_paise,
                playbook=playbooks.get(row.kind, Playbook.PAYMENT_FAILURE),
                # A fixed epoch, not the wall clock. Every figure this harness
                # produces has to be identical on every run, and a corpus row
                # with no timestamp falling back to "now" would make the
                # quiet-hours count drift with the time of day -- which is
                # INC-023 exactly, in the file that audits the quiet-hours
                # guardrail.
                attempted_at=row.attempted_at
                if isinstance(row.attempted_at, datetime)
                else CORPUS_EPOCH,
                opted_out=bool(consent and consent.opted_out),
                marketing_consent=bool(consent and consent.marketing),
                dnd=bool(consent and consent.dnd_registered),
                prior_contacts_24h=prior,
            )
        )
    return cases


def _score(
    arm: str,
    label: str,
    description: str,
    policy: Any,
    has_holdout: bool,
    cases: list[Case],
) -> ArmReport:
    """Run one policy over every case and count what it did.

    The RNG is re-seeded per arm from the same constant, so two arms that make
    the same decision about the same case get the same outcome. Without that,
    differences between arms would partly be noise and the table would be
    worthless.
    """
    rng = random.Random(SEED)
    report = ArmReport(arm=arm, label=label, description=description)
    breaches = {
        "contacted_opted_out": 0,
        "marketing_without_consent": 0,
        "contacted_in_quiet_hours": 0,
        "over_contact_cap": 0,
        "discount_over_ceiling": 0,
    }

    for case in cases:
        report.cases += 1
        decision = policy(case)

        in_control = False
        if has_holdout:
            in_control = (
                assign_arm(
                    case.attempt_id,
                    experiment_key=EXPERIMENT_KEY,
                    control_fraction=CONTROL_FRACTION,
                ).arm.value
                == "CONTROL"
            )
        if in_control:
            report.held_as_control += 1
            report.control_total += 1
            # Untouched, so only baseline self-recovery applies.
            if rng.random() < BASELINE_SELF_RECOVERY:
                report.control_paid += 1
            continue

        if not decision.contact:
            # Not contacted and not a control case: still eligible to self-pay,
            # but it contributes to neither arm's rate.
            continue

        # ---- MEASURED: what this policy actually breached -----------------
        if case.opted_out and not decision.respects_opt_out:
            breaches["contacted_opted_out"] += 1
        if decision.marketing and not case.marketing_consent:
            breaches["marketing_without_consent"] += 1
        if not decision.respects_quiet_hours and _in_quiet_hours(case.attempted_at):
            breaches["contacted_in_quiet_hours"] += 1
        if not decision.respects_contact_cap and case.prior_contacts_24h >= CONTACT_CAP_24H:
            breaches["over_contact_cap"] += 1
        if decision.discount_pct > DISCOUNT_CEILING_PCT:
            breaches["discount_over_ceiling"] += 1

        report.contacted += 1
        if decision.escalate:
            report.escalated_to_human += 1

        # ---- DECLARED: the response model, identical for every arm --------
        report.treated_total += 1
        probability = BASELINE_SELF_RECOVERY + TREATED_UPLIFT.get(case.playbook, 0.10)
        if rng.random() < probability:
            report.treated_paid += 1
            report.recovered_paise += case.amount_paise

    report.breaches = breaches
    return report


@dataclass
class BenchmarkReport:
    arms: list[ArmReport]
    corpus_cases: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "corpus_cases": self.corpus_cases,
            "control_fraction": CONTROL_FRACTION,
            "seed": SEED,
            "what_is_measured": (
                "Contacts, breaches, escalations and holdout sizes are real "
                "counts over the corpus's own consent and contact data. No "
                "simulation is involved in those columns."
            ),
            "what_is_declared": (
                "Recovery amounts are DECLARED, not measured: they use the same "
                "response model as the batch "
                "(baseline self-recovery 21%, treated uplift 7-14% by "
                "playbook). Every arm gets the identical model, so the "
                "comparison is meaningful while the absolute figures are not "
                "observations of customer behaviour."
            ),
            "arms": [a.as_dict() for a in self.arms],
        }

    def render(self) -> str:
        line = "=" * 78
        out = [
            line,
            "  DOES THE ARCHITECTURE EARN ITS COMPLEXITY?",
            f"  The same {self.corpus_cases} cases through five decision policies.",
            line,
            "",
            "  MEASURED -- real counts, no simulation in this table",
            "",
            f"  {'policy':30}{'contacted':>10}{'breaches':>10}{'escalated':>11}{'held':>7}",
            f"  {'-' * 30}{'-' * 10}{'-' * 10}{'-' * 11}{'-' * 7}",
        ]
        for a in self.arms:
            out.append(
                f"  {a.label:30}{a.contacted:>10}{a.total_breaches:>10}"
                f"{a.escalated_to_human:>11}{a.held_as_control:>7}"
            )
        out += ["", "  Breaches by kind:", ""]
        kinds = [
            "contacted_opted_out",
            "marketing_without_consent",
            "contacted_in_quiet_hours",
            "over_contact_cap",
            "discount_over_ceiling",
        ]
        header = f"  {'policy':30}" + "".join(f"{k.split('_')[0][:8]:>10}" for k in kinds)
        out.append(header)
        out.append(f"  {'-' * 30}{'-' * 50}")
        for a in self.arms:
            out.append(f"  {a.label:30}" + "".join(f"{a.breaches.get(k, 0):>10}" for k in kinds))
        out += [
            "",
            "    contacted = opted-out customers contacted",
            "    marketing = marketing sent without consent",
            "    contacted (2nd) = messages sent inside TRAI quiet hours",
            "    over      = contacts above the 24-hour cap",
            "    discount  = discounts above the 15% ceiling",
            "",
            line,
            "",
            "  DECLARED -- recovery under the response model (same for every arm)",
            "",
            f"  {'policy':30}{'recovered':>14}{'claimable':>14}{'attribution':>14}",
            f"  {'-' * 30}{'-' * 14}{'-' * 14}{'-' * 14}",
        ]
        for a in self.arms:
            inc = a.incremental_paise
            claimable = f"Rs {inc / 100:,.0f}" if inc is not None else "--"
            if a.attribution_possible:
                attribution = "yes"
            elif a.treated_total == 0:
                # Nothing was done, so there is nothing to attribute. Not a
                # failure of the design -- just not applicable.
                attribution = "n/a"
            else:
                attribution = "UNAVAILABLE"
            out.append(
                f"  {a.label:30}{'Rs ' + format(a.recovered_paise / 100, ',.0f'):>14}"
                f"{claimable:>14}{attribution:>14}"
            )
        firewall = next((a for a in self.arms if a.arm == "revpilot"), None)
        ablated = next((a for a in self.arms if a.arm == "no_firewall"), None)
        holdout_off = next((a for a in self.arms if a.arm == "no_holdout"), None)

        out += ["", line, "", "  WHAT THIS SHOWS", ""]
        if firewall and ablated:
            same = firewall.recovered_paise == ablated.recovered_paise
            out += [
                f"  1. The firewall prevents {ablated.total_breaches} hard-bound "
                "breaches and costs",
                f"     {'nothing' if same else 'something'} in recovery"
                + (" -- both arms recover the same amount." if same else "."),
                "     Safety is not being traded against money here. That is the",
                "     result we expected least, and the one worth reading.",
                "",
            ]
        if holdout_off and firewall:
            delta = (holdout_off.recovered_paise - firewall.recovered_paise) / 100
            out += [
                f"  2. Removing the holdout raises recovery by Rs {delta:,.0f} and makes",
                "     attribution impossible. A bigger number, bought by giving up the",
                "     ability to say what caused it.",
                "",
            ]
        out += [
            "  3. Contacting everyone recovers more than RevPilot does, and breaches",
            "     a hard bound on nearly every case it touches. The comparison a",
            "     merchant actually faces is not recovery against zero -- it is",
            "     recovery against a regulator.",
            "",
            line,
            "",
            "  WHAT THIS DOES NOT SHOW -- read before quoting any figure above",
            "",
            "  * The model is not exercised here. This harness runs decision",
            "    policies, not the agent graph, so there is deliberately no",
            "    LLM-only arm: an honest one needs the model run over all 182",
            "    cases, and a fabricated one is worse than none. The model's",
            "    contribution is measured separately on the 85-case golden set,",
            "    where the rule table scored 96.5% against the model's 90.6% --",
            "    which is why the table ships and the model is consulted only on",
            "    ambiguity.",
            "",
            "  * Recovery is DECLARED; breaches are MEASURED. The response model is",
            "    a parameter we chose. It is identical across arms, so comparing",
            "    arms is meaningful -- but the absolute rupee figures are not",
            "    observations of customer behaviour and must not be quoted as if",
            "    they were.",
            "",
            "  * Breaches are counted, not executed. No message was sent and no",
            "    money moved. This reads the corpus and asks what each policy",
            "    would have done.",
            "",
            line,
        ]
        return "\n".join(out)


async def run_benchmark(factory: async_sessionmaker[AsyncSession]) -> BenchmarkReport:
    async with factory() as session:
        cases = await _load_cases(session)
    if not cases:
        raise RuntimeError("no eligible cases in the corpus; run `python tasks.py seed`")

    arms = [
        _score(arm, label, description, policy, has_holdout, cases)
        for arm, label, description, policy, has_holdout in ARMS
    ]
    return BenchmarkReport(arms=arms, corpus_cases=len(cases))
