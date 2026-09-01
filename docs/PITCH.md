# Pitch — five minutes, and the twelve form answers

Razorpay AI Buildathon 2026 · Track 03, AI Revenue Recovery · applications close 5 September.

Everything quoted here is a number the running system produces. Regenerate them all with
`python tasks.py demo` and read them off the dashboard before you record; if any figure below
disagrees with the screen, the screen is right and this file is stale.

---

## Part 1 — What the judges said they grade

Four criteria, in their words:

| Criterion | Their gloss | Where this project answers it |
|---|---|---|
| **Problem taste** | did you pick something that actually matters | the gross-vs-incremental split — most recovery tools report the first number |
| **Build quality** | does it run, is it structured, would you trust it | one command, no Docker, 1,275 tests, `mypy --strict` |
| **AI judgment** | the right tool in the right place, **and where you chose not to use one** | the rule table answers 158 of 199 diagnoses; the model is consulted on 41 |
| **Failure recovery** | what broke, and what you did about it | 44 written incidents, each with the reason no test caught it |

And the line that decides the running order: *"The last one is the one we read first."* Answer
12 is the submission. Everything else supports it.

---

## Part 2 — The five-minute video

**The script lives in [`DEMO-SCRIPT.md`](DEMO-SCRIPT.md), word for word.**

It is kept there and not here because two documents holding two versions of one script is exactly
the drift `docs/EVIDENCE.md` exists to prevent, one level up. An earlier draft of this file told the
presenter to click `RC-0142` while narrating facts about `RC-0001`, and pointed at a terminal
command for a step that now has a button.

`DEMO-SCRIPT.md` is the tested one: `tests/test_pitch_script_is_accurate.py` checks that every case
id resolves against the committed corpus, every panel name it tells you to click actually renders,
the segment timings are monotonic and inside five minutes, and — after the narration measured **317
words per minute** on its first draft — that each segment can be spoken in the time it is given.

## Part 3 — The twelve form answers

Six are facts only you can supply. Six are below, ready to paste.

### The six about you

| # | Field | Answer |
|---|---|---|
| 1 | Full name | *yours* |
| 2 | College | *yours* |
| 3 | Graduation year | *yours* |
| 4 | In-person in Bangalore from September | *yes / no — answer honestly; they schedule on it* |
| 5 | 6 or 12 months | *your pick. 12 reads as more committed; only say it if it's true* |
| 6 | Resume | *upload the file. They stated they don't screen on it* |

### 7 · Your track

> Track 03 — AI Revenue Recovery

### 8 · Project name

> RevPilot AI — Revenue Recovery Autopilot for Razorpay

### 9 · What it solves

