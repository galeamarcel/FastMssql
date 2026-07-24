from __future__ import annotations

import asyncio
import threading
import time

from fastmssql import Connection, PoolConfig, SqlError, SslConfig
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import (
    event_loop_ticks,
    max_event_loop_gap,
    scalar,
)


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


def _connection(
    config: SqlAuthConfig,
    *,
    max_size: int,
    min_idle: int = 0,
) -> Connection:
    return Connection(
        server=config.host,
        port=config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=max_size,
            min_idle=min_idle,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=2,
            retry_connection=False,
        ),
    )


@pytest.mark.parametrize(
    ("finished", "ticks", "expected_responsive"),
    [
        pytest.param(
            0.045,
            [0.010, 0.020, 0.030, 0.040],
            True,
            id="fast-workload",
        ),
        pytest.param(0.500, [], False, id="starvation-boundary"),
        pytest.param(
            1.000,
            [-1.000, 0.100, 0.900, 2.000],
            False,
            id="out-of-window-ticks",
        ),
    ],
)
def test_event_loop_gap_contract(
    finished: float,
    ticks: list[float],
    expected_responsive: bool,
) -> None:
    gap = max_event_loop_gap(0.0, finished, ticks)
    assert (gap < 0.5) is expected_responsive


async def _wait_for_active(
    connection: Connection,
    expected: int,
    *,
    timeout: float = 3.0,
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


async def _delayed_scalar(
    connection: Connection,
    value: int,
    *,
    delay: str = "00:00:01",
) -> int:
    return await scalar(
        connection,
        f"WAITFOR DELAY '{delay}'; SELECT @P1 AS value",
        [value],
    )


@case("ASYNC-001")
@pytest.mark.asyncio
async def test_event_loop_ticker_progresses_during_sql_wait(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(sql_auth_config, max_size=1)
    stop = asyncio.Event()
    ticker = asyncio.create_task(event_loop_ticks(stop, interval=0.01))
    try:
        started = time.monotonic()
        assert await _delayed_scalar(connection, 1) == 1
        elapsed = time.monotonic() - started
    finally:
        stop.set()
        ticks = await ticker
        await connection.disconnect()
    assert 0.8 <= elapsed < 2.5
    assert len(ticks) >= 40
    assert ticks[-1] - ticks[0] >= 0.7


@case("ASYNC-002")
@pytest.mark.asyncio
async def test_pooled_waits_overlap_in_wall_clock_time(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(sql_auth_config, max_size=5, min_idle=5)
    try:
        await connection.connect()
        started = time.monotonic()
        values = await asyncio.gather(
            *(_delayed_scalar(connection, value) for value in range(5))
        )
        elapsed = time.monotonic() - started
        assert values == list(range(5))
        assert elapsed < 2.5
    finally:
        await connection.disconnect()


@case("ASYNC-003")
@pytest.mark.asyncio
async def test_concurrent_lane_beats_measured_sequential_baseline(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(sql_auth_config, max_size=5, min_idle=5)
    try:
        await connection.connect()
        sequential_started = time.monotonic()
        sequential_values = [
            await _delayed_scalar(connection, value) for value in range(5)
        ]
        sequential_elapsed = time.monotonic() - sequential_started

        concurrent_started = time.monotonic()
        concurrent_values = await asyncio.gather(
            *(_delayed_scalar(connection, value) for value in range(5))
        )
        concurrent_elapsed = time.monotonic() - concurrent_started

        assert sequential_values == list(range(5))
        assert concurrent_values == list(range(5))
        assert sequential_elapsed >= 4.0
        assert concurrent_elapsed < 2.5
        assert sequential_elapsed / concurrent_elapsed >= 2.0
    finally:
        await connection.disconnect()


@case("ASYNC-004")
@pytest.mark.asyncio
async def test_python_thread_progresses_during_rust_sql_wait(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(sql_auth_config, max_size=1)
    stop = threading.Event()
    started = threading.Event()
    counter = [0]

    def worker() -> None:
        started.set()
        while not stop.is_set():
            counter[0] += 1

    thread = threading.Thread(target=worker, name="fastmssql-gil-probe")
    thread.start()
    try:
        assert started.wait(timeout=1.0)
        before = counter[0]
        assert await _delayed_scalar(connection, 4) == 4
        after = counter[0]
    finally:
        stop.set()
        thread.join(timeout=2.0)
        await connection.disconnect()
    assert thread.is_alive() is False
    assert after - before >= 10_000


@case("ASYNC-005")
@pytest.mark.asyncio
async def test_concurrent_success_and_failure_are_isolated(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(sql_auth_config, max_size=4)
    try:
        outcomes = await asyncio.gather(
            scalar(connection, "SELECT CAST(1 AS INT)"),
            connection.query("SELECT * FROM dbo.strict_missing_async_table"),
            scalar(connection, "SELECT CAST(3 AS INT)"),
            return_exceptions=True,
        )
        assert outcomes[0] == 1
        assert isinstance(outcomes[1], SqlError)
        assert outcomes[1].code == 208
        assert outcomes[2] == 3
        assert await scalar(connection, "SELECT 5") == 5
    finally:
        await connection.disconnect()


@case("ASYNC-006")
@pytest.mark.asyncio
async def test_same_connection_wrapper_is_concurrency_safe(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(sql_auth_config, max_size=8)
    try:
        values = await asyncio.gather(
            *(
                _delayed_scalar(
                    connection,
                    value,
                    delay="00:00:00.100",
                )
                for value in range(40)
            )
        )
        assert values == list(range(40))
        stats = await connection.pool_stats()
        assert stats["active_connections"] == 0
        assert stats["connections"] <= 8
    finally:
        await connection.disconnect()


@case("ASYNC-007")
@pytest.mark.asyncio
async def test_multiple_connection_wrappers_run_concurrently(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connections = [
        _connection(sql_auth_config, max_size=1) for _ in range(5)
    ]
    try:
        started = time.monotonic()
        values = await asyncio.gather(
            *(
                _delayed_scalar(connection, index)
                for index, connection in enumerate(connections)
            )
        )
        elapsed = time.monotonic() - started
        assert values == list(range(5))
        assert elapsed < 2.5
    finally:
        await asyncio.gather(
            *(connection.disconnect() for connection in connections)
        )


@case("ASYNC-008")
@pytest.mark.asyncio
async def test_wait_for_cancels_sql_operation_within_bound(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(sql_auth_config, max_size=1)
    try:
        started = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                _delayed_scalar(
                    connection,
                    8,
                    delay="00:00:05",
                ),
                timeout=0.2,
            )
        elapsed = time.monotonic() - started
        assert elapsed < 1.0
    finally:
        await connection.disconnect()


@case("ASYNC-009")
@pytest.mark.asyncio
async def test_pool_is_immediately_usable_after_cancellation(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(sql_auth_config, max_size=1)
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                _delayed_scalar(
                    connection,
                    9,
                    delay="00:00:05",
                ),
                timeout=0.2,
            )
        assert (
            await asyncio.wait_for(
                scalar(connection, "SELECT 9"), timeout=2.5
            )
            == 9
        )
        stats = await connection.pool_stats()
        assert stats["active_connections"] == 0
        assert stats["idle_connections"] >= 1
    finally:
        await connection.disconnect()


@case("ASYNC-010")
@pytest.mark.asyncio
async def test_cancellation_storm_does_not_leak_pool_capacity(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(sql_auth_config, max_size=3)
    try:
        tasks = [
            asyncio.create_task(
                _delayed_scalar(
                    connection,
                    value,
                    delay="00:00:05",
                )
            )
            for value in range(12)
        ]
        await _wait_for_active(connection, 3)
        for task in tasks:
            task.cancel()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        assert len(outcomes) == 12
        assert all(
            isinstance(outcome, asyncio.CancelledError)
            for outcome in outcomes
        )
        await _wait_for_active(connection, 0)
        values = await asyncio.wait_for(
            asyncio.gather(
                scalar(connection, "SELECT 1"),
                scalar(connection, "SELECT 2"),
                scalar(connection, "SELECT 3"),
            ),
            timeout=3.0,
        )
        assert values == [1, 2, 3]
        stats = await connection.pool_stats()
        assert stats["active_connections"] == 0
        assert stats["connections"] <= 3
    finally:
        await connection.disconnect()


@case("ASYNC-011")
@pytest.mark.asyncio
async def test_cancellation_during_pool_acquisition_returns_capacity(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(sql_auth_config, max_size=1)
    try:
        holder = asyncio.create_task(
            _delayed_scalar(
                connection,
                1,
                delay="00:00:01",
            )
        )
        await _wait_for_active(connection, 1)
        waiter = asyncio.create_task(scalar(connection, "SELECT 2"))
        await asyncio.sleep(0.1)
        assert waiter.done() is False
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert await holder == 1
        assert await asyncio.wait_for(
            scalar(connection, "SELECT 3"), timeout=2.0
        ) == 3
        stats = await connection.pool_stats()
        assert stats["active_connections"] == 0
        assert stats["connections"] == 1
    finally:
        await connection.disconnect()


@case("ASYNC-012")
@pytest.mark.asyncio
async def test_transaction_operations_serialize_without_corruption(
    transaction_factory,
) -> None:
    transaction = transaction_factory()
    try:
        await transaction.begin()

        async def delayed(value: int) -> int:
            return await scalar(
                transaction,
                """
                WAITFOR DELAY '00:00:00.150';
                SELECT @P1 AS value;
                """,
                [value],
            )

        started = time.monotonic()
        values = await asyncio.gather(*(delayed(value) for value in range(4)))
        elapsed = time.monotonic() - started
        assert values == list(range(4))
        assert 0.5 <= elapsed < 2.0
        assert await scalar(transaction, "SELECT @@TRANCOUNT") == 1
        await transaction.rollback()
    finally:
        await transaction.close()


@case("ASYNC-013")
@pytest.mark.asyncio
async def test_concurrent_result_conversion_has_bounded_loop_stalls(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(sql_auth_config, max_size=4)
    stop = asyncio.Event()
    ticker = asyncio.create_task(event_loop_ticks(stop, interval=0.01))

    async def query_and_convert(seed: int) -> tuple[int, int]:
        result = await connection.query(
            """
            WITH numbers AS (
                SELECT 1 AS value
                UNION ALL
                SELECT value + 1 FROM numbers WHERE value < 500
            )
            SELECT
                @P1 AS seed,
                REPLICATE(CAST(N'x' AS NVARCHAR(MAX)), 1000) AS payload
            FROM numbers
            OPTION (MAXRECURSION 0)
            """,
            [seed],
        )
        rows = result.rows()
        assert all(row["seed"] == seed for row in rows)
        return seed, len(rows)

    try:
        await asyncio.sleep(0)
        started = time.monotonic()
        outcomes = await asyncio.gather(
            *(query_and_convert(seed) for seed in range(4))
        )
        finished = time.monotonic()
    finally:
        stop.set()
        ticks = await ticker
        await connection.disconnect()
    assert outcomes == [(0, 500), (1, 500), (2, 500), (3, 500)]
    assert max_event_loop_gap(started, finished, ticks) < 0.5
