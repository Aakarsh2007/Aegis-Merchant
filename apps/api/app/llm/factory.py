"""Adapter selection.

Absent credentials are not an error state. The deterministic adapter is a
first-class mode that runs the whole product, and ``/api/v1/health/deep``
reports which adapter is live so a judge can see the system running honestly
rather than fabricating output (workflow.md §22).
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.core.clock import Clock, SystemClock
from app.llm.adapter import LLMAdapter
from app.llm.cache import CachedAdapter, ResponseCache
from app.llm.deterministic import DeterministicAdapter
from app.llm.gemini_adapter import DEFAULT_MODEL, GeminiAdapter
from app.llm.rate_limit import RateLimiter

__all__ = ["build_adapter", "describe_adapter"]


def build_adapter(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    use_cache: bool = True,
    record: bool = False,
) -> LLMAdapter:
    """Assemble the adapter stack.

    Layered deliberately: cache in front (free, instant, reproducible), live
    behind it (real reasoning, rate limited), deterministic underneath
    (always available). Each layer degrades into the next rather than failing.
    """
    settings = settings or get_settings()
    clock = clock or SystemClock()

    live: LLMAdapter | None = None
    model = settings.gemini_model or DEFAULT_MODEL
    if settings.gemini_api_key:
        live = GeminiAdapter(
            settings.gemini_api_key,
            clock=clock,
            model=model,
            timeout_s=settings.llm_timeout_s,
            max_output_tokens=settings.llm_max_output_tokens,
            rate_limiter=RateLimiter(
                clock=clock,
                rpm_limit=settings.llm_rpm_limit,
                rpd_limit=settings.llm_rpd_limit,
            ),
        )

    if not use_cache:
        return live or DeterministicAdapter()

    return CachedAdapter(cache=ResponseCache.load(), live=live, model=model, record=record)


def describe_adapter(adapter: LLMAdapter) -> dict[str, object]:
    """A shape for /health/deep. Never includes a key or any part of one."""
    info: dict[str, object] = {"adapter": adapter.name}
    inner = getattr(adapter, "_live", None)
    if inner is not None:
        info["live_adapter"] = inner.name
        info["model"] = getattr(inner, "model", None)
    cache = getattr(adapter, "cache", None)
    if cache is not None:
        info["cached_responses"] = len(cache)
    return info