> Razorpay merchants lose revenue in four places — failed payments, abandoned checkouts,
> overdue invoices and dead subscription mandates — and the tools that chase it back all report
> the same misleading number: gross money recovered. Some of those customers would have paid
> anyway. Billing for them is charging for weather.
>
> RevPilot AI detects revenue at risk from Razorpay's own failure telemetry, diagnoses the
> cause, and executes one bounded recovery action inside a deterministic policy firewall. Two
> things make it different from a retry script.
>
> **It measures what it actually caused.** A holdout arm of 39 cases is deliberately never
> contacted. Treated converts at 29.2%, control at 23.1%, so the incremental lift is
> ₹60,217 against a ₹2,02,760 gross figure — 30% of what a dashboard would claim. Both numbers
> are on screen, the smaller one is dominant, and it reports the result as not statistically
> significant at this sample size — z = 0.77, p = 0.44, with the 95% interval on the difference
> running from −8.7 to +21.0 points.
>
> **The model cannot touch money.** It diagnoses ambiguity and argues for an action; a
> deterministic firewall clamps every number and mints an HMAC-signed capability token, and the
> execution node reads only the clamped action. Asked to charge more than a customer owes, the
> system returns `UNREPRESENTABLE` — the proposal object has no amount field, so there is no
> number to raise.
>
> Compliance is structural, not advisory: twelve named stopping rules (opt-out, TRAI quiet
> hours, contact caps, consent class, discount budget, kill switch), a SHA-256 hash-chained
> audit ledger with a public verifier and a tamper endpoint, and a property-based proof that
> every case terminates.
>
> One real Test Mode rupee has been recovered end-to-end and proven by Razorpay's own signed
> webhook. It sits on its own tile, badged `RAZORPAY VERIFIED`, never averaged with the
> simulation — because a signed webhook and a seeded corpus are different kinds of evidence.
>
> **And the question it has not answered is on the dashboard too, third from the top.** Whether
> RevPilot *caused* additional customers to pay is unproven: that needs 1,592 cases at a
> balanced split and a DLT-registered merchant, and the dashboard reports that we are at 4.9%
> of the control arm required. The full design — primary endpoint, allocation, stopping rule,
> and the result that would make us abandon the hypothesis — is pre-registered in
> `docs/PRE-REGISTRATION.md`, committed before any of the data existed so the ordering is
> checkable rather than claimed. The randomised holdout itself has been exercised end-to-end
> against real Razorpay: both arms, real links for treated cases, nothing sent to control, and
> a control payment recorded as organic rather than credited to us. That is a test of the
> instrument, labelled as one.
>
> Runs with one command. No Docker, no Postgres, no Redis, no API key required.

### 10 · GitHub repo URL

> https://github.com/Aakarsh2007/Aegis-Merchant

### 11 · Pitch video

> *unlisted YouTube link — see Part 2*

### 12 · What broke, and how you got out

> Twenty-nine incidents are written up in `docs/INCIDENTS.md`, each with the part that matters:
> why no test caught it. Three of the worst are the same defect wearing different clothes — **a
> green test that cannot distinguish working from absent** — and I only started finding that
> class of bug when I stopped trusting the suite.
>
> **INC-026 — a table with a reader and no writer.** The panel showing how many model calls were
> made and what fraction came from cache displayed zero inferences. On every clone, since the
> day it was written. `llm_calls` was declared, registered, migrated, indexed twice and read by
> the cost report; nothing in the codebase ever inserted a row. The existing test passed
> *because* the feature was missing — it called the cost report against an empty table and
> asserted the zeros that came back. It was a test of SQL `COUNT` over no rows, written and
> reviewed as a test of cost accounting. The graph is pure and the persistence layer is dumb;
> both halves were well tested and nothing tested the join. I found it by reading a screenshot
> of my own dashboard.
>
> Fixing it exposed **INC-029** in the same hour. The committed response cache — 81 entries,
> there so the demo runs offline and reproducibly — had a **structurally guaranteed** 0% hit
> rate. The cache key is a hash of the whole model context; the warming script built a
> five-key context and the agent built an eight-key one. Not one entry could ever match a
> lookup. The two bugs had concealed each other: the hit rate was the symptom, and the only
> instrument that reports it read the table nothing wrote to. The fix removes the second place
> contexts are built rather than trying to keep two copies in step — warming now *runs the
> batch itself*, so a recorded key is by construction the key looked up later.
>
> **INC-022 — twelve tested rules, three that could never fire.** Five fields on the stopping-rule
> context were never populated by the agent, and every default was the permissive value. So
> S-10 (promise-to-pay freeze), S-11 (merchant budget) and S-12 (the kill switch) were
> unreachable. **The kill switch could not kill.** Twelve rules had unit tests and all twelve
> passed, because the tests constructed the context by hand and filled in the fields the
> product never filled in.
>
> **INC-024 — the live webhook path stored events and dropped them.** `_process_event` was still
> the Phase-2 stub. Signature verification worked, storage worked, attribution worked — and
> nothing connected them, so a real payment recovered a real rupee and the dashboard showed
> zero. Two well-tested halves, no test across the join. Same shape as INC-026, three weeks
> earlier, and I did not recognise the pattern until the third time.
>
> **INC-023 — a headline number that changed with the time of day.** The batch read the wall
> clock. The ₹2,02,760 figure was different in the morning and the evening. Every test injected
> a fake clock, so the suite was structurally incapable of seeing it. The fix was a lint rule
> that forbids wall-clock reads in application code — which then caught me doing it again two
> phases later.
>
> **INC-021 — I was wrong, and it cost the user time.** I told them their webhook secret was
> wrong and had them re-enter it. It wasn't; a 300-second replay window was rejecting Razorpay's
> retries, which return 401 the same way a bad signature does. I couldn't support the diagnosis
> I'd already acted on. Both paths now log distinguishably, and the write-up says plainly that
> the original secret was almost certainly correct all along.
>
> **What I actually changed about how I work.** Green tests stopped counting as evidence. Every
> new test now gets sabotage-verified — I break the thing it covers and confirm the test fails,
> and that step has caught vacuous tests inside the fixes for vacuous tests. Every real bug in
> this project was found by touching the real provider or by looking at the running product;
> none was reachable from local testing at any volume. The suite is at 1,275 tests and I trust it
> considerably less than I did at 400.

