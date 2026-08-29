"""The twelve stopping rules (workflow.md §8.1).

The track bar names *"stopping rules"* as a requirement in its own right, so
these are enumerable, individually named, and individually counted rather than
scattered through the agent as early returns.

Three design commitments:

**Pure.** Every rule is a function from a frozen context to a result. No I/O, no
clock read, no database. The caller assembles the context; that is what makes
termination testable by fast-forwarding a `FakeClock` instead of waiting.

**Evaluated in full, never short-circuited.** All twelve run even once one has
decided the outcome, because the dashboard reports firings *per rule* (§14.6)
and "S-05 fired 4 times today" is the evidence that the brakes work. They are
pure functions over in-memory data, so this costs nothing.

**Evaluated twice.** Once at TRIAGE, before a single token is spent, and again
at POLICY immediately before execution — state changes in between, and the
expensive mistake is discovering at execution time that the customer paid
organically ten seconds ago.

The outcome vocabulary matters as much as the rules. *Deferring* is not
*stopping*: a message held for quiet hours is sent at 09:05, not dropped. A
system that silently discarded held messages would look identical in the logs
and lose money quietly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from app.core.clock import IST, to_ist
from app.db.enums import CaseStatus, MessageClass, StoppingRule

__all__ = [
    "Decision",
    "PolicyLimits",
    "RuleResult",
    "StoppingContext",
    "StoppingVerdict",
    "evaluate",
    "in_quiet_hours",
    "next_quiet_hours_release",
]


class Decision(StrEnum):
    """What the engine concluded.

    Ordered by severity in :data:`_SEVERITY`; a STOP always beats a DEFER,
    which always beats a DEGRADE.
    """

    PROCEED = "PROCEED"
    #: Act, but with the action reduced — a discount stripped, or a marketing
    #: message downgraded to transactional. Not every brake is a full stop.
    DEGRADE = "DEGRADE"
    #: Hold and retry at ``defer_until``. Explicitly not a drop.
    DEFER = "DEFER"
    #: Terminal. The case is finished and will never transition again.
    STOP = "STOP"


_SEVERITY: Final[dict[Decision, int]] = {
    Decision.PROCEED: 0,
    Decision.DEGRADE: 1,
    Decision.DEFER: 2,
    Decision.STOP: 3,
}


@dataclass(frozen=True)
class PolicyLimits:
    """A merchant's bounds, detached from the ORM so rules stay pure."""

    max_attempts_per_case: int = 2
    max_discount_bearing_attempts: int = 1
    max_contacts_24h: int = 1
    max_contacts_48h: int = 2
    quiet_hours_start_ist: int = 21
    quiet_hours_end_ist: int = 9
    quiet_hours_release_minute: int = 5
    daily_action_budget: int = 50
    monthly_discount_exposure_paise: int = 20_000_000
    promise_freeze_h: int = 24


@dataclass(frozen=True)
class StoppingContext:
    """Everything the rules need, gathered once by the caller.

    Frozen on purpose: a rule that could mutate shared state would make the
    "evaluate all twelve" guarantee meaningless, since an earlier rule could
    change what a later one sees.
    """

    now_utc: datetime
    policy: PolicyLimits

    # --- case ---
    case_status: CaseStatus = CaseStatus.DETECTED
    attempt_no: int = 0
    discount_bearing_attempts: int = 0
    window_expires_at: datetime | None = None

    # --- external truth, re-read immediately before acting ---
    #: Razorpay's own view of the order. `paid` means we are too late, and
    #: being too late is a success, not a failure.
    order_status: str | None = None

    # --- consent ---
    opted_out: bool = False
    dnd_registered: bool = False
    marketing_consent: bool = False
    transactional_consent: bool = True

    # --- contact ledger (rolling windows, counted by the caller in SQL) ---
    contacts_24h: int = 0
    contacts_48h: int = 0
    last_contact_at: datetime | None = None

    # --- promise to pay ---
    promise_active: bool = False
    promised_at: datetime | None = None

    # --- merchant circuit breakers ---
    autopilot_enabled: bool = True
    actions_today: int = 0
    discount_exposure_mtd_paise: int = 0

    # --- the action being considered ---
    proposed_message_class: MessageClass = MessageClass.TRANSACTIONAL
    proposed_discount_pct: float = 0.0
    #: Some actions touch no customer at all (creating a human task, writing an
    #: audit block). Contact caps and quiet hours must not block those.
    is_outbound_contact: bool = True


