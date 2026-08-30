# Real webhooks over a tunnel

Razorpay will not POST to `localhost`, and it will not POST to plain HTTP. To
receive a genuine signed webhook you need a public HTTPS URL pointing at the
local API.

```bash
python tasks.py api        # terminal 1 — must be running first
python tasks.py tunnel     # terminal 2 — prints the URL and the steps
```

The tunnel downloads `cloudflared` on first use into a gitignored `tools/`
directory. No Cloudflare account, no card, no configuration.

---

## Registering it

1. **https://dashboard.razorpay.com/app/webhooks** — confirm the toggle says **Test Mode**
2. **+ Add New Webhook**
3. **Webhook URL** — the `https://….trycloudflare.com/api/v1/webhooks/razorpay` the tunnel printed
4. **Secret** — the value of `RAZORPAY_WEBHOOK_SECRET` in `.env`, character for character
5. **Active Events** — `payment.failed`, `payment_link.paid`, `invoice.paid`, `subscription.charged`
6. **Create Webhook**

The URL changes on every tunnel restart. A quick tunnel is anonymous and
ephemeral by design; a named tunnel would need a Cloudflare login and a domain,
which is the right trade for production and the wrong one for a demo.

---

## It works — verified on a real delivery

```
event_id          TW3Rfq6VhWiuwC
event_type        payment_link.paid
signature_valid   TRUE
status            ACCEPTED  (HTTP 200)
delivered by      52.66.76.63   (Razorpay, Mumbai)
reference_id      rvp_live_v2_...   <- a reference WE issued
```

The captured payload is committed as
`tests/fixtures/razorpay/payment_link.paid.captured.json` with
`provenance: captured_live_webhook` and contact details redacted.

The `reference_id` matching one we issued is the part that matters: it is
attribution condition 3, and this is the first time it has been satisfied by a
real Razorpay event rather than a constructed one.

## Verified behaviour

Measured through the live public tunnel, not asserted:

| Request | Response | Latency |
|---|---|---|
| Valid signature | `200 accepted` | 150 ms |
| Same event id again | `200 duplicate` | 122 ms |
| Forged signature | `401 signature_mismatch` | 98 ms |
| Re-serialised body, original signature | `401 signature_mismatch` | 109 ms |
| No signature header | `401` | — |
| No `x-razorpay-event-id` header | `400 event_id_header_missing` | — |

**Latency is 98–150 ms through the tunnel against 7.4 ms locally.** Nearly all
of that is the Cloudflare round trip, not our handler. It is recorded because
the Phase 2 measurement was local and quoting it for a tunnelled deployment
would be wrong by a factor of fifteen.

---

## `subscription.charged` may not be in your event list

Subscriptions is a separate Razorpay product. If it is not enabled on the
account, the event does not appear in the dashboard and the API refuses the
endpoints:

```
GET /subscriptions  -> 401 Unauthorized
GET /plans          -> 401 Unauthorized
GET /payment_links  -> 200 OK          <- same credentials
```

**Tick the three that exist** (`payment.failed`, `payment_link.paid`,
`invoice.paid`) and carry on. The subscription playbook is exercised entirely by
the seeded corpus, and its balance-vs-mandate split is tested on all 24 cases —
only the *live* subscription webhook is unavailable, which is a property of the
account rather than of the code.

Finding this crashed our own client, which had assumed Razorpay's documented
error shape. See INC-019.

---

## The signature failure everyone hits

**Razorpay signs the exact bytes it puts on the wire.** Any code path that
parses the JSON and re-serialises it before verifying will produce a different
byte sequence and therefore a different HMAC — even though the *object* is
identical.

The table above includes that case deliberately: the same payload, the same
secret, the same signature, re-serialised with `indent=2`, is rejected. Our
handler reads `await request.body()` and verifies those raw bytes before
anything parses them.

This is worth knowing because the symptom is indistinguishable from a wrong
secret, and the usual first response — regenerating the secret — cannot fix it.

**Checklist when a delivery is rejected:**

| Symptom | Cause |
|---|---|
| `401 signature_mismatch` on every delivery | Secret in the dashboard differs from `.env`, or a proxy is re-encoding the body. The log prints the signature received **and** the one our secret produces over the same bytes |
| `401` but the log says **rejected as stale** | The signature was VALID. These are retries of old events outside the 300 s replay window — the secret is **not** the problem. Two 401s meaning opposite things cost a debugging session (INC-021) |
| `502` from the tunnel | The API is not running. `tasks.py tunnel` checks `/healthz` first and refuses to start, precisely because a 502 looks like a signature failure and is not |
| `400 event_id_header_missing` | Something other than Razorpay is posting; Razorpay always sends `x-razorpay-event-id` |
| Deliveries stop after a restart | The tunnel URL changed. Re-register it |

---

## What is stored

The raw body is persisted verbatim before parsing, so a signature dispute can
be re-checked later against exactly what arrived. `event_id` carries a UNIQUE
constraint: a redelivery is recorded as `duplicate` and processed once, which
is what makes at-least-once delivery safe.

Razorpay retries failed deliveries, so an endpoint that is slow or returns 5xx
gets the same event again. That is the normal case, not the edge case.
