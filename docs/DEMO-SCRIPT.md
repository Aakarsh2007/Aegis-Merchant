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

Ten clips. Timecodes are cumulative so you can check your pace.

The narration is **695 words across five minutes — about 139 words a minute**, which is comfortable
clear speech. That is measured, not estimated: the first draft of this script ran to 1,586 words,
which is 317 words a minute and physically unspeakable. `tests/test_pitch_script_is_accurate.py`
now fails if any segment cannot be read in the time it is given.

So do not rush. If you find yourself hurrying, you have drifted off the script.

---

### `0:00 – 0:25` — The number every recovery tool shows you

**DO**

1. Dashboard scrolled to the panel headed **"Where the money went"**. The **MONEY THAT ARRIVED**
   row, the two rows beneath it, and the `residual: ₹0.00 ✓ balances` line should all be in frame
   at once.
2. Nothing moves. Hold the frame — the viewer is reading four numbers, and one of them is a zero
   that matters.

**SAY**
> Every recovery tool shows one number: money recovered.
>
> Three lakh forty-one thousand arrived here. Two lakh came in on a path we drove. One lakh
> thirty-nine thousand arrived on its own — and we credited ourselves **nothing**.
>
> Of that two lakh, sixty thousand is what we can defend.
>
> A tool that bills you for the rest is charging for the weather.

---

### `0:25 – 0:55` — Why the smaller number is the honest one

**DO**

1. Dashboard open, scrolled to the panel headed **"Did it actually recover money?"**
2. Nothing moves. Hold this frame and talk.

**SAY**
> Why is that number smaller?
>
> **Because people pay without us.** Thirty-nine of these customers were never contacted — that's
> the control group — and twenty-three percent of them paid anyway.
>
> So the treated group's twenty-nine percent isn't our achievement. The **six-point gap** is.
>
> We can't claim every payment we saw. Only the difference. And holding that control group back
> costs the merchant real recovery — we give up money on purpose, so the number we do report means
> something.

---

### `0:55 – 1:25` — The question we cannot answer

**DO**

1. Scroll to the panel headed **"What we have not proven"**.
2. Let the two progress bars sit on screen — `control 39 / 796` and `treated 171 / 796`.

**SAY**
> And there's a question I can't answer. Did we *cause* those payments?
>
> Four levels. Verified — Razorpay confirms it. Eligible — it passes all six attribution rules.
> Claimable — yes.
>
> **Incremental — not reached.** The lift is six points, but p equals nought point four four —
> indistinguishable from chance at this sample size.
>
> Settling it needs seven hundred and ninety-six control cases. I have thirty-nine, and the design
> was pre-registered before any of this data existed.

---

### `1:25 – 2:00` — One case, and who decided it

**DO**

1. Scroll to the **Cases** table near the bottom.
2. Click **Held as control**. All thirty-nine appear, greyed, with a dash where an action would be.
   Hold two seconds.
3. Click **All**, then click the case id **`RC-0001`**.
4. The case page opens with three numbered sections. Scroll slowly through all three.

**SAY**
> One case. Ananya, four thousand two hundred and ninety-nine rupees.
>
> What Razorpay reported: bank, authorization, timeout. Their telemetry, not our guess.
>
> What we concluded, and who concluded it: rail fault, ninety-five percent — decided by the **rule
> table**, not a model. A bank timeout isn't a customer problem, so retrying the same rail is the one
> thing you must not do. That's a lookup, not a judgement call.
>
> And every hash on that page recomputes.

---

### `2:00 – 2:40` — AI proposes. Policy disposes.

**DO**

1. Back to the dashboard. Scroll to **"Where the AI stops"**.
2. Then the **"Try to make it do something dangerous"** panel beside it.
3. Click all five buttons, one at a time, waiting for each result before the next.

