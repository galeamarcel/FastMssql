from __future__ import annotations

import asyncio
import importlib
import importlib.util
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field

import pytest

from fastmssql import Parameters


class ProducerFailure(BaseException):
    pass


class CleanupFailure(Exception):
    pass


class FakeOperationTimeoutError(TimeoutError):
    pass


class FakeCommitOutcomeUnknown(Exception):
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
    abort_progress: dict[str, int | bool | None] = field(
        default_factory=lambda: {
            "active_parameter_set_index": None,
            "confirmed_committed_parameter_sets": 0,
            "partial_commit_possible": False,
        }
    )
    push_error: BaseException | None = None
    push_error_at: int | None = None
    timeout_error: BaseException = field(default_factory=FakeOperationTimeoutError)
    push_calls: int = 0
    active_pushes: int = 0
    maximum_active_pushes: int = 0

    async def reserve(self) -> None:
        self.events.append("reserve")

    async def activate(self) -> None:
        self.events.append("activate")

    async def push(self, parameter_sets: list[object]) -> int:
        push_call = self.push_calls
        self.push_calls += 1
        self.active_pushes += 1
        self.maximum_active_pushes = max(
            self.maximum_active_pushes,
            self.active_pushes,
        )
        self.events.append(("push", parameter_sets.copy()))
        if self.push_started is not None:
            self.push_started.set()
        try:
            if self.push_release is not None:
                await self.push_release.wait()
            if (
                self.push_error is not None
                and (
                    self.push_error_at is None
                    or self.push_error_at == push_call
                )
            ):
                raise self.push_error
            self.total += len(parameter_sets)
            return len(parameter_sets)
        finally:
            self.active_pushes -= 1

    async def finish(self) -> int:
        self.events.append("finish")
        return self.total

    async def abort(self, outcome: str) -> dict[str, int | bool | None]:
        self.events.append(("abort", outcome))
        if self.abort_started is not None:
            self.abort_started.set()
        if self.abort_release is not None:
            await self.abort_release.wait()
        if self.abort_error is not None:
            raise self.abort_error
        return self.abort_progress.copy()

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
    transaction: bool = False
    invalid_chunk: bool = False
    invalid_atomic: bool = False
    constructions: int = 0

    def _execute_many_sequence(
        self,
        sql: str,
        *,
        atomic: bool | None = None,
        chunk_size: int,
    ) -> FakeSequence:
        self.constructions += 1
        self.events.append(("construct", sql, atomic, chunk_size))
        if self.invalid_chunk:
            raise ValueError("invalid chunk")
        if self.invalid_atomic:
            raise TypeError("invalid atomic")
        if self.transaction:
            assert atomic is None
        else:
            assert isinstance(atomic, bool)
        return self.sequence


def _coordinator():
    spec = importlib.util.find_spec("fastmssql._execute_many")
    assert spec is not None, "missing bounded execute-many coordinator"
    module = importlib.import_module("fastmssql._execute_many")
    return module.execute_many_iterable


async def _run(
    raw: FakeRawOwner,
    parameter_sets: object,
    *,
    chunk_size: int,
    atomic: bool | None = True,
    coordinator=None,
) -> int:
    if coordinator is None:
        coordinator = _coordinator()
    kwargs: dict[str, object] = {"chunk_size": chunk_size}
    if not raw.transaction:
        kwargs["atomic"] = atomic
    return await coordinator(
        raw,
        "INSERT INTO dbo.items VALUES (@P1, @P2)",
        parameter_sets,
        **kwargs,
    )


class CountingSyncSets:
    def __init__(self, values: list[object]) -> None:
        self.values = values
        self.iter_calls = 0
        self.pull_calls = 0
        self.close_calls = 0

    def __iter__(self) -> CountingSyncSets:
        self.iter_calls += 1
        return self

    def __next__(self) -> object:
        self.pull_calls += 1
        if not self.values:
            raise StopIteration
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self) -> None:
        self.close_calls += 1


