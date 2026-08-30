"""Server-sent events (§20).

The dashboard needs a live feed and the constraint is ADL-003: no Redis, no
broker. So the stream is an in-process fan-out — a set of per-subscriber
``asyncio.Queue`` objects that publishers push to.

Three decisions worth stating, because each one has an obvious wrong answer:

**Bounded queues, and a slow subscriber loses events rather than memory.**
An unbounded queue behind a browser tab that has been backgrounded for an hour
is an out-of-memory in a process that also moves money. When a queue is full
the oldest event is dropped and a ``dropped`` counter goes out with the next
event, so the client knows its view is incomplete instead of silently
believing it is current.

**Events are notifications, not state.** Each carries an id and enough to
identify what changed; the client re-fetches from the REST endpoints. A stream
that shipped full state would become a second, subtly different source of
truth for numbers the dashboard already gets from ``/metrics`` — and the two
would drift.

**Heartbeats.** A quiet SSE connection is indistinguishable from a dead one,
and proxies close idle connections. A comment frame every 15 seconds keeps it
open and lets the client tell "nothing is happening" from "nothing is
connected".

Single-process only. That is written down rather than discovered: with two
workers a subscriber attached to worker A never sees worker B's events. The
transactional outbox is what makes swapping in a real broker mechanical.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.core.clock import Clock
from app.deps import get_clock
from app.security.auth import Principal, require_api_token

router = APIRouter(prefix="/api/v1/stream", tags=["stream"])

log = logging.getLogger(__name__)

#: Per-subscriber buffer. Deep enough to absorb a burst from a batch run,
#: shallow enough that a hundred abandoned tabs cannot exhaust memory.
QUEUE_MAXSIZE = 256

#: Proxies commonly idle out at 30-60s.
HEARTBEAT_SECONDS = 15.0

#: Events a client may see. An allowlist, so a future publisher cannot leak an
#: internal event name -- or its payload -- to a browser by accident.
PUBLIC_EVENTS: frozenset[str] = frozenset(
    {
        "case.detected",
        "case.diagnosed",
        "case.strategy_formed",
        "case.awaiting_approval",
        "case.executing",
        "case.monitoring",
        "case.recovered",
        "case.stopped",
        "case.control_held",
        "policy.clamped",
        "approval.requested",
        "approval.approved",
        "approval.rejected",
        "approval.expired",
        "action.dispatched",
        "recovery.verified",
        "stopping_rule.fired",
        "outbox.deferral_cancelled",
    }
)


@dataclass(eq=False)
class _Subscriber:
    """One connected client.

    ``eq=False`` keeps identity-based equality and hashing. The default
    ``eq=True`` would compare subscribers by field value and make the class
    unhashable, so the subscriber set could not hold it — and two clients that
    happened to have equally-full queues would compare equal, which is not what
    "the same subscriber" means. Identity is exactly the right notion here.
    """

    queue: asyncio.Queue[str] = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_MAXSIZE))
    dropped: int = 0


class EventBus:
    """In-process fan-out. One instance per application."""

    def __init__(self) -> None:
        self._subscribers: set[_Subscriber] = set()
        self._seq = 0

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: str, data: dict[str, Any]) -> int:
        """Fan an event out to every subscriber. Never raises, never blocks.

        Publishing happens on the request path, sometimes inside a database
        transaction. A failure to notify a browser must never fail a recovery,
        so every error here is swallowed and logged.
        """
        if event not in PUBLIC_EVENTS:
            log.warning("refusing to publish non-public event %r", event)
            return 0

        self._seq += 1
        delivered = 0
        for sub in list(self._subscribers):
            payload = json.dumps({**data, "seq": self._seq, "dropped": sub.dropped})
            frame = f"id: {self._seq}\nevent: {event}\ndata: {payload}\n\n"
            try:
                sub.queue.put_nowait(frame)
                delivered += 1
            except asyncio.QueueFull:
                # Drop the OLDEST, keep the newest: a stale view of a case is
                # worth less than the current one, and the dropped counter
                # tells the client its view is incomplete.
                try:
                    sub.queue.get_nowait()
                    sub.queue.put_nowait(frame)
                    sub.dropped += 1
                    delivered += 1
                except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                    sub.dropped += 1
            except Exception:  # pragma: no cover - defensive
                log.exception("event publish failed; continuing")
        return delivered

    def subscribe(self) -> _Subscriber:
        sub = _Subscriber()
        self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: _Subscriber) -> None:
        self._subscribers.discard(sub)


#: The application-wide bus. A module global because it is process-local
#: state by definition -- see the module docstring on single-process scope.
bus = EventBus()


async def _event_stream(request: Request, sub: _Subscriber, clock: Clock) -> AsyncIterator[str]:
    try:
        yield (
            "event: connected\n"
            f"data: {json.dumps({'at': clock.now_utc().isoformat(), 'note': 'single-process in-memory bus'})}\n\n"
        )
        while True:
            if await request.is_disconnected():
                return
            try:
                frame = await asyncio.wait_for(sub.queue.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                # A comment frame. Distinguishes "quiet" from "disconnected".
                yield ": heartbeat\n\n"
                continue
            yield frame
    finally:
        bus.unsubscribe(sub)


@router.get("/events", summary="Live event feed (SSE)")
async def stream_events(
    request: Request,
    clock: Annotated[Clock, Depends(get_clock)],
    _principal: Annotated[Principal, Depends(require_api_token)],
) -> StreamingResponse:
    """Subscribe to the live feed.

    ``X-Accel-Buffering: no`` is set because a buffering reverse proxy turns
    SSE into a request that appears to hang — the events arrive, eventually,
    all at once, which looks exactly like a broken feature during a demo.
    """
    sub = bus.subscribe()
    return StreamingResponse(
        _event_stream(request, sub, clock),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
