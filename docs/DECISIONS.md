# Decision Log — including what we rejected

A running record of choices made *during the build*, with dates.

Its companion is workflow.md §30 (the formal ADL) and §4.2 (*where we deliberately
did not use AI*). This file is the raw material: it captures decisions at the moment
they were made, so the reasoning is real rather than reverse-engineered at
submission time. Rejections matter as much as adoptions — the judging rubric asks
for *"the right tool in the right place, **and where you chose not to use one**."*

## Format

```markdown
## DEC-00N · YYYY-MM-DD · Title
**Phase:** N
**Decision:** What we are doing.
**Rejected:** The alternative, and why it lost.
**Cost of being wrong:** How we would find out, and how expensive the reversal is.
```

---

## Decisions

## DEC-001 · 2026-08-29 · Rebuild rather than adapt the pre-v3.1 implementation

**Phase:** 0

**Decision:** Archive the existing ~4,000-line implementation to `legacy/`
(gitignored, kept on disk) and rebuild against the v3.1 contract in workflow.md.

**Rejected:** Incrementally migrating it. The old tree was written against the v2.1
contract: 9 tables where v3.1 specifies 18, and no transactional outbox, consent
ledger, contact ledger, experiment assignment, injected clock, or LLM response
cache. Those are not additions — the outbox changes how *every* write path works,
and the experiment assignment changes what every metric means. Migration would have
been slower than rebuilding and would have left v2.1 assumptions in load-bearing
places.

**Cost of being wrong:** Low. `legacy/` is intact on disk, so any component that
turns out to be worth salvaging can be lifted across.

## DEC-002 · 2026-08-29 · A Python task runner as the source of truth, Makefile as a shim

**Phase:** 0

**Decision:** `tasks.py` holds every project command. The `Makefile` is generated
delegation (`make test` → `python tasks.py test`).

**Rejected:** A Makefile alone. `make` is not installed on Windows, which is the
development machine, and requiring a judge to install a build tool works against
the 60-second setup promise (workflow.md §22). Rejected also: npm scripts as the
entry point, since the backend is the larger half and Python is already required.

**Cost of being wrong:** Negligible; the two stay in sync because one delegates to
the other rather than duplicating it.

## DEC-003 · 2026-08-29 · Fixed UTC+05:30 offset instead of `ZoneInfo("Asia/Kolkata")`

**Phase:** 0

**Decision:** `IST` is a fixed `timezone(timedelta(hours=5, minutes=30))`.

**Rejected:** `zoneinfo.ZoneInfo("Asia/Kolkata")`. India has never observed DST, so
a fixed offset is not an approximation — it is exact. And `ZoneInfo` on Windows
needs the `tzdata` package because Windows ships no IANA database, which would make
the zero-friction install depend on an extra wheel for no correctness gain.

**Cost of being wrong:** Would only matter if India adopted DST, which would be a
config change in one module.

## DEC-004 · 2026-08-29 · Enforce the injected clock as a test, not a convention

**Phase:** 0

**Decision:** `tests/test_no_wall_clock_reads.py` walks the AST of every module
under `apps/api/app` and fails if anything but `core/clock.py` calls
`datetime.now()`, `datetime.utcnow()`, `date.today()` or `time.time()`.

**Rejected:** Relying on code review and the `DTZ` ruff rules alone. `DTZ` catches
*naive* datetimes but permits `datetime.now(tz=...)`, which would still bypass the
injected clock and make quiet-hours behaviour untestable. A single wall-clock read
in the quiet-hours check could send a customer a message at 2 AM — that is worth an
executable rule. The check uses `ast` rather than a regex so that a docstring
mentioning `datetime.now()` does not trip it, and it carries a meta-test proving it
can actually fail.

**Cost of being wrong:** None; the allow-list is one line if a genuine exemption
ever appears.

## DEC-005 · 2026-08-29 · No Docker, Postgres, Redis or message broker

**Phase:** 0

**Decision:** SQLite (WAL) + in-process APScheduler + a transactional outbox.
Confirmed against the actual requirements rather than inherited from the plan.

**Rejected:** Postgres (nothing at this scale needs it); Redis (contradicts the
zero-infrastructure goal, and the outbox *is* the durable store); Kafka/RabbitMQ (a
broker adds an operational failure mode to a project whose Definition of Done is
"a judge clones it and it runs in 60 seconds"); Celery (APScheduler suffices
in-process). Durability comes from the outbox pattern, not from a broker.

**Cost of being wrong:** The honest limitation is single-process, no horizontal
scaling — stated in the README. The outbox is precisely what makes swapping in a
real queue consumer mechanical if that ever matters.

## DEC-006 · 2026-08-29 · No Alembic; the seed script is the schema fixture

**Phase:** 1

**Decision:** Create the schema with `Base.metadata.create_all` and regenerate data with
`tasks.py seed`. No migration tool.

