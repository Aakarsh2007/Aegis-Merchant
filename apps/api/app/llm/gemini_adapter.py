"""Gemini adapter (Google AI Studio free tier).

Every provider trouble degrades to the deterministic path rather than raising.
A timeout, an exhausted quota, a retired model or an unparseable response all
produce a valid :class:`StructuredResult` with ``source=DETERMINISTIC`` and
``fell_back=True``. An exception escaping here would drop a recoverable
payment, which is a worse outcome than a slightly less nuanced diagnosis.

**On model selection.** The model is config, and it has to be: this project has
now had a hard-coded model retired underneath it twice. The plan specified
Gemini 1.5 Flash (retired), v3.1 corrected that to Gemini 2.5 Flash — which
this key also cannot call, discovered by probing rather than reading (INC-008).
The models-list endpoint is not authoritative either; it advertises models that
return 404 on use. Only a real call tells the truth.

**On latency.** Measured, not assumed: median ~3.9s on the free tier for these
short structured tasks, against a plan that budgeted 1.4s p95 and a 2.5s
timeout. Those numbers were written before anything existed to measure. §4.6
now carries the measured figures and the reasoning about why ~4s is acceptable
here (the webhook is already acknowledged; the customer is not waiting on us).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.clock import Clock
from app.db.enums import LLMSource, LLMTask
from app.llm.adapter import StructuredResult
from app.llm.deterministic import DeterministicAdapter
from app.llm.prompts import PROMPT_VERSION, build_prompt
from app.llm.rate_limit import RateLimiter
from app.llm.schemas import SCHEMA_FOR_TASK

__all__ = ["CANDIDATE_MODELS", "DEFAULT_MODEL", "GeminiAdapter"]

#: Chosen by measurement (accuracy on the golden set, then latency), not by
#: reputation. See docs/DECISIONS.md DEC-017.
DEFAULT_MODEL = "gemini-3.1-flash-lite"

#: Probed alternatives, kept so the next retirement is a config change rather
#: than an investigation.
CANDIDATE_MODELS = ("gemini-3.1-flash-lite", "gemini-3.5-flash", "gemini-3.6-flash")

_JSON_TYPES: dict[str, str] = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}


def _to_gemini_schema(model_cls: type[BaseModel]) -> dict[str, Any]:
    """Translate a Pydantic JSON schema into Gemini's response schema.

    Only the subset our outputs use. Gemini rejects unknown keywords, so
    ``$defs``, ``anyOf`` and format hints are resolved or dropped here rather
    than being sent and failing at request time.
    """
    schema = model_cls.model_json_schema()
    defs = schema.get("$defs", {})

    def convert(node: dict[str, Any]) -> dict[str, Any]:
        if "$ref" in node:
            ref = node["$ref"].rsplit("/", 1)[-1]
            node = {**defs.get(ref, {}), **{k: v for k, v in node.items() if k != "$ref"}}
        if "anyOf" in node:
            # Optional[X] renders as anyOf[X, null]; take the non-null branch
            # and mark it nullable, which is what Gemini understands.
            options = [o for o in node["anyOf"] if o.get("type") != "null"]
            base: dict[str, Any] = convert(options[0]) if options else {"type": "STRING"}
            base["nullable"] = True
            return base

        out: dict[str, Any] = {}
        json_type = node.get("type")
        if "enum" in node:
            out["type"] = "STRING"
            out["enum"] = [str(v) for v in node["enum"]]
            return out
        if json_type in _JSON_TYPES:
            out["type"] = _JSON_TYPES[json_type]
        if json_type == "object":
            out["properties"] = {k: convert(v) for k, v in node.get("properties", {}).items()}
            if node.get("required"):
                out["required"] = list(node["required"])
        if json_type == "array":
            out["items"] = convert(node.get("items", {"type": "string"}))

        # Propagate bounds. Dropping maxLength was a real defect: the model
        # returned a 279-character reasoning against a 240-character limit,
        # validation rejected it, and a perfectly good diagnosis was discarded
        # in favour of the rule table (INC-011). A constraint the provider is
        # never told is a constraint the model cannot honour.
        for src, dst in (
            ("maxLength", "maxLength"),
            ("minLength", "minLength"),
            ("minimum", "minimum"),
            ("maximum", "maximum"),
            ("maxItems", "maxItems"),
        ):
            if src in node:
                out[dst] = node[src]

        # Descriptions are deliberately NOT forwarded. They are our docstrings,
        # and sending them took the DIAGNOSE prompt from 41 to 465 input tokens
        # -- an 11x cost increase on a free tier, to restate what the system
        # prompt already says at length.
        return out

    return convert(schema)


def _truncate_overlong_strings(text: str, schema_cls: type[BaseModel]) -> BaseModel | None:
    """Trim string fields that exceed their declared maxLength, and re-validate.

    Returns ``None`` unless the *only* problem was length -- if anything else is
    wrong the caller falls back, because a malformed category is a different
    kind of failure from a verbose one.
    """
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    schema = schema_cls.model_json_schema()
    changed = False
    for name, spec in schema.get("properties", {}).items():
        limit = spec.get("maxLength")
        value = payload.get(name)
        if limit and isinstance(value, str) and len(value) > limit:
            payload[name] = value[: limit - 1].rstrip() + "…"
            changed = True
    if not changed:
        return None
    try:
        return schema_cls.model_validate(payload)
    except (ValidationError, ValueError):
        return None


class GeminiAdapter:
    """Live Gemini calls, with a deterministic floor under every failure."""

    name = "gemini"

    def __init__(
        self,
        api_key: str,
        *,
        clock: Clock,
        model: str = DEFAULT_MODEL,
        timeout_s: float = 12.0,
        max_output_tokens: int = 1024,
        rate_limiter: RateLimiter | None = None,
        fallback: DeterministicAdapter | None = None,
        wait_for_slot_s: float = 0.0,
    ) -> None:
        if not api_key:
            raise ValueError("GeminiAdapter requires an API key")
        self._api_key = api_key
        self._clock = clock
        self.model = model
        self._timeout = timeout_s
        self._max_output = max_output_tokens
        self._limiter = rate_limiter
        self._fallback = fallback or DeterministicAdapter()
        #: How long to wait for a rate-limit slot before degrading.
        #: Zero on the webhook path -- waiting a minute for a token would be
        #: worse than answering deterministically. Non-zero only for the
        #: offline warm-up, which is allowed to be slow. Measuring model
        #: accuracy with this at zero silently measures the FALLBACK instead,
        #: which is how the first comparison run produced a fabricated 100%
        #: (INC-010).
        self._wait_for_slot_s = wait_for_slot_s
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    # ------------------------------------------------------------------
    async def complete_structured(
        self,
        *,
        task: LLMTask,
        context: dict[str, Any],
        timeout_s: float | None = None,
    ) -> StructuredResult:
        if self._limiter is not None:
            if self._wait_for_slot_s > 0:
                allowed, reason = await self._limiter.acquire(max_wait_s=self._wait_for_slot_s)
            else:
                allowed, reason = await self._limiter.try_acquire()
            if not allowed:
                return await self._degrade(task, context, note=f"rate limited: {reason}")

        system, user = build_prompt(task, context)
        schema_cls = SCHEMA_FOR_TASK[task.value]
        budget = timeout_s if timeout_s is not None else self._timeout

        started = time.perf_counter()
        try:
            text, usage = await asyncio.wait_for(
                self._call(system, user, schema_cls), timeout=budget
            )
        except TimeoutError:
            return await self._degrade(task, context, note=f"timed out after {budget}s")
        except Exception as exc:  # provider errors are expected, not exceptional
            return await self._degrade(
                task, context, note=f"{type(exc).__name__}: {str(exc)[:160]}"
            )

        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            output = schema_cls.model_validate_json(text)
        except (ValidationError, ValueError):
            repaired = _truncate_overlong_strings(text, schema_cls)
            if repaired is not None:
                # The content was right and only the length was wrong. Falling
                # back to the rule table here would discard a good diagnosis
                # over a few characters. Deliberately narrow: only string
                # fields with a declared maxLength are trimmed. Numbers and
                # enums are never coerced -- a wrong category must fail, not
                # be massaged into shape.
                return StructuredResult(
                    task=task,
                    output=repaired,
                    source=LLMSource.LIVE,
                    model=self.model,
                    provider="gemini",
                    prompt_version=PROMPT_VERSION,
                    input_tokens=usage[0],
                    output_tokens=usage[1],
                    latency_ms=latency_ms,
                    schema_valid_first_try=False,
                    raw_text=text,
                )
            # One re-prompt with the error, then the floor (§16 scenario 11).
            retry = await self._retry_once(system, user, schema_cls, text, budget)
            if retry is None:
                return await self._degrade(task, context, note="schema validation failed twice")
            retried_output, extra_usage, retry_ms = retry
            return StructuredResult(
                task=task,
                output=retried_output,
                source=LLMSource.LIVE,
                model=self.model,
                provider="gemini",
                prompt_version=PROMPT_VERSION,
                input_tokens=usage[0] + extra_usage[0],
                output_tokens=usage[1] + extra_usage[1],
                latency_ms=latency_ms + retry_ms,
                schema_valid_first_try=False,
                raw_text=text,
            )

        return StructuredResult(
            task=task,
            output=output,
            source=LLMSource.LIVE,
            model=self.model,
            provider="gemini",
            prompt_version=PROMPT_VERSION,
            input_tokens=usage[0],
            output_tokens=usage[1],
            latency_ms=latency_ms,
            raw_text=text,
        )

    async def _call(self, system: str, user: str, schema_cls: type) -> tuple[str, tuple[int, int]]:
        from google.genai import types

        client = self._get_client()
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=_to_gemini_schema(schema_cls),
            temperature=0.2,
            max_output_tokens=self._max_output,
            # We never expose tools to the model. Leaving automatic function
            # calling on would be a capability we do not want it to have.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        response = await asyncio.to_thread(
            client.models.generate_content, model=self.model, contents=user, config=config
        )
        usage = getattr(response, "usage_metadata", None)
        tokens = (
            int(getattr(usage, "prompt_token_count", 0) or 0),
            int(getattr(usage, "candidates_token_count", 0) or 0),
        )
        return response.text or "", tokens

    async def _retry_once(
        self, system: str, user: str, schema_cls: type[BaseModel], bad: str, budget: float
    ) -> tuple[BaseModel, tuple[int, int], int] | None:
        """Re-prompt with the malformed output, once."""
        repair = (
            f"{user}\n\nYour previous response did not match the schema and was "
            f"rejected:\n{bad[:400]}\n\nReturn ONLY valid JSON matching the schema."
        )
        started = time.perf_counter()
        try:
            text, usage = await asyncio.wait_for(
                self._call(system, repair, schema_cls), timeout=budget
            )
            return (
                schema_cls.model_validate_json(text),
                usage,
                int((time.perf_counter() - started) * 1000),
            )
        except Exception:
            return None

    async def _degrade(
        self, task: LLMTask, context: dict[str, Any], *, note: str
    ) -> StructuredResult:
        result = await self._fallback.complete_structured(task=task, context=context)
        return StructuredResult(
            task=result.task,
            output=result.output,
            source=LLMSource.DETERMINISTIC,
            model=None,
            provider="deterministic",
            prompt_version=PROMPT_VERSION,
            fell_back=True,
            raw_text=note,
        )

    async def health(self) -> bool:
        try:
            from google.genai import types

            client = self._get_client()
            await asyncio.wait_for(
                asyncio.to_thread(
                    client.models.generate_content,
                    model=self.model,
                    contents="ok",
                    config=types.GenerateContentConfig(max_output_tokens=8),
                ),
                timeout=self._timeout,
            )
        except Exception:
            return False
        return True

    @staticmethod
    def usable_models(api_key: str, candidates: tuple[str, ...] = CANDIDATE_MODELS) -> list[str]:
        """Which candidates this key can actually call.

        A real call, not the models-list endpoint -- that endpoint advertises
        models which return 404 on use (INC-008).
        """
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        usable = []
        for name in candidates:
            try:
                client.models.generate_content(
                    model=name,
                    contents="ok",
                    config=types.GenerateContentConfig(max_output_tokens=8),
                )
                usable.append(name)
            except Exception:
                continue
        return usable


def gemini_schema_for(model_cls: type[BaseModel]) -> dict[str, Any]:
    """Exposed for tests: the translated schema sent to the provider."""
    return _to_gemini_schema(model_cls)
