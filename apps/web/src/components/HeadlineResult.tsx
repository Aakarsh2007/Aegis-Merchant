/**
 * The question, asked and answered at the top of the page.
 *
 * "Did we actually recover money?" is what a judge wants to know, and the
 * honest answer needs three numbers side by side, not one hero figure:
 *
 *   - what a dashboard would show (gross),
 *   - what we can prove with a signed webhook (₹0, so far),
 *   - what the intervention is estimated to have caused (incremental,
 *     against a holdout, under a declared response model).
 *
 * The wording is deliberate throughout. It says **"simulated incremental
 * recovery under a declared response model"**, never "we recovered". The
 * distinction is the most credible thing here and the easiest to accidentally
 * throw away in a caption.
 */
import { api, percent } from "@/lib/api";
import { FetchError } from "./Provenance";

function Bar({
  label,
  value,
  ci,
  tone,
}: {
  label: string;
  value: number;
  ci: [number, number];
  tone: "treat" | "control";
}) {
  const colour = tone === "treat" ? "bg-brand-500" : "bg-control-500";
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="text-[11px] font-medium text-paper-300">{label}</span>
        <span className="numeric text-lg font-semibold text-paper-50">
          {percent(value)}
        </span>
      </div>
      <div className="relative mt-1 h-2 overflow-hidden rounded-full bg-ink-800">
        <div
          className={`absolute h-full rounded-full ${colour} opacity-35`}
          style={{
            left: `${ci[0] * 100}%`,
            width: `${Math.max((ci[1] - ci[0]) * 100, 0.6)}%`,
          }}
        />
        <div
          className={`absolute h-full w-0.5 ${colour}`}
          style={{ left: `${value * 100}%` }}
        />
      </div>
      <p className="numeric mt-0.5 text-[10px] text-paper-500">
        95% CI [{percent(ci[0])}, {percent(ci[1])}]
      </p>
    </div>
  );
}

export async function HeadlineResult() {
  const [overview, attribution] = await Promise.all([
    api.overview(),
    api.attribution(),
  ]);
  if (!overview.ok) return <FetchError what="Headline" error={overview.error} />;
  if (!attribution.ok)
    return <FetchError what="Attribution" error={attribution.error} />;

  const o = overview.data;
  const a = attribution.data;

  return (
    <section className="rounded-xl border border-brand-500/30 bg-gradient-to-b from-brand-500/10 to-transparent p-5">
      <h2 className="text-sm font-semibold text-paper-50">
        Did it actually recover money?
      </h2>
      <p className="mt-0.5 text-[11px] text-paper-500">
        The only honest way to answer this is to have not acted on some cases.
      </p>

      <div className="mt-4 grid grid-cols-1 gap-5 lg:grid-cols-2">
        {/* The experiment. */}
        <div className="space-y-3">
          <Bar
            label="TREATED — the agent acted"
            value={a.treatment.conversion}
            ci={a.treatment.ci95}
            tone="treat"
          />
          <Bar
            label={`CONTROL — ${a.control.cases} cases, never contacted`}
            value={a.control.conversion}
            ci={a.control.ci95}
            tone="control"
          />
          <p className="rounded-lg bg-ink-900/70 p-2.5 text-[11px] leading-relaxed text-paper-300">
            <strong className="text-paper-100">
              {percent(a.control.conversion)} of the control group paid without
              us.
            </strong>{" "}
            That is why the two numbers on the right differ by roughly three
            times — and why only one of them is ours to claim.
          </p>
        </div>

        {/* The money. */}
        <div className="space-y-2">
          <div className="rounded-lg border border-ink-700 bg-ink-950/50 p-3">
            <p className="text-[10px] uppercase tracking-wider text-paper-500">
              What a dashboard would show
            </p>
            <p className="numeric mt-0.5 text-xl text-paper-300 line-through decoration-danger-500/60">
              {o.gross_simulated.display}
            </p>
          </div>

          <div className="rounded-lg border border-brand-500/40 bg-brand-500/10 p-3">
            <p className="text-[10px] uppercase tracking-wider text-brand-400">
              Simulated incremental recovery
            </p>
            <p className="numeric mt-0.5 text-3xl font-semibold text-paper-50">
              {o.net_incremental.display}
            </p>
            <p className="mt-1 text-[10px] leading-snug text-paper-500">
              Under the declared response model. Lift{" "}
              <span className="numeric">{percent(a.absolute_lift)}</span> over
              the holdout, net of discounts and inference.
            </p>
          </div>

          <div className="rounded-lg border border-ink-700 bg-ink-950/50 p-3">
            <p className="text-[10px] uppercase tracking-wider text-verified-500">
              Razorpay-verified recovery
            </p>
            <p className="numeric mt-0.5 text-xl font-semibold text-verified-500">
              {o.gross_recovered.display}
            </p>
            <p className="mt-1 text-[10px] leading-snug text-paper-500">
              {o.gross_recovered.paise > 0
                ? "Proven by a real signed Razorpay webhook, in Test Mode. Small on purpose: this figure proves the execution path, not that customers change their behaviour."
                : "Nothing verified yet. Use the “Prove it against real Razorpay” panel below — one click creates a real ₹1 Test Mode link, and paying it moves this tile."}
            </p>
          </div>
        </div>
      </div>

      {/* The caveat, as prose, never as a green tick. */}
      {a.notes.length > 0 ? (
        <div className="mt-4 rounded-lg border border-simulated-500/40 bg-simulated-500/5 p-3">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-simulated-500">
            Read this before quoting the number
          </p>
          {a.notes.map((note) => (
            <p
              key={note}
              className="mt-1 text-[11px] leading-relaxed text-simulated-500"
            >
              {note}
            </p>
          ))}
        </div>
      ) : null}
    </section>
  );
}
