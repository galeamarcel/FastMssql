#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import gc
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
from fastmssql import Connection, PoolConfig, SslConfig, TimeoutConfig
import psutil


ROOT = Path(__file__).resolve().parents[2]
MAX_ROWS = 99_999
MAX_CHUNK_SIZE = 10_000
EXTENDED_ROWS = 99_999
DEFAULT_PROFILES = "1_000:250,10_000:1_000"
DEFAULT_RSS_GROWTH_LIMIT_BYTES = 134_217_728
DEFAULT_EVENT_LOOP_STALL_LIMIT_SECONDS = 0.5
DEFAULT_OPERATION_TIMEOUT_SECONDS = 300.0
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
        required = {
            "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD": os.getenv(
                "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD",
                "",
            ),
            "FASTMSSQL_SQL_AUTH_SA_PASSWORD": os.getenv(
                "FASTMSSQL_SQL_AUTH_SA_PASSWORD",
                "",
            ),
        }
        missing = sorted(name for name, value in required.items() if not value)
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
            owner_password=required["FASTMSSQL_SQL_AUTH_OWNER_PASSWORD"],
            observer_username=os.getenv("FASTMSSQL_SQL_AUTH_SA_USER", "sa"),
            observer_password=required["FASTMSSQL_SQL_AUTH_SA_PASSWORD"],
        )

    def connection(
        self,
        *,
        application_name: str,
        operation_timeout_seconds: float,
        observer: bool = False,
    ) -> Connection:
        return Connection(
            server=self.host,
            port=self.port,
            database=self.database,
            username=(self.observer_username if observer else self.owner_username),
            password=(self.observer_password if observer else self.owner_password),
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
        )


def parse_profiles(value: str) -> tuple[Profile, ...]:
    profiles: list[Profile] = []
    seen_rows: set[int] = set()
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
            raise argparse.ArgumentTypeError("chunk size must be between 1 and 10,000")
        if rows in seen_rows:
            raise argparse.ArgumentTypeError(f"duplicate row-count profile {rows:,}")
        seen_rows.add(rows)
        profiles.append(Profile(rows, chunk_size))
    if not profiles:
        raise argparse.ArgumentTypeError("at least one profile is required")
    return tuple(profiles)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run bounded FastMssql native-TDS bulk SQL-auth profiles. "
            "The 99,999-row profile requires --allow-extended."
        )
    )
    parser.add_argument(
        "--profiles",
        type=parse_profiles,
        default=parse_profiles(DEFAULT_PROFILES),
        help="comma-separated ROWS:CHUNK_SIZE profiles",
    )
    parser.add_argument(
        "--allow-extended",
        action="store_true",
        help="explicitly allow the 99,999-row profile",
    )
    parser.add_argument("--metrics-output", type=Path, required=True)
    parser.add_argument(
        "--rss-growth-limit-bytes",
        type=int,
        default=DEFAULT_RSS_GROWTH_LIMIT_BYTES,
    )
    parser.add_argument(
        "--event-loop-stall-limit-seconds",
        type=float,
        default=DEFAULT_EVENT_LOOP_STALL_LIMIT_SECONDS,
    )
    parser.add_argument(
        "--operation-timeout-seconds",
        type=float,
        default=DEFAULT_OPERATION_TIMEOUT_SECONDS,
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
        not math.isfinite(args.event_loop_stall_limit_seconds)
        or args.event_loop_stall_limit_seconds <= 0.0
    ):
        parser.error("--event-loop-stall-limit-seconds must be finite and positive")
    if (
        not math.isfinite(args.operation_timeout_seconds)
        or args.operation_timeout_seconds <= 0.0
    ):
        parser.error("--operation-timeout-seconds must be finite and positive")
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


def nearest_rank(values: list[int], percentile: int) -> int:
    if not values:
        raise ValueError("cannot calculate a percentile without observations")
    if not 1 <= percentile <= 100:
        raise ValueError("percentile must be between 1 and 100")
    ordered = sorted(values)
    rank = math.ceil(percentile * len(ordered) / 100)
    return ordered[rank - 1]


def latency_summary(latencies_ns: list[int]) -> dict[str, float]:
    return {
        "p50": nearest_rank(latencies_ns, 50) / 1_000_000_000,
        "p95": nearest_rank(latencies_ns, 95) / 1_000_000_000,
        "p99": nearest_rank(latencies_ns, 99) / 1_000_000_000,
        "maximum": max(latencies_ns) / 1_000_000_000,
    }