**Rejected:** Alembic, which the v3.1 roadmap listed. Asked what it would actually do
here and found nothing: there is no deployed instance whose data must survive a schema
change, and during the build the correct response to a schema change is to delete the dev
database and re-seed — which takes under a second. Alembic would have contributed an
`env.py`, a `versions/` directory and a migration chain that nothing ever runs. The
rubric rewards "the right tool in the right place", and that cuts against ceremony as
much as it cuts against reaching for an LLM.

**Cost of being wrong:** Low and clearly signposted. Alembic goes in the moment there is
a persistent instance with data worth preserving; `create_all` on an existing database is
a no-op, so adding it later is additive rather than a rewrite. Recorded in workflow.md as
ADL-012.

## DEC-007 · 2026-08-29 · Timestamps as ISO-8601 UTC text, not SQLite DATETIME

**Phase:** 1

**Decision:** A `UtcDateTime` type decorator storing a fixed-width
`YYYY-MM-DDTHH:MM:SS.sssZ` string, rejecting naive datetimes on both write and read.

**Rejected:** `DateTime(timezone=True)`. SQLite has no native timestamp type and no
concept of a time zone: SQLAlchemy writes whatever it is handed and reads back a **naive**
datetime, silently discarding the offset. In a system where a timezone error means
messaging a customer at 2 AM in breach of quiet hours, a silent tz loss is not an
acceptable failure mode. Also rejected: Unix epoch integers, which are unambiguous but
unreadable to a judge inspecting the database file — and §12.1 makes independent
inspection part of the credibility argument.

**Cost of being wrong:** Fixed-width text sorts correctly, so `ORDER BY`, `BETWEEN` and
the rolling-window queries behave exactly as they would with a native type. The cost is
~8 bytes per column versus an integer, which is irrelevant at this scale.

## DEC-008 · 2026-08-29 · Razorpay REST API over httpx, not the official Python SDK

**Phase:** 2

**Decision:** Call Razorpay's documented REST API directly with `httpx.AsyncClient`.

**Rejected:** The official `razorpay` package. Verified rather than assumed — it wraps
`requests` and is fully synchronous, with no async client. Every call from this
application would block the event loop, and that is not theoretical here: the outbox
drainer runs in-process alongside the API, so one slow provider call would stall webhook
acknowledgement for every other merchant event. It also exposes no per-request timeout
control, which the retry policy in workflow.md §10.4 depends on. Also rejected: wrapping
the SDK in `run_in_executor`, which keeps the "official SDK" label but adds a thread pool
to work around a problem that disappears if we just make the HTTP call ourselves. Auth is
HTTP Basic with `key_id:key_secret` — exactly what the SDK does.

**Cost of being wrong:** We now own the request/response mapping, so a Razorpay API change
lands on us instead of on a dependency. Mitigated by keeping the surface tiny (five
operations) and by capturing real Test Mode responses as fixtures. The dependency was
removed from requirements.txt rather than left unused.

## DEC-009 · 2026-08-29 · The mock provider enforces reference_id uniqueness

**Phase:** 2

**Decision:** `MockRazorpayProvider` rejects a duplicate `reference_id` exactly as
Razorpay does, and simulates non-zero latency.

**Rejected:** A permissive mock that always succeeds. The entire two-phase outbox design
rests on the provider rejecting a duplicate reference — that rejection is what makes a
post-crash retry idempotent. A mock without it would let the Phase 8 crash-recovery tests
pass against a false model of the world, and the bug would surface only against real
Razorpay, in the worst possible way: two live payment links for one cart. A mock is
allowed to be simpler than reality, but never *more permissive* on the property the
design depends on.

**Cost of being wrong:** None; it makes the mock strictly closer to reality.

## DEC-010 · 2026-08-29 · Wilson lower bound for rail ranking, not raw success rate

**Phase:** 3

**Decision:** Rank rails by the Wilson score interval's lower bound at 95%.

**Rejected:** The raw success rate. It is actively wrong at the sample sizes this system
lives at: a rail with 1 success in 1 attempt scores 100% and outranks a rail with 36 in 40,
so every recovery would be routed to whichever rail got lucky most recently. The mirror
failure is worse -- 0 successes in 3 attempts scores 0%, permanently blacklisting a healthy
rail that had an unlucky afternoon. Also rejected: a minimum-sample cutoff, which throws
away real information and just relocates the arbitrary threshold; and Laplace smoothing
toward a prior, which works but needs a tuning constant nobody can justify. Wilson needs no
tuning parameter and degrades smoothly.

**Consequence worth stating:** `best_alternative` returns `None` when nothing is
*confidently* better, and `None` is a real answer meaning "reissue on the same rail". With
only two attempts per case, spending one on a hunch is expensive, and churning rails on
noise makes the recovery message harder to explain to a customer.

**Cost of being wrong:** Slightly conservative -- a genuinely better rail with a thin
sample will not be recommended until it has evidence. That is the correct direction to be
wrong in.

## DEC-011 · 2026-08-29 · The classifier accepts `method` and deliberately ignores it

**Phase:** 3

**Decision:** `classify()` takes a `method` parameter and does not use it. Three golden-set
cases (G-M001..003) fail because of this, and they are declared in the data as expected
misses.

