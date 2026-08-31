/**
 * What the agent spent, and what it would cost at published rates (§19.2).
 *
 * Actual spend is Rs 0 and is badged RAZORPAY_VERIFIED rather than ESTIMATED:
 * zero is exactly what was spent, and hedging it would be false modesty in the
 * wrong direction. The projection beside it is ESTIMATED, because a published
 * price list is not a bill.
 *
 * The LIVE / CACHED / DETERMINISTIC split is the honest part. A cached
 * response is never presented as a live one, and a deterministic answer is not
 * dressed up as model reasoning.
 */
import { api, percent } from "@/lib/api";
import { FetchError, MoneyTile, ProvenanceBadge } from "./Provenance";

const SOURCE_STYLES: Record<string, string> = {
  LIVE: "text-brand-400",
  CACHED: "text-verified-500",
  DETERMINISTIC: "text-paper-300",
};

const SOURCE_HINTS: Record<string, string> = {
  LIVE: "A real model call, made now.",
  CACHED: "A committed, content-addressed response — replayed, not re-billed.",
  DETERMINISTIC: "No model involved. A rule table produced this.",
};

export async function CostPanel() {
  const result = await api.cost();
  if (!result.ok) {
    return <FetchError what="Cost" error={result.error} />;
  }
  const c = result.data;
  const total = Math.max(1, c.llm_calls);

  return (
    <section className="space-y-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <MoneyTile
          label="Actual spend"
          figure={c.actual_spend}
          caption="Free tier plus a committed cache; nothing was billed"
        />
        <MoneyTile
          label="Projected at paid rates"
          figure={c.projected_spend}
          caption="What this would cost if it were billed"
        />
      </div>

      <div className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
        <header className="flex items-start justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold text-paper-50">
              Where the answers came from
            </h2>
            <p className="mt-0.5 text-[11px] text-paper-500">
              {c.llm_calls} inference{c.llm_calls === 1 ? "" : "s"} ·{" "}
              {percent(c.cache_hit_rate)} served from cache
            </p>
          </div>
          {/*
            Not RAZORPAY_VERIFIED: Razorpay has nothing to do with how many
            times we called a language model. The badge means "a signed webhook
            proves this", and applying it to an inference count devalues it
            everywhere else it appears.
          */}
          <ProvenanceBadge
            provenance="SIMULATED"
            basis="Counted from our own llm_calls ledger; every call records its own source. Razorpay is not involved in this figure."
          />
        </header>

        <ul className="mt-3 space-y-2">
          {["LIVE", "CACHED", "DETERMINISTIC"].map((source) => {
            const count = c.by_source[source] ?? 0;
            return (
              <li key={source} className="flex items-center gap-2.5">
                <span
                  className={`w-24 shrink-0 text-[11px] font-medium ${SOURCE_STYLES[source]}`}
                  title={SOURCE_HINTS[source]}
                >
                  {source}
                </span>
                <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-800">
                  <span
                    className="block h-full rounded-full bg-brand-500"
                    style={{ width: `${(count / total) * 100}%` }}
                  />
                </span>
                <span className="numeric w-10 shrink-0 text-right text-[11px] text-paper-100">
                  {count}
                </span>
              </li>
            );
          })}
        </ul>

        <div className="numeric mt-3 flex justify-between border-t border-ink-800 pt-3 text-[10px] text-paper-500">
          <span>{c.input_tokens.toLocaleString("en-IN")} input tokens</span>
          <span>{c.output_tokens.toLocaleString("en-IN")} output tokens</span>
        </div>
      </div>
    </section>
  );
}
