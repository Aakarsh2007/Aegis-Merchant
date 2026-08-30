"use client";

/**
 * Fault injection (§19.2).
 *
 * Same argument as the tamper button: a resilience claim nobody has watched
 * fail is indistinguishable from an absent one. Each button states what it
 * breaks **and what should happen next**, because a chaos control with no
 * stated expectation proves nothing — a viewer cannot tell a graceful
 * degradation from a bug.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

interface FaultInfo {
  fault: string;
  effect: string;
}

const LABELS: Record<string, string> = {
  provider_down: "Provider down",
  provider_slow: "Provider slow",
  provider_duplicate: "Duplicate reference",
  llm_quota_exhausted: "LLM quota spent",
};

export function ChaosPanel() {
  const [faults, setFaults] = useState<FaultInfo[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [expectation, setExpectation] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const result = await api.faults();
      if (cancelled) return;
      if (result.ok) {
        setFaults(result.data.faults);
        setActive(result.data.active);
      } else {
        setError(result.error);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const inject = useCallback(async (fault: string) => {
    setBusy(true);
    const result = await api.injectFault(fault);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setError(null);
    setActive(result.data.active ?? null);
    setExpectation(result.data.expected_behaviour ?? null);
  }, []);

  return (
    <section className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <header className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-paper-50">Break it</h2>
          <p className="mt-0.5 text-[11px] text-paper-500">
            Inject a fault and watch the system degrade rather than lose work
          </p>
        </div>
        {active ? (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-danger-500/15 px-2 py-0.5 text-[10px] font-semibold text-danger-500 ring-1 ring-inset ring-danger-500/30">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-danger-500" />
            {LABELS[active] ?? active}
          </span>
        ) : null}
      </header>

      {error ? <p className="mt-3 text-[11px] text-danger-500">{error}</p> : null}

      <div className="mt-3 flex flex-wrap gap-2">
        {faults.map((f) => (
          <button
            key={f.fault}
            type="button"
            disabled={busy}
            title={f.effect}
            onClick={() => void inject(f.fault)}
            className={`rounded-lg border px-2.5 py-1.5 text-[11px] font-medium transition disabled:opacity-40 ${
              active === f.fault
                ? "border-danger-500/60 bg-danger-500/20 text-danger-500"
                : "border-simulated-500/30 bg-simulated-500/5 text-simulated-500 hover:bg-simulated-500/15"
            }`}
          >
            {LABELS[f.fault] ?? f.fault}
          </button>
        ))}
        <button
          type="button"
          disabled={busy || !active}
          onClick={() => void inject("clear")}
          className="rounded-lg border border-ink-600 bg-ink-800 px-2.5 py-1.5 text-[11px] font-medium text-paper-300 transition hover:bg-ink-700 disabled:opacity-40"
        >
          Clear
        </button>
      </div>

      {/* What the fault does, and what should happen — the part that makes
          the button evidence rather than decoration. */}
      {active ? (
        <div className="mt-3 space-y-1.5 rounded-lg bg-ink-800/50 p-2.5">
          <p className="text-[11px] leading-relaxed text-paper-300">
            {faults.find((f) => f.fault === active)?.effect}
          </p>
          {expectation ? (
            <p className="text-[11px] leading-relaxed text-brand-400">{expectation}</p>
          ) : null}
        </div>
      ) : (
        <p className="mt-3 text-[10px] leading-relaxed text-paper-500">
          Each button states what it breaks and what should happen next. A chaos
          control with no stated expectation proves nothing.
        </p>
      )}
    </section>
  );
}
