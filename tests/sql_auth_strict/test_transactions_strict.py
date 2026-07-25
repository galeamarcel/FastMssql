from __future__ import annotations

import asyncio
from collections.abc import Callable
import time

import fastmssql
from fastmssql import Connection, PoolConfig, SqlError, SslConfig, Transaction
from fastmssql.fastmssql import Transaction as RustTransaction
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import CleanupRegistry, quote_identifier, scalar
from sql_auth_strict.tcp_fault_proxy import DownstreamGateProxy


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


@case("TX-027")
def test_commit_outcome_unknown_is_a_distinct_public_exception() -> None:
    unknown_type = getattr(fastmssql, "CommitOutcomeUnknown", None)
    assert unknown_type is not None
    assert issubclass(unknown_type, Exception)
    assert not issubclass(unknown_type, fastmssql.SqlConnectionError)
    assert not issubclass(unknown_type, fastmssql.SqlError)
    assert "CommitOutcomeUnknown" in fastmssql.__all__


class _RecordingUnknownCommitCore:
    def __init__(self, unknown_type: type[Exception]) -> None:
        self._unknown_type = unknown_type
        self.begin_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    async def begin(self) -> None:
        self.begin_calls += 1

    async def commit(self) -> None:
        self.commit_calls += 1
        raise self._unknown_type(
            "COMMIT completion was not confirmed; "
            "the transaction outcome is unknown"
        )

    async def rollback(self) -> None:
        self.rollback_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


@case("TX-030")
@pytest.mark.asyncio
async def test_context_manager_does_not_rollback_unknown_commit() -> None:
    unknown_type = getattr(fastmssql, "CommitOutcomeUnknown", None)
    assert unknown_type is not None
    core = _RecordingUnknownCommitCore(unknown_type)
    transaction = Transaction._from_rust(core)

    with pytest.raises(unknown_type):
        async with transaction:
            pass

    assert core.begin_calls == 1
    assert core.commit_calls == 1
    assert core.rollback_calls == 0
    assert core.close_calls == 1


def _observer_connection(config: SqlAuthConfig) -> Connection:
    return Connection(
        server=config.host,
        port=config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=1,
            min_idle=1,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=2,
            retry_connection=False,
        ),
    )


def _state_contract_transaction(
    config: SqlAuthConfig,
    implementation: str,
):
    transaction_type = (
        Transaction if implementation == "public" else RustTransaction
    )
    return transaction_type(
        config.connection_string(
            config.owner_user,
            config.owner_password,
        )
    )


def _pooled_transaction_connection(
    config: SqlAuthConfig,
    *,
    max_size: int,
    application_name: str | None = None,
    server: str | None = None,
    port: int | None = None,
) -> Connection:
    return Connection(
        server=server or config.host,
        port=port or config.port,
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
    )


@pytest.mark.asyncio
async def test_tcp_fault_proxy_smoke(
    sql_auth_config: SqlAuthConfig,
) -> None:
    proxy = DownstreamGateProxy(
        sql_auth_config.host,
        sql_auth_config.port,
    )
    await proxy.start()
    try:
        connection = _pooled_transaction_connection(
            sql_auth_config,
            max_size=1,
            server=proxy.host,
            port=proxy.port,
        )
        try:
            result = await asyncio.wait_for(
                scalar(connection, "SELECT 1"),
                timeout=2.0,
            )
            assert result == 1
        finally:
            await asyncio.wait_for(connection.disconnect(), timeout=2.0)
        assert proxy.accepted_connections == 1
    finally:
        await asyncio.wait_for(proxy.close(), timeout=2.0)


