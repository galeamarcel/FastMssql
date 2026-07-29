#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import gc
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
MAX_PARAMETER_SETS = 99_999
MAX_CHUNK_SIZE = 10_000
EXTENDED_PARAMETER_SETS = 99_999
PARTIAL_PARAMETER_SETS = 10_000
DEFAULT_PROFILES = "1_000:100,10_000:1_000"
DEFAULT_MODES = "sync,async"
DEFAULT_RSS_GROWTH_LIMIT_BYTES = 67_108_864
DEFAULT_EVENT_LOOP_GAP_LIMIT_SECONDS = 0.100
DEFAULT_OPERATION_TIMEOUT_SECONDS = 300.0
DEFAULT_PAYLOAD_BYTES = 128
SAMPLE_INTERVAL_SECONDS = 0.005
SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class Profile:
    sets: int
    chunk_size: int


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
                max_size=1,
                min_idle=1,
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


class BufferTracker:
    def __init__(self) -> None:
        self.current_sets = 0
        self.maximum_sets = 0
        self.current_cells = 0
        self.maximum_cells = 0

    def retain(self, cells: int) -> None:
        self.current_sets += 1
        self.current_cells += cells
        self.maximum_sets = max(self.maximum_sets, self.current_sets)
        self.maximum_cells = max(self.maximum_cells, self.current_cells)

    def release(self, cells: int) -> None:
        self.current_sets -= 1
        self.current_cells -= cells
        if self.current_sets < 0 or self.current_cells < 0:
            raise RuntimeError("execute-many buffer tracker underflow")


class TrackedParameterSet(list[object]):
    def __init__(self, values: list[object], tracker: BufferTracker) -> None:
        super().__init__(values)
        self._tracker = tracker
        self._cells = len(values)
        tracker.retain(self._cells)

    def __del__(self) -> None:
        self._tracker.release(self._cells)


class SyncProducer:
    def __init__(
        self,
        parameter_set_count: int,
        payload: str,
        tracker: BufferTracker,
    ) -> None:
        self.parameter_set_count = parameter_set_count
        self.payload = payload
        self.tracker = tracker
        self.index = 0
        self.pulls = 0
        self.iter_calls = 0
        self.close_calls = 0

    def __iter__(self) -> SyncProducer:
        self.iter_calls += 1
        return self

    def __next__(self) -> TrackedParameterSet:
        if self.index >= self.parameter_set_count:
            raise StopIteration
        set_id = self.index
        self.index += 1
        self.pulls += 1
        return TrackedParameterSet(
            [set_id, f"{self.payload}{set_id % 10}"],
            self.tracker,
        )

    def close(self) -> None:
        self.close_calls += 1


class AsyncProducer:
    def __init__(
        self,
        parameter_set_count: int,
        payload: str,
        tracker: BufferTracker,
    ) -> None:
        self.parameter_set_count = parameter_set_count
        self.payload = payload
        self.tracker = tracker
        self.index = 0
        self.pulls = 0
        self.aiter_calls = 0
        self.aclose_calls = 0

    def __aiter__(self) -> AsyncProducer:
        self.aiter_calls += 1
        return self

    async def __anext__(self) -> TrackedParameterSet:
        if self.index >= self.parameter_set_count:
            raise StopAsyncIteration
        set_id = self.index
        self.index += 1
        self.pulls += 1
        return TrackedParameterSet(
            [set_id, f"{self.payload}{set_id % 10}"],
            self.tracker,
        )

    async def aclose(self) -> None:
        self.aclose_calls += 1


