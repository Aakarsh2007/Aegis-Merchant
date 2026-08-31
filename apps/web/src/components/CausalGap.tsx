/**
 * The question we have NOT answered, priced in cases.
 *
 * A reviewer put the three claims apart cleanly, and they were right:
 *
 *   ₹1.00     "can this execute and verify a recovery?"     -> yes
 *   ₹60,217   "what might it recover at scale?"             -> a declared model
 *   —         "did RevPilot cause customers to pay?"        -> not proven
 *
 * Every other panel on this page reports something the system did. This one
 * reports something it has not shown, and says exactly what showing it would
 * cost. That is a deliberate inclusion: "not statistically significant" is a
 * disclaimer, and a disclaimer invites the reader to guess how close we are. A
 * completion bar at 4.9% and a count of 1,382 missing cases does not.
 *
 * Two things this panel will never render, because the API does not send them:
 * a p-value, and a projected completion date computed from seeded traffic. The
 * `PowerPlan` type has no field for the first, and `eta` is null until real
 * arrivals give a real velocity.
 */
import { api, percent } from "@/lib/api";
import { FetchError } from "./Provenance";

function Bar({ have, need }: { have: number; need: number }) {
  const pct = need > 0 ? Math.min(100, (have / need) * 100) : 0;
  return (
    <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-800">
      <span
        className="block h-full rounded-full bg-brand-500"
        style={{ width: `${Math.max(pct, have > 0 ? 1.5 : 0)}%` }}
      />
    </span>
  );
}

