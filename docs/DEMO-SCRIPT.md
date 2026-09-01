# RevPilot — the five-minute pitch video, word for word

Razorpay AI Buildathon 2026 · Track 03, AI Revenue Recovery · run time **5:00**

Everything you say is written out. Everything you click is numbered. Read the **SAY** blocks aloud;
do the **DO** blocks with your hands.

Every figure in this file was read off the running system on 1 September 2026, not remembered. **If
the dashboard disagrees with this file on the day, the dashboard is right** — say what is on screen.

---

## 1 · Before you press record

### Recording software

You are on Windows 11, so you already have both tools you need.

| Tool | Use it for | How |
|---|---|---|
| **OBS Studio** (free, obsproject.com) | Recording screen + voice | Preferred — it gives you a mic level meter, which the Game Bar does not. |
| **Clipchamp** (built into Windows 11) | Joining the eight clips | Search "Clipchamp" in Start. Drag clips onto the timeline in order, export 1080p. |

**Set up OBS once:**

1. **Settings → Video** — Base and Output resolution both `1920×1080`. FPS `30`.
2. **Settings → Output** — Recording Quality `High Quality, Medium File Size`. Format `mp4`.
3. **Settings → Audio** — Mic/Auxiliary = your headset. Set **Desktop Audio to Disabled** — you do
   not want notification pings in the take.
4. **Sources → + → Display Capture.** With two monitors, capture only the one the dashboard is on.
5. Speak a test line and watch the meter. Aim for green, peaking into yellow. Never leaving green →
   raise gain. Hitting red → lower it.

> **Audio matters more than video.** Judges forgive a plain screen recording; they do not forgive
> audio they have to strain at. Use wired earbuds with a mic rather than the laptop's built-in one —
> the laptop mic sits next to the fan. Record in the smallest room you have, door shut, something
> soft nearby (a bed, curtains, a sofa). Mic about a hand's width from your mouth and slightly to
> the side, so plosives don't thump.

### Screen preparation

- Browser at **100% zoom** (`Ctrl` + `0`). Hide the bookmarks bar (`Ctrl`+`Shift`+`B`).
- Close every other tab. Windows **Focus assist** on, so no notifications appear.
- Terminal font size **up two or three steps**. What is readable to you at 30 cm is unreadable in a
  1080p video.
- Dark or light theme — either is fine, but pick one and stay in it for all nine clips.

### Pre-flight, in this order

Two of these will silently ruin a take if skipped.

- [ ] **Terminal A:** `python tasks.py demo` — wait for the dashboard to open.
- [ ] **Check the "Needs a human" panel is not greyed out.** Approvals carry a four-hour TTL. If
      they have aged out, run `python tasks.py batch` and reload. **Nineteen live approvals** is what
      you want.
- [ ] **Terminal B:** `python tasks.py tunnel` — **and leave this window open.** The tunnel dies with
      the window and its URL changes every run. This is the step that cost us a lost webhook once
      already.
- [ ] **Copy the tunnel URL into Razorpay.** Dashboard → Settings → Webhooks → edit → URL =
      `<tunnel>/api/v1/webhooks/razorpay` → Save. Leave the secret and event ticks alone; confirm
      `payment_link.paid` is ticked.
- [ ] **Open a third tab** on the Razorpay dashboard, logged in — so the payment page loads instantly
      on camera instead of showing a login screen.
- [ ] **Verify the chain is valid before you film breaking it.** Open
      `localhost:8000/api/v1/audit/verify` — expect `"valid": true`.
- [ ] **Read the whole script aloud once** without recording. You will find two sentences that don't
      sit right in your mouth. Change those words — they are yours to change.

> **Record nine separate clips, one per segment. Do not attempt one take.** Fluff a line → stop,
> breathe, redo that clip only. Then join them in Clipchamp in order. Nobody can tell, and it turns a
> stressful hour into nine easy minutes.

> **Do not speed anything up.** A sped-up terminal tells a judge the real thing was slow. Every
> command here finishes in under ten seconds; the batch takes about five.

---

## 2 · The script

Nine clips. Timecodes are cumulative so you can check your pace. The narration is about 700 words —
five minutes at a normal speaking pace. Resist the urge to rush.

---

### `0:00 – 0:20` — This is real Razorpay, right now

**DO**

1. Dashboard already scrolled to **"Prove it against real Razorpay"**, with the **Live pipeline**
   panel visible.
2. Click **Create a real ₹1 recovery link**. Let the seven nodes fill in.
3. Cut here. You will pay it in the 2:35 segment — this opener exists to establish, in the first
   twenty seconds, that the integration is real.