def parse_profiles(value: str) -> tuple[Profile, ...]:
    profiles: list[Profile] = []
    seen: set[tuple[int, int]] = set()
    for raw_profile in value.split(","):
        parts = raw_profile.split(":")
        if len(parts) != 2:
            raise argparse.ArgumentTypeError(
                "profiles must use parameter_sets:chunk_size"
            )
        try:
            parameter_sets = int(parts[0].replace("_", ""))
            chunk_size = int(parts[1].replace("_", ""))
        except ValueError as error:
            raise argparse.ArgumentTypeError(
                "profile counts must be integers"
            ) from error
        if not 1 <= parameter_sets <= MAX_PARAMETER_SETS:
            raise argparse.ArgumentTypeError(
                f"parameter sets must be in 1..{MAX_PARAMETER_SETS}"
            )
        if not 1 <= chunk_size <= MAX_CHUNK_SIZE:
            raise argparse.ArgumentTypeError(
                f"chunk size must be in 1..{MAX_CHUNK_SIZE}"
            )
        key = (parameter_sets, chunk_size)
        if key in seen:
            raise argparse.ArgumentTypeError("profiles must be unique")
        seen.add(key)
        profiles.append(Profile(parameter_sets, chunk_size))
    if not profiles:
        raise argparse.ArgumentTypeError("at least one profile is required")
    return tuple(profiles)


def parse_modes(value: str) -> tuple[str, ...]:
    modes = tuple(item.strip() for item in value.split(",") if item.strip())
    if (
        not modes
        or len(set(modes)) != len(modes)
        or any(mode not in {"sync", "async"} for mode in modes)
    ):
        raise argparse.ArgumentTypeError(
            "modes must be unique values from sync,async"
        )
    return modes


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bounded real-SQL execute_many driver stress"
    )
    parser.add_argument(
        "--profiles",
        type=parse_profiles,
        default=parse_profiles(DEFAULT_PROFILES),
    )
    parser.add_argument(
        "--modes",
        type=parse_modes,
        default=parse_modes(DEFAULT_MODES),
    )
    parser.add_argument("--include-partial-profile", action="store_true")
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
    parser.add_argument(
        "--payload-bytes",
        type=int,
        default=DEFAULT_PAYLOAD_BYTES,
    )
    args = parser.parse_args(argv)
    if any(
        profile.sets >= EXTENDED_PARAMETER_SETS for profile in args.profiles
    ) and not args.allow_extended:
        parser.error("99,999-set profiles require --allow-extended")
    if args.include_partial_profile and not any(
        profile.sets == PARTIAL_PARAMETER_SETS for profile in args.profiles
    ):
        parser.error(
            "--include-partial-profile requires a 10,000-set profile"
        )
    if args.rss_growth_limit_bytes <= 0:
        parser.error("--rss-growth-limit-bytes must be positive")
    if args.event_loop_gap_limit_seconds <= 0.0:
        parser.error("--event-loop-gap-limit-seconds must be positive")
    if args.operation_timeout_seconds <= 0.0:
        parser.error("--operation-timeout-seconds must be positive")
    if not 1 <= args.payload_bytes <= 1024:
        parser.error("--payload-bytes must be in 1..1024")
    metrics_output = args.metrics_output.resolve()
    if metrics_output == ROOT or ROOT in metrics_output.parents:
        parser.error("--metrics-output must be outside the repository")
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


def quote_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,120}", value):
        raise ValueError("unsafe generated SQL identifier")
    return f"[{value}]"


async def scalar(
    connection: Connection,
    sql: str,
    params: list[object] | None = None,
) -> object:
    row = (await connection.query(sql, params)).fetchone()
    if row is None or len(row) != 1:
        raise AssertionError("expected one scalar row")
    return row[0]


async def physical_identity(connection: Connection) -> tuple[int, str]:
    row = (
        await connection.query(
            """
            SELECT
                @@SPID,
                CONVERT(NVARCHAR(36), connection_id)
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """
        )
    ).fetchone()
    if row is None:
        raise AssertionError("missing physical identity")
    return int(row[0]), str(row[1])


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
    timeout: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await application_session_count(observer, application_name) == 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("candidate SQL sessions did not reach zero")


def expected_summary(parameter_set_count: int) -> dict[str, int]:
    return {
        "count": parameter_set_count,
        "minimum": 0,
        "maximum": parameter_set_count - 1,
        "sum": parameter_set_count * (parameter_set_count - 1) // 2,
    }