@dataclass(frozen=True)
class RuleResult:
    rule: StoppingRule
    decision: Decision
    detail: str
    terminal_status: CaseStatus | None = None
    defer_until: datetime | None = None
    #: For DEGRADE: what to change about the proposed action.
    degrade: dict[str, object] = field(default_factory=dict)

    @property
    def fired(self) -> bool:
        return self.decision is not Decision.PROCEED


@dataclass(frozen=True)
class StoppingVerdict:
    decision: Decision
    results: tuple[RuleResult, ...]
    terminal_status: CaseStatus | None = None
    defer_until: datetime | None = None
    degradations: dict[str, object] = field(default_factory=dict)

    @property
    def fired(self) -> tuple[RuleResult, ...]:
        """Every rule that had something to say. Counted per rule on the dashboard."""
        return tuple(r for r in self.results if r.fired)

    @property
    def blocking_rule(self) -> StoppingRule | None:
        """The rule that decided the outcome, for the audit trail."""
        for result in self.results:
            if result.decision is self.decision and self.decision is not Decision.PROCEED:
                return result.rule
        return None

    @property
    def may_act(self) -> bool:
        return self.decision in (Decision.PROCEED, Decision.DEGRADE)


# ---------------------------------------------------------------------------
# Quiet hours
# ---------------------------------------------------------------------------
def in_quiet_hours(moment: datetime, *, start_ist: int, end_ist: int) -> bool:
    """Whether an instant falls inside the quiet window, in IST.

    The window normally wraps midnight (21:00 → 09:00), which is the case a
    naive ``start <= hour < end`` comparison gets silently wrong — it would
    report 23:00 as *outside* quiet hours and message someone at 11 PM.
    ``start == end`` means quiet hours are disabled.
    """
    if start_ist == end_ist:
        return False
    hour = to_ist(moment).hour
    if start_ist > end_ist:  # wraps midnight
        return hour >= start_ist or hour < end_ist
    return start_ist <= hour < end_ist


def next_quiet_hours_release(
    moment: datetime, *, start_ist: int, end_ist: int, release_minute: int = 5
) -> datetime:
    """The next instant at which sending is permitted.

    Returns ``moment`` unchanged when already allowed. Otherwise the next
    ``end_ist:release_minute`` in IST — today if we are in the small hours,
    tomorrow if it is the evening.
    """
    if not in_quiet_hours(moment, start_ist=start_ist, end_ist=end_ist):
        return moment

    local = to_ist(moment)
    release = local.replace(hour=end_ist, minute=release_minute, second=0, microsecond=0)
    if release <= local:
        release += timedelta(days=1)
    return release.astimezone(moment.tzinfo or IST)


# ---------------------------------------------------------------------------
# The twelve rules
# ---------------------------------------------------------------------------
def _proceed(rule: StoppingRule) -> RuleResult:
    return RuleResult(rule, Decision.PROCEED, "ok")


def s01_already_resolved(ctx: StoppingContext) -> RuleResult:
    """Paid without us. Being too late is a *success*, not a failure.

    Re-read immediately before acting: the expensive mistake is discovering at
    execution time that the customer paid ten seconds ago, and messaging them
    about a payment they already made.
    """
    rule = StoppingRule.S01_ALREADY_RESOLVED
    if (ctx.order_status or "").lower() in {"paid", "captured"}:
        return RuleResult(
            rule,
            Decision.STOP,
            f"order status is {ctx.order_status!r}; resolved without intervention",
            terminal_status=CaseStatus.RESOLVED_ORGANIC,
        )
    if ctx.case_status in {CaseStatus.RECOVERED, CaseStatus.RESOLVED_ORGANIC}:
        return RuleResult(
            rule,
            Decision.STOP,
            f"case already terminal ({ctx.case_status.value})",
            terminal_status=ctx.case_status,
        )
    return _proceed(rule)


def s02_attempt_budget(ctx: StoppingContext) -> RuleResult:
    """A hard ceiling on attempts. Half of the termination guarantee."""
    rule = StoppingRule.S02_ATTEMPT_BUDGET
    limit = ctx.policy.max_attempts_per_case
    if ctx.attempt_no >= limit:
        return RuleResult(
            rule,
            Decision.STOP,
            f"attempt budget exhausted ({ctx.attempt_no}/{limit})",
            terminal_status=CaseStatus.EXPIRED,
        )
    return _proceed(rule)


