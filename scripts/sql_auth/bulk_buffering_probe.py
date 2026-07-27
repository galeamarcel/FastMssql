#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import gc
import json
import os
import time
from uuid import uuid4

from fastmssql import Connection, PoolConfig, SslConfig
import psutil


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
                "missing required environment setting FASTMSSQL_SQL_AUTH_OWNER_PASSWORD"
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

    def connection(self, *, application_name: str) -> Connection:
        return Connection(
            server=self.host,
            port=self.port,
            database=self.database,
            username=self.username,
            password=self.password,
            application_name=application_name,
            ssl_config=SslConfig.development(),
            pool_config=PoolConfig(
                max_size=1,
                min_idle=1,
                max_lifetime_secs=None,
                idle_timeout_secs=None,
                connection_timeout_secs=5,
                retry_connection=False,
            ),
        )


def quote_identifier_part(value: str) -> str:
    return f"[{value.replace(']', ']]')}]"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Measure compatibility bulk driver-memory growth and event-loop "
            "stall after the complete Python input list already exists."
        )
    )
    parser.add_argument(
        "--rows",
        type=int,
        choices=(1_000, 10_000, 99_999),
        required=True,
    )
    parser.add_argument("--payload-bytes", type=int, default=1024)
    parser.add_argument(
        "--rss-growth-limit-bytes",
        type=int,
        default=67_108_864,
    )
    parser.add_argument(
        "--event-loop-stall-limit-seconds",
        type=float,
        default=0.100,
    )
    args = parser.parse_args()
    if args.payload_bytes <= 0:
        parser.error("--payload-bytes must be positive")
    if args.rss_growth_limit_bytes < 0:
        parser.error("--rss-growth-limit-bytes must be non-negative")
    if args.event_loop_stall_limit_seconds <= 0:
        parser.error("--event-loop-stall-limit-seconds must be positive")
    return args


async def run_probe(args: argparse.Namespace) -> int:
    settings = SqlAuthSettings.from_env()
    application_name = f"fastmssql-bulk-buffer-{uuid4().hex}"
    connection = settings.connection(application_name=application_name)
    raw_table = f"fastmssql_bulk_buffer_{uuid4().hex}"
    qualified_table = f"dbo.{raw_table}"
    sql_table = f"[dbo].{quote_identifier_part(raw_table)}"
    process = psutil.Process()

    async with connection:
        await connection.execute(
            f"""
            CREATE TABLE {sql_table} (
                id INT PRIMARY KEY,
                payload VARCHAR(MAX) NOT NULL
            )
            """
        )
        try:
            payload = "x" * args.payload_bytes
            rows: list[list[object]] = [
                [row_index, payload] for row_index in range(args.rows)
            ]
            gc.collect()
            rss_baseline = process.memory_info().rss
            rss_peak = rss_baseline
            max_stall = 0.0
            stop_sampling = asyncio.Event()
            loop = asyncio.get_running_loop()

            async def sample_resources() -> None:
                nonlocal rss_peak, max_stall
                previous = loop.time()
                while not stop_sampling.is_set():
                    await asyncio.sleep(0.001)
                    current = loop.time()
                    max_stall = max(max_stall, current - previous - 0.001)
                    previous = current
                    rss_peak = max(rss_peak, process.memory_info().rss)

            sampler = asyncio.create_task(sample_resources())
            await asyncio.sleep(0)
            started = time.perf_counter()
            try:
                affected = await connection.bulk_insert(
                    qualified_table,
                    ["id", "payload"],
                    rows,
                )
            finally:
                stop_sampling.set()
                await sampler
            elapsed = time.perf_counter() - started
            rss_peak = max(rss_peak, process.memory_info().rss)
            persisted = (
                await connection.query(
                    f"SELECT COUNT_BIG(*) AS row_count FROM {sql_table}"
                )
            ).fetchone()["row_count"]
            post_smoke_value = (
                await connection.query("SELECT CAST(1 AS INT) AS value")
            ).fetchone()["value"]
        finally:
            await connection.execute(f"DROP TABLE IF EXISTS {sql_table}")

    rss_growth = max(0, rss_peak - rss_baseline)
    violations: list[str] = []
    if affected != args.rows or persisted != args.rows:
        violations.append("row_count_mismatch")
    if post_smoke_value != 1:
        violations.append("post_smoke_failed")
    if rss_growth > args.rss_growth_limit_bytes:
        violations.append("rss_growth_exceeded")
    if max_stall > args.event_loop_stall_limit_seconds:
        violations.append("event_loop_stall_exceeded")

    result = {
        "affected_rows": affected,
        "elapsed_seconds": elapsed,
        "max_event_loop_stall_seconds": max_stall,
        "payload_bytes": args.payload_bytes,
        "persisted_rows": persisted,
        "post_smoke_value": post_smoke_value,
        "row_count": args.rows,
        "rss_baseline_bytes": rss_baseline,
        "rss_growth_bytes": rss_growth,
        "rss_peak_bytes": rss_peak,
        "violations": violations,
    }
    print(json.dumps(result, sort_keys=True))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_probe(parse_args())))