async def _wait_for_pool_active(
    connection: Connection,
    expected: int,
    *,
    timeout: float = 2.0,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stats = await connection.pool_stats()
        if stats["active_connections"] == expected:
            return stats
        await asyncio.sleep(0.02)
    stats = await connection.pool_stats()
    raise AssertionError(
        f"expected {expected} active pooled connection(s), observed {stats}"
    )


async def _wait_for_request(
    sa_connection: Connection,
    token: str,
    *,
    present: bool,
    timeout: float = 3.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        count = await scalar(
            sa_connection,
            """
            SELECT COUNT(*)
            FROM sys.dm_exec_requests AS request
            CROSS APPLY sys.dm_exec_sql_text(request.sql_handle) AS sql_text
            WHERE request.session_id <> @@SPID
              AND sql_text.text LIKE @P1
            """,
            [f"%{token}%"],
        )
        if (count > 0) is present:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(
        f"request token {token!r} did not reach present={present}"
    )


async def _wait_for_request_identity(
    sa_connection: Connection,
    token: str,
    *,
    timeout: float = 3.0,
) -> tuple[int, str]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        result = await sa_connection.query(
            """
            SELECT TOP (1)
                request.session_id,
                CONVERT(NVARCHAR(36), connection.connection_id)
            FROM sys.dm_exec_requests AS request
            JOIN sys.dm_exec_connections AS connection
              ON connection.session_id = request.session_id
            CROSS APPLY sys.dm_exec_sql_text(request.sql_handle) AS sql_text
            WHERE request.session_id <> @@SPID
              AND sql_text.text LIKE @P1
            ORDER BY request.session_id
            """,
            [f"%{token}%"],
        )
        row = result.fetchone()
        if row is not None:
            return int(row[0]), str(row[1])
        await asyncio.sleep(0.02)
    raise AssertionError(
        f"request token {token!r} did not become active"
    )


async def _wait_for_session_absent(
    sa_connection: Connection,
    session_id: int,
    *,
    timeout: float = 2.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        count = await scalar(
            sa_connection,
            """
            SELECT COUNT(*)
            FROM sys.dm_exec_sessions
            WHERE session_id = @P1
            """,
            [session_id],
        )
        if count == 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(
        f"SQL Server session {session_id} did not disappear"
    )


async def _wait_for_row_count(
    connection: Connection,
    table: str,
    expected: int,
    *,
    timeout: float = 3.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        count = await scalar(
            connection,
            f"SELECT COUNT(*) FROM {table} WHERE id = 1",
        )
        if count == expected:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"row count in {table} did not reach {expected}"
    )


@case("TX-001")
@pytest.mark.asyncio
async def test_dedicated_session_id_remains_constant(
    transaction_factory: Callable,
) -> None:
    transaction = transaction_factory()
    try:
        await transaction.begin()
        first_session = await scalar(transaction, "SELECT @@SPID")
        first_connection_id = await scalar(
            transaction,
            """
            SELECT connection_id
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """,
        )
        second_session = await scalar(transaction, "SELECT @@SPID")
        second_connection_id = await scalar(
            transaction,
            """
            SELECT connection_id
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """,
        )
        assert second_session == first_session
        assert second_connection_id == first_connection_id
        await transaction.rollback()
    finally:
        await transaction.close()


@case("TX-022")
@pytest.mark.asyncio
async def test_connection_transaction_reserves_one_shared_pool_session(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _pooled_transaction_connection(
        sql_auth_config,
        max_size=2,
    )
    transaction = connection.transaction()

    try:
        await transaction.begin()
        first_session = await scalar(transaction, "SELECT @@SPID")
        second_session = await scalar(transaction, "SELECT @@SPID")
        stats = await connection.pool_stats()

        assert first_session == second_session
        assert stats["max_size"] == 2
        assert stats["connections"] == 1
        assert stats["active_connections"] == 1
        assert stats["idle_connections"] == 0

        await transaction.rollback()
        released = await _wait_for_pool_active(connection, 0)
        assert released["connections"] == 1
        assert released["idle_connections"] == 1
        assert transaction.is_connected() is False
    finally:
        await transaction.close()
        await connection.disconnect()


@case("TX-023")
@pytest.mark.asyncio
async def test_pooled_transactions_obey_max_size_and_settlement_releases_waiter(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _pooled_transaction_connection(
        sql_auth_config,
        max_size=2,
    )
    first = connection.transaction()
    second = connection.transaction()
    waiting = connection.transaction()
    waiting_begin: asyncio.Task | None = None

    try:
        await asyncio.gather(first.begin(), second.begin())
        first_session, second_session = await asyncio.gather(
            scalar(first, "SELECT @@SPID"),
            scalar(second, "SELECT @@SPID"),
        )
        assert first_session != second_session

        saturated = await _wait_for_pool_active(connection, 2)
        assert saturated["connections"] == 2
        assert saturated["idle_connections"] == 0

        waiting_begin = asyncio.create_task(waiting.begin())
        await asyncio.sleep(0.1)
        assert waiting_begin.done() is False

        await first.commit()
        await asyncio.wait_for(waiting_begin, timeout=1.0)
        waiting_session = await scalar(waiting, "SELECT @@SPID")
        assert waiting_session in {first_session, second_session}

        still_bounded = await _wait_for_pool_active(connection, 2)
        assert still_bounded["connections"] == 2
        assert still_bounded["active_connections"] == 2

        await second.rollback()
        await waiting.rollback()
        released = await _wait_for_pool_active(connection, 0)
        assert released["connections"] == 2
        assert released["idle_connections"] == 2
    finally:
        if waiting_begin is not None and not waiting_begin.done():
            waiting_begin.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting_begin
        await first.close()
        await second.close()
        await waiting.close()
        await connection.disconnect()


@case("TX-024")
@pytest.mark.asyncio
async def test_regular_query_and_transaction_share_one_pool_budget(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _pooled_transaction_connection(
        sql_auth_config,
        max_size=1,
    )
    transaction = connection.transaction()
    waiting_query: asyncio.Task | None = None

    try:
        await transaction.begin()
        transaction_session = await scalar(transaction, "SELECT @@SPID")
        waiting_query = asyncio.create_task(scalar(connection, "SELECT @@SPID"))
        await asyncio.sleep(0.1)
        assert waiting_query.done() is False

        stats = await connection.pool_stats()
        assert stats["connections"] == 1
        assert stats["active_connections"] == 1
        assert stats["max_size"] == 1

        await transaction.commit()
        query_session = await asyncio.wait_for(waiting_query, timeout=1.0)
        assert query_session == transaction_session
        released = await _wait_for_pool_active(connection, 0)
        assert released["connections"] == 1
    finally:
        if waiting_query is not None and not waiting_query.done():
            waiting_query.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting_query
        await transaction.close()
        await connection.disconnect()


@case("TX-025")
@pytest.mark.asyncio
async def test_transaction_lease_is_reset_before_cross_lease_reuse(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _pooled_transaction_connection(
        sql_auth_config,
        max_size=1,
    )
    first = connection.transaction()
    second = connection.transaction()

    try:
        await first.begin()
        first_session = await scalar(first, "SELECT @@SPID")
        await first.execute(
            "EXEC sys.sp_set_session_context "
            "@key=N'fastmssql_tx_lease', @value=N'contaminated', "
            "@read_only=1"
        )
        await first.execute(
            "CREATE TABLE #fastmssql_tx_lease (value INT NOT NULL)"
        )
        await first.execute(
            "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"
        )
        await first.commit()

        await second.begin()
        second_session = await scalar(second, "SELECT @@SPID")
        context_value = await scalar(
            second,
            "SELECT CONVERT(NVARCHAR(100), "
            "SESSION_CONTEXT(N'fastmssql_tx_lease'))",
        )
        temp_object = await scalar(
            second,
            "SELECT OBJECT_ID(N'tempdb..#fastmssql_tx_lease')",
        )
        isolation_level = await scalar(
            second,
            """
            SELECT transaction_isolation_level
            FROM sys.dm_exec_sessions
            WHERE session_id = @@SPID
            """,
        )

        assert second_session == first_session
        assert context_value is None
        assert temp_object is None
        assert isolation_level == 2
        await second.rollback()
    finally:
        await first.close()
        await second.close()
        await connection.disconnect()


@case("TX-026")
@pytest.mark.asyncio
async def test_cancelled_transaction_lease_is_retired_and_waiter_recovers(
    sa_connection: Connection,
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("strict_tx_lease_cancel")
    token = unique_sql_name("strict_tx_lease_wait")
    connection = _pooled_transaction_connection(
        sql_auth_config,
        max_size=1,
        application_name=application_name,
    )
    cancelled = connection.transaction()
    waiting = connection.transaction()
    query_task: asyncio.Task | None = None
    waiting_begin: asyncio.Task | None = None

    try:
        await cancelled.begin()
        cancelled_session_id = await scalar(cancelled, "SELECT @@SPID")
        cancelled_connection_id = await scalar(
            cancelled,
            """
            SELECT CONVERT(NVARCHAR(36), connection_id)
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """,
        )
        query_task = asyncio.create_task(
            cancelled.simple_query(
                "WAITFOR DELAY '00:00:05'; "
                f"SELECT 1 AS value /* {token} */"
            )
        )
        await _wait_for_request(sa_connection, token, present=True)

        waiting_begin = asyncio.create_task(waiting.begin())
        await asyncio.sleep(0.1)
        assert waiting_begin.done() is False

        query_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await query_task

        with pytest.raises(
            RuntimeError,
            match="state is indeterminate; call close",
        ):
            await cancelled.commit()

        # Cancellation itself must retire the connection and release the
        # waiter; explicit close remains a later compatibility operation.
        await _wait_for_request(sa_connection, token, present=False)
        await _wait_for_session_absent(
            sa_connection,
            cancelled_session_id,
        )
        await asyncio.wait_for(waiting_begin, timeout=2.0)
        recovered_connection_id = await scalar(
            waiting,
            """
            SELECT CONVERT(NVARCHAR(36), connection_id)
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """,
        )
        assert recovered_connection_id != cancelled_connection_id
        assert cancelled.is_connected() is False

        stats = await connection.pool_stats()
        assert stats["connections"] == 1
        assert stats["active_connections"] == 1
        await waiting.rollback()
        await _wait_for_pool_active(connection, 0)

        await cancelled.close()
        await cancelled.close()
    finally:
        if query_task is not None and not query_task.done():
            query_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await query_task
        if waiting_begin is not None and not waiting_begin.done():
            waiting_begin.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting_begin
        await cancelled.close()
        await waiting.close()
        await connection.disconnect()


@case("TX-028")
@pytest.mark.asyncio
async def test_pooled_commit_ack_loss_is_typed_and_retires_connection(
    sa_connection: Connection,
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_commit_unknown_pool"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await sa_connection.execute(
        f"CREATE TABLE {table} (id INT NOT NULL PRIMARY KEY)"
    )

    proxy = DownstreamGateProxy(
        sql_auth_config.host,
        sql_auth_config.port,
    )
    await proxy.start()
    connection = _pooled_transaction_connection(
        sql_auth_config,
        max_size=1,
        application_name=unique_sql_name("strict_commit_unknown_pool_app"),
        server=proxy.host,
        port=proxy.port,
    )
    committing = connection.transaction()
    waiting = connection.transaction()
    commit_task: asyncio.Task | None = None
    waiting_begin: asyncio.Task | None = None

    try:
        await committing.begin()
        encrypt_option = await scalar(
            committing,
            """
            SELECT encrypt_option
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """,
        )
        assert encrypt_option == "TRUE"
        original_connection_id = await scalar(
            committing,
            """
            SELECT CONVERT(NVARCHAR(36), connection_id)
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """,
        )
        await committing.execute(f"INSERT INTO {table} (id) VALUES (1)")

        waiting_begin = asyncio.create_task(waiting.begin())
        await asyncio.sleep(0.05)
        assert waiting_begin.done() is False

        proxy.pause_downstream()
        commit_task = asyncio.create_task(committing.commit())
        await _wait_for_row_count(sa_connection, table, 1)
        await proxy.wait_until_downstream_held()
        await proxy.abort_connections()
        proxy.resume_downstream()

        with pytest.raises(Exception) as captured:
            await asyncio.wait_for(commit_task, timeout=2.0)
        error = captured.value
        assert type(error).__name__ == "CommitOutcomeUnknown"
        assert error.operation == "commit"
        assert error.retryable is False
        assert error.connection_discarded is True
        assert error.__cause__ is not None
        assert await scalar(
            sa_connection,
            f"SELECT COUNT(*) FROM {table} WHERE id = 1",
        ) == 1

        await asyncio.wait_for(waiting_begin, timeout=2.0)
        replacement_connection_id = await scalar(
            waiting,
            """
            SELECT CONVERT(NVARCHAR(36), connection_id)
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """,
        )
        assert replacement_connection_id != original_connection_id
        stats = await connection.pool_stats()
        assert stats["connections"] == 1
        assert stats["active_connections"] == 1

        await waiting.rollback()
        await _wait_for_pool_active(connection, 0)
    finally:
        proxy.resume_downstream()
        for task in (commit_task, waiting_begin):
            if task is not None and not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        await committing.close()
        await waiting.close()
        await connection.disconnect()
        await proxy.close()


@case("TX-029")
@pytest.mark.asyncio
async def test_direct_commit_ack_loss_is_typed_and_closes_socket(
    sa_connection: Connection,
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_commit_unknown_direct"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await sa_connection.execute(
        f"CREATE TABLE {table} (id INT NOT NULL PRIMARY KEY)"
    )

    proxy = DownstreamGateProxy(
        sql_auth_config.host,
        sql_auth_config.port,
    )
    await proxy.start()
    transaction = Transaction(
        server=proxy.host,
        port=proxy.port,
        database=sql_auth_config.database,
        username=sql_auth_config.owner_user,
        password=sql_auth_config.owner_password,
        ssl_config=SslConfig.development(),
    )
    commit_task: asyncio.Task | None = None

    try:
        await transaction.begin()
        assert await scalar(
            transaction,
            """
            SELECT encrypt_option
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """,
        ) == "TRUE"
        await transaction.execute(f"INSERT INTO {table} (id) VALUES (1)")

        proxy.pause_downstream()
        commit_task = asyncio.create_task(transaction.commit())
        await _wait_for_row_count(sa_connection, table, 1)
        await proxy.wait_until_downstream_held()
        await proxy.abort_connections()
        proxy.resume_downstream()

        with pytest.raises(Exception) as captured:
            await asyncio.wait_for(commit_task, timeout=2.0)
        assert type(captured.value).__name__ == "CommitOutcomeUnknown"
        assert await scalar(
            sa_connection,
            f"SELECT COUNT(*) FROM {table} WHERE id = 1",
        ) == 1
        assert transaction.is_connected() is False
    finally:
        proxy.resume_downstream()
        if commit_task is not None and not commit_task.done():
            commit_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await commit_task
        await transaction.close()
        await transaction.close()
        await proxy.close()


@case("TX-031")
@pytest.mark.asyncio
async def test_server_commit_rejection_remains_sql_error(
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_commit_rejection"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    connection = _pooled_transaction_connection(
        sql_auth_config,
        max_size=1,
    )
    transaction = connection.transaction()

    try:
        await connection.execute(
            f"CREATE TABLE {table} (id INT NOT NULL PRIMARY KEY)"
        )
        await transaction.begin()
        with pytest.raises(SqlError) as statement_error:
            await transaction.simple_query(
                "SET XACT_ABORT ON; "
                f"INSERT INTO {table} (id) VALUES (1); "
                f"INSERT INTO {table} (id) VALUES (1)"
            )
        assert statement_error.value.code in {2601, 2627}

        with pytest.raises(SqlError) as commit_error:
            await transaction.commit()
        assert commit_error.value.code == 3902
        assert commit_error.value.severity == 16
        assert type(commit_error.value).__name__ == "SqlError"
    finally:
        await transaction.close()
        await connection.disconnect()


@case("TX-032")
@pytest.mark.asyncio
async def test_cancelled_pooled_transaction_retires_without_explicit_close(
    sa_connection: Connection,
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
) -> None:
    token = unique_sql_name("strict_tx_auto_retire_pool")
    connection = _pooled_transaction_connection(
        sql_auth_config,
        max_size=1,
        application_name=unique_sql_name("strict_tx_auto_retire_pool_app"),
    )
    cancelled = connection.transaction()
    waiting = connection.transaction()
    query_task: asyncio.Task | None = None
    waiting_begin: asyncio.Task | None = None

    try:
        await cancelled.begin()
        original_session_id = await scalar(cancelled, "SELECT @@SPID")
        original_connection_id = await scalar(
            cancelled,
            """
            SELECT CONVERT(NVARCHAR(36), connection_id)
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """,
        )
        query_task = asyncio.create_task(
            cancelled.simple_query(
                "WAITFOR DELAY '00:00:10'; "
                f"SELECT 1 AS value /* {token} */"
            )
        )
        request_identity = await _wait_for_request_identity(
            sa_connection,
            token,
        )
        assert request_identity == (
            original_session_id,
            original_connection_id,
        )

        waiting_begin = asyncio.create_task(waiting.begin())
        await asyncio.sleep(0.1)
        assert waiting_begin.done() is False

        query_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await query_task

        # No cancelled.close() is permitted before all autonomous cleanup
        # assertions below.
        await _wait_for_request(
            sa_connection,
            token,
            present=False,
            timeout=2.0,
        )
        await _wait_for_session_absent(
            sa_connection,
            original_session_id,
            timeout=2.0,
        )
        await asyncio.wait_for(waiting_begin, timeout=2.0)
        replacement_connection_id = await scalar(
            waiting,
            """
            SELECT CONVERT(NVARCHAR(36), connection_id)
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """,
        )
        assert replacement_connection_id != original_connection_id

        with pytest.raises(
            RuntimeError,
            match="state is indeterminate; call close",
        ):
            await cancelled.commit()
        assert cancelled.is_connected() is False

        stats = await connection.pool_stats()
        assert stats["connections"] == 1
        assert stats["active_connections"] == 1
        await waiting.rollback()
        await _wait_for_pool_active(connection, 0)

        await cancelled.close()
        await cancelled.close()
    finally:
        if query_task is not None and not query_task.done():
            query_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await query_task
        if waiting_begin is not None:
            if not waiting_begin.done():
                waiting_begin.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiting_begin
            elif not waiting_begin.cancelled():
                waiting_begin.exception()
        await cancelled.close()
        await waiting.close()
        await connection.disconnect()


@case("TX-033")
@pytest.mark.asyncio
async def test_cancelled_direct_transaction_closes_and_rolls_back_without_close(
    owner_connection: Connection,
    sa_connection: Connection,
    transaction_factory: Callable,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_tx_auto_retire_direct"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT NOT NULL PRIMARY KEY)"
    )
    token = unique_sql_name("strict_tx_auto_retire_direct_wait")
    transaction = transaction_factory()
    query_task: asyncio.Task | None = None

    try:
        await transaction.begin()
        await transaction.execute(f"INSERT INTO {table} (id) VALUES (1)")
        original_session_id = await scalar(transaction, "SELECT @@SPID")
        original_connection_id = await scalar(
            transaction,
            """
            SELECT CONVERT(NVARCHAR(36), connection_id)
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """,
        )
        query_task = asyncio.create_task(
            transaction.query(
                "WAITFOR DELAY '00:00:10'; "
                f"SELECT CAST(1 AS INT) AS value /* {token} */"
            )
        )
        request_identity = await _wait_for_request_identity(
            sa_connection,
            token,
        )
        assert request_identity == (
            original_session_id,
            original_connection_id,
        )

        query_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await query_task

        # No transaction.close() is permitted before SQL Server cleanup and
        # rollback are proven.
        await _wait_for_request(
            sa_connection,
            token,
            present=False,
            timeout=2.0,
        )
        await _wait_for_session_absent(
            sa_connection,
            original_session_id,
            timeout=2.0,
        )
        assert transaction.is_connected() is False
        assert await scalar(
            owner_connection,
            f"SELECT COUNT(*) FROM {table} WHERE id = 1",
        ) == 0

        await transaction.close()
        await transaction.close()
    finally:
        if query_task is not None and not query_task.done():
            query_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await query_task
        await transaction.close()


@case("TX-034")
@pytest.mark.asyncio
async def test_cancelled_commit_retires_lease_without_claiming_rollback(
    sa_connection: Connection,
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_cancelled_commit"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await sa_connection.execute(
        f"CREATE TABLE {table} (id INT NOT NULL PRIMARY KEY)"
    )

    proxy = DownstreamGateProxy(
        sql_auth_config.host,
        sql_auth_config.port,
    )
    await proxy.start()
    connection = _pooled_transaction_connection(
        sql_auth_config,
        max_size=1,
        application_name=unique_sql_name("strict_cancelled_commit_app"),
        server=proxy.host,
        port=proxy.port,
    )
    committing = connection.transaction()
    waiting = connection.transaction()
    commit_task: asyncio.Task | None = None
    waiting_begin: asyncio.Task | None = None

    try:
        await committing.begin()
        original_connection_id = await scalar(
            committing,
            """
            SELECT CONVERT(NVARCHAR(36), connection_id)
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """,
        )
        await committing.execute(f"INSERT INTO {table} (id) VALUES (1)")

        waiting_begin = asyncio.create_task(waiting.begin())
        await asyncio.sleep(0.1)
        assert waiting_begin.done() is False

        proxy.pause_downstream()
        commit_task = asyncio.create_task(committing.commit())
        await _wait_for_row_count(sa_connection, table, 1)
        await proxy.wait_until_downstream_held()
        commit_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await commit_task

        # Releasing the transparent proxy gate lets it observe the cancelled
        # client's EOF. No committing.close() occurs before the assertions.
        proxy.resume_downstream()
        await asyncio.wait_for(waiting_begin, timeout=2.0)
        replacement_connection_id = await scalar(
            waiting,
            """
            SELECT CONVERT(NVARCHAR(36), connection_id)
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """,
        )
        assert replacement_connection_id != original_connection_id
        assert await scalar(
            sa_connection,
            f"SELECT COUNT(*) FROM {table} WHERE id = 1",
        ) == 1
        assert committing.is_connected() is False
        with pytest.raises(
            RuntimeError,
            match="state is indeterminate; call close",
        ):
            await committing.commit()

        await waiting.rollback()
        await _wait_for_pool_active(connection, 0)
    finally:
        proxy.resume_downstream()
        for task in (commit_task, waiting_begin):
            if task is None:
                continue
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif not task.cancelled():
                task.exception()
        await committing.close()
        await waiting.close()
        await connection.disconnect()
        await proxy.close()


@case("TX-002", "TX-003")
@pytest.mark.asyncio
async def test_explicit_commit_and_rollback_persistence(
    owner_connection: Connection,
    transaction_factory: Callable,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_tx_explicit"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")

    transaction = transaction_factory()
    try:
        await transaction.begin()
        await transaction.execute(f"INSERT INTO {table} VALUES (1)")
        await transaction.commit()
        assert (
            await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}")
            == 1
        )

        await transaction.begin()
        await transaction.execute(f"INSERT INTO {table} VALUES (2)")
        await transaction.rollback()
        assert (
            await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}")
            == 1
        )
        assert await scalar(owner_connection, f"SELECT id FROM {table}") == 1
    finally:
        await transaction.close()


@case("TX-004")
@pytest.mark.asyncio
async def test_context_manager_auto_begin_and_commit(
    owner_connection: Connection,
    transaction_factory: Callable,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_tx_context_commit"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")
    transaction = transaction_factory()
    async with transaction:
        assert transaction.is_connected() is True
        assert await scalar(transaction, "SELECT @@TRANCOUNT") == 1
        await transaction.execute(f"INSERT INTO {table} VALUES (1)")
    assert transaction.is_connected() is False
    assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == 1


@case("TX-005")
@pytest.mark.asyncio
async def test_context_exception_rolls_back_and_propagates(
    owner_connection: Connection,
    transaction_factory: Callable,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_tx_context_rollback"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")
    transaction = transaction_factory()
    with pytest.raises(RuntimeError, match="force strict rollback"):
        async with transaction:
            await transaction.execute(f"INSERT INTO {table} VALUES (1)")
            raise RuntimeError("force strict rollback")
    assert transaction.is_connected() is False
    assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == 0


@case("TX-006")
@pytest.mark.asyncio
async def test_manual_commit_and_rollback_inside_context(
    owner_connection: Connection,
    transaction_factory: Callable,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_tx_manual_context"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")

    committed = transaction_factory()
    async with committed:
        await committed.execute(f"INSERT INTO {table} VALUES (1)")
        await committed.commit()

    rolled_back = transaction_factory()
    async with rolled_back:
        await rolled_back.execute(f"INSERT INTO {table} VALUES (2)")
        await rolled_back.rollback()

    rows = await owner_connection.query(f"SELECT id FROM {table} ORDER BY id")
    assert [row["id"] for row in rows.rows()] == [1]


@case("TX-007")
@pytest.mark.asyncio
async def test_repeated_transaction_state_errors(
    transaction_factory: Callable,
) -> None:
    committed = transaction_factory()
    try:
        with pytest.raises(RuntimeError, match="has not begun"):
            await committed.commit()
        with pytest.raises(RuntimeError, match="has not begun"):
            await committed.rollback()
        await committed.begin()
        with pytest.raises(RuntimeError, match="already begun"):
            await committed.begin()
        await committed.commit()
        with pytest.raises(
            RuntimeError, match=r"already (?:been )?committed"
        ):
            await committed.commit()
        with pytest.raises(
            RuntimeError, match=r"already (?:been )?committed"
        ):
            await committed.rollback()
    finally:
        await committed.close()

    rolled_back = transaction_factory()
    try:
        await rolled_back.begin()
        await rolled_back.rollback()
        with pytest.raises(
            RuntimeError, match=r"already (?:been )?rolled back"
        ):
            await rolled_back.rollback()
        with pytest.raises(
            RuntimeError, match=r"already (?:been )?rolled back"
        ):
            await rolled_back.commit()
    finally:
        await rolled_back.close()


@case("TX-008")
@pytest.mark.asyncio
async def test_close_and_reuse_opens_new_physical_connection(
    transaction_factory: Callable,
) -> None:
    transaction = transaction_factory()
    assert transaction.is_connected() is False
    await transaction.close()
    assert transaction.is_connected() is False

    await transaction.begin()
    first_connection_id = await scalar(
        transaction,
        """
        SELECT connection_id
        FROM sys.dm_exec_connections
        WHERE session_id = @@SPID
        """,
    )
    await transaction.rollback()
    await transaction.close()
    assert transaction.is_connected() is False

    try:
        await transaction.begin()
        second_connection_id = await scalar(
            transaction,
            """
            SELECT connection_id
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """,
        )
        assert second_connection_id != first_connection_id
        await transaction.rollback()
    finally:
        await transaction.close()


@case("TX-009")
@pytest.mark.asyncio
async def test_sequential_reuse_preserves_dedicated_session(
    owner_connection: Connection,
    transaction_factory: Callable,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_tx_reuse"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")
    transaction = transaction_factory()
    try:
        await transaction.begin()
        first_session = await scalar(transaction, "SELECT @@SPID")
        await transaction.execute(f"INSERT INTO {table} VALUES (1)")
        await transaction.commit()

        await transaction.begin()
        second_session = await scalar(transaction, "SELECT @@SPID")
        await transaction.execute(f"INSERT INTO {table} VALUES (2)")
        await transaction.rollback()
        assert second_session == first_session
        assert (
            await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}")
            == 1
        )
    finally:
        await transaction.close()


@case("TX-010")
@pytest.mark.asyncio
async def test_query_simple_query_and_execute_forwarding(
    transaction_factory: Callable,
) -> None:
    transaction = transaction_factory()
    try:
        await transaction.begin()
        await transaction.execute(
            "CREATE TABLE #strict_tx_forward (id INT PRIMARY KEY)"
        )
        assert (
            await transaction.execute(
                "INSERT INTO #strict_tx_forward VALUES (@P1)", [1]
            )
            == 1
        )
        assert (
            await scalar(
                transaction, "SELECT id FROM #strict_tx_forward WHERE id=@P1", [1]
            )
            == 1
        )
        raw = await transaction.simple_query(
            "SELECT CAST(2 AS INT) AS raw_value"
        )
        assert raw.fetchone()["raw_value"] == 2
        await transaction.rollback()
    finally:
        await transaction.close()


@case("TX-011")
@pytest.mark.asyncio
async def test_query_and_execute_batch_forwarding(
    owner_connection: Connection,
    transaction_factory: Callable,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_tx_batches"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")
    transaction = transaction_factory()
    try:
        await transaction.begin()
        counts = await transaction.execute_batch(
            [
                (f"INSERT INTO {table} VALUES (@P1)", [1]),
                (f"INSERT INTO {table} VALUES (@P1)", [2]),
            ]
        )
        assert counts == [1, 1]
        results = await transaction.query_batch(
            [
                (f"SELECT COUNT(*) AS value FROM {table}", None),
                (f"SELECT MAX(id) AS value FROM {table}", None),
            ]
        )
        assert [result.fetchone()["value"] for result in results] == [2, 2]
        await transaction.commit()
        assert (
            await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}")
            == 2
        )
    finally:
        await transaction.close()


@case("TX-012")
@pytest.mark.asyncio
async def test_local_temp_table_and_session_state(
    transaction_factory: Callable,
) -> None:
    transaction = transaction_factory()
    try:
        await transaction.begin()
        await transaction.execute(
            "CREATE TABLE #strict_tx_session (value INT NOT NULL)"
        )
        await transaction.execute(
            "INSERT INTO #strict_tx_session VALUES (41)"
        )
        await transaction.simple_query("SET NOCOUNT ON")
        assert (
            await scalar(
                transaction, "SELECT value FROM #strict_tx_session"
            )
            == 41
        )
        assert (
            await scalar(
                transaction,
                "SELECT CASE WHEN (@@OPTIONS & 512) = 512 THEN 1 ELSE 0 END",
            )
            == 1
        )
        await transaction.rollback()
    finally:
        await transaction.close()


@case("TX-013")
@pytest.mark.asyncio
async def test_ddl_is_rolled_back(
    owner_connection: Connection,
    transaction_factory: Callable,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_tx_ddl")
    table = quote_identifier(raw_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    transaction = transaction_factory()
    try:
        await transaction.begin()
        await transaction.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")
        assert await scalar(transaction, "SELECT OBJECT_ID(@P1)", [raw_table])
        await transaction.rollback()
        assert (
            await scalar(owner_connection, "SELECT OBJECT_ID(@P1)", [raw_table])
            is None
        )
    finally:
        await transaction.close()


@case("TX-014")
@pytest.mark.asyncio
async def test_savepoint_behavior_through_raw_sql(
    owner_connection: Connection,
    transaction_factory: Callable,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_tx_savepoint"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")
    transaction = transaction_factory()
    try:
        await transaction.begin()
        await transaction.execute(f"INSERT INTO {table} VALUES (1)")
        await transaction.simple_query("SAVE TRANSACTION strict_savepoint")
        await transaction.execute(f"INSERT INTO {table} VALUES (2)")
        await transaction.simple_query(
            "ROLLBACK TRANSACTION strict_savepoint"
        )
        await transaction.commit()
        rows = await owner_connection.query(
            f"SELECT id FROM {table} ORDER BY id"
        )
        assert [row["id"] for row in rows.rows()] == [1]
    finally:
        await transaction.close()


@case("TX-015")
@pytest.mark.asyncio
async def test_read_uncommitted_and_read_committed_visibility(
    owner_connection: Connection,
    transaction_factory: Callable,
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_tx_visibility"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")
    writer = transaction_factory()
    observer = _observer_connection(sql_auth_config)
    try:
        await writer.begin()
        await writer.execute(f"INSERT INTO {table} VALUES (1)")
        await observer.connect()
        dirty_read = await observer.simple_query(
            f"""
            SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;
            SELECT COUNT_BIG(*) AS visible_rows FROM {table};
            """
        )
        dirty_row = dirty_read.fetchone()
        assert dirty_row is not None
        assert dirty_row["visible_rows"] == 1
        await writer.rollback()
        committed_read = await observer.simple_query(
            f"""
            SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
            SELECT COUNT_BIG(*) AS visible_rows FROM {table};
            """
        )
        committed_row = committed_read.fetchone()
        assert committed_row is not None
        assert committed_row["visible_rows"] == 0
    finally:
        await writer.close()
        await observer.disconnect()


@case("TX-016")
@pytest.mark.asyncio
async def test_blocking_lock_releases_after_commit(
    owner_connection: Connection,
    transaction_factory: Callable,
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_tx_blocking"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value INT NOT NULL)"
    )
    await owner_connection.execute(f"INSERT INTO {table} VALUES (1, 10)")
    writer = transaction_factory()
    observer = _observer_connection(sql_auth_config)
    try:
        await writer.begin()
        await writer.execute(f"UPDATE {table} SET value = 20 WHERE id = 1")
        blocked_read = asyncio.create_task(
            scalar(observer, f"SELECT value FROM {table} WHERE id = 1")
        )
        await asyncio.sleep(0.25)
        assert blocked_read.done() is False
        await writer.commit()
        assert await asyncio.wait_for(blocked_read, timeout=3.0) == 20
    finally:
        await writer.close()
        await observer.disconnect()


@case("TX-017")
@pytest.mark.asyncio
async def test_deterministic_deadlock_reports_victim_1205(
    owner_connection: Connection,
    transaction_factory: Callable,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_tx_deadlock"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value INT NOT NULL)"
    )
    await owner_connection.execute(
        f"INSERT INTO {table} VALUES (1, 0), (2, 0)"
    )
    first = transaction_factory()
    second = transaction_factory()
    try:
        await first.begin()
        await second.begin()
        await first.execute(f"UPDATE {table} SET value = 1 WHERE id = 1")
        await second.execute(f"UPDATE {table} SET value = 2 WHERE id = 2")

        outcomes = await asyncio.wait_for(
            asyncio.gather(
                first.execute(f"UPDATE {table} SET value = 1 WHERE id = 2"),
                second.execute(f"UPDATE {table} SET value = 2 WHERE id = 1"),
                return_exceptions=True,
            ),
            timeout=10.0,
        )
        errors = [
            (index, outcome)
            for index, outcome in enumerate(outcomes)
            if isinstance(outcome, BaseException)
        ]
        successes = [
            outcome
            for outcome in outcomes
            if not isinstance(outcome, BaseException)
        ]
        assert len(errors) == 1
        assert len(successes) == 1
        assert successes == [1]
        victim_index, victim_error = errors[0]
        assert isinstance(victim_error, SqlError)
        assert victim_error.code == 1205

        survivor = second if victim_index == 0 else first
        await survivor.commit()
        assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == 2
    finally:
        await first.close()
        await second.close()


@case("TX-018")
@pytest.mark.asyncio
async def test_cancellation_is_explicitly_closed_and_recoverable(
    owner_connection: Connection,
    sa_connection: Connection,
    transaction_factory: Callable,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_tx_cancel"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")
    token = unique_sql_name("strict_tx_wait")
    transaction = transaction_factory()
    try:
        await transaction.begin()
        await transaction.execute(f"INSERT INTO {table} VALUES (1)")
        wait_task = asyncio.create_task(
            transaction.query(
                "WAITFOR DELAY '00:00:02'; "
                f"SELECT CAST(1 AS INT) AS value; -- {token}"
            )
        )
        await _wait_for_request(sa_connection, token, present=True)
        wait_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await wait_task

        await asyncio.wait_for(transaction.close(), timeout=4.0)
        await _wait_for_request(sa_connection, token, present=False)
        assert (
            await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}")
            == 0
        )

        await transaction.begin()
        await transaction.execute(f"INSERT INTO {table} VALUES (2)")
        await transaction.commit()
        assert await scalar(owner_connection, f"SELECT id FROM {table}") == 2
    finally:
        await transaction.close()


@case("TX-019")
@pytest.mark.asyncio
async def test_concurrent_calls_serialize_on_dedicated_client(
    transaction_factory: Callable,
) -> None:
    transaction = transaction_factory()
    try:
        await transaction.begin()

        async def delayed_value(value: int):
            result = await transaction.query(
                """
                WAITFOR DELAY '00:00:00.200';
                SELECT @P1 AS value;
                """,
                [value],
            )
            return result.fetchone()["value"]

        started = time.monotonic()
        values = await asyncio.gather(
            delayed_value(1),
            delayed_value(2),
            delayed_value(3),
        )
        elapsed = time.monotonic() - started
        assert values == [1, 2, 3]
        assert 0.5 <= elapsed < 2.0
        await transaction.rollback()
    finally:
        await transaction.close()


@case("TX-020")
@pytest.mark.parametrize(
    "implementation",
    ("public", "rust-core"),
    ids=("public-wrapper", "rust-core"),
)
@pytest.mark.asyncio
async def test_concurrent_begin_has_exactly_one_atomic_winner(
    sql_auth_config: SqlAuthConfig,
    implementation: str,
) -> None:
    transaction = _state_contract_transaction(
        sql_auth_config,
        implementation,
    )
    start = asyncio.Event()

    async def begin_once() -> None:
        await start.wait()
        await transaction.begin()

    tasks = [asyncio.create_task(begin_once()) for _ in range(16)]
    await asyncio.sleep(0)
    start.set()

    try:
        outcomes = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=10.0,
        )
        transaction_count = await scalar(transaction, "SELECT @@TRANCOUNT")

        successes = [
            outcome
            for outcome in outcomes
            if not isinstance(outcome, BaseException)
        ]
        failures = [
            outcome
            for outcome in outcomes
            if isinstance(outcome, BaseException)
        ]

        assert successes == [None]
        assert len(failures) == 15
        assert all(isinstance(error, RuntimeError) for error in failures)
        assert {
            str(error)
            for error in failures
        } == {"Transaction has already begun"}
        assert transaction_count == 1
    finally:
        await transaction.close()


@case("TX-021")
@pytest.mark.parametrize(
    "implementation",
    ("public", "rust-core"),
    ids=("public-wrapper", "rust-core"),
)
@pytest.mark.parametrize(
    ("first_action", "second_action"),
    (
        ("commit", "commit"),
        ("rollback", "rollback"),
        ("commit", "rollback"),
        ("rollback", "commit"),
    ),
    ids=(
        "commit-vs-commit",
        "rollback-vs-rollback",
        "commit-vs-rollback",
        "rollback-vs-commit",
    ),
)
@pytest.mark.asyncio
async def test_concurrent_settlement_has_exactly_one_atomic_winner(
    owner_connection: Connection,
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
    implementation: str,
    first_action: str,
    second_action: str,
) -> None:
    table = quote_identifier(unique_sql_name("strict_tx_state_race"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")

    transaction = _state_contract_transaction(
        sql_auth_config,
        implementation,
    )
    await transaction.begin()
    await transaction.execute(f"INSERT INTO {table} VALUES (1)")
    start = asyncio.Event()

    async def settle(action: str) -> None:
        await start.wait()
        await getattr(transaction, action)()

    actions = (first_action, second_action)
    tasks = [
        asyncio.create_task(settle(action))
        for action in actions
    ]
    await asyncio.sleep(0)
    start.set()

    try:
        outcomes = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=10.0,
        )
        winners = [
            action
            for action, outcome in zip(actions, outcomes, strict=True)
            if not isinstance(outcome, BaseException)
        ]
        failures = [
            outcome
            for outcome in outcomes
            if isinstance(outcome, BaseException)
        ]
        persisted_rows = await scalar(
            owner_connection,
            f"SELECT COUNT(*) FROM {table}",
        )

        assert len(winners) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], RuntimeError)

        winner = winners[0]
        terminal_state = (
            "committed" if winner == "commit" else "rolled back"
        )
        assert str(failures[0]) == (
            f"Transaction has already been {terminal_state}"
        )
        assert persisted_rows == (1 if winner == "commit" else 0)
    finally:
        await transaction.close()