**Rejected:** Adding method-aware rules. Payment method genuinely changes the right answer
-- a bank-side failure at initiation means a rail outage on UPI but an unregistered mandate
on e-mandate -- but encoding that is a combinatorial table of (source x step x method x
reason) that would be guesswork dressed as logic. It is exactly the multi-signal judgement
section 4.3 task 1 describes, and it is where the LLM should earn its cost.

**Cost of being wrong:** A measured 96.5% baseline instead of a higher one. That is the
point: the parameter stays in the signature so the handoff is visible in the code rather
than hidden, and Phase 6 has a concrete, pre-registered target to beat rather than a vague
aspiration.

## DEC-012 · 2026-08-29 · Four outcomes, not two — DEFER and DEGRADE are first-class

**Phase:** 4

**Decision:** The stopping engine returns PROCEED / DEGRADE / DEFER / STOP.

**Rejected:** A boolean gate (act / do not act), which is what "stopping rules" sounds like
it needs. It collapses two distinctions that are worth real money:

*Deferring is not stopping.* A message held for quiet hours must be sent at 09:05, not
dropped. A boolean gate returning `False` at 22:00 would silently discard it, and the logs
would look identical to a correct system while revenue quietly went missing.

*Degrading is not stopping either.* When a customer has no marketing consent, the right
move is to send the transactional recovery link at 0% — not to send nothing. Under a
boolean gate the consent rule would suppress the whole recovery, and the compliance
constraint would be costing us money instead of merely shaping the message.

**Consequence:** the engine merges degradations from every firing rule (stripping a
discount and downgrading a message class are independent reductions that can both apply),
and a deferral past the recovery window is converted into a STOP, because holding until
after expiry is a drop with extra steps.

**Cost of being wrong:** More states to reason about, and a precedence order that has to be
justified — STOP beats DEFER beats DEGRADE. Both are covered by property tests over
generated contexts rather than by convention.

## DEC-013 · 2026-08-29 · All twelve rules evaluate every time; never short-circuited

**Phase:** 4

**Decision:** `evaluate()` runs all twelve rules even after one has decided the outcome.

**Rejected:** Returning at the first STOP. It is the obvious optimisation and it would cost
us the thing the rules are *for* as evidence: the dashboard reports firings per rule
(§14.6), and "S-05 fired 4 times today" is what shows a merchant the brakes work. Short-
circuiting would silently under-report every rule ordered after the first blocker, and the
under-reporting would be invisible.

**Cost of being wrong:** Nothing measurable — twelve pure comparisons over in-memory data,
with no I/O. The context is assembled once by the caller, so the cost was already paid
before the first rule ran.

## DEC-014 · 2026-08-29 · An HMAC-signed capability token, not a naming convention

**Phase:** 5

**Decision:** `PolicyToken` carries an HMAC over the applied action, signed with a key
generated at import and held module-private. Write tools call `verify()` before touching a
provider.

**Rejected:** A plain marker object, or a `_minted_by` string field, or a code-review
convention. Each of those fails silently: a developer who constructs one by hand gets a
working token and no signal. With a signature, skipping the firewall raises at the call
site.

**What is NOT claimed:** Python has no private state, so code that deliberately reaches for
`_SIGNING_KEY` can forge a token. The honest claim is narrower — no *accidental* path
exists, and every deliberate one is visible in a diff. The static half is enforced by
`tests/test_no_unauthorised_writes.py`, which walks the import graph and fails if any module
outside `guardrails` imports the mint function.

**Why the key is per-process and ephemeral:** a token is a capability for one immediate
execution, not a durable grant. It must not survive a restart or be replayed tomorrow. Human
approvals genuinely do need to persist, and they use a different mechanism — a content hash
of the exact approved action, re-checked at execution time.

## DEC-015 · 2026-08-29 · Violations and routine reductions are different things

**Phase:** 5

**Decision:** `Clamp` carries `is_violation`. The escalation ladder keys on violations only.

**Rejected:** Treating every clamp as evidence the model misbehaved. That was the first
implementation, and the fuzzer's coverage guard exposed what it would do in production:
Ananya's recovery has its discount stripped because she has no marketing consent, which is
the compliance design working exactly as intended (§9.2) — and it would have escalated her
case to a human approval queue. Every no-marketing-consent recovery would have needed a
person, which is the product not working.

The distinction is between a proposal that breached a hard bound (90%, NaN, negative — the
model tried something it is not permitted to do) and a reduction that made a reasonable
proposal *safer* (consent downgrade, absolute rupee cap on a large cart).

**Consequence:** the dashboard's "unsafe proposals intercepted" figure (§14.6) counts
violations, not all clamps. Counting routine downgrades would inflate the metric with the
system's own good behaviour and make it meaningless as evidence.

## DEC-016 · 2026-08-29 · Sabotage every safety test before trusting it

**Phase:** 5

**Decision:** Standing practice: any test asserting a safety property is verified by
deliberately breaking the thing it protects and confirming it goes red.