**SAY**

> This is a real Razorpay payment link, created two seconds ago through the live API.
>
> Watch the pipeline on the right: enrich, triage — twelve of twelve stopping rules clear —
> diagnose, and it says *rule table, no model call*. Strategise. Policy: passed, zero clamps.
> Execute.
>
> Every step names which layer decided it. That's the whole architecture, running, in one click.
>
> Now — here is why the two lakh rupees on this dashboard is **not** the number I'm claiming.

> **Why this is first.** A judge's unconscious first question is *"is any of this real?"*. Answering
> it in twenty seconds buys you the attention to make the harder argument that follows. This is the
> one change a reviewer pushed hardest for, and they were right.

---

### `0:20 – 0:50` — The number that is wrong everywhere else

**DO**

1. Dashboard open, scrolled to the panel headed **"Did it actually recover money?"**
2. Nothing moves. Hold this frame and talk.

**SAY**

> Every payment-recovery tool shows a merchant one number: money recovered. Here it's two lakh two
> thousand, seven hundred and sixty rupees.
>
> That number is real, and it is the wrong number — because some of those customers would have come
> back on their own. A tool that bills you for them is charging you for the weather.
>
> So this system holds back thirty-nine cases and never contacts them at all. The treated group
> converts at 29.2%. The untouched group converts at **23.1%**. Which means the money we can actually
> claim is sixty thousand, two hundred and seventeen rupees — **about thirty percent** of what a
> dashboard would show.
>
> The smaller number is the one on the bigger tile. And the API will not return the first figure
> without the second.

> **Pause two seconds before you stop recording.** This is the whole pitch. If a judge stops watching
> at thirty seconds, this is what they keep.

---

### `0:50 – 1:18` — And the number that is still missing

**DO**

1. Scroll to the panel headed **"What we have not proven"**.
2. Let the two progress bars sit on screen — `control 39 / 796` and `treated 171 / 796`.

**SAY**

> There's a third question, and I have not answered it. Did RevPilot actually *cause* additional
> customers to pay?
>
> No. Not proven. That sixty thousand is a simulation — real machinery, declared customer responses.
>
> Answering it properly needs one thousand five hundred and ninety-two cases at a balanced split. I
> have two hundred and ten, which is **4.9%** of the control arm I'd need. That's not a number I
> invented for this video: the experiment is pre-registered in the repo, committed *before* any of
> this data existed, and it states what result would make me abandon the hypothesis.
>
> What's blocking it isn't code. It's a merchant with the traffic, and DLT registration in *their*
> name — which takes weeks.

> **Why this is at thirty seconds and not four minutes.** A judge will find this limitation whether
> or not you mention it. Saying it before you show eight panels of things that work is the difference
> between honesty and damage control. Say it calmly — it is a strength.

---

### `1:18 – 1:52` — One case, and who decided it

**DO**

1. Scroll to the **Cases** table near the bottom.
2. Click **Held as control**. All thirty-nine appear, greyed, with a dash where an action would be.
   Hold two seconds.
3. Click **All**, then click the case id **`RC-0001`**.
4. The case page opens with three numbered sections. Scroll slowly through all three.

**SAY**

> *(on clicking "Held as control")* These are the thirty-nine we deliberately never touched. A
> holdout you can only read about is indistinguishable from one that doesn't exist, so you can click
> it.
>
> *(on opening RC-0001)* One case. Ananya, four thousand two hundred and ninety-nine rupees.
>
> Section one: **what Razorpay reported.** Error source, bank. Error step, authorization. Reason,
> bank timeout. Not our guess — their telemetry.
>
> Section two: **what we concluded, and who concluded it.** Rail fault, ninety-five percent
> confidence, decided by *deterministic fallback*. That means the rule table answered this, not a
> model. A bank timeout is a rail problem, not a customer problem — so retrying the same rail is the
> one thing you must not do, and that's a lookup, not a judgement call.
>
> Section three: **the audit chain for this case.** Every hash there is recomputable, and it records
> which arm the case was in.

---

### `1:52 – 2:35` — AI proposes. Policy disposes.

**DO**

1. Back to the dashboard. Scroll to **"Where the AI stops"**.
2. Then the **"Try to make it do something dangerous"** panel beside it.
3. Click all five buttons, one at a time, waiting for each result before the next.

**SAY**

