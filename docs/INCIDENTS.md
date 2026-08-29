# Engineering Journal — Incidents

Real breakages, written down **while they were still broken**.

This file is a Definition-of-Done item on every build phase (workflow.md §17.1).
It is not a retrospective. Razorpay's application form asks *"What broke, and how
you got out"* and states that it is **the answer they read first** — an answer
reconstructed the night before submission reads like fiction, because it is.

## Rules

1. Write the entry **while the thing is broken**, not after it is fixed.
2. Include the **wrong theory** you held first, and the time it cost. Debugging is
   mostly being wrong first; an account with no wrong turn is not a real account.
3. State the root cause at **design altitude** — a flaw, not a typo.
4. Name the **regression test** that keeps it fixed.
5. Say what it **changed about the architecture**. Not "I learned to check my
   assumptions" — something like "the two-phase outbox exists because of this."
6. Never invent an incident. An empty section below is honest; a fabricated one is
   the fastest way to lose a payments company's trust.

## Entry template

```markdown
## INC-00N · YYYY-MM-DD HH:MM IST · One-line symptom

**Phase:** N (name)
**Symptom:** What was observably wrong.

**Wrong theory (N min lost):** What I assumed, what I did about it, how I found out
it was wrong.

**Root cause:** The actual design flaw.

**Fix:** What changed, with `path/to/file.py:LINE`.

**Why it stayed fixed:** `test_name` asserts the invariant.

**What I actually learned:** The generalisation, and which section of workflow.md
it changed.
```

---

## Incidents

<!-- Append new entries below, newest last. Do not pre-write entries. -->

## INC-001 · 2026-08-29 · Enum CHECK constraints were never created

**Phase:** 1 (data model)

**Symptom:** `test_invalid_enum_value_is_rejected` failed with `DID NOT RAISE`. A raw
`UPDATE recovery_cases SET status = 'NOT_A_STATUS'` was accepted by the database.

**Wrong theory (~10 min lost):** assumed the test was wrong — that SQLAlchemy would
reject the value at the ORM layer, and that going around the ORM with raw SQL was an
unfair test. It is not unfair: the outbox drainer and the reconciler both issue direct
UPDATEs by design, so the ORM is not the only writer.

**Root cause:** I wrote the docstring in `db/models.py` claiming enums were "VARCHAR
with a CHECK constraint" *before* verifying one existed. `SQLAlchemy.Enum` defaults
`create_constraint=False`, so `native_enum=False` produced a bare VARCHAR with **no
constraint at all**. The schema looked like it enforced the enum and enforced nothing.

**Fix:** `create_constraint=True` in the `_enum()` helper
(`apps/api/app/db/models.py:70`). All 24 enum columns now carry a real CHECK.

**Why it stayed fixed:** `test_invalid_enum_value_is_rejected` attacks the column with
raw SQL rather than through the ORM, so it tests the database rather than the mapper.

**What I actually learned:** two things. A docstring is not a constraint — I asserted a
guarantee in prose and shipped nothing behind it, and only a test that tried to violate
it caught the gap. And the real hazard was specific: an unconstrained `status` column
could hold a value the state machine has no branch for, so a case would silently stop
being processed rather than fail loudly. This is why §16.2 insists tests assert
invariants by attacking them, not by asserting that the happy path works.

## INC-002 · 2026-08-29 · Seeded "historical" rows landed in the future

**Phase:** 1 (seed corpus)

**Symptom:** `test_all_timestamps_precede_the_anchor` failed — the newest attempt was
`2026-09-01 16:43 UTC`, well after the `09:00 IST` anchor the corpus is built around.

**Wrong theory (~5 min lost):** suspected a timezone conversion error in `UtcDateTime`,
since the failure printed a UTC datetime against an IST one. The type was fine; the
comparison was correct and the *data* was wrong.

**Root cause:** the timestamp helper wrote
`ANCHOR - timedelta(days=offset, hours=-(hour - 9))`. Negating the hours term to shift
time-of-day is a trick, and with `offset = 0` and `hour = 23` it produces
`ANCHOR + 14h`. So "up to 60 days ago" could mean 14 hours from now.

