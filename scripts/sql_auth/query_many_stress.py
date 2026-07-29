#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import time
from typing import Any
from uuid import uuid4

import fastmssql
from fastmssql import (
    Connection,
    OperationMetricsConfig,
    OperationTimeoutError,
    PoolConfig,
    SslConfig,
    TimeoutConfig,
)
import psutil


ROOT = Path(__file__).resolve().parents[2]
MAX_OPERATIONS = 99_999
MAX_REQUESTED_CONCURRENCY = 10_000
EXTENDED_OPERATIONS = 99_999
DEFAULT_PROFILES = (
    "1_000:sync:true:10:10,"
    "1_000:async:false:25:8,"
    "10_000:sync:false:50:16,"
    "10_000:async:true:100:16"
)
DEFAULT_RSS_GROWTH_LIMIT_BYTES = 134_217_728
DEFAULT_EVENT_LOOP_GAP_LIMIT_SECONDS = 0.100
DEFAULT_OPERATION_TIMEOUT_SECONDS = 300.0
SAMPLE_INTERVAL_SECONDS = 0.005
LATENCY_SAMPLE_CAPACITY = 2_048
SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class Profile:
    operations: int
    source: str
    ordered: bool
    requested_concurrency: int
    pool_max: int


@dataclass(frozen=True, repr=False)
class SqlAuthSettings:
    host: str
    port: int
    database: str
    owner_username: str
    owner_password: str
    observer_username: str
    observer_password: str

    @classmethod
    def from_env(cls) -> SqlAuthSettings:
        owner_password = os.getenv("FASTMSSQL_SQL_AUTH_OWNER_PASSWORD", "")
        observer_password = os.getenv("FASTMSSQL_SQL_AUTH_SA_PASSWORD", "")
        missing = [
            name
            for name, value in (
                ("FASTMSSQL_SQL_AUTH_OWNER_PASSWORD", owner_password),
                ("FASTMSSQL_SQL_AUTH_SA_PASSWORD", observer_password),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "missing required environment setting(s): " + ", ".join(missing)
            )
        return cls(
            host=os.getenv("FASTMSSQL_SQL_AUTH_HOST", "127.0.0.1"),
            port=int(os.getenv("FASTMSSQL_SQL_AUTH_PORT", "14334")),
            database=os.getenv(
                "FASTMSSQL_SQL_AUTH_DATABASE",
                "fastmssql_validation",
            ),
            owner_username=os.getenv(
                "FASTMSSQL_SQL_AUTH_OWNER_USER",
                "fastmssql_owner",
            ),
            owner_password=owner_password,
            observer_username=os.getenv("FASTMSSQL_SQL_AUTH_SA_USER", "sa"),
            observer_password=observer_password,
        )

    def connection(
        self,
        *,
        application_name: str,
        operation_timeout_seconds: float,
        max_size: int,
        observer: bool = False,
        metrics: bool = False,
    ) -> Connection:
        return Connection(
            server=self.host,
            port=self.port,
            database=self.database,
            username=(
                self.observer_username if observer else self.owner_username
            ),
            password=(
                self.observer_password if observer else self.owner_password
            ),
            application_name=application_name,
            ssl_config=SslConfig.development(),
            pool_config=PoolConfig(
                max_size=max_size,
                min_idle=0,
                max_lifetime_secs=None,
                idle_timeout_secs=None,
                connection_timeout_secs=10,
                test_on_check_out=False,
                retry_connection=False,
            ),
            timeout_config=TimeoutConfig(
                connect_timeout_secs=10.0,
                acquire_timeout_secs=10.0,
                operation_timeout_secs=operation_timeout_seconds,
                rollback_timeout_secs=10.0,
            ),
            operation_metrics_config=OperationMetricsConfig(enabled=metrics),
        )


class AdmissionTracker:
    def __init__(self) -> None:
        self.current = 0
        self.maximum = 0
        self._accepted_at: dict[int, float] = {}

    def accept(self, operation_id: int) -> None:
        if operation_id in self._accepted_at:
            raise RuntimeError("query-many admission identifier reused")
        self._accepted_at[operation_id] = time.perf_counter()
        self.current += 1
        self.maximum = max(self.maximum, self.current)

    def mark_yielded(self, operation_id: int | None = None) -> float | None:
        if self.current <= 0:
            raise RuntimeError("query-many admission tracker underflow")
        if operation_id is None:
            operation_id = next(iter(self._accepted_at))
        accepted_at = self._accepted_at.pop(operation_id, None)
        if accepted_at is None:
            raise RuntimeError("query-many yielded an unaccepted identifier")
        self.current -= 1
        return max(0.0, time.perf_counter() - accepted_at)

    def discard_all(self) -> None:
        self._accepted_at.clear()
        self.current = 0


class SyncProducer:
    def __init__(self, operations: int, tracker: AdmissionTracker) -> None:
        self.operations = operations
        self.tracker = tracker
        self.index = 0
        self.pulls = 0
        self.iter_calls = 0
        self.close_calls = 0

    def __iter__(self) -> SyncProducer:
        self.iter_calls += 1
        return self

    def __next__(self) -> list[object]:
        if self.index >= self.operations:
            raise StopIteration
        operation_id = self.index
        self.index += 1
        self.pulls += 1
        self.tracker.accept(operation_id)
        return [operation_id]

    def close(self) -> None:
        self.close_calls += 1


class AsyncProducer:
    def __init__(self, operations: int, tracker: AdmissionTracker) -> None:
        self.operations = operations
        self.tracker = tracker
        self.index = 0
        self.pulls = 0
        self.aiter_calls = 0
        self.aclose_calls = 0

    def __aiter__(self) -> AsyncProducer:
        self.aiter_calls += 1
        return self

    async def __anext__(self) -> list[object]:
        if self.index >= self.operations:
            raise StopAsyncIteration
        operation_id = self.index
        self.index += 1
        self.pulls += 1
        self.tracker.accept(operation_id)
        return [operation_id]

    async def aclose(self) -> None:
        self.aclose_calls += 1


class BoundedLatencySamples:
    def __init__(self, capacity: int = LATENCY_SAMPLE_CAPACITY) -> None:
        if capacity <= 0:
            raise ValueError("latency sample capacity must be positive")
        self.capacity = capacity
        self.count = 0
        self._values: list[float] = []

    def add(self, value: float) -> None:
        self.count += 1
        if len(self._values) < self.capacity:
            self._values.append(value)
            return
        self._values[(self.count - 1) % self.capacity] = value

    def percentiles_ms(self) -> dict[str, float | None]:
        if not self._values:
            return {"p50": None, "p95": None, "p99": None}
        ordered = sorted(self._values)

        def percentile(fraction: float) -> float:
            index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction))
            return ordered[index] * 1_000.0

        return {
            "p50": percentile(0.50),
            "p95": percentile(0.95),
            "p99": percentile(0.99),
        }


