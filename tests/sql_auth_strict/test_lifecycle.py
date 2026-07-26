from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
import time
from typing import Any

import fastmssql
from fastmssql import (
    CommitOutcomeUnknown,
    Connection,
    PoolConfig,
    QueryStream,
    SqlConnectionError,
    SslConfig,
)
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import CleanupRegistry, quote_identifier, scalar
from sql_auth_strict.tcp_fault_proxy import DownstreamGateProxy
from sql_auth_strict.timeout_fixtures import wait_until


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


def lifecycle_api():
    names = (
        "LifecycleConfig",
        "ConnectionLifecycleState",
        "ConnectionLifecycleError",
        "ShutdownTimeoutError",
    )
    missing = [name for name in names if not hasattr(fastmssql, name)]
    assert not missing, f"missing lifecycle API: {missing}"
    return tuple(getattr(fastmssql, name) for name in names)


def lifecycle_connection(
    config: SqlAuthConfig,
    *,
    application_name: str,
    max_size: int = 2,
    shutdown_timeout: float = 3.0,
    force_timeout: float = 1.0,
    host: str | None = None,
    port: int | None = None,
) -> Connection:
    LifecycleConfig, _, _, _ = lifecycle_api()
    return Connection(
        server=host or config.host,
        port=port or config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=max_size,
            min_idle=0,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=3,
            retry_connection=False,
        ),
        lifecycle_config=LifecycleConfig(
            shutdown_timeout_secs=shutdown_timeout,
            force_timeout_secs=force_timeout,
        ),
        application_name=application_name,
    )


async def application_requests(
    observer: Connection,
    application_name: str,
) -> list:
    return (
        await observer.query(
            """
            SELECT request.session_id, request.status, request.command
            FROM sys.dm_exec_requests AS request
            INNER JOIN sys.dm_exec_sessions AS session
                ON session.session_id = request.session_id
            WHERE session.program_name = @P1
              AND request.session_id <> @@SPID
            ORDER BY request.session_id
            """,
            [application_name],
        )
    ).rows()


async def application_sessions(
    observer: Connection,
    application_name: str,
) -> list:
    return (
        await observer.query(
            """
            SELECT session_id, status, open_transaction_count
            FROM sys.dm_exec_sessions
            WHERE program_name = @P1
              AND session_id <> @@SPID
            ORDER BY session_id
            """,
            [application_name],
        )
    ).rows()


async def application_transaction_count(
    observer: Connection,
    application_name: str,
) -> int:
    return int(
        await scalar(
            observer,
            """
            SELECT COUNT(*)
            FROM sys.dm_tran_session_transactions AS transaction_session
            INNER JOIN sys.dm_exec_sessions AS session
                ON session.session_id = transaction_session.session_id
            WHERE session.program_name = @P1
              AND session.session_id <> @@SPID
            """,
            [application_name],
        )
    )


async def wait_for_state(
    connection: Connection,
    expected: Any,
    *,
    timeout: float = 3.0,
) -> None:
    async def state_matches() -> bool:
        return connection.lifecycle_state == expected

    await wait_until(state_matches, timeout=timeout)


async def wait_for_one_visible_request(
    observer: Connection,
    application_name: str,
) -> None:
    async def one_request() -> bool:
        return len(
            await application_requests(observer, application_name)
        ) == 1

    await wait_until(one_request, timeout=3.0)


async def wait_for_zero_sessions(
    observer: Connection,
    application_name: str,
) -> None:
    async def no_sessions() -> bool:
        return not await application_sessions(observer, application_name)

    await wait_until(no_sessions, timeout=5.0)


async def start_visible_waitfor(
    connection: Connection,
    observer: Connection,
    application_name: str,
    *,
    seconds: int = 1,
    slot: int = 1,
) -> asyncio.Task:
    task = asyncio.ensure_future(
        connection.query(
            f"WAITFOR DELAY '00:00:{seconds:02d}'; SELECT @P1 AS slot",
            [slot],
        )
    )
    await wait_for_one_visible_request(observer, application_name)
    return task