**Fix:** subtract whole days (minimum 1) and then `.replace()` the time-of-day
(`apps/api/app/db/seed.py:_build_attempts.ts`). `day_offset >= 1` makes the result
strictly earlier than the anchor for every hour in range, with no clamping needed.

**Why it stayed fixed:** `test_all_timestamps_precede_the_anchor` asserts the bound over
the whole corpus, not a sample.

**What I actually learned:** arithmetic on a datetime to move *time-of-day* is the wrong
tool — `replace()` says what it means and cannot overshoot. Worth more than the bug: an
unnoticed future timestamp would have silently corrupted the rail-health index (a
"1-hour success rate" computed over rows dated in the future) and the recovery-window
checks. Reproducibility tests earn their keep by catching data faults, not just
non-determinism.

## INC-003 · 2026-08-29 · A free-text field could unblock a fraud control

**Phase:** 3 (deterministic classifier)

**Symptom:** `test_never_acts_autonomously_on_a_risk_block` failed on two golden-set
cases. The classifier marked payments with `error_source=business` -- meaning the
merchant's own risk rules rejected them -- as **recoverable**. The agent would have
issued a fresh payment link for a payment its own fraud controls had blocked.

**Wrong theory (~15 min lost):** assumed the golden-set labels were too strict, and that
"blocked after a risk check timed out" was arguably a rail fault worth retrying. Sat with
the consequence instead: if the retry succeeded, we would have collected money on a
transaction the business deliberately refused. That is not a labelling disagreement, it is
the system defeating its own control.

**Root cause:** substring matching on `error_reason` was allowed to override
`error_source`. `payment_blocked_after_risk_check_timeout` contains "timeout", so it
matched RAIL_FAULT; `mandate_presented_but_blocked_by_risk` contains "mandate", so it
matched MANDATE_INVALID. The design treated the reason string as "more specific evidence",
which is right for ordinary disagreements and catastrophically wrong when one of the
signals is an authorisation decision rather than a description.

**Fix:** `error_source=business` is now an authoritative gate checked before any reason
matching can run (`apps/api/app/agent/classifier.py`, the SAFETY GATE block). No reason
string can reverse it. Separately, `_REASON_MARKERS` was reordered by specificity, since
the same flaw made `otp_entry_timed_out_by_user` -- a person giving up -- read as a rail
outage.

**Why it stayed fixed:** `test_no_reason_string_can_unblock_a_business_block` feeds a
reason containing every rail, funds and mandate marker at once and asserts the verdict is
still RISK_BLOCKED. `test_never_acts_autonomously_on_a_risk_block` runs over the whole
golden set.

**What I actually learned:** "the more specific signal wins" is a good default and a bad
absolute. Some fields are *descriptions* and some are *decisions*, and a decision must not
be outranked by a description. This is the same shape as the LLM/policy boundary the whole
architecture rests on -- a model's proposal is a description, the policy firewall's verdict
is a decision -- and I had just reproduced, one layer down, the bug the architecture exists
to prevent. Both failing cases were ones I had *predicted* in the golden set as expected
misses; I had written them off as accuracy noise rather than noticing one was a safety
property.

## INC-004 · 2026-08-29 · The corpus contained no degraded rail to find

**Phase:** 3 (rail health)

**Symptom:** Running the classifier and rail-health index over the real committed corpus,
the Ananya hero case produced `alternative: none confidently better -> reissue same rail`.
The demo script in workflow.md narrates "HDFC UPI success fell to 42%, so switch rails". In
the actual data HDFC UPI was at **72.7%, above the 65.4% baseline** -- not degraded at all.

**Wrong theory (~10 min lost):** suspected the Wilson lower bound was too conservative and
that `best_alternative` was refusing a switch it should have made. It was not: with HDFC
above baseline there was genuinely nothing better to switch to, and the refusal was
correct. The code was right and the *data* did not contain the scenario.

**Root cause:** the seed generator sprinkled failures uniformly at random across every
rail. Real rail failures are **bursty** -- a bank has a bad three hours -- and uniform
sprinkling produces a corpus in which no rail is ever meaningfully worse than any other.
The product's central claim is that it detects a degraded rail and routes around it, and
the corpus could not exercise that claim.