class CountingAsyncSets:
    def __init__(
        self,
        values: list[object],
        *,
        wait_before_pull: asyncio.Event | None = None,
    ) -> None:
        self.values = values
        self.wait_before_pull = wait_before_pull
        self.aiter_calls = 0
        self.pull_calls = 0
        self.aclose_calls = 0
        self.pull_started = asyncio.Event()

    def __aiter__(self) -> CountingAsyncSets:
        self.aiter_calls += 1
        return self

    async def __anext__(self) -> object:
        self.pull_calls += 1
        self.pull_started.set()
        if self.wait_before_pull is not None:
            await self.wait_before_pull.wait()
        if not self.values:
            raise StopAsyncIteration
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    async def aclose(self) -> None:
        self.aclose_calls += 1


class DualProtocolSets:
    def __init__(self) -> None:
        self.iter_calls = 0
        self.aiter_calls = 0
        self._async = CountingAsyncSets([[1, "one"]])

    def __iter__(self) -> Iterator[list[object]]:
        self.iter_calls += 1
        yield [99, "wrong"]

    def __aiter__(self) -> AsyncIterator[object]:
        self.aiter_calls += 1
        return self._async


def _fixture_sequence(
    *,
    remaining: float | None = None,
    push_started: asyncio.Event | None = None,
    push_release: asyncio.Event | None = None,
    transaction: bool = False,
) -> tuple[list[object], FakeSequence, FakeRawOwner]:
    events: list[object] = []
    sequence = FakeSequence(
        events,
        remaining=remaining,
        push_started=push_started,
        push_release=push_release,
    )
    return events, sequence, FakeRawOwner(
        sequence,
        events,
        transaction=transaction,
    )


def _assert_failure_metadata(
    error: BaseException,
    *,
    index: int,
    committed: int,
    partial: bool,
) -> None:
    assert error.parameter_set_index == index
    assert error.confirmed_committed_parameter_sets == committed
    assert error.partial_commit_possible is partial


@pytest.mark.asyncio
@pytest.mark.parametrize("transaction", (False, True))
async def test_sync_protocol_is_once_and_reservation_precedes_first_pull(
    transaction: bool,
) -> None:
    events, _, raw = _fixture_sequence(transaction=transaction)

    class OrderedSets(CountingSyncSets):
        def __next__(self) -> object:
            events.append("sync_pull")
            return super().__next__()

    values = OrderedSets([[1, "one"], [2, "two"]])

    assert await _run(raw, values, chunk_size=2) == 2
    assert values.iter_calls == 1
    assert values.pull_calls == 3
    assert values.close_calls == 0
    assert events.index("reserve") < events.index("sync_pull")
    assert [event for event in events if event != "remaining_timeout"] == [
        (
            "construct",
            "INSERT INTO dbo.items VALUES (@P1, @P2)",
            None if transaction else True,
            2,
        ),
        "reserve",
        "sync_pull",
        "activate",
        "sync_pull",
        ("push", [[1, "one"], [2, "two"]]),
        "sync_pull",
        "finish",
    ]


@pytest.mark.asyncio
async def test_dual_protocol_prefers_async_and_acquires_once() -> None:
    _, _, raw = _fixture_sequence()
    values = DualProtocolSets()

    assert await _run(raw, values, chunk_size=4) == 1
    assert values.aiter_calls == 1
    assert values.iter_calls == 0
    assert values._async.aiter_calls == 0
    assert values._async.pull_calls == 2
    assert values._async.aclose_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", (False, True))
async def test_empty_reserves_then_finishes_without_activation_or_close(
    asynchronous: bool,
) -> None:
    events, _, raw = _fixture_sequence(transaction=True)
    values: CountingSyncSets | CountingAsyncSets
    values = CountingAsyncSets([]) if asynchronous else CountingSyncSets([])

    assert await _run(raw, values, chunk_size=3) == 0
    assert [event for event in events if event != "remaining_timeout"] == [
        (
            "construct",
            "INSERT INTO dbo.items VALUES (@P1, @P2)",
            None,
            3,
        ),
        "reserve",
        "finish",
    ]
    close_calls = (
        values.aclose_calls
        if isinstance(values, CountingAsyncSets)
        else values.close_calls
    )
    assert close_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ("chunk", "atomic"))
