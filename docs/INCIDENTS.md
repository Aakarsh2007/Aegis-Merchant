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

_No incidents recorded yet — the build starts at Phase 0._
