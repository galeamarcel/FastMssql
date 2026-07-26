#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import platform
import re
from statistics import median
import time
from typing import Any
from uuid import uuid4

import fastmssql
from fastmssql import Connection, PoolConfig, SslConfig
import psutil


MAX_OPERATIONS = 99_999
MAX_WORKERS = 500
DEFAULT_OPERATIONS = 99_999
DEFAULT_WORKERS = 200
DEFAULT_POOL_SIZE = 100
REQUIRED_PAIRS = 3
DEFAULT_MAXIMUM_MEDIAN_DEGRADATION = 0.15
PAIR_MODE_ORDER = (
    (False, True),
    (True, False),
    (False, True),
)
SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
OPERATION_NAMES = (
    "connect",
    "ping",
    "query",
    "simple_query",
    "execute",
    "query_batch",
    "execute_batch",
    "bulk_insert",
    "begin",
    "commit",
    "rollback",
    "close",
    "disconnect",
)
BUCKET_BOUNDS_SECONDS = (
    0.0001,
    0.00025,
    0.0005,
    0.001,
    0.0025,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
)
OUTCOME_KEYS = (
    "succeeded",
    "errors",
    "timed_out",
    "cancelled",
    "outcome_unknown",
)
ENTRY_KEYS = {
    "started",
    "completed",
    "in_flight",
    *OUTCOME_KEYS,
    "duration_seconds_sum",
    "duration_seconds_min",
    "duration_seconds_max",
    "duration_seconds_buckets",
    "saturated",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the opt-in FastMssql operation-metrics overhead gate "
            "against the dedicated SQL-auth environment."
        )
    )
    parser.add_argument("--operations", type=int, default=DEFAULT_OPERATIONS)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--pool-size", type=int, default=DEFAULT_POOL_SIZE)
    parser.add_argument("--pairs", type=int, default=REQUIRED_PAIRS)
    parser.add_argument(
        "--maximum-median-degradation",
        type=float,
        default=DEFAULT_MAXIMUM_MEDIAN_DEGRADATION,
    )
    parser.add_argument("--source-sha", required=True)
    parser.add_argument(
        "--metrics-output",
        type=Path,
        required=True,
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.operations <= MAX_OPERATIONS:
        raise ValueError("operations must be between 1 and 99,999")
    if not 1 <= args.workers <= MAX_WORKERS:
        raise ValueError("workers must be between 1 and 500")
    if not 1 <= args.pool_size <= MAX_WORKERS:
        raise ValueError("pool size must be between 1 and 500")
    if args.pairs != REQUIRED_PAIRS:
        raise ValueError("pairs must equal the approved value 3")
    if (
        not math.isfinite(args.maximum_median_degradation)
        or args.maximum_median_degradation < 0.0
    ):
        raise ValueError("maximum median degradation must be finite and non-negative")
    if not SOURCE_SHA.fullmatch(args.source_sha):
        raise ValueError(
            "source SHA must be exactly 40 lowercase hexadecimal characters"
        )


def required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment setting {name}")
    return value


def sql_auth_settings() -> dict[str, object]:
    return {
        "server": required_environment("FASTMSSQL_SQL_AUTH_HOST"),
        "port": int(required_environment("FASTMSSQL_SQL_AUTH_PORT")),
        "database": required_environment("FASTMSSQL_SQL_AUTH_DATABASE"),
        "username": required_environment("FASTMSSQL_SQL_AUTH_OWNER_USER"),
        "password": required_environment("FASTMSSQL_SQL_AUTH_OWNER_PASSWORD"),
    }


def make_connection(
    settings: dict[str, object],
    *,
    application_name: str,
    enabled: bool,
    pool_size: int,
) -> Connection:
    metrics_type = getattr(fastmssql, "OperationMetricsConfig")
    return Connection(
        **settings,
        application_name=application_name,
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=pool_size,
            min_idle=0,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=5,
            test_on_check_out=False,
            retry_connection=False,
        ),
        operation_metrics_config=metrics_type(enabled=enabled),
    )


