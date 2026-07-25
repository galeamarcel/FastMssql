#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import gc
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from fastmssql import Connection, PoolConfig, SslConfig, Transaction
import psutil


MAX_TRANSACTIONS = 99_999
MAX_CONCURRENCY = 500


@dataclass(frozen=True)
class Profile:
    transactions: int
    concurrency: int


@dataclass(frozen=True, repr=False)
class SqlAuthSettings:
    host: str
    port: int
    database: str
    username: str
    password: str

    @classmethod
    def from_env(cls) -> SqlAuthSettings:
        required = {
            "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD": os.getenv(
                "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD", ""
            )
        }
        missing = sorted(name for name, value in required.items() if not value)
        if missing:
            raise RuntimeError(
                f"missing required environment variable(s): {', '.join(missing)}"
            )
        return cls(
            host=os.getenv("FASTMSSQL_SQL_AUTH_HOST", "127.0.0.1"),
            port=int(os.getenv("FASTMSSQL_SQL_AUTH_PORT", "14334")),
            database=os.getenv(
                "FASTMSSQL_SQL_AUTH_DATABASE", "fastmssql_validation"
            ),
            username=os.getenv(
                "FASTMSSQL_SQL_AUTH_OWNER_USER", "fastmssql_owner"
            ),
            password=required["FASTMSSQL_SQL_AUTH_OWNER_PASSWORD"],
        )

    def connection(self, *, application_name: str, max_size: int) -> Connection:
        return Connection(
            server=self.host,
            port=self.port,
            database=self.database,
            username=self.username,
            password=self.password,
            ssl_config=SslConfig.development(),
            pool_config=PoolConfig(
                max_size=max_size,
                min_idle=1,
                max_lifetime_secs=None,
                idle_timeout_secs=None,
                connection_timeout_secs=5,
                retry_connection=False,
            ),
            application_name=application_name,
        )

    def transaction(self, *, application_name: str) -> Transaction:
        return Transaction(
            server=self.host,
            port=self.port,
            database=self.database,
            username=self.username,
            password=self.password,
            ssl_config=SslConfig.development(),
            application_name=application_name,
        )


def parse_profiles(value: str) -> tuple[Profile, ...]:
    profiles: list[Profile] = []
    for raw_profile in value.split(","):
        parts = raw_profile.strip().split(":")
        if len(parts) != 2:
            raise argparse.ArgumentTypeError(
                f"invalid profile {raw_profile!r}; expected TRANSACTIONS:CONCURRENCY"
            )
        try:
            transactions = int(parts[0].replace("_", ""))
            concurrency = int(parts[1].replace("_", ""))
        except ValueError as error:
            raise argparse.ArgumentTypeError(
                f"invalid numeric profile {raw_profile!r}"
            ) from error
        if not 1 <= transactions <= MAX_TRANSACTIONS:
            raise argparse.ArgumentTypeError(
                "transactions must be between 1 and 99,999"
            )
        if not 1 <= concurrency <= MAX_CONCURRENCY:
            raise argparse.ArgumentTypeError(
                "concurrency must be between 1 and 500"
            )
        if concurrency > transactions:
            raise argparse.ArgumentTypeError(
                "concurrency cannot exceed transaction count"
            )
        profiles.append(Profile(transactions, concurrency))
    if not profiles:
        raise argparse.ArgumentTypeError("at least one profile is required")
    return tuple(profiles)


def quote_identifier(value: str) -> str:
    if not value.replace("_", "").isalnum() or not value[0].isalpha():
        raise ValueError(f"unsafe generated identifier {value!r}")
    return f"[{value}]"


async def scalar(client, sql: str, parameters=None):
    result = await client.query(sql, parameters)
    row = result.fetchone()
    if row is None or len(row) != 1:
        raise AssertionError(f"expected one scalar row, got {result.len()} rows")
    return row[0]


async def event_loop_ticker(stop: asyncio.Event) -> int:
    ticks = 0
    while not stop.is_set():
        ticks += 1
        await asyncio.sleep(0.01)
    return ticks


async def application_session_count(
    observer: Connection, application_name: str
) -> int:
    return await scalar(
        observer,
        """
        SELECT COUNT(*)
        FROM sys.dm_exec_sessions
        WHERE program_name = @P1
        """,
        [application_name],
    )


async def wait_for_no_sessions(
    observer: Connection,
    application_name: str,
    *,
    timeout: float = 10.0,
) -> None:
    deadline = time.monotonic() + timeout
    observed = -1
    while time.monotonic() < deadline:
        observed = await application_session_count(observer, application_name)
        if observed == 0:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(
        f"{observed} SQL session(s) remained for {application_name!r}"
    )


