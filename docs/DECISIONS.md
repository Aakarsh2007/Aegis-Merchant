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
