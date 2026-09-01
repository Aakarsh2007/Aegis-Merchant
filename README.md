# RevPilot AI — Revenue Recovery Autopilot for Razorpay

Razorpay AI Buildathon 2026 · Track: **AI Revenue Recovery**

[![CI](https://github.com/Aakarsh2007/Aegis-Merchant/actions/workflows/ci.yml/badge.svg)](https://github.com/Aakarsh2007/Aegis-Merchant/actions/workflows/ci.yml)

**An autonomous revenue-recovery agent for Razorpay merchants.** It finds money
that is slipping away — failed payments, abandoned checkouts, overdue invoices,
dead subscription mandates — diagnoses why, takes the cheapest bounded action
inside a policy firewall, and proves any recovery against Razorpay itself.

Three numbers, three different questions, and the third one is open:

| | | Question it answers |
|---|---|---|
| **₹2.00** | **RAZORPAY VERIFIED** | *Can it execute and verify a recovery through Razorpay?* **Yes, both ways** — two real Test Mode payments: one proven by Razorpay's own signed webhook (`TWSSP5BW90Y89E`), one by direct API reconciliation after a webhook was lost to a dead tunnel (`plink_TWPwcbsfrYnIQQ`). A lost webhook cost nothing. |
| **₹60,217** | **SIMULATED** | *What might it recover at scale?* Estimated incremental lift over a 39-case holdout, under a **declared** response model. |
| **₹0** | **LIVE PRODUCTION** | No real merchant traffic. Not attempted. |
| **—** | **NOT PROVEN** | *Did it cause additional customers to pay?* **No.** That needs 1,592 cases and a DLT-registered merchant. The plan is [pre-registered](docs/PRE-REGISTRATION.md), committed before any data existed, and the dashboard shows how far short we are: **4.9%**. |

The fourth row is on the dashboard, third from the top, above the panels showing
things that work. `python tasks.py power` prints the arithmetic.

> ### AI proposes. Policy disposes.
>
> The model reads ambiguity and argues for an action. It **cannot touch money** —
> it has no field to change an amount with, and nothing happens without a
> capability token the policy firewall mints.

```bash
python tasks.py demo        # no credentials, no Docker, ~40 seconds
```

1,139 tests · `mypy --strict` clean · 37 documented incidents · one command to run.

> **On a fresh clone the verified tile reads ₹0.00, and that is correct.** Nothing has been
> proven on *your* machine yet. The two recoveries above happened here, on 31 August 2026, with
> the provider ids shown. Click **"Prove it against real Razorpay"** on the dashboard — or run
> `python tasks.py testmode-recover` — and make your own.

---

## How it works, in 60 seconds

**The problem.** A merchant does not lose ₹10 lakh at once. They lose ₹4,299
here and ₹18,500 there — a UPI timeout, an abandoned cart, an invoice nobody
chased, a subscription mandate that quietly died.

**What it does.** Detect → Diagnose → Decide → Act → Verify → Attribute.
Four revenue leaks, one agent, one attribution system.

**What makes it safe.** A deterministic policy firewall between the model and
the money: consent classes, DND, quiet hours, twelve stopping rules, hard
amount and discount ceilings, and a capability token without which no side
effect can occur. Nine places where an LLM was deliberately *rejected* in
favour of a rule — a limit check that sometimes hallucinates is strictly worse
than one that cannot.

**How we know it worked.** 210 cases, **39 deliberately never contacted.** The
gross-versus-incremental argument is [below](#the-number-and-why-it-is-smaller-than-the-one-you-expected),
and it is the point of the whole project.

**The ₹2 matters more than the ₹60,217.** It is the whole loop on real Razorpay
infrastructure: agent → policy firewall → capability token → real payment link
→ Razorpay's own signed webhook → HMAC verified → reference matched →
attributed → audit block. Test Mode proves the *execution path*. It does not
prove that customers change their behaviour, which is why the lift experiment
stays labelled SIMULATED.

**The proof.** 900+ tests · one real signed Razorpay webhook verified end to
end · tamper-evident audit ledger you can break yourself · 12 stopping rules
with a property-based termination proof · 37 documented incidents.

```
                        RAZORPAY
                            |
                      signed webhook
                            v
                    +---------------+
                    |    DETECT     |   deterministic
                    +-------+-------+
                            v
                    +---------------+
                    |   DIAGNOSE    |   <-- AI (or a rule table: 96.5%)
                    +-------+-------+
                            v
                    +---------------+
                    |    PROPOSE    |   <-- AI
                    +-------+-------+
                            v
            +-------------------------------+
            |       POLICY FIREWALL         |   deterministic
            |  consent · DND · quiet hours  |
            |  limits · budgets · 12 rules  |
            +---------------+---------------+
                            v
                   CAPABILITY TOKEN          no token, no side effect
                            v
                       EXECUTION             transactional outbox
                            v
                    SIGNED WEBHOOK
                            v
                     ATTRIBUTION             6 conditions, all required
                            v
                  INCREMENTAL REVENUE        measured against the holdout
```

## Run it — 40 seconds

```bash
pip install -r apps/api/requirements.txt
cd apps/web && npm install && cd ../..
python tasks.py demo
```

Then open **<http://localhost:3000>** and read the guided tour at the top of the
page. It walks you through the four things worth looking at, and doubles as the
demo script.

**The one thing to click:** the *Try to make it do something dangerous* panel.
Ask the agent for a 90% discount, try to charge double, try to market to a DND
customer, try to act with the kill switch off. Each is refused by a **different
mechanism**, and one legitimate action is allowed — so you can tell the
firewall apart from a system that just says no.

---

> **Status: complete and running.** 900+ tests, `mypy --strict` clean, CI green.
> Clone it and `python tasks.py demo` — no credentials, no Docker, no Postgres.
>
> The full architecture and the reasoning behind every choice is in
> [`workflow.md`](workflow.md), a build contract written before the first line of
> code. Twenty-two things broke along the way and each is written up in
> [`docs/INCIDENTS.md`](docs/INCIDENTS.md), wrong theories included.

---

## The number, and why it is smaller than the one you expected

Over 210 recovery cases, with 39 of them deliberately **never contacted**:

| | |
|---|---|
| Gross recovered | **₹2,02,760** — what a dashboard would show |
| **Simulated incremental** | **₹60,217** — estimated causal lift under the declared response model |
| Absolute lift | 6.2% (treatment 29.2%, control 23.1%) |
| Statistically significant | **No.** 39 control cases; the intervals overlap. |

**Nearly a quarter of the control group paid without us.** Reporting the gross
figure would overstate our contribution by roughly three times, so the system
refuses to: `/metrics/overview` cannot return gross without also returning net,
and a lift that is not distinguishable from zero says so *on the report*, not in
a boolean a caller might forget to render.

That is the whole argument. An agent that moves money is only as trustworthy as
its willingness to report a smaller number.

> **Provenance, enforced as a type.** Every rupee figure carries a badge —
> `RAZORPAY_VERIFIED` (a signed webhook proves it), `SIMULATED` (real machinery,
> seeded inputs), `ESTIMATED` (a projection). `Figure` cannot be constructed
> without one, and a test walks the API response looking for anything
> money-shaped, so it fails when a tile is *added* without a badge.

## The corpus, measured

Run `python tasks.py seed` and it prints this — so the numbers in this README cannot drift
away from the data without someone noticing.

| | |
|---|---|
| Transactions | **420** — 210 captured · 96 failed checkout · 62 abandoned · 28 overdue invoices · 24 subscription failures |
| Customers | 140 (6 opted out · 4 DND-registered · ≥22 without marketing consent) |
| Captured GMV | ₹7,93,199 over a 14-day window (implied ~₹17.0L/month) |
| Revenue at risk **in the corpus** | ₹8,61,995 — every failed and abandoned attempt. Not the same quantity as the dashboard's *At risk* tile, which counts only cases still **open** after the agent has run (₹6,64,067 over 117). Same words, different populations — distinguished here because a reader comparing the two would otherwise conclude one of them is wrong. |
| Recovered so far | **₹2.00** `RAZORPAY VERIFIED` — two real Test Mode payments, one proven by a signed webhook and one by API reconciliation after a webhook was lost. No **production** merchant traffic. |
| Reproducible | Byte-for-byte from `SEED=20260905` against a fixed anchor instant |
| Declared scenario | A 3-hour `upi/HDFC` outage (41.0% over 39 attempts vs a 65% baseline), so there is a genuinely degraded rail to detect. Scenario design, not metric tuning — see [INC-004](docs/INCIDENTS.md) |

Failures are **deliberately over-sampled** — a corpus with three failures would exercise
nothing — so rates computed over it are rates of the sample, not of GlowKart's true funnel.

### What the agent does with it, measured

Running all 210 eligible cases through the agent and the attribution rules:

| | |
|---|---|
| Authorised for execution | 139 · escalated to a human 25 · stopped by policy 11 |
| Held as CONTROL, never acted on | 39 (18.6%) |
| Gross recovered | ₹2,02,760 — *what a dashboard would show* |
| **Simulated incremental** | **₹60,217** — *estimated causal lift under the declared response model* |
| Absolute lift | 6.2% (treatment 29.2%, control 23.1%) |
| Statistically significant | **No.** 39 control cases; the 95% intervals overlap. Reported as directional. |

Nearly a quarter of the control group paid **without us**. Reporting the gross figure would
have overstated our contribution by a factor of three, and the system refuses to.

> **Declared:** the customer-response model in the simulated batch is a parameter (21%
> baseline self-recovery, 7–14% treated uplift by playbook), grounded in published
> recovery benchmarks — not a measured population value. What is real and unmodified is the
> **machinery**: arm assignment, the six attribution conditions and the lift computation run
> identically on live Razorpay traffic, where the outcomes are genuine.

---

## Adding credentials — every step is free

| Add to `.env` | What upgrades |
|---|---|
| *nothing* | Mock Razorpay provider + deterministic reasoning. **Everything runs.** |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Real Razorpay **Test Mode** payment links |
| `GEMINI_API_KEY` | Live LLM reasoning (Google AI Studio free tier) |
| A Cloudflare Tunnel URL | Real signed inbound webhooks |

---

---

## What is real, and what is mocked

**Stated before the architecture, deliberately.** A judge should not have to dig for
the boundary between what this system does and what it simulates.

| Real | Mocked, and labelled as such in the UI |
|---|---|
| Razorpay Test Mode: Payment Links created for real, `reference_id` idempotency **verified live** — a duplicate is refused and the existing link is retrievable | WhatsApp/SMS **delivery** — we hold no DLT registration or WhatsApp Business Account. The consent, template and quiet-hours machinery in front of the adapter is real and fully enforced. |
| HMAC-SHA256 webhook verification over raw bytes, with a replay window | The control-arm self-recovery baseline **inside the simulated batch** is a declared parameter, not a measured population value |
| The deterministic policy firewall, and every bound it enforces | |
| SHA-256 hash-chained audit ledger, with a public verifier | |
| Signed-webhook-only revenue attribution | |
| Randomised holdout control arm and incremental-lift computation | |

Every rupee figure in the dashboard carries a provenance badge —
`RAZORPAY VERIFIED`, `SIMULATED`, or `ESTIMATED` — and figures of different
provenance are never summed into one number.

**Cost to build and run: ₹0.** Every dependency is a free tier or self-hosted.

---

## How it is judged, and where to look

| Judging criterion | Where the evidence is |
|---|---|
| **Problem taste** — *did you pick something that actually matters* | [`workflow.md` §0](workflow.md), §5 — four distinct revenue leaks, with the scope we cut and why |
| **Build quality** — *does it run, is it structured, would you trust it* | This section, §13 (auth, PII, injection containment), §22 (Judge Mode), CI badge above. The policy firewall is proven closed by property test — and the proof itself is checked for vacuity, after the first version passed while proving nothing ([INC-006](docs/INCIDENTS.md)) |
| **AI judgment** — *the right tool in the right place, and where you chose not to use one* | §4.2 — nine places we **rejected** an LLM in favour of deterministic code. Now measured: the rule table scores **96.5%** on an 85-case golden set at zero cost, and **Phase 6's model must beat that or we ship the rule table** ([baseline](tests/eval/test_classifier_baseline.py)) |
| **Failure recovery** — *what broke, and what you did about it* | [`docs/INCIDENTS.md`](docs/INCIDENTS.md) — written while things were broken, wrong theories included |

The track bar asks for *"measured money recovered across a batch, with compliant
escalation, stopping rules, and an audit trail."* Each clause maps to a named
artefact in [`workflow.md` §1](workflow.md). **Stopping rules** are built and proven:
twelve named rules ([`stopping_rules.py`](apps/api/app/guardrails/stopping_rules.py)),
each individually counted, with termination established by property test over 2,000
generated contexts per run rather than by example
([proof](tests/property/test_stopping_termination.py)).

### A real Razorpay recovery, end to end

Not a fixture. The agent created a real Test Mode payment link, it was paid,
and Razorpay's own infrastructure delivered the settlement:

```
case            RC-TM64210
status          RECOVERED
recovered       ₹1.00
verified_by     TWK4SYivi78jL4        <- a real Razorpay event id
delivered by    52.66.76.63           (Razorpay, Mumbai)
reference       rvp_rc-tm64210_278    <- a reference WE issued
audit chain     valid, 214 blocks
```

**Reproduce it in three steps — no tunnel needed:**

```bash
curl -X POST localhost:8000/api/v1/testmode/recover     # returns a real Razorpay link
#   ... pay it with card 4111 1111 1111 1111, any future expiry, any CVV
python tasks.py reconcile                                # asks Razorpay what was paid
```

The agent runs the whole path — diagnose, policy firewall, capability token,
real payment link with a reference we issued. `reconcile` then asks Razorpay
directly whether it was paid, which needs no public URL.

Webhooks work too, and are faster ([`docs/webhooks.md`](docs/webhooks.md)) — but
a webhook is a notification, not a source of truth. It can be lost, delayed, or
delivered to a URL that has since died. All three happened while building this,
which is why the poller exists ([DEC-037](docs/DECISIONS.md)).

**Doing this found two bugs nothing local could have.** The webhook handler was
storing events and dropping them, so attribution never ran on the live path
(INC-024). And a real `payment_link.paid` carries three entities where only one
holds the `reference_id` — our single-entity fixture could not have revealed
it (INC-025). Both are written up in
[`docs/INCIDENTS.md`](docs/INCIDENTS.md).

### A real Razorpay webhook, verified

Not a fixture we wrote. Razorpay's own infrastructure delivered this over a
public tunnel:

```
event_id          TW3Rfq6VhWiuwC
event_type        payment_link.paid
signature_valid   TRUE
status            ACCEPTED (HTTP 200)
delivered by      52.66.76.63   (Razorpay, Mumbai)
reference_id      rvp_live_v2_...   <- a reference WE issued
```

That last line is attribution condition 3 — the difference between attribution
and coincidence — satisfied by a real event rather than a constructed one. The
payload is committed at `tests/fixtures/razorpay/payment_link.paid.captured.json`
with contact details redacted.

Reproduce it with `python tasks.py tunnel`; the steps are in
[`docs/webhooks.md`](docs/webhooks.md).

### Break the audit chain yourself

The one claim in this project you should not take on trust. A verifier nobody
has watched fail is indistinguishable from one that returns `true`:

```bash
curl -s localhost:8000/api/v1/audit/verify
#  {"valid": true, "blocks": 5, "head_hash": "462e7c54...", ...}

curl -s -X POST localhost:8000/api/v1/audit/tamper      -H 'content-type: application/json'      -d '{"block_index": 2, "mode": "payload"}'

curl -s localhost:8000/api/v1/audit/verify
#  {"valid": false,
#   "first_divergence_index": 2,
#   "reason": "block 2: payload does not match its stored hash"}
```

`mode` also accepts `hash` and `timestamp`, which trip different checks. The
endpoint is refused in production and the tests assert that.

**What the chain does not do**, stated because it is the first thing a reader
who knows hash chains will ask: **deleting the last *k* blocks is undetectable
from the chain alone** — what remains is a shorter, perfectly valid chain. No
construction living entirely inside the database it protects can prevent that.
`verify` therefore returns `head_hash` and `blocks` so an external observer who
recorded them earlier can catch a rollback we cannot catch ourselves, and a
test asserts this limitation rather than hiding it ([DEC-022](docs/DECISIONS.md)).
What is honestly claimed: an in-place edit requires rewriting every subsequent
block, a partial edit is loudly detectable, and the cost of a silent change goes
from one `UPDATE` to a full rewrite.

---

## Architecture in one paragraph

Razorpay webhooks arrive, are HMAC-verified over raw bytes, deduplicated, and
normalised. A bounded seven-node state machine — plain async Python, no agent
framework ([DEC-019](docs/DECISIONS.md)) — enriches the case, runs twelve
stopping rules **before spending a token**, diagnoses the failure, and proposes
a recovery. Every proposal then passes a deterministic policy firewall the
LLM cannot reach — amount ceilings, discount caps, contact-frequency limits, quiet
hours, consent class, spend budgets. Only the firewall can mint the token that
unlocks a write tool. Execution goes through a transactional outbox whose
idempotency key is committed *before* the Razorpay call, so a crash mid-execution
cannot double-charge anyone. Recovery is counted only when a signed webhook arrives
carrying the exact reference we issued. Eighteen percent of eligible cases are
randomly held back with no intervention at all, so the recovery number can be
stated as incremental lift rather than asserted.

**The structural rule: the language model diagnoses and writes. It never touches
money.**

---

## Repository layout

```
apps/api/app/
  core/clock.py            injected Clock — the only sanctioned wall-clock read
  core/provenance.py       a rupee figure cannot exist without a badge and a basis
  core/stats.py            Wilson intervals, shared by rail health and lift
  config.py                every policy bound, as config rather than a literal
  main.py                  app factory, auth posture, real health probes
  db/types.py              UtcDateTime — rejects naive datetimes at the DB boundary
  db/enums.py              24 enum columns, each with a real CHECK constraint
  db/models.py             the 18 tables
  db/session.py            async engine + WAL/foreign-key/busy-timeout pragmas
  db/seed.py               the 420-transaction GlowKart corpus
  agent/classifier.py      deterministic failure classifier; business source is absolute
  agent/graph.py           the seven-node state machine — no LangGraph
  guardrails/stopping_rules.py  twelve rules; termination proved by property test
  guardrails/policy_engine.py   the firewall; the only place a token is minted
  guardrails/token.py      HMAC capability token — no token, no side effect
  guardrails/consent.py    template + slots render boundary; free text is impossible
  llm/                     adapter, response cache, rate limit, prompts, routing
  tools/outbox.py          two-phase execution intent
  tools/audit.py           hash chain + verifier, limitations stated
  workers/drainer.py       retry drainer + startup reconciler (crash recovery)
  services/attribution.py  six conditions before a rupee is counted
  services/experiments.py  deterministic, immutable arm assignment
  services/scheduler.py    approval TTL sweeper + stale-deferral cancellation
  services/metrics.py      tile queries; gross and net are never separated
  security/auth.py         bearer auth; refuses to start unset in production
  routers/                 webhooks, cases, metrics, approvals, audit, dlq, stream
  agent/playbooks.py       per-playbook strategy; what each playbook forbids
  workers/batch.py         puts the corpus through the agent; marks output SIMULATED
apps/web/openapi.json      the committed API contract the UI generates types from
apps/web/src/lib/api.ts    typed client; a failed fetch is an error, never a zero
apps/web/src/components/   metrics, attribution, stopping rules, audit verifier, SSE
data/revpilot.seed.db      committed demo database — inspectable without our code
tests/                     unit, integration, eval, property suites
docs/INCIDENTS.md          engineering journal — real breakages, wrong theories included
docs/DECISIONS.md          decisions, including what we rejected
workflow.md                the build contract
tasks.py                   every project command
```

---

## Is this a product, or a demo?

A fair question, answered precisely.

**What is production-shaped and would survive contact with real traffic:** the
signed-webhook boundary, the deterministic classifier, the policy firewall and
its capability token, the twelve stopping rules, the transactional outbox with
crash recovery, the attribution rules, the audit chain, the approval gate with
its hash check, and the template render boundary. These are not sketches. They
have edge-case tests, property tests, and in several cases sabotage tests that
prove the test would catch the bug it was written for.

**What is deliberately single-tenant.** Six of the eighteen tables carry a
`merchant_id`; the other twelve reach a merchant transitively through a case. No
API route filters by merchant, and the bearer token is one shared secret rather
than a per-merchant credential. Turning this into multi-tenant SaaS is a real
piece of work — scoping every query, per-merchant Razorpay credentials with
encryption at rest, an onboarding flow, and a migration — and it is **not
started**. Claiming otherwise would be the kind of overstatement the rest of
this project exists to avoid.

**What is simulated, and only this:** whether a customer pays after we contact
them. The response model is a declared parameter (21% baseline self-recovery,
7–14% treated uplift by playbook), printed on every batch run. Every settled
case is written with a `sim_evt_` verifier so it reports as `SIMULATED` and
**cannot reach the `RAZORPAY_VERIFIED` tile** — which is why that tile honestly
reads ₹0.00.

**What is mocked:** WhatsApp and SMS delivery. DLT template registration with
Indian carriers is neither free nor instant, so the messages are rendered
through the real template boundary and then not sent. The rendering is real; the
delivery is not.

**What would take this to production**, in order of effort: DLT registration and
a messaging provider; multi-tenant scoping; Postgres instead of SQLite (the
outbox is what makes that a swap rather than a rewrite); per-merchant encrypted
credentials; and an onboarding flow. Nothing in that list requires redesigning
what is here.

---

## Known limitations

Named plainly, because volunteering them is cheaper than having them found.

- **Single-process.** SQLite plus in-process scheduling does not scale
  horizontally. The transactional outbox is precisely what makes swapping in a real
  queue consumer mechanical, and that trade is recorded as ADL-003.
- **Messaging delivery is mocked.** DLT registration is neither free nor instant.
- **Out of scope for a hackathon build**, and not pretended otherwise: multi-tenant
  isolation beyond a merchant token, key rotation, public-endpoint rate limiting,
  dashboard CSRF, secret management beyond a local `.env`.
- **The audit chain cannot detect tail truncation**, and says so on every successful
  verification. See the section above; this is a property of the construction, not a
  gap in the implementation.
- **Outbound messaging is template-only by construction.** The model fills named
  slots in a DLT-registered template and cannot emit free text. That is a real
  constraint, not a limitation we regret: a message's compliance class is a property
  of what it says, and no post-hoc classifier would be a better control than making
  the failure unrepresentable ([DEC-025](docs/DECISIONS.md)).
- **Auth is a single shared bearer token**, not per-user identity. Unset, the API is
  open outside production and marks every response `X-Auth-Mode: disabled`; in
  production an unset token makes the app refuse to start ([DEC-023](docs/DECISIONS.md)).
- **NPCI mandate timings** are implemented as the correct *mechanism* with the exact
  numbers as config flagged `VERIFY_BEFORE_PRODUCTION`. Claiming certainty we do not
  have would be worse than flagging it.

---

## Deploying it

For judging, **running it locally is the best option** — a judge who clones the
repo gets exactly what you have, with no environment drift and no credentials.

If you do want it hosted: **frontend on Vercel, API on Render or Railway.** The
API keeps state in SQLite and runs an in-process event bus, so it needs a
persistent disk and a single long-lived instance — which is why the API does
*not* go on Vercel's serverless runtime. Step-by-step instructions, the two
things that will bite you, and a production checklist are in
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

---

## License

MIT
