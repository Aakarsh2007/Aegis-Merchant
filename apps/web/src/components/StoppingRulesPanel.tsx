/**
 * The brakes, made visible (§19.2).
 *
 * All twelve rules are listed, **including the ones that never fired**. A
 * panel showing only non-zero rules makes an inactive brake indistinguishable
 * from an absent one, and "which brakes exist" is the question this answers.
 *
 * The rule descriptions are here rather than in the API because they are
 * presentation: the API returns rule ids and counts, and a client that wanted
 * different wording should not need a backend change.
 */
import { api } from "@/lib/api";
import { FetchError, ProvenanceBadge } from "./Provenance";

const DESCRIPTIONS: Record<string, string> = {
  S01_ALREADY_RESOLVED: "Already paid — do not contact",
  S02_ATTEMPT_BUDGET: "Attempt budget spent",
  S03_DISCOUNT_BUDGET: "Discount budget spent — retry at 0%",
  S04_CONTACT_CAP_24H: "24-hour contact cap",
  S05_CONTACT_CAP_48H: "48-hour contact cap",
  S06_RECOVERY_WINDOW: "Recovery window closed",
  S07_OPT_OUT: "Opted out — permanent, every case",
  S08_CONSENT_CLASS: "No marketing consent — downgrade or stop",
  S09_QUIET_HOURS: "Quiet hours — held, never dropped",
  S10_PROMISE_TO_PAY: "Active promise — outreach frozen",
  S11_MERCHANT_BUDGET: "Merchant daily/monthly budget",
  S12_KILL_SWITCH: "Autopilot disabled",
};

export async function StoppingRulesPanel() {
  const result = await api.stoppingRules();
  if (!result.ok) {
    return <FetchError what="Stopping rules" error={result.error} />;
  }
  const { rules, total_interceptions, provenance, basis } = result.data;
  const peak = Math.max(1, ...rules.map((r) => r.fired));

  return (
    <section className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <header className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-paper-50">Stopping rules</h2>
          <p className="mt-0.5 text-[11px] text-paper-500">
            {total_interceptions} interception
            {total_interceptions === 1 ? "" : "s"} · all twelve listed, zeroes
            included
          </p>
        </div>
        <ProvenanceBadge provenance={provenance} basis={basis} />
      </header>

      <ul className="mt-3 space-y-1">
        {rules.map((rule) => (
          <li key={rule.rule} className="flex items-center gap-2.5">
            <span
              className="numeric w-10 shrink-0 text-[10px] text-paper-500"
              title={rule.rule}
            >
              {rule.rule.slice(0, 3)}
            </span>
            <span className="flex-1 truncate text-[11px] text-paper-300">
              {DESCRIPTIONS[rule.rule] ?? rule.rule}
            </span>
            <span className="h-1.5 w-20 shrink-0 overflow-hidden rounded-full bg-ink-800">
              <span
                className={`block h-full rounded-full ${
                  rule.fired > 0 ? "bg-brand-500" : "bg-transparent"
                }`}
                style={{ width: `${(rule.fired / peak) * 100}%` }}
              />
            </span>
            <span
              className={`numeric w-8 shrink-0 text-right text-[11px] ${
                rule.fired > 0 ? "font-semibold text-paper-50" : "text-paper-500"
              }`}
            >
              {rule.fired}
            </span>
          </li>
        ))}
      </ul>

      <p className="mt-3 border-t border-ink-800 pt-3 text-[10px] leading-relaxed text-paper-500">
        Termination is proved, not asserted: hypothesis generates hostile
        contexts and checks that every case reaches a terminal state. No
        reachable input produces a case that runs forever.
      </p>
    </section>
  );
}
