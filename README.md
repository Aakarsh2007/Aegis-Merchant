# RevPilot AI — Merchant Autopilot for Razorpay

**An autonomous revenue-recovery employee for Razorpay merchants: it diagnoses why
money was lost, takes the cheapest bounded action that recovers it, proves the
recovery against signed webhooks, measures its own incremental lift against a
control group, and asks a human only when a human decision is genuinely required.**

Razorpay AI Buildathon 2026 · Track: **AI Revenue Recovery**

[![CI](https://github.com/Aakarsh2007/Aegis-Merchant/actions/workflows/ci.yml/badge.svg)](https://github.com/Aakarsh2007/Aegis-Merchant/actions/workflows/ci.yml)

> **Build status: Phase 0 of 15 complete.** This README grows with the build. The
> full architecture, and the reasoning behind every choice in it, is in
> [`workflow.md`](workflow.md) — 2,400 lines of build contract written before the
> first line of code.

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
| Razorpay Test Mode: Orders, Payments, Payment Links, Invoices, Subscriptions | WhatsApp/SMS **delivery** — we hold no DLT registration or WhatsApp Business Account. The consent, template and quiet-hours machinery in front of the adapter is real and fully enforced. |
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
| **Build quality** — *does it run, is it structured, would you trust it* | This section, §13 (auth, PII, injection containment), §22 (Judge Mode), CI badge above |
| **AI judgment** — *the right tool in the right place, and where you chose not to use one* | §4.2 — nine places we **rejected** an LLM in favour of deterministic code, and why |
| **Failure recovery** — *what broke, and what you did about it* | [`docs/INCIDENTS.md`](docs/INCIDENTS.md) — written while things were broken, wrong theories included |

The track bar asks for *"measured money recovered across a batch, with compliant
escalation, stopping rules, and an audit trail."* Each clause maps to a named
artefact in [`workflow.md` §1](workflow.md).

---

## Architecture in one paragraph

Razorpay webhooks arrive, are HMAC-verified over raw bytes, deduplicated, and
normalised. A bounded seven-node LangGraph state machine enriches the case, runs
twelve stopping rules **before spending a token**, diagnoses the failure, and
proposes a recovery. Every proposal then passes a deterministic policy firewall the
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
apps/api/app/         FastAPI backend
  core/clock.py       injected Clock — the only sanctioned wall-clock read
  config.py           every policy bound, as config rather than a literal
  main.py             app factory, health endpoints
apps/web/             Next.js Command Center            (Phase 12)
tests/                unit, integration, eval, property suites
docs/INCIDENTS.md     engineering journal
docs/DECISIONS.md     decisions, including what we rejected
workflow.md           the build contract
tasks.py              every project command
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
- **NPCI mandate timings** are implemented as the correct *mechanism* with the exact
  numbers as config flagged `VERIFY_BEFORE_PRODUCTION`. Claiming certainty we do not
  have would be worse than flagging it.

---

## License

MIT
