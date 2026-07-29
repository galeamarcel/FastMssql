from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import time
from typing import Any

from fastmssql import (
    Connection,
    ConversionError,
    OperationMetricsConfig,
    OperationTimeoutError,
    PoolConfig,
    SslConfig,
    TimeoutConfig,
    Transaction,
)
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import CleanupRegistry, quote_identifier, scalar


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


class ProducerFailure(BaseException):
    pass


class SyncRows:
    def __init__(
        self,
        rows: Sequence[Sequence[object]],
        *,
        failure: BaseException | None = None,
    ) -> None:
        self._rows = list(rows)
        self._failure = failure
        self._index = 0
        self.iter_calls = 0
        self.next_calls = 0
        self.yielded = 0
        self.closed = False

    def __iter__(self) -> SyncRows:
        self.iter_calls += 1
        return self

    def __next__(self) -> Sequence[object]:
        self.next_calls += 1
        if self._index < len(self._rows):
            row = self._rows[self._index]
            self._index += 1
            self.yielded += 1
            return row
        if self._failure is not None:
            failure, self._failure = self._failure, None
            raise failure
        raise StopIteration

    def close(self) -> None:
        self.closed = True


class AsyncRows:
    def __init__(
        self,
        rows: Sequence[Sequence[object]],
        *,
        failure: BaseException | None = None,
        wait_at: int | None = None,
    ) -> None:
        self._rows = list(rows)
        self._failure = failure
        self._wait_at = wait_at
        self._index = 0
        self.aiter_calls = 0
        self.anext_calls = 0
        self.yielded = 0
        self.closed = False
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()

    def __aiter__(self) -> AsyncRows:
        self.aiter_calls += 1
        return self

    async def __anext__(self) -> Sequence[object]:
        self.anext_calls += 1
        if self._wait_at == self._index:
            self.waiting.set()
            await self.release.wait()
            self._wait_at = None
        if self._index < len(self._rows):
            row = self._rows[self._index]
            self._index += 1
            self.yielded += 1
            return row
        if self._failure is not None:
            failure, self._failure = self._failure, None
            raise failure
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.closed = True


@dataclass(frozen=True)
class LockedBulkTables:
    raw_parent: str
    parent: str
    raw_child: str
    child: str