**Rejected:** Trusting a green suite. INC-006 is the argument — twenty-five property tests
passed while proving nothing, and only sabotage revealed it. A guarded assertion
(`if precondition: assert ...`) fails *open*: when the precondition is never met it is
indistinguishable from passing.

**How it is applied:** sabotage runs are manual and recorded in the phase notes rather than
committed, since a permanently-broken copy of the firewall in the repo would be worse than
the problem. What *is* committed is the coverage guard — `TestTheProofIsNotVacuous` —
which asserts the proof's preconditions are actually met, so the decay is caught
automatically next time.

## DEC-017 · 2026-08-29 · The rule table ships for diagnosis; the model advises

**Phase:** 6

**Decision:** The deterministic classifier decides diagnosis. The model is consulted only
where the classifier declares itself unsure (`needs_llm_review` — conflicting signals, or
confidence below 0.6).

**Measured, both prompt revisions reported:**

| system | overall | conflicting_signals |
|---|---|---|
| deterministic rule table | **96.5%** (82/85) | 10/10 |
| gemini-3.1-flash-lite, prompt v1 | 82.4% (70/85) | 10/10 |
| gemini-3.1-flash-lite, prompt v2 | 90.6% (77/85) | 10/10 |

§15.1 committed to this *before the model existed*: if it does not beat the rule table, we
ship the rule table and say so. It did not, so we do.

**Why v1 lost:** 13 of its 15 misses were `X -> UNKNOWN`. The prompt told it that an honest
UNKNOWN beats a confident guess, and it took that seriously on degraded telemetry — but
nothing told it that `error_source` is an attribution Razorpay has *already made*, not a
hint to weigh. Given only `error_source=bank`, it answered "not enough evidence", which is
cautious and wrong: that IS the evidence.

**Why I stopped at v2:** the fix worked (degraded_telemetry 9/15 -> 14/15, hard_ambiguous
3/5 -> 5/5) but traded errors elsewhere (clean 45/45 -> 42/45) and still lost by 6 points.
A third revision tuned against the same 85 cases would be fitting the test set, not
improving the system. Two shots, both reported, verdict stands.

**The sub-result that justifies the architecture:** on `conflicting_signals` — the band the
model exists to handle, where Razorpay's own fields disagree — it scored **10/10 in both
runs**, matching the rule table exactly. It loses overall on cases the rule table already
answers well, not on the ones it was designed to be asked about. So this is not a
compromise between two systems; it is the split §4.2 specified before either was built,
now supported by measurement instead of assertion.

**Cost of being wrong:** `test_the_baseline_still_beats_the_model` asserts the ordering in
the direction the measurement went. If a future model or prompt genuinely overtakes the
rule table, that test fails — and the failure is the signal to change the routing, not to
loosen the assertion. A gate that only fires in the flattering direction is not a gate.

## DEC-018 · 2026-08-29 · A committed response cache, and what it is allowed to claim

**Phase:** 6

**Decision:** Real model responses are recorded into `data/llm_cache.jsonl` and committed.
The batch demo and CI score from it.

**Rejected:** calling the model live in CI. It needs a secret (so nobody who forks the repo
can reproduce the badge), burns free-tier quota on every push, and returns a slightly
different answer each run — which is precisely what a regression gate must not do.

**What this buys beyond speed:** the batch result becomes **byte-for-byte reproducible**. A
judge who clones the repo gets exactly the numbers in the README, because the model's
contribution is pinned rather than re-rolled. Non-reproducible benchmarks are a real
problem in LLM-in-the-loop systems, and pinning outputs is the standard answer.

**The honesty constraints, which are not optional:**
- Every served response is marked `source=CACHED` and is never displayed as live.
- `prompt_version` is part of the key, so a prompt edit invalidates everything derived
  from it. Demonstrated in practice: revising the DIAGNOSE prompt to v2 invalidated all 83
  v1 entries, and a test asserts no stale versions remain.
- Cached rows are **re-validated on read**. The cache is a file in a repo; a hand-edited
  entry that no longer matches the schema must fail like any other malformed response.

**Cost of being wrong:** a stale cache would silently score an old prompt. The version key
and `test_every_cached_entry_matches_the_current_prompt_version` close that.

## DEC-019 · 2026-08-29 · No LangGraph; the case row is the checkpoint

**Phase:** 7 · **Supersedes ADL-008**

**Decision:** The agent is an explicit async state machine over the `recovery_cases` row.
Seven nodes, one pure `next_node()` function holding the entire control flow.

**Rejected:** LangGraph, which ADL-008 had chosen. That decision was made in the planning
phase for one stated reason — checkpointed pause/resume, so a case escalated to a human
could suspend for hours and resume mid-graph. The justification does not survive contact
with what actually got built:

*The checkpoint already exists.* A case awaiting approval is a row with
`status = AWAITING_APPROVAL`. That row is the authoritative state — the dashboard reads it,
the audit chain hashes it, the attribution matcher queries it. A graph checkpointer would
store the same case state a second time, and the two would have to agree. **Duplicated
state that must agree is exactly the defect INC-007 produced one phase earlier**, where the
proposed message class lived in two places and diverged.