> Here is where the model is, and where it isn't. Three bands: deterministic facts at the top, the
> model in the middle, and deterministic authority at the bottom — where the model never reaches.
>
> Across the seeded corpus the rule table settles a hundred and fifty-nine of a hundred and
> ninety-nine diagnoses. The model gets the other forty — the ones where Razorpay sent no error
> fields at all and there is genuinely nothing to look up. I measured the model against the rule
> table: it scored 90.6% against the table's 96.5%. So the table ships, and the model gets the cases
> the table admits it's unsure about.
>
> *(clicking through the five attacks)* And the model cannot touch money. Five attacks, through the
> real policy engine — not a mock. Ninety percent off: **escalated**, clamped to the ceiling and held
> for a human. Market to a do-not-disturb customer: **neutralised** — the marketing class was
> stripped to transactional. Act with the kill switch off: **refused**.
>
> And charge more than the customer owes — the answer isn't "blocked", it's **unrepresentable**. The
> object the model fills in *has no amount field*. There is no number for it to raise. That's not a
> guardrail; it's an absence.
>
> The fifth one passes, and that's deliberate. A firewall that refused all five would score perfectly
> here and be useless.

---

### `2:35 – 3:15` — Pay it, and watch Razorpay confirm it

> **This is your best fifty seconds.** Rehearse it twice before recording. Keep the tunnel terminal
> visible in a corner, or alt-tab to it when the webhook lands.

**DO**

1. Back to **"Prove it against real Razorpay"** — the link you created in the opener is still
   there.
2. Click **Open the Razorpay link and pay ₹1**.
5. Card `4111 1111 1111 1111`, any future expiry, any CVV → **Success**.
6. Alt-tab to the tunnel terminal to show the inbound `POST`, then back to the dashboard and reload.

**SAY**

> Back to the link from the opening. Everything since has run on a seeded corpus. This doesn't.
>
> I'm paying it now.
>
> *(after paying, on the tunnel log)* That's Razorpay's webhook arriving at my machine. HMAC
> verified. The reference ID matches the action we took — so the money is attributable to us and not
> to luck.
>
> *(reload the dashboard)* One rupee. And it sits on its own tile, badged Razorpay-verified, separate
> from the two lakh — because a signed webhook and a simulation are different kinds of evidence, and
> averaging them would make both worthless.
>
> Hover that tile and it tells you *how* each rupee was proven: one by signed webhook, one by direct
> API reconciliation. That second one is there because a tunnel died mid-test and the webhook was
> lost. The reconciler asked Razorpay directly and picked the payment up. A lost webhook cost us
> nothing.

**If the webhook does not arrive:** stop the clip and fix it — don't narrate around it. Run
`python tasks.py reconcile`, which polls Razorpay and records the payment anyway. That is a
legitimate and interesting shot in its own right: *"the webhook didn't make it, so the reconciler
went and asked"* is arguably stronger than the happy path. The usual cause is the tunnel URL in the
Razorpay dashboard not matching the tunnel you have running — check that first. **Do not re-enter
the webhook secret**; that was a wrong diagnosis we made once and it wasted an hour.

---

### `3:15 – 3:45` — What it chose not to do, and breaking it on purpose

**DO**

1. Scroll to the **morning briefing** — the "Good morning, GlowKart" block.
2. Then the **Stopping rules** panel. Hold long enough to read a few rows.
3. Then **Audit chain**. Click **Edit a payload**, then **Re-verify**.

**SAY**

> Every other panel shows actions taken. This one shows restraint: *"I did not contact twenty-two
> customers who were in quiet hours, and eleven who have opted out."* An agent that reports what it
> refused to do is the only kind you can audit.
>
> Twelve stopping rules, and all twelve are listed *including the ten that fired zero times* —
> because a brake that didn't fire and a brake that doesn't exist look identical if you only show the
> non-zero rows. Quiet hours held twenty-two actions. Opt-out is permanent and checked before
> everything else.
>
> Termination isn't asserted, it's proved — a property test generates hostile inputs and checks every
> case reaches a terminal state.
>
> *(clicking Edit a payload, then Re-verify)* And every decision is a block in a SHA-256 hash chain.
> Let me break it. One field, one block — **valid: false**, and it names the block that diverged. A
> verifier nobody has watched fail is indistinguishable from one that always returns true.

---

### `3:45 – 4:20` — What broke

> **This is the segment the judges said they read first.** Their form asks "what broke, and how you
> got out". Do not rush it and do not soften it.

**DO**

1. Open `docs/INCIDENTS.md` in your editor. Scroll from the top so the sheer length registers.
2. Stop on **INC-026**. Then scroll to **INC-032**.

