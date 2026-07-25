from __future__ import annotations

import asyncio
from collections.abc import Callable
import time

from fastmssql import (
    ApplicationIntent,
    Connection,
    OperationTimeoutError,
    PoolConfig,
    ProtocolError,
    SqlConnectionError,
    SslConfig,
    TlsError,
    TimeoutConfig,
)
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import scalar
from sql_auth_strict.tcp_fault_proxy import DownstreamGateProxy


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


def _small_pool() -> PoolConfig:
    return PoolConfig(
        max_size=2,
        min_idle=1,
        max_lifetime_secs=None,
        idle_timeout_secs=None,
        connection_timeout_secs=2,
        retry_connection=False,
    )


def _individual_connection(
    config: SqlAuthConfig,
    *,
    pool_config: PoolConfig | None = None,
    application_name: str | None = None,
    application_intent: ApplicationIntent | str | None = None,
) -> Connection:
    return Connection(
        server=config.host,
        port=config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        pool_config=pool_config or _small_pool(),
        application_name=application_name,
        application_intent=application_intent,
    )


def _closed_endpoint_connection() -> Connection:
    return Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="fastmssql_readiness_closed",
        password="not-a-credential",
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=1,
            min_idle=0,
            connection_timeout_secs=1,
            retry_connection=False,
        ),
    )


async def _application_session_rows(
    observer: Connection,
    application_name: str,
) -> list:
    return (
        await observer.query(
            """
            SELECT
                session_id,
                login_name,
                DB_NAME(database_id) AS database_name,
                program_name
            FROM sys.dm_exec_sessions
            WHERE program_name = @P1
              AND session_id <> @@SPID
            ORDER BY session_id
            """,
            [application_name],
        )
    ).rows()


async def _wait_for_application_session_count(
    observer: Connection,
    application_name: str,
    *,
    expected: int,
    timeout: float = 3.0,
) -> list:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = await _application_session_rows(observer, application_name)
        if len(rows) == expected:
            return rows
        await asyncio.sleep(0.02)
    rows = await _application_session_rows(observer, application_name)
    raise AssertionError(
        f"expected {expected} session(s) for {application_name!r}, "
        f"observed {len(rows)}"
    )


async def _pool_identity(connection: Connection) -> tuple[int, str]:
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


