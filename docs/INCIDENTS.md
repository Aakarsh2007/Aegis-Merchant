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

## INC-008 · 2026-08-29 · The model in the plan could not be called, again

**Phase:** 6 (LLM adapter)

**Symptom:** First live call with the newly-supplied key: `404 NOT_FOUND. This model
models/gemini-2.5-flash is no longer available to new users.`

**Wrong theory (~2 min):** assumed a bad key, since a 404 on a well-known model looked
like an auth problem wearing a disguise. The key was fine — it authenticated, and a 404 is
a model error, not an auth error. Reading the message rather than pattern-matching the
status code settled it in one step.

**Root cause:** `gemini-2.5-flash` is retired for new API keys. This is the *second* time
this project has had a hard-coded model retired underneath it: the original plan specified
Gemini 1.5 Flash (retired), v3.1 "corrected" that to 2.5 Flash, and 2.5 Flash is now gone
too. Correcting a stale model name to another stale model name is not a fix.

**A second finding, worse than the first:** the models-list endpoint is **not
authoritative**. It cheerfully lists `gemini-2.5-flash` among 53 models — including ones
that return 404 or 503 on use. Of the flash candidates, only three actually worked.
Listing a model is not the same as being able to call it, and only a real call tells the
truth.

**Fix:** the model is config (`GEMINI_MODEL`), `CANDIDATE_MODELS` records the probed
alternatives, and `GeminiAdapter.usable_models()` probes by *calling* rather than by
listing. The next retirement is a config change instead of an investigation.

**What I actually learned:** a model name is not a stable identifier and should never be
treated as one. More generally: when an API offers both a directory and a real call, the
directory is a hint and the call is the fact. I had written "verify against live docs" into
the workflow for Razorpay endpoints and then trusted a listing for models.

## INC-009 · 2026-08-29 · The latency budget was fiction

**Phase:** 6

**Symptom:** Every working model measured 2.7-8.4 s median on the free tier, against a
plan that budgeted **1,400 ms p95 with a 2,500 ms timeout**. At those settings essentially
every live call would have timed out and fallen back, and the system would have looked
like it had an LLM while never actually using one.

**Root cause:** the budget was written before there was anything to measure, against a
model that no longer exists. It was an assumption formatted as a specification.

**Fix:** measured properly (warm, n=5 per model), then set `llm_timeout_s = 12.0` and
recorded the real figures in §4.6. Reasoned about whether ~4 s is acceptable rather than
just relaxing the number until it passed: the webhook is acknowledged in ~7 ms and
diagnosis runs in a background task, so nobody is waiting on it. A customer whose payment
just failed does not perceive a difference between a link in 4 s and one in 400 ms.

**What I actually learned:** a performance target written before the first measurement is a
guess with a decimal point. The tell is that it was suspiciously round — 1,400 ms and
2,500 ms are numbers someone chose, not numbers anyone observed.

## INC-010 · 2026-08-29 · A model comparison that measured the fallback

**Phase:** 6

**Symptom:** The first model comparison reported **100.0% for all three candidates**. A
result that good should have been the first clue.

**Caught by:** reading the columns next to the headline. `fell_back: 16, 24, 23` out of 25 —
most "model" answers had come from the deterministic fallback. The 100% was the rule table
being measured while wearing the model's name.

**Root cause:** two independent bugs producing one flattering number.
1. The rate limiter's `try_acquire` returns False rather than waiting, and the adapter
   degrades on refusal. Correct on the webhook path — waiting a minute for a token would
   be worse than answering deterministically — and completely wrong for an offline
   benchmark, where waiting is free.
2. The subset was `cases[:25]`, and the golden set is ordered with the `clean` band first.
   So the comparison ran entirely on the easy cases, which is exactly where the rule table
   already scores 100% and where no two systems can be distinguished.

**Fix:** `wait_for_slot_s` on the adapter (non-zero only for the offline warm-up),
stratified sampling across difficulty bands, and a contamination guard that prints
`UNUSABLE` and refuses to report any accuracy computed with a non-zero fallback count.

**What I actually learned:** the sanity check on a benchmark is not "is the number good"
but "could this number have come from something other than what I think I measured". Both
bugs pushed the result in the flattering direction, which is the direction one is least
inclined to interrogate. The guard now makes that interrogation automatic, because I will
not reliably do it by hand at 2 a.m. before a submission.

## INC-011 · 2026-08-29 · A constraint the model was never told about

**Phase:** 6

**Symptom:** Live calls to `gemini-3.1-flash-lite` fell back with `schema validation failed
twice`, while the raw response was obviously fine.

**Root cause:** the response was valid JSON with a sensible diagnosis, and `reasoning` was
279 characters against a `max_length=240`. My Pydantic-to-Gemini schema translator
propagated types and enums but **silently dropped `maxLength`** — so the provider was never
told the bound, the model had no way to honour it, and a good diagnosis was discarded in
favour of the rule table.

**Fix:** two parts.
1. Propagate `maxLength`, `minLength`, `minimum`, `maximum`, `maxItems` into the provider
   schema. A constraint the provider is never told is a constraint the model cannot honour.
2. A narrow, recorded repair: string fields with a declared `maxLength` are trimmed and
   re-validated before falling back. Deliberately narrow — numbers and enums are never
   coerced, because a wrong category must fail rather than be massaged into shape. Losing a
   correct diagnosis over a few characters of prose is the wrong trade; losing one over a
   wrong category is the right one.

**Bonus finding in the same investigation:** the translator was also forwarding Pydantic
`description` fields, which are our docstrings. That took the DIAGNOSE prompt from 41 to
465 input tokens — an 11x increase on a free tier, to restate what the system prompt
already says at length. Dropped.

**What I actually learned:** a translation layer between two schema languages fails
*silently and asymmetrically*. It dropped a constraint (invisible until a model exceeded
it) and added 400 tokens of noise (invisible until someone looked at a usage counter).
Neither showed up as an error. Round-tripping the translated schema and asserting the
bounds survived would have caught both, and there is now a test that does exactly that.

## INC-012 · 2026-08-29 · Two uniqueness domains that disagreed about case

**Phase:** 8 (outbox)

**Symptom:** None yet — found by probing live Razorpay Test Mode before writing the outbox,
rather than by a failure.

**What I was checking:** DEC-009 claims the mock provider rejects a duplicate
`reference_id` "exactly as Razorpay does", and the entire two-phase outbox rests on that.
I had never confirmed Razorpay actually does it. It does — sending the same reference twice
returned `payment link with given reference_id ... already exists`, and the existing link
was retrievable.

**What the error message gave away:** it echoed my `rvp_RC-TEST01_1` back as
`rvp_rc-test01_1`. Following that up established the exact semantics:

| behaviour | result |
|---|---|
| storage | **case-preserving** — reads back as `rvp_RC-TEST01_1` |
| lookup | **case-insensitive** — a lowercase query finds the uppercase link |
| uniqueness | **case-insensitive** — two case-variants cannot both exist |

**The latent bug:** our own `UNIQUE(reference_id)` in SQLite is case-*sensitive*, while
Razorpay's is case-*insensitive*. Two references differing only in case would pass our
constraint and then be rejected by the provider — a confusing failure with no local trace,
and one that would only appear once case IDs happened to collide that way.

**Fix:** `reference_id()` now emits lowercase. Both uniqueness domains become identical, so
the asymmetry cannot arise. Fixed at the source rather than by normalising at each
comparison site, because there is more than one comparison site (our UNIQUE, the attribution
matcher, the outbox lookup) and missing one is exactly how this class of bug survives.

**What I actually learned:** an error message is evidence about implementation. The
lowercasing was incidental to the failure I was testing, and it was the most informative
thing in the response. More generally: when two systems both enforce "the same" constraint,
the interesting question is not whether they agree on the happy path but whether their
*equivalence classes* match — case, whitespace, and unicode normalisation are where they
usually do not.