*And resuming is the wrong semantics anyway.* When a human approves, the correct behaviour
is not to continue a frozen graph. It is to reload the case and re-run the policy firewall,
because §6.1 requires the stopping rules to be evaluated again immediately before acting —
the customer may have paid in the intervening four hours. Resuming a checkpoint would skip
precisely the check that exists to catch that.

Also weighed: `langgraph` pulls `langchain-core`, `langsmith`, `langgraph-checkpoint` and
`langgraph-sdk` into a project whose Definition of Done is that a judge clones it and it
runs. That alone would not have decided it — the duplicated state did.

**The honest trade:** "we use LangGraph" is a more recognisable sentence to a judge
skimming a repo, and this costs us that. What it buys is a control flow readable in twenty
lines, no dependency whose main feature duplicates our own state, and an answer to "where
did you choose *not* to use the obvious tool" that is about engineering rather than taste.

**Cost of being wrong:** if the graph ever needs genuine mid-node suspension — a node that
blocks on an external callback rather than ending a run — this would need revisiting.
Nothing in the four playbooks does. `tests/test_agent_graph.py::TestNoLangGraph` asserts the
dependency stays out, so re-adding it has to be deliberate.

## DEC-020 · 2026-08-29 · Verify the provider's behaviour before depending on it

**Phase:** 8

**Decision:** Before writing the outbox, probe live Razorpay Test Mode to confirm the
property the whole design rests on: that a duplicate `reference_id` is refused and the
existing link is retrievable.

**Rejected:** building on the documented behaviour and the mock. Both said the right thing,
and both were things *I* had written down. The outbox's entire correctness argument reduces
to "the provider will refuse our second attempt" — depending on that without testing it
would have been the same class of mistake as INC-010, where a benchmark measured the
fallback and reported it as the model.

**What the probe established:**
- A duplicate `reference_id` is refused. DEC-009 is validated; the mock models something
  true, so the crash-recovery tests are proving something.
- The existing link is retrievable, so the recovery path works.
- Case semantics differ from ours (INC-012), which the mock would never have revealed.

**Then verified end-to-end against live Test Mode:** phase one commits the intent, the
provider creates a real link, the process "dies" before phase two — and on restart the
reconciler resumes and finishes with **one link at Razorpay, one action locally, consistent
state.** That is the §16 scenario-9 claim, demonstrated rather than asserted.

**Cost of being wrong:** the probe costs one Test Mode payment link and about a minute. The
alternative was discovering during the demo that two live links exist for one cart.

## DEC-021 · 2026-08-30 · Report a smaller number, and say when it is not significant

**Phase:** 9

**Decision:** `/api/v1/metrics/attribution` returns gross and incremental side by side, with
Wilson intervals on both arms, and attaches a written caveat to the report itself whenever
the lift is not statistically distinguishable from zero.

**Rejected:** reporting gross recovery alone, which is what a dashboard normally shows and
what every one of our own illustrative figures had been. Measured over the real corpus the
difference is not cosmetic:

| | |
|---|---|
| gross recovered | ₹2,02,760 |
| **incremental** | **₹60,217** |
| absolute lift | 6.2% (treatment 29.2%, control 23.1%) |
| statistically significant | **no** — 171 treated, 39 control, intervals overlap |

Reporting ₹2.03L would have been defensible-sounding and wrong by a factor of three. Nearly
a quarter of the control group paid without us.

**Also rejected:** reporting the lift without the significance caveat. `lift_is_significant`
existed as a boolean before this, and a boolean is something a caller can fail to read. The
number is going on a dashboard, where an unqualified 6% reads as a result rather than as
noise, so the caveat is now a sentence on the report.

**The uncomfortable part, kept deliberately:** with a hackathon-sized batch the honest
answer is usually "not significant", and the system says so. A measurement framework that
only ever confirms the intervention would not be a measurement framework.

**Cost of being wrong:** none. Every condition in the attribution rule can only *reduce*
what we claim, and the report cannot silently become optimistic — `has_control_arm: false`
sets incremental to zero with a note rather than falling back to gross.

## DEC-022 · 2026-08-30 · Ship the audit chain with its limitations written down

**Phase:** 10

**Decision:** `verify_blocks` returns `head_hash` and `blocks` on every response, and the
success `reason` states in plain words that **tail truncation cannot be detected from the
chain alone**. A test asserts that limitation rather than papering over it.

**Rejected:** the usual presentation — `{"valid": true}` and a paragraph about
tamper-proofing. A hash chain that lives entirely inside the database it protects cannot
detect the deletion of its own last *k* blocks: what remains is a shorter, perfectly valid
chain. Every construction of this kind has that property. Most write-ups do not mention it.

Claiming "tamper-proof" would be the single easiest thing in this project to disprove, and a
judge who knows hash chains would find it in one question. What we can honestly claim is
narrower and still worth having: an in-place edit requires rewriting every subsequent block,
a partial edit is loudly detectable, and the cost of a silent change goes from one `UPDATE`
to a full rewrite.

