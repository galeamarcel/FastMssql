#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import gc
import json
import math
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
MAX_ROWS = 99_999
MAX_CHUNK_SIZE = 10_000
EXTENDED_ROWS = 99_999
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
    rows: int
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
        self.current = 0
        self.maximum = 0

    def retain(self) -> None:
        self.current += 1
        self.maximum = max(self.maximum, self.current)

    def release(self) -> None:
        self.current -= 1
        if self.current < 0:
            raise RuntimeError("buffer tracker underflow")


class TrackedPayload(str):
    def __new__(
        cls,
        value: str,
        tracker: BufferTracker,
    ) -> TrackedPayload:
        instance = super().__new__(cls, value)
        instance._tracker = tracker
        tracker.retain()
        return instance

    def __del__(self) -> None:
        self._tracker.release()


class SyncProducer:
    def __init__(
        self,
        row_count: int,
        payload: str,
        tracker: BufferTracker,
    ) -> None:
        self.row_count = row_count
        self.payload = payload
        self.tracker = tracker
        self.index = 0
        self.pulls = 0
        self.iter_calls = 0
        self.closed = False

    def __iter__(self) -> SyncProducer:
        self.iter_calls += 1
        return self

    def __next__(self) -> list[object]:
        if self.index >= self.row_count:
            raise StopIteration
        row_id = self.index
        self.index += 1
        self.pulls += 1
        return [
            row_id,
            TrackedPayload(self.payload, self.tracker),
            row_id % 100,
        ]

    def close(self) -> None:
        self.closed = True


class AsyncProducer:
    def __init__(
        self,
        row_count: int,
        payload: str,
        tracker: BufferTracker,
    ) -> None:
        self.row_count = row_count
        self.payload = payload
        self.tracker = tracker
        self.index = 0
        self.pulls = 0
        self.aiter_calls = 0
        self.closed = False

    def __aiter__(self) -> AsyncProducer:
        self.aiter_calls += 1
        return self

    async def __anext__(self) -> list[object]:
        if self.index >= self.row_count:
            raise StopAsyncIteration
        row_id = self.index
        self.index += 1
        self.pulls += 1
        return [
            row_id,
            TrackedPayload(self.payload, self.tracker),
            row_id % 100,
        ]

    async def aclose(self) -> None:
        self.closed = True


def parse_profiles(value: str) -> tuple[Profile, ...]:
    profiles: list[Profile] = []
    seen: set[tuple[int, int]] = set()
    for raw_profile in value.split(","):
        parts = raw_profile.strip().split(":")
        if len(parts) != 2:
            raise argparse.ArgumentTypeError(
                f"invalid profile {raw_profile!r}; expected ROWS:CHUNK_SIZE"
            )
        try:
            rows = int(parts[0].replace("_", ""))
            chunk_size = int(parts[1].replace("_", ""))
        except ValueError as error:
            raise argparse.ArgumentTypeError(
                f"invalid numeric profile {raw_profile!r}"
            ) from error
        if not 1 <= rows <= MAX_ROWS:
            raise argparse.ArgumentTypeError("rows must be between 1 and 99,999")
        if not 1 <= chunk_size <= MAX_CHUNK_SIZE:
            raise argparse.ArgumentTypeError(
                "chunk size must be between 1 and 10,000"
            )
        key = (rows, chunk_size)
        if key in seen:
            raise argparse.ArgumentTypeError(
                f"duplicate profile {rows:,}:{chunk_size:,}"
            )
        seen.add(key)
        profiles.append(Profile(rows, chunk_size))
    if not profiles:
        raise argparse.ArgumentTypeError("at least one profile is required")
    return tuple(profiles)


