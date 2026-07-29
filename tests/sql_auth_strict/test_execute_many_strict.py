from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from contextlib import suppress
from decimal import Decimal
import time
from typing import Any

from fastmssql import (
    CommitOutcomeUnknown,
    Connection,
    ConversionError,
    OperationMetricsConfig,
    OperationTimeoutError,
    Parameter,
    Parameters,
    PoolConfig,
    SqlError,
    SslConfig,
    TimeoutConfig,
    Transaction,
)
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import CleanupRegistry, quote_identifier, scalar
from sql_auth_strict.operation_metrics_assertions import (
    OUTCOME_KEYS,
    operation_delta,
    zero_outcomes,
)
from sql_auth_strict.tcp_fault_proxy import DownstreamGateProxy


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


class ProducerFailure(BaseException):
    pass


class SyncSets:
    def __init__(
        self,
        values: Sequence[object],
        *,
        failure: BaseException | None = None,
        on_exhausted: Callable[[], None] | None = None,
    ) -> None:
        self._values = list(values)
        self._failure = failure
        self._on_exhausted = on_exhausted
        self._index = 0
        self.iter_calls = 0
        self.next_calls = 0
        self.yielded = 0
        self.close_calls = 0

    def __iter__(self) -> SyncSets:
        self.iter_calls += 1
        return self

    def __next__(self) -> object:
        self.next_calls += 1
        if self._index < len(self._values):
            value = self._values[self._index]
            self._index += 1
            self.yielded += 1
            return value
        if self._failure is not None:
            failure, self._failure = self._failure, None
            raise failure
        if self._on_exhausted is not None:
            callback, self._on_exhausted = self._on_exhausted, None
            callback()
        raise StopIteration

    def close(self) -> None:
        self.close_calls += 1


class AsyncSets:
    def __init__(
        self,
        values: Sequence[object],
        *,
        failure: BaseException | None = None,
        wait_at: int | None = None,
    ) -> None:
        self._values = list(values)
        self._failure = failure
        self._wait_at = wait_at
        self._index = 0
        self.aiter_calls = 0
        self.anext_calls = 0
        self.yielded = 0
        self.aclose_calls = 0
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()

    def __aiter__(self) -> AsyncSets:
        self.aiter_calls += 1
        return self

    async def __anext__(self) -> object:
        self.anext_calls += 1
        if self._wait_at == self._index:
            self.waiting.set()
            await self.release.wait()
            self._wait_at = None
        if self._index < len(self._values):
            value = self._values[self._index]
            self._index += 1
            self.yielded += 1
            return value
        if self._failure is not None:
            failure, self._failure = self._failure, None
            raise failure
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.aclose_calls += 1


def _isolated_connection(
    config: SqlAuthConfig,
    *,
    application_name: str,
    operation_timeout_secs: float | None = None,
    metrics: bool = False,
    host: str | None = None,
    port: int | None = None,
) -> Connection:
    return Connection(
        server=host or config.host,
        port=port or config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        application_name=application_name,
        pool_config=PoolConfig(
            max_size=1,
            min_idle=0,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=2,
            test_on_check_out=False,
            retry_connection=False,
        ),
        timeout_config=TimeoutConfig(
            connect_timeout_secs=2.0,
            acquire_timeout_secs=2.0,
            operation_timeout_secs=operation_timeout_secs,
            rollback_timeout_secs=2.0,
        ),
        operation_metrics_config=OperationMetricsConfig(enabled=metrics),
    )


def _execute_many_metric(snapshot: dict[str, Any]) -> dict[str, Any]:
    return snapshot["operations"]["execute_many"]


def _assert_metadata(
    error: BaseException,
    *,
    index: int,
    committed: int,
    partial: bool,
) -> None:
    assert error.parameter_set_index == index
    assert error.confirmed_committed_parameter_sets == committed
    assert error.partial_commit_possible is partial


async def _row_count(connection: Any, table: str) -> int:
    return int(await scalar(connection, f"SELECT COUNT_BIG(*) FROM {table}"))


async def _application_session_count(
    observer: Connection,
    application_name: str,
) -> int:
    return int(
        await scalar(
            observer,
            """
            SELECT COUNT_BIG(*)
            FROM sys.dm_exec_sessions
            WHERE program_name = @P1
            """,
            [application_name],
        )
    )