async def run_profile(
    settings: SqlAuthSettings,
    profile: Profile,
    *,
    connection_strategy: str,
    pool_size: int,
    rss_growth_limit_bytes: int,
) -> dict[str, object]:
    suffix = uuid4().hex[:12]
    raw_table = f"stress_transactions_{suffix}"
    table = quote_identifier(raw_table)
    application_name = f"fastmssql_tx_stress_{suffix}"
    observer = settings.connection(
        application_name=f"{application_name}_observer",
        max_size=2,
    )
    shared_pool = (
        settings.connection(
            application_name=application_name,
            max_size=pool_size,
        )
        if connection_strategy == "pooled"
        else None
    )
    await observer.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value BIGINT NOT NULL)"
    )

    process = psutil.Process()
    gc.collect()
    rss_before = process.memory_info().rss
    next_value = 0
    claim_lock = asyncio.Lock()
    start_barrier = asyncio.Barrier(profile.concurrency)
    sampled_session_ids: set[int] = set()
    sample_limit = min(profile.transactions, profile.concurrency * 2)
    stop_ticker = asyncio.Event()
    ticker = asyncio.create_task(event_loop_ticker(stop_ticker))

    async def claim_value() -> int | None:
        nonlocal next_value
        async with claim_lock:
            if next_value >= profile.transactions:
                return None
            value = next_value
            next_value += 1
            return value

    async def execute_transaction(
        transaction: Transaction,
        value: int,
        *,
        synchronize: bool,
    ) -> None:
        await transaction.begin()
        if synchronize:
            await start_barrier.wait()
        affected = await transaction.execute(
            f"INSERT INTO {table} (id, value) VALUES (@P1, @P2)",
            [value, value * 10],
        )
        if affected != 1:
            raise AssertionError(
                f"transaction {value} affected {affected} rows"
            )
        if value < sample_limit:
            sampled_session_ids.add(
                await scalar(transaction, "SELECT @@SPID")
            )
        if value % 2 == 0:
            await transaction.commit()
        else:
            await transaction.rollback()

    async def persistent_worker() -> None:
        transaction = settings.transaction(application_name=application_name)
        synchronize = True
        try:
            while True:
                value = await claim_value()
                if value is None:
                    return
                await execute_transaction(
                    transaction,
                    value,
                    synchronize=synchronize,
                )
                synchronize = False
        finally:
            await transaction.close()

    async def per_transaction_worker() -> None:
        while True:
            value = await claim_value()
            if value is None:
                return
            transaction = settings.transaction(
                application_name=application_name
            )
            try:
                await execute_transaction(
                    transaction,
                    value,
                    synchronize=value < profile.concurrency,
                )
            finally:
                await transaction.close()

    async def pooled_worker() -> None:
        if shared_pool is None:
            raise AssertionError("pooled worker requires a shared pool")
        transaction = shared_pool.transaction()
        try:
            while True:
                value = await claim_value()
                if value is None:
                    return
                # A full-concurrency barrier would deadlock when concurrency is
                # intentionally greater than pool_size: the checked-out leases
                # would wait for tasks that are correctly blocked in pool.get().
                await execute_transaction(
                    transaction,
                    value,
                    synchronize=False,
                )
        finally:
            await transaction.close()

    worker = {
        "persistent": persistent_worker,
        "per-transaction": per_transaction_worker,
        "pooled": pooled_worker,
    }[connection_strategy]

    started = time.monotonic()
    pool_stats: dict | None = None
    pooled_session_count: int | None = None
    try:
        async with asyncio.TaskGroup() as task_group:
            for _ in range(profile.concurrency):
                task_group.create_task(worker())
        elapsed = time.monotonic() - started
        if shared_pool is not None:
            pool_stats = await shared_pool.pool_stats()
            if pool_stats["connections"] > pool_size:
                raise AssertionError(
                    "shared transaction pool exceeded max_size: "
                    f"{pool_stats['connections']} > {pool_size}"
                )
            if pool_stats["active_connections"] != 0:
                raise AssertionError(
                    "transaction leases remained active after workers: "
                    f"{pool_stats['active_connections']}"
                )
            pooled_session_count = await application_session_count(
                observer,
                application_name,
            )
            if pooled_session_count > pool_size:
                raise AssertionError(
                    "SQL application sessions exceeded pool size: "
                    f"{pooled_session_count} > {pool_size}"
                )
            await shared_pool.disconnect()
    except BaseException:
        if shared_pool is not None:
            await shared_pool.disconnect()
        await observer.execute(f"DROP TABLE IF EXISTS {table}")
        await observer.disconnect()
        raise
    finally:
        stop_ticker.set()
        ticker_ticks = await ticker

    committed_values = tuple(range(0, profile.transactions, 2))
    expected_count = len(committed_values)
    expected_sum = sum(value * 10 for value in committed_values)
    try:
        actual_count = await scalar(observer, f"SELECT COUNT(*) FROM {table}")
        actual_sum = await scalar(observer, f"SELECT SUM(value) FROM {table}")
        rolled_back_rows = await scalar(
            observer,
            f"SELECT COUNT(*) FROM {table} WHERE id % 2 = 1",
        )
        if actual_count != expected_count:
            raise AssertionError(
                f"expected {expected_count} committed rows, got {actual_count}"
            )
        if actual_sum != expected_sum:
            raise AssertionError(
                f"expected committed sum {expected_sum}, got {actual_sum}"
            )
        if rolled_back_rows != 0:
            raise AssertionError(
                f"found {rolled_back_rows} rows from rolled-back transactions"
            )
        if connection_strategy == "pooled":
            if len(sampled_session_ids) > pool_size:
                raise AssertionError(
                    "distinct sampled SQL sessions exceeded pool size: "
                    f"{len(sampled_session_ids)} > {pool_size}"
                )
            minimum_sessions = min(
                pool_size,
                profile.concurrency,
                sample_limit,
            ) // 2
        else:
            minimum_sessions = min(profile.concurrency, sample_limit) // 2
        if len(sampled_session_ids) < minimum_sessions:
            raise AssertionError(
                "insufficient distinct SQL sessions: "
                f"{len(sampled_session_ids)} < {minimum_sessions}"
            )
        if ticker_ticks < 10:
            raise AssertionError(
                f"event loop advanced only {ticker_ticks} times"
            )
        if await scalar(observer, "SELECT 1") != 1:
            raise AssertionError("post-load SQL smoke query failed")
        await wait_for_no_sessions(observer, application_name)
        gc.collect()
        rss_after = process.memory_info().rss
        rss_growth = max(0, rss_after - rss_before)
        if rss_growth > rss_growth_limit_bytes:
            raise AssertionError(
                f"RSS grew by {rss_growth} bytes, limit is "
                f"{rss_growth_limit_bytes} bytes"
            )
        return {
            "transactions": profile.transactions,
            "concurrency": profile.concurrency,
            "connection_strategy": connection_strategy,
            "physical_connection_count": (
                pool_stats["connections"]
                if pool_stats is not None
                else (
                    profile.concurrency
                    if connection_strategy == "persistent"
                    else profile.transactions
                )
            ),
            "pool_size": (
                pool_size if connection_strategy == "pooled" else None
            ),
            "pooled_application_sessions": pooled_session_count,
            "committed": expected_count,
            "rolled_back": profile.transactions - expected_count,
            "elapsed_seconds": elapsed,
            "transactions_per_second": profile.transactions / elapsed,
            "distinct_sampled_sessions": len(sampled_session_ids),
            "event_loop_ticks": ticker_ticks,
            "rss_before_bytes": rss_before,
            "rss_after_bytes": rss_after,
            "rss_growth_bytes": rss_growth,
            "remaining_application_sessions": 0,
            "post_load_smoke": "PASS",
        }
    finally:
        await observer.execute(f"DROP TABLE IF EXISTS {table}")
        await observer.disconnect()