**Fix:** a declared HDFC UPI outage window in `db/seed.py` -- 18 bank-side authorisation
timeouts concentrated into 3 hours, taken **out of** the existing 96-failure budget rather
than added on top. HDFC UPI is now 41.0% over 39 attempts and the index recommends
upi/ICICI at 87.1%. The seed prints the scenario on every run so it can never become a
hidden assumption.

**Why it stayed fixed:** the seed output states the scenario explicitly, and section 12.5
records it as scenario design.

**What I actually learned:** two things. Synthetic data has to contain the phenomenon the
product exists to handle, or the tests pass while proving nothing -- the data-side version
of a mock that is more permissive than reality (DEC-009). And the line between legitimate
scenario design and rigging is not fuzzy: designing the corpus to *contain a realistic
degraded rail* is legitimate and is declared; tuning it until the recovery rate hits a
target would be rigging, and is exactly what section 16.2 forbids.

Related finding, recorded because it will matter in Phase 12: "degraded" is measured
*relative to the baseline*, so during a broad outage nothing looks degraded -- everything
is equally bad and the baseline collapses with it. Measured across windows at the outage
peak: 14 days -> degraded, 24 hours -> degraded, 3 hours -> **not** degraded (rail 10.0%
against a baseline that had itself fallen to 16.7%). The 24-hour default is what keeps
enough healthy history in frame for the comparison to mean anything.

## INC-005 · 2026-08-29 · A deferral that never advanced the clock

**Phase:** 4 (stopping rules)

**Symptom:** The property-based termination proof failed on its first run.
`test_advancing_the_clock_always_reaches_a_terminal_state` walks a case forward by
repeatedly jumping to whatever instant the engine asked it to wait for; hypothesis found a
context where the engine deferred to an instant **that had already passed**, so the walk
never advanced and the assertion `defer_until > now` tripped.

**Wrong theory (~10 min lost):** assumed hypothesis had generated an impossible context and
that the right fix was to constrain the strategy — `contacts_24h = 1` with a
`last_contact_at` more than 24 hours old is self-contradictory, so a real caller would
never produce it. Then worked out how a real caller produces it anyway: the count and the
timestamp are **two separate queries**. Under load, or with any clock skew between the
application and the database, the count can be read before a contact ages out and the
timestamp after. Constraining the test would have hidden a production bug behind an
assumption about inputs.

**Root cause:** S-04 computed `defer_until = last_contact_at + 24h` without checking that
the result was in the future. When the counter and the timestamp disagreed, the rule
deferred to a moment in the past. The scheduler would then re-evaluate immediately, the
same rule would fire with the same already-passed instant, and the case would spin — a
busy loop in the component whose entire purpose is guaranteeing termination.

**Fix:** two layers, deliberately.
1. `s04_contact_cap_24h` now returns PROCEED when the computed release has already passed,
   trusting the timestamp over the counter: if the last contact was more than 24 hours ago,
   a 24-hour cap cannot be binding.
2. `evaluate()` drops any deferral that does not move the clock forward, whatever rule
   produced it (`apps/api/app/guardrails/stopping_rules.py`, the engine-level guard).

**Why it stayed fixed:** `test_defer_always_carries_a_future_instant` and
`test_advancing_the_clock_always_reaches_a_terminal_state` both run over 2,000 generated
contexts per CI run.

**What I actually learned:** the fix I nearly made was to the *test*. Narrowing the
strategy would have produced a green suite and left a livelock in the termination
guarantee — the exact failure mode the track bar's "stopping rules" requirement is asking
about. Two lessons worth keeping. When a property test finds an "impossible" input, work
out how production produces it before deciding it cannot; distributed reads of related
values disagree routinely. And a safety invariant should be enforced *structurally* rather
than in each rule: the engine-level guard means a future thirteenth rule cannot reintroduce
this bug, which per-rule diligence alone could not promise.

## INC-006 · 2026-08-29 · The safety proof proved nothing

**Phase:** 5 (policy firewall)

**Symptom:** None. That is what makes this the most important entry in the file.
Twenty-five property tests asserting that the policy firewall is closed all passed on the
first run, and the suite was green.