async def create_table(
    cleanup_registry: CleanupRegistry,
    unique_sql_name: Callable[[str], str],
    prefix: str,
    definition: str,
) -> str:
    table = f"[dbo].{quote_identifier(unique_sql_name(prefix))}"
    await cleanup_registry.connection.execute(
        f"CREATE TABLE {table} ({definition})"
    )
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    return table


async def cancel_and_wait(task: asyncio.Task | None) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError):
        await task


@case("LIFE-001")
@pytest.mark.asyncio
async def test_public_lifecycle_api_on_real_sql_auth(
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
) -> None:
    (
        LifecycleConfig,
        ConnectionLifecycleState,
        ConnectionLifecycleError,
        ShutdownTimeoutError,
    ) = lifecycle_api()
    config = LifecycleConfig(
        shutdown_timeout_secs=2.0,
        force_timeout_secs=1.0,
    )
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=unique_sql_name("strict_lifecycle_api"),
        shutdown_timeout=config.shutdown_timeout_secs,
        force_timeout=config.force_timeout_secs,
    )
    assert connection.lifecycle_state == ConnectionLifecycleState.OPEN
    assert issubclass(ConnectionLifecycleError, SqlConnectionError)
    assert issubclass(ShutdownTimeoutError, ConnectionLifecycleError)
    assert await connection.connect() is True
    assert await connection.disconnect() is True


@case("LIFE-002")
@pytest.mark.asyncio
async def test_lifecycle_state_is_pool_independent_and_reconnects(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    _, ConnectionLifecycleState, _, _ = lifecycle_api()
    application_name = unique_sql_name("strict_lifecycle_state")
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
    )
    try:
        assert connection.lifecycle_state == ConnectionLifecycleState.OPEN
        assert await connection.is_connected() is False
        assert await connection.disconnect() is False
        assert connection.lifecycle_state == ConnectionLifecycleState.CLOSED
        assert await scalar(connection, "SELECT 1") == 1
        assert connection.lifecycle_state == ConnectionLifecycleState.OPEN
        assert await connection.disconnect() is True
        assert connection.lifecycle_state == ConnectionLifecycleState.CLOSED
    finally:
        await connection.disconnect()
    await wait_for_zero_sessions(sa_connection, application_name)


@case("LIFE-003")
@pytest.mark.asyncio
async def test_disconnect_waits_for_admitted_query(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    _, ConnectionLifecycleState, _, _ = lifecycle_api()
    application_name = unique_sql_name("strict_lifecycle_grace")
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
    )
    query: asyncio.Task | None = None
    shutdown: asyncio.Task | None = None
    try:
        await connection.connect()
        query = asyncio.ensure_future(
            connection.simple_query(
                "WAITFOR DELAY '00:00:02'; SELECT 42 AS answer"
            )
        )
        await wait_for_one_visible_request(sa_connection, application_name)
        started = time.monotonic()
        shutdown = asyncio.create_task(connection.disconnect())
        await wait_for_state(
            connection,
            ConnectionLifecycleState.CLOSING,
        )
        await asyncio.sleep(0.1)
        assert shutdown.done() is False
        assert (await query).fetchone()["answer"] == 42
        assert await shutdown is True
        assert time.monotonic() - started >= 1.0
        assert connection.lifecycle_state == ConnectionLifecycleState.CLOSED
    finally:
        await cancel_and_wait(query)
        await cancel_and_wait(shutdown)
        await connection.disconnect()
    await wait_for_zero_sessions(sa_connection, application_name)


