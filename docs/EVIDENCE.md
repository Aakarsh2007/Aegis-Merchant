# Evidence snapshot

**Generated, not written.** Every figure this submission quotes comes from here,
and this file comes from one run of the system. Regenerate with
`python tasks.py snapshot`.

A reviewer found the README and the demo script quoting different test counts.
They were right, and the cause was that every document held its own copy of every
number. Documents now cite this snapshot; this snapshot cites the system.

| | |
|---|---|
| Snapshot | `20260901-1158-24e43ad` |
| Generated | 2026-09-01 11:58 UTC |
| Commit | `24e43ad` on `main` |
| Corpus seed | `20260905` |
| Tests collected | 1,192 |
| Incidents | 39 |
| Decisions | 45 |

## The three numbers, and the question that is open

| Figure | Value | Provenance |
|---|---|---|
| Razorpay verified | Rs 2.00 | `RAZORPAY_VERIFIED` |
| Gross recovered | Rs 2,02,759.95 | `SIMULATED` |
| Net incremental | Rs 60,216.66 | `SIMULATED` |
| At risk | Rs 6,64,067.23 | `SIMULATED` |

**Did RevPilot cause additional customers to pay?** Not proven, and not provable
at this sample size. The design that would settle it is pre-registered in
`docs/PRE-REGISTRATION.md`, committed before any of this data existed.

## Attribution

| Arm | Cases | Paid | Rate | 95% CI |
|---|---|---|---|---|
| Treated | 171 | 50 | 29.2% | 22.9% to 36.4% |
| Control | 39 | 9 | 23.1% | 12.7% to 38.3% |

Absolute lift **6.16%**. Statistically significant:
**False**. The intervals overlap, so it is directional.

## Razorpay-verified recoveries

| Case | Amount | Verified by | Mechanism |
|---|---|---|---|
| `RC-TM88163` | Rs 1.00 | `plink_TWPwcbsfrYnIQQ` | API_RECONCILIATION |
| `RC-TM96648` | Rs 1.00 | `TWSSP5BW90Y89E` | WEBHOOK |

A fresh clone shows Rs 0.00 here, and that is correct: nothing has been proven on
*your* machine. Click **Prove it against real Razorpay** on the dashboard, or run
`python tasks.py testmode-recover`, and make your own.

## Where the AI is, and where it is not

- Inferences recorded: **398**
- Served from the committed cache: **55.3%**
- By source: `{'CACHED': 220, 'DETERMINISTIC': 178}`
- Actual spend **Rs 0.00**, projected at published paid
  rates **Rs 1.28**

The rule table scored **96.5%** on the 85-case golden set against the model's
**90.6%**. So the rule table ships and the model is consulted only where the
classifier declares itself unsure -- which is the whole of our AI judgment claim,
and it is a measurement rather than a preference. See DEC-017.

## Restraint

**33** unsafe proposals intercepted. Rules that fired:

- `S-07` fired 11 times
- `S-09` fired 22 times

All twelve rules are listed on the dashboard including the ones that fired zero
times, because a brake that did not fire and a brake that does not exist look
identical if you only show the non-zero rows.

## Does the architecture earn its complexity?

The same 182 cases through 5 decision
policies. **Contacts, breaches and escalations are measured. Recovery is
declared.**

| Policy | Contacted | Breaches | Escalated | Recovered | Claimable | Attribution |
|---|---|---|---|---|---|---|
| No intervention | 0 | **0** | 0 | Rs 0 | -- | n/a |
| Contact everyone | 145 | **308** | 0 | Rs 240,114 | Rs 31,006 | yes |
| RevPilot | 138 | **0** | 9 | Rs 222,359 | Rs 56,491 | yes |
| RevPilot, firewall removed | 138 | **284** | 0 | Rs 222,359 | Rs 56,491 | yes |
| RevPilot, holdout removed | 171 | **0** | 13 | Rs 286,013 | -- | **UNAVAILABLE** |

> Contacts, breaches, escalations and holdout sizes are real counts over the corpus's own consent and contact data. No simulation is involved in those columns.

> Recovery amounts are DECLARED, not measured: they use the same response model as the batch (baseline self-recovery 21%, treated uplift 7-14% by playbook). Every arm gets the identical model, so the comparison is meaningful while the absolute figures are not observations of customer behaviour.

Two findings worth reading twice:

1. **The firewall prevents every one of those breaches and costs nothing in
   recovery.** Safety is normally a trade-off; here the clamps change *how* an
   action is taken rather than *whether*, so both arms recover the same amount.
2. **Removing the holdout recovers the most of any policy and can claim none of
   it.** A bigger number bought by giving up the ability to say what caused it.

Full table with breaches by kind, and the limitations, from
`python tasks.py benchmark`.

---

*Snapshot `20260901-1158-24e43ad`, commit `24e43ad`, 2026-09-01 11:58 UTC.*
*Any figure elsewhere in this repository that disagrees with this file is stale.*
