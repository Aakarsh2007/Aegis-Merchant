/**
 * The typed client the Command Center talks to.
 *
 * Shapes come from `api.generated.ts`, which is generated from the committed
 * `openapi.json`. Rename a field in the API and this stops compiling, rather
 * than rendering `undefined` in a tile during the demo.
 *
 * Two rules this module enforces on the client side:
 *
 * 1. **A failed fetch is a visible state, never a zero.** `safeFetch` returns
 *    a discriminated result rather than throwing or defaulting. A dashboard
 *    that renders 0 when the API is unreachable is worse than one that renders
 *    an error: 0 is a number a viewer will believe.
 * 2. **Money is only ever read through `Figure`.** The API cannot emit an
 *    unbadged rupee amount, and the components cannot render one, because the
 *    type has nowhere to put a bare number.
 */

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** Mirrors `app.core.provenance.Provenance`. */
export type Provenance = "RAZORPAY_VERIFIED" | "SIMULATED" | "ESTIMATED";

/** Mirrors `app.core.provenance.Figure`. A rupee amount and where it came from. */
export interface Figure {
  paise: number;
  display: string;
  provenance: Provenance;
  basis: string;
}

/** Mirrors `app.core.provenance.Count`. */
export interface Count {
  value: number;
  provenance: Provenance;
  basis: string;
}

export interface Overview {
  at_risk: Figure;
  /** Proven by a REAL signed webhook. Zero until live traffic runs. */
  gross_recovered: Figure;
  /** Settled by the batch simulator. A different claim, so a different tile. */
  gross_simulated: Figure;
  net_incremental: Figure;
  open_cases: Count;
  control_cases: Count;
  interceptions: Count;
  pending_approvals: Count;
  lift_is_significant: boolean;
  notes: string[];
}

export interface ArmStats {
  cases: number;
  paid: number;
  conversion: number;
  ci95: [number, number];
}

export interface Attribution {
  treatment: ArmStats;
  control: ArmStats;
  gross_recovered_paise: number;
  absolute_lift: number;
  incremental_revenue_paise: number;
  discount_cost_paise: number;
  inference_cost_micro_inr: number;
  net_incremental_paise: number;
  lift_is_significant: boolean;
  has_control_arm: boolean;
  excluded_demo_cases: number;
  notes: string[];
}

export interface CostReport {
  llm_calls: number;
  by_source: Record<string, number>;
  input_tokens: number;
  output_tokens: number;
  actual_spend: Figure;
  projected_spend: Figure;
  cache_hit_rate: number;
}

export interface StoppingRuleRow {
  rule: string;
  fired: number;
}

export interface StoppingRules {
  rules: StoppingRuleRow[];
  total_interceptions: number;
  provenance: Provenance;
  basis: string;
}

export interface CaseSummary {
  id: string;
  status: string;
  playbook: string;
  amount_paise: number;
  recovered_amount_paise: number;
  recovery_verified_by: string | null;
  arm: string | null;
  diagnosis: string | null;
  diagnosis_source: string | null;
  confidence: number | null;
  attempt_no: number;
  stopping_rule_fired: string | null;
  is_demo: boolean;
  window_expires_at: string;
  created_at: string;
  resolved_at: string | null;
}

export interface CaseList {
  cases: CaseSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface CaseTrace {
  case: CaseSummary;
  customer: {
    id: string;
    first_name: string;
    phone_masked: string;
    ltv_paise: number;
    prior_orders: number;
    language_pref: string;
  } | null;
  failure: {
    error_source: string | null;
    error_step: string | null;
    error_reason: string | null;
  };
  diagnosis: {
    category: string | null;
    source: string | null;
    confidence: number | null;
  };
  actions: Array<Record<string, unknown>>;
  outbox: Array<Record<string, unknown>>;
  approvals: Array<Record<string, unknown>>;
  audit: Array<{
    block_index: number;
    event_name: string;
    actor: string;
    created_at: string;
    current_hash: string;
    payload: Record<string, unknown>;
  }>;
}

export interface Approval {
  id: string;
  case_id: string;
  trigger_rung: string;
  trigger_reason: string;
  amount_paise: number;
  policy_applied: string;
  policy_applied_hash: string;
  expires_at: string;
  seconds_remaining: number;
  expired: boolean;
}

export interface Briefing {
  greeting: string;
  as_of_ist: string;
  headline: Record<string, Figure>;
  lines: string[];
  restraint: {
    total: number;
    items: Array<{ rule: string; count: number; wording: string }>;
    sentence: string;
  };
  caveats: string[];
  narration: string;
}

export interface ChainVerification {
  valid: boolean;
  blocks: number;
  first_divergence_index: number | null;
  reason: string | null;
  head_hash: string | null;
}

/**
 * A fetch that cannot silently become a zero.
 *
 * The failure mode this prevents: `fetch(...).catch(() => ({}))` and a tile
 * that renders `Rs 0.00` when the API is down. Zero is a number a viewer will
 * believe, and it is indistinguishable from a real result.
 */
export type Result<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; status?: number };