def parse_modes(value: str) -> tuple[str, ...]:
    modes = tuple(item.strip().lower() for item in value.split(",") if item.strip())
    if not modes:
        raise argparse.ArgumentTypeError("at least one mode is required")
    if len(modes) != len(set(modes)):
        raise argparse.ArgumentTypeError("producer modes must not repeat")
    invalid = sorted(set(modes) - {"sync", "async"})
    if invalid:
        raise argparse.ArgumentTypeError(
            "producer modes must contain only sync and async"
        )
    return modes


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run bounded sync/async native-bulk iterable SQL-auth profiles. "
            "The 99,999-row profile requires --allow-extended."
        )
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
    if (
        any(profile.rows == EXTENDED_ROWS for profile in args.profiles)
        and not args.allow_extended
    ):
        parser.error("the 99,999-row profile requires explicit --allow-extended")
    if args.rss_growth_limit_bytes < 0:
        parser.error("--rss-growth-limit-bytes must be non-negative")
    if (
        not math.isfinite(args.event_loop_gap_limit_seconds)
        or args.event_loop_gap_limit_seconds <= 0.0
    ):
        parser.error(
            "--event-loop-gap-limit-seconds must be finite and positive"
        )
    if (
        not math.isfinite(args.operation_timeout_seconds)
        or args.operation_timeout_seconds <= 0.0
    ):
        parser.error("--operation-timeout-seconds must be finite and positive")
    if not 1 <= args.payload_bytes <= 1_024:
        parser.error("--payload-bytes must be between 1 and 1024")
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


def worktree_is_dirty() -> bool:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(completed.stdout)


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
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def quote_identifier(value: str) -> str:
    if not value or not value[0].isalpha() or not value.replace("_", "").isalnum():
        raise ValueError("unsafe generated SQL identifier")
    return f"[{value}]"


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


async def physical_identity(connection: Connection) -> tuple[int, str]:
    row = (
        await connection.query(
            """
            SELECT
                @@SPID AS session_id,
                CONVERT(NVARCHAR(36), connection_id) AS connection_id
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """
        )
    ).fetchone()
    if row is None:
        raise AssertionError("physical SQL identity row is missing")
    return int(row["session_id"]), str(row["connection_id"])


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
    timeout_seconds: float = 10.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if await application_session_count(observer, application_name) == 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("candidate SQL sessions did not reach zero")


def expected_summary(row_count: int) -> dict[str, int]:
    return {
        "rows": row_count,
        "minimum_id": 0,
        "maximum_id": row_count - 1,
        "id_sum": row_count * (row_count - 1) // 2,
    }


async def persisted_summary(
    connection: Connection,
    table: str,
) -> dict[str, int | None]:
    row = (
        await connection.query(
            f"""
            SELECT
                COUNT_BIG(*) AS row_count,
                MIN(id) AS minimum_id,
                MAX(id) AS maximum_id,
                SUM(id) AS id_sum
            FROM {table}
            """
        )
    ).fetchone()
    if row is None:
        raise AssertionError("persisted summary row is missing")
    return {
        "rows": int(row["row_count"]),
        "minimum_id": (
            None if row["minimum_id"] is None else int(row["minimum_id"])
        ),
        "maximum_id": (
            None if row["maximum_id"] is None else int(row["maximum_id"])
        ),
        "id_sum": None if row["id_sum"] is None else int(row["id_sum"]),
    }


def metric_delta(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, int]:
    fields = (
        "started",
        "completed",
        "succeeded",
        "errors",
        "timed_out",
        "cancelled",
        "outcome_unknown",
    )
    return {
        field: int(after[field]) - int(before[field])
        for field in fields
    }


