from __future__ import annotations

import ast
import asyncio
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import re
import tomllib
from types import SimpleNamespace
from typing import Any

import pytest

from production_framework import app as framework_app
from production_framework import gunicorn_conf


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
LOCKFILE = ROOT / "uv.lock"
APP = ROOT / "tests/production_framework/app.py"
GUNICORN_CONFIG = ROOT / "tests/production_framework/gunicorn_conf.py"
PYTHON_RUNNER = ROOT / "scripts/sql_auth/production_framework_matrix.py"
SHELL_RUNNER = ROOT / "scripts/sql_auth/run_production_framework_matrix.sh"
FULL_RUNNER = ROOT / "scripts/sql_auth/run_all.sh"
REPORT_GENERATOR = ROOT / "scripts/sql_auth/generate_report.py"
HOSTED_WORKFLOW = ROOT / ".github/workflows/production-framework-matrix.yml"
RUST_WORKFLOW = ROOT / ".github/workflows/rust-unit-tests.yml"
SECURITY_WORKFLOW = ROOT / ".github/workflows/dependency-security.yml"
EVIDENCE_TEST = ROOT / "tests/sql_auth_strict/test_production_framework_matrix.py"

EXPECTED_BRANCHES = {
    "test/production-framework-matrix",
    "feat/production-framework-matrix",
    "verify/production-framework-matrix",
    "docs/production-framework-matrix-status",
}
EXPECTED_WORKER_COUNTS = (1, 2, 4, 8)
EXPECTED_PROFILE_FAMILIES = {
    "fastapi-uvicorn-asyncio",
    "fastapi-uvicorn-uvloop",
    "fastapi-gunicorn-uvicorn-worker",
    "flask-gunicorn-sync",
    "flask-gunicorn-gthread",
    "flask-asgi-uvicorn-asyncio",
    "flask-asgi-uvicorn-uvloop",
}
EXPECTED_CASES = {f"FRAME-{number:03d}" for number in range(27, 55)}


def _required_text(path: Path) -> str:
    assert path.is_file(), f"missing required production-framework file: {path}"
    return path.read_text(encoding="utf-8")


def _case_occurrences(path: Path) -> dict[str, int]:
    tree = ast.parse(_required_text(path), filename=str(path))
    occurrences: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "case":
            continue
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                occurrences[argument.value] = occurrences.get(argument.value, 0) + 1
    return occurrences


def _workflow_push_branches(source: str) -> set[str]:
    lines = source.splitlines()
    on_index = next(
        (index for index, line in enumerate(lines) if line in {"on:", '"on":'}),
        None,
    )
    assert on_index is not None, "workflow has no on mapping"
    on_end = next(
        (
            index
            for index in range(on_index + 1, len(lines))
            if lines[index] and not lines[index].startswith((" ", "#"))
        ),
        len(lines),
    )
    on_lines = lines[on_index + 1 : on_end]
    push_index = next(
        (index for index, line in enumerate(on_lines) if line == "  push:"),
        None,
    )
    assert push_index is not None, "workflow has no push trigger"
    branch_index = next(
        (
            index
            for index in range(push_index + 1, len(on_lines))
            if on_lines[index] == "    branches:"
        ),
        None,
    )
    assert branch_index is not None, "workflow has no explicit push branches"
    branches: set[str] = set()
    for line in on_lines[branch_index + 1 :]:
        if line.startswith("      - "):
            branches.add(line.removeprefix("      - ").strip("'\""))
            continue
        if line.strip() and len(line) - len(line.lstrip()) <= 4:
            break
    assert branches, "workflow push branch list is empty"
    return branches