export async function safeFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<Result<T>> {
  const token = process.env.NEXT_PUBLIC_API_TOKEN;
  try {
    const response = await fetch(`${BASE}${path}`, {
      ...init,
      cache: "no-store",
      headers: {
        "content-type": "application/json",
        ...(token ? { authorization: `Bearer ${token}` } : {}),
        ...init?.headers,
      },
    });
    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      return {
        ok: false,
        status: response.status,
        error:
          response.status === 401
            ? "Unauthorised. Set NEXT_PUBLIC_API_TOKEN to match the API's API_TOKEN."
            : `${response.status} ${response.statusText}${detail ? `: ${detail.slice(0, 200)}` : ""}`,
      };
    }
    return { ok: true, data: (await response.json()) as T };
  } catch (cause) {
    return {
      ok: false,
      error:
        cause instanceof Error && cause.message.includes("fetch")
          ? `Cannot reach the API at ${BASE}. Is it running? (python tasks.py api)`
          : String(cause),
    };
  }
}

/**
 * The gap between what we have and what would settle the causal question.
 *
 * Note what is NOT in this type: no p-value and no significance field. The API
 * does not send one, by design -- `docs/PRE-REGISTRATION.md` §6 commits to a
 * single analysis at the full sample, and a significance number available while
 * data accumulates is an invitation to stop when it looks good. Typing it as
 * absent means a future panel cannot render one by accident.
 */
export type PowerPlan = {
  registered: string;
  design: {
    alpha: number;
    power: number;
    control_fraction: number;
    assumed_control_rate: number;
    assumed_treatment_rate: number;
    assumption_basis: string;
  };
  have: { control: number; treatment: number };
  need: { control: number; treatment: number };
  completion: number;
  completion_basis: string;
  cases_remaining: number;
  attempts_remaining: Record<string, number>;
  is_powered: boolean;
  eta: string | null;
  eta_basis: string;
  blocked_on: string[];
  today: string;
};

/** The real-provider randomised holdout. `significance` is always null. */
export type Holdout = {
  experiment_key: string;
  control_fraction: number;
  arms: Record<
    string,
    {
      cases: number;
      razorpay_verified_recoveries: number;
      organic: number;
      paise: number;
    }
  >;
  what_this_proves: string[];
  what_this_does_not_prove: string;
  significance: null;
  significance_basis: string;
};

/**
 * Paise to rupees, Indian grouping, always two decimals.
 *
 * A shared helper because the option that matters is easy to forget:
 * `toLocaleString` drops a trailing zero, which is right for a quantity and
 * wrong for money. Four separate components got this wrong -- an approval card
 * showed `Rs 20,055.6` for a figure a human was being asked to authorise, and
 * the cases table showed `Rs 7,765.6` and `Rs 4,299`. Mirrors `rupees()` in
 * `core/provenance.py` so the two layers agree.
 */