async def test_keyword_validation_never_acquires_or_advances_producer(
    invalid: str,
) -> None:
    events, _, raw = _fixture_sequence()
    raw.invalid_chunk = invalid == "chunk"
    raw.invalid_atomic = invalid == "atomic"
    values = CountingSyncSets([[1, "one"]])

    with pytest.raises((TypeError, ValueError)):
        await _run(raw, values, chunk_size=3)

    assert values.iter_calls == 0
    assert values.pull_calls == 0
    assert "reserve" not in events


@pytest.mark.asyncio
async def test_explicit_connection_atomic_none_reaches_raw_validation() -> None:
    observed_kwargs: dict[str, object] = {}

    class RejectingRawOwner:
        def _execute_many_sequence(
            self,
            sql: str,
            **kwargs: object,
        ) -> None:
            observed_kwargs.update(kwargs)
            raise TypeError("atomic must be exactly bool")

    values = CountingSyncSets([[1]])
    with pytest.raises(TypeError, match="atomic"):
        await _coordinator()(
            RejectingRawOwner(),
            "INSERT INTO dbo.items VALUES (@P1)",
            values,
            atomic=None,
            chunk_size=2,
        )

    assert observed_kwargs == {"atomic": None, "chunk_size": 2}
    assert values.iter_calls == values.pull_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values",
    (
        "not sets",
        b"not sets",
        bytearray(b"not sets"),
        memoryview(b"not sets"),
        {"id": 1},
    ),
)
async def test_invalid_top_level_source_never_constructs_or_reserves(
    values: object,
) -> None:
    events, _, raw = _fixture_sequence()

    with pytest.raises(TypeError):
        await _run(raw, values, chunk_size=3)

    assert raw.constructions == 0
    assert "reserve" not in events


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", (False, True))
async def test_backpressure_stops_pulls_while_push_is_pending(
    asynchronous: bool,
) -> None:
    _coordinator()
    push_started = asyncio.Event()
    push_release = asyncio.Event()
    events, sequence, raw = _fixture_sequence(
        push_started=push_started,
        push_release=push_release,
    )
    items = [[index, f"row-{index}"] for index in range(5)]
    values: CountingSyncSets | CountingAsyncSets
    values = (
        CountingAsyncSets(items)
        if asynchronous
        else CountingSyncSets(items)
    )

    task = asyncio.create_task(_run(raw, values, chunk_size=2))
    await asyncio.wait_for(push_started.wait(), timeout=1.0)
    assert values.pull_calls == 2
    for _ in range(5):
        await asyncio.sleep(0)
    assert values.pull_calls == 2
    assert sequence.active_pushes == 1

    push_release.set()
    assert await asyncio.wait_for(task, timeout=1.0) == 5
    assert values.pull_calls == 6
    assert sequence.maximum_active_pushes == 1
    pushes = [
        event
        for event in events
        if isinstance(event, tuple) and event[0] == "push"
    ]
    assert pushes == [
        ("push", [[0, "row-0"], [1, "row-1"]]),
        ("push", [[2, "row-2"], [3, "row-3"]]),
        ("push", [[4, "row-4"]]),
    ]


@pytest.mark.asyncio
async def test_invalid_parameter_set_aborts_with_global_index_and_privacy() -> None:
    events, _, raw = _fixture_sequence()
    values = CountingSyncSets([[1, "one"], ("not", "a list")])

    with pytest.raises(TypeError) as raised:
        await _run(raw, values, chunk_size=3)

    _assert_failure_metadata(
        raised.value,
        index=1,
        committed=0,
        partial=False,
    )
    assert "not a list" not in str(raised.value)
    assert values.close_calls == 1
    assert ("abort", "error") in events
    # The approved lazy contract activates after the first valid set. A later
    # set can still fail before any chunk reaches SQL Server.
    assert [event for event in events if event == "activate"] == ["activate"]
    assert not any(
        isinstance(event, tuple) and event[0] == "push" for event in events
    )


