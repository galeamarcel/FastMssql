"""Worker-local application interfaces for the production framework matrix.

The executable factories and routes are introduced by the next TDD task. The
shared lifecycle primitive is intentionally small and keeps connection
construction outside module import.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import NoReturn

from fastmssql import (
    Connection,
    LifecycleConfig,
    OperationMetricsConfig,
    PoolConfig,
    TimeoutConfig,
)


def _current_pid() -> int:
    return os.getpid()


@dataclass(frozen=True)
class WorkerDriverConfig:
    """Driver options supplied only after the server has spawned its worker."""

    pool: PoolConfig
    lifecycle: LifecycleConfig
    timeouts: TimeoutConfig
    operation_metrics: OperationMetricsConfig


@dataclass
class WorkerLifecycle:
    """Own one already-constructed connection inside one worker process."""

    connection: Connection
    record_directory: Path
    pid: int = field(default_factory=_current_pid)

    def _record(self, phase: str) -> None:
        destination = self.record_directory / f"{phase}-{self.pid}.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"phase": phase, "pid": self.pid}, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, destination)

    async def start(self) -> None:
        await self.connection.connect(validate=True)
        self._record("ready")

    async def stop(self) -> None:
        await self.connection.disconnect()
        self._record("shutdown")


@asynccontextmanager
async def worker_lifespan(
    lifecycle: WorkerLifecycle,
) -> AsyncIterator[WorkerLifecycle]:
    """Connect before readiness and disconnect before shutdown evidence."""

    await lifecycle.start()
    try:
        yield lifecycle
    finally:
        await lifecycle.stop()


def _not_implemented(factory: str) -> NoReturn:
    raise RuntimeError(f"{factory} lifecycle is not implemented")


def create_fastapi_app() -> NoReturn:
    """Create the native FastAPI application inside a spawned worker."""

    return _not_implemented("FastAPI")


def create_flask_app() -> NoReturn:
    """Create the Flask WSGI application inside a forked worker."""

    return _not_implemented("Flask WSGI")


def create_adapted_flask_app() -> NoReturn:
    """Create the Flask application adapted to ASGI inside its worker."""

    return _not_implemented("Flask ASGI")