export function rupees(paise: number): string {
  return (paise / 100).toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** A rupee we claim, and the six conditions that let us. */
export type Claim = {
  case_id: string;
  amount: Figure;
  verified_by: string | null;
  mechanism: string | null;
  arm: string;
  conditions: Array<{
    n: number;
    name: string;
    detail: string;
    satisfied: boolean;
  }>;
};

/** Money that arrived and was credited to us at zero. */
export type Claims = {
  claimed: Claim[];
  claimed_total: Figure;
  not_claimed: Array<{
    case_id: string;
    amount: Figure;
    arm: string | null;
    credited_to_us_paise: number;
    reason: string;
  }>;
  not_claimed_total: Figure;
  not_claimed_count: number;
  note: string;
};

/**
 * The four levels a rupee passes through, and which we have reached.
 *
 * `INCREMENTAL` is the one we have not, and the type does not pretend
 * otherwise: `reached` is a plain boolean the API computes from the actual
 * confidence intervals, not a field a panel can decide for itself.
 */
export type Proof = {
  levels: Array<{
    level: string;
    question: string;
    means: string;
    reached: boolean;
    evidence: string;
  }>;
  summary: string;
};

export const api = {
  overview: () => safeFetch<Overview>("/api/v1/metrics/overview"),
  attribution: () => safeFetch<Attribution>("/api/v1/metrics/attribution"),
  cost: () => safeFetch<CostReport>("/api/v1/metrics/cost"),
  stoppingRules: () => safeFetch<StoppingRules>("/api/v1/metrics/stopping-rules"),
  power: () => safeFetch<PowerPlan>("/api/v1/metrics/power"),
  holdout: () => safeFetch<Holdout>("/api/v1/metrics/holdout"),
  claims: () => safeFetch<Claims>("/api/v1/metrics/claims"),
  proof: () => safeFetch<Proof>("/api/v1/metrics/proof"),
  testmodeStatus: () =>
    safeFetch<{
      razorpay_configured: boolean;
      webhook_secret_configured: boolean;
      ready: boolean;
      missing: string[];
      webhook_note: string;
      verified_count: number;
    }>("/api/v1/testmode/status"),
  testmodeRecover: () =>
    safeFetch<{
      stopped_before_execution: boolean;
      case_id: string;
      pay_url?: string;
      reference_id?: string;
      diagnosis?: string | null;
      strategy?: string | null;
      stopping_rule?: string | null;
      note?: string;
      next_step?: string;
    }>("/api/v1/testmode/recover", { method: "POST", body: JSON.stringify({}) }),
  cases: (query = "") => safeFetch<CaseList>(`/api/v1/cases${query}`),
  caseTrace: (id: string) => safeFetch<CaseTrace>(`/api/v1/cases/${id}`),
  approvals: () =>
    safeFetch<{ approvals: Approval[]; count: number }>("/api/v1/approvals"),
  briefing: () => safeFetch<Briefing>("/api/v1/briefing/today"),
  faults: () =>
    safeFetch<{ active: string | null; enabled: boolean; faults: Array<{ fault: string; effect: string }> }>(
      "/api/v1/simulation/faults",
    ),
  injectFault: (fault: string) =>
    safeFetch<{ active: string | null; effect?: string; expected_behaviour?: string }>(
      "/api/v1/simulation/inject",
      { method: "POST", body: JSON.stringify({ fault }) },
    ),
  attacks: () =>
    safeFetch<{
      attacks: Array<{
        attack: string;
        asks: string;
        why_tempting: string;
        expected: string;
        mechanism: string;
      }>;
    }>("/api/v1/adversarial/attacks"),
  runAttack: (attack: string) =>
    safeFetch<{
      attack: string;
      asked_for: string;
      mechanism: string;
      /**
       * What happened to the REQUEST: REFUSED, ESCALATED, NEUTRALISED,
       * UNREPRESENTABLE, or ALLOWED_AS_ASKED.
       *
       * Distinct from `verdict`, which is the policy engine's answer to "may
       * some action proceed". Conflating them made `marketing_to_dnd` render as
       * PASSED on the panel demonstrating that attacks do not get through
       * (INC-033).
       */
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
    }>("/api/v1/adversarial/run", {
      method: "POST",
      body: JSON.stringify({ attack }),
    }),
  verifyChain: () => safeFetch<ChainVerification>("/api/v1/audit/verify"),
  tamper: (blockIndex: number, mode: "payload" | "hash" | "timestamp") =>
    safeFetch<Record<string, unknown>>("/api/v1/audit/tamper", {
      method: "POST",
      body: JSON.stringify({ block_index: blockIndex, mode }),
    }),
};

export const streamUrl = `${BASE}/api/v1/stream/events`;

/** `0.0621` renders as `6.2%`. Signed, because a negative lift stays negative. */
export function percent(value: number, digits = 1): string {
  return `${value >= 0 ? "" : "-"}${Math.abs(value * 100).toFixed(digits)}%`;
}
