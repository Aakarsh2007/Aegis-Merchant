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
