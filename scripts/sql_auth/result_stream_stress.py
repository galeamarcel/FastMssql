#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
from typing import Any
from uuid import uuid4

import fastmssql
from fastmssql import (
    Connection,
    OperationTimeoutError,
    PoolConfig,
    SslConfig,
)
import psutil


ROOT = Path(__file__).resolve().parents[2]
MAX_OPERATIONS = 99_999
MAX_CONCURRENCY = 500
MAX_POOL_SIZE = 500
MAX_BUFFER_SIZE = 1_024
DEFAULT_PROFILE = "1000:64"
DEFAULT_POOL_SIZE = 8
DEFAULT_BUFFER_SIZE = 8
DEFAULT_RSS_GROWTH_LIMIT_BYTES = 134_217_728
SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
POOL_DELTA_KEYS = (
    "get_started",
    "get_direct",
    "get_waited",
    "get_timed_out",
    "get_wait_time_seconds",
)


@dataclass(frozen=True)
class Profile:
    operations: int
    concurrency: int


@dataclass
class WorkItem:
    operation_id: int
    scheduled_ns: int = 0
    ready: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(frozen=True, repr=False)
class SqlAuthSettings:
    host: str
    port: int
    database: str
    username: str
    password: str

    @classmethod
    def from_env(cls) -> SqlAuthSettings:
        password = os.getenv("FASTMSSQL_SQL_AUTH_OWNER_PASSWORD", "")
        if not password:
            raise RuntimeError(
                "missing required environment setting "
                "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD"
            )
        return cls(
            host=os.getenv("FASTMSSQL_SQL_AUTH_HOST", "127.0.0.1"),
            port=int(os.getenv("FASTMSSQL_SQL_AUTH_PORT", "14334")),
            database=os.getenv(
                "FASTMSSQL_SQL_AUTH_DATABASE",
                "fastmssql_validation",
            ),
            username=os.getenv(
                "FASTMSSQL_SQL_AUTH_OWNER_USER",
                "fastmssql_owner",
            ),
            password=password,
        )

    def connection(
        self,
        *,
        application_name: str,
        pool_size: int,
    ) -> Connection:
        return Connection(
            server=self.host,
            port=self.port,
            database=self.database,
            username=self.username,
            password=self.password,
            application_name=application_name,
            ssl_config=SslConfig.development(),
            pool_config=PoolConfig(
                max_size=pool_size,
                min_idle=0,
                max_lifetime_secs=None,
                idle_timeout_secs=None,
                connection_timeout_secs=10,
                test_on_check_out=False,
                retry_connection=False,
            ),
        )


