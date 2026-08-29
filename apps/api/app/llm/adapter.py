"""The LLM boundary.

One protocol, four implementations (workflow.md §4.4). Provider choice is a
config value, not a code dependency — which is not architectural taste but a
lesson already learned twice on this project: the plan specified Gemini 1.5
Flash, which Google retired; v3.1 corrected that to Gemini 2.5 Flash, which
this key cannot call either (INC-008). A model name is not a stable thing to
hard-code.

Every implementation returns a :class:`StructuredResult`, which carries not
just the parsed output but **where it came from**. That provenance is
load-bearing: a cached response must never be displayed as a live one, and a
deterministic fallback must never be displayed as model reasoning (§19.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from app.db.enums import LLMSource, LLMTask

__all__ = [
    "LLMAdapter",
    "LLMError",
    "LLMQuotaExhausted",
    "LLMTimeout",
    "LLMUnavailable",
    "StructuredResult",
]


class LLMError(RuntimeError):
    """Base for every adapter failure."""


class LLMTimeout(LLMError):
    """The model did not answer inside the budget."""


class LLMQuotaExhausted(LLMError):
    """Free-tier quota is spent. Expected, not exceptional."""


class LLMUnavailable(LLMError):
    """Provider refused: bad key, retired model, or an outage."""


@dataclass(frozen=True)
class StructuredResult:
    """A validated model output, plus how it was obtained."""

    task: LLMTask
    output: BaseModel
    source: LLMSource
    model: str | None = None
    provider: str | None = None
    prompt_version: str | None = None
    cache_key: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    #: False when the first response failed schema validation and a re-prompt
    #: was needed. Tracked because a model that needs two attempts to produce
    #: valid JSON is a different cost and latency profile than one that does not.
    schema_valid_first_try: bool = True
    #: True when the deterministic path produced this, after the model failed
    #: or was unavailable.
    fell_back: bool = False
    raw_text: str | None = field(default=None, repr=False)

    @property
    def is_live(self) -> bool:
        return self.source is LLMSource.LIVE

    def cost_micro_inr(self, input_rate: float, output_rate: float) -> int:
        """Projected cost at published paid rates, in micro-rupees.

        Actual spend on a free tier is zero. This is the number that answers
        *"would this work in production"* (§4.6), and it is computed from
        logged token counts rather than estimated.
        """
        if self.source is not LLMSource.LIVE:
            return 0
        return int(self.input_tokens * input_rate + self.output_tokens * output_rate)


class LLMAdapter(Protocol):
    """What the agent may ask a model to do.

    Deliberately narrow. There is no free-form ``complete(prompt)`` — every
    call names a task with a known schema, so an unexpected output shape is a
    parse failure rather than a string the caller has to interpret.
    """

    name: str

    async def complete_structured(
        self,
        *,
        task: LLMTask,
        context: dict[str, Any],
        timeout_s: float | None = None,
    ) -> StructuredResult:
        """Run one cognitive task and return validated output.

        Implementations must not raise for ordinary provider trouble: a
        timeout, a quota exhaustion or a malformed response degrades to the
        deterministic path and is reported through ``source``/``fell_back``. An
        exception here would drop a recoverable payment.
        """
        ...

    async def health(self) -> bool:
        """Cheap reachability check for /api/v1/health/deep."""
        ...
