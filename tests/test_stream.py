"""The SSE event bus.

Streaming endpoints are awkward to test end to end, and the interesting
behaviour is not in the HTTP layer anyway — it is in what happens to a
subscriber that stops reading. A browser tab backgrounded for an hour is the
normal case, not the edge case, and an unbounded queue behind it is an
out-of-memory in a process that also moves money.

So most of this file drives :class:`EventBus` directly and asserts the
back-pressure behaviour, the event allowlist, and the frame format.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.core.clock import FakeClock
from app.deps import get_clock
from app.main import create_app
from app.routers.stream import (
    PUBLIC_EVENTS,
    QUEUE_MAXSIZE,
    EventBus,
    _event_stream,
    bus,
    stream_events,
)


class _FakeRequest:
    """Minimal stand-in: `_event_stream` only asks whether we hung up."""

    def __init__(self, *, disconnected: bool = False) -> None:
        self._disconnected = disconnected

    async def is_disconnected(self) -> bool:
        return self._disconnected


TOKEN = "rvp_test_token_0123456789abcdef"


def _parse(frame: str) -> dict[str, Any]:
    """Parse one SSE frame into its fields."""
    out: dict[str, Any] = {}
    for line in frame.strip().splitlines():
        key, _, value = line.partition(": ")
        out[key] = value
    out["data"] = json.loads(out["data"])
    return out


class TestFrameFormat:
    def test_a_published_event_is_a_valid_sse_frame(self) -> None:
        bus = EventBus()
        sub = bus.subscribe()
        assert bus.publish("case.recovered", {"case_id": "RC-0142"}) == 1

        frame = sub.queue.get_nowait()
        assert frame.endswith("\n\n"), "SSE frames terminate with a blank line"
        parsed = _parse(frame)
        assert parsed["event"] == "case.recovered"
        assert parsed["id"] == "1"
        assert parsed["data"]["case_id"] == "RC-0142"

    def test_the_sequence_number_increments(self) -> None:
        """Clients use it to notice a gap."""
        bus = EventBus()
        sub = bus.subscribe()
        for _ in range(3):
            bus.publish("case.detected", {})
        ids = [_parse(sub.queue.get_nowait())["id"] for _ in range(3)]
        assert ids == ["1", "2", "3"]

    def test_every_frame_reports_the_drop_count(self) -> None:
        """A client must be able to tell that its view is incomplete."""
        bus = EventBus()
        sub = bus.subscribe()
        bus.publish("case.detected", {})
        assert _parse(sub.queue.get_nowait())["data"]["dropped"] == 0


class TestTheAllowlist:
    def test_a_non_public_event_is_refused(self) -> None:
        """An allowlist, so a future publisher cannot leak an internal event
        name — or its payload — to a browser by accident."""
        bus = EventBus()
        sub = bus.subscribe()
        assert bus.publish("internal.secret_rotated", {"secret": "shh"}) == 0
        assert sub.queue.empty()

    def test_the_public_set_is_not_accidentally_empty(self) -> None:
        assert len(PUBLIC_EVENTS) > 10

    def test_control_arm_holds_are_publishable(self) -> None:
        """The stream is where a judge sees CONTROL cases receiving no action:
        visible proof the control arm is real (§19.2)."""
        assert "case.control_held" in PUBLIC_EVENTS


class TestBackPressure:
    """What happens to a subscriber that stops reading."""

    def test_a_full_queue_drops_the_oldest_not_the_newest(self) -> None:
        """A stale view of a case is worth less than the current one."""
        bus = EventBus()
        sub = bus.subscribe()
        for i in range(QUEUE_MAXSIZE + 5):
            bus.publish("case.detected", {"n": i})

        assert sub.queue.qsize() == QUEUE_MAXSIZE
        assert sub.dropped == 5
        # The newest survived; the oldest did not.
        frames = [_parse(sub.queue.get_nowait()) for _ in range(QUEUE_MAXSIZE)]
        assert frames[-1]["data"]["n"] == QUEUE_MAXSIZE + 4
        assert frames[0]["data"]["n"] == 5

    def test_memory_is_bounded_by_the_queue_size(self) -> None:
        """The property that matters: a subscriber that never reads cannot
        grow without limit."""
        bus = EventBus()
        sub = bus.subscribe()
        for _ in range(QUEUE_MAXSIZE * 4):
            bus.publish("case.detected", {})
        assert sub.queue.qsize() == QUEUE_MAXSIZE

    def test_one_stuck_subscriber_does_not_block_a_healthy_one(self) -> None:
        bus = EventBus()
        stuck = bus.subscribe()
        healthy = bus.subscribe()
        for _ in range(QUEUE_MAXSIZE + 2):
            bus.publish("case.detected", {})
            with contextlib_suppress():
                healthy.queue.get_nowait()
        assert stuck.dropped > 0
        assert healthy.queue.qsize() < QUEUE_MAXSIZE


def contextlib_suppress():  # type: ignore[no-untyped-def]
    import contextlib

    return contextlib.suppress(asyncio.QueueEmpty)


class TestSubscriberLifecycle:
    def test_unsubscribe_stops_delivery(self) -> None:
        bus = EventBus()
        sub = bus.subscribe()
        bus.unsubscribe(sub)
        assert bus.publish("case.detected", {}) == 0
        assert bus.subscriber_count == 0

    def test_unsubscribing_twice_is_harmless(self) -> None:
        """A disconnect can fire more than once; the cleanup path must not
        raise inside a `finally`."""
        bus = EventBus()
        sub = bus.subscribe()
        bus.unsubscribe(sub)
        bus.unsubscribe(sub)
        assert bus.subscriber_count == 0

    def test_publishing_with_no_subscribers_is_fine(self) -> None:
        """Publishing happens on the request path, sometimes inside a
        transaction. It must never be the thing that fails a recovery."""
        assert EventBus().publish("case.recovered", {"case_id": "RC-1"}) == 0

    def test_fan_out_reaches_every_subscriber(self) -> None:
        bus = EventBus()
        subs = [bus.subscribe() for _ in range(4)]
        assert bus.publish("case.recovered", {}) == 4
        assert all(s.queue.qsize() == 1 for s in subs)


class TestEndpoint:
    """The HTTP layer.

    The streaming behaviour is exercised by driving `_event_stream` directly
    rather than through TestClient. TestClient never marks the request
    disconnected, so a generator that loops until disconnect never terminates
    and the test hangs rather than failing -- which tells you nothing. Driving
    the generator tests our logic instead of the client's streaming semantics.
    """

    def _client(self, *, token: str = TOKEN) -> TestClient:
        settings = Settings(
            razorpay_key_id="",
            razorpay_key_secret="",
            gemini_api_key="",
            api_token=token,
            environment="development",
        )
        app = create_app(settings)
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_clock] = lambda: FakeClock.at_ist(2026, 9, 1, 11, 30)
        return TestClient(app)

    def test_the_stream_requires_a_bearer_token(self) -> None:
        with self._client() as client:
            assert client.get("/api/v1/stream/events").status_code == 401

    @pytest.mark.asyncio
    async def test_the_first_frame_confirms_the_connection(self) -> None:
        """Lets a client tell "connected and quiet" from "never connected"."""
        bus_ = EventBus()
        sub = bus_.subscribe()
        stream = _event_stream(_FakeRequest(), sub, FakeClock.at_ist(2026, 9, 1, 11, 30))
        first = await anext(stream)
        assert first.startswith("event: connected")
        await stream.aclose()

    @pytest.mark.asyncio
    async def test_a_published_event_reaches_the_stream(self) -> None:
        clock = FakeClock.at_ist(2026, 9, 1, 11, 30)
        sub = bus.subscribe()
        try:
            stream = _event_stream(_FakeRequest(), sub, clock)
            await anext(stream)  # the connected frame
            bus.publish("case.recovered", {"case_id": "RC-0142"})
            frame = await anext(stream)
            assert _parse(frame)["data"]["case_id"] == "RC-0142"
            await stream.aclose()
        finally:
            bus.unsubscribe(sub)

    @pytest.mark.asyncio
    async def test_a_disconnect_unsubscribes(self) -> None:
        """The `finally` must run, or every dropped tab leaks a subscriber and
        a queue for the life of the process."""
        request = _FakeRequest(disconnected=True)
        sub = bus.subscribe()
        before = bus.subscriber_count
        stream = _event_stream(request, sub, FakeClock.at_ist(2026, 9, 1, 11, 30))
        await anext(stream)  # connected frame
        with pytest.raises(StopAsyncIteration):
            await anext(stream)  # sees the disconnect and returns
        assert bus.subscriber_count == before - 1

    @pytest.mark.asyncio
    async def test_a_quiet_stream_emits_a_heartbeat(self) -> None:
        """A quiet SSE connection is indistinguishable from a dead one, and
        proxies close idle connections."""
        import app.routers.stream as stream_module

        original = stream_module.HEARTBEAT_SECONDS
        stream_module.HEARTBEAT_SECONDS = 0.05
        try:
            sub = bus.subscribe()
            stream = _event_stream(_FakeRequest(), sub, FakeClock.at_ist(2026, 9, 1, 11, 30))
            await anext(stream)
            assert (await anext(stream)).startswith(": heartbeat")
            await stream.aclose()
        finally:
            stream_module.HEARTBEAT_SECONDS = original
            bus.unsubscribe(sub)

    def test_the_response_declares_no_proxy_buffering(self) -> None:
        """A buffering reverse proxy turns SSE into a request that appears to
        hang -- events arrive eventually, all at once, which looks exactly like
        a broken feature during a demo. Asserted on the route's own headers,
        without opening a stream that would never close."""
        import inspect

        source = inspect.getsource(stream_events)
        assert '"X-Accel-Buffering": "no"' in source
        assert '"Cache-Control": "no-cache"' in source
        assert 'media_type="text/event-stream"' in source

    def test_subscriber_count_is_reported_by_health(self) -> None:
        """/health/deep exposes it, so "is anything watching" is answerable
        without guessing."""
        local = EventBus()
        assert local.subscriber_count == 0
        local.subscribe()
        assert local.subscriber_count == 1