def parse_profiles(value: str) -> tuple[Profile, ...]:
    profiles: list[Profile] = []
    for raw_profile in value.split(","):
        parts = raw_profile.strip().split(":")
        if len(parts) != 2:
            raise argparse.ArgumentTypeError(
                f"invalid profile {raw_profile!r}; "
                "expected OPERATIONS:CONCURRENCY"
            )
        try:
            operations = int(parts[0].replace("_", ""))
            concurrency = int(parts[1].replace("_", ""))
        except ValueError as error:
            raise argparse.ArgumentTypeError(
                f"invalid numeric profile {raw_profile!r}"
            ) from error
        if not 1 <= operations <= MAX_OPERATIONS:
            raise argparse.ArgumentTypeError(
                "operations must be between 1 and 99,999"
            )
        if not 1 <= concurrency <= MAX_CONCURRENCY:
            raise argparse.ArgumentTypeError(
                "concurrency must be between 1 and 500"
            )
        if concurrency > operations:
            raise argparse.ArgumentTypeError(
                "concurrency cannot exceed operation count"
            )
        profiles.append(Profile(operations, concurrency))
    if not profiles:
        raise argparse.ArgumentTypeError("at least one profile is required")
    return tuple(profiles)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run bounded FastMssql ResultStream SQL-auth profiles. "
            "Operations may reach 99,999; concurrency is capped at 500."
        )
    )
    parser.add_argument(
        "--profiles",
        type=parse_profiles,
        default=parse_profiles(DEFAULT_PROFILE),
        help="comma-separated OPERATIONS:CONCURRENCY profiles",
    )
    parser.add_argument(
        "--pool-size",
        type=int,
        default=DEFAULT_POOL_SIZE,
    )
    parser.add_argument(
        "--buffer-size",
        type=int,
        default=DEFAULT_BUFFER_SIZE,
    )
    parser.add_argument(
        "--rss-growth-limit-bytes",
        type=int,
        default=DEFAULT_RSS_GROWTH_LIMIT_BYTES,
    )
    parser.add_argument("--metrics-output", type=Path, required=True)
    parser.add_argument("--results-output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.pool_size <= MAX_POOL_SIZE:
        parser.error("--pool-size must be between 1 and 500")
    if not 1 <= args.buffer_size <= MAX_BUFFER_SIZE:
        parser.error("--buffer-size must be between 1 and 1,024")
    if args.rss_growth_limit_bytes < 0:
        parser.error("--rss-growth-limit-bytes must be non-negative")
    return args


def git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    value = completed.stdout.strip()
    if not SOURCE_SHA.fullmatch(value):
        raise RuntimeError("git HEAD is not a full lowercase source SHA")
    return value


def runtime_metadata() -> dict[str, object]:
    return {
        "platform_family": platform.system(),
        "platform_release": platform.release(),
        "python_version": platform.python_version(),
        "fastmssql_version": fastmssql.version(),
        "logical_cpu_count": os.cpu_count(),
        "total_memory_bytes": int(psutil.virtual_memory().total),
    }


def atomic_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def nearest_rank(values: list[int], percentile: int) -> int:
    if not values:
        raise ValueError("cannot calculate a percentile without observations")
    if not 1 <= percentile <= 100:
        raise ValueError("percentile must be between 1 and 100")
    ordered = sorted(values)
    rank = math.ceil(percentile * len(ordered) / 100)
    return ordered[rank - 1]


def latency_summary(values: list[int]) -> dict[str, int]:
    return {
        "p50_ns": nearest_rank(values, 50),
        "p95_ns": nearest_rank(values, 95),
        "p99_ns": nearest_rank(values, 99),
        "max_ns": max(values),
    }


def id_digest(operation_ids: list[int]) -> str:
    digest = hashlib.sha256()
    for operation_id in sorted(operation_ids):
        digest.update(str(operation_id).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


async def scalar(
    connection: Connection,
    sql: str,
    params: list[object] | None = None,
) -> object:
    result = await connection.query(sql, params)
    row = result.fetchone()
    if row is None or len(row) != 1:
        raise AssertionError("expected exactly one scalar row")
    return row[0]


async def session_metrics(
    observer: Connection,
    application_name: str,
) -> tuple[int, int]:
    result = await observer.query(
        """
        SELECT
            COUNT_BIG(*) AS session_count,
            COALESCE(SUM(CONVERT(BIGINT, cpu_time)), 0) AS cpu_time_ms
        FROM sys.dm_exec_sessions
        WHERE program_name = @P1
        """,
        [application_name],
    )
    row = result.fetchone()
    if row is None:
        raise AssertionError("SQL session metric row is missing")
    return int(row["session_count"]), int(row["cpu_time_ms"])


def _require_result_stream_api() -> None:
    required = (
        "ResultStream",
        "ResultSet",
        "ResultSummary",
        "ColumnMetadata",
        "DoneResult",
        "SqlMessage",
    )
    missing = [name for name in required if not hasattr(fastmssql, name)]
    if missing or not hasattr(Connection, "stream"):
        raise RuntimeError("bounded ResultStream API is unavailable")


def _profile_violations(
    metrics: dict[str, Any],
    *,
    pool_size: int,
    rss_growth_limit_bytes: int,
) -> list[str]:
    operations = int(metrics["operations"])
    violations: list[str] = []
    if (
        metrics["total"] != operations
        or metrics["succeeded"] != operations
        or metrics["failed"] != 0
        or metrics["timed_out"] != 0
    ):
        violations.append("operation_outcome_mismatch")
    if (
        metrics["completed_id_count"] != operations
        or metrics["completed_id_min"] != 0
        or metrics["completed_id_max"] != operations - 1
        or metrics["completed_id_sum"] != operations * (operations - 1) // 2
        or metrics["missing_ids"]
        or metrics["duplicate_ids"]
    ):
        violations.append("exact_once_id_mismatch")
    pool = metrics["pool"]
    deltas = pool["deltas"]
    if (
        deltas["get_started"] != operations
        or deltas["get_timed_out"] != 0
        or deltas["get_direct"]
        + deltas["get_waited"]
        + deltas["get_timed_out"]
        != deltas["get_started"]
        or pool["peak_active_connections"] > pool_size
        or pool["final_active_connections"] != 0
    ):
        violations.append("pool_bound_mismatch")
    if (
        metrics["unique_sql_spid_count"] < 1
        or metrics["unique_sql_spid_count"] > pool_size
        or metrics["max_concurrent_sql_spids"] < 1
        or metrics["max_concurrent_sql_spids"] > pool_size
    ):
        violations.append("sql_session_bound_mismatch")
    rss = metrics["rss"]
    if (
        rss["growth_bytes"] > rss_growth_limit_bytes
        or rss["limit_bytes"] != rss_growth_limit_bytes
    ):
        violations.append("rss_growth_exceeded")
    if metrics["event_loop_ticker"]["count"] < 1:
        violations.append("event_loop_stalled")
    if not metrics["post_load_smoke"]:
        violations.append("post_load_smoke_failed")
    return violations


async def run_profile(
    connection: Connection,
    observer: Connection,
    profile: Profile,
    *,
    application_name: str,
    pool_size: int,
    buffer_size: int,
    rss_growth_limit_bytes: int,
) -> dict[str, object]:
    process = psutil.Process()
    queue: asyncio.Queue[WorkItem | None] = asyncio.Queue(
        maxsize=2 * profile.concurrency
    )
    concurrency_permit = asyncio.Semaphore(profile.concurrency)
    completed_ids: list[int] = []
    succeeded_ids: list[int] = []
    failed_ids: list[int] = []
    timed_out_ids: list[int] = []
    admitted_latencies_ns: list[int] = []
    scheduled_latencies_ns: list[int] = []
    sql_spids: set[int] = set()
    failure_types: Counter[str] = Counter()
    stop_monitors = asyncio.Event()
    monitor_state: dict[str, int | float] = {
        "sampler_iterations": 0,
        "peak_pending_gets": 0,
        "peak_active_connections": 0,
        "max_concurrent_sql_spids": 0,
        "peak_rss_bytes": 0,
        "ticker_count": 0,
        "max_ticker_gap_seconds": 0.0,
        "maximum_sampled_sql_cpu_time_ms": 0,
    }

    async def producer() -> None:
        for operation_id in range(profile.operations):
            item = WorkItem(operation_id)
            await queue.put(item)
            item.scheduled_ns = time.perf_counter_ns()
            item.ready.set()
        for _ in range(profile.concurrency):
            await queue.put(None)

    async def execute_item(item: WorkItem) -> None:
        await item.ready.wait()
        async with concurrency_permit:
            admitted_ns = time.perf_counter_ns()
            outcome = "succeeded"
            try:
                response = await connection.stream(
                    """
                    SELECT
                        @P1 AS operation_id,
                        @@SPID AS session_id
                    """,
                    [item.operation_id],
                    buffer_size=buffer_size,
                )
                row_count = 0
                async with response:
                    async for result_set in response:
                        async for row in result_set:
                            row_count += 1
                            if int(row["operation_id"]) != item.operation_id:
                                raise AssertionError(
                                    "streamed operation ID mismatch"
                                )
                            sql_spids.add(int(row["session_id"]))
                if row_count != 1:
                    raise AssertionError(
                        "stream operation did not return exactly one row"
                    )
                if not response.complete:
                    raise AssertionError("stream did not reach complete state")
                succeeded_ids.append(item.operation_id)
            except OperationTimeoutError:
                outcome = "timed_out"
                timed_out_ids.append(item.operation_id)
                failure_types["OperationTimeoutError"] += 1
            except Exception as error:
                outcome = "failed"
                failed_ids.append(item.operation_id)
                failure_types[type(error).__name__] += 1
            finally:
                completed_ns = time.perf_counter_ns()
                completed_ids.append(item.operation_id)
                admitted_latencies_ns.append(completed_ns - admitted_ns)
                scheduled_latencies_ns.append(
                    completed_ns - item.scheduled_ns
                )
                if outcome not in {"succeeded", "failed", "timed_out"}:
                    raise AssertionError("unknown stress operation outcome")

    async def worker() -> None:
        while True:
            item = await queue.get()
            try:
                if item is None:
                    return
                await execute_item(item)
            finally:
                queue.task_done()

    async def ticker() -> None:
        previous = time.perf_counter()
        while not stop_monitors.is_set():
            await asyncio.sleep(0.005)
            current = time.perf_counter()
            monitor_state["ticker_count"] = (
                int(monitor_state["ticker_count"]) + 1
            )
            monitor_state["max_ticker_gap_seconds"] = max(
                float(monitor_state["max_ticker_gap_seconds"]),
                current - previous,
            )
            previous = current

    async def sampler() -> None:
        while not stop_monitors.is_set():
            stats = await connection.pool_stats()
            session_count, cpu_time_ms = await session_metrics(
                observer,
                application_name,
            )
            monitor_state["sampler_iterations"] = (
                int(monitor_state["sampler_iterations"]) + 1
            )
            monitor_state["peak_pending_gets"] = max(
                int(monitor_state["peak_pending_gets"]),
                int(stats["pending_gets"]),
            )
            monitor_state["peak_active_connections"] = max(
                int(monitor_state["peak_active_connections"]),
                int(stats["active_connections"]),
            )
            monitor_state["max_concurrent_sql_spids"] = max(
                int(monitor_state["max_concurrent_sql_spids"]),
                session_count,
            )
            monitor_state["peak_rss_bytes"] = max(
                int(monitor_state["peak_rss_bytes"]),
                process.memory_info().rss,
            )
            monitor_state["maximum_sampled_sql_cpu_time_ms"] = max(
                int(monitor_state["maximum_sampled_sql_cpu_time_ms"]),
                cpu_time_ms,
            )
            await asyncio.sleep(0.005)

    ticker_task: asyncio.Task[None] | None = None
    sampler_task: asyncio.Task[None] | None = None
    try:
        await connection.connect()
        before_pool = await connection.pool_stats()
        _, sql_cpu_before = await session_metrics(
            observer,
            application_name,
        )
        gc.collect()
        rss_baseline = process.memory_info().rss
        monitor_state["peak_rss_bytes"] = rss_baseline
        cpu_before = process.cpu_times()
        ticker_task = asyncio.create_task(ticker())
        sampler_task = asyncio.create_task(sampler())

        started_ns = time.perf_counter_ns()
        async with asyncio.TaskGroup() as group:
            group.create_task(producer())
            for _ in range(profile.concurrency):
                group.create_task(worker())
        await queue.join()
        finished_ns = time.perf_counter_ns()
    finally:
        stop_monitors.set()
        if ticker_task is not None:
            await ticker_task
        if sampler_task is not None:
            await sampler_task

    wall_duration_seconds = (finished_ns - started_ns) / 1_000_000_000
    after_pool = await connection.pool_stats()
    _, sql_cpu_after = await session_metrics(observer, application_name)
    post_load_smoke = await scalar(connection, "SELECT 1") == 1
    cpu_after = process.cpu_times()
    gc.collect()
    rss_final = process.memory_info().rss
    rss_peak = max(int(monitor_state["peak_rss_bytes"]), rss_final)
    rss_growth = max(0, rss_peak - rss_baseline)
    counts = Counter(completed_ids)
    missing_ids = sorted(set(range(profile.operations)) - set(counts))
    duplicate_ids = sorted(
        operation_id
        for operation_id, count in counts.items()
        if count != 1
    )
    pool_deltas = {
        key: after_pool[key] - before_pool[key]
        for key in POOL_DELTA_KEYS
    }
    metrics: dict[str, Any] = {
        "status": "running",
        "operations": profile.operations,
        "concurrency": profile.concurrency,
        "application_name": application_name,
        "worker_count": profile.concurrency,
        "queue_maxsize": 2 * profile.concurrency,
        "total": len(completed_ids),
        "succeeded": len(succeeded_ids),
        "failed": len(failed_ids),
        "timed_out": len(timed_out_ids),
        "completed_id_count": len(completed_ids),
        "completed_id_min": min(completed_ids),
        "completed_id_max": max(completed_ids),
        "completed_id_sum": sum(completed_ids),
        "completed_ids_sha256": id_digest(completed_ids),
        "missing_ids": missing_ids,
        "duplicate_ids": duplicate_ids,
        "failure_types": dict(sorted(failure_types.items())),
        "wall_duration_seconds": wall_duration_seconds,
        "operations_per_second": (
            profile.operations / wall_duration_seconds
        ),
        "admitted_driver_latency_ns": latency_summary(
            admitted_latencies_ns
        ),
        "scheduled_end_to_end_latency_ns": latency_summary(
            scheduled_latencies_ns
        ),
        "pool": {
            "deltas": pool_deltas,
            "peak_pending_gets": int(
                monitor_state["peak_pending_gets"]
            ),
            "peak_active_connections": int(
                monitor_state["peak_active_connections"]
            ),
            "final_active_connections": int(
                after_pool["active_connections"]
            ),
        },
        "unique_sql_spids": sorted(sql_spids),
        "unique_sql_spid_count": len(sql_spids),
        "max_concurrent_sql_spids": int(
            monitor_state["max_concurrent_sql_spids"]
        ),
        "rss": {
            "baseline_bytes": rss_baseline,
            "peak_bytes": rss_peak,
            "final_bytes": rss_final,
            "growth_bytes": rss_growth,
            "limit_bytes": rss_growth_limit_bytes,
        },
        "python_process_cpu_seconds": (
            cpu_after.user
            + cpu_after.system
            - cpu_before.user
            - cpu_before.system
        ),
        "sql_session_cpu_time_ms_delta": max(
            0,
            max(
                sql_cpu_after,
                int(
                    monitor_state[
                        "maximum_sampled_sql_cpu_time_ms"
                    ]
                ),
            )
            - sql_cpu_before,
        ),
        "event_loop_ticker": {
            "count": int(monitor_state["ticker_count"]),
            "max_scheduling_gap_seconds": float(
                monitor_state["max_ticker_gap_seconds"]
            ),
        },
        "sampler_iterations": int(
            monitor_state["sampler_iterations"]
        ),
        "post_load_smoke": post_load_smoke,
    }
    violations = _profile_violations(
        metrics,
        pool_size=pool_size,
        rss_growth_limit_bytes=rss_growth_limit_bytes,
    )
    metrics["violations"] = violations
    metrics["status"] = "passed" if not violations else "failed"
    return metrics


def required_gate(args: argparse.Namespace) -> bool:
    return (
        args.profiles == (Profile(1_000, 64),)
        and args.pool_size == DEFAULT_POOL_SIZE
        and args.buffer_size == DEFAULT_BUFFER_SIZE
        and args.rss_growth_limit_bytes
        == DEFAULT_RSS_GROWTH_LIMIT_BYTES
    )


def matrix_result(
    *,
    source_sha: str,
    outcome: str,
    duration_seconds: float,
    metrics_path: Path,
    failure_type: str | None = None,
) -> dict[str, object]:
    message = f"metrics={metrics_path.name}"
    if failure_type:
        message = (
            f"result-stream stress gate failed ({failure_type}); {message}"
        )
    return {
        "schema_version": 1,
        "source_sha": source_sha,
        "cases": {
            "RESULT-029": {
                "outcome": outcome,
                "nodeid": (
                    "external::result_stream_stress[1000:64]"
                ),
                "duration_seconds": duration_seconds,
                "message": message,
            }
        },
    }


async def run_gate(args: argparse.Namespace) -> int:
    source_sha = git_head()
    is_required = required_gate(args)
    evidence: dict[str, object] = {
        "schema_version": 1,
        "source_sha": source_sha,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "runtime": runtime_metadata(),
        "configuration": {
            "profiles": [
                {
                    "operations": profile.operations,
                    "concurrency": profile.concurrency,
                }
                for profile in args.profiles
            ],
            "pool_size": args.pool_size,
            "buffer_size": args.buffer_size,
            "rss_growth_limit_bytes": args.rss_growth_limit_bytes,
            "queue_maxsize_factor": 2,
            "percentile_method": "nearest_rank",
            "worker_model": "long_lived",
        },
        "profiles": [],
    }
    atomic_write(args.metrics_output, evidence)
    started = time.perf_counter()
    failure_type: str | None = None

    try:
        _require_result_stream_api()
        settings = SqlAuthSettings.from_env()
        observer = settings.connection(
            application_name=(
                f"fastmssql_result_observer_{uuid4().hex[:12]}"
            ),
            pool_size=1,
        )
        try:
            await observer.connect()
            profiles: list[dict[str, object]] = []
            for profile in args.profiles:
                application_name = (
                    f"fastmssql_result_stress_{uuid4().hex[:12]}"
                )
                candidate = settings.connection(
                    application_name=application_name,
                    pool_size=args.pool_size,
                )
                print(
                    f"[result-stream-stress] starting "
                    f"{profile.operations:,} operations at concurrency "
                    f"{profile.concurrency}",
                    flush=True,
                )
                try:
                    metrics = await run_profile(
                        candidate,
                        observer,
                        profile,
                        application_name=application_name,
                        pool_size=args.pool_size,
                        buffer_size=args.buffer_size,
                        rss_growth_limit_bytes=(
                            args.rss_growth_limit_bytes
                        ),
                    )
                finally:
                    await candidate.disconnect()
                profiles.append(metrics)
                evidence["profiles"] = profiles
                atomic_write(args.metrics_output, evidence)
                print(
                    f"[result-stream-stress] {metrics['status']} "
                    f"{profile.operations:,}: "
                    f"{metrics['operations_per_second']:.2f} ops/s",
                    flush=True,
                )
        finally:
            await observer.disconnect()
        passed = all(
            profile["status"] == "passed"
            for profile in evidence["profiles"]
        )
        evidence["status"] = "passed" if passed else "failed"
        if not passed:
            failure_type = "InvariantViolation"
    except Exception as error:
        failure_type = type(error).__name__
        evidence["status"] = "failed"
        evidence["failure"] = {
            "reason": "result_stream_stress_failed",
            "type": failure_type,
        }

    atomic_write(args.metrics_output, evidence)
    duration = (
        sum(
            float(profile["wall_duration_seconds"])
            for profile in evidence["profiles"]
        )
        if evidence["profiles"]
        else time.perf_counter() - started
    )
    if is_required:
        atomic_write(
            args.results_output,
            matrix_result(
                source_sha=source_sha,
                outcome=(
                    "passed"
                    if evidence["status"] == "passed"
                    else "failed"
                ),
                duration_seconds=duration,
                metrics_path=args.metrics_output,
                failure_type=failure_type,
            ),
        )
    if evidence["status"] != "passed":
        print(
            "[result-stream-stress] failed; "
            f"type={failure_type or 'InvariantViolation'}; "
            f"evidence={args.metrics_output.name}",
            file=sys.stderr,
        )
    return 0 if evidence["status"] == "passed" else 1


def main() -> int:
    return asyncio.run(run_gate(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
