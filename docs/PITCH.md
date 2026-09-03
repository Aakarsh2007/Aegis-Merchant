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
| **Build quality** | does it run, is it structured, would you trust it | one command, no Docker, 1,282 tests, `mypy --strict` |
| **AI judgment** | the right tool in the right place, **and where you chose not to use one** | the rule table answers 158 of 199 diagnoses; the model is consulted on 41 |
| **Failure recovery** | what broke, and what you did about it | 46 written incidents, each with the reason no test caught it |

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

> Razorpay merchants leak revenue in four places: failed payments, abandoned checkouts, overdue
> invoices, and dead subscription mandates. Every tool that chases it back reports the same
> misleading number — gross money recovered. Some of those customers would have paid anyway, and
> billing a merchant for them is charging them for the weather.
>
> RevPilot reads Razorpay's own failure telemetry, diagnoses the cause, and executes one bounded
> recovery action per case. Three things make it more than a retry script.
>
> **It holds a control group back on purpose.** 39 of 210 cases are randomly assigned and never
> contacted. That costs the merchant real recovery, which is exactly the point — it is the only way
> to know what the agent caused. On the seeded corpus ₹3,41,781 arrived across 59 customers:
> ₹2,02,760 on a path the agent drove, and ₹1,39,021 that arrived on its own and was credited to us
> at ₹0.00. Of the part we drove, ₹60,217 is defensible as incremental. The lift is 6.16 percentage
> points, p = 0.44 — not significant at this sample size, and the dashboard says so before a judge
> has to ask.
>
> **The model cannot touch money.** It diagnoses ambiguity and argues for an action. A deterministic
> firewall clamps every number and mints an HMAC-signed capability token; the execution node reads
> only the clamped action. Asked to charge a customer more than they owe, the system returns
> `UNREPRESENTABLE` — the proposal type has no amount field, so there is no number to raise. Not
> blocked. Unrepresentable.
>
> **Compliance is structural, not advisory.** Twelve named stopping rules — opt-out, DND, TRAI quiet
> hours, contact caps, consent class, discount budget, kill switch — and all twelve are shown on the
> dashboard including the ten that fired zero times, because a brake that did not fire and a brake
> that does not exist look identical if you only show the non-zero rows. Every decision is a block in
> a SHA-256 hash chain with a public verifier and a tamper endpoint you can break yourself.
> Termination is proved by property test over generated hostile contexts, not asserted.
>
> And the part that is not simulated: **₹2.00 has been recovered end to end through real Razorpay
> Test Mode and proven by Razorpay itself** — one by signed webhook, one by API reconciliation after
> a webhook was lost to a dead tunnel. It sits on its own tile badged `RAZORPAY VERIFIED` and is
> never averaged into the simulation, because a signed webhook and a seeded corpus are different
> kinds of evidence.
>
> What it has **not** proven is on the dashboard too, third from the top: whether the agent *caused*
> additional customers to pay. That needs 1,592 cases at a balanced split and a DLT-registered
> merchant. We are at 13.2% overall and 4.9% of the control arm, which is the arm that governs power.
> The full design — primary endpoint, allocation, stopping rule, and the result that would make me
> abandon the hypothesis — is pre-registered in `docs/PRE-REGISTRATION.md`, committed before any of
> the data existed so the ordering is checkable rather than claimed.
>
> Runs with one command. No Docker, no Postgres, no Redis, no API key required.

### 10 · GitHub repo URL

> https://github.com/Aakarsh2007/Aegis-Merchant

### 11 · Pitch video

> *unlisted YouTube link — see Part 2*

### 12 · What broke, and how you got out

> Forty-six incidents are written up in `docs/INCIDENTS.md`, wrong theories left in. The pattern
> behind most of them is one thing: **a green test that cannot tell working from absent.**
>
> **The one a reviewer found, three days out.** They added up my own README headline — ₹2,02,760
> gross, ₹60,217 claimable, ₹1,39,021 not claimed — and it came to ₹3,522 short. Every figure was
> correct. The arrow and the plus sign were wrong: gross and not-claimed are sums over *disjoint*
> sets of cases, and incremental is a statistical estimate over the treated arm's exposure, not a
> slice of gross. I had laid an estimate out as a subset of a total, which is the exact overstatement
> this project exists to refuse, in the most visible place in the repository. Twelve hundred tests
> were green because no test had an opinion about how the three figures related to each other. Now
> the identity is computed — `arrived = driven + organic` — the residual is published so a reader can
> see it is zero rather than take my word, and a test fails if it ever stops balancing.
>
> **Then I went looking myself and found the worse one.** The measured result is that the
> deterministic rule table beats the model — 96.4% against 90.4%, over the 83 of 85 golden cases the
> committed response cache covers. Every document had been quoting both rates a tenth of a point too
> high, over a denominator that was the size of the golden *file* rather than the number of cases
> actually scored. Two of those figures were **hardcoded string literals inside the generator**
> of the file that opens *"Generated, not written. Every figure this submission quotes comes from
> here."* The entire AI-judgment claim was resting on numbers I had typed by hand, wrong in the
> flattering direction, in the one place a technical judge would reproduce. The scoring now lives in
> application code, the test and the snapshot both import it, and a test compares every accuracy
> figure in every document against it.
>
> **And the one that would have cost the demo.** On a clean clone, `python tasks.py batch` printed
> "BATCH COMPLETE — 0 cases" and exited zero. There was no corpus. Worse, it left an empty database
> behind, and `demo` decided whether to seed by asking whether the *file* existed — so the next
> `demo` announced "database present" and served a dashboard of zeroes. Judge Mode was one stray
> command away from silently producing nothing. Found by cloning fresh and running the commands in
> the order a stranger would, because every test builds its own database and no test had ever *been*
> a clean clone.
>
> Same shape earlier, three times before I recognised it: a webhook handler that verified signatures,
> stored events, and dropped them. An `llm_calls` table with a reader and no writer, where the
> existing test passed *because* the feature was missing — it asserted the zeros that came back from
> counting an empty table. An event bus whose only publisher was in tests. Both ends present, both
> ends tested, nothing testing the link. And once a pre-existing test that **asserted the bug**: 41
> of 199 diagnoses were labelled "model reasoning" when a deterministic adapter had answered, and the
> test locked it in.
>
> **What I actually changed about how I work.** Green stopped counting as evidence. Every new test
> gets sabotage-verified — I break the thing it covers and confirm the test fails — and that step has
> caught vacuous tests *inside the fixes for vacuous tests*. Every real bug in this project was found
> by touching the real provider or by looking at the running product: a screenshot, a hover caption,
> a scanned PDF of my own dashboard. None was reachable from local testing at any volume. Three of my
> own checking tools were wrong before they were right, all in the same direction — too confident
> about what they were measuring. The suite is at 1,282 tests and I trust it considerably less than I
> did at 400.

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