**Cost of being wrong:** none for the mechanism, which is unaffected. The cost of the
*opposite* choice would have been credibility on every other number we report.

---

## DEC-023 · 2026-08-30 · Refuse to start rather than serve unauthenticated

**Phase:** 10

**Decision:** `API_TOKEN` unset in production raises at `create_app`, and the process does
not start. Unset outside production means the API is open, every response carries
`X-Auth-Mode: disabled`, `/health/deep` reports `auth: "disabled"`, and a warning is logged
at startup.

**Rejected:** requiring a token everywhere. Judge Mode must run with zero credentials (§22),
and a demo that cannot start without secrets is a demo nobody runs.

**Also rejected:** defaulting to a token baked into the repo. A default credential in a
public repository is not authentication, and it would be the first thing found.

**Also rejected:** checking at request time. The realistic failure is not "someone chose weak
auth", it is "auth was never configured and nothing said so". A request-time check leaves
every endpoint open until somebody notices; a startup failure is loud, immediate and
impossible to miss.

**Cost of being wrong:** a misconfigured production deploy fails to boot. That is the correct
direction to fail — the alternative is an open money-moving API that looks healthy.

---

## DEC-024 · 2026-08-30 · Ship the tamper button

**Phase:** 10

**Decision:** `POST /api/v1/audit/tamper` corrupts a chosen block three different ways, is
gated on `settings.simulation_allowed` (environment, not a caller-supplied header), and is
refused in production with 403.

**Rejected:** describing the verifier's behaviour in the README instead. A verifier nobody has
watched fail is indistinguishable from `return True`, and the reader has no way to tell them
apart. The button lets a judge break the chain on their own machine and watch the check name
the block, the index and the reason.

**Cost of being wrong:** an endpoint that damages audit data exists in the codebase. Contained
by the environment gate, a `pattern`-constrained enum of modes, `extra="forbid"`, and tests
asserting the 403 in production.

## DEC-025 · 2026-08-30 · The model fills slots; it never writes a message

**Phase:** 11

**Decision:** `guardrails/consent.py` renders outbound text by substituting named slots into
a template row marked `approved`. Slot names are checked against an allowlist, values are
substituted in a single pass and never re-scanned, and any failure refuses rather than
producing a partial message.

**Rejected:** `body.format(**slots)`, which is the obvious implementation. `str.format`
walks attribute and index expressions, so `{x.__class__.__init__.__globals__}` reads
process globals and a value containing braces gets re-examined. Our bodies come from an
approved DB row rather than user input, which makes that unlikely rather than impossible —
and "unlikely" is a poor property for the code that renders every outbound message.

**Also rejected:** letting the LLM write the final copy and checking it afterwards. A
message's compliance class is a property of what it *says*, and the interesting failure is
not "the agent chose to send marketing without consent" — S-08 catches that — but "the
agent chose transactional and the model wrote *20% off!* into it". No classifier we could
write would be a better control than making the failure unrepresentable.

**A real bug this produced, and the fix:** the first version checked for leftover braces in
the *rendered* output, which conflated a malformed template (`"Hi {first_name"`, our bug)
with a slot value that legitimately contains a brace (a customer who typed `{link}` into
the name field, their data). The tests caught it. Structure is now validated on the
template before substitution, and values are treated as opaque throughout.

**Cost of being wrong:** a template that cannot render refuses and the case escalates to a
human, which is the correct direction. The seeded templates are all asserted renderable, so
the refusal path cannot fire during the demo.

---

## DEC-026 · 2026-08-30 · Expiry is checked before the hash

**Phase:** 11

**Decision:** `POST /approvals/{id}/action` checks, in order: already-actioned (409),
past TTL (409), then `policy_applied_hash` (409). `reviewed_by` comes from the
authenticated principal and the request schema is `extra="forbid"`, so a body attempting
to name its own reviewer is a 422 rather than a silently ignored field.

**Rejected:** checking the hash first. A correct hash does not make four-hour-old
information fresh — the TTL exists precisely because the world moves — so an expired
approval must lose either way. Ordering them the other way would let a stale approval
through whenever the underlying action happened not to change.

**Also rejected:** making `policy_applied_hash` optional with a skip-if-absent fallback.
A guard that can be bypassed by omitting a field is not a guard.

**Cost of being wrong:** a reviewer occasionally has to re-read and re-approve. That is
the intended cost: the alternative is a human authorising one action and a different one
executing.

---

## DEC-027 · 2026-08-30 · A held message is cancelled, not silently sent late

**Phase:** 11

**Decision:** a queued send whose case reached a terminal state, or whose release time
falls after `window_expires_at`, is marked DEAD by the sweeper before the drainer can
reach it — with the reason recorded in the audit chain.

**Rejected:** letting the drainer send it. See INC-016: it spends one of two permitted
contacts, messages someone about a payment we are no longer pursuing, and the result is
structurally unattributable because no case is in `MONITORING`.

**Also rejected:** dropping the row silently. `stale_deferrals` is reported separately
from `expired_approvals` in `SweepResult` specifically because this number is a *loss* —
money we held a message for and then could not pursue — and folding it into a general
"cleaned up N rows" would hide the signal that quiet hours are costing us recoveries.