**SAY**
> So where is the model? It handles forty of a hundred and ninety-nine diagnoses — the ones where
> Razorpay sent no error fields at all.
>
> We benchmarked it against the rule table. Ninety-point-six against ninety-six-point-five. The
> model lost, so we didn't use it there.
>
> And it cannot touch money. Five attacks, real policy engine. Ninety percent off — escalated.
> Market to a do-not-disturb customer — neutralised. Kill switch off — refused.
>
> Charge more than they owe? Not blocked. **Unrepresentable.** The proposal object has no amount
> field. That's not a guardrail; it's an absence.

---

### `2:40 – 3:20` — Prove it against real Razorpay

**DO**

1. Scroll to **"Prove it against real Razorpay"**, with the **Live pipeline** panel visible.
2. Click **Create a real ₹1 recovery link**. Let the seven nodes fill in — pause for them.
3. Click **Open the Razorpay link and pay ₹1**.
5. Card `4111 1111 1111 1111`, any future expiry, any CVV → **Success**.
6. Alt-tab to the tunnel terminal to show the inbound `POST`, then back to the dashboard and reload.

**SAY**
> Everything so far is a seeded corpus. This isn't.
>
> *(clicking)* One click — real agent, real firewall. Watch the pipeline name which layer decided
> each step. Diagnose says *rule table, no model call*.
>
> And a real Razorpay link. Paying it now.
>
> *(on the tunnel log)* Razorpay's webhook, arriving here. Signature verified, reference matched.
> That proves the execution path — not a lift in behaviour.
>
> Hover the tile: one rupee by webhook, one by reconciliation. That second one is there because a
> tunnel died and the delivery failed. **A lost webhook cost us nothing.**

**If the webhook does not arrive:** stop the clip and fix it — don't narrate around it. Run
`python tasks.py reconcile`, which polls Razorpay and records the payment anyway. That is a
legitimate and interesting shot in its own right: *"the webhook didn't make it, so the reconciler
went and asked"* is arguably stronger than the happy path. The usual cause is the tunnel URL in the
Razorpay dashboard not matching the tunnel you have running — check that first. **Do not re-enter
the webhook secret**; that was a wrong diagnosis we made once and it wasted an hour.

---

### `3:20 – 3:50` — What it chose not to do, and breaking it on purpose

**DO**

1. Scroll to the **morning briefing** — the "Good morning, GlowKart" block.
2. Then the **Stopping rules** panel. Hold long enough to read a few rows.
3. Then **Audit chain**. Click **Edit a payload**, then **Re-verify**.

**SAY**
> Every other panel shows actions taken. This one shows restraint — twenty-two held for quiet
> hours, eleven opted out.
>
> Twelve stopping rules, and all twelve are listed including the ten that fired zero times. A brake
> that didn't fire and a brake that doesn't exist look identical if you only show the non-zero rows.
>
> *(clicking Tamper, then Re-verify)* Now let me break the audit chain. One field — **valid: false**,
> and it names the block.

---

### `3:50 – 4:20` — What broke

**DO**

1. Open `docs/INCIDENTS.md` in your editor. Scroll from the top so the sheer length registers.
2. Stop on **INC-026**. Then scroll to **INC-032**.

**SAY**
> Forty-two incidents, each with the part that matters: why no test caught it.
>
> The one to read is INC-026. A metrics table with a reader and no writer — the panel showed zero
> forever, and the test passed *because* the feature was missing.
>
> The pattern across most of them: **a green test that can't tell working from absent.** So every
> new test now gets deliberately broken, to confirm it can fail.

---

### `4:20 – 4:50` — Does the architecture earn its complexity?

**DO**

1. Terminal: `python tasks.py benchmark`. It finishes instantly.
2. Let the **MEASURED** table sit on screen while you talk.

**SAY**
> Does the architecture earn it? Same corpus, five policies.
>
> Contacting everyone breaches a hard bound **three hundred and eight times**. RevPilot: **zero** —
> and both arms recover the same amount, so in this corpus the firewall cost no recovery.
>
> Last row: remove the holdout, recover the most of any policy, and claim none of it.

