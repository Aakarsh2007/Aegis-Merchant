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