def write_metrics(path: Path, metrics: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"schema_version": 1, "profiles": metrics},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


async def run(args: argparse.Namespace) -> int:
    settings = SqlAuthSettings.from_env()
    metrics: list[dict[str, object]] = []
    for profile in args.profiles:
        print(
            f"[transaction-stress] starting {profile.transactions:,} "
            f"transactions at concurrency {profile.concurrency}",
            flush=True,
        )
        result = await run_profile(
            settings,
            profile,
            connection_strategy=args.connection_strategy,
            pool_size=args.pool_size,
            rss_growth_limit_bytes=args.rss_growth_limit_mb * 1024 * 1024,
        )
        metrics.append(result)
        write_metrics(args.metrics_output, metrics)
        print(
            f"[transaction-stress] passed {profile.transactions:,}: "
            f"{result['transactions_per_second']:.2f} tx/s, "
            f"{result['elapsed_seconds']:.2f}s",
            flush=True,
        )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run bounded FastMssql SQL-auth transaction stress profiles. "
            "Transaction totals may reach 99,999; concurrency is capped at 500."
        )
    )
    parser.add_argument(
        "--profiles",
        type=parse_profiles,
        required=True,
        help="comma-separated TRANSACTIONS:CONCURRENCY profiles",
    )
    parser.add_argument("--metrics-output", type=Path, required=True)
    parser.add_argument(
        "--connection-strategy",
        choices=("persistent", "per-transaction", "pooled"),
        default="persistent",
        help=(
            "reuse one direct Transaction per worker, open a direct connection "
            "per transaction, or lease transactions from one shared pool"
        ),
    )
    parser.add_argument(
        "--pool-size",
        type=int,
        default=100,
        help="shared pool max_size for the pooled strategy (1-500)",
    )
    parser.add_argument(
        "--rss-growth-limit-mb",
        type=int,
        default=512,
    )
    args = parser.parse_args()
    if not 1 <= args.pool_size <= MAX_CONCURRENCY:
        parser.error("--pool-size must be between 1 and 500")
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