def parse_profiles(value: str) -> tuple[Profile, ...]:
    profiles: list[Profile] = []
    seen: set[tuple[int, str, bool, int, int]] = set()
    for raw_profile in value.split(","):
        parts = raw_profile.split(":")
        if len(parts) != 5:
            raise argparse.ArgumentTypeError(
                "profiles must use "
                "operations:source:ordered:requested_concurrency:pool_max"
            )
        try:
            operations = int(parts[0].replace("_", ""))
            requested_concurrency = int(parts[3].replace("_", ""))
            pool_max = int(parts[4].replace("_", ""))
        except ValueError as error:
            raise argparse.ArgumentTypeError(
                "profile counts must be integers"
            ) from error
        source = parts[1]
        ordered_raw = parts[2]
        if source not in {"sync", "async"}:
            raise argparse.ArgumentTypeError("source must be sync or async")
        if ordered_raw not in {"true", "false"}:
            raise argparse.ArgumentTypeError("ordered must be true or false")
        ordered = ordered_raw == "true"
        if not 1 <= operations <= MAX_OPERATIONS:
            raise argparse.ArgumentTypeError(
                f"operations must be in 1..{MAX_OPERATIONS}"
            )
        if not 1 <= requested_concurrency <= MAX_REQUESTED_CONCURRENCY:
            raise argparse.ArgumentTypeError(
                "requested concurrency must be in "
                f"1..{MAX_REQUESTED_CONCURRENCY}"
            )
        if pool_max < 1:
            raise argparse.ArgumentTypeError("pool max must be positive")
        key = (
            operations,
            source,
            ordered,
            requested_concurrency,
            pool_max,
        )
        if key in seen:
            raise argparse.ArgumentTypeError("profiles must be unique")
        seen.add(key)
        profiles.append(
            Profile(
                operations=operations,
                source=source,
                ordered=ordered,
                requested_concurrency=requested_concurrency,
                pool_max=pool_max,
            )
        )
    if not profiles:
        raise argparse.ArgumentTypeError("at least one profile is required")
    return tuple(profiles)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bounded real-SQL query_many driver stress"
    )
    parser.add_argument(
        "--profiles",
        type=parse_profiles,
        default=parse_profiles(DEFAULT_PROFILES),
    )
    parser.add_argument("--allow-extended", action="store_true")
    parser.add_argument("--metrics-output", type=Path, required=True)
    parser.add_argument(
        "--rss-growth-limit-bytes",
        type=int,
        default=DEFAULT_RSS_GROWTH_LIMIT_BYTES,
    )
    parser.add_argument(
        "--event-loop-gap-limit-seconds",
        type=float,
        default=DEFAULT_EVENT_LOOP_GAP_LIMIT_SECONDS,
    )
    parser.add_argument(
        "--operation-timeout-seconds",
        type=float,
        default=DEFAULT_OPERATION_TIMEOUT_SECONDS,
    )
    args = parser.parse_args(argv)
    if any(
        profile.operations >= EXTENDED_OPERATIONS for profile in args.profiles
    ) and not args.allow_extended:
        parser.error("99,999-operation profiles require --allow-extended")
    if args.rss_growth_limit_bytes <= 0:
        parser.error("--rss-growth-limit-bytes must be positive")
    if args.event_loop_gap_limit_seconds <= 0.0:
        parser.error("--event-loop-gap-limit-seconds must be positive")
    if args.operation_timeout_seconds <= 0.0:
        parser.error("--operation-timeout-seconds must be positive")
    metrics_output = args.metrics_output.resolve()
    artifacts = (ROOT / ".artifacts/sql-auth").resolve()
    if (
        metrics_output == ROOT
        or (
            ROOT in metrics_output.parents
            and metrics_output != artifacts
            and artifacts not in metrics_output.parents
        )
    ):
        parser.error(
            "--metrics-output inside the repository must be under "
            ".artifacts/sql-auth"
        )
    args.metrics_output = metrics_output
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
        raise RuntimeError("git HEAD is not a full SHA")
    return value