> **The breach counts are measured, not simulated.** They are real counts over the corpus's own
> consent data. Only the recovery column comes from the response model. Say that if asked; the
> command prints it too.

---

### `4:50 – 5:00` — Run it yourself

**DO**

1. A clean terminal. Type `git clone <your repo URL>` and let it run.
2. `cd Aegis-Merchant` then `python tasks.py demo`. Let it run at real speed — about forty seconds.
3. When the dashboard opens, scroll once to the **cost** panel at the bottom, then stop recording.

**SAY**
> One command. No Docker, no API key, same numbers every run.
>
> We don't claim every payment we see. We claim only what the evidence lets us prove.

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
| Money that arrived | ₹3,41,780.70 | Say "three lakh forty-one thousand seven hundred and eighty" — the total, and the only figure that is a sum |
| ├ recovered on our path | ₹2,02,759.95 | 42 cases. Say "two lakh two thousand seven hundred and sixty" |
| └ arrived organically | ₹1,39,020.75 | 17 cases, credited ₹0.00. The two rows above add to the total exactly |
| Incremental estimate | ₹60,216.66 | Say "sixty thousand two hundred and seventeen". **An estimate, not a slice** — 17.6% of what arrived |
| Razorpay verified | ₹2.00 | **Becomes ₹3.00 after your demo payment** |
| Treated conversion | 29.2% | 50 of 171 · CI 22.9–36.4% |
| Control conversion | 23.1% | 9 of 39 · CI 12.7–38.3% |
| Absolute lift | 6.16 pp | **p = 0.44**, CI on the difference −8.7 to +21.0 pp. Not significant, and say the p-value, not "the intervals overlap" |
| Cases held as control | 39 | Clickable via the "Held as control" filter |
| Unsafe proposals intercepted | 33 | S-07 opt-out: 11 · S-09 quiet hours: 22 |
| Awaiting a human | 19 | Only if the batch ran within four hours |
| Experiment progress | 13.2% overall | 210 of 1,592. Control 4.9% (39/796) · treated 21.5% (171/796). Control is the binding arm |
| Cases still needed | 1,382 | To reach 1,592 total |
| Rule table vs model | 159 / 40 | Of 199 diagnoses |
| Model accuracy measured | 90.6% vs 96.5% | Model lost to the rule table, so the table ships |
| Inferences | 398 | 55.3% served from the committed cache · 0 live calls |
| Actual spend | ₹0.00 | Projected at paid rates: ₹1.28 |
| Audit chain | valid | **Don't quote a block count** — it changes on every batch run. Say "valid", and let the screen show the number. |
| Tests | 1,257 | ruff, mypy --strict, tsc, eslint all clean |
| Incidents · decisions | 42 · 46 | `docs/INCIDENTS.md` · `docs/DECISIONS.md` |
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

---

## 6 · Notes to yourself while recording

Kept out of the script above so the narrative reads without interruption. Read these once
before you start; do not read them on camera.

- **Why this is at thirty seconds and not four minutes.** A judge will find this limitation whether or not you mention it. Saying it before you show eight panels of things that work is the difference between honesty and damage control. Say it calmly — it is a strength.

- **This is your best fifty seconds.** Rehearse it twice before recording. Keep the tunnel terminal visible in a corner, or alt-tab to it when the webhook lands.

- **This is the segment the judges said they read first.** Their form asks "what broke, and how you got out". Do not rush it and do not soften it.

- **Pause two seconds before you stop recording.** This is the whole pitch. If a judge stops watching at thirty seconds, this is what they keep.

- **Let the screen carry the numbers.** Speak the insight; the dashboard shows the evidence.
  If a figure is legible on screen, you do not need to read it aloud.

- **Pause after each segment's last sentence** before you stop recording. Half a second of
  silence cuts cleanly; a clipped word does not.
