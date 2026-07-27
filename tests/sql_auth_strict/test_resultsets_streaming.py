from __future__ import annotations

import asyncio
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

from fastmssql import Connection, PoolConfig, SslConfig
import psutil
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import CleanupRegistry, quote_identifier, scalar


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STRESS_METRICS = (
    ROOT / ".artifacts/sql-auth/result-stream-stress-metrics.json"
)
DEFAULT_STRESS_RESULTS = (
    ROOT / ".artifacts/sql-auth/result-stream-load-results.json"
)
SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_OPERATIONS = 1_000
REQUIRED_CONCURRENCY = 64
REQUIRED_POOL_SIZE = 8
REQUIRED_BUFFER_SIZE = 8
REQUIRED_RSS_GROWTH_LIMIT = 134_217_728


def _single_pool_connection(
    config: SqlAuthConfig,
    *,
    application_name: str,
) -> Connection:
    return Connection(
        server=config.host,
        port=config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=1,
            min_idle=0,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=5,
            test_on_check_out=False,
            retry_connection=False,
        ),
        application_name=application_name,
    )


async def _stream(
    connection: Connection,
    sql: str,
    params: list[object] | None = None,
    *,
    buffer_size: int = 64,
):
    method = getattr(connection, "stream", None)
    assert callable(method), "Connection.stream() result API is missing"
    return await method(sql, params, buffer_size=buffer_size)


async def _batch(
    connection: Connection,
    sql: str,
    *,
    buffer_size: int = 64,
):
    method = getattr(connection, "batch", None)
    assert callable(method), "Connection.batch() result API is missing"
    return await method(sql, buffer_size=buffer_size)


async def _rows(result_set) -> list[dict[str, object]]:
    return [row.to_dict() async for row in result_set]