async def _wait_for_zero_sessions(
    observer: Connection,
    application_name: str,
    *,
    timeout: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await _application_session_count(observer, application_name) == 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("execute-many application sessions did not reach zero")


async def _wait_for_application_lock(
    observer: Connection,
    application_name: str,
    task: asyncio.Task[int],
    *,
    timeout: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if task.done():
            result = await task
            raise AssertionError(
                f"execute_many completed before lock wait with {result} rows"
            )
        waiting = await scalar(
            observer,
            """
            SELECT COUNT_BIG(*)
            FROM sys.dm_exec_requests AS request
            JOIN sys.dm_exec_sessions AS session
              ON session.session_id = request.session_id
            WHERE session.program_name = @P1
              AND request.wait_type LIKE N'LCK_M_%'
            """,
            [application_name],
        )
        if waiting == 1:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("execute_many did not enter the expected lock wait")


async def _wait_for_row_count(
    observer: Connection,
    table: str,
    expected: int,
    *,
    timeout: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await _row_count(observer, table) == expected:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"row count did not reach {expected}")


async def _settle_task(task: asyncio.Task[int] | None) -> None:
    if task is None:
        return
    if task.done():
        if not task.cancelled():
            task.exception()
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def _create_locked_target(
    owner: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup: CleanupRegistry,
) -> tuple[str, str]:
    parent = quote_identifier(unique_sql_name("strict_emany_parent"))
    child = quote_identifier(unique_sql_name("strict_emany_child"))
    constraint = quote_identifier(unique_sql_name("strict_emany_fk"))
    cleanup.add(f"DROP TABLE IF EXISTS {parent}")
    cleanup.add(f"DROP TABLE IF EXISTS {child}")
    await owner.execute(
        f"CREATE TABLE {parent} (id INT PRIMARY KEY, touched INT NOT NULL)"
    )
    await owner.execute(
        f"""
        CREATE TABLE {child} (
            id INT PRIMARY KEY,
            parent_id INT NOT NULL,
            payload NVARCHAR(80) NOT NULL,
            CONSTRAINT {constraint}
              FOREIGN KEY (parent_id) REFERENCES {parent}(id)
        )
        """
    )
    await owner.execute(f"INSERT INTO {parent} VALUES (1, 0)")
    return parent, child


async def _hold_parent_lock(
    transaction_factory: Callable[..., Transaction],
    parent: str,
) -> Transaction:
    blocker = transaction_factory()
    await blocker.begin()
    await blocker.execute(
        f"UPDATE {parent} SET touched = touched + 1 WHERE id = 1"
    )
    return blocker


@case("EMANY-001")
@pytest.mark.asyncio
async def test_execute_many_public_sources_and_raw_list_fast_path(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_emany_surface"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, payload NVARCHAR(40) NOT NULL)"
    )
    sql = f"INSERT INTO {table} VALUES (@P1, @P2)"

    concrete = [[1, "list"]]
    assert await owner_connection.execute_many(sql, concrete) == 1
    assert concrete == [[1, "list"]]

    sync_sets = SyncSets([[2, "sync"]])
    assert await owner_connection.execute_many(sql, sync_sets) == 1
    async_sets = AsyncSets([[3, "async"]])
    assert await owner_connection.execute_many(sql, async_sets) == 1

    with pytest.raises(TypeError):
        await owner_connection._conn.execute_many(sql, SyncSets([[4, "raw"]]))

    transaction = owner_connection.transaction()
    try:
        await transaction.begin()
        assert await transaction.execute_many(sql, [[4, "transaction"]]) == 1
        with pytest.raises(TypeError):
            await transaction.execute_many(sql, [[5, "invalid"]], atomic=True)
        await transaction.rollback()
    finally:
        await transaction.close()

    rows = (
        await owner_connection.query(f"SELECT id, payload FROM {table} ORDER BY id")
    ).rows()
    assert [(row[0], row[1]) for row in rows] == [
        (1, "list"),
        (2, "sync"),
        (3, "async"),
    ]


@case("EMANY-002")
@pytest.mark.asyncio
async def test_execute_many_validation_performs_zero_forbidden_work() -> None:
    connection = Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="execute_many_validation_probe",
        password="not-used",
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=1,
            min_idle=0,
            connection_timeout_secs=1,
            retry_connection=False,
        ),
        operation_metrics_config=OperationMetricsConfig(enabled=True),
    )
    sql = "INSERT INTO dbo.target VALUES (@P1)"

    for invalid_atomic in (0, 1, "true", None, object()):
        producer = SyncSets([[1]])
        with pytest.raises(TypeError, match="atomic"):
            await connection.execute_many(
                sql,
                producer,
                atomic=invalid_atomic,
            )
        assert producer.iter_calls == producer.next_calls == 0

    for invalid_chunk in (True, False, 0, -1, 10_001, 1.5, "1"):
        producer = SyncSets([[1]])
        with pytest.raises((TypeError, ValueError), match="chunk_size"):
            await connection.execute_many(
                sql,
                producer,
                chunk_size=invalid_chunk,
            )
        assert producer.iter_calls == producer.next_calls == 0

    for invalid_source in (
        "not sets",
        b"not sets",
        bytearray(b"not sets"),
        memoryview(b"not sets"),
        {"value": 1},
    ):
        with pytest.raises(TypeError):
            await connection.execute_many(sql, invalid_source)

    for invalid_set in (None, (1,), {"value": 1}, "one", b"one"):
        producer = SyncSets([invalid_set])
        with pytest.raises(TypeError) as raised:
            await connection.execute_many(sql, producer)
        _assert_metadata(
            raised.value,
            index=0,
            committed=0,
            partial=False,
        )
        assert producer.next_calls == 1
        assert producer.close_calls == 1

    named = SyncSets([Parameters(secret_value="NeverExposeThis")])
    with pytest.raises(
        ValueError,
        match="^Named parameters are not supported",
    ) as raised:
        await connection.execute_many(sql, named)
    _assert_metadata(raised.value, index=0, committed=0, partial=False)
    assert "NeverExposeThis" not in str(raised.value)
    assert named.next_calls == named.close_calls == 1

    over_limit = SyncSets([list(range(2_099))])
    with pytest.raises(
        ValueError,
        match="^Too many parameters: 2099 provided",
    ) as raised:
        await connection.execute_many(sql, over_limit)
    _assert_metadata(raised.value, index=0, committed=0, partial=False)
    assert over_limit.next_calls == over_limit.close_calls == 1

    assert await connection.is_connected() is False
    metric = _execute_many_metric(await connection.operation_stats())
    assert metric["started"] == metric["completed"] == 0


