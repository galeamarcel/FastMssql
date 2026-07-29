from __future__ import annotations

import asyncio
import importlib
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest


class ProducerFailure(BaseException):
    pass


class CleanupFailure(Exception):
    pass


class FakeOperationTimeoutError(TimeoutError):
    pass


@dataclass
class FakeSequence:
    events: list[object]
    remaining: float | None = None
    remaining_provider: Callable[[], float | None] | None = None
    total: int = 0
    push_started: asyncio.Event | None = None
    push_release: asyncio.Event | None = None
    abort_started: asyncio.Event | None = None
    abort_release: asyncio.Event | None = None
    abort_error: BaseException | None = None
    timeout_error: BaseException = field(default_factory=FakeOperationTimeoutError)
    active_pushes: int = 0
    maximum_active_pushes: int = 0

    async def reserve(self) -> None:
        self.events.append("reserve")

    async def activate(self) -> None:
        self.events.append("activate")

    async def push(self, rows: list[list[Any]]) -> int:
        self.active_pushes += 1
        self.maximum_active_pushes = max(
            self.maximum_active_pushes,
            self.active_pushes,
        )
        self.events.append(("push", [row.copy() for row in rows]))
        if self.push_started is not None:
            self.push_started.set()
        try:
            if self.push_release is not None:
                await self.push_release.wait()
            self.total += len(rows)
            return len(rows)
        finally:
            self.active_pushes -= 1

    async def finish(self) -> int:
        self.events.append("finish")
        return self.total

    async def abort(self, outcome: str) -> None:
        self.events.append(("abort", outcome))
        if self.abort_started is not None:
            self.abort_started.set()
        if self.abort_release is not None:
            await self.abort_release.wait()
        if self.abort_error is not None:
            raise self.abort_error

    async def expire(self) -> None:
        self.events.append("expire")
        raise self.timeout_error

    def remaining_timeout(self) -> float | None:
        self.events.append("remaining_timeout")
        if self.remaining_provider is not None:
            return self.remaining_provider()
        return self.remaining


@dataclass
class FakeRawOwner:
    sequence: FakeSequence
    events: list[object]
    invalid_chunk: bool = False
    invalid_identifier: bool = False
    constructions: int = 0

    def _native_bulk_sequence(
        self,
        table: str,
        columns: list[str],
        *,
        chunk_size: int,
    ) -> FakeSequence:
        self.constructions += 1
        self.events.append(("construct", table, columns, chunk_size))
        if self.invalid_chunk:
            raise ValueError("invalid chunk")
        if self.invalid_identifier:
            raise ValueError("invalid identifier")
        return self.sequence


def _coordinator():
    module = importlib.import_module("fastmssql._bulk_iterable")
    return module.native_bulk_insert_iterable


async def _run(
    raw: FakeRawOwner,
    rows: object,
    *,
    chunk_size: int,
    coordinator=None,
) -> int:
    if coordinator is None:
        coordinator = _coordinator()
    return await coordinator(
        raw,
        "dbo.items",
        ["id", "payload"],
        rows,
        chunk_size=chunk_size,
    )


class CountingSyncRows:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows
        self.iter_calls = 0
        self.pull_calls = 0
        self.closed = False

    def __iter__(self) -> CountingSyncRows:
        self.iter_calls += 1
        return self

    def __next__(self) -> object:
        self.pull_calls += 1
        if not self.rows:
            raise StopIteration
        value = self.rows.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self) -> None:
        self.closed = True


class CountingAsyncRows:
    def __init__(
        self,
        rows: list[object],
        *,
        wait_before_pull: asyncio.Event | None = None,
    ) -> None:
        self.rows = rows
        self.wait_before_pull = wait_before_pull
        self.aiter_calls = 0
        self.pull_calls = 0
        self.closed = False
        self.pull_started = asyncio.Event()

    def __aiter__(self) -> CountingAsyncRows:
        self.aiter_calls += 1
        return self

    async def __anext__(self) -> object:
        self.pull_calls += 1
        self.pull_started.set()
        if self.wait_before_pull is not None:
            await self.wait_before_pull.wait()
        if not self.rows:
            raise StopAsyncIteration
        value = self.rows.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    async def aclose(self) -> None:
        self.closed = True


