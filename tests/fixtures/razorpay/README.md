# Razorpay webhook fixtures

## Provenance — read this before trusting these files

Most of these payloads are built from Razorpay's **published documentation**.
One — `payment.failed.captured.json` — is a real Test Mode capture. That
distinction matters and is stated here rather than buried, because the
deterministic failure classifier in Phase 3 keys on `error_source` /
`error_step`, and a classifier built against assumed field shapes is a
classifier built on sand.

**It was not a hypothetical risk.** The first real capture (2026-08-30)
immediately showed that Razorpay returns `error_reason: "payment_cancelled"`
for an abandoned checkout, while the classifier's marker table carried four
*invented* spellings of that event and not the real one. See INC-015. The
verdict survived on a fallback path; the confidence and the audit-trail
reasoning did not.

Each file carries a `_fixture_meta.provenance` field:

| Value | Meaning |
|---|---|
| `documented_shape` | Derived from Razorpay docs. Structurally correct; exact value sets unverified. |
| `captured_test_mode` | Recorded from a real Razorpay Test Mode response. Authoritative. |

**Anything still marked `documented_shape` when the classifier ships is a known
risk, and is listed as such in the README's limitations section.**

## Replacing these with real captures

Once `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` are set in `.env`:

```bash
python tasks.py capture-fixtures
```

This calls the real Test Mode API, writes the responses here with
`provenance: captured_test_mode`, and prints a diff against the documented shape.
Any divergence found is journalled in `docs/INCIDENTS.md` — field shapes differing
from documentation is a normal and expected finding, and it is exactly the sort of
thing worth recording.

## Why fixtures at all

Razorpay Test Mode cannot induce every real-world failure — you cannot ask it for
a genuine HDFC UPI authorisation timeout on demand. Where a failure cannot be
produced honestly, we replay a fixture through the **real HMAC verification path**.
The ingestion is real even when the event is synthetic, and the README says so
rather than blurring the two.
