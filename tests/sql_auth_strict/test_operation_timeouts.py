from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
import time

import fastmssql
from fastmssql import Connection, PoolConfig, SslConfig, Transaction
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import CleanupRegistry, quote_identifier, scalar
from sql_auth_strict.tcp_fault_proxy import DownstreamGateProxy
from sql_auth_strict.timeout_fixtures import HandshakeBlackhole, wait_until


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


def timeout_api():
    assert hasattr(fastmssql, "TimeoutConfig")
    assert hasattr(fastmssql, "OperationTimeoutError")
    return fastmssql.TimeoutConfig, fastmssql.OperationTimeoutError


def assert_timeout(
    error: BaseException,
    *,
    phase: str,
    operation: str,
    retryable: bool,
    discarded: bool,
    outcome_unknown: bool,
) -> None:
    _, error_type = timeout_api()
    assert isinstance(error, error_type)
    assert isinstance(error, fastmssql.SqlConnectionError)
    assert error.phase == phase
    assert error.operation == operation
    assert error.retryable is retryable
    assert error.connection_discarded is discarded
    assert error.outcome_unknown is outcome_unknown
    assert isinstance(error.timeout_seconds, float)
    assert error.timeout_seconds > 0.0
    assert error.message


def timeout_connection(
    config: SqlAuthConfig,
    *,
    pool_config: PoolConfig,
    timeout_config,
    application_name: str,
    host: str | None = None,
    port: int | None = None,
) -> Connection:
    return Connection(
        server=config.host if host is None else host,
        port=config.port if port is None else port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        pool_config=pool_config,
        timeout_config=timeout_config,
        application_name=application_name,
    )


def bounded_pool(
    *,
    max_size: int = 1,
    min_idle: int = 0,
    connection_timeout_secs: int | None = 2,
) -> PoolConfig:
    return PoolConfig(
        max_size=max_size,
        min_idle=min_idle,
        max_lifetime_secs=None,
        idle_timeout_secs=None,
        connection_timeout_secs=connection_timeout_secs,
        test_on_check_out=False,
        retry_connection=False,
    )


async def _server_identity(connection) -> tuple[int, str]:
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


async def _identity_absent(
    observer: Connection,
    session_id: int,
    connection_id: str,
) -> bool:
    return (
        await scalar(
            observer,
            """
            SELECT COUNT(*)
            FROM sys.dm_exec_connections
            WHERE session_id = @P1
              AND CONVERT(NVARCHAR(36), connection_id) = @P2
            """,
            [session_id, connection_id],
        )
        == 0
    )


async def _application_session_count(
    observer: Connection,
    application_name: str,
) -> int:
    return await scalar(
        observer,
        """
        SELECT COUNT(*)
        FROM sys.dm_exec_sessions
        WHERE program_name = @P1
          AND session_id <> @@SPID
        """,
        [application_name],
    )


async def _application_sessions_absent(
    observer: Connection,
    application_name: str,
) -> bool:
    return await _application_session_count(observer, application_name) == 0


async def _row_count(
    connection: Connection,
    table: str,
    *,
    predicate: str = "1 = 1",
    parameters=None,
) -> int:
    return await scalar(
        connection,
        f"SELECT COUNT(*) FROM {table} WHERE {predicate}",
        parameters,
    )


async def _sample_tokens(
    observer: Connection,
    tokens: tuple[str, ...],
    stop: asyncio.Event,
) -> set[str]:
    observed: set[str] = set()
    prefix = tokens[0].rsplit("_", 1)[0]
    while not stop.is_set():
        result = await observer.query(
            """
            SELECT sql_text.text
            FROM sys.dm_exec_requests AS request
            CROSS APPLY sys.dm_exec_sql_text(request.sql_handle) AS sql_text
            WHERE request.session_id <> @@SPID
              AND sql_text.text LIKE @P1
            """,
            [f"%{prefix}%"],
        )
        texts = [str(row[0]) for row in result.rows()]
        for token in tokens:
            if any(token in text for text in texts):
                observed.add(token)
        await asyncio.sleep(0.005)
    return observed


async def _sample_request_identities(
    observer: Connection,
    *,
    application_name: str,
    sql_token: str,
    stop: asyncio.Event,
) -> set[tuple[int, int, object]]:
    observed: set[tuple[int, int, object]] = set()
    while not stop.is_set():
        result = await observer.query(
            """
            SELECT
                request.session_id,
                request.request_id,
                request.start_time
            FROM sys.dm_exec_requests AS request
            JOIN sys.dm_exec_sessions AS session
              ON session.session_id = request.session_id
            CROSS APPLY sys.dm_exec_sql_text(request.sql_handle) AS sql_text
            WHERE request.session_id <> @@SPID
              AND session.program_name = @P1
              AND sql_text.text LIKE @P2
            """,
            [application_name, f"%{sql_token}%"],
        )
        for row in result.rows():
            observed.add((int(row[0]), int(row[1]), row[2]))
        await asyncio.sleep(0.005)
    return observed


