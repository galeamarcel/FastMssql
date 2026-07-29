from __future__ import annotations

import asyncio
import gc
import math
import subprocess
import time
import tracemalloc
from collections.abc import AsyncIterator, Callable

import fastmssql
from fastmssql import (
    Connection,
    Parameter,
    PoolConfig,
    ProtocolError,
    SqlConnectionError,
    SslConfig,
    TlsError,
)
import psutil
import pytest
import pytest_asyncio

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.framework_apps import wait_for_session_count
from sql_auth_strict.helpers import (
    assert_dedicated_container,
    quote_identifier,
    scalar,
)
from sql_auth_strict.operation_metrics_assertions import (
    OUTCOME_KEYS,
    assert_operation_stats,
    operation_delta,
)


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]

CONTAINER = "fastmssql-sql-auth-dev"
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


def _connection(
    config: SqlAuthConfig,
    *,
    max_size: int = 5,
    min_idle: int = 1,
    application_name: str | None = None,
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
        application_name=application_name,
    )


def _assert_pool_metric_invariants(stats: dict) -> None:
    assert set(stats) == POOL_STATS_KEYS
    assert stats["connected"] is True
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
    assert all(
        stats[key] >= 0
        for key in POOL_STATS_KEYS - {"connected", "min_idle", "get_wait_time_seconds"}
    )
    assert math.isfinite(stats["get_wait_time_seconds"])
    assert stats["get_wait_time_seconds"] >= 0.0


def _docker_command(*arguments: str) -> list[str]:
    assert_dedicated_container(CONTAINER)
    return ["docker", *arguments, CONTAINER]