async def scalar(
    connection: Connection,
    sql: str,
    parameters: list[object] | None = None,
) -> object:
    result = await connection.query(sql, parameters)
    row = result.fetchone()
    if row is None or len(row) != 1:
        raise AssertionError("expected exactly one scalar row")
    return row[0]


async def application_session_count(
    observer: Connection,
    application_name: str,
) -> int:
    value = await scalar(
        observer,
        """
        SELECT COUNT(*)
        FROM sys.dm_exec_sessions
        WHERE program_name = @P1
          AND session_id <> @@SPID
        """,
        [application_name],
    )
    return int(value)


async def wait_for_zero_sessions(
    observer: Connection,
    application_name: str,
    *,
    timeout: float = 8.0,
) -> None:
    deadline = time.monotonic() + timeout
    latest = -1
    while time.monotonic() < deadline:
        latest = await application_session_count(observer, application_name)
        if latest == 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(
        f"candidate SQL sessions did not reach zero; observed {latest}"
    )


def assert_snapshot(snapshot: dict[str, Any], *, enabled: bool) -> None:
    if set(snapshot) != {
        "schema_version",
        "enabled",
        "bucket_bounds_seconds",
        "operations",
    }:
        raise AssertionError("operation statistics root schema changed")
    if snapshot["schema_version"] != 1 or snapshot["enabled"] is not enabled:
        raise AssertionError("operation statistics version/enabled mismatch")
    if tuple(snapshot["bucket_bounds_seconds"]) != BUCKET_BOUNDS_SECONDS:
        raise AssertionError("operation statistics bounds changed")
    if tuple(snapshot["operations"]) != OPERATION_NAMES:
        raise AssertionError("operation statistics names changed")
    for entry in snapshot["operations"].values():
        if set(entry) != ENTRY_KEYS:
            raise AssertionError("operation statistics entry schema changed")
        if entry["started"] != entry["completed"] + entry["in_flight"]:
            raise AssertionError("started/completed/in-flight invariant failed")
        if entry["completed"] != sum(entry[key] for key in OUTCOME_KEYS):
            raise AssertionError("completed/outcome invariant failed")
        buckets = entry["duration_seconds_buckets"]
        if len(buckets) != len(BUCKET_BOUNDS_SECONDS):
            raise AssertionError("operation histogram length changed")
        if any(left > right for left, right in zip(buckets, buckets[1:])):
            raise AssertionError("operation histogram is not cumulative")
        if any(value > entry["completed"] for value in buckets):
            raise AssertionError("operation histogram exceeds completions")


def assert_disabled_zero(snapshot: dict[str, Any]) -> None:
    assert_snapshot(snapshot, enabled=False)
    for entry in snapshot["operations"].values():
        if any(entry[key] != 0 for key in ("started", "completed", *OUTCOME_KEYS)):
            raise AssertionError("disabled operation counter is non-zero")
        if entry["in_flight"] != 0:
            raise AssertionError("disabled in-flight counter is non-zero")
        if entry["duration_seconds_sum"] != 0.0:
            raise AssertionError("disabled duration sum is non-zero")
        if entry["duration_seconds_min"] is not None:
            raise AssertionError("disabled duration minimum is populated")
        if entry["duration_seconds_max"] is not None:
            raise AssertionError("disabled duration maximum is populated")
        if entry["duration_seconds_buckets"] != [0] * 17:
            raise AssertionError("disabled histogram is non-zero")
        if entry["saturated"] is not False:
            raise AssertionError("disabled saturation flag is set")


def query_delta(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, int]:
    left = before["operations"]["query"]
    right = after["operations"]["query"]
    return {
        key: int(right[key]) - int(left[key])
        for key in ("started", "completed", *OUTCOME_KEYS)
    }


def runtime_metadata() -> dict[str, object]:
    return {
        "platform_family": platform.system(),
        "platform_release": platform.release(),
        "python_version": platform.python_version(),
        "fastmssql_version": fastmssql.version(),
        "logical_cpu_count": os.cpu_count(),
        "total_memory_bytes": int(psutil.virtual_memory().total),
    }


