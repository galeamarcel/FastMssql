from __future__ import annotations

import asyncio
from dataclasses import dataclass
import gc
import traceback
from types import SimpleNamespace
import weakref

import fastmssql
import pytest


TASK_PREFIX = "fastmssql-query-many-"


class QueryFailure(Exception):
    pass


class ProducerFailure(BaseException):
    pass


class ConversionFailure(Exception):
    pass


class PoolStatsFailure(BaseException):
    pass


class CleanupFailure(Exception):
    pass


class BodyFailure(Exception):
    pass


@dataclass
class QueryGate:
    started: asyncio.Event
    release: asyncio.Event
    finished: asyncio.Event
    cancelled: asyncio.Event
    result: object
    error: BaseException | None = None


def _gate(
    index: int,
    *,
    error: BaseException | None = None,
    released: bool = False,
) -> QueryGate:
    release = asyncio.Event()
    if released:
        release.set()
    return QueryGate(
        started=asyncio.Event(),
        release=release,
        finished=asyncio.Event(),
        cancelled=asyncio.Event(),
        result=f"result-{index}",
        error=error,
    )


def _pool_stats(max_size: object) -> dict[str, object]:
    return {
        "connected": False,
        "connections": 0,
        "idle_connections": 0,
        "active_connections": 0,
        "max_size": max_size,
        "min_idle": 0,
        "get_started": 0,
        "get_direct": 0,
        "get_waited": 0,
        "get_timed_out": 0,
        "pending_gets": 0,
        "get_wait_time_seconds": 0.0,
        "connections_created": 0,
        "connections_closed_broken": 0,
        "connections_closed_invalid": 0,
        "connections_closed_max_lifetime": 0,
        "connections_closed_idle_timeout": 0,
    }


class FakeRawConnection:
    def __init__(
        self,
        *,
        max_size: object,
        gates: dict[int, QueryGate] | None = None,
        stats_error: BaseException | None = None,
        operation_timeout_seconds: float = 30.0,
    ) -> None:
        self.max_size = max_size
        self.gates = gates or {}
        self.stats_error = stats_error
        self.timeout_config = SimpleNamespace(
            operation_timeout_secs=operation_timeout_seconds
        )
        self.pool_stats_calls = 0
        self.query_calls: list[int] = []
        self.query_task_names: list[str] = []
        self.active_queries = 0
        self.maximum_active_queries = 0
        self.all_inactive = asyncio.Event()
        self.all_inactive.set()

    async def pool_stats(self) -> dict[str, object]:
        self.pool_stats_calls += 1
        if self.stats_error is not None:
            raise self.stats_error
        return _pool_stats(self.max_size)

    async def query(self, sql: str, params: object) -> object:
        assert sql == "SELECT @P1"
        index = int(params[0])
        gate = self.gates[index]
        self.query_calls.append(index)
        task = asyncio.current_task()
        assert task is not None
        self.query_task_names.append(task.get_name())
        self.active_queries += 1
        self.maximum_active_queries = max(
            self.maximum_active_queries,
            self.active_queries,
        )
        self.all_inactive.clear()
        gate.started.set()
        try:
            await gate.release.wait()
            if gate.error is not None:
                raise gate.error
            return gate.result
        except asyncio.CancelledError:
            gate.cancelled.set()
            raise
        finally:
            self.active_queries -= 1
            if self.active_queries == 0:
                self.all_inactive.set()
            gate.finished.set()


