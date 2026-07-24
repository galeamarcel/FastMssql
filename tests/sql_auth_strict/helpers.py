from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import re
import time
from uuid import uuid4

from fastmssql import Connection


IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,120}$")


def unique_sql_name(prefix: str) -> str:
    candidate = f"{prefix}_{uuid4().hex[:12]}"
    if not IDENTIFIER.fullmatch(candidate):
        raise ValueError(f"unsafe generated identifier {candidate!r}")
    return candidate


def quote_identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(f"unsafe SQL identifier {value!r}")
    return f"[{value}]"


async def scalar(connection: Connection, sql: str, params=None):
    result = await connection.query(sql, params)
    row = result.fetchone()
    if row is None or len(row) != 1:
        raise AssertionError(f"expected one scalar row, got {result.len()} rows")
    return row[0]


@dataclass
class CleanupRegistry:
    connection: Connection
    statements: list[str] = field(default_factory=list)

    def add(self, statement: str) -> None:
        self.statements.append(statement)

    async def cleanup(self) -> None:
        failures: list[str] = []
        for statement in reversed(self.statements):
            try:
                await self.connection.execute(statement)
            except BaseException as error:
                failures.append(f"{type(error).__name__}: {error}")
        if failures:
            raise AssertionError("cleanup failed: " + " | ".join(failures))


async def timed(coro) -> tuple[float, object]:
    started = time.monotonic()
    result = await coro
    return time.monotonic() - started, result


async def event_loop_ticks(
    stop: asyncio.Event, interval: float = 0.02
) -> list[float]:
    ticks: list[float] = []
    while not stop.is_set():
        ticks.append(time.monotonic())
        await asyncio.sleep(interval)
    return ticks


def max_event_loop_gap(
    started: float,
    finished: float,
    ticks: list[float],
) -> float:
    timeline = [
        started,
        *(tick for tick in ticks if started < tick < finished),
        finished,
    ]
    return max(
        later - earlier for earlier, later in zip(timeline, timeline[1:])
    )


def assert_dedicated_container(name: str) -> None:
    if name != "fastmssql-sql-auth-dev":
        raise RuntimeError(f"refusing disruptive Docker action for {name!r}")