**Cost of being wrong:** a message that could legitimately have been sent is cancelled if
the window arithmetic is wrong. Bounded by tests at every hour of the day, and by the
control case asserting a healthy deferral survives the same sweep.

## DEC-028 · 2026-08-30 · Provenance is a type, not a UI convention

**Phase:** 12a

**Decision:** `core/provenance.py` defines `Figure` and `Count`, neither of which can be
constructed without a `Provenance` and a non-empty `basis`. Every rupee figure the API emits
is one of these.

**Rejected:** the workflow's own framing, which called this a UI design rule. A convention is
followed until the afternoon somebody adds a tile in a hurry, and the tile that gets added in
a hurry is exactly the one that ends up on screen unqualified.

**Also rejected:** a hand-maintained list of money fields in the test. It passes forever while
going quietly out of date. The test now *walks the actual response body* looking for anything
with a `paise` key and asserts a badge on each — so it fails when a tile is **added**, not
when someone remembers to update a list. Verified by sabotage: injecting an unbadged tile
fails the test naming the exact JSON path.

**The number this exists to protect:** ₹2,02,760 gross next to ₹60,217 incremental. Both are
true, they answer different questions, and a viewer who cannot tell which is which will take
the larger one.

**Cost of being wrong:** a little ceremony around every number. Cheap, and the alternative is
the one mistake that would undermine every other claim in the project.

---

## DEC-029 · 2026-08-30 · A slow SSE subscriber loses events, not memory

**Phase:** 12a

**Decision:** each subscriber gets a 256-frame bounded queue. When it fills, the **oldest**
frame is dropped and a `dropped` counter rides out with every subsequent frame.

**Rejected:** an unbounded queue. A browser tab backgrounded for an hour is the normal case,
not the edge case, and an unbounded queue behind it is an out-of-memory in a process that
also moves money.

**Rejected:** dropping the newest. A stale view of a case is worth less than the current one.

**Rejected:** dropping silently. A client that has missed events and does not know it will
show a confidently wrong picture; one that knows can re-fetch. The counter is the difference.

**Also decided:** events are notifications, not state. Each carries an id and enough to say
what changed; the client re-fetches from REST. A stream that shipped full state would become
a second, subtly different source of truth for numbers `/metrics` already owns — and the two
would drift.

**Cost of being wrong:** single-process only, written down rather than discovered. With two
workers a subscriber attached to worker A never sees worker B's events. The transactional
outbox is what makes swapping in a real broker mechanical (ADL-003).

---

## DEC-030 · 2026-08-30 · The DLQ replays the original reference_id

**Phase:** 12a

**Decision:** `POST /dlq/{id}/replay` re-queues the **existing** outbox row with its original
`reference_id`, and refuses a second replay with 409.

**Rejected:** minting a fresh reference on replay. It looks equivalent and is the bug: it
converts *"retry this action"* into *"perform this action again"*. Reusing the original means
Razorpay's own uniqueness constraint rejects a replay of something that actually succeeded,
so the provider — not our bookkeeping — is what prevents the double charge.

**Cost of being wrong:** a genuinely-failed action whose reference was somehow consumed
cannot be retried through this path and needs an operator. That is the correct direction to
fail for an endpoint that can move money twice.

## DEC-031 · 2026-08-30 · Simulated recoveries get their own tile, not a footnote

**Phase:** 12b

**Decision:** `/metrics/overview` returns **two** gross figures. `gross_recovered` sums only
recoveries proven by a real signed webhook and is badged `RAZORPAY_VERIFIED`;
`gross_simulated` sums those settled by the batch runner and is badged `SIMULATED`. Both
render, and the verified one renders **even at zero**.

**Why this was forced.** `recovery_requires_proof` is a CHECK constraint: a recovered amount
cannot exist without a verifying event id. So the batch runner *must* write one. Any
realistic-looking id would have been summed into the verified column, and the dashboard would
have reported ₹2,02,760 of seeded outcomes as webhook-proven — the exact overclaim the badge
exists to prevent, and undetectable from the outside. The `sim_evt_` prefix is what makes the
two separable.

**Rejected:** one combined figure with the weaker badge. §14.5 says a figure needing two
badges is two figures, and averaging a verified number with a simulated one produces something
that is neither, labelled as whichever the author preferred.

**Rejected:** hiding the verified tile while it reads zero. A missing tile reads as *"not
measured"*; a zero reads as *"measured, and none"*. The second is true and is the more useful
thing for a judge to see.

**Cost of being wrong:** the headline number on screen is smaller and needs a sentence of
explanation. That sentence is the product.

---

## DEC-032 · 2026-08-30 · A failed fetch renders an error, never a zero

**Phase:** 12b

**Decision:** `safeFetch` returns a discriminated `Result<T>` and every panel renders an
explicit `FetchError` when the API is unreachable.