def worktree_is_dirty() -> bool:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(completed.stdout.strip())


def runtime_metadata() -> dict[str, object]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "fastmssql_version": fastmssql.version(),
    }


def atomic_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


async def scalar(
    connection: Connection,
    sql: str,
    params: list[object] | None = None,
) -> object:
    row = (await connection.query(sql, params)).fetchone()
    if row is None or len(row) != 1:
        raise AssertionError("expected one scalar row")
    return row[0]


async def application_session_count(
    observer: Connection,
    application_name: str,
) -> int:
    return int(
        await scalar(
            observer,
            """
            SELECT COUNT_BIG(*)
            FROM sys.dm_exec_sessions
            WHERE program_name = @P1
            """,
            [application_name],
        )
    )


async def wait_for_zero_sessions(
    observer: Connection,
    application_name: str,
    *,
    timeout: float = 10.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await application_session_count(observer, application_name) == 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("candidate SQL sessions did not reach zero")


def metric_delta(
    before: dict[str, int],
    after: dict[str, int],
) -> dict[str, int]:
    keys = (
        "started",
        "completed",
        "succeeded",
        "errors",
        "timed_out",
        "cancelled",
        "outcome_unknown",
    )
    return {key: int(after[key]) - int(before[key]) for key in keys}


def zero_metric() -> dict[str, int]:
    return {
        "started": 0,
        "completed": 0,
        "succeeded": 0,
        "errors": 0,
        "timed_out": 0,
        "cancelled": 0,
        "outcome_unknown": 0,
    }


async def settle_monitor_tasks(
    tasks: list[asyncio.Task[None]],
) -> list[str]:
    if not tasks:
        return []
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [
        type(result).__name__
        for result in results
        if isinstance(result, BaseException)
    ]


def _yield_integrity(metrics: dict[str, Any]) -> tuple[bool, bool]:
    operations = int(metrics["operations"])
    yielded_ids = metrics.get("yielded_ids")
    if yielded_ids is not None:
        identifiers = tuple(int(value) for value in yielded_ids)
        identity_ok = (
            len(identifiers) == operations
            and len(set(identifiers)) == operations
            and set(identifiers) == set(range(operations))
        )
        ordered_ok = identifiers == tuple(range(operations))
        return identity_ok, ordered_ok
    identity_ok = (
        int(metrics.get("yielded_count", -1)) == operations
        and int(metrics.get("missing_ids", -1)) == 0
        and int(metrics.get("duplicate_ids", -1)) == 0
    )
    ordered_ok = int(metrics.get("ordered_mismatches", -1)) == 0
    return identity_ok, ordered_ok


def profile_violations(
    metrics: dict[str, Any],
    *,
    rss_growth_limit_bytes: int,
    event_loop_gap_limit_seconds: float,
) -> list[str]:
    violations: list[str] = []
    identity_ok, ordered_ok = _yield_integrity(metrics)
    if not identity_ok:
        violations.append("missing_or_duplicate_ids")
    if metrics["ordered"] and not ordered_ok:
        violations.append("ordered_output_mismatch")
    if metrics["producer_pulls"] != metrics["operations"]:
        violations.append("producer_pull_mismatch")
    effective = int(metrics["effective_concurrency"])
    if metrics["maximum_accepted_window"] > effective:
        violations.append("accepted_window_exceeded")
    if metrics["maximum_active_queries"] > effective:
        violations.append("active_query_bound_exceeded")
    if (
        metrics["maximum_pool_connections"] > metrics["pool_max"]
        or metrics["maximum_sql_sessions"] > metrics["pool_max"]
    ):
        violations.append("pool_bound_exceeded")
    query = metrics["query_metric_delta"]
    operations = int(metrics["operations"])
    if not (
        query["started"] == operations
        and query["completed"] == operations
        and query["succeeded"] == operations
        and all(
            query[key] == 0
            for key in ("errors", "timed_out", "cancelled", "outcome_unknown")
        )
    ):
        violations.append("query_metric_mismatch")
    if "query_many" in metrics["operation_metric_names"]:
        violations.append("unexpected_query_many_metric")
    if metrics["operation_schema_version"] != 2:
        violations.append("schema_version_mismatch")
    if metrics["rss_growth_bytes"] > rss_growth_limit_bytes:
        violations.append("rss_growth_exceeded")
    if (
        metrics["maximum_event_loop_gap_seconds"]
        > event_loop_gap_limit_seconds
    ):
        violations.append("event_loop_gap_exceeded")
    if not metrics["post_load_smoke"]:
        violations.append("post_load_smoke_failed")
    if metrics["teardown_sessions"] != 0:
        violations.append("teardown_session_leak")
    if metrics["unhandled_task_exceptions"]:
        violations.append("unhandled_task_exception")
    if metrics.get("accepted_after_consume", 0) != 0:
        violations.append("accepted_window_not_released")
    if metrics.get("pool_active_after_consume", 0) != 0:
        violations.append("pool_not_idle")
    if metrics["errors"]:
        violations.append("operation_failure")
    return violations


async def run_profile(
    settings: SqlAuthSettings,
    observer: Connection,
    profile: Profile,
    *,
    rss_growth_limit_bytes: int,
    event_loop_gap_limit_seconds: float,
    operation_timeout_seconds: float,
) -> dict[str, object]:
    suffix = uuid4().hex[:12]
    application_name = f"fastmssql_query_many_{suffix}"
    connection = settings.connection(
        application_name=application_name,
        operation_timeout_seconds=operation_timeout_seconds,
        max_size=profile.pool_max,
        metrics=True,
    )
    tracker = AdmissionTracker()
    producer: SyncProducer | AsyncProducer
    if profile.source == "sync":
        producer = SyncProducer(profile.operations, tracker)
    else:
        producer = AsyncProducer(profile.operations, tracker)

    process = psutil.Process()
    seen = bytearray(profile.operations)
    yielded_count = 0
    duplicate_ids = 0
    ordered_mismatches = 0
    latency = BoundedLatencySamples()
    maximum_active_queries = 0
    maximum_pool_connections = 0
    maximum_sql_sessions = 0
    maximum_event_loop_gap = 0.0
    event_loop_ticks = 0
    rss_baseline = 0
    rss_peak = 0
    elapsed_seconds: float | None = None
    operation_schema_version: int | None = None
    operation_metric_names: list[str] = []
    query_metric_delta: dict[str, int] | None = None
    pool_active_after_consume: int | None = None
    post_load_smoke = False
    teardown_sessions: int | None = None
    errors: list[str] = []
    unhandled_task_exceptions: list[str] = []
    monitor_stop = asyncio.Event()
    monitor_tasks: list[asyncio.Task[None]] = []
    loop = asyncio.get_running_loop()
    previous_exception_handler = loop.get_exception_handler()

    def loop_exception_handler(
        active_loop: asyncio.AbstractEventLoop,
        context: dict[str, Any],
    ) -> None:
        exception = context.get("exception")
        unhandled_task_exceptions.append(
            type(exception).__name__
            if isinstance(exception, BaseException)
            else "UnhandledTask"
        )
        if previous_exception_handler is not None:
            previous_exception_handler(active_loop, context)

    async def ticker() -> None:
        nonlocal event_loop_ticks, maximum_event_loop_gap
        previous = time.perf_counter()
        while not monitor_stop.is_set():
            await asyncio.sleep(SAMPLE_INTERVAL_SECONDS)
            current = time.perf_counter()
            event_loop_ticks += 1
            maximum_event_loop_gap = max(
                maximum_event_loop_gap,
                current - previous,
            )
            previous = current

    async def sampler() -> None:
        nonlocal maximum_active_queries
        nonlocal maximum_pool_connections
        nonlocal maximum_sql_sessions
        nonlocal rss_peak
        while not monitor_stop.is_set():
            operation_stats = await connection.operation_stats()
            query = operation_stats["operations"]["query"]
            maximum_active_queries = max(
                maximum_active_queries,
                int(query["in_flight"]),
            )
            pool = await connection.pool_stats()
            maximum_pool_connections = max(
                maximum_pool_connections,
                int(pool["connections"]),
            )
            maximum_sql_sessions = max(
                maximum_sql_sessions,
                await application_session_count(observer, application_name),
            )
            rss_peak = max(rss_peak, process.memory_info().rss)
            await asyncio.sleep(SAMPLE_INTERVAL_SECONDS)

    loop.set_exception_handler(loop_exception_handler)
    before_query = zero_metric()
    try:
        await connection.connect()
        before = await connection.operation_stats()
        before_query = before["operations"]["query"]
        rss_baseline = process.memory_info().rss
        rss_peak = rss_baseline
        maximum_sql_sessions = await application_session_count(
            observer,
            application_name,
        )
        monitor_tasks = [
            asyncio.create_task(
                ticker(),
                name="fastmssql-query-many-stress-ticker",
            ),
            asyncio.create_task(
                sampler(),
                name="fastmssql-query-many-stress-sampler",
            ),
        ]
        await asyncio.sleep(SAMPLE_INTERVAL_SECONDS * 2)

        started = time.perf_counter()
        async with connection.query_many(
            "SELECT @P1 AS operation_id, @@SPID AS spid",
            producer,
            concurrency=profile.requested_concurrency,
            ordered=profile.ordered,
        ) as results:
            async for result in results:
                row = result.fetchone()
                if row is None:
                    raise AssertionError("query-many result row is missing")
                operation_id = int(row["operation_id"])
                if not 0 <= operation_id < profile.operations:
                    raise AssertionError(
                        "query-many result identifier is out of range"
                    )
                if seen[operation_id]:
                    duplicate_ids += 1
                else:
                    seen[operation_id] = 1
                if profile.ordered and operation_id != yielded_count:
                    ordered_mismatches += 1
                sample = tracker.mark_yielded(operation_id)
                if sample is not None:
                    latency.add(sample)
                yielded_count += 1
        elapsed_seconds = time.perf_counter() - started

        after = await connection.operation_stats()
        operation_schema_version = int(after["schema_version"])
        operation_metric_names = sorted(after["operations"])
        query_metric_delta = metric_delta(
            before_query,
            after["operations"]["query"],
        )
        pool = await connection.pool_stats()
        pool_active_after_consume = int(pool["active_connections"])
        maximum_pool_connections = max(
            maximum_pool_connections,
            int(pool["connections"]),
        )
        post_load_smoke = int(await scalar(connection, "SELECT 1")) == 1
    except OperationTimeoutError:
        errors.append("OperationTimeoutError")
    except Exception as error:
        errors.append(type(error).__name__)
    finally:
        monitor_stop.set()
        unhandled_task_exceptions.extend(
            await settle_monitor_tasks(monitor_tasks)
        )
        rss_peak = max(rss_peak, process.memory_info().rss)
        try:
            await connection.disconnect()
        except Exception as error:
            errors.append(type(error).__name__)
        try:
            await wait_for_zero_sessions(observer, application_name)
            teardown_sessions = await application_session_count(
                observer,
                application_name,
            )
        except Exception as error:
            errors.append(type(error).__name__)
        loop.set_exception_handler(previous_exception_handler)

    if query_metric_delta is None:
        query_metric_delta = zero_metric()
    missing_ids = profile.operations - int(sum(seen))
    effective_concurrency = min(
        profile.requested_concurrency,
        profile.pool_max,
    )
    metrics: dict[str, Any] = {
        "operations": profile.operations,
        "source": profile.source,
        "ordered": profile.ordered,
        "requested_concurrency": profile.requested_concurrency,
        "pool_max": profile.pool_max,
        "effective_concurrency": effective_concurrency,
        "producer_pulls": producer.pulls,
        "yielded_count": yielded_count,
        "missing_ids": missing_ids,
        "duplicate_ids": duplicate_ids,
        "ordered_mismatches": ordered_mismatches,
        "maximum_accepted_window": tracker.maximum,
        "accepted_after_consume": tracker.current,
        "maximum_active_queries": maximum_active_queries,
        "maximum_pool_connections": maximum_pool_connections,
        "maximum_sql_sessions": maximum_sql_sessions,
        "operation_schema_version": operation_schema_version,
        "query_metric_delta": query_metric_delta,
        "operation_metric_names": operation_metric_names,
        "pool_active_after_consume": pool_active_after_consume,
        "rss_baseline_bytes": rss_baseline,
        "rss_peak_bytes": rss_peak,
        "rss_growth_bytes": max(0, rss_peak - rss_baseline),
        "rss_growth_limit_bytes": rss_growth_limit_bytes,
        "event_loop_ticks": event_loop_ticks,
        "maximum_event_loop_gap_seconds": maximum_event_loop_gap,
        "event_loop_gap_limit_seconds": event_loop_gap_limit_seconds,
        "elapsed_seconds": elapsed_seconds,
        "throughput_operations_per_second": (
            None
            if elapsed_seconds is None or elapsed_seconds <= 0.0
            else profile.operations / elapsed_seconds
        ),
        "completion_latency_ms": latency.percentiles_ms(),
        "post_load_smoke": post_load_smoke,
        "teardown_sessions": teardown_sessions,
        "unhandled_task_exceptions": unhandled_task_exceptions,
        "errors": errors,
    }
    metrics["violations"] = profile_violations(
        metrics,
        rss_growth_limit_bytes=rss_growth_limit_bytes,
        event_loop_gap_limit_seconds=event_loop_gap_limit_seconds,
    )
    metrics["status"] = "passed" if not metrics["violations"] else "failed"
    tracker.discard_all()
    return metrics


async def run(args: argparse.Namespace) -> dict[str, object]:
    settings = SqlAuthSettings.from_env()
    observer = settings.connection(
        application_name=f"fastmssql_qmany_observer_{uuid4().hex[:12]}",
        operation_timeout_seconds=args.operation_timeout_seconds,
        max_size=1,
        observer=True,
    )
    profiles: list[dict[str, object]] = []
    async with observer:
        for profile in args.profiles:
            profiles.append(
                await run_profile(
                    settings,
                    observer,
                    profile,
                    rss_growth_limit_bytes=args.rss_growth_limit_bytes,
                    event_loop_gap_limit_seconds=(
                        args.event_loop_gap_limit_seconds
                    ),
                    operation_timeout_seconds=args.operation_timeout_seconds,
                )
            )
    passed = all(profile["status"] == "passed" for profile in profiles)
    return {
        "schema_version": 1,
        "status": "passed" if passed else "failed",
        "source_sha": git_head(),
        "worktree_dirty": worktree_is_dirty(),
        "runtime": runtime_metadata(),
        "limits": {
            "maximum_operations": MAX_OPERATIONS,
            "maximum_requested_concurrency": MAX_REQUESTED_CONCURRENCY,
            "rss_growth_bytes": args.rss_growth_limit_bytes,
            "event_loop_gap_seconds": args.event_loop_gap_limit_seconds,
        },
        "profiles": profiles,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = asyncio.run(run(args))
    atomic_write(args.metrics_output, payload)
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