## INC-013 · 2026-08-29 · The crash-recovery path crashed

**Phase:** 8 (outbox reconciler)

**Symptom:** `test_the_reconciler_resumes_a_crash_between_phases` failed with
`AttributeError: 'str' object has no attribute 'value'`, deep inside the token signature.

**Wrong theory (~5 min):** assumed the token module had a bug, since that is where the
traceback ended. It did not. The traceback ended there because that is the first place the
bad data was *used*; it was produced two frames earlier.

**Root cause:** the reconciler reads an outbox row committed before the crash and rebuilds
the action from JSON. It reconstructed `AppliedAction` field by field, which left the enum
fields as plain strings — and computing the signature then called `.value` on a `str`. The
serialise and deserialise halves lived in different modules, and only one of them knew the
types.

**Fix:** `AppliedAction.from_payload()`, next to its inverse `as_payload()`, with three
round-trip tests: the action survives, **the content hash survives**, and enums come back as
enums. The hash one matters most — a human approves a hash, so if deserialisation changed
it, an approved action could never be matched back at execution time.

**What I actually learned:** this is the one code path that only executes *after something
has already gone wrong*, which makes it the least likely to be exercised by accident and the
most costly to have broken. It was found only because the crash-recovery test simulates a
real crash rather than mocking around it. And the structural lesson: a serialise/deserialise
pair belongs in one place with a round-trip test, because when they live apart they drift
silently and the failure surfaces somewhere unrelated.


## INC-015 · Four guesses at a string, and not the one Razorpay sends

**Symptom:** none. That is the point of this entry.

The deterministic classifier returned the correct category for a real captured
cancellation, the golden set scored 96.5%, and 558 tests passed. Nothing was
red.

**What was actually wrong:** `_REASON_MARKERS` carried four invented spellings
of a customer cancellation — `user_cancel`, `cancelled_by`, `canceled_by`,
`authentication_cancelled` — and Razorpay Test Mode actually returns
`payment_cancelled`, which matches none of them. Every genuine cancellation
missed the reason table entirely and fell through to the `(error_source,
error_step)` fallback.

**Why it survived:** the fallback happens to map
`(customer, payment_authentication)` to `AUTHENTICATION_ABANDONED`, which is
the right answer. The classifier reached a correct verdict by a route it did
not intend, from evidence it did not have. A test asserting only the category
passes on the broken code — which is why the tests added here assert the
*evidence*, not the outcome.

**What it cost, concretely:**

| | before | after |
|---|---|---|
| confidence | 0.85 (source+step) | 0.95 (exact) |
| stated reasoning | "**No usable error_reason**" | "reason='payment_cancelled'. All three agree." |

The reasoning string is written into the audit chain and shown to the operator.
It asserted that Razorpay sent no usable reason when Razorpay had sent a precise
one. A human reviewing that case would have been told the provider was vaguer
than it was — a false statement in an audit record, which is worse than a wrong
number because it is not checkable from inside the system.

The understated confidence did not change behaviour here (`needs_llm_review`
triggers below 0.6, and 0.85 clears it), but it was luck, not design: the same
gap on a category with a threshold nearer 0.85 would have sent every real
cancellation for an unnecessary LLM review.

**Root cause:** the marker table was written from documentation and reasoning
about what Razorpay *would* plausibly call things. Four variants were invented
for one event, which felt like thoroughness and was actually four guesses with
no ground truth behind any of them. Breadth is not evidence.

**Fix:** added `payment_cancelled`, the string observed live, placed in the
customer-agency block so mandate cancellations keep priority (a mandate retry
burns a scheme re-presentation). Entries are now annotated by provenance, so
the next reader can tell which are grounded and which are still guesses.

**Regression test:** four tests in `tests/test_classifier.py`, pinned to
`payment.failed.captured.json` rather than to a literal we chose — if Razorpay
changes the string, they fail. One asserts the fixture is still
`provenance: captured_test_mode`, because a fixture that quietly reverted to
`documented_shape` would make the others vacuous while still green (INC-006).
Verified by sabotage: removing the marker fails exactly the two tests that
should fail, and the other 43 stay green — which is the demonstration that the
old suite could not see this.

**What was learned:** the golden set measured *accuracy against inputs we
wrote*. Our inputs and our classifier shared an author, so they shared his
assumptions, and the score was partly a measurement of that agreement. One real
payment from the provider found in ninety seconds what 96.5% on 200 constructed
cases could not. The `documented_shape` provenance marker was carrying real
risk, exactly as the fixtures README warned it was.

**Related:** INC-003 (the same table, ordered wrongly), INC-006 (green tests
proving nothing), INC-008/INC-012 (both also found only by calling the real
provider).


## INC-016 · The held message that would have been sent to a dead case

**Symptom:** none yet, because no message has ever been held in production. Found by
reading the drainer's query while wiring the Phase 11 sweeper, not by anything breaking.

**What was wrong:** S-09 defers a quiet-hours message by setting
`outbox.next_attempt_at` to 09:05 IST. The drainer collects work with

```sql
WHERE status = 'PENDING' AND next_attempt_at <= now
```

and **nothing checked whether the case was still alive**. A message deferred at 22:00 for
a case whose 24-hour recovery window closed at 03:00 would be sent at 09:05 — a fresh
payment link, six hours after the case was over.

**Why it is worse than a dropped message.** A drop loses one opportunity. This *spends*
one of the two contacts S-05 permits, messages someone about a payment we are no longer
pursuing, and produces a link attributable to nothing: no case is in `MONITORING`, so
attribution condition 4 rejects the resulting webhook and the money — if it arrives —
counts as organic. We would have paid the full compliance and goodwill cost of a contact
and been structurally unable to claim the result.

**Why nobody caught it earlier.** Quiet hours were tested as a *predicate* — the wrapping
21:00/09:00 window, IST versus UTC, month rollover, merchant-editable bounds — and every
one of those tests passes. The predicate was never wrong. What was missing was any test
that followed a message *through* the deferral, and the gap only exists because quiet
hours can hold a message for eleven hours while a 24-hour window is running.

**Root cause:** two components each correct in isolation. S-09 decides *when* to send;
the drainer decides *what* is due. Neither was responsible for "is this still worth
sending", so neither did it.

**Fix:** `Scheduler._kill_stale_deferrals` marks a queued send DEAD before the drainer can
reach it, when either the case has reached a terminal state or the release time is past
`window_expires_at`. Both conditions are audited with the reason.

**Regression test:** `tests/test_quiet_hours_roundtrip.py` follows one message across all
four components at every hour of the day. The load-bearing pair is
`test_a_message_held_at_2200_is_due_at_0905` (held, not dropped) and
`test_a_hold_that_outlives_the_window_is_cancelled_not_sent` (cancelled, not sent) — a
sweep that killed everything would pass the second alone, and a sweep that killed nothing
would pass the first alone. Verified by sabotage: stubbing the sweep to return 0 fails
exactly three tests.

**What was learned:** testing a predicate is not testing the behaviour the predicate exists
to produce. "Never drop a message" is a claim about four components, and it was only ever
tested in one of them. The tests that found this are the ones that follow a thing through
a system rather than asserting a function returns the right value.


## INC-017 · Every SSE connection would have crashed, and no test would have known

**Symptom:** `TypeError: unhashable type: '_Subscriber'` — raised on the *first* line of the
first SSE test, before a single assertion ran.

**What was wrong:** the subscriber record was a plain `@dataclass`. Python's dataclass
defaults to `eq=True`, and defining `__eq__` sets `__hash__ = None`. The `EventBus` keeps
subscribers in a `set`, so `set.add()` raised on every connection.

**Severity, stated plainly:** `GET /api/v1/stream/events` would have returned 500 for every
client, always. The live pipeline feed is roughly a third of the demo, and it was completely
broken in a way that type-checking and linting both passed.