def _isolated_connection(
    config: SqlAuthConfig,
    *,
    application_name: str,
    operation_timeout_secs: float | None = None,
    metrics: bool = False,
) -> Connection:
    return Connection(
        server=config.host,
        port=config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        application_name=application_name,
        pool_config=PoolConfig(
            max_size=1,
            min_idle=1,
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


async def _physical_identity(connection: Any) -> tuple[int, str]:
    row = (
        await connection.query(
            """
            SELECT
                @@SPID AS session_id,
                CONVERT(NVARCHAR(36), connection_id) AS connection_id
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """
        )
    ).fetchone()
    assert row is not None
    return int(row["session_id"]), str(row["connection_id"])


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
    raise AssertionError("candidate application sessions did not reach zero")


async def _wait_for_identity_absent(
    observer: Connection,
    identity: tuple[int, str],
    *,
    timeout: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        count = await scalar(
            observer,
            """
            SELECT COUNT_BIG(*)
            FROM sys.dm_exec_connections
            WHERE session_id = @P1
              AND CONVERT(NVARCHAR(36), connection_id) = @P2
            """,
            [identity[0], identity[1]],
        )
        if count == 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("candidate physical identity remained present")


async def _wait_for_event_or_task(
    event: asyncio.Event,
    task: asyncio.Task[int],
    *,
    timeout: float = 5.0,
) -> None:
    event_waiter = asyncio.create_task(event.wait())
    try:
        done, _ = await asyncio.wait(
            {event_waiter, task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if task in done:
            result = await task
            raise AssertionError(
                f"native bulk completed before producer gate with {result} rows"
            )
        if event_waiter not in done:
            raise AssertionError("producer did not reach the expected wait")
    finally:
        if not event_waiter.done():
            event_waiter.cancel()
        await asyncio.gather(event_waiter, return_exceptions=True)


async def _wait_for_lock_or_task(
    observer: Connection,
    identity: tuple[int, str],
    task: asyncio.Task[int],
    *,
    timeout: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if task.done():
            result = await task
            raise AssertionError(
                f"native bulk completed before lock wait with {result} rows"
            )
        waiting = await scalar(
            observer,
            """
            SELECT COUNT_BIG(*)
            FROM sys.dm_exec_requests
            WHERE session_id = @P1
              AND wait_type LIKE N'LCK_M_%'
            """,
            [identity[0]],
        )
        if waiting == 1:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("candidate native bulk did not enter a lock wait")


async def _settle_task(task: asyncio.Task[int] | None) -> None:
    if task is None:
        return
    if task.done():
        if not task.cancelled():
            task.exception()
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return


async def _create_locked_tables(
    owner: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup: CleanupRegistry,
) -> LockedBulkTables:
    raw_parent = unique_sql_name("strict_iter_bulk_parent")
    raw_child = unique_sql_name("strict_iter_bulk_child")
    parent = quote_identifier(raw_parent)
    child = quote_identifier(raw_child)
    foreign_key = quote_identifier(unique_sql_name("strict_iter_bulk_fk"))
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
            CONSTRAINT {foreign_key}
                FOREIGN KEY (parent_id) REFERENCES {parent}(id)
        )
        """
    )
    await owner.execute(f"INSERT INTO {parent} VALUES (1, 0)")
    return LockedBulkTables(raw_parent, parent, raw_child, child)


async def _hold_parent_lock(
    transaction_factory: Callable[..., Transaction],
    tables: LockedBulkTables,
) -> Transaction:
    blocker = transaction_factory()
    await blocker.begin()
    await blocker.execute(
        f"UPDATE {tables.parent} SET touched = touched + 1 WHERE id = 1"
    )
    return blocker


def _bulk_metric(snapshot: dict[str, Any]) -> dict[str, Any]:
    return snapshot["operations"]["bulk_insert"]


@case("BULK-013")
@pytest.mark.asyncio
async def test_native_bulk_public_iterables_and_raw_list_fast_path(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_iter_bulk_surface")
    table = quote_identifier(raw_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, payload NVARCHAR(40) NOT NULL)"
    )

    concrete = [[1, "list"]]
    assert (
        await owner_connection.native_bulk_insert(
            raw_table,
            ["id", "payload"],
            concrete,
        )
        == 1
    )

    def sync_rows():
        yield (2, "sync")

    async def async_rows():
        yield (3, "async")

    assert (
        await owner_connection.native_bulk_insert(
            raw_table,
            ["id", "payload"],
            sync_rows(),
        )
        == 1
    )
    assert (
        await owner_connection.native_bulk_insert(
            raw_table,
            ["id", "payload"],
            async_rows(),
        )
        == 1
    )

    with pytest.raises(TypeError):
        await owner_connection._conn.native_bulk_insert(
            raw_table,
            ["id", "payload"],
            sync_rows(),
        )

    rows = (
        await owner_connection.query(
            f"SELECT id, payload FROM {table} ORDER BY id"
        )
    ).rows()
    assert [(row["id"], row["payload"]) for row in rows] == [
        (1, "list"),
        (2, "sync"),
        (3, "async"),
    ]


@case("BULK-014")
@pytest.mark.asyncio
async def test_native_bulk_iterable_validation_advances_zero_rows() -> None:
    connection = Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="iterable_validation_probe",
        password="not-used",
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=1,
            min_idle=0,
            connection_timeout_secs=1,
            retry_connection=False,
        ),
        timeout_config=TimeoutConfig(
            connect_timeout_secs=1.0,
            acquire_timeout_secs=1.0,
        ),
        operation_metrics_config=OperationMetricsConfig(enabled=True),
    )

    for invalid in (True, False, 0, -1, 10_001, 1.5, "1"):
        producer = SyncRows([[1]])
        with pytest.raises((TypeError, ValueError), match="chunk_size"):
            await connection.native_bulk_insert(
                "dbo.target",
                ["value"],
                producer,
                chunk_size=invalid,
            )
        assert producer.iter_calls == 0
        assert producer.next_calls == 0

    producer = SyncRows([[1]])
    with pytest.raises(ConversionError) as captured:
        await connection.native_bulk_insert(
            "dbo..target",
            ["value"],
            producer,
        )
    assert captured.value.reason == "identifier_validation_failed"
    assert producer.iter_calls == 0
    assert producer.next_calls == 0

    for invalid_top_level in (
        "not rows",
        b"not rows",
        bytearray(b"not rows"),
        memoryview(b"not rows"),
        {"id": 1},
    ):
        with pytest.raises(TypeError):
            await connection.native_bulk_insert(
                "dbo.target",
                ["value"],
                invalid_top_level,
            )

    assert await connection.is_connected() is False
    metric = _bulk_metric(await connection.operation_stats())
    assert metric["started"] == 0
    assert metric["completed"] == 0


@case("BULK-015")
@pytest.mark.asyncio
async def test_native_bulk_empty_iterables_have_zero_sql_and_metrics(
    owner_connection: Connection,
) -> None:
    disconnected = Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="iterable_empty_probe",
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
    empty_sync = SyncRows([])
    assert (
        await disconnected.native_bulk_insert(
            "dbo.target",
            ["value"],
            empty_sync,
        )
        == 0
    )
    assert empty_sync.next_calls == 1
    assert await disconnected.is_connected() is False
    metric = _bulk_metric(await disconnected.operation_stats())
    assert metric["started"] == 0
    assert metric["completed"] == 0

    transaction = owner_connection.transaction()
    try:
        await transaction.begin()
        empty_async = AsyncRows([])
        assert (
            await transaction.native_bulk_insert(
                "dbo.target",
                ["value"],
                empty_async,
            )
            == 0
        )
        assert empty_async.anext_calls == 1
        assert await scalar(transaction, "SELECT @@TRANCOUNT") == 1
        assert await scalar(transaction, "SELECT XACT_STATE()") == 1
        await transaction.rollback()
    finally:
        await transaction.close()


@case("BULK-016")
@pytest.mark.asyncio
async def test_native_bulk_sync_generator_is_backpressured_by_tds(
    sql_auth_config: SqlAuthConfig,
    owner_connection: Connection,
    sa_connection: Connection,
    transaction_factory: Callable[..., Transaction],
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    tables = await _create_locked_tables(
        owner_connection,
        unique_sql_name,
        cleanup_registry,
    )
    candidate = _isolated_connection(
        sql_auth_config,
        application_name=unique_sql_name("strict_iter_bulk_sync_app"),
    )
    blocker = await _hold_parent_lock(transaction_factory, tables)
    rows = SyncRows(
        [(index, 1, f"sync-{index}") for index in range(5)]
    )
    task: asyncio.Task[int] | None = None
    try:
        await candidate.connect()
        identity = await _physical_identity(candidate)
        task = asyncio.create_task(
            candidate.native_bulk_insert(
                tables.raw_child,
                ["id", "parent_id", "payload"],
                rows,
                chunk_size=2,
            )
        )
        await _wait_for_lock_or_task(sa_connection, identity, task)
        assert rows.yielded == 2
        await asyncio.sleep(0.05)
        assert rows.yielded == 2

        await blocker.rollback()
        assert await task == 5
        task = None
        assert rows.iter_calls == 1
        assert rows.yielded == 5
        assert rows.next_calls == 6
        assert await _physical_identity(candidate) == identity
        persisted = (
            await owner_connection.query(
                f"SELECT id, payload FROM {tables.child} ORDER BY id"
            )
        ).rows()
        assert [(row["id"], row["payload"]) for row in persisted] == [
            (index, f"sync-{index}") for index in range(5)
        ]
    finally:
        await _settle_task(task)
        await blocker.close()
        await candidate.disconnect()


@case("BULK-017")
@pytest.mark.asyncio
async def test_native_bulk_async_generator_is_backpressured_by_tds(
    sql_auth_config: SqlAuthConfig,
    owner_connection: Connection,
    sa_connection: Connection,
    transaction_factory: Callable[..., Transaction],
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    tables = await _create_locked_tables(
        owner_connection,
        unique_sql_name,
        cleanup_registry,
    )
    candidate = _isolated_connection(
        sql_auth_config,
        application_name=unique_sql_name("strict_iter_bulk_async_app"),
    )
    blocker = await _hold_parent_lock(transaction_factory, tables)
    rows = AsyncRows(
        [(index, 1, f"async-{index}") for index in range(5)]
    )
    task: asyncio.Task[int] | None = None
    try:
        await candidate.connect()
        identity = await _physical_identity(candidate)
        task = asyncio.create_task(
            candidate.native_bulk_insert(
                tables.raw_child,
                ["id", "parent_id", "payload"],
                rows,
                chunk_size=2,
            )
        )
        await _wait_for_lock_or_task(sa_connection, identity, task)
        assert rows.yielded == 2
        await asyncio.sleep(0.05)
        assert rows.yielded == 2

        await blocker.rollback()
        assert await task == 5
        task = None
        assert rows.aiter_calls == 1
        assert rows.yielded == 5
        assert rows.anext_calls == 6
        assert await _physical_identity(candidate) == identity
        persisted = (
            await owner_connection.query(
                f"SELECT id, payload FROM {tables.child} ORDER BY id"
            )
        ).rows()
        assert [(row["id"], row["payload"]) for row in persisted] == [
            (index, f"async-{index}") for index in range(5)
        ]
    finally:
        await _settle_task(task)
        await blocker.close()
        await candidate.disconnect()


@case("BULK-018")
@pytest.mark.asyncio
async def test_native_bulk_late_iterable_failures_are_atomic_and_global(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_iter_bulk_failure")
    table = quote_identifier(raw_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, payload INT NOT NULL)"
    )

    primary = ProducerFailure()
    producer = SyncRows([(1, 10), (2, 20)], failure=primary)
    with pytest.raises(ProducerFailure) as captured:
        await owner_connection.native_bulk_insert(
            raw_table,
            ["id", "payload"],
            producer,
            chunk_size=1,
        )
    assert captured.value is primary
    assert producer.closed is True
    assert await scalar(owner_connection, f"SELECT COUNT_BIG(*) FROM {table}") == 0
    assert await scalar(owner_connection, "SELECT 1") == 1

    invalid_value = object()
    conversion_rows = SyncRows(
        [(1, 10), (2, 20), (3, invalid_value)]
    )
    with pytest.raises(ConversionError) as conversion:
        await owner_connection.native_bulk_insert(
            raw_table,
            ["id", "payload"],
            conversion_rows,
            chunk_size=2,
        )
    error = conversion.value
    assert error.row_index == 2
    assert error.column_index == 1
    assert error.parameter_index == 5
    assert conversion_rows.closed is True
    assert await scalar(owner_connection, f"SELECT COUNT_BIG(*) FROM {table}") == 0
    assert await scalar(owner_connection, "SELECT 1") == 1


@case("BULK-019")
@pytest.mark.asyncio
async def test_native_bulk_iterable_transaction_is_neutral_then_rollback_only(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_iter_bulk_transaction")
    table = quote_identifier(raw_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")

    transaction = owner_connection.transaction()
    try:
        await transaction.begin()
        success_rows = AsyncRows([(1,), (2,)])
        assert (
            await transaction.native_bulk_insert(
                raw_table,
                ["id"],
                success_rows,
                chunk_size=1,
            )
            == 2
        )
        assert await scalar(transaction, f"SELECT COUNT_BIG(*) FROM {table}") == 2
        assert await scalar(transaction, "SELECT @@TRANCOUNT") == 1
        assert await scalar(transaction, "SELECT XACT_STATE()") == 1

        primary = ProducerFailure()
        failure_rows = SyncRows([(3,)], failure=primary)
        with pytest.raises(ProducerFailure) as captured:
            await transaction.native_bulk_insert(
                raw_table,
                ["id"],
                failure_rows,
                chunk_size=1,
            )
        assert captured.value is primary
        assert failure_rows.closed is True

        for rejected in (
            lambda: transaction.query("SELECT 1"),
            lambda: transaction.execute(f"INSERT INTO {table} VALUES (4)"),
            lambda: transaction.native_bulk_insert(
                raw_table,
                ["id"],
                SyncRows([(5,)]),
            ),
            transaction.commit,
        ):
            with pytest.raises(RuntimeError, match="rollback required"):
                await rejected()

        await transaction.rollback()
        assert await scalar(owner_connection, f"SELECT COUNT_BIG(*) FROM {table}") == 0
    finally:
        await transaction.close()


@case("BULK-020")
@pytest.mark.asyncio
async def test_native_bulk_iterable_cancellation_stops_pulls_and_cleans_up(
    sql_auth_config: SqlAuthConfig,
    owner_connection: Connection,
    sa_connection: Connection,
    transaction_factory: Callable[..., Transaction],
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_wait_table = unique_sql_name("strict_iter_bulk_cancel_wait")
    wait_table = quote_identifier(raw_wait_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {wait_table}")
    await owner_connection.execute(f"CREATE TABLE {wait_table} (id INT PRIMARY KEY)")

    wait_application = unique_sql_name("strict_iter_bulk_cancel_wait_app")
    wait_connection = _isolated_connection(
        sql_auth_config,
        application_name=wait_application,
        metrics=True,
    )
    waiting_rows = AsyncRows([(1,)], wait_at=1)
    wait_task: asyncio.Task[int] | None = None
    try:
        wait_task = asyncio.create_task(
            wait_connection.native_bulk_insert(
                raw_wait_table,
                ["id"],
                waiting_rows,
                chunk_size=1,
            )
        )
        await _wait_for_event_or_task(waiting_rows.waiting, wait_task)
        assert waiting_rows.yielded == 1
        wait_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await wait_task
        wait_task = None
        assert waiting_rows.yielded == 1
        assert waiting_rows.closed is True
        assert await scalar(owner_connection, f"SELECT COUNT_BIG(*) FROM {wait_table}") == 0
        assert await scalar(wait_connection, "SELECT 1") == 1
        metric = _bulk_metric(await wait_connection.operation_stats())
        assert metric["started"] == 1
        assert metric["cancelled"] == 1
    finally:
        await _settle_task(wait_task)
        await wait_connection.disconnect()
        await _wait_for_zero_sessions(sa_connection, wait_application)

    tables = await _create_locked_tables(
        owner_connection,
        unique_sql_name,
        cleanup_registry,
    )
    wire_application = unique_sql_name("strict_iter_bulk_cancel_wire_app")
    wire_connection = _isolated_connection(
        sql_auth_config,
        application_name=wire_application,
        metrics=True,
    )
    blocker = await _hold_parent_lock(transaction_factory, tables)
    wire_rows = AsyncRows([(1, 1, "cancel")])
    wire_task: asyncio.Task[int] | None = None
    try:
        await wire_connection.connect()
        identity = await _physical_identity(wire_connection)
        wire_task = asyncio.create_task(
            wire_connection.native_bulk_insert(
                tables.raw_child,
                ["id", "parent_id", "payload"],
                wire_rows,
                chunk_size=1,
            )
        )
        await _wait_for_lock_or_task(sa_connection, identity, wire_task)
        assert wire_rows.yielded == 1
        wire_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await wire_task
        wire_task = None
        await blocker.rollback()
        await _wait_for_identity_absent(sa_connection, identity)
        assert wire_rows.yielded == 1
        assert wire_rows.closed is True
        assert await scalar(owner_connection, f"SELECT COUNT_BIG(*) FROM {tables.child}") == 0
        assert await scalar(wire_connection, "SELECT 1") == 1
        metric = _bulk_metric(await wire_connection.operation_stats())
        assert metric["started"] == 1
        assert metric["cancelled"] == 1, metric
    finally:
        await _settle_task(wire_task)
        await blocker.close()
        await wire_connection.disconnect()
        await _wait_for_zero_sessions(sa_connection, wire_application)


@case("BULK-021")
@pytest.mark.asyncio
async def test_native_bulk_iterable_timeout_is_typed_bounded_and_cleans_up(
    sql_auth_config: SqlAuthConfig,
    owner_connection: Connection,
    sa_connection: Connection,
    transaction_factory: Callable[..., Transaction],
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    producer_application = unique_sql_name("strict_iter_bulk_timeout_wait_app")
    producer_connection = _isolated_connection(
        sql_auth_config,
        application_name=producer_application,
        operation_timeout_secs=0.25,
        metrics=True,
    )
    waiting_rows = AsyncRows([(1,)], wait_at=0)
    try:
        started = time.monotonic()
        with pytest.raises(OperationTimeoutError) as producer_timeout:
            await asyncio.wait_for(
                producer_connection.native_bulk_insert(
                    "dbo.timeout_probe",
                    ["id"],
                    waiting_rows,
                    chunk_size=1,
                ),
                timeout=2.0,
            )
        assert time.monotonic() - started < 2.0
        assert producer_timeout.value.operation == "bulk_insert"
        assert waiting_rows.yielded == 0
        assert waiting_rows.closed is True
        assert await producer_connection.is_connected() is False
        assert (
            await _application_session_count(sa_connection, producer_application)
            == 0
        )
        metric = _bulk_metric(await producer_connection.operation_stats())
        assert metric["started"] == 1
        assert metric["timed_out"] == 1
    finally:
        await producer_connection.disconnect()
        await _wait_for_zero_sessions(sa_connection, producer_application)

    tables = await _create_locked_tables(
        owner_connection,
        unique_sql_name,
        cleanup_registry,
    )
    wire_application = unique_sql_name("strict_iter_bulk_timeout_wire_app")
    wire_connection = _isolated_connection(
        sql_auth_config,
        application_name=wire_application,
        operation_timeout_secs=0.5,
        metrics=True,
    )
    blocker = await _hold_parent_lock(transaction_factory, tables)
    wire_rows = AsyncRows([(1, 1, "timeout")])
    wire_task: asyncio.Task[int] | None = None
    try:
        await wire_connection.connect()
        identity = await _physical_identity(wire_connection)
        wire_task = asyncio.create_task(
            wire_connection.native_bulk_insert(
                tables.raw_child,
                ["id", "parent_id", "payload"],
                wire_rows,
                chunk_size=1,
            )
        )
        await _wait_for_lock_or_task(sa_connection, identity, wire_task)
        with pytest.raises(OperationTimeoutError) as wire_timeout:
            await asyncio.wait_for(wire_task, timeout=2.0)
        wire_task = None
        error = wire_timeout.value
        assert error.operation == "bulk_insert"
        assert error.connection_discarded is True
        assert error.outcome_unknown is False
        await blocker.rollback()
        await _wait_for_identity_absent(sa_connection, identity)
        assert wire_rows.yielded == 1
        assert wire_rows.closed is True
        assert await scalar(owner_connection, f"SELECT COUNT_BIG(*) FROM {tables.child}") == 0
        assert await scalar(wire_connection, "SELECT 1") == 1
        metric = _bulk_metric(await wire_connection.operation_stats())
        assert metric["started"] == 1
        assert metric["timed_out"] == 1
    finally:
        await _settle_task(wire_task)
        await blocker.close()
        await wire_connection.disconnect()
        await _wait_for_zero_sessions(sa_connection, wire_application)
