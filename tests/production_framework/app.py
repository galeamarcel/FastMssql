"""Worker-local application interfaces for the production framework matrix.

The structural ``/package`` route proves installed-wheel provenance without a
database. SQL-auth behavior routes are introduced by the next TDD task. The
shared lifecycle keeps connection construction outside module import.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Protocol

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
from fastapi import FastAPI
from flask import Flask


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
SAFE_RUN_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
SAFE_SQL_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
SAFE_SERVER = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:[\]_-]{0,252}")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
WHEEL_FILENAME_PATTERN = re.compile(r"fastmssql-[0-9]+\.[0-9]+\.[0-9]+-.+\.whl")


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
            connection_timeout_secs=5,
            retry_connection=False,
        ),
        lifecycle=LifecycleConfig(
            shutdown_timeout_secs=15,
            force_timeout_secs=5,
        ),
        timeouts=TimeoutConfig(
            connect_timeout_secs=10,
            acquire_timeout_secs=5,
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
        application_name=f"{config.application_name}-{pid}",
        ssl_config=SslConfig.development(),
        pool_config=driver.pool,
        lifecycle_config=driver.lifecycle,
        timeout_config=driver.timeouts,
        operation_metrics_config=driver.operation_metrics,
    )


@dataclass
class WorkerLifecycle:
    """Construct and own one connection inside exactly one worker process."""

    config: WorkerConfig
    connection_factory: ConnectionFactory = _default_connection_factory
    pid: int = field(default_factory=_current_pid)
    state: WorkerState = field(default=WorkerState.NEW, init=False)
    connection: DriverConnection | None = field(
        default=None,
        init=False,
        repr=False,
    )
    pool_identity: str | None = field(default=None, init=False)

    def _record(self, phase: str) -> None:
        destination = self.config.artifact_directory / f"{phase}-{self.pid}.json"
        if destination.exists():
            raise WorkerLifecycleError(
                f"{phase} record already exists for worker PID {self.pid}"
            )
        payload = {
            **self.config.public_record(),
            "phase": phase,
            "pid": self.pid,
            "pool_identity": self.pool_identity,
        }
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.config.artifact_directory,
                prefix=f".{phase}-{self.pid}-",
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
            if self.config.database_mode is DatabaseMode.SQL_AUTH:
                connection = self.connection_factory(self.config, self.pid)
                self.connection = connection
                self.pool_identity = f"{self.pid}-{id(connection):x}"
                await connection.connect(validate=True)
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

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.fastmssql_worker = lifecycle
        async with lifecycle.lifespan():
            yield

    application = FastAPI(lifespan=lifespan)
    application.state.fastmssql_worker = lifecycle

    @application.get("/package")
    async def package_probe() -> dict[str, object]:
        lifecycle.ensure_ready()
        return package_record(config)

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
    application.extensions["fastmssql_worker"] = lifecycle

    @application.get("/package")
    def package_probe() -> dict[str, object]:
        lifecycle.ensure_ready()
        return package_record(config)

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
