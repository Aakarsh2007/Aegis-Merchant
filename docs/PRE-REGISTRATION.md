# Pre-registration · RevPilot AI incremental recovery experiment

**Status: registered, not yet run against real merchant traffic.**

**Committed 2026-08-31, before any live-traffic data exists.** That timing is the point of this
document, and it is checkable rather than claimed: `git log --follow docs/PRE-REGISTRATION.md`
gives the commit date, and this file's SHA-256 is written into the audit ledger by
`python tasks.py verify-audit`. A plan written after seeing results is not a plan, so the
ordering is made auditable rather than asserted.

Nothing in this file will be edited once real data exists. If the design turns out to be wrong,
the amendment goes in a **new** dated section at the bottom, with the reason, and the original
text stays where it is.

---

## 1. Why this document exists

The dashboard reports three numbers, and they answer three different questions:

| Figure | Question it answers | Status |
|---|---|---|
| ₹1.00 `RAZORPAY_VERIFIED` | Can the system execute and verify a recovery through Razorpay? | **Answered: yes** |
| ₹60,217 `SIMULATED` | What might it recover at scale? | **Not proven — a simulation cannot establish causation** |
| — | **Did RevPilot cause additional customers to pay?** | **Not proven. This document is the plan that would settle it.** |

The third question is the one that matters commercially, and it is the one we have not
answered. Writing down in advance exactly what would answer it — including what result would
make us abandon the hypothesis — is the most honest thing available to someone who does not yet
have the data.

---

## 2. Hypothesis

**H₁.** Among Razorpay payment failures, checkout abandonments, overdue invoices and failed
subscription mandates, cases that receive a RevPilot recovery action convert to payment at a
higher rate, within the recovery window, than cases that receive nothing.

**H₀.** No difference in conversion rate between arms.

Directional, one hypothesis, one primary endpoint. Everything else in §7 is secondary and is
labelled as such, because a study with nine primary endpoints has none.

---

## 3. Primary endpoint

**Conversion rate within the recovery window**, defined as: a case whose payment is confirmed
by a signed Razorpay webhook (`payment.captured`, `payment_link.paid`, `invoice.paid` or
`subscription.charged`) with an `event_id` we have not seen before, arriving before
`window_expires_at`.

Deliberate choices, each of which could be gamed the other way:

- **Provider-confirmed only.** Our own belief that we recovered something does not count. Only
  a signed webhook from Razorpay does. This is the same bar the `RAZORPAY_VERIFIED` badge uses.
- **Within the window, not ever.** A payment 40 days later is not attributable to a nudge sent
  on day one, and letting the window run indefinitely would inflate the treated arm because
  treated cases are the ones we are watching.
- **The case is the unit, not the customer and not the rupee.** Randomisation happens at the
  case, so analysis happens at the case. Analysing at a different level than you randomised is
  the most common way an A/B result turns out to be nothing.

**Secondary endpoint, pre-specified:** incremental revenue in paise, computed as
`(conv_T − conv_C) × n_T × mean_amount_T`, net of discount cost and projected inference cost.
Secondary because it inherits all the variance of the primary endpoint plus the variance of the
amount distribution, and a revenue figure will reach significance later than a rate.

---

## 4. Randomisation

`services/experiments.assign_arm`, unchanged: `SHA-256(experiment_key : case_id)`, first 8 bytes
as a big-endian integer scaled into `[0, 1)`, compared against `control_fraction`.

- **Deterministic and recomputable.** The assignment hash is stored on the row, so an auditor
  can recompute the arm from the case id and confirm it was not chosen after the outcome was
  known. This is the single most important property here: post-hoc arm assignment is the
  easiest way to fabricate a lift, and it is the hardest to detect from summary statistics.
- **Assigned at case creation**, before diagnosis, before any action, and before anything is
  known about whether the case looks likely to recover.
- **No re-randomisation, ever.** A case assigned to control stays in control even if it looks
  valuable, and especially then.

**Allocation: 50/50** (`control_fraction = 0.5`) for the experiment, and this is a change from
the 19% holdout the demo runs at. The reason is arithmetic, and it is stated here rather than
discovered later:

| Split | Control needed | Treated needed | Total failed payments |
|---|---|---|---|
| 81/19 (current demo) | 465 | 2,039 | **2,504** |
| 50/50 (this experiment) | 796 | 796 | **1,592** |

A balanced split reaches the same power with **36.4% fewer cases**, because power is governed by
the smaller arm. The cost is real and falls on the merchant: half of all recoverable cases are
deliberately not acted on for the duration. That trade — a shorter experiment against more
foregone recovery — is the merchant's to make, so `control_fraction` stays configurable and the
figure used is recorded per experiment rather than hard-coded.

---

## 5. Sample size and power

Two-proportion z-test, α = 0.05 two-sided, power = 0.80.

Effect size assumed: **control 23.1%, treated 29.2%, absolute lift 6.16 pp.** These are the
rates the simulation produces, and using them here is a declared assumption, not evidence. If
the true effect is smaller, this study is underpowered and §6 says what happens then.

Required, at 50/50: **796 per arm, 1,592 cases total.**

1,382 cases short of that. At an 8–20% payment failure rate the shortfall is 6,910–17,275
payment attempts. Reproduce the number:

```bash
python tasks.py power
```

