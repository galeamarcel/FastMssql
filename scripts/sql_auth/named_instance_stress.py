#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4

from fastmssql import (
    Connection,
    OperationTimeoutError,
    PoolConfig,
    SslConfig,
    TimeoutConfig,
)
import psutil


ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = ROOT / "tests"
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))
SqlBrowserFixture = importlib.import_module(
    "sql_auth_strict.sql_browser_fixture"
).SqlBrowserFixture
INSTANCE_NAME = "FASTMSSQL"
MAX_OPERATIONS = 99_999
EXTENDED_OPERATIONS = 99_999
MAX_WORKERS = 1_000
MAX_POOL_SIZE = 100
DEFAULT_OPERATIONS = 1_000
DEFAULT_WORKERS = 32
DEFAULT_POOL_SIZE = 8
DEFAULT_RSS_GROWTH_LIMIT_BYTES = 134_217_728
DEFAULT_EVENT_LOOP_GAP_LIMIT_SECONDS = 0.100
DEFAULT_OPERATION_TIMEOUT_SECONDS = 30.0
SAMPLE_INTERVAL_SECONDS = 0.005
SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")


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
    def from_env(cls) -> "SqlAuthSettings":
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

    def workload_connection(
        self,
        *,
        application_name: str,
        pool_size: int,
        operation_timeout_seconds: float,
    ) -> Connection:
        return Connection(
            server=self.host,
            instance_name=INSTANCE_NAME,
            database=self.database,
            username=self.owner_username,
            password=self.owner_password,
            application_name=application_name,
            ssl_config=SslConfig.development(),
            pool_config=PoolConfig(
                max_size=pool_size,
                min_idle=pool_size,
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
                transaction_timeout_secs=None,
                rollback_timeout_secs=10.0,
            ),
        )

    def observer_connection(self) -> Connection:
        return Connection(
            server=self.host,
            port=self.port,
            database=self.database,
            username=self.observer_username,
            password=self.observer_password,
            application_name="fastmssql_named_instance_stress_observer",
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
                operation_timeout_secs=10.0,
                transaction_timeout_secs=None,
                rollback_timeout_secs=10.0,
            ),
        )


def positive_int(value: str) -> int:
    parsed = int(value.replace("_", ""))
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def positive_float(value: str) -> float:
    parsed = float(value)
    if not 0 < parsed < float("inf"):
        raise argparse.ArgumentTypeError("value must be finite and positive")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--operations",
        type=positive_int,
        default=DEFAULT_OPERATIONS,
    )
    parser.add_argument("--workers", type=positive_int, default=DEFAULT_WORKERS)
    parser.add_argument(
        "--pool-size",
        type=positive_int,
        default=DEFAULT_POOL_SIZE,
    )
    parser.add_argument(
        "--operation-timeout-seconds",
        type=positive_float,
        default=DEFAULT_OPERATION_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--rss-growth-limit-bytes",
        type=positive_int,
        default=DEFAULT_RSS_GROWTH_LIMIT_BYTES,
    )
    parser.add_argument(
        "--event-loop-gap-limit-seconds",
        type=positive_float,
        default=DEFAULT_EVENT_LOOP_GAP_LIMIT_SECONDS,
    )
    parser.add_argument("--allow-extended", action="store_true")
    parser.add_argument("--source-sha", default="")
    parser.add_argument(
        "--metrics-output",
        type=Path,
        required=True,
    )
    args = parser.parse_args(argv)

    if args.operations > MAX_OPERATIONS:
        parser.error("operations must be between 1 and 99,999")
    if args.workers > MAX_WORKERS:
        parser.error("workers must be between 1 and 1,000")
    if args.pool_size > MAX_POOL_SIZE:
        parser.error("pool size must be between 1 and 100")
    if args.operations >= EXTENDED_OPERATIONS and not args.allow_extended:
        parser.error("99,999 operations require --allow-extended")
    if args.source_sha and SOURCE_SHA.fullmatch(args.source_sha) is None:
        parser.error("source SHA must be exactly 40 lowercase hexadecimal digits")
    return args


def git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    source_sha = completed.stdout.strip()
    if SOURCE_SHA.fullmatch(source_sha) is None:
        raise RuntimeError("repository HEAD is not a full Git SHA")
    return source_sha


def atomic_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


async def _scalar(connection: Connection, sql: str, params=None):
    result = await connection.query(sql, params)
    row = result.fetchone()
    if row is None or len(row) != 1:
        raise AssertionError("stress observer expected exactly one scalar row")
    return row[0]


async def _application_activity(
    observer: Connection,
    application_name: str,
) -> tuple[int, int]:
    result = await observer.query(
        """
        SELECT
            COUNT(DISTINCT session.session_id) AS sessions,
            COUNT(DISTINCT transaction_session.transaction_id) AS transactions
        FROM sys.dm_exec_sessions AS session
        LEFT JOIN sys.dm_tran_session_transactions AS transaction_session
          ON transaction_session.session_id = session.session_id
        WHERE session.program_name = @P1
          AND session.session_id <> @@SPID
        """,
        [application_name],
    )
    row = result.fetchone()
    return int(row["sessions"]), int(row["transactions"])


