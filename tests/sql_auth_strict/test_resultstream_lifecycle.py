from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
import gc

import fastmssql
from fastmssql import (
    Connection,
    ConnectionLifecycleError,
    ConversionError,
    LifecycleConfig,
    OperationTimeoutError,
    PoolConfig,
    ShutdownTimeoutError,
    SqlError,
    SslConfig,
    TimeoutConfig,
    Transaction,
)
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import CleanupRegistry, quote_identifier, scalar
from sql_auth_strict.timeout_fixtures import wait_until


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


def _connection(
    config: SqlAuthConfig,
    *,
    application_name: str,
    operation_timeout: float | None = None,
    shutdown_timeout: float = 3.0,
    force_timeout: float = 1.0,
) -> Connection:
    return Connection(
        server=config.host,
        port=config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=1,
            min_idle=0,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=3,
            test_on_check_out=False,
            retry_connection=False,
        ),
        timeout_config=TimeoutConfig(
            acquire_timeout_secs=2.0,
            operation_timeout_secs=operation_timeout,
        ),
        lifecycle_config=LifecycleConfig(
            shutdown_timeout_secs=shutdown_timeout,
            force_timeout_secs=force_timeout,
        ),
        application_name=application_name,
    )


def _direct_transaction(
    config: SqlAuthConfig,
    *,
    application_name: str,
) -> Transaction:
    return Transaction(
        server=config.host,
        port=config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        application_name=application_name,
    )


async def _stream(
    owner,
    sql: str,
    params: list[object] | None = None,
    *,
    buffer_size: int,
):
    method = getattr(owner, "stream", None)
    assert callable(method), f"{type(owner).__name__}.stream() is missing"
    return await method(sql, params, buffer_size=buffer_size)


async def _batch(
    owner,
    sql: str,
    *,
    buffer_size: int,
):
    method = getattr(owner, "batch", None)
    assert callable(method), f"{type(owner).__name__}.batch() is missing"
    return await method(sql, buffer_size=buffer_size)


async def _rows(result_set) -> list[dict[str, object]]:
    return [row.to_dict() async for row in result_set]


