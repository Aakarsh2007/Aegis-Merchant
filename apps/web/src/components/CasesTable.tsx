/**
 * The case list, with the diagnosis *source* shown (§19.2).
 *
 * The `source` column is the "AI judgment" exhibit. A table showing
 * "AUTHENTICATION_ABANDONED · 0.95" is indistinguishable from a model
 * guessing confidently; showing `DETERMINISTIC_EXACT` beside it is what makes
 * the claim *"nine places we chose not to use an LLM"* checkable rather than
 * asserted.
 *
 * Control-arm rows are rendered in grey with a dash where an action would be.
 * They are the counterfactual, and seeing them in the same table as the
 * treated cases is what makes the holdout legible.
 */
import Link from "next/link";
import { api, type CaseSummary } from "@/lib/api";
import { FetchError } from "./Provenance";

const STATUS_STYLES: Record<string, string> = {
  RECOVERED: "bg-verified-500/15 text-verified-500",
  RESOLVED_ORGANIC: "bg-control-500/20 text-paper-300",
  MONITORING: "bg-brand-500/15 text-brand-400",
  AWAITING_APPROVAL: "bg-simulated-500/15 text-simulated-500",
  SUPPRESSED: "bg-ink-800 text-paper-500",
  EXPIRED: "bg-ink-800 text-paper-500",
  REJECTED: "bg-danger-500/15 text-danger-500",
  FAILED_PERMANENT: "bg-danger-500/15 text-danger-500",
};

const SOURCE_LABELS: Record<string, { short: string; hint: string }> = {
  DETERMINISTIC_EXACT: {
    short: "RULE",
    hint: "A rule table matched source, step and reason exactly. No model involved.",
  },
  DETERMINISTIC_FALLBACK: {
    short: "RULE·FB",
    hint: "A rule table matched on (source, step) without a recognised reason.",
  },
  LLM_REVIEWED: {
    short: "LLM",
    hint: "Signals conflicted or confidence was low, so a model was asked for a second opinion.",
  },
  LLM_CACHED: {
    short: "LLM·$",
    hint: "A committed, content-addressed model response — replayed, not re-billed.",
  },
};

function Row({ row }: { row: CaseSummary }) {
  const isControl = row.arm === "CONTROL";
  const source = row.diagnosis_source
    ? (SOURCE_LABELS[row.diagnosis_source] ?? {
        short: row.diagnosis_source,
        hint: row.diagnosis_source,
      })
    : null;

  return (
    <tr className={`border-b border-ink-800 ${isControl ? "opacity-55" : ""}`}>
      <td className="numeric py-2 pr-3 text-[11px] text-paper-300">
        <Link href={`/cases/${row.id}`} className="hover:text-brand-400">
          {row.id}
        </Link>
      </td>
      <td className="numeric py-2 pr-3 text-right text-[11px] text-paper-100">
        {(row.amount_paise / 100).toLocaleString("en-IN")}
      </td>
      <td className="py-2 pr-3">
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
            STATUS_STYLES[row.status] ?? "bg-ink-800 text-paper-500"
          }`}
        >
          {row.status}
        </span>
      </td>
      <td className="py-2 pr-3 text-[11px] text-paper-500">
        {isControl ? (
          <span title="Held as control. No action was taken — this is the counterfactual.">
            CONTROL · not contacted
          </span>
        ) : (
          (row.diagnosis ?? "—")
        )}
      </td>
      <td className="py-2 pr-3">
        {source ? (
          <span
            title={source.hint}
            className={`numeric rounded px-1.5 py-0.5 text-[10px] ${
              row.diagnosis_source?.startsWith("DETERMINISTIC")
                ? "bg-ink-800 text-paper-300"
                : "bg-brand-500/15 text-brand-400"
            }`}
          >
            {source.short}
          </span>
        ) : (
          <span className="text-[10px] text-paper-500">—</span>
        )}
      </td>
      <td className="numeric py-2 text-right text-[11px] text-paper-500">
        {row.confidence !== null ? row.confidence.toFixed(2) : "—"}
      </td>
    </tr>
  );
}

export async function CasesTable({ query = "?limit=25" }: { query?: string }) {
  const result = await api.cases(query);
  if (!result.ok) {
    return <FetchError what="Cases" error={result.error} />;
  }
  const { cases, total } = result.data;

  return (
    <section className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <header className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-paper-50">Cases</h2>
          <p className="mt-0.5 text-[11px] text-paper-500">
            Showing {cases.length} of {total} · the SOURCE column says whether a
            rule or a model decided
          </p>
        </div>
      </header>

      {cases.length === 0 ? (
        <p className="mt-4 rounded-lg bg-ink-800/40 py-6 text-center text-[11px] text-paper-500">
          No cases yet. Run <span className="numeric">python tasks.py batch</span>{" "}
          to put the corpus through the agent.
        </p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-ink-700 text-left">
                {["CASE", "AMOUNT", "STATUS", "DIAGNOSIS", "SOURCE", "CONF"].map(
                  (heading, i) => (
                    <th
                      key={heading}
                      className={`pb-2 text-[10px] font-semibold tracking-wider text-paper-500 ${
                        i === 1 || i === 5 ? "text-right" : ""
                      }`}
                    >
                      {heading}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {cases.map((row) => (
                <Row key={row.id} row={row} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