def _valid_worker_environment(tmp_path: Path) -> dict[str, str]:
    run_root = tmp_path / "run"
    artifact_directory = run_root / "worker-records"
    artifact_directory.mkdir(parents=True)
    return {
        "FASTMSSQL_FRAMEWORK_DATABASE_MODE": "sql_auth",
        "FASTMSSQL_FRAMEWORK_WORKER_COUNT": "4",
        "FASTMSSQL_FRAMEWORK_GLOBAL_CONNECTION_BUDGET": "16",
        "FASTMSSQL_FRAMEWORK_APPLICATION_NAME": "framework_app_01",
        "FASTMSSQL_FRAMEWORK_RUN_ID": "run-0123456789abcdef",
        "FASTMSSQL_FRAMEWORK_RUN_ROOT": str(run_root),
        "FASTMSSQL_FRAMEWORK_ARTIFACT_DIR": str(artifact_directory),
        "FASTMSSQL_FRAMEWORK_TABLE": "framework_items_01234567",
        "FASTMSSQL_FRAMEWORK_SQL_DELAY_MS": "100",
        "FASTMSSQL_SQL_AUTH_HOST": "127.0.0.1",
        "FASTMSSQL_SQL_AUTH_PORT": "14334",
        "FASTMSSQL_SQL_AUTH_DATABASE": "fastmssql_validation",
        "FASTMSSQL_SQL_AUTH_OWNER_USER": "fastmssql_owner",
        "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD": "private-test-value",
    }


