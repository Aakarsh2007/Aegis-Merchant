/**
 * Four questions, and which of them this system has actually answered.
 *
 * "Prove causality" and "causal lift: not proven" are both true, and together
 * they read as a contradiction — because they answer different questions and
 * nothing on the page separated them. A reviewer proposed this vocabulary and
 * it fixes a real problem: the pitch script had drifted into claiming a signed
 * webhook made a payment *"attributable to us and not to luck"*, which
 * contradicts our own six conditions.
 *
 * `INCREMENTAL` is the one deliberately reported as not reached. Showing three
 * ticks and one open circle is a stronger opening than a paragraph explaining
 * what "simulated" means, because the reader gets the shape of the claim before
 * they read a single number.
 */
import { api } from "@/lib/api";
import { FetchError } from "./Provenance";

export async function ProofStatus() {
  const result = await api.proof();
  if (!result.ok) {
    return <FetchError what="Proof status" error={result.error} />;
  }
  const { levels, summary } = result.data;

  return (
    <section className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-paper-50">
          What is proven, and what isn&rsquo;t
        </h2>
        <span className="text-[10px] text-paper-500">
          four questions, in order — each one needs the one above it
        </span>
      </header>

      <ol className="mt-3 space-y-2">
        {levels.map((level) => (
          <li
            key={level.level}
            className={`rounded-lg border p-2.5 ${
              level.reached
                ? "border-verified-500/25 bg-verified-500/[0.04]"
                : "border-simulated-500/30 bg-simulated-500/[0.05]"
            }`}
          >
            <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
              <span
                className={`shrink-0 text-sm ${
                  level.reached ? "text-verified-500" : "text-simulated-500"
                }`}
                aria-hidden="true"
              >
                {level.reached ? "✓" : "○"}
              </span>
              <span
                className={`shrink-0 text-[11px] font-semibold tracking-wider ${
                  level.reached ? "text-verified-500" : "text-simulated-500"
                }`}
              >
                {level.level}
              </span>
              <span className="text-[11px] text-paper-200">
                {level.question}
              </span>
              {!level.reached ? (
                <span className="shrink-0 rounded bg-simulated-500/20 px-1.5 py-0.5 text-[9px] font-semibold tracking-wider text-simulated-500">
                  NOT REACHED
                </span>
              ) : null}
            </div>
            <p className="mt-1 pl-6 text-[10px] leading-relaxed text-paper-500">
              {level.evidence}
            </p>
          </li>
        ))}
      </ol>

      <p className="mt-3 border-t border-ink-800 pt-3 text-[11px] leading-relaxed text-paper-300">
        {summary}
      </p>
    </section>
  );
}
