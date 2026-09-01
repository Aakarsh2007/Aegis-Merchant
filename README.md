# RevPilot AI

### Recover revenue. Prove causality. Stop safely.

**Most recovery tools can tell you what they recovered. This one can tell you whether the
recovery belongs to it.**

Razorpay AI Buildathon 2026 · Track: **AI Revenue Recovery**

[![CI](https://github.com/Aakarsh2007/Aegis-Merchant/actions/workflows/ci.yml/badge.svg)](https://github.com/Aakarsh2007/Aegis-Merchant/actions/workflows/ci.yml)

```bash
python tasks.py demo        # no credentials, no Docker, ~40 seconds
```

---

## The problem

Revenue leaks in four places — failed payments, abandoned checkouts, overdue invoices, dead
subscription mandates. Every tool that chases it back reports the same misleading number: **gross
money recovered.** Some of those customers would have paid anyway. Billing for them is charging for
the weather.

## Four numbers, four different questions

| | | Question it answers |
|---|---|---|
| **₹2.00** | `RAZORPAY VERIFIED` | *Can it execute and verify a recovery through Razorpay?* **Yes, both ways** — two real Test Mode payments, one proven by a signed webhook, one by API reconciliation after a webhook was lost to a dead tunnel. |
| **₹60,217** | `SIMULATED` | *What might it recover at scale?* Estimated incremental lift over a 39-case holdout, under a **declared** response model. |
| **₹1,39,021** | `NOT CLAIMED` | *Money that arrived and we credited ourselves nothing.* 17 cases. A gross-recovery dashboard would have counted all of it. |
| **—** | `NOT PROVEN` | *Did it cause additional customers to pay?* **No.** That needs 1,592 cases and a DLT-registered merchant. [Pre-registered](docs/PRE-REGISTRATION.md) before any data existed; the dashboard shows we are at **4.9%**. |

The third row is the one to look at twice. Every figure here is generated into
[`docs/EVIDENCE.md`](docs/EVIDENCE.md) from one run — commit, timestamp, seed. **Anything in this
repository that disagrees with that file is stale.**

## The loop

```
Detect → Diagnose → Propose → Policy → Execute → Verify → Attribute
```

Razorpay's own failure telemetry in; a bounded action out; a signed webhook back; and then the part
most systems skip — deciding whether the money is ours to claim.

> ### AI proposes. Policy disposes. Evidence decides.

## Why AI, and why not

The rule table settles **159 of 199** diagnoses. The model gets the other 40 — the cases where
Razorpay sent no error fields at all and there is genuinely nothing to look up.

That split is a measurement, not a preference. On an 85-case golden set the rule table scored
**96.5%** against the model's **90.6%**. §15.1 committed in advance to shipping the rule table if
the model lost. It lost.

> **Facts, safety, compliance and money → deterministic code. Ambiguity and strategy → the model.**

## Why the model cannot touch money

Not a prompt instruction. A structural one.

```
        AI  ──  diagnosis · strategy proposal · rationale
                            │
                            ▼   UNTRUSTED / ADVISORY
        ╔═══════════════════════════════════════╗
        ║          POLICY FIREWALL              ║
        ║  consent · amount · discount · caps   ║
        ║  DND · quiet hours · 12 stopping rules║
        ╚═══════════════════╤═══════════════════╝
                            ▼   TRUSTED / DETERMINISTIC
                   CAPABILITY TOKEN
                            ▼
                       EXECUTION  ──▶  RAZORPAY
```

**The execution layer does not trust the model. It trusts a token minted by policy.** Ask the agent
to charge more than a customer owes and the answer is not *blocked* — it is `UNREPRESENTABLE`. The
proposal object has no amount field. We did not teach the model not to do it; we made the action
impossible to express.

Run the five attacks yourself from the dashboard, or `POST /api/v1/adversarial/run`.

## Does the architecture earn its complexity?

```bash
python tasks.py benchmark
```

The same 182 cases through five decision policies. **Contacts and breaches are measured — real
counts over the corpus's own consent data. Recovery is declared, identical across arms.**

| Policy | Contacted | Breaches | Recovered | Claimable | Attribution |
|---|---|---|---|---|---|
| No intervention | 0 | **0** | ₹0 | — | n/a |
| Contact everyone | 145 | **308** | ₹2,40,114 | ₹31,006 | yes |
| RevPilot | 138 | **0** | ₹2,22,359 | ₹56,491 | yes |
| RevPilot, firewall removed | 138 | **284** | ₹2,22,359 | ₹56,491 | yes |
| RevPilot, holdout removed | 171 | **0** | ₹2,86,013 | — | **UNAVAILABLE** |

Two findings:

1. **In this corpus the firewall introduced no recovery cost.** Both arms recover the identical
   amount and one of them breaches a hard bound 284 times — the clamps change *how* an action is
   taken, not *whether*. Safety did not trade off against recovery here.
2. **Removing the holdout recovers the most of any policy and can claim none of it.** A bigger
   number, bought by giving up the ability to say what caused it.

The command prints what the table does *not* show, including why there is deliberately no LLM-only
arm.

## Why this architecture exists

Positioning, not a benchmark claim — we have not run anyone else's system and will not invent
numbers for one.

| Capability | Here | Measured how |
|---|---|---|
| Failure diagnosis from provider telemetry | ✓ | 96.5% on an 85-case golden set |
| Model consulted only where rules are unsure | ✓ | 40 of 199; the model *lost* at 90.6% |
| Deterministic policy authority over money | ✓ | Ablation: removing it → **284** breaches |
| Capability token — no token, no side effect | ✓ | AST test: execution never reads the proposal |
| Randomised holdout | **✓** | Ablation: removing it → attribution **impossible** |
| Incremental attribution, not gross | **✓** | ₹60,217 claimable against ₹2,02,760 gross |
| Refuses credit it cannot prove | **✓** | ₹1,39,021 arrived, ₹0 claimed |
| Signed provider evidence | ✓ | Two real Test Mode recoveries |
| Honest non-significance | **✓** | The dashboard says the lift is not significant |
| AI authority over money | **None** | The proposal object has no amount field |

## A real Razorpay recovery

Not a fixture. Two payments, and both verification paths exercised:

```
RC-TM96648   ₹1.00   WEBHOOK              event TWSSP5BW90Y89E  from 52.66.75.174
RC-TM88163   ₹1.00   API_RECONCILIATION   plink_TWPwcbsfrYnIQQ
```

The second exists because a tunnel died mid-test and Razorpay's delivery failed. The reconciler
asked Razorpay directly and recovered the truth. **A lost webhook cost nothing** — which is what
[DEC-037](docs/DECISIONS.md) was written to guarantee and had never been asked to prove.

Make your own: click **Prove it against real Razorpay** on the dashboard, or

```bash
python tasks.py testmode-recover     # returns a real Razorpay link
#   ... pay it: card 4111 1111 1111 1111, any future expiry, any CVV
python tasks.py reconcile            # asks Razorpay what was actually paid
```

A fresh clone shows **₹0.00** verified, and that is correct — nothing has been proven on *your*
machine yet.

## Why we claim what we claim

Six conditions, ANDed in [`attribution.py`](apps/api/app/services/attribution.py). Failing any one
sends the payment to the *not claimed* column:

1. Signed by Razorpay — HMAC verified before storage
2. An event type that settles a payment
3. Carries a reference **we issued**, committed to the outbox *before* the provider call
4. We actually acted — a control-arm case that pays is the counterfactual, not a recovery
5. Paid inside the recovery window
6. Counted exactly once — `UNIQUE(event_id)`

Visible per case on the dashboard, and at `GET /api/v1/metrics/claims`.

## Safety, and the brakes you can watch fail

Twelve named stopping rules, **all twelve listed on the dashboard including the ten that fired
zero times** — a brake that did not fire and a brake that does not exist look identical if you only
show the non-zero rows. Termination is proved by property test over generated hostile contexts, not
asserted.

Every decision is a block in a SHA-256 hash chain. Break it yourself from the dashboard, or:

```bash
python tasks.py verify-audit
```

## The corpus

Reproducible byte-for-byte from `SEED=20260905` against a fixed anchor instant.

| | |
|---|---|
| Transactions | **420** — 210 captured · 96 failed checkout · 62 abandoned · 28 overdue invoices · 24 subscription failures |
| Customers | 140 (6 opted out · 4 DND-registered · ≥22 without marketing consent) |
| Captured GMV | ₹7,93,199 over 14 days |
| Revenue at risk **in the corpus** | ₹8,61,995 — every failed and abandoned attempt. Not the same quantity as the dashboard's *At risk* tile, which counts only cases still **open** after the agent has run. Same words, different populations. |

Failures are deliberately over-sampled — a corpus with three failures exercises nothing — so rates
over it are rates of the sample, not of a real funnel.

## What is real, and what is not

| | |
|---|---|
| **Real** | Razorpay API calls · HMAC webhook verification · the policy firewall · capability tokens · twelve stopping rules · attribution · the hash chain · idempotency and the transactional outbox |
| **Simulated** | Customer responses. Baseline self-recovery 21%, treated uplift 7–14% by playbook — declared parameters, printed on every batch run. |
| **Mocked** | Message *delivery*. Template rendering, consent class, DND, quiet hours and every policy check are real; nothing is sent to a phone. |
| **Not attempted** | Production merchant traffic. Multi-tenancy. |

The **payment-recovery path is proven end to end.** The other three playbooks run through the same
policy and attribution machinery but have no production-integrated delivery path.

## What broke

39 incidents in [`docs/INCIDENTS.md`](docs/INCIDENTS.md), each with the part that matters: why no
test caught it. Three worth reading:

- **[INC-026]** A metrics table with a reader and no writer. The panel showed zero forever, and the
  test passed *because* the feature was missing — it queried an empty table and got the zeros it
  expected.
- **[INC-032]** `tasks.py batch` ran an unfiltered delete and destroyed every Razorpay-verified
  recovery. Silently. That is how this project lost its first live verification, which I had written
  off as carelessness. It was a bug.
- **[INC-039]** `overview()` returned a placeholder that every caller had to remember to overwrite.
  Two did. The evidence snapshot did not, and published ₹0.00 into the file whose job is being
  trusted.

> **The common failure was never a crash. It was a test passing while the feature was absent or
> wrong.** So every new test is now deliberately sabotaged — break the thing it covers, confirm the
> test fails. That step has caught vacuous tests *inside* the fixes for vacuous tests, twice.

## Limitations

Stated once.

- **The lift is not statistically significant** at 171 treated against 39 control, and the dashboard
  says so. Reaching significance needs 1,592 cases and merchant traffic we do not have.
- **The response model is ours.** ₹60,217 is only as good as that assumption, and it cannot be
  validated without real traffic.
- **The audit chain detects in-place modification and corruption. It does not detect truncation of
  its own tail** — a chain cut short verifies clean. External head checkpoints are required for that
  threat; the limitation is written into
  [`audit.py`](apps/api/app/tools/audit.py) rather than left to be found.
- **Single tenant. SQLite. Message delivery mocked.** Current scope, not hidden failure.

## Reproduce everything

```bash
python tasks.py demo         # seed, batch, API, dashboard
python tasks.py benchmark    # the ablation table
python tasks.py power        # what proving causality would cost
python tasks.py verify-audit # recompute the hash chain
python tasks.py snapshot     # regenerate docs/EVIDENCE.md
python tasks.py check        # lint, types, the full suite, the web build
```

1,194 tests. Safety properties are tested adversarially: termination, token isolation, idempotency,
attribution, tamper detection, and the absence of wall-clock reads. `mypy --strict` clean.

## Where the detail lives

| | |
|---|---|
| [`docs/EVIDENCE.md`](docs/EVIDENCE.md) | Every figure, generated from one run |
| [`docs/PRE-REGISTRATION.md`](docs/PRE-REGISTRATION.md) | The causal experiment, registered before the data |
| [`docs/INCIDENTS.md`](docs/INCIDENTS.md) | 39 incidents, wrong theories included |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | 45 decisions, including what we rejected |
| [`workflow.md`](workflow.md) | The full design document |
| [`docs/DEMO-SCRIPT.md`](docs/DEMO-SCRIPT.md) | The five-minute pitch, word for word |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Deployment options |

`apps/api` is the FastAPI service and the agent; `apps/web` the Next.js dashboard; `tests/`
mirrors the API package, with `property/` for the firewall and termination proofs.

---

**RevPilot doesn't just recover revenue — it determines whether it deserves the credit.**

MIT