def test_worker_config_requires_every_common_and_sql_auth_setting(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    for name in tuple(environment):
        incomplete = environment.copy()
        del incomplete[name]
        with pytest.raises(framework_app.ConfigurationError) as captured:
            framework_app.WorkerConfig.from_environment(incomplete)
        assert name in str(captured.value)
        assert "private-test-value" not in str(captured.value)


@pytest.mark.parametrize("worker_count", ["0", "3", "16", "1.5"])
def test_worker_config_rejects_undeclared_worker_counts(
    tmp_path: Path,
    worker_count: str,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    environment["FASTMSSQL_FRAMEWORK_WORKER_COUNT"] = worker_count
    with pytest.raises(framework_app.ConfigurationError):
        framework_app.WorkerConfig.from_environment(environment)


@pytest.mark.parametrize("worker_count", ["1", "2", "4", "8"])
def test_worker_config_accepts_every_declared_worker_count(
    tmp_path: Path,
    worker_count: str,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    environment["FASTMSSQL_FRAMEWORK_WORKER_COUNT"] = worker_count
    config = framework_app.WorkerConfig.from_environment(environment)
    assert config.worker_count == int(worker_count)
    assert config.pool_max_per_worker == 16 // int(worker_count)


@pytest.mark.parametrize("budget", ["0", "-1", "3", "15", "not-an-int"])
def test_worker_config_requires_an_exact_global_budget_division(
    tmp_path: Path,
    budget: str,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    environment["FASTMSSQL_FRAMEWORK_GLOBAL_CONNECTION_BUDGET"] = budget
    with pytest.raises(framework_app.ConfigurationError):
        framework_app.WorkerConfig.from_environment(environment)

    environment["FASTMSSQL_FRAMEWORK_GLOBAL_CONNECTION_BUDGET"] = "16"
    config = framework_app.WorkerConfig.from_environment(environment)
    assert config.pool_max_per_worker == 4
    assert (
        config.worker_count * config.pool_max_per_worker
        == config.global_connection_budget
    )


@pytest.mark.parametrize("port", ["0", "65536", "1.5", "tcp"])
def test_worker_config_rejects_out_of_range_database_ports(
    tmp_path: Path,
    port: str,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    environment["FASTMSSQL_SQL_AUTH_PORT"] = port
    with pytest.raises(framework_app.ConfigurationError):
        framework_app.WorkerConfig.from_environment(environment)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("FASTMSSQL_FRAMEWORK_APPLICATION_NAME", "unsafe name"),
        ("FASTMSSQL_FRAMEWORK_RUN_ID", "../escape"),
        ("FASTMSSQL_FRAMEWORK_TABLE", "items; DROP TABLE items"),
        ("FASTMSSQL_FRAMEWORK_SQL_DELAY_MS", "101"),
    ],
)
def test_worker_config_rejects_unsafe_identifiers_and_unlisted_delays(
    tmp_path: Path,
    name: str,
    value: str,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    environment[name] = value
    with pytest.raises(framework_app.ConfigurationError):
        framework_app.WorkerConfig.from_environment(environment)


def test_worker_config_requires_artifacts_inside_the_run_root(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    environment["FASTMSSQL_FRAMEWORK_ARTIFACT_DIR"] = str(tmp_path / "outside")
    with pytest.raises(
        framework_app.ConfigurationError,
        match="artifact directory must be contained by run root",
    ):
        framework_app.WorkerConfig.from_environment(environment)


def test_worker_config_is_immutable_and_never_records_credentials(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    secret = environment["FASTMSSQL_SQL_AUTH_OWNER_PASSWORD"]
    config = framework_app.WorkerConfig.from_environment(environment)

    with pytest.raises(FrozenInstanceError):
        config.worker_count = 8

    rendered = repr(config)
    record = json.dumps(config.public_record(), sort_keys=True)
    assert secret not in rendered
    assert secret not in record
    assert "password" not in record.lower()

    invalid = environment.copy()
    invalid["FASTMSSQL_SQL_AUTH_PORT"] = secret
    with pytest.raises(framework_app.ConfigurationError) as captured:
        framework_app.WorkerConfig.from_environment(invalid)
    assert secret not in str(captured.value)


def test_worker_config_keeps_database_and_offline_modes_disjoint(
    tmp_path: Path,
) -> None:
    sql_auth_environment = _valid_worker_environment(tmp_path)
    sql_auth = framework_app.WorkerConfig.from_environment(sql_auth_environment)
    assert sql_auth.database_mode is framework_app.DatabaseMode.SQL_AUTH
    assert sql_auth.database is not None

    offline_environment = sql_auth_environment.copy()
    offline_environment["FASTMSSQL_FRAMEWORK_DATABASE_MODE"] = "offline"
    for name in framework_app.SQL_AUTH_ENVIRONMENT_KEYS:
        offline_environment.pop(name)
    offline = framework_app.WorkerConfig.from_environment(offline_environment)
    assert offline.database_mode is framework_app.DatabaseMode.OFFLINE
    assert offline.database is None

    confused_environment = offline_environment.copy()
    confused_environment["FASTMSSQL_SQL_AUTH_OWNER_PASSWORD"] = "must-not-be-accepted"
    with pytest.raises(
        framework_app.ConfigurationError,
        match="offline mode forbids SQL-auth settings",
    ):
        framework_app.WorkerConfig.from_environment(confused_environment)


def test_importing_application_module_does_not_construct_a_connection() -> None:
    assert not any(
        isinstance(value, framework_app.Connection)
        for value in vars(framework_app).values()
    )


class _FakeConnection:
    def __init__(
        self,
        events: list[object],
        *,
        connect_error: BaseException | None = None,
        connect_delay_seconds: float = 0,
        before_disconnect=None,
        disconnect_delay_seconds: float = 0,
        disconnect_error: BaseException | None = None,
    ) -> None:
        self.events = events
        self.connect_error = connect_error
        self.connect_delay_seconds = connect_delay_seconds
        self.before_disconnect = before_disconnect
        self.disconnect_delay_seconds = disconnect_delay_seconds
        self.disconnect_error = disconnect_error

    async def connect(self, *, validate: bool = True) -> None:
        self.events.append(("connect", validate, os.getpid()))
        if self.connect_delay_seconds:
            await asyncio.sleep(self.connect_delay_seconds)
        if self.connect_error is not None:
            raise self.connect_error

    async def disconnect(self) -> None:
        if self.before_disconnect is not None:
            self.before_disconnect()
        self.events.append(("disconnect", os.getpid()))
        if self.disconnect_delay_seconds:
            await asyncio.sleep(self.disconnect_delay_seconds)
        if self.disconnect_error is not None:
            raise self.disconnect_error


def _connection_factory(
    connection: _FakeConnection,
    events: list[object],
):
    def create(config, pid: int):
        events.append(("construct", pid, os.getpid(), config.application_name))
        return connection

    return create


def _worker_record(
    environment: dict[str, str],
    phase: str,
    pid: int | None = None,
) -> Path:
    worker_pid = os.getpid() if pid is None else pid
    return (
        Path(environment["FASTMSSQL_FRAMEWORK_ARTIFACT_DIR"])
        / f"{phase}-{worker_pid}.json"
    )


@pytest.mark.asyncio
async def test_native_fastapi_lifespan_owns_one_pool_in_the_worker_pid(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    shutdown_record = _worker_record(environment, "shutdown")
    connection = _FakeConnection(
        events,
        before_disconnect=lambda: (
            pytest.fail("shutdown recorded before disconnect")
            if shutdown_record.exists()
            else None
        ),
    )
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )
    assert events == []

    async with application.router.lifespan_context(application):
        ready_record = _worker_record(environment, "ready")
        assert ready_record.is_file()
        assert not shutdown_record.exists()
        assert events == [
            (
                "construct",
                os.getpid(),
                os.getpid(),
                environment["FASTMSSQL_FRAMEWORK_APPLICATION_NAME"],
            ),
            ("connect", True, os.getpid()),
        ]
        ready_payload = json.loads(ready_record.read_text(encoding="utf-8"))
        assert ready_payload["phase"] == "ready"
        assert ready_payload["pid"] == os.getpid()
        assert environment[
            "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD"
        ] not in ready_record.read_text(encoding="utf-8")
        assert list(ready_record.parent.glob("ready-*.json")) == [ready_record]
        assert list(ready_record.parent.glob("*.tmp")) == []

    assert events[-1] == ("disconnect", os.getpid())
    assert shutdown_record.is_file()
    shutdown_payload = json.loads(shutdown_record.read_text(encoding="utf-8"))
    assert shutdown_payload["phase"] == "shutdown"
    assert shutdown_payload["pid"] == os.getpid()


@pytest.mark.asyncio
async def test_default_connection_factory_applies_the_per_worker_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _FakeConnection(events)
    constructor_arguments: dict[str, object] = {}

    def capture_connection(**arguments):
        constructor_arguments.update(arguments)
        return connection

    monkeypatch.setattr(framework_app, "Connection", capture_connection)
    application = framework_app.create_fastapi_app(environment=environment)

    async with application.router.lifespan_context(application):
        assert constructor_arguments["application_name"] == (
            f"{environment['FASTMSSQL_FRAMEWORK_APPLICATION_NAME']}-{os.getpid()}"
        )
        pool = constructor_arguments["pool_config"]
        assert pool.max_size == 4
        assert pool.min_idle == 0
        assert constructor_arguments["operation_metrics_config"].enabled is True
        assert constructor_arguments["timeout_config"].acquire_timeout_secs == 5
        assert constructor_arguments["lifecycle_config"].shutdown_timeout_secs == 15


@pytest.mark.asyncio
async def test_native_fastapi_startup_failure_propagates_without_readiness(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _FakeConnection(
        events,
        connect_error=RuntimeError("synthetic-connect-failure"),
    )
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )

    with pytest.raises(RuntimeError, match="synthetic-connect-failure"):
        async with application.router.lifespan_context(application):
            pytest.fail("startup failure must prevent service")

    assert not _worker_record(environment, "ready").exists()
    assert not _worker_record(environment, "shutdown").exists()
    assert ("disconnect", os.getpid()) in events


@pytest.mark.asyncio
async def test_native_fastapi_rejects_a_stale_ready_record_and_cleans_pool(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    ready_record = _worker_record(environment, "ready")
    ready_record.write_text('{"sentinel": true}\n', encoding="utf-8")
    events: list[object] = []
    connection = _FakeConnection(events)
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )

    with pytest.raises(
        framework_app.WorkerLifecycleError,
        match="ready record already exists",
    ):
        async with application.router.lifespan_context(application):
            pytest.fail("a stale ready record must prevent service")

    assert ready_record.read_text(encoding="utf-8") == '{"sentinel": true}\n'
    assert events[-1] == ("disconnect", os.getpid())
    assert list(ready_record.parent.glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_native_fastapi_disconnect_failure_prevents_shutdown_record(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _FakeConnection(
        events,
        disconnect_error=RuntimeError("synthetic-disconnect-failure"),
    )
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )

    with pytest.raises(RuntimeError, match="synthetic-disconnect-failure"):
        async with application.router.lifespan_context(application):
            assert _worker_record(environment, "ready").is_file()

    assert not _worker_record(environment, "shutdown").exists()
    assert application.state.fastmssql_worker.state is framework_app.WorkerState.FAILED


@pytest.mark.asyncio
async def test_native_fastapi_offline_lifespan_never_constructs_a_pool(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    environment["FASTMSSQL_FRAMEWORK_DATABASE_MODE"] = "offline"
    for name in framework_app.SQL_AUTH_ENVIRONMENT_KEYS:
        environment.pop(name)

    def forbidden_factory(config, pid):
        del config, pid
        pytest.fail("offline lifecycle must not construct a connection")

    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=forbidden_factory,
    )
    async with application.router.lifespan_context(application):
        lifecycle = application.state.fastmssql_worker
        assert lifecycle.connection is None
        ready_payload = json.loads(
            _worker_record(environment, "ready").read_text(encoding="utf-8")
        )
        assert ready_payload["database_mode"] == "offline"
        assert ready_payload["pool_identity"].endswith("-offline")
    assert _worker_record(environment, "shutdown").is_file()


def test_gunicorn_wsgi_hooks_are_post_fork_bounded_and_deterministic(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _FakeConnection(events)
    application = framework_app.create_flask_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )
    worker = SimpleNamespace(wsgi=application, pid=os.getpid())

    assert gunicorn_conf.preload_app is False
    assert events == []
    gunicorn_conf.post_worker_init(worker)
    assert _worker_record(environment, "ready").is_file()
    assert events[:2] == [
        (
            "construct",
            os.getpid(),
            os.getpid(),
            environment["FASTMSSQL_FRAMEWORK_APPLICATION_NAME"],
        ),
        ("connect", True, os.getpid()),
    ]

    with pytest.raises(
        framework_app.WorkerLifecycleError,
        match="worker lifecycle is already ready",
    ):
        gunicorn_conf.post_worker_init(worker)

    gunicorn_conf.worker_exit(None, worker)
    assert _worker_record(environment, "shutdown").is_file()
    assert events.count(("disconnect", os.getpid())) == 1
    gunicorn_conf.worker_exit(None, worker)
    assert events.count(("disconnect", os.getpid())) == 1


def test_gunicorn_wsgi_startup_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _FakeConnection(events, connect_delay_seconds=60)
    application = framework_app.create_flask_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )
    worker = SimpleNamespace(wsgi=application, pid=os.getpid())
    monkeypatch.setattr(
        gunicorn_conf,
        "worker_hook_timeout_seconds",
        0.01,
    )

    with pytest.raises(TimeoutError):
        gunicorn_conf.post_worker_init(worker)

    assert not _worker_record(environment, "ready").exists()
    assert events[-1] == ("disconnect", os.getpid())


def test_gunicorn_wsgi_teardown_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _FakeConnection(events, disconnect_delay_seconds=60)
    application = framework_app.create_flask_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )
    worker = SimpleNamespace(wsgi=application, pid=os.getpid())
    gunicorn_conf.post_worker_init(worker)
    monkeypatch.setattr(
        gunicorn_conf,
        "worker_hook_timeout_seconds",
        0.01,
    )

    with pytest.raises(TimeoutError):
        gunicorn_conf.worker_exit(None, worker)

    assert not _worker_record(environment, "shutdown").exists()
    assert (
        framework_app.flask_worker_lifecycle(application).state
        is framework_app.WorkerState.FAILED
    )


@pytest.mark.asyncio
async def test_adapted_flask_owns_lifespan_and_delegates_only_http(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _FakeConnection(events)
    delegated_scopes: list[str] = []

    async def adapter(scope, receive, send) -> None:
        del receive, send
        delegated_scopes.append(scope["type"])

    application = framework_app.create_adapted_flask_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
        adapter_factory=lambda wsgi: adapter,
    )
    lifespan_receive: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    lifespan_send: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def receive_lifespan() -> dict[str, Any]:
        return await lifespan_receive.get()

    async def send_lifespan(message: dict[str, Any]) -> None:
        await lifespan_send.put(message)

    lifespan_task = asyncio.create_task(
        application(
            {"type": "lifespan"},
            receive_lifespan,
            send_lifespan,
        )
    )
    await lifespan_receive.put({"type": "lifespan.startup"})
    assert await asyncio.wait_for(lifespan_send.get(), timeout=1) == {
        "type": "lifespan.startup.complete"
    }

    async def unused() -> dict[str, Any]:
        raise AssertionError("adapter spy must not receive")

    async def discard(message: dict[str, Any]) -> None:
        del message

    await application({"type": "http"}, unused, discard)
    assert delegated_scopes == ["http"]
    with pytest.raises(
        ValueError,
        match="only HTTP and lifespan scopes are supported",
    ):
        await application({"type": "websocket"}, unused, discard)
    assert delegated_scopes == ["http"]

    await lifespan_receive.put({"type": "lifespan.shutdown"})
    assert await asyncio.wait_for(lifespan_send.get(), timeout=1) == {
        "type": "lifespan.shutdown.complete"
    }
    await asyncio.wait_for(lifespan_task, timeout=1)
    assert _worker_record(environment, "shutdown").is_file()


@pytest.mark.asyncio
async def test_adapted_flask_startup_failure_prevents_http_service(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _FakeConnection(
        events,
        connect_error=RuntimeError("synthetic-adapter-startup-failure"),
    )
    delegated_scopes: list[str] = []

    async def adapter(scope, receive, send) -> None:
        del receive, send
        delegated_scopes.append(scope["type"])

    application = framework_app.create_adapted_flask_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
        adapter_factory=lambda wsgi: adapter,
    )
    messages = iter(({"type": "lifespan.startup"},))
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return next(messages)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    with pytest.raises(
        RuntimeError,
        match="synthetic-adapter-startup-failure",
    ):
        await application({"type": "lifespan"}, receive, send)
    assert sent == [
        {
            "type": "lifespan.startup.failed",
            "message": "worker startup failed",
        }
    ]
    assert not _worker_record(environment, "ready").exists()

    with pytest.raises(
        framework_app.WorkerLifecycleError,
        match="worker lifecycle is not ready",
    ):
        await application(
            {"type": "http"},
            receive,
            send,
        )
    assert delegated_scopes == []


def test_production_server_dependencies_are_locked_and_development_only() -> None:
    project = tomllib.loads(_required_text(PYPROJECT))
    assert tuple(project["project"].get("dependencies", ())) == ()
    development = set(project["dependency-groups"]["dev"])
    assert {
        "uvicorn==0.51.0",
        "gunicorn==26.0.0; sys_platform != 'win32'",
        "uvicorn-worker==0.4.0; sys_platform != 'win32'",
        "uvloop==0.22.1; sys_platform != 'win32'",
    } <= development
    assert "uvloop==0.22.1" not in development

    lock = _required_text(LOCKFILE)
    for distribution in ("uvicorn", "gunicorn", "uvicorn-worker", "uvloop"):
        assert f'name = "{distribution}"' in lock


def test_production_framework_files_and_marker_are_repository_owned() -> None:
    for path in (
        APP,
        GUNICORN_CONFIG,
        PYTHON_RUNNER,
        SHELL_RUNNER,
        HOSTED_WORKFLOW,
    ):
        assert path.is_file(), f"missing {path.relative_to(ROOT)}"
    assert SHELL_RUNNER.stat().st_mode & 0o111

    project = tomllib.loads(_required_text(PYPROJECT))
    assert any(
        marker.startswith("production_framework:")
        for marker in project["tool"]["pytest"]["ini_options"]["markers"]
    )


def test_application_constructs_worker_pools_after_spawn_or_fork() -> None:
    source = _required_text(APP)
    tree = ast.parse(source, filename=str(APP))
    for token in (
        "create_fastapi_app",
        "create_flask_app",
        "create_adapted_flask_app",
        "asynccontextmanager",
        "connect(validate=True)",
        "disconnect()",
        "os.getpid()",
        "ready",
        "shutdown",
        "os.replace",
        "PoolConfig",
        "LifecycleConfig",
        "TimeoutConfig",
        "OperationMetricsConfig",
    ):
        assert token in source
    module_scope = "\n".join(
        ast.unparse(statement)
        for statement in tree.body
        if not isinstance(
            statement,
            (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        )
    )
    assert "Connection(" not in module_scope
    assert "os.environ" not in module_scope
    assert "connection_string" not in source.lower()


def test_application_routes_encode_real_driver_behavior() -> None:
    source = _required_text(APP)
    for token in (
        '"/ready"',
        '"/value/{value}"',
        '"/pool"',
        '"/wait/{value}"',
        '"/cancel/{token}"',
        '"/transaction/{item_id}"',
        '"/saturated/{value}"',
        '"/stream"',
        '"/loop"',
        '"/gather"',
        "request.is_disconnected()",
        "connection.transaction()",
        "connection.stream(",
        "StreamingResponse",
        "asyncio.BoundedSemaphore",
    ):
        assert token in source
    for forbidden in (
        "format(value)",
        "format(item_id)",
        "shell=True",
        "repr(error)",
        "os.environ.copy()",
    ):
        assert forbidden not in source


def test_gunicorn_configuration_is_post_fork_and_explicit() -> None:
    source = _required_text(GUNICORN_CONFIG)
    for token in (
        "preload_app = False",
        "post_worker_init",
        "worker_exit",
        "sync",
        "gthread",
        "threads",
        "graceful_timeout",
        "worker_tmp_dir",
    ):
        assert token in source
    assert "preload_app = True" not in source
    assert "uvicorn.workers" not in source
    assert "gevent" not in source
    assert "eventlet" not in source


def test_runner_declares_closed_platform_and_worker_matrix() -> None:
    source = _required_text(PYTHON_RUNNER)
    for token in (
        "SCHEMA_VERSION = 1",
        "WORKER_COUNTS = (1, 2, 4, 8)",
        "MAX_OPERATIONS = 99_999",
        "REQUIRED_OPERATIONS = 1_000",
        "LARGE_OPERATIONS = 10_000",
        "EXTENDED_OPERATIONS = 99_999",
        "sys.platform",
        "Gunicorn is not supported on Windows",
        "uvloop is not supported on Windows",
    ):
        assert token in source
    for family in EXPECTED_PROFILE_FAMILIES:
        assert family in source


def test_runner_builds_current_server_commands_without_a_shell() -> None:
    source = _required_text(PYTHON_RUNNER)
    tree = ast.parse(source, filename=str(PYTHON_RUNNER))
    assert "uvicorn_worker.UvicornWorker" in source
    assert "uvicorn.workers.UvicornWorker" not in source
    assert "--preload" not in source
    assert "--workers" in source
    assert "--loop" in source
    assert "gthread" in source
    assert "--threads" in source

    forbidden_calls: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        rendered = ast.unparse(node.func)
        if rendered in {
            "asyncio.create_subprocess_shell",
            "subprocess.call",
            "subprocess.check_call",
            "subprocess.check_output",
            "os.system",
        }:
            forbidden_calls.append(rendered)
        for keyword in node.keywords:
            if (
                keyword.arg == "shell"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
            ):
                forbidden_calls.append(f"{rendered}(shell=True)")
    assert forbidden_calls == []
    assert "asyncio.create_subprocess_exec" in source


def test_runner_is_bounded_atomic_and_fail_closed() -> None:
    source = _required_text(PYTHON_RUNNER)
    for token in (
        "asyncio.TaskGroup",
        "asyncio.wait_for",
        "psutil.Process",
        "children(recursive=True)",
        "start_new_session",
        "CREATE_NEW_PROCESS_GROUP",
        "os.replace",
        "violations",
        "forced_cleanup",
        "ready_pids",
        "shutdown_pids",
        "listening_sockets_after",
        "sessions_after",
        "pending_after",
    ):
        assert token in source
    assert "create_task(request_one(" not in source
    assert "for operation in range(operations)" not in source
    assert "except Exception:\n        pass" not in source
    assert "except BaseException:\n        pass" not in source


def test_runner_proves_wheel_isolation_and_privacy() -> None:
    source = _required_text(PYTHON_RUNNER)
    for token in (
        "wheel_sha256",
        "candidate_sha",
        "site-packages",
        "fastmssql.__file__",
        "sys.path",
        "PYTHONPATH",
        "redact",
        "credential",
        "sanitized_command",
    ):
        assert token in source
    assert "connection_string" not in source.lower()
    assert "environment.copy()" not in source
    assert "os.environ.copy()" not in source
    assert "dict(os.environ)" not in source


def test_fixed_worker_load_is_opt_in_and_not_task_per_operation() -> None:
    source = _required_text(PYTHON_RUNNER)
    for token in (
        "--allow-extended",
        "operations must be between 1 and 99,999",
        "99,999 operations require --allow-extended",
        "client_worker_count",
        "for operation_id in range(worker_id, operations, worker_count)",
        "maximum_sql_sessions",
        "rss_peak_bytes",
        "latency_histogram",
        "value_digest",
    ):
        assert token in source
    assert "range(operations)" not in source
    assert "gather(*(request" not in source


def test_shell_runner_builds_one_isolated_wheel_and_never_logs_secrets() -> None:
    source = _required_text(SHELL_RUNNER)
    for token in (
        ".env.sql-auth.local",
        "fastmssql-sql-auth-dev",
        "maturin build",
        "--locked",
        "sha256",
        "uv venv",
        "uv pip install",
        "uv pip check",
        "PYTHONPATH",
        "production-framework-metrics.json",
        "--allow-extended",
    ):
        assert token in source
    assert source.count("maturin build") == 1
    assert "set -x" not in source
    assert "echo ${FASTMSSQL_SQL_AUTH" not in source


def test_full_runner_orders_process_execution_before_evidence_validation() -> None:
    source = _required_text(FULL_RUNNER)
    process_lane = "record production-framework-process "
    evidence_lane = "record production-framework-evidence "
    assert process_lane in source
    assert evidence_lane in source
    assert source.index("record provision ") < source.index(process_lane)
    assert source.index(process_lane) < source.index(evidence_lane)
    assert source.index(evidence_lane) < source.index("record report ")
    assert "tests/sql_auth_strict/test_production_framework_matrix.py" in source


def test_report_generator_consumes_production_framework_evidence() -> None:
    source = _required_text(REPORT_GENERATOR)
    for token in (
        "production-framework-metrics.json",
        "Production framework process matrix",
        "native ASGI: concurrent requests on persistent event loop",
        "Flask WSGI: async view, occupied WSGI worker/thread",
        "thread-sensitive WSGI serialization per process",
        "99,999",
    ):
        assert token in source


def test_hosted_workflow_has_exact_platform_claims_and_safety() -> None:
    source = _required_text(HOSTED_WORKFLOW)
    for token in (
        "permissions:\n  contents: read",
        "persist-credentials: false",
        "ubuntu-latest",
        "macos-latest",
        "windows-2022",
        "Generate and mask ephemeral SQL credentials",
        "Microsoft SQL Server 2022",
        "maturin build",
        "uv pip check",
        "production-framework-metrics",
        "hosted_sql_auth",
        "Gunicorn is not supported on Windows",
        "uvloop is not supported on Windows",
        "actions/upload-artifact@",
    ):
        assert token in source
    for forbidden in (
        "continue-on-error",
        "pytest.skip",
        "|| true",
        "contents: write",
        "id-token: write",
        "pull-requests: write",
    ):
        assert forbidden not in source
    action_references = re.findall(
        r"(?m)^\s*uses:\s*([^\s#]+)",
        source,
    )
    assert action_references
    assert all(
        reference.startswith("./") or re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference)
        for reference in action_references
    )
    assert EXPECTED_BRANCHES <= _workflow_push_branches(source)


def test_cumulative_hosted_workflows_trigger_every_candidate_branch() -> None:
    for path in (RUST_WORKFLOW, SECURITY_WORKFLOW):
        branches = _workflow_push_branches(_required_text(path))
        assert EXPECTED_BRANCHES <= branches, path.name


def test_evidence_test_owns_every_new_case_exactly_once() -> None:
    occurrences = _case_occurrences(EVIDENCE_TEST)
    assert {case_id: occurrences.get(case_id, 0) for case_id in EXPECTED_CASES} == {
        case_id: 1 for case_id in EXPECTED_CASES
    }
    assert not (set(occurrences) - EXPECTED_CASES)


def test_required_contracts_never_skip_or_swallow_failures() -> None:
    sources = "\n".join(
        _required_text(path)
        for path in (
            APP,
            GUNICORN_CONFIG,
            PYTHON_RUNNER,
            SHELL_RUNNER,
            HOSTED_WORKFLOW,
            EVIDENCE_TEST,
        )
    )
    for forbidden in (
        "pytest.skip",
        "unittest.skip",
        "continue-on-error",
        "except Exception:\n        pass",
        "except BaseException:\n        pass",
        "return True  # tolerate",
    ):
        assert forbidden not in sources

    for path in (APP, GUNICORN_CONFIG, PYTHON_RUNNER, EVIDENCE_TEST):
        tree = ast.parse(_required_text(path), filename=str(path))
        broad_noop_handlers = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
            and isinstance(node.type, ast.Name)
            and node.type.id in {"Exception", "BaseException"}
            and all(
                isinstance(statement, (ast.Pass, ast.Continue))
                for statement in node.body
            )
        ]
        assert broad_noop_handlers == [], path.relative_to(ROOT)