def write_evidence(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def run_trial(
    *,
    settings: dict[str, object],
    observer: Connection,
    operations: int,
    workers: int,
    pool_size: int,
    enabled: bool,
    pair_index: int,
    trial_index: int,
) -> dict[str, object]:
    application_name = f"opmet_stress_{pair_index}_{trial_index}_{uuid4().hex[:12]}"
    connection = make_connection(
        settings,
        application_name=application_name,
        enabled=enabled,
        pool_size=pool_size,
    )
    stop = asyncio.Event()
    ticker_task: asyncio.Task[int] | None = None
    sampler_task: asyncio.Task[tuple[int, int, int]] | None = None

    async def worker(worker_id: int) -> tuple[int, int]:
        completed = 0
        value_sum = 0
        for value in range(worker_id, operations, workers):
            returned = await scalar(
                connection,
                "SELECT @P1 AS value",
                [value],
            )
            completed += 1
            value_sum += int(returned)
        return completed, value_sum

    async def ticker() -> int:
        ticks = 0
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0)
        return ticks

    async def sample_bounds() -> tuple[int, int, int]:
        samples = 0
        maximum_pool_connections = 0
        maximum_sql_sessions = 0
        while not stop.is_set():
            pool_stats = await connection.pool_stats()
            maximum_pool_connections = max(
                maximum_pool_connections,
                int(pool_stats["connections"]),
            )
            maximum_sql_sessions = max(
                maximum_sql_sessions,
                await application_session_count(
                    observer,
                    application_name,
                ),
            )
            samples += 1
            await asyncio.sleep(0.01)
        return samples, maximum_pool_connections, maximum_sql_sessions

    try:
        warm_value = await scalar(connection, "SELECT @P1", [-1])
        if warm_value != -1:
            raise AssertionError("warm-up result mismatch")
        before = await connection.operation_stats()
        if enabled:
            assert_snapshot(before, enabled=True)
        else:
            assert_disabled_zero(before)

        ticker_task = asyncio.create_task(ticker())
        sampler_task = asyncio.create_task(sample_bounds())
        worker_tasks: list[asyncio.Task[tuple[int, int]]] = []
        started = time.perf_counter()
        try:
            async with asyncio.TaskGroup() as group:
                for worker_id in range(workers):
                    worker_tasks.append(group.create_task(worker(worker_id)))
            elapsed = time.perf_counter() - started
        finally:
            stop.set()
            ticks = await ticker_task
            ticker_task = None
            (
                samples,
                maximum_pool_connections,
                maximum_sql_sessions,
            ) = await sampler_task
            sampler_task = None

        completed = sum(task.result()[0] for task in worker_tasks)
        value_sum = sum(task.result()[1] for task in worker_tasks)
        if completed != operations:
            raise AssertionError(
                f"completed {completed} operations, expected {operations}"
            )
        expected_sum = operations * (operations - 1) // 2
        if value_sum != expected_sum:
            raise AssertionError(f"result sum {value_sum}, expected {expected_sum}")
        if ticks <= 10:
            raise AssertionError("event loop ticker did not make progress")
        if maximum_pool_connections > pool_size:
            raise AssertionError("pool connection maximum exceeded")
        if maximum_sql_sessions > pool_size:
            raise AssertionError("SQL session maximum exceeded")

        after = await connection.operation_stats()
        if enabled:
            assert_snapshot(after, enabled=True)
            delta = query_delta(before, after)
            expected_delta = {
                "started": operations,
                "completed": operations,
                "succeeded": operations,
                "errors": 0,
                "timed_out": 0,
                "cancelled": 0,
                "outcome_unknown": 0,
            }
            if delta != expected_delta:
                raise AssertionError(f"enabled query metric mismatch: {delta}")
        else:
            assert_disabled_zero(after)
            delta = {
                "started": 0,
                "completed": 0,
                "succeeded": 0,
                "errors": 0,
                "timed_out": 0,
                "cancelled": 0,
                "outcome_unknown": 0,
            }

        if await scalar(connection, "SELECT @P1", [operations]) != operations:
            raise AssertionError("post-load health query failed")
        throughput = operations / elapsed
        return {
            "pair_index": pair_index,
            "trial_index": trial_index,
            "metrics_enabled": enabled,
            "operation_count": operations,
            "worker_count": workers,
            "pool_size": pool_size,
            "elapsed_seconds": elapsed,
            "transactions_per_second": throughput,
            "completed_results": completed,
            "result_sum": value_sum,
            "event_loop_ticks": ticks,
            "sampler_iterations": samples,
            "maximum_pool_connections": maximum_pool_connections,
            "maximum_sql_sessions": maximum_sql_sessions,
            "query_metric_delta": delta,
            "zero_sessions_after_teardown": True,
        }
    finally:
        stop.set()
        for task in (ticker_task, sampler_task):
            if task is not None:
                await task
        await connection.disconnect()
        await wait_for_zero_sessions(observer, application_name)