class DualProtocolRows:
    def __init__(self) -> None:
        self.iter_calls = 0
        self.aiter_calls = 0
        self._async = CountingAsyncRows([[1, "one"]])

    def __iter__(self) -> Iterator[list[object]]:
        self.iter_calls += 1
        yield [99, "wrong"]

    def __aiter__(self) -> AsyncIterator[object]:
        self.aiter_calls += 1
        return self._async


class DeadlineCrossingRow(Sequence[object]):
    def __init__(self, failure: BaseException) -> None:
        self.failure = failure
        self.normalization_started = False

    def __len__(self) -> int:
        self.normalization_started = True
        return 2

    def __getitem__(self, index: int) -> object:
        raise self.failure


def _fixture_sequence(
    *,
    remaining: float | None = None,
    push_started: asyncio.Event | None = None,
    push_release: asyncio.Event | None = None,
) -> tuple[list[object], FakeSequence, FakeRawOwner]:
    events: list[object] = []
    sequence = FakeSequence(
        events,
        remaining=remaining,
        push_started=push_started,
        push_release=push_release,
    )
    return events, sequence, FakeRawOwner(sequence, events)


@pytest.mark.asyncio
async def test_sync_protocol_is_acquired_once_and_reservation_precedes_pull() -> None:
    events, _, raw = _fixture_sequence()

    class OrderedRows(CountingSyncRows):
        def __next__(self) -> object:
            events.append("sync_pull")
            return super().__next__()

    rows = OrderedRows([[1, "one"], (2, "two")])

    assert await _run(raw, rows, chunk_size=2) == 2
    assert rows.iter_calls == 1
    assert rows.pull_calls == 3
    assert events.index("reserve") < events.index("sync_pull")
    assert [event for event in events if event != "remaining_timeout"] == [
        ("construct", "dbo.items", ["id", "payload"], 2),
        "reserve",
        "sync_pull",
        "activate",
        "sync_pull",
        ("push", [[1, "one"], [2, "two"]]),
        "sync_pull",
        "finish",
    ]


@pytest.mark.asyncio
async def test_dual_protocol_prefers_async_and_acquires_it_once() -> None:
    _, _, raw = _fixture_sequence()
    rows = DualProtocolRows()

    assert await _run(raw, rows, chunk_size=4) == 1
    assert rows.aiter_calls == 1
    assert rows.iter_calls == 0
    assert rows._async.aiter_calls == 0
    assert rows._async.pull_calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", (False, True))
async def test_empty_producer_reserves_then_finishes_without_activation(
    asynchronous: bool,
) -> None:
    events, _, raw = _fixture_sequence()
    rows: object
    if asynchronous:
        rows = CountingAsyncRows([])
    else:
        rows = CountingSyncRows([])

    assert await _run(raw, rows, chunk_size=3) == 0
    assert [event for event in events if event != "remaining_timeout"] == [
        ("construct", "dbo.items", ["id", "payload"], 3),
        "reserve",
        "finish",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ("chunk", "identifier"))