**How it was caught:** not by a failing test. By deliberately sabotaging the firewall —
deleting the NaN/infinity guard from the discount clamp — and re-running the proof. **It
still passed.** A NaN discount could reach the applied action and the "proof" did not
notice.

**Root cause:** every invariant is written as `if verdict is PASSED: assert ...`. Measuring
the actual verdicts showed **100% of 1,500 generated contexts were BLOCKED at the first
gate**, so no assertion ever executed. With around eight independent block conditions
(kill switch, opt-out, consent, window, attempt budget, contact caps, control arm,
non-positive amount) each firing roughly half the time, the chance of a random context
surviving to the clamping code is under 0.1%. Randomness alone was never going to find that
path — it had to be constructed.

A second instance of the same flaw hid behind the first. The properties of the form *"if X
then it must be refused"* were also vacuous: they ran against the hostile strategy, where
everything is refused for a dozen other reasons. Deleting the CONTROL-arm block entirely did
not fail `test_the_control_arm_never_executes`, because something else was blocking every
example. The test never observed the code it was named after.

**Fix:** three changes.
1. A `viable_contexts` strategy that reaches the clamping code by construction, keeping
   random exactly what the invariants are about — the discount (still NaN, infinite,
   negative, 10,000%), the expiry, consent, the amount.
2. One isolating strategy per refusal, viable in every respect except the single condition
   under test, so each property fails if and only if its own guard is removed.
3. `TestTheProofIsNotVacuous` — coverage guards asserting that a meaningful share of
   examples reach PASSED, that the clamps are exercised, and that tokens are actually
   minted. Built from a seeded RNG, because a measurement should give the same answer every
   run.

**Why it stayed fixed:** re-ran the sabotages. Removing the NaN guard now fails 6
properties; removing the discount ceiling fails 15; allowing the CONTROL arm to act fails
its own property. Before the fix, all three passed.

**What I actually learned:** a green property test is not evidence until you have watched it
go red. Guarded assertions (`if precondition: assert ...`) fail open — when the
precondition is never met they are indistinguishable from passing, and the stronger the
guard, the more likely the test is silently dead. Two habits came out of this and are now
standing practice in this repo: **sabotage every safety test at least once**, and **assert
the coverage the proof depends on**, so it cannot decay quietly.

The uncomfortable part: I had already written this exact lesson twice — the meta-test in
`test_no_wall_clock_reads.py` and `test_accuracy_is_not_suspiciously_perfect` in the
classifier baseline — and still shipped a vacuous proof in the module where it mattered
most. Knowing the failure mode is not the same as checking for it.

## INC-007 · 2026-08-29 · The same field in two places, disagreeing

**Phase:** 5 (policy firewall)

**Symptom:** Three unit tests failed at once. A proposal explicitly marked
`message_class=MARKETING` for a consenting customer had its discount silently stripped to
zero, and the recorded reason was "transactional message cannot carry an offer" — for a
message that was not transactional.

**Wrong theory (~5 min lost):** assumed the consent-downgrade logic was firing when it
should not, and started tracing S-08. S-08 was correct. It was reading a different value
than the one the test set.

**Root cause:** the proposed message class and discount existed in **two places** —
`RecoveryProposal` (what the model suggested) and `StoppingContext` (what the stopping
rules evaluate) — and the caller was responsible for keeping them in sync. The policy
engine then read the stopping context's copy, which still held its default. Two sources of
truth for one fact, with nothing enforcing agreement.

**Fix:** the proposal is now copied into the stopping context inside `evaluate_policy`
before the rules run (`apps/api/app/guardrails/policy_engine.py`, step 0). The caller can
no longer supply a disagreeing pair, because the caller no longer supplies it at all.

**Why it stayed fixed:** `test_ananya_recovers_without_a_discount` and
`test_an_excessive_discount_resets_to_the_safe_default` both depend on the proposal's class
reaching the rules.

**What I actually learned:** duplicated state does not announce itself; it waits for the
two copies to diverge and then produces a correct-looking answer to the wrong question. The
telling detail was the *reason string* — it said "transactional" about a message the test
had marked marketing, and that mismatch was the whole bug visible in one line. Error
messages that quote the value they acted on pay for themselves.