The figures above are **computed by that command, not typed here** — `tests/test_power.py::TestAgreementWithThePreRegistration` reads this file and asserts the
published numbers match `core/power.py`. The first draft of this document said 795 per arm; the
code said 796, because a sample size must round *up* and the draft had rounded to nearest. The
test caught it. A pre-registration whose arithmetic nobody checks is a wish.

**Current position: 39 control cases, 4.9% of the control arm a 50/50 design needs.** The
dashboard shows this as a completion percentage and a projected date at the observed daily
volume, so the gap is visible rather than described.

---

## 6. Stopping rule, and what would falsify the hypothesis

Fixed sample size. **Analysis happens once, at n = 1,592.** No peeking, and this matters more
than it sounds: repeatedly testing a growing sample and stopping at the first p < 0.05 produces
a "significant" result from pure noise roughly one time in three.

The dashboard will show arm sizes and the completion percentage while the experiment runs. It
will **not** show a p-value or a significance verdict before the sample is complete, because a
number on a screen is an invitation to stop when it looks good.

**We abandon H₁ if**, at n = 1,592, the 95% confidence interval on the absolute lift contains
zero. Not "we gather more data" — that is the same peeking problem with extra steps. The
outcome is written up in `docs/INCIDENTS.md` and the recovery figures on the dashboard revert
to gross with an explicit statement that no incremental effect was demonstrated.

**We would also abandon it, or amend, if any of these occur** — pre-committed, so they cannot
become post-hoc explanations for a null result:

- Control conversion exceeds treated conversion by more than 2 pp at any interim arm size above
  200 per arm. That is a signal our outreach is actively harmful, and the kill switch (S-12)
  goes on immediately regardless of the analysis schedule. Harm stops early; benefit does not.
- Opt-out rate in the treated arm exceeds 5%. A lift bought with unsubscribes is not a lift.
- More than 10% of treated cases fail to deliver (S-01 through S-12 firing is *not* a failure —
  a blocked action is the system working; this means outbox failures and provider errors).

---

## 7. Analysis, specified in advance

- **Test:** two-proportion z-test on the primary endpoint. Wilson score intervals on each arm's
  rate, reported alongside, because Wilson does not misbehave at small n or rates near zero the
  way the normal approximation does.
- **Population:** every case assigned to an arm, analysed in the arm it was assigned to
  (intention-to-treat). A case where a stopping rule blocked the action **stays in the treated
  arm**. Excluding it would remove exactly the cases the policy firewall protected and inflate
  the treated rate — this is the INC-018 failure mode, and it produced a 66-point swing in the
  lift when we got it wrong internally.
- **Exclusions, pre-specified and exhaustive:** demo-seeded cases (`is_demo = 1`) and cases
  whose window had already expired at assignment. Nothing else. No outlier removal, no
  post-hoc segment filtering.
- **No subgroup analysis is primary.** Per-playbook rates will be reported as descriptive, with
  no significance claim, because four playbooks tested at α = 0.05 gives a 19% chance of one
  false positive.
- **Multiple comparisons:** one primary endpoint, one test. Nothing to correct.

---

## 8. What this experiment cannot show, even if it succeeds

Written here so it cannot be quietly dropped from the write-up:

- **One merchant is one merchant.** A lift measured on a single catalogue, price point and
  customer base does not generalise to Razorpay's merchant base, and will not be described as
  if it does.
- **Novelty effects are not separated.** A first-ever recovery message may outperform the
  hundredth. Distinguishing them needs a longer run than this design.
- **The channel and the agent are confounded.** This tests "RevPilot's action vs nothing", not
  "RevPilot's choice of action vs a naive reminder to everyone". Establishing that the
  *intelligence* adds value over a blanket nudge needs a third arm, and is the honest next
  study rather than something to imply from this one.

---

## 9. Ethics and legal preconditions

None of the following is optional, and the experiment does not start until all four hold:

1. **Merchant consent**, in writing, covering contact with their customers and a data
   processing agreement for the PII involved.
2. **DLT/TRAI registration.** Commercial SMS and WhatsApp to Indian numbers requires a
   registered principal entity with approved templates, and that entity is the **merchant**,
   not us. Lead time is weeks; this is the binding external constraint on when any real
   experiment can begin.
3. **DND and opt-out honoured absolutely.** S-07 is permanent and checked before every other
   rule. Quiet hours (21:00–09:00 IST) hold messages rather than dropping them.
4. **The control arm is never contacted.** Not a delayed message, not a different message. The
   merchant's ordinary checkout stays open to them, which is exactly the counterfactual the
   design needs.

---

## 10. Interim: what has actually been exercised

Real, and small, and labelled as such — this is an apparatus test, not a behavioural
measurement:

`python tasks.py testmode-experiment` runs a genuinely randomised holdout against **real
Razorpay Test Mode**. Both arms get real cases. The treated arm gets real payment links created
through the live API. The control arm gets none and is never contacted. Real payments produce
real signed webhooks, and attribution computes arm-level rates from `RAZORPAY_VERIFIED` events
only.

**What that proves:** the randomisation, the arm-level attribution, the webhook verification and
the incremental-lift arithmetic all work end-to-end on real provider data rather than only on a
seeded corpus.

**What it does not prove, and the dashboard says so on the tile:** anything about customer
behaviour. The payments are made by the developer, so the "customer decisions" are not
independent observations of anything. n is far below §5 and the interval is uselessly wide. It
is a test of the instrument, and reporting it as a test of the effect would be precisely the
error this document exists to prevent.

---

*Registered 2026-08-31. Unamended.*