def s03_discount_attempt_budget(ctx: StoppingContext) -> RuleResult:
    """Degrade, not stop. One discount-bearing attempt per case.

    A second discount is margin spent on a customer already shown one, so the
    discount is stripped and the recovery still goes out at 0%.
    """
    rule = StoppingRule.S03_DISCOUNT_ATTEMPT_BUDGET
    if ctx.proposed_discount_pct <= 0:
        return _proceed(rule)
    limit = ctx.policy.max_discount_bearing_attempts
    if ctx.discount_bearing_attempts >= limit:
        return RuleResult(
            rule,
            Decision.DEGRADE,
            f"discount budget exhausted ({ctx.discount_bearing_attempts}/{limit}); "
            "proceeding at 0%",
            degrade={"discount_pct": 0.0},
        )
    return _proceed(rule)


def s04_contact_cap_24h(ctx: StoppingContext) -> RuleResult:
    """At most one contact per 24 hours. Defers rather than stopping."""
    rule = StoppingRule.S04_CONTACT_CAP_24H
    if not ctx.is_outbound_contact:
        return _proceed(rule)
    if ctx.contacts_24h < ctx.policy.max_contacts_24h:
        return _proceed(rule)

    if ctx.last_contact_at is None:
        # Count says we contacted them but we cannot say when. Stopping is the
        # safe reading: a cap we cannot schedule around is a cap.
        return RuleResult(
            rule,
            Decision.STOP,
            "24h contact cap reached and no last-contact timestamp to defer against",
            terminal_status=CaseStatus.SUPPRESSED,
        )
    release = ctx.last_contact_at + timedelta(hours=24)
    if release <= ctx.now_utc:
        # The counter and the timestamp disagree: the count says we contacted
        # them inside the window, but the timestamp has already aged out of it.
        # In production this is a race -- the count and the timestamp are two
        # queries -- or clock skew. Trust the timestamp: deferring to a moment
        # that has already passed would re-fire immediately and spin forever.
        # Found by test_advancing_the_clock_always_reaches_a_terminal_state
        # (INC-005).
        return RuleResult(
            rule,
            Decision.PROCEED,
            f"24h count is {ctx.contacts_24h} but the last contact "
            f"({ctx.last_contact_at.isoformat()}) has aged out of the window",
        )
    return RuleResult(
        rule,
        Decision.DEFER,
        f"24h contact cap reached ({ctx.contacts_24h}/{ctx.policy.max_contacts_24h})",
        defer_until=release,
    )


def s05_contact_cap_48h(ctx: StoppingContext) -> RuleResult:
    """At most two contacts per 48 hours. A hard stop, not a deferral.

    Deferring here would mean planning a third message, which is the thing the
    cap exists to prevent.
    """
    rule = StoppingRule.S05_CONTACT_CAP_48H
    if not ctx.is_outbound_contact:
        return _proceed(rule)
    if ctx.contacts_48h >= ctx.policy.max_contacts_48h:
        return RuleResult(
            rule,
            Decision.STOP,
            f"48h contact cap reached ({ctx.contacts_48h}/{ctx.policy.max_contacts_48h})",
            terminal_status=CaseStatus.SUPPRESSED,
        )
    return _proceed(rule)


def s06_recovery_window(ctx: StoppingContext) -> RuleResult:
    """The wall-clock deadline. The other half of the termination guarantee."""
    rule = StoppingRule.S06_RECOVERY_WINDOW
    if ctx.window_expires_at is not None and ctx.now_utc >= ctx.window_expires_at:
        return RuleResult(
            rule,
            Decision.STOP,
            f"recovery window closed at {ctx.window_expires_at.isoformat()}",
            terminal_status=CaseStatus.EXPIRED,
        )
    return _proceed(rule)


def s07_opt_out(ctx: StoppingContext) -> RuleResult:
    """Someone said stop. Permanent, across every case, forever.

    Nothing overrides this — not a high-value cart, not a first contact, not a
    merchant override.
    """
    rule = StoppingRule.S07_OPT_OUT
    if ctx.opted_out and ctx.is_outbound_contact:
        return RuleResult(
            rule,
            Decision.STOP,
            "customer has opted out; no further contact on any case",
            terminal_status=CaseStatus.SUPPRESSED,
        )
    return _proceed(rule)