@case("LIFE-004")
@pytest.mark.asyncio
async def test_closing_rejects_new_sql_then_closed_reopens(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    (
        _,
        ConnectionLifecycleState,
        ConnectionLifecycleError,
        _,
    ) = lifecycle_api()
    application_name = unique_sql_name("strict_lifecycle_barrier")
    table = await create_table(
        cleanup_registry,
        unique_sql_name,
        "strict_lifecycle_barrier_rows",
        "id INT NOT NULL PRIMARY KEY",
    )
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
    )
    holder: asyncio.Task | None = None
    shutdown: asyncio.Task | None = None
    try:
        holder = await start_visible_waitfor(
            connection,
            sa_connection,
            application_name,
        )
        shutdown = asyncio.create_task(connection.disconnect())
        await wait_for_state(
            connection,
            ConnectionLifecycleState.CLOSING,
        )
        with pytest.raises(ConnectionLifecycleError) as captured:
            await connection.execute(
                f"INSERT INTO {table} (id) VALUES (1)"
            )
        error = captured.value
        assert error.operation == "execute"
        assert error.phase == "shutdown"
        assert error.state == "Closing"
        assert error.retryable is True
        assert error.connection_discarded is False
        assert error.outcome_unknown is False
        assert error.forced is False
        assert await scalar(
            sa_connection,
            f"SELECT COUNT(*) FROM {table}",
        ) == 0
        assert (await holder).fetchone()["slot"] == 1
        assert await shutdown is True
        assert await scalar(connection, "SELECT 9") == 9
        assert connection.lifecycle_state == ConnectionLifecycleState.OPEN
    finally:
        await cancel_and_wait(holder)
        await cancel_and_wait(shutdown)
        await connection.disconnect()
    await wait_for_zero_sessions(sa_connection, application_name)


@case("LIFE-005")
@pytest.mark.asyncio
async def test_concurrent_shutdown_is_coalesced_and_waiter_safe(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    _, ConnectionLifecycleState, _, _ = lifecycle_api()
    application_name = unique_sql_name("strict_lifecycle_coalesced")
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
    )
    holder: asyncio.Task | None = None
    first: asyncio.Task | None = None
    second: asyncio.Task | None = None
    try:
        holder = await start_visible_waitfor(
            connection,
            sa_connection,
            application_name,
        )
        first = asyncio.create_task(connection.disconnect())
        second = asyncio.create_task(connection.disconnect())
        await wait_for_state(
            connection,
            ConnectionLifecycleState.CLOSING,
        )
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await holder).fetchone()["slot"] == 1
        assert await second is True
        assert connection.lifecycle_state == ConnectionLifecycleState.CLOSED
    finally:
        await cancel_and_wait(holder)
        await cancel_and_wait(first)
        await cancel_and_wait(second)
        await connection.disconnect()
    await wait_for_zero_sessions(sa_connection, application_name)


@case("LIFE-006")
@pytest.mark.asyncio
async def test_admitted_pool_waiter_is_inside_shutdown_barrier(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    (
        _,
        ConnectionLifecycleState,
        ConnectionLifecycleError,
        _,
    ) = lifecycle_api()
    application_name = unique_sql_name("strict_lifecycle_waiter")
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
        max_size=1,
    )
    holder: asyncio.Task | None = None
    waiting: asyncio.Task | None = None
    shutdown: asyncio.Task | None = None
    try:
        holder = await start_visible_waitfor(
            connection,
            sa_connection,
            application_name,
            seconds=1,
            slot=1,
        )
        waiter_entered = asyncio.Event()

        async def wait_for_slot() -> QueryStream:
            waiter_entered.set()
            return await connection.query("SELECT 2 AS slot")

        waiting = asyncio.create_task(wait_for_slot())
        await waiter_entered.wait()
        await asyncio.sleep(0)
        assert waiting.done() is False
        shutdown = asyncio.create_task(connection.disconnect())
        await wait_for_state(
            connection,
            ConnectionLifecycleState.CLOSING,
        )
        with pytest.raises(ConnectionLifecycleError):
            await connection.query("SELECT 3 AS late_slot")
        assert shutdown.done() is False
        assert (await holder).fetchone()["slot"] == 1
        assert (await waiting).fetchone()["slot"] == 2
        assert await shutdown is True
        assert connection.lifecycle_state == ConnectionLifecycleState.CLOSED
    finally:
        await cancel_and_wait(holder)
        await cancel_and_wait(waiting)
        await cancel_and_wait(shutdown)
        await connection.disconnect()
    await wait_for_zero_sessions(sa_connection, application_name)