class SyncSource:
    def __init__(
        self,
        values: list[object],
        *,
        failure: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.values = list(values)
        self.failure = failure
        self.close_error = close_error
        self.index = 0
        self.iter_calls = 0
        self.pull_calls = 0
        self.items_yielded = 0
        self.close_calls = 0
        self.close_finished = asyncio.Event()

    def __iter__(self) -> SyncSource:
        self.iter_calls += 1
        return self

    def __next__(self) -> object:
        self.pull_calls += 1
        if self.index < len(self.values):
            value = self.values[self.index]
            self.index += 1
            self.items_yielded += 1
            return value
        if self.failure is not None:
            failure, self.failure = self.failure, None
            raise failure
        raise StopIteration

    def close(self) -> None:
        self.close_calls += 1
        self.close_finished.set()
        if self.close_error is not None:
            raise self.close_error

    def __repr__(self) -> str:
        return "SOURCE_PRIVACY_SENTINEL"


class AsyncSource:
    def __init__(
        self,
        values: list[object],
        *,
        failure: BaseException | None = None,
        wait_at: int | None = None,
        close_error: BaseException | None = None,
        block_close: bool = False,
    ) -> None:
        self.values = list(values)
        self.failure = failure
        self.wait_at = wait_at
        self.close_error = close_error
        self.index = 0
        self.aiter_calls = 0
        self.anext_calls = 0
        self.items_yielded = 0
        self.aclose_calls = 0
        self.anext_active = False
        self.waiting = asyncio.Event()
        self.pull_release = asyncio.Event()
        self.close_started = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_finished = asyncio.Event()
        if not block_close:
            self.close_release.set()

    def __aiter__(self) -> AsyncSource:
        self.aiter_calls += 1
        return self

    async def __anext__(self) -> object:
        self.anext_calls += 1
        self.anext_active = True
        try:
            if self.wait_at == self.index:
                self.waiting.set()
                await self.pull_release.wait()
                self.wait_at = None
            if self.index < len(self.values):
                value = self.values[self.index]
                self.index += 1
                self.items_yielded += 1
                return value
            if self.failure is not None:
                failure, self.failure = self.failure, None
                raise failure
            raise StopAsyncIteration
        finally:
            self.anext_active = False

    async def aclose(self) -> None:
        self.aclose_calls += 1
        if self.anext_active:
            raise AssertionError("producer closed while anext remained active")
        self.close_started.set()
        try:
            await self.close_release.wait()
            if self.close_error is not None:
                raise self.close_error
        finally:
            self.close_finished.set()


def _connection(raw: FakeRawConnection) -> fastmssql.Connection:
    connection = object.__new__(fastmssql.Connection)
    connection._conn = raw
    return connection


def _named_query_many_tasks() -> list[asyncio.Task[object]]:
    current = asyncio.current_task()
    return sorted(
        [
            task
            for task in asyncio.all_tasks()
            if task is not current
            and not task.done()
            and task.get_name().startswith(TASK_PREFIX)
        ],
        key=lambda task: task.get_name(),
    )


async def _assert_no_named_query_many_tasks() -> None:
    for _ in range(100):
        tasks = _named_query_many_tasks()
        if not tasks:
            return
        await asyncio.sleep(0)
    assert [task.get_name() for task in tasks] == []


async def _wait(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), timeout=2.0)


async def _spin(turns: int = 20) -> None:
    for _ in range(turns):
        await asyncio.sleep(0)


async def _collect(iterator: object) -> list[object]:
    values: list[object] = []
    async for value in iterator:
        values.append(value)
    return values


@pytest.mark.asyncio
async def test_empty_sources_are_terminal_without_query_or_normal_close() -> None:
    for source in (SyncSource([]), AsyncSource([])):
        raw = FakeRawConnection(max_size=3)
        iterator = _connection(raw).query_many(
            "SELECT @P1",
            source,
            concurrency=3,
        )

        assert await _collect(iterator) == []
        with pytest.raises(StopAsyncIteration):
            await iterator.__anext__()
        await iterator.aclose()

        assert raw.pool_stats_calls == 1
        assert raw.query_calls == []
        if isinstance(source, SyncSource):
            assert source.close_calls == 0
        else:
            assert source.aclose_calls == 0
        await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