async def persisted_summary(
    connection: Connection,
    table: str,
) -> dict[str, int | None]:
    row = (
        await connection.query(
            f"""
            SELECT
                COUNT_BIG(*),
                MIN(id),
                MAX(id),
                SUM(CONVERT(BIGINT, id))
            FROM {table}
            """
        )
    ).fetchone()
    if row is None:
        raise AssertionError("missing persistence summary")
    return {
        "count": int(row[0]),
        "minimum": None if row[1] is None else int(row[1]),
        "maximum": None if row[2] is None else int(row[2]),
        "sum": None if row[3] is None else int(row[3]),
    }


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


def profile_violations(
    metrics: dict[str, Any],
    *,
    rss_growth_limit_bytes: int,
    event_loop_gap_limit_seconds: float,
) -> list[str]:
    violations: list[str] = []
    expected = expected_summary(metrics["parameter_set_count"])
    if metrics["producer_pulls"] != metrics["parameter_set_count"]:
        violations.append("producer_pull_mismatch")
    if metrics["executed_parameter_sets"] != metrics["parameter_set_count"]:
        violations.append("executed_set_mismatch")
    if metrics["affected_rows"] != metrics["parameter_set_count"]:
        violations.append("affected_count_mismatch")
    if metrics["persisted"] != expected:
        violations.append("persistence_mismatch")
    if metrics["maximum_buffered_sets"] > metrics["chunk_size"]:
        violations.append("buffer_bound_exceeded")
    if metrics["maximum_buffered_cells"] > metrics["chunk_size"] * 2:
        violations.append("buffer_cell_bound_exceeded")
    if metrics["buffered_sets_after_gc"] != 0:
        violations.append("retained_sets_after_gc")
    if metrics["buffered_cells_after_gc"] != 0:
        violations.append("retained_cells_after_gc")
    if metrics["rss_growth_bytes"] > rss_growth_limit_bytes:
        violations.append("rss_growth_exceeded")
    if metrics["maximum_event_loop_gap_seconds"] > (
        event_loop_gap_limit_seconds
    ):
        violations.append("event_loop_gap_exceeded")
    if metrics["maximum_sql_sessions"] > 1:
        violations.append("session_bound_exceeded")
    if not metrics["physical_identity_stable"]:
        violations.append("physical_identity_changed")
    expected_confirmed = (
        0 if metrics["atomic"] else metrics["parameter_set_count"]
    )
    if metrics["confirmed_committed_parameter_sets"] != expected_confirmed:
        violations.append("confirmed_commit_mismatch")
    operation = metrics["operation_metric_delta"]
    if not (
        operation["started"] == 1
        and operation["completed"] == 1
        and operation["succeeded"] == 1
        and all(
            operation[key] == 0
            for key in (
                "errors",
                "timed_out",
                "cancelled",
                "outcome_unknown",
            )
        )
    ):
        violations.append("execute_many_metric_mismatch")
    execute = metrics["execute_metric_delta"]
    if execute["started"] != 0 or execute["completed"] != 0:
        violations.append("execute_metric_inflation")
    if metrics["errors"] or metrics["timed_out"]:
        violations.append("operation_failure")
    pool = metrics["pool"]
    if (
        pool["max_size"] != 1
        or pool["connections"] > 1
        or pool["active_connections"] != 0
    ):
        violations.append("pool_size_mismatch")
    if not metrics["post_load_smoke"]:
        violations.append("post_load_smoke_failed")
    if metrics["teardown_sessions"] != 0:
        violations.append("teardown_session_leak")
    return violations


