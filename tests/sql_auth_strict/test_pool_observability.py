from __future__ import annotations

import asyncio
from collections.abc import Callable
import math
import time

from fastmssql import (
    Connection,
    OperationTimeoutError,
    PoolConfig,
    SslConfig,
    TimeoutConfig,
)
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import scalar


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]

POOL_STATS_KEYS = {
    "connected",
    "connections",
    "idle_connections",
    "active_connections",
    "max_size",
    "min_idle",
    "get_started",
    "get_direct",
    "get_waited",
    "get_timed_out",
    "pending_gets",
    "get_wait_time_seconds",
    "connections_created",
    "connections_closed_broken",
    "connections_closed_invalid",
    "connections_closed_max_lifetime",
    "connections_closed_idle_timeout",
}
POOL_INTEGER_KEYS = POOL_STATS_KEYS - {
    "connected",
    "min_idle",
    "get_wait_time_seconds",
}
POOL_CLOSE_REASON_KEYS = {
    "connections_closed_broken",
    "connections_closed_invalid",
    "connections_closed_max_lifetime",
    "connections_closed_idle_timeout",
}


def _connection(
    config: SqlAuthConfig,
    pool_config: PoolConfig,
    *,
    timeout_config: TimeoutConfig | None = None,
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
        timeout_config=timeout_config,
        application_name=application_name,
    )


def _assert_pool_metrics(stats: dict, *, connected: bool) -> None:
    assert set(stats) == POOL_STATS_KEYS
    assert type(stats["connected"]) is bool
    assert stats["connected"] is connected
    assert type(stats["get_wait_time_seconds"]) is float
    assert math.isfinite(stats["get_wait_time_seconds"])
    assert stats["get_wait_time_seconds"] >= 0.0
    assert all(type(stats[key]) is int for key in POOL_INTEGER_KEYS)
    assert all(stats[key] >= 0 for key in POOL_INTEGER_KEYS)
    assert stats["active_connections"] == (
        stats["connections"] - stats["idle_connections"]
    )
    assert 0 <= stats["idle_connections"] <= stats["connections"]
    assert stats["connections"] <= stats["max_size"]
    assert stats["get_started"] == (
        stats["get_direct"]
        + stats["get_waited"]
        + stats["get_timed_out"]
        + stats["pending_gets"]
    )


async def _wait_for_metric(
    connection: Connection,
    key: str,
    expected: int,
    *,
    timeout: float = 2.0,
) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = await connection.pool_stats()
        _assert_pool_metrics(last, connected=True)
        if last[key] == expected:
            return last
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"expected pool metric {key}={expected}, observed {last.get(key)!r}"
    )