@case("EMANY-003")
@pytest.mark.asyncio
async def test_execute_many_empty_sources_are_zero_io_and_transaction_neutral(
    owner_connection: Connection,
) -> None:
    disconnected = Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="execute_many_empty_probe",
        password="not-used",
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=1,
            min_idle=0,
            connection_timeout_secs=1,
            retry_connection=False,
        ),
        operation_metrics_config=OperationMetricsConfig(enabled=True),
    )
    sql = "INSERT INTO dbo.target VALUES (@P1)"

    assert await disconnected.execute_many(sql, []) == 0
    empty_sync = SyncSets([])
    assert await disconnected.execute_many(sql, empty_sync) == 0
    empty_async = AsyncSets([])
    assert await disconnected.execute_many(sql, empty_async) == 0
    assert empty_sync.close_calls == empty_async.aclose_calls == 0
    assert await disconnected.is_connected() is False
    metric = _execute_many_metric(await disconnected.operation_stats())
    assert metric["started"] == metric["completed"] == 0

    transaction = owner_connection.transaction()
    try:
        await transaction.begin()
        assert await transaction.execute_many(sql, AsyncSets([])) == 0
        assert await scalar(transaction, "SELECT @@TRANCOUNT") == 1
        assert await scalar(transaction, "SELECT XACT_STATE()") == 1
        await transaction.rollback()
    finally:
        await transaction.close()