def quote_identifier(value: str) -> str:
    if not value or not value[0].isalpha() or not value.replace("_", "").isalnum():
        raise ValueError("unsafe generated SQL identifier")
    return f"[{value}]"


async def scalar(
    client: Any,
    sql: str,
    parameters: list[object] | None = None,
) -> object:
    result = await client.query(sql, parameters)
    row = result.fetchone()
    if row is None or len(row) != 1:
        raise AssertionError("expected exactly one scalar row")
    return row[0]


async def physical_identity(client: Any) -> tuple[int, str]:
    result = await client.query(
        """
        SELECT
            @@SPID AS session_id,
            CONVERT(NVARCHAR(36), connection_id) AS connection_id
        FROM sys.dm_exec_connections
        WHERE session_id = @@SPID
        """
    )
    row = result.fetchone()
    if row is None:
        raise AssertionError("physical SQL identity row is missing")
    return int(row["session_id"]), str(row["connection_id"])


async def persisted_summary(
    connection: Connection,
    quoted_table: str,
) -> dict[str, int | None]:
    result = await connection.query(
        f"""
        SELECT
            COUNT_BIG(*) AS row_count,
            MIN(id) AS minimum_id,
            MAX(id) AS maximum_id,
            SUM(id) AS id_sum
        FROM {quoted_table}
        """
    )
    row = result.fetchone()
    if row is None:
        raise AssertionError("persisted native-bulk summary row is missing")
    return {
        "rows": int(row["row_count"]),
        "minimum_id": (None if row["minimum_id"] is None else int(row["minimum_id"])),
        "maximum_id": (None if row["maximum_id"] is None else int(row["maximum_id"])),
        "id_sum": None if row["id_sum"] is None else int(row["id_sum"]),
    }


async def application_session_count(
    observer: Connection,
    application_name: str,
) -> int:
    value = await scalar(
        observer,
        """
        SELECT COUNT_BIG(*)
        FROM sys.dm_exec_sessions
        WHERE program_name = @P1
        """,
        [application_name],
    )
    return int(value)


async def wait_for_zero_sessions(
    observer: Connection,
    application_name: str,
    *,
    timeout_seconds: float = 10.0,
) -> int:
    deadline = time.monotonic() + timeout_seconds
    latest = -1
    while time.monotonic() < deadline:
        latest = await application_session_count(observer, application_name)
        if latest == 0:
            return latest
        await asyncio.sleep(0.02)
    raise AssertionError(
        f"candidate SQL sessions did not reach zero; observed {latest}"
    )


def expected_summary(row_count: int) -> dict[str, int]:
    return {
        "rows": row_count,
        "minimum_id": 0,
        "maximum_id": row_count - 1,
        "id_sum": row_count * (row_count - 1) // 2,
    }


def build_rows(row_count: int) -> list[list[object]]:
    return [
        [row_id, f"native-bulk-{row_id}", row_id % 100] for row_id in range(row_count)
    ]


def profile_violations(
    metrics: dict[str, Any],
    *,
    rss_growth_limit_bytes: int,
    event_loop_stall_limit_seconds: float,
) -> list[str]:
    violations: list[str] = []
    if metrics["errors"] or metrics["timed_out"]:
        violations.append("operation_error")

    expected = expected_summary(int(metrics["rows"]))
    primary = metrics["primary_single_call"]
    if primary["affected_rows"] != metrics["rows"] or primary["persisted"] != expected:
        violations.append("primary_row_count_mismatch")
    if not primary["physical_identity_stable"]:
        violations.append("primary_physical_identity_changed")

    probe = metrics["chunk_call_probe"]
    expected_calls = math.ceil(metrics["rows"] / metrics["chunk_size"])
    if (
        probe["affected_rows"] != metrics["rows"]
        or probe["persisted"] != expected
        or probe["call_count"] != expected_calls
    ):
        violations.append("chunk_probe_row_count_mismatch")
    if not probe["physical_identity_stable"]:
        violations.append("chunk_probe_physical_identity_changed")

    resources = metrics["resources"]
    if resources["maximum_sql_sessions"] != 1:
        violations.append("physical_session_bound_mismatch")
    rss_growth = resources["rss_growth_bytes"]
    maximum_stall = resources["maximum_event_loop_stall_seconds"]
    if rss_growth is None or maximum_stall is None:
        violations.append("resource_metrics_unavailable")
    if resources["rss_growth_limit_bytes"] != rss_growth_limit_bytes or (
        rss_growth is not None and rss_growth > rss_growth_limit_bytes
    ):
        violations.append("rss_growth_exceeded")
    if (
        resources["event_loop_ticks"] < 1
        or resources["event_loop_stall_limit_seconds"] != event_loop_stall_limit_seconds
        or (
            maximum_stall is not None and maximum_stall > event_loop_stall_limit_seconds
        )
    ):
        violations.append("event_loop_stall_exceeded")

    pool = metrics["pool"]
    if (
        pool["maximum_size"] != 1
        or pool["connections"] > 1
        or pool["final_active_connections"] != 0
    ):
        violations.append("pool_bound_mismatch")
    if not metrics["post_load_smoke"]:
        violations.append("post_load_smoke_failed")
    if metrics["teardown_sessions"] != 0:
        violations.append("teardown_session_leak")
    return violations


