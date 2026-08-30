# Deployment

Three options, cheapest first. All three are free or near-free for a demo.

**Read this first:** the API keeps state in a SQLite file. That single fact
decides everything below — it rules out platforms with ephemeral disks and
platforms that run more than one instance, and it is why "deploy the backend to
Vercel" is not on this list.

---

## Option A — Don't deploy (recommended for judging)

For a hackathon submission this is genuinely the best option, not a cop-out.

```bash
python tasks.py demo        # everything, locally
python tasks.py tunnel      # a public HTTPS URL when you need one
```

**Why it wins for a demo:** a judge who clones the repo gets the same thing you
have, with no environment drift, no cold starts and no credentials. The audit
chain, the tamper button, the kill switch and the chaos panel all work. The
tunnel gives you a real public URL for real Razorpay webhooks when you want one.

**Cost:** nothing. **Setup:** none.

---

## Option B — Frontend on Vercel, API on Render

The standard split. The frontend is stateless and belongs on a CDN; the API is
stateful and does not.

### B1. The API on Render

Render gives a free web service with a **persistent disk**, which is the part
that matters — SQLite needs a filesystem that survives a restart.

1. Push to GitHub (already done).
2. **render.com → New → Web Service → connect the repo**
3. Settings:

   | Field | Value |
   |---|---|
   | Root directory | `apps/api` |
   | Runtime | Python 3 |
   | Build command | `pip install -r requirements.txt` |
   | Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
   | Instance type | Free |

4. **Add a disk** (Settings → Disks): mount path `/data`, size 1 GB.
   Without this the database is wiped on every deploy.
5. Environment variables:

   ```
   ENVIRONMENT=production
   API_TOKEN=<generate one, see below>
   DATABASE_URL=sqlite+aiosqlite:////data/revpilot.db
   RAZORPAY_KEY_ID=<your test key>
   RAZORPAY_KEY_SECRET=<your test secret>
   RAZORPAY_WEBHOOK_SECRET=<the one in your Razorpay webhook>
   GEMINI_API_KEY=<optional>
   ```

   Generate the token with:
   ```bash
   python -c "import secrets; print('rvp_' + secrets.token_urlsafe(32))"
   ```

   **`API_TOKEN` is not optional in production.** The app refuses to start
   without it (DEC-023), deliberately: a service that boots happily with
   authentication disabled is the failure that is worth preventing.

6. First deploy will have an empty database. Open Render's shell and run:
   ```bash
   python -m app.db.seed && python -m app.workers.batch_cli
   ```

**Note the four-slash `sqlite+aiosqlite:////data/...`** — three slashes is a
relative path and would put the database on the ephemeral disk.

### B2. The frontend on Vercel

1. **vercel.com → Add New → Project → import the repo**
2. Settings:

   | Field | Value |
   |---|---|
   | Framework | Next.js (detected) |
   | Root directory | `apps/web` |

3. Environment variables:

   ```
   NEXT_PUBLIC_API_BASE_URL=https://your-api.onrender.com
   NEXT_PUBLIC_API_TOKEN=<the same API_TOKEN>
   ```

4. Deploy.

### B3. Two things that will bite you

**CORS.** `main.py` allows `localhost:3000` only. Add your Vercel origin:

```python
allow_origins=["http://localhost:3000", "https://your-app.vercel.app"],
```

**`NEXT_PUBLIC_` variables are public.** Anything with that prefix is compiled
into the browser bundle and readable by anyone. `NEXT_PUBLIC_API_TOKEN` is
therefore a *demo* arrangement, not a security boundary — it is fine for a
hackathon deployment where the data is seeded, and it is not fine for real
merchant data. The real fix is a session cookie and a server-side proxy route,
which is a day of work and out of scope here. Said plainly rather than left for
someone to discover.

**Cost:** free tier on both. Render's free instance sleeps after 15 minutes of
inactivity and takes ~30 s to wake, so click the dashboard once before a demo.

---

## Option C — Everything on Railway

Simplest if you want one platform and one URL.

1. **railway.app → New Project → Deploy from GitHub**
2. Two services from the same repo:

   | Service | Root | Start command |
   |---|---|---|
   | api | `apps/api` | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
   | web | `apps/web` | `npm run build && npm start` |

3. Add a **volume** to the api service, mounted at `/data`, and set
   `DATABASE_URL=sqlite+aiosqlite:////data/revpilot.db`.
4. Same environment variables as Option B.

**Cost:** $5 of monthly credit on the free plan, which comfortably covers a
demo. No cold starts, which is the advantage over Render's free tier.

---

## Why not Vercel for the API?

Asked often enough to answer directly. Vercel's Python runtime is serverless:

- **The filesystem is ephemeral.** SQLite would be wiped between invocations.
- **Instances are not shared.** The SSE event bus is in-process, so a browser
  connected to one instance would never see another instance's events.
- **Background tasks do not survive the response.** The webhook handler
  acknowledges in ~7 ms and processes afterwards; serverless kills that.
- **There is no long-lived connection.** SSE needs one.

None of these are Vercel's fault — they are the trade the architecture made
(ADL-003: SQLite and in-process scheduling, no Docker or Redis, so the whole
thing runs on a laptop with no infrastructure). The transactional outbox is
precisely what makes migrating to a queue and Postgres mechanical if that trade
ever needs reversing.

**Deploy the Next.js frontend to Vercel. Put the API somewhere with a disk.**

---

## Production checklist

Before pointing this at anything real:

- [ ] `ENVIRONMENT=production` — disables the tamper endpoint, the chaos
      endpoints and the simulation batch, all gated on the environment rather
      than on a header a caller controls
- [ ] `API_TOKEN` set — the app will not start without it
- [ ] `RAZORPAY_WEBHOOK_SECRET` matches the Razorpay dashboard exactly
- [ ] A persistent disk, and `DATABASE_URL` pointing at it (four slashes)
- [ ] CORS restricted to your actual frontend origin
- [ ] `GET /api/v1/health/deep` returns `auth: "enforced"` and
      `database.wal: true`
- [ ] `GET /api/v1/audit/verify` returns `valid: true`
- [ ] Understood: `NEXT_PUBLIC_API_TOKEN` is visible to anyone who opens the
      page

And the honest one:

- [ ] This is **single-tenant**. Every request sees every merchant's data.
      Do not put two merchants on one instance.
