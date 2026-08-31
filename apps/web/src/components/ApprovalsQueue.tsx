/**
 * The human-in-the-loop queue (§19.2).
 *
 * The `policy_applied_hash` is **displayed**, not hidden in the request. That
 * is the point of the gate: a reviewer approves a specific action with
 * specific numbers, the hash is what identifies it, and if anything changes
 * between this screen and execution the server refuses with 409.
 *
 * Rows past their TTL are shown as expired rather than filtered out. Hiding
 * them would make the queue look healthier than it is, and "something aged out
 * unactioned" is a signal a reviewer needs about their own response time.
 */
import { api, rupees, type Approval } from "@/lib/api";
import { FetchError } from "./Provenance";

function remaining(seconds: number): string {
  if (seconds <= 0) return "expired";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours > 0 ? `${hours}h ${minutes}m left` : `${minutes}m left`;
}

function ApprovalRow({ approval }: { approval: Approval }) {
  let applied: Record<string, unknown> = {};
  try {
    applied = JSON.parse(approval.policy_applied) as Record<string, unknown>;
  } catch {
    // A malformed record must still render; the reviewer needs to see the
    // hash and the amount even if the detail cannot be parsed.
  }

  return (
    <li
      className={`rounded-lg border p-3 ${
        approval.expired
          ? "border-ink-800 bg-ink-900/40 opacity-60"
          : "border-brand-500/30 bg-brand-500/5"
      }`}
    >
      <div className="flex items-baseline justify-between gap-3">
        <span className="numeric text-sm font-semibold text-paper-50">
          {/*
            minimumFractionDigits, or currency loses a digit: Rs 20,055.6 was
            rendered for 2005560 paise. `toLocaleString` drops a trailing zero
            by default, which is correct for a quantity and wrong for money --
            and this figure sits on a card asking a human to authorise it.
          */}
          Rs {rupees(approval.amount_paise)}
        </span>
        <span
          className={`text-[10px] font-medium ${
            approval.expired ? "text-danger-500" : "text-paper-500"
          }`}
        >
          {remaining(approval.seconds_remaining)}
        </span>
      </div>

      <p className="mt-1 text-[11px] text-paper-300">{approval.trigger_reason}</p>
      <p className="numeric mt-0.5 text-[10px] text-paper-500">
        {approval.case_id} · rung {approval.trigger_rung}
      </p>

      {Object.keys(applied).length > 0 ? (
        <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 border-t border-ink-800 pt-2 text-[10px]">
          {Object.entries(applied).map(([key, value]) => (
            <div key={key} className="flex gap-1.5">
              <dt className="text-paper-500">{key.replace(/_/g, " ")}</dt>
              <dd className="numeric text-paper-300">{String(value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      {/*
        Shown deliberately. The reviewer is approving THIS hash; the server
        rejects the action with 409 if the underlying proposal has changed.
      */}
      <p
        className="numeric mt-2 truncate text-[10px] text-paper-500"
        title={`policy_applied_hash — presented back on approval; a mismatch is refused with 409.\n${approval.policy_applied_hash}`}
      >
        hash {approval.policy_applied_hash.slice(0, 24)}…
      </p>
    </li>
  );
}

export async function ApprovalsQueue() {
  const result = await api.approvals();
  if (!result.ok) {
    return <FetchError what="Approvals" error={result.error} />;
  }
  const { approvals, count } = result.data;

  return (
    <section className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <header>
        <h2 className="text-sm font-semibold text-paper-50">Needs a human</h2>
        <p className="mt-0.5 text-[11px] text-paper-500">
          Above the autonomous limit, or the policy clamped the proposal
        </p>
      </header>

      {count === 0 ? (
        <p className="mt-4 rounded-lg bg-ink-800/40 py-6 text-center text-[11px] text-paper-500">
          Nothing waiting. Approvals appear here when an action exceeds the
          merchant&apos;s autonomous limit.
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {approvals.map((approval) => (
            <ApprovalRow key={approval.id} approval={approval} />
          ))}
        </ul>
      )}
    </section>
  );
}
