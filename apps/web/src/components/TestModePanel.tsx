"use client";

/**
 * The one button that touches real Razorpay.
 *
 * This existed only as a curl string in a caption — *"Run POST
 * /api/v1/testmode/recover, pay the link, and this moves"* — which meant the
 * single most convincing thing in the project was reachable only from a
 * terminal. A judge reading the dashboard for the first time would see an
 * honest ₹0.00 on the strongest tile and no way to change it.
 *
 * The panel reports its own preconditions before offering the button, and
 * distinguishes the two that fail differently: creating a link needs only the
 * API keys, while *receiving* the webhook needs a public HTTPS URL. Those get
 * separate lines, because "it didn't work" is a much worse message than "the
 * link was created and Razorpay cannot reach you".
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

interface Status {
  razorpay_configured: boolean;
  webhook_secret_configured: boolean;
  ready: boolean;
  missing: string[];
  webhook_note: string;
  verified_count: number;
}

interface Created {
  stopped_before_execution: boolean;
  case_id: string;
  pay_url?: string;
  reference_id?: string;
  diagnosis?: string | null;
  strategy?: string | null;
  stopping_rule?: string | null;
  note?: string;
  next_step?: string;
}

export function TestModePanel() {
  const [status, setStatus] = useState<Status | null>(null);
  const [created, setCreated] = useState<Created | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const result = await api.testmodeStatus();
      if (cancelled) return;
      if (result.ok) setStatus(result.data);
      else setError(result.error);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const recover = useCallback(async () => {
    setBusy(true);
    setError(null);
    const result = await api.testmodeRecover();
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setCreated(result.data);
  }, []);

  return (
    <section className="rounded-xl border border-verified-500/30 bg-verified-500/[0.04] p-4">
      <header>
        <h2 className="text-sm font-semibold text-paper-50">
          Prove it against real Razorpay
        </h2>
        <p className="mt-1 text-[11px] leading-relaxed text-paper-400">
          One click runs a real case through the real agent and the real policy
          firewall, then creates a genuine ₹1 Razorpay Test Mode payment link.
          Pay it and Razorpay&rsquo;s own signed webhook moves the{" "}
          <span className="text-paper-200">Razorpay verified</span> tile.
        </p>
      </header>

      {/* Until the status fetch resolves there is nothing actionable here, and
          an empty bordered box reads as a broken panel rather than a pending
          one. Server-rendered HTML always shows this first. */}
      {!status && !error && (
        <p className="mt-3 text-[10px] text-paper-500">
          Checking whether Razorpay credentials are configured…
        </p>
      )}

      {status && !status.ready && (
        <p className="mt-3 rounded-lg border border-amber-500/25 bg-amber-500/[0.06] p-2.5 text-[10px] leading-relaxed text-amber-200">
          Not configured: {status.missing.join(", ")}. Set these in{" "}
          <span className="numeric">apps/api/.env</span> and restart the API.
        </p>
      )}

      {status?.ready && (
        <>
          <button
            type="button"
            onClick={recover}
            disabled={busy}
            className="mt-3 rounded-lg border border-verified-500/40 bg-verified-500/10 px-3 py-1.5 text-[11px] font-medium text-paper-50 transition hover:bg-verified-500/20 disabled:opacity-50"
          >
            {busy ? "Calling Razorpay…" : "Create a real ₹1 recovery link"}
          </button>
          <p className="mt-2 text-[10px] leading-relaxed text-paper-500">
            {status.webhook_note}
          </p>
        </>
      )}

      {created?.stopped_before_execution && (
        <div className="mt-3 rounded-lg border border-ink-700 bg-ink-900/60 p-3">
          {/* A refusal is a legitimate outcome and is reported as one. A panel
              that only rendered success would make the firewall invisible
              exactly when it did its job. */}
          <span className="text-[11px] font-medium text-paper-100">
            The firewall refused. Nothing was sent.
          </span>
          <p className="mt-1 text-[10px] leading-relaxed text-paper-400">
            {created.stopping_rule
              ? `Stopping rule ${created.stopping_rule} fired. `
              : ""}
            {created.note}
          </p>
        </div>
      )}

      {created && !created.stopped_before_execution && created.pay_url && (
        <div className="mt-3 rounded-lg border border-verified-500/30 bg-ink-900/60 p-3">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[10px]">
            <div>
              <dt className="text-paper-500">Case</dt>
              <dd className="numeric text-paper-100">{created.case_id}</dd>
            </div>
            <div>
              <dt className="text-paper-500">Diagnosis</dt>
              <dd className="text-paper-100">{created.diagnosis ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-paper-500">Strategy</dt>
              <dd className="text-paper-100">{created.strategy ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-paper-500">Reference</dt>
              <dd className="numeric truncate text-paper-100">
                {created.reference_id}
              </dd>
            </div>
          </dl>
          <a
            href={created.pay_url}
            target="_blank"
            rel="noreferrer"
            className="mt-3 inline-block rounded-lg border border-brand-500/50 bg-brand-500/15 px-3 py-1.5 text-[11px] font-medium text-paper-50 transition hover:bg-brand-500/25"
          >
            Open the Razorpay link and pay ₹1 →
          </a>
          <p className="mt-2 text-[10px] leading-relaxed text-paper-500">
            Card <span className="numeric">4111 1111 1111 1111</span>, any
            future expiry, any CVV, then choose Success.{" "}
            {created.next_step}
          </p>
        </div>
      )}

      {error && (
        <p className="mt-3 text-[10px] leading-relaxed text-amber-300">
          {error}
        </p>
      )}
    </section>
  );
}