async def _wait_for_zero_activity(
    observer: Connection,
    application_name: str,
) -> tuple[int, int]:
    deadline = time.monotonic() + 5.0
    activity = await _application_activity(observer, application_name)
    while activity != (0, 0) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        activity = await _application_activity(observer, application_name)
    return activity


async def run_profile(
    *,
    settings: SqlAuthSettings,
    operations: int,
    workers: int,
    pool_size: int,
    operation_timeout_seconds: float,
    rss_growth_limit_bytes: int,
    event_loop_gap_limit_seconds: float,
) -> dict[str, object]:
    worker_count = min(workers, operations)
    application_name = f"fastmssql_named_stress_{uuid4().hex[:12]}"
    connection = settings.workload_connection(
        application_name=application_name,
        pool_size=pool_size,
        operation_timeout_seconds=operation_timeout_seconds,
    )
    observer = settings.observer_connection()
    fixture = SqlBrowserFixture(
        host=settings.host,
        tcp_port=settings.port,
    )
    process = psutil.Process()
    baseline_rss = process.memory_info().rss
    peak_rss = baseline_rss
    maximum_event_loop_gap = 0.0
    maximum_pool_connections = 0
    maximum_sql_sessions = 0
    maximum_in_flight = 0
    in_flight = 0
    succeeded = 0
    timed_out = 0
    failure_types: Counter[str] = Counter()
    seen = bytearray(operations)
    duplicate_ids: list[int] = []
    spids: set[int] = set()
    stop = asyncio.Event()
    sampler_failures: list[str] = []
    started = time.monotonic()

    async def ticker() -> None:
        nonlocal maximum_event_loop_gap
        previous = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(SAMPLE_INTERVAL_SECONDS)
            current = time.monotonic()
            maximum_event_loop_gap = max(
                maximum_event_loop_gap,
                current - previous,
            )
            previous = current

    async def sampler() -> None:
        nonlocal peak_rss, maximum_pool_connections, maximum_sql_sessions
        while not stop.is_set():
            try:
                peak_rss = max(peak_rss, process.memory_info().rss)
                stats = await connection.pool_stats()
                maximum_pool_connections = max(
                    maximum_pool_connections,
                    int(stats["connections"]),
                )
                sessions, _ = await _application_activity(
                    observer,
                    application_name,
                )
                maximum_sql_sessions = max(maximum_sql_sessions, sessions)
            except BaseException as error:
                sampler_failures.append(type(error).__name__)
                stop.set()
                return
            await asyncio.sleep(SAMPLE_INTERVAL_SECONDS)

    queue: asyncio.Queue[int | None] = asyncio.Queue(maxsize=max(1, worker_count * 2))

    async def producer() -> None:
        for operation_id in range(operations):
            await queue.put(operation_id)
        for _ in range(worker_count):
            await queue.put(None)

    async def worker() -> None:
        nonlocal in_flight, maximum_in_flight, succeeded, timed_out
        while True:
            operation_id = await queue.get()
            try:
                if operation_id is None:
                    return
                in_flight += 1
                maximum_in_flight = max(maximum_in_flight, in_flight)
                try:
                    result = await connection.query(
                        "SELECT @P1 AS operation_id, @@SPID AS spid",
                        [operation_id],
                    )
                    row = result.fetchone()
                    actual_id = int(row["operation_id"])
                    if actual_id != operation_id:
                        raise AssertionError("SQL result identifier mismatch")
                    if seen[actual_id]:
                        duplicate_ids.append(actual_id)
                    else:
                        seen[actual_id] = 1
                    spids.add(int(row["spid"]))
                    succeeded += 1
                except OperationTimeoutError:
                    timed_out += 1
                except BaseException as error:
                    failure_types[type(error).__name__] += 1
                finally:
                    in_flight -= 1
            finally:
                queue.task_done()

    post_load_smoke = False
    final_stats: dict[str, object] = {}
    fixture_snapshot = None
    sessions_after_disconnect = -1
    transactions_after_disconnect = -1
    await observer.connect()
    try:
        baseline_activity = await _application_activity(
            observer,
            application_name,
        )
        if baseline_activity != (0, 0):
            raise AssertionError("named-instance stress application is not isolated")
        async with fixture:
            await connection.connect()
            ticker_task = asyncio.create_task(ticker())
            sampler_task = asyncio.create_task(sampler())
            try:
                async with asyncio.TaskGroup() as group:
                    group.create_task(producer())
                    for _ in range(worker_count):
                        group.create_task(worker())
                await queue.join()
            finally:
                stop.set()
                await ticker_task
                await sampler_task

            post_load_smoke = (
                int(await _scalar(connection, "SELECT @P1 + 1", [40])) == 41
            )
            final_stats = await connection.pool_stats()
            maximum_pool_connections = max(
                maximum_pool_connections,
                int(final_stats["connections"]),
            )
            await connection.disconnect()
            (
                sessions_after_disconnect,
                transactions_after_disconnect,
            ) = await _wait_for_zero_activity(observer, application_name)
            fixture_snapshot = fixture.snapshot()
    finally:
        stop.set()
        await connection.disconnect()
        await observer.disconnect()

    finished = time.monotonic()
    peak_rss = max(peak_rss, process.memory_info().rss)
    missing_ids = [
        operation_id for operation_id, present in enumerate(seen) if not present
    ]
    completed_digest = hashlib.sha256()
    for operation_id, present in enumerate(seen):
        if present:
            completed_digest.update(operation_id.to_bytes(8, "little"))

    if fixture_snapshot is None:
        raise AssertionError("SQL Browser fixture produced no terminal snapshot")
    successful_physical_connections = int(final_stats.get("connections_created", 0))
    failed = sum(failure_types.values())
    rss_growth = max(0, peak_rss - baseline_rss)
    violations: list[str] = []
    if succeeded != operations:
        violations.append("success_count")
    if timed_out:
        violations.append("timeouts")
    if failed:
        violations.append("failures")
    if missing_ids:
        violations.append("missing_ids")
    if duplicate_ids:
        violations.append("duplicate_ids")
    if fixture_snapshot.invalid_requests:
        violations.append("invalid_browser_request")
    if fixture_snapshot.valid_requests != successful_physical_connections:
        violations.append("discovery_connection_mismatch")
    if maximum_pool_connections > pool_size:
        violations.append("pool_bound")
    if maximum_sql_sessions > pool_size:
        violations.append("session_bound")
    if maximum_in_flight > worker_count:
        violations.append("worker_bound")
    if rss_growth > rss_growth_limit_bytes:
        violations.append("rss_bound")
    if maximum_event_loop_gap > event_loop_gap_limit_seconds:
        violations.append("event_loop_gap")
    if not post_load_smoke:
        violations.append("post_load_smoke")
    if sessions_after_disconnect:
        violations.append("teardown_sessions")
    if transactions_after_disconnect:
        violations.append("teardown_transactions")
    if sampler_failures:
        violations.append("sampler_failure")

    return {
        "status": "passed" if not violations else "failed",
        "operations": operations,
        "worker_count": worker_count,
        "pool_max_size": pool_size,
        "succeeded": succeeded,
        "failed": failed,
        "timed_out": timed_out,
        "failure_types": dict(sorted(failure_types.items())),
        "completed_id_count": sum(seen),
        "completed_ids_sha256": completed_digest.hexdigest(),
        "missing_ids": missing_ids[:100],
        "missing_id_count": len(missing_ids),
        "duplicate_ids": duplicate_ids[:100],
        "duplicate_id_count": len(duplicate_ids),
        "unique_sql_spids": len(spids),
        "browser_requests": fixture_snapshot.valid_requests,
        "invalid_browser_requests": fixture_snapshot.invalid_requests,
        "successful_physical_connections": successful_physical_connections,
        "maximum_pool_connections": maximum_pool_connections,
        "maximum_sql_sessions": maximum_sql_sessions,
        "maximum_in_flight": maximum_in_flight,
        "queue_capacity": max(1, worker_count * 2),
        "rss_growth_bytes": rss_growth,
        "rss_growth_limit_bytes": rss_growth_limit_bytes,
        "maximum_event_loop_gap_seconds": maximum_event_loop_gap,
        "event_loop_gap_limit_seconds": event_loop_gap_limit_seconds,
        "duration_seconds": finished - started,
        "operations_per_second": operations / max(finished - started, 1e-9),
        "post_load_smoke": post_load_smoke,
        "sessions_after_disconnect": sessions_after_disconnect,
        "transactions_after_disconnect": transactions_after_disconnect,
        "sampler_failures": sampler_failures,
        "violations": violations,
    }