export async function CausalGap() {
  const [plan, holdout] = await Promise.all([api.power(), api.holdout()]);
  if (!plan.ok) {
    return <FetchError what="Causal gap" error={plan.error} />;
  }
  const p = plan.data;
  const real = holdout.ok ? holdout.data : null;
  const treated = real?.arms?.TREATMENT;
  const control = real?.arms?.CONTROL;

  return (
    <section className="rounded-xl border border-amber-500/25 bg-amber-500/[0.04] p-4">
      <header>
        <h2 className="text-sm font-semibold text-paper-50">
          What we have <span className="text-paper-500">not</span> proven
        </h2>
        <p className="mt-1 text-[11px] leading-relaxed text-paper-400">
          Three questions, three different answers. The third is the one that
          matters commercially, and it is open.
        </p>
      </header>

      <ul className="mt-3 space-y-1.5 text-[11px]">
        <li className="flex gap-2">
          <span className="text-verified-500">✓</span>
          <span className="text-paper-300">
            <span className="font-medium text-paper-100">
              Can it execute and verify a recovery through Razorpay?
            </span>{" "}
            Yes — proven by a signed webhook.
          </span>
        </li>
        <li className="flex gap-2">
          <span className="text-brand-400">~</span>
          <span className="text-paper-300">
            <span className="font-medium text-paper-100">
              What might it recover at scale?
            </span>{" "}
            A declared response model. Real machinery, seeded responses.
          </span>
        </li>
        <li className="flex gap-2">
          <span className="text-amber-400">✗</span>
          <span className="text-paper-300">
            <span className="font-medium text-paper-100">
              Did it cause additional customers to pay?
            </span>{" "}
            <span className="text-amber-300">Not proven.</span> The plan that
            would settle it is pre-registered, below.
          </span>
        </li>
      </ul>

      <div className="mt-4 rounded-lg border border-ink-700 bg-ink-900/60 p-3">
        <div className="flex items-baseline justify-between">
          <span className="text-[11px] font-medium text-paper-100">
            Progress toward a powered test
          </span>
          <span className="numeric text-sm font-semibold text-paper-50">
            {percent(p.completion)}
          </span>
        </div>

        <div className="mt-2.5 space-y-2">
          {(
            [
              ["control", p.have.control, p.need.control],
              ["treated", p.have.treatment, p.need.treatment],
            ] as const
          ).map(([label, have, need]) => (
            <div key={label} className="flex items-center gap-2.5">
              <span className="w-14 shrink-0 text-[10px] text-paper-500">
                {label}
              </span>
              <Bar have={have} need={need} />
              <span className="numeric w-20 shrink-0 text-right text-[10px] text-paper-300">
                {have.toLocaleString("en-IN")} /{" "}
                {need.toLocaleString("en-IN")}
              </span>
            </div>
          ))}
        </div>

        <p className="mt-2.5 text-[10px] leading-relaxed text-paper-500">
          {p.completion_basis}
        </p>

        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 border-t border-ink-800 pt-2.5 text-[10px]">
          <div>
            <dt className="text-paper-500">Cases still needed</dt>
            <dd className="numeric text-paper-100">
              {p.cases_remaining.toLocaleString("en-IN")}
            </dd>
          </div>
          <div>
            <dt className="text-paper-500">
              Payment attempts, at a 12% failure rate
            </dt>
            <dd className="numeric text-paper-100">
              {(
                p.attempts_remaining.at_12pc_failure_rate ?? 0
              ).toLocaleString("en-IN")}
            </dd>
          </div>
          <div>
            <dt className="text-paper-500">Design</dt>
            <dd className="text-paper-100">
              {percent(p.design.control_fraction)} holdout, alpha{" "}
              {p.design.alpha}, power {percent(p.design.power)}
            </dd>
          </div>
          <div>
            <dt className="text-paper-500">Projected completion</dt>
            {/* Null on seeded data, and rendered as an absence rather than a
                guess. A countdown extrapolated from traffic that all arrived at
                once is fiction. */}
            <dd className="text-paper-100">
              {p.eta ?? "unknown — no real arrival rate yet"}
            </dd>
          </div>
        </dl>
      </div>

      {real && (treated?.cases ?? 0) + (control?.cases ?? 0) > 0 && (
        <div className="mt-3 rounded-lg border border-verified-500/25 bg-verified-500/[0.04] p-3">
          <span className="text-[11px] font-medium text-paper-100">
            The holdout, exercised against real Razorpay
          </span>
          <p className="mt-1 text-[10px] leading-relaxed text-paper-400">
            Both arms, real provider. Treated cases got real payment links;
            control cases were never contacted. Settlements are real signed
            webhooks.
          </p>
          <table className="mt-2 w-full text-[10px]">
            <thead>
              <tr className="text-paper-500">
                <th className="text-left font-normal">arm</th>
                <th className="text-right font-normal">cases</th>
                <th className="text-right font-normal">verified</th>
                <th className="text-right font-normal">organic</th>
              </tr>
            </thead>
            <tbody className="numeric text-paper-100">
              {(["TREATMENT", "CONTROL"] as const).map((arm) => (
                <tr key={arm}>
                  <td className="text-left text-paper-300">
                    {arm.toLowerCase()}
                  </td>
                  <td className="text-right">{real.arms[arm]?.cases ?? 0}</td>
                  <td className="text-right">
                    {real.arms[arm]?.razorpay_verified_recoveries ?? 0}
                  </td>
                  <td className="text-right">{real.arms[arm]?.organic ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 border-t border-ink-800 pt-2 text-[10px] leading-relaxed text-amber-300/90">
            {real.what_this_does_not_prove}
          </p>
        </div>
      )}

      <div className="mt-3 border-t border-ink-800 pt-2.5">
        <span className="text-[10px] font-medium text-paper-400">
          Blocked on — and neither is about our code:
        </span>
        <ul className="mt-1 space-y-0.5">
          {p.blocked_on.map((item) => (
            <li key={item} className="text-[10px] leading-relaxed text-paper-500">
              · {item}
            </li>
          ))}
        </ul>
        <p className="mt-2 text-[10px] text-paper-500">
          Full design, stopping rule and abandonment criteria:{" "}
          <span className="text-paper-300">{p.registered}</span>, committed
          before any live data existed. No p-value is shown here until the
          sample is complete.
        </p>
      </div>
    </section>
  );
}