@case("CONN-001")
@pytest.mark.asyncio
async def test_connection_string_parses_host_and_port(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = Connection(
        sql_auth_config.connection_string(
            sql_auth_config.owner_user,
            sql_auth_config.owner_password,
        ),
        pool_config=_small_pool(),
    )
    async with connection:
        assert await scalar(connection, "SELECT 1") == 1


@case("CONN-002")
@pytest.mark.asyncio
async def test_individual_connection_parameters(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    async with connection:
        row = (
            await connection.query(
                """
                SELECT
                  CAST(SERVERPROPERTY('ServerName') AS NVARCHAR(128)) AS server,
                  DB_NAME() AS database_name,
                  CAST(SUSER_SNAME() AS NVARCHAR(128)) AS login_name
                """
            )
        ).fetchone()
        assert row is not None
        assert row["database_name"] == sql_auth_config.database
        assert row["login_name"] == sql_auth_config.owner_user


@case("CONN-003")
@pytest.mark.asyncio
async def test_connection_string_takes_precedence(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = Connection(
        sql_auth_config.connection_string(
            sql_auth_config.owner_user,
            sql_auth_config.owner_password,
        ),
        server="127.0.0.1",
        port=1,
        database="ignored_database",
        username="ignored_user",
        password="ignored_password",
        pool_config=_small_pool(),
    )
    async with connection:
        row = (
            await connection.query(
                """
                SELECT DB_NAME() AS database_name,
                       CAST(SUSER_SNAME() AS NVARCHAR(128)) AS login_name
                """
            )
        ).fetchone()
        assert row is not None
        assert row["database_name"] == sql_auth_config.database
        assert row["login_name"] == sql_auth_config.owner_user


@case("CONN-004")
@pytest.mark.asyncio
async def test_application_name_is_visible(
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("fastmssql_app")
    connection = _individual_connection(
        sql_auth_config, application_name=application_name
    )
    async with connection:
        assert await scalar(connection, "SELECT APP_NAME()") == application_name


@case("CONN-005")
@pytest.mark.asyncio
async def test_readwrite_application_intent(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(
        sql_auth_config,
        application_intent=ApplicationIntent.READ_WRITE,
    )
    async with connection:
        assert await scalar(connection, "SELECT 1") == 1
        await connection.execute(
            """
            DECLARE @probe TABLE (id INT PRIMARY KEY);
            INSERT INTO @probe VALUES (1);
            """
        )


@case("CONN-006")
@pytest.mark.asyncio
async def test_readonly_intent_on_standalone_server(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(
        sql_auth_config,
        application_intent=ApplicationIntent.READ_ONLY,
    )
    async with connection:
        updateability = await scalar(
            connection,
            """
            SELECT CAST(
              DATABASEPROPERTYEX(DB_NAME(), 'Updateability')
              AS NVARCHAR(32)
            )
            """,
        )
        assert updateability == "READ_WRITE"
        await connection.execute(
            """
            DECLARE @probe TABLE (id INT PRIMARY KEY);
            INSERT INTO @probe VALUES (1);
            """
        )


@case("CONN-007")
def test_invalid_application_intent_fails_before_network_io(
    sql_auth_config: SqlAuthConfig,
) -> None:
    with pytest.raises(ValueError, match="Invalid application_intent"):
        Connection(
            server="host-that-must-not-be-resolved.invalid",
            port=1,
            database=sql_auth_config.database,
            username=sql_auth_config.owner_user,
            password=sql_auth_config.owner_password,
            application_intent="not-an-intent",
        )


@case("CONN-008")
def test_malformed_connection_string_is_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid connection string"):
        Connection("not a valid ADO connection string")


@case("CONN-009")
@pytest.mark.asyncio
async def test_closed_local_port_is_rejected() -> None:
    connection = Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="fastmssql_closed_port",
        password="not-a-credential",
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=1,
            min_idle=1,
            connection_timeout_secs=1,
            retry_connection=False,
        ),
    )
    with pytest.raises(SqlConnectionError):
        await connection.connect()


@case("CONN-010")
@pytest.mark.asyncio
async def test_first_query_initializes_connection_lazily(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    assert await connection.is_connected() is False
    assert await scalar(connection, "SELECT 1") == 1
    assert await connection.is_connected() is True
    assert await connection.disconnect() is True


@case("CONN-011")
@pytest.mark.asyncio
async def test_explicit_connect_is_idempotent(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    assert await connection.connect() is True
    first_stats = await connection.pool_stats()
    assert await connection.connect() is True
    assert await connection.pool_stats() == first_stats
    assert await connection.disconnect() is True


@case("CONN-012")
@pytest.mark.asyncio
async def test_disconnect_before_and_after_connection(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    assert await connection.disconnect() is False
    assert await connection.connect() is True
    assert await connection.disconnect() is True
    assert await connection.disconnect() is False


@case("CONN-013")
@pytest.mark.asyncio
async def test_reconnect_after_disconnect(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    assert await connection.connect() is True
    assert await scalar(connection, "SELECT 1") == 1
    assert await connection.disconnect() is True
    assert await connection.connect() is True
    assert await scalar(connection, "SELECT 2") == 2
    assert await connection.disconnect() is True


@case("CONN-014")
@pytest.mark.asyncio
async def test_context_manager_normal_exit(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    async with connection:
        assert await connection.is_connected() is True
        assert await scalar(connection, "SELECT 1") == 1
    assert await connection.is_connected() is False


@case("CONN-015")
@pytest.mark.asyncio
async def test_context_manager_exceptional_exit(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    with pytest.raises(RuntimeError, match="intentional context failure"):
        async with connection:
            assert await connection.is_connected() is True
            raise RuntimeError("intentional context failure")
    assert await connection.is_connected() is False


@case("CONN-016")
@pytest.mark.asyncio
async def test_same_wrapper_supports_sequential_contexts(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    async with connection:
        assert await scalar(connection, "SELECT 1") == 1
    assert await connection.is_connected() is False
    async with connection:
        assert await scalar(connection, "SELECT 2") == 2
    assert await connection.is_connected() is False


@case("CONN-017")
@pytest.mark.asyncio
async def test_nested_context_resets_then_lazily_reconnects(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    async with connection:
        assert await connection.is_connected() is True
        async with connection:
            assert await scalar(connection, "SELECT 1") == 1
        assert await connection.is_connected() is False
        assert await scalar(connection, "SELECT 2") == 2
        assert await connection.is_connected() is True
    assert await connection.is_connected() is False


@case("CONN-018")
@pytest.mark.asyncio
async def test_is_connected_state_transitions(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    assert await connection.is_connected() is False
    assert await connection.connect() is True
    assert await connection.is_connected() is True
    assert await connection.disconnect() is True
    assert await connection.is_connected() is False


@case("CONN-019")
@pytest.mark.asyncio
async def test_pool_stats_keys_and_invariants(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    assert await connection.connect() is True
    stats = await connection.pool_stats()
    assert set(stats) == {
        "connected",
        "connections",
        "idle_connections",
        "active_connections",
        "max_size",
        "min_idle",
    }
    assert stats["connected"] is True
    assert 0 <= stats["idle_connections"] <= stats["connections"]
    assert stats["active_connections"] == (
        stats["connections"] - stats["idle_connections"]
    )
    assert stats["connections"] <= stats["max_size"]
    assert stats["max_size"] == 2
    assert stats["min_idle"] == 1
    assert await connection.disconnect() is True


@case("CONN-020")
@pytest.mark.asyncio
async def test_default_connect_rejects_lazy_false_positive() -> None:
    connection = _closed_endpoint_connection()
    try:
        with pytest.raises(SqlConnectionError):
            await connection.connect()
    finally:
        await connection.disconnect()


@case("CONN-021")
@pytest.mark.asyncio
async def test_explicit_lazy_connect_requires_ping_for_readiness() -> None:
    connection = _closed_endpoint_connection()
    try:
        assert await connection.connect(validate=False) is True
        assert await connection.is_connected() is True
        stats = await connection.pool_stats()
        assert stats["connections"] == 0
        assert stats["idle_connections"] == 0
        with pytest.raises(SqlConnectionError):
            await connection.ping()
    finally:
        disconnected = await connection.disconnect()
    assert disconnected is True
    assert await connection.is_connected() is False


@case("CONN-022")
@pytest.mark.asyncio
async def test_strict_connect_creates_authenticated_session_with_min_idle_zero(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("strict_connect_readiness")
    connection = _individual_connection(
        sql_auth_config,
        application_name=application_name,
        pool_config=PoolConfig(
            max_size=1,
            min_idle=0,
            connection_timeout_secs=2,
            retry_connection=False,
        ),
    )
    assert await _application_session_rows(sa_connection, application_name) == []
    try:
        assert await connection.connect() is True
        stats = await connection.pool_stats()
        assert stats["connections"] == 1
        assert stats["idle_connections"] == 1
        rows = await _wait_for_application_session_count(
            sa_connection,
            application_name,
            expected=1,
        )
        row = rows[0]
        assert row["login_name"] == sql_auth_config.owner_user
        assert row["database_name"] == sql_auth_config.database
        assert row["program_name"] == application_name
    finally:
        await connection.disconnect()
    await _wait_for_application_session_count(
        sa_connection,
        application_name,
        expected=0,
    )


@case("CONN-023")
@pytest.mark.asyncio
async def test_async_context_validates_before_body_entry() -> None:
    connection = _closed_endpoint_connection()
    body_entered = False
    try:
        with pytest.raises(SqlConnectionError):
            async with connection:
                body_entered = True
    finally:
        await connection.disconnect()
    assert body_entered is False


@case("CONN-024")
@pytest.mark.asyncio
async def test_ping_retires_killed_connection_then_recovers_explicitly(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("strict_ping_recovery")
    connection = _individual_connection(
        sql_auth_config,
        application_name=application_name,
        pool_config=PoolConfig(
            max_size=1,
            min_idle=0,
            connection_timeout_secs=2,
            test_on_check_out=False,
            retry_connection=False,
        ),
    )
    try:
        assert await connection.connect() is True
        first_session_id, first_connection_id = await _pool_identity(connection)
        await sa_connection.execute(f"KILL {first_session_id}")
        await _wait_for_application_session_count(
            sa_connection,
            application_name,
            expected=0,
        )

        with pytest.raises((SqlConnectionError, ProtocolError, TlsError)):
            await connection.ping()
        failed_stats = await connection.pool_stats()
        assert failed_stats["active_connections"] == 0
        assert failed_stats["connections"] == 0

        assert await connection.ping() is True
        second_session_id, second_connection_id = await _pool_identity(connection)
        assert second_connection_id != first_connection_id
        assert second_session_id > 0
    finally:
        await connection.disconnect()
    await _wait_for_application_session_count(
        sa_connection,
        application_name,
        expected=0,
    )


@pytest.mark.asyncio
async def test_ping_timeout_includes_saturated_pool_checkout(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(
        sql_auth_config,
        pool_config=PoolConfig(
            max_size=1,
            min_idle=0,
            connection_timeout_secs=1,
            retry_connection=False,
        ),
    )
    blocker = asyncio.create_task(
        scalar(connection, "WAITFOR DELAY '00:00:05'; SELECT 1")
    )
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if (await connection.pool_stats())["active_connections"] == 1:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("blocking query never acquired the only lease")

        started = time.monotonic()
        with pytest.raises(SqlConnectionError):
            await connection.ping()
        elapsed = time.monotonic() - started
        assert 0.8 <= elapsed < 2.0
    finally:
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        await connection.disconnect()


@pytest.mark.asyncio
async def test_ping_timeout_retires_partial_tds_response(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("strict_ping_timeout")
    proxy = DownstreamGateProxy(sql_auth_config.host, sql_auth_config.port)
    await proxy.start()
    connection = Connection(
        server=proxy.host,
        port=proxy.port,
        database=sql_auth_config.database,
        username=sql_auth_config.owner_user,
        password=sql_auth_config.owner_password,
        application_name=application_name,
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=1,
            min_idle=0,
            connection_timeout_secs=1,
            test_on_check_out=False,
            retry_connection=False,
        ),
        timeout_config=TimeoutConfig(
            acquire_timeout_secs=1.0,
            operation_timeout_secs=1.0,
        ),
    )
    try:
        assert await connection.connect() is True
        _, first_connection_id = await _pool_identity(connection)
        proxy.pause_downstream()
        started = time.monotonic()
        with pytest.raises(SqlConnectionError) as captured:
            await connection.ping()
        elapsed = time.monotonic() - started
        assert isinstance(captured.value, OperationTimeoutError)
        assert captured.value.operation == "ping"
        assert captured.value.phase == "operation"
        assert captured.value.retryable is False
        assert captured.value.connection_discarded is True
        assert captured.value.outcome_unknown is False
        assert 0.8 <= elapsed < 2.0
        await proxy.wait_until_downstream_held()
        proxy.resume_downstream()
        await _wait_for_application_session_count(
            sa_connection,
            application_name,
            expected=0,
        )

        assert await connection.ping() is True
        _, replacement_connection_id = await _pool_identity(connection)
        assert replacement_connection_id != first_connection_id
    finally:
        proxy.resume_downstream()
        await connection.disconnect()
        await proxy.close()
    await _wait_for_application_session_count(
        sa_connection,
        application_name,
        expected=0,
    )
