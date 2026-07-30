from __future__ import annotations

import ast
import asyncio
from dataclasses import FrozenInstanceError
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import textwrap
import tomllib
from types import SimpleNamespace
from typing import Any

import httpx
import psutil
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


def _load_production_framework_runner():
    assert PYTHON_RUNNER.is_file(), (
        "production framework runner has not been implemented"
    )
    spec = importlib.util.spec_from_file_location(
        "fastmssql_production_framework_runner",
        PYTHON_RUNNER,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _runner_cli_arguments(tmp_path: Path) -> list[str]:
    run_root = tmp_path / "run"
    run_root.mkdir(exist_ok=True)
    wheel = tmp_path / "fastmssql-0.7.7-cp311-abi3.whl"
    wheel.write_bytes(b"test-wheel")
    return [
        "--candidate-sha",
        "a" * 40,
        "--wheel",
        str(wheel),
        "--wheel-sha256",
        "b" * 64,
        "--venv-python",
        sys.executable,
        "--run-root",
        str(run_root),
        "--output",
        str(run_root / "production-framework-metrics.json"),
        "--database-mode",
        "offline",
        "--global-connection-budget",
        "16",
    ]


def test_runner_cli_uses_schema_one_and_closed_operation_bounds(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    assert runner.SCHEMA_VERSION == 1
    assert runner.WORKER_COUNTS == (1, 2, 4, 8)
    assert runner.REQUIRED_OPERATIONS == 1_000
    assert runner.LARGE_OPERATIONS == 10_000
    assert runner.EXTENDED_OPERATIONS == 99_999
    assert runner.MAX_OPERATIONS == 99_999

    required = runner.parse_cli(_runner_cli_arguments(tmp_path))
    assert required.operations == (1_000,)
    assert required.allow_extended is False

    for operations in (0, 100_000):
        with pytest.raises(
            runner.RunnerConfigurationError,
            match="operations must be between 1 and 99,999",
        ):
            runner.validate_operations(operations, allow_extended=True)

    with pytest.raises(
        runner.RunnerConfigurationError,
        match="99,999 operations require --allow-extended",
    ):
        runner.parse_cli(
            [
                *_runner_cli_arguments(tmp_path),
                "--operations",
                "99999",
            ]
        )

    extended = runner.parse_cli(
        [
            *_runner_cli_arguments(tmp_path),
            "--operations",
            "1000",
            "--operations",
            "10000",
            "--operations",
            "99999",
            "--allow-extended",
        ]
    )
    assert extended.operations == (1_000, 10_000, 99_999)
    assert extended.allow_extended is True


@pytest.mark.parametrize("platform_name", ["linux", "darwin"])
def test_posix_profile_expansion_is_complete_and_unique(
    platform_name: str,
) -> None:
    runner = _load_production_framework_runner()
    profiles = runner.expand_profiles(
        platform_name,
        database_mode="sql_auth",
    )
    assert len(profiles) == 28
    assert all(profile.applicable for profile in profiles)
    assert all(profile.status == "PENDING" for profile in profiles)
    assert {profile.family for profile in profiles} == EXPECTED_PROFILE_FAMILIES
    assert {profile.workers for profile in profiles} == set(EXPECTED_WORKER_COUNTS)
    assert len({profile.id for profile in profiles}) == len(profiles)


def test_windows_profile_expansion_is_explicit_not_applicable() -> None:
    runner = _load_production_framework_runner()
    profiles = runner.expand_profiles(
        "win32",
        database_mode="sql_auth",
    )
    assert len(profiles) == 28
    applicable = [profile for profile in profiles if profile.applicable]
    not_applicable = [profile for profile in profiles if not profile.applicable]
    assert len(applicable) == 8
    assert {profile.family for profile in applicable} == {
        "fastapi-uvicorn-asyncio",
        "flask-asgi-uvicorn-asyncio",
    }
    assert all(profile.status == "N/A" for profile in not_applicable)
    assert {
        profile.not_applicable_reason
        for profile in not_applicable
        if profile.loop == "uvloop"
    } == {"uvloop is not supported on Windows"}
    assert {
        profile.not_applicable_reason
        for profile in not_applicable
        if profile.server == "gunicorn"
    } == {"Gunicorn is not supported on Windows"}


@pytest.mark.parametrize("platform_name", ["linux", "darwin", "win32"])
def test_representative_profiles_are_stable_and_platform_applicable(
    platform_name: str,
) -> None:
    runner = _load_production_framework_runner()
    profiles = {
        profile.id: profile
        for profile in runner.expand_profiles(
            platform_name,
            database_mode="sql_auth",
        )
    }
    selected = runner.representative_profiles(platform_name)
    assert selected["native_asgi"] in profiles
    assert selected["load"] == selected["native_asgi"]
    assert profiles[selected["native_asgi"]].workers == 4
    assert profiles[selected["native_asgi"]].applicable
    if platform_name == "win32":
        assert selected["flask_sync"] is None
        assert selected["flask_gthread"] is None
    else:
        assert profiles[selected["flask_sync"]].applicable
        assert profiles[selected["flask_gthread"]].applicable


def test_profile_serialization_is_deterministic_and_key_sorted() -> None:
    runner = _load_production_framework_runner()
    profiles = runner.expand_profiles(
        "linux",
        database_mode="offline",
    )
    first = runner.profiles_json(profiles)
    second = runner.profiles_json(
        runner.expand_profiles("linux", database_mode="offline")
    )
    assert first == second
    assert first.startswith('[{"app_factory":')
    records = json.loads(first)
    assert [record["id"] for record in records] == sorted(
        record["id"] for record in records
    )
    assert all(record["database_mode"] == "offline" for record in records)


def _profile_by_family(
    runner,
    family: str,
    *,
    workers: int = 4,
):
    return next(
        profile
        for profile in runner.expand_profiles(
            "linux",
            database_mode="offline",
        )
        if profile.family == family and profile.workers == workers
    )


@pytest.mark.parametrize(
    ("family", "loop"),
    [
        ("fastapi-uvicorn-asyncio", "asyncio"),
        ("fastapi-uvicorn-uvloop", "uvloop"),
        ("flask-asgi-uvicorn-asyncio", "asyncio"),
        ("flask-asgi-uvicorn-uvloop", "uvloop"),
    ],
)
def test_uvicorn_command_is_explicit_factory_worker_and_loop(
    tmp_path: Path,
    family: str,
    loop: str,
) -> None:
    runner = _load_production_framework_runner()
    config = runner.parse_cli(_runner_cli_arguments(tmp_path))
    profile = _profile_by_family(runner, family)
    command = runner.build_server_command(
        config,
        profile,
        port=48_123,
    )
    assert isinstance(command, list)
    assert command[:3] == [str(config.venv_python), "-m", "uvicorn"]
    assert command[3] == profile.app_factory
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--port") + 1] == "48123"
    assert command[command.index("--workers") + 1] == "4"
    assert command[command.index("--loop") + 1] == loop
    assert command[command.index("--timeout-graceful-shutdown") + 1] == "20"
    assert command[command.index("--timeout-worker-healthcheck") + 1] == "5"
    assert "--factory" in command
    assert "--reload" not in command
    assert "--preload" not in command


def test_gunicorn_uvicorn_worker_command_uses_current_external_path(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    config = runner.parse_cli(_runner_cli_arguments(tmp_path))
    profile = _profile_by_family(
        runner,
        "fastapi-gunicorn-uvicorn-worker",
    )
    command = runner.build_server_command(
        config,
        profile,
        port=48_124,
    )
    assert command[:3] == [str(config.venv_python), "-m", "gunicorn"]
    assert command[command.index("-k") + 1] == ("uvicorn_worker.UvicornWorker")
    assert "uvicorn.workers.UvicornWorker" not in command
    assert command[command.index("--workers") + 1] == "4"
    assert command[command.index("--bind") + 1] == "127.0.0.1:48124"
    assert command[command.index("--config") + 1] == (
        "python:production_framework.gunicorn_conf"
    )
    assert "--preload" not in command


@pytest.mark.parametrize(
    ("family", "worker_class", "threads"),
    [
        ("flask-gunicorn-sync", "sync", None),
        ("flask-gunicorn-gthread", "gthread", "4"),
    ],
)
def test_flask_gunicorn_commands_are_explicit(
    tmp_path: Path,
    family: str,
    worker_class: str,
    threads: str | None,
) -> None:
    runner = _load_production_framework_runner()
    config = runner.parse_cli(_runner_cli_arguments(tmp_path))
    profile = _profile_by_family(runner, family)
    command = runner.build_server_command(
        config,
        profile,
        port=48_125,
    )
    assert command[command.index("-k") + 1] == worker_class
    if threads is None:
        assert "--threads" not in command
    else:
        assert command[command.index("--threads") + 1] == threads
    assert "--preload" not in command
    assert profile.app_factory in command


def test_command_builder_rejects_ports_and_not_applicable_profiles(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    config = runner.parse_cli(_runner_cli_arguments(tmp_path))
    profile = _profile_by_family(runner, "fastapi-uvicorn-asyncio")
    for port in (0, 65_536):
        with pytest.raises(
            runner.RunnerConfigurationError,
            match="port must be between 1 and 65,535",
        ):
            runner.build_server_command(config, profile, port=port)

    windows_profile = next(
        candidate
        for candidate in runner.expand_profiles(
            "win32",
            database_mode="offline",
        )
        if not candidate.applicable
    )
    with pytest.raises(
        runner.RunnerConfigurationError,
        match="cannot build a command for a not-applicable profile",
    ):
        runner.build_server_command(
            config,
            windows_profile,
            port=48_126,
        )


def _write_process_program(
    tmp_path: Path,
    name: str,
    source: str,
) -> Path:
    program = tmp_path / name
    program.write_text(textwrap.dedent(source), encoding="utf-8")
    return program


def _normal_process_program(tmp_path: Path) -> Path:
    return _write_process_program(
        tmp_path,
        "normal_process.py",
        """
        import json
        import os
        from pathlib import Path
        import signal
        import subprocess
        import sys
        import time

        ready = Path(sys.argv[1])
        shutdown = Path(sys.argv[2])
        run_id = sys.argv[3]
        descendant = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import time; time.sleep(60)",
            ]
        )

        def stop(_signum, _frame):
            if descendant.poll() is None:
                descendant.terminate()
                try:
                    descendant.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    descendant.kill()
                    descendant.wait(timeout=2)
            shutdown.write_text(
                json.dumps({"phase": "shutdown", "pid": os.getpid()}) + "\\n",
                encoding="utf-8",
            )
            raise SystemExit(0)

        for signal_name in ("SIGTERM", "SIGBREAK"):
            selected = getattr(signal, signal_name, None)
            if selected is not None:
                signal.signal(selected, stop)

        print("fixture-stdout", flush=True)
        print("fixture-stderr", file=sys.stderr, flush=True)
        with ready.open("x", encoding="utf-8") as destination:
            destination.write(
                json.dumps(
                    {
                        "phase": "ready",
                        "pid": os.getpid(),
                        "run_id": run_id,
                    }
                )
                + "\\n"
            )
        while True:
            time.sleep(0.05)
        """,
    )


def _stubborn_process_program(tmp_path: Path) -> Path:
    return _write_process_program(
        tmp_path,
        "stubborn_process.py",
        """
        import os
        from pathlib import Path
        import signal
        import subprocess
        import sys
        import time

        for signal_name in ("SIGTERM", "SIGBREAK"):
            selected = getattr(signal, signal_name, None)
            if selected is not None:
                signal.signal(selected, signal.SIG_IGN)
        descendant = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import signal,time;"
                    "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                    "time.sleep(60)"
                ),
            ]
        )
        Path(sys.argv[1]).write_text(
            f"{os.getpid()}:{descendant.pid}\\n",
            encoding="utf-8",
        )
        while True:
            time.sleep(0.05)
        """,
    )


def _listener_process_program(tmp_path: Path) -> Path:
    return _write_process_program(
        tmp_path,
        "listener_process.py",
        """
        import signal
        import socket
        import sys
        import time

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(("127.0.0.1", int(sys.argv[1])))
        except OSError as error:
            print(error, file=sys.stderr, flush=True)
            raise SystemExit(98)
        listener.listen()
        with open(sys.argv[2], "x", encoding="utf-8") as ready:
            ready.write("ready\\n")

        def stop(_signum, _frame):
            listener.close()
            raise SystemExit(0)

        for signal_name in ("SIGTERM", "SIGBREAK"):
            selected = getattr(signal, signal_name, None)
            if selected is not None:
                signal.signal(selected, stop)
        print("listener-ready", flush=True)
        while True:
            time.sleep(0.05)
        """,
    )


def _collision_with_stubborn_descendant_program(tmp_path: Path) -> Path:
    return _write_process_program(
        tmp_path,
        "collision_with_stubborn_descendant.py",
        """
        import os
        from pathlib import Path
        import signal
        import socket
        import subprocess
        import sys
        import time

        descendant = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import signal,time;"
                    "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                    "time.sleep(60)"
                ),
            ]
        )
        Path(sys.argv[2], f"collision-{os.getpid()}-{descendant.pid}.ready").write_text(
            "ready\\n",
            encoding="utf-8",
        )
        time.sleep(0.25)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(("127.0.0.1", int(sys.argv[1])))
        except OSError as error:
            print(error, file=sys.stderr, flush=True)
            raise SystemExit(98)
        raise SystemExit("fixture expected an occupied port")
        """,
    )


def _supervisor_policy(runner, **overrides):
    values = {
        "startup_timeout_seconds": 2.0,
        "graceful_timeout_seconds": 2.0,
        "force_timeout_seconds": 2.0,
        "poll_interval_seconds": 0.01,
        "port_retry_attempts": 3,
        "maximum_capture_bytes": 65_536,
    }
    values.update(overrides)
    return runner.SupervisorPolicy(**values)


def _process_environment(runner) -> dict[str, str]:
    environment = runner.build_child_environment({})
    assert "PYTHONPATH" not in environment
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PYTHONUNBUFFERED"] == "1"
    return environment


def _assert_pids_are_gone(pids: tuple[int, ...]) -> None:
    assert all(not psutil.pid_exists(pid) for pid in pids)


@pytest.mark.asyncio
async def test_process_supervisor_owns_group_logs_records_and_descendants(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    program = _normal_process_program(tmp_path)
    ready = tmp_path / "ready-1.json"
    shutdown = tmp_path / "shutdown-1.json"
    supervisor = await runner.ProcessSupervisor.start(
        [sys.executable, str(program), str(ready), str(shutdown), "run-one"],
        cwd=tmp_path,
        environment=_process_environment(runner),
        policy=_supervisor_policy(runner),
    )

    async with supervisor:
        records = await supervisor.wait_for_worker_records(
            directory=tmp_path,
            phase="ready",
            expected_run_id="run-one",
            expected_count=1,
        )
        assert records[0]["pid"] == supervisor.pid
        if os.name == "posix":
            assert os.getpgid(supervisor.pid) == supervisor.pid
        descendants = supervisor.descendant_pids()
        assert len(descendants) == 1
        all_pids = (supervisor.pid, *descendants)
        outcome = await supervisor.stop()

    assert outcome.returncode == 0
    assert outcome.graceful_stop is (os.name == "posix")
    assert outcome.forced_cleanup is False
    assert outcome.descendant_pids == descendants
    assert "fixture-stdout" in outcome.stdout
    assert "fixture-stderr" in outcome.stderr
    assert outcome.output_truncated is False
    assert shutdown.is_file()
    _assert_pids_are_gone(all_pids)


@pytest.mark.asyncio
async def test_process_supervisor_propagates_exact_child_exit(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    program = _write_process_program(
        tmp_path,
        "exit_process.py",
        """
        import sys

        print("exit-stdout", flush=True)
        print("exit-stderr", file=sys.stderr, flush=True)
        raise SystemExit(23)
        """,
    )
    supervisor = await runner.ProcessSupervisor.start(
        [sys.executable, str(program)],
        cwd=tmp_path,
        environment=_process_environment(runner),
        policy=_supervisor_policy(runner),
    )

    async with supervisor:
        with pytest.raises(runner.ProcessExitedError) as captured:
            await supervisor.wait_for_readiness(lambda: False)

    assert captured.value.returncode == 23
    assert "exit-stdout" in captured.value.stdout
    assert "exit-stderr" in captured.value.stderr
    _assert_pids_are_gone((supervisor.pid,))


@pytest.mark.asyncio
async def test_process_supervisor_bounds_both_diagnostic_streams(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    program = _write_process_program(
        tmp_path,
        "large_diagnostics.py",
        """
        import sys

        print("x" * 4096, flush=True)
        print("y" * 4096, file=sys.stderr, flush=True)
        raise SystemExit(17)
        """,
    )
    supervisor = await runner.ProcessSupervisor.start(
        [sys.executable, str(program)],
        cwd=tmp_path,
        environment=_process_environment(runner),
        policy=_supervisor_policy(
            runner,
            maximum_capture_bytes=1_024,
        ),
    )

    async with supervisor:
        with pytest.raises(runner.ProcessExitedError) as captured:
            await supervisor.wait_for_readiness(lambda: False)

    assert captured.value.returncode == 17
    assert len(captured.value.stdout.encode("utf-8")) == 1_024
    assert len(captured.value.stderr.encode("utf-8")) == 1_024
    assert captured.value.outcome.output_truncated is True
    _assert_pids_are_gone((supervisor.pid,))


@pytest.mark.asyncio
async def test_process_supervisor_redacts_environment_and_inline_credentials(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    synthetic_credential = f"synthetic-{'x' * 2_048}"
    program = _write_process_program(
        tmp_path,
        "credential_diagnostics.py",
        """
        import os
        import sys

        print(os.environ["FASTMSSQL_TEST_PASSWORD"], flush=True)
        print("token=inline-private-value", file=sys.stderr, flush=True)
        raise SystemExit(19)
        """,
    )
    environment = runner.build_child_environment(
        {
            "FASTMSSQL_TEST_PASSWORD": synthetic_credential,
        }
    )
    supervisor = await runner.ProcessSupervisor.start(
        [sys.executable, str(program)],
        cwd=tmp_path,
        environment=environment,
        policy=_supervisor_policy(
            runner,
            maximum_capture_bytes=1_024,
        ),
    )

    async with supervisor:
        with pytest.raises(runner.ProcessExitedError) as captured:
            await supervisor.wait_for_readiness(lambda: False)

    diagnostics = f"{captured.value.stdout}\n{captured.value.stderr}"
    assert synthetic_credential not in diagnostics
    assert "x" * 32 not in diagnostics
    assert "inline-private-value" not in diagnostics
    assert diagnostics.count("<redacted>") == 2
    assert not hasattr(supervisor, "environment")
    _assert_pids_are_gone((supervisor.pid,))


@pytest.mark.asyncio
async def test_process_supervisor_rejects_stale_ready_records(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    ready = tmp_path / "ready-1.json"
    ready.write_text(
        json.dumps({"phase": "ready", "pid": 1, "run_id": "run-stale"}) + "\n",
        encoding="utf-8",
    )
    program = _write_process_program(
        tmp_path,
        "stale_record_sleeper.py",
        """
        import signal
        import time

        def stop(_signum, _frame):
            raise SystemExit(0)

        for signal_name in ("SIGTERM", "SIGBREAK"):
            selected = getattr(signal, signal_name, None)
            if selected is not None:
                signal.signal(selected, stop)
        while True:
            time.sleep(0.05)
        """,
    )
    supervisor = await runner.ProcessSupervisor.start(
        [sys.executable, str(program)],
        cwd=tmp_path,
        environment=_process_environment(runner),
        policy=_supervisor_policy(runner),
    )

    async with supervisor:
        with pytest.raises(
            runner.WorkerEvidenceError,
            match="ready record predates the supervised process",
        ):
            await supervisor.wait_for_worker_records(
                directory=tmp_path,
                phase="ready",
                expected_run_id="run-current",
                expected_count=1,
            )
        descendants = supervisor.descendant_pids()
        all_pids = (supervisor.pid, *descendants)

    assert ready.read_text(encoding="utf-8").endswith("\n")
    _assert_pids_are_gone(all_pids)


@pytest.mark.asyncio
async def test_forced_process_tree_cleanup_is_recorded_as_failure(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    program = _stubborn_process_program(tmp_path)
    ready = tmp_path / "stubborn-ready.txt"
    supervisor = await runner.ProcessSupervisor.start(
        [sys.executable, str(program), str(ready)],
        cwd=tmp_path,
        environment=_process_environment(runner),
        policy=_supervisor_policy(
            runner,
            graceful_timeout_seconds=0.1,
        ),
    )

    async with supervisor:
        await supervisor.wait_for_readiness(ready.is_file)
        parent_pid, descendant_pid = (
            int(value) for value in ready.read_text(encoding="utf-8").strip().split(":")
        )
        assert parent_pid == supervisor.pid
        with pytest.raises(runner.ForcedProcessCleanupError) as captured:
            await supervisor.stop()

    assert captured.value.outcome.forced_cleanup is True
    assert captured.value.outcome.graceful_stop is False
    _assert_pids_are_gone((parent_pid, descendant_pid))


@pytest.mark.asyncio
async def test_process_context_cleans_tree_after_body_exception(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    program = _normal_process_program(tmp_path)
    ready = tmp_path / "ready-exception.json"
    supervisor = await runner.ProcessSupervisor.start(
        [
            sys.executable,
            str(program),
            str(ready),
            str(tmp_path / "shutdown-exception.json"),
            "run-exception",
        ],
        cwd=tmp_path,
        environment=_process_environment(runner),
        policy=_supervisor_policy(runner),
    )
    all_pids: tuple[int, ...] = ()

    with pytest.raises(RuntimeError, match="synthetic-body-failure"):
        async with supervisor:
            await supervisor.wait_for_readiness(ready.is_file)
            all_pids = (supervisor.pid, *supervisor.descendant_pids())
            raise RuntimeError("synthetic-body-failure")

    assert all_pids
    _assert_pids_are_gone(all_pids)


@pytest.mark.asyncio
async def test_process_context_cleans_tree_after_task_cancellation(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    program = _normal_process_program(tmp_path)
    ready = tmp_path / "ready-cancel.json"
    owned_pids: asyncio.Future[tuple[int, ...]] = (
        asyncio.get_running_loop().create_future()
    )

    async def own_process() -> None:
        supervisor = await runner.ProcessSupervisor.start(
            [
                sys.executable,
                str(program),
                str(ready),
                str(tmp_path / "shutdown-cancel.json"),
                "run-cancel",
            ],
            cwd=tmp_path,
            environment=_process_environment(runner),
            policy=_supervisor_policy(runner),
        )
        async with supervisor:
            await supervisor.wait_for_readiness(ready.is_file)
            owned_pids.set_result((supervisor.pid, *supervisor.descendant_pids()))
            await asyncio.Event().wait()

    owner = asyncio.create_task(own_process())
    all_pids = await asyncio.wait_for(owned_pids, timeout=3)
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(owner, timeout=5)

    _assert_pids_are_gone(all_pids)


@pytest.mark.asyncio
async def test_loopback_port_collision_retry_is_bounded(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    program = _listener_process_program(tmp_path)
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    occupied_port = occupied.getsockname()[1]
    available_port = runner.reserve_loopback_port()
    candidates = iter((occupied_port, available_port))

    async def listener_ready(port: int) -> bool:
        if not (tmp_path / f"listener-{port}.ready").is_file():
            return False
        return await runner.loopback_port_is_listening(port)

    try:
        launch = await runner.launch_with_port_retry(
            command_builder=lambda port: [
                sys.executable,
                str(program),
                str(port),
                str(tmp_path / f"listener-{port}.ready"),
            ],
            cwd=tmp_path,
            environment=_process_environment(runner),
            readiness_probe=listener_ready,
            policy=_supervisor_policy(runner, port_retry_attempts=2),
            port_allocator=lambda: next(candidates),
        )
    finally:
        occupied.close()

    async with launch.supervisor:
        assert launch.port == available_port
        assert launch.attempts == 2
        outcome = await launch.supervisor.stop()
    assert outcome.forced_cleanup is False
    _assert_pids_are_gone((outcome.pid, *outcome.descendant_pids))


@pytest.mark.asyncio
async def test_loopback_port_collision_exhaustion_preserves_failure(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    program = _listener_process_program(tmp_path)
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    occupied_port = occupied.getsockname()[1]

    async def listener_ready(port: int) -> bool:
        if not (tmp_path / f"listener-{port}.ready").is_file():
            return False
        return await runner.loopback_port_is_listening(port)

    try:
        with pytest.raises(runner.PortCollisionError) as captured:
            await runner.launch_with_port_retry(
                command_builder=lambda port: [
                    sys.executable,
                    str(program),
                    str(port),
                    str(tmp_path / f"listener-{port}.ready"),
                ],
                cwd=tmp_path,
                environment=_process_environment(runner),
                readiness_probe=listener_ready,
                policy=_supervisor_policy(runner, port_retry_attempts=2),
                port_allocator=lambda: occupied_port,
            )
    finally:
        occupied.close()

    assert captured.value.attempts == 2
    assert captured.value.ports == (occupied_port, occupied_port)


@pytest.mark.asyncio
async def test_port_collision_never_hides_forced_descendant_cleanup(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    program = _collision_with_stubborn_descendant_program(tmp_path)
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    occupied_port = occupied.getsockname()[1]

    try:
        with pytest.raises(runner.ForcedProcessCleanupError) as captured:
            await runner.launch_with_port_retry(
                command_builder=lambda port: [
                    sys.executable,
                    str(program),
                    str(port),
                    str(tmp_path),
                ],
                cwd=tmp_path,
                environment=_process_environment(runner),
                readiness_probe=lambda port: False,
                policy=_supervisor_policy(
                    runner,
                    port_retry_attempts=2,
                ),
                port_allocator=lambda: occupied_port,
            )
    finally:
        occupied.close()

    assert captured.value.outcome.forced_cleanup is True
    recorded_pids = tuple(
        int(value)
        for path in tmp_path.glob("collision-*.ready")
        for value in path.stem.removeprefix("collision-").split("-")
    )
    assert recorded_pids
    _assert_pids_are_gone(recorded_pids)


def _config_with_actual_wheel_hash(runner, tmp_path: Path):
    arguments = _runner_cli_arguments(tmp_path)
    wheel = Path(arguments[arguments.index("--wheel") + 1])
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    arguments[arguments.index("--wheel-sha256") + 1] = digest
    return runner.parse_cli(arguments)


def test_isolated_application_copy_is_closed_atomic_and_hash_verified(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    config = _config_with_actual_wheel_hash(runner, tmp_path)
    source = ROOT / "tests/production_framework"

    isolated = runner.prepare_isolated_application(
        config,
        source_directory=source,
        directory_name="offline-smoke",
    )

    assert isolated.root == config.run_root / "offline-smoke"
    assert isolated.package_directory == isolated.root / "production_framework"
    assert isolated.manifest_path == isolated.root / "application-manifest.json"
    assert sorted(
        path.relative_to(isolated.root).as_posix()
        for path in isolated.root.rglob("*")
        if path.is_file()
    ) == [
        "application-manifest.json",
        "production_framework/__init__.py",
        "production_framework/app.py",
        "production_framework/gunicorn_conf.py",
    ]
    manifest = json.loads(isolated.manifest_path.read_text(encoding="utf-8"))
    assert manifest == {
        "candidate_sha": config.candidate_sha,
        "source_sha256": {
            name: hashlib.sha256((source / name).read_bytes()).hexdigest()
            for name in runner.APPLICATION_SOURCE_FILES
        },
        "wheel_filename": config.wheel.name,
        "wheel_sha256": config.wheel_sha256,
    }
    runner.verify_isolated_application(isolated)
    assert not list(isolated.root.rglob("__pycache__"))
    assert not list(isolated.root.rglob("*.pyc"))

    (isolated.package_directory / "app.py").write_text(
        "# tampered\n",
        encoding="utf-8",
    )
    with pytest.raises(
        runner.IsolatedApplicationError,
        match="isolated application source hash mismatch",
    ):
        runner.verify_isolated_application(isolated)
    with pytest.raises(
        runner.IsolatedApplicationError,
        match="isolated application destination already exists",
    ):
        runner.prepare_isolated_application(
            config,
            source_directory=source,
            directory_name="offline-smoke",
        )


def test_isolated_application_rejects_wheel_hash_mismatch_before_copy(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    config = runner.parse_cli(_runner_cli_arguments(tmp_path))

    with pytest.raises(
        runner.CandidateProvenanceError,
        match="candidate wheel SHA-256 mismatch",
    ):
        runner.prepare_isolated_application(
            config,
            source_directory=ROOT / "tests/production_framework",
            directory_name="must-not-exist",
        )

    assert not (config.run_root / "must-not-exist").exists()


def _valid_package_record(config, isolated) -> dict[str, object]:
    venv_root = config.venv_python.parent.parent
    site_packages = (venv_root / "lib/python3.12/site-packages").resolve()
    return {
        "candidate_sha": config.candidate_sha,
        "cwd": str(isolated.root),
        "distribution_direct_url": {
            "archive_info": {},
            "url": config.wheel.as_uri(),
        },
        "distribution_version": "0.7.7",
        "fastmssql_import_path": str(site_packages / "fastmssql/__init__.py"),
        "pid": 1234,
        "ppid": 123,
        "python_executable": str(config.venv_python),
        "sys_path": [str(isolated.root), str(site_packages)],
        "wheel_filename": config.wheel.name,
        "wheel_sha256": config.wheel_sha256,
    }


def test_package_provenance_accepts_only_isolated_site_packages(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    config = _config_with_actual_wheel_hash(runner, tmp_path)
    isolated = runner.prepare_isolated_application(
        config,
        source_directory=ROOT / "tests/production_framework",
        directory_name="provenance",
    )
    record = _valid_package_record(config, isolated)

    assert (
        runner.validate_package_record(
            config,
            isolated,
            record,
            repository_root=ROOT,
        )
        == record
    )

    invalid_records = {
        "candidate SHA": {
            **record,
            "candidate_sha": "f" * 40,
        },
        "wheel filename": {
            **record,
            "wheel_filename": "fastmssql-0.7.6-test.whl",
        },
        "wheel hash": {
            **record,
            "wheel_sha256": "f" * 64,
        },
        "wheel origin": {
            **record,
            "distribution_direct_url": {
                "archive_info": {},
                "url": (tmp_path / "other-fastmssql.whl").resolve().as_uri(),
            },
        },
        "editable origin": {
            **record,
            "distribution_direct_url": {
                "dir_info": {"editable": True},
                "url": config.wheel.as_uri(),
            },
        },
        "worker CWD": {
            **record,
            "cwd": str(ROOT),
        },
        "source import": {
            **record,
            "fastmssql_import_path": str(ROOT / "python/fastmssql/__init__.py"),
        },
        "source sys.path": {
            **record,
            "sys_path": [
                *record["sys_path"],
                str(ROOT / "python"),
            ],
        },
        "repository sys.path": {
            **record,
            "sys_path": [
                *record["sys_path"],
                str(ROOT),
            ],
        },
        "Python executable": {
            **record,
            "python_executable": sys.executable + "-wrong",
        },
    }
    for reason, invalid in invalid_records.items():
        with pytest.raises(
            runner.CandidateProvenanceError,
            match=reason,
        ):
            runner.validate_package_record(
                config,
                isolated,
                invalid,
                repository_root=ROOT,
            )


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
        "FASTMSSQL_FRAMEWORK_CANDIDATE_SHA": "a" * 40,
        "FASTMSSQL_FRAMEWORK_WHEEL_FILENAME": (
            "fastmssql-0.7.7-cp39-abi3-macosx_10_12_universal2.whl"
        ),
        "FASTMSSQL_FRAMEWORK_WHEEL_SHA256": "b" * 64,
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


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        (
            "FASTMSSQL_FRAMEWORK_CANDIDATE_SHA",
            "not-a-sha",
            "candidate SHA",
        ),
        (
            "FASTMSSQL_FRAMEWORK_WHEEL_FILENAME",
            "../candidate.whl",
            "wheel filename",
        ),
        (
            "FASTMSSQL_FRAMEWORK_WHEEL_SHA256",
            "not-a-digest",
            "wheel SHA-256",
        ),
    ],
)
def test_worker_config_rejects_invalid_package_provenance(
    tmp_path: Path,
    name: str,
    value: str,
    message: str,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    environment[name] = value

    with pytest.raises(
        framework_app.ConfigurationError,
        match=message,
    ):
        framework_app.WorkerConfig.from_environment(environment)


def _offline_worker_environment(tmp_path: Path) -> dict[str, str]:
    environment = _valid_worker_environment(tmp_path)
    environment["FASTMSSQL_FRAMEWORK_DATABASE_MODE"] = "offline"
    for name in framework_app.SQL_AUTH_ENVIRONMENT_KEYS:
        environment.pop(name)
    return environment


def _assert_package_payload(
    payload: dict[str, object],
    environment: dict[str, str],
) -> None:
    assert payload["candidate_sha"] == environment["FASTMSSQL_FRAMEWORK_CANDIDATE_SHA"]
    assert (
        payload["wheel_filename"] == environment["FASTMSSQL_FRAMEWORK_WHEEL_FILENAME"]
    )
    assert payload["wheel_sha256"] == environment["FASTMSSQL_FRAMEWORK_WHEEL_SHA256"]
    assert payload["cwd"] == str(Path.cwd().resolve())
    assert isinstance(payload["distribution_direct_url"], dict)
    assert payload["distribution_version"] == "0.7.7"
    assert Path(payload["fastmssql_import_path"]).is_file()
    assert payload["pid"] == os.getpid()
    assert payload["ppid"] == os.getppid()
    assert payload["python_executable"] == sys.executable
    assert isinstance(payload["sys_path"], list)
    rendered = json.dumps(payload, sort_keys=True)
    assert "private-test-value" not in rendered
    assert "password" not in rendered.lower()


@pytest.mark.asyncio
async def test_fastapi_package_route_reports_runtime_provenance_after_ready(
    tmp_path: Path,
) -> None:
    environment = _offline_worker_environment(tmp_path / "fastapi")
    application = framework_app.create_fastapi_app(environment=environment)

    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get("/package")

    assert response.status_code == 200
    _assert_package_payload(response.json(), environment)


@pytest.mark.asyncio
async def test_flask_package_route_reports_runtime_provenance_after_ready(
    tmp_path: Path,
) -> None:
    environment = _offline_worker_environment(tmp_path / "flask")
    application = framework_app.create_flask_app(environment=environment)
    lifecycle = framework_app.flask_worker_lifecycle(application)
    await lifecycle.start()
    try:
        response = application.test_client().get("/package")
    finally:
        await lifecycle.stop()

    assert response.status_code == 200
    _assert_package_payload(response.get_json(), environment)


def test_offline_profile_environment_is_closed_and_credential_free(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    config = _config_with_actual_wheel_hash(runner, tmp_path)
    isolated = runner.prepare_isolated_application(
        config,
        source_directory=ROOT / "tests/production_framework",
        directory_name="offline-environment",
    )
    profile = _profile_by_family(
        runner,
        "fastapi-uvicorn-asyncio",
        workers=1,
    )
    artifact_directory = config.run_root / "records" / profile.id

    environment = runner.build_profile_environment(
        config,
        isolated,
        profile,
        run_id="offline-run-01",
        artifact_directory=artifact_directory,
    )

    assert artifact_directory.is_dir()
    assert environment["FASTMSSQL_FRAMEWORK_DATABASE_MODE"] == "offline"
    assert environment["FASTMSSQL_FRAMEWORK_WORKER_COUNT"] == "1"
    assert environment["FASTMSSQL_FRAMEWORK_GLOBAL_CONNECTION_BUDGET"] == "16"
    assert environment["FASTMSSQL_FRAMEWORK_RUN_ROOT"] == str(config.run_root)
    assert environment["FASTMSSQL_FRAMEWORK_ARTIFACT_DIR"] == str(artifact_directory)
    assert environment["FASTMSSQL_FRAMEWORK_CANDIDATE_SHA"] == (config.candidate_sha)
    assert environment["FASTMSSQL_FRAMEWORK_WHEEL_FILENAME"] == (config.wheel.name)
    assert environment["FASTMSSQL_FRAMEWORK_WHEEL_SHA256"] == (config.wheel_sha256)
    assert "PYTHONPATH" not in environment
    assert not any(name.startswith("FASTMSSQL_SQL_AUTH") for name in environment)
    assert "password" not in json.dumps(environment).lower()

    imported = subprocess.run(
        [
            str(config.venv_python),
            "-c",
            "import production_framework.app",
        ],
        cwd=isolated.root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert imported.returncode == 0, imported.stderr
    runner.verify_isolated_application(isolated)
    assert not list(isolated.root.rglob("__pycache__"))
    assert not list(isolated.root.rglob("*.pyc"))


def test_gunicorn_hooks_leave_asgi_lifespan_ownership_to_uvicorn() -> None:
    worker = SimpleNamespace(wsgi=object(), pid=os.getpid())

    assert gunicorn_conf.post_worker_init(worker) is None
    assert gunicorn_conf.worker_exit(None, worker) is None


@pytest.mark.parametrize(
    ("platform_name", "expected_families"),
    [
        (
            "linux",
            {
                "fastapi-uvicorn-asyncio",
                "fastapi-uvicorn-uvloop",
                "fastapi-gunicorn-uvicorn-worker",
                "flask-gunicorn-sync",
                "flask-gunicorn-gthread",
            },
        ),
        (
            "darwin",
            {
                "fastapi-uvicorn-asyncio",
                "fastapi-uvicorn-uvloop",
                "fastapi-gunicorn-uvicorn-worker",
                "flask-gunicorn-sync",
                "flask-gunicorn-gthread",
            },
        ),
        (
            "win32",
            {
                "fastapi-uvicorn-asyncio",
            },
        ),
    ],
)
def test_offline_smoke_profile_selection_is_explicit_and_portable(
    platform_name: str,
    expected_families: set[str],
) -> None:
    runner = _load_production_framework_runner()
    profiles = runner.offline_smoke_profiles(platform_name)

    assert {profile.family for profile in profiles} == expected_families
    assert all(profile.workers == 1 for profile in profiles)
    assert all(profile.database_mode == "offline" for profile in profiles)
    assert all(profile.applicable for profile in profiles)


@pytest.mark.asyncio
async def test_offline_matrix_rechecks_copy_after_the_last_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_production_framework_runner()
    config = _config_with_actual_wheel_hash(runner, tmp_path)
    profile = _profile_by_family(
        runner,
        "fastapi-uvicorn-asyncio",
        workers=1,
    )
    monkeypatch.setattr(
        runner,
        "offline_smoke_profiles",
        lambda platform_name: (profile,),
    )

    async def tamper_after_start(
        config,
        isolated,
        selected_profile,
        *,
        repository_root,
        run_id,
        policy,
    ):
        del config, repository_root, run_id, policy
        assert selected_profile == profile
        (isolated.package_directory / "app.py").write_text(
            "# modified by the last supervised process\n",
            encoding="utf-8",
        )
        return runner.OfflineSmokeResult(
            profile_id=profile.id,
            port=43122,
            launch_attempts=1,
            sanitized_command=(sys.executable, "-m", "uvicorn"),
            package={},
            ready_pids=(101,),
            shutdown_pids=(101,),
            manager_pid=100,
            descendant_pids=(101,),
            returncode=0,
            graceful_stop=True,
            forced_cleanup=False,
            listening_sockets_after=(),
        )

    monkeypatch.setattr(runner, "run_offline_smoke_profile", tamper_after_start)

    with pytest.raises(
        runner.IsolatedApplicationError,
        match="isolated application source hash mismatch",
    ):
        await runner.run_offline_smoke_matrix(
            config,
            repository_root=ROOT,
            source_directory=ROOT / "tests/production_framework",
        )


def test_offline_cli_writes_exact_schema_one_process_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_production_framework_runner()
    arguments = _runner_cli_arguments(tmp_path)
    wheel = Path(arguments[arguments.index("--wheel") + 1])
    wheel_sha256 = hashlib.sha256(wheel.read_bytes()).hexdigest()
    arguments[arguments.index("--wheel-sha256") + 1] = wheel_sha256
    package = {
        "candidate_sha": "a" * 40,
        "cwd": "/isolated/application",
        "distribution_version": "0.7.7",
        "fastmssql_import_path": "/isolated/site-packages/fastmssql/__init__.py",
        "pid": 101,
        "ppid": 100,
        "python_executable": sys.executable,
        "sys_path": [
            "/isolated/application",
            "/isolated/site-packages",
        ],
        "wheel_filename": wheel.name,
        "wheel_sha256": wheel_sha256,
    }
    result = runner.OfflineSmokeResult(
        profile_id="fastapi-uvicorn-asyncio-w1",
        port=43123,
        launch_attempts=1,
        sanitized_command=(sys.executable, "-m", "uvicorn"),
        package=package,
        ready_pids=(101,),
        shutdown_pids=(101,),
        manager_pid=100,
        descendant_pids=(101,),
        returncode=0,
        graceful_stop=True,
        forced_cleanup=False,
        listening_sockets_after=(),
    )

    async def run_smoke(config, *, repository_root, source_directory, policy=None):
        assert config.database_mode == "offline"
        assert repository_root == ROOT
        assert source_directory == ROOT / "tests/production_framework"
        assert policy is None
        return (result,)

    monkeypatch.setattr(runner, "run_offline_smoke_matrix", run_smoke)

    assert runner.main(arguments) == 0
    output = Path(arguments[arguments.index("--output") + 1])
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload == {
        "candidate_sha": "a" * 40,
        "database_mode": "offline",
        "overall": "PASS",
        "platform_system": runner.normalize_platform(sys.platform),
        "profiles": [
            {
                "descendant_pids": [101],
                "forced_cleanup": False,
                "graceful_stop": True,
                "launch_attempts": 1,
                "listening_sockets_after": [],
                "manager_pid": 100,
                "package": package,
                "port": 43123,
                "profile_id": "fastapi-uvicorn-asyncio-w1",
                "ready_pids": [101],
                "returncode": 0,
                "sanitized_command": [
                    sys.executable,
                    "-m",
                    "uvicorn",
                ],
                "shutdown_pids": [101],
            }
        ],
        "schema_version": 1,
        "violations": [],
        "wheel": {
            "filename": wheel.name,
            "sha256": wheel_sha256,
        },
    }
    assert output.read_text(encoding="utf-8").endswith("\n")
    assert not list(output.parent.glob(f".{output.name}-*.tmp"))


def test_cli_refuses_sql_auth_execution_until_routes_and_observer_exist(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    arguments = _runner_cli_arguments(tmp_path)
    arguments[arguments.index("--database-mode") + 1] = "sql_auth"
    output = Path(arguments[arguments.index("--output") + 1])

    with pytest.raises(
        runner.RunnerConfigurationError,
        match="SQL-auth process execution is not implemented",
    ):
        runner.main(arguments)

    assert not output.exists()


def test_cli_rejects_a_preexisting_output_artifact(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    arguments = _runner_cli_arguments(tmp_path)
    output = Path(arguments[arguments.index("--output") + 1])
    output.write_text('{"overall":"STALE"}\n', encoding="utf-8")

    with pytest.raises(
        runner.RunnerConfigurationError,
        match="output artifact already exists",
    ):
        runner.parse_cli(arguments)

    assert output.read_text(encoding="utf-8") == '{"overall":"STALE"}\n'


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
