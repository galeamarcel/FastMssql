"""Worker-local application interfaces for the production framework matrix.

The routes exercise one worker-owned FastMssql pool through real framework
servers. The shared lifecycle keeps connection construction outside module
import and every response is intentionally safe to persist as test evidence.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any, Literal, Protocol

from asgiref.wsgi import WsgiToAsgi
import fastmssql
from fastmssql import (
    Connection,
    LifecycleConfig,
    OperationMetricsConfig,
    PoolConfig,
    SslConfig,
    TimeoutConfig,
)
from fastapi import FastAPI, HTTPException, Path as PathParameter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from flask import Flask, request as flask_request
from werkzeug.exceptions import HTTPException as WerkzeugHttpException


COMMON_ENVIRONMENT_KEYS = (
    "FASTMSSQL_FRAMEWORK_DATABASE_MODE",
    "FASTMSSQL_FRAMEWORK_WORKER_COUNT",
    "FASTMSSQL_FRAMEWORK_GLOBAL_CONNECTION_BUDGET",
    "FASTMSSQL_FRAMEWORK_APPLICATION_NAME",
    "FASTMSSQL_FRAMEWORK_RUN_ID",
    "FASTMSSQL_FRAMEWORK_RUN_ROOT",
    "FASTMSSQL_FRAMEWORK_ARTIFACT_DIR",
    "FASTMSSQL_FRAMEWORK_TABLE",
    "FASTMSSQL_FRAMEWORK_SQL_DELAY_MS",
    "FASTMSSQL_FRAMEWORK_ACQUIRE_TIMEOUT_MS",
    "FASTMSSQL_FRAMEWORK_CANDIDATE_SHA",
    "FASTMSSQL_FRAMEWORK_WHEEL_FILENAME",
    "FASTMSSQL_FRAMEWORK_WHEEL_SHA256",
)
SQL_AUTH_ENVIRONMENT_KEYS = (
    "FASTMSSQL_SQL_AUTH_HOST",
    "FASTMSSQL_SQL_AUTH_PORT",
    "FASTMSSQL_SQL_AUTH_DATABASE",
    "FASTMSSQL_SQL_AUTH_OWNER_USER",
    "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD",
)
WORKER_COUNTS = frozenset({1, 2, 4, 8})
SQL_DELAY_MILLISECONDS = frozenset({0, 50, 100, 200, 250, 500, 1_000, 2_000, 5_000})
ACQUIRE_TIMEOUT_MILLISECONDS = frozenset({100, 250, 500, 1_000, 5_000})
SAFE_RUN_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
SAFE_SQL_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
SAFE_SERVER = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:[\]_-]{0,252}")
SAFE_CANCEL_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
WHEEL_FILENAME_PATTERN = re.compile(r"fastmssql-[0-9]+\.[0-9]+\.[0-9]+-.+\.whl")
ADMITTED_WAITER_RATIO = 1
STREAM_BUFFER_ROWS = 8
MAX_STREAM_ROWS = 10_000
MAX_SQL_SERVER_APPLICATION_NAME = 128
MIN_SQL_BIGINT = -(2**63)
MAX_SQL_BIGINT = 2**63 - 1
SIGNED_DECIMAL_INTEGER = re.compile(r"-?[0-9]+")
LOGGER = logging.getLogger("fastmssql.production_framework")

WAIT_PREFIX_BY_MILLISECONDS = {
    0: "",
    50: "WAITFOR DELAY '00:00:00.050'; ",
    100: "WAITFOR DELAY '00:00:00.100'; ",
    200: "WAITFOR DELAY '00:00:00.200'; ",
    250: "WAITFOR DELAY '00:00:00.250'; ",
    500: "WAITFOR DELAY '00:00:00.500'; ",
    1_000: "WAITFOR DELAY '00:00:01.000'; ",
    2_000: "WAITFOR DELAY '00:00:02.000'; ",
    5_000: "WAITFOR DELAY '00:00:05.000'; ",
}

PRINCIPAL_SQL = (
    "SELECT CAST(SUSER_SNAME() AS NVARCHAR(128)) AS principal, "
    "CAST(APP_NAME() AS NVARCHAR(128)) AS application_name, "
    "@@SPID AS session_id"
)
VALUE_SQL = "SELECT CAST(@P1 AS BIGINT) AS value, @@SPID AS session_id"
SET_CONTEXT_INFO_SQL = "SET CONTEXT_INFO @P1"
CANCEL_WAIT_SQL = (
    "WAITFOR DELAY '00:00:05.000'; SELECT @@SPID AS session_id"
)
STREAM_SQL = """
WITH generated AS (
    SELECT CAST(1 AS BIGINT) AS value
    UNION ALL
    SELECT value + 1 FROM generated WHERE value < @P1
)
SELECT value FROM generated ORDER BY value OPTION (MAXRECURSION 0)
""".strip()
STREAM_CONTEXT_SQL = (
    "SET CONTEXT_INFO @P1;\n" + STREAM_SQL.replace("@P1", "@P2")
)
ERROR_SQL = "SELECT value FROM dbo.fastmssql_framework_intentional_missing_table"


def _bounded_sql_bigint(
    raw_value: str,
    *,
    minimum: int = MIN_SQL_BIGINT,
) -> int | None:
    if (
        len(raw_value) > len(str(MIN_SQL_BIGINT))
        or SIGNED_DECIMAL_INTEGER.fullmatch(raw_value) is None
    ):
        return None
    value = int(raw_value)
    if value < minimum or value > MAX_SQL_BIGINT:
        return None
    return value


class ConfigurationError(ValueError):
    """A privacy-safe failure in worker configuration."""


class DatabaseMode(str, Enum):
    SQL_AUTH = "sql_auth"
    OFFLINE = "offline"


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CandidateProvenance:
    git_sha: str
    wheel_filename: str
    wheel_sha256: str


class WorkerState(str, Enum):
    NEW = "new"
    STARTING = "starting"
    READY = "ready"
    STOPPING = "stopping"
    CLOSED = "closed"
    FAILED = "failed"


class WorkerLifecycleError(RuntimeError):
    """A deterministic worker-local lifecycle violation."""


class DriverConnection(Protocol):
    async def connect(self, *, validate: bool = True) -> None: ...

    async def disconnect(self) -> None: ...

    async def query(
        self,
        sql: str,
        params: list[object] | None = None,
    ) -> Any: ...

    async def pool_stats(self) -> dict[str, object]: ...

    async def operation_stats(self) -> dict[str, object]: ...

    def transaction(self) -> Any: ...

    async def stream(
        self,
        sql: str,
        params: list[object] | None = None,
        *,
        buffer_size: int,
    ) -> Any: ...


class ConnectionFactory(Protocol):
    def __call__(
        self,
        config: WorkerConfig,
        pid: int,
    ) -> DriverConnection: ...


class AsgiApplication(Protocol):
    async def __call__(
        self,
        scope: dict[str, Any],
        receive,
        send,
    ) -> None: ...


def _required(environment: Mapping[str, str], name: str) -> str:
    try:
        value = environment[name]
    except KeyError:
        raise ConfigurationError(
            f"missing required environment setting {name}"
        ) from None
    if not value or not value.strip():
        raise ConfigurationError(f"empty required environment setting {name}")
    return value


def _integer(
    environment: Mapping[str, str],
    name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = _required(environment, name)
    try:
        value = int(raw)
    except ValueError:
        raise ConfigurationError(
            f"environment setting {name} must be an integer"
        ) from None
    if minimum is not None and value < minimum:
        raise ConfigurationError(f"environment setting {name} is below its minimum")
    if maximum is not None and value > maximum:
        raise ConfigurationError(f"environment setting {name} exceeds its maximum")
    return value


def _identifier(
    environment: Mapping[str, str],
    name: str,
    pattern: re.Pattern[str],
) -> str:
    value = _required(environment, name)
    if pattern.fullmatch(value) is None:
        raise ConfigurationError(f"environment setting {name} is unsafe")
    return value


def _database_settings(
    environment: Mapping[str, str],
) -> DatabaseSettings:
    host = _required(environment, "FASTMSSQL_SQL_AUTH_HOST")
    if SAFE_SERVER.fullmatch(host) is None:
        raise ConfigurationError(
            "environment setting FASTMSSQL_SQL_AUTH_HOST is unsafe"
        )
    return DatabaseSettings(
        host=host,
        port=_integer(
            environment,
            "FASTMSSQL_SQL_AUTH_PORT",
            minimum=1,
            maximum=65_535,
        ),
        database=_identifier(
            environment,
            "FASTMSSQL_SQL_AUTH_DATABASE",
            SAFE_SQL_IDENTIFIER,
        ),
        username=_identifier(
            environment,
            "FASTMSSQL_SQL_AUTH_OWNER_USER",
            SAFE_SQL_IDENTIFIER,
        ),
        password=_required(
            environment,
            "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD",
        ),
    )


def _candidate_provenance(
    environment: Mapping[str, str],
) -> CandidateProvenance:
    git_sha = _required(
        environment,
        "FASTMSSQL_FRAMEWORK_CANDIDATE_SHA",
    )
    if SHA_PATTERN.fullmatch(git_sha) is None:
        raise ConfigurationError(
            "candidate SHA must be exactly 40 lowercase hexadecimal characters"
        )
    wheel_filename = _required(
        environment,
        "FASTMSSQL_FRAMEWORK_WHEEL_FILENAME",
    )
    if (
        Path(wheel_filename).name != wheel_filename
        or WHEEL_FILENAME_PATTERN.fullmatch(wheel_filename) is None
    ):
        raise ConfigurationError("wheel filename is invalid")
    wheel_sha256 = _required(
        environment,
        "FASTMSSQL_FRAMEWORK_WHEEL_SHA256",
    )
    if SHA256_PATTERN.fullmatch(wheel_sha256) is None:
        raise ConfigurationError(
            "wheel SHA-256 must be exactly 64 lowercase hexadecimal characters"
        )
    return CandidateProvenance(
        git_sha=git_sha,
        wheel_filename=wheel_filename,
        wheel_sha256=wheel_sha256,
    )


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    database_mode: DatabaseMode
    worker_count: int
    global_connection_budget: int
    pool_max_per_worker: int
    application_name: str
    run_id: str
    run_root: Path
    artifact_directory: Path
    table_name: str
    sql_delay_ms: int
    acquire_timeout_ms: int
    candidate: CandidateProvenance
    database: DatabaseSettings | None

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str],
    ) -> WorkerConfig:
        mode_name = _required(
            environment,
            "FASTMSSQL_FRAMEWORK_DATABASE_MODE",
        )
        try:
            database_mode = DatabaseMode(mode_name)
        except ValueError:
            raise ConfigurationError(
                "database mode must be sql_auth or offline"
            ) from None

        worker_count = _integer(
            environment,
            "FASTMSSQL_FRAMEWORK_WORKER_COUNT",
        )
        if worker_count not in WORKER_COUNTS:
            raise ConfigurationError("worker count must be one of 1, 2, 4, 8")
        global_budget = _integer(
            environment,
            "FASTMSSQL_FRAMEWORK_GLOBAL_CONNECTION_BUDGET",
            minimum=1,
        )
        if global_budget < worker_count:
            raise ConfigurationError(
                "global connection budget is smaller than worker count"
            )
        pool_max, remainder = divmod(global_budget, worker_count)
        if remainder:
            raise ConfigurationError(
                "global connection budget must divide worker count exactly"
            )

        run_root_raw = Path(_required(environment, "FASTMSSQL_FRAMEWORK_RUN_ROOT"))
        artifact_raw = Path(_required(environment, "FASTMSSQL_FRAMEWORK_ARTIFACT_DIR"))
        if not run_root_raw.is_absolute() or not artifact_raw.is_absolute():
            raise ConfigurationError("run root and artifact directory must be absolute")
        run_root = run_root_raw.resolve()
        artifact_directory = artifact_raw.resolve()
        if not artifact_directory.is_relative_to(run_root):
            raise ConfigurationError("artifact directory must be contained by run root")

        delay = _integer(
            environment,
            "FASTMSSQL_FRAMEWORK_SQL_DELAY_MS",
            minimum=0,
        )
        if delay not in SQL_DELAY_MILLISECONDS:
            raise ConfigurationError(
                "SQL delay must be selected from the closed allowlist"
            )
        acquire_timeout = _integer(
            environment,
            "FASTMSSQL_FRAMEWORK_ACQUIRE_TIMEOUT_MS",
            minimum=1,
        )
        if acquire_timeout not in ACQUIRE_TIMEOUT_MILLISECONDS:
            raise ConfigurationError(
                "acquire timeout must be selected from the closed allowlist"
            )

        if database_mode is DatabaseMode.SQL_AUTH:
            database = _database_settings(environment)
        else:
            configured_sql_auth = [
                name for name in SQL_AUTH_ENVIRONMENT_KEYS if name in environment
            ]
            if configured_sql_auth:
                raise ConfigurationError("offline mode forbids SQL-auth settings")
            database = None

        return cls(
            database_mode=database_mode,
            worker_count=worker_count,
            global_connection_budget=global_budget,
            pool_max_per_worker=pool_max,
            application_name=_identifier(
                environment,
                "FASTMSSQL_FRAMEWORK_APPLICATION_NAME",
                SAFE_RUN_IDENTIFIER,
            ),
            run_id=_identifier(
                environment,
                "FASTMSSQL_FRAMEWORK_RUN_ID",
                SAFE_RUN_IDENTIFIER,
            ),
            run_root=run_root,
            artifact_directory=artifact_directory,
            table_name=_identifier(
                environment,
                "FASTMSSQL_FRAMEWORK_TABLE",
                SAFE_SQL_IDENTIFIER,
            ),
            sql_delay_ms=delay,
            acquire_timeout_ms=acquire_timeout,
            candidate=_candidate_provenance(environment),
            database=database,
        )

    @classmethod
    def from_os_environment(cls) -> WorkerConfig:
        return cls.from_environment(os.environ)

    def public_record(self) -> dict[str, object]:
        return {
            "application_name": self.application_name,
            "candidate_sha": self.candidate.git_sha,
            "database_configured": self.database is not None,
            "database_mode": self.database_mode.value,
            "global_connection_budget": self.global_connection_budget,
            "pool_max_per_worker": self.pool_max_per_worker,
            "run_id": self.run_id,
            "sql_delay_ms": self.sql_delay_ms,
            "acquire_timeout_ms": self.acquire_timeout_ms,
            "table_name": self.table_name,
            "worker_count": self.worker_count,
            "wheel_filename": self.candidate.wheel_filename,
            "wheel_sha256": self.candidate.wheel_sha256,
        }


def package_record(config: WorkerConfig) -> dict[str, object]:
    """Return privacy-safe runtime and installed-candidate provenance."""

    import_location = fastmssql.__file__
    if import_location is None:
        raise WorkerLifecycleError("FastMssql import has no filesystem path")
    distribution = importlib.metadata.distribution("fastmssql")
    direct_url_text = distribution.read_text("direct_url.json")
    if direct_url_text is None:
        raise WorkerLifecycleError(
            "FastMssql distribution has no direct installation origin"
        )
    try:
        distribution_direct_url = json.loads(direct_url_text)
    except json.JSONDecodeError as error:
        raise WorkerLifecycleError(
            "FastMssql direct installation origin is invalid"
        ) from error
    if not isinstance(distribution_direct_url, dict):
        raise WorkerLifecycleError(
            "FastMssql direct installation origin must be an object"
        )
    cwd = Path.cwd().resolve()
    normalized_sys_path: list[str] = []
    for entry in sys.path:
        normalized = str(Path(entry or cwd).resolve())
        if normalized not in normalized_sys_path:
            normalized_sys_path.append(normalized)
    return {
        "candidate_sha": config.candidate.git_sha,
        "cwd": str(cwd),
        "distribution_direct_url": distribution_direct_url,
        "distribution_version": distribution.version,
        "fastmssql_import_path": str(Path(import_location).resolve()),
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "python_executable": sys.executable,
        "sys_path": normalized_sys_path,
        "wheel_filename": config.candidate.wheel_filename,
        "wheel_sha256": config.candidate.wheel_sha256,
    }


def _current_pid() -> int:
    return os.getpid()


@dataclass(frozen=True, slots=True)
class WorkerDriverConfig:
    """Driver options supplied only after the server has spawned its worker."""

    pool: PoolConfig
    lifecycle: LifecycleConfig
    timeouts: TimeoutConfig
    operation_metrics: OperationMetricsConfig


def _driver_config(config: WorkerConfig) -> WorkerDriverConfig:
    return WorkerDriverConfig(
        pool=PoolConfig(
            max_size=config.pool_max_per_worker,
            min_idle=0,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=None,
            retry_connection=False,
        ),
        lifecycle=LifecycleConfig(
            shutdown_timeout_secs=15,
            force_timeout_secs=5,
        ),
        timeouts=TimeoutConfig(
            connect_timeout_secs=10,
            acquire_timeout_secs=config.acquire_timeout_ms / 1_000,
            operation_timeout_secs=15,
            transaction_timeout_secs=20,
            rollback_timeout_secs=5,
        ),
        operation_metrics=OperationMetricsConfig(enabled=True),
    )


def _default_connection_factory(
    config: WorkerConfig,
    pid: int,
) -> Connection:
    database = config.database
    if database is None:
        raise WorkerLifecycleError(
            "SQL-auth connection requested while database mode is offline"
        )
    driver = _driver_config(config)
    return Connection(
        server=database.host,
        port=database.port,
        database=database.database,
        username=database.username,
        password=database.password,
        application_name=_worker_application_name(config, pid),
        ssl_config=SslConfig.development(),
        pool_config=driver.pool,
        lifecycle_config=driver.lifecycle,
        timeout_config=driver.timeouts,
        operation_metrics_config=driver.operation_metrics,
    )


def _worker_application_name(config: WorkerConfig, pid: int) -> str:
    application_name = f"{config.application_name}-{pid}"
    if len(application_name) > MAX_SQL_SERVER_APPLICATION_NAME:
        raise ConfigurationError(
            "worker application name exceeds SQL Server's 128-character limit"
        )
    return application_name


@dataclass
class WorkerLifecycle:
    """Construct and own one connection inside exactly one worker process."""

    config: WorkerConfig
    connection_factory: ConnectionFactory = _default_connection_factory
    pid: int = field(default_factory=_current_pid)
    process_started_monotonic: float = field(
        default_factory=time.monotonic,
        init=False,
    )
    state: WorkerState = field(default=WorkerState.NEW, init=False)
    connection: DriverConnection | None = field(
        default=None,
        init=False,
        repr=False,
    )
    pool_identity: str | None = field(default=None, init=False)
    pool_created_pid: int | None = field(default=None, init=False)
    pool_created_monotonic: float | None = field(default=None, init=False)
    pool_connected_monotonic: float | None = field(default=None, init=False)
    worker_application_name: str | None = field(default=None, init=False)

    def _write_atomic_record(
        self,
        destination: Path,
        payload: Mapping[str, object],
        *,
        record_name: str,
    ) -> None:
        if destination.exists():
            raise WorkerLifecycleError(
                f"{record_name} record already exists for worker PID {self.pid}"
            )
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.config.artifact_directory,
                prefix=f".{record_name}-{self.pid}-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(payload, temporary, sort_keys=True)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, destination)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _record(self, phase: str) -> None:
        destination = self.config.artifact_directory / f"{phase}-{self.pid}.json"
        self._write_atomic_record(
            destination,
            {
                **self.config.public_record(),
                "phase": phase,
                "pid": self.pid,
                "pool_connected_monotonic": self.pool_connected_monotonic,
                "pool_created_monotonic": self.pool_created_monotonic,
                "pool_created_pid": self.pool_created_pid,
                "pool_identity": self.pool_identity,
                "process_started_monotonic": self.process_started_monotonic,
                "worker_application_name": self.worker_application_name,
            },
            record_name=phase,
        )

    def record_transaction_phase(
        self,
        item_id: int,
        outcome: Literal["commit", "rollback"],
        transaction_phase: Literal["holding", "settled"],
    ) -> None:
        self.ensure_ready()
        destination = self.config.artifact_directory / (
            f"transaction-{transaction_phase}-{outcome}-{self.pid}-{item_id}.json"
        )
        context_token = f"transaction:{item_id}:{outcome}"
        self._write_atomic_record(
            destination,
            {
                **self.config.public_record(),
                "context_token_sha256": hashlib.sha256(
                    context_token.encode("ascii")
                ).hexdigest(),
                "item_id": item_id,
                "outcome": outcome,
                "phase": "transaction",
                "pid": self.pid,
                "transaction_phase": transaction_phase,
                "worker_application_name": self.worker_application_name,
            },
            record_name=f"transaction-{transaction_phase}",
        )

    def _require_worker_pid(self) -> None:
        current_pid = os.getpid()
        if current_pid != self.pid:
            raise WorkerLifecycleError(
                "worker lifecycle was created before the current process"
            )

    async def start(self) -> None:
        self._require_worker_pid()
        if self.state is not WorkerState.NEW:
            raise WorkerLifecycleError(
                f"worker lifecycle is already {self.state.value}"
            )
        self.state = WorkerState.STARTING
        try:
            self.worker_application_name = _worker_application_name(
                self.config,
                self.pid,
            )
            if self.config.database_mode is DatabaseMode.SQL_AUTH:
                self.pool_created_pid = os.getpid()
                self.pool_created_monotonic = time.monotonic()
                connection = self.connection_factory(self.config, self.pid)
                self.connection = connection
                self.pool_identity = f"{self.pid}-{id(connection):x}"
                await connection.connect(validate=True)
                self.pool_connected_monotonic = time.monotonic()
            else:
                self.pool_identity = f"{self.pid}-offline"
            self._record("ready")
        except BaseException as startup_error:
            self.state = WorkerState.FAILED
            if self.connection is not None:
                try:
                    await self.connection.disconnect()
                except BaseException as cleanup_error:
                    raise BaseExceptionGroup(
                        "worker startup and cleanup both failed",
                        [startup_error, cleanup_error],
                    ) from None
            raise
        self.state = WorkerState.READY

    async def stop(self) -> None:
        self._require_worker_pid()
        if self.state is WorkerState.CLOSED:
            return
        if self.state in {WorkerState.NEW, WorkerState.FAILED}:
            return
        if self.state is not WorkerState.READY:
            raise WorkerLifecycleError(
                f"worker lifecycle cannot stop from {self.state.value}"
            )
        self.state = WorkerState.STOPPING
        try:
            if self.connection is not None:
                await self.connection.disconnect()
            self._record("shutdown")
        except BaseException:
            self.state = WorkerState.FAILED
            raise
        self.state = WorkerState.CLOSED

    def ensure_ready(self) -> None:
        if self.state is not WorkerState.READY:
            raise WorkerLifecycleError("worker lifecycle is not ready")

    def require_connection(self) -> DriverConnection:
        self.ensure_ready()
        if self.connection is None:
            raise WorkerLifecycleError(
                "SQL-auth route is unavailable in offline mode"
            )
        return self.connection

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[WorkerLifecycle]:
        await self.start()
        try:
            yield self
        finally:
            await self.stop()


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


def _result_row(result: Any) -> Any:
    row = result.fetchone()
    if row is None:
        raise WorkerLifecycleError("SQL query returned no row")
    return row


def _stream_row_record(row: Any, column_names: tuple[str, ...]) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    to_dict = getattr(row, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        if isinstance(converted, dict):
            return converted
    return {name: row[index] for index, name in enumerate(column_names)}


async def _cancel_and_settle(task: asyncio.Future[Any]) -> None:
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return


async def _wait_for_disconnect(request: Request) -> None:
    while True:
        if await request.is_disconnected():
            return
        await asyncio.sleep(0.01)


@dataclass(slots=True)
class WorkerRouteState:
    """Common SQL behavior backed by exactly one worker-local pool."""

    config: WorkerConfig
    lifecycle: WorkerLifecycle
    admission: asyncio.BoundedSemaphore = field(init=False, repr=False)
    admission_active: int = field(default=0, init=False)
    admission_rejected: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.admission = asyncio.BoundedSemaphore(self.admission_capacity)

    @property
    def application_name(self) -> str:
        application_name = self.lifecycle.worker_application_name
        if application_name is None:
            raise WorkerLifecycleError("worker application name is unavailable")
        return application_name

    @property
    def admission_capacity(self) -> int:
        waiter_capacity = (
            self.config.pool_max_per_worker * ADMITTED_WAITER_RATIO
        )
        return self.config.pool_max_per_worker + waiter_capacity

    def ready_payload(self) -> dict[str, object]:
        self.lifecycle.ensure_ready()
        return {
            "application_name": self.application_name,
            "pid": self.lifecycle.pid,
            "pool_identity": self.lifecycle.pool_identity,
            "state": self.lifecycle.state.value,
        }

    async def principal_payload(self) -> dict[str, object]:
        connection = self.lifecycle.require_connection()
        row = _result_row(await connection.query(PRINCIPAL_SQL))
        database_application_name = str(row["application_name"])
        if database_application_name != self.application_name:
            raise WorkerLifecycleError(
                "SQL session application name does not match its worker"
            )
        return {
            "application_name": database_application_name,
            "pid": self.lifecycle.pid,
            "principal": str(row["principal"]),
            "session_id": int(row["session_id"]),
        }

    async def value_payload(self, value: int) -> dict[str, int]:
        connection = self.lifecycle.require_connection()
        row = _result_row(await connection.query(VALUE_SQL, [value]))
        return {
            "session_id": int(row["session_id"]),
            "value": int(row["value"]),
        }

    async def wait_payload(
        self,
        value: int,
        *,
        context_token: str | None = None,
    ) -> dict[str, int]:
        connection = self.lifecycle.require_connection()
        delay_prefix = WAIT_PREFIX_BY_MILLISECONDS[self.config.sql_delay_ms]
        wait_sql = f"{delay_prefix}{VALUE_SQL}"
        if context_token is None:
            row = _result_row(await connection.query(wait_sql, [value]))
        else:
            if SAFE_CANCEL_TOKEN.fullmatch(context_token) is None:
                raise HTTPException(status_code=422, detail="invalid context token")
            async with connection.transaction() as transaction:
                await transaction.execute(
                    SET_CONTEXT_INFO_SQL,
                    [context_token.encode("ascii")],
                )
                row = _result_row(await transaction.query(wait_sql, [value]))
        return {
            "delay_ms": self.config.sql_delay_ms,
            "session_id": int(row["session_id"]),
            "value": int(row["value"]),
        }

    async def pool_payload(self) -> dict[str, object]:
        connection = self.lifecycle.require_connection()
        pool = await connection.pool_stats()
        operations = await connection.operation_stats()
        return {
            "admission": {
                "active": self.admission_active,
                "capacity": self.admission_capacity,
                "rejected": self.admission_rejected,
            },
            "application_name": self.application_name,
            "operations": dict(operations),
            "pid": self.lifecycle.pid,
            "pool": dict(pool),
        }

    async def transaction_payload(
        self,
        item_id: int,
        outcome: Literal["commit", "rollback"],
    ) -> dict[str, object]:
        connection = self.lifecycle.require_connection()
        delay_prefix = WAIT_PREFIX_BY_MILLISECONDS[self.config.sql_delay_ms]
        table_name = self.config.table_name
        async with connection.transaction() as transaction:
            await transaction.execute(
                f"INSERT INTO {table_name} (id, value) VALUES (@P1, @P2)",
                [item_id, "transaction"],
            )
            context_token = f"transaction:{item_id}:{outcome}".encode("ascii")
            await transaction.execute(
                SET_CONTEXT_INFO_SQL,
                [context_token],
            )
            self.lifecycle.record_transaction_phase(
                item_id,
                outcome,
                "holding",
            )
            row = _result_row(
                await transaction.query(
                    f"{delay_prefix}SELECT @@SPID AS session_id",
                )
            )
            if outcome == "commit":
                await transaction.commit()
            else:
                await transaction.rollback()
            self.lifecycle.record_transaction_phase(
                item_id,
                outcome,
                "settled",
            )
        return {
            "item_id": item_id,
            "outcome": outcome,
            "session_id": int(row["session_id"]),
        }

    async def cancel_payload(
        self,
        request: Request,
        token: str,
    ) -> dict[str, object]:
        if SAFE_CANCEL_TOKEN.fullmatch(token) is None:
            raise HTTPException(status_code=422, detail="invalid cancellation token")
        connection = self.lifecycle.require_connection()

        async def identified_wait():
            async with connection.transaction() as transaction:
                await transaction.execute(
                    SET_CONTEXT_INFO_SQL,
                    [token.encode("ascii")],
                )
                return await transaction.query(CANCEL_WAIT_SQL)

        query_task = asyncio.create_task(identified_wait())
        disconnect_task = asyncio.create_task(_wait_for_disconnect(request))
        try:
            completed, _ = await asyncio.wait(
                {query_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if query_task in completed:
                await _cancel_and_settle(disconnect_task)
                row = _result_row(await query_task)
                return {
                    "cancelled": False,
                    "session_id": int(row["session_id"]),
                }
            await disconnect_task
            await _cancel_and_settle(query_task)
            return {"cancelled": True}
        finally:
            if not query_task.done():
                await _cancel_and_settle(query_task)
            if not disconnect_task.done():
                await _cancel_and_settle(disconnect_task)

    async def admitted_value_payload(
        self,
        value: int,
    ) -> dict[str, int] | None:
        if self.admission.locked():
            self.admission_rejected += 1
            return None
        await self.admission.acquire()
        self.admission_active += 1
        try:
            payload = await self.wait_payload(value)
            return {
                "session_id": payload["session_id"],
                "value": payload["value"],
            }
        finally:
            self.admission_active -= 1
            self.admission.release()

    async def stream_records(
        self,
        rows: int,
        token: str | None = None,
    ) -> AsyncIterator[str]:
        connection = self.lifecycle.require_connection()
        if token is None:
            sql = STREAM_SQL
            parameters: list[object] = [rows]
        else:
            if SAFE_CANCEL_TOKEN.fullmatch(token) is None:
                raise HTTPException(status_code=422, detail="invalid stream token")
            sql = STREAM_CONTEXT_SQL
            parameters = [token.encode("ascii"), rows]
        stream = await connection.stream(
            sql,
            parameters,
            buffer_size=STREAM_BUFFER_ROWS,
        )
        async with stream:
            async for result_set in stream:
                async for row in result_set:
                    payload = {
                        "result_set": int(result_set.index),
                        "row": _stream_row_record(row, result_set.column_names),
                    }
                    yield json.dumps(
                        payload,
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ) + "\n"
                    await asyncio.sleep(0)

    async def error_probe(self) -> None:
        connection = self.lifecycle.require_connection()
        await connection.query(ERROR_SQL)
        raise WorkerLifecycleError("intentional SQL error route unexpectedly passed")

    async def loop_payload(self) -> dict[str, int]:
        value = await self.value_payload(17)
        return {
            "loop_id": id(asyncio.get_running_loop()),
            "value": value["value"],
        }

    async def gather_payload(self) -> dict[str, object]:
        values = list(range(4))
        sequential_started = time.perf_counter()
        sequential = [
            (await self.wait_payload(value))["value"] for value in values
        ]
        sequential_seconds = time.perf_counter() - sequential_started

        concurrent_started = time.perf_counter()
        concurrent_rows = await asyncio.gather(
            *(self.wait_payload(value) for value in values)
        )
        concurrent_seconds = time.perf_counter() - concurrent_started
        return {
            "concurrent": [row["value"] for row in concurrent_rows],
            "concurrent_seconds": concurrent_seconds,
            "sequential": sequential,
            "sequential_seconds": sequential_seconds,
        }


def _worker_config(
    environment: Mapping[str, str] | None,
) -> WorkerConfig:
    if environment is None:
        return WorkerConfig.from_os_environment()
    return WorkerConfig.from_environment(environment)


def create_fastapi_app(
    *,
    environment: Mapping[str, str] | None = None,
    connection_factory: ConnectionFactory = _default_connection_factory,
) -> FastAPI:
    """Create the native FastAPI application inside a spawned worker."""

    config = _worker_config(environment)
    lifecycle = WorkerLifecycle(
        config=config,
        connection_factory=connection_factory,
    )
    routes = WorkerRouteState(config=config, lifecycle=lifecycle)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.fastmssql_worker = lifecycle
        async with lifecycle.lifespan():
            yield

    application = FastAPI(lifespan=lifespan)
    application.state.fastmssql_worker = lifecycle

    @application.exception_handler(Exception)
    async def safe_internal_error(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        del request
        LOGGER.error(
            "production framework request failed: error_type=%s",
            type(error).__name__,
        )
        return JSONResponse(
            {"error": "internal_error"},
            status_code=500,
        )

    @application.get("/package")
    async def package_probe() -> dict[str, object]:
        lifecycle.ensure_ready()
        return package_record(config)

    @application.get("/ready")
    async def ready_probe() -> dict[str, object]:
        return routes.ready_payload()

    @application.get("/principal")
    async def principal_probe() -> dict[str, object]:
        return await routes.principal_payload()

    @application.get("/value/{value}")
    async def value_probe(
        value: int = PathParameter(ge=MIN_SQL_BIGINT, le=MAX_SQL_BIGINT),
    ) -> dict[str, int]:
        return await routes.value_payload(value)

    @application.get("/pool")
    async def pool_probe() -> dict[str, object]:
        return await routes.pool_payload()

    @application.get("/wait/{value}")
    async def wait_probe(
        request: Request,
        value: int = PathParameter(ge=MIN_SQL_BIGINT, le=MAX_SQL_BIGINT),
    ) -> dict[str, int]:
        return await routes.wait_payload(
            value,
            context_token=request.headers.get(
                "x-fastmssql-context-token"
            ),
        )

    @application.get("/cancel/{token}")
    async def cancel_probe(
        request: Request,
        token: str,
    ) -> dict[str, object]:
        return await routes.cancel_payload(request, token)

    @application.post("/transaction/{item_id}")
    async def transaction_probe(
        item_id: int = PathParameter(ge=1, le=MAX_SQL_BIGINT),
        outcome: Literal["commit", "rollback"] = Query(default="commit"),
    ) -> dict[str, object]:
        return await routes.transaction_payload(item_id, outcome)

    @application.get("/saturated/{value}")
    async def saturated_probe(
        value: int = PathParameter(ge=MIN_SQL_BIGINT, le=MAX_SQL_BIGINT),
    ):
        try:
            payload = await routes.admitted_value_payload(value)
        except fastmssql.OperationTimeoutError as error:
            if getattr(error, "phase", None) != "acquire":
                raise
            return JSONResponse(
                {
                    "error": "pool_acquire_timeout",
                    "operation": "query",
                    "phase": "acquire",
                    "retryable": bool(getattr(error, "retryable", False)),
                },
                status_code=504,
            )
        if payload is None:
            return JSONResponse(
                {"error": "saturated"},
                status_code=503,
            )
        return payload

    @application.get("/stream")
    async def stream_probe(
        rows: int = Query(default=1_000, ge=1, le=MAX_STREAM_ROWS),
        token: str | None = Query(default=None),
    ) -> StreamingResponse:
        if token is not None and SAFE_CANCEL_TOKEN.fullmatch(token) is None:
            raise HTTPException(status_code=422, detail="invalid stream token")
        return StreamingResponse(
            routes.stream_records(rows, token),
            media_type="application/x-ndjson",
        )

    @application.get("/error")
    async def error_probe() -> None:
        await routes.error_probe()

    return application


def create_flask_app(
    *,
    environment: Mapping[str, str] | None = None,
    connection_factory: ConnectionFactory = _default_connection_factory,
) -> Flask:
    """Create the Flask WSGI application inside a forked worker."""

    config = _worker_config(environment)
    application = Flask("fastmssql-production-framework")
    lifecycle = WorkerLifecycle(
        config=config,
        connection_factory=connection_factory,
    )
    routes = WorkerRouteState(config=config, lifecycle=lifecycle)
    application.extensions["fastmssql_worker"] = lifecycle

    @application.errorhandler(Exception)
    def safe_internal_error(error: Exception):
        if isinstance(error, WerkzeugHttpException):
            return error
        LOGGER.error(
            "production framework request failed: error_type=%s",
            type(error).__name__,
        )
        return {"error": "internal_error"}, 500

    @application.get("/package")
    def package_probe() -> dict[str, object]:
        lifecycle.ensure_ready()
        return package_record(config)

    @application.get("/ready")
    def ready_probe() -> dict[str, object]:
        return routes.ready_payload()

    @application.get("/principal")
    async def principal_probe() -> dict[str, object]:
        return await routes.principal_payload()

    @application.get("/value/<value>")
    async def value_probe(value: str):
        parsed = _bounded_sql_bigint(value)
        if parsed is None:
            return {"error": "invalid_bigint"}, 422
        return await routes.value_payload(parsed)

    @application.get("/pool")
    async def pool_probe() -> dict[str, object]:
        return await routes.pool_payload()

    @application.get("/wait/<value>")
    async def wait_probe(value: str):
        parsed = _bounded_sql_bigint(value)
        if parsed is None:
            return {"error": "invalid_bigint"}, 422
        return await routes.wait_payload(parsed)

    @application.post("/transaction/<item_id>")
    async def transaction_probe(item_id: str):
        parsed = _bounded_sql_bigint(item_id, minimum=1)
        if parsed is None:
            return {"error": "invalid_bigint"}, 422
        outcome = flask_request.args.get("outcome", "commit")
        if outcome not in {"commit", "rollback"}:
            return {"error": "invalid_outcome"}, 422
        return await routes.transaction_payload(parsed, outcome)

    @application.get("/loop")
    async def loop_probe() -> dict[str, int]:
        return await routes.loop_payload()

    @application.get("/gather")
    async def gather_probe() -> dict[str, object]:
        return await routes.gather_payload()

    @application.get("/error")
    async def error_probe() -> None:
        await routes.error_probe()

    return application


def flask_worker_lifecycle(application) -> WorkerLifecycle:
    extensions = getattr(application, "extensions", None)
    lifecycle = (
        extensions.get("fastmssql_worker") if isinstance(extensions, dict) else None
    )
    if not isinstance(lifecycle, WorkerLifecycle):
        raise WorkerLifecycleError("Gunicorn worker has no FastMssql lifecycle")
    return lifecycle


class LifespanWsgiToAsgi:
    """Own ASGI lifespan and delegate HTTP only to WsgiToAsgi."""

    def __init__(
        self,
        wsgi_application: Flask,
        lifecycle: WorkerLifecycle,
        adapter: AsgiApplication,
    ) -> None:
        self.wsgi_application = wsgi_application
        self.worker_lifecycle = lifecycle
        self.adapter = adapter

    async def _lifespan(self, receive, send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    await self.worker_lifecycle.start()
                except BaseException:
                    await send(
                        {
                            "type": "lifespan.startup.failed",
                            "message": "worker startup failed",
                        }
                    )
                    raise
                await send({"type": "lifespan.startup.complete"})
                continue
            if message["type"] == "lifespan.shutdown":
                try:
                    await self.worker_lifecycle.stop()
                except BaseException:
                    await send(
                        {
                            "type": "lifespan.shutdown.failed",
                            "message": "worker shutdown failed",
                        }
                    )
                    raise
                await send({"type": "lifespan.shutdown.complete"})
                return
            raise ValueError("unsupported ASGI lifespan message")

    async def __call__(self, scope, receive, send) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope_type != "http":
            raise ValueError("only HTTP and lifespan scopes are supported")
        self.worker_lifecycle.ensure_ready()
        await self.adapter(scope, receive, send)


def create_adapted_flask_app(
    *,
    environment: Mapping[str, str] | None = None,
    connection_factory: ConnectionFactory = _default_connection_factory,
    adapter_factory=WsgiToAsgi,
) -> LifespanWsgiToAsgi:
    """Create the Flask application adapted to ASGI inside its worker."""

    wsgi_application = create_flask_app(
        environment=environment,
        connection_factory=connection_factory,
    )
    lifecycle = flask_worker_lifecycle(wsgi_application)
    return LifespanWsgiToAsgi(
        wsgi_application,
        lifecycle,
        adapter_factory(wsgi_application),
    )