@case("LIFE-007")
@pytest.mark.asyncio
async def test_graceful_shutdown_waits_for_pooled_transaction_commit(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    _, ConnectionLifecycleState, _, _ = lifecycle_api()
    application_name = unique_sql_name("strict_lifecycle_commit")
    table = await create_table(
        cleanup_registry,
        unique_sql_name,
        "strict_lifecycle_commit_rows",
        "id INT NOT NULL PRIMARY KEY",
    )
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
        max_size=1,
    )
    transaction = connection.transaction()
    shutdown: asyncio.Task | None = None
    try:
        await transaction.begin()
        await transaction.execute(
            f"INSERT INTO {table} (id) VALUES (@P1)",
            [7],
        )
        assert (
            await application_transaction_count(
                sa_connection,
                application_name,
            )
            == 1
        )

        shutdown = asyncio.create_task(connection.disconnect())
        await wait_for_state(
            connection,
            ConnectionLifecycleState.CLOSING,
        )
        await asyncio.sleep(0.1)
        assert shutdown.done() is False

        await transaction.commit()
        assert await shutdown is True
        assert transaction.is_connected() is False
        assert connection.lifecycle_state == ConnectionLifecycleState.CLOSED
        assert await scalar(
            sa_connection,
            f"SELECT COUNT(*) FROM {table} WHERE id = @P1",
            [7],
        ) == 1
    finally:
        await cancel_and_wait(shutdown)
        await transaction.close()
        await connection.disconnect()
    await wait_for_zero_sessions(sa_connection, application_name)


@case("LIFE-008")
@pytest.mark.parametrize("settlement", ("rollback", "close"))
@pytest.mark.asyncio
async def test_graceful_shutdown_allows_transaction_cleanup_once(
    settlement: str,
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    _, ConnectionLifecycleState, _, _ = lifecycle_api()
    application_name = unique_sql_name(
        f"strict_lifecycle_{settlement}"
    )
    table = await create_table(
        cleanup_registry,
        unique_sql_name,
        f"strict_lifecycle_{settlement}_rows",
        "id INT NOT NULL PRIMARY KEY",
    )
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
        max_size=1,
    )
    transaction = connection.transaction()
    shutdown: asyncio.Task | None = None
    try:
        await transaction.begin()
        await transaction.execute(
            f"INSERT INTO {table} (id) VALUES (@P1)",
            [8],
        )
        assert (
            await application_transaction_count(
                sa_connection,
                application_name,
            )
            == 1
        )

        shutdown = asyncio.create_task(connection.disconnect())
        await wait_for_state(
            connection,
            ConnectionLifecycleState.CLOSING,
        )
        await asyncio.sleep(0.1)
        assert shutdown.done() is False

        if settlement == "rollback":
            await transaction.rollback()
        else:
            await transaction.close()
        assert await shutdown is True
        assert transaction.is_connected() is False
        assert connection.lifecycle_state == ConnectionLifecycleState.CLOSED
        assert await scalar(
            sa_connection,
            f"SELECT COUNT(*) FROM {table} WHERE id = @P1",
            [8],
        ) == 0

        # Repeated cleanup must remain idempotent and cannot release the
        # lifecycle permit a second time.
        await transaction.close()
        await transaction.close()
    finally:
        await cancel_and_wait(shutdown)
        await transaction.close()
        await connection.disconnect()
    await wait_for_zero_sessions(sa_connection, application_name)


