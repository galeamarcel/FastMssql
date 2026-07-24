from __future__ import annotations

import asyncio
import gc
import subprocess
import time
import tracemalloc
from collections.abc import AsyncIterator, Callable

from fastmssql import (
    Connection,
    PoolConfig,
    ProtocolError,
    SqlConnectionError,
    SslConfig,
)
import psutil
import pytest
import pytest_asyncio

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import (
    assert_dedicated_container,
    quote_identifier,
    scalar,
)


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]

CONTAINER = "fastmssql-sql-auth-dev"


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
        assert await asyncio.wait_for(
            scalar(recovered, "SELECT 3"), timeout=3.0
        ) == 3
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
        assert await asyncio.wait_for(
            scalar(recreated, "SELECT 5"), timeout=3.0
        ) == 5
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
                "WAITFOR DELAY '00:00:30'; "
                f"SELECT CAST(1 AS INT) AS value; -- {token}"
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
            (SqlConnectionError, ProtocolError),
        )
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
            return await scalar(
                connection, "SELECT @P1 AS value", [value]
            )

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
            rows = [
                [offset + index, (offset + index) * 2]
                for index in range(size)
            ]
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
    baseline = await _application_session_count(
        sa_connection, application_name
    )
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
        observed = await _application_session_count(
            sa_connection, application_name
        )
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
            asyncio.gather(
                *(transaction_operation(value) for value in range(100))
            ),
        )
        elapsed = time.monotonic() - started
        assert query_values == list(range(300))
        assert update_counts == [1] * 100
        assert transaction_values == list(range(100))
        assert await scalar(
            owner_connection, f"SELECT SUM(value) FROM {table}"
        ) == 100
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
        assert await scalar(
            connection,
            """
            SELECT CASE
                WHEN CAST(SERVERPROPERTY('Edition') AS NVARCHAR(128))
                     LIKE '%Developer%'
                THEN 1 ELSE 0
            END
            """,
        ) == 1
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

        assert sorted(value for value, _ in outcomes) == list(
            range(transaction_count)
        )
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
            distinct_session_count=len(
                {session_id for _, session_id in outcomes}
            ),
            transactions_per_second=transaction_count / elapsed,
        )
    finally:
        await owner_connection.execute(f"DROP TABLE IF EXISTS {table}")