async def test_full_exhaustion_preserves_results_without_abnormal_close() -> None:
    gates = {index: _gate(index, released=True) for index in range(4)}
    source = SyncSource([[index] for index in range(4)])
    raw = FakeRawConnection(max_size=2, gates=gates)
    iterator = _connection(raw).query_many(
        "SELECT @P1",
        source,
        concurrency=2,
        ordered=True,
    )

    assert await _collect(iterator) == [
        "result-0",
        "result-1",
        "result-2",
        "result-3",
    ]

    assert source.pull_calls == 5
    assert source.close_calls == 0
    assert sorted(raw.query_calls) == [0, 1, 2, 3]
    assert raw.maximum_active_queries <= 2
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
async def test_fixed_workers_capacity_and_slow_consumer_share_one_window() -> None:
    gates = {index: _gate(index) for index in range(6)}
    source = SyncSource([[index] for index in range(6)])
    raw = FakeRawConnection(max_size=3, gates=gates)
    iterator = _connection(raw).query_many(
        "SELECT @P1",
        source,
        concurrency=5,
        ordered=True,
    )
    first_result = asyncio.create_task(
        iterator.__anext__(),
        name="query-many-test-consumer",
    )

    for index in range(3):
        await _wait(gates[index].started)

    worker_tasks = [
        task
        for task in _named_query_many_tasks()
        if task.get_name().startswith("fastmssql-query-many-worker-")
    ]
    worker_names = [task.get_name() for task in worker_tasks]
    worker_ids = {id(task) for task in worker_tasks}
    assert worker_names == [
        "fastmssql-query-many-worker-0",
        "fastmssql-query-many-worker-1",
        "fastmssql-query-many-worker-2",
    ]
    assert sorted(raw.query_task_names) == worker_names
    assert source.items_yielded == 3
    assert raw.maximum_active_queries == 3

    gates[0].release.set()
    assert await first_result == "result-0"
    await _wait(gates[3].started)
    assert {
        id(task)
        for task in _named_query_many_tasks()
        if task.get_name().startswith("fastmssql-query-many-worker-")
    } == worker_ids

    for index in (1, 2, 3):
        gates[index].release.set()
        await _wait(gates[index].finished)
    await _spin()

    assert source.items_yielded == 4
    assert source.pull_calls == 4
    assert not gates[4].started.is_set()
    assert not gates[5].started.is_set()
    assert source.items_yielded - 1 == 3
    assert raw.maximum_active_queries == 3

    await iterator.aclose()

    assert source.close_calls == 1
    assert raw.active_queries == 0
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
async def test_requested_concurrency_limits_workers_below_pool_max() -> None:
    gates = {index: _gate(index) for index in range(3)}
    source = SyncSource([[0], [1], [2]])
    raw = FakeRawConnection(max_size=5, gates=gates)
    iterator = _connection(raw).query_many(
        "SELECT @P1",
        source,
        concurrency=2,
    )
    next_result = asyncio.create_task(iterator.__anext__())

    await _wait(gates[0].started)
    await _wait(gates[1].started)
    await _spin()

    assert not gates[2].started.is_set()
    assert source.items_yielded == 2
    assert raw.maximum_active_queries == 2
    assert [
        task.get_name()
        for task in _named_query_many_tasks()
        if task.get_name().startswith("fastmssql-query-many-worker-")
    ] == [
        "fastmssql-query-many-worker-0",
        "fastmssql-query-many-worker-1",
    ]

    await iterator.aclose()
    with pytest.raises(StopAsyncIteration):
        await next_result
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("ordered", (True, False))
async def test_ordered_and_completion_order_delivery_are_exact(
    ordered: bool,
) -> None:
    gates = {index: _gate(index) for index in range(3)}
    source = SyncSource([[0], [1], [2]])
    raw = FakeRawConnection(max_size=3, gates=gates)
    iterator = _connection(raw).query_many(
        "SELECT @P1",
        source,
        concurrency=3,
        ordered=ordered,
    )
    first_result = asyncio.create_task(iterator.__anext__())

    for index in range(3):
        await _wait(gates[index].started)

    gates[2].release.set()
    await _wait(gates[2].finished)
    await _spin()

    if ordered:
        assert not first_result.done()
        gates[0].release.set()
        assert await first_result == "result-0"
        second_result = asyncio.create_task(iterator.__anext__())
        gates[1].release.set()
        assert await second_result == "result-1"
        assert await iterator.__anext__() == "result-2"
    else:
        assert await first_result == "result-2"
        gates[0].release.set()
        assert await iterator.__anext__() == "result-0"
        gates[1].release.set()
        assert await iterator.__anext__() == "result-1"

    with pytest.raises(StopAsyncIteration):
        await iterator.__anext__()
    assert source.close_calls == 0
    assert sorted(raw.query_calls) == [0, 1, 2]
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
async def test_first_child_failure_cancels_siblings_and_preserves_traceback() -> None:
    failure = QueryFailure("query failed without private values")
    gates = {
        0: _gate(0),
        1: _gate(1),
        2: _gate(2, error=failure),
        3: _gate(3),
    }
    source = SyncSource([[0], [1], [2], [3]])
    raw = FakeRawConnection(max_size=3, gates=gates)
    iterator = _connection(raw).query_many(
        "SELECT @P1",
        source,
        concurrency=3,
    )
    next_result = asyncio.create_task(iterator.__anext__())

    for index in range(3):
        await _wait(gates[index].started)
    gates[2].release.set()

    with pytest.raises(QueryFailure) as caught:
        await next_result

    assert caught.value is failure
    assert caught.value.query_index == 2
    assert "query" in {
        frame.name for frame in traceback.extract_tb(caught.value.__traceback__)
    }
    assert source.close_calls == 1
    assert raw.active_queries == 0
    assert gates[0].cancelled.is_set()
    assert gates[1].cancelled.is_set()
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
async def test_producer_failure_uses_the_next_assignable_index() -> None:
    failure = ProducerFailure("producer failed without private values")
    source = SyncSource([[0]], failure=failure)
    raw = FakeRawConnection(max_size=2, gates={0: _gate(0)})
    iterator = _connection(raw).query_many(
        "SELECT @P1",
        source,
        concurrency=2,
    )

    with pytest.raises(ProducerFailure) as caught:
        await iterator.__anext__()

    assert caught.value is failure
    assert caught.value.query_index == 1
    assert "__next__" in {
        frame.name for frame in traceback.extract_tb(caught.value.__traceback__)
    }
    assert source.close_calls == 1
    assert raw.active_queries == 0
    assert raw.query_calls in ([], [0])
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
async def test_local_parameter_set_failure_is_indexed_and_privacy_safe() -> None:
    source = SyncSource([[0], ("PARAMETER_PRIVACY_SENTINEL",)])
    raw = FakeRawConnection(max_size=2, gates={0: _gate(0)})
    iterator = _connection(raw).query_many(
        "SELECT @P1",
        source,
        concurrency=2,
    )

    with pytest.raises(TypeError) as caught:
        await iterator.__anext__()

    assert caught.value.query_index == 1
    assert "PARAMETER_PRIVACY_SENTINEL" not in str(caught.value)
    assert "SOURCE_PRIVACY_SENTINEL" not in str(caught.value)
    assert source.close_calls == 1
    assert raw.active_queries == 0
    assert raw.query_calls in ([], [0])
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
async def test_conversion_failure_keeps_parameter_and_query_indices() -> None:
    failure = ConversionFailure("conversion failed")
    failure.parameter_index = 7
    gates = {
        0: _gate(0),
        1: _gate(1, error=failure),
    }
    source = SyncSource([[0], [1]])
    raw = FakeRawConnection(max_size=2, gates=gates)
    iterator = _connection(raw).query_many(
        "SELECT @P1",
        source,
        concurrency=2,
    )
    next_result = asyncio.create_task(iterator.__anext__())

    await _wait(gates[0].started)
    await _wait(gates[1].started)
    gates[1].release.set()

    with pytest.raises(ConversionFailure) as caught:
        await next_result

    assert caught.value is failure
    assert caught.value.parameter_index == 7
    assert caught.value.query_index == 1
    assert gates[0].cancelled.is_set()
    assert source.close_calls == 1
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
async def test_pool_stats_failure_closes_before_pull_without_query_index() -> None:
    failure = PoolStatsFailure("pool statistics sentinel")
    source = SyncSource([[0]])
    raw = FakeRawConnection(max_size=2, stats_error=failure)
    iterator = _connection(raw).query_many(
        "SELECT @P1",
        source,
        concurrency=2,
    )

    with pytest.raises(PoolStatsFailure) as caught:
        await iterator.__anext__()

    assert caught.value is failure
    assert not hasattr(caught.value, "query_index")
    assert "pool_stats" in {
        frame.name for frame in traceback.extract_tb(caught.value.__traceback__)
    }
    assert source.pull_calls == 0
    assert source.close_calls == 1
    assert raw.query_calls == []
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("max_size", "error_type"),
    (
        (True, TypeError),
        (None, TypeError),
        (1.5, TypeError),
        ("2", TypeError),
        (0, ValueError),
        (-1, ValueError),
    ),
)
async def test_invalid_pool_max_fails_before_pull_and_closes(
    max_size: object,
    error_type: type[Exception],
) -> None:
    source = SyncSource([[0]])
    raw = FakeRawConnection(max_size=max_size)
    iterator = _connection(raw).query_many(
        "SELECT @P1",
        source,
        concurrency=2,
    )

    with pytest.raises(error_type, match="max_size") as caught:
        await iterator.__anext__()

    assert not hasattr(caught.value, "query_index")
    assert source.pull_calls == 0
    assert source.close_calls == 1
    assert raw.query_calls == []
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
async def test_primary_failure_chains_abnormal_producer_close_failure() -> None:
    primary = QueryFailure("primary query failure")
    cleanup = CleanupFailure("producer close failure")
    source = SyncSource([[0]], close_error=cleanup)
    raw = FakeRawConnection(
        max_size=1,
        gates={0: _gate(0, error=primary)},
    )
    iterator = _connection(raw).query_many("SELECT @P1", source)
    next_result = asyncio.create_task(iterator.__anext__())
    await _wait(raw.gates[0].started)
    raw.gates[0].release.set()

    with pytest.raises(QueryFailure) as caught:
        await next_result

    assert caught.value is primary
    assert caught.value.__cause__ is cleanup
    assert source.close_calls == 1
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
async def test_explicit_close_raises_cleanup_failure_without_a_primary() -> None:
    cleanup = CleanupFailure("explicit close failure")
    source = SyncSource([], close_error=cleanup)
    raw = FakeRawConnection(max_size=1)
    iterator = _connection(raw).query_many("SELECT @P1", source)

    with pytest.raises(CleanupFailure) as caught:
        await iterator.aclose()

    assert caught.value is cleanup
    assert not hasattr(caught.value, "query_index")
    assert source.close_calls == 1
    assert raw.pool_stats_calls == 0
    assert raw.query_calls == []
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
async def test_context_body_failure_stays_primary_when_close_fails() -> None:
    body = BodyFailure("body failure")
    cleanup = CleanupFailure("context close failure")
    source = AsyncSource([], close_error=cleanup)
    raw = FakeRawConnection(max_size=1)
    iterator = _connection(raw).query_many("SELECT @P1", source)

    with pytest.raises(BodyFailure) as caught:
        async with iterator:
            raise body

    assert caught.value is body
    assert caught.value.__cause__ is cleanup
    assert not hasattr(caught.value, "query_index")
    assert not hasattr(cleanup, "query_index")
    assert source.aclose_calls == 1
    assert raw.pool_stats_calls == 0
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
async def test_aclose_before_start_is_idempotent_and_terminal() -> None:
    source = SyncSource([[0]])
    raw = FakeRawConnection(max_size=1, gates={0: _gate(0)})
    iterator = _connection(raw).query_many("SELECT @P1", source)

    await iterator.aclose()
    await iterator.aclose()
    with pytest.raises(StopAsyncIteration):
        await iterator.__anext__()

    assert source.close_calls == 1
    assert source.pull_calls == 0
    assert raw.pool_stats_calls == 0
    assert raw.query_calls == []
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
async def test_aclose_after_start_settles_pending_consumer_and_queries() -> None:
    gates = {index: _gate(index) for index in range(2)}
    source = SyncSource([[0], [1]])
    raw = FakeRawConnection(max_size=2, gates=gates)
    iterator = _connection(raw).query_many(
        "SELECT @P1",
        source,
        concurrency=2,
    )
    next_result = asyncio.create_task(iterator.__anext__())
    await _wait(gates[0].started)
    await _wait(gates[1].started)

    await iterator.aclose()

    with pytest.raises(StopAsyncIteration):
        await next_result
    assert source.close_calls == 1
    assert raw.active_queries == 0
    assert gates[0].cancelled.is_set()
    assert gates[1].cancelled.is_set()
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
async def test_async_context_early_break_settles_all_workers() -> None:
    gates = {index: _gate(index) for index in range(4)}
    source = SyncSource([[0], [1], [2], [3]])
    raw = FakeRawConnection(max_size=3, gates=gates)
    iterator = _connection(raw).query_many(
        "SELECT @P1",
        source,
        concurrency=3,
    )

    async def release_first() -> None:
        for index in range(3):
            await _wait(gates[index].started)
        gates[0].release.set()

    releaser = asyncio.create_task(release_first())
    async with iterator as results:
        async for result in results:
            assert result == "result-0"
            break
    await releaser

    assert source.close_calls == 1
    assert raw.active_queries == 0
    assert gates[1].cancelled.is_set()
    assert gates[2].cancelled.is_set()
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
async def test_consumer_cancellation_is_unindexed_and_awaits_cleanup() -> None:
    gates = {index: _gate(index) for index in range(2)}
    source = SyncSource([[0], [1]])
    raw = FakeRawConnection(max_size=2, gates=gates)
    iterator = _connection(raw).query_many(
        "SELECT @P1",
        source,
        concurrency=2,
    )
    next_result = asyncio.create_task(iterator.__anext__())
    await _wait(gates[0].started)
    await _wait(gates[1].started)

    next_result.cancel("consumer cancellation sentinel")
    with pytest.raises(asyncio.CancelledError) as caught:
        await next_result

    assert not hasattr(caught.value, "query_index")
    assert source.close_calls == 1
    assert raw.active_queries == 0
    assert gates[0].cancelled.is_set()
    assert gates[1].cancelled.is_set()
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
async def test_repeated_cancellation_cannot_interrupt_quiescent_close() -> None:
    source = AsyncSource(
        [[0]],
        wait_at=0,
        block_close=True,
    )
    raw = FakeRawConnection(max_size=1, gates={0: _gate(0)})
    iterator = _connection(raw).query_many("SELECT @P1", source)
    next_result = asyncio.create_task(iterator.__anext__())
    await _wait(source.waiting)

    next_result.cancel("first cancellation")
    await _wait(source.close_started)
    assert not source.anext_active
    next_result.cancel("repeated cancellation")
    source.close_release.set()

    with pytest.raises(asyncio.CancelledError) as caught:
        await next_result

    assert not hasattr(caught.value, "query_index")
    assert source.aclose_calls == 1
    assert source.close_finished.is_set()
    assert raw.query_calls == []
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
async def test_concurrent_anext_is_rejected_without_dividing_the_iterator() -> None:
    gate = _gate(0)
    source = SyncSource([[0]])
    raw = FakeRawConnection(max_size=1, gates={0: gate})
    iterator = _connection(raw).query_many("SELECT @P1", source)
    first = asyncio.create_task(iterator.__anext__())
    await _wait(gate.started)

    with pytest.raises(RuntimeError, match="concurrent") as caught:
        await iterator.__anext__()

    assert not hasattr(caught.value, "query_index")
    await iterator.aclose()
    with pytest.raises(StopAsyncIteration):
        await first
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
async def test_second_event_loop_use_is_rejected() -> None:
    gate = _gate(0)
    source = SyncSource([[0]])
    raw = FakeRawConnection(max_size=1, gates={0: gate})
    iterator = _connection(raw).query_many("SELECT @P1", source)
    first = asyncio.create_task(iterator.__anext__())
    await _wait(gate.started)

    def use_from_new_loop() -> BaseException | None:
        try:
            asyncio.run(iterator.__anext__())
        except BaseException as error:
            return error
        return None

    error = await asyncio.to_thread(use_from_new_loop)

    assert isinstance(error, RuntimeError)
    assert "event loop" in str(error)
    assert not hasattr(error, "query_index")
    await iterator.aclose()
    with pytest.raises(StopAsyncIteration):
        await first
    await _assert_no_named_query_many_tasks()


