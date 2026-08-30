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