**Why mypy and ruff missed it.** Nothing is type-incorrect: `set[_Subscriber]` is a
well-formed type, and hashability is a runtime protocol, not a static one. `mypy --strict`
was clean across 65 modules with this bug present. It is a good reminder of what a green
type-check does and does not license.

**How it was caught:** by writing tests for the bus at all. There was no clever technique —
the tests simply exercised the object rather than the wire format. Had the SSE tests only
asserted response headers, the mock would have passed and the endpoint would have 500'd in
front of a judge.

**Fix:** `@dataclass(eq=False)`, which restores identity-based equality and hashing. That is
also the semantically correct choice independently of the crash: two subscribers with
equally-full queues are not "the same subscriber", and identity is exactly the notion a
connection registry wants.

**Regression test:** the whole of `tests/test_stream.py` — every test that calls
`bus.subscribe()` fails without the fix, which is fifteen of them.

**A second thing the same session found:** driving the endpoint through `TestClient.stream()`
*hung* rather than failed. The generator loops until `request.is_disconnected()`, which
TestClient never sets, so the test ran until the 400-second timeout. A hanging test tells you
nothing, so those tests now drive `_event_stream` directly with a stub request — which also
tests our logic instead of the client library's streaming semantics.

**What was learned:** `mypy --strict` clean and `ruff` clean is not evidence that an object
works. Both tools were completely satisfied by a class that could not be put in a set.


## INC-018 · One enum value meaning two incompatible things, and a lift of minus sixty percent

**Symptom:** the dashboard's first live render showed **net incremental of −₹5,91,178** and a
lift of **−60.5%**, where Phase 9 had measured +6.2% and +₹60,217 over the same corpus.

**Wrong theory, five minutes lost:** that the batch runner's response model was
mis-parameterised. It was not — the parameters were byte-identical to Phase 9's.

**Root cause.** `RESOLVED_ORGANIC` was carrying two incompatible meanings.

`agent/nodes.py::_blocked_status` maps a control-arm block to `RESOLVED_ORGANIC` with the
comment *"a control-arm case is doing its job"* — meaning **held as a holdout**.
`services/attribution.py` reads the same value as **settled without our involvement**, which
is what it means everywhere else in the system.

So every control case that was merely *held* was counted as a case that *paid*:

| | before | after |
|---|---|---|
| control settled | 35/39 = **89.7%** | 9/39 = **23.1%** |
| treatment settled | 50/171 = 29.2% | 50/171 = 29.2% |
| lift | **−60.5%** | **+6.2%** |

Treatment was correct throughout. Only the control arm was wrong, and it was wrong in the
direction that makes the product look *worse* — which is the only reason it was noticed
rather than shipped.

**Why the tests did not catch it.** `tests/test_attribution.py` builds `CaseOutcome` objects
directly with an explicit `paid` boolean, so it never exercises the inference from case
status. The Phase 9 measurement script computed `paid` from its own in-memory simulation
rather than by reading a status back out of the database. Nothing had ever round-tripped a
control case through *persistence* and back into the attribution population — the batch
runner was the first thing to do it, and it found the collision immediately.

**Fix:** the batch derives `acted` from the **arm**, not from the status, and writes the
terminal status from whether the customer actually paid. A control case that did not pay is
`EXPIRED` — the window closed with no payment — not `RESOLVED_ORGANIC`, because claiming
otherwise asserts a settlement that never happened.

**Regression test:** `test_control_conversion_matches_the_declared_baseline` asserts control
conversion sits near the declared 21% rather than near 90%. A test asserting only "lift is
positive" would have passed on the broken code once the sign flipped back.

**Not yet fixed, and stated as such:** `_blocked_status` still returns `RESOLVED_ORGANIC` for
a control-arm block. The batch now overrides it, so the measured numbers are right, but any
*other* consumer reading case status directly would hit the same collision. The real fix is a
distinct terminal state for "observed, never acted on", which is a schema change and is
deferred to Phase 13 rather than smuggled into a UI phase.

**What was learned:** an enum value is an interface. `RESOLVED_ORGANIC` was given a second
meaning in one module by someone reasoning locally and correctly — a held control case *is*
doing its job — and the collision was invisible until a component read it from a different
angle. The systems that caught it were the ones that persisted data and read it back, not the
ones that constructed objects in memory.


## INC-019 · An error shape Razorpay documents, and one it actually sends

**Symptom:** `AttributeError: 'str' object has no attribute 'get'` from the Razorpay client,
while investigating why `subscription.charged` was missing from the webhook event list.

**Root cause.** Razorpay documents its errors as
`{"error": {"code": ..., "description": ...}}`, and the client assumed that shape:

```python
error = payload.get("error", {})
description = str(error.get("description", "")).lower()   # <- explodes
```

A 401 for a product that is **not enabled on the account** returns
`{"error": "Unauthorized"}` — the value is a plain string, so `.get` does not exist.

**Why it is worse than a crash.** `AttributeError` is neither `ProviderRetryable` nor
`ProviderPermanent`, so it escaped the outbox's entire classification path. The executor
catches those two and decides retry-or-dead-letter; an unclassified exception propagates past
both, leaving the outbox row in `SENDING` — the exact state the reconciler exists to clean up,
reached by a route the reconciler was never designed for. A payment action could have been
stranded by an error-message format.

**How it was found.** Not by a test. By calling `/subscriptions` on the real account to check
whether Subscriptions was enabled, because the user reported a missing webhook event. The
answer was "no, it is 401" — and getting that answer crashed our client.

**Fix:** both shapes are handled, plus a list, plus a missing key, plus a non-JSON body. Every
path now produces a classified provider error.

**Regression test:** `test_every_error_shape_is_classified_not_crashed`, parametrised over
four bodies, and `test_a_non_json_error_body_is_classified` for an HTML page from a proxy.
Verified by sabotage: restoring the assumption fails exactly the two shapes it cannot handle.

**Related finding, not a bug:** `subscription.charged` is absent from the dashboard's event
list because Subscriptions is not enabled on this Test Mode account — `/subscriptions` and
`/plans` return 401 while `/payment_links` returns 200 on the same credentials. The
subscription playbook is exercised entirely by the seeded corpus and needs no live
subscription; only the *live webhook* for it is unavailable, and that is a property of the
account rather than of the code.

**What was learned:** an API's documented error shape is the shape it returns when it is
working. The interesting responses — the ones from a disabled product, a proxy, a rate
limiter — are exactly the ones nobody documents, and they arrive on the error path where the
handling is least exercised.


## INC-020 · Every log line the application wrote at runtime went nowhere

**Symptom:** a signature diagnostic added specifically to debug a failing webhook did not
appear in the log. The rejection happened — the HTTP 401 was there — but the `log.warning`
immediately before it produced nothing.

**Root cause:** uvicorn configures the `uvicorn.*` loggers and leaves the **root logger
without a handler**. Application loggers propagate to root, find no handler, and the record
is discarded. Eight call sites were affected: the approval-TTL sweeper's expiry counts, the
outbox drainer's retry notices, the SSE bus's dropped-event warning, the fault-injection
audit line, and the webhook diagnostics.

**Why it stayed hidden for fourteen phases.** One warning *did* appear — the `API_TOKEN is
not set` line — and it appears on every boot, at the top of the log, where it reads as proof
that logging works. It is emitted inside `create_app`, while the application is being built,
**before** uvicorn installs its logging config. Everything after startup vanished.

That is the worst available arrangement. Silence would have prompted a look; a single
convincing line at the top prompted nothing.

**Fix:** `_configure_logging` attaches a `StreamHandler` to the root logger if it has none,
and sets the level from `settings.debug`. Guarded on "if it has none" so a real deployment
with its own logging configuration is left alone.

**What it cost:** the webhook debugging session below. Without runtime logs the only evidence
was HTTP status codes, and two entirely different rejections both return 401.

