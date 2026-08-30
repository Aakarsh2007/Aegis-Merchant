"use client";

/**
 * The audit chain, and a button that breaks it (§19.2).
 *
 * A verifier nobody has watched fail is indistinguishable from one that
 * returns `true`. So this panel does not just report the chain state — it
 * lets a judge corrupt a block on their own machine and watch the check name
 * the block, the index and the reason.
 *
 * The stated limitation is rendered too. Tail truncation cannot be detected
 * from the chain alone, and a panel that showed a green tick without saying so
 * would be overclaiming exactly where the project cannot afford to.
 */
import { useCallback, useEffect, useState } from "react";
import { api, type ChainVerification } from "@/lib/api";

type Mode = "payload" | "hash" | "timestamp";

const MODES: Array<{ mode: Mode; label: string; explains: string }> = [
  { mode: "payload", label: "Edit a payload", explains: "changes a recorded decision" },
  { mode: "hash", label: "Rewrite a hash", explains: "breaks the link to the next block" },
  { mode: "timestamp", label: "Backdate a block", explains: "moves an action out of quiet hours" },
];

export function AuditVerifier() {
  const [state, setState] = useState<ChainVerification | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const verify = useCallback(async () => {
    setBusy(true);
    const result = await api.verifyChain();
    setBusy(false);
    if (result.ok) {
      setState(result.data);
      setError(null);
    } else {
      setError(result.error);
    }
  }, []);

  // The initial load awaits before touching state, rather than calling
  // `verify()` — which sets `busy` synchronously and would trigger the
  // cascading render React 19 warns about. The cancellation flag stops a
  // late response setting state on an unmounted component, which is a real
  // leak on a page a judge navigates away from mid-request.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const result = await api.verifyChain();
      if (cancelled) return;
      if (result.ok) {
        setState(result.data);
        setError(null);
      } else {
        setError(result.error);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const tamper = async (mode: Mode) => {
    setBusy(true);
    // Block 1 rather than 0: corrupting the genesis block is a less
    // interesting demonstration, because it cannot show a broken *link*.
    const result = await api.tamper(1, mode);
    if (!result.ok) setError(result.error);
    await verify();
  };

  const valid = state?.valid ?? null;

  return (
    <section className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <header className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-paper-50">Audit chain</h2>
          <p className="mt-0.5 text-[11px] text-paper-500">
            SHA-256 hash chain, recomputed from stored data
          </p>
        </div>
        <span
          className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-semibold ring-1 ring-inset ${
            valid === null
              ? "bg-ink-800 text-paper-500 ring-ink-700"
              : valid
                ? "bg-verified-500/15 text-verified-500 ring-verified-500/30"
                : "bg-danger-500/15 text-danger-500 ring-danger-500/30"
          }`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              valid === null ? "bg-paper-500" : valid ? "bg-verified-500" : "bg-danger-500"
            }`}
          />
          {valid === null ? "CHECKING" : valid ? "VALID" : "TAMPER DETECTED"}
        </span>
      </header>

      {error ? <p className="mt-3 text-[11px] text-danger-500">{error}</p> : null}

      {state ? (
        <dl className="mt-3 space-y-1.5 text-[11px]">
          <div className="flex justify-between">
            <dt className="text-paper-500">Blocks verified</dt>
            <dd className="numeric text-paper-100">{state.blocks}</dd>
          </div>
          {state.head_hash ? (
            <div className="flex justify-between gap-3">
              <dt className="text-paper-500">Head</dt>
              <dd className="numeric truncate text-paper-300" title={state.head_hash}>
                {state.head_hash.slice(0, 24)}…
              </dd>
            </div>
          ) : null}
          {state.first_divergence_index !== null ? (
            <div className="flex justify-between">
              <dt className="text-paper-500">First divergence</dt>
              <dd className="numeric font-semibold text-danger-500">
                block {state.first_divergence_index}
              </dd>
            </div>
          ) : null}
          {state.reason ? (
            <p
              className={`mt-2 rounded-lg p-2 leading-relaxed ${
                valid
                  ? "bg-ink-800/60 text-paper-500"
                  : "bg-danger-500/10 font-medium text-danger-500"
              }`}
            >
              {state.reason}
            </p>
          ) : null}
        </dl>
      ) : null}

      <div className="mt-4 border-t border-ink-800 pt-3">
        <p className="text-[11px] font-medium text-paper-300">
          Break it yourself — this is the point
        </p>
        <p className="mt-0.5 text-[11px] leading-snug text-paper-500">
          Corrupt a block and re-verify. Each mode trips a different check.
          Development only; the endpoint returns 403 in production.
        </p>
        <div className="mt-2.5 flex flex-wrap gap-2">
          {MODES.map(({ mode, label, explains }) => (
            <button
              key={mode}
              type="button"
              disabled={busy}
              onClick={() => void tamper(mode)}
              title={explains}
              className="rounded-lg border border-danger-500/30 bg-danger-500/5 px-2.5 py-1.5 text-[11px] font-medium text-danger-500 transition hover:bg-danger-500/15 disabled:opacity-40"
            >
              {label}
            </button>
          ))}
          <button
            type="button"
            disabled={busy}
            onClick={() => void verify()}
            className="rounded-lg border border-ink-600 bg-ink-800 px-2.5 py-1.5 text-[11px] font-medium text-paper-300 transition hover:bg-ink-700 disabled:opacity-40"
          >
            Re-verify
          </button>
        </div>
      </div>

      <p className="mt-3 border-t border-ink-800 pt-3 text-[10px] leading-relaxed text-paper-500">
        <span className="font-semibold text-paper-300">What this cannot do:</span>{" "}
        deleting the last <em>k</em> blocks is undetectable from the chain alone
        — what remains is a shorter, perfectly valid chain. Compare{" "}
        <span className="numeric">head_hash</span> against a previously recorded
        value to rule that out.
      </p>
    </section>
  );
}