async def run_profile(
    settings: SqlAuthSettings,
    observer: Connection,
    profile: Profile,
    mode: str,
    *,
    atomic: bool,
    rss_growth_limit_bytes: int,
    event_loop_gap_limit_seconds: float,
    operation_timeout_seconds: float,
    payload_bytes: int,
) -> dict[str, object]:
    suffix = uuid4().hex[:12]
    raw_table = f"execute_many_{suffix}"
    table = quote_identifier(raw_table)
    application_name = f"fastmssql_execute_many_{suffix}"
    connection = settings.connection(
        application_name=application_name,
        operation_timeout_seconds=operation_timeout_seconds,
        metrics=True,
    )
    process = psutil.Process()
    tracker = BufferTracker()
    payload = "x" * max(1, payload_bytes - 1)
    producer: SyncProducer | AsyncProducer
    if mode == "sync":
        producer = SyncProducer(profile.sets, payload, tracker)
    elif mode == "async":
        producer = AsyncProducer(profile.sets, payload, tracker)
    else:
        raise ValueError("mode must be sync or async")

    errors: list[str] = []
    timed_out = 0
    affected_rows: int | None = None
    persisted: dict[str, int | None] | None = None
    identity_before: tuple[int, str] | None = None
    identity_after: tuple[int, str] | None = None
    operation_metric_delta: dict[str, int] | None = None
    execute_metric_delta: dict[str, int] | None = None
    pool: dict[str, Any] = {"max_size": 1}
    post_load_smoke = False
    elapsed_seconds: float | None = None
    teardown_sessions: int | None = None
    monitor_stop = asyncio.Event()
    maximum_sql_sessions = 0
    event_loop_ticks = 0
    maximum_event_loop_gap = 0.0
    rss_baseline = 0
    rss_peak = 0
    ticker_task: asyncio.Task[None] | None = None
    sampler_task: asyncio.Task[None] | None = None

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
        nonlocal maximum_sql_sessions, rss_peak
        while not monitor_stop.is_set():
            maximum_sql_sessions = max(
                maximum_sql_sessions,
                await application_session_count(observer, application_name),
            )
            rss_peak = max(rss_peak, process.memory_info().rss)
            await asyncio.sleep(SAMPLE_INTERVAL_SECONDS)

    try:
        await connection.connect()
        await connection.execute(
            f"""
            CREATE TABLE {table} (
                id BIGINT NOT NULL PRIMARY KEY,
                payload NVARCHAR(1024) NOT NULL
            )
            """
        )
        identity_before = await physical_identity(connection)
        before = await connection.operation_stats()
        before_operation = before["operations"].get(
            "execute_many",
            zero_metric(),
        )
        before_execute = before["operations"]["execute"]
        gc.collect()
        rss_baseline = process.memory_info().rss
        rss_peak = rss_baseline
        maximum_sql_sessions = await application_session_count(
            observer,
            application_name,
        )
        ticker_task = asyncio.create_task(ticker())
        sampler_task = asyncio.create_task(sampler())
        await asyncio.sleep(SAMPLE_INTERVAL_SECONDS * 2)

        started = time.perf_counter()
        affected_rows = int(
            await connection.execute_many(
                f"INSERT INTO {table} VALUES (@P1, @P2)",
                producer,
                atomic=atomic,
                chunk_size=profile.chunk_size,
            )
        )
        elapsed_seconds = time.perf_counter() - started
        identity_after = await physical_identity(connection)
        persisted = await persisted_summary(connection, table)
        post_load_smoke = int(await scalar(connection, "SELECT 1")) == 1
        after = await connection.operation_stats()
        operation_metric_delta = metric_delta(
            before_operation,
            after["operations"]["execute_many"],
        )
        execute_metric_delta = metric_delta(
            before_execute,
            after["operations"]["execute"],
        )
        pool = await connection.pool_stats()
    except OperationTimeoutError:
        timed_out += 1
        errors.append("OperationTimeoutError")
    except Exception as error:
        errors.append(type(error).__name__)
    finally:
        monitor_stop.set()
        monitor_results = await asyncio.gather(
            *(
                task
                for task in (ticker_task, sampler_task)
                if task is not None
            ),
            return_exceptions=True,
        )
        for result in monitor_results:
            if isinstance(result, BaseException):
                errors.append(type(result).__name__)
        gc.collect()
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
        try:
            await observer.execute(f"DROP TABLE IF EXISTS {table}")
        except Exception as error:
            errors.append(type(error).__name__)

    if operation_metric_delta is None:
        operation_metric_delta = zero_metric()
    if execute_metric_delta is None:
        execute_metric_delta = zero_metric()
    metrics: dict[str, Any] = {
        "input_model": f"{mode}_iterable",
        "parameter_set_count": profile.sets,
        "chunk_size": profile.chunk_size,
        "atomic": atomic,
        "affected_rows": affected_rows,
        "persisted": persisted,
        "producer_pulls": producer.pulls,
        "executed_parameter_sets": affected_rows,
        "confirmed_committed_parameter_sets": (
            0 if atomic or affected_rows is None else affected_rows
        ),
        "maximum_buffered_sets": tracker.maximum_sets,
        "maximum_buffered_cells": tracker.maximum_cells,
        "buffered_sets_after_gc": tracker.current_sets,
        "buffered_cells_after_gc": tracker.current_cells,
        "rss_baseline_bytes": rss_baseline,
        "rss_peak_bytes": rss_peak,
        "rss_growth_bytes": max(0, rss_peak - rss_baseline),
        "rss_growth_limit_bytes": rss_growth_limit_bytes,
        "event_loop_ticks": event_loop_ticks,
        "maximum_event_loop_gap_seconds": maximum_event_loop_gap,
        "event_loop_gap_limit_seconds": event_loop_gap_limit_seconds,
        "maximum_sql_sessions": maximum_sql_sessions,
        "physical_identity_before": identity_before,
        "physical_identity_after": identity_after,
        "physical_identity_stable": (
            identity_before is not None and identity_before == identity_after
        ),
        "operation_metric_delta": operation_metric_delta,
        "execute_metric_delta": execute_metric_delta,
        "pool": {
            "max_size": int(pool.get("max_size", 1)),
            "connections": int(pool.get("connections", 0)),
            "active_connections": int(pool.get("active_connections", 0)),
        },
        "elapsed_seconds": elapsed_seconds,
        "throughput_parameter_sets_per_second": (
            None
            if elapsed_seconds is None or elapsed_seconds <= 0.0
            else profile.sets / elapsed_seconds
        ),
        "errors": errors,
        "timed_out": timed_out,
        "post_load_smoke": post_load_smoke,
        "teardown_sessions": teardown_sessions,
    }
    metrics["violations"] = profile_violations(
        metrics,
        rss_growth_limit_bytes=rss_growth_limit_bytes,
        event_loop_gap_limit_seconds=event_loop_gap_limit_seconds,
    )
    metrics["status"] = "passed" if not metrics["violations"] else "failed"
    return metrics


