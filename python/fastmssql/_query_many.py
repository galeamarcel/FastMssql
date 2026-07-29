"""Pool-bounded coordination for repeated independent queries."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ._bounded_sequence import (
    _acquire_producer,
    _await_cleanup,
    _close_producer,
)
from ._parameter_sets import (
    INVALID_PRODUCER_TYPES,
    preflight_parameter_set,
)


MAX_QUERY_MANY_CONCURRENCY = 10_000
PRODUCER_ERROR = (
    "parameter_sets must be an iterable or async iterable of lists or "
    "Parameters objects"
)
_END = object()
_WORKER_DONE = object()
_FAILURE = object()
_CLOSED = object()
_MISSING = object()


class _CapturedListProducer:
    """Retain one concrete list and detect resizing at pull boundaries."""

    __slots__ = ("source", "captured_length", "index")

    def __init__(self, source: list[object]) -> None:
        self.source = source
        self.captured_length = list.__len__(source)
        self.index = 0

    def pull(self) -> object:
        if list.__len__(self.source) != self.captured_length:
            raise ValueError(
                "parameter_sets concrete list changed size during iteration"
            )
        if self.index == self.captured_length:
            return _END
        item = list.__getitem__(self.source, self.index)
        self.index += 1
        return item


class _CapacityToken:
    __slots__ = ("_semaphore", "_released")

    def __init__(self, semaphore: asyncio.Semaphore) -> None:
        self._semaphore = semaphore
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._semaphore.release()


@dataclass(slots=True)
class _WorkItem:
    index: int
    parameters: object
    capacity: _CapacityToken


@dataclass(slots=True)
class _CompletedQuery:
    index: int
    result: object
    capacity: _CapacityToken


class _QueryManyState:
    """Private mutable state retained by background coordination tasks."""

    def __init__(
        self,
        raw_connection: object,
        sql: str,
        producer: object,
        *,
        asynchronous: bool,
        concurrency: int,
        ordered: bool,
    ) -> None:
        self._raw_connection = raw_connection
        self._sql = sql
        self._producer = producer
        self._producer_is_async = asynchronous
        self._requested_concurrency = concurrency
        self._ordered = ordered
        self._loop: asyncio.AbstractEventLoop | None = None
        self._start_lock: asyncio.Lock | None = None
        self._started = False
        self._closing = False
        self._closed = False
        self._next_active = False
        self._effective_concurrency = 0
        self._capacity: asyncio.Semaphore | None = None
        self._work_queue: asyncio.Queue[object] | None = None
        self._completed_queue: asyncio.Queue[object] | None = None
        self._producer_task: asyncio.Task[None] | None = None
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._cleanup_task: asyncio.Task[None] | None = None
        self._producer_closed = False
        self._workers_finished = 0
        self._pending_ordered: dict[int, _CompletedQuery] = {}
        self._next_ordered_index = 0
        self._primary_failure: BaseException | None = None
        self._failure_raised = False
        self._failure_origin_task: asyncio.Task[object] | None = None

    def _bind_loop(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
            self._start_lock = asyncio.Lock()
        elif self._loop is not loop:
            raise RuntimeError(
                "query_many iterator cannot be used from a different event loop"
            )
        return loop

    async def ensure_started(self) -> None:
        loop = self._bind_loop()
        if self._started or self._closed:
            return
        start_lock = self._start_lock
        assert start_lock is not None
        async with start_lock:
            if self._started or self._closed:
                return
            stats = await self._raw_connection.pool_stats()
            max_size = stats.get("max_size")
            if isinstance(max_size, bool) or not isinstance(max_size, int):
                raise TypeError("pool max_size must be a positive integer")
            if max_size <= 0:
                raise ValueError("pool max_size must be greater than zero")

            effective = min(self._requested_concurrency, max_size)
            self._effective_concurrency = effective
            self._capacity = asyncio.Semaphore(effective)
            self._work_queue = asyncio.Queue(maxsize=effective)
            self._completed_queue = asyncio.Queue(maxsize=effective)
            self._started = True
            self._producer_task = loop.create_task(
                self._produce(),
                name="fastmssql-query-many-producer",
            )
            self._worker_tasks = [
                loop.create_task(
                    self._worker(worker_index),
                    name=f"fastmssql-query-many-worker-{worker_index}",
                )
                for worker_index in range(effective)
            ]

    async def _pull_one(self) -> object:
        if isinstance(self._producer, _CapturedListProducer):
            item = self._producer.pull()
            if item is _END:
                raise StopAsyncIteration
            return item
        if self._producer_is_async:
            return await anext(self._producer)
        try:
            return next(self._producer)
        except StopIteration:
            raise StopAsyncIteration from None

    def _register_failure(
        self,
        query_index: int,
        error: BaseException,
    ) -> bool:
        if self._primary_failure is not None or self._closing:
            return False
        try:
            error.query_index = query_index
        except BaseException:
            pass
        self._primary_failure = error
        self._closing = True
        self._failure_origin_task = asyncio.current_task()
        self._cancel_background_tasks(exclude=self._failure_origin_task)
        completed_queue = self._completed_queue
        if completed_queue is not None:
            try:
                completed_queue.put_nowait(_FAILURE)
            except asyncio.QueueFull:
                pass
        self._ensure_cleanup_task()
        return True

    def _cancel_background_tasks(
        self,
        *,
        exclude: asyncio.Task[object] | None = None,
    ) -> None:
        for task in [self._producer_task, *self._worker_tasks]:
            if task is not None and task is not exclude and not task.done():
                task.cancel()

    async def _produce(self) -> None:
        capacity = self._capacity
        work_queue = self._work_queue
        assert capacity is not None
        assert work_queue is not None
        query_index = 0
        while not self._closing:
            token: _CapacityToken | None = None
            try:
                await capacity.acquire()
                token = _CapacityToken(capacity)
                parameters = await self._pull_one()
                preflight_parameter_set(
                    parameters,
                    operation="query_many",
                )
                await work_queue.put(
                    _WorkItem(query_index, parameters, token)
                )
                token = None
                query_index += 1
            except StopAsyncIteration:
                if token is not None:
                    token.release()
                for _ in range(self._effective_concurrency):
                    await work_queue.put(_END)
                return
            except asyncio.CancelledError:
                if token is not None:
                    token.release()
                return
            except BaseException as error:
                if token is not None:
                    token.release()
                self._register_failure(query_index, error)
                return

    async def _worker(self, worker_index: int) -> None:
        del worker_index
        work_queue = self._work_queue
        completed_queue = self._completed_queue
        assert work_queue is not None
        assert completed_queue is not None
        while not self._closing:
            item = await work_queue.get()
            if item is _END:
                await completed_queue.put(_WORKER_DONE)
                return
            assert isinstance(item, _WorkItem)
            transferred = False
            try:
                preflight_parameter_set(
                    item.parameters,
                    operation="query_many",
                )
                result = await self._raw_connection.query(
                    self._sql,
                    item.parameters,
                )
                await completed_queue.put(
                    _CompletedQuery(
                        item.index,
                        result,
                        item.capacity,
                    )
                )
                transferred = True
            except asyncio.CancelledError:
                return
            except BaseException as error:
                self._register_failure(item.index, error)
                return
            finally:
                if not transferred:
                    item.capacity.release()

    def _take_ordered(self) -> object:
        completed = self._pending_ordered.pop(
            self._next_ordered_index,
            None,
        )
        if completed is None:
            return _MISSING
        self._next_ordered_index += 1
        completed.capacity.release()
        return completed.result

    async def _finish_normally(self) -> None:
        tasks = [
            task
            for task in [self._producer_task, *self._worker_tasks]
            if task is not None
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._closed = True

    def _release_retained_items(self) -> None:
        work_queue = self._work_queue
        if work_queue is not None:
            while True:
                try:
                    item = work_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if isinstance(item, _WorkItem):
                    item.capacity.release()

        completed_queue = self._completed_queue
        if completed_queue is not None:
            while True:
                try:
                    completed = completed_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if isinstance(completed, _CompletedQuery):
                    completed.capacity.release()

        for completed in self._pending_ordered.values():
            completed.capacity.release()
        self._pending_ordered.clear()

    async def _cleanup(self) -> None:
        self._closing = True
        cleanup_error: BaseException | None = None
        self._cancel_background_tasks(exclude=self._failure_origin_task)
        tasks: list[asyncio.Task[None]] = [
            task
            for task in [self._producer_task, *self._worker_tasks]
            if task is not None
        ]
        if tasks:
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            for outcome in outcomes:
                if (
                    isinstance(outcome, BaseException)
                    and not isinstance(outcome, asyncio.CancelledError)
                    and outcome is not self._primary_failure
                    and cleanup_error is None
                ):
                    cleanup_error = outcome

        if not self._producer_closed:
            self._producer_closed = True
            try:
                await _close_producer(self._producer)
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error

        self._release_retained_items()
        self._closed = True
        completed_queue = self._completed_queue
        if completed_queue is not None:
            try:
                completed_queue.put_nowait(_CLOSED)
            except asyncio.QueueFull:
                pass

        if cleanup_error is not None:
            raise cleanup_error

    async def _supervise_cleanup(self) -> None:
        await _await_cleanup(
            self._cleanup(),
            name="query-many-terminal-cleanup-body",
        )

    @staticmethod
    def _observe_cleanup_task(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        task.exception()

    def _ensure_cleanup_task(self) -> asyncio.Task[None]:
        task = self._cleanup_task
        if task is None:
            loop = self._bind_loop()
            self._closing = True
            self._cancel_background_tasks(
                exclude=self._failure_origin_task,
            )
            task = loop.create_task(
                self._supervise_cleanup(),
                name="fastmssql-query-many-cleanup",
            )
            task.add_done_callback(self._observe_cleanup_task)
            self._cleanup_task = task
        return task

    async def _settle_cleanup(self) -> BaseException | None:
        task = self._ensure_cleanup_task()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        try:
            task.result()
        except BaseException as error:
            return error
        return None

    @staticmethod
    def _chain_cleanup_error(
        primary: BaseException,
        cleanup_error: BaseException | None,
    ) -> None:
        if cleanup_error is not None and primary.__cause__ is None:
            primary.__cause__ = cleanup_error

    async def _raise_primary_failure(self) -> None:
        error = self._primary_failure
        assert error is not None
        cleanup_error = await self._settle_cleanup()
        self._chain_cleanup_error(error, cleanup_error)
        self._failure_raised = True
        raise error

    async def next_result(self) -> object:
        self._bind_loop()
        if self._next_active:
            raise RuntimeError("concurrent query_many iteration is not allowed")
        if self._closed:
            if (
                self._primary_failure is not None
                and not self._failure_raised
            ):
                await self._raise_primary_failure()
            raise StopAsyncIteration

        self._next_active = True
        try:
            try:
                await self.ensure_started()
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                cleanup_error = await self._settle_cleanup()
                self._chain_cleanup_error(error, cleanup_error)
                raise

            completed_queue = self._completed_queue
            assert completed_queue is not None
            while True:
                if self._primary_failure is not None:
                    await self._raise_primary_failure()

                if self._ordered:
                    ordered_result = self._take_ordered()
                    if ordered_result is not _MISSING:
                        return ordered_result

                if (
                    self._workers_finished
                    == self._effective_concurrency
                ):
                    await self._finish_normally()
                    raise StopAsyncIteration

                completed = await completed_queue.get()
                if self._primary_failure is not None:
                    if isinstance(completed, _CompletedQuery):
                        completed.capacity.release()
                    continue
                if completed is _FAILURE:
                    continue
                if completed is _CLOSED:
                    raise StopAsyncIteration
                if completed is _WORKER_DONE:
                    self._workers_finished += 1
                    continue

                assert isinstance(completed, _CompletedQuery)
                if self._ordered:
                    self._pending_ordered[completed.index] = completed
                    continue
                completed.capacity.release()
                return completed.result
        except asyncio.CancelledError as cancelled:
            cleanup_error = await self._settle_cleanup()
            self._chain_cleanup_error(cancelled, cleanup_error)
            raise
        finally:
            self._next_active = False

    async def aclose(self) -> None:
        self._bind_loop()
        if self._closed:
            return
        cleanup_error = await self._settle_cleanup()
        if cleanup_error is not None:
            raise cleanup_error

    async def enter(self) -> None:
        self._bind_loop()

    async def exit(
        self,
        exc_type: object,
        exc: BaseException | None,
        traceback: object,
    ) -> bool:
        del exc_type, traceback
        if exc is None:
            await self.aclose()
        else:
            cleanup_error = await self._settle_cleanup()
            self._chain_cleanup_error(exc, cleanup_error)
        return False

    def request_drop_cleanup(self) -> None:
        loop = self._loop
        if (
            not self._started
            or self._closed
            or loop is None
            or loop.is_closed()
        ):
            return

        def schedule_cleanup() -> None:
            if not self._closed:
                self._ensure_cleanup_task()

        try:
            loop.call_soon_threadsafe(schedule_cleanup)
        except RuntimeError:
            return


class QueryManyIterator:
    """Async iterator facade for one bounded repeated-query operation."""

    def __init__(self, state: _QueryManyState) -> None:
        self._state = state

    def __aiter__(self) -> QueryManyIterator:
        return self

    async def __anext__(self) -> object:
        return await self._state.next_result()

    async def aclose(self) -> None:
        await self._state.aclose()

    async def __aenter__(self) -> QueryManyIterator:
        await self._state.enter()
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: BaseException | None,
        traceback: object,
    ) -> bool:
        return await self._state.exit(exc_type, exc, traceback)

    def __del__(self) -> None:
        try:
            self._state.request_drop_cleanup()
        except BaseException:
            pass


def create_query_many_iterator(
    raw_connection: object,
    sql: object,
    parameter_sets: object,
    *,
    concurrency: object,
    ordered: object,
) -> QueryManyIterator:
    """Validate synchronously and capture exactly one producer protocol."""
    if not isinstance(sql, str):
        raise TypeError("sql must be a string")
    if isinstance(concurrency, bool) or not isinstance(concurrency, int):
        raise TypeError("concurrency must be an integer between 1 and 10000")
    if not 1 <= concurrency <= MAX_QUERY_MANY_CONCURRENCY:
        raise ValueError("concurrency must be between 1 and 10,000")
    if type(ordered) is not bool:
        raise TypeError("ordered must be exactly bool")
    if isinstance(parameter_sets, INVALID_PRODUCER_TYPES):
        raise TypeError(PRODUCER_ERROR)

    if isinstance(parameter_sets, list):
        asynchronous = False
        producer: object = _CapturedListProducer(parameter_sets)
    else:
        asynchronous, producer = _acquire_producer(
            parameter_sets,
            error_message=PRODUCER_ERROR,
        )

    state = _QueryManyState(
        raw_connection,
        sql,
        producer,
        asynchronous=asynchronous,
        concurrency=concurrency,
        ordered=ordered,
    )
    return QueryManyIterator(state)
