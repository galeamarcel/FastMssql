from __future__ import annotations

import asyncio
from collections.abc import Callable
import time

from fastmssql import (
    Connection,
    PoolConfig,
    ProtocolError,
    SqlConnectionError,
    SqlError,
    SslConfig,
    TlsError,
)
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import quote_identifier, scalar


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


def _connection(
    config: SqlAuthConfig,
    pool_config: PoolConfig,
    *,
    application_name: str | None = None,
) -> Connection:
    return Connection(
        server=config.host,
        port=config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        pool_config=pool_config,
        application_name=application_name,
    )


def _assert_pool_invariants(stats: dict) -> None:
    assert stats["connected"] is True
    assert 0 <= stats["idle_connections"] <= stats["connections"]
    assert stats["active_connections"] == (
        stats["connections"] - stats["idle_connections"]
    )
    assert stats["connections"] <= stats["max_size"]


async def _wait_for_active(
    connection: Connection, expected: int, *, timeout: float = 2.0
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stats = await connection.pool_stats()
        if stats["active_connections"] == expected:
            return stats
        await asyncio.sleep(0.02)
    stats = await connection.pool_stats()
    raise AssertionError(
        f"expected {expected} active connection(s), observed {stats}"
    )


async def _wait_for_connection_count(
    connection: Connection, expected: int, *, timeout: float
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stats = await connection.pool_stats()
        if stats["connections"] == expected:
            return stats
        await asyncio.sleep(0.1)
    stats = await connection.pool_stats()
    raise AssertionError(
        f"expected {expected} managed connection(s), observed {stats}"
    )


async def _application_sessions(
    sa_connection: Connection, application_name: str
) -> set[int]:
    rows = (
        await sa_connection.query(
            """
            SELECT session_id
            FROM sys.dm_exec_sessions
            WHERE program_name = @P1
            """,
            [application_name],
        )
    ).rows()
    return {int(row["session_id"]) for row in rows}


async def _server_connection_id(
    sa_connection: Connection, session_id: int
) -> str:
    value = await scalar(
        sa_connection,
        """
        SELECT CONVERT(NVARCHAR(36), connection_id)
        FROM sys.dm_exec_connections
        WHERE session_id = @P1
        """,
        [session_id],
    )
    assert isinstance(value, str)
    return value


async def _wait_for_session_absent(
    sa_connection: Connection, session_id: int, *, timeout: float = 2.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        count = await scalar(
            sa_connection,
            """
            SELECT COUNT_BIG(*)
            FROM sys.dm_exec_sessions
            WHERE session_id = @P1
            """,
            [session_id],
        )
        if count == 0:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"session {session_id} was not terminated")


async def _wait_for_active_request(
    sa_connection: Connection, session_id: int, *, timeout: float = 2.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        count = await scalar(
            sa_connection,
            """
            SELECT COUNT_BIG(*)
            FROM sys.dm_exec_requests
            WHERE session_id = @P1
            """,
            [session_id],
        )
        if count == 1:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"session {session_id} did not start its request")


@case("POOL-001")
@pytest.mark.asyncio
async def test_default_pool_config_matches_runtime(
    sql_auth_config: SqlAuthConfig,
) -> None:
    pool_config = PoolConfig()
    assert pool_config.max_size == 20
    assert pool_config.min_idle == 2
    assert pool_config.max_lifetime_secs is None
    assert pool_config.idle_timeout_secs is None
    assert pool_config.connection_timeout_secs == 30
    connection = _connection(sql_auth_config, pool_config)
    assert await connection.connect() is True
    stats = await connection.pool_stats()
    _assert_pool_invariants(stats)
    assert stats["max_size"] == pool_config.max_size
    assert stats["min_idle"] == pool_config.min_idle
    assert stats["connections"] >= pool_config.min_idle
    assert await connection.disconnect() is True


@case("POOL-002")
@pytest.mark.asyncio
async def test_all_pool_presets_connect(
    sql_auth_config: SqlAuthConfig,
) -> None:
    presets = {
        "one": (PoolConfig.one(), (1, 1, 1800, 300, 30)),
        "low_resource": (
            PoolConfig.low_resource(),
            (3, 1, 900, 300, 15),
        ),
        "development": (
            PoolConfig.development(),
            (5, 1, 600, 180, 10),
        ),
        "high_throughput": (
            PoolConfig.high_throughput(),
            (25, 8, 1800, 600, 30),
        ),
        "performance": (
            PoolConfig.performance(),
            (30, 10, 7200, 1800, 10),
        ),
    }
    for pool_config, expected in presets.values():
        assert (
            pool_config.max_size,
            pool_config.min_idle,
            pool_config.max_lifetime_secs,
            pool_config.idle_timeout_secs,
            pool_config.connection_timeout_secs,
        ) == expected
        connection = _connection(sql_auth_config, pool_config)
        async with connection:
            assert await scalar(connection, "SELECT 1") == 1


@case("POOL-003")
def test_adaptive_pool_boundaries() -> None:
    expected = {
        0: (5, 2),
        1: (7, 2),
        5: (11, 3),
        20: (29, 9),
        100: (125, 41),
    }
    for workers, values in expected.items():
        pool_config = PoolConfig.adaptive(workers)
        assert (pool_config.max_size, pool_config.min_idle) == values


@case("POOL-004")
def test_invalid_pool_sizes_and_timeouts() -> None:
    with pytest.raises(ValueError, match="max_size must be >= 1"):
        PoolConfig(max_size=0)
    with pytest.raises(ValueError, match="cannot be greater than max_size"):
        PoolConfig(max_size=2, min_idle=3)
    with pytest.raises(ValueError, match="max_lifetime_secs must be > 0"):
        PoolConfig(max_lifetime_secs=0)
    with pytest.raises(ValueError, match="idle_timeout_secs must be > 0"):
        PoolConfig(idle_timeout_secs=0)
    with pytest.raises(ValueError, match="connection_timeout_secs must be >= 1"):
        PoolConfig(connection_timeout_secs=0)


@case("POOL-005")
@pytest.mark.asyncio
async def test_minimum_idle_warmup(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(
        sql_auth_config,
        PoolConfig(
            max_size=3,
            min_idle=2,
            connection_timeout_secs=2,
            retry_connection=False,
        ),
    )
    assert await connection.connect() is True
    stats = await connection.pool_stats()
    _assert_pool_invariants(stats)
    assert stats["connections"] >= 2
    assert stats["idle_connections"] >= 2
    assert await connection.disconnect() is True


@case("POOL-006")
@pytest.mark.asyncio
async def test_concurrent_lazy_initialization_uses_one_pool(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(
        sql_auth_config,
        PoolConfig(
            max_size=4,
            min_idle=0,
            connection_timeout_secs=2,
            retry_connection=False,
        ),
    )
    assert await connection.is_connected() is False
    values = await asyncio.gather(
        *(
            scalar(
                connection,
                "WAITFOR DELAY '00:00:00.100'; SELECT @P1",
                [value],
            )
            for value in range(8)
        )
    )
    assert values == list(range(8))
    stats = await connection.pool_stats()
    _assert_pool_invariants(stats)
    assert stats["max_size"] == 4
    assert 1 <= stats["connections"] <= 4
    assert await connection.disconnect() is True


@case("POOL-007")
@pytest.mark.asyncio
async def test_single_connection_pool_reuses_server_session(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(
        sql_auth_config,
        PoolConfig(
            max_size=1,
            min_idle=1,
            connection_timeout_secs=2,
            retry_connection=False,
        ),
    )
    first = await scalar(connection, "SELECT @@SPID")
    second = await scalar(connection, "SELECT @@SPID")
    assert first == second
    assert await connection.disconnect() is True


@case("POOL-008")
@pytest.mark.asyncio
async def test_parallel_acquisition_reaches_max_size(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(
        sql_auth_config,
        PoolConfig(
            max_size=3,
            min_idle=0,
            connection_timeout_secs=2,
            retry_connection=False,
        ),
    )
    tasks = [
        asyncio.create_task(
            scalar(
                connection,
                "WAITFOR DELAY '00:00:00.500'; SELECT @P1",
                [value],
            )
        )
        for value in range(3)
    ]
    stats = await _wait_for_active(connection, 3)
    assert stats["connections"] == 3
    assert await asyncio.gather(*tasks) == [0, 1, 2]
    assert await connection.disconnect() is True


@case("POOL-009")
@pytest.mark.asyncio
async def test_pool_saturation_times_out(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(
        sql_auth_config,
        PoolConfig(
            max_size=1,
            min_idle=0,
            connection_timeout_secs=1,
            retry_connection=False,
        ),
    )
    holder = asyncio.create_task(
        scalar(
            connection,
            "WAITFOR DELAY '00:00:02'; SELECT 1",
        )
    )
    await _wait_for_active(connection, 1)
    started = time.monotonic()
    with pytest.raises(SqlConnectionError, match="pool timeout"):
        await scalar(connection, "SELECT 2")
    elapsed = time.monotonic() - started
    assert 0.8 <= elapsed < 1.8
    assert await holder == 1
    assert await connection.disconnect() is True


@case("POOL-010")
@pytest.mark.asyncio
async def test_resources_return_after_task_completion(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(
        sql_auth_config,
        PoolConfig(
            max_size=2,
            min_idle=0,
            connection_timeout_secs=2,
            retry_connection=False,
        ),
    )
    assert await asyncio.gather(
        scalar(
            connection,
            "WAITFOR DELAY '00:00:00.100'; SELECT 1",
        ),
        scalar(
            connection,
            "WAITFOR DELAY '00:00:00.100'; SELECT 2",
        ),
    ) == [1, 2]
    stats = await _wait_for_active(connection, 0)
    assert stats["idle_connections"] == stats["connections"]
    assert await connection.disconnect() is True


@case("POOL-011")
@pytest.mark.asyncio
async def test_resources_return_after_query_error(
    sa_connection: Connection,
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(
        sql_auth_config,
        PoolConfig(
            max_size=1,
            min_idle=0,
            connection_timeout_secs=2,
            retry_connection=False,
        ),
    )
    first_session = int(await scalar(connection, "SELECT @@SPID"))
    first_connection_id = await _server_connection_id(
        sa_connection, first_session
    )
    with pytest.raises(SqlError):
        await connection.query("SELECT * FROM object_that_does_not_exist")
    stats = await _wait_for_active(connection, 0)
    assert stats["idle_connections"] == stats["connections"]
    second_session = int(await scalar(connection, "SELECT @@SPID"))
    second_connection_id = await _server_connection_id(
        sa_connection, second_session
    )
    assert second_connection_id == first_connection_id
    assert await connection.disconnect() is True


@case("POOL-012")
@pytest.mark.asyncio
async def test_resources_return_after_task_cancellation(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(
        sql_auth_config,
        PoolConfig(
            max_size=1,
            min_idle=0,
            connection_timeout_secs=2,
            retry_connection=False,
        ),
    )
    task = asyncio.create_task(
        scalar(
            connection,
            "WAITFOR DELAY '00:00:05'; SELECT 1",
        )
    )
    await _wait_for_active(connection, 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _wait_for_active(connection, 0)
    assert await scalar(connection, "SELECT 2") == 2
    assert await connection.disconnect() is True


@case("POOL-013")
@pytest.mark.asyncio
@pytest.mark.timeout(45)
async def test_idle_timeout_retires_connection(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(
        sql_auth_config,
        PoolConfig(
            max_size=1,
            min_idle=0,
            max_lifetime_secs=None,
            idle_timeout_secs=1,
            connection_timeout_secs=2,
            retry_connection=False,
        ),
    )
    assert await scalar(connection, "SELECT 1") == 1
    before = await connection.pool_stats()
    assert before["connections"] == 1
    after = await _wait_for_connection_count(
        connection, 0, timeout=35.0
    )
    assert after["idle_connections"] == 0
    assert await connection.disconnect() is True


@case("POOL-014")
@pytest.mark.asyncio
async def test_max_lifetime_retires_connection(
    sa_connection: Connection,
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("fastmssql_max_lifetime")
    connection = _connection(
        sql_auth_config,
        PoolConfig(
            max_size=1,
            min_idle=0,
            max_lifetime_secs=1,
            idle_timeout_secs=None,
            connection_timeout_secs=2,
            retry_connection=False,
        ),
        application_name=application_name,
    )
    first_query = asyncio.create_task(
        scalar(
            connection,
            "WAITFOR DELAY '00:00:01.100'; SELECT @@SPID",
        )
    )
    await _wait_for_active(connection, 1)
    first_sessions = await _application_sessions(
        sa_connection, application_name
    )
    assert len(first_sessions) == 1
    first_session = next(iter(first_sessions))
    first_connection_id = await _server_connection_id(
        sa_connection, first_session
    )
    assert await first_query == first_session

    second_query = asyncio.create_task(
        scalar(
            connection,
            "WAITFOR DELAY '00:00:00.100'; SELECT @@SPID",
        )
    )
    await _wait_for_active(connection, 1)
    second_sessions = await _application_sessions(
        sa_connection, application_name
    )
    assert len(second_sessions) == 1
    second_session = next(iter(second_sessions))
    second_connection_id = await _server_connection_id(
        sa_connection, second_session
    )
    assert await second_query == second_session
    assert second_connection_id != first_connection_id
    assert await connection.disconnect() is True


@case("POOL-015")
@pytest.mark.asyncio
async def test_checkout_validation_preserves_healthy_connection(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(
        sql_auth_config,
        PoolConfig(
            max_size=1,
            min_idle=1,
            connection_timeout_secs=2,
            test_on_check_out=True,
            retry_connection=False,
        ),
    )
    first_session = await scalar(connection, "SELECT @@SPID")
    second_session = await scalar(connection, "SELECT @@SPID")
    assert second_session == first_session
    _assert_pool_invariants(await connection.pool_stats())
    assert await connection.disconnect() is True


@case("POOL-016")
@pytest.mark.parametrize(
    "operation",
    ("query", "simple_query", "execute", "query_batch"),
)
@pytest.mark.asyncio
async def test_operation_error_discards_broken_connection_without_checkout_validation(
    sa_connection: Connection,
    sql_auth_config: SqlAuthConfig,
    operation: str,
) -> None:
    connection = _connection(
        sql_auth_config,
        PoolConfig(
            max_size=1,
            min_idle=1,
            connection_timeout_secs=2,
            test_on_check_out=False,
            retry_connection=False,
        ),
    )
    try:
        first_session = int(await scalar(connection, "SELECT @@SPID"))
        first_connection_id = await _server_connection_id(sa_connection, first_session)

        async def run_faulting_operation() -> None:
            delayed_select = "WAITFOR DELAY '00:00:05'; SELECT 1"
            if operation == "query":
                await connection.query(delayed_select)
            elif operation == "simple_query":
                await connection.simple_query(delayed_select)
            elif operation == "execute":
                await connection.execute(delayed_select)
            else:
                await connection.query_batch([(delayed_select, None)])

        fault_task = asyncio.create_task(run_faulting_operation())
        await _wait_for_active_request(sa_connection, first_session)
        await sa_connection.execute(f"KILL {first_session}")

        with pytest.raises((SqlConnectionError, ProtocolError, SqlError, TlsError)):
            await asyncio.wait_for(fault_task, timeout=3.0)
        await _wait_for_session_absent(sa_connection, first_session)

        second_session = int(
            await asyncio.wait_for(scalar(connection, "SELECT @@SPID"), timeout=3.0)
        )
        second_connection_id = await _server_connection_id(
            sa_connection, second_session
        )
        assert second_connection_id != first_connection_id
        stats = await connection.pool_stats()
        _assert_pool_invariants(stats)
        assert stats["connections"] == 1
        assert stats["idle_connections"] == 1
    finally:
        assert await connection.disconnect() is True


@case("POOL-017")
@pytest.mark.asyncio
async def test_rapid_context_lifecycle_does_not_leak_sessions(
    sa_connection: Connection,
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("fastmssql_lifecycle")
    assert await _application_sessions(sa_connection, application_name) == set()
    connection = _connection(
        sql_auth_config,
        PoolConfig.one(),
        application_name=application_name,
    )
    for _ in range(5):
        async with connection:
            assert await scalar(connection, "SELECT 1") == 1
    assert await connection.is_connected() is False

    deadline = time.monotonic() + 2.0
    remaining: set[int] = set()
    while time.monotonic() < deadline:
        remaining = await _application_sessions(
            sa_connection, application_name
        )
        if not remaining:
            break
        await asyncio.sleep(0.05)
    assert remaining == set()


@case("POOL-018")
@pytest.mark.asyncio
async def test_checkout_reset_rolls_back_leaked_local_transaction(
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
) -> None:
    table = quote_identifier(unique_sql_name("strict_pool_reset_tx"))
    connection = _connection(
        sql_auth_config,
        PoolConfig(
            max_size=1,
            min_idle=1,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=2,
            test_on_check_out=False,
            retry_connection=False,
        ),
    )

    async with connection:
        await connection.execute(f"CREATE TABLE {table} (id INT NOT NULL PRIMARY KEY)")
        first_session = int(await scalar(connection, "SELECT @@SPID"))
        baseline = (
            await connection.query(
                f"""
                SELECT
                    XACT_STATE() AS transaction_state,
                    COUNT_BIG(*) AS visible_rows
                FROM {table}
                """
            )
        ).fetchone()
        assert baseline is not None
        assert baseline["visible_rows"] == 0
        baseline_transaction_state = int(
            baseline["transaction_state"]
        )

        try:
            leaked_transaction = await connection.simple_query(
                f"""
                BEGIN TRANSACTION;
                INSERT INTO {table} (id) VALUES (1);
                SELECT @@SPID AS session_id;
                """
            )
            leaked_row = leaked_transaction.fetchone()
            assert leaked_row is not None
            assert leaked_row["session_id"] == first_session

            observed = (
                await connection.query(
                    f"""
                    SELECT
                        @@SPID AS session_id,
                        @@TRANCOUNT AS transaction_count,
                        XACT_STATE() AS transaction_state,
                        COUNT_BIG(*) AS visible_rows
                    FROM {table}
                    """
                )
            ).fetchone()
            assert observed is not None
            assert observed.to_dict() == {
                "session_id": first_session,
                "transaction_count": 0,
                "transaction_state": baseline_transaction_state,
                "visible_rows": 0,
            }
        finally:
            await connection.simple_query(
                f"""
                IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
                DROP TABLE IF EXISTS {table};
                """
            )


@case("POOL-019")
@pytest.mark.asyncio
async def test_nonfatal_sql_error_still_resets_session_state(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(
        sql_auth_config,
        PoolConfig(
            max_size=1,
            min_idle=1,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=2,
            test_on_check_out=False,
            retry_connection=False,
        ),
    )

    async with connection:
        first_session = int(await scalar(connection, "SELECT @@SPID"))

        with pytest.raises(SqlError) as captured:
            await connection.simple_query(
                """
                EXEC sys.sp_set_session_context
                    @key = N'fastmssql_pool_reset_after_error',
                    @value = N'contaminated',
                    @read_only = 1;
                THROW 51019, N'expected reset reproduction', 1;
                """
            )
        assert captured.value.code == 51019
        assert captured.value.severity == 16

        observed = (
            await connection.query(
                """
                SELECT
                    @@SPID AS session_id,
                    CONVERT(
                        NVARCHAR(128),
                        SESSION_CONTEXT(
                            N'fastmssql_pool_reset_after_error'
                        )
                    ) AS leaked_value
                """
            )
        ).fetchone()
        assert observed is not None
        assert observed.to_dict() == {
            "session_id": first_session,
            "leaked_value": None,
        }