def s08_consent_class(ctx: StoppingContext) -> RuleResult:
    """Marketing needs opt-in; transactional does not (§9.2).

    A discount offer is a marketing message. Because marketing consent is often
    absent, the agent's default action ends up being the zero-discount
    transactional link — the compliance constraint and the margin constraint
    point the same way, which is why Ananya is recovered without a discount.
    """
    rule = StoppingRule.S08_CONSENT_CLASS
    if not ctx.is_outbound_contact:
        return _proceed(rule)

    if ctx.proposed_message_class is MessageClass.MARKETING and (
        not ctx.marketing_consent or ctx.dnd_registered
    ):
        reason = "no marketing consent" if not ctx.marketing_consent else "DND-registered"
        if ctx.transactional_consent:
            return RuleResult(
                rule,
                Decision.DEGRADE,
                f"{reason}; downgrading to a transactional message at 0% discount",
                degrade={
                    "message_class": MessageClass.TRANSACTIONAL,
                    "discount_pct": 0.0,
                },
            )
        return RuleResult(
            rule,
            Decision.STOP,
            f"{reason} and no transactional consent either",
            terminal_status=CaseStatus.SUPPRESSED,
        )

    if not ctx.transactional_consent:
        return RuleResult(
            rule,
            Decision.STOP,
            "no transactional consent on record",
            terminal_status=CaseStatus.SUPPRESSED,
        )
    return _proceed(rule)


def s09_quiet_hours(ctx: StoppingContext) -> RuleResult:
    """21:00-09:00 IST. Queued to 09:05, **never dropped**.

    A system that silently discarded held messages would look identical in the
    logs and lose money quietly.
    """
    rule = StoppingRule.S09_QUIET_HOURS
    if not ctx.is_outbound_contact:
        return _proceed(rule)
    if not in_quiet_hours(
        ctx.now_utc,
        start_ist=ctx.policy.quiet_hours_start_ist,
        end_ist=ctx.policy.quiet_hours_end_ist,
    ):
        return _proceed(rule)

    release = next_quiet_hours_release(
        ctx.now_utc,
        start_ist=ctx.policy.quiet_hours_start_ist,
        end_ist=ctx.policy.quiet_hours_end_ist,
        release_minute=ctx.policy.quiet_hours_release_minute,
    )
    return RuleResult(
        rule,
        Decision.DEFER,
        f"quiet hours in IST; holding until {to_ist(release).strftime('%H:%M %Z')}",
        defer_until=release,
    )


def s10_promise_freeze(ctx: StoppingContext) -> RuleResult:
    """Someone said "Friday". Stop chasing them until Friday has passed.

    An agent that keeps chasing a customer who already committed is worse than
    no agent — it damages the relationship the invoice depends on.
    """
    rule = StoppingRule.S10_PROMISE_FREEZE
    if not (ctx.promise_active and ctx.promised_at):
        return _proceed(rule)

    thaw = ctx.promised_at + timedelta(hours=ctx.policy.promise_freeze_h)
    if ctx.now_utc < thaw:
        return RuleResult(
            rule,
            Decision.DEFER,
            f"promise to pay by {ctx.promised_at.isoformat()}; frozen until {thaw.isoformat()}",
            defer_until=thaw,
        )
    return _proceed(rule)


def s11_merchant_budget(ctx: StoppingContext) -> RuleResult:
    """Per-merchant circuit breakers.

    Bounds the blast radius of a bad deploy: a bug that tried to message every
    customer stops at the daily action budget instead of at the customer list.
    """
    rule = StoppingRule.S11_MERCHANT_BUDGET
    if ctx.actions_today >= ctx.policy.daily_action_budget:
        return RuleResult(
            rule,
            Decision.STOP,
            f"merchant daily action budget exhausted "
            f"({ctx.actions_today}/{ctx.policy.daily_action_budget})",
            terminal_status=CaseStatus.SUPPRESSED,
        )
    if ctx.discount_exposure_mtd_paise >= ctx.policy.monthly_discount_exposure_paise:
        return RuleResult(
            rule,
            Decision.STOP,
            f"monthly discount exposure exhausted (₹{ctx.discount_exposure_mtd_paise / 100:,.0f})",
            terminal_status=CaseStatus.SUPPRESSED,
        )
    return _proceed(rule)


def s12_kill_switch(ctx: StoppingContext) -> RuleResult:
    """One toggle, effective on the next evaluation.

    Evaluated first so that turning autopilot off cannot be outvoted by any
    other consideration.
    """
    rule = StoppingRule.S12_KILL_SWITCH
    if not ctx.autopilot_enabled:
        return RuleResult(
            rule,
            Decision.STOP,
            "autopilot is disabled for this merchant",
            terminal_status=CaseStatus.SUPPRESSED,
        )
    return _proceed(rule)


