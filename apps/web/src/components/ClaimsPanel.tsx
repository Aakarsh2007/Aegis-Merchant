/**
 * Why each rupee is claimed — and the larger figure that isn't.
 *
 * Two halves, and the second is what makes the first mean anything.
 *
 * Every recovery dashboard can show money coming back. This one shows the six
 * conditions a rupee has to satisfy before it is allowed to count, and beside
 * them the ₹1.39 lakh that arrived and was credited to us at zero — because a
 * control customer paid without being contacted, or because no reference of
 * ours matched the settlement.
 *
 * A system that only ever explains its successes is indistinguishable from one
 * that claims everything. That is the whole argument, and it is the reason this
 * panel leads with the smaller number.
 */
import { api } from "@/lib/api";
import { FetchError, ProvenanceBadge } from "./Provenance";

export async function ClaimsPanel() {
  const result = await api.claims();
  if (!result.ok) {
    return <FetchError what="Claims" error={result.error} />;
  }
  const { claimed, claimed_total, not_claimed, not_claimed_total, not_claimed_count } =
    result.data;

  return (
    <section className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <header>
        <h2 className="text-sm font-semibold text-paper-50">
          Why we claim what we claim
        </h2>
        <p className="mt-1 text-[11px] leading-relaxed text-paper-400">
          Every recovery tool shows money coming back. These are the six
          conditions a rupee must satisfy before it is allowed to count — and
          beside them, the money that arrived and was credited to us at zero.
        </p>
      </header>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* ---------------------------------------------------- claimed */}
        <div className="rounded-lg border border-verified-500/30 bg-verified-500/[0.04] p-3">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-[11px] font-semibold tracking-wider text-verified-500">
              CLAIMED
            </span>
            <ProvenanceBadge
              provenance="RAZORPAY_VERIFIED"
              basis={claimed_total.basis}
            />
          </div>
          <p className="numeric mt-1 text-2xl font-semibold text-paper-50">
            {claimed_total.display}
          </p>

          {claimed.length === 0 ? (
            <p className="mt-3 text-[11px] leading-relaxed text-paper-500">
              Nothing claimed yet on this machine. Use the{" "}
              <span className="text-paper-300">
                Prove it against real Razorpay
              </span>{" "}
              panel above — a rupee appears here once all six conditions hold.
            </p>
          ) : (
            <div className="mt-3 space-y-3">
              {claimed.map((claim) => (
                <div
                  key={claim.case_id}
                  className="rounded-lg border border-ink-700 bg-ink-900/60 p-2.5"
                >
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                    <span className="numeric text-[11px] text-paper-300">
                      {claim.case_id}
                    </span>
                    <span className="numeric text-[11px] font-semibold text-paper-50">
                      {claim.amount.display}
                    </span>
                  </div>
                  <p className="numeric mt-0.5 truncate text-[10px] text-paper-500">
                    {claim.mechanism === "WEBHOOK"
                      ? "signed webhook"
                      : "API reconciliation"}{" "}
                    · {claim.verified_by}
                  </p>

                  <ol className="mt-2 space-y-1">
                    {claim.conditions.map((condition) => (
                      <li key={condition.n} className="flex gap-2">
                        <span className="mt-px shrink-0 text-[10px] text-verified-500">
                          ✓
                        </span>
                        <span
                          className="text-[10px] leading-relaxed text-paper-300"
                          title={condition.detail}
                        >
                          {condition.name}
                        </span>
                      </li>
                    ))}
                  </ol>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ------------------------------------------------ not claimed */}
        <div className="rounded-lg border border-control-500/30 bg-control-500/[0.04] p-3">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-[11px] font-semibold tracking-wider text-control-500">
              ARRIVED, NOT CLAIMED
            </span>
            <ProvenanceBadge
              provenance="SIMULATED"
              basis={not_claimed_total.basis}
            />
          </div>
          <p className="numeric mt-1 text-2xl font-semibold text-paper-50">
            {not_claimed_total.display}
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-paper-400">
            Across {not_claimed_count} cases the customer paid and we credited
            ourselves <span className="numeric text-paper-100">₹0.00</span>.
            This is the number a gross-recovery dashboard would have counted.
          </p>

          <ul className="mt-3 space-y-1.5">
            {not_claimed.map((row) => (
              <li
                key={row.case_id}
                className="flex items-baseline gap-2 border-b border-ink-800 pb-1.5 last:border-0"
              >
                <span className="numeric w-16 shrink-0 text-[10px] text-paper-500">
                  {row.case_id}
                </span>
                <span className="numeric w-24 shrink-0 text-right text-[11px] text-paper-200">
                  {row.amount.display}
                </span>
                {row.arm === "CONTROL" ? (
                  <span className="shrink-0 rounded bg-control-500/20 px-1.5 py-0.5 text-[9px] font-medium tracking-wider text-control-500">
                    HELD AS CONTROL
                  </span>
                ) : null}
                <span
                  className="truncate text-[10px] text-paper-500"
                  title={row.reason}
                >
                  {row.reason}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <p className="mt-3 border-t border-ink-800 pt-3 text-[10px] leading-relaxed text-paper-500">
        The six conditions are ANDed in{" "}
        <span className="numeric">services/attribution.attribute()</span>.
        Failing any one of them moves the payment to the right-hand column. A
        system that only explains its successes is indistinguishable from one
        that claims everything.
      </p>
    </section>
  );
}
