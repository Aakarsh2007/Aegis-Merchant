/**
 * The five headline tiles (§19.2).
 *
 * Gross and net are rendered **adjacent and in that order**, with net given
 * the emphasis. This is the single most important layout decision in the
 * dashboard: `Rs 2,02,760` and `Rs 60,217` are both true, they answer
 * different questions, and a viewer shown only the first will take it.
 *
 * The caption under net does the work a badge cannot — it names the control
 * arm, which is the reason the two numbers differ.
 */
import { api, type Overview } from "@/lib/api";
import { CountTile, FetchError, MoneyTile } from "./Provenance";

export async function MetricsBar() {
  const result = await api.overview();
  if (!result.ok) {
    return <FetchError what="Metrics" error={result.error} />;
  }
  const m: Overview = result.data;

  return (
    <section aria-label="Headline metrics">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MoneyTile
          label="At risk"
          figure={m.at_risk}
          caption={`${m.open_cases.value} open cases`}
        />
        <MoneyTile
          label="Gross recovered"
          figure={m.gross_simulated}
          caption="What a dashboard would show"
        />
        <MoneyTile
          label="Net incremental"
          figure={m.net_incremental}
          emphasis
          caption="Estimated causal lift vs the holdout, net of costs"
        />
        <CountTile
          label="Held as control"
          count={m.control_cases}
          caption="Deliberately not acted on, so the number above is falsifiable"
        />
      </div>

      {/*
        The verified tile is rendered even at zero, and deliberately so. Zero
        webhook-proven rupees is the honest state before live traffic, and
        showing it beside the simulated figure is what stops the simulated one
        being read as verified.
      */}
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <MoneyTile
          label="Razorpay verified"
          figure={m.gross_recovered}
          caption={
            m.gross_recovered.paise > 0
              ? "Proven by Razorpay itself. Small on purpose — it proves the path, not the lift."
              : "Nothing proven by Razorpay yet. This zero is the honest figure."
          }
        />
        <CountTile
          label="Unsafe proposals intercepted"
          count={m.interceptions}
          caption="Stopping rules that fired and blocked or degraded an action"
        />
        <CountTile
          label="Awaiting a human"
          count={m.pending_approvals}
          caption="Above the autonomous limit; excludes those past their TTL"
        />
      </div>

      {m.notes.length > 0 ? (
        <div className="mt-3 rounded-xl border border-simulated-500/30 bg-simulated-500/5 p-3">
          {m.notes.map((note) => (
            <p key={note} className="text-[11px] leading-relaxed text-simulated-500">
              {note}
            </p>
          ))}
        </div>
      ) : null}
    </section>
  );
}