@case("LIFE-009")
@pytest.mark.asyncio
async def test_shutdown_deadline_forces_query_and_recovers_generation(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    (
        _,
        ConnectionLifecycleState,
        ConnectionLifecycleError,
        ShutdownTimeoutError,
    ) = lifecycle_api()
    application_name = unique_sql_name("strict_lifecycle_force_query")
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
        max_size=1,
        shutdown_timeout=0.2,
        force_timeout=2.0,
    )
    query: asyncio.Task | None = None
    shutdown: asyncio.Task | None = None
    try:
        query = await start_visible_waitfor(
            connection,
            sa_connection,
            application_name,
            seconds=5,
            slot=9,
        )
        shutdown = asyncio.create_task(connection.disconnect())

        with pytest.raises(ShutdownTimeoutError) as shutdown_captured:
            await asyncio.wait_for(shutdown, timeout=4.0)
        shutdown = None
        shutdown_error = shutdown_captured.value
        assert shutdown_error.operation == "disconnect"
        assert shutdown_error.phase == "shutdown"
        assert shutdown_error.state == "Closed"
        assert shutdown_error.forced is True
        assert shutdown_error.retryable is False
        assert shutdown_error.connection_discarded is True
        assert shutdown_error.outcome_unknown is False
        assert shutdown_error.shutdown_timeout_seconds == 0.2
        assert shutdown_error.force_timeout_seconds == 2.0
        assert shutdown_error.active_operations_at_timeout == 1
        assert shutdown_error.active_transactions_at_timeout == 0
        assert shutdown_error.force_completed is True

        with pytest.raises(ConnectionLifecycleError) as query_captured:
            await asyncio.wait_for(query, timeout=1.0)
        query = None
        query_error = query_captured.value
        assert query_error.operation == "query"
        assert query_error.phase == "shutdown"
        assert query_error.state in {"Closing", "Closed"}
        assert query_error.retryable is False
        assert query_error.connection_discarded is True
        assert query_error.outcome_unknown is True
        assert query_error.forced is True
        assert isinstance(query_error.generation, int)
        assert connection.lifecycle_state == ConnectionLifecycleState.CLOSED

        assert await scalar(connection, "SELECT 909") == 909
        assert connection.lifecycle_state == ConnectionLifecycleState.OPEN
        assert await connection.disconnect() is True
    finally:
        await cancel_and_wait(query)
        await cancel_and_wait(shutdown)
        await connection.disconnect()
    await wait_for_zero_sessions(sa_connection, application_name)


@case("LIFE-010")
@pytest.mark.asyncio
async def test_forced_write_has_unknown_outcome_and_is_not_retried(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    (
        _,
        ConnectionLifecycleState,
        ConnectionLifecycleError,
        ShutdownTimeoutError,
    ) = lifecycle_api()
    application_name = unique_sql_name("strict_lifecycle_force_write")
    table = await create_table(
        cleanup_registry,
        unique_sql_name,
        "strict_lifecycle_force_write_rows",
        "business_key INT NOT NULL PRIMARY KEY, attempt INT NOT NULL",
    )
    proxy = DownstreamGateProxy(
        sql_auth_config.host,
        sql_auth_config.port,
    )
    await proxy.start()
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
        max_size=1,
        shutdown_timeout=0.2,
        force_timeout=2.0,
        host=proxy.host,
        port=proxy.port,
    )
    write: asyncio.Task | None = None
    shutdown: asyncio.Task | None = None
    try:
        assert await connection.connect() is True
        assert await scalar(connection, "SELECT 10") == 10
        assert proxy.accepted_connections == 1
        proxy.pause_downstream()
        proxy.expect_client_disconnect()
        write = asyncio.ensure_future(
            connection.execute(
                f"""
                INSERT INTO {table} (business_key, attempt)
                VALUES (@P1, @P2)
                """,
                [10, 1],
            )
        )

        async def row_applied_once() -> bool:
            return (
                await scalar(
                    sa_connection,
                    f"""
                    SELECT COUNT(*)
                    FROM {table}
                    WHERE business_key = @P1 AND attempt = @P2
                    """,
                    [10, 1],
                )
                == 1
            )

        await wait_until(row_applied_once, timeout=3.0)
        await proxy.wait_until_downstream_held()
        assert write.done() is False

        shutdown = asyncio.create_task(connection.disconnect())
        with pytest.raises(ShutdownTimeoutError) as shutdown_captured:
            await asyncio.wait_for(shutdown, timeout=4.0)
        shutdown = None
        assert shutdown_captured.value.force_completed is True
        assert (
            shutdown_captured.value.active_operations_at_timeout == 1
        )

        with pytest.raises(ConnectionLifecycleError) as write_captured:
            await asyncio.wait_for(write, timeout=1.0)
        write = None
        write_error = write_captured.value
        assert write_error.operation == "execute"
        assert write_error.phase == "shutdown"
        assert write_error.retryable is False
        assert write_error.connection_discarded is True
        assert write_error.outcome_unknown is True
        assert write_error.forced is True
        assert proxy.accepted_connections == 1
        assert connection.lifecycle_state == ConnectionLifecycleState.CLOSED
        assert await scalar(
            sa_connection,
            f"SELECT COUNT(*) FROM {table} WHERE business_key = @P1",
            [10],
        ) == 1
    finally:
        proxy.resume_downstream()
        await cancel_and_wait(write)
        await cancel_and_wait(shutdown)
        await connection.disconnect()
        await proxy.close()
    await wait_for_zero_sessions(sa_connection, application_name)