async def _server_connection_id(
    sa_connection: Connection,
    session_id: int,
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


async def _wait_for_active_request(
    sa_connection: Connection,
    session_id: int,
    *,
    timeout: float = 2.0,
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


async def _wait_for_session_absent(
    sa_connection: Connection,
    session_id: int,
    *,
    timeout: float = 2.0,
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


async def _cancel_unfinished(*tasks: asyncio.Task | None) -> None:
    pending = [task for task in tasks if task is not None and not task.done()]
    for task in pending:
        task.cancel()
    for task in pending:
        try:
            await task
        except asyncio.CancelledError:
            pass


@case("OBS-002")
@pytest.mark.asyncio
async def test_direct_checkout_and_creation_counters_are_exact(
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
    try:
        assert await connection.connect() is True
        before = await connection.pool_stats()
        _assert_pool_metrics(before, connected=True)
        assert before["connections_created"] == 1

        assert await scalar(connection, "SELECT @P1", [42]) == 42
        after = await connection.pool_stats()
        _assert_pool_metrics(after, connected=True)
        assert after["get_started"] - before["get_started"] == 1
        assert after["get_direct"] - before["get_direct"] == 1
        assert after["get_waited"] == before["get_waited"]
        assert after["get_timed_out"] == before["get_timed_out"]
        assert after["pending_gets"] == 0
        assert after["connections_created"] == before["connections_created"]
        assert after["connections"] == 1
        assert after["connections"] <= after["max_size"]
    finally:
        assert await connection.disconnect() is True


@case("OBS-003")
@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_saturation_exposes_pending_then_waited_checkout(
    sa_connection: Connection,
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
    holder: asyncio.Task | None = None
    waiter: asyncio.Task | None = None
    try:
        assert await connection.connect() is True
        session_id = int(await scalar(connection, "SELECT @@SPID"))
        before = await connection.pool_stats()
        _assert_pool_metrics(before, connected=True)

        holder = asyncio.create_task(
            scalar(
                connection,
                "WAITFOR DELAY '00:00:00.750'; SELECT 1",
            )
        )
        await _wait_for_active_request(sa_connection, session_id)
        waiter = asyncio.create_task(scalar(connection, "SELECT @P1", [2]))

        pending = await _wait_for_metric(
            connection,
            "pending_gets",
            1,
        )
        assert pending["get_started"] - before["get_started"] == 2
        assert await asyncio.gather(holder, waiter) == [1, 2]

        after = await connection.pool_stats()
        _assert_pool_metrics(after, connected=True)
        assert after["get_started"] - before["get_started"] == 2
        assert after["get_direct"] - before["get_direct"] == 1
        assert after["get_waited"] - before["get_waited"] == 1
        assert after["get_timed_out"] == before["get_timed_out"]
        assert after["pending_gets"] == 0
        assert after["get_wait_time_seconds"] > before["get_wait_time_seconds"]
    finally:
        await _cancel_unfinished(holder, waiter)
        assert await connection.disconnect() is True


@case("OBS-004")
@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_acquire_timeout_counter_increments_once_and_recovers(
    sa_connection: Connection,
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
        timeout_config=TimeoutConfig(
            connect_timeout_secs=2.0,
            acquire_timeout_secs=0.25,
            operation_timeout_secs=3.0,
            transaction_timeout_secs=3.0,
            rollback_timeout_secs=2.0,
        ),
    )
    holder: asyncio.Task | None = None
    try:
        assert await connection.connect() is True
        session_id = int(await scalar(connection, "SELECT @@SPID"))
        before = await connection.pool_stats()
        _assert_pool_metrics(before, connected=True)

        holder = asyncio.create_task(
            scalar(
                connection,
                "WAITFOR DELAY '00:00:01'; SELECT 1",
            )
        )
        await _wait_for_active_request(sa_connection, session_id)
        started = time.monotonic()
        with pytest.raises(OperationTimeoutError) as captured:
            await scalar(connection, "SELECT 2")
        elapsed = time.monotonic() - started

        assert captured.value.operation == "query"
        assert captured.value.phase == "acquire"
        assert captured.value.timeout_seconds == pytest.approx(0.25)
        assert captured.value.retryable is True
        assert captured.value.connection_discarded is False
        assert captured.value.outcome_unknown is False
        assert 0.15 <= elapsed < 1.0

        after_timeout = await connection.pool_stats()
        _assert_pool_metrics(after_timeout, connected=True)
        assert after_timeout["get_timed_out"] - before["get_timed_out"] == 1
        assert after_timeout["pending_gets"] == 0

        assert await holder == 1
        assert await scalar(connection, "SELECT 2") == 2
        recovered = await connection.pool_stats()
        _assert_pool_metrics(recovered, connected=True)
        assert recovered["get_timed_out"] == after_timeout["get_timed_out"]
    finally:
        await _cancel_unfinished(holder)
        assert await connection.disconnect() is True


@case("OBS-006")
@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_checkout_validation_counts_killed_idle_connection_as_invalid(
    sa_connection: Connection,
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
            test_on_check_out=True,
            retry_connection=False,
        ),
    )
    try:
        assert await connection.connect() is True
        killed_spid = int(await scalar(connection, "SELECT @@SPID"))
        killed_connection_id = await _server_connection_id(
            sa_connection,
            killed_spid,
        )
        before = await connection.pool_stats()
        _assert_pool_metrics(before, connected=True)
        assert before["idle_connections"] == 1

        await sa_connection.execute(f"KILL {killed_spid}")
        await _wait_for_session_absent(sa_connection, killed_spid)

        replacement_spid = int(
            await asyncio.wait_for(
                scalar(connection, "SELECT @@SPID"),
                timeout=3.0,
            )
        )
        replacement_connection_id = await _server_connection_id(
            sa_connection,
            replacement_spid,
        )
        after = await connection.pool_stats()
        _assert_pool_metrics(after, connected=True)

        # SQL Server may immediately reuse the smallint SPID after KILL.
        # connection_id is the stable physical-connection identity.
        assert replacement_connection_id != killed_connection_id
        assert (
            after["connections_closed_invalid"]
            == before["connections_closed_invalid"] + 1
        )
        assert after["connections_created"] == before["connections_created"] + 1
        assert after["connections_closed_broken"] == before["connections_closed_broken"]
        assert all(
            after[key] == before[key]
            for key in POOL_CLOSE_REASON_KEYS - {"connections_closed_invalid"}
        )
    finally:
        assert await connection.disconnect() is True


@case("OBS-010")
@pytest.mark.asyncio
async def test_pool_stats_never_expose_sql_parameters_or_identifiers(
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
) -> None:
    sql_sentinel = "FASTMSSQL_OBS_PRIVACY_SENTINEL_2026_SQL"
    parameter_sentinel = "FASTMSSQL_OBS_PRIVACY_SENTINEL_2026_PARAM"
    application_name = unique_sql_name("FASTMSSQL_OBS_PRIVACY_SENTINEL_2026_APP")
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
        application_name=application_name,
    )
    try:
        observed = await scalar(
            connection,
            f"SELECT @P1 /* {sql_sentinel} */",
            [parameter_sentinel],
        )
        assert observed == parameter_sentinel
        stats = await connection.pool_stats()
        _assert_pool_metrics(stats, connected=True)
        rendered = repr((stats, tuple(stats), tuple(stats.values())))
        sensitive_values = (
            ("sql", sql_sentinel),
            ("parameter", parameter_sentinel),
            ("application", application_name),
            ("host", sql_auth_config.host),
            ("database", sql_auth_config.database),
            ("login", sql_auth_config.owner_user),
            ("credential", sql_auth_config.owner_password),
        )
        for label, value in sensitive_values:
            if value and value in rendered:
                raise AssertionError(f"{label} leaked into pool statistics")
        assert all(
            type(value) in {bool, int, float, type(None)} for value in stats.values()
        )
    finally:
        assert await connection.disconnect() is True
