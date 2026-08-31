"use client";

/**
 * Attack the agent and watch it refuse.
 *
 * Showing that a system works proves much less than showing it under attack. A
 * policy firewall nobody has watched refuse is indistinguishable from one that
 * passes everything, and every submission in this track will claim guardrails.
 *
 * Each button runs a real proposal through the **real** `evaluate_policy` — the
 * same function the agent path calls. Nothing is written and no money moves, so
 * it is safe to click repeatedly in front of an audience.
 *
 * The five attacks refuse in four *different* ways, which is the actual point:
 * a clamp, a structural impossibility, a degrade, and a hard block. One red
 * light would suggest a single `if` statement; four mechanisms show layers.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

interface AttackInfo {
  attack: string;
  asks: string;
  why_tempting: string;
  expected: string;
  mechanism: string;
}

interface AttackResult {
  attack: string;
  asked_for: string;
  mechanism: string;
  /** What happened to the REQUEST. Not the policy verdict -- see below. */
  attack_outcome: string;
  attack_outcome_detail: string;
  verdict: string;
  may_execute: boolean;
  capability_token_minted: boolean;
  clamps: Array<{
    field: string;
    asked_for: unknown;
    allowed: unknown;
    reason: string;
    was_a_violation: boolean;
  }>;
  block_reasons: string[];
  stopping_rule: string | null;
  note: string;
}

const LABELS: Record<string, string> = {
  honest_baseline: "A legitimate action",
  discount_90_percent: "Ask for 90% off",
  charge_more_than_owed: "Charge double",
  marketing_to_dnd: "Market to a DND customer",
  act_with_autopilot_off: "Act with the kill switch off",
};

/**
 * Coloured by what happened to the ATTACK, not by the policy verdict.
 *
 * INC-033: this keyed off `verdict`, so `marketing_to_dnd` rendered as the
 * green "allowed" tone with the word PASSED -- on a panel whose entire purpose
 * is showing that dangerous requests do not get through. The system was right
 * (the message class was clamped MARKETING -> TRANSACTIONAL); the label was
 * telling a judge the opposite.
 *
 * `honest_baseline` is the one row that should be green. A firewall that
 * refused all five would prove nothing, so one legitimate action passing is
 * part of the demonstration rather than a gap in it.
 */
function tone(r: AttackResult): "allowed" | "reduced" | "refused" {
  if (r.attack_outcome === "ALLOWED_AS_ASKED") return "allowed";
  if (r.attack_outcome === "NEUTRALISED") return "reduced";
  return "refused";
}

export function AdversarialPanel() {
  const [attacks, setAttacks] = useState<AttackInfo[]>([]);
  const [results, setResults] = useState<Record<string, AttackResult>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const r = await api.attacks();
      if (cancelled) return;
      if (r.ok) setAttacks(r.data.attacks);
      else setError(r.error);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const run = useCallback(async (attack: string) => {
    setBusy(attack);
    const r = await api.runAttack(attack);
    setBusy(null);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    setError(null);
    setResults((prev) => ({ ...prev, [attack]: r.data }));
  }, []);

  return (
    <section className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <header>
        <h2 className="text-sm font-semibold text-paper-50">
          Try to make it do something dangerous
        </h2>
        <p className="mt-0.5 text-[11px] leading-relaxed text-paper-500">
          Each button sends a real proposal through the real policy firewall.
          Nothing is written and no money moves. Note that they are refused in{" "}
          <strong>four different ways</strong> — that is the point.
        </p>
      </header>

      {error ? <p className="mt-3 text-[11px] text-danger-500">{error}</p> : null}

      <ul className="mt-3 space-y-2">
        {attacks.map((info) => {
          const result = results[info.attack];
          const t = result ? tone(result) : null;
          return (
            <li
              key={info.attack}
              className={`rounded-lg border p-3 ${
                t === "refused"
                  ? "border-verified-500/40 bg-verified-500/5"
                  : t === "reduced"
                    ? "border-simulated-500/40 bg-simulated-500/5"
                    : t === "allowed"
                      ? "border-brand-500/40 bg-brand-500/5"
                      : "border-ink-700 bg-ink-950/40"
              }`}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <p className="text-[12px] font-medium text-paper-100">
                    {LABELS[info.attack] ?? info.attack}
                  </p>
                  <p className="mt-0.5 text-[11px] leading-snug text-paper-500">
                    &ldquo;{info.asks}&rdquo;
                  </p>
                </div>
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void run(info.attack)}
                  className="shrink-0 rounded-lg border border-ink-600 bg-ink-800 px-2.5 py-1.5 text-[11px] font-medium text-paper-300 transition hover:bg-ink-700 disabled:opacity-40"
                >
                  {busy === info.attack ? "running…" : result ? "again" : "run"}
                </button>
              </div>

              {result ? (
                <div className="mt-2 border-t border-ink-800 pt-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                        t === "refused"
                          ? "bg-verified-500/20 text-verified-500"
                          : t === "reduced"
                            ? "bg-simulated-500/20 text-simulated-500"
                            : "bg-brand-500/20 text-brand-400"
                      }`}
                    >
                      {result.attack_outcome}
                    </span>
                    <span className="numeric text-[10px] text-paper-500">
                      policy verdict: {result.verdict}
                    </span>
                    <span className="text-[10px] text-paper-500">
                      {result.mechanism}
                    </span>
                    <span
                      className={`text-[10px] ${
                        result.capability_token_minted
                          ? "text-brand-400"
                          : "text-paper-500"
                      }`}
                      title="No capability token means no side effect is possible at all."
                    >
                      token{" "}
                      {result.capability_token_minted ? "minted" : "withheld"}
                    </span>
                  </div>

                  {result.clamps.map((c) => (
                    <p
                      key={c.field}
                      className="numeric mt-1.5 text-[11px] text-simulated-500"
                    >
                      {c.field}: asked {String(c.asked_for)} → allowed{" "}
                      {String(c.allowed)}
                      {c.was_a_violation ? "  (recorded as a violation)" : ""}
                    </p>
                  ))}

                  {result.block_reasons.map((reason) => (
                    <p
                      key={reason}
                      className="mt-1.5 text-[11px] leading-snug text-paper-300"
                    >
                      {reason}
                    </p>
                  ))}

                  {result.stopping_rule ? (
                    <p className="numeric mt-1 text-[10px] text-paper-500">
                      stopping rule {result.stopping_rule}
                    </p>
                  ) : null}
                </div>
              ) : (
                <p className="mt-1.5 text-[10px] leading-snug text-paper-500">
                  Expected: {info.expected}
                </p>
              )}
            </li>
          );
        })}
      </ul>

      <p className="mt-3 border-t border-ink-800 pt-3 text-[10px] leading-relaxed text-paper-500">
        The 90% request is <strong>clamped, not rejected</strong> — the action
        still happens at the ceiling, and the gap is recorded as a violation.
        Clamping to the ceiling rather than the request is deliberate: rewarding
        a model for asking high is how a firewall becomes a suggestion.
      </p>
    </section>
  );
}
