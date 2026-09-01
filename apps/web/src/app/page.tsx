/**
 * The Command Center.
 *
 * Layout order is the argument, in sequence:
 *
 * 1. **The numbers**, with gross and net adjacent — because the first question
 *    is "how much", and the second is "how do you know".
 * 2. **The lift panel**, which answers the second question with a control arm.
 * 3. **The brakes and the ledger**, which answer "what stopped it" and "can I
 *    check any of this".
 * 4. **The cases**, where a judge can follow one recovery end to end.
 *
 * Every panel is a server component that fetches independently, so a failing
 * endpoint degrades one card rather than blanking the page — and each failure
 * renders an explicit error instead of a zero.
 */
import { Suspense } from "react";
import { ApprovalsQueue } from "@/components/ApprovalsQueue";
import { AttributionPanel } from "@/components/AttributionPanel";
import { AuditVerifier } from "@/components/AuditVerifier";
import { AdversarialPanel } from "@/components/AdversarialPanel";
import { AuthModeBanner } from "@/components/AuthModeBanner";
import { CausalGap } from "@/components/CausalGap";
import { ClaimsPanel } from "@/components/ClaimsPanel";
import { CasesTable } from "@/components/CasesTable";
import { ChaosPanel } from "@/components/ChaosPanel";
import { CostPanel } from "@/components/CostPanel";
import { HeadlineResult } from "@/components/HeadlineResult";
import { MetricsBar } from "@/components/MetricsBar";
import { MorningBriefing } from "@/components/MorningBriefing";
import { PipelineStream } from "@/components/PipelineStream";
import { StartHere } from "@/components/StartHere";
import { TestModePanel } from "@/components/TestModePanel";
import { WhereAIStops } from "@/components/WhereAIStops";
import { StoppingRulesPanel } from "@/components/StoppingRulesPanel";

export const dynamic = "force-dynamic";

function Skeleton({ label }: { label: string }) {
  return (
    <div className="rounded-xl border border-ink-700 bg-ink-900/40 p-4">
      <div className="h-3 w-32 animate-pulse rounded bg-ink-700" />
      <div className="mt-3 h-6 w-24 animate-pulse rounded bg-ink-800" />
      <p className="mt-3 text-[10px] text-paper-500">Loading {label}…</p>
    </div>
  );
}

export default function Page() {
  return (
    <main className="mx-auto max-w-[1400px] px-5 py-6">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4 border-b border-ink-800 pb-5">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-paper-50">
            RevPilot
            <span className="ml-2 text-sm font-normal text-paper-500">
              Revenue Recovery Command Center
            </span>
          </h1>
          <p className="mt-1 max-w-2xl text-[11px] leading-relaxed text-paper-500">
            An agent that finds lost revenue and acts on it inside a policy
            firewall. Every rupee figure below carries a provenance badge —{" "}
            <span className="text-verified-500">RAZORPAY VERIFIED</span> means a
            signed webhook proves it,{" "}
            <span className="text-simulated-500">SIMULATED</span> means real
            machinery over a seeded corpus,{" "}
            <span className="text-estimated-500">ESTIMATED</span> means a
            projection. Hover any number for its basis.
          </p>
        </div>
        <div className="text-right">
          <p className="text-[10px] uppercase tracking-wider text-paper-500">
            Merchant
          </p>
          <p className="text-sm font-medium text-paper-100">GlowKart</p>
        </div>
      </header>

      {/* Stated before anything else, because it changes how every action on
           this page should be read. */}
      <AuthModeBanner />

      <StartHere />

      {/* 1. The question a judge actually has, answered first. */}
      <Suspense fallback={<Skeleton label="the headline result" />}>
        <HeadlineResult />
      </Suspense>

      {/* 2. The tiles behind it. */}
      <div className="mt-6">
        <Suspense fallback={<Skeleton label="metrics" />}>
          <MetricsBar />
        </Suspense>
      </div>

      {/* 3. The argument, immediately after the numbers it is about: six
             conditions for what we claim, and the larger figure we don't. */}
      <div className="mt-6">
        <Suspense fallback={<Skeleton label="claims" />}>
          <ClaimsPanel />
        </Suspense>
      </div>

      {/* 4. The one button that touches real Razorpay. Directly under the
             tile it moves, because the tile's honest zero is otherwise a dead
             end -- the instruction used to be a curl string in a caption. */}
      <div className="mt-6">
        <TestModePanel />
      </div>

      {/* 5. The limitation, stated before the features rather than after.
             Placed here deliberately: a reader who sees Rs 2,02,760 and then
             Rs 60,217 should learn what neither number proves BEFORE they are
             shown eight panels of things that work. Burying it at the bottom
             would be technically honest and practically misleading. */}
      <div className="mt-6">
        <Suspense fallback={<Skeleton label="the causal gap" />}>
          <CausalGap />
        </Suspense>
      </div>

      {/* 6. Why it is an AI project, and where the AI is not allowed. */}
      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <WhereAIStops />
        <AdversarialPanel />
      </div>

      {/* 7. What it chose not to do, and whether any of it can be checked. */}
      <div className="mt-6">
        <Suspense fallback={<Skeleton label="briefing" />}>
          <MorningBriefing />
        </Suspense>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="space-y-4">
          <Suspense fallback={<Skeleton label="stopping rules" />}>
            <StoppingRulesPanel />
          </Suspense>
          <Suspense fallback={<Skeleton label="approvals" />}>
            <ApprovalsQueue />
          </Suspense>
        </div>

        <div className="space-y-4">
          <AuditVerifier />
          <ChaosPanel />
        </div>

        <div className="flex min-h-[520px] flex-col gap-4">
          <PipelineStream />
          <Suspense fallback={<Skeleton label="attribution detail" />}>
            <AttributionPanel />
          </Suspense>
        </div>
      </div>

      {/* 8. Follow one case end to end. */}
      <div className="mt-6">
        <CasesTable />
      </div>

      <div className="mt-4">
        <Suspense fallback={<Skeleton label="cost" />}>
          <CostPanel />
        </Suspense>
      </div>

      <footer className="mt-8 border-t border-ink-800 pt-4 text-[10px] leading-relaxed text-paper-500">
        Nothing here has run against live production traffic. The machinery —
        arm assignment, the six attribution conditions, the policy firewall, the
        audit chain — is the same code that would run on real Razorpay traffic;
        the customer responses in the seeded corpus are a declared parameter and
        are badged <span className="text-simulated-500">SIMULATED</span>{" "}
        accordingly.
      </footer>
    </main>
  );
}