async def run(args: argparse.Namespace) -> dict[str, object]:
    settings = SqlAuthSettings.from_env()
    observer = settings.connection(
        application_name=f"fastmssql_emany_observer_{uuid4().hex[:12]}",
        operation_timeout_seconds=args.operation_timeout_seconds,
        observer=True,
    )
    profiles: list[dict[str, object]] = []
    async with observer:
        for profile in args.profiles:
            for mode in args.modes:
                profiles.append(
                    await run_profile(
                        settings,
                        observer,
                        profile,
                        mode,
                        atomic=True,
                        rss_growth_limit_bytes=args.rss_growth_limit_bytes,
                        event_loop_gap_limit_seconds=(
                            args.event_loop_gap_limit_seconds
                        ),
                        operation_timeout_seconds=args.operation_timeout_seconds,
                        payload_bytes=args.payload_bytes,
                    )
                )
            if (
                args.include_partial_profile
                and profile.sets == PARTIAL_PARAMETER_SETS
            ):
                profiles.append(
                    await run_profile(
                        settings,
                        observer,
                        profile,
                        "sync",
                        atomic=False,
                        rss_growth_limit_bytes=args.rss_growth_limit_bytes,
                        event_loop_gap_limit_seconds=(
                            args.event_loop_gap_limit_seconds
                        ),
                        operation_timeout_seconds=args.operation_timeout_seconds,
                        payload_bytes=args.payload_bytes,
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
            "maximum_parameter_sets": MAX_PARAMETER_SETS,
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