@pytest.mark.asyncio
async def test_invalid_parameter_set_message_is_stable() -> None:
    _, _, raw = _fixture_sequence()

    with pytest.raises(
        TypeError,
        match=(
            "^each execute_many parameter set must be a list or "
            "Parameters object$"
        ),
    ):
        await _run(raw, [(1,)], chunk_size=1)


@pytest.mark.asyncio
async def test_named_parameter_set_message_is_stable() -> None:
    _, _, raw = _fixture_sequence()

    with pytest.raises(ValueError) as raised:
        await _run(
            raw,
            [Parameters(secret_value="NeverExposeThis")],
            chunk_size=1,
        )

    assert str(raised.value) == (
        "Named parameters are not supported by the SQL Server wire protocol. "
        "Use positional parameters instead. Found 1 named parameter(s)"
    )
    assert "NeverExposeThis" not in str(raised.value)


@pytest.mark.asyncio
async def test_parameter_count_limit_message_is_stable() -> None:
    _, _, raw = _fixture_sequence()

    with pytest.raises(ValueError) as raised:
        await _run(raw, [list(range(2_099))], chunk_size=1)

    assert str(raised.value) == (
        "Too many parameters: 2099 provided, but FastMssql supports maximum "
        "2,098 user parameters per query "
        "(SQL Server RPC limit 2,100 minus 2 internal parameters)"
    )


@pytest.mark.asyncio
async def test_atomic_false_producer_failure_reports_confirmed_chunks() -> None:
    events, _, raw = _fixture_sequence()
    primary = ProducerFailure()
    values = CountingSyncSets([[1], [2], [3], primary])

    with pytest.raises(ProducerFailure) as raised:
        await _run(
            raw,
            values,
            chunk_size=2,
            atomic=False,
        )

    assert raised.value is primary
    _assert_failure_metadata(
        raised.value,
        index=3,
        committed=2,
        partial=True,
    )
    assert values.close_calls == 1
    assert [event for event in events if isinstance(event, tuple)] == [
        (
            "construct",
            "INSERT INTO dbo.items VALUES (@P1, @P2)",
            False,
            2,
        ),
        ("push", [[1], [2]]),
        ("abort", "error"),
    ]


@pytest.mark.asyncio
async def test_primary_baseexception_survives_cleanup_failure() -> None:
    _, sequence, raw = _fixture_sequence()
    primary = ProducerFailure()
    cleanup = CleanupFailure("cleanup failed")
    sequence.abort_error = cleanup
    values = CountingSyncSets([[1], primary])

    with pytest.raises(ProducerFailure) as raised:
        await _run(raw, values, chunk_size=1)

    assert raised.value is primary
    assert raised.value.__cause__ is cleanup
    _assert_failure_metadata(
        raised.value,
        index=1,
        committed=0,
        partial=False,
    )
    assert values.close_calls == 1


@pytest.mark.asyncio
async def test_chunk_commit_unknown_keeps_rust_progress_and_no_retry() -> None:
    events, sequence, raw = _fixture_sequence()
    primary = FakeCommitOutcomeUnknown("commit acknowledgement lost")
    primary.parameter_set_index = 2
    primary.confirmed_committed_parameter_sets = 2
    primary.partial_commit_possible = True
    sequence.push_error = primary
    sequence.push_error_at = 1
    values = CountingSyncSets([[1], [2], [3], [4], [5]])

    with pytest.raises(FakeCommitOutcomeUnknown) as raised:
        await _run(raw, values, chunk_size=2, atomic=False)

    assert raised.value is primary
    _assert_failure_metadata(
        raised.value,
        index=2,
        committed=2,
        partial=True,
    )
    assert values.pull_calls == 4
    assert values.close_calls == 1
    assert [event for event in events if event == "activate"] == ["activate"]
    assert [
        event
        for event in events
        if isinstance(event, tuple) and event[0] == "push"
    ] == [
        ("push", [[1], [2]]),
        ("push", [[3], [4]]),
    ]


