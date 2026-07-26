from __future__ import annotations

import asyncio
from collections.abc import Callable
import importlib
import inspect
import time

import fastmssql
from fastmssql import Connection, PoolConfig, SslConfig, TimeoutConfig
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.framework_apps import (
    wait_for_lifecycle_state,
    wait_for_pool_active,
    wait_for_session_count,
    wait_for_sql_request,
)
from sql_auth_strict.helpers import CleanupRegistry, quote_identifier, scalar
from sql_auth_strict.operation_metrics_assertions import (
    ENTRY_KEYS,
    OPERATION_NAMES,
    OUTCOME_KEYS,
    assert_operation_stats,
    operation_delta,
    zero_outcomes,
)


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


def metrics_connection(
    config: SqlAuthConfig,
    *,
    application_name: str,
    enabled: bool,
    max_size: int = 4,
    timeout_config: TimeoutConfig | None = None,
    lifecycle_config=None,
) -> Connection:
    kwargs = {}
    if timeout_config is not None:
        kwargs["timeout_config"] = timeout_config
    if lifecycle_config is not None:
        kwargs["lifecycle_config"] = lifecycle_config
    metrics_type = getattr(fastmssql, "OperationMetricsConfig")
    return Connection(
        server=config.host,
        port=config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        application_name=application_name,
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=max_size,
            min_idle=0,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=2,
            test_on_check_out=False,
            retry_connection=False,
        ),
        operation_metrics_config=metrics_type(enabled=enabled),
        **kwargs,
    )


def assert_exact_outcome(
    before: dict[str, object],
    after: dict[str, object],
    operation: str,
    outcome: str,
    *,
    count: int = 1,
) -> None:
    delta = operation_delta(before, after, operation)
    assert delta["started"] == delta["completed"] == count
    assert {key: delta[key] for key in OUTCOME_KEYS} == zero_outcomes(
        **{outcome: count}
    )


async def wait_for_operation_outcome(
    connection: Connection,
    before: dict[str, object],
    operation: str,
    outcome: str,
    *,
    expected: int = 1,
    timeout: float = 3.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    latest = await connection.operation_stats()
    while time.monotonic() < deadline:
        delta = operation_delta(before, latest, operation)
        if delta[outcome] == expected:
            return latest
        await asyncio.sleep(0.01)
        latest = await connection.operation_stats()
    raise AssertionError(
        f"expected {operation}.{outcome} delta {expected}, "
        f"observed {operation_delta(before, latest, operation)}"
    )


async def physical_connection_id(connection: Connection) -> str:
    value = await scalar(
        connection,
        """
        SELECT CONVERT(NVARCHAR(36), connection_id)
        FROM sys.dm_exec_connections
        WHERE session_id = @@SPID
        """,
    )
    assert isinstance(value, str)
    return value


@case("OPMET-001")
def test_operation_metrics_config_and_connection_copy_contract() -> None:
    metrics_type = getattr(fastmssql, "OperationMetricsConfig", None)
    assert metrics_type is not None
    assert (
        getattr(
            importlib.import_module("fastmssql.fastmssql"),
            "OperationMetricsConfig",
        )
        is metrics_type
    )
    assert metrics_type.__text_signature__ == "(enabled=False)"
    signature = inspect.signature(metrics_type)
    assert tuple(signature.parameters) == ("enabled",)
    assert signature.parameters["enabled"].default is False
    assert repr(metrics_type()) == "OperationMetricsConfig(enabled=False)"
    assert repr(metrics_type(enabled=True)) == ("OperationMetricsConfig(enabled=True)")
    assert "OperationMetricsConfig" in fastmssql.__all__

    for invalid in (0, 1, "true", None, object()):
        with pytest.raises(TypeError):
            metrics_type(enabled=invalid)
        mutable = metrics_type()
        with pytest.raises(TypeError):
            mutable.enabled = invalid
        assert mutable.enabled is False

    core = importlib.import_module("fastmssql.fastmssql")
    parameters = tuple(inspect.signature(core.Connection).parameters.values())
    assert parameters[-1].name == "operation_metrics_config"
    assert parameters[-1].default is None

    source = metrics_type(enabled=True)
    connection = Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="contract",
        password="not-used",
        ssl_config=SslConfig.development(),
        operation_metrics_config=source,
    )
    source.enabled = False
    assert connection.operation_metrics_config.enabled is True
    exposed = connection.operation_metrics_config
    exposed.enabled = False
    assert connection.operation_metrics_config.enabled is True
    with pytest.raises(AttributeError):
        connection.operation_metrics_config = metrics_type(enabled=False)


