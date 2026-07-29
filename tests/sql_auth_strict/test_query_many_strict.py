from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from contextlib import suppress
from decimal import Decimal
import inspect
from typing import Any

import fastmssql
from fastmssql import (
    Connection,
    ConversionError,
    OperationMetricsConfig,
    OperationTimeoutError,
    Parameter,
    Parameters,
    PoolConfig,
    ProtocolError,
    QueryStream,
    SqlConnectionError,
    SqlError,
    SslConfig,
    TimeoutConfig,
    TlsError,
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


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


class ProducerFailure(BaseException):
    pass


class SyncParameterSets:
    def __init__(
        self,
        values: Sequence[object],
        *,
        failure: BaseException | None = None,
    ) -> None:
        self._values = list(values)
        self._failure = failure
        self._index = 0
        self.iter_calls = 0
        self.next_calls = 0
        self.yielded = 0
        self.close_calls = 0

    def __iter__(self) -> SyncParameterSets:
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
        raise StopIteration

    def close(self) -> None:
        self.close_calls += 1


class AsyncParameterSets:
    def __init__(self, values: Sequence[object]) -> None:
        self._values = list(values)
        self._index = 0
        self.aiter_calls = 0
        self.anext_calls = 0
        self.yielded = 0
        self.aclose_calls = 0

    def __aiter__(self) -> AsyncParameterSets:
        self.aiter_calls += 1
        return self

    async def __anext__(self) -> object:
        self.anext_calls += 1
        if self._index >= len(self._values):
            raise StopAsyncIteration
        value = self._values[self._index]
        self._index += 1
        self.yielded += 1
        return value

    async def aclose(self) -> None:
        self.aclose_calls += 1


class SensitiveValue:
    def __repr__(self) -> str:
        return "QMANY_PRIVATE_VALUE"


def _connection(
    config: SqlAuthConfig,
    *,
    application_name: str,
    max_size: int = 3,
    operation_timeout_secs: float | None = 3.0,
    acquire_timeout_secs: float = 2.0,
    metrics: bool = False,
    shutdown_timeout_secs: float | None = None,
    force_timeout_secs: float | None = None,
) -> Connection:
    lifecycle_config = None
    if shutdown_timeout_secs is not None or force_timeout_secs is not None:
        lifecycle_type = getattr(fastmssql, "LifecycleConfig")
        lifecycle_config = lifecycle_type(
            shutdown_timeout_secs=shutdown_timeout_secs or 3.0,
            force_timeout_secs=force_timeout_secs or 1.0,
        )
    return Connection(
        server=config.host,
        port=config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        application_name=application_name,
        pool_config=PoolConfig(
            max_size=max_size,
            min_idle=0,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=2,
            test_on_check_out=False,
            retry_connection=False,
        ),
        timeout_config=TimeoutConfig(
            connect_timeout_secs=2.0,
            acquire_timeout_secs=acquire_timeout_secs,
            operation_timeout_secs=operation_timeout_secs,
            rollback_timeout_secs=2.0,
        ),
        operation_metrics_config=OperationMetricsConfig(enabled=metrics),
        lifecycle_config=lifecycle_config,
    )


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
              AND session_id <> @@SPID
            """,
            [application_name],
        )
    )


async def _application_request_spids(
    observer: Connection,
    application_name: str,
) -> list[int]:
    rows = (
        await observer.query(
            """
            SELECT request.session_id
            FROM sys.dm_exec_requests AS request
            JOIN sys.dm_exec_sessions AS session
              ON session.session_id = request.session_id
            WHERE session.program_name = @P1
              AND request.session_id <> @@SPID
            ORDER BY request.session_id
            """,
            [application_name],
        )
    ).rows()
    return [int(row[0]) for row in rows]


async def _application_lock_wait_count(
    observer: Connection,
    application_name: str,
) -> int:
    return int(
        await scalar(
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
    )


async def _wait_for_value(
    value: Callable[[], Any],
    expected: object,
    *,
    task: asyncio.Task[Any] | None = None,
    timeout: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    latest: object = None
    while asyncio.get_running_loop().time() < deadline:
        if task is not None and task.done():
            if task.cancelled():
                raise AssertionError("query-many task was cancelled unexpectedly")
            error = task.exception()
            if error is not None:
                raise AssertionError(
                    "query-many task failed before observed SQL state"
                ) from error
            raise AssertionError("query-many task completed before observed SQL state")
        latest = await value()
        if latest == expected:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"expected observed value {expected!r}, got {latest!r}")


async def _wait_for_zero_sessions(
    observer: Connection,
    application_name: str,
) -> None:
    await _wait_for_value(
        lambda: _application_session_count(observer, application_name),
        0,
        timeout=8.0,
    )


async def _wait_for_lock_count(
    observer: Connection,
    application_name: str,
    expected: int,
    *,
    task: asyncio.Task[Any],
) -> None:
    await _wait_for_value(
        lambda: _application_lock_wait_count(observer, application_name),
        expected,
        task=task,
    )


async def _wait_for_request_count(
    observer: Connection,
    application_name: str,
    expected: int,
    *,
    task: asyncio.Task[Any],
) -> None:
    async def count() -> int:
        return len(
            await _application_request_spids(observer, application_name)
        )

    await _wait_for_value(count, expected, task=task)


async def _physical_connection_id(connection: Connection) -> str:
    return str(
        await scalar(
            connection,
            """
            SELECT CONVERT(NVARCHAR(36), connection_id)
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """,
        )
    )


async def _settle_task(task: asyncio.Task[Any] | None) -> None:
    if task is None:
        return
    if task.done():
        if not task.cancelled():
            task.exception()
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def _rollback_and_close(transaction: Transaction) -> None:
    with suppress(Exception):
        await transaction.rollback()
    await transaction.close()


async def _block_gate_rows(
    transaction_factory: Callable[..., Transaction],
    table: str,
    gate_ids: Sequence[int],
) -> dict[int, Transaction]:
    blockers: dict[int, Transaction] = {}
    try:
        for gate_id in gate_ids:
            blocker = transaction_factory()
            blockers[gate_id] = blocker
            await blocker.begin()
            await blocker.execute(
                f"""
                UPDATE {table}
                SET touched = touched + 1
                WHERE gate_id = @P1
                """,
                [gate_id],
            )
        return blockers
    except BaseException:
        for blocker in blockers.values():
            await _rollback_and_close(blocker)
        raise


async def _consume_ids(
    iterator: Any,
    yielded: list[int],
    spids: list[int],
    events: Sequence[asyncio.Event] | None = None,
) -> None:
    async with iterator as results:
        async for result in results:
            assert isinstance(result, QueryStream)
            row = result.fetchone()
            assert row is not None
            yielded.append(int(row["gate_id"]))
            spids.append(int(row["spid"]))
            if events is not None:
                events[len(yielded) - 1].set()


async def _collect_rows(iterator: Any) -> list[QueryStream]:
    streams: list[QueryStream] = []
    async with iterator as results:
        async for result in results:
            assert isinstance(result, QueryStream)
            streams.append(result)
    return streams


async def _wait_for_lifecycle_state(
    connection: Connection,
    expected: object,
) -> None:
    async def state() -> object:
        return connection.lifecycle_state

    await _wait_for_value(state, expected)


@case("QMANY-001", "QMANY-002", "QMANY-003")
@pytest.mark.asyncio
async def test_query_many_surface_validation_and_empty(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("strict_qmany_surface")
    connection = _connection(
        sql_auth_config,
        application_name=application_name,
        metrics=True,
    )
    transaction = Transaction(
        sql_auth_config.connection_string(
            sql_auth_config.owner_user,
            sql_auth_config.owner_password,
        )
    )
    source = SyncParameterSets([[1]])
    try:
        assert callable(getattr(Connection, "query_many", None))
        assert not hasattr(connection._conn, "query_many")
        assert not hasattr(transaction, "query_many")

        for concurrency in (True, False, 0, -1, 10_001, 1.5, "2", None):
            with pytest.raises((TypeError, ValueError)):
                connection.query_many(
                    "SELECT @P1",
                    source,
                    concurrency=concurrency,
                )
        for ordered in (0, 1, "yes", None):
            with pytest.raises((TypeError, ValueError)):
                connection.query_many(
                    "SELECT @P1",
                    source,
                    ordered=ordered,
                )
        for invalid_source in (
            "rows",
            b"rows",
            bytearray(b"rows"),
            memoryview(b"rows"),
            {},
        ):
            with pytest.raises((TypeError, ValueError)):
                connection.query_many("SELECT @P1", invalid_source)

        assert source.iter_calls == 0
        assert source.next_calls == 0
        assert await _application_session_count(
            sa_connection,
            application_name,
        ) == 0

        empty_sources = (
            [],
            SyncParameterSets([]),
            AsyncParameterSets([]),
        )
        for empty_source in empty_sources:
            iterator = connection.query_many(
                "SELECT @P1",
                empty_source,
                concurrency=3,
                ordered=True,
            )
            assert not inspect.isawaitable(iterator)
            assert iterator.__aiter__() is iterator
            assert await _collect_rows(iterator) == []

        stats = await connection.operation_stats()
        assert stats["schema_version"] == 2
        assert stats["operations"]["query"]["started"] == 0
        pool = await connection.pool_stats()
        assert pool["connections"] == 0
        assert pool["active_connections"] == 0
    finally:
        await transaction.close()
        await connection.disconnect()
    await _wait_for_zero_sessions(sa_connection, application_name)


@case("QMANY-004")
@pytest.mark.asyncio
async def test_query_many_parameter_sources_and_typed_results(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    customers = quote_identifier(unique_sql_name("strict_qmany_customers"))
    orders = quote_identifier(unique_sql_name("strict_qmany_orders"))
    procedure = quote_identifier(unique_sql_name("strict_qmany_proc"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {customers}")
    cleanup_registry.add(f"DROP TABLE IF EXISTS {orders}")
    cleanup_registry.add(f"DROP PROCEDURE IF EXISTS {procedure}")
    await owner_connection.execute(
        f"CREATE TABLE {customers} (id INT PRIMARY KEY, name NVARCHAR(40))"
    )
    await owner_connection.execute(
        f"""
        CREATE TABLE {orders} (
            id INT PRIMARY KEY,
            customer_id INT NOT NULL,
            amount DECIMAL(9,2) NOT NULL
        )
        """
    )
    await owner_connection.execute(
        f"INSERT INTO {customers} VALUES (1, N'Ada'), (2, N'Bob')"
    )
    await owner_connection.execute(
        f"""
        INSERT INTO {orders}
        VALUES (10, 1, 12.50), (11, 1, 7.25), (12, 2, 20.00)
        """
    )
    await owner_connection.execute(
        f"""
        CREATE PROCEDURE {procedure}
            @customer_id INT
        AS
        BEGIN
            SET NOCOUNT ON;
            SELECT COUNT_BIG(*) AS order_count
            FROM {orders}
            WHERE customer_id = @customer_id;
        END
        """
    )

    join_streams = await _collect_rows(
        owner_connection.query_many(
            f"""
            SELECT customer.name, SUM([order].amount) AS total
            FROM {customers} AS customer
            JOIN {orders} AS [order]
              ON [order].customer_id = customer.id
            WHERE customer.id = @P1
            GROUP BY customer.name
            """,
            [
                Parameters(Parameter(1, "INT")),
                Parameters(Parameter(2, "INT")),
            ],
            concurrency=2,
            ordered=True,
        )
    )
    join_rows = [stream.fetchone() for stream in join_streams]
    assert [(row["name"], row["total"]) for row in join_rows] == [
        ("Ada", Decimal("19.75")),
        ("Bob", Decimal("20.00")),
    ]

    cte_streams = await _collect_rows(
        owner_connection.query_many(
            f"""
            WITH selected AS (
                SELECT id, amount
                FROM {orders}
                WHERE customer_id = @P1
            )
            SELECT COUNT_BIG(*) AS count_value, SUM(amount) AS total
            FROM selected
            """,
            SyncParameterSets(
                [
                    Parameters(Parameter(1, "INT")),
                    [2],
                ]
            ),
            concurrency=2,
            ordered=True,
        )
    )
    cte_rows = [stream.fetchone() for stream in cte_streams]
    assert [(row["count_value"], row["total"]) for row in cte_rows] == [
        (2, Decimal("19.75")),
        (1, Decimal("20.00")),
    ]

    empty_streams = await _collect_rows(
        owner_connection.query_many(
            f"SELECT id FROM {orders} WHERE id = @P1",
            AsyncParameterSets([[999], [998]]),
            concurrency=2,
            ordered=True,
        )
    )
    assert [stream.fetchone() for stream in empty_streams] == [None, None]

    procedure_streams = await _collect_rows(
        owner_connection.query_many(
            f"EXEC {procedure} @P1",
            [[1], [2]],
            concurrency=2,
            ordered=True,
        )
    )
    assert [
        int(stream.fetchone()["order_count"]) for stream in procedure_streams
    ] == [2, 1]


async def _run_order_case(
    config: SqlAuthConfig,
    observer: Connection,
    transaction_factory: Callable[..., Transaction],
    unique_sql_name: Callable[[str], str],
    table: str,
    *,
    ordered: bool,
) -> None:
    blockers = await _block_gate_rows(transaction_factory, table, (0, 1, 2))
    application_name = unique_sql_name(
        "strict_qmany_ordered" if ordered else "strict_qmany_completion"
    )
    connection = _connection(
        config,
        application_name=application_name,
        max_size=3,
    )
    yielded: list[int] = []
    spids: list[int] = []
    events = [asyncio.Event() for _ in range(3)]
    task: asyncio.Task[None] | None = None
    try:
        task = asyncio.create_task(
            _consume_ids(
                connection.query_many(
                    f"""
                    SELECT gate_id, @@SPID AS spid
                    FROM {table} WITH (UPDLOCK, ROWLOCK)
                    WHERE gate_id = @P1
                    """,
                    [[0], [1], [2]],
                    concurrency=3,
                    ordered=ordered,
                ),
                yielded,
                spids,
                events,
            )
        )
        await _wait_for_lock_count(
            observer,
            application_name,
            3,
            task=task,
        )
        assert await _application_session_count(
            observer,
            application_name,
        ) == 3

        await blockers[2].rollback()
        await _wait_for_lock_count(
            observer,
            application_name,
            2,
            task=task,
        )
        if ordered:
            assert yielded == []
        else:
            await asyncio.wait_for(events[0].wait(), timeout=2.0)
            assert yielded == [2]

        await blockers[0].rollback()
        await asyncio.wait_for(
            events[0 if ordered else 1].wait(),
            timeout=2.0,
        )
        assert yielded == ([0] if ordered else [2, 0])

        await blockers[1].rollback()
        await asyncio.wait_for(task, timeout=3.0)
        task = None
        assert yielded == ([0, 1, 2] if ordered else [2, 0, 1])
        assert len(set(spids)) == 3
    finally:
        await _settle_task(task)
        for blocker in blockers.values():
            await _rollback_and_close(blocker)
        await connection.disconnect()
    await _wait_for_zero_sessions(observer, application_name)


async def _run_pool_cap_case(
    config: SqlAuthConfig,
    observer: Connection,
    transaction_factory: Callable[..., Transaction],
    unique_sql_name: Callable[[str], str],
    table: str,
) -> None:
    blockers = await _block_gate_rows(transaction_factory, table, (0, 1, 2))
    application_name = unique_sql_name("strict_qmany_pool_cap")
    connection = _connection(
        config,
        application_name=application_name,
        max_size=2,
    )
    producer = SyncParameterSets([[0], [1], [2]])
    yielded: list[int] = []
    spids: list[int] = []
    events = [asyncio.Event() for _ in range(3)]
    task: asyncio.Task[None] | None = None
    try:
        task = asyncio.create_task(
            _consume_ids(
                connection.query_many(
                    f"""
                    SELECT gate_id, @@SPID AS spid
                    FROM {table} WITH (UPDLOCK, ROWLOCK)
                    WHERE gate_id = @P1
                    """,
                    producer,
                    concurrency=5,
                    ordered=False,
                ),
                yielded,
                spids,
                events,
            )
        )
        await _wait_for_lock_count(
            observer,
            application_name,
            2,
            task=task,
        )
        assert producer.yielded == 2
        assert await _application_session_count(
            observer,
            application_name,
        ) == 2

        await blockers[0].rollback()
        await asyncio.wait_for(events[0].wait(), timeout=2.0)
        await _wait_for_lock_count(
            observer,
            application_name,
            2,
            task=task,
        )
        assert producer.yielded == 3

        await blockers[1].rollback()
        await blockers[2].rollback()
        await asyncio.wait_for(task, timeout=3.0)
        task = None
        assert sorted(yielded) == [0, 1, 2]
        assert len(set(spids)) == 2
    finally:
        await _settle_task(task)
        for blocker in blockers.values():
            await _rollback_and_close(blocker)
        await connection.disconnect()
    await _wait_for_zero_sessions(observer, application_name)


@case("QMANY-005", "QMANY-006", "QMANY-007")
@pytest.mark.asyncio
async def test_query_many_pool_window_and_ordering(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    transaction_factory: Callable[..., Transaction],
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_qmany_gate"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await cleanup_registry.connection.execute(
        f"""
        CREATE TABLE {table} (
            gate_id INT PRIMARY KEY,
            touched INT NOT NULL
        )
        """
    )
    await cleanup_registry.connection.execute(
        f"INSERT INTO {table} VALUES (0, 0), (1, 0), (2, 0)"
    )

    await _run_order_case(
        sql_auth_config,
        sa_connection,
        transaction_factory,
        unique_sql_name,
        table,
        ordered=True,
    )
    await _run_order_case(
        sql_auth_config,
        sa_connection,
        transaction_factory,
        unique_sql_name,
        table,
        ordered=False,
    )
    await _run_pool_cap_case(
        sql_auth_config,
        sa_connection,
        transaction_factory,
        unique_sql_name,
        table,
    )


async def _assert_failure_sessions_recover(
    connection: Connection,
    observer: Connection,
    application_name: str,
) -> None:
    try:
        assert int(await scalar(connection, "SELECT 1")) == 1
        pool = await connection.pool_stats()
        assert pool["active_connections"] == 0
    finally:
        await connection.disconnect()
    await _wait_for_zero_sessions(observer, application_name)


@case("QMANY-008", "QMANY-009")
@pytest.mark.asyncio
async def test_query_many_failure_and_consumer_cleanup(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    transaction_factory: Callable[..., Transaction],
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    producer_app = unique_sql_name("strict_qmany_producer_error")
    producer_connection = _connection(
        sql_auth_config,
        application_name=producer_app,
        max_size=1,
    )
    producer_failure = ProducerFailure()
    producer = SyncParameterSets([[0]], failure=producer_failure)
    try:
        with pytest.raises(ProducerFailure) as caught:
            await _collect_rows(
                producer_connection.query_many(
                    "SELECT @P1 AS value",
                    producer,
                    concurrency=1,
                )
            )
        assert caught.value is producer_failure
        assert caught.value.query_index == 1
        assert producer.close_calls == 1
    finally:
        await _assert_failure_sessions_recover(
            producer_connection,
            sa_connection,
            producer_app,
        )

    conversion_app = unique_sql_name("strict_qmany_conversion_error")
    conversion_connection = _connection(
        sql_auth_config,
        application_name=conversion_app,
        max_size=1,
    )
    try:
        with pytest.raises(ConversionError) as caught:
            await _collect_rows(
                conversion_connection.query_many(
                    "SELECT @P1 AS value",
                    [[0], [SensitiveValue()]],
                    concurrency=1,
                )
            )
        assert caught.value.query_index == 1
        assert caught.value.parameter_index == 0
        assert "QMANY_PRIVATE_VALUE" not in str(caught.value)
    finally:
        await _assert_failure_sessions_recover(
            conversion_connection,
            sa_connection,
            conversion_app,
        )

    sql_app = unique_sql_name("strict_qmany_sql_error")
    sql_connection = _connection(
        sql_auth_config,
        application_name=sql_app,
        max_size=1,
    )
    try:
        with pytest.raises(SqlError) as caught:
            await _collect_rows(
                sql_connection.query_many(
                    """
                    IF @P1 = 1
                        THROW 51000, 'query-many deterministic failure', 1;
                    SELECT @P1 AS value;
                    """,
                    [[0], [1], [2]],
                    concurrency=1,
                )
            )
        assert caught.value.query_index == 1
    finally:
        await _assert_failure_sessions_recover(
            sql_connection,
            sa_connection,
            sql_app,
        )

    gate = quote_identifier(unique_sql_name("strict_qmany_cleanup_gate"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {gate}")
    await cleanup_registry.connection.execute(
        f"CREATE TABLE {gate} (gate_id INT PRIMARY KEY, touched INT NOT NULL)"
    )
    await cleanup_registry.connection.execute(
        f"INSERT INTO {gate} VALUES (0, 0), (1, 0), (2, 0)"
    )
    blockers = await _block_gate_rows(transaction_factory, gate, (1, 2))
    early_app = unique_sql_name("strict_qmany_early_exit")
    early_connection = _connection(
        sql_auth_config,
        application_name=early_app,
        max_size=3,
    )
    try:
        async with early_connection.query_many(
            f"""
            SELECT gate_id, @@SPID AS spid
            FROM {gate} WITH (UPDLOCK, ROWLOCK)
            WHERE gate_id = @P1
            """,
            [[0], [1], [2]],
            concurrency=3,
            ordered=True,
        ) as results:
            first = await results.__anext__()
            assert first.fetchone()["gate_id"] == 0
            await _wait_for_value(
                lambda: _application_lock_wait_count(
                    sa_connection,
                    early_app,
                ),
                2,
            )
        assert (await early_connection.pool_stats())["active_connections"] == 0
    finally:
        for blocker in blockers.values():
            await _rollback_and_close(blocker)
        await early_connection.disconnect()
    await _wait_for_zero_sessions(sa_connection, early_app)

    cancellation_blocker = await _block_gate_rows(
        transaction_factory,
        gate,
        (0,),
    )
    cancel_app = unique_sql_name("strict_qmany_consumer_cancel")
    cancel_connection = _connection(
        sql_auth_config,
        application_name=cancel_app,
        max_size=1,
    )
    iterator = cancel_connection.query_many(
        f"""
        SELECT gate_id, @@SPID AS spid
        FROM {gate} WITH (UPDLOCK, ROWLOCK)
        WHERE gate_id = @P1
        """,
        [[0]],
        concurrency=1,
    )
    next_task: asyncio.Task[Any] | None = None
    try:
        next_task = asyncio.create_task(iterator.__anext__())
        await _wait_for_lock_count(
            sa_connection,
            cancel_app,
            1,
            task=next_task,
        )
        next_task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await next_task
        assert not hasattr(caught.value, "query_index")
        next_task = None
        assert (await cancel_connection.pool_stats())["active_connections"] == 0
    finally:
        await _settle_task(next_task)
        await iterator.aclose()
        for blocker in cancellation_blocker.values():
            await _rollback_and_close(blocker)
        await cancel_connection.disconnect()
    await _wait_for_zero_sessions(sa_connection, cancel_app)


@case("QMANY-010", "QMANY-011")
@pytest.mark.asyncio
async def test_query_many_faults_and_lifecycle(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    transaction_factory: Callable[..., Transaction],
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    del transaction_factory, cleanup_registry
    timeout_app = unique_sql_name("strict_qmany_timeout")
    timeout_connection = _connection(
        sql_auth_config,
        application_name=timeout_app,
        max_size=2,
        operation_timeout_secs=0.10,
    )
    try:
        with pytest.raises(OperationTimeoutError) as caught:
            await _collect_rows(
                timeout_connection.query_many(
                    "WAITFOR DELAY '00:00:02'; SELECT @P1 AS value",
                    [[0], [1]],
                    concurrency=2,
                )
            )
        assert caught.value.query_index in {0, 1}
        assert int(await scalar(timeout_connection, "SELECT 1")) == 1
    finally:
        await timeout_connection.disconnect()
    await _wait_for_zero_sessions(sa_connection, timeout_app)

    acquire_app = unique_sql_name("strict_qmany_acquire")
    acquire_connection = _connection(
        sql_auth_config,
        application_name=acquire_app,
        max_size=1,
        operation_timeout_secs=3.0,
        acquire_timeout_secs=0.10,
    )
    holder: asyncio.Task[Any] | None = None
    try:
        holder = asyncio.create_task(
            acquire_connection.query(
                "WAITFOR DELAY '00:00:01'; SELECT 1 AS value"
            )
        )
        await _wait_for_request_count(
            sa_connection,
            acquire_app,
            1,
            task=holder,
        )
        with pytest.raises(OperationTimeoutError) as caught:
            await _collect_rows(
                acquire_connection.query_many(
                    "SELECT @P1 AS value",
                    [[7]],
                    concurrency=1,
                )
            )
        assert caught.value.query_index == 0
        assert (await holder).fetchone()["value"] == 1
        holder = None
    finally:
        await _settle_task(holder)
        await acquire_connection.disconnect()
    await _wait_for_zero_sessions(sa_connection, acquire_app)

    kill_app = unique_sql_name("strict_qmany_kill")
    kill_connection = _connection(
        sql_auth_config,
        application_name=kill_app,
        max_size=2,
        operation_timeout_secs=10.0,
    )
    kill_task: asyncio.Task[Any] | None = None
    try:
        kill_task = asyncio.create_task(
            _collect_rows(
                kill_connection.query_many(
                    "WAITFOR DELAY '00:00:05'; SELECT @P1 AS value",
                    [[0], [1]],
                    concurrency=2,
                )
            )
        )
        await _wait_for_request_count(
            sa_connection,
            kill_app,
            2,
            task=kill_task,
        )
        spids = await _application_request_spids(sa_connection, kill_app)
        assert len(spids) == 2
        await sa_connection.execute(f"KILL {spids[0]}")
        with pytest.raises(
            (SqlConnectionError, ProtocolError, SqlError, TlsError)
        ) as caught:
            await asyncio.wait_for(kill_task, timeout=3.0)
        assert caught.value.query_index in {0, 1}
        kill_task = None
        assert int(await scalar(kill_connection, "SELECT 1")) == 1
    finally:
        await _settle_task(kill_task)
        await kill_connection.disconnect()
    await _wait_for_zero_sessions(sa_connection, kill_app)

    lifecycle_state = getattr(fastmssql, "ConnectionLifecycleState")
    lifecycle_error = getattr(fastmssql, "ConnectionLifecycleError")
    graceful_app = unique_sql_name("strict_qmany_graceful")
    graceful_connection = _connection(
        sql_auth_config,
        application_name=graceful_app,
        max_size=2,
        operation_timeout_secs=3.0,
        shutdown_timeout_secs=3.0,
        force_timeout_secs=1.0,
    )
    graceful_query: asyncio.Task[Any] | None = None
    graceful_shutdown: asyncio.Task[Any] | None = None
    try:
        graceful_query = asyncio.create_task(
            _collect_rows(
                graceful_connection.query_many(
                    "WAITFOR DELAY '00:00:01'; SELECT @P1 AS value",
                    [[0], [1]],
                    concurrency=2,
                )
            )
        )
        await _wait_for_request_count(
            sa_connection,
            graceful_app,
            2,
            task=graceful_query,
        )
        graceful_shutdown = asyncio.create_task(
            graceful_connection.disconnect()
        )
        await _wait_for_lifecycle_state(
            graceful_connection,
            lifecycle_state.CLOSING,
        )
        rejected = graceful_connection.query_many(
            "SELECT @P1 AS value",
            [[9]],
            concurrency=1,
        )
        with pytest.raises(lifecycle_error) as caught:
            await rejected.__anext__()
        assert caught.value.state == "Closing"
        await rejected.aclose()

        graceful_results = await asyncio.wait_for(
            graceful_query,
            timeout=3.0,
        )
        graceful_query = None
        assert [stream.fetchone()["value"] for stream in graceful_results] == [
            0,
            1,
        ]
        assert await graceful_shutdown is True
        graceful_shutdown = None
    finally:
        await _settle_task(graceful_query)
        await _settle_task(graceful_shutdown)
        await graceful_connection.disconnect()
    await _wait_for_zero_sessions(sa_connection, graceful_app)

    shutdown_timeout = getattr(fastmssql, "ShutdownTimeoutError")
    force_app = unique_sql_name("strict_qmany_force")
    force_connection = _connection(
        sql_auth_config,
        application_name=force_app,
        max_size=1,
        operation_timeout_secs=10.0,
        shutdown_timeout_secs=0.10,
        force_timeout_secs=2.0,
    )
    forced_query: asyncio.Task[Any] | None = None
    try:
        forced_query = asyncio.create_task(
            _collect_rows(
                force_connection.query_many(
                    "WAITFOR DELAY '00:00:05'; SELECT @P1 AS value",
                    [[0]],
                    concurrency=1,
                )
            )
        )
        await _wait_for_request_count(
            sa_connection,
            force_app,
            1,
            task=forced_query,
        )
        with pytest.raises(shutdown_timeout) as shutdown_caught:
            await force_connection.disconnect()
        assert shutdown_caught.value.forced is True
        with pytest.raises(lifecycle_error) as query_caught:
            await asyncio.wait_for(forced_query, timeout=1.0)
        assert query_caught.value.query_index == 0
        forced_query = None
        assert int(await scalar(force_connection, "SELECT 1")) == 1
    finally:
        await _settle_task(forced_query)
        await force_connection.disconnect()
    await _wait_for_zero_sessions(sa_connection, force_app)


@case("QMANY-012")
@pytest.mark.asyncio
async def test_query_many_metrics_and_security_retirement(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("strict_qmany_metrics")
    connection = _connection(
        sql_auth_config,
        application_name=application_name,
        max_size=2,
        metrics=True,
    )
    try:
        before = await connection.operation_stats()
        ordinary = await _collect_rows(
            connection.query_many(
                "SELECT @P1 AS value",
                [[0], [1], [2]],
                concurrency=2,
                ordered=True,
            )
        )
        assert [stream.fetchone()["value"] for stream in ordinary] == [0, 1, 2]

        retired = await _collect_rows(
            connection.query_many(
                """
                DECLARE @physical_id NVARCHAR(36) = (
                    SELECT CONVERT(NVARCHAR(36), connection_id)
                    FROM sys.dm_exec_connections
                    WHERE session_id = @@SPID
                );
                EXECUTE AS USER = 'dbo';
                SELECT
                    @P1 AS value,
                    @physical_id AS connection_id;
                """,
                [[10], [11]],
                concurrency=2,
                ordered=True,
            )
        )
        retired_rows = [stream.fetchone() for stream in retired]
        assert [row["value"] for row in retired_rows] == [10, 11]
        retired_ids = {str(row["connection_id"]) for row in retired_rows}
        assert retired_ids

        after = await connection.operation_stats()
        assert after["schema_version"] == 2
        delta = operation_delta(before, after, "query")
        assert delta["started"] == delta["completed"] == 5
        assert {key: delta[key] for key in OUTCOME_KEYS} == zero_outcomes(
            succeeded=5
        )
        assert "query_many" not in after["operations"]

        replacement_id = await _physical_connection_id(connection)
        assert replacement_id not in retired_ids
    finally:
        await connection.disconnect()
    await _wait_for_zero_sessions(sa_connection, application_name)