@pytest.mark.asyncio
async def test_cancellation_during_async_pull_finishes_abort_and_aclose() -> None:
    _coordinator()
    events, sequence, raw = _fixture_sequence()
    pull_release = asyncio.Event()
    abort_started = asyncio.Event()
    abort_release = asyncio.Event()
    sequence.abort_started = abort_started
    sequence.abort_release = abort_release
    values = CountingAsyncSets([[1]], wait_before_pull=pull_release)

    async def capture_cancellation() -> asyncio.CancelledError:
        try:
            await _run(raw, values, chunk_size=2)
        except asyncio.CancelledError as error:
            # A terminal cancelled Task reconstructs CancelledError for its
            # waiter and drops instance metadata. Catch it at the API boundary
            # where application cleanup code can actually observe it.
            return error
        raise AssertionError("execute_many unexpectedly completed")

    task = asyncio.create_task(capture_cancellation())
    await asyncio.wait_for(values.pull_started.wait(), timeout=1.0)
    task.cancel()
    await asyncio.wait_for(abort_started.wait(), timeout=1.0)
    task.cancel()
    await asyncio.sleep(0)
    assert task.done() is False

    abort_release.set()
    cancellation = await asyncio.wait_for(asyncio.shield(task), timeout=1.0)

    _assert_failure_metadata(
        cancellation,
        index=0,
        committed=0,
        partial=False,
    )
    assert values.pull_calls == 1
    assert values.aclose_calls == 1
    assert ("abort", "cancelled") in events


@pytest.mark.asyncio
async def test_cancellation_after_reservation_effect_aborts_before_pull() -> None:
    _coordinator()
    events: list[object] = []

    class CancellingReservationSequence(FakeSequence):
        async def reserve(self) -> None:
            self.events.append("reserve_effective")
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            await asyncio.sleep(0)

    sequence = CancellingReservationSequence(events)
    raw = FakeRawOwner(sequence, events, transaction=True)
    values = CountingSyncSets([[1]])

    task = asyncio.create_task(_run(raw, values, chunk_size=2))
    with pytest.raises(asyncio.CancelledError) as raised:
        await task

    _assert_failure_metadata(
        raised.value,
        index=0,
        committed=0,
        partial=False,
    )
    assert values.pull_calls == 0
    assert values.close_calls == 1
    assert events == [
        (
            "construct",
            "INSERT INTO dbo.items VALUES (@P1, @P2)",
            None,
            2,
        ),
        "reserve_effective",
        ("abort", "cancelled"),
    ]


@pytest.mark.asyncio
async def test_cancellation_during_push_stops_later_pulls() -> None:
    _coordinator()
    push_started = asyncio.Event()
    push_release = asyncio.Event()
    events, sequence, raw = _fixture_sequence(
        push_started=push_started,
        push_release=push_release,
    )
    sequence.abort_progress["active_parameter_set_index"] = 0
    values = CountingAsyncSets([[1], [2], [3]])

    task = asyncio.create_task(_run(raw, values, chunk_size=2))
    await asyncio.wait_for(push_started.wait(), timeout=1.0)
    assert values.pull_calls == 2
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as raised:
        await task

    _assert_failure_metadata(
        raised.value,
        index=0,
        committed=0,
        partial=False,
    )
    assert values.pull_calls == 2
    assert values.aclose_calls == 1
    assert ("abort", "cancelled") in events


@pytest.mark.asyncio
async def test_async_pull_timeout_uses_sequence_typed_error_and_closes() -> None:
    events, sequence, raw = _fixture_sequence(remaining=0.01)
    values = CountingAsyncSets(
        [[1]],
        wait_before_pull=asyncio.Event(),
    )

    with pytest.raises(FakeOperationTimeoutError) as raised:
        await asyncio.wait_for(_run(raw, values, chunk_size=2), timeout=1.0)

    assert raised.value is sequence.timeout_error
    _assert_failure_metadata(
        raised.value,
        index=0,
        committed=0,
        partial=False,
    )
    assert values.aclose_calls == 1
    assert "expire" in events
    assert not any(
        isinstance(event, tuple) and event[0] == "abort" for event in events
    )