@case("OPMET-002")
@pytest.mark.asyncio
async def test_disabled_operation_metrics_remain_zero_across_work(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("strict_opmet_disabled")
    cancellation_token = unique_sql_name("strict_opmet_disabled_cancel")
    connection = metrics_connection(
        sql_auth_config,
        application_name=application_name,
        enabled=False,
        max_size=1,
    )
    query_task: asyncio.Task | None = None
    try:
        before = await connection.operation_stats()
        assert_operation_stats(before, enabled=False)
        assert await scalar(connection, "SELECT @P1", [2]) == 2
        with pytest.raises(fastmssql.SqlError) as captured:
            await connection.query("SELECT * FROM dbo.strict_opmet_disabled_missing")
        assert captured.value.code == 208

        query_task = asyncio.create_task(
            connection.query(
                f"""
                /* {cancellation_token} */
                WAITFOR DELAY '00:00:05';
                SELECT 1;
                """
            )
        )
        await wait_for_sql_request(
            sa_connection,
            cancellation_token,
            present=True,
            timeout=2.0,
        )
        query_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await query_task
        assert await scalar(connection, "SELECT @P1", [22]) == 22

        after = await connection.operation_stats()
        assert_operation_stats(after, enabled=False)
        assert after == before
        after["operations"]["query"]["started"] = 999
        assert await connection.operation_stats() == before
    finally:
        if query_task is not None and not query_task.done():
            query_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await query_task
        await connection.disconnect()
    await wait_for_session_count(
        sa_connection,
        application_name,
        expected=0,
    )


@case("OPMET-003")
@pytest.mark.asyncio
async def test_successful_operations_record_durations_and_buckets(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_opmet_success")
    table = quote_identifier(raw_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await sa_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value INT NOT NULL)"
    )
    application_name = unique_sql_name("strict_opmet_success_app")
    connection = metrics_connection(
        sql_auth_config,
        application_name=application_name,
        enabled=True,
    )
    try:
        before = await connection.operation_stats()
        assert_operation_stats(before, enabled=True)
        assert await connection.connect() is True
        assert await scalar(connection, "SELECT @P1", [3]) == 3
        simple = await connection.simple_query(
            "WAITFOR DELAY '00:00:00.050'; SELECT 33"
        )
        assert simple.fetchone()[0] == 33
        assert (
            await connection.execute(
                f"INSERT INTO {table} (id, value) VALUES (@P1, @P2)",
                [3, 30],
            )
            == 1
        )
        after = await connection.operation_stats()
        assert_operation_stats(after, enabled=True)

        for operation in ("connect", "query", "simple_query", "execute"):
            assert_exact_outcome(
                before,
                after,
                operation,
                "succeeded",
            )
        simple_entry = after["operations"]["simple_query"]
        assert simple_entry["duration_seconds_sum"] > 0.0
        assert simple_entry["duration_seconds_max"] >= 0.04
        assert any(
            count == 1
            for bound, count in zip(
                after["bucket_bounds_seconds"],
                simple_entry["duration_seconds_buckets"],
            )
            if bound >= 0.25
        )
    finally:
        await connection.disconnect()
    await wait_for_session_count(
        sa_connection,
        application_name,
        expected=0,
    )


@case("OPMET-004")
@pytest.mark.asyncio
async def test_errors_are_classified_and_preflight_is_excluded(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    lifecycle_type = getattr(fastmssql, "ConnectionLifecycleState")
    application_name = unique_sql_name("strict_opmet_errors")
    connection = metrics_connection(
        sql_auth_config,
        application_name=application_name,
        enabled=True,
        max_size=2,
        lifecycle_config=fastmssql.LifecycleConfig(
            shutdown_timeout_secs=3.0,
            force_timeout_secs=1.0,
        ),
    )
    holder: asyncio.Task | None = None
    shutdown: asyncio.Task | None = None
    try:
        assert await connection.connect() is True
        before_error = await connection.operation_stats()
        with pytest.raises(fastmssql.SqlError) as captured:
            await connection.query("SELECT * FROM dbo.strict_opmet_missing_table")
        assert captured.value.code == 208
        after_error = await connection.operation_stats()
        assert_exact_outcome(
            before_error,
            after_error,
            "query",
            "errors",
        )

        before_preflight = await connection.operation_stats()
        with pytest.raises(Exception):
            connection.query("SELECT @P1", [object()])
        with pytest.raises(Exception):
            connection.execute_batch([("SELECT @P1", [object()])])
        after_preflight = await connection.operation_stats()
        assert (
            after_preflight["operations"]["query"]
            == before_preflight["operations"]["query"]
        )
        assert (
            after_preflight["operations"]["execute_batch"]
            == before_preflight["operations"]["execute_batch"]
        )

        token = unique_sql_name("strict_opmet_closing")
        before_lifecycle = await connection.operation_stats()
        holder = asyncio.create_task(
            scalar(
                connection,
                f"""
                /* {token} */
                WAITFOR DELAY '00:00:01';
                SELECT 4;
                """,
            )
        )
        await wait_for_sql_request(
            sa_connection,
            token,
            present=True,
            timeout=2.0,
        )
        shutdown = asyncio.create_task(connection.disconnect())
        await wait_for_lifecycle_state(
            connection,
            lifecycle_type.CLOSING,
        )
        with pytest.raises(fastmssql.ConnectionLifecycleError):
            await connection.query("SELECT 44")
        assert await holder == 4
        holder = None
        assert await shutdown is True
        shutdown = None
        after_lifecycle = await connection.operation_stats()
        lifecycle_delta = operation_delta(
            before_lifecycle,
            after_lifecycle,
            "query",
        )
        assert lifecycle_delta["started"] == lifecycle_delta["completed"] == 2
        assert {key: lifecycle_delta[key] for key in OUTCOME_KEYS} == zero_outcomes(
            succeeded=1, errors=1
        )
        assert_operation_stats(after_lifecycle, enabled=True)
    finally:
        if holder is not None and not holder.done():
            holder.cancel()
            with pytest.raises(asyncio.CancelledError):
                await holder
        if shutdown is not None:
            await shutdown
        await connection.disconnect()
    await wait_for_session_count(
        sa_connection,
        application_name,
        expected=0,
    )


@case("OPMET-005")
@pytest.mark.asyncio
async def test_acquire_timeout_is_counted_once_and_recovers(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("strict_opmet_acquire_timeout")
    connection = metrics_connection(
        sql_auth_config,
        application_name=application_name,
        enabled=True,
        max_size=1,
        timeout_config=TimeoutConfig(
            connect_timeout_secs=2.0,
            acquire_timeout_secs=0.20,
            operation_timeout_secs=2.0,
            transaction_timeout_secs=2.0,
            rollback_timeout_secs=1.0,
        ),
    )
    holder = connection.transaction()
    try:
        await holder.begin()
        await wait_for_pool_active(connection, expected=1)
        before = await connection.operation_stats()
        with pytest.raises(fastmssql.OperationTimeoutError) as captured:
            await connection.query("SELECT @P1", [5])
        assert captured.value.phase == "acquire"
        assert captured.value.operation == "query"
        after_timeout = await connection.operation_stats()
        assert_exact_outcome(
            before,
            after_timeout,
            "query",
            "timed_out",
        )

        await holder.rollback()
        await holder.close()
        assert await scalar(connection, "SELECT @P1", [55]) == 55
        recovered = await connection.operation_stats()
        assert operation_delta(before, recovered, "query")["timed_out"] == 1
        assert recovered["operations"]["query"]["in_flight"] == 0
    finally:
        await holder.close()
        await connection.disconnect()
    await wait_for_session_count(
        sa_connection,
        application_name,
        expected=0,
    )


@case("OPMET-006")
@pytest.mark.asyncio
async def test_operation_and_shutdown_timeouts_keep_exact_types(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    operation_application = unique_sql_name("strict_opmet_operation_timeout")
    operation_connection = metrics_connection(
        sql_auth_config,
        application_name=operation_application,
        enabled=True,
        max_size=1,
        timeout_config=TimeoutConfig(
            connect_timeout_secs=2.0,
            acquire_timeout_secs=1.0,
            operation_timeout_secs=0.15,
            transaction_timeout_secs=1.0,
            rollback_timeout_secs=1.0,
        ),
    )
    try:
        before = await operation_connection.operation_stats()
        with pytest.raises(fastmssql.OperationTimeoutError) as captured:
            await operation_connection.query("WAITFOR DELAY '00:00:01'; SELECT 6")
        assert captured.value.phase == "operation"
        assert captured.value.operation == "query"
        after = await operation_connection.operation_stats()
        assert_exact_outcome(before, after, "query", "timed_out")
        assert await scalar(operation_connection, "SELECT 66") == 66
    finally:
        await operation_connection.disconnect()
    await wait_for_session_count(
        sa_connection,
        operation_application,
        expected=0,
    )

    shutdown_application = unique_sql_name("strict_opmet_shutdown_timeout")
    shutdown_connection = metrics_connection(
        sql_auth_config,
        application_name=shutdown_application,
        enabled=True,
        max_size=1,
        timeout_config=TimeoutConfig(
            connect_timeout_secs=2.0,
            acquire_timeout_secs=1.0,
            operation_timeout_secs=None,
            transaction_timeout_secs=None,
            rollback_timeout_secs=1.0,
        ),
        lifecycle_config=fastmssql.LifecycleConfig(
            shutdown_timeout_secs=0.15,
            force_timeout_secs=1.0,
        ),
    )
    query_task: asyncio.Task | None = None
    shutdown_task: asyncio.Task | None = None
    try:
        token = unique_sql_name("strict_opmet_forced_query")
        before_query = await shutdown_connection.operation_stats()
        query_task = asyncio.create_task(
            scalar(
                shutdown_connection,
                f"""
                /* {token} */
                WAITFOR DELAY '00:00:05';
                SELECT 6;
                """,
            )
        )
        await wait_for_sql_request(
            sa_connection,
            token,
            present=True,
            timeout=2.0,
        )
        before_disconnect = await shutdown_connection.operation_stats()
        shutdown_task = asyncio.create_task(shutdown_connection.disconnect())
        with pytest.raises(fastmssql.ShutdownTimeoutError):
            await shutdown_task
        shutdown_task = None
        with pytest.raises(fastmssql.ConnectionLifecycleError):
            await query_task
        query_task = None
        after_shutdown = await shutdown_connection.operation_stats()
        assert_exact_outcome(
            before_disconnect,
            after_shutdown,
            "disconnect",
            "timed_out",
        )
        assert_exact_outcome(
            before_query,
            after_shutdown,
            "query",
            "errors",
        )
        assert await scalar(shutdown_connection, "SELECT 666") == 666
    finally:
        if query_task is not None and not query_task.done():
            query_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await query_task
        if shutdown_task is not None:
            await shutdown_task
        await shutdown_connection.disconnect()
    await wait_for_session_count(
        sa_connection,
        shutdown_application,
        expected=0,
    )


@case("OPMET-007")
@pytest.mark.asyncio
async def test_server_confirmed_cancellation_is_counted_and_retires(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("strict_opmet_cancel")
    token = unique_sql_name("strict_opmet_cancel_request")
    connection = metrics_connection(
        sql_auth_config,
        application_name=application_name,
        enabled=True,
        max_size=1,
    )
    query_task: asyncio.Task | None = None
    try:
        original_connection_id = await physical_connection_id(connection)
        before = await connection.operation_stats()
        query_task = asyncio.create_task(
            connection.query(
                f"""
                /* {token} */
                WAITFOR DELAY '00:00:05';
                SELECT 7;
                """
            )
        )
        await wait_for_sql_request(
            sa_connection,
            token,
            present=True,
            timeout=2.0,
        )
        query_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await query_task
        query_task = None
        after_cancel = await wait_for_operation_outcome(
            connection,
            before,
            "query",
            "cancelled",
        )
        assert_exact_outcome(
            before,
            after_cancel,
            "query",
            "cancelled",
        )
        replacement_connection_id = await physical_connection_id(connection)
        assert replacement_connection_id != original_connection_id
        pool_stats = await connection.pool_stats()
        assert pool_stats["active_connections"] == 0
    finally:
        if query_task is not None and not query_task.done():
            query_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await query_task
        await connection.disconnect()
    await wait_for_session_count(
        sa_connection,
        application_name,
        expected=0,
    )


@case("OPMET-008")
@pytest.mark.asyncio
async def test_pooled_transaction_metrics_aggregate_into_owner_only(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_opmet_transactions")
    table = quote_identifier(raw_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await sa_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value INT NOT NULL)"
    )
    application_name = unique_sql_name("strict_opmet_transactions_app")
    connection = metrics_connection(
        sql_auth_config,
        application_name=application_name,
        enabled=True,
        max_size=2,
    )
    try:
        before = await connection.operation_stats()

        committed = connection.transaction()
        try:
            await committed.begin()
            assert await scalar(committed, "SELECT @P1", [8]) == 8
            simple = await committed.simple_query("SELECT 80")
            assert simple.fetchone()[0] == 80
            assert (
                await committed.execute(
                    f"INSERT INTO {table} VALUES (@P1, @P2)",
                    [1, 10],
                )
                == 1
            )
            query_results = await committed.query_batch(
                [("SELECT @P1", [81]), ("SELECT @P1", [82])]
            )
            assert [result.fetchone()[0] for result in query_results] == [
                81,
                82,
            ]
            assert await committed.execute_batch(
                [
                    (f"UPDATE {table} SET value = value + 1 WHERE id = 1", None),
                    (f"UPDATE {table} SET value = value + 1 WHERE id = 1", None),
                ]
            ) == [1, 1]
            await committed.commit()
        finally:
            await committed.close()

        rolled_back = connection.transaction()
        try:
            await rolled_back.begin()
            assert (
                await rolled_back.execute(
                    f"INSERT INTO {table} VALUES (@P1, @P2)",
                    [2, 20],
                )
                == 1
            )
            await rolled_back.rollback()
        finally:
            await rolled_back.close()

        async with connection.transaction() as automatic_commit:
            assert (
                await automatic_commit.execute(
                    f"INSERT INTO {table} VALUES (@P1, @P2)",
                    [3, 30],
                )
                == 1
            )

        with pytest.raises(RuntimeError, match="intentional opmet rollback"):
            async with connection.transaction() as automatic_rollback:
                assert (
                    await automatic_rollback.execute(
                        f"INSERT INTO {table} VALUES (@P1, @P2)",
                        [4, 40],
                    )
                    == 1
                )
                raise RuntimeError("intentional opmet rollback")

        after = await connection.operation_stats()
        expected = {
            "begin": 4,
            "query": 1,
            "simple_query": 1,
            "execute": 4,
            "query_batch": 1,
            "execute_batch": 1,
            "commit": 2,
            "rollback": 2,
            "close": 4,
        }
        for operation, count in expected.items():
            assert_exact_outcome(
                before,
                after,
                operation,
                "succeeded",
                count=count,
            )
        assert (
            await scalar(
                sa_connection,
                f"SELECT value FROM {table} WHERE id = 1",
            )
            == 12
        )
        assert (
            await scalar(
                sa_connection,
                f"SELECT COUNT(*) FROM {table} WHERE id IN (2, 4)",
            )
            == 0
        )
        assert (
            await scalar(
                sa_connection,
                f"SELECT value FROM {table} WHERE id = 3",
            )
            == 30
        )

        owner_before_direct = await connection.operation_stats()
        direct = fastmssql.Transaction(
            sql_auth_config.connection_string(
                sql_auth_config.owner_user,
                sql_auth_config.owner_password,
            )
        )
        try:
            await direct.begin()
            assert await scalar(direct, "SELECT 88") == 88
            assert (
                await direct.execute(
                    f"INSERT INTO {table} VALUES (@P1, @P2)",
                    [88, 880],
                )
                == 1
            )
            await direct.rollback()
        finally:
            await direct.close()
        assert await connection.operation_stats() == owner_before_direct
    finally:
        await connection.disconnect()
    await wait_for_session_count(
        sa_connection,
        application_name,
        expected=0,
    )


@case("OPMET-009")
@pytest.mark.asyncio
async def test_operation_metrics_persist_across_connection_generations(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("strict_opmet_generations")
    connection = metrics_connection(
        sql_auth_config,
        application_name=application_name,
        enabled=True,
        max_size=1,
    )
    before = await connection.operation_stats()
    try:
        assert await connection.connect(validate=False) is True
        assert await connection.ping() is True
        assert await connection.disconnect() is True
        assert await scalar(connection, "SELECT 9") == 9
        assert await connection.disconnect() is True
        async with connection:
            assert await connection.ping() is True
        assert await connection.disconnect() is False

        after = await connection.operation_stats()
        expected = {
            "connect": 2,
            "ping": 2,
            "query": 1,
            "disconnect": 4,
        }
        for operation in OPERATION_NAMES:
            count = expected.get(operation, 0)
            delta = operation_delta(before, after, operation)
            assert delta["started"] == delta["completed"] == count
            assert {key: delta[key] for key in OUTCOME_KEYS} == zero_outcomes(
                succeeded=count
            )
        assert_operation_stats(after, enabled=True)
    finally:
        await connection.disconnect()
    await wait_for_session_count(
        sa_connection,
        application_name,
        expected=0,
    )


@case("OPMET-010")
@pytest.mark.asyncio
async def test_batch_metrics_count_each_whole_public_call_once(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_opmet_batches")
    table = quote_identifier(raw_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await sa_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value INT NOT NULL)"
    )
    application_name = unique_sql_name("strict_opmet_batches_app")
    connection = metrics_connection(
        sql_auth_config,
        application_name=application_name,
        enabled=True,
        max_size=2,
    )
    try:
        before = await connection.operation_stats()
        query_results = await connection.query_batch(
            [("SELECT @P1", [10]), ("SELECT @P1", [11])]
        )
        assert [result.fetchone()[0] for result in query_results] == [10, 11]
        assert await connection.execute_batch(
            [
                (
                    f"INSERT INTO {table} (id, value) VALUES (@P1, @P2)",
                    [10, 100],
                ),
                (
                    f"INSERT INTO {table} (id, value) VALUES (@P1, @P2)",
                    [11, 110],
                ),
            ]
        ) == [1, 1]
        assert (
            await connection.bulk_insert(
                raw_table,
                ["id", "value"],
                [[12, 120], [13, 130]],
            )
            == 2
        )
        after = await connection.operation_stats()
        for operation in ("query_batch", "execute_batch", "bulk_insert"):
            assert_exact_outcome(
                before,
                after,
                operation,
                "succeeded",
            )
        for operation in ("begin", "commit", "query", "execute"):
            assert operation_delta(before, after, operation)["started"] == 0
        rows = (
            await sa_connection.query(f"SELECT id, value FROM {table} ORDER BY id")
        ).rows()
        assert [(row[0], row[1]) for row in rows] == [
            (10, 100),
            (11, 110),
            (12, 120),
            (13, 130),
        ]
        pool_stats = await connection.pool_stats()
        await wait_for_session_count(
            sa_connection,
            application_name,
            expected=pool_stats["connections"],
        )
    finally:
        await connection.disconnect()
    await wait_for_session_count(
        sa_connection,
        application_name,
        expected=0,
    )


@case("OPMET-012")
@pytest.mark.asyncio
async def test_operation_metrics_snapshot_is_fixed_and_private(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
) -> None:
    sentinel = "FASTMSSQL_OPMET_PRIVACY_SENTINEL_2026"
    connection = metrics_connection(
        sql_auth_config,
        application_name=sentinel,
        enabled=True,
        max_size=1,
    )
    try:
        assert (
            await scalar(
                connection,
                f"SELECT @P1 /* {sentinel} */",
                [sentinel],
            )
            == sentinel
        )
        snapshot = await connection.operation_stats()
        assert_operation_stats(snapshot, enabled=True)
        assert tuple(snapshot) == (
            "schema_version",
            "enabled",
            "bucket_bounds_seconds",
            "operations",
        )
        assert tuple(snapshot["operations"]) == OPERATION_NAMES
        assert all(
            set(entry) == ENTRY_KEYS for entry in snapshot["operations"].values()
        )
        flat_values = tuple(
            value
            for entry in snapshot["operations"].values()
            for value in entry.values()
        )
        assert all(
            value is None or type(value) in {int, float, bool, list}
            for value in flat_values
        )
        rendered = "\n".join(
            (
                repr(snapshot),
                repr(tuple(snapshot)),
                repr(tuple(snapshot["operations"])),
                repr(flat_values),
                repr(connection.operation_metrics_config),
            )
        )
        for forbidden in (
            sentinel,
            sql_auth_config.host,
            sql_auth_config.database,
            sql_auth_config.owner_user,
        ):
            assert forbidden not in rendered
        if sql_auth_config.owner_password in rendered:
            raise AssertionError("credential leaked into operation statistics")
    finally:
        await connection.disconnect()
    await wait_for_session_count(
        sa_connection,
        sentinel,
        expected=0,
    )