async def _wait_for_active(
    connection: Connection,
    expected: int,
    *,
    timeout: float = 5.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stats = await connection.pool_stats()
        if stats["active_connections"] == expected:
            return stats
        await asyncio.sleep(0.01)
    observed = await connection.pool_stats()
    raise AssertionError(
        f"expected {expected} active connection(s), observed {observed}"
    )


async def _connection_identity(connection: Connection) -> tuple[int, str]:
    result = await connection.query(
        """
        SELECT
            @@SPID AS session_id,
            CONVERT(VARCHAR(36), connection_id) AS connection_id
        FROM sys.dm_exec_connections
        WHERE session_id = @@SPID
        """
    )
    row = result.fetchone()
    assert row is not None
    return int(row["session_id"]), str(row["connection_id"])


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    value = completed.stdout.strip()
    assert SOURCE_SHA.fullmatch(value)
    return value


def _id_digest(operations: int) -> str:
    digest = hashlib.sha256()
    for operation_id in range(operations):
        digest.update(str(operation_id).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _assert_latency_summary(summary: object) -> dict[str, int]:
    assert isinstance(summary, dict)
    assert set(summary) == {"p50_ns", "p95_ns", "p99_ns", "max_ns"}
    values = {
        name: int(value)
        for name, value in summary.items()
    }
    assert 0 <= values["p50_ns"] <= values["p95_ns"]
    assert values["p95_ns"] <= values["p99_ns"] <= values["max_ns"]
    return values


@case("RESULT-016")
@pytest.mark.asyncio
async def test_three_result_sets_are_streamed_in_wire_order(
    owner_connection: Connection,
) -> None:
    response = await _batch(
        owner_connection,
        """
        SET NOCOUNT ON;
        SELECT CAST(1 AS INT) AS set_number, N'first' AS label;
        SELECT value
        FROM (VALUES (20), (10)) AS source(value)
        ORDER BY value;
        SELECT CAST(3 AS INT) AS set_number, N'third' AS label;
        """,
        buffer_size=3,
    )

    observed: list[tuple[int, tuple[str, ...], list[dict[str, object]]]] = []
    async with response as entered:
        assert entered is response
        async for result_set in response:
            observed.append(
                (
                    result_set.index,
                    result_set.column_names,
                    await _rows(result_set),
                )
            )

    assert observed == [
        (0, ("set_number", "label"), [{"set_number": 1, "label": "first"}]),
        (1, ("value",), [{"value": 10}, {"value": 20}]),
        (2, ("set_number", "label"), [{"set_number": 3, "label": "third"}]),
    ]
    assert response.complete is True
    assert response.closed is True
    assert response.summary.result_set_count == 3
    assert await scalar(owner_connection, "SELECT 1") == 1


@case("RESULT-017")
@pytest.mark.asyncio
async def test_empty_middle_set_retains_exact_declared_metadata(
    owner_connection: Connection,
    cleanup_registry: CleanupRegistry,
    unique_sql_name,
) -> None:
    raw_table = unique_sql_name("result_metadata")
    table = quote_identifier(raw_table)
    await owner_connection.execute(
        f"""
        CREATE TABLE {table} (
            nchar_value NCHAR(5) NOT NULL,
            nvarchar_value NVARCHAR(7) NULL,
            char_value CHAR(9) NOT NULL,
            varchar_value VARCHAR(11) NULL,
            binary_value VARBINARY(13) NOT NULL,
            nvarchar_max NVARCHAR(MAX) NULL,
            varchar_max VARCHAR(MAX) NULL,
            binary_max VARBINARY(MAX) NULL,
            decimal_value DECIMAL(19, 4) NOT NULL,
            datetime2_value DATETIME2(3) NULL
        )
        """
    )
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")

    response = await _batch(
        owner_connection,
        f"""
        SET NOCOUNT ON;
        SELECT CAST(1 AS INT) AS marker;
        SELECT
            nchar_value,
            nvarchar_value,
            char_value,
            varchar_value,
            binary_value,
            nvarchar_max,
            varchar_max,
            binary_max,
            decimal_value,
            datetime2_value
        FROM {table}
        WHERE 1 = 0;
        SELECT CAST(3 AS INT) AS marker;
        """,
        buffer_size=2,
    )

    first = await response.__anext__()
    assert await _rows(first) == [{"marker": 1}]
    middle = await response.__anext__()
    assert middle.index == 1
    assert await _rows(middle) == []
    assert tuple(
        (
            column.ordinal,
            column.name,
            column.type_name,
            column.nullable,
            column.precision,
            column.scale,
            column.length,
        )
        for column in middle.columns
    ) == (
        (0, "nchar_value", "nchar", False, None, None, 5),
        (1, "nvarchar_value", "nvarchar", True, None, None, 7),
        (2, "char_value", "char", False, None, None, 9),
        (3, "varchar_value", "varchar", True, None, None, 11),
        (4, "binary_value", "varbinary", False, None, None, 13),
        (5, "nvarchar_max", "nvarchar", True, None, None, "MAX"),
        (6, "varchar_max", "varchar", True, None, None, "MAX"),
        (7, "binary_max", "varbinary", True, None, None, "MAX"),
        (8, "decimal_value", "decimal", False, 19, 4, None),
        (9, "datetime2_value", "datetime2", True, None, 3, None),
    )
    with pytest.raises(AttributeError):
        middle.columns[0].name = "mutated"
    third = await response.__anext__()
    assert await _rows(third) == [{"marker": 3}]
    with pytest.raises(StopAsyncIteration):
        await response.__anext__()
    assert response.summary.result_set_count == 3


@case("RESULT-018")
@pytest.mark.asyncio
async def test_outer_inner_protocols_are_async_and_context_exit_is_explicit(
    owner_connection: Connection,
) -> None:
    response = await _stream(
        owner_connection,
        "SELECT @P1 AS value",
        [18],
        buffer_size=1,
    )
    assert response.__aiter__() is response
    with pytest.raises(TypeError):
        iter(response)
    with pytest.raises(TypeError):
        next(response)

    pending_set = response.__anext__()
    assert hasattr(pending_set, "__await__")
    result_set = await pending_set
    assert result_set.__aiter__() is result_set
    with pytest.raises(TypeError):
        iter(result_set)
    with pytest.raises(TypeError):
        next(result_set)
    pending_row = result_set.__anext__()
    assert hasattr(pending_row, "__await__")
    assert (await pending_row)["value"] == 18
    with pytest.raises(StopAsyncIteration):
        await result_set.__anext__()
    summary = await response.finish()
    assert summary.result_set_count == 1
    assert response.summary.result_set_count == 1
    assert response.complete is True

    early = await _batch(
        owner_connection,
        """
        WAITFOR DELAY '00:00:00.050';
        SELECT value
        FROM (VALUES (1), (2), (3)) AS source(value);
        """,
        buffer_size=1,
    )
    async with early:
        assert early.closed is False
    assert early.closed is True
    assert early.complete is False
    assert await scalar(owner_connection, "SELECT 18") == 18


@case("RESULT-019")
@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_slow_consumer_has_bounded_event_credit_and_measured_rss(
    owner_connection: Connection,
    record_property,
) -> None:
    row_count = 2_048
    payload_bytes_per_row = 32_768
    buffer_size = 8
    rss_growth_limit_bytes = 64 * 1024 * 1024
    process = psutil.Process()
    gc.collect()
    baseline_rss = process.memory_info().rss
    peak_rss = baseline_rss
    stop_sampler = asyncio.Event()

    async def sample_rss() -> None:
        nonlocal peak_rss
        while not stop_sampler.is_set():
            peak_rss = max(peak_rss, process.memory_info().rss)
            await asyncio.sleep(0.002)

    response = await _stream(
        owner_connection,
        """
        WITH numbers AS (
            SELECT 1 AS value
            UNION ALL
            SELECT value + 1
            FROM numbers
            WHERE value < 2048
        )
        SELECT
            value,
            CONVERT(
                VARBINARY(MAX),
                REPLICATE(CAST('x' AS VARCHAR(MAX)), @P1)
            ) AS payload
        FROM numbers
        ORDER BY value
        OPTION (MAXRECURSION 0)
        """,
        [payload_bytes_per_row],
        buffer_size=buffer_size,
    )
    await _wait_for_active(owner_connection, 1)
    sampler = asyncio.create_task(sample_rss())
    observed = 0
    try:
        async for result_set in response:
            async for row in result_set:
                observed += 1
                assert row["value"] == observed
                assert len(row["payload"]) == payload_bytes_per_row
                await asyncio.sleep(0.0005)
    finally:
        stop_sampler.set()
        await sampler

    gc.collect()
    final_rss = process.memory_info().rss
    peak_rss = max(peak_rss, final_rss)
    rss_growth = max(0, peak_rss - baseline_rss)
    assert observed == row_count
    assert response.complete is True
    assert rss_growth <= rss_growth_limit_bytes
    assert buffer_size * payload_bytes_per_row < rss_growth_limit_bytes
    await _wait_for_active(owner_connection, 0)

    record_property("row_count", row_count)
    record_property("payload_bytes_per_row", payload_bytes_per_row)
    record_property("buffer_size", buffer_size)
    record_property("baseline_rss_bytes", baseline_rss)
    record_property("peak_rss_bytes", peak_rss)
    record_property("final_rss_bytes", final_rss)
    record_property("rss_growth_limit_bytes", rss_growth_limit_bytes)
    record_property(
        "bound_scope",
        "event-count bound; individual rows and LOBs are not byte-chunked",
    )


@case("RESULT-020")
@pytest.mark.asyncio
async def test_normal_eof_reuses_but_security_sql_retires_size_one_lease(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _single_pool_connection(
        sql_auth_config,
        application_name="fastmssql_result_020",
    )
    try:
        await connection.connect()
        first_identity = await _connection_identity(connection)
        normal = await _stream(
            connection,
            "SELECT @@SPID AS session_id, @P1 AS value",
            [20],
            buffer_size=1,
        )
        normal_rows: list[dict[str, object]] = []
        async for result_set in normal:
            normal_rows.extend(await _rows(result_set))
        assert normal_rows == [
            {"session_id": first_identity[0], "value": 20}
        ]
        await _wait_for_active(connection, 0)
        reset_identity = await _connection_identity(connection)
        assert reset_identity == first_identity

        retiring = await _batch(
            connection,
            """
            EXECUTE AS USER = 'dbo';
            SELECT @@SPID AS retired_session_id, CAST(1 AS INT) AS value;
            REVERT;
            """,
            buffer_size=1,
        )
        retiring_rows: list[dict[str, object]] = []
        async for result_set in retiring:
            retiring_rows.extend(await _rows(result_set))
        assert retiring_rows == [
            {"retired_session_id": first_identity[0], "value": 1}
        ]
        assert retiring.complete is True
        await _wait_for_active(connection, 0)

        replacement_identity = await _connection_identity(connection)
        assert replacement_identity[0] != first_identity[0]
        assert replacement_identity[1] != first_identity[1]
        assert await scalar(connection, "SELECT 20") == 20
    finally:
        await connection.disconnect()


@case("RESULT-021")
@pytest.mark.asyncio
async def test_result_set_aclose_skips_only_that_set_and_keeps_empty_next_set(
    owner_connection: Connection,
) -> None:
    response = await _batch(
        owner_connection,
        """
        SET NOCOUNT ON;
        WITH numbers AS (
            SELECT 1 AS value
            UNION ALL
            SELECT value + 1 FROM numbers WHERE value < 512
        )
        SELECT value, REPLICATE(N'x', 1024) AS skipped_payload
        FROM numbers
        ORDER BY value
        OPTION (MAXRECURSION 0);
        SELECT CAST(NULL AS NVARCHAR(12)) AS empty_value WHERE 1 = 0;
        SELECT CAST(21 AS INT) AS retained_value;
        """,
        buffer_size=2,
    )

    first = await response.__anext__()
    assert first.index == 0
    await first.aclose()
    assert first.closed is True

    empty = await response.__anext__()
    assert empty.index == 1
    assert empty.column_names == ("empty_value",)
    assert empty.columns[0].type_name == "nvarchar"
    assert empty.columns[0].length == 12
    assert await _rows(empty) == []

    retained = await response.__anext__()
    assert retained.index == 2
    assert await _rows(retained) == [{"retained_value": 21}]
    with pytest.raises(StopAsyncIteration):
        await response.__anext__()
    assert response.summary.result_set_count == 3
    assert await scalar(owner_connection, "SELECT 21") == 21


@case("RESULT-025")
@pytest.mark.asyncio
async def test_concurrent_consumer_and_buffer_errors_fail_locally_and_safely(
    owner_connection: Connection,
) -> None:
    before = await owner_connection.pool_stats()
    probe = "result_025_private_sql_probe"
    invalid_values = (True, False, 0, 1_025, -1, 8.0, "8", None)
    for invalid in invalid_values:
        with pytest.raises((TypeError, ValueError)) as captured:
            await _stream(
                owner_connection,
                f"SELECT N'{probe}'",
                buffer_size=invalid,
            )
        assert probe not in str(captured.value)
    after = await owner_connection.pool_stats()
    assert after["get_started"] == before["get_started"]

    response = await _batch(
        owner_connection,
        """
        WAITFOR DELAY '00:00:00.150';
        SELECT CAST(25 AS INT) AS value;
        """,
        buffer_size=1,
    )
    first_consumer = asyncio.create_task(response.__anext__())
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError) as captured:
        await response.__anext__()
    assert probe not in str(captured.value)
    result_set = await first_consumer
    assert await _rows(result_set) == [{"value": 25}]
    assert (await response.finish()).result_set_count == 1


@case("RESULT-028")
@pytest.mark.asyncio
async def test_done_zero_counts_and_info_messages_remain_separate(
    owner_connection: Connection,
) -> None:
    response = await _batch(
        owner_connection,
        """
        SET NOCOUNT OFF;
        CREATE TABLE #result_done_probe (id INT NOT NULL PRIMARY KEY);
        INSERT INTO #result_done_probe (id) VALUES (1), (2);
        UPDATE #result_done_probe SET id = id WHERE id < 0;
        PRINT N'fastmssql print message';
        RAISERROR(N'fastmssql informational message', 10, 7);
        SELECT COUNT_BIG(*) AS retained_rows FROM #result_done_probe;
        DROP TABLE #result_done_probe;
        """,
        buffer_size=2,
    )
    rows: list[dict[str, object]] = []
    async for result_set in response:
        rows.extend(await _rows(result_set))
    assert rows == [{"retained_rows": 2}]

    summary = response.summary
    assert summary.result_set_count == 1
    assert isinstance(summary.done, tuple)
    assert any(done.rows_affected == 2 for done in summary.done)
    assert any(done.rows_affected == 0 for done in summary.done)
    assert any(done.rows_affected is None for done in summary.done)
    assert all(
        done.kind in {"DONE", "DONEPROC", "DONEINPROC"}
        for done in summary.done
    )
    assert isinstance(summary.messages, tuple)
    assert {
        message.message for message in summary.messages
    } >= {
        "fastmssql print message",
        "fastmssql informational message",
    }
    assert all(message.severity <= 10 for message in summary.messages)
    assert summary.return_status is None
    with pytest.raises(AttributeError):
        summary.result_set_count = 99
    with pytest.raises(AttributeError):
        summary.done[0].rows_affected = 99
    with pytest.raises(AttributeError):
        summary.messages[0].message = "mutated"
    assert "fastmssql print message" not in repr(summary)
    assert all(
        message.message not in repr(message)
        for message in summary.messages
    )
    first_snapshot = summary.output_parameters
    second_snapshot = summary.output_parameters
    assert first_snapshot == second_snapshot == {}
    assert first_snapshot is not second_snapshot
    first_snapshot["mutated"] = True
    assert summary.output_parameters == {}


@case("RESULT-029")
def test_required_result_stream_stress_artifact_is_fresh_and_complete() -> None:
    metrics_path = Path(
        os.getenv(
            "FASTMSSQL_RESULT_STREAM_STRESS_METRICS_PATH",
            str(DEFAULT_STRESS_METRICS),
        )
    )
    results_path = Path(
        os.getenv(
            "FASTMSSQL_RESULT_STREAM_STRESS_RESULTS_PATH",
            str(DEFAULT_STRESS_RESULTS),
        )
    )
    assert metrics_path.is_file(), f"missing stress metrics: {metrics_path}"
    assert results_path.is_file(), f"missing stress result: {results_path}"

    head = _git_head()
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    results = json.loads(results_path.read_text(encoding="utf-8"))
    assert metrics["schema_version"] == 1
    assert metrics["source_sha"] == head
    assert metrics["status"] == "passed"
    assert results["schema_version"] == 1
    assert results["source_sha"] == head
    result = results["cases"]["RESULT-029"]
    assert result["outcome"] == "passed"
    assert result["nodeid"] == "external::result_stream_stress[1000:64]"

    configuration = metrics["configuration"]
    assert configuration == {
        "profiles": [{"operations": 1_000, "concurrency": 64}],
        "pool_size": REQUIRED_POOL_SIZE,
        "buffer_size": REQUIRED_BUFFER_SIZE,
        "rss_growth_limit_bytes": REQUIRED_RSS_GROWTH_LIMIT,
        "queue_maxsize_factor": 2,
        "percentile_method": "nearest_rank",
        "worker_model": "long_lived",
    }
    assert len(metrics["profiles"]) == 1
    profile = metrics["profiles"][0]
    assert profile["status"] == "passed"
    assert profile["operations"] == REQUIRED_OPERATIONS
    assert profile["concurrency"] == REQUIRED_CONCURRENCY
    assert profile["application_name"].startswith(
        "fastmssql_result_stress_"
    )
    assert profile["worker_count"] == REQUIRED_CONCURRENCY
    assert profile["queue_maxsize"] == 2 * REQUIRED_CONCURRENCY
    assert profile["total"] == REQUIRED_OPERATIONS
    assert profile["succeeded"] == REQUIRED_OPERATIONS
    assert profile["failed"] == 0
    assert profile["timed_out"] == 0
    assert profile["completed_id_count"] == REQUIRED_OPERATIONS
    assert profile["completed_id_min"] == 0
    assert profile["completed_id_max"] == REQUIRED_OPERATIONS - 1
    assert profile["completed_id_sum"] == (
        REQUIRED_OPERATIONS * (REQUIRED_OPERATIONS - 1) // 2
    )
    assert profile["completed_ids_sha256"] == _id_digest(
        REQUIRED_OPERATIONS
    )
    assert profile["missing_ids"] == []
    assert profile["duplicate_ids"] == []
    assert profile["failure_types"] == {}
    assert profile["violations"] == []

    wall = float(profile["wall_duration_seconds"])
    throughput = float(profile["operations_per_second"])
    assert math.isfinite(wall) and wall > 0.0
    assert math.isfinite(throughput) and throughput > 0.0
    assert math.isclose(
        throughput,
        REQUIRED_OPERATIONS / wall,
        rel_tol=1e-12,
    )
    admitted = _assert_latency_summary(
        profile["admitted_driver_latency_ns"]
    )
    scheduled = _assert_latency_summary(
        profile["scheduled_end_to_end_latency_ns"]
    )
    assert all(
        scheduled[name] >= admitted[name]
        for name in ("p50_ns", "p95_ns", "p99_ns", "max_ns")
    )

    pool = profile["pool"]
    deltas = pool["deltas"]
    assert deltas["get_started"] == REQUIRED_OPERATIONS
    assert deltas["get_timed_out"] == 0
    assert (
        deltas["get_direct"]
        + deltas["get_waited"]
        + deltas["get_timed_out"]
        == deltas["get_started"]
    )
    assert deltas["get_wait_time_seconds"] >= 0.0
    assert 0 <= pool["peak_pending_gets"] <= REQUIRED_CONCURRENCY
    assert 1 <= pool["peak_active_connections"] <= REQUIRED_POOL_SIZE
    assert pool["final_active_connections"] == 0
    assert profile["unique_sql_spid_count"] == len(
        profile["unique_sql_spids"]
    )
    assert profile["unique_sql_spids"] == sorted(
        set(profile["unique_sql_spids"])
    )
    assert 1 <= profile["unique_sql_spid_count"] <= REQUIRED_POOL_SIZE
    assert 1 <= profile["max_concurrent_sql_spids"] <= REQUIRED_POOL_SIZE

    rss = profile["rss"]
    assert rss["limit_bytes"] == REQUIRED_RSS_GROWTH_LIMIT
    assert rss["baseline_bytes"] > 0
    assert rss["peak_bytes"] >= rss["baseline_bytes"]
    assert rss["peak_bytes"] >= rss["final_bytes"]
    assert rss["growth_bytes"] == max(
        0,
        rss["peak_bytes"] - rss["baseline_bytes"],
    )
    assert rss["growth_bytes"] <= rss["limit_bytes"]
    assert math.isfinite(profile["python_process_cpu_seconds"])
    assert profile["python_process_cpu_seconds"] >= 0.0
    assert profile["sql_session_cpu_time_ms_delta"] >= 0
    ticker = profile["event_loop_ticker"]
    assert ticker["count"] > 0
    assert math.isfinite(ticker["max_scheduling_gap_seconds"])
    assert ticker["max_scheduling_gap_seconds"] >= 0.0
    assert profile["sampler_iterations"] > 0
    assert profile["post_load_smoke"] is True
    assert math.isclose(
        float(result["duration_seconds"]),
        wall,
        rel_tol=1e-12,
    )

    serialized = json.dumps(
        {"metrics": metrics, "results": results},
        sort_keys=True,
    )
    for name, value in os.environ.items():
        if value and any(
            token in name.upper()
            for token in ("PASSWORD", "TOKEN", "SECRET")
        ):
            assert value not in serialized