@pytest.mark.asyncio
async def test_timeout_waiting_for_second_set_uses_python_next_index() -> None:
    events, sequence, raw = _fixture_sequence(remaining=0.01)

    class SecondPullWaitSets(CountingAsyncSets):
        async def __anext__(self) -> object:
            self.pull_calls += 1
            self.pull_started.set()
            if self.pull_calls == 1:
                return self.values.pop(0)
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    values = SecondPullWaitSets([[1]])
    with pytest.raises(FakeOperationTimeoutError) as raised:
        await asyncio.wait_for(_run(raw, values, chunk_size=2), timeout=1.0)

    assert raised.value is sequence.timeout_error
    _assert_failure_metadata(
        raised.value,
        index=1,
        committed=0,
        partial=False,
    )
    assert values.pull_calls == 2
    assert values.aclose_calls == 1
    assert "expire" in events


@pytest.mark.asyncio
async def test_deadline_crossing_sync_pull_uses_typed_timeout() -> None:
    events, sequence, raw = _fixture_sequence()
    primary = ProducerFailure()

    class DeadlineCrossingSets(CountingSyncSets):
        def __next__(self) -> object:
            self.pull_calls += 1
            raise primary

    values = DeadlineCrossingSets([])
    sequence.remaining_provider = (
        lambda: 1.0 if values.pull_calls == 0 else 0.0
    )

    with pytest.raises(FakeOperationTimeoutError) as raised:
        await _run(raw, values, chunk_size=2)

    assert raised.value is sequence.timeout_error
    _assert_failure_metadata(
        raised.value,
        index=0,
        committed=0,
        partial=False,
    )
    assert values.pull_calls == 1
    assert values.close_calls == 1
    assert "expire" in events
    assert not any(
        isinstance(event, tuple) and event[0] == "abort" for event in events
    )


@pytest.mark.asyncio
async def test_async_deadline_wins_if_producer_transforms_cancellation() -> None:
    events, sequence, raw = _fixture_sequence(remaining=0.01)
    primary = ProducerFailure()

    class CancellationTransformingSets(CountingAsyncSets):
        async def __anext__(self) -> object:
            self.pull_calls += 1
            self.pull_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise primary

    values = CancellationTransformingSets([])

    with pytest.raises(FakeOperationTimeoutError) as raised:
        await asyncio.wait_for(_run(raw, values, chunk_size=2), timeout=1.0)

    assert raised.value is sequence.timeout_error
    _assert_failure_metadata(
        raised.value,
        index=0,
        committed=0,
        partial=False,
    )
    assert values.aclose_calls == 1
    assert "expire" in events
    assert not any(
        isinstance(event, tuple) and event[0] == "abort" for event in events
    )


@pytest.mark.asyncio
async def test_repeated_cancellation_cannot_interrupt_failing_producer_close() -> None:
    _coordinator()
    _, _, raw = _fixture_sequence()
    pull_release = asyncio.Event()
    close_started = asyncio.Event()
    close_release = asyncio.Event()
    cleanup = CleanupFailure("producer close failed")

    class SlowFailingCloseSets(CountingAsyncSets):
        async def aclose(self) -> None:
            self.aclose_calls += 1
            close_started.set()
            await close_release.wait()
            raise cleanup

    values = SlowFailingCloseSets([[1]], wait_before_pull=pull_release)

    async def capture_cancellation() -> asyncio.CancelledError:
        try:
            await _run(raw, values, chunk_size=2)
        except asyncio.CancelledError as error:
            return error
        raise AssertionError("execute_many unexpectedly completed")

    task = asyncio.create_task(capture_cancellation())
    await asyncio.wait_for(values.pull_started.wait(), timeout=1.0)
    task.cancel()
    await asyncio.wait_for(close_started.wait(), timeout=1.0)
    task.cancel()
    await asyncio.sleep(0)
    assert task.done() is False

    close_release.set()
    cancellation = await asyncio.wait_for(asyncio.shield(task), timeout=1.0)

    assert cancellation.__cause__ is cleanup
    _assert_failure_metadata(
        cancellation,
        index=0,
        committed=0,
        partial=False,
    )
    assert values.aclose_calls == 1
