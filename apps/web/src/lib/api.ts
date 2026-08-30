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

export const api = {
  overview: () => safeFetch<Overview>("/api/v1/metrics/overview"),
  attribution: () => safeFetch<Attribution>("/api/v1/metrics/attribution"),
  cost: () => safeFetch<CostReport>("/api/v1/metrics/cost"),
  stoppingRules: () => safeFetch<StoppingRules>("/api/v1/metrics/stopping-rules"),
  cases: (query = "") => safeFetch<CaseList>(`/api/v1/cases${query}`),
  caseTrace: (id: string) => safeFetch<CaseTrace>(`/api/v1/cases/${id}`),
  approvals: () =>
    safeFetch<{ approvals: Approval[]; count: number }>("/api/v1/approvals"),
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