@case("LIFE-011")
@pytest.mark.asyncio
async def test_force_retires_idle_pooled_transaction_and_old_generation(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    (
        _,
        ConnectionLifecycleState,
        ConnectionLifecycleError,
        ShutdownTimeoutError,
    ) = lifecycle_api()
    application_name = unique_sql_name(
        "strict_lifecycle_force_idle_transaction"
    )
    table = await create_table(
        cleanup_registry,
        unique_sql_name,
        "strict_lifecycle_force_idle_rows",
        "id INT NOT NULL PRIMARY KEY",
    )
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
        max_size=1,
        shutdown_timeout=0.2,
        force_timeout=2.0,
    )
    old_transaction = connection.transaction()
    new_transaction = None
    shutdown: asyncio.Task | None = None
    try:
        await old_transaction.begin()
        await old_transaction.execute(
            f"INSERT INTO {table} (id) VALUES (@P1)",
            [11],
        )
        assert (
            await application_transaction_count(
                sa_connection,
                application_name,
            )
            == 1
        )

        shutdown = asyncio.create_task(connection.disconnect())
        with pytest.raises(ShutdownTimeoutError) as shutdown_captured:
            await asyncio.wait_for(shutdown, timeout=4.0)
        shutdown = None
        shutdown_error = shutdown_captured.value
        assert shutdown_error.force_completed is True
        assert shutdown_error.active_operations_at_timeout == 0
        assert shutdown_error.active_transactions_at_timeout == 1
        assert connection.lifecycle_state == ConnectionLifecycleState.CLOSED
        assert old_transaction.is_connected() is False

        await wait_for_zero_sessions(sa_connection, application_name)
        assert await scalar(
            sa_connection,
            f"SELECT COUNT(*) FROM {table} WHERE id = @P1",
            [11],
        ) == 0

        with pytest.raises(ConnectionLifecycleError) as old_captured:
            await old_transaction.query("SELECT 11")
        old_error = old_captured.value
        assert old_error.phase == "shutdown"
        assert old_error.state in {"Closing", "Closed"}
        assert old_error.retryable is False
        assert old_error.connection_discarded is True
        assert old_error.outcome_unknown is False
        assert old_error.forced is True

        new_transaction = connection.transaction()
        await new_transaction.begin()
        await new_transaction.execute(
            f"INSERT INTO {table} (id) VALUES (@P1)",
            [111],
        )
        await new_transaction.commit()
        assert connection.lifecycle_state == ConnectionLifecycleState.OPEN
        assert await scalar(
            sa_connection,
            f"SELECT COUNT(*) FROM {table} WHERE id = @P1",
            [111],
        ) == 1
        assert await connection.disconnect() is True
    finally:
        await cancel_and_wait(shutdown)
        await old_transaction.close()
        if new_transaction is not None:
            await new_transaction.close()
        await connection.disconnect()
    await wait_for_zero_sessions(sa_connection, application_name)