**Rejected:** the ordinary `fetch(...).catch(() => defaults)` pattern, which renders
`Rs 0.00` when the backend is down. Zero is a number a viewer will believe, and it is
indistinguishable from a real measurement — during a live demo it would be read as "the agent
recovered nothing".

**Also decided:** each panel is an independent server component, so one failing endpoint
degrades one card rather than blanking the page.

**Cost of being wrong:** a visibly broken card instead of a plausibly wrong number. That is
the correct direction.

## DEC-033 · 2026-08-30 · The model may argue for an action; it may not choose one the playbook forbids

**Phase:** 13

**Decision:** `agent/playbooks.py::violations()` runs against the **LLM's** proposal as well as
our own deterministic one. A forbidden strategy is replaced with the playbook's own choice,
and the substitution is recorded in the rationale so the trace shows what the model wanted
and why it was overruled.

**Rejected:** trusting the proposal because its rationale reads well. A plausible-sounding
justification is precisely what a model produces for a wrong action — that is the failure
mode, not an aberration — so the check is on the *action*, never on how well it was argued
for.

**What it caught immediately.** The model proposed `FRESH_LINK_SAME_RAIL` for eight overdue
B2B invoices. Defensible in isolation: a payment link does collect an invoice. Wrong in
context: accounts payable need the invoice number, amount and due date restated so they can
reconcile it, and "here is a fresh payment link" is consumer-checkout language. The first
version of the forbidden set missed this — it blocked only `INCENTIVISED_LINK` — and the
batch showed `RECEIVABLE → FRESH_LINK_SAME_RAIL ×8` in the routing table.

**Cost of being wrong:** the agent is less able to be creative on strategy. That is the
intended trade: strategy is where a wrong choice costs a scheme re-presentation or writes off
a receivable, and neither is a decision worth delegating for the upside of novelty.

---

## DEC-034 · 2026-08-30 · The subscription split, and why it is the most expensive decision here

**Phase:** 13

**Decision:** a subscription failure routes on whether the **mandate is alive**, not on the
amount or the customer:

| Signal | Action | Why the other one is wrong |
|---|---|---|
| `INSUFFICIENT_FUNDS` — mandate alive | `MANDATE_RETRY` | A fresh link works *once* and converts a recurring customer into a one-off payment, losing every future collection |
| `MANDATE_INVALID` / `requires_reauth` | `MANDATE_REAUTH` | Re-presenting cannot succeed and **burns a scheme re-presentation**, of which NPCI permits only a few before penalties |

Both arrive as "subscription payment failed". Measured across the corpus the split is 13 / 11
on 24 cases, so getting it wrong is not a corner case — it is roughly half the playbook.

**Rejected:** treating the two alike and retrying everything. It is the cheaper
implementation and it loses money in both directions at once.

**Also decided:** `requires_reauth` overrides the category. If the classifier says the mandate
is dead, nothing else about the failure matters, and a test asserts the two paths cannot be
collapsed by a refactor — which every individual assertion would still pass.

**Cost of being wrong:** a live mandate gets re-presented when a link would have converted
sooner. Recoverable. The opposite error is not.

## DEC-035 · 2026-08-30 · An anonymous ephemeral tunnel, and saying so

**Phase:** 14

**Decision:** `python tasks.py tunnel` starts a Cloudflare **quick tunnel** — no account, no
card, no configuration — and downloads `cloudflared` on demand into a gitignored `tools/`
directory.

**Rejected:** a named Cloudflare tunnel, which needs a login and a domain. Correct for
production, wrong for a project whose whole premise is that a judge can clone it and run it
on free tiers.

**Rejected:** committing the 55 MB platform-specific binary, and installing it system-wide. A
hackathon project should not modify the machine it is cloned onto.

**The cost, stated rather than discovered:** the URL changes on every run, so the webhook must
be re-registered each time. That is written in `docs/webhooks.md` and printed by the tunnel
itself, because the alternative is finding out tomorrow when deliveries stop.

**Also decided:** the task checks `/healthz` before starting and refuses if nothing answers.
A tunnel to a dead port returns 502 for every delivery, which looks exactly like a signature
failure and is not.

## DEC-036 · 2026-08-30 · The policy endpoint tightens only

**Phase:** 15 (audit)

**Decision:** `POST /api/v1/policy` accepts a bound only if it is **stricter** than the current
one. A request that would loosen any bound is refused with 409, and a mixed request is
refused **entirely** rather than half-applied.

**Rejected:** a symmetric endpoint. Tightening reduces what the agent may do and is safe to
expose over an API. Loosening unlocks the firewall — raising a discount ceiling or a contact
cap — and an endpoint that can do that is an endpoint worth attacking, and an endpoint a bad
deploy can misuse. Loosening requires editing configuration and restarting, which is
deliberately more friction than a POST.

**Rejected:** partial application of a mixed request. Applying the tightening and skipping the
loosening would leave the caller unsure which bounds are in force, and "some of your changes
were applied" is the worst possible answer about a safety limit.

**Cost of being wrong:** a merchant who genuinely wants a looser bound needs a restart. That
is the correct amount of friction for the direction that costs money.