@case("EMANY-004")
@pytest.mark.asyncio
async def test_execute_many_preserves_typed_order_and_tds_backpressure(
    sql_auth_config: SqlAuthConfig,
    owner_connection: Connection,
    sa_connection: Connection,
    transaction_factory: Callable[..., Transaction],
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    typed_table = quote_identifier(unique_sql_name("strict_emany_typed"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {typed_table}")
    await owner_connection.execute(
        f"""
        CREATE TABLE {typed_table} (
            id INT PRIMARY KEY,
            amount DECIMAL(9,2) NOT NULL,
            payload NVARCHAR(40) NOT NULL
        )
        """
    )
    sql = f"INSERT INTO {typed_table} VALUES (@P1, @P2, @P3)"
    assert await owner_connection.execute_many(
        sql,
        [[1, Decimal("1.25"), "list"]],
    ) == 1
    assert await owner_connection.execute_many(
        sql,
        SyncSets(
            [
                Parameters(
                    Parameter(2, "INT"),
                    Parameter(Decimal("2.50"), "DECIMAL(9,2)"),
                    Parameter("sync", "NVARCHAR(40)"),
                )
            ]
        ),
    ) == 1
    assert await owner_connection.execute_many(
        sql,
        AsyncSets([[3, Decimal("3.75"), "async"]]),
    ) == 1
    rows = (
        await owner_connection.query(
            f"SELECT id, amount, payload FROM {typed_table} ORDER BY id"
        )
    ).rows()
    assert [(row[0], row[1], row[2]) for row in rows] == [
        (1, Decimal("1.25"), "list"),
        (2, Decimal("2.50"), "sync"),
        (3, Decimal("3.75"), "async"),
    ]

    parent, child = await _create_locked_target(
        owner_connection,
        unique_sql_name,
        cleanup_registry,
    )
    blocker = await _hold_parent_lock(transaction_factory, parent)
    application_name = unique_sql_name("strict_emany_backpressure")
    candidate = _isolated_connection(
        sql_auth_config,
        application_name=application_name,
    )
    producer = SyncSets(
        [[10, 1, "ten"], [11, 1, "eleven"], [12, 1, "twelve"]]
    )
    task: asyncio.Task[int] | None = None
    try:
        task = asyncio.create_task(
            candidate.execute_many(
                f"INSERT INTO {child} VALUES (@P1, @P2, @P3)",
                producer,
                chunk_size=2,
            )
        )
        await _wait_for_application_lock(
            sa_connection,
            application_name,
            task,
        )
        assert producer.yielded == 2
        await blocker.rollback()
        assert await asyncio.wait_for(task, timeout=3.0) == 3
        assert producer.next_calls == 4
    finally:
        await _settle_task(task)
        with suppress(Exception):
            await blocker.rollback()
        await blocker.close()
        await candidate.disconnect()
        await _wait_for_zero_sessions(sa_connection, application_name)


@case("EMANY-005")
@pytest.mark.asyncio
async def test_execute_many_default_atomic_rolls_back_late_failures(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_emany_atomic"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, payload NVARCHAR(40) NOT NULL)"
    )
    sql = f"INSERT INTO {table} VALUES (@P1, @P2)"

    with pytest.raises(SqlError) as duplicate:
        await owner_connection.execute_many(
            sql,
            [[1, "one"], [2, "two"], [3, "three"], [3, "duplicate"]],
            chunk_size=2,
        )
    _assert_metadata(duplicate.value, index=3, committed=0, partial=False)
    assert await _row_count(owner_connection, table) == 0

    class Unsupported:
        pass

    with pytest.raises((ConversionError, ValueError)) as conversion:
        await owner_connection.execute_many(
            sql,
            [[4, "four"], [5, "five"], [6, Unsupported()], [7, "seven"]],
            chunk_size=2,
        )
    _assert_metadata(conversion.value, index=2, committed=0, partial=False)
    assert await _row_count(owner_connection, table) == 0

    primary = ProducerFailure()
    producer = SyncSets(
        [[8, "eight"], [9, "nine"], [10, "ten"]],
        failure=primary,
    )
    with pytest.raises(ProducerFailure) as production:
        await owner_connection.execute_many(sql, producer, chunk_size=2)
    assert production.value is primary
    _assert_metadata(production.value, index=3, committed=0, partial=False)
    assert producer.close_calls == 1
    assert await _row_count(owner_connection, table) == 0


@case("EMANY-006")
@pytest.mark.asyncio
async def test_execute_many_partial_mode_reports_confirmed_commits(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_emany_partial"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, payload NVARCHAR(40) NOT NULL)"
    )
    sql = f"INSERT INTO {table} VALUES (@P1, @P2)"

    with pytest.raises(SqlError) as duplicate:
        await owner_connection.execute_many(
            sql,
            [[1, "one"], [2, "two"], [3, "three"], [3, "duplicate"]],
            atomic=False,
            chunk_size=2,
        )
    _assert_metadata(duplicate.value, index=3, committed=2, partial=True)
    rows = (
        await owner_connection.query(f"SELECT id FROM {table} ORDER BY id")
    ).rows()
    assert [row[0] for row in rows] == [1, 2]

    await owner_connection.execute(f"TRUNCATE TABLE {table}")
    primary = ProducerFailure()
    producer = SyncSets(
        [[4, "four"], [5, "five"], [6, "six"]],
        failure=primary,
    )
    with pytest.raises(ProducerFailure) as production:
        await owner_connection.execute_many(
            sql,
            producer,
            atomic=False,
            chunk_size=2,
        )
    _assert_metadata(production.value, index=3, committed=2, partial=True)
    rows = (
        await owner_connection.query(f"SELECT id FROM {table} ORDER BY id")
    ).rows()
    assert [row[0] for row in rows] == [4, 5]
    assert await scalar(owner_connection, "SELECT 1") == 1


@case("EMANY-007")
@pytest.mark.asyncio
async def test_execute_many_cancellation_stops_pulls_and_cleans_sessions(
    sql_auth_config: SqlAuthConfig,
    owner_connection: Connection,
    sa_connection: Connection,
    transaction_factory: Callable[..., Transaction],
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_emany_cancel"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")
    producer_app = unique_sql_name("strict_emany_cancel_producer")
    producer_connection = _isolated_connection(
        sql_auth_config,
        application_name=producer_app,
        metrics=True,
    )
    producer = AsyncSets([[1], [2]], wait_at=1)
    producer_task: asyncio.Task[int] | None = None
    try:
        producer_task = asyncio.create_task(
            producer_connection.execute_many(
                f"INSERT INTO {table} VALUES (@P1)",
                producer,
                chunk_size=2,
            )
        )
        await asyncio.wait_for(producer.waiting.wait(), timeout=2.0)
        producer_task.cancel()
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await producer_task
        _assert_metadata(cancelled.value, index=1, committed=0, partial=False)
        assert producer.yielded == 1
        assert producer.aclose_calls == 1
        metric = _execute_many_metric(await producer_connection.operation_stats())
        assert metric["cancelled"] == 1
    finally:
        producer.release.set()
        await _settle_task(producer_task)
        await producer_connection.disconnect()
        await _wait_for_zero_sessions(sa_connection, producer_app)

    parent, child = await _create_locked_target(
        owner_connection,
        unique_sql_name,
        cleanup_registry,
    )
    blocker = await _hold_parent_lock(transaction_factory, parent)
    wire_app = unique_sql_name("strict_emany_cancel_wire")
    wire_connection = _isolated_connection(
        sql_auth_config,
        application_name=wire_app,
        metrics=True,
    )
    wire_producer = SyncSets([[10, 1, "ten"], [11, 1, "eleven"]])
    wire_task: asyncio.Task[int] | None = None
    try:
        wire_task = asyncio.create_task(
            wire_connection.execute_many(
                f"INSERT INTO {child} VALUES (@P1, @P2, @P3)",
                wire_producer,
                chunk_size=2,
            )
        )
        await _wait_for_application_lock(sa_connection, wire_app, wire_task)
        wire_task.cancel()
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await wire_task
        _assert_metadata(cancelled.value, index=0, committed=0, partial=False)
        assert wire_producer.yielded == 2
        metric = _execute_many_metric(await wire_connection.operation_stats())
        assert metric["cancelled"] == 1
    finally:
        await _settle_task(wire_task)
        with suppress(Exception):
            await blocker.rollback()
        await blocker.close()
        await wire_connection.disconnect()
        await _wait_for_zero_sessions(sa_connection, wire_app)
    assert await _row_count(owner_connection, child) == 0


@case("EMANY-008")
@pytest.mark.asyncio
async def test_execute_many_timeout_is_typed_bounded_and_reusable(
    sql_auth_config: SqlAuthConfig,
    owner_connection: Connection,
    sa_connection: Connection,
    transaction_factory: Callable[..., Transaction],
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_emany_timeout"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")
    producer_app = unique_sql_name("strict_emany_timeout_producer")
    producer_connection = _isolated_connection(
        sql_auth_config,
        application_name=producer_app,
        operation_timeout_secs=0.15,
        metrics=True,
    )
    producer = AsyncSets([[1], [2]], wait_at=1)
    started = time.monotonic()
    try:
        with pytest.raises(OperationTimeoutError) as timeout:
            await asyncio.wait_for(
                producer_connection.execute_many(
                    f"INSERT INTO {table} VALUES (@P1)",
                    producer,
                    chunk_size=2,
                ),
                timeout=2.0,
            )
        assert time.monotonic() - started < 1.5
        assert timeout.value.operation == "execute_many"
        _assert_metadata(timeout.value, index=1, committed=0, partial=False)
        assert table not in str(timeout.value)
        assert producer.aclose_calls == 1
        metric = _execute_many_metric(await producer_connection.operation_stats())
        assert metric["timed_out"] == 1
        assert await scalar(producer_connection, "SELECT 1") == 1
    finally:
        producer.release.set()
        await producer_connection.disconnect()
        await _wait_for_zero_sessions(sa_connection, producer_app)

    parent, child = await _create_locked_target(
        owner_connection,
        unique_sql_name,
        cleanup_registry,
    )
    blocker = await _hold_parent_lock(transaction_factory, parent)
    wire_app = unique_sql_name("strict_emany_timeout_wire")
    wire_connection = _isolated_connection(
        sql_auth_config,
        application_name=wire_app,
        operation_timeout_secs=0.20,
        metrics=True,
    )
    try:
        with pytest.raises(OperationTimeoutError) as timeout:
            await asyncio.wait_for(
                wire_connection.execute_many(
                    f"INSERT INTO {child} VALUES (@P1, @P2, @P3)",
                    [[10, 1, "ten"], [11, 1, "eleven"]],
                    chunk_size=2,
                ),
                timeout=2.0,
            )
        assert timeout.value.operation == "execute_many"
        _assert_metadata(timeout.value, index=0, committed=0, partial=False)
        assert child not in str(timeout.value)
        assert await scalar(wire_connection, "SELECT 1") == 1
    finally:
        with suppress(Exception):
            await blocker.rollback()
        await blocker.close()
        await wire_connection.disconnect()
        await _wait_for_zero_sessions(sa_connection, wire_app)
    assert await _row_count(owner_connection, child) == 0


@case("EMANY-009")
@pytest.mark.asyncio
async def test_execute_many_transaction_is_neutral_then_rollback_only(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_emany_transaction"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")
    transaction = owner_connection.transaction()
    try:
        await transaction.begin()
        assert await transaction.execute_many(
            f"INSERT INTO {table} VALUES (@P1)",
            [[1], [2]],
            chunk_size=1,
        ) == 2
        assert await _row_count(transaction, table) == 2
        # READ_COMMITTED_SNAPSHOT is intentionally off in the canonical
        # container, so an ordinary external COUNT correctly waits on these
        # uncommitted inserts. READPAST proves no committed row is visible
        # without turning SQL Server's lock wait into a driver deadlock.
        assert await scalar(
            owner_connection,
            f"SELECT COUNT_BIG(*) FROM {table} WITH (READPAST)",
        ) == 0
        assert await scalar(transaction, "SELECT @@TRANCOUNT") == 1

        with pytest.raises(SqlError) as failure:
            await transaction.execute_many(
                f"INSERT INTO {table} VALUES (@P1)",
                [[3], [3]],
                chunk_size=2,
            )
        _assert_metadata(failure.value, index=1, committed=0, partial=False)
        with pytest.raises(RuntimeError, match="rollback"):
            await transaction.query("SELECT 1")
        with pytest.raises(RuntimeError, match="rollback"):
            await transaction.commit()
        await transaction.rollback()
    finally:
        await transaction.close()
    assert await _row_count(owner_connection, table) == 0


@case("EMANY-010")
@pytest.mark.asyncio
async def test_execute_many_real_dml_procedure_and_schema_two_metrics(
    sql_auth_config: SqlAuthConfig,
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_emany_use_cases"))
    procedure = quote_identifier(unique_sql_name("strict_emany_proc"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    cleanup_registry.add(f"DROP PROCEDURE IF EXISTS {procedure}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value INT NOT NULL)"
    )
    await owner_connection.execute(
        f"""
        CREATE PROCEDURE {procedure}
            @id INT,
            @delta INT
        AS
        BEGIN
            SET NOCOUNT OFF;
            UPDATE {table} SET value = value + @delta WHERE id = @id;
        END
        """
    )
    connection = _isolated_connection(
        sql_auth_config,
        application_name=unique_sql_name("strict_emany_use_cases_app"),
        metrics=True,
    )
    try:
        before = await connection.operation_stats()
        assert await connection.execute_many(
            f"INSERT INTO {table} VALUES (@P1, @P2)",
            [[1, 10], [2, 20]],
        ) == 2
        assert await connection.execute_many(
            f"UPDATE {table} SET value = @P2 WHERE id = @P1",
            [[1, 11], [2, 21]],
        ) == 2
        assert await connection.execute_many(
            f"DELETE FROM {table} WHERE id = @P1",
            [[2], [999]],
        ) == 1
        await owner_connection.execute(f"INSERT INTO {table} VALUES (3, 30)")
        assert await connection.execute_many(
            f"EXEC {procedure} @P1, @P2",
            [[1, 5], [3, 7]],
        ) == 2
        after = await connection.operation_stats()

        delta = operation_delta(before, after, "execute_many")
        assert delta["started"] == delta["completed"] == 4
        assert {key: delta[key] for key in OUTCOME_KEYS} == zero_outcomes(
            succeeded=4
        )
        for operation in ("execute", "begin", "commit", "rollback"):
            assert operation_delta(before, after, operation)["started"] == 0
        assert after["schema_version"] == 2
        rows = (
            await owner_connection.query(
                f"SELECT id, value FROM {table} ORDER BY id"
            )
        ).rows()
        assert [(row[0], row[1]) for row in rows] == [(1, 16), (3, 37)]
    finally:
        await connection.disconnect()


@case("EMANY-011")
@pytest.mark.asyncio
async def test_execute_many_lost_atomic_commit_ack_is_unknown_without_retry(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_emany_commit_unknown"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await sa_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")
    proxy = DownstreamGateProxy(sql_auth_config.host, sql_auth_config.port)
    await proxy.start()
    application_name = unique_sql_name("strict_emany_commit_unknown_app")
    connection = _isolated_connection(
        sql_auth_config,
        application_name=application_name,
        metrics=True,
        host=proxy.host,
        port=proxy.port,
    )
    producer = SyncSets(
        [[1], [2]],
        on_exhausted=proxy.pause_downstream,
    )
    task: asyncio.Task[int] | None = None
    try:
        before = await connection.operation_stats()
        task = asyncio.create_task(
            connection.execute_many(
                f"INSERT INTO {table} VALUES (@P1)",
                producer,
                chunk_size=2,
            )
        )
        await _wait_for_row_count(sa_connection, table, 2)
        await proxy.wait_until_downstream_held()
        await proxy.abort_connections()
        proxy.resume_downstream()

        with pytest.raises(CommitOutcomeUnknown) as captured:
            await asyncio.wait_for(task, timeout=2.0)
        error = captured.value
        assert error.operation == "execute_many"
        assert error.retryable is False
        assert error.connection_discarded is True
        _assert_metadata(error, index=0, committed=0, partial=False)
        assert producer.next_calls == 3
        after = await connection.operation_stats()
        delta = operation_delta(before, after, "execute_many")
        assert delta["started"] == delta["completed"] == 1
        assert {key: delta[key] for key in OUTCOME_KEYS} == zero_outcomes(
            outcome_unknown=1
        )
        for operation in ("execute", "begin", "commit", "rollback"):
            assert operation_delta(before, after, operation)["started"] == 0
        assert await _row_count(sa_connection, table) == 2
        assert proxy.accepted_connections == 1
    finally:
        proxy.resume_downstream()
        await _settle_task(task)
        await connection.disconnect()
        await proxy.close()
        await _wait_for_zero_sessions(sa_connection, application_name)