@case("LIFE-012")
@pytest.mark.asyncio
async def test_forced_unconfirmed_commit_preserves_outcome_unknown(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    (
        _,
        ConnectionLifecycleState,
        ConnectionLifecycleError,
        ShutdownTimeoutError,
    ) = lifecycle_api()
    application_name = unique_sql_name(
        "strict_lifecycle_force_commit"
    )
    table = await create_table(
        cleanup_registry,
        unique_sql_name,
        "strict_lifecycle_force_commit_rows",
        "business_key INT NOT NULL PRIMARY KEY, attempt INT NOT NULL",
    )
    proxy = DownstreamGateProxy(
        sql_auth_config.host,
        sql_auth_config.port,
    )
    await proxy.start()
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
        max_size=1,
        shutdown_timeout=0.2,
        force_timeout=2.0,
        host=proxy.host,
        port=proxy.port,
    )
    transaction = connection.transaction()
    commit: asyncio.Task | None = None
    shutdown: asyncio.Task | None = None
    try:
        await transaction.begin()
        await transaction.execute(
            f"""
            INSERT INTO {table} (business_key, attempt)
            VALUES (@P1, @P2)
            """,
            [12, 1],
        )
        assert proxy.accepted_connections == 1

        proxy.pause_downstream()
        proxy.expect_client_disconnect()
        commit = asyncio.create_task(transaction.commit())

        async def commit_applied_once() -> bool:
            return (
                await scalar(
                    sa_connection,
                    f"""
                    SELECT COUNT(*)
                    FROM {table}
                    WHERE business_key = @P1 AND attempt = @P2
                    """,
                    [12, 1],
                )
                == 1
            )

        await wait_until(commit_applied_once, timeout=3.0)
        await proxy.wait_until_downstream_held()
        assert commit.done() is False

        shutdown = asyncio.create_task(connection.disconnect())
        with pytest.raises(ShutdownTimeoutError) as shutdown_captured:
            await asyncio.wait_for(shutdown, timeout=4.0)
        shutdown = None
        assert shutdown_captured.value.force_completed is True
        assert (
            shutdown_captured.value.active_transactions_at_timeout == 1
        )

        with pytest.raises(CommitOutcomeUnknown) as commit_captured:
            await asyncio.wait_for(commit, timeout=1.0)
        commit = None
        error = commit_captured.value
        assert error.operation == "commit"
        assert error.retryable is False
        assert error.connection_discarded is True
        assert isinstance(error.__cause__, ConnectionLifecycleError)
        cause = error.__cause__
        assert cause.operation == "commit"
        assert cause.phase == "shutdown"
        assert cause.retryable is False
        assert cause.connection_discarded is True
        assert cause.outcome_unknown is True
        assert cause.forced is True
        assert transaction.is_connected() is False
        assert proxy.accepted_connections == 1
        assert connection.lifecycle_state == ConnectionLifecycleState.CLOSED
        assert await scalar(
            sa_connection,
            f"SELECT COUNT(*) FROM {table} WHERE business_key = @P1",
            [12],
        ) == 1
    finally:
        proxy.resume_downstream()
        await cancel_and_wait(commit)
        await cancel_and_wait(shutdown)
        await transaction.close()
        await connection.disconnect()
        await proxy.close()
    await wait_for_zero_sessions(sa_connection, application_name)