**SAY**

> Thirty-eight incidents, each written up with the part that matters: why no test caught it.
>
> The one I'd point you at is **INC-026**. The panel showing how many model calls were made, and what
> fraction came from cache, displayed zero. Forever. On every clone. Because the table had a reader
> and no writer — nothing in the entire codebase ever inserted a row. And the test passed *because*
> the feature was missing: it queried an empty table and got the zeros it expected.
>
> Fixing it exposed the next one within the hour: the committed response cache had a structurally
> guaranteed zero percent hit rate, because two code paths built the model's input differently and
> the cache key is a hash of that input. The two bugs had been hiding each other.
>
> And **INC-032** is the one that still bothers me. A routine command — `tasks.py batch` — ran an
> unfiltered delete and destroyed every Razorpay-verified recovery in the database. Silently. That's
> how this project lost its *first* live verification, which I'd written off as my own carelessness.
> It was a bug.
>
> The pattern across most of them is one thing: **a green test that cannot tell working from
> absent.** So every new test now gets deliberately sabotaged — I break the thing it covers and
> confirm the test fails. That step has caught vacuous tests *inside* the fixes for vacuous tests,
> twice.

---

### `4:20 – 4:40` — Does the architecture earn its complexity?

**DO**

1. Terminal: `python tasks.py benchmark`. It finishes instantly.
2. Let the **MEASURED** table sit on screen while you talk.

**SAY**

> One more thing, because "we built a safety layer" is easy to say. Same corpus, five different
> decision policies.
>
> Contacting everyone the way most recovery scripts do breaches a hard bound **three hundred and
> eight times** — opted-out customers contacted, marketing without consent, messages inside TRAI
> quiet hours. RevPilot: **zero**.
>
> And here's the result I expected least. Remove the firewall and recovery doesn't go *up* — both
> arms recover exactly the same amount. The clamps change *how* an action is taken, not whether. So
> the safety layer costs nothing.
>
> Last row: remove the holdout and it recovers the most of any policy — and can claim none of it.
> Attribution: unavailable.

> **The breach counts are measured, not simulated.** They are real counts over the corpus's own
> consent data. Only the recovery column comes from the response model. Say that if asked; the
> command prints it too.

---

### `4:40 – 5:00` — Run it yourself

**DO**

1. A clean terminal. Type `git clone <your repo URL>` and let it run.
2. `cd Aegis-Merchant` then `python tasks.py demo`. Let it run at real speed — about forty seconds.
3. When the dashboard opens, scroll once to the **cost** panel at the bottom, then stop recording.

**SAY**

> One command. No Docker, no Postgres, no Redis, no Kafka, and no API key required.
>
> Forty seconds and you have the dashboard I just showed you, with the same numbers — because the
> model's responses are committed to the repo and content-addressed, so it replays instead of
> re-billing.
>
> Actual inference spend: **zero rupees**, on a free tier plus that cache. The projection at
> published paid rates is one rupee twenty-eight, and it's labelled *estimated*, because a price list
> is not a bill.
>
> Eleven hundred and seventy-nine tests. Thirty-nine written incidents.
>
> We don't claim every payment we see. We claim only what the evidence lets us prove. Thank you.

---

## 3 · After the recording

1. Open **Clipchamp**. Drag the nine clips onto the timeline in order.
2. Trim the dead air at the start and end of each clip — leave about half a second.
3. **No music, no titles, no transitions.** A cut is fine. Music competes with your voice and judges
   are listening to the words.
4. **Export → 1080p.** Check the total is at or just under 5:00.
5. Upload to YouTube as **Unlisted** — *not Private*. Judges cannot open a private video.
6. Watch it once, all the way through, with the sound on. This is the only way to catch a clip that
   recorded silence.

> **The one check people skip.** After uploading, open the unlisted link in a private browsing window
> — logged out. If it plays there, the judges can watch it. If it asks you to sign in, you set it to
> Private by mistake.

---

## 4 · Every figure you will say aloud

Read off the running system. **If the dashboard disagrees with this table on the day, the dashboard is
right** — say what is on screen.