async def run_gate(args: argparse.Namespace) -> int:
    settings = sql_auth_settings()
    observer = Connection(
        **settings,
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
    )
    evidence: dict[str, object] = {
        "schema_version": 1,
        "source_sha": args.source_sha,
        "status": "running",
        "runtime": runtime_metadata(),
        "configuration": {
            "operations_per_trial": args.operations,
            "workers": args.workers,
            "pool_size": args.pool_size,
            "pairs": args.pairs,
            "maximum_median_degradation": (args.maximum_median_degradation),
            "pair_mode_order": [
                ["enabled" if enabled else "disabled" for enabled in pair]
                for pair in PAIR_MODE_ORDER
            ],
            "warmups_per_fresh_connection": 1,
            "operation_stats_scrapes_during_timing": 0,
        },
        "trials": [],
        "pairs": [],
    }
    write_evidence(args.metrics_output, evidence)

    try:
        trials: list[dict[str, object]] = []
        pair_results: list[dict[str, object]] = []
        degradations: list[float] = []
        for pair_index, modes in enumerate(PAIR_MODE_ORDER, start=1):
            current: list[dict[str, object]] = []
            for trial_index, enabled in enumerate(modes, start=1):
                trial = await run_trial(
                    settings=settings,
                    observer=observer,
                    operations=args.operations,
                    workers=args.workers,
                    pool_size=args.pool_size,
                    enabled=enabled,
                    pair_index=pair_index,
                    trial_index=trial_index,
                )
                current.append(trial)
                trials.append(trial)
                evidence["trials"] = trials
                write_evidence(args.metrics_output, evidence)

            disabled = next(
                trial for trial in current if trial["metrics_enabled"] is False
            )
            enabled_trial = next(
                trial for trial in current if trial["metrics_enabled"] is True
            )
            disabled_throughput = float(disabled["transactions_per_second"])
            enabled_throughput = float(enabled_trial["transactions_per_second"])
            degradation = (
                disabled_throughput - enabled_throughput
            ) / disabled_throughput
            degradations.append(degradation)
            pair_result = {
                "pair_index": pair_index,
                "trial_order": [
                    "enabled" if trial["metrics_enabled"] else "disabled"
                    for trial in current
                ],
                "disabled_transactions_per_second": disabled_throughput,
                "enabled_transactions_per_second": enabled_throughput,
                "degradation": degradation,
            }
            pair_results.append(pair_result)
            evidence["pairs"] = pair_results
            write_evidence(args.metrics_output, evidence)

        median_degradation = median(degradations)
        passed = median_degradation <= args.maximum_median_degradation
        evidence.update(
            {
                "status": "passed" if passed else "failed",
                "median_degradation": median_degradation,
                "gate_passed": passed,
            }
        )
        write_evidence(args.metrics_output, evidence)
        return 0 if passed else 1
    finally:
        await observer.disconnect()


def main() -> int:
    args = parse_args()
    validate_args(args)
    return asyncio.run(run_gate(args))


if __name__ == "__main__":
    raise SystemExit(main())
