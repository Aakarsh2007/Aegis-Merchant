"use client";

/**
 * The panel that answers "what am I looking at?"
 *
 * Everything else on this page assumes you already know what the project
 * claims. A first-time viewer — which is every judge — does not, and a wall of
 * correct panels with no entry point reads as noise rather than as evidence.
 *
 * So this is a four-step guided tour, in the order the argument runs:
 * the number, why it is smaller than expected, what stopped the agent, and
 * whether any of it can be checked. Each step says what to click and what to
 * expect, so it works as a script for a live demo as well as for a reader.
 *
 * Dismissed state is remembered per browser, because the second thing a
 * returning viewer wants is for it to be out of the way.
 */
import { useSyncExternalStore } from "react";

interface Step {
  n: number;
  title: string;
  body: React.ReactNode;
  action?: { label: string; href: string };
}

const STEPS: Step[] = [
  {
    n: 1,
    title: "The number, and the smaller one beside it",
    body: (
      <>
        The tiles below show <strong>₹2,02,760 recovered</strong> and{" "}
        <strong>₹60,217 net incremental</strong>. Both are true. The second is
        what we actually caused, because{" "}
        <strong>39 cases were deliberately never contacted</strong> as a control
        group — and nearly a quarter of them paid anyway. The API cannot return
        the first figure without the second.
      </>
    ),
  },
  {
    n: 2,
    title: "Every number says where it came from",
    body: (
      <>
        Hover any figure for its basis. A badge reads{" "}
        <span className="text-verified-500">RAZORPAY VERIFIED</span> only when a
        signed webhook proves it — which is why{" "}
        <em>Verified by webhook</em> honestly reads <strong>₹0.00</strong>:
        nothing has run against live production traffic.{" "}
        <span className="text-simulated-500">SIMULATED</span> means the
        machinery is real and the customer responses are a declared parameter.
      </>
    ),
  },
  {
    n: 3,
    title: "What it chose NOT to do",
    body: (
      <>
        Read the briefing line above and the{" "}
        <strong>Stopping rules</strong> panel. Twelve named rules, all listed
        including the ones that never fired. An agent that reports its own
        restraint is the only kind you can audit — every other panel shows
        actions taken, which is the half that always looks good.
      </>
    ),
  },
  {
    n: 4,
    title: "Break it yourself",
    body: (
      <>
        In <strong>Audit chain</strong>, click a red tamper button and then
        Re-verify. The chain reports <code>valid: false</code> and names the
        block. In <strong>Break it</strong>, inject a fault and watch the system
        degrade rather than lose work. A verifier nobody has watched fail is
        indistinguishable from one that returns true.
      </>
    ),
    action: { label: "Open the audit endpoint", href: "/api/v1/audit/verify" },
  },
];

const STORAGE_KEY = "revpilot.starthere.dismissed";

/**
 * Read the dismissed flag through `useSyncExternalStore`.
 *
 * `localStorage` is external state, and React 19 is right to flag reading it
 * with a setState inside an effect: that is a cascading render, and it also
 * flashes the panel for a returning viewer before hiding it again. This is the
 * primitive the API is for.
 *
 * The server snapshot is `false` — show the tour — because a first-time viewer
 * is the case this panel exists for, and rendering it on the server keeps the
 * markup stable for anyone reading with JavaScript disabled.
 */
const listeners = new Set<() => void>();

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

function getSnapshot(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    // Private browsing, or storage disabled. Showing the tour is the safe
    // default for the panel whose whole job is orienting a first-time viewer.
    return false;
  }
}

function getServerSnapshot(): boolean {
  return false;
}

function setDismissedFlag(value: boolean): void {
  try {
    if (value) window.localStorage.setItem(STORAGE_KEY, "1");
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing to persist; the panel still toggles for this session.
  }
  listeners.forEach((notify) => notify());
}

export function StartHere() {
  const dismissed = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  if (dismissed) {
    return (
      <button
        type="button"
        onClick={() => setDismissedFlag(false)}
        className="mb-4 rounded-lg border border-ink-700 bg-ink-900/60 px-3 py-1.5 text-[11px] text-paper-500 transition hover:text-brand-400"
      >
        Show the guided tour
      </button>
    );
  }

  return (
    <section className="mb-6 rounded-xl border border-brand-500/30 bg-brand-500/5 p-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-paper-50">
            New here? Four things, in this order.
          </h2>
          <p className="mt-0.5 text-[11px] text-paper-500">
            About ninety seconds. This is also the demo script.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setDismissedFlag(true)}
          className="rounded-lg border border-ink-600 px-2.5 py-1 text-[11px] text-paper-500 transition hover:text-paper-100"
        >
          Dismiss
        </button>
      </header>

      <ol className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
        {STEPS.map((step) => (
          <li
            key={step.n}
            className="rounded-lg border border-ink-700 bg-ink-950/40 p-3"
          >
            <div className="flex items-baseline gap-2">
              <span className="numeric flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-500/20 text-[10px] font-semibold text-brand-400">
                {step.n}
              </span>
              <h3 className="text-[12px] font-semibold text-paper-100">
                {step.title}
              </h3>
            </div>
            <p className="mt-1.5 pl-7 text-[11px] leading-relaxed text-paper-300">
              {step.body}
            </p>
            {step.action ? (
              <a
                href={step.action.href}
                target="_blank"
                rel="noreferrer"
                className="mt-2 ml-7 inline-block text-[11px] text-brand-400 hover:underline"
              >
                {step.action.label} →
              </a>
            ) : null}
          </li>
        ))}
      </ol>

      <p className="mt-4 border-t border-ink-800 pt-3 text-[10px] leading-relaxed text-paper-500">
        <strong className="text-paper-300">What this is.</strong> An agent that
        finds revenue at risk across four leaks — failed payments, abandoned
        checkouts, overdue invoices, failed subscription mandates — diagnoses
        the cause, picks a bounded action inside a policy firewall, and proves
        any recovery against a signed Razorpay webhook. It measures its own
        incremental lift against a holdout group, and reports that the lift is
        not statistically significant when it is not.
      </p>
    </section>
  );
}