| Figure | Value | Note |
|---|---|---|
| Gross recovered | ₹2,02,759.95 | Say "two lakh two thousand seven hundred and sixty" |
| Net incremental | ₹60,216.66 | Say "sixty thousand two hundred and seventeen" |
| Razorpay verified | ₹2.00 | **Becomes ₹3.00 after your demo payment** |
| Treated conversion | 29.2% | 50 of 171 · CI 22.9–36.4% |
| Control conversion | 23.1% | 9 of 39 · CI 12.7–38.3% |
| Absolute lift | 6.16 pp | Not statistically significant — the panel says so |
| Cases held as control | 39 | Clickable via the "Held as control" filter |
| Unsafe proposals intercepted | 33 | S-07 opt-out: 11 · S-09 quiet hours: 22 |
| Awaiting a human | 19 | Only if the batch ran within four hours |
| Power completion | 4.9% | 39 of 796 control · 171 of 796 treated |
| Cases still needed | 1,382 | To reach 1,592 total |
| Rule table vs model | 159 / 40 | Of 199 diagnoses |
| Model accuracy measured | 90.6% vs 96.5% | Model lost to the rule table, so the table ships |
| Inferences | 398 | 55.3% served from the committed cache · 0 live calls |
| Actual spend | ₹0.00 | Projected at paid rates: ₹1.28 |
| Audit chain | 213 blocks | Valid before you tamper with it |
| Tests | 1,149 | ruff, mypy --strict, tsc, eslint all clean |
| Incidents · decisions | 39 · 45 | `docs/INCIDENTS.md` · `docs/DECISIONS.md` |
| Benchmark: naive breaches | 308 | **Measured**, not simulated — `python tasks.py benchmark` |
| Benchmark: RevPilot breaches | 0 | Measured |
| Benchmark: firewall removed | 284 breaches | Same recovery as RevPilot — safety costs nothing |
| Benchmark: holdout removed | ₹2,86,013 | Highest recovery of any arm · attribution **unavailable** |
| Evidence snapshot | `docs/EVIDENCE.md` | Every figure above, generated from one run |

---

## 5 · If a judge asks

Short answers. State the number, then the caveat, then stop talking.

**"Is the two lakh real money?"**
No, and the dashboard says so. It's a seeded corpus through real machinery — the same attribution
rules, the same arm assignment, the same arithmetic. The customer *responses* are a declared
parameter, and the badge says simulated. The only figure badged Razorpay-verified is the rupees, and
those have signed webhooks behind them.

**"So you've recovered three rupees."**
Correct, and I'd rather say that than inflate it. Two lakh of machinery, three rupees of proof, and
they're on separate tiles so you can tell which is which.

**"You're claiming a lift you haven't measured."**
Correct, and the dashboard says so before it says anything else. What *is* measured is that the
machinery computing the lift works on real provider data: the arm assignment is recomputable from the
case id, a treated settlement matches a reference we issued, and a control settlement resolves as
organic rather than being credited to us. What is not measured is customer behaviour, and the
pre-registration says exactly what would measure it.

**"Why not run it on a few real people and report that?"**
Because thirty cases gives a confidence interval about forty points wide. Publishing that would
destroy the only thing that makes this submission worth reading. `python tasks.py power` prints the
arithmetic: 796 per arm at the effect size I'm assuming.

**"Where does the AI actually do anything?"**
Two places. It diagnoses the forty of a hundred and ninety-nine cases where Razorpay sent no error
fields, so there's nothing to look up. And it argues for a strategy, which the playbook then
overrides if the action is forbidden. It does not choose amounts, it cannot send anything, and it has
no field to change a rupee figure with.

**"What's the weakest part?"**
The response model in the simulation is mine, so the sixty thousand is only as good as that
assumption and I can't validate it without real traffic. Second is tail truncation in the audit
chain: the hash chain detects any edit to history, but a chain cut short at the end verifies clean.
That's written down in `audit.py` rather than left for someone to find.

**"How is this different from any other payment-recovery agent?"**
Most of them can tell you what they recovered. This one can tell you whether the recovery belongs to
it. Thirty-nine cases are deliberately never contacted, so there is a counterfactual; the gross
figure and the claimable figure are different numbers on the same screen; and `tasks.py benchmark`
shows what happens when you remove that holdout — recovery goes up and attribution becomes
impossible. The architecture pattern is becoming common. The evidence discipline is the part that
isn't.

**"You could just add a voice agent like the others."**
I could, and I deliberately didn't. Delivery is not the hard problem here — attribution and safe
autonomous financial action are. Adding voice would mean DLT and telephony dependencies, more
compliance surface, and demo risk, in exchange for feature parity on the part of the problem that is
already solved.

**"What would you do next?"
Get it in front of one real merchant with enough volume to make the holdout arm significant.
Everything else — the firewall, the audit chain, the stopping rules — is built to survive that. The
statistics are the only part that needs traffic I can't simulate.