#: Evaluation order. Not alphabetical and not arbitrary: the most authoritative
#: and cheapest checks come first, so that when several rules fire the one that
#: gets recorded as `blocking_rule` is the one a merchant would name as the
#: reason. Kill switch outranks everything; "already paid" outranks every
#: reason to act; opt-out outranks every reason to contact.
RULES: Final[tuple[Callable[[StoppingContext], RuleResult], ...]] = (
    s12_kill_switch,
    s01_already_resolved,
    s07_opt_out,
    s06_recovery_window,
    s02_attempt_budget,
    s11_merchant_budget,
    s05_contact_cap_48h,
    s08_consent_class,
    s10_promise_freeze,
    s04_contact_cap_24h,
    s09_quiet_hours,
    s03_discount_attempt_budget,
)


def evaluate(ctx: StoppingContext) -> StoppingVerdict:
    """Run all twelve rules and combine them.

    STOP beats DEFER beats DEGRADE beats PROCEED. Degradations from *every*
    firing rule are merged, because stripping a discount and downgrading a
    message class are independent reductions that can both apply.

    A deferral past the recovery window becomes a stop: holding a message until
    after the case has expired is not a deferral, it is a drop with extra steps.
    """
    results = tuple(rule(ctx) for rule in RULES)

    decision = Decision.PROCEED
    terminal: CaseStatus | None = None
    defer_until: datetime | None = None
    degradations: dict[str, object] = {}

    for result in results:
        if result.decision is Decision.DEGRADE:
            degradations.update(result.degrade)
        if _SEVERITY[result.decision] > _SEVERITY[decision]:
            decision = result.decision
            terminal = result.terminal_status
            defer_until = result.defer_until
        elif (
            # Several holds at once -- honour the latest, or we would send
            # while another cap is still in force.
            result.decision is decision
            and decision is Decision.DEFER
            and result.defer_until
            and defer_until
            and result.defer_until > defer_until
        ):
            defer_until = result.defer_until

    # A deferral must move the clock forward. If it does not, the next
    # evaluation re-fires the same rule at the same instant and the case spins.
    # Belt-and-braces on top of each rule's own check, so the guarantee is
    # structural rather than dependent on twelve authors getting it right
    # (INC-005).
    if decision is Decision.DEFER and defer_until is not None and defer_until <= ctx.now_utc:
        defer_until = None
        decision = Decision.DEGRADE if degradations else Decision.PROCEED

    if (
        decision is Decision.DEFER
        and defer_until is not None
        and ctx.window_expires_at is not None
        and defer_until >= ctx.window_expires_at
    ):
        return StoppingVerdict(
            decision=Decision.STOP,
            results=(
                *results,
                RuleResult(
                    StoppingRule.S06_RECOVERY_WINDOW,
                    Decision.STOP,
                    f"deferral to {defer_until.isoformat()} would fall outside the "
                    f"recovery window closing {ctx.window_expires_at.isoformat()}",
                    terminal_status=CaseStatus.EXPIRED,
                ),
            ),
            terminal_status=CaseStatus.EXPIRED,
            degradations=degradations,
        )

    return StoppingVerdict(
        decision=decision,
        results=results,
        terminal_status=terminal,
        defer_until=defer_until,
        degradations=degradations,
    )


def apply_degradations(ctx: StoppingContext, verdict: StoppingVerdict) -> StoppingContext:
    """Return a context with the verdict's reductions applied.

    Used to re-evaluate after degrading: stripping a discount can itself change
    what other rules say, and the second pass must see the reduced action
    rather than the original proposal.

    Fields are unpacked one at a time rather than splatted from a dict, so the
    type checker verifies each assignment. A ``**dict[str, object]`` would
    typecheck as anything and silently accept a wrong-typed degradation.
    """
    discount = verdict.degradations.get("discount_pct")
    message_class = verdict.degradations.get("message_class")
    if discount is None and message_class is None:
        return ctx
    return replace(
        ctx,
        proposed_discount_pct=(
            float(discount) if isinstance(discount, (int, float)) else ctx.proposed_discount_pct
        ),
        proposed_message_class=(
            message_class if isinstance(message_class, MessageClass) else ctx.proposed_message_class
        ),
    )