**What was learned:** "the logs show nothing" is ambiguous between *nothing happened* and
*nothing is being written*, and the difference is not visible from inside the thing you are
debugging. A single log line that appears at startup is not evidence that logging works —
it is evidence that logging worked at startup.

---

## INC-021 · Two 401s that meant opposite things, and a fix I cannot prove was necessary

**Symptom:** every Razorpay webhook delivery returned 401. Deliveries were definitely
arriving — Razorpay's Mumbai egress IPs (`52.66.76.63`, `52.66.75.174`) were in the access
log — and the payment itself had succeeded (`plink` status `paid`, `pay_TW2aaFse6F6wJw`
captured).

**What I concluded, and told the user:** the secret in the Razorpay dashboard must differ from
`RAZORPAY_WEBHOOK_SECRET`. I had verified our side thoroughly — 38 bytes, no whitespace, no
quotes, loaded correctly — and had proved our HMAC verification worked by sending a signed
request through the same public tunnel and getting `200 accepted`.

**What was actually also true:** `verify_timestamp` returns **401 as well**, on a completely
different code path, for an event outside the 300-second replay window. Razorpay retries
failed deliveries for hours, so by the time I was watching, every arriving request was a
retry of an event forty minutes old — which would have been rejected for *staleness* even
with a perfect signature.

Two rejections, one status code, no logging on either (INC-020), and they mean opposite
things: one says *your secret is wrong*, the other says *your secret is fine and this event
is simply old*.

**Resolution:** after the user re-entered the secret, a fresh payment produced
`event_id TW3Rfq6VhWiuwC`, `payment_link.paid`, `signature_valid=1`, `200 ACCEPTED`.

**The honest part:** I do not know whether re-entering the secret fixed it. The original
secret may have been correct all along and every 401 I saw may have been staleness. I asked
the user to redo work on a diagnosis I could not support, and the evidence to distinguish the
two cases did not exist until I added it.

**Fix:** the two rejections now log distinguishable messages —
`webhook signature rejected | received=… | expected=…` versus
`webhook rejected as stale | signature was VALID`. The second states explicitly that the
secret is not the problem, because that is the sentence that would have saved the session.

**What was learned:** before asking somebody to change a configuration, be able to state what
evidence would prove the change unnecessary. I could not, which means I was guessing with
someone else's time.


## INC-022 · Three of the twelve stopping rules could never fire

**Symptom:** none. Twelve rules implemented, twelve rules unit-tested, termination proved by
property test over 2,000 generated contexts per run, 878 tests green. Found by a systematic
audit, not by anything breaking.

**What was wrong.** `StoppingContext` is a dataclass with sensible defaults. The agent built
it field by field in `nodes._stopping_context` — and never set five of them:

| Field | Default it silently kept | Rule it disabled |
|---|---|---|
| `autopilot_enabled` | `True` | **S-12 kill switch** |
| `actions_today` | `0` | **S-11 merchant budget** |
| `discount_exposure_mtd_paise` | `0` | **S-11 merchant budget** |
| `promise_active` | `False` | **S-10 promise-to-pay freeze** |
| `promised_at` | `None` | **S-10 promise-to-pay freeze** |

So `s12_kill_switch` evaluated `if not ctx.autopilot_enabled` against a constant `True`,
forever. The merchant's kill switch — described in §8.2 as *"one dashboard toggle, effective
immediately"*, the control that halts everything — **could not halt anything.**

**Why every test passed.** The property tests hand `StoppingContext` objects to `evaluate()`
directly, so hypothesis sets those fields itself and the rules behave correctly. The rules
were never wrong. They were never *fed*. This is the INC-006 shape one level up: proven in
isolation, disconnected in practice, and the proof is what makes it invisible — twelve
passing rule tests read as twelve working rules.

**The compounding factor:** the defaults are all the *permissive* value. `autopilot_enabled`
defaulting to `True` means the failure mode is "the agent keeps acting", not "the agent stops".
A dead safety control that defaults to *off* announces itself in about a minute; one that
defaults to *on* is silent forever.

**Fix:** the five fields are carried on `RecoveryState`, populated by the batch from
`merchants.autopilot_enabled` and the promises table, and passed into the stopping context.
`POST /api/v1/autopilot/toggle` was also specified in §20 and missing — a control with no
operator interface, which is how it went unnoticed.

**Regression test:** `tests/test_controls.py` runs a real case through the actual graph with
autopilot off and asserts it reaches `SUPPRESSED` via `S12_KILL_SWITCH`, plus a control case
with autopilot on that must *not* be suppressed — a graph that suppressed everything would
pass the first test alone. Same for S-10 and S-11.

The systematic guard is `test_every_stopping_rule_input_is_reachable_from_state`: it reflects
over `StoppingContext`'s fields and fails if the agent does not populate one. A field added
later without being plumbed through is caught by a test rather than by an incident.

Verified by sabotage: un-wiring the five fields fails exactly those four tests.

**What was learned:** a dataclass default is a decision, and on a safety-critical input it is
a decision to disable the control. `StoppingContext` should arguably have required these
fields with no default at all, so that forgetting one is a `TypeError` at construction rather
than a rule that quietly never fires. Defaults are for values that are genuinely optional, and
"is the kill switch on" is not optional.


## INC-023 · The headline number depended on what time of day you ran it

**Symptom:** immediately after fixing INC-022, the batch produced ₹85,958 instead of
₹2,02,760, with **125 cases stopped by S-11** and **74 by S-09**. Every figure in the README
was suddenly wrong.

**First read:** the INC-022 fix had broken something. It had not. The rules were now *working*,
and they immediately exposed that the batch's model of time was wrong in two ways.

**Cause 1 — three months compressed into one instant.** The corpus spans 2026-05-31 to
2026-09-01. The batch replayed all 210 cases against a single `now`, so all 171 treated
actions counted against one day's `daily_action_budget` of 50. S-11 correctly stopped the
125th onward. The bound was right; "today" was wrong.

**Cause 2 — the batch read the wall clock.** It used `SystemClock`, so quiet hours were
evaluated against *the hour the batch happened to be run*. Running it at 21:30 IST deferred 74
cases. **The headline recovery figure moved depending on whether you demoed in the morning or
the evening**, and the ₹2,02,760 quoted in the README had been produced by a daytime run.

That is the worse of the two. A number that changes between a rehearsal and a demo is not a
measurement, and nothing in the test suite could catch it — the tests inject a `FakeClock`, so
they were immune to the exact bug that would bite in front of a judge.

**Fix:** each case is now evaluated at **its own** timestamp — `attempted_at + 30 minutes`,
which is also the honest model, since a failure is learned about from a webhook rather than
instantly. Budgets are counted per `(merchant, simulated day)` and discount exposure per
`(merchant, month)`. `AgentDeps` is rebuilt per case with a `FakeClock` rather than sharing a
mutated one, because a shared clock would make the batch order-dependent in a way that is very
hard to see.

**Result:** ₹2,02,760 again, byte-identical across consecutive runs, and now identical at any
hour. S-11 no longer misfires. S-09 fires **22** times — on cases whose payments genuinely
happened at night, which is the rule doing real work on real timestamps rather than an
artefact of when the demo was recorded.

**What was learned:** injecting the clock everywhere in the *application* is worth nothing if
the *entry point* still reads the wall clock. The lint rule forbids `datetime.now()` outside
`SystemClock`; it cannot forbid handing `SystemClock` to something that should have been given
a fixed anchor. And a test suite that always injects a fake clock is structurally unable to
notice.


## INC-024 · The webhook handler stored the event and dropped it

**Symptom:** none available. A real signed Razorpay webhook had already arrived, verified, and
been stored — and the `RAZORPAY_VERIFIED` figure stayed at ₹0. Which looked exactly like the
honest answer it was supposed to be.

**What was wrong:** `_process_event` was still the Phase-2 stub. It normalised the payload,
assigned it to `_`, and returned. Fourteen phases of attribution work — the six conditions,
the settling-event set, the reference match, the window, the idempotency check — was code that
**nothing called on the live path.**