async def _identity(owner) -> tuple[int, str]:
    row = (
        await owner.query(
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


async def _identity_exists(
    observer: Connection,
    identity: tuple[int, str],
) -> bool:
    session_id, connection_id = identity
    return (
        await scalar(
            observer,
            """
            SELECT COUNT_BIG(*)
            FROM sys.dm_exec_connections
            WHERE session_id = @P1
              AND CONVERT(NVARCHAR(36), connection_id) = @P2
            """,
            [session_id, connection_id],
        )
        == 1
    )


async def _wait_identity_absent(
    observer: Connection,
    identity: tuple[int, str],
    *,
    timeout: float = 4.0,
) -> None:
    async def absent() -> bool:
        return not await _identity_exists(observer, identity)

    await wait_until(absent, timeout=timeout)


async def _request_exists(
    observer: Connection,
    session_id: int,
) -> bool:
    return (
        await scalar(
            observer,
            """
            SELECT COUNT_BIG(*)
            FROM sys.dm_exec_requests
            WHERE session_id = @P1
            """,
            [session_id],
        )
        == 1
    )


async def _wait_request(
    observer: Connection,
    session_id: int,
    *,
    present: bool,
    timeout: float = 4.0,
) -> None:
    async def matches() -> bool:
        return await _request_exists(observer, session_id) is present

    await wait_until(matches, timeout=timeout)


async def _wait_pool_active(
    connection: Connection,
    expected: int,
    *,
    timeout: float = 4.0,
) -> dict[str, object]:
    observed: dict[str, object] = {}

    async def matches() -> bool:
        nonlocal observed
        observed = await connection.pool_stats()
        return observed["active_connections"] == expected

    await wait_until(matches, timeout=timeout)
    return observed


async def _wait_transaction_disconnected(
    transaction: Transaction,
    *,
    timeout: float = 4.0,
) -> None:
    async def disconnected() -> bool:
        return transaction.is_connected() is False

    await wait_until(disconnected, timeout=timeout)


async def _cancel(task: asyncio.Future | None) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def _error_signature(error: BaseException) -> tuple[object, ...]:
    return (
        type(error),
        getattr(error, "code", None),
        getattr(error, "state", None),
        getattr(error, "severity", None),
        getattr(error, "operation", None),
        getattr(error, "phase", None),
        getattr(error, "connection_discarded", None),
        getattr(error, "outcome_unknown", None),
    )


@case("RESULT-022")
@pytest.mark.asyncio
async def test_complete_aclose_retires_spid_before_pool_recovery(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    connection = _connection(
        sql_auth_config,
        application_name=unique_sql_name("result_022_aclose"),
    )
    response = None
    try:
        await connection.connect()
        original = await _identity(connection)
        response = await _batch(
            connection,
            """
            WITH numbers AS (
                SELECT 1 AS value
                UNION ALL
                SELECT value + 1 FROM numbers WHERE value < 10000
            )
            SELECT
                @@SPID AS session_id,
                CONVERT(NVARCHAR(36), connection_id) AS connection_id,
                value,
                REPLICATE('x', 1024) AS payload
            FROM sys.dm_exec_connections
            CROSS JOIN numbers
            WHERE session_id = @@SPID
            ORDER BY value
            OPTION (MAXRECURSION 0);
            """,
            buffer_size=1,
        )
        result_set = await response.__anext__()
        row = await result_set.__anext__()
        assert (row["session_id"], row["connection_id"]) == original
        assert row["value"] == 1
        assert len(row["payload"]) == 1024
        assert response.complete is False
        assert (await connection.pool_stats())["active_connections"] == 1
        assert await _identity_exists(sa_connection, original)

        await response.aclose()
        assert response.closed is True
        assert response.complete is False
        assert not await _identity_exists(sa_connection, original)
        await _wait_pool_active(connection, 0)

        replacement = await _identity(connection)
        assert replacement != original
        assert await scalar(connection, "SELECT 22") == 22
    finally:
        if response is not None and not response.closed:
            await response.aclose()
        await connection.disconnect()


@case("RESULT-023")
@pytest.mark.parametrize("drop_target", ("response", "active_result_set"))
@pytest.mark.asyncio
async def test_dropped_response_objects_retire_the_complete_physical_session(
    drop_target: str,
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    connection = _connection(
        sql_auth_config,
        application_name=unique_sql_name(f"result_023_{drop_target}"),
    )
    response = None
    result_set = None
    try:
        await connection.connect()
        original = await _identity(connection)
        response = await _batch(
            connection,
            """
            WITH numbers AS (
                SELECT 1 AS value
                UNION ALL
                SELECT value + 1 FROM numbers WHERE value < 10000
            )
            SELECT
                @@SPID AS session_id,
                CONVERT(NVARCHAR(36), connection_id) AS connection_id,
                value,
                REPLICATE('x', 1024) AS payload
            FROM sys.dm_exec_connections
            CROSS JOIN numbers
            WHERE session_id = @@SPID
            ORDER BY value
            OPTION (MAXRECURSION 0);
            """,
            buffer_size=1,
        )
        result_set = await response.__anext__()
        row = await result_set.__anext__()
        assert (row["session_id"], row["connection_id"]) == original
        assert row["value"] == 1
        assert len(row["payload"]) == 1024
        assert response.complete is False
        assert (await connection.pool_stats())["active_connections"] == 1
        assert await _identity_exists(sa_connection, original)

        if drop_target == "response":
            response = None
        else:
            result_set = None
        gc.collect()

        await _wait_identity_absent(sa_connection, original)
        await _wait_pool_active(connection, 0)

        if response is not None:
            with pytest.raises(RuntimeError):
                await response.finish()
            await response.aclose()
            assert response.complete is False
        result_set = None
        gc.collect()

        replacement = await _identity(connection)
        assert replacement != original
        assert await scalar(connection, "SELECT 23") == 23
    finally:
        result_set = None
        gc.collect()
        if response is not None and not response.closed:
            await response.aclose()
        await connection.disconnect()


@case("RESULT-024")
@pytest.mark.parametrize(
    "scenario",
    ("cancelled_receive", "queued_server_error", "conversion_failure"),
)
@pytest.mark.asyncio
async def test_cancelled_receives_and_terminal_errors_are_fail_closed(
    scenario: str,
    sql_auth_config: SqlAuthConfig,
    owner_connection: Connection,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name(f"result_024_{scenario}")
    connection = _connection(
        sql_auth_config,
        application_name=application_name,
    )
    response = None
    pending: asyncio.Future | None = None
    terminal_error_type: type[BaseException] | None = None
    try:
        await connection.connect()
        original = await _identity(connection)

        if scenario == "cancelled_receive":
            response = await _batch(
                connection,
                """
                WAITFOR DELAY '00:00:01';
                SELECT
                    @@SPID AS session_id,
                    CAST(24 AS INT) AS value;
                """,
                buffer_size=1,
            )
            pending = asyncio.ensure_future(response.__anext__())
            await _wait_request(sa_connection, original[0], present=True)
            assert pending.done() is False
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            pending = None

            result_set = await response.__anext__()
            assert await _rows(result_set) == [
                {"session_id": original[0], "value": 24}
            ]
            assert (await response.finish()).result_set_count == 1
            assert await _identity(connection) == original

            response = await _batch(
                connection,
                """
                WAITFOR DELAY '00:00:01';
                SELECT
                    @@SPID AS session_id,
                    CAST(240 AS INT) AS value;
                """,
                buffer_size=1,
            )
            abandoned_receive = response.__anext__()
            await asyncio.sleep(0)
            abandoned_receive.close()
            abandoned_receive = None
            gc.collect()
            result_set = await response.__anext__()
            assert await _rows(result_set) == [
                {"session_id": original[0], "value": 240}
            ]
            assert (await response.finish()).result_set_count == 1
            assert await _identity(connection) == original

            response = await _batch(
                connection,
                """
                    SELECT
                        @@SPID AS session_id,
                        CAST(0 AS INT) AS id,
                        CAST(24 AS INT) AS value;
                    RAISERROR(
                        N'fastmssql result receive boundary',
                        10,
                        1
                    ) WITH NOWAIT;
                    WAITFOR DELAY '00:00:03';
                SELECT
                    @@SPID AS session_id,
                    CAST(1 AS INT) AS id,
                    CAST(24 AS INT) AS value;
                """,
                buffer_size=1,
            )
            result_set = await response.__anext__()
            assert (await result_set.__anext__()).to_dict() == {
                "session_id": original[0],
                "id": 0,
                "value": 24,
            }
            await _wait_request(sa_connection, original[0], present=True)
            pending = asyncio.ensure_future(result_set.__anext__())
            await asyncio.sleep(0)
            assert pending.done() is False
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            pending = None

            with pytest.raises(StopAsyncIteration):
                await result_set.__anext__()
            second_result_set = await response.__anext__()
            assert (await second_result_set.__anext__()).to_dict() == {
                "session_id": original[0],
                "id": 1,
                "value": 24,
            }
            assert await _rows(second_result_set) == []
            assert (await response.finish()).result_set_count == 2

            response = await _batch(
                connection,
                """
                WAITFOR DELAY '00:00:05';
                SELECT CAST(24 AS INT) AS unreachable_value;
                """,
                buffer_size=1,
            )
            pending = asyncio.ensure_future(response.__anext__())
            await _wait_request(sa_connection, original[0], present=True)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            pending = None
            response = None
            gc.collect()
            await _wait_identity_absent(sa_connection, original)
            await _wait_pool_active(connection, 0)
            replacement = await _identity(connection)
            assert replacement != original
            assert await scalar(connection, "SELECT 24") == 24
            return

        if scenario == "queued_server_error":
            before = await connection.pool_stats()
            response = await _batch(
                connection,
                """
                SELECT
                    @@SPID AS session_id,
                    CONVERT(NVARCHAR(36), connection_id) AS connection_id,
                    CAST(24 AS INT) AS value
                FROM sys.dm_exec_connections
                WHERE session_id = @@SPID;
                WAITFOR DELAY '00:00:01';
                RAISERROR(N'fastmssql result lifecycle server error', 16, 1);
                """,
                buffer_size=1,
            )
            result_set = await response.__anext__()
            await _wait_request(sa_connection, original[0], present=False)
            active = await connection.pool_stats()
            assert active["active_connections"] == 1

            row = await result_set.__anext__()
            assert (
                row["session_id"],
                row["connection_id"],
                row["value"],
            ) == (*original, 24)
            with pytest.raises(SqlError) as first:
                await result_set.__anext__()
            error = first.value
            terminal_error_type = SqlError
            assert error.code == 50000
            assert error.severity == 16
            assert error.connection_discarded is False
            with pytest.raises(SqlError) as repeated:
                await response.finish()
            assert _error_signature(repeated.value) == _error_signature(error)

            await _wait_pool_active(connection, 0)
            after = await connection.pool_stats()
            assert (
                after["connections_closed_broken"]
                == before["connections_closed_broken"]
            )
            assert await _identity(connection) == original
            assert await scalar(connection, "SELECT 24") == 24
            return

        before = await connection.pool_stats()
        response = await _batch(
            connection,
            """
            SELECT
                CAST(922337203685477.5807 AS MONEY) AS exact_money;
            SELECT CAST(24 AS INT) AS read_ahead_marker;
            """,
            buffer_size=4,
        )
        result_set = await response.__anext__()
        await _wait_request(sa_connection, original[0], present=False)
        assert (await connection.pool_stats())["active_connections"] == 1

        with pytest.raises(ConversionError) as captured:
            await result_set.__anext__()
        error = captured.value
        terminal_error_type = ConversionError
        assert error.wire_sent is True
        assert error.connection_discarded is True
        assert error.outcome_unknown is False
        with pytest.raises(ConversionError) as repeated:
            await response.finish()
        assert _error_signature(repeated.value) == _error_signature(error)

        await _wait_pool_active(connection, 0)
        after = await connection.pool_stats()
        assert (
            after["connections_closed_broken"]
            == before["connections_closed_broken"] + 1
        )
        replacement = await _identity(connection)
        assert replacement != original
        assert await scalar(connection, "SELECT 24") == 24
    finally:
        await _cancel(pending)
        if response is not None and not response.closed:
            if scenario == "cancelled_receive":
                response = None
                gc.collect()
            elif terminal_error_type is None:
                await response.aclose()
            else:
                with pytest.raises(terminal_error_type):
                    await response.aclose()
        await connection.disconnect()


@case("RESULT-026")
@pytest.mark.parametrize("shutdown_mode", ("graceful", "forced"))
@pytest.mark.asyncio
async def test_result_stream_participates_in_graceful_and_forced_shutdown(
    shutdown_mode: str,
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    forced = shutdown_mode == "forced"
    connection = _connection(
        sql_auth_config,
        application_name=unique_sql_name(f"result_026_{shutdown_mode}"),
        shutdown_timeout=0.2 if forced else 3.0,
        force_timeout=2.0,
    )
    response = None
    shutdown: asyncio.Task | None = None
    try:
        await connection.connect()
        original = await _identity(connection)
        response = await _batch(
            connection,
            (
                "WAITFOR DELAY '00:00:05'; "
                if forced
                else "WAITFOR DELAY '00:00:01'; "
            )
            + "SELECT CAST(26 AS INT) AS value;",
            buffer_size=1,
        )
        await _wait_request(sa_connection, original[0], present=True)
        shutdown = asyncio.create_task(connection.disconnect())

        async def closing() -> bool:
            return connection.lifecycle_state == fastmssql.ConnectionLifecycleState.CLOSING

        await wait_until(closing, timeout=2.0)
        assert shutdown.done() is False

        if not forced:
            summary = await response.finish()
            assert summary.result_set_count == 1
            assert await shutdown is True
            shutdown = None
            assert response.complete is True
            await _wait_identity_absent(sa_connection, original)
            return

        with pytest.raises(ShutdownTimeoutError) as shutdown_captured:
            await asyncio.wait_for(shutdown, timeout=4.0)
        shutdown = None
        shutdown_error = shutdown_captured.value
        assert shutdown_error.operation == "disconnect"
        assert shutdown_error.phase == "shutdown"
        assert shutdown_error.forced is True
        assert shutdown_error.connection_discarded is True
        assert shutdown_error.outcome_unknown is False
        assert shutdown_error.active_operations_at_timeout == 1
        assert shutdown_error.active_transactions_at_timeout == 0
        assert shutdown_error.force_completed is True

        with pytest.raises(ConnectionLifecycleError) as response_captured:
            await response.finish()
        response_error = response_captured.value
        assert response_error.operation == "query_batch"
        assert response_error.phase == "shutdown"
        assert response_error.forced is True
        assert response_error.connection_discarded is True
        assert response_error.outcome_unknown is True
        await _wait_identity_absent(sa_connection, original)

        assert await scalar(connection, "SELECT 26") == 26
        assert await connection.disconnect() is True
    finally:
        await _cancel(shutdown)
        if response is not None and not response.closed:
            await response.aclose()
        await connection.disconnect()


async def _new_transaction(
    mode: str,
    parent: Connection,
    config: SqlAuthConfig,
    *,
    application_name: str,
) -> Transaction:
    if mode == "pooled":
        return parent.transaction()
    return _direct_transaction(config, application_name=application_name)


async def _assert_commit_rejected(transaction: Transaction) -> None:
    with pytest.raises(RuntimeError):
        await transaction.commit()


@case("RESULT-027")
@pytest.mark.parametrize("mode", ("pooled", "direct"))
@pytest.mark.asyncio
async def test_transaction_stream_owns_session_until_terminal_disposition(
    mode: str,
    sql_auth_config: SqlAuthConfig,
    owner_connection: Connection,
    sa_connection: Connection,
    cleanup_registry: CleanupRegistry,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name(f"result_027_{mode}")
    parent = _connection(
        sql_auth_config,
        application_name=application_name,
    )
    table = quote_identifier(unique_sql_name(f"result_027_rows_{mode}"))
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT NOT NULL PRIMARY KEY)"
    )
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    if mode == "pooled":
        await parent.connect()

    transaction = await _new_transaction(
        mode,
        parent,
        sql_auth_config,
        application_name=application_name,
    )
    response = None
    competing: asyncio.Task | None = None
    try:
        await transaction.begin()
        response = await _stream(
            transaction,
            "SELECT @@TRANCOUNT AS transaction_count, @P1 AS marker",
            [27],
            buffer_size=1,
        )
        result_sets = [
            await _rows(result_set)
            async for result_set in response
        ]
        assert result_sets == [[{"transaction_count": 1, "marker": 27}]]
        assert response.complete is True
        assert transaction.is_connected() is True
        assert await scalar(transaction, "SELECT @@TRANCOUNT") == 1

        response = await _batch(
            transaction,
            """
            WITH numbers AS (
                SELECT 1 AS value
                UNION ALL
                SELECT value + 1 FROM numbers WHERE value < 256
            )
            SELECT value FROM numbers OPTION (MAXRECURSION 0);
            SELECT CAST(27 AS INT) AS retained_value;
            """,
            buffer_size=2,
        )
        first = await response.__anext__()
        await first.aclose()
        second = await response.__anext__()
        assert await _rows(second) == [{"retained_value": 27}]
        assert (await response.finish()).result_set_count == 2
        assert transaction.is_connected() is True

        identity = await _identity(transaction)
        response = await _batch(
            transaction,
            """
            WITH numbers AS (
                SELECT 1 AS value
                UNION ALL
                SELECT value + 1 FROM numbers WHERE value < 10000
            )
            SELECT
                value,
                REPLICATE('x', 1024) AS payload
            FROM numbers
            ORDER BY value
            OPTION (MAXRECURSION 0);
            """,
            buffer_size=1,
        )
        first = await response.__anext__()
        first_row = await first.__anext__()
        assert first_row["value"] == 1
        assert len(first_row["payload"]) == 1024
        await _wait_request(sa_connection, identity[0], present=True)
        competing = asyncio.create_task(
            transaction.execute(f"INSERT INTO {table} (id) VALUES (1)")
        )
        await asyncio.sleep(0)
        assert competing.done() is False
        assert (await response.finish()).result_set_count == 1
        assert await competing == 1
        competing = None
        assert await scalar(transaction, "SELECT @@TRANCOUNT") == 1
        await transaction.rollback()
    finally:
        await _cancel(competing)
        if response is not None and not response.closed:
            await response.aclose()
        await transaction.close()

    assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == 0

    retirement = await _new_transaction(
        mode,
        parent,
        sql_auth_config,
        application_name=application_name,
    )
    try:
        await retirement.begin()
        response = await _batch(
            retirement,
            """
            EXECUTE AS USER = 'dbo';
            SELECT @@SPID AS retired_session_id;
            REVERT;
            """,
            buffer_size=1,
        )
        rows = [
            row
            async for result_set in response
            for row in await _rows(result_set)
        ]
        assert len(rows) == 1
        assert response.complete is True
        await _wait_transaction_disconnected(retirement)
        await _assert_commit_rejected(retirement)
    finally:
        await retirement.close()

    for abandonment in ("close", "drop"):
        abandoned = await _new_transaction(
            mode,
            parent,
            sql_auth_config,
            application_name=application_name,
        )
        response = None
        active_set = None
        try:
            await abandoned.begin()
            row_id = 2 if abandonment == "close" else 3
            await abandoned.execute(
                f"INSERT INTO {table} (id) VALUES (@P1)",
                [row_id],
            )
            identity = await _identity(abandoned)
            response = await _batch(
                abandoned,
                """
                WITH numbers AS (
                    SELECT 1 AS value
                    UNION ALL
                    SELECT value + 1 FROM numbers WHERE value < 10000
                )
                SELECT
                    value,
                    REPLICATE('x', 1024) AS payload
                FROM numbers
                ORDER BY value
                OPTION (MAXRECURSION 0);
                """,
                buffer_size=1,
            )
            active_set = await response.__anext__()
            first_row = await active_set.__anext__()
            assert first_row["value"] == 1
            assert len(first_row["payload"]) == 1024
            assert await _identity_exists(sa_connection, identity)

            if abandonment == "close":
                await response.aclose()
            else:
                response = None
                gc.collect()
            await _wait_identity_absent(sa_connection, identity)
            await _wait_transaction_disconnected(abandoned)
            await _assert_commit_rejected(abandoned)
        finally:
            active_set = None
            gc.collect()
            if response is not None and not response.closed:
                await response.aclose()
            await abandoned.close()

        assert (
            await scalar(
                owner_connection,
                f"SELECT COUNT(*) FROM {table} WHERE id = @P1",
                [row_id],
            )
            == 0
        )

    if mode == "pooled":
        assert await scalar(parent, "SELECT 27") == 27
    await parent.disconnect()


@case("RESULT-031")
@pytest.mark.parametrize(
    "scenario",
    ("ack_before_release", "full_queue_timeout", "conversion_after_eof"),
)
@pytest.mark.asyncio
async def test_consumer_ack_precedes_release_and_preserves_terminal_failure(
    scenario: str,
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    operation_timeout = 0.75 if scenario == "full_queue_timeout" else None
    connection = _connection(
        sql_auth_config,
        application_name=unique_sql_name(f"result_031_{scenario}"),
        operation_timeout=operation_timeout,
    )
    response = None
    terminal_error_type: type[BaseException] | None = None
    try:
        await connection.connect()
        original = await _identity(connection)
        before = await connection.pool_stats()

        if scenario == "ack_before_release":
            response = await _batch(
                connection,
                """
                SELECT value
                FROM (VALUES (1), (2), (3), (4)) AS source(value)
                ORDER BY value;
                WAITFOR DELAY '00:00:01';
                """,
                buffer_size=4,
            )
            result_set = await response.__anext__()
            await _wait_request(sa_connection, original[0], present=False)
            assert (await connection.pool_stats())["active_connections"] == 1

            assert [
                row["value"]
                async for row in result_set
            ] == [1, 2, 3, 4]
            summary = await response.finish()
            assert summary.result_set_count == 1
            await _wait_pool_active(connection, 0)
            assert await _identity(connection) == original
            return

        if scenario == "full_queue_timeout":
            response = await _batch(
                connection,
                """
                WITH numbers AS (
                    SELECT 1 AS value
                    UNION ALL
                    SELECT value + 1 FROM numbers WHERE value < 10000
                )
                SELECT
                    value,
                    REPLICATE('x', 1024) AS payload
                FROM numbers
                ORDER BY value
                OPTION (MAXRECURSION 0);
                """,
                buffer_size=4,
            )
            result_set = await response.__anext__()
            await _wait_request(sa_connection, original[0], present=True)
            await _wait_identity_absent(sa_connection, original)
            await _wait_pool_active(connection, 0)

            observed = []
            for _ in range(4):
                observed.append((await result_set.__anext__())["value"])
            assert observed == [1, 2, 3, 4]
            with pytest.raises(OperationTimeoutError) as first:
                await result_set.__anext__()
            error = first.value
            terminal_error_type = OperationTimeoutError
            assert error.operation == "query_batch"
            assert error.phase == "operation"
            assert error.timeout_seconds == pytest.approx(0.75)
            assert error.connection_discarded is True
            assert error.outcome_unknown is True
            with pytest.raises(OperationTimeoutError) as repeated:
                await response.finish()
            assert _error_signature(repeated.value) == _error_signature(error)

            after = await connection.pool_stats()
            assert (
                after["connections_closed_broken"]
                == before["connections_closed_broken"] + 1
            )
            replacement = await _identity(connection)
            assert replacement != original
            assert await scalar(connection, "SELECT 31") == 31
            return

        response = await _batch(
            connection,
            "SELECT CAST(922337203685477.5807 AS MONEY) AS exact_money;",
            buffer_size=4,
        )
        result_set = await response.__anext__()
        await _wait_request(sa_connection, original[0], present=False)
        assert (await connection.pool_stats())["active_connections"] == 1

        with pytest.raises(ConversionError) as first:
            await result_set.__anext__()
        error = first.value
        terminal_error_type = ConversionError
        assert error.wire_sent is True
        assert error.connection_discarded is True
        assert error.outcome_unknown is False
        with pytest.raises(ConversionError) as repeated:
            await response.finish()
        assert _error_signature(repeated.value) == _error_signature(error)
        await _wait_pool_active(connection, 0)

        after = await connection.pool_stats()
        assert (
            after["connections_closed_broken"]
            == before["connections_closed_broken"] + 1
        )
        replacement = await _identity(connection)
        assert replacement != original
        assert await scalar(connection, "SELECT 31") == 31
    finally:
        if response is not None and not response.closed:
            if terminal_error_type is None:
                await response.aclose()
            else:
                with pytest.raises(terminal_error_type):
                    await response.aclose()
        await connection.disconnect()