async def run_profile(
    settings: SqlAuthSettings,
    observer: Connection,
    profile: Profile,
    *,
    rss_growth_limit_bytes: int,
    event_loop_stall_limit_seconds: float,
    operation_timeout_seconds: float,
) -> dict[str, object]:
    suffix = uuid4().hex[:12]
    raw_table = f"native_bulk_stress_{suffix}"
    quoted_table = quote_identifier(raw_table)
    application_name = f"fastmssql_native_bulk_{suffix}"
    connection = settings.connection(
        application_name=application_name,
        operation_timeout_seconds=operation_timeout_seconds,
    )
    process = psutil.Process()
    errors: list[str] = []
    timed_out = 0
    created = False
    monitor_stop = asyncio.Event()
    ticker_task: asyncio.Task[None] | None = None
    sampler_task: asyncio.Task[None] | None = None
    monitor_state: dict[str, int | float] = {
        "ticks": 0,
        "maximum_stall_seconds": 0.0,
        "maximum_sql_sessions": 0,
        "peak_rss_bytes": 0,
        "sampler_iterations": 0,
    }
    metrics: dict[str, Any] = {
        "status": "running",
        "rows": profile.rows,
        "chunk_size": profile.chunk_size,
        "input_model": "concrete_list",
        "errors": errors,
        "timed_out": timed_out,
        "primary_single_call": {
            "affected_rows": None,
            "persisted": None,
            "elapsed_seconds": None,
            "throughput_rows_per_second": None,
            "physical_identity_stable": False,
        },
        "chunk_call_probe": {
            "affected_rows": None,
            "persisted": None,
            "call_count": 0,
            "latency_seconds": None,
            "physical_identity_stable": False,
        },
        "resources": {
            "rss_baseline_bytes": None,
            "rss_peak_bytes": None,
            "rss_final_bytes": None,
            "rss_growth_bytes": None,
            "rss_growth_limit_bytes": rss_growth_limit_bytes,
            "event_loop_ticks": 0,
            "maximum_event_loop_stall_seconds": 0.0,
            "event_loop_stall_limit_seconds": (event_loop_stall_limit_seconds),
            "maximum_sql_sessions": 0,
            "sampler_iterations": 0,
        },
        "pool": {
            "maximum_size": 1,
            "connections": 0,
            "final_active_connections": 0,
        },
        "post_load_smoke": False,
        "teardown_sessions": None,
    }

    async def ticker() -> None:
        previous = time.perf_counter()
        while not monitor_stop.is_set():
            await asyncio.sleep(SAMPLE_INTERVAL_SECONDS)
            current = time.perf_counter()
            monitor_state["ticks"] = int(monitor_state["ticks"]) + 1
            monitor_state["maximum_stall_seconds"] = max(
                float(monitor_state["maximum_stall_seconds"]),
                current - previous,
            )
            previous = current

    async def sampler() -> None:
        while not monitor_stop.is_set():
            sessions = await application_session_count(
                observer,
                application_name,
            )
            monitor_state["maximum_sql_sessions"] = max(
                int(monitor_state["maximum_sql_sessions"]),
                sessions,
            )
            monitor_state["peak_rss_bytes"] = max(
                int(monitor_state["peak_rss_bytes"]),
                process.memory_info().rss,
            )
            monitor_state["sampler_iterations"] = (
                int(monitor_state["sampler_iterations"]) + 1
            )
            await asyncio.sleep(SAMPLE_INTERVAL_SECONDS)

    rows: list[list[object]] | None = None
    rss_baseline = 0
    try:
        await connection.connect()
        await connection.execute(
            f"""
            CREATE TABLE {quoted_table} (
                id BIGINT NOT NULL PRIMARY KEY,
                payload NVARCHAR(64) NOT NULL,
                bucket INT NOT NULL
                    CHECK (bucket >= 0 AND bucket < 100)
            )
            """
        )
        created = True
        original_identity = await physical_identity(connection)
        rows = build_rows(profile.rows)
        gc.collect()
        rss_baseline = process.memory_info().rss
        initial_sessions = await application_session_count(
            observer,
            application_name,
        )
        monitor_state["maximum_sql_sessions"] = initial_sessions
        monitor_state["peak_rss_bytes"] = rss_baseline
        ticker_task = asyncio.create_task(ticker())
        sampler_task = asyncio.create_task(sampler())
        await asyncio.sleep(0)

        primary_started_ns = time.perf_counter_ns()
        primary_affected = await connection.native_bulk_insert(
            raw_table,
            ["id", "payload", "bucket"],
            rows,
            chunk_size=profile.chunk_size,
        )
        primary_finished_ns = time.perf_counter_ns()
        primary_elapsed_seconds = (
            primary_finished_ns - primary_started_ns
        ) / 1_000_000_000
        primary_identity = await physical_identity(connection)
        primary_persisted = await persisted_summary(
            connection,
            quoted_table,
        )
        metrics["primary_single_call"] = {
            "affected_rows": int(primary_affected),
            "persisted": primary_persisted,
            "elapsed_seconds": primary_elapsed_seconds,
            "throughput_rows_per_second": (profile.rows / primary_elapsed_seconds),
            "physical_identity_stable": (primary_identity == original_identity),
        }

        await connection.execute(f"TRUNCATE TABLE {quoted_table}")
        transaction = connection.transaction()
        transaction_started = False
        transaction_settled = False
        probe_affected = 0
        probe_latencies_ns: list[int] = []
        transaction_identity: tuple[int, str] | None = None
        try:
            await transaction.begin()
            transaction_started = True
            transaction_identity = await physical_identity(transaction)
            for offset in range(0, profile.rows, profile.chunk_size):
                chunk = rows[offset : offset + profile.chunk_size]
                chunk_started_ns = time.perf_counter_ns()
                affected = await transaction.native_bulk_insert(
                    raw_table,
                    ["id", "payload", "bucket"],
                    chunk,
                    chunk_size=len(chunk),
                )
                probe_latencies_ns.append(time.perf_counter_ns() - chunk_started_ns)
                probe_affected += int(affected)
            await transaction.commit()
            transaction_settled = True
        except BaseException:
            if transaction_started and not transaction_settled:
                await transaction.rollback()
                transaction_settled = True
            raise
        finally:
            await transaction.close()

        probe_identity = await physical_identity(connection)
        probe_persisted = await persisted_summary(
            connection,
            quoted_table,
        )
        metrics["chunk_call_probe"] = {
            "affected_rows": probe_affected,
            "persisted": probe_persisted,
            "call_count": len(probe_latencies_ns),
            "latency_seconds": latency_summary(probe_latencies_ns),
            "physical_identity_stable": (
                transaction_identity == original_identity
                and probe_identity == original_identity
            ),
        }
        metrics["post_load_smoke"] = int(await scalar(connection, "SELECT 1")) == 1
        pool_stats = await connection.pool_stats()
        metrics["pool"] = {
            "maximum_size": int(pool_stats["max_size"]),
            "connections": int(pool_stats["connections"]),
            "final_active_connections": int(pool_stats["active_connections"]),
        }
    except Exception as error:
        error_type = type(error).__name__
        errors.append(error_type)
        if error_type == "OperationTimeoutError":
            timed_out += 1
            metrics["timed_out"] = timed_out
    finally:
        monitor_stop.set()
        monitor_results = await asyncio.gather(
            *(task for task in (ticker_task, sampler_task) if task is not None),
            return_exceptions=True,
        )
        for result in monitor_results:
            if isinstance(result, BaseException):
                errors.append(type(result).__name__)

        if rss_baseline:
            gc.collect()
            rss_final = process.memory_info().rss
            rss_peak = max(
                int(monitor_state["peak_rss_bytes"]),
                rss_final,
            )
            metrics["resources"] = {
                "rss_baseline_bytes": rss_baseline,
                "rss_peak_bytes": rss_peak,
                "rss_final_bytes": rss_final,
                "rss_growth_bytes": max(0, rss_peak - rss_baseline),
                "rss_growth_limit_bytes": rss_growth_limit_bytes,
                "event_loop_ticks": int(monitor_state["ticks"]),
                "maximum_event_loop_stall_seconds": float(
                    monitor_state["maximum_stall_seconds"]
                ),
                "event_loop_stall_limit_seconds": (event_loop_stall_limit_seconds),
                "maximum_sql_sessions": int(monitor_state["maximum_sql_sessions"]),
                "sampler_iterations": int(monitor_state["sampler_iterations"]),
            }

        try:
            await connection.disconnect()
        except Exception as error:
            errors.append(type(error).__name__)
        try:
            metrics["teardown_sessions"] = await wait_for_zero_sessions(
                observer,
                application_name,
            )
        except Exception as error:
            errors.append(type(error).__name__)
        if created:
            try:
                await observer.execute(f"DROP TABLE IF EXISTS {quoted_table}")
            except Exception as error:
                errors.append(type(error).__name__)
        rows = None

    violations = profile_violations(
        metrics,
        rss_growth_limit_bytes=rss_growth_limit_bytes,
        event_loop_stall_limit_seconds=event_loop_stall_limit_seconds,
    )
    metrics["violations"] = violations
    metrics["status"] = "passed" if not violations else "failed"
    return metrics