Stated plainly: every claim this project made about attribution was true of a function no
webhook ever reached.

**Why the tests did not catch it.** They test `attribute()` directly, with hand-built
arguments, and they are thorough. Not one of them went through the HTTP handler. The
webhook tests asserted 200/401/duplicate — the *acknowledgement* contract — and never that
anything happened afterwards. Two well-tested halves with no test across the join.

**How it surfaced:** building the one-click Test Mode demo, because that demo's final step is
"the dashboard shows RAZORPAY_VERIFIED" and it could not.

**Fix:** `app/ingest/settle.py`. Applies the same `attribute()` unchanged, and on a pass marks
the case RECOVERED with the **real** Razorpay event id — no `sim_evt_` prefix, so the amount
lands in the verified column rather than the simulated one. Runs in its own session, because
`BackgroundTasks` fires after the request-scoped session has closed. Never raises: a
background task that throws is a log line nobody reads and a silently unattributed payment.

**Verified on live traffic.** `case=RC-TM64210 amount=100 event=TWK4SYivi78jL4`, delivered by
Razorpay from `52.66.76.63`. `RAZORPAY_VERIFIED` moved from ₹0.00 to ₹1.00 for the first time.

**What was learned:** "the component is tested" and "the component runs" are different claims,
and the gap between them is invisible from either side. The tests were not weak; they simply
all stopped at the same seam. A single end-to-end test through the HTTP handler would have
caught this on day one, and there was none because each half looked well covered.

---

## INC-025 · Razorpay sends three entities; we read the wrong one

**Symptom:** the real webhook arrived, verified, and was recorded as
`IGNORED_UNKNOWN_EVENT`. The log said: `no reference_id: not attributable to any action of
ours`.

**Cause.** A genuine `payment_link.paid` carries **three** entities:

```
contains: ["payment_link", "order", "payment"]
  order.entity         -> id, status, amount        (no reference_id)
  payment.entity       -> id, status, amount        (no reference_id)
  payment_link.entity  -> id, status, amount, REFERENCE_ID   <- the only one
```

`_first_entity` walked a fixed priority list — `("payment", "order", "invoice",
"subscription", "payment_link", "refund")` — and returned the **first present**. `payment` is
first; `payment_link` is fifth. So it took the entity without the reference, and
`reference_id` came back `None`.

The reference is attribution condition 3, the line between attribution and coincidence. A
genuine recovery, correctly executed and correctly signed, was unattributable because the
parser read the wrong sibling.

**Why every test passed.** Our fixture had **one** entity:
`contains: ["payment_link"]`. With a single entity present, a priority list cannot pick the
wrong one. The fixture was simpler than reality in exactly the dimension that mattered — the
same failure as INC-015, in a different file, three weeks later.

**Fix, two layers.** The entity is chosen from the **event name**: everything before the first
dot names what the event is about, so `payment_link.paid` selects `payment_link`. The fixed
list survives only as a fallback for event names we do not recognise, because a webhook
Razorpay adds next year must still parse. And `_find_reference` searches **every** entity plus
its `notes` — belt and braces, because losing this field costs a recovery that actually
happened, and looking everywhere is cheap.

**Regression test:** pinned to the captured multi-entity delivery, with a test asserting the
fixture is still `provenance: captured_live_webhook` and still contains all three entities —
so a fixture that quietly reverted to a single-entity reconstruction would make the others
vacuous while staying green. Sabotage-verified: restoring the priority list fails seven tests.

**A third finding, in passing.** The `webhook_replay_tolerance_s` of 300 seconds rejected every
one of Razorpay's retries with a **valid signature**. Razorpay retries a failed delivery for
hours, so by the second attempt a legitimate event was "stale". Replay is already prevented by
`UNIQUE(event_id)`, which is strictly stronger — a timestamp is attacker-controlled data
inside a signed payload, whereas a duplicate id is refused whatever it claims. Widened to 24
hours, and the tests now assert the defence that exists rather than the one that was removed.

**This also settles INC-021.** The original webhook secret was almost certainly correct all
along; every 401 I diagnosed as a secret mismatch was this window. I asked the user to redo
configuration on a theory I could not support.

**What was learned:** a fixture is a hypothesis about the shape of reality, and a passing test
against it only confirms internal consistency. Both INC-015 and INC-025 were found in the
first ninety seconds of touching the real provider, and neither was reachable from any amount
of local testing.

---

## INC-026 · The LLM ledger had a reader and no writer

**Symptom:** "Where the answers came from" — the panel showing the LIVE / CACHED /
DETERMINISTIC split — rendered three empty bars and *"0 inferences · 0% served from cache"*.
On every clone. Since the day it was written.

**Cause.** `llm_calls` was declared as a table, registered in the model list, created by
`init_db`, indexed twice, and read by `cost_report`. Nothing anywhere in the codebase ever
inserted a row into it. Not the agent, not the batch, not a single test.

The existing cost test passed **because** the feature was missing: it built a session,
called `cost_report`, and asserted the zeros an empty table returns. It was a test of SQL
`COUNT` over no rows, written and reviewed as a test of cost accounting.

**Why no test caught it.** The graph is deliberately pure — it holds no session and returns a
value — and the persistence layer is deliberately dumb. Each half was well tested. Nothing
tested the join, because there was nothing at the join to test: the wire was never run. This
is INC-024 exactly, one subsystem over, and I did not recognise it until the dashboard was
photographed.

**Fix.** `RecoveryState` accumulates an `llm_ledger` of `LLMCallRecord`s — one per routed
decision, appended by `diagnose_node` and `strategise_node`. The batch writes them after
flushing the case. A record is written **even when no model was consulted**: a DETERMINISTIC
row is not a gap in the data, it is the measurement behind *"the rule table handled this and
no token was spent"*, which is the claim the cost panel exists to make.

**Regression test:** `tests/test_llm_ledger.py`, driven through the real `run_batch` rather
than a hand-built session, because a unit test that does not cross the boundary cannot see
this class of defect. Sabotage-verified — and the sabotage found a second problem: the
"re-running does not double the ledger" test passed at `0 == 0` with the writer deleted. A
`first > 0` guard was added. A test that cannot distinguish "correct" from "absent" is the
INC-006 pattern, and it appeared inside the fix for it.

---

## INC-027 · A deterministic answer stored as model reasoning

**Symptom:** 41 of 199 cases in the batch had `diagnosis_source = LLM` and a decision trace
reading `provenance: model`. No model had produced any of them.

**Cause.** `routing.diagnose` set `source=DiagnosisSource.LLM` whenever the adapter returned a
valid `DiagnosisOutput`. But the adapter stack degrades: cache in front, live behind it,
`DeterministicAdapter` underneath. With a cache miss and no live adapter — the batch's normal
configuration, and a judge's — the deterministic floor answers, in the same
`StructuredResult` shape, with the same schema. Routing read the shape and inferred a model.

`adapter.py`'s own module docstring states the rule this broke: *"a deterministic fallback
must never be displayed as model reasoning."* The rule was written down, tested elsewhere, and
violated in the one function that decides the label.

**The root cause is worth naming precisely: a structured output is not evidence that a model
produced it.** Every layer returns the identical shape, deliberately, so that the caller need
not care which answered. That design makes the shape useless as a signal. Only `source` can
carry it, and the code was ignoring `source`.

**Fix.** `DiagnosisSource.LLM` only when `result.source` is LIVE or CACHED. `consulted_model`
likewise — it drives the *"this came from a model"* annotation, and must be false for an
answer no model produced. CACHED counts as the model: a cached response is the model's own
words, replayed, and calling it deterministic would be the same error in the other direction.
The node's trace string now names the layer that answered rather than saying "model".

**Regression test:** parametrised over all three sources, with a stub whose *output is
identical in every case* and only `source` differs — so a test that guessed from the payload
could not pass. Also driven against the real shipping `DeterministicAdapter`, since a stub can
agree with a wrong implementation of itself.