async def async_main(args: argparse.Namespace) -> dict[str, object]:
    settings = SqlAuthSettings.from_env()
    profile = await run_profile(
        settings=settings,
        operations=args.operations,
        workers=args.workers,
        pool_size=args.pool_size,
        operation_timeout_seconds=args.operation_timeout_seconds,
        rss_growth_limit_bytes=args.rss_growth_limit_bytes,
        event_loop_gap_limit_seconds=args.event_loop_gap_limit_seconds,
    )
    return {
        "schema_version": 1,
        "source_sha": args.source_sha or git_head(),
        "status": profile["status"],
        "configuration": {
            "extended": args.operations >= EXTENDED_OPERATIONS,
            "fixed_worker_model": True,
            "persistent_connection": True,
        },
        "profile": profile,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = asyncio.run(async_main(args))
    except BaseException as error:
        payload = {
            "schema_version": 1,
            "source_sha": args.source_sha or git_head(),
            "status": "failed",
            "configuration": {
                "extended": args.operations >= EXTENDED_OPERATIONS,
                "fixed_worker_model": True,
                "persistent_connection": True,
            },
            "profile": {
                "status": "failed",
                "operations": args.operations,
                "failure_types": {type(error).__name__: 1},
                "violations": ["stress_execution_failure"],
            },
        }
    atomic_write(args.metrics_output, payload)
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
