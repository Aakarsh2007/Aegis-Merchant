"""Configuration.

Every policy bound in RevPilot is a *config value*, never a literal buried in
code (workflow.md §12.3). That is what lets the demo tighten a bound live and
have it take effect immediately, and what lets each merchant carry its own
limits.

Money is always paise (integers). Never floats — a float rupee is how payment
systems lose half a paisa a million times.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]
LLMProvider = Literal["gemini", "deterministic", "openai_compat"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True
    environment: Environment = "development"

    # ------------------------------------------------------------- database
    database_url: str = "sqlite+aiosqlite:///./revpilot.db"

    #: Bearer token for the read and money-moving endpoints (§13.5).
    #: Deliberately empty by default so Judge Mode runs with zero credentials.
    #: When empty outside production the API is open and says so loudly
    #: (X-Auth-Mode header, /healthz/deep, a startup warning); when empty IN
    #: production the app refuses to start. A service that boots happily with
    #: authentication silently disabled is the failure this prevents.
    api_token: str = ""

    # ------------------------------------------------------------- razorpay
    # Absent keys are not an error: the app falls back to MockRazorpayProvider
    # so Judge Mode runs with zero credentials (workflow.md §22).
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    #: Reject webhooks whose event timestamp is older than this. Signature
    #: validity alone does not stop replay of a captured valid payload (§10.1).
    webhook_replay_tolerance_s: int = 300

    # ------------------------------------------------------------------ llm
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"

    #: Free-tier quotas. Flagged VERIFY_CURRENT_QUOTA in workflow.md §4.6 —
    #: free-tier limits change, so read them from config and re-check them
    #: rather than trusting a number hardcoded months ago.
    llm_rpm_limit: int = 10
    llm_rpd_limit: int = 200
    #: Measured, not assumed. Free-tier Gemini 3.x answers these short
    #: structured tasks in ~3.9 s median; the original 2.5 s budget was written
    #: before there was anything to measure and would have made every live call
    #: fall back (INC-009). The customer is not waiting on this -- the webhook
    #: was acknowledged in ~7 ms and diagnosis runs in a background task.
    llm_timeout_s: float = 12.0
    llm_max_output_tokens: int = 1024
    llm_max_calls_per_case: int = 3

    #: Bumping this invalidates every response-cache key, which forces a fresh
    #: `warm-cache` run rather than letting a stale cache pass CI (§4.5).
    prompt_version: str = "v1"

    # --------------------------------------------------------------- policy
    #: Amount at or above which a human must approve (rung A2).
    max_autonomous_amount_paise: int = 1_000_000  # ₹10,000
    #: Amount at or above which approval also records the approving principal
    #: against the exact action approved (rung A3).
    hitl_dual_signal_amount_paise: int = 10_000_000  # ₹1,00,000

    #: Hard ceiling. An LLM proposal above this is clamped, never honoured.
    max_discount_pct: float = 7.0
    #: What a clamp resolves *to*. Deliberately the safe default rather than
    #: the ceiling: clamping to the ceiling would reward a model for asking
    #: high (workflow.md §26.2).
    default_discount_pct: float = 5.0
    max_discount_absolute_paise: int = 50_000  # ₹500

    max_contacts_24h: int = 1
    max_contacts_48h: int = 2
    max_attempts_per_case: int = 2
    max_discount_bearing_attempts: int = 1

    link_expiry_minutes: int = 30
    link_expiry_min_minutes: int = 15
    link_expiry_max_minutes: int = 1440

    #: TRAI-aligned quiet hours in IST. Outbound is deferred, never dropped.
    quiet_hours_start_ist: int = 21
    quiet_hours_end_ist: int = 9
    quiet_hours_release_minute: int = 5  # released at 09:05 IST

    #: Recovery windows per playbook, in hours.
    window_payment_failure_h: int = 24
    window_checkout_abandon_h: int = 72
    window_receivable_h: int = 720  # due + 30d
    window_subscription_h: int = 168

    approval_ttl_minutes: int = 240  # 4h
    attribution_grace_h: int = 24
    promise_freeze_h: int = 24

    #: Per-merchant circuit breakers (S-11).
    daily_action_budget: int = 50
    monthly_discount_exposure_paise: int = 20_000_000  # ₹2,00,000

    #: NPCI mandate rules. VERIFY_BEFORE_PRODUCTION — implement the mechanism
    #: correctly, and re-check the exact numbers against the current circular.
    pre_debit_notice_hours: int = 24
    max_representations: int = 3

    # ----------------------------------------------------------- experiment
    #: Fraction of eligible cases held back with NO intervention, so that
    #: incremental lift is measurable rather than asserted (workflow.md §14.2).
    control_arm_fraction: float = 0.18
    experiment_key: str = "revpilot_recovery_v1"

    # ------------------------------------------------------------ execution
    outbox_max_attempts: int = 4
    outbox_reconcile_after_s: int = 60

    # ----------------------------------------------------------------- demo
    seed: int = 20260905
    seed_transaction_count: int = 420

    # ----------------------------------------------------------- validators
    @field_validator("control_arm_fraction")
    @classmethod
    def _fraction_in_range(cls, v: float) -> float:
        if not 0.0 <= v < 1.0:
            raise ValueError("control_arm_fraction must be in [0.0, 1.0)")
        return v

    @field_validator("default_discount_pct")
    @classmethod
    def _default_not_above_ceiling(cls, v: float, info) -> float:  # type: ignore[no-untyped-def]
        ceiling = info.data.get("max_discount_pct")
        if ceiling is not None and v > ceiling:
            raise ValueError(
                f"default_discount_pct ({v}) cannot exceed max_discount_pct ({ceiling})"
            )
        return v

    @field_validator("quiet_hours_start_ist", "quiet_hours_end_ist")
    @classmethod
    def _valid_hour(cls, v: int) -> int:
        if not 0 <= v <= 23:
            raise ValueError("quiet hour must be 0-23")
        return v

    # ------------------------------------------------------- derived values
    @computed_field  # type: ignore[prop-decorator]
    @property
    def razorpay_live(self) -> bool:
        """True when real Razorpay Test Mode credentials are present."""
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def llm_provider(self) -> LLMProvider:
        """Which adapter the app will use. 'deterministic' is a valid,
        fully-functional mode, not a degraded error state (§22)."""
        return "gemini" if self.gemini_api_key else "deterministic"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def simulation_allowed(self) -> bool:
        """Simulation and chaos endpoints must never be reachable in prod."""
        return self.environment != "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