@case("TIME-001")
@pytest.mark.asyncio
async def test_timeout_configuration_fallback_precedence_and_clone_isolation(
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
) -> None:
    TimeoutConfig, _ = timeout_api()
    legacy = bounded_pool(connection_timeout_secs=2)
    external = TimeoutConfig(acquire_timeout_secs=0.375)
    connection = timeout_connection(
        sql_auth_config,
        pool_config=legacy,
        timeout_config=external,
        application_name=unique_sql_name("strict_timeout_config"),
    )
    transaction = connection.transaction()
    direct_default = Transaction(
        server=sql_auth_config.host,
        port=sql_auth_config.port,
        database=sql_auth_config.database,
        username=sql_auth_config.owner_user,
        password=sql_auth_config.owner_password,
        ssl_config=SslConfig.development(),
    )
    direct_explicit: Transaction | None = None
    legacy_connection: Connection | None = None
    bb8_default_connection: Connection | None = None

    try:
        assert connection.timeout_config.acquire_timeout_secs == 0.375
        external.acquire_timeout_secs = 9.0
        assert connection.timeout_config.acquire_timeout_secs == 0.375
        clone = connection.timeout_config
        clone.acquire_timeout_secs = 8.0
        assert connection.timeout_config.acquire_timeout_secs == 0.375
        with pytest.raises(AttributeError):
            connection.timeout_config = TimeoutConfig()

        assert transaction.timeout_config.acquire_timeout_secs == 0.375
        with pytest.raises(AttributeError):
            transaction.timeout_config = TimeoutConfig()
        await connection.connect()
        assert await scalar(connection, "SELECT 1") == 1

        legacy_connection = timeout_connection(
            sql_auth_config,
            pool_config=legacy,
            timeout_config=None,
            application_name=unique_sql_name("strict_timeout_legacy"),
        )
        assert legacy_connection.timeout_config.connect_timeout_secs == 2.0
        assert legacy_connection.timeout_config.acquire_timeout_secs == 2.0
        assert legacy_connection.timeout_config.operation_timeout_secs is None

        bb8_default_connection = timeout_connection(
            sql_auth_config,
            pool_config=bounded_pool(connection_timeout_secs=None),
            timeout_config=None,
            application_name=unique_sql_name("strict_timeout_bb8_default"),
        )
        assert (
            bb8_default_connection.timeout_config.connect_timeout_secs
            == 30.0
        )
        assert (
            bb8_default_connection.timeout_config.acquire_timeout_secs
            == 30.0
        )

        assert direct_default.timeout_config.acquire_timeout_secs == 30.0
        direct_explicit = Transaction(
            server=sql_auth_config.host,
            port=sql_auth_config.port,
            database=sql_auth_config.database,
            username=sql_auth_config.owner_user,
            password=sql_auth_config.owner_password,
            ssl_config=SslConfig.development(),
            timeout_config=external,
        )
        assert direct_explicit.timeout_config.acquire_timeout_secs == 9.0
        external.acquire_timeout_secs = 7.0
        assert direct_explicit.timeout_config.acquire_timeout_secs == 9.0
    finally:
        await transaction.close()
        await connection.disconnect()
        if legacy_connection is not None:
            await legacy_connection.disconnect()
        if bb8_default_connection is not None:
            await bb8_default_connection.disconnect()
        await direct_default.close()
        if direct_explicit is not None:
            await direct_explicit.close()


@case("TIME-002")
@pytest.mark.asyncio
async def test_physical_connect_timeout_covers_unanswered_prelogin(
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
) -> None:
    TimeoutConfig, error_type = timeout_api()

    async def exercise() -> None:
        async with HandshakeBlackhole() as blackhole:
            connection = timeout_connection(
                sql_auth_config,
                host=blackhole.host,
                port=blackhole.port,
                pool_config=bounded_pool(max_size=1, min_idle=1),
                timeout_config=TimeoutConfig(
                    connect_timeout_secs=0.2,
                    acquire_timeout_secs=1.0,
                ),
                application_name=unique_sql_name(
                    "strict_timeout_connect_blackhole"
                ),
            )
            try:
                started = time.monotonic()
                with pytest.raises(error_type) as captured:
                    await connection.connect(validate=False)
                elapsed = time.monotonic() - started
                assert 0.15 <= elapsed < 1.0
                assert_timeout(
                    captured.value,
                    phase="connect",
                    operation="connect",
                    retryable=True,
                    discarded=False,
                    outcome_unknown=False,
                )
                assert captured.value.timeout_seconds == pytest.approx(0.2)
                assert blackhole.accepted.is_set()
                assert await connection.is_connected() is False

                async def blackhole_is_drained() -> bool:
                    return blackhole.open_connections == 0

                await wait_until(blackhole_is_drained, timeout=1.0)
            finally:
                await connection.disconnect()

    await asyncio.wait_for(exercise(), timeout=2.0)