def _docker_sync(
    *arguments: str,
    check: bool = True,
    timeout: float = 90.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _docker_command(*arguments),
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


async def _docker(
    *arguments: str,
    check: bool = True,
    timeout: float = 90.0,
) -> subprocess.CompletedProcess[str]:
    return await asyncio.to_thread(
        _docker_sync,
        *arguments,
        check=check,
        timeout=timeout,
    )


async def _wait_healthy(timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        result = await _docker(
            "inspect",
            "--format",
            "{{.State.Health.Status}}",
            check=False,
            timeout=10.0,
        )
        last = result.stdout.strip() or result.stderr.strip()
        if result.returncode == 0 and result.stdout.strip() == "healthy":
            return
        await asyncio.sleep(1.0)
    raise AssertionError(
        f"{CONTAINER} did not become healthy within {timeout}s: {last}"
    )


async def _physical_connection_id(connection: Connection) -> str:
    value = await scalar(
        connection,
        """
        SELECT CONVERT(VARCHAR(36), connection_id)
        FROM sys.dm_exec_connections
        WHERE session_id = @@SPID
        """,
    )
    assert isinstance(value, str)
    return value


async def _restore_container() -> None:
    await _docker("unpause", check=False, timeout=20.0)
    await _docker("start", check=False, timeout=30.0)
    await _wait_healthy()


@pytest_asyncio.fixture
async def dedicated_container_guard() -> AsyncIterator[None]:
    assert_dedicated_container(CONTAINER)
    try:
        yield
    finally:
        await _restore_container()


async def _wait_for_request(
    connection: Connection,
    token: str,
    *,
    timeout: float = 3.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        count = await scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM sys.dm_exec_requests AS request
            CROSS APPLY sys.dm_exec_sql_text(request.sql_handle) AS sql_text
            WHERE request.session_id <> @@SPID
              AND sql_text.text LIKE @P1
            """,
            [f"%{token}%"],
        )
        if count > 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"request token {token!r} was not observed")


async def _application_session_count(
    connection: Connection,
    application_name: str,
) -> int:
    return await scalar(
        connection,
        """
        SELECT COUNT(*)
        FROM sys.dm_exec_sessions
        WHERE program_name = @P1
        """,
        [application_name],
    )


@case("RES-001", "RES-002")
@pytest.mark.resilience
@pytest.mark.asyncio
async def test_pause_has_bounded_failure_and_unpause_recovers(
    sql_auth_config: SqlAuthConfig,
    dedicated_container_guard: None,
) -> None:
    del dedicated_container_guard
    connection = _connection(sql_auth_config, max_size=1)
    try:
        assert await scalar(connection, "SELECT 1") == 1
        await _docker("pause")
        started = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                scalar(connection, "SELECT 2"),
                timeout=1.0,
            )
        assert time.monotonic() - started < 2.0
    finally:
        await _docker("unpause", check=False, timeout=20.0)
        await _wait_healthy()
        await connection.disconnect()

    recovered = _connection(sql_auth_config, max_size=1)
    try:
        assert await asyncio.wait_for(scalar(recovered, "SELECT 3"), timeout=3.0) == 3
    finally:
        await recovered.disconnect()


@case("RES-003")
@pytest.mark.resilience
@pytest.mark.asyncio
async def test_restart_invalidates_old_session_predictably(
    sql_auth_config: SqlAuthConfig,
    dedicated_container_guard: None,
) -> None:
    del dedicated_container_guard
    connection = _connection(sql_auth_config, max_size=1)
    try:
        connection_id = await _physical_connection_id(connection)
        await _docker("restart", "--time", "1")
        await _wait_healthy()
        try:
            new_connection_id = await asyncio.wait_for(
                _physical_connection_id(connection), timeout=3.0
            )
        except (SqlConnectionError, ProtocolError):
            new_connection_id = await asyncio.wait_for(
                _physical_connection_id(connection), timeout=3.0
            )
        assert new_connection_id != connection_id
    finally:
        await connection.disconnect()


@case("RES-004")
@pytest.mark.resilience
@pytest.mark.asyncio
async def test_existing_pool_discards_restart_broken_connection(
    sql_auth_config: SqlAuthConfig,
    dedicated_container_guard: None,
) -> None:
    del dedicated_container_guard
    connection = _connection(sql_auth_config, max_size=1)
    try:
        old_connection_id = await _physical_connection_id(connection)
        await _docker("restart", "--time", "1")
        await _wait_healthy()
        try:
            await connection.query("SELECT 1")
        except (SqlConnectionError, ProtocolError):
            pass
        new_connection_id = await asyncio.wait_for(
            _physical_connection_id(connection), timeout=3.0
        )
        stats = await connection.pool_stats()
        assert stats["active_connections"] == 0
        assert stats["idle_connections"] == 1
        assert new_connection_id != old_connection_id
    finally:
        await connection.disconnect()


@case("RES-005")
@pytest.mark.resilience
@pytest.mark.asyncio
async def test_recreated_pool_works_after_restart(
    sql_auth_config: SqlAuthConfig,
    dedicated_container_guard: None,
) -> None:
    del dedicated_container_guard
    connection = _connection(sql_auth_config, max_size=1)
    assert await scalar(connection, "SELECT 1") == 1
    await _docker("restart", "--time", "1")
    await _wait_healthy()
    await connection.disconnect()
    recreated = _connection(sql_auth_config, max_size=1)
    try:
        assert await asyncio.wait_for(scalar(recreated, "SELECT 5"), timeout=3.0) == 5
    finally:
        await recreated.disconnect()


@case("RES-006")
@pytest.mark.resilience
@pytest.mark.asyncio
async def test_restart_does_not_falsely_commit_inflight_transaction(
    sql_auth_config: SqlAuthConfig,
    transaction_factory: Callable,
    unique_sql_name: Callable[[str], str],
    dedicated_container_guard: None,
) -> None:
    del dedicated_container_guard
    raw_table = unique_sql_name("strict_restart_transaction")
    table = quote_identifier(raw_table)
    setup = _connection(sql_auth_config, max_size=1)
    await setup.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")
    await setup.disconnect()

    observer = _connection(
        sql_auth_config,
        max_size=1,
        application_name=unique_sql_name("strict_restart_observer"),
    )
    transaction = transaction_factory()
    token = unique_sql_name("strict_restart_wait")
    try:
        await transaction.begin()
        await transaction.execute(f"INSERT INTO {table} VALUES (1)")
        wait_task = asyncio.ensure_future(
            transaction.query(
                f"WAITFOR DELAY '00:00:30'; SELECT CAST(1 AS INT) AS value; -- {token}"
            )
        )
        await _wait_for_request(observer, token)
        await _docker("restart", "--time", "1")
        await _wait_healthy()
        outcomes = await asyncio.wait_for(
            asyncio.gather(wait_task, return_exceptions=True),
            timeout=5.0,
        )
        assert len(outcomes) == 1
        assert isinstance(
            outcomes[0],
            (SqlConnectionError, ProtocolError, TlsError),
        )
        assert transaction.is_connected() is False
    finally:
        await transaction.close()
        await observer.disconnect()

    verifier = _connection(sql_auth_config, max_size=1)
    try:
        assert await scalar(verifier, f"SELECT COUNT(*) FROM {table}") == 0
    finally:
        await verifier.execute(f"DROP TABLE IF EXISTS {table}")
        await verifier.disconnect()


@case("RES-007")
@pytest.mark.resilience
def test_docker_target_safety_contract() -> None:
    assert_dedicated_container(CONTAINER)
    with pytest.raises(RuntimeError, match="refusing disruptive Docker action"):
        assert_dedicated_container("unrelated-production-database")
    commands = [
        _docker_command("pause"),
        _docker_command("unpause"),
        _docker_command("restart", "--time", "1"),
        _docker_command("start"),
        _docker_command(
            "inspect",
            "--format",
            "{{.State.Health.Status}}",
        ),
    ]
    assert all(command[0] == "docker" for command in commands)
    assert all(command[-1] == CONTAINER for command in commands)
    assert all(command.count(CONTAINER) == 1 for command in commands)


@case("LOAD-001")
@pytest.mark.load
@pytest.mark.asyncio
async def test_thousand_short_queries_at_concurrency_twenty(
    sql_auth_config: SqlAuthConfig,
    record_load_metric,
) -> None:
    connection = _connection(
        sql_auth_config,
        max_size=20,
        min_idle=20,
    )
    semaphore = asyncio.Semaphore(20)

    async def execute(value: int) -> int:
        async with semaphore:
            return await scalar(connection, "SELECT @P1 AS value", [value])

    try:
        started = time.monotonic()
        values = await asyncio.gather(*(execute(value) for value in range(1000)))
        elapsed = time.monotonic() - started
        assert values == list(range(1000))
        stats = await connection.pool_stats()
        assert stats["active_connections"] == 0
        assert stats["connections"] <= 20
        record_load_metric(
            "LOAD-001",
            elapsed_seconds=elapsed,
            queries_per_second=1000 / elapsed,
        )
    finally:
        await connection.disconnect()


@case("LOAD-002")
@pytest.mark.load
@pytest.mark.asyncio
async def test_large_result_memory_has_recorded_bound(
    owner_connection: Connection,
    record_load_metric,
) -> None:
    process = psutil.Process()
    rss_before = process.memory_info().rss
    tracemalloc.start()
    try:
        for size in (1000, 10_000, 50_000):
            result = await owner_connection.query(
                f"""
                WITH numbers AS (
                    SELECT 1 AS value
                    UNION ALL
                    SELECT value + 1 FROM numbers WHERE value < {size}
                )
                SELECT value FROM numbers ORDER BY value
                OPTION (MAXRECURSION 0)
                """
            )
            rows = result.rows()
            assert len(rows) == size
            assert rows[0]["value"] == 1
            assert rows[-1]["value"] == size
            del rows, result
            gc.collect()
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    rss_after = process.memory_info().rss
    rss_growth = max(0, rss_after - rss_before)
    assert rss_growth < 256 * 1024 * 1024
    assert peak < 256 * 1024 * 1024
    record_load_metric(
        "LOAD-002",
        rss_before_bytes=rss_before,
        rss_after_bytes=rss_after,
        python_current_bytes=current,
        python_peak_bytes=peak,
    )


@case("LOAD-003")
@pytest.mark.load
@pytest.mark.asyncio
async def test_repeated_result_conversion_has_bounded_growth(
    owner_connection: Connection,
    record_load_metric,
) -> None:
    tracemalloc.start()
    samples: list[int] = []
    try:
        for cycle in range(100):
            result = await owner_connection.query(
                """
                WITH numbers AS (
                    SELECT 1 AS value
                    UNION ALL
                    SELECT value + 1 FROM numbers WHERE value < 200
                )
                SELECT value, REPLICATE(N'x', 200) AS payload
                FROM numbers
                OPTION (MAXRECURSION 0)
                """
            )
            rows = result.rows()
            assert len(rows) == 200
            del rows, result
            if (cycle + 1) % 10 == 0:
                gc.collect()
                current, _ = tracemalloc.get_traced_memory()
                samples.append(current)
    finally:
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    assert len(samples) == 10
    assert max(samples) - min(samples) < 8 * 1024 * 1024
    assert samples[-1] - samples[0] < 8 * 1024 * 1024
    record_load_metric(
        "LOAD-003",
        python_current_bytes=current,
        python_peak_bytes=peak,
        samples_bytes=samples,
    )


@case("LOAD-004")
@pytest.mark.load
@pytest.mark.asyncio
async def test_bulk_insert_increasing_sizes_and_correctness(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    record_load_metric,
) -> None:
    raw_table = unique_sql_name("strict_load_bulk")
    table = quote_identifier(raw_table)
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value INT NOT NULL)"
    )
    sizes = (1, 100, 1000, 10_000)
    elapsed_by_size: dict[int, float] = {}
    offset = 0
    try:
        for size in sizes:
            rows = [[offset + index, (offset + index) * 2] for index in range(size)]
            started = time.monotonic()
            affected = await owner_connection.bulk_insert(
                raw_table, ["id", "value"], rows
            )
            elapsed_by_size[size] = time.monotonic() - started
            assert affected == size
            offset += size
        assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == sum(
            sizes
        )
        assert await scalar(
            owner_connection,
            f"SELECT SUM(CAST(value AS BIGINT)) FROM {table}",
        ) == sum(index * 2 for index in range(sum(sizes)))
        record_load_metric("LOAD-004", elapsed_by_size=elapsed_by_size)
    finally:
        await owner_connection.execute(f"DROP TABLE IF EXISTS {table}")


@case("LOAD-005")
@pytest.mark.load
@pytest.mark.asyncio
async def test_rapid_lifecycle_does_not_grow_sql_sessions(
    sa_connection: Connection,
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
    record_load_metric,
) -> None:
    application_name = unique_sql_name("strict_load_lifecycle")
    baseline = await _application_session_count(sa_connection, application_name)
    started = time.monotonic()
    for cycle in range(250):
        connection = _connection(
            sql_auth_config,
            max_size=1,
            application_name=application_name,
        )
        async with connection:
            assert await scalar(connection, "SELECT @P1", [cycle]) == cycle
    elapsed = time.monotonic() - started
    deadline = time.monotonic() + 5.0
    observed = -1
    while time.monotonic() < deadline:
        observed = await _application_session_count(sa_connection, application_name)
        if observed == baseline:
            break
        await asyncio.sleep(0.1)
    assert observed == baseline
    record_load_metric(
        "LOAD-005",
        elapsed_seconds=elapsed,
        baseline_sessions=baseline,
        final_sessions=observed,
    )


@case("LOAD-006")
@pytest.mark.load
@pytest.mark.asyncio
async def test_five_hundred_mixed_operations(
    owner_connection: Connection,
    transaction_factory: Callable,
    unique_sql_name: Callable[[str], str],
    record_load_metric,
) -> None:
    table = quote_identifier(unique_sql_name("strict_load_mixed"))
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value INT NOT NULL)"
    )
    await owner_connection.bulk_insert(
        table[1:-1],
        ["id", "value"],
        [[index, 0] for index in range(100)],
    )
    semaphore = asyncio.Semaphore(20)

    async def query_operation(value: int) -> int:
        async with semaphore:
            return await scalar(owner_connection, "SELECT @P1", [value])

    async def update_operation(row_id: int) -> int:
        async with semaphore:
            return await owner_connection.execute(
                f"UPDATE {table} SET value = value + 1 WHERE id = @P1",
                [row_id],
            )

    async def transaction_operation(value: int) -> int:
        async with semaphore:
            transaction = transaction_factory()
            try:
                await transaction.begin()
                returned = await scalar(transaction, "SELECT @P1", [value])
                await transaction.rollback()
                return returned
            finally:
                await transaction.close()

    try:
        started = time.monotonic()
        query_values, update_counts, transaction_values = await asyncio.gather(
            asyncio.gather(*(query_operation(value) for value in range(300))),
            asyncio.gather(*(update_operation(row_id) for row_id in range(100))),
            asyncio.gather(*(transaction_operation(value) for value in range(100))),
        )
        elapsed = time.monotonic() - started
        assert query_values == list(range(300))
        assert update_counts == [1] * 100
        assert transaction_values == list(range(100))
        assert await scalar(owner_connection, f"SELECT SUM(value) FROM {table}") == 100
        record_load_metric(
            "LOAD-006",
            elapsed_seconds=elapsed,
            operation_count=500,
        )
    finally:
        await owner_connection.execute(f"DROP TABLE IF EXISTS {table}")


@case("LOAD-007")
@pytest.mark.load
@pytest.mark.asyncio
async def test_post_load_smoke_query_and_pool_state(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(
        sql_auth_config,
        max_size=5,
        min_idle=2,
    )
    try:
        assert (
            await scalar(
                connection,
                """
            SELECT CASE
                WHEN CAST(SERVERPROPERTY('Edition') AS NVARCHAR(128))
                     LIKE '%Developer%'
                THEN 1 ELSE 0
            END
            """,
            )
            == 1
        )
        values = await asyncio.gather(
            *(scalar(connection, "SELECT @P1", [value]) for value in range(20))
        )
        assert values == list(range(20))
        stats = await connection.pool_stats()
        assert stats["active_connections"] == 0
        assert stats["connections"] <= 5
        assert stats["idle_connections"] >= 2
    finally:
        await connection.disconnect()


@case("LOAD-008")
@pytest.mark.load
@pytest.mark.asyncio
async def test_concurrent_write_transactions_preserve_exact_state(
    owner_connection: Connection,
    transaction_factory: Callable,
    unique_sql_name: Callable[[str], str],
    record_load_metric,
) -> None:
    transaction_count = 1_000
    concurrency = 50
    wait_seconds = 0.02
    table = quote_identifier(unique_sql_name("strict_load_transactions"))
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value INT NOT NULL)"
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def write_transaction(value: int) -> tuple[int, int]:
        async with semaphore:
            transaction = transaction_factory()
            try:
                await transaction.begin()
                assert (
                    await transaction.execute(
                        f"INSERT INTO {table} (id, value) VALUES (@P1, @P2)",
                        [value, value * 10],
                    )
                    == 1
                )
                session_id = await scalar(
                    transaction,
                    "WAITFOR DELAY '00:00:00.020'; SELECT @@SPID",
                )
                if value % 2 == 0:
                    await transaction.commit()
                else:
                    await transaction.rollback()
                return value, session_id
            finally:
                await transaction.close()

    try:
        started = time.monotonic()
        outcomes = await asyncio.wait_for(
            asyncio.gather(
                *(write_transaction(value) for value in range(transaction_count))
            ),
            timeout=30.0,
        )
        elapsed = time.monotonic() - started
        committed = tuple(range(0, transaction_count, 2))
        sequential_wait_floor = transaction_count * wait_seconds

        assert sorted(value for value, _ in outcomes) == list(range(transaction_count))
        assert len({session_id for _, session_id in outcomes}) >= concurrency // 2
        assert elapsed < sequential_wait_floor * 0.5
        assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == len(
            committed
        )
        assert await scalar(
            owner_connection,
            f"SELECT SUM(value) FROM {table}",
        ) == sum(value * 10 for value in committed)
        assert (
            await scalar(
                owner_connection,
                f"SELECT COUNT(*) FROM {table} WHERE id % 2 = 1",
            )
            == 0
        )
        record_load_metric(
            "LOAD-008",
            elapsed_seconds=elapsed,
            transaction_count=transaction_count,
            concurrency=concurrency,
            distinct_session_count=len({session_id for _, session_id in outcomes}),
            transactions_per_second=transaction_count / elapsed,
        )
    finally:
        await owner_connection.execute(f"DROP TABLE IF EXISTS {table}")


@case("LOAD-009")
@pytest.mark.load
@pytest.mark.asyncio
async def test_thousand_readiness_probes_remain_pool_bounded(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    record_load_metric,
) -> None:
    probe_count = 1_000
    concurrency = 100
    max_size = 20
    application_name = unique_sql_name("strict_load_readiness")
    connection = _connection(
        sql_auth_config,
        max_size=max_size,
        min_idle=0,
        application_name=application_name,
    )
    semaphore = asyncio.Semaphore(concurrency)
    sampling_done = asyncio.Event()
    sampled_session_counts: list[int] = []

    async def probe() -> bool:
        async with semaphore:
            return await connection.ping()

    async def sample_sessions() -> None:
        while not sampling_done.is_set():
            sampled_session_counts.append(
                await _application_session_count(
                    sa_connection,
                    application_name,
                )
            )
            await asyncio.sleep(0.005)

    sampler = asyncio.create_task(sample_sessions())
    try:
        started = time.monotonic()
        outcomes = await asyncio.wait_for(
            asyncio.gather(*(probe() for _ in range(probe_count))),
            timeout=30.0,
        )
        elapsed = time.monotonic() - started
        sampling_done.set()
        await sampler

        assert outcomes == [True] * probe_count
        assert sampled_session_counts
        assert max(sampled_session_counts) <= max_size
        stats = await connection.pool_stats()
        assert stats["active_connections"] == 0
        assert stats["connections"] <= max_size
        assert await scalar(connection, "SELECT 9009") == 9009
        record_load_metric(
            "LOAD-009",
            probe_count=probe_count,
            task_concurrency=concurrency,
            pool_max_size=max_size,
            peak_observed_sessions=max(sampled_session_counts),
            elapsed_seconds=elapsed,
            probes_per_second=probe_count / elapsed,
        )
    finally:
        sampling_done.set()
        if not sampler.done():
            await sampler
        await connection.disconnect()

    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        remaining = await _application_session_count(
            sa_connection,
            application_name,
        )
        if remaining == 0:
            break
        await asyncio.sleep(0.02)
    else:
        raise AssertionError(
            f"readiness load left {remaining} SQL application session(s)"
        )


@case("OBS-009")
@pytest.mark.load
@pytest.mark.asyncio
@pytest.mark.timeout(90)
async def test_pool_metrics_remain_consistent_during_ten_thousand_queries(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    record_load_metric,
) -> None:
    operation_count = 10_000
    worker_count = 100
    max_size = 20
    application_name = unique_sql_name("strict_pool_observability_load")
    connection = _connection(
        sql_auth_config,
        max_size=max_size,
        min_idle=max_size,
        application_name=application_name,
    )
    stop = asyncio.Event()
    scraper: asyncio.Task | None = None
    ticker: asyncio.Task | None = None

    async def worker(worker_id: int) -> list[int]:
        values: list[int] = []
        for value in range(worker_id, operation_count, worker_count):
            values.append(
                await scalar(
                    connection,
                    "SELECT @P1 AS value",
                    [value],
                )
            )
        return values

    async def scrape() -> tuple[int, int]:
        samples = 0
        max_pending = 0
        while not stop.is_set():
            stats = await connection.pool_stats()
            _assert_pool_metric_invariants(stats)
            samples += 1
            max_pending = max(max_pending, stats["pending_gets"])
            await asyncio.sleep(0)
        return samples, max_pending

    async def tick() -> int:
        ticks = 0
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0)
        return ticks

    try:
        assert await connection.connect() is True
        before = await connection.pool_stats()
        _assert_pool_metric_invariants(before)

        scraper = asyncio.create_task(scrape())
        ticker = asyncio.create_task(tick())
        started = time.monotonic()
        worker_results = await asyncio.wait_for(
            asyncio.gather(*(worker(worker_id) for worker_id in range(worker_count))),
            timeout=75.0,
        )
        elapsed = time.monotonic() - started
        stop.set()
        try:
            samples, max_pending = await scraper
        finally:
            scraper = None
        try:
            ticks = await ticker
        finally:
            ticker = None

        values = sorted(
            value for worker_values in worker_results for value in worker_values
        )
        assert values == list(range(operation_count))
        final = await connection.pool_stats()
        _assert_pool_metric_invariants(final)
        assert final["get_started"] - before["get_started"] == operation_count
        assert (
            final["get_direct"]
            - before["get_direct"]
            + final["get_waited"]
            - before["get_waited"]
            == operation_count
        )
        assert final["get_timed_out"] == before["get_timed_out"]
        assert final["pending_gets"] == 0
        assert final["active_connections"] == 0
        assert final["connections"] <= max_size
        assert samples > 0
        assert ticks > 10
        assert await scalar(connection, "SELECT 321") == 321
        record_load_metric(
            "OBS-009",
            elapsed_seconds=elapsed,
            operation_count=operation_count,
            worker_count=worker_count,
            pool_max_size=max_size,
            scrape_samples=samples,
            event_loop_ticks=ticks,
            max_pending_gets=max_pending,
            physical_connections=final["connections"],
            queries_per_second=operation_count / elapsed,
        )
    finally:
        stop.set()
        try:
            for task in (scraper, ticker):
                if task is not None:
                    await task
        finally:
            assert await connection.disconnect() is True

    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        remaining = await _application_session_count(
            sa_connection,
            application_name,
        )
        if remaining == 0:
            break
        await asyncio.sleep(0.02)
    else:
        raise AssertionError(
            f"observability load left {remaining} SQL application session(s)"
        )


@case("OPMET-011")
@pytest.mark.load
@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_operation_metrics_survive_ten_thousand_queries_and_scrapes(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    record_load_metric,
) -> None:
    operation_count = 10_000
    worker_count = 100
    max_size = 20
    application_name = unique_sql_name("strict_opmet_load")
    metrics_type = getattr(fastmssql, "OperationMetricsConfig")
    connection = Connection(
        server=sql_auth_config.host,
        port=sql_auth_config.port,
        database=sql_auth_config.database,
        username=sql_auth_config.owner_user,
        password=sql_auth_config.owner_password,
        application_name=application_name,
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=max_size,
            min_idle=0,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=3,
            test_on_check_out=False,
            retry_connection=False,
        ),
        operation_metrics_config=metrics_type(enabled=True),
    )
    stop = asyncio.Event()
    scraper: asyncio.Task | None = None
    ticker: asyncio.Task | None = None

    async def worker(worker_id: int) -> list[int]:
        values: list[int] = []
        for value in range(worker_id, operation_count, worker_count):
            values.append(
                await scalar(
                    connection,
                    """
                    IF @P1 < 100
                        WAITFOR DELAY '00:00:00.050';
                    SELECT @P1;
                    """,
                    [value],
                )
            )
        return values

    async def scrape() -> tuple[int, int, int]:
        samples = 0
        maximum_in_flight = 0
        maximum_connections = 0
        while not stop.is_set():
            operation_stats = await connection.operation_stats()
            assert_operation_stats(operation_stats, enabled=True)
            pool_stats = await connection.pool_stats()
            samples += 1
            maximum_in_flight = max(
                maximum_in_flight,
                operation_stats["operations"]["query"]["in_flight"],
            )
            maximum_connections = max(
                maximum_connections,
                pool_stats["connections"],
            )
            await asyncio.sleep(0)
        return samples, maximum_in_flight, maximum_connections

    async def tick() -> int:
        ticks = 0
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0)
        return ticks

    try:
        assert await connection.connect() is True
        assert await scalar(connection, "SELECT -1") == -1
        before = await connection.operation_stats()
        scraper = asyncio.create_task(scrape())
        ticker = asyncio.create_task(tick())
        started = time.monotonic()
        try:
            worker_results = await asyncio.wait_for(
                asyncio.gather(
                    *(worker(worker_id) for worker_id in range(worker_count))
                ),
                timeout=90.0,
            )
        finally:
            stop.set()
            assert scraper is not None
            assert ticker is not None
            samples, maximum_in_flight, maximum_connections = await scraper
            scraper = None
            ticks = await ticker
            ticker = None
        elapsed = time.monotonic() - started

        values = sorted(
            value for worker_values in worker_results for value in worker_values
        )
        assert values == list(range(operation_count))
        final = await connection.operation_stats()
        assert_operation_stats(final, enabled=True)
        delta = operation_delta(before, final, "query")
        assert delta["started"] == delta["completed"] == operation_count
        assert delta["succeeded"] == operation_count
        assert all(
            delta[key] == 0
            for key in (
                "errors",
                "timed_out",
                "cancelled",
                "outcome_unknown",
            )
        )
        assert final["operations"]["query"]["in_flight"] == 0
        assert samples > 0
        assert maximum_in_flight > 0
        assert maximum_in_flight <= worker_count
        assert maximum_connections <= max_size
        assert ticks > 10
        assert await scalar(connection, "SELECT 11") == 11
        record_load_metric(
            "OPMET-011",
            elapsed_seconds=elapsed,
            operation_count=operation_count,
            worker_count=worker_count,
            pool_max_size=max_size,
            scrape_samples=samples,
            event_loop_ticks=ticks,
            maximum_in_flight=maximum_in_flight,
            maximum_connections=maximum_connections,
            queries_per_second=operation_count / elapsed,
            query_histogram=final["operations"]["query"]["duration_seconds_buckets"],
            exact_outcomes={key: delta[key] for key in OUTCOME_KEYS},
        )
    finally:
        stop.set()
        for task in (scraper, ticker):
            if task is not None:
                await task
        await connection.disconnect()

    await wait_for_session_count(
        sa_connection,
        application_name,
        expected=0,
    )


@case("PARAM-033")
@pytest.mark.load
@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_thousand_concurrent_typed_operations_are_exact_and_pool_bounded(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    record_load_metric,
) -> None:
    operation_count = 1_000
    concurrency = 64
    max_size = 8
    application_name = unique_sql_name("strict_typed_load")
    metrics_type = getattr(fastmssql, "OperationMetricsConfig")
    connection = Connection(
        server=sql_auth_config.host,
        port=sql_auth_config.port,
        database=sql_auth_config.database,
        username=sql_auth_config.owner_user,
        password=sql_auth_config.owner_password,
        application_name=application_name,
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=max_size,
            min_idle=0,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=3,
            test_on_check_out=False,
            retry_connection=False,
        ),
        operation_metrics_config=metrics_type(enabled=True),
    )
    semaphore = asyncio.Semaphore(concurrency)
    stop = asyncio.Event()
    application_in_flight = 0
    maximum_application_in_flight = 0

    async def execute(value: int) -> tuple[int, str, int]:
        nonlocal application_in_flight, maximum_application_in_flight
        async with semaphore:
            application_in_flight += 1
            maximum_application_in_flight = max(
                maximum_application_in_flight,
                application_in_flight,
            )
            try:
                row = (
                    await connection.query(
                        """
                        WAITFOR DELAY '00:00:00.002';
                        SELECT
                            @P1 AS value,
                            CONVERT(
                                VARCHAR(128),
                                SQL_VARIANT_PROPERTY(@P1, 'BaseType')
                            ) AS base_type,
                            @@SPID AS session_id
                        """,
                        [Parameter(value, "INT")],
                    )
                ).fetchone()
                return (
                    row["value"],
                    row["base_type"],
                    row["session_id"],
                )
            finally:
                application_in_flight -= 1

    async def sample() -> tuple[int, int, int, int]:
        samples = 0
        maximum_operation_in_flight = 0
        maximum_connections = 0
        maximum_sessions = 0
        while not stop.is_set():
            operation_stats = await connection.operation_stats()
            pool_stats = await connection.pool_stats()
            session_count = await _application_session_count(
                sa_connection,
                application_name,
            )
            samples += 1
            maximum_operation_in_flight = max(
                maximum_operation_in_flight,
                operation_stats["operations"]["query"]["in_flight"],
            )
            maximum_connections = max(
                maximum_connections,
                pool_stats["connections"],
            )
            maximum_sessions = max(maximum_sessions, session_count)
            await asyncio.sleep(0.002)
        return (
            samples,
            maximum_operation_in_flight,
            maximum_connections,
            maximum_sessions,
        )

    sampler: asyncio.Task | None = None
    baseline_sessions = await _application_session_count(
        sa_connection,
        application_name,
    )
    assert baseline_sessions == 0

    try:
        assert await connection.connect() is True
        before = await connection.operation_stats()
        sampler = asyncio.create_task(sample())
        started = time.monotonic()
        outcomes = await asyncio.wait_for(
            asyncio.gather(
                *(execute(value) for value in range(operation_count)),
                return_exceptions=True,
            ),
            timeout=45.0,
        )
        elapsed = time.monotonic() - started
        stop.set()
        (
            samples,
            maximum_operation_in_flight,
            maximum_connections,
            maximum_sessions,
        ) = await sampler
        sampler = None

        failures = [
            outcome for outcome in outcomes if isinstance(outcome, BaseException)
        ]
        successes = [
            outcome for outcome in outcomes if not isinstance(outcome, BaseException)
        ]
        assert len(outcomes) == operation_count
        assert failures == []
        assert len(successes) == operation_count
        assert sorted(value for value, _, _ in successes) == list(
            range(operation_count)
        )
        assert {base_type for _, base_type, _ in successes} == {"int"}

        session_ids = {session_id for _, _, session_id in successes}
        after = await connection.operation_stats()
        query_delta = operation_delta(before, after, "query")
        assert query_delta == {
            "started": operation_count,
            "completed": operation_count,
            "succeeded": operation_count,
            "errors": 0,
            "timed_out": 0,
            "cancelled": 0,
            "outcome_unknown": 0,
        }
        assert after["operations"]["query"]["in_flight"] == 0

        stats = await connection.pool_stats()
        _assert_pool_metric_invariants(stats)
        assert stats["connections"] == max_size
        assert stats["active_connections"] == 0
        assert len(session_ids) == max_size
        assert samples > 0
        assert 1 < maximum_application_in_flight <= concurrency
        assert max_size <= maximum_operation_in_flight <= concurrency
        assert maximum_connections == max_size
        assert maximum_sessions == max_size

        record_load_metric(
            "PARAM-033",
            elapsed_seconds=elapsed,
            operations_per_second=operation_count / elapsed,
            submitted=operation_count,
            completed=len(outcomes),
            succeeded=len(successes),
            failed=len(failures),
            maximum_in_flight=maximum_operation_in_flight,
            physical_sessions=len(session_ids),
            pool_max_size=max_size,
        )
    finally:
        stop.set()
        if sampler is not None:
            await sampler
        await connection.disconnect()

    await wait_for_session_count(
        sa_connection,
        application_name,
        expected=baseline_sessions,
    )


@case("QMANY-013")
@pytest.mark.load
@pytest.mark.asyncio
async def test_query_many_thousand_operation_required_profile(
    sql_auth_config: SqlAuthConfig,
    record_load_metric,
) -> None:
    operation_count = 1_000
    connection = _connection(sql_auth_config, max_size=10, min_idle=10)
    yielded = 0
    try:
        async with connection.query_many(
            "SELECT @P1 AS value, @@SPID AS spid",
            ([value] for value in range(operation_count)),
            concurrency=10,
            ordered=True,
        ) as results:
            async for result in results:
                row = result.fetchone()
                assert row["value"] == yielded
                yielded += 1
        assert yielded == operation_count
        stats = await connection.pool_stats()
        assert stats["active_connections"] == 0
        assert stats["connections"] <= 10
        record_load_metric("QMANY-013", operations=yielded)
    finally:
        await connection.disconnect()