async def test_validation_failure_never_acquires_or_advances_producer(
    invalid: str,
) -> None:
    events, _, raw = _fixture_sequence()
    raw.invalid_chunk = invalid == "chunk"
    raw.invalid_identifier = invalid == "identifier"
    rows = CountingSyncRows([[1, "one"]])

    with pytest.raises(ValueError):
        await _run(raw, rows, chunk_size=3)

    assert rows.iter_calls == 0
    assert rows.pull_calls == 0
    assert not any(event == "reserve" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    (
        "not rows",
        b"not rows",
        bytearray(b"not rows"),
        memoryview(b"not rows"),
        {"id": 1},
    ),
)
async def test_invalid_top_level_producer_never_reserves(rows: object) -> None:
    events, _, raw = _fixture_sequence()

    with pytest.raises(TypeError):
        await _run(raw, rows, chunk_size=3)

    assert "reserve" not in events
    assert not any(
        isinstance(event, tuple) and event[0] == "push" for event in events
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", (False, True))
async def test_backpressure_stops_pulls_while_push_is_pending(
    asynchronous: bool,
) -> None:
    push_started = asyncio.Event()
    push_release = asyncio.Event()
    events, sequence, raw = _fixture_sequence(
        push_started=push_started,
        push_release=push_release,
    )
    values = [[index, f"row-{index}"] for index in range(5)]
    rows: CountingSyncRows | CountingAsyncRows
    if asynchronous:
        rows = CountingAsyncRows(values)
    else:
        rows = CountingSyncRows(values)

    coordinator = _coordinator()
    task = asyncio.create_task(
        _run(raw, rows, chunk_size=3, coordinator=coordinator)
    )
    await asyncio.wait_for(push_started.wait(), timeout=1.0)
    assert rows.pull_calls == 3
    for _ in range(5):
        await asyncio.sleep(0)
    assert rows.pull_calls == 3
    assert sequence.active_pushes == 1

    push_release.set()
    assert await asyncio.wait_for(task, timeout=1.0) == 5
    assert rows.pull_calls == 6
    assert sequence.maximum_active_pushes == 1
    assert [event for event in events if isinstance(event, tuple)] == [
        ("construct", "dbo.items", ["id", "payload"], 3),
        (
            "push",
            [[0, "row-0"], [1, "row-1"], [2, "row-2"]],
        ),
        ("push", [[3, "row-3"], [4, "row-4"]]),
    ]


@pytest.mark.asyncio
async def test_non_sequence_row_aborts_and_preserves_normalization_error() -> None:
    events, _, raw = _fixture_sequence()
    rows = CountingSyncRows([[1, "one"], "not a row"])

    with pytest.raises(TypeError) as raised:
        await _run(raw, rows, chunk_size=3)

    assert "row" in str(raised.value).lower()
    assert rows.closed is True
    assert ("abort", "error") in events
    assert not any(
        isinstance(event, tuple) and event[0] == "push" for event in events
    )


@pytest.mark.asyncio
async def test_producer_baseexception_stays_primary_and_cleanup_is_chained() -> None:
    events, sequence, raw = _fixture_sequence()
    primary = ProducerFailure()
    cleanup = CleanupFailure("cleanup failed")
    sequence.abort_error = cleanup
    rows = CountingSyncRows([[1, "one"], primary])

    with pytest.raises(ProducerFailure) as raised:
        await _run(raw, rows, chunk_size=1)

    assert raised.value is primary
    assert raised.value.__cause__ is cleanup
    assert rows.closed is True
    assert ("abort", "error") in events


@pytest.mark.asyncio
async def test_cancellation_during_async_pull_finishes_abort_and_aclose() -> None:
    events, sequence, raw = _fixture_sequence()
    pull_release = asyncio.Event()
    abort_started = asyncio.Event()
    abort_release = asyncio.Event()
    sequence.abort_started = abort_started
    sequence.abort_release = abort_release
    rows = CountingAsyncRows([[1, "one"]], wait_before_pull=pull_release)

    coordinator = _coordinator()
    task = asyncio.create_task(
        _run(raw, rows, chunk_size=2, coordinator=coordinator)
    )
    await asyncio.wait_for(rows.pull_started.wait(), timeout=1.0)
    task.cancel()
    await asyncio.wait_for(abort_started.wait(), timeout=1.0)
    task.cancel()
    await asyncio.sleep(0)
    assert task.done() is False

    abort_release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(task), timeout=1.0)

    assert rows.pull_calls == 1
    assert rows.closed is True
    assert ("abort", "cancelled") in events


@pytest.mark.asyncio
async def test_cancellation_after_reservation_effect_waits_for_explicit_abort() -> None:
    events: list[object] = []

    class CancellingReservationSequence(FakeSequence):
        async def reserve(self) -> None:
            self.events.append("reserve_effective")
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            await asyncio.sleep(0)

    sequence = CancellingReservationSequence(events)
    raw = FakeRawOwner(sequence, events)
    rows = CountingSyncRows([[1, "one"]])

    task = asyncio.create_task(_run(raw, rows, chunk_size=2))
    with pytest.raises(asyncio.CancelledError):
        await task

    assert rows.pull_calls == 0
    assert rows.closed is True
    assert events == [
        ("construct", "dbo.items", ["id", "payload"], 2),
        "reserve_effective",
        ("abort", "cancelled"),
    ]