---

## INC-028 · Twelve rules, twelve blank labels

**Symptom:** in the scanned dashboard, every one of the twelve stopping-rule rows showed a
bare identifier and no description. Earlier, all twelve read `S-0`.

**Cause, two of them.** First, `slice(0, 3)` applied to `"S-01"` yields `"S-0"` — not an
identifier, and it reads as a rendering fault in the panel whose entire job is to be checked.
Second, and worse: the description map was keyed on the Python enum's **member** names
(`S01_ALREADY_RESOLVED`), which never cross the wire. The API sends the enum's **value**,
`"S-01"`. Every lookup missed and fell back to printing the id. Two of the keys were also
wrong on their own terms — `S03_DISCOUNT_BUDGET` for `S03_DISCOUNT_ATTEMPT_BUDGET`,
`S10_PROMISE_TO_PAY` for `S10_PROMISE_FREEZE` — which is what a map written from memory rather
than from the enum looks like.

**Fix.** Keyed on the wire value. Plus `tests/test_stopping_rule_descriptions.py`, which reads
the `.tsx` file from Python and asserts the key set equals `StoppingRule`'s values exactly —
in both directions, so a stale key is caught as well as a missing one. Reading a TypeScript
file from a Python test is unusual; it is also the cheapest instrument that can actually fail
here, and the alternative (moving labels into the API) would couple presentation to the
backend for no gain.

---

## INC-029 · A cache with a structurally guaranteed 0% hit rate

**Symptom:** 81 committed cache entries, and the batch recorded zero cache hits — every one of
226 model consultations fell through to the deterministic floor. Found immediately after
INC-026 gave the hit rate a place to be seen.

**Cause.** The cache key is a SHA-256 over `(task, model, prompt_version, canonical context)`.
`warm_cache.context_for()` builds a **five**-key context: error source, step, reason, method,
playbook. The agent's `_llm_context()` builds **eight** — it adds `customer_ltv_paise`,
`customer_prior_orders` and `amount_paise`. Different context, different hash, guaranteed
miss. Not one of the 81 entries could ever match a lookup the batch makes.

The cache exists for exactly one purpose — *"the batch demo and CI run in seconds with zero
API calls and byte-for-byte reproducible numbers"* — and it could not serve that purpose at
all, in the only run that matters.

**INC-026 and INC-029 concealed each other.** The hit rate was the symptom, and the only
instrument that reports it reads a table nothing wrote to. Fixing the ledger exposed the
cache in the same afternoon.

**Fix.** `batch_cli --warm`: the same batch run, with a live model behind the cache and
recording on. The contexts are the batch's own, so a key recorded during warming is *by
construction* the key looked up later. Reconstructing contexts in a second place is what
broke; the fix removes the second place rather than trying to keep two copies in step.

**What was learned, across all four.** Every one of these was found by looking at the running
product — a screenshot, then the numbers behind it — and none was reachable from the test
suite, which was green at 938 tests throughout. Three of the four are the same defect wearing
different clothes: **a green test that cannot distinguish working from absent.** An empty
table returning zeros, a shape that every layer produces, a key that never crosses the wire.
The instrument has to be able to tell the two states apart, and for a dashboard that
instrument is a pair of eyes on the actual screen.

---

## INC-030 · The tile said "webhook"; the rupee came from a poll

**Symptom:** ₹1.00 appeared on the RAZORPAY_VERIFIED tile with a basis reading *"proven by a
**REAL signed Razorpay webhook**"*. No webhook had arrived. The rupee was proven by
`workers/reconcile` polling Razorpay's API, because the tunnel had died and the delivery
failed.

**Cause.** The basis string asserted the mechanism unconditionally. `recovery_verified_by`
holds whatever id proved the payment — an event id from a webhook, a payment or payment-link
id from a poll — and nothing recorded *which*. The badge was correct: Razorpay did assert the
payment, both ways. The sentence under it was false, on the one tile this project asks to be
taken at face value.

**A second problem, found in the same read.** The real/simulated split was decided by sniffing
an id prefix (`sim_evt_`). The comment above that constant already named the hazard: *"a
simulator that wrote a realistic-looking one would silently promote seeded outcomes to
RAZORPAY_VERIFIED."* A convention that holds only while everyone remembers it is not a
guarantee, and this one guarded the most damaging possible overclaim.

**Fix.** `RecoveryVerifier` — `WEBHOOK` / `API_RECONCILIATION` / `SIMULATOR` — as a column,
set at each of the three write sites. The verified query filters on the column **and** keeps
the prefix check as a second condition, so a bug in either one cannot promote a simulated
rupee. The basis now reports the actual split: *"1 by signed webhook, 1 by direct API
reconciliation"*.

**Regression test:** weighted towards the promotion hazard, including a SIMULATOR row whose id
is `TWSSP5BW90Y89E` — indistinguishable from a real event id — which the old prefix check would
have promoted. Sabotage-verified three ways.

---

## INC-031 · Asked to approve doing nothing, at the wrong rung

**Symptom:** an approval card reading `RC-0007 · rung A3_APPROVAL_DUAL` directly above its own
reason, *"amount ₹12,848 requires approval at rung A2_APPROVAL"*. The card contradicted itself.

**Cause.** The batch computed `trigger_rung` from its own hardcoded ₹10,000 threshold while
`trigger_reason` carried the policy firewall's actual decision. Two implementations of one
number — the INC-007 shape — and the batch's was the wrong one, because escalation is the
firewall's decision to make. Over-escalating is not a safe default: it would make a merchant
demand two signatures where policy requires one.

**A worse finding behind it.** RC-0023 was `AWAITING_APPROVAL` with `strategy: NO_ACTION`. The
case was RISK_BLOCKED, the agent correctly decided to do nothing, and a human was being asked
to *approve doing nothing*. They can neither grant nor withhold anything. The escalation ladder
was computed from the amount alone, with no regard for whether the action did anything at all.

That is not cosmetic. A queue padded with unactionable items gets rubber-stamped, and the items
that *do* matter get rubber-stamped along with them.

**Fix.** `trigger_rung` reads the firewall's `escalation_rung`. `NO_ACTION` returns
`A0_AUTONOMOUS` — the ladder governs authority to act, and doing nothing needs none. Restraint
is still reported, in the morning briefing's *"what I chose not to do"* section, which is where
a decision a human should know about but cannot act on belongs.

**The property suite then caught two things the fix had missed**, which is the best argument for
having it:

1. `test_a_fully_autonomous_action_carries_no_discount` started failing, because an applied
   action could now read *"NO_ACTION at 15% off"* — incoherent, and it would have put a
   discount figure into the audit payload and the approval hash for an action that never
   happens.
2. Zeroing that discount then broke `test_every_reduction_is_recorded`. It is now recorded as a
   non-violation clamp, because "every reduction is recorded" is a property this file is proved
   against and quietly exempting one would hollow it out.

**The exemption is proved, not assumed.** `test_no_action_can_never_move_money` asserts that a
NO_ACTION applied action carries no discount and authorises no charge beyond the order itself,
over every generated amount including ones far above the dual-signal ceiling. Without it,
exempting NO_ACTION from the authority ladder would be an unproven hole.

**Effect:** 20 approvals became 19, and rung mismatches went from every row to zero.

---

## INC-032 · A routine command deleted the money we had proved

**Symptom:** none, until it was looked for. `_clear` in the batch worker was an unfiltered
`delete(RecoveryCase)`.

**So `python tasks.py batch` — and therefore `demo` — destroyed every RAZORPAY_VERIFIED
recovery in the database.** Silently. The one figure in this system meant to be beyond argument,
deleted by the most routine command it has.

This is not hypothetical, and it is not a near-miss. It is how the first live Test Mode
verification of this project was lost: the ₹1.00 from `RC-TM64210`, gone because a database was
reset during testing. I recorded that as my own carelessness at the time. It was a bug, and the
carelessness was only the trigger.