def profile_violations(
    metrics: dict[str, Any],
    *,
    rss_growth_limit_bytes: int,
    event_loop_gap_limit_seconds: float,
) -> list[str]:
    violations: list[str] = []
    row_count = int(metrics["row_count"])
    if metrics["errors"] or metrics["timed_out"]:
        violations.append("operation_error")
    if metrics["affected_rows"] != row_count:
        violations.append("affected_row_count_mismatch")
    if metrics["persisted"] != expected_summary(row_count):
        violations.append("persisted_row_count_mismatch")
    if metrics["producer_pulls"] != row_count:
        violations.append("producer_pull_count_mismatch")
    if metrics["maximum_buffered_rows"] > metrics["chunk_size"]:
        violations.append("buffer_bound_exceeded")
    if metrics["buffered_rows_after_gc"] != 0:
        violations.append("buffered_rows_not_released")
    if metrics["rss_growth_bytes"] > rss_growth_limit_bytes:
        violations.append("rss_growth_exceeded")
    if metrics["maximum_event_loop_gap_seconds"] > event_loop_gap_limit_seconds:
        violations.append("event_loop_gap_exceeded")
    if metrics["event_loop_ticks"] < 1:
        violations.append("event_loop_not_observed")
    if metrics["maximum_sql_sessions"] != 1:
        violations.append("physical_session_bound_mismatch")
    if not metrics["physical_identity_stable"]:
        violations.append("physical_identity_changed")
    operation = metrics["operation_metric_delta"]
    if (
        operation["started"] != 1
        or operation["completed"] != 1
        or operation["succeeded"] != 1
        or operation["errors"] != 0
        or operation["timed_out"] != 0
        or operation["cancelled"] != 0
        or operation["outcome_unknown"] != 0
    ):
        violations.append("operation_metric_mismatch")
    if (
        metrics["pool"]["max_size"] != 1
        or metrics["pool"]["connections"] > 1
        or metrics["pool"]["active_connections"] != 0
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
    rss_growth_limit_bytes: int,
    event_loop_gap_limit_seconds: float,
    operation_timeout_seconds: float,
    payload_bytes: int,
) -> dict[str, object]:
    suffix = uuid4().hex[:12]
    raw_table = f"native_bulk_iterable_{suffix}"
    table = quote_identifier(raw_table)
    application_name = f"fastmssql_iter_bulk_{suffix}"
    connection = settings.connection(
        application_name=application_name,
        operation_timeout_seconds=operation_timeout_seconds,
        metrics=True,
    )
    process = psutil.Process()
    tracker = BufferTracker()
    payload = "x" * payload_bytes
    producer: SyncProducer | AsyncProducer
    if mode == "sync":
        producer = SyncProducer(profile.rows, payload, tracker)
    elif mode == "async":
        producer = AsyncProducer(profile.rows, payload, tracker)
    else:
        raise ValueError("mode must be sync or async")

    errors: list[str] = []
    timed_out = 0
    affected_rows: int | None = None
    persisted: dict[str, int | None] | None = None
    identity_before: tuple[int, str] | None = None
    identity_after: tuple[int, str] | None = None
    operation_delta: dict[str, int] | None = None
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
                payload NVARCHAR(1024) NOT NULL,
                bucket INT NOT NULL
                    CHECK (bucket >= 0 AND bucket < 100)
            )
            """
        )
        identity_before = await physical_identity(connection)
        before_metric = (
            await connection.operation_stats()
        )["operations"]["bulk_insert"]
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
            await connection.native_bulk_insert(
                raw_table,
                ["id", "payload", "bucket"],
                producer,
                chunk_size=profile.chunk_size,
            )
        )
        elapsed_seconds = time.perf_counter() - started
        identity_after = await physical_identity(connection)
        persisted = await persisted_summary(connection, table)
        post_load_smoke = int(await scalar(connection, "SELECT 1")) == 1
        after_metric = (
            await connection.operation_stats()
        )["operations"]["bulk_insert"]
        operation_delta = metric_delta(before_metric, after_metric)
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

    if operation_delta is None:
        operation_delta = {
            "started": 0,
            "completed": 0,
            "succeeded": 0,
            "errors": 0,
            "timed_out": 0,
            "cancelled": 0,
            "outcome_unknown": 0,
        }
    metrics: dict[str, Any] = {
        "input_model": f"{mode}_iterable",
        "row_count": profile.rows,
        "chunk_size": profile.chunk_size,
        "affected_rows": affected_rows,
        "persisted": persisted,
        "producer_pulls": producer.pulls,
        "maximum_buffered_rows": tracker.maximum,
        "buffered_rows_after_gc": tracker.current,
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
        "operation_metric_delta": operation_delta,
        "pool": {
            "max_size": int(pool.get("max_size", 1)),
            "connections": int(pool.get("connections", 0)),
            "active_connections": int(pool.get("active_connections", 0)),
        },
        "elapsed_seconds": elapsed_seconds,
        "throughput_rows_per_second": (
            None
            if elapsed_seconds is None or elapsed_seconds <= 0.0
            else profile.rows / elapsed_seconds
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
        application_name=f"fastmssql_iter_bulk_observer_{uuid4().hex[:12]}",
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
            "maximum_rows": MAX_ROWS,
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