async def run_gate(args: argparse.Namespace) -> int:
    source_sha = git_head()
    evidence: dict[str, object] = {
        "schema_version": 1,
        "source_sha": source_sha,
        "source_worktree_dirty": worktree_is_dirty(),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "runtime": runtime_metadata(),
        "configuration": {
            "profiles": [
                {
                    "rows": profile.rows,
                    "chunk_size": profile.chunk_size,
                }
                for profile in args.profiles
            ],
            "allow_extended": args.allow_extended,
            "rss_growth_limit_bytes": args.rss_growth_limit_bytes,
            "event_loop_stall_limit_seconds": (args.event_loop_stall_limit_seconds),
            "operation_timeout_seconds": args.operation_timeout_seconds,
            "pool_max_size": 1,
            "input_model": "concrete_list",
            "percentile_method": "nearest_rank",
            "latency_semantics": "one_chunk_transaction_calls",
        },
        "profiles": [],
    }
    atomic_write(args.metrics_output, evidence)
    failure_type: str | None = None
    observer: Connection | None = None
    try:
        if not hasattr(Connection, "native_bulk_insert"):
            raise RuntimeError("native bulk API is unavailable")
        settings = SqlAuthSettings.from_env()
        observer = settings.connection(
            application_name=f"fastmssql_native_observer_{uuid4().hex[:12]}",
            operation_timeout_seconds=args.operation_timeout_seconds,
            observer=True,
        )
        await observer.connect()
        profiles: list[dict[str, object]] = []
        for profile in args.profiles:
            print(
                "[native-bulk-stress] starting "
                f"{profile.rows:,} rows with chunk size "
                f"{profile.chunk_size:,}",
                flush=True,
            )
            metrics = await run_profile(
                settings,
                observer,
                profile,
                rss_growth_limit_bytes=args.rss_growth_limit_bytes,
                event_loop_stall_limit_seconds=(args.event_loop_stall_limit_seconds),
                operation_timeout_seconds=args.operation_timeout_seconds,
            )
            profiles.append(metrics)
            evidence["profiles"] = profiles
            atomic_write(args.metrics_output, evidence)
            primary = metrics["primary_single_call"]
            throughput = primary["throughput_rows_per_second"]
            throughput_text = (
                "unavailable" if throughput is None else f"{throughput:.2f} rows/s"
            )
            print(
                f"[native-bulk-stress] {metrics['status']} "
                f"{profile.rows:,}: {throughput_text}",
                flush=True,
            )
        evidence["status"] = (
            "passed"
            if all(profile["status"] == "passed" for profile in profiles)
            else "failed"
        )
        if evidence["status"] != "passed":
            failure_type = "InvariantViolation"
    except Exception as error:
        failure_type = type(error).__name__
        evidence["status"] = "failed"
        evidence["failure"] = {
            "reason": "native_bulk_stress_failed",
            "type": failure_type,
        }
    finally:
        if observer is not None:
            try:
                await observer.disconnect()
            except Exception as error:
                failure_type = failure_type or type(error).__name__
                evidence["status"] = "failed"
                evidence["failure"] = {
                    "reason": "observer_disconnect_failed",
                    "type": type(error).__name__,
                }

    atomic_write(args.metrics_output, evidence)
    if evidence["status"] != "passed":
        print(
            "[native-bulk-stress] failed; "
            f"type={failure_type or 'InvariantViolation'}; "
            f"evidence={args.metrics_output.name}",
            file=sys.stderr,
        )
    return 0 if evidence["status"] == "passed" else 1


def main() -> int:
    return asyncio.run(run_gate(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
