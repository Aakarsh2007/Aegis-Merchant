"""Strict output schemas for every cognitive task.

Two independent validations, deliberately (workflow.md §4.4):

1. The provider's own ``response_schema`` constrains generation.
2. The response is then re-parsed through these Pydantic models with
   ``extra="forbid"``.

The provider's constraint is a *convenience*; these models are the *contract*.
Trusting the provider alone would mean a schema change on their side, or a
degraded response under load, silently becoming a malformed action. And the
containment argument in §13.2 depends on this layer: even granting a total
prompt-injection success, the output still has to survive a strict parse before
anything downstream sees it.

Bounds live here rather than in prompts. "Please keep it under 300 characters"
is a request; ``max_length=300`` is a guarantee.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.enums import Channel, FailureCategory, MessageClass, RecoveryStrategy

__all__ = [
    "SCHEMA_FOR_TASK",
    "BriefingOutput",
    "DiagnosisOutput",
    "MessageOutput",
    "PromiseOutput",
    "ProposalOutput",
]


class _Strict(BaseModel):
    """Reject anything the schema does not name.

    An extra key means the model produced something we did not ask for, which
    is the shape a successful injection takes. Better a hard parse failure and
    a deterministic fallback than a silent pass-through.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class DiagnosisOutput(_Strict):
    """Task 1 — why the payment failed.

    Used only where the deterministic classifier is unsure: conflicting
    signals, or missing telemetry (§4.2 item 1).
    """

    category: FailureCategory
    is_recoverable: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(max_length=240)

    @field_validator("reasoning")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("reasoning must not be empty: the trace has to explain itself")
        return v


class ProposalOutput(_Strict):
    """Task 2 — what to do about it.

    Advisory. Every number here is a *request* that the policy firewall may
    reduce; nothing in this object reaches a payment API unchanged.
    """

    strategy: RecoveryStrategy
    discount_pct: float = Field(ge=0.0, le=100.0)
    link_validity_minutes: int = Field(ge=1, le=10_080)
    channel: Channel
    message_class: MessageClass
    rationale: str = Field(max_length=240)


class MessageOutput(_Strict):
    """Task 3 — what the customer reads.

    Length bounds are enforced rather than requested: an over-long WhatsApp
    body is truncated by the channel, and a truncated payment link is a broken
    recovery.
    """

    headline: str = Field(max_length=60)
    body: str = Field(max_length=300)
    cta: str = Field(max_length=24)
    language: str = Field(max_length=12)


class PromiseOutput(_Strict):
    """Task 4 — a commitment extracted from a free-text reply.

    The input is the most hostile in the system: whatever a customer typed.
    ``promised_at`` is an ISO-8601 date string rather than a datetime so a
    malformed value fails here instead of at the scheduler.
    """

    has_promise: bool
    promised_at: str | None = Field(default=None, max_length=32)
    promised_amount_paise: int | None = Field(default=None, ge=0)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("promised_at")
    @classmethod
    def _iso_or_none(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        from datetime import datetime

        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"promised_at is not ISO-8601: {v!r}") from exc
        return v


class BriefingOutput(_Strict):
    """Task 5 — the merchant's morning digest.

    Narration only. Every figure it mentions was computed in SQL before the
    model saw it; the model re-phrases and never calculates (§4.3, hard rule).
    """

    headline: str = Field(max_length=120)
    bullets: list[str] = Field(max_length=6)
    closing: str = Field(max_length=240)

    @field_validator("bullets")
    @classmethod
    def _bounded(cls, v: list[str]) -> list[str]:
        if any(len(b) > 160 for b in v):
            raise ValueError("each bullet must be 160 characters or fewer")
        return v


#: Task name -> schema. Keyed by ``LLMTask`` value so the adapter can look up
#: the contract without a conditional per task.
SCHEMA_FOR_TASK: dict[str, type[_Strict]] = {
    "DIAGNOSE": DiagnosisOutput,
    "STRATEGISE": ProposalOutput,
    "COMPOSE_MESSAGE": MessageOutput,
    "EXTRACT_PROMISE": PromiseOutput,
    "DAILY_BRIEFING": BriefingOutput,
}