@case("LIFE-013")
@pytest.mark.asyncio
async def test_direct_batch_and_nested_contexts_share_lifecycle_contract(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    (
        _,
        ConnectionLifecycleState,
        ConnectionLifecycleError,
        ShutdownTimeoutError,
    ) = lifecycle_api()
    application_name = unique_sql_name("strict_lifecycle_direct_batch")
    table = await create_table(
        cleanup_registry,
        unique_sql_name,
        "strict_lifecycle_direct_batch_rows",
        "id INT NOT NULL PRIMARY KEY",
    )
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
        max_size=1,
        shutdown_timeout=0.2,
        force_timeout=2.0,
    )
    batch: asyncio.Task | None = None
    shutdown: asyncio.Task | None = None
    try:
        assert await connection.is_connected() is False
        batch = asyncio.ensure_future(
            connection.execute_batch(
                [
                    (
                        (
                            "WAITFOR DELAY '00:00:05'; "
                            f"INSERT INTO {table} (id) VALUES (@P1)"
                        ),
                        [13],
                    )
                ]
            )
        )
        await wait_for_one_visible_request(sa_connection, application_name)

        shutdown = asyncio.create_task(connection.disconnect())
        with pytest.raises(ShutdownTimeoutError) as shutdown_captured:
            await asyncio.wait_for(shutdown, timeout=4.0)
        shutdown = None
        assert shutdown_captured.value.force_completed is True
        assert (
            shutdown_captured.value.active_operations_at_timeout == 1
        )

        with pytest.raises(ConnectionLifecycleError) as batch_captured:
            await asyncio.wait_for(batch, timeout=1.0)
        batch = None
        batch_error = batch_captured.value
        assert batch_error.operation == "execute_batch"
        assert batch_error.phase == "shutdown"
        assert batch_error.retryable is False
        assert batch_error.connection_discarded is True
        assert batch_error.outcome_unknown is True
        assert batch_error.forced is True
        assert connection.lifecycle_state == ConnectionLifecycleState.CLOSED

        await wait_for_zero_sessions(sa_connection, application_name)
        assert await scalar(
            sa_connection,
            f"SELECT COUNT(*) FROM {table} WHERE id = @P1",
            [13],
        ) == 0

        async with connection:
            assert connection.lifecycle_state == ConnectionLifecycleState.OPEN
            assert await scalar(connection, "SELECT 13") == 13
            async with connection:
                assert await scalar(connection, "SELECT 130") == 130
            assert (
                connection.lifecycle_state
                == ConnectionLifecycleState.CLOSED
            )
            assert await scalar(connection, "SELECT 131") == 131
            assert connection.lifecycle_state == ConnectionLifecycleState.OPEN
        assert connection.lifecycle_state == ConnectionLifecycleState.CLOSED
    finally:
        await cancel_and_wait(batch)
        await cancel_and_wait(shutdown)
        await connection.disconnect()
    await wait_for_zero_sessions(sa_connection, application_name)


@case("LIFE-014")
@pytest.mark.timeout(180)
@pytest.mark.asyncio
async def test_one_hundred_generations_run_two_thousand_operations(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    _, ConnectionLifecycleState, _, _ = lifecycle_api()
    application_name = unique_sql_name("strict_lifecycle_generations")
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
        max_size=20,
        shutdown_timeout=3.0,
        force_timeout=1.0,
    )
    all_connection_ids: set[str] = set()
    completed_values: list[int] = []
    try:
        for generation in range(100):
            values = range(generation * 20, (generation + 1) * 20)
            streams = await asyncio.gather(
                *(
                    connection.query(
                        """
                        SELECT
                            @P1 AS value,
                            CONVERT(NVARCHAR(36), connection_id)
                                AS connection_id
                        FROM sys.dm_exec_connections
                        WHERE session_id = @@SPID
                        """,
                        [value],
                    )
                    for value in values
                )
            )
            rows = [stream.fetchone() for stream in streams]
            assert all(row is not None for row in rows)
            round_values = [int(row["value"]) for row in rows]
            assert round_values == list(values)
            completed_values.extend(round_values)

            round_connection_ids = {
                str(row["connection_id"]) for row in rows
            }
            assert round_connection_ids
            assert round_connection_ids.isdisjoint(all_connection_ids)
            all_connection_ids.update(round_connection_ids)

            assert await connection.disconnect() is True
            assert (
                connection.lifecycle_state
                == ConnectionLifecycleState.CLOSED
            )
            await wait_for_zero_sessions(sa_connection, application_name)
    finally:
        await connection.disconnect()

    assert completed_values == list(range(2_000))
    assert len(completed_values) == 2_000
    await wait_for_zero_sessions(sa_connection, application_name)
