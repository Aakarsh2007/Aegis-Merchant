/**
 * Treatment versus control, with intervals (§19.2).
 *
 * The panel that answers the question a judge will certainly ask: *how do you
 * know they would not have paid anyway?*
 *
 * Two things are rendered that a flattering dashboard would omit:
 *
 * - The **control conversion rate**, right next to treatment. Nearly a quarter
 *   of the holdout paid without us, and that is the whole reason gross and
 *   incremental differ by a factor of three.
 * - The **significance caveat as a sentence**, not a green tick. With a
 *   hackathon-sized batch the honest answer is usually "not significant", and
 *   an unqualified 6% on a dashboard reads as a result rather than as noise.
 */
import { api, percent, rupees, type Attribution } from "@/lib/api";
import { FetchError, ProvenanceBadge } from "./Provenance";

function ArmRow({
  name,
  arm,
  tone,
}: {
  name: string;
  arm: Attribution["treatment"];
  tone: "brand" | "control";
}) {
  const bar = tone === "brand" ? "bg-brand-500" : "bg-control-500";
  return (
    <div className="py-2.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-xs font-semibold tracking-wide text-paper-300">{name}</span>
        <span className="numeric text-sm text-paper-50">
          {percent(arm.conversion)}
          <span className="ml-2 text-[11px] text-paper-500">
            {arm.paid}/{arm.cases}
          </span>
        </span>
      </div>
      {/* The interval, drawn to scale. Overlapping bars are the argument. */}
      <div className="relative mt-1.5 h-1.5 overflow-hidden rounded-full bg-ink-800">
        <div
          className={`absolute h-full rounded-full ${bar} opacity-40`}
          style={{
            left: `${arm.ci95[0] * 100}%`,
            width: `${Math.max((arm.ci95[1] - arm.ci95[0]) * 100, 0.5)}%`,
          }}
        />
        <div
          className={`absolute h-full w-0.5 ${bar}`}
          style={{ left: `${arm.conversion * 100}%` }}
        />
      </div>
      <div className="numeric mt-1 text-[10px] text-paper-500">
        95% CI [{percent(arm.ci95[0])}, {percent(arm.ci95[1])}]
      </div>
    </div>
  );
}

export async function AttributionPanel() {
  const result = await api.attribution();
  if (!result.ok) {
    return <FetchError what="Attribution" error={result.error} />;
  }
  const a: Attribution = result.data;

  return (
    <section className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <header className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-paper-50">Incremental lift</h2>
          <p className="mt-0.5 text-[11px] text-paper-500">
            Against an untouched holdout arm
          </p>
        </div>
        <ProvenanceBadge
          provenance="SIMULATED"
          basis="Real attribution machinery over the seeded corpus; responses are a declared parameter."
        />
      </header>

      <div className="mt-3 divide-y divide-ink-800">
        <ArmRow name="TREATMENT" arm={a.treatment} tone="brand" />
        <ArmRow name="CONTROL — never contacted" arm={a.control} tone="control" />
      </div>

      <dl className="mt-4 space-y-2 border-t border-ink-800 pt-3">
        <div className="flex items-baseline justify-between">
          <dt className="text-xs text-paper-500">Absolute lift</dt>
          <dd
            className={`numeric text-sm font-semibold ${
              a.absolute_lift < 0 ? "text-danger-500" : "text-paper-50"
            }`}
          >
            {percent(a.absolute_lift)}
          </dd>
        </div>
        <div className="flex items-baseline justify-between">
          <dt className="text-xs text-paper-500">Gross recovered</dt>
          <dd className="numeric text-sm text-paper-300">
            Rs {rupees(a.gross_recovered_paise)}
          </dd>
        </div>
        <div className="flex items-baseline justify-between">
          <dt className="text-xs font-medium text-brand-400">Net incremental</dt>
          <dd className="numeric text-sm font-semibold text-brand-400">
            Rs {rupees(a.net_incremental_paise)}
          </dd>
        </div>
      </dl>

      {/*
        The caveat, as prose. `lift_is_significant` exists as a boolean too,
        and a boolean is something a client can forget to render.
      */}
      {a.notes.length > 0 ? (
        <div className="mt-3 rounded-lg border border-simulated-500/30 bg-simulated-500/5 p-2.5">
          {a.notes.map((note) => (
            <p key={note} className="text-[11px] leading-relaxed text-simulated-500">
              {note}
            </p>
          ))}
        </div>
      ) : null}

      {/*
        The actual test, spelled out. This panel used to lean on
        `lift_is_significant` plus a note saying the intervals overlapped --
        which is a weaker statement than the data supports and, as a reviewer
        pointed out, a weak thing to say out loud. The honest sentence is
        available: here is the lift, here is the p-value, here is the interval
        on the difference, and it contains zero.
      */}
      {a.has_control_arm && a.significance.well_defined ? (
        <div className="mt-3 rounded-lg border border-ink-700 bg-ink-800/40 p-2.5">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <span className="text-[11px] text-paper-300">
              Two-proportion z-test
            </span>
            <span className="numeric text-[11px] text-paper-100">
              z = {a.significance.z.toFixed(2)} · p ={" "}
              <span
                className={
                  a.significance.is_significant
                    ? "font-semibold text-verified-500"
                    : "font-semibold text-simulated-500"
                }
              >
                {a.significance.p_value.toFixed(4)}
              </span>
            </span>
          </div>
          <p className="mt-1.5 text-[10px] leading-relaxed text-paper-500">
            Lift{" "}
            <span className="numeric text-paper-300">
              {(a.significance.diff * 100).toFixed(2)} points
            </span>
            , 95% interval on the difference{" "}
            <span className="numeric text-paper-300">
              {(a.significance.diff_ci95[0] * 100).toFixed(1)} to{" "}
              {(a.significance.diff_ci95[1] * 100).toFixed(1)} points
            </span>
            .{" "}
            {a.significance.is_significant
              ? "The interval excludes zero."
              : "The interval contains zero, so the observed lift is indistinguishable from chance at this sample size."}
          </p>
        </div>
      ) : null}

      {!a.has_control_arm ? (
        <p className="mt-3 text-[11px] text-danger-500">
          No control arm in this population: incremental cannot be computed and
          is not claimed.
        </p>
      ) : null}
    </section>
  );
}
