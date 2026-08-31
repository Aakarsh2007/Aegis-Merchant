/**
 * Says out loud that the API is unauthenticated, when it is.
 *
 * The API already knows. It sets `X-Auth-Mode: disabled` on every response,
 * logs a warning at startup, and records `unauthenticated_principal: true` in
 * the audit block for every action taken this way. Nothing rendered any of it,
 * so a judge could approve a ₹12,848 recovery, see it succeed, and never learn
 * that the ledger attributed the decision to `anonymous(unauthenticated)`.
 *
 * That is a gap this project in particular cannot leave open. Its claim is that
 * every action is attributable and every number says where it came from — and
 * the actor field, on the one screen where a human exercises authority, was
 * silently empty.
 *
 * Deliberately not styled as an error. Running with no credentials is the
 * intended demo mode: it is what lets `python tasks.py demo` work on a judge's
 * machine with nothing configured, and `create_app` refuses to start in
 * production without a token. The banner states a fact rather than raising an
 * alarm about a supported configuration.
 */
"use client";

import { useEffect, useState } from "react";

type Mode = "disabled" | "enabled" | "unknown";

export function AuthModeBanner() {
  const [mode, setMode] = useState<Mode>("unknown");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
        const token = process.env.NEXT_PUBLIC_API_TOKEN;
        // `/healthz`: unauthenticated, cheap, and the middleware stamps
        // `X-Auth-Mode` on every response including this one. An earlier
        // version sent HEAD to a metrics route, which answers 405 -- the
        // header was still there, so it worked, but reading a header off a
        // Method Not Allowed is the kind of thing that breaks silently later.
        const response = await fetch(`${base}/healthz`, {
          cache: "no-store",
          headers: token ? { authorization: `Bearer ${token}` } : {},
        });
        if (cancelled) return;
        const header = response.headers.get("x-auth-mode");
        setMode(header === "disabled" ? "disabled" : header ? "enabled" : "unknown");
      } catch {
        // The API being unreachable is reported by every other panel already.
        // Adding a sixth copy of that message here would be noise.
        if (!cancelled) setMode("unknown");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (mode !== "disabled") return null;

  return (
    <div className="mb-4 rounded-lg border border-simulated-500/30 bg-simulated-500/[0.06] px-3 py-2">
      <p className="text-[11px] leading-relaxed text-simulated-500">
        <span className="font-semibold">Authentication is off.</span>{" "}
        <span className="text-paper-300">
          <code className="numeric">API_TOKEN</code> is unset, so these
          endpoints are open and every action you take here is recorded in the
          audit ledger as{" "}
          <code className="numeric">anonymous(unauthenticated)</code> — approvals
          included. This is the intended demo mode: it is what lets the project
          run with zero credentials. The API refuses to start in production
          without a token.
        </span>
      </p>
    </div>
  );
}