It would have hit the user directly. The intended demo-day sequence is *make a live payment,
then run the demo* — which would have erased the payment they had just made, on camera.

**Fix.** `_clear` never deletes a case whose `recovery_verified_via` is `WEBHOOK` or
`API_RECONCILIATION`. The invariant, stated once so it can be checked: **the batch owns
simulated data and may clear it; a payment Razorpay confirmed is not the batch's to delete.**

The audit chain is still rebuilt from scratch, because the blocks are a hash chain ordered by
`block_index` and deleting an interleaved subset would leave gaps and broken links — a chain
failing verification for a reason that has nothing to do with tampering. Each preserved case
therefore gets a `case.carried_over` block in the rebuilt chain, so a verified case never
appears in the totals with no entry in the ledger. And the batch *reports* what it preserved
rather than only logging it, because the point is that the operator can see their evidence
survived.

**Regression test:** one case per real mechanism plus a simulated row that must still be
cleared — without that second half the fix could have been "never delete anything", which would
make the batch non-re-runnable and double every figure. Sabotage-verified: restoring the
unfiltered delete fails five of eight.

**What was learned, across all three.** Every one of these was found by reading the running
product rather than the test suite, which was green at 1,040 tests throughout. INC-030 came from
reading a hover caption. INC-031 came from a scanned PDF of the dashboard. INC-032 came from
asking, before re-running a command I had already run a dozen times, *what does this delete?* —
a question worth asking about any command that says it makes something "re-runnable".

---

## INC-033 · The adversarial panel said the attack succeeded

**Symptom:** running the five attacks from the dashboard, `marketing_to_dnd` displayed
**PASSED**, in the green "allowed" tone, directly beneath its own description: *"Send a
promotional discount message to a DND-registered customer."*

Found by calling every endpoint in turn rather than by reading code.

**Cause.** The panel rendered `decision.verdict` — the policy engine's answer to *"may some
action proceed?"* — as though it answered *"did the attacker get what they asked for?"* Those
are different questions, and for this attack they have opposite answers.

The system was behaving **correctly**. The message class was clamped MARKETING →
TRANSACTIONAL and the discount zeroed, so what proceeded was a transactional notice with no
discount — which is exactly right: you may still send a transactional message to a DND number,
you may not market to one. The verdict PASSED is a true statement about that residual action.

But a reader sees `PASSED` next to a described attack and concludes the firewall let it
through. On the one panel whose entire purpose is demonstrating *"AI proposes, policy
disposes"*, a label that reads as a failure is worse than a wrong number somewhere else.

**Fix.** `attack_outcome` — REFUSED / ESCALATED / NEUTRALISED / UNREPRESENTABLE /
ALLOWED_AS_ASKED — reported alongside the raw verdict rather than instead of it, with a sentence
saying what happened to the request. The panel colours on the outcome; the verdict stays
visible, because hiding it would be the opposite error.

**The first version of the fix was also wrong**, and the same kind of wrong. It inferred the
outcome from `block_reasons`, which labelled `discount_90_percent` **REFUSED** — contradicting
the panel's own note three lines below, *"the 90% request is clamped, not rejected"*. It now
keys off the engine's verdict instead of re-deriving a conclusion the engine had already
reached. Deriving a fact a second way is how the two copies disagree; that is INC-007, INC-031,
and this, three times in one project.

**`honest_baseline` returning ALLOWED_AS_ASKED is the point of that row**, and it now says so in
the payload. A firewall that refused all five attacks would score perfectly here and be
useless. A reader seeing four refusals and one pass will otherwise assume the pass is the bug.

**Regression test:** each outcome pinned individually, plus two properties over all five at
once — that only the baseline is ALLOWED_AS_ASKED, and that at least four distinct outcomes
occur, because a claim of layered controls is worth nothing if every attack trips the same
check. One test asserts the message class is *actually* downgraded, so the fix cannot become a
hardcoded string over an unfixed hole. Sabotage-verified: collapsing the outcome back into the
verdict fails five, mislabelling which row is the baseline fails three.

---

## INC-034 · Approve returned 200 and dispatched nothing

**Symptom:** approving a ₹12,848 recovery returns `200 {"status": "APPROVED"}`. The case moves to
`STRATEGY_FORMED`. An audit block is written. **Nothing is dispatched, and nothing said so.**

Found by exercising the endpoint rather than reading it.

**Cause.** Nothing in the codebase consumes `STRATEGY_FORMED`. There is no worker that executes
an approved action, and the response gave no hint of it — so a reviewer who clicked approve and
saw success would reasonably conclude a message had gone to a customer.

**Not dispatching is the correct behaviour.** The seeded corpus contains fabricated customers,
and firing a real provider call at one would be considerably worse than doing nothing. The
defect was silence about it: the human-in-the-loop reads as decorative if approval visibly
achieves nothing, and reads as *dishonest* if it implies something it did not do.

**Fix.** The response carries `dispatched: false` and a `what_happens_next` sentence saying the
authorisation was recorded and hash-pinned, that nothing was sent, why, and where to watch a
real dispatch instead (the Test Mode panel, which does call Razorpay).

**Regression test** includes `test_no_worker_consumes_strategy_formed`, which walks the source
and fails if anything starts reading that status. If someone adds an executor, the message
becomes wrong at the same moment the test breaks — which is exactly when it should be updated.

---

## INC-035 · A security banner that could not appear

**Symptom:** none visible, which is the problem. The dashboard rendered nothing about
authentication being disabled, and the absence read as "auth is on".

**Cause, in two parts.**

**Part one:** nothing rendered the auth posture at all. The API already knew — it sets
`X-Auth-Mode: disabled` on every response, warns at startup, and records
`unauthenticated_principal: true` in the audit block for every action taken that way. None of it
reached the screen. So a judge could approve a ₹12,848 recovery, see it succeed, and never learn
the ledger had attributed their decision to `anonymous(unauthenticated)`. For a project whose
claim is that every action is attributable, the actor field was silently empty on the one screen
where a human exercises authority.

**Part two, and the more interesting half.** Having written the banner, it would have *silently
never rendered*. CORS exposes only a safe-list of response headers to JavaScript —
`Cache-Control`, `Content-Language`, `Content-Type`, `Expires`, `Last-Modified`, `Pragma` —
unless the server names more in `Access-Control-Expose-Headers`. The config had
`allow_headers=["*"]`, which looks like it covers this and does not: that governs which
**request** headers a browser may send, not which **response** headers script may read. Two
similarly-named settings pointing in opposite directions.

`headers.get("x-auth-mode")` returns `null` rather than throwing, so the component would have
set mode to "unknown" and rendered nothing, forever, with no error anywhere. **A security notice
that cannot appear is worse than no notice, because its absence is read as reassurance.**

Caught by checking the headers a *browser* would receive rather than the ones curl prints.

**INC-035b, found by the test written for 035a.** The header was missing from 500 responses
entirely: the middleware returned early when a handler raised. The startup log promised "every
response is marked X-Auth-Mode: disabled", and that was false for precisely the responses a
client probing the API is most likely to see. The exception path now sets it and re-raises.

**Regression test** asserts the header is *exposed*, not merely sent; asserts the origin is
allowed, so the exposure check cannot pass vacuously against a CORS-less app; and states as an
assertion that `X-Auth-Mode` is not itself safe-listed, so the whole file cannot quietly become
a tautology. Sabotage-verified both ways.

---

## INC-036 · The holdout could be read about but not inspected

**Symptom:** `/api/v1/cases?arm=CONTROL` had existed from the beginning, with a docstring
stating its purpose plainly: *"`arm` is filterable specifically so a judge can ask for
`?arm=CONTROL` and see cases we deliberately did not act on. A control arm nobody can inspect is
indistinguishable from one that does not exist."*