@pytest.mark.asyncio
async def test_cancellation_during_push_stops_later_pulls() -> None:
    push_started = asyncio.Event()
    push_release = asyncio.Event()
    events, _, raw = _fixture_sequence(
        push_started=push_started,
        push_release=push_release,
    )
    rows = CountingAsyncRows(
        [[1, "one"], [2, "two"], [3, "three"]],
    )

    coordinator = _coordinator()
    task = asyncio.create_task(
        _run(raw, rows, chunk_size=2, coordinator=coordinator)
    )
    await asyncio.wait_for(push_started.wait(), timeout=1.0)
    assert rows.pull_calls == 2
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert rows.pull_calls == 2
    assert rows.closed is True
    assert ("abort", "cancelled") in events


@pytest.mark.asyncio
async def test_async_pull_timeout_uses_sequence_typed_error_and_closes() -> None:
    events, sequence, raw = _fixture_sequence(remaining=0.01)
    pull_release = asyncio.Event()
    rows = CountingAsyncRows([[1, "one"]], wait_before_pull=pull_release)

    with pytest.raises(FakeOperationTimeoutError) as raised:
        await asyncio.wait_for(_run(raw, rows, chunk_size=2), timeout=1.0)

    assert raised.value is sequence.timeout_error
    assert rows.closed is True
    assert "expire" in events
    assert not any(
        isinstance(event, tuple) and event[0] == "abort" for event in events
    )


@pytest.mark.asyncio
async def test_deadline_crossing_sync_pull_uses_typed_timeout_not_producer_error() -> None:
    events, sequence, raw = _fixture_sequence()
    primary = ProducerFailure()

    class DeadlineCrossingRows(CountingSyncRows):
        def __next__(self) -> object:
            self.pull_calls += 1
            raise primary

    rows = DeadlineCrossingRows([])
    sequence.remaining_provider = (
        lambda: 1.0 if rows.pull_calls == 0 else 0.0
    )

    with pytest.raises(FakeOperationTimeoutError) as raised:
        await _run(raw, rows, chunk_size=2)

    assert raised.value is sequence.timeout_error
    assert rows.pull_calls == 1
    assert rows.closed is True
    assert "expire" in events
    assert not any(
        isinstance(event, tuple) and event[0] == "abort" for event in events
    )


@pytest.mark.asyncio
async def test_deadline_crossing_normalization_uses_typed_timeout() -> None:
    events, sequence, raw = _fixture_sequence()
    row = DeadlineCrossingRow(ProducerFailure())
    rows = CountingSyncRows([row])
    sequence.remaining_provider = (
        lambda: 0.0 if row.normalization_started else 1.0
    )

    with pytest.raises(FakeOperationTimeoutError) as raised:
        await _run(raw, rows, chunk_size=2)

    assert raised.value is sequence.timeout_error
    assert rows.closed is True
    assert "expire" in events
    assert not any(
        isinstance(event, tuple) and event[0] == "abort" for event in events
    )


@pytest.mark.asyncio
async def test_async_deadline_wins_if_producer_transforms_timeout_cancellation() -> None:
    events, sequence, raw = _fixture_sequence(remaining=0.01)
    primary = ProducerFailure()

    class CancellationTransformingRows(CountingAsyncRows):
        async def __anext__(self) -> object:
            self.pull_calls += 1
            self.pull_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise primary

    rows = CancellationTransformingRows([])

    with pytest.raises(FakeOperationTimeoutError) as raised:
        await asyncio.wait_for(_run(raw, rows, chunk_size=2), timeout=1.0)

    assert raised.value is sequence.timeout_error
    assert rows.closed is True
    assert "expire" in events
    assert not any(
        isinstance(event, tuple) and event[0] == "abort" for event in events
    )
