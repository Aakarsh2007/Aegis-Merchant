/**
 * The money identity, shown as an identity.
 *
 * A reviewer added up the three figures this dashboard leads with and found
 * them ₹3,522.54 short of each other. They asked the fair question: *if the
 * headline numbers don't reconcile, can I trust the attribution system?*
 *
 * The arithmetic was right. The **layout** was wrong — three correct quantities
 * arranged as `gross → claimable + not claimed`, which reads as a partition and
 * is not one. Incremental is an estimate over the treated arm's exposure, and it
 * happens to be smaller than gross, which is exactly what made it look like a
 * slice of gross.
 *
 * So this panel does two things a prose paragraph could not. It shows the sum
 * that does hold, with a rule under it and the residual printed — a reader can
 * *see* the zero instead of being told about it. And it puts the estimate below
 * that rule, visually outside the addition, labelled as an estimate.
 *
 * Presenting an estimate as a subset of a total is the overstatement this whole
 * project exists to refuse. It had gotten into our own headline, which is the
 * most visible place available and, evidently, the least examined.
 */
import { api } from "@/lib/api";
import { FetchError, ProvenanceBadge } from "./Provenance";

export async function ReconciliationPanel() {
  const result = await api.reconciliation();
  if (!result.ok) {
    return <FetchError what="Reconciliation" error={result.error} />;
  }
  const {
    arrived,
    driven,
    organic,
    demo_verified,
    incremental_estimate,
    residual_paise,
    balances,
    claimed_share,
  } = result.data;

  return (
    <section className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-paper-50">
          Where the money went
        </h2>
        <code className="rounded bg-ink-800 px-1.5 py-0.5 text-[10px] text-paper-400">
          arrived = driven + organic
        </code>
      </header>
      <p className="mt-1 text-[11px] leading-relaxed text-paper-400">
        Two of these add up, and they add up exactly. The third is an estimate
        and sits below the line on purpose.
      </p>

      {/* ------------------------------------------------ the sum */}
      <div className="mt-4 space-y-2">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 rounded-lg border border-ink-700 bg-ink-800/40 p-3">
          <div>
            <span className="text-[11px] font-semibold tracking-wider text-paper-300">
              MONEY THAT ARRIVED
            </span>
            <ProvenanceBadge
              provenance={arrived.provenance}
              basis={arrived.basis}
            />
          </div>
          <p className="numeric text-2xl font-semibold text-paper-50">
            {arrived.display}
          </p>
        </div>

        <ul className="space-y-2 pl-3">
          <li className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 rounded-lg border border-ink-700/70 bg-ink-900/40 p-2.5">
            <span className="text-[11px] text-paper-300">
              <span className="text-paper-600">├─</span> recovered on a path we
              drove
              <ProvenanceBadge
                provenance={driven.provenance}
                basis={driven.basis}
              />
            </span>
            <span className="numeric text-sm font-semibold text-paper-100">
              {driven.display}
            </span>
          </li>
          <li className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 rounded-lg border border-simulated-500/25 bg-simulated-500/[0.04] p-2.5">
            <span className="text-[11px] text-paper-300">
              <span className="text-paper-600">└─</span> arrived organically,
              credited{" "}
              <span className="numeric font-semibold text-simulated-500">
                ₹0.00
              </span>
              <ProvenanceBadge
                provenance={organic.provenance}
                basis={organic.basis}
              />
            </span>
            <span className="numeric text-sm font-semibold text-paper-100">
              {organic.display}
            </span>
          </li>
        </ul>

        {/* The residual, printed rather than promised. */}
        <div
          className={`flex flex-wrap items-baseline justify-between gap-2 rounded-lg border p-2.5 ${
            balances
              ? "border-verified-500/30 bg-verified-500/[0.05]"
              : "border-red-500/40 bg-red-500/[0.06]"
          }`}
        >
          <span className="text-[11px] text-paper-300">
            Residual{" "}
            <span className="text-paper-500">
              — arrived minus the two rows above
            </span>
          </span>
          <span
            className={`numeric text-sm font-semibold ${
              balances ? "text-verified-500" : "text-red-400"
            }`}
          >
            ₹{(residual_paise / 100).toFixed(2)}{" "}
            {balances ? "✓ balances" : "✗ DOES NOT BALANCE"}
          </span>
        </div>
      </div>

      {/* ------------------------------- below the line: the estimate */}
      <div className="mt-4 border-t border-dashed border-ink-700 pt-4">
        <div className="rounded-lg border border-simulated-500/30 bg-simulated-500/[0.05] p-3">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <div>
              <span className="text-[11px] font-semibold tracking-wider text-simulated-500">
                INCREMENTAL — ESTIMATE, NOT A SLICE
              </span>
              <ProvenanceBadge
                provenance={incremental_estimate.provenance}
                basis={incremental_estimate.basis}
              />
            </div>
            <p className="numeric text-xl font-semibold text-paper-50">
              {incremental_estimate.display}
            </p>
          </div>
          <p className="mt-2 text-[10px] leading-relaxed text-paper-500">
            The measured lift applied to the treated arm&rsquo;s exposure, less
            costs — <strong className="text-paper-400">not</strong> a subset of
            the figures above, which is why it is below the line. That is{" "}
            <span className="numeric text-paper-300">
              {(claimed_share * 100).toFixed(1)}%
            </span>{" "}
            of everything that arrived.
          </p>
        </div>

        {demo_verified.paise > 0 ? (
          <p className="mt-2 text-[10px] leading-relaxed text-paper-500">
            Held out of the identity entirely:{" "}
            <span className="numeric text-verified-500">
              {demo_verified.display}
            </span>{" "}
            of real Razorpay Test Mode recoveries. A demonstration of mechanism
            is not a data point, so it is reported and not counted.
          </p>
        ) : null}
      </div>
    </section>
  );
}
