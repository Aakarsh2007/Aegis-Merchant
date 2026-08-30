# RevPilot AI — Merchant Autopilot for Razorpay

**An autonomous revenue-recovery employee for Razorpay merchants: it diagnoses why
money was lost, takes the cheapest bounded action that recovers it, proves the
recovery against signed webhooks, measures its own incremental lift against a
control group, and asks a human only when a human decision is genuinely required.**

Razorpay AI Buildathon 2026 · Track: **AI Revenue Recovery**

[![CI](https://github.com/Aakarsh2007/Aegis-Merchant/actions/workflows/ci.yml/badge.svg)](https://github.com/Aakarsh2007/Aegis-Merchant/actions/workflows/ci.yml)

> **Build status: Phase 13 of 15 complete.** Built and tested: the injected clock,
> the 18-table schema and seeded corpus, the signed-webhook boundary, the
> deterministic classifier, twelve stopping rules with a property-based termination
> proof, the policy firewall and capability token, the LLM adapter stack with a
> committed response cache, the seven-node agent graph, the transactional outbox,
> attribution with a holdout control arm, the SHA-256 audit chain and its public
> verifier, bearer auth, HITL approvals, the template render boundary, and the REST +
> SSE surface, the Command Center, the batch runner, all four playbooks, the morning
> briefing and the chaos suite. Remaining: real inbound webhooks over a tunnel (14), and
> the eval harness and video (15).
>
> The full architecture, and the reasoning behind every choice in it, is in
> [`workflow.md`](workflow.md) — a build contract written before the first line of code.
>
> **Every rupee figure below carries a provenance badge**, and the API enforces that
> as a type rather than a convention: `RAZORPAY_VERIFIED` means a signed webhook
> proves it, `SIMULATED` means real machinery over seeded inputs, `ESTIMATED` means a
> projection. Nothing has run against live production traffic, and no figure claims
> otherwise.

## The corpus, measured

Run `python tasks.py seed` and it prints this — so the numbers in this README cannot drift
away from the data without someone noticing.

| | |
|---|---|
| Transactions | **420** — 210 captured · 96 failed checkout · 62 abandoned · 28 overdue invoices · 24 subscription failures |
| Customers | 140 (6 opted out · 4 DND-registered · ≥22 without marketing consent) |
| Captured GMV | ₹7,93,199 over a 14-day window (implied ~₹17.0L/month) |
| Revenue at risk | ₹11,84,629 |
| Recovered so far | **₹0** — nothing has run against live traffic |
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
| **Net incremental** | **₹60,217** — *what we actually caused* |
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

## Run it

No credentials. No Docker, Postgres, Redis or message broker. Two commands.

```bash
pip install -r apps/api/requirements.txt
python tasks.py demo          # or: make demo
```

Then open <http://localhost:8000/docs> · health at `/healthz` ·
full dependency report at `/api/v1/health/deep`.

`python tasks.py` on its own lists every task. `make <task>` is an exact alias
(`make` is not installed on Windows, so `tasks.py` is the source of truth and the
Makefile delegates to it).

### Progressive fidelity — every step is free

| Add to `.env` | What upgrades |
|---|---|
| *nothing* | Mock Razorpay provider + deterministic reasoning. **Everything runs.** |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Real Razorpay **Test Mode** payment links |
| `GEMINI_API_KEY` | Live LLM reasoning (Google AI Studio free tier) |
| A Cloudflare Tunnel URL | Real signed inbound webhooks |

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

## License

MIT
