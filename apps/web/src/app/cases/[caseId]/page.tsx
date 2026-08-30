/**
 * The glass-box decision trace for one case (§19.2).
 *
 * The exhibit for "AI judgment": a judge picks a case and follows it from the
 * failure Razorpay reported, through the diagnosis and *who made it*, through
 * the policy decision, to the audit blocks that record each step with a hash
 * they can verify independently.
 *
 * The audit blocks are rendered with their hashes precisely so this page and
 * `/audit/verify` can be cross-checked. They are the same events; a
 * discrepancy is what the chain exists to surface.
 */
import Link from "next/link";
import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { FetchError } from "@/components/Provenance";

const SOURCE_HINTS: Record<string, string> = {
  DETERMINISTIC_EXACT:
    "A rule table matched error_source, error_step and a recognised error_reason. No model was called.",
  DETERMINISTIC_FALLBACK:
    "A rule table matched on (error_source, error_step) without a recognised reason.",
  LLM_REVIEWED:
    "Signals conflicted or confidence was low, so a model was asked for a second opinion.",
  LLM_CACHED: "A committed, content-addressed model response — replayed, not re-billed.",
};

function Field({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-wider text-paper-500">{label}</dt>
      <dd className="numeric mt-0.5 text-[12px] text-paper-100" title={hint}>
        {value}
      </dd>
    </div>
  );
}

export default async function CasePage({
  params,
}: {
  params: Promise<{ caseId: string }>;
}) {
  const { caseId } = await params;
  const result = await api.caseTrace(caseId);

  if (!result.ok) {
    if (result.status === 404) notFound();
    return (
      <main className="mx-auto max-w-4xl px-5 py-6">
        <FetchError what="Case trace" error={result.error} />
      </main>
    );
  }

  const trace = result.data;
  const c = trace.case;
  const isControl = c.arm === "CONTROL";

  return (
    <main className="mx-auto max-w-4xl px-5 py-6">
      <Link href="/" className="text-[11px] text-paper-500 hover:text-brand-400">
        ← Command Center
      </Link>

      <header className="mt-3 border-b border-ink-800 pb-4">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h1 className="numeric text-lg font-semibold text-paper-50">{c.id}</h1>
          <span className="numeric text-lg font-semibold text-paper-50">
            Rs {(c.amount_paise / 100).toLocaleString("en-IN")}
          </span>
        </div>
        <p className="mt-1 text-[11px] text-paper-500">
          {c.playbook} · {c.status}
          {isControl ? (
            <span className="ml-2 rounded bg-control-500/20 px-1.5 py-0.5 text-control-500">
              CONTROL — deliberately not acted on
            </span>
          ) : null}
        </p>
      </header>

      <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <section className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
          <h2 className="text-xs font-semibold text-paper-300">
            1 · What Razorpay reported
          </h2>
          <dl className="mt-3 space-y-2">
            <Field label="error_source" value={trace.failure.error_source ?? "—"} />
            <Field label="error_step" value={trace.failure.error_step ?? "—"} />
            <Field label="error_reason" value={trace.failure.error_reason ?? "—"} />
          </dl>
        </section>

        <section className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
          <h2 className="text-xs font-semibold text-paper-300">
            2 · What we concluded, and who concluded it
          </h2>
          <dl className="mt-3 space-y-2">
            <Field label="category" value={trace.diagnosis.category ?? "—"} />
            <Field
              label="decided by"
              value={trace.diagnosis.source ?? "—"}
              hint={
                trace.diagnosis.source
                  ? SOURCE_HINTS[trace.diagnosis.source]
                  : undefined
              }
            />
            <Field
              label="confidence"
              value={
                trace.diagnosis.confidence !== null
                  ? trace.diagnosis.confidence.toFixed(2)
                  : "—"
              }
              hint="How specific the matching evidence was — not a model's feeling."
            />
          </dl>
        </section>
      </div>

      {trace.approvals.length > 0 ? (
        <section className="mt-4 rounded-xl border border-simulated-500/30 bg-simulated-500/5 p-4">
          <h2 className="text-xs font-semibold text-simulated-500">
            3 · Escalated to a human
          </h2>
          <ul className="mt-2 space-y-1 text-[11px] text-paper-300">
            {trace.approvals.map((a, i) => (
              <li key={i} className="numeric">
                {String(a.status)} · {String(a.trigger_rung)} ·{" "}
                {String(a.trigger_reason)}
                {a.reviewed_by ? ` · by ${String(a.reviewed_by)}` : ""}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className="mt-4 rounded-xl border border-ink-700 bg-ink-900/60 p-4">
        <h2 className="text-xs font-semibold text-paper-300">
          {trace.approvals.length > 0 ? "4" : "3"} · The audit chain for this case
        </h2>
        <p className="mt-0.5 text-[11px] text-paper-500">
          Every hash below is recomputable. Cross-check against{" "}
          <span className="numeric">/api/v1/audit/verify</span>.
        </p>

        {trace.audit.length === 0 ? (
          <p className="mt-3 text-[11px] text-paper-500">
            No audit blocks for this case yet.
          </p>
        ) : (
          <ol className="mt-3 space-y-2">
            {trace.audit.map((block) => (
              <li
                key={block.block_index}
                className="rounded-lg border border-ink-800 bg-ink-950/40 p-2.5"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="numeric text-[11px] font-medium text-brand-400">
                    #{block.block_index} {block.event_name}
                  </span>
                  <span className="numeric text-[10px] text-paper-500">
                    {block.actor}
                  </span>
                </div>
                <p
                  className="numeric mt-1 truncate text-[10px] text-paper-500"
                  title={block.current_hash}
                >
                  {block.current_hash.slice(0, 32)}…
                </p>
                <pre className="numeric mt-1.5 overflow-x-auto whitespace-pre-wrap break-all text-[10px] leading-relaxed text-paper-300">
                  {JSON.stringify(block.payload, null, 1)}
                </pre>
              </li>
            ))}
          </ol>
        )}
      </section>

      {isControl ? (
        <p className="mt-4 rounded-xl border border-control-500/30 bg-control-500/5 p-3 text-[11px] leading-relaxed text-paper-300">
          This case was assigned to the <strong>control arm</strong> and was
          never contacted. If it settled, that settlement is counted as{" "}
          <span className="numeric">RESOLVED_ORGANIC</span>, not as a recovery —
          counting it would destroy the measurement the holdout exists to
          provide.
        </p>
      ) : null}
    </main>
  );
}