@pytest.mark.asyncio
async def test_dropped_active_iterator_schedules_best_effort_cleanup() -> None:
    loop = asyncio.get_running_loop()
    unhandled: list[dict[str, object]] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        gates = {index: _gate(index) for index in range(4)}
        source = SyncSource([[0], [1], [2], [3]])
        raw = FakeRawConnection(max_size=3, gates=gates)
        iterator = _connection(raw).query_many(
            "SELECT @P1",
            source,
            concurrency=3,
        )
        first = asyncio.create_task(iterator.__anext__())
        for index in range(3):
            await _wait(gates[index].started)
        gates[0].release.set()
        assert await first == "result-0"
        del first

        reference = weakref.ref(iterator)
        del iterator
        gc.collect()

        await _wait(source.close_finished)
        await _wait(raw.all_inactive)
        await _assert_no_named_query_many_tasks()
        await _spin()

        assert reference() is None
        assert source.close_calls == 1
        assert not [
            context
            for context in unhandled
            if "Task exception was never retrieved"
            in str(context.get("message", ""))
        ]
    finally:
        loop.set_exception_handler(previous_handler)


@pytest.mark.asyncio
@pytest.mark.timeout(2)
async def test_caller_timeout_not_raw_operation_timeout_bounds_producer_wait() -> None:
    source = AsyncSource([[0]], wait_at=0)
    raw = FakeRawConnection(
        max_size=1,
        gates={0: _gate(0)},
        operation_timeout_seconds=0.001,
    )
    iterator = _connection(raw).query_many("SELECT @P1", source)
    loop = asyncio.get_running_loop()
    started = loop.time()

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            async with iterator:
                await iterator.__anext__()

    elapsed = loop.time() - started
    assert elapsed >= 0.03
    assert source.aclose_calls == 1
    assert not source.anext_active
    assert raw.query_calls == []
    await _assert_no_named_query_many_tasks()