**The dashboard never exposed the filter.** The cases table was hardcoded to `?limit=25`.

So the capability built specifically for a judge was reachable only by hand-editing a URL, and
the holdout — the mechanism every rupee of the incremental figure rests on — could be described
but not checked. That is the same defect as an audit chain nobody can verify, two panels down
from the audit verifier that exists precisely to avoid it.

**Fix.** Five filters on the table: All, Held as control, Treated, Recovered, Awaiting a human.
Each carries a one-line hint saying what the selection means, because "CONTROL" alone does not
tell a reader that these are the cases the agent was forbidden from touching.

**A note on the implementation.** Making the table interactive required a client component, and
the first version called `setState` synchronously in an effect body — which the lint rule caught,
correctly: it causes a cascading render on every filter click. Loading is now *derived*
(`loaded?.index !== active`) rather than tracked in a second piece of state.

---

## INC-037 · The pitch script told the presenter to click the wrong case

**Symptom:** `docs/PITCH.md` instructs the presenter, in the hero-case segment, to click
`RC-0142` while narrating *"Ananya's four thousand two hundred and ninety-nine rupee order
failed. Razorpay's own telemetry says `error_source: bank`, `error_step:
payment_authorization`, reason `bank_timeout`."*

`RC-0142` is a **₹3,551.04 `INTENT_DECAY` abandoned checkout.** The case being described is
`RC-0001`.

**Why this one matters more than its size suggests.** A wrong number in a document is
embarrassing. A wrong number a judge can watch being contradicted — the screen showing ₹3,551
and an abandoned cart while the voice-over says ₹4,299 and a bank timeout — lands in the
segment whose entire job is establishing that the figures are real. Every number afterwards
would be read differently.

**Found** by resolving the case ids in the script against the API, rather than by re-reading the
script. I wrote that script; re-reading my own work was never going to catch it.

**Fix.** The shot names `RC-0001`, verified against the corpus: Ananya, ₹4,299,
`bank / payment_authorization / payment_failed_due_to_bank_timeout`, diagnosed `RAIL_FAULT` at
0.95 by the **rule table**, one action, one audit block. The note about the earlier mistake stays
in the file, because the next person to retime the script needs to know the ids are load-bearing.

**Two more errors in the same document, found by the test written for this one:**

* The script said *"the **Where AI stops** panel"*. The panel is titled *"Where **the** AI
  stops"*. Small, and it is a presenter hunting for a heading that does not exist.
* The Test Mode segment still told the presenter to run `python tasks.py testmode-recover` in a
  terminal. There is a **button** now (INC-033's sibling fix), and a judge watching a button
  produce a real Razorpay link is a stronger shot than one watching a command. Rewritten to the
  button, with the command kept as the fallback.

**And the test's own instrument was wrong twice before it was right**, which is worth recording:

1. A raw substring search over `.tsx` reported *"What we have not proven"* as missing from the
   UI. It is authored as `What we have <span>not</span> proven`. Taken at face value, that false
   positive would have sent me to "fix" a panel that was correct.
2. Stripping tags and `{...}` expressions fixed that and immediately broke *"Held as control"*,
   which lives inside an object literal the brace-stripper ate.

It now searches the raw source **and** a tag-stripped copy, because each form finds what the
other misses and the question is only ever "is this string present". Two cheap views beat one
clever one.

**Regression test** also pins the segment timings as monotonic and within five minutes — a
retimed script with a reversed or overrunning segment is a script that cannot be followed —
and asserts every panel name the script tells the presenter to click is a string the UI
actually renders.

---

## INC-038 · A live pipeline with no publisher

**Symptom:** the "Live pipeline" panel connects, reports itself **live**, sends heartbeats
indefinitely — and displays nothing. Ever. Its own subtitle reads *"Control-arm holds appear here
too — that is the proof they are real."*

**Cause.** `EventBus.publish` was called from `tests/test_stream.py` and **from nowhere else in the
codebase.** The bus had subscribers, an event allowlist, bounded queues, drop-oldest back-pressure,
a dropped-frame counter surfaced in the UI, and eleven tests. No producer.

**This is the third time this exact shape has appeared in this project**, and that is the finding
worth recording:

* **INC-024** — the webhook handler verified signatures, stored events, and dropped them.
* **INC-026** — `llm_calls` had a reader and no writer.
* **INC-038** — a bus with subscribers and no publisher.

Each was green across the entire suite, because a test that publishes its own event proves the bus
works and says nothing about whether anything uses it. Every one of the three was found by looking
at the running product; none was reachable from the tests, at any coverage.

**Found** while writing the demo script — checking what the panel would actually show on camera
before telling anyone to point at it.

**Fix.** Two real publishers, on the two paths that run in-process:

* `testmode/recover` publishes `case.detected`, then **one frame per graph node** carrying the
  node's name, its trace summary and its provenance string, then `action.dispatched` with the real
  Razorpay link id. A viewer now watches `DIAGNOSE · AUTHENTICATION_ABANDONED (confidence 95%) ·
  rule table (no model call)` arrive live — which is the project's central claim, moving.
* the webhook path publishes `recovery.verified` when a settlement counts, and `case.control_held`
  when a real payment lands on a control case and is deliberately not credited to us. That second
  one is what the subtitle had been promising.

**The panel was throwing the good data away too.** `summarise()` ignored `node`, `summary` and
`provenance` and fell through to a generic `name.replace(/[._]/g, " ")` — so even with a publisher
it would have rendered "case diagnosed" eight times. Fixed in the same pass.

**Regression test** asserts on the *application*, not the bus: that something publishes, that the
Test Mode and webhook paths specifically do, that `case.control_held` exists somewhere so the
subtitle's promise is kept, that every published literal is on `PUBLIC_EVENTS` (anything else is
silently dropped with a log line nobody reads), and that the panel reads all three trace fields.

**And that test caught itself being vacuous.** Its check that the panel reads `provenance` matched
the word inside the comment *explaining why provenance mattered* — so deleting the field from the
code left the test green. It now matches the narrowing expression, `typeof data.provenance ===
"string"`. A test satisfied by its own documentation, which is a new variety of the INC-006 pattern
and the fourth instance of that pattern in this repository.

---

## INC-039 · A field that was always wrong unless you knew to overwrite it

**Symptom:** `docs/EVIDENCE.md` — the file whose entire job is being the single source of truth for
every figure in the submission — published **Net incremental: Rs 0.00**, three lines above an
attribution table reporting a 6.16% lift. The dashboard said ₹60,216.66.

**Cause.** `services/metrics.overview()` returned `net_incremental` as `Figure(paise=0)` with a
basis reading *"computed by /metrics/attribution"*. The router overwrote it after the fact. The
reasoning was sound and is in the original comment: recomputing the lift inside `overview` would be
two implementations of one number, which is the INC-007 shape.

Sound reasoning, wrong remedy. It left a field that is **always wrong unless the caller knows to
replace it**. The router knew. The morning briefing knew, and computed attribution itself. The
snapshot tool, written months later by someone who had read neither, did not — and published the
placeholder into the one document that exists to stop figures disagreeing.

Found within a minute of the snapshot's first run, by reading its output against the dashboard.
Which is the point of the snapshot.

**Fix.** `attribution` is a **required** argument to `overview()`. There is still exactly one
implementation of the lift, and no caller can obtain a placeholder. `mypy --strict` reported all
four call sites the moment the signature changed — a comment could not have done that, and the
comment had been there the whole time.

**The general lesson, which this project keeps relearning:** a correct invariant enforced by
convention is a defect waiting for a new caller. The remedy for "don't compute this twice" is not
"return a zero and hope"; it is to make the correct value the only obtainable one.

**Also from this round:** `tasks.py benchmark`, the ablation table, and `tasks.py snapshot`. Both
exist because a reviewer asked the two questions this submission could not answer — *does the
architecture earn its complexity*, and *why do two of your documents quote different numbers*.