@case("TIME-003")
@pytest.mark.asyncio
async def test_saturated_pool_acquire_timeout_starts_no_application_sql(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    TimeoutConfig, error_type = timeout_api()
    table = quote_identifier(unique_sql_name("strict_timeout_acquire_log"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await sa_connection.execute(
        f"CREATE TABLE {table} (business_key NVARCHAR(100) PRIMARY KEY)"
    )
    business_key = unique_sql_name("acquire_never_submitted")
    connection = timeout_connection(
        sql_auth_config,
        pool_config=bounded_pool(max_size=1, connection_timeout_secs=2),
        timeout_config=TimeoutConfig(
            acquire_timeout_secs=0.2,
            operation_timeout_secs=2.0,
        ),
        application_name=unique_sql_name("strict_timeout_acquire"),
    )
    holder: asyncio.Future | None = None
    try:
        holder = asyncio.ensure_future(
            connection.query(
                "WAITFOR DELAY '00:00:00.600'; SELECT 1 AS value"
            )
        )

        async def pool_is_saturated() -> bool:
            return (
                await connection.pool_stats()
            )["active_connections"] == 1

        await wait_until(pool_is_saturated)
        started = time.monotonic()
        with pytest.raises(error_type) as captured:
            await connection.query_batch(
                [
                    (
                        f"""
                        INSERT INTO {table} (business_key) VALUES (@P1);
                        SELECT CAST(2 AS INT) AS value
                        """,
                        [business_key],
                    )
                ]
            )
        elapsed = time.monotonic() - started
        assert 0.15 <= elapsed < 0.8
        assert_timeout(
            captured.value,
            phase="acquire",
            operation="query_batch",
            retryable=True,
            discarded=False,
            outcome_unknown=False,
        )
        assert captured.value.timeout_seconds == pytest.approx(0.2)
        assert (await holder).fetchone()["value"] == 1
        assert (
            await _row_count(
                sa_connection,
                table,
                predicate="business_key = @P1",
                parameters=[business_key],
            )
            == 0
        )
        assert await scalar(connection, "SELECT 3") == 3
    finally:
        if holder is not None and not holder.done():
            holder.cancel()
            with suppress(asyncio.CancelledError):
                await holder
        await connection.disconnect()


@case("TIME-004")
@pytest.mark.asyncio
async def test_operation_timeout_retires_session_and_pool_recovers(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    TimeoutConfig, error_type = timeout_api()
    application_name = unique_sql_name("strict_timeout_operation")
    connection = timeout_connection(
        sql_auth_config,
        pool_config=bounded_pool(),
        timeout_config=TimeoutConfig(
            acquire_timeout_secs=1.0,
            operation_timeout_secs=0.2,
        ),
        application_name=application_name,
    )
    try:
        old_session_id, old_connection_id = await _server_identity(connection)
        token = unique_sql_name("strict_timeout_query")
        started = time.monotonic()
        with pytest.raises(error_type) as captured:
            await connection.query(
                f"WAITFOR DELAY '00:00:05'; SELECT 4; -- {token}"
            )
        assert 0.15 <= time.monotonic() - started < 0.8
        assert_timeout(
            captured.value,
            phase="operation",
            operation="query",
            retryable=False,
            discarded=True,
            outcome_unknown=True,
        )

        await wait_until(
            lambda: _identity_absent(
                sa_connection,
                old_session_id,
                old_connection_id,
            )
        )
        assert await scalar(connection, "SELECT 4") == 4
        new_session_id, new_connection_id = await _server_identity(connection)
        assert (new_session_id, new_connection_id) != (
            old_session_id,
            old_connection_id,
        )
        stats = await connection.pool_stats()
        assert stats["connections"] <= 1
        assert stats["active_connections"] == 0
    finally:
        await connection.disconnect()

    await wait_until(
        lambda: _application_sessions_absent(
            sa_connection,
            application_name,
        )
    )


@case("TIME-005")
@pytest.mark.asyncio
async def test_timed_out_parameterized_write_is_not_retried_and_reconciles(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    TimeoutConfig, error_type = timeout_api()
    raw_business = unique_sql_name("strict_timeout_business")
    raw_attempts = unique_sql_name("strict_timeout_attempts")
    raw_procedure = unique_sql_name("strict_timeout_apply_once")
    business = quote_identifier(raw_business)
    attempts = quote_identifier(raw_attempts)
    procedure = quote_identifier(raw_procedure)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {business}")
    cleanup_registry.add(f"DROP TABLE IF EXISTS {attempts}")
    cleanup_registry.add(f"DROP PROCEDURE IF EXISTS {procedure}")
    await sa_connection.execute(
        f"""
        CREATE TABLE {business} (
            business_key NVARCHAR(100) PRIMARY KEY,
            value INT NOT NULL
        )
        """
    )
    await sa_connection.execute(
        f"""
        CREATE TABLE {attempts} (
            attempt_id INT IDENTITY PRIMARY KEY,
            business_key NVARCHAR(100) NOT NULL
        )
        """
    )
    await sa_connection.simple_query(
        f"""
        CREATE PROCEDURE {procedure}
            @business_key NVARCHAR(100),
            @value INT
        AS
        BEGIN
            SET NOCOUNT ON;
            INSERT INTO {attempts} (business_key) VALUES (@business_key);
            IF NOT EXISTS (
                SELECT 1
                FROM {business}
                WHERE business_key = @business_key
            )
                INSERT INTO {business} (business_key, value)
                VALUES (@business_key, @value);
            WAITFOR DELAY '00:00:01';
            RAISERROR(N'fastmssql downstream gate', 0, 1) WITH NOWAIT;
            WAITFOR DELAY '00:00:05';
        END
        """
    )

    proxy = DownstreamGateProxy(sql_auth_config.host, sql_auth_config.port)
    await proxy.start()
    business_key = unique_sql_name("strict_timeout_write_key")
    connection = timeout_connection(
        sql_auth_config,
        host=proxy.host,
        port=proxy.port,
        pool_config=bounded_pool(),
        timeout_config=TimeoutConfig(
            acquire_timeout_secs=1.0,
            operation_timeout_secs=2.0,
        ),
        application_name=unique_sql_name("strict_timeout_write"),
    )
    write_task: asyncio.Task | None = None
    try:
        await connection.connect()
        assert await scalar(connection, "SELECT 5") == 5
        proxy.expect_client_disconnect()
        write_task = asyncio.ensure_future(
            connection.execute(
                f"EXEC {procedure} @business_key = @P1, @value = @P2",
                [business_key, 505],
            )
        )

        async def business_row_is_durable() -> bool:
            return (
                await _row_count(
                    sa_connection,
                    business,
                    predicate="business_key = @P1",
                    parameters=[business_key],
                )
                == 1
            )

        await wait_until(business_row_is_durable, timeout=1.5)
        proxy.pause_downstream()
        await proxy.wait_until_downstream_held()
        with pytest.raises(error_type) as captured:
            await write_task
        assert_timeout(
            captured.value,
            phase="operation",
            operation="execute",
            retryable=False,
            discarded=True,
            outcome_unknown=True,
        )
        proxy.resume_downstream()

        assert (
            await _row_count(
                sa_connection,
                attempts,
                predicate="business_key = @P1",
                parameters=[business_key],
            )
            == 1
        )
        assert (
            await _row_count(
                sa_connection,
                business,
                predicate="business_key = @P1",
                parameters=[business_key],
            )
            == 1
        )
        assert (
            await scalar(
                sa_connection,
                f"SELECT value FROM {business} WHERE business_key = @P1",
                [business_key],
            )
            == 505
        )
    finally:
        proxy.resume_downstream()
        if write_task is not None and not write_task.done():
            write_task.cancel()
            with suppress(asyncio.CancelledError):
                await write_task
        await connection.disconnect()
        await proxy.close()


@case("TIME-006")
@pytest.mark.asyncio
async def test_batch_and_bulk_share_one_absolute_operation_budget(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    TimeoutConfig, error_type = timeout_api()

    raw_batch_table = unique_sql_name("strict_timeout_batch_budget")
    batch_table = quote_identifier(raw_batch_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {batch_table}")
    await sa_connection.execute(
        f"CREATE TABLE {batch_table} (id INT PRIMARY KEY)"
    )
    batch_connection = timeout_connection(
        sql_auth_config,
        pool_config=bounded_pool(),
        timeout_config=TimeoutConfig(
            acquire_timeout_secs=1.0,
            operation_timeout_secs=0.25,
        ),
        application_name=unique_sql_name("strict_timeout_batch"),
    )
    token_prefix = unique_sql_name("strict_time_batch")
    batch_tokens = tuple(f"{token_prefix}_{index}" for index in range(1, 4))
    batch_stop = asyncio.Event()
    batch_sampler = asyncio.create_task(
        _sample_tokens(sa_connection, batch_tokens, batch_stop)
    )
    try:
        started = time.monotonic()
        with pytest.raises(error_type) as captured:
            await batch_connection.execute_batch(
                [
                    (
                        "WAITFOR DELAY '00:00:00.140'; "
                        f"INSERT INTO {batch_table} (id) VALUES (@P1); "
                        f"-- {token}",
                        [index],
                    )
                    for index, token in enumerate(batch_tokens, start=1)
                ]
            )
        elapsed = time.monotonic() - started
        assert 0.18 <= elapsed < 0.75
        assert_timeout(
            captured.value,
            phase="operation",
            operation="execute_batch",
            retryable=False,
            discarded=True,
            outcome_unknown=True,
        )
    finally:
        batch_stop.set()
        observed_batch_tokens = await batch_sampler
        await batch_connection.disconnect()

    assert set(batch_tokens[:2]) <= observed_batch_tokens
    assert batch_tokens[2] not in observed_batch_tokens

    async def batch_rolled_back() -> bool:
        return await _row_count(sa_connection, batch_table) == 0

    await wait_until(batch_rolled_back)

    raw_bulk_table = unique_sql_name("strict_timeout_bulk_budget")
    raw_trigger = unique_sql_name("strict_timeout_bulk_trigger")
    bulk_table = quote_identifier(raw_bulk_table)
    trigger = quote_identifier(raw_trigger)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {bulk_table}")
    cleanup_registry.add(f"DROP TRIGGER IF EXISTS {trigger}")
    await sa_connection.execute(
        f"CREATE TABLE {bulk_table} (id INT PRIMARY KEY)"
    )
    await sa_connection.simple_query(
        f"""
        CREATE TRIGGER {trigger}
        ON {bulk_table}
        AFTER INSERT
        AS
        BEGIN
            SET NOCOUNT ON;
            WAITFOR DELAY '00:00:00.500';
        END
        """
    )

    bulk_application = unique_sql_name("strict_timeout_bulk")
    bulk_connection = timeout_connection(
        sql_auth_config,
        pool_config=bounded_pool(max_size=1),
        timeout_config=TimeoutConfig(
            acquire_timeout_secs=2.0,
            operation_timeout_secs=0.85,
        ),
        application_name=bulk_application,
    )
    holder = bulk_connection.transaction()
    bulk_task: asyncio.Future | None = None
    bulk_stop = asyncio.Event()
    bulk_sampler = asyncio.create_task(
        _sample_request_identities(
            sa_connection,
            application_name=bulk_application,
            sql_token=raw_bulk_table,
            stop=bulk_stop,
        )
    )
    rows = [[index] for index in range(4001)]
    operation_phase_elapsed: float | None = None
    try:
        await holder.begin()
        bulk_task = asyncio.ensure_future(
            bulk_connection.bulk_insert(
                raw_bulk_table,
                ["id"],
                rows,
            )
        )

        async def bulk_is_waiting_for_checkout() -> bool:
            return (
                await bulk_connection.pool_stats()
            )["pending_gets"] == 1

        await wait_until(bulk_is_waiting_for_checkout)
        await asyncio.sleep(0.95)
        assert bulk_task.done() is False
        await holder.rollback()
        await holder.close()

        operation_phase_started = time.monotonic()
        with pytest.raises(error_type) as captured:
            await bulk_task
        operation_phase_elapsed = (
            time.monotonic() - operation_phase_started
        )
        assert_timeout(
            captured.value,
            phase="operation",
            operation="bulk_insert",
            retryable=False,
            discarded=True,
            outcome_unknown=True,
        )
    finally:
        if bulk_task is not None and not bulk_task.done():
            bulk_task.cancel()
            with suppress(asyncio.CancelledError):
                await bulk_task
        await holder.close()
        bulk_stop.set()
        bulk_requests = await bulk_sampler
        await bulk_connection.disconnect()

    # The 950 ms pending checkout is longer than the operation budget but
    # shorter than the separate acquire budget. Once checkout completes, the
    # first 500 ms request must finish, the second must start, and the shared
    # 850 ms operation budget must expire before a third request can start.
    # A reset-per-chunk timeout would allow all five requests to run.
    assert len(bulk_requests) == 2
    assert operation_phase_elapsed is not None
    assert 0.70 <= operation_phase_elapsed < 1.50

    async def bulk_rolled_back() -> bool:
        return await _row_count(sa_connection, bulk_table) == 0

    await wait_until(bulk_rolled_back)


@case("TIME-007")
@pytest.mark.asyncio
async def test_expired_idle_transaction_retires_lease_and_rolls_back(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    TimeoutConfig, error_type = timeout_api()
    table = quote_identifier(unique_sql_name("strict_timeout_tx_lifetime"))
    probe = quote_identifier(unique_sql_name("strict_timeout_tx_probe"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    cleanup_registry.add(f"DROP TABLE IF EXISTS {probe}")
    await sa_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY)"
    )
    await sa_connection.execute(
        f"CREATE TABLE {probe} (token NVARCHAR(100) PRIMARY KEY)"
    )

    connection = timeout_connection(
        sql_auth_config,
        pool_config=bounded_pool(max_size=1),
        timeout_config=TimeoutConfig(
            acquire_timeout_secs=1.0,
            operation_timeout_secs=2.0,
            transaction_timeout_secs=0.25,
        ),
        application_name=unique_sql_name("strict_timeout_tx_idle"),
    )
    first = connection.transaction()
    waiting = connection.transaction()
    waiting_begin: asyncio.Task | None = None
    sampler: asyncio.Task | None = None
    stop = asyncio.Event()
    token = unique_sql_name("strict_timeout_expired_token")
    try:
        await first.begin()
        first_session_id, first_connection_id = await _server_identity(first)
        await first.execute(f"INSERT INTO {table} (id) VALUES (1)")

        waiting_begin = asyncio.create_task(waiting.begin())

        async def one_lease_is_active() -> bool:
            return (
                await connection.pool_stats()
            )["active_connections"] == 1

        await wait_until(one_lease_is_active)
        await asyncio.sleep(0.30)
        assert waiting_begin.done() is False

        sampler = asyncio.create_task(
            _sample_tokens(sa_connection, (token,), stop)
        )
        with pytest.raises(error_type) as captured:
            await first.query(
                "WAITFOR DELAY '00:00:01'; "
                f"INSERT INTO {probe} (token) VALUES (@P1); -- {token}",
                [token],
            )
        assert_timeout(
            captured.value,
            phase="transaction",
            operation="query",
            retryable=False,
            discarded=True,
            outcome_unknown=False,
        )
        assert captured.value.timeout_seconds == pytest.approx(0.25)
        assert first.is_connected() is False
        stop.set()
        observed = await sampler
        sampler = None
        assert token not in observed

        await asyncio.wait_for(waiting_begin, timeout=2.0)
        waiting_session_id, waiting_connection_id = await _server_identity(
            waiting
        )
        assert (waiting_session_id, waiting_connection_id) != (
            first_session_id,
            first_connection_id,
        )
        assert await _row_count(sa_connection, table) == 0
        assert await _row_count(sa_connection, probe) == 0
        await waiting.rollback()
        await wait_until(
            lambda: _identity_absent(
                sa_connection,
                first_session_id,
                first_connection_id,
            )
        )
    finally:
        stop.set()
        if sampler is not None:
            await sampler
        if waiting_begin is not None and not waiting_begin.done():
            waiting_begin.cancel()
            with suppress(asyncio.CancelledError):
                await waiting_begin
        await first.close()
        await waiting.close()
        await connection.disconnect()


async def _exercise_deadline_winner(
    *,
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    operation_timeout: float,
    transaction_timeout: float,
    delay_before_query: float,
    expected_phase: str,
    expected_timeout: float,
) -> None:
    TimeoutConfig, error_type = timeout_api()
    application_name = unique_sql_name(
        f"strict_timeout_winner_{expected_phase}"
    )
    connection = timeout_connection(
        sql_auth_config,
        pool_config=bounded_pool(max_size=1),
        timeout_config=TimeoutConfig(
            acquire_timeout_secs=1.0,
            operation_timeout_secs=operation_timeout,
            transaction_timeout_secs=transaction_timeout,
        ),
        application_name=application_name,
    )
    transaction = connection.transaction()
    recovery = connection.transaction()
    try:
        await transaction.begin()
        old_session_id, old_connection_id = await _server_identity(transaction)
        if delay_before_query:
            await asyncio.sleep(delay_before_query)
        token = unique_sql_name(f"strict_timeout_{expected_phase}_winner")
        started = time.monotonic()
        with pytest.raises(error_type) as captured:
            await transaction.query(
                f"WAITFOR DELAY '00:00:05'; SELECT 8; -- {token}"
            )
        elapsed = time.monotonic() - started
        assert elapsed < 0.8
        assert_timeout(
            captured.value,
            phase=expected_phase,
            operation="query",
            retryable=False,
            discarded=True,
            outcome_unknown=True,
        )
        assert captured.value.timeout_seconds == pytest.approx(
            expected_timeout
        )
        assert transaction.is_connected() is False
        await wait_until(
            lambda: _identity_absent(
                sa_connection,
                old_session_id,
                old_connection_id,
            )
        )

        await recovery.begin()
        new_session_id, new_connection_id = await _server_identity(recovery)
        assert (new_session_id, new_connection_id) != (
            old_session_id,
            old_connection_id,
        )
        await asyncio.sleep(0.10)
        assert await scalar(recovery, "SELECT 808") == 808
        assert await _server_identity(recovery) == (
            new_session_id,
            new_connection_id,
        )
        await recovery.rollback()
    finally:
        await transaction.close()
        await recovery.close()
        await connection.disconnect()

    await wait_until(
        lambda: _application_sessions_absent(
            sa_connection,
            application_name,
        )
    )


@case("TIME-008")
@pytest.mark.asyncio
async def test_earliest_operation_or_transaction_deadline_wins(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    await _exercise_deadline_winner(
        sql_auth_config=sql_auth_config,
        sa_connection=sa_connection,
        unique_sql_name=unique_sql_name,
        operation_timeout=0.20,
        transaction_timeout=2.00,
        delay_before_query=0.0,
        expected_phase="operation",
        expected_timeout=0.20,
    )
    await _exercise_deadline_winner(
        sql_auth_config=sql_auth_config,
        sa_connection=sa_connection,
        unique_sql_name=unique_sql_name,
        operation_timeout=2.00,
        transaction_timeout=0.35,
        delay_before_query=0.20,
        expected_phase="transaction",
        expected_timeout=0.35,
    )


@case("TIME-009")
@pytest.mark.asyncio
async def test_commit_unknown_and_rollback_close_timeouts_preserve_precedence(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    TimeoutConfig, error_type = timeout_api()
    unknown_type = getattr(fastmssql, "CommitOutcomeUnknown")
    table = quote_identifier(unique_sql_name("strict_timeout_settlement"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await sa_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY)"
    )

    commit_proxy = DownstreamGateProxy(
        sql_auth_config.host,
        sql_auth_config.port,
    )
    await commit_proxy.start()
    commit_connection = timeout_connection(
        sql_auth_config,
        host=commit_proxy.host,
        port=commit_proxy.port,
        pool_config=bounded_pool(),
        timeout_config=TimeoutConfig(
            acquire_timeout_secs=1.0,
            operation_timeout_secs=0.2,
            rollback_timeout_secs=0.2,
        ),
        application_name=unique_sql_name("strict_timeout_commit"),
    )
    committing = commit_connection.transaction()
    commit_task: asyncio.Task | None = None
    try:
        await committing.begin()
        await committing.execute(f"INSERT INTO {table} (id) VALUES (1)")
        commit_proxy.pause_downstream()
        commit_proxy.expect_client_disconnect()
        commit_task = asyncio.create_task(committing.commit())

        async def committed_row_is_visible() -> bool:
            return await _row_count(sa_connection, table) == 1

        await wait_until(committed_row_is_visible)
        await commit_proxy.wait_until_downstream_held()
        with pytest.raises(unknown_type) as captured:
            await commit_task
        error = captured.value
        assert error.operation == "commit"
        assert error.retryable is False
        assert error.connection_discarded is True
        assert isinstance(error.__cause__, error_type)
        assert error.__cause__.phase in {"operation", "transaction"}
        assert error.__cause__.operation == "commit"
        assert error.__cause__.outcome_unknown is True
        assert error.__cause__.connection_discarded is True
        assert await _row_count(sa_connection, table) == 1
    finally:
        commit_proxy.resume_downstream()
        if commit_task is not None and not commit_task.done():
            commit_task.cancel()
            with suppress(asyncio.CancelledError):
                await commit_task
        await committing.close()
        await commit_connection.disconnect()
        await commit_proxy.close()

    rollback_proxy = DownstreamGateProxy(
        sql_auth_config.host,
        sql_auth_config.port,
    )
    await rollback_proxy.start()
    rollback_connection = timeout_connection(
        sql_auth_config,
        host=rollback_proxy.host,
        port=rollback_proxy.port,
        pool_config=bounded_pool(),
        timeout_config=TimeoutConfig(
            acquire_timeout_secs=1.0,
            operation_timeout_secs=2.0,
            rollback_timeout_secs=0.2,
        ),
        application_name=unique_sql_name("strict_timeout_rollback"),
    )
    rolling_back = rollback_connection.transaction()
    rollback_task: asyncio.Task | None = None
    try:
        await rolling_back.begin()
        await rolling_back.execute(f"INSERT INTO {table} (id) VALUES (2)")
        rollback_proxy.pause_downstream()
        rollback_proxy.expect_client_disconnect()
        rollback_task = asyncio.create_task(rolling_back.rollback())
        await rollback_proxy.wait_until_downstream_held()
        with pytest.raises(error_type) as captured:
            await rollback_task
        assert_timeout(
            captured.value,
            phase="rollback",
            operation="rollback",
            retryable=False,
            discarded=True,
            outcome_unknown=False,
        )
        rollback_proxy.resume_downstream()

        async def rollback_row_is_absent() -> bool:
            return (
                await _row_count(
                    sa_connection,
                    table,
                    predicate="id = 2",
                )
                == 0
            )

        await wait_until(rollback_row_is_absent)
    finally:
        rollback_proxy.resume_downstream()
        if rollback_task is not None and not rollback_task.done():
            rollback_task.cancel()
            with suppress(asyncio.CancelledError):
                await rollback_task
        await rolling_back.close()
        await rollback_connection.disconnect()
        await rollback_proxy.close()

    close_proxy = DownstreamGateProxy(
        sql_auth_config.host,
        sql_auth_config.port,
    )
    await close_proxy.start()
    close_connection = timeout_connection(
        sql_auth_config,
        host=close_proxy.host,
        port=close_proxy.port,
        pool_config=bounded_pool(),
        timeout_config=TimeoutConfig(
            acquire_timeout_secs=1.0,
            operation_timeout_secs=2.0,
            rollback_timeout_secs=0.2,
        ),
        application_name=unique_sql_name("strict_timeout_close"),
    )
    closing = close_connection.transaction()
    close_task: asyncio.Task | None = None
    try:
        await closing.begin()
        await closing.execute(f"INSERT INTO {table} (id) VALUES (3)")
        close_proxy.pause_downstream()
        close_proxy.expect_client_disconnect()
        close_task = asyncio.create_task(closing.close())
        await close_proxy.wait_until_downstream_held()
        with pytest.raises(error_type) as captured:
            await close_task
        assert_timeout(
            captured.value,
            phase="rollback",
            operation="close",
            retryable=False,
            discarded=True,
            outcome_unknown=False,
        )
        close_proxy.resume_downstream()

        async def close_row_is_absent() -> bool:
            return (
                await _row_count(
                    sa_connection,
                    table,
                    predicate="id = 3",
                )
                == 0
            )

        await wait_until(close_row_is_absent)
    finally:
        close_proxy.resume_downstream()
        if close_task is not None and not close_task.done():
            close_task.cancel()
            with suppress(asyncio.CancelledError):
                await close_task
        await closing.close()
        await close_connection.disconnect()
        await close_proxy.close()


@case("TIME-011")
@pytest.mark.asyncio
async def test_checkout_reset_uses_acquire_deadline_before_application_sql(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    TimeoutConfig, error_type = timeout_api()
    table = quote_identifier(
        unique_sql_name("strict_reset_acquire_timeout")
    )
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await sa_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")

    application_name = unique_sql_name("strict_reset_acquire")
    proxy = DownstreamGateProxy(
        sql_auth_config.host,
        sql_auth_config.port,
    )
    await proxy.start()
    connection = timeout_connection(
        sql_auth_config,
        host=proxy.host,
        port=proxy.port,
        pool_config=bounded_pool(max_size=1, min_idle=0),
        timeout_config=TimeoutConfig(
            acquire_timeout_secs=0.2,
            operation_timeout_secs=0.6,
        ),
        application_name=application_name,
    )
    write_task: asyncio.Task | None = None
    try:
        first_session, first_connection_id = await _server_identity(connection)

        proxy.pause_downstream()
        proxy.expect_client_disconnect()
        started = time.monotonic()
        write_task = asyncio.ensure_future(
            connection.execute(f"INSERT INTO {table} (id) VALUES (11)")
        )
        await proxy.wait_until_downstream_held()

        with pytest.raises(error_type) as captured:
            await write_task
        elapsed = time.monotonic() - started
        assert 0.1 <= elapsed < 0.5
        assert_timeout(
            captured.value,
            phase="acquire",
            operation="execute",
            retryable=True,
            discarded=False,
            outcome_unknown=False,
        )
        assert captured.value.timeout_seconds == pytest.approx(0.2)

        proxy.resume_downstream()
        assert await _row_count(sa_connection, table) == 0
        await wait_until(
            lambda: _identity_absent(
                sa_connection,
                first_session,
                first_connection_id,
            )
        )

        second_session, second_connection_id = await _server_identity(connection)
        assert (second_session, second_connection_id) != (
            first_session,
            first_connection_id,
        )
        assert await scalar(connection, "SELECT 11") == 11
    finally:
        proxy.resume_downstream()
        if write_task is not None and not write_task.done():
            write_task.cancel()
            with suppress(asyncio.CancelledError):
                await write_task
        await connection.disconnect()
        await proxy.close()

    await wait_until(
        lambda: _application_sessions_absent(
            sa_connection,
            application_name,
        )
    )