---

## Part 4 — Rehearsal: the questions a panel will actually ask

Short answers. Say the number, then the caveat, then stop.

**"Is the ₹2 lakh real money?"**
No, and the dashboard says so. It's a seeded corpus through real machinery — the same
attribution rules, the same arm assignment, the same arithmetic. The customer *responses* are a
declared parameter, and the badge says `SIMULATED`. The only figure badged `RAZORPAY VERIFIED`
is ₹1.00, and that one is a real Test Mode payment with a signed webhook behind it.

**"So you've only recovered one rupee."**
Correct, and I'd rather say that than inflate it. Two lakh of machinery, one rupee of proof,
and the two are on separate tiles so you can tell which is which.

**"Why is the lift not significant?"**
39 control cases isn't enough. The observed lift is 6.16 percentage points, but p = 0.44 — at
this sample size that is indistinguishable from chance, and the panel says so rather than hiding
it. Getting to significance needs a real merchant's traffic volume, which I don't have and
didn't fake.

**"Where does the AI actually do anything?"**
Two places. It diagnoses the 41 of 199 cases where Razorpay sent no error fields, so there's
nothing to look up — and it argues for a strategy, which the playbook then overrides if the
action is forbidden. It does not choose amounts, it cannot send anything, and it has no field
to change a rupee figure with.

**"Why not use the model everywhere?"**
I measured it. 90.4% against the rule table's 96.4%, over the 83 of 85 golden cases the committed cache covers. The commitment was
written down before the measurement: if the model doesn't beat the table, ship the table and
say so. It didn't, so the table ships.

**"You're claiming a lift you haven't measured."**
Correct, and the dashboard says so before it says anything else. What is measured is that the
machinery computing the lift works on real provider data: the arm assignment is recomputable
from the case id, a treated settlement matches a reference we issued, and a control settlement
resolves as organic rather than being credited to us. What is *not* measured is customer
behaviour, and the pre-registration says exactly what would measure it and what would make me
abandon the hypothesis.

**"Why not just run it on a few real people and report that?"**
Because 30 cases gives a confidence interval about 40 points wide, and reporting it would
destroy the only thing that makes this submission worth reading. The arithmetic is in
`tasks.py power`: 796 per arm at the effect size I'm assuming. Anything less and I'd be
publishing noise with a decimal point on it.

**"What would you do next?"**
Get it in front of one real merchant with enough volume to make the holdout arm significant.
Everything else — the firewall, the audit chain, the stopping rules — is built to survive that;
the statistics are the only part that needs traffic I can't simulate.

**"What's the weakest part?"**
The response model in the simulation is mine, so the ₹60,217 is only as good as that
assumption, and I can't validate it without real traffic. Second is tail truncation in the
audit chain: the hash chain detects any edit to history, but a chain cut short at the end
verifies clean. That's written down in `audit.py` rather than left for someone to find.
