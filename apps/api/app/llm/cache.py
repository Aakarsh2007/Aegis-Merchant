"""Content-addressed response cache (workflow.md §4.5).

The most important free-tier design decision, and a real architectural problem
rather than a config tweak. The batch demo processes 420 transactions; at two
model calls per eligible case that is ~840 requests, against a free-tier limit
of a handful per minute. A live batch run would take **hours** and consume a
day's quota. A demo that takes hours is not a demo.

The cache is committed to the repo, and that turns a workaround into a property
worth having: **the batch result becomes byte-for-byte reproducible.** A judge
who clones the repo gets exactly the numbers in the README, because the model's
contribution is pinned rather than re-rolled. Non-reproducible benchmarks are a
real problem in LLM systems and pinning outputs is the standard answer.

Two rules keep it honest:

* ``prompt_version`` is part of every key, so editing a prompt invalidates the
  responses derived from it. A stale cache cannot silently pass CI.
* Every served response is marked ``source=CACHED``. **A cached response is
  never presented as live** (§19.2), and the UI shows the difference.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.db.enums import LLMSource, LLMTask
from app.llm.adapter import LLMAdapter, StructuredResult
from app.llm.deterministic import DeterministicAdapter
from app.llm.prompts import PROMPT_VERSION
from app.llm.schemas import SCHEMA_FOR_TASK

__all__ = ["CACHE_FILE", "CachedAdapter", "ResponseCache", "cache_key"]

#: Committed to git, so `make demo` needs no warm-up and no key.
CACHE_FILE = Path(__file__).resolve().parents[4] / "data" / "llm_cache.jsonl"


def cache_key(*, task: LLMTask, model: str, prompt_version: str, context: dict[str, Any]) -> str:
    """SHA-256 over (task, model, prompt_version, canonical context).

    Canonical JSON — sorted keys, fixed separators — because the same dict can
    serialise two ways and a key that depends on dict ordering silently misses.
    """
    canonical = json.dumps(context, sort_keys=True, separators=(",", ":"), default=str)
    material = f"{task.value}|{model}|{prompt_version}|{canonical}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass
class ResponseCache:
    """JSONL-backed store. One JSON object per line, sorted by key.

    JSONL rather than a single JSON document so a warm-up run appends instead
    of rewriting, and so a git diff shows which responses changed rather than
    one unreadable blob.
    """

    path: Path = CACHE_FILE
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = CACHE_FILE) -> ResponseCache:
        cache = cls(path=path)
        if not path.exists():
            return cache
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # a corrupt line must not take the whole cache down
            key = row.get("cache_key")
            if key:
                cache.entries[key] = row
        return cache

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8", newline="\n") as handle:
            for key in sorted(self.entries):
                handle.write(
                    json.dumps(self.entries[key], sort_keys=True, ensure_ascii=False) + "\n"
                )

    def get(self, key: str) -> dict[str, Any] | None:
        return self.entries.get(key)

    def put(self, key: str, row: dict[str, Any]) -> None:
        self.entries[key] = row

    def __len__(self) -> int:
        return len(self.entries)

    def stats(self) -> dict[str, int]:
        by_task: dict[str, int] = {}
        for row in self.entries.values():
            by_task[row.get("task", "?")] = by_task.get(row.get("task", "?"), 0) + 1
        return by_task


class CachedAdapter:
    """Serve from the cache; fall through to a live adapter on a miss.

    On the batch path this means zero API calls and a run that finishes in
    seconds. On the demo path, hero cases pass ``force_live=True`` so the
    audience sees a real, unscripted model call.
    """

    name = "cached"

    def __init__(
        self,
        *,
        cache: ResponseCache | None = None,
        live: LLMAdapter | None = None,
        model: str = "unknown",
        record: bool = False,
    ) -> None:
        self._cache = cache if cache is not None else ResponseCache.load()
        self._live = live
        self._model = model
        self._record = record
        self._fallback = DeterministicAdapter()
        self.hits = 0
        self.misses = 0

    async def complete_structured(
        self,
        *,
        task: LLMTask,
        context: dict[str, Any],
        timeout_s: float | None = None,
        force_live: bool = False,
    ) -> StructuredResult:
        key = cache_key(
            task=task, model=self._model, prompt_version=PROMPT_VERSION, context=context
        )

        if not force_live:
            row = self._cache.get(key)
            if row is not None:
                hit = self._from_row(task, row, key)
                if hit is not None:
                    self.hits += 1
                    return hit

        self.misses += 1

        if self._live is None:
            # No live adapter configured: the floor, not an error. This is the
            # Judge Mode path -- everything runs, nothing is fabricated.
            result = await self._fallback.complete_structured(task=task, context=context)
            return StructuredResult(**{**result.__dict__, "cache_key": key})

        live = await self._live.complete_structured(task=task, context=context, timeout_s=timeout_s)
        if self._record and live.source is LLMSource.LIVE:
            self._cache.put(
                key,
                {
                    "cache_key": key,
                    "task": task.value,
                    "model": live.model or self._model,
                    "prompt_version": PROMPT_VERSION,
                    "context": context,
                    "response": live.output.model_dump(mode="json"),
                    "input_tokens": live.input_tokens,
                    "output_tokens": live.output_tokens,
                    "latency_ms": live.latency_ms,
                },
            )
        return StructuredResult(**{**live.__dict__, "cache_key": key})

    def _from_row(self, task: LLMTask, row: dict[str, Any], key: str) -> StructuredResult | None:
        """Rebuild a result from a cached row, re-validating the payload.

        Re-validation is not paranoia: the cache is a file in a repo, and a
        hand-edited entry that no longer matches the schema must fail like any
        other malformed response rather than flow into an action.
        """
        schema_cls = SCHEMA_FOR_TASK[task.value]
        try:
            output = schema_cls.model_validate(row["response"])
        except Exception:
            return None
        return StructuredResult(
            task=task,
            output=output,
            source=LLMSource.CACHED,
            model=row.get("model"),
            provider="cache",
            prompt_version=row.get("prompt_version"),
            cache_key=key,
            input_tokens=int(row.get("input_tokens", 0)),
            output_tokens=int(row.get("output_tokens", 0)),
            latency_ms=0,
        )

    async def health(self) -> bool:
        return True

    def save(self) -> None:
        self._cache.save()

    @property
    def cache(self) -> ResponseCache:
        return self._cache

    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0
