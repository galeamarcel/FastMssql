from __future__ import annotations

import ast
import asyncio
from dataclasses import FrozenInstanceError, replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import textwrap
import time
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


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX signal re-raise semantics are not portable to Windows",
)
@pytest.mark.asyncio
async def test_process_supervisor_accepts_graceful_sigterm_reraise(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    ready = tmp_path / "ready.txt"
    shutdown = tmp_path / "shutdown.txt"
    program = _write_process_program(
        tmp_path,
        "graceful_sigterm_reraise.py",
        """
        import signal
        import sys
        import time

        ready, shutdown = sys.argv[1:]
        captured = []

        def request_stop(signum, _frame):
            captured.append(signum)

        signal.signal(signal.SIGTERM, request_stop)
        with open(ready, "w", encoding="utf-8") as handle:
            handle.write("ready\\n")
        while not captured:
            time.sleep(0.01)
        with open(shutdown, "w", encoding="utf-8") as handle:
            handle.write("shutdown-complete\\n")
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.raise_signal(captured[-1])
        """,
    )
    supervisor = await runner.ProcessSupervisor.start(
        [sys.executable, str(program), str(ready), str(shutdown)],
        cwd=tmp_path,
        environment=_process_environment(runner),
        policy=_supervisor_policy(runner),
    )

    async with supervisor:
        await supervisor.wait_for_readiness(ready.is_file)
        outcome = await supervisor.stop()

    assert shutdown.read_text(encoding="utf-8") == "shutdown-complete\n"
    assert outcome.returncode == -signal.SIGTERM
    assert outcome.graceful_stop is True
    assert outcome.forced_cleanup is False
    _assert_pids_are_gone((supervisor.pid,))


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
        "FASTMSSQL_FRAMEWORK_ACQUIRE_TIMEOUT_MS": "250",
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
        ("FASTMSSQL_FRAMEWORK_ACQUIRE_TIMEOUT_MS", "0"),
        ("FASTMSSQL_FRAMEWORK_ACQUIRE_TIMEOUT_MS", "251"),
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


@pytest.mark.parametrize(
    ("platform_name", "expected_families", "expected_count"),
    [
        (
            "darwin",
            {
                "fastapi-gunicorn-uvicorn-worker",
                "fastapi-uvicorn-asyncio",
                "fastapi-uvicorn-uvloop",
            },
            12,
        ),
        ("win32", {"fastapi-uvicorn-asyncio"}, 4),
    ],
)
def test_native_fastapi_profile_selection_is_exact(
    platform_name: str,
    expected_families: set[str],
    expected_count: int,
) -> None:
    runner = _load_production_framework_runner()

    profiles = runner.native_fastapi_profiles(platform_name)

    assert len(profiles) == expected_count
    assert {profile.family for profile in profiles} == expected_families
    assert {profile.workers for profile in profiles} == {1, 2, 4, 8}
    assert all(profile.database_mode == "sql_auth" for profile in profiles)
    assert all(profile.applicable for profile in profiles)


def test_sql_auth_profile_environment_is_closed_bounded_and_redactable(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    offline_config = _config_with_actual_wheel_hash(runner, tmp_path)
    config = replace(offline_config, database_mode="sql_auth")
    isolated = runner.prepare_isolated_application(
        config,
        source_directory=ROOT / "tests/production_framework",
        directory_name="sql-auth-environment",
    )
    profile = next(
        profile
        for profile in runner.native_fastapi_profiles("darwin")
        if profile.family == "fastapi-uvicorn-asyncio" and profile.workers == 2
    )
    settings = runner.SqlAuthObserverSettings(
        host="127.0.0.1",
        port=14334,
        database="fastmssql_validation",
        username="fastmssql_owner",
        password="private-sql-auth-value",
    )
    artifact_directory = config.run_root / "records" / profile.id

    environment = runner.build_profile_environment(
        config,
        isolated,
        profile,
        run_id="native-run-01",
        artifact_directory=artifact_directory,
        sql_auth_settings=settings,
        table_name="framework_items_native_01",
        sql_delay_ms=500,
        acquire_timeout_ms=250,
    )

    assert artifact_directory.is_dir()
    assert environment["FASTMSSQL_FRAMEWORK_DATABASE_MODE"] == "sql_auth"
    assert environment["FASTMSSQL_FRAMEWORK_WORKER_COUNT"] == "2"
    assert environment["FASTMSSQL_FRAMEWORK_GLOBAL_CONNECTION_BUDGET"] == "16"
    assert environment["FASTMSSQL_FRAMEWORK_TABLE"] == "framework_items_native_01"
    assert environment["FASTMSSQL_FRAMEWORK_SQL_DELAY_MS"] == "500"
    assert environment["FASTMSSQL_FRAMEWORK_ACQUIRE_TIMEOUT_MS"] == "250"
    assert environment["FASTMSSQL_SQL_AUTH_OWNER_PASSWORD"] == (
        "private-sql-auth-value"
    )
    assert "PYTHONPATH" not in environment
    persisted = runner.redact(
        json.dumps(environment, sort_keys=True),
        runner._credential_values(environment),
    )
    assert "private-sql-auth-value" not in persisted

    for invalid_delay in (-1, 101, 5_001):
        with pytest.raises(runner.RunnerConfigurationError):
            runner.build_profile_environment(
                config,
                isolated,
                profile,
                run_id=f"native-invalid-{invalid_delay}",
                artifact_directory=(
                    config.run_root / "records" / f"invalid-{invalid_delay}"
                ),
                sql_auth_settings=settings,
                table_name="framework_items_native_01",
                sql_delay_ms=invalid_delay,
                acquire_timeout_ms=250,
            )


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


class _FakeQueryResult:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, object] | None:
        return self._row


class _FakeTransaction:
    def __init__(self, events: list[object], session_id: int) -> None:
        self.events = events
        self.session_id = session_id

    async def __aenter__(self):
        self.events.append(("transaction_enter", self.session_id))
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> bool:
        self.events.append(
            (
                "transaction_exit",
                None if exc_type is None else exc_type.__name__,
            )
        )
        return False

    async def execute(
        self,
        sql: str,
        params: list[object] | None = None,
    ) -> int:
        self.events.append(("transaction_execute", sql, list(params or ())))
        return 1

    async def query(
        self,
        sql: str,
        params: list[object] | None = None,
    ) -> _FakeQueryResult:
        self.events.append(("transaction_query", sql, list(params or ())))
        return _FakeQueryResult({"session_id": self.session_id})

    async def commit(self) -> None:
        self.events.append(("transaction_commit", self.session_id))

    async def rollback(self) -> None:
        self.events.append(("transaction_rollback", self.session_id))


class _RouteTransaction(_FakeTransaction):
    def __init__(
        self,
        events: list[object],
        session_id: int,
        connection: "_RouteConnection",
    ) -> None:
        super().__init__(events, session_id)
        self.connection = connection

    async def query(
        self,
        sql: str,
        params: list[object] | None = None,
    ) -> _FakeQueryResult:
        arguments = list(params or ())
        self.events.append(("transaction_query", sql, arguments))
        if "WAITFOR DELAY '00:00:05.000'" in sql and not arguments:
            return await self.connection.query(sql, params)
        row: dict[str, object] = {"session_id": self.session_id}
        if arguments and isinstance(arguments[-1], int):
            row["value"] = arguments[-1]
        return _FakeQueryResult(row)


class _FakeResultSet:
    def __init__(
        self,
        index: int,
        rows: list[dict[str, object]],
    ) -> None:
        self.index = index
        self.column_names = tuple(rows[0]) if rows else ("value",)
        self._rows = iter(rows)

    def __aiter__(self):
        return self

    async def __anext__(self) -> dict[str, object]:
        try:
            return next(self._rows)
        except StopIteration:
            raise StopAsyncIteration from None


class _FakeResultStream:
    def __init__(self, result_sets: list[_FakeResultSet]) -> None:
        self._result_sets = iter(result_sets)
        self.entered = False
        self.exited = False
        self.exit_type: type[BaseException] | None = None

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        self.exited = True
        self.exit_type = exc_type

    def __aiter__(self):
        return self

    async def __anext__(self) -> _FakeResultSet:
        try:
            return next(self._result_sets)
        except StopIteration:
            raise StopAsyncIteration from None


class _FailingResultSet:
    index = 0
    column_names = ("value",)

    def __aiter__(self):
        return self

    async def __anext__(self) -> dict[str, object]:
        raise RuntimeError("intentional stream failure")


class _BlockingResultSet:
    index = 0
    column_names = ("value",)

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self) -> dict[str, object]:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError("unreachable")


class _RouteConnection(_FakeConnection):
    def __init__(
        self,
        events: list[object],
        *,
        block_queries: bool = False,
        query_delay_seconds: float = 0,
        query_error: BaseException | None = None,
        stream_rows: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__(events)
        self.block_queries = block_queries
        self.query_delay_seconds = query_delay_seconds
        self.query_error = query_error
        self.query_started = asyncio.Event()
        self.query_release = asyncio.Event()
        self.query_cancelled = asyncio.Event()
        self.transactions: list[_FakeTransaction] = []
        self.stream_result = _FakeResultStream(
            [_FakeResultSet(0, stream_rows or [{"value": 1}, {"value": 2}])]
        )

    async def query(
        self,
        sql: str,
        params: list[object] | None = None,
    ) -> _FakeQueryResult:
        arguments = list(params or ())
        self.events.append(("query", sql, arguments))
        self.query_started.set()
        if self.query_error is not None:
            raise self.query_error
        if self.block_queries:
            try:
                await self.query_release.wait()
            except asyncio.CancelledError:
                self.query_cancelled.set()
                raise
        if self.query_delay_seconds:
            await asyncio.sleep(self.query_delay_seconds)
        if "SUSER_SNAME" in sql:
            row = {
                "application_name": f"framework_app_01-{os.getpid()}",
                "principal": "fastmssql_owner",
                "session_id": 701,
            }
        else:
            value = arguments[0] if arguments else 0
            if isinstance(value, bytes):
                value = value.decode("ascii")
            row = {
                "value": value,
                "token": value,
                "session_id": 702,
            }
        return _FakeQueryResult(row)

    async def pool_stats(self) -> dict[str, object]:
        self.events.append("pool_stats")
        return {
            "connected": True,
            "connections": 2,
            "idle_connections": 2,
            "active_connections": 0,
            "max_size": 4,
            "min_idle": 0,
            "get_started": 3,
            "get_direct": 3,
            "get_waited": 0,
            "get_timed_out": 0,
            "pending_gets": 0,
            "get_wait_time_seconds": 0.0,
            "connections_created": 2,
            "connections_closed_broken": 0,
            "connections_closed_invalid": 0,
            "connections_closed_max_lifetime": 0,
            "connections_closed_idle_timeout": 0,
        }

    async def operation_stats(self) -> dict[str, object]:
        self.events.append("operation_stats")
        return {
            "schema_version": 2,
            "enabled": True,
            "bucket_bounds_seconds": [0.001, 0.01, 0.1, 1.0],
            "operations": {},
        }

    def transaction(self) -> _FakeTransaction:
        transaction = _RouteTransaction(
            self.events,
            session_id=800 + len(self.transactions),
            connection=self,
        )
        self.transactions.append(transaction)
        self.events.append("connection_transaction")
        return transaction

    async def stream(
        self,
        sql: str,
        params: list[object] | None = None,
        *,
        buffer_size: int,
    ) -> _FakeResultStream:
        self.events.append(
            ("stream", sql, list(params or ()), buffer_size)
        )
        return self.stream_result


def _route_query_events(events: list[object]) -> list[tuple[object, ...]]:
    return [
        event
        for event in events
        if isinstance(event, tuple) and event and event[0] == "query"
    ]


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
async def test_fastapi_sql_auth_identity_value_wait_and_pool_routes_are_bounded(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _RouteConnection(events)
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )

    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            ready = await client.get("/ready")
            principal = await client.get("/principal")
            value = await client.get("/value/731947")
            overflow = await client.get("/value/9223372036854775808")
            underflow = await client.get("/value/-9223372036854775809")
            waited = await client.get("/wait/842059")
            pool = await client.get("/pool")

    assert ready.status_code == 200
    assert ready.json() == {
        "application_name": (
            f"{environment['FASTMSSQL_FRAMEWORK_APPLICATION_NAME']}-{os.getpid()}"
        ),
        "pid": os.getpid(),
        "pool_identity": f"{os.getpid()}-{id(connection):x}",
        "state": "ready",
    }
    assert principal.json() == {
        "application_name": ready.json()["application_name"],
        "pid": os.getpid(),
        "principal": "fastmssql_owner",
        "session_id": 701,
    }
    assert value.json() == {"session_id": 702, "value": 731947}
    assert overflow.status_code == 422
    assert underflow.status_code == 422
    assert waited.json() == {
        "delay_ms": 100,
        "session_id": 702,
        "value": 842059,
    }
    assert pool.json() == {
        "admission": {
            "active": 0,
            "capacity": 8,
            "rejected": 0,
        },
        "application_name": ready.json()["application_name"],
        "operations": {
            "bucket_bounds_seconds": [0.001, 0.01, 0.1, 1.0],
            "enabled": True,
            "operations": {},
            "schema_version": 2,
        },
        "pid": os.getpid(),
        "pool": {
            "active_connections": 0,
            "connected": True,
            "connections": 2,
            "connections_closed_broken": 0,
            "connections_closed_idle_timeout": 0,
            "connections_closed_invalid": 0,
            "connections_closed_max_lifetime": 0,
            "connections_created": 2,
            "get_direct": 3,
            "get_started": 3,
            "get_timed_out": 0,
            "get_wait_time_seconds": 0.0,
            "get_waited": 0,
            "idle_connections": 2,
            "max_size": 4,
            "min_idle": 0,
            "pending_gets": 0,
        },
    }

    query_events = _route_query_events(events)
    assert len(query_events) == 3
    principal_sql, principal_params = query_events[0][1:]
    assert "SUSER_SNAME" in principal_sql
    assert principal_params == []
    value_sql, value_params = query_events[1][1:]
    assert "@P1" in value_sql
    assert "731947" not in value_sql
    assert value_params == [731947]
    wait_sql, wait_params = query_events[2][1:]
    assert "WAITFOR DELAY '00:00:00.100'" in wait_sql
    assert "842059" not in wait_sql
    assert wait_params == [842059]
    rendered = "\n".join(
        response.text for response in (ready, principal, value, waited, pool)
    )
    assert environment["FASTMSSQL_SQL_AUTH_OWNER_PASSWORD"] not in rendered
    assert "SELECT" not in rendered


@pytest.mark.asyncio
async def test_fastapi_wait_header_identifies_the_inflight_pinned_session(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _RouteConnection(events)
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )

    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/wait/73",
                headers={
                    "X-FastMssql-Context-Token": "graceful_query_token_42"
                },
            )

    assert response.status_code == 200
    assert response.json() == {
        "delay_ms": 100,
        "session_id": 800,
        "value": 73,
    }
    execute_event = next(
        event
        for event in events
        if isinstance(event, tuple) and event[0] == "transaction_execute"
    )
    query_event = next(
        event
        for event in events
        if isinstance(event, tuple) and event[0] == "transaction_query"
    )
    assert events.index(execute_event) < events.index(query_event)
    assert execute_event[1:] == (
        "SET CONTEXT_INFO @P1",
        [b"graceful_query_token_42"],
    )
    assert "WAITFOR DELAY '00:00:00.100'" in query_event[1]
    assert "graceful_query_token_42" not in query_event[1]
    assert query_event[2] == [73]
    assert "graceful_query_token_42" not in response.text


@pytest.mark.asyncio
async def test_fastapi_transaction_route_uses_the_worker_pool_and_explicit_outcome(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _RouteConnection(events)
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )

    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            committed = await client.post(
                "/transaction/81471",
                params={"outcome": "commit"},
            )
            rolled_back = await client.post(
                "/transaction/92583",
                params={"outcome": "rollback"},
            )
            invalid = await client.post(
                "/transaction/1",
                params={"outcome": "discard"},
            )
            invalid_item = await client.post(
                "/transaction/0",
                params={"outcome": "commit"},
            )

    assert committed.status_code == 200
    assert committed.json() == {
        "item_id": 81471,
        "outcome": "commit",
        "session_id": 800,
    }
    assert rolled_back.status_code == 200
    assert rolled_back.json() == {
        "item_id": 92583,
        "outcome": "rollback",
        "session_id": 801,
    }
    assert invalid.status_code == 422
    assert invalid_item.status_code == 422
    assert events.count("connection_transaction") == 2
    assert ("transaction_commit", 800) in events
    assert ("transaction_rollback", 801) in events
    execute_events = [
        event
        for event in events
        if isinstance(event, tuple) and event[0] == "transaction_execute"
    ]
    insert_events = [
        event
        for event in execute_events
        if environment["FASTMSSQL_FRAMEWORK_TABLE"] in event[1]
    ]
    context_events = [
        event
        for event in execute_events
        if event[1] == "SET CONTEXT_INFO @P1"
    ]
    query_events = [
        event
        for event in events
        if isinstance(event, tuple) and event[0] == "transaction_query"
    ]
    query_indices = [
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event[0] == "transaction_query"
    ]
    assert len(insert_events) == 2
    assert len(context_events) == 2
    assert len(query_events) == 2
    for expected_item_id, event in zip((81471, 92583), insert_events):
        _, sql, params = event
        assert environment["FASTMSSQL_FRAMEWORK_TABLE"] in sql
        assert "@P1" in sql and "@P2" in sql
        assert str(expected_item_id) not in sql
        assert params == [expected_item_id, "transaction"]
    for expected_item_id, outcome, context_event, query_event, query_index in zip(
        (81471, 92583),
        ("commit", "rollback"),
        context_events,
        query_events,
        query_indices,
    ):
        assert context_event[2] == [
            f"transaction:{expected_item_id}:{outcome}".encode("ascii")
        ]
        assert "CONTEXT_INFO" not in query_event[1]
        assert "WAITFOR DELAY '00:00:00.100'" in query_event[1]
        assert query_event[2] == []
        assert events.index(context_event) < query_index
    assert query_indices[0] < events.index(
        ("transaction_commit", 800)
    )
    assert query_indices[1] < events.index(
        ("transaction_rollback", 801)
    )

    holding_records = sorted(
        Path(environment["FASTMSSQL_FRAMEWORK_ARTIFACT_DIR"]).glob(
            "transaction-holding-*.json"
        )
    )
    settled_records = sorted(
        Path(environment["FASTMSSQL_FRAMEWORK_ARTIFACT_DIR"]).glob(
            "transaction-settled-*.json"
        )
    )
    assert len(holding_records) == 2
    assert len(settled_records) == 2
    assert {
        (record["item_id"], record["outcome"], record["transaction_phase"])
        for path in (*holding_records, *settled_records)
        for record in [json.loads(path.read_text(encoding="utf-8"))]
    } == {
        (81471, "commit", "holding"),
        (81471, "commit", "settled"),
        (92583, "rollback", "holding"),
        (92583, "rollback", "settled"),
    }


class _DisconnectingRequest:
    def __init__(self) -> None:
        self.calls = 0

    async def is_disconnected(self) -> bool:
        self.calls += 1
        await asyncio.sleep(0)
        return self.calls >= 2


class _ConnectedRequest:
    async def is_disconnected(self) -> bool:
        await asyncio.sleep(0)
        return False


@pytest.mark.asyncio
async def test_fastapi_cancel_route_settles_the_sql_task_after_disconnect(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _RouteConnection(events, block_queries=True)
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )
    endpoint = next(
        route.endpoint
        for route in application.routes
        if getattr(route, "path", None) == "/cancel/{token}"
    )

    async with application.router.lifespan_context(application):
        payload = await asyncio.wait_for(
            endpoint(
                request=_DisconnectingRequest(),
                token="cancel_token_42",
            ),
            timeout=1,
        )

    assert payload == {"cancelled": True}
    assert connection.query_cancelled.is_set()
    execute_event = next(
        event
        for event in events
        if isinstance(event, tuple) and event[0] == "transaction_execute"
    )
    query_event = _route_query_events(events)[0]
    _, sql, params = query_event
    assert "cancel_token_42" not in sql
    assert params == []
    assert execute_event[1:] == (
        "SET CONTEXT_INFO @P1",
        [b"cancel_token_42"],
    )


@pytest.mark.asyncio
async def test_fastapi_cancel_route_identifies_a_pinned_session_before_waiting(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []

    class BlockingTransaction:
        def __init__(self) -> None:
            self.query_cancelled = asyncio.Event()

        async def __aenter__(self):
            events.append("transaction_enter")
            return self

        async def __aexit__(self, exc_type, exc_value, traceback) -> bool:
            events.append(
                (
                    "transaction_exit",
                    None if exc_type is None else exc_type.__name__,
                )
            )
            return False

        async def execute(
            self,
            sql: str,
            params: list[object] | None = None,
        ) -> int:
            events.append(("transaction_execute", sql, list(params or ())))
            return 1

        async def query(
            self,
            sql: str,
            params: list[object] | None = None,
        ) -> _FakeQueryResult:
            events.append(("transaction_query", sql, list(params or ())))
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.query_cancelled.set()
                raise
            raise AssertionError("unreachable")

    class PinnedCancellationConnection(_RouteConnection):
        def __init__(self) -> None:
            super().__init__(events)
            self.pinned_transaction = BlockingTransaction()

        async def query(
            self,
            sql: str,
            params: list[object] | None = None,
        ) -> _FakeQueryResult:
            raise AssertionError(
                "the identified wait must use one pinned transaction session"
            )

        def transaction(self) -> BlockingTransaction:
            events.append("connection_transaction")
            return self.pinned_transaction

    connection = PinnedCancellationConnection()
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )
    endpoint = next(
        route.endpoint
        for route in application.routes
        if getattr(route, "path", None) == "/cancel/{token}"
    )

    async with application.router.lifespan_context(application):
        payload = await asyncio.wait_for(
            endpoint(
                request=_DisconnectingRequest(),
                token="pinned_cancel_token_42",
            ),
            timeout=1,
        )

    assert payload == {"cancelled": True}
    assert connection.pinned_transaction.query_cancelled.is_set()
    execute_event = next(
        event
        for event in events
        if isinstance(event, tuple) and event[0] == "transaction_execute"
    )
    query_event = next(
        event
        for event in events
        if isinstance(event, tuple) and event[0] == "transaction_query"
    )
    assert events.index(execute_event) < events.index(query_event)
    assert execute_event[1:] == (
        "SET CONTEXT_INFO @P1",
        [b"pinned_cancel_token_42"],
    )
    assert "WAITFOR" in query_event[1]
    assert "CONTEXT_INFO" not in query_event[1]
    assert query_event[2] == []
    assert ("transaction_exit", "CancelledError") in events


@pytest.mark.asyncio
async def test_fastapi_cancel_route_accepts_the_driver_future_awaitable(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []

    class FutureQueryConnection(_RouteConnection):
        def query(
            self,
            sql: str,
            params: list[object] | None = None,
        ) -> asyncio.Future[_FakeQueryResult]:
            self.events.append(("query", sql, list(params or ())))
            self.query_started.set()
            future = asyncio.get_running_loop().create_future()
            self.returned_future = future
            return future

    connection = FutureQueryConnection(events)
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )
    endpoint = next(
        route.endpoint
        for route in application.routes
        if getattr(route, "path", None) == "/cancel/{token}"
    )

    async with application.router.lifespan_context(application):
        payload = await asyncio.wait_for(
            endpoint(
                request=_DisconnectingRequest(),
                token="future_cancel_token_42",
            ),
            timeout=1,
        )

    assert payload == {"cancelled": True}
    assert connection.returned_future.cancelled()


@pytest.mark.asyncio
async def test_fastapi_cancel_route_never_reflects_the_context_token(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _RouteConnection(events)
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )
    endpoint = next(
        route.endpoint
        for route in application.routes
        if getattr(route, "path", None) == "/cancel/{token}"
    )

    async with application.router.lifespan_context(application):
        payload = await endpoint(
            request=_ConnectedRequest(),
            token="never_reflect_this_token",
        )

    assert payload == {"cancelled": False, "session_id": 702}
    assert "never_reflect_this_token" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_fastapi_saturation_rejects_excess_and_recovers_capacity(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    environment["FASTMSSQL_FRAMEWORK_WORKER_COUNT"] = "1"
    environment["FASTMSSQL_FRAMEWORK_GLOBAL_CONNECTION_BUDGET"] = "1"
    events: list[object] = []
    connection = _RouteConnection(events, block_queries=True)
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )

    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            holder = asyncio.create_task(client.get("/saturated/31"))
            await asyncio.wait_for(connection.query_started.wait(), timeout=1)
            waiter = asyncio.create_task(client.get("/saturated/47"))
            deadline = time.monotonic() + 1
            while len(_route_query_events(events)) < 2:
                assert time.monotonic() < deadline
                await asyncio.sleep(0.001)
            started = time.monotonic()
            rejected = await client.get("/saturated/53")
            rejection_seconds = time.monotonic() - started
            connection.query_release.set()
            holder_response = await asyncio.wait_for(holder, timeout=1)
            waiter_response = await asyncio.wait_for(waiter, timeout=1)
            recovered = await client.get("/saturated/59")
            pool = await client.get("/pool")

    assert holder_response.status_code == 200
    assert holder_response.json() == {"session_id": 702, "value": 31}
    assert waiter_response.status_code == 200
    assert waiter_response.json() == {"session_id": 702, "value": 47}
    assert rejected.status_code == 503
    assert rejected.json() == {"error": "saturated"}
    assert rejection_seconds < 0.5
    assert recovered.status_code == 200
    assert recovered.json() == {"session_id": 702, "value": 59}
    assert pool.json()["admission"] == {
        "active": 0,
        "capacity": 2,
        "rejected": 1,
    }
    query_events = _route_query_events(events)
    assert [event[2] for event in query_events] == [
        [31],
        [47],
        [59],
    ]
    assert all(
        "WAITFOR DELAY '00:00:00.100'" in str(event[1])
        for event in query_events
    )


@pytest.mark.asyncio
async def test_fastapi_saturation_reports_private_pool_acquire_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAcquireTimeout(Exception):
        phase = "acquire"
        operation = "query"
        retryable = True

    monkeypatch.setattr(
        framework_app.fastmssql,
        "OperationTimeoutError",
        FakeAcquireTimeout,
    )
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _RouteConnection(
        events,
        query_error=FakeAcquireTimeout("private pool timeout detail"),
    )
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )

    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get("/saturated/71")
            pool = await client.get("/pool")

    assert response.status_code == 504
    assert response.json() == {
        "error": "pool_acquire_timeout",
        "operation": "query",
        "phase": "acquire",
        "retryable": True,
    }
    assert "private pool timeout detail" not in response.text
    assert pool.json()["admission"]["active"] == 0


@pytest.mark.asyncio
async def test_fastapi_stream_route_closes_bounded_result_stream(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _RouteConnection(
        events,
        stream_rows=[{"value": 11}, {"value": 13}],
    )
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )

    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get("/stream", params={"rows": 173})
            invalid = await client.get("/stream", params={"rows": 0})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/x-ndjson"
    )
    assert [json.loads(line) for line in response.text.splitlines()] == [
        {"result_set": 0, "row": {"value": 11}},
        {"result_set": 0, "row": {"value": 13}},
    ]
    assert invalid.status_code == 422
    stream_event = next(event for event in events if event[0] == "stream")
    _, sql, params, buffer_size = stream_event
    assert "@P1" in sql
    assert "173" not in sql
    assert params == [173]
    assert buffer_size == 8
    assert connection.stream_result.entered is True
    assert connection.stream_result.exited is True


@pytest.mark.asyncio
async def test_fastapi_stream_context_token_is_parameterized_and_private(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _RouteConnection(events, stream_rows=[{"value": 29}])
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )

    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/stream",
                params={"rows": 1, "token": "stream_token_29"},
            )
            invalid = await client.get(
                "/stream",
                params={"rows": 1, "token": "unsafe token"},
            )

    assert response.status_code == 200
    assert "stream_token_29" not in response.text
    stream_event = next(event for event in events if event[0] == "stream")
    _, sql, params, _ = stream_event
    assert "SET CONTEXT_INFO @P1" in sql
    assert "stream_token_29" not in sql
    assert params == [b"stream_token_29", 1]
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_fastapi_stream_closes_driver_stream_when_generator_is_closed(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _RouteConnection(
        events,
        stream_rows=[{"value": 11}, {"value": 13}],
    )
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )
    endpoint = next(
        route.endpoint
        for route in application.routes
        if getattr(route, "path", None) == "/stream"
    )

    async with application.router.lifespan_context(application):
        response = await endpoint(rows=2, token=None)
        iterator = response.body_iterator
        first = await anext(iterator)
        await iterator.aclose()

    assert json.loads(first) == {"result_set": 0, "row": {"value": 11}}
    assert connection.stream_result.entered is True
    assert connection.stream_result.exited is True
    assert connection.stream_result.exit_type is GeneratorExit


@pytest.mark.asyncio
async def test_fastapi_stream_closes_driver_stream_on_iteration_error(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _RouteConnection(events)
    connection.stream_result = _FakeResultStream([_FailingResultSet()])
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )
    endpoint = next(
        route.endpoint
        for route in application.routes
        if getattr(route, "path", None) == "/stream"
    )

    async with application.router.lifespan_context(application):
        response = await endpoint(rows=1, token=None)
        with pytest.raises(RuntimeError, match="intentional stream failure"):
            await anext(response.body_iterator)

    assert connection.stream_result.entered is True
    assert connection.stream_result.exited is True
    assert connection.stream_result.exit_type is RuntimeError


@pytest.mark.asyncio
async def test_fastapi_stream_closes_driver_stream_on_task_cancellation(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    blocking_result_set = _BlockingResultSet()
    connection = _RouteConnection(events)
    connection.stream_result = _FakeResultStream([blocking_result_set])
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )
    endpoint = next(
        route.endpoint
        for route in application.routes
        if getattr(route, "path", None) == "/stream"
    )

    async with application.router.lifespan_context(application):
        response = await endpoint(rows=1, token=None)
        pending_row = asyncio.create_task(anext(response.body_iterator))
        await asyncio.wait_for(blocking_result_set.started.wait(), timeout=1)
        pending_row.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending_row

    assert blocking_result_set.cancelled.is_set()
    assert connection.stream_result.entered is True
    assert connection.stream_result.exited is True
    assert connection.stream_result.exit_type is asyncio.CancelledError


@pytest.mark.asyncio
async def test_fastapi_error_response_never_exposes_exception_or_credentials(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    secret = environment["FASTMSSQL_SQL_AUTH_OWNER_PASSWORD"]
    sql_sentinel = "SELECT private_framework_payload"
    events: list[object] = []
    connection = _RouteConnection(
        events,
        query_error=RuntimeError(f"password={secret}; {sql_sentinel}"),
    )
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )

    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
                response = await client.get("/error")

    assert response.status_code == 500
    assert response.json() == {"error": "internal_error"}
    assert secret not in response.text
    assert sql_sentinel not in response.text


def test_flask_loop_gather_and_errors_preserve_wsgi_contract(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _RouteConnection(events, query_delay_seconds=0.015)
    application = framework_app.create_flask_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )
    worker = SimpleNamespace(wsgi=application, pid=os.getpid())
    gunicorn_conf.post_worker_init(worker)
    try:
        client = application.test_client()
        ready = client.get("/ready")
        principal = client.get("/principal")
        value = client.get("/value/19")
        waited = client.get("/wait/23")
        pool = client.get("/pool")
        loop = client.get("/loop")
        gathered = client.get("/gather")
    finally:
        gunicorn_conf.worker_exit(None, worker)

    assert ready.status_code == 200
    assert ready.get_json()["state"] == "ready"
    assert principal.get_json()["principal"] == "fastmssql_owner"
    assert value.get_json() == {"session_id": 702, "value": 19}
    assert waited.get_json() == {
        "delay_ms": 100,
        "session_id": 702,
        "value": 23,
    }
    assert pool.get_json()["admission"] == {
        "active": 0,
        "capacity": 8,
        "rejected": 0,
    }
    assert loop.status_code == 200
    assert loop.get_json()["loop_id"] > 0
    assert loop.get_json()["value"] == 17
    assert gathered.status_code == 200
    payload = gathered.get_json()
    assert payload["sequential"] == [0, 1, 2, 3]
    assert payload["concurrent"] == [0, 1, 2, 3]
    assert payload["concurrent_seconds"] < payload["sequential_seconds"] * 0.6

    error_environment = _valid_worker_environment(tmp_path / "error")
    secret = error_environment["FASTMSSQL_SQL_AUTH_OWNER_PASSWORD"]
    error_connection = _RouteConnection(
        [],
        query_error=RuntimeError(f"password={secret}; SELECT private_payload"),
    )
    error_application = framework_app.create_flask_app(
        environment=error_environment,
        connection_factory=_connection_factory(error_connection, []),
    )
    error_worker = SimpleNamespace(wsgi=error_application, pid=os.getpid())
    gunicorn_conf.post_worker_init(error_worker)
    try:
        error_response = error_application.test_client().get("/error")
    finally:
        gunicorn_conf.worker_exit(None, error_worker)
    assert error_response.status_code == 500
    assert error_response.get_json() == {"error": "internal_error"}
    assert secret not in error_response.get_data(as_text=True)


def test_flask_execution_evidence_is_request_scoped_and_privacy_safe(
    tmp_path: Path,
) -> None:
    """Catch loss of the WSGI slot/thread/loop evidence used by Task 8."""

    environment = _valid_worker_environment(tmp_path)
    secret = environment["FASTMSSQL_SQL_AUTH_OWNER_PASSWORD"]
    events: list[object] = []
    connection = _RouteConnection(events)
    application = framework_app.create_flask_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )
    worker = SimpleNamespace(wsgi=application, pid=os.getpid())
    gunicorn_conf.post_worker_init(worker)
    try:
        client = application.test_client()
        ready = client.get("/ready")
        pool = client.get("/pool")
        first_loop = client.get("/loop")
        second_loop = client.get("/loop")
        wait = client.get("/execution/wait/31")
        settled = client.get("/execution/state")
        settled_again = client.get("/execution/state")
    finally:
        gunicorn_conf.worker_exit(None, worker)

    assert ready.status_code == 200
    assert pool.status_code == 200
    assert first_loop.status_code == 200
    assert second_loop.status_code == 200
    assert first_loop.get_json()["loop_token"] != second_loop.get_json()[
        "loop_token"
    ]

    assert wait.status_code == 200
    wait_payload = wait.get_json()
    assert wait_payload == {
        "async_thread_token": wait_payload["async_thread_token"],
        "delay_ms": 100,
        "execution_model": (
            "Flask WSGI: async view, occupied WSGI worker/thread"
        ),
        "loop_token": wait_payload["loop_token"],
        "pid": os.getpid(),
        "request_sequence": 3,
        "session_id": 702,
        "value": 31,
        "wsgi_active_requests": 1,
        "wsgi_maximum_active_requests": 1,
        "wsgi_thread_token": wait_payload["wsgi_thread_token"],
    }
    for token_name in (
        "async_thread_token",
        "loop_token",
        "wsgi_thread_token",
    ):
        assert isinstance(wait_payload[token_name], int)
        assert wait_payload[token_name] > 0

    assert settled.status_code == 200
    assert settled.get_json() == {
        "active_other_requests": 0,
        "completed_requests": 3,
        "execution_model": (
            "Flask WSGI: async view, occupied WSGI worker/thread"
        ),
        "maximum_active_requests": 1,
        "pid": os.getpid(),
        "wsgi_thread_count": 1,
    }
    assert settled_again.status_code == 200
    assert settled_again.get_json() == settled.get_json()
    assert secret not in wait.get_data(as_text=True)
    assert secret not in settled.get_data(as_text=True)


def test_flask_numeric_routes_enforce_sql_bigint_bounds(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _RouteConnection(events)
    application = framework_app.create_flask_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )
    worker = SimpleNamespace(wsgi=application, pid=os.getpid())
    gunicorn_conf.post_worker_init(worker)
    try:
        client = application.test_client()
        minimum_value = client.get(f"/value/{-(2**63)}")
        maximum_wait = client.get(f"/wait/{2**63 - 1}")
        maximum_item = client.post(
            f"/transaction/{2**63 - 1}?outcome=rollback"
        )
        invalid = [
            client.get(f"/value/{-(2**63) - 1}"),
            client.get(f"/value/{2**63}"),
            client.get(f"/wait/{-(2**63) - 1}"),
            client.get(f"/wait/{2**63}"),
            client.post("/transaction/0?outcome=commit"),
            client.post(f"/transaction/{2**63}?outcome=commit"),
            client.get(f"/value/{'9' * 4_301}"),
        ]
    finally:
        gunicorn_conf.worker_exit(None, worker)

    assert minimum_value.status_code == 200
    assert minimum_value.get_json()["value"] == -(2**63)
    assert maximum_wait.status_code == 200
    assert maximum_wait.get_json()["value"] == 2**63 - 1
    assert maximum_item.status_code == 200
    assert maximum_item.get_json()["item_id"] == 2**63 - 1
    assert all(response.status_code == 422 for response in invalid)
    assert all(
        response.get_json() == {"error": "invalid_bigint"}
        for response in invalid
    )


class _FakeObserverQueryResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, object]]:
        return list(self._rows)


class _FakeObserverSource:
    def __init__(
        self,
        samples: list[list[dict[str, object]]],
    ) -> None:
        self._samples = iter(samples)
        self._last: list[dict[str, object]] = []
        self.calls: list[tuple[str, list[object]]] = []

    async def query(
        self,
        sql: str,
        params: list[object] | None = None,
    ) -> _FakeObserverQueryResult:
        self.calls.append((sql, list(params or ())))
        try:
            self._last = next(self._samples)
        except StopIteration:
            pass
        return _FakeObserverQueryResult(self._last)


def _observer_rows(prefix: str, observer_name: str) -> list[dict[str, object]]:
    return [
        {
            "application_name": f"{prefix}-101",
            "context_info": b"cancel_token_42\0\0",
            "has_request": 1,
            "host_process_id": 0,
            "session_id": 701,
            "sql_text": "SELECT private_framework_payload",
        },
        {
            "application_name": f"{prefix}-101",
            "context_info": b"",
            "has_request": 0,
            "host_process_id": 0,
            "session_id": 702,
        },
        {
            "application_name": f"{prefix}-202",
            "context_info": b"transaction:8:commit\0",
            "has_request": 1,
            "host_process_id": 0,
            "session_id": 703,
        },
        {
            "application_name": f"{prefix}ish-303",
            "context_info": b"near-prefix",
            "has_request": 1,
            "host_process_id": 303,
            "session_id": 704,
        },
        {
            "application_name": observer_name,
            "context_info": b"observer",
            "has_request": 1,
            "host_process_id": os.getpid(),
            "session_id": 705,
        },
    ]


@pytest.mark.asyncio
async def test_sql_observer_filters_exact_workers_tracks_maxima_and_is_private() -> None:
    runner = _load_production_framework_runner()
    prefix = "fm-run-native"
    observer_name = f"{prefix}-observer"
    first_rows = _observer_rows(prefix, observer_name)
    second_rows = [first_rows[2]]
    source = _FakeObserverSource([first_rows, second_rows])
    observer = runner.SqlServerObserver(
        source=source,
        worker_prefix=prefix,
        observer_application_name=observer_name,
    )

    first = await observer.sample()
    second = await observer.sample()

    assert first.to_record() == {
        "applications": [
            {
                "application_name": f"{prefix}-101",
                "host_process_ids": [0],
                "requests": 1,
                "sessions": 2,
            },
            {
                "application_name": f"{prefix}-202",
                "host_process_ids": [0],
                "requests": 1,
                "sessions": 1,
            },
        ],
        "current_requests": 2,
        "current_sessions": 3,
        "maximum_requests": 2,
        "maximum_sessions": 3,
        "request_context_token_sha256": [
            hashlib.sha256(b"cancel_token_42").hexdigest(),
            hashlib.sha256(b"transaction:8:commit").hexdigest(),
        ],
    }
    assert first.request_context_tokens == (
        "cancel_token_42",
        "transaction:8:commit",
    )
    assert second.current_sessions == 1
    assert second.current_requests == 1
    assert second.maximum_sessions == 3
    assert second.maximum_requests == 2

    query_sql, query_params = source.calls[0]
    assert "sys.dm_exec_sessions" in query_sql
    assert "sys.dm_exec_requests" in query_sql
    assert "dm_exec_sql_text" not in query_sql
    assert prefix not in query_sql
    assert query_params == [f"{prefix}-", observer_name]
    persisted = json.dumps(first.to_record(), sort_keys=True)
    assert "sql_text" not in persisted
    assert "private_framework_payload" not in persisted
    assert "cancel_token_42" not in persisted
    assert "transaction:8:commit" not in persisted
    assert observer_name not in persisted
    assert f"{prefix}ish-303" not in persisted

    ready_records = [
        {
            "phase": "ready",
            "pid": 101,
            "worker_application_name": f"{prefix}-101",
        },
        {
            "phase": "ready",
            "pid": 202,
            "worker_application_name": f"{prefix}-202",
        },
    ]
    assert runner.reconcile_worker_sessions(first, ready_records) == (
        (f"{prefix}-101", 101),
        (f"{prefix}-202", 202),
    )
    mismatched = [*ready_records]
    mismatched[1] = {**mismatched[1], "pid": 999}
    with pytest.raises(
        runner.WorkerEvidenceError,
        match="worker SQL sessions do not match ready records",
    ):
        runner.reconcile_worker_sessions(first, mismatched)


@pytest.mark.asyncio
async def test_sql_observer_zero_session_wait_is_bounded() -> None:
    runner = _load_production_framework_runner()
    prefix = "fm-run-native"
    observer_name = f"{prefix}-observer"
    clock_value = 0.0

    def clock() -> float:
        return clock_value

    async def advance(delay: float) -> None:
        nonlocal clock_value
        clock_value += delay

    clearing_source = _FakeObserverSource(
        [_observer_rows(prefix, observer_name), []]
    )
    clearing = runner.SqlServerObserver(
        source=clearing_source,
        worker_prefix=prefix,
        observer_application_name=observer_name,
        poll_interval_seconds=0.01,
        clock=clock,
        sleep=advance,
    )
    zero = await clearing.wait_for_zero_sessions(timeout_seconds=0.05)
    assert zero.current_sessions == 0
    assert len(clearing_source.calls) == 2

    clock_value = 0.0
    persistent_source = _FakeObserverSource(
        [_observer_rows(prefix, observer_name)]
    )
    persistent = runner.SqlServerObserver(
        source=persistent_source,
        worker_prefix=prefix,
        observer_application_name=observer_name,
        poll_interval_seconds=0.01,
        clock=clock,
        sleep=advance,
    )
    with pytest.raises(
        runner.ReadinessTimeoutError,
        match="worker SQL sessions did not reach zero within the bound",
    ) as captured:
        await persistent.wait_for_zero_sessions(timeout_seconds=0.025)
    assert clock_value == pytest.approx(0.025)
    assert "SELECT" not in str(captured.value)
    assert "password" not in str(captured.value).lower()


@pytest.mark.asyncio
async def test_sql_observer_waits_for_zero_requests_without_zero_sessions() -> None:
    runner = _load_production_framework_runner()
    prefix = "fm-run-native"
    observer_name = f"{prefix}-observer"
    active_rows = _observer_rows(prefix, observer_name)
    idle_rows = [
        {**row, "has_request": 0, "context_info": b""}
        for row in active_rows
    ]
    source = _FakeObserverSource([active_rows, idle_rows])
    observer = runner.SqlServerObserver(
        source=source,
        worker_prefix=prefix,
        observer_application_name=observer_name,
        poll_interval_seconds=0.001,
    )

    idle = await observer.wait_for_zero_requests(timeout_seconds=0.1)

    assert idle.current_requests == 0
    assert idle.current_sessions == 3
    assert idle.maximum_requests == 2
    assert len(source.calls) == 2


@pytest.mark.asyncio
async def test_sql_observer_deadline_cancels_a_hanging_sample() -> None:
    runner = _load_production_framework_runner()

    class HangingSource:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def query(
            self,
            sql: str,
            params: list[object] | None = None,
        ) -> _FakeObserverQueryResult:
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            raise AssertionError("unreachable")

    source = HangingSource()
    observer = runner.SqlServerObserver(
        source=source,
        worker_prefix="fm-run-native",
        observer_application_name="fm-run-native-observer",
        poll_interval_seconds=0.01,
    )

    with pytest.raises(
        runner.ReadinessTimeoutError,
        match="worker SQL sessions did not reach zero within the bound",
    ):
        await asyncio.wait_for(
            observer.wait_for_zero_sessions(timeout_seconds=0.01),
            timeout=0.5,
        )

    assert source.started.is_set()
    assert source.cancelled.is_set()


@pytest.mark.asyncio
async def test_sql_observer_waits_for_context_and_request_bounds() -> None:
    runner = _load_production_framework_runner()
    prefix = "fm-native-observer"
    observer_name = f"{prefix}-observer"
    rows = _observer_rows(prefix, observer_name)
    source = _FakeObserverSource([[], rows, rows, []])
    observer = runner.SqlServerObserver(
        source=source,
        worker_prefix=prefix,
        observer_application_name=observer_name,
        poll_interval_seconds=0.001,
    )

    appeared = await observer.wait_for_context_token(
        "cancel_token_42",
        present=True,
        timeout_seconds=0.1,
    )
    busy = await observer.wait_for_minimum_requests(
        2,
        timeout_seconds=0.1,
    )
    disappeared = await observer.wait_for_context_token(
        "cancel_token_42",
        present=False,
        timeout_seconds=0.1,
    )

    assert "cancel_token_42" in appeared.request_context_tokens
    assert busy.current_requests >= 2
    assert "cancel_token_42" not in disappeared.request_context_tokens

    with pytest.raises(runner.RunnerConfigurationError):
        await observer.wait_for_context_token(
            "unsafe token",
            present=True,
            timeout_seconds=0.1,
        )
    with pytest.raises(runner.RunnerConfigurationError):
        await observer.wait_for_minimum_requests(0, timeout_seconds=0.1)


@pytest.mark.asyncio
async def test_sql_observer_waits_for_every_ready_worker_identity() -> None:
    runner = _load_production_framework_runner()
    prefix = "fm-native-workers"
    observer_name = f"{prefix}-observer"
    rows = _observer_rows(prefix, observer_name)
    source = _FakeObserverSource([[], rows])
    observer = runner.SqlServerObserver(
        source=source,
        worker_prefix=prefix,
        observer_application_name=observer_name,
        poll_interval_seconds=0.001,
    )
    ready_records = (
        {
            "phase": "ready",
            "pid": 101,
            "worker_application_name": f"{prefix}-101",
        },
        {
            "phase": "ready",
            "pid": 202,
            "worker_application_name": f"{prefix}-202",
        },
    )

    sample = await observer.wait_for_ready_workers(
        ready_records,
        timeout_seconds=0.1,
    )

    assert runner.reconcile_worker_sessions(sample, ready_records) == (
        (f"{prefix}-101", 101),
        (f"{prefix}-202", 202),
    )


@pytest.mark.asyncio
async def test_async_http_json_is_status_aware_bounded_and_private() -> None:
    runner = _load_production_framework_runner()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/saturated/19"
        return httpx.Response(503, json={"error": "saturated"})

    async def private_handler(request: httpx.Request) -> httpx.Response:
        assert (
            request.headers["x-fastmssql-context-token"]
            == "graceful_query_token_42"
        )
        return await handler(request)

    response = await runner.http_request_json(
        8123,
        "/saturated/19",
        method="POST",
        expected_statuses=(503,),
        context_token="graceful_query_token_42",
        transport=httpx.MockTransport(private_handler),
    )

    assert response.status_code == 503
    assert response.payload == {"error": "saturated"}
    assert response.elapsed_seconds >= 0
    assert response.to_record() == {
        "elapsed_seconds": response.elapsed_seconds,
        "payload": {"error": "saturated"},
        "status_code": 503,
    }

    with pytest.raises(
        runner.HttpProbeError,
        match="loopback HTTP probe returned status 503",
    ):
        await runner.http_request_json(
            8123,
            "/saturated/19",
            method="POST",
            transport=httpx.MockTransport(handler),
        )

    async def oversized_handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            content=b"{" + b"x" * (runner.MAX_HTTP_PROBE_BYTES + 1) + b"}",
        )

    with pytest.raises(
        runner.HttpProbeError,
        match="loopback HTTP response exceeded its byte limit",
    ):
        await runner.http_request_json(
            8123,
            "/oversized",
            transport=httpx.MockTransport(oversized_handler),
        )


@pytest.mark.asyncio
async def test_raw_http_request_stays_open_until_explicit_private_close() -> None:
    runner = _load_production_framework_runner()
    received: asyncio.Queue[bytes] = asyncio.Queue()
    peer_closed = asyncio.Event()

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            await received.put(request)
            assert await reader.read() == b""
            peer_closed.set()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    try:
        request = await runner.open_raw_http_request(
            port,
            "/cancel/cancel_token_42",
            timeout_seconds=0.2,
        )
        raw = await asyncio.wait_for(received.get(), timeout=0.2)
        assert raw.startswith(b"GET /cancel/cancel_token_42 HTTP/1.1\r\n")
        assert b"Connection: close\r\n" in raw
        assert not peer_closed.is_set()
        assert "cancel_token_42" not in repr(request)

        await request.close(timeout_seconds=0.2)
        await asyncio.wait_for(peer_closed.wait(), timeout=0.2)
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_http_ndjson_client_validates_incremental_order_and_digest() -> None:
    runner = _load_production_framework_runner()
    first = b'{"result_set":0,"row":{"value":1}}\n'
    second = b'{"result_set":0,"row":{"value":2}}\n'

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/x-ndjson\r\n"
                + f"Content-Length: {len(first) + len(second)}\r\n".encode(
                    "ascii"
                )
                + b"Connection: close\r\n\r\n"
            )
            writer.write(first)
            await writer.drain()
            await asyncio.sleep(0.02)
            writer.write(second)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    try:
        observation = await runner.consume_ndjson_stream(
            port,
            "/stream?rows=2",
            expected_rows=2,
            timeout_seconds=1,
        )
    finally:
        server.close()
        await server.wait_closed()

    assert observation.status_code == 200
    assert observation.row_count == 2
    assert observation.value_digest == hashlib.sha256(b"1\n2\n").hexdigest()
    assert observation.bytes_received == len(first) + len(second)
    assert 0 <= observation.first_data_seconds < observation.elapsed_seconds


@pytest.mark.asyncio
async def test_http_ndjson_prefix_close_closes_the_real_peer() -> None:
    runner = _load_production_framework_runner()
    peer_closed = asyncio.Event()
    body = b"".join(
        f'{{"result_set":0,"row":{{"value":{value}}}}}\n'.encode("ascii")
        for value in range(1, 101)
    )

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/x-ndjson\r\n"
                + f"Content-Length: {len(body)}\r\n".encode("ascii")
                + b"Connection: close\r\n\r\n"
            )
            for value in range(1, 4):
                writer.write(
                    f'{{"result_set":0,"row":{{"value":{value}}}}}\n'.encode(
                        "ascii"
                    )
                )
                await writer.drain()
                await asyncio.sleep(0.005)
            assert await reader.read() == b""
            peer_closed.set()
        finally:
            writer.close()
            await writer.wait_closed()

    callbacks: list[str] = []

    async def before_read() -> None:
        callbacks.append("response-open")

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    try:
        observation = await runner.read_ndjson_prefix_and_close(
            port,
            "/stream?rows=100",
            requested_rows=100,
            prefix_rows=3,
            timeout_seconds=1,
            before_read=before_read,
        )
        await asyncio.wait_for(peer_closed.wait(), timeout=1)
    finally:
        server.close()
        await server.wait_closed()

    assert callbacks == ["response-open"]
    assert observation.status_code == 200
    assert observation.prefix_rows == 3
    assert observation.prefix_digest == hashlib.sha256(b"1\n2\n3\n").hexdigest()
    assert 0 <= observation.first_data_seconds < observation.close_seconds


@pytest.mark.asyncio
async def test_http_ndjson_clients_enforce_one_global_deadline() -> None:
    runner = _load_production_framework_runner()
    rows = tuple(
        f'{{"result_set":0,"row":{{"value":{value}}}}}\n'.encode("ascii")
        for value in range(1, 4)
    )

    async def assert_global_deadline(client_call) -> None:
        handler_done = asyncio.Event()

        async def handler(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            try:
                await reader.readuntil(b"\r\n\r\n")
                writer.write(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/x-ndjson\r\n"
                    + f"Content-Length: {sum(map(len, rows))}\r\n".encode(
                        "ascii"
                    )
                    + b"Connection: close\r\n\r\n"
                )
                for index, row in enumerate(rows):
                    if index:
                        await asyncio.sleep(0.04)
                    writer.write(row)
                    await writer.drain()
            except (BrokenPipeError, ConnectionResetError):
                return
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                handler_done.set()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = int(server.sockets[0].getsockname()[1])
        started = time.monotonic()
        try:
            with pytest.raises(
                runner.HttpProbeError,
                match="global deadline",
            ):
                await client_call(port)
            assert time.monotonic() - started < 0.2
        finally:
            server.close()
            await server.wait_closed()
            await asyncio.wait_for(handler_done.wait(), timeout=0.5)

    await assert_global_deadline(
        lambda port: runner.consume_ndjson_stream(
            port,
            "/stream?rows=3",
            expected_rows=3,
            timeout_seconds=0.06,
        )
    )
    await assert_global_deadline(
        lambda port: runner.read_ndjson_prefix_and_close(
            port,
            "/stream?rows=100",
            requested_rows=100,
            prefix_rows=3,
            timeout_seconds=0.06,
        )
    )


@pytest.mark.asyncio
async def test_process_rss_monitor_records_the_peak_until_stopped() -> None:
    runner = _load_production_framework_runner()
    values = iter((100, 300, 200))
    stop = asyncio.Event()
    calls: list[tuple[int, ...]] = []

    def sample(pids: tuple[int, ...]) -> int:
        calls.append(pids)
        value = next(values)
        if len(calls) == 3:
            stop.set()
        return value

    peak = await runner.monitor_process_rss(
        (101,),
        stop=stop,
        poll_interval_seconds=0.001,
        rss_sampler=sample,
    )

    assert peak == 300
    assert calls == [(101,), (101,), (101,)]


@pytest.mark.asyncio
async def test_pool_settlement_wait_is_bounded_and_requires_zero_usage() -> None:
    runner = _load_production_framework_runner()
    responses = iter(
        (
            {
                "admission": {"active": 1},
                "pid": 101,
                "pool": {"active_connections": 1, "pending_gets": 1},
            },
            {
                "admission": {"active": 0},
                "pid": 101,
                "pool": {"active_connections": 0, "pending_gets": 0},
            },
        )
    )

    async def request_json(port: int, path: str, **kwargs):
        assert port == 8123
        assert path == "/pool"
        assert kwargs["timeout_seconds"] > 0
        return runner.LoopbackJsonResponse(
            status_code=200,
            payload=next(responses),
            elapsed_seconds=0.001,
        )

    settled = await runner.wait_for_pool_settlement(
        8123,
        expected_pid=101,
        timeout_seconds=0.1,
        poll_interval_seconds=0.001,
        request_json=request_json,
    )

    assert settled["pool"] == {
        "active_connections": 0,
        "pending_gets": 0,
    }


@pytest.mark.asyncio
async def test_saturation_wait_requires_exact_pool_and_admission_bounds() -> None:
    runner = _load_production_framework_runner()
    responses = iter(
        (
            {
                "admission": {"active": 2, "capacity": 4, "rejected": 0},
                "pid": 101,
                "pool": {
                    "active_connections": 2,
                    "connections": 2,
                    "idle_connections": 0,
                    "max_size": 2,
                    "pending_gets": 0,
                },
            },
            {
                "admission": {"active": 4, "capacity": 4, "rejected": 0},
                "pid": 101,
                "pool": {
                    "active_connections": 2,
                    "connections": 2,
                    "idle_connections": 0,
                    "max_size": 2,
                    "pending_gets": 2,
                },
            },
        )
    )

    async def request_json(port: int, path: str, **kwargs):
        assert port == 8123
        assert path == "/pool"
        assert kwargs["expected_statuses"] == (200,)
        assert kwargs["timeout_seconds"] > 0
        return runner.LoopbackJsonResponse(
            status_code=200,
            payload=next(responses),
            elapsed_seconds=0.001,
        )

    saturated = await runner.wait_for_saturation_state(
        8123,
        expected_pid=101,
        pool_max=2,
        expected_waiters=2,
        timeout_seconds=0.1,
        poll_interval_seconds=0.001,
        request_json=request_json,
    )

    assert saturated["admission"] == {
        "active": 4,
        "capacity": 4,
        "rejected": 0,
    }
    assert saturated["pool"]["pending_gets"] == 2


@pytest.mark.asyncio
async def test_transaction_phase_wait_is_bounded_and_identity_strict(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    item_id = 84_271
    outcome = "commit"
    token = f"transaction:{item_id}:{outcome}"
    record = {
        "context_token_sha256": hashlib.sha256(token.encode("ascii")).hexdigest(),
        "item_id": item_id,
        "outcome": outcome,
        "phase": "transaction",
        "pid": 101,
        "run_id": "native-graceful-transaction-commit",
        "transaction_phase": "holding",
        "worker_application_name": "fm-native-transaction-commit-101",
    }
    path = tmp_path / "transaction-holding-commit-101-84271.json"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    class FakeSupervisor:
        started_wall_time_ns = 0

        async def wait_for_readiness(self, probe, *, timeout_seconds):
            assert timeout_seconds == 0.1
            assert probe() is True

    selected = await runner.wait_for_transaction_phase_record(
        FakeSupervisor(),
        directory=tmp_path,
        expected_run_id="native-graceful-transaction-commit",
        expected_pid=101,
        expected_worker_application_name="fm-native-transaction-commit-101",
        item_id=item_id,
        outcome=outcome,
        transaction_phase="holding",
        timeout_seconds=0.1,
    )

    assert selected == record
    assert token not in json.dumps(selected, sort_keys=True)

    path.write_text(
        json.dumps({**record, "run_id": "stale-run"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        runner.WorkerEvidenceError,
        match="transaction phase record is inconsistent",
    ):
        await runner.wait_for_transaction_phase_record(
            FakeSupervisor(),
            directory=tmp_path,
            expected_run_id="native-graceful-transaction-commit",
            expected_pid=101,
            expected_worker_application_name="fm-native-transaction-commit-101",
            item_id=item_id,
            outcome=outcome,
            transaction_phase="holding",
            timeout_seconds=0.1,
        )


@pytest.mark.asyncio
async def test_transaction_phase_wait_reconciles_a_fresh_post_exit_record(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    item_id = 95_381
    outcome = "rollback"
    token = f"transaction:{item_id}:{outcome}"
    record = {
        "context_token_sha256": hashlib.sha256(
            token.encode("ascii")
        ).hexdigest(),
        "item_id": item_id,
        "outcome": outcome,
        "phase": "transaction",
        "pid": 101,
        "run_id": "native-graceful-transaction-rollback",
        "transaction_phase": "settled",
        "worker_application_name": "fm-native-transaction-rollback-101",
    }
    path = tmp_path / "transaction-settled-rollback-101-95381.json"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    class ExitedSupervisor:
        started_wall_time_ns = 0

        async def wait_for_readiness(self, probe, *, timeout_seconds):
            del probe, timeout_seconds
            raise AssertionError(
                "an already-written post-exit record must be read directly"
            )

    selected = await runner.wait_for_transaction_phase_record(
        ExitedSupervisor(),
        directory=tmp_path,
        expected_run_id="native-graceful-transaction-rollback",
        expected_pid=101,
        expected_worker_application_name="fm-native-transaction-rollback-101",
        item_id=item_id,
        outcome=outcome,
        transaction_phase="settled",
        timeout_seconds=0.1,
    )

    assert selected == record
    assert token not in json.dumps(selected, sort_keys=True)


def test_disconnect_evidence_requires_retirement_recovery_and_hashes_token() -> None:
    runner = _load_production_framework_runner()
    token = "cancel_token_42"
    observed = runner.SqlObserverSample(
        applications=(
            runner.ObserverApplicationSample(
                application_name="fm-native-cancel-101",
                host_process_ids=(101,),
                sessions=1,
                requests=1,
            ),
        ),
        current_sessions=1,
        current_requests=1,
        maximum_sessions=1,
        maximum_requests=1,
        request_context_tokens=(token,),
    )
    settled = replace(
        observed,
        current_requests=0,
        request_context_tokens=(),
    )
    before_pool = {
        "active_connections": 0,
        "connections": 1,
        "connections_closed_broken": 0,
        "connections_created": 1,
        "idle_connections": 1,
        "max_size": 1,
        "pending_gets": 0,
    }
    after_pool = {
        **before_pool,
        "connections_closed_broken": 1,
        "connections_created": 2,
    }

    evidence = runner.validate_disconnect_evidence(
        token=token,
        observed_sample=observed,
        settled_sample=settled,
        before_pool=before_pool,
        after_pool=after_pool,
        recovery_payload={"session_id": 52, "value": 36},
    )

    record = evidence.to_record()
    assert record == {
        "connection_replaced": True,
        "context_token_sha256": hashlib.sha256(token.encode("ascii")).hexdigest(),
        "pool_active_after": 0,
        "pool_pending_after": 0,
        "recovery_value": 36,
        "sql_observed_before_close": True,
        "sql_requests_after": 0,
        "status": "PASS",
        "transport": "raw-tcp-client-close",
    }
    assert token not in json.dumps(record, sort_keys=True)

    with pytest.raises(
        runner.WorkerEvidenceError,
        match="replacement counters",
    ):
        runner.validate_disconnect_evidence(
            token=token,
            observed_sample=observed,
            settled_sample=settled,
            before_pool=before_pool,
            after_pool=before_pool,
            recovery_payload={"session_id": 52, "value": 36},
        )


def test_graceful_query_evidence_binds_active_sql_to_the_completed_response() -> None:
    runner = _load_production_framework_runner()
    token = "graceful_query_token_42"
    observed = runner.SqlObserverSample(
        applications=(
            runner.ObserverApplicationSample(
                application_name="fm-native-graceful-query-101",
                host_process_ids=(101,),
                sessions=1,
                requests=1,
            ),
        ),
        current_sessions=1,
        current_requests=1,
        maximum_sessions=1,
        maximum_requests=1,
        request_context_tokens=(token,),
    )

    evidence = runner.validate_graceful_query_evidence(
        token=token,
        observed_sample=observed,
        response_payload={
            "delay_ms": 250,
            "session_id": 52,
            "value": 73,
        },
        expected_value=73,
        sql_delay_ms=250,
    )

    record = evidence.to_record()
    assert record == {
        "context_token_sha256": hashlib.sha256(token.encode("ascii")).hexdigest(),
        "response_completed_after_signal": True,
        "response_session_id": 52,
        "response_value": 73,
        "sql_delay_seconds": 0.25,
        "sql_observed_before_signal": True,
        "status": "PASS",
    }
    assert token not in json.dumps(record, sort_keys=True)

    with pytest.raises(
        runner.WorkerEvidenceError,
        match="graceful query token",
    ):
        runner.validate_graceful_query_evidence(
            token=token,
            observed_sample=replace(observed, request_context_tokens=()),
            response_payload={
                "delay_ms": 250,
                "session_id": 52,
                "value": 73,
            },
            expected_value=73,
            sql_delay_ms=250,
        )


@pytest.mark.parametrize(
    ("outcome", "durable_rows", "durable_result"),
    (
        ("commit", ({"value": "transaction"},), "row-present"),
        ("rollback", (), "row-absent"),
    ),
)
def test_graceful_transaction_evidence_requires_the_selected_durable_outcome(
    outcome: str,
    durable_rows: tuple[dict[str, object], ...],
    durable_result: str,
) -> None:
    runner = _load_production_framework_runner()
    item_id = 84_271 if outcome == "commit" else 95_381
    token = f"transaction:{item_id}:{outcome}"
    token_sha256 = hashlib.sha256(token.encode("ascii")).hexdigest()
    base_record = {
        "context_token_sha256": token_sha256,
        "item_id": item_id,
        "outcome": outcome,
        "phase": "transaction",
        "pid": 101,
        "run_id": f"native-graceful-transaction-{outcome}",
        "worker_application_name": f"fm-native-transaction-{outcome}-101",
    }
    holding_record = {**base_record, "transaction_phase": "holding"}
    settled_record = {**base_record, "transaction_phase": "settled"}
    observed = runner.SqlObserverSample(
        applications=(
            runner.ObserverApplicationSample(
                application_name=base_record["worker_application_name"],
                host_process_ids=(101,),
                sessions=1,
                requests=1,
            ),
        ),
        current_sessions=1,
        current_requests=1,
        maximum_sessions=1,
        maximum_requests=1,
        request_context_tokens=(token,),
    )

    evidence = runner.validate_graceful_transaction_evidence(
        token=token,
        item_id=item_id,
        outcome=outcome,
        holding_record=holding_record,
        settled_record=settled_record,
        observed_sample=observed,
        response_payload={
            "item_id": item_id,
            "outcome": outcome,
            "session_id": 52,
        },
        durable_rows=durable_rows,
    )

    record = evidence.to_record()
    assert record == {
        "context_token_sha256": token_sha256,
        "durable_result": durable_result,
        "item_id": item_id,
        "outcome": outcome,
        "response_completed_after_signal": True,
        "response_session_id": 52,
        "sql_observed_before_signal": True,
        "status": "PASS",
        "transaction_holding_before_signal": True,
        "transaction_settled": True,
    }
    assert token not in json.dumps(record, sort_keys=True)

    wrong_rows = () if outcome == "commit" else ({"value": "transaction"},)
    with pytest.raises(
        runner.WorkerEvidenceError,
        match="durable outcome",
    ):
        runner.validate_graceful_transaction_evidence(
            token=token,
            item_id=item_id,
            outcome=outcome,
            holding_record=holding_record,
            settled_record=settled_record,
            observed_sample=observed,
            response_payload={
                "item_id": item_id,
                "outcome": outcome,
                "session_id": 52,
            },
            durable_rows=wrong_rows,
        )


def test_native_saturation_evidence_requires_exact_two_layer_bounds() -> None:
    runner = _load_production_framework_runner()
    application_name = "fm-native-saturation-101"
    busy = runner.SqlObserverSample(
        applications=(
            runner.ObserverApplicationSample(
                application_name=application_name,
                host_process_ids=(0,),
                sessions=2,
                requests=2,
            ),
        ),
        current_sessions=2,
        current_requests=2,
        maximum_sessions=2,
        maximum_requests=2,
        request_context_tokens=(),
    )
    baseline = {
        "admission": {"active": 0, "capacity": 4, "rejected": 0},
        "pid": 101,
        "pool": {
            "active_connections": 0,
            "connections": 1,
            "get_timed_out": 0,
            "idle_connections": 1,
            "max_size": 2,
            "pending_gets": 0,
        },
    }
    saturated = {
        "admission": {"active": 4, "capacity": 4, "rejected": 0},
        "pid": 101,
        "pool": {
            "active_connections": 2,
            "connections": 2,
            "get_timed_out": 0,
            "idle_connections": 0,
            "max_size": 2,
            "pending_gets": 2,
        },
    }
    settled = {
        "admission": {"active": 0, "capacity": 4, "rejected": 2},
        "pid": 101,
        "pool": {
            "active_connections": 0,
            "connections": 2,
            "get_timed_out": 2,
            "idle_connections": 2,
            "max_size": 2,
            "pending_gets": 0,
        },
    }
    holders = tuple(
        runner.LoopbackJsonResponse(
            200,
            {"session_id": session_id, "value": value},
            2.0,
        )
        for session_id, value in ((51, 31), (52, 37))
    )
    waiters = tuple(
        runner.LoopbackJsonResponse(
            504,
            {
                "error": "pool_acquire_timeout",
                "operation": "query",
                "phase": "acquire",
                "retryable": True,
            },
            0.5,
        )
        for _ in range(2)
    )
    rejected = tuple(
        runner.LoopbackJsonResponse(503, {"error": "saturated"}, 0.01)
        for _ in range(2)
    )
    recovery = runner.LoopbackJsonResponse(
        200,
        {"session_id": 53, "value": 59},
        2.0,
    )

    evidence = runner.validate_native_saturation_evidence(
        expected_pid=101,
        expected_application_name=application_name,
        holder_values=(31, 37),
        waiter_values=(41, 43),
        excess_values=(47, 49),
        recovery_value=59,
        acquire_timeout_ms=500,
        busy_sample=busy,
        baseline_pool_record=baseline,
        saturated_pool_record=saturated,
        settled_pool_record=settled,
        holder_responses=holders,
        waiter_responses=waiters,
        rejection_responses=rejected,
        recovery_response=recovery,
    )

    assert evidence.to_record() == {
        "acquire_timeouts": 2,
        "admission_active_after": 0,
        "admission_capacity": 4,
        "admitted_holders": 2,
        "admitted_waiters": 2,
        "maximum_sql_requests": 2,
        "maximum_sql_sessions": 2,
        "observed_active_connections": 2,
        "observed_pending_gets": 2,
        "pool_active_after": 0,
        "pool_get_timed_out_delta": 2,
        "pool_max_per_worker": 2,
        "pool_pending_after": 0,
        "recovery_value": 59,
        "rejected_requests": 2,
        "status": "PASS",
    }

    malformed = {
        **saturated,
        "pool": {**saturated["pool"], "pending_gets": 1},
    }
    with pytest.raises(
        runner.WorkerEvidenceError,
        match="saturation bounds",
    ):
        runner.validate_native_saturation_evidence(
            expected_pid=101,
            expected_application_name=application_name,
            holder_values=(31, 37),
            waiter_values=(41, 43),
            excess_values=(47, 49),
            recovery_value=59,
            acquire_timeout_ms=500,
            busy_sample=busy,
            baseline_pool_record=baseline,
            saturated_pool_record=malformed,
            settled_pool_record=settled,
            holder_responses=holders,
            waiter_responses=waiters,
            rejection_responses=rejected,
            recovery_response=recovery,
        )


def test_native_streaming_evidence_requires_incremental_bounded_recovery() -> None:
    runner = _load_production_framework_runner()
    full = runner.FullNdjsonObservation(
        status_code=200,
        content_type="application/x-ndjson; charset=utf-8",
        row_count=3,
        value_digest=hashlib.sha256(b"1\n2\n3\n").hexdigest(),
        bytes_received=108,
        first_data_seconds=0.01,
        elapsed_seconds=0.10,
    )
    early = runner.EarlyCloseNdjsonObservation(
        status_code=200,
        content_type="application/x-ndjson; charset=utf-8",
        requested_rows=100,
        prefix_rows=3,
        prefix_digest=hashlib.sha256(b"1\n2\n3\n").hexdigest(),
        bytes_received=108,
        first_data_seconds=0.01,
        close_seconds=0.05,
    )
    settled_pool = {
        "admission": {"active": 0, "capacity": 8, "rejected": 0},
        "pid": 101,
        "pool": {
            "active_connections": 0,
            "connections": 4,
            "idle_connections": 4,
            "max_size": 4,
            "pending_gets": 0,
        },
    }
    idle = runner.SqlObserverSample(
        applications=(
            runner.ObserverApplicationSample(
                application_name="fm-native-streaming-101",
                host_process_ids=(0,),
                sessions=4,
                requests=0,
            ),
        ),
        current_sessions=4,
        current_requests=0,
        maximum_sessions=4,
        maximum_requests=1,
        request_context_tokens=(),
    )
    recovery = runner.LoopbackJsonResponse(
        200,
        {"session_id": 53, "value": 73},
        0.01,
    )

    evidence = runner.validate_native_streaming_evidence(
        expected_pid=101,
        pool_max=4,
        full_observation=full,
        early_observation=early,
        driver_buffer_rows=8,
        rss_start_bytes=1_000,
        rss_peak_bytes=1_200,
        rss_end_bytes=1_100,
        rss_growth_limit_bytes=500,
        full_settled_pool_record=settled_pool,
        early_settled_pool_record=settled_pool,
        early_settled_sample=idle,
        recovery_value=73,
        recovery_response=recovery,
    )

    assert evidence.to_record() == {
        "driver_buffer_rows": 8,
        "early_bytes_received": 108,
        "early_client_closed": True,
        "early_close_seconds": 0.05,
        "early_first_data_seconds": 0.01,
        "early_pool_active_after": 0,
        "early_pool_pending_after": 0,
        "early_prefix_digest": early.prefix_digest,
        "early_prefix_rows": 3,
        "early_requested_rows": 100,
        "early_sql_requests_after": 0,
        "full_bytes_received": 108,
        "full_elapsed_seconds": 0.10,
        "full_first_data_seconds": 0.01,
        "full_pool_active_after": 0,
        "full_pool_pending_after": 0,
        "full_rows": 3,
        "full_value_digest": full.value_digest,
        "incremental_first_data": True,
        "recovery_value": 73,
        "rss_end_bytes": 1_100,
        "rss_growth_bytes": 200,
        "rss_growth_limit_bytes": 500,
        "rss_peak_bytes": 1_200,
        "rss_start_bytes": 1_000,
        "status": "PASS",
    }

    with pytest.raises(
        runner.WorkerEvidenceError,
        match="RSS",
    ):
        runner.validate_native_streaming_evidence(
            expected_pid=101,
            pool_max=4,
            full_observation=full,
            early_observation=early,
            driver_buffer_rows=8,
            rss_start_bytes=1_000,
            rss_peak_bytes=1_600,
            rss_end_bytes=1_100,
            rss_growth_limit_bytes=500,
            full_settled_pool_record=settled_pool,
            early_settled_pool_record=settled_pool,
            early_settled_sample=idle,
            recovery_value=73,
            recovery_response=recovery,
        )


@pytest.mark.asyncio
async def test_disconnect_scenario_closes_peer_only_after_sql_is_observed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=8,
    )
    profile = replace(
        _profile_by_family(
            runner,
            "fastapi-uvicorn-asyncio",
            workers=1,
        ),
        database_mode="sql_auth",
        platform_system=config.platform_system,
    )
    isolated = runner.prepare_isolated_application(
        config,
        source_directory=ROOT / "tests/production_framework",
        directory_name="native-disconnect-test",
    )
    settings = runner.SqlAuthObserverSettings(
        host="127.0.0.1",
        port=14334,
        database="fastmssql_validation",
        username="fastmssql_owner",
        password="private-owner-password",
    )
    token = "cancel_token_42"
    ready = {
        "phase": "ready",
        "pid": 101,
        "run_id": "native-disconnect",
        "worker_application_name": "fm-native-disconnect-101",
    }
    observed = runner.SqlObserverSample(
        applications=(
            runner.ObserverApplicationSample(
                application_name="fm-native-disconnect-101",
                host_process_ids=(101,),
                sessions=1,
                requests=1,
            ),
        ),
        current_sessions=1,
        current_requests=1,
        maximum_sessions=1,
        maximum_requests=1,
        request_context_tokens=(token,),
    )
    settled = replace(
        observed,
        current_requests=0,
        request_context_tokens=(),
    )
    zero = replace(settled, applications=(), current_sessions=0)
    before_pool = {
        "active_connections": 0,
        "connections": 1,
        "connections_closed_broken": 0,
        "connections_created": 1,
        "idle_connections": 1,
        "max_size": 8,
        "pending_gets": 0,
    }
    after_pool = {
        **before_pool,
        "connections_closed_broken": 1,
        "connections_created": 2,
    }
    events: list[str] = []

    class FakeConnection:
        async def connect(self, *, validate: bool) -> None:
            assert validate is True
            events.append("observer-connect")

        async def disconnect(self) -> None:
            events.append("observer-disconnect")

    class FakeObserver:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def wait_for_ready_workers(self, records, *, timeout_seconds):
            assert tuple(records) == (ready,)
            assert timeout_seconds > 0
            return settled

        async def wait_for_context_token(self, selected, *, present, timeout_seconds):
            assert selected == token
            assert timeout_seconds > 0
            events.append("sql-observed" if present else "sql-settled")
            return observed if present else settled

        async def wait_for_zero_sessions(self, *, timeout_seconds):
            assert timeout_seconds > 0
            return zero

    class FakeRawRequest:
        async def close(self, *, timeout_seconds):
            assert timeout_seconds > 0
            events.append("peer-close")

    outcome = runner.ProcessOutcome(
        pid=90,
        returncode=0,
        stdout="",
        stderr="",
        output_truncated=False,
        descendant_pids=(101,),
        graceful_stop=True,
        forced_cleanup=False,
    )

    class FakeSupervisor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            del args

        async def wait_for_worker_records(self, **kwargs):
            assert kwargs["expected_count"] == 1
            return (ready,)

        def read_worker_records(self, **kwargs):
            assert kwargs["phase"] == "shutdown"
            return ({**ready, "phase": "shutdown"},)

        async def stop(self):
            events.append("server-stop")
            return outcome

    async def fake_launch(**kwargs):
        assert await kwargs["readiness_probe"](8125)
        return runner.ServerLaunch(
            supervisor=FakeSupervisor(),
            port=8125,
            attempts=1,
            sanitized_command=(sys.executable, "-m", "uvicorn"),
        )

    async def fake_get(port: int, path: str, **kwargs):
        assert port == 8125
        assert path == "/ready"
        assert kwargs["timeout_seconds"] > 0
        return {"pid": 101, "state": "ready"}

    request_count = 0

    async def fake_request(port: int, path: str, **kwargs):
        nonlocal request_count
        assert port == 8125
        assert kwargs["expected_statuses"] == (200,)
        request_count += 1
        if path == "/pool":
            payload = {"admission": {"active": 0}, "pid": 101, "pool": before_pool}
        else:
            assert path == "/value/36"
            events.append("recovery")
            payload = {"session_id": 52, "value": 36}
        return runner.LoopbackJsonResponse(200, payload, 0.001)

    settlements = iter(
        (
            {"admission": {"active": 0}, "pid": 101, "pool": {**after_pool, "connections": 0}},
            {"admission": {"active": 0}, "pid": 101, "pool": after_pool},
        )
    )

    async def fake_settlement(port: int, **kwargs):
        assert port == 8125
        assert kwargs["expected_pid"] == 101
        return next(settlements)

    async def fake_raw(port: int, path: str, **kwargs):
        assert port == 8125
        assert path == f"/cancel/{token}"
        assert kwargs["timeout_seconds"] > 0
        events.append("peer-open")
        return FakeRawRequest()

    async def fake_port_not_listening(port: int) -> bool:
        assert port == 8125
        return False

    monkeypatch.setattr(runner, "create_observer_connection", lambda *a, **k: FakeConnection())
    monkeypatch.setattr(runner, "SqlServerObserver", FakeObserver)
    monkeypatch.setattr(runner, "launch_with_port_retry", fake_launch)
    monkeypatch.setattr(runner, "http_get_json", fake_get)
    monkeypatch.setattr(runner, "http_request_json", fake_request)
    monkeypatch.setattr(runner, "open_raw_http_request", fake_raw)
    monkeypatch.setattr(runner, "wait_for_pool_settlement", fake_settlement)
    monkeypatch.setattr(runner, "loopback_port_is_listening", fake_port_not_listening)

    result = await runner.run_native_disconnect_scenario(
        config,
        isolated,
        profile,
        run_id="native-disconnect",
        policy=runner.SupervisorPolicy(),
        sql_auth_settings=settings,
        table_name="framework_items_native",
        token=token,
    )

    record = result.to_record()
    assert record["status"] == "PASS"
    assert record["recovery_value"] == 36
    assert record["sessions_after"] == 0
    assert request_count == 2
    assert events.index("sql-observed") < events.index("peer-close")
    assert events.index("peer-close") < events.index("recovery")
    assert events[-1] == "observer-disconnect"


@pytest.mark.asyncio
async def test_graceful_query_scenario_signals_only_after_sql_is_observed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=8,
    )
    profile = replace(
        _profile_by_family(
            runner,
            "fastapi-uvicorn-asyncio",
            workers=1,
        ),
        database_mode="sql_auth",
        platform_system=config.platform_system,
    )
    isolated = runner.prepare_isolated_application(
        config,
        source_directory=ROOT / "tests/production_framework",
        directory_name="native-graceful-query-test",
    )
    settings = runner.SqlAuthObserverSettings(
        host="127.0.0.1",
        port=14334,
        database="fastmssql_validation",
        username="fastmssql_owner",
        password="private-owner-password",
    )
    token = "graceful_query_token_42"
    ready = {
        "phase": "ready",
        "pid": 101,
        "run_id": "native-graceful-query",
        "worker_application_name": "fm-native-graceful-query-101",
    }
    observed = runner.SqlObserverSample(
        applications=(
            runner.ObserverApplicationSample(
                application_name="fm-native-graceful-query-101",
                host_process_ids=(101,),
                sessions=1,
                requests=1,
            ),
        ),
        current_sessions=1,
        current_requests=1,
        maximum_sessions=1,
        maximum_requests=1,
        request_context_tokens=(token,),
    )
    zero = replace(
        observed,
        applications=(),
        current_sessions=0,
        current_requests=0,
        request_context_tokens=(),
    )
    events: list[str] = []
    signal_sent = asyncio.Event()
    response_completed = asyncio.Event()

    class FakeConnection:
        async def connect(self, *, validate: bool) -> None:
            assert validate is True
            events.append("observer-connect")

        async def disconnect(self) -> None:
            events.append("observer-disconnect")

    class FakeObserver:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def wait_for_ready_workers(self, records, *, timeout_seconds):
            assert tuple(records) == (ready,)
            assert timeout_seconds > 0
            return observed

        async def wait_for_context_token(self, selected, *, present, timeout_seconds):
            assert selected == token
            assert present is True
            assert timeout_seconds > 0
            events.append("sql-observed")
            return observed

        async def wait_for_zero_sessions(self, *, timeout_seconds):
            assert timeout_seconds > 0
            return zero

    outcome = runner.ProcessOutcome(
        pid=90,
        returncode=-signal.SIGTERM,
        stdout="",
        stderr="",
        output_truncated=False,
        descendant_pids=(101,),
        graceful_stop=True,
        forced_cleanup=False,
    )

    class FakeSupervisor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            del args

        async def wait_for_worker_records(self, **kwargs):
            assert kwargs["expected_count"] == 1
            return (ready,)

        def read_worker_records(self, **kwargs):
            assert kwargs["phase"] == "shutdown"
            return ({**ready, "phase": "shutdown"},)

        async def stop(self):
            events.append("signal-sent")
            signal_sent.set()
            await response_completed.wait()
            events.append("server-exit")
            return outcome

    async def fake_launch(**kwargs):
        assert await kwargs["readiness_probe"](8126)
        return runner.ServerLaunch(
            supervisor=FakeSupervisor(),
            port=8126,
            attempts=1,
            sanitized_command=(sys.executable, "-m", "uvicorn"),
        )

    async def fake_get(port: int, path: str, **kwargs):
        assert port == 8126
        assert path == "/ready"
        assert kwargs["timeout_seconds"] > 0
        return {"pid": 101, "state": "ready"}

    async def fake_request(port: int, path: str, **kwargs):
        assert port == 8126
        assert path == "/wait/73"
        assert kwargs["expected_statuses"] == (200,)
        assert kwargs["context_token"] == token
        assert kwargs["timeout_seconds"] > 0
        events.append("request-start")
        await signal_sent.wait()
        events.append("response-complete")
        response_completed.set()
        return runner.LoopbackJsonResponse(
            200,
            {"delay_ms": 250, "session_id": 52, "value": 73},
            0.25,
        )

    async def fake_port_not_listening(port: int) -> bool:
        assert port == 8126
        return False

    monkeypatch.setattr(
        runner,
        "create_observer_connection",
        lambda *args, **kwargs: FakeConnection(),
    )
    monkeypatch.setattr(runner, "SqlServerObserver", FakeObserver)
    monkeypatch.setattr(runner, "launch_with_port_retry", fake_launch)
    monkeypatch.setattr(runner, "http_get_json", fake_get)
    monkeypatch.setattr(runner, "http_request_json", fake_request)
    monkeypatch.setattr(
        runner,
        "loopback_port_is_listening",
        fake_port_not_listening,
    )

    result = await runner.run_native_graceful_query_scenario(
        config,
        isolated,
        profile,
        run_id="native-graceful-query",
        policy=runner.SupervisorPolicy(),
        sql_auth_settings=settings,
        table_name="framework_items_native",
        token=token,
        value=73,
        sql_delay_ms=250,
    )

    record = result.to_record()
    assert record["status"] == "PASS"
    assert record["graceful_stop"] is True
    assert record["returncode"] == -signal.SIGTERM
    assert record["response_value"] == 73
    assert record["sessions_after"] == 0
    assert events.index("request-start") < events.index("sql-observed")
    assert events.index("sql-observed") < events.index("signal-sent")
    assert events.index("signal-sent") < events.index("response-complete")
    assert events.index("response-complete") < events.index("server-exit")
    assert events[-1] == "observer-disconnect"


@pytest.mark.parametrize(
    ("outcome", "item_id", "durable_rows"),
    (
        ("commit", 84_271, ({"value": "transaction"},)),
        ("rollback", 95_381, ()),
    ),
)
@pytest.mark.asyncio
async def test_graceful_transaction_scenario_proves_the_durable_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    item_id: int,
    durable_rows: tuple[dict[str, object], ...],
) -> None:
    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=8,
    )
    profile = replace(
        _profile_by_family(
            runner,
            "fastapi-uvicorn-asyncio",
            workers=1,
        ),
        database_mode="sql_auth",
        platform_system=config.platform_system,
    )
    isolated = runner.prepare_isolated_application(
        config,
        source_directory=ROOT / "tests/production_framework",
        directory_name=f"native-graceful-transaction-{outcome}-test",
    )
    settings = runner.SqlAuthObserverSettings(
        host="127.0.0.1",
        port=14334,
        database="fastmssql_validation",
        username="fastmssql_owner",
        password="private-owner-password",
    )
    run_id = f"native-graceful-transaction-{outcome}"
    token = f"transaction:{item_id}:{outcome}"
    worker_application_name = f"fm-native-transaction-{outcome}-101"
    ready = {
        "phase": "ready",
        "pid": 101,
        "run_id": run_id,
        "worker_application_name": worker_application_name,
    }
    observed = runner.SqlObserverSample(
        applications=(
            runner.ObserverApplicationSample(
                application_name=worker_application_name,
                host_process_ids=(101,),
                sessions=1,
                requests=1,
            ),
        ),
        current_sessions=1,
        current_requests=1,
        maximum_sessions=1,
        maximum_requests=1,
        request_context_tokens=(token,),
    )
    zero = replace(
        observed,
        applications=(),
        current_sessions=0,
        current_requests=0,
        request_context_tokens=(),
    )
    base_phase_record = {
        "context_token_sha256": hashlib.sha256(token.encode("ascii")).hexdigest(),
        "item_id": item_id,
        "outcome": outcome,
        "phase": "transaction",
        "pid": 101,
        "run_id": run_id,
        "worker_application_name": worker_application_name,
    }
    events: list[str] = []
    signal_sent = asyncio.Event()
    response_completed = asyncio.Event()

    class FakeQueryResult:
        def all(self):
            return list(durable_rows)

    class FakeConnection:
        async def connect(self, *, validate: bool) -> None:
            assert validate is True
            events.append("observer-connect")

        async def execute(self, sql: str) -> None:
            if sql.startswith("DROP TABLE"):
                events.append("fixture-drop")
            else:
                assert sql.startswith("CREATE TABLE")
                events.append("fixture-create")

        async def query(self, sql: str, params: list[object]):
            assert "WHERE [id] = @P1" in sql
            assert params == [item_id]
            events.append("durable-read")
            return FakeQueryResult()

        async def disconnect(self) -> None:
            events.append("observer-disconnect")

    class FakeObserver:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def wait_for_ready_workers(self, records, *, timeout_seconds):
            assert tuple(records) == (ready,)
            assert timeout_seconds > 0
            return observed

        async def wait_for_context_token(self, selected, *, present, timeout_seconds):
            assert selected == token
            assert present is True
            assert timeout_seconds > 0
            events.append("sql-observed")
            return observed

        async def wait_for_zero_sessions(self, *, timeout_seconds):
            assert timeout_seconds > 0
            return zero

    process_outcome = runner.ProcessOutcome(
        pid=90,
        returncode=-signal.SIGTERM,
        stdout="",
        stderr="",
        output_truncated=False,
        descendant_pids=(101,),
        graceful_stop=True,
        forced_cleanup=False,
    )

    class FakeSupervisor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            del args

        async def wait_for_worker_records(self, **kwargs):
            assert kwargs["expected_count"] == 1
            return (ready,)

        def read_worker_records(self, **kwargs):
            assert kwargs["phase"] == "shutdown"
            return ({**ready, "phase": "shutdown"},)

        async def stop(self):
            events.append("signal-sent")
            signal_sent.set()
            await response_completed.wait()
            events.append("server-exit")
            return process_outcome

    async def fake_launch(**kwargs):
        assert await kwargs["readiness_probe"](8127)
        return runner.ServerLaunch(
            supervisor=FakeSupervisor(),
            port=8127,
            attempts=1,
            sanitized_command=(sys.executable, "-m", "uvicorn"),
        )

    async def fake_get(port: int, path: str, **kwargs):
        assert port == 8127
        assert path == "/ready"
        assert kwargs["timeout_seconds"] > 0
        return {"pid": 101, "state": "ready"}

    async def fake_request(port: int, path: str, **kwargs):
        assert port == 8127
        assert path == f"/transaction/{item_id}?outcome={outcome}"
        assert kwargs["method"] == "POST"
        assert kwargs["expected_statuses"] == (200,)
        events.append("request-start")
        await signal_sent.wait()
        events.append("response-complete")
        response_completed.set()
        return runner.LoopbackJsonResponse(
            200,
            {"item_id": item_id, "outcome": outcome, "session_id": 52},
            0.25,
        )

    async def fake_phase_record(supervisor, **kwargs):
        del supervisor
        assert kwargs["expected_run_id"] == run_id
        assert kwargs["expected_pid"] == 101
        assert kwargs["expected_worker_application_name"] == worker_application_name
        assert kwargs["item_id"] == item_id
        assert kwargs["outcome"] == outcome
        phase = kwargs["transaction_phase"]
        events.append(f"phase-{phase}")
        return {**base_phase_record, "transaction_phase": phase}

    async def fake_port_not_listening(port: int) -> bool:
        assert port == 8127
        return False

    connection = FakeConnection()
    monkeypatch.setattr(
        runner,
        "create_observer_connection",
        lambda *args, **kwargs: connection,
    )
    monkeypatch.setattr(runner, "SqlServerObserver", FakeObserver)
    monkeypatch.setattr(runner, "launch_with_port_retry", fake_launch)
    monkeypatch.setattr(runner, "http_get_json", fake_get)
    monkeypatch.setattr(runner, "http_request_json", fake_request)
    monkeypatch.setattr(
        runner,
        "wait_for_transaction_phase_record",
        fake_phase_record,
    )
    monkeypatch.setattr(
        runner,
        "loopback_port_is_listening",
        fake_port_not_listening,
    )

    result = await runner.run_native_graceful_transaction_scenario(
        config,
        isolated,
        profile,
        run_id=run_id,
        policy=runner.SupervisorPolicy(),
        sql_auth_settings=settings,
        table_name=f"framework_tx_{outcome}_test",
        item_id=item_id,
        outcome=outcome,
        sql_delay_ms=250,
    )

    record = result.to_record()
    assert record["status"] == "PASS"
    assert record["outcome"] == outcome
    assert record["durable_result"] == (
        "row-present" if outcome == "commit" else "row-absent"
    )
    assert record["graceful_stop"] is True
    assert record["sessions_after"] == 0
    assert events.index("request-start") < events.index("phase-holding")
    assert events.index("phase-holding") < events.index("sql-observed")
    assert events.index("sql-observed") < events.index("signal-sent")
    assert events.index("signal-sent") < events.index("response-complete")
    assert events.index("response-complete") < events.index("server-exit")
    assert events.index("server-exit") < events.index("phase-settled")
    assert events.index("phase-settled") < events.index("durable-read")
    assert events[-2:] == ["fixture-drop", "observer-disconnect"]


@pytest.mark.asyncio
async def test_native_saturation_scenario_bounds_waiters_and_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=2,
    )
    profile = replace(
        _profile_by_family(
            runner,
            "fastapi-uvicorn-asyncio",
            workers=1,
        ),
        database_mode="sql_auth",
        platform_system=config.platform_system,
    )
    isolated = runner.prepare_isolated_application(
        config,
        source_directory=ROOT / "tests/production_framework",
        directory_name="native-saturation-test",
    )
    settings = runner.SqlAuthObserverSettings(
        host="127.0.0.1",
        port=14334,
        database="fastmssql_validation",
        username="fastmssql_owner",
        password="private-owner-password",
    )
    run_id = "native-saturation"
    application_name = "fm-native-saturation-101"
    ready = {
        "phase": "ready",
        "pid": 101,
        "run_id": run_id,
        "worker_application_name": application_name,
    }
    busy = runner.SqlObserverSample(
        applications=(
            runner.ObserverApplicationSample(
                application_name=application_name,
                host_process_ids=(101,),
                sessions=2,
                requests=2,
            ),
        ),
        current_sessions=2,
        current_requests=2,
        maximum_sessions=2,
        maximum_requests=2,
        request_context_tokens=(),
    )
    zero = replace(
        busy,
        applications=(),
        current_sessions=0,
        current_requests=0,
    )
    baseline = {
        "admission": {"active": 0, "capacity": 4, "rejected": 0},
        "pid": 101,
        "pool": {
            "active_connections": 0,
            "connections": 1,
            "get_timed_out": 0,
            "idle_connections": 1,
            "max_size": 2,
            "pending_gets": 0,
        },
    }
    saturated = {
        "admission": {"active": 4, "capacity": 4, "rejected": 0},
        "pid": 101,
        "pool": {
            "active_connections": 2,
            "connections": 2,
            "get_timed_out": 0,
            "idle_connections": 0,
            "max_size": 2,
            "pending_gets": 2,
        },
    }
    settled = {
        "admission": {"active": 0, "capacity": 4, "rejected": 2},
        "pid": 101,
        "pool": {
            "active_connections": 0,
            "connections": 2,
            "get_timed_out": 2,
            "idle_connections": 2,
            "max_size": 2,
            "pending_gets": 0,
        },
    }
    events: list[str] = []
    release_waiters = asyncio.Event()
    release_holders = asyncio.Event()
    waiter_completions = 0
    excess_starts = 0
    settlement_calls = 0

    class FakeConnection:
        async def connect(self, *, validate: bool) -> None:
            assert validate is True
            events.append("observer-connect")

        async def disconnect(self) -> None:
            events.append("observer-disconnect")

    class FakeObserver:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def wait_for_ready_workers(self, records, *, timeout_seconds):
            assert tuple(records) == (ready,)
            assert timeout_seconds > 0
            return busy

        async def wait_for_minimum_requests(
            self,
            minimum_requests,
            *,
            timeout_seconds,
        ):
            assert minimum_requests == 2
            assert timeout_seconds > 0
            events.append("sql-holders-observed")
            return busy

        async def wait_for_zero_sessions(self, *, timeout_seconds):
            assert timeout_seconds > 0
            events.append("sql-zero")
            return zero

    process_outcome = runner.ProcessOutcome(
        pid=90,
        returncode=0,
        stdout="",
        stderr="",
        output_truncated=False,
        descendant_pids=(101,),
        graceful_stop=True,
        forced_cleanup=False,
    )

    class FakeSupervisor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            del args

        async def wait_for_worker_records(self, **kwargs):
            assert kwargs["expected_count"] == 1
            return (ready,)

        def read_worker_records(self, **kwargs):
            assert kwargs["phase"] == "shutdown"
            return ({**ready, "phase": "shutdown"},)

        async def stop(self):
            events.append("server-stop")
            return process_outcome

    async def fake_launch(**kwargs):
        assert await kwargs["readiness_probe"](8128)
        return runner.ServerLaunch(
            supervisor=FakeSupervisor(),
            port=8128,
            attempts=1,
            sanitized_command=(sys.executable, "-m", "uvicorn"),
        )

    async def fake_get(port: int, path: str, **kwargs):
        assert port == 8128
        assert path == "/ready"
        assert kwargs["timeout_seconds"] > 0
        return {"pid": 101, "state": "ready"}

    async def fake_request(port: int, path: str, **kwargs):
        nonlocal waiter_completions, excess_starts
        assert port == 8128
        assert kwargs["timeout_seconds"] > 0
        if path == "/pool":
            assert kwargs["expected_statuses"] == (200,)
            return runner.LoopbackJsonResponse(200, baseline, 0.001)
        value = int(path.rsplit("/", maxsplit=1)[1])
        if value in {31, 37}:
            assert kwargs["expected_statuses"] == (200,)
            events.append(f"holder-start-{value}")
            await release_holders.wait()
            events.append(f"holder-complete-{value}")
            return runner.LoopbackJsonResponse(
                200,
                {"session_id": 50 + (value == 37), "value": value},
                2.0,
            )
        if value in {41, 43}:
            assert kwargs["expected_statuses"] == (504,)
            events.append(f"waiter-start-{value}")
            await release_waiters.wait()
            waiter_completions += 1
            events.append(f"waiter-timeout-{value}")
            if waiter_completions == 2:
                release_holders.set()
            return runner.LoopbackJsonResponse(
                504,
                {
                    "error": "pool_acquire_timeout",
                    "operation": "query",
                    "phase": "acquire",
                    "retryable": True,
                },
                0.5,
            )
        if value in {47, 49}:
            assert kwargs["expected_statuses"] == (503,)
            excess_starts += 1
            events.append(f"rejected-{value}")
            if excess_starts == 2:
                release_waiters.set()
            return runner.LoopbackJsonResponse(
                503,
                {"error": "saturated"},
                0.01,
            )
        assert value == 59
        assert kwargs["expected_statuses"] == (200,)
        assert release_holders.is_set()
        events.append("recovery")
        return runner.LoopbackJsonResponse(
            200,
            {"session_id": 53, "value": 59},
            2.0,
        )

    async def fake_saturation_wait(port: int, **kwargs):
        assert port == 8128
        assert kwargs["expected_pid"] == 101
        assert kwargs["pool_max"] == 2
        assert kwargs["expected_waiters"] == 2
        assert kwargs["timeout_seconds"] > 0
        assert "holder-start-31" in events
        assert "holder-start-37" in events
        assert "waiter-start-41" in events
        assert "waiter-start-43" in events
        events.append("saturation-observed")
        return saturated

    async def fake_pool_settlement(port: int, **kwargs):
        nonlocal settlement_calls
        assert port == 8128
        assert kwargs["expected_pid"] == 101
        assert kwargs["timeout_seconds"] > 0
        settlement_calls += 1
        events.append(f"settled-{settlement_calls}")
        return settled

    async def fake_port_not_listening(port: int) -> bool:
        assert port == 8128
        return False

    monkeypatch.setattr(
        runner,
        "create_observer_connection",
        lambda *args, **kwargs: FakeConnection(),
    )
    monkeypatch.setattr(runner, "SqlServerObserver", FakeObserver)
    monkeypatch.setattr(runner, "launch_with_port_retry", fake_launch)
    monkeypatch.setattr(runner, "http_get_json", fake_get)
    monkeypatch.setattr(runner, "http_request_json", fake_request)
    monkeypatch.setattr(
        runner,
        "wait_for_saturation_state",
        fake_saturation_wait,
    )
    monkeypatch.setattr(
        runner,
        "wait_for_pool_settlement",
        fake_pool_settlement,
    )
    monkeypatch.setattr(
        runner,
        "loopback_port_is_listening",
        fake_port_not_listening,
    )

    result = await runner.run_native_saturation_scenario(
        config,
        isolated,
        profile,
        run_id=run_id,
        policy=runner.SupervisorPolicy(),
        sql_auth_settings=settings,
        table_name="framework_items_native",
        holder_values=(31, 37),
        waiter_values=(41, 43),
        excess_values=(47, 49),
        recovery_value=59,
        sql_delay_ms=2_000,
        acquire_timeout_ms=500,
    )

    record = result.to_record()
    assert record["status"] == "PASS"
    assert record["pool_max_per_worker"] == 2
    assert record["admitted_waiters"] == 2
    assert record["rejected_requests"] == 2
    assert record["acquire_timeouts"] == 2
    assert record["sessions_after"] == 0
    assert events.index("sql-holders-observed") < events.index(
        "saturation-observed"
    )
    assert events.index("saturation-observed") < events.index("rejected-47")
    assert events.index("rejected-49") < events.index("waiter-timeout-41")
    assert events.index("waiter-timeout-43") < events.index(
        "holder-complete-31"
    )
    assert events.index("holder-complete-37") < events.index("settled-1")
    assert events.index("settled-1") < events.index("recovery")
    assert events.index("recovery") < events.index("settled-2")
    assert events.index("settled-2") < events.index("server-stop")
    assert events[-1] == "observer-disconnect"


@pytest.mark.asyncio
async def test_native_streaming_scenario_consumes_closes_and_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=2,
    )
    profile = replace(
        _profile_by_family(
            runner,
            "fastapi-uvicorn-asyncio",
            workers=1,
        ),
        database_mode="sql_auth",
        platform_system=config.platform_system,
    )
    isolated = runner.prepare_isolated_application(
        config,
        source_directory=ROOT / "tests/production_framework",
        directory_name="native-streaming-test",
    )
    settings = runner.SqlAuthObserverSettings(
        host="127.0.0.1",
        port=14334,
        database="fastmssql_validation",
        username="fastmssql_owner",
        password="private-owner-password",
    )
    run_id = "native-streaming"
    application_name = "fm-native-streaming-101"
    ready = {
        "phase": "ready",
        "pid": 101,
        "run_id": run_id,
        "worker_application_name": application_name,
    }
    active = runner.SqlObserverSample(
        applications=(
            runner.ObserverApplicationSample(
                application_name=application_name,
                host_process_ids=(0,),
                sessions=1,
                requests=1,
            ),
        ),
        current_sessions=1,
        current_requests=1,
        maximum_sessions=1,
        maximum_requests=1,
        request_context_tokens=(),
    )
    idle = replace(active, current_requests=0)
    zero = replace(
        active,
        applications=(),
        current_sessions=0,
        current_requests=0,
    )
    settled_pool = {
        "admission": {"active": 0, "capacity": 4, "rejected": 0},
        "pid": 101,
        "pool": {
            "active_connections": 0,
            "connections": 1,
            "idle_connections": 1,
            "max_size": 2,
            "pending_gets": 0,
        },
    }
    full_observation = runner.FullNdjsonObservation(
        status_code=200,
        content_type="application/x-ndjson; charset=utf-8",
        row_count=100,
        value_digest=hashlib.sha256(
            b"".join(f"{value}\n".encode("ascii") for value in range(1, 101))
        ).hexdigest(),
        bytes_received=3_692,
        first_data_seconds=0.01,
        elapsed_seconds=0.2,
    )
    early_observation = runner.EarlyCloseNdjsonObservation(
        status_code=200,
        content_type="application/x-ndjson; charset=utf-8",
        requested_rows=1_000,
        prefix_rows=32,
        prefix_digest=hashlib.sha256(
            b"".join(f"{value}\n".encode("ascii") for value in range(1, 33))
        ).hexdigest(),
        bytes_received=1_174,
        first_data_seconds=0.01,
        close_seconds=0.05,
    )
    events: list[str] = []
    settlement_calls = 0
    rss_samples = iter((1_000, 1_100))

    class FakeConnection:
        async def connect(self, *, validate: bool) -> None:
            assert validate is True
            events.append("observer-connect")

        async def disconnect(self) -> None:
            events.append("observer-disconnect")

    class FakeObserver:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def wait_for_ready_workers(self, records, *, timeout_seconds):
            assert tuple(records) == (ready,)
            assert timeout_seconds > 0
            return active

        async def wait_for_minimum_requests(
            self,
            minimum_requests,
            *,
            timeout_seconds,
        ):
            assert minimum_requests == 1
            assert timeout_seconds > 0
            events.append("early-sql-active")
            return active

        async def wait_for_zero_requests(self, *, timeout_seconds):
            assert timeout_seconds > 0
            events.append("early-sql-idle")
            return idle

        async def wait_for_zero_sessions(self, *, timeout_seconds):
            assert timeout_seconds > 0
            events.append("sql-zero")
            return zero

    process_outcome = runner.ProcessOutcome(
        pid=90,
        returncode=0,
        stdout="",
        stderr="",
        output_truncated=False,
        descendant_pids=(101,),
        graceful_stop=True,
        forced_cleanup=False,
    )

    class FakeSupervisor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            del args

        async def wait_for_worker_records(self, **kwargs):
            assert kwargs["expected_count"] == 1
            return (ready,)

        def read_worker_records(self, **kwargs):
            assert kwargs["phase"] == "shutdown"
            return ({**ready, "phase": "shutdown"},)

        async def stop(self):
            events.append("server-stop")
            return process_outcome

    async def fake_launch(**kwargs):
        assert await kwargs["readiness_probe"](8129)
        return runner.ServerLaunch(
            supervisor=FakeSupervisor(),
            port=8129,
            attempts=1,
            sanitized_command=(sys.executable, "-m", "uvicorn"),
        )

    async def fake_get(port: int, path: str, **kwargs):
        assert port == 8129
        assert path == "/ready"
        assert kwargs["timeout_seconds"] > 0
        return {"pid": 101, "state": "ready"}

    async def fake_full_stream(port: int, path: str, **kwargs):
        assert port == 8129
        assert path == "/stream?rows=100"
        assert kwargs["expected_rows"] == 100
        assert kwargs["timeout_seconds"] > 0
        events.append("full-stream")
        return full_observation

    async def fake_early_stream(port: int, path: str, **kwargs):
        assert port == 8129
        assert path == "/stream?rows=1000"
        assert kwargs["requested_rows"] == 1_000
        assert kwargs["prefix_rows"] == 32
        assert kwargs["timeout_seconds"] > 0
        events.append("early-open")
        await kwargs["before_read"]()
        events.append("early-close")
        return early_observation

    async def fake_pool_settlement(port: int, **kwargs):
        nonlocal settlement_calls
        assert port == 8129
        assert kwargs["expected_pid"] == 101
        assert kwargs["timeout_seconds"] > 0
        settlement_calls += 1
        events.append(f"settled-{settlement_calls}")
        return settled_pool

    async def fake_request(port: int, path: str, **kwargs):
        assert port == 8129
        assert path == "/value/73"
        assert kwargs["expected_statuses"] == (200,)
        events.append("recovery")
        return runner.LoopbackJsonResponse(
            200,
            {"session_id": 53, "value": 73},
            0.01,
        )

    def fake_rss(pids: tuple[int, ...]) -> int:
        assert pids == (101,)
        value = next(rss_samples)
        events.append(f"rss-{value}")
        return value

    async def fake_rss_monitor(pids, *, stop, **kwargs):
        assert pids == (101,)
        del kwargs
        await stop.wait()
        events.append("rss-peak")
        return 1_200

    async def fake_port_not_listening(port: int) -> bool:
        assert port == 8129
        return False

    monkeypatch.setattr(
        runner,
        "create_observer_connection",
        lambda *args, **kwargs: FakeConnection(),
    )
    monkeypatch.setattr(runner, "SqlServerObserver", FakeObserver)
    monkeypatch.setattr(runner, "launch_with_port_retry", fake_launch)
    monkeypatch.setattr(runner, "http_get_json", fake_get)
    monkeypatch.setattr(runner, "consume_ndjson_stream", fake_full_stream)
    monkeypatch.setattr(
        runner,
        "read_ndjson_prefix_and_close",
        fake_early_stream,
    )
    monkeypatch.setattr(
        runner,
        "wait_for_pool_settlement",
        fake_pool_settlement,
    )
    monkeypatch.setattr(runner, "http_request_json", fake_request)
    monkeypatch.setattr(runner, "process_rss_bytes", fake_rss)
    monkeypatch.setattr(runner, "monitor_process_rss", fake_rss_monitor)
    monkeypatch.setattr(
        runner,
        "loopback_port_is_listening",
        fake_port_not_listening,
    )

    result = await runner.run_native_streaming_scenario(
        config,
        isolated,
        profile,
        run_id=run_id,
        policy=runner.SupervisorPolicy(),
        sql_auth_settings=settings,
        table_name="framework_items_native",
        full_rows=100,
        early_rows=1_000,
        early_prefix_rows=32,
        recovery_value=73,
        rss_growth_limit_bytes=500,
    )

    record = result.to_record()
    assert record["status"] == "PASS"
    assert record["full_rows"] == 100
    assert record["early_prefix_rows"] == 32
    assert record["early_sql_requests_after"] == 0
    assert record["rss_growth_bytes"] == 200
    assert record["sessions_after"] == 0
    assert events.index("full-stream") < events.index("settled-1")
    assert events.index("early-open") < events.index("early-sql-active")
    assert events.index("early-sql-active") < events.index("early-close")
    assert events.index("early-close") < events.index("early-sql-idle")
    assert events.index("early-sql-idle") < events.index("settled-2")
    assert events.index("settled-2") < events.index("recovery")
    assert events.index("recovery") < events.index("settled-3")
    assert events.index("rss-peak") < events.index("server-stop")
    assert events[-1] == "observer-disconnect"


@pytest.mark.asyncio
async def test_collect_worker_payloads_reaches_every_expected_pid() -> None:
    runner = _load_production_framework_runner()
    observed = iter(
        [
            {"pid": 202, "principal": "fastmssql_owner"},
            {"pid": 202, "principal": "fastmssql_owner"},
            {"pid": 101, "principal": "fastmssql_owner"},
            {"pid": 101, "principal": "fastmssql_owner"},
        ]
    )

    async def request_json(port: int, path: str, **kwargs):
        assert port == 8123
        assert path == "/principal"
        assert kwargs["timeout_seconds"] > 0
        return next(observed)

    payloads = await runner.collect_worker_payloads(
        8123,
        "/principal",
        expected_pids=(101, 202),
        timeout_seconds=0.1,
        request_json=request_json,
    )

    assert tuple(payload["pid"] for payload in payloads) == (101, 202)

    async def unexpected_worker(port: int, path: str, **kwargs):
        del port, path, kwargs
        return {"pid": 303}

    with pytest.raises(
        runner.WorkerEvidenceError,
        match="unexpected worker PID",
    ):
        await runner.collect_worker_payloads(
            8123,
            "/principal",
            expected_pids=(101, 202),
            timeout_seconds=0.1,
            request_json=unexpected_worker,
        )


@pytest.mark.asyncio
async def test_collect_worker_payloads_uses_bounded_parallel_probe_waves() -> None:
    runner = _load_production_framework_runner()
    released = asyncio.Event()
    active = 0
    maximum_active = 0
    call_index = 0

    async def concurrency_sensitive_request(port: int, path: str, **kwargs):
        nonlocal active, maximum_active, call_index
        assert port == 8123
        assert path == "/principal"
        assert kwargs["timeout_seconds"] > 0
        selected_index = call_index
        call_index += 1
        active += 1
        maximum_active = max(maximum_active, active)
        if active >= 2:
            released.set()
        try:
            await released.wait()
            return {
                "pid": 101 if selected_index % 2 == 0 else 202,
                "principal": "fastmssql_owner",
            }
        finally:
            active -= 1

    payloads = await runner.collect_worker_payloads(
        8123,
        "/principal",
        expected_pids=(101, 202),
        timeout_seconds=0.1,
        request_json=concurrency_sensitive_request,
    )

    assert tuple(payload["pid"] for payload in payloads) == (101, 202)
    assert maximum_active == 2


@pytest.mark.asyncio
async def test_collect_worker_payloads_accepts_only_declared_monotonic_counter(
) -> None:
    """Catch rejection of valid loop evidence or acceptance of identity drift."""

    runner = _load_production_framework_runner()
    observations = iter(
        (
            {"loop_token": 22, "pid": 202, "request_sequence": 1},
            {"loop_token": 22, "pid": 202, "request_sequence": 2},
            {"loop_token": 11, "pid": 101, "request_sequence": 2},
            {"loop_token": 11, "pid": 101, "request_sequence": 1},
        )
    )

    async def changing_counter(port: int, path: str, **kwargs):
        assert port == 8123
        assert path == "/loop"
        assert kwargs["timeout_seconds"] > 0
        return next(observations)

    payloads = await runner.collect_worker_payloads(
        8123,
        "/loop",
        expected_pids=(101, 202),
        timeout_seconds=0.1,
        request_json=changing_counter,
        monotonic_counter="request_sequence",
    )

    assert payloads == (
        {"loop_token": 11, "pid": 101, "request_sequence": 2},
        {"loop_token": 22, "pid": 202, "request_sequence": 2},
    )

    identity_drift = iter(
        (
            {"loop_token": 22, "pid": 202, "request_sequence": 1},
            {"loop_token": 99, "pid": 202, "request_sequence": 2},
        )
    )

    async def changing_identity(port: int, path: str, **kwargs):
        del port, path, kwargs
        return next(identity_drift)

    with pytest.raises(
        runner.WorkerEvidenceError,
        match="stable evidence changed",
    ):
        await runner.collect_worker_payloads(
            8123,
            "/loop",
            expected_pids=(101, 202),
            timeout_seconds=0.1,
            request_json=changing_identity,
            monotonic_counter="request_sequence",
        )


def test_native_scaling_evidence_reconciles_workers_pool_and_sql_budget(
    tmp_path: Path,
) -> None:
    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=8,
    )
    profile = replace(
        _profile_by_family(
            runner,
            "fastapi-uvicorn-asyncio",
            workers=2,
        ),
        database_mode="sql_auth",
        platform_system=config.platform_system,
    )
    ready_records = tuple(
        {
            "candidate_sha": config.candidate_sha,
            "phase": "ready",
            "pid": pid,
            "pool_connected_monotonic": 12.0 + offset,
            "pool_created_monotonic": 11.0 + offset,
            "pool_created_pid": pid,
            "pool_identity": f"pool-{pid}",
            "pool_max_per_worker": 4,
            "process_started_monotonic": 10.0 + offset,
            "worker_application_name": f"fm-native-scale-{pid}",
            "wheel_filename": config.wheel.name,
            "wheel_sha256": config.wheel_sha256,
        }
        for offset, pid in enumerate((101, 202))
    )
    package_records = tuple(
        {
            "candidate_sha": config.candidate_sha,
            "fastmssql_import_path": (
                f"/isolated/site-packages/fastmssql-{pid}.so"
            ),
            "pid": pid,
            "wheel_filename": config.wheel.name,
            "wheel_sha256": config.wheel_sha256,
        }
        for pid in (101, 202)
    )
    principal_records = tuple(
        {
            "application_name": f"fm-native-scale-{pid}",
            "pid": pid,
            "principal": "fastmssql_owner",
            "session_id": session_id,
        }
        for pid, session_id in ((101, 51), (202, 52))
    )
    pool_records = tuple(
        {
            "application_name": f"fm-native-scale-{pid}",
            "pid": pid,
            "admission": {"active": 0, "capacity": 8, "rejected": 0},
            "operations": {"pending_operations": 0},
            "pool": {
                "active_connections": 0,
                "connections": 2,
                "idle_connections": 2,
                "max_size": 4,
                "pending_gets": 0,
            },
        }
        for pid in (101, 202)
    )
    observer_sample = runner.SqlObserverSample(
        applications=tuple(
            runner.ObserverApplicationSample(
                application_name=f"fm-native-scale-{pid}",
                host_process_ids=(pid,),
                sessions=2,
                requests=1,
            )
            for pid in (101, 202)
        ),
        current_sessions=4,
        current_requests=2,
        maximum_sessions=4,
        maximum_requests=2,
        request_context_tokens=(),
    )

    evidence = runner.validate_native_scaling_evidence(
        config=config,
        profile=profile,
        ready_records=ready_records,
        package_records=package_records,
        principal_records=principal_records,
        pool_records=pool_records,
        parameter_payloads=(
            {"session_id": 51, "value": 731_901},
            {"session_id": 52, "value": 731_902},
        ),
        expected_values=(731_901, 731_902),
        expected_principal="fastmssql_owner",
        observer_sample=observer_sample,
    )

    record = evidence.to_record()
    assert record["profile_id"] == profile.id
    assert record["ready_pids"] == [101, 202]
    assert record["parameter_values"] == [731_901, 731_902]
    assert record["pool_max_per_worker"] == 4
    assert record["maximum_aggregate_sql_sessions"] == 4
    assert record["maximum_simultaneous_sql_requests"] == 2
    assert [worker["pid"] for worker in record["worker_records"]] == [101, 202]
    assert all(worker["pool_created_pid"] == worker["pid"] for worker in record["worker_records"])

    excessive_sessions = replace(observer_sample, maximum_sessions=9)
    with pytest.raises(
        runner.WorkerEvidenceError,
        match="global connection budget",
    ):
        runner.validate_native_scaling_evidence(
            config=config,
            profile=profile,
            ready_records=ready_records,
            package_records=package_records,
            principal_records=principal_records,
            pool_records=pool_records,
            parameter_payloads=(
                {"session_id": 51, "value": 731_901},
                {"session_id": 52, "value": 731_902},
            ),
            expected_values=(731_901, 731_902),
            expected_principal="fastmssql_owner",
            observer_sample=excessive_sessions,
        )


@pytest.mark.asyncio
async def test_native_scaling_profile_runs_every_layer_and_tears_down(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=8,
    )
    profile = replace(
        _profile_by_family(
            runner,
            "fastapi-uvicorn-asyncio",
            workers=2,
        ),
        database_mode="sql_auth",
        platform_system=config.platform_system,
    )
    isolated = runner.prepare_isolated_application(
        config,
        source_directory=ROOT / "tests/production_framework",
        directory_name="native-scaling-test",
    )
    settings = runner.SqlAuthObserverSettings(
        host="127.0.0.1",
        port=14334,
        database="fastmssql_validation",
        username="fastmssql_owner",
        password="private-owner-password",
    )
    events: list[object] = []
    ready_records = tuple(
        {
            "candidate_sha": config.candidate_sha,
            "phase": "ready",
            "pid": pid,
            "pool_connected_monotonic": 12.0 + offset,
            "pool_created_monotonic": 11.0 + offset,
            "pool_created_pid": pid,
            "pool_identity": f"pool-{pid}",
            "pool_max_per_worker": 4,
            "process_started_monotonic": 10.0 + offset,
            "run_id": "native-scale",
            "worker_application_name": f"fm-native-scale-{pid}",
            "wheel_filename": config.wheel.name,
            "wheel_sha256": config.wheel_sha256,
        }
        for offset, pid in enumerate((101, 202))
    )
    shutdown_records = tuple(
        {**record, "phase": "shutdown"} for record in ready_records
    )
    busy_sample = runner.SqlObserverSample(
        applications=tuple(
            runner.ObserverApplicationSample(
                application_name=f"fm-native-scale-{pid}",
                host_process_ids=(pid,),
                sessions=2,
                requests=1,
            )
            for pid in (101, 202)
        ),
        current_sessions=4,
        current_requests=2,
        maximum_sessions=4,
        maximum_requests=2,
        request_context_tokens=(),
    )
    zero_sample = runner.SqlObserverSample(
        applications=(),
        current_sessions=0,
        current_requests=0,
        maximum_sessions=4,
        maximum_requests=2,
        request_context_tokens=(),
    )

    class FakeObserverConnection:
        async def connect(self, *, validate: bool) -> None:
            events.append(("observer-connect", validate))

        async def disconnect(self) -> None:
            events.append("observer-disconnect")

    class FakeObserver:
        def __init__(self, **kwargs) -> None:
            events.append(("observer", kwargs["worker_prefix"]))

        async def wait_for_ready_workers(self, records, *, timeout_seconds):
            assert tuple(records) == ready_records
            assert timeout_seconds > 0
            events.append("workers-observed")
            return busy_sample

        async def wait_for_minimum_requests(self, minimum, *, timeout_seconds):
            assert minimum == 2
            assert timeout_seconds > 0
            events.append("wave-observed")
            return busy_sample

        async def sample(self):
            return busy_sample

        async def wait_for_zero_sessions(self, *, timeout_seconds):
            assert timeout_seconds > 0
            events.append("zero-sessions")
            return zero_sample

    outcome = runner.ProcessOutcome(
        pid=90,
        returncode=0,
        stdout="",
        stderr="",
        output_truncated=False,
        descendant_pids=(101, 202),
        graceful_stop=True,
        forced_cleanup=False,
    )

    class FakeSupervisor:
        pid = 90
        selected_outcome = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback
            if self.selected_outcome is None:
                await self.stop()

        async def wait_for_worker_records(self, **kwargs):
            assert kwargs["expected_count"] == 2
            assert kwargs["expected_run_id"] == "native-scale"
            return ready_records

        def read_worker_records(self, **kwargs):
            assert kwargs["phase"] == "shutdown"
            return shutdown_records

        def descendant_pids(self):
            return (101, 202)

        async def stop(self):
            events.append("server-stop")
            self.selected_outcome = outcome
            return outcome

    supervisor = FakeSupervisor()

    async def fake_launch(**kwargs):
        assert await kwargs["readiness_probe"](8123)
        events.append("server-launch")
        return runner.ServerLaunch(
            supervisor=supervisor,
            port=8123,
            attempts=1,
            sanitized_command=(sys.executable, "-m", "uvicorn"),
        )

    async def fake_get(port: int, path: str, **kwargs):
        assert port == 8123
        assert kwargs["timeout_seconds"] > 0
        assert path == "/ready"
        return {"pid": 101, "state": "ready"}

    async def fake_collect(port: int, path: str, **kwargs):
        assert port == 8123
        assert kwargs["expected_pids"] == (101, 202)
        if path == "/package":
            return tuple(
                {
                    "candidate_sha": config.candidate_sha,
                    "fastmssql_import_path": f"/site-packages/fastmssql-{pid}.so",
                    "pid": pid,
                    "wheel_filename": config.wheel.name,
                    "wheel_sha256": config.wheel_sha256,
                }
                for pid in (101, 202)
            )
        if path == "/principal":
            return tuple(
                {
                    "application_name": f"fm-native-scale-{pid}",
                    "pid": pid,
                    "principal": "fastmssql_owner",
                    "session_id": session_id,
                }
                for pid, session_id in ((101, 51), (202, 52))
            )
        assert path == "/pool"
        return tuple(
            {
                "application_name": f"fm-native-scale-{pid}",
                "pid": pid,
                "admission": {"active": 0, "capacity": 8, "rejected": 0},
                "operations": {},
                "pool": {
                    "active_connections": 0,
                    "connections": 2,
                    "idle_connections": 2,
                    "max_size": 4,
                    "pending_gets": 0,
                },
            }
            for pid in (101, 202)
        )

    async def fake_request(port: int, path: str, **kwargs):
        assert port == 8123
        assert kwargs["expected_statuses"] == (200,)
        value = int(path.removeprefix("/wait/"))
        return runner.LoopbackJsonResponse(
            status_code=200,
            payload={"session_id": 51, "value": value},
            elapsed_seconds=0.01,
        )

    async def fake_port_not_listening(port: int) -> bool:
        assert port == 8123
        return False

    monkeypatch.setattr(runner, "create_observer_connection", lambda *a, **k: FakeObserverConnection())
    monkeypatch.setattr(runner, "SqlServerObserver", FakeObserver)
    monkeypatch.setattr(runner, "launch_with_port_retry", fake_launch)
    monkeypatch.setattr(runner, "http_get_json", fake_get)
    monkeypatch.setattr(runner, "collect_worker_payloads", fake_collect)
    monkeypatch.setattr(runner, "http_request_json", fake_request)
    monkeypatch.setattr(runner, "validate_package_record", lambda *a, **k: dict(a[2]))
    monkeypatch.setattr(
        runner,
        "loopback_port_is_listening",
        fake_port_not_listening,
    )

    result = await runner.run_native_scaling_profile(
        config,
        isolated,
        profile,
        repository_root=ROOT,
        run_id="native-scale",
        policy=runner.SupervisorPolicy(),
        sql_auth_settings=settings,
        table_name="framework_items_native",
        wave_values=(731_901, 731_902),
    )

    record = result.to_record()
    assert record["status"] == "PASS"
    assert record["ready_pids"] == [101, 202]
    assert record["shutdown_pids"] == [101, 202]
    assert record["sessions_after"] == 0
    assert record["forced_cleanup"] is False
    assert events[0] == ("observer-connect", True)
    assert events[-1] == "observer-disconnect"
    assert events.index("wave-observed") < events.index("server-stop")


@pytest.mark.asyncio
async def test_native_scaling_matrix_runs_every_selected_profile_deterministically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=8,
    )
    profiles = tuple(
        replace(
            _profile_by_family(
                runner,
                "fastapi-uvicorn-asyncio",
                workers=workers,
            ),
            database_mode="sql_auth",
            platform_system=config.platform_system,
        )
        for workers in (1, 4)
    )
    settings = runner.SqlAuthObserverSettings(
        host="127.0.0.1",
        port=14334,
        database="fastmssql_validation",
        username="fastmssql_owner",
        password="private-owner-password",
    )
    calls: list[dict[str, object]] = []

    async def fake_profile(config_arg, isolated, profile, **kwargs):
        assert config_arg is config
        assert isolated.root.is_relative_to(config.run_root)
        calls.append({"profile": profile, **kwargs})
        return profile.id

    monkeypatch.setattr(runner, "native_fastapi_profiles", lambda platform: profiles)
    monkeypatch.setattr(runner, "run_native_scaling_profile", fake_profile)

    results = await runner.run_native_scaling_matrix(
        config,
        repository_root=ROOT,
        source_directory=ROOT / "tests/production_framework",
        sql_auth_settings=settings,
        table_name="framework_items_native",
        policy=runner.SupervisorPolicy(),
    )

    assert results == tuple(profile.id for profile in profiles)
    assert [call["profile"] for call in calls] == list(profiles)
    assert [call["run_id"] for call in calls] == [
        "nscale-aaaaaaaa-1",
        "nscale-aaaaaaaa-2",
    ]
    assert [call["wave_values"] for call in calls] == [
        (731_101, 731_102),
        (731_201, 731_202, 731_203, 731_204),
    ]


def test_native_concurrency_evidence_requires_true_overlap_and_responsiveness() -> None:
    runner = _load_production_framework_runner()
    busy_sample = runner.SqlObserverSample(
        applications=(
            runner.ObserverApplicationSample(
                application_name="fm-native-concurrency-101",
                host_process_ids=(101,),
                sessions=4,
                requests=4,
            ),
        ),
        current_sessions=4,
        current_requests=4,
        maximum_sessions=4,
        maximum_requests=4,
        request_context_tokens=(),
    )
    payloads = tuple(
        {"delay_ms": 250, "session_id": 50 + offset, "value": value}
        for offset, value in enumerate((741_001, 741_002, 741_003, 741_004), start=1)
    )
    pool_record = {
        "admission": {"active": 0, "capacity": 16, "rejected": 0},
        "operations": {},
        "pid": 101,
        "pool": {
            "active_connections": 0,
            "connections": 4,
            "idle_connections": 4,
            "max_size": 8,
            "pending_gets": 0,
        },
    }

    evidence = runner.validate_native_concurrency_evidence(
        sequential_payloads=payloads,
        concurrent_payloads=payloads,
        expected_values=(741_001, 741_002, 741_003, 741_004),
        sequential_seconds=1.05,
        concurrent_seconds=0.28,
        health_seconds=0.01,
        sql_delay_ms=250,
        observer_sample=busy_sample,
        pool_record=pool_record,
    )

    assert evidence.to_record() == {
        "concurrent_seconds": 0.28,
        "health_seconds": 0.01,
        "maximum_simultaneous_sql_requests": 4,
        "pool_active_after": 0,
        "pool_pending_after": 0,
        "sequential_seconds": 1.05,
        "sql_delay_seconds": 0.25,
        "status": "PASS",
        "values_exact": True,
    }

    with pytest.raises(
        runner.WorkerEvidenceError,
        match="concurrent SQL wave did not beat",
    ):
        runner.validate_native_concurrency_evidence(
            sequential_payloads=payloads,
            concurrent_payloads=payloads,
            expected_values=(741_001, 741_002, 741_003, 741_004),
            sequential_seconds=1.0,
            concurrent_seconds=0.75,
            health_seconds=0.01,
            sql_delay_ms=250,
            observer_sample=busy_sample,
            pool_record=pool_record,
        )


@pytest.mark.asyncio
async def test_native_concurrency_scenario_measures_same_server_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=8,
    )
    profile = replace(
        _profile_by_family(
            runner,
            "fastapi-uvicorn-asyncio",
            workers=1,
        ),
        database_mode="sql_auth",
        platform_system=config.platform_system,
    )
    isolated = runner.prepare_isolated_application(
        config,
        source_directory=ROOT / "tests/production_framework",
        directory_name="native-concurrency-test",
    )
    settings = runner.SqlAuthObserverSettings(
        host="127.0.0.1",
        port=14334,
        database="fastmssql_validation",
        username="fastmssql_owner",
        password="private-owner-password",
    )
    ready_record = {
        "phase": "ready",
        "pid": 101,
        "run_id": "native-concurrency",
        "worker_application_name": "fm-native-concurrency-101",
    }
    shutdown_record = {**ready_record, "phase": "shutdown"}
    busy_sample = runner.SqlObserverSample(
        applications=(
            runner.ObserverApplicationSample(
                application_name="fm-native-concurrency-101",
                host_process_ids=(101,),
                sessions=4,
                requests=4,
            ),
        ),
        current_sessions=4,
        current_requests=4,
        maximum_sessions=4,
        maximum_requests=4,
        request_context_tokens=(),
    )
    zero_sample = replace(
        busy_sample,
        applications=(),
        current_sessions=0,
        current_requests=0,
    )
    events: list[str] = []

    class FakeConnection:
        async def connect(self, *, validate: bool) -> None:
            assert validate is True
            events.append("observer-connect")

        async def disconnect(self) -> None:
            events.append("observer-disconnect")

    class FakeObserver:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def wait_for_ready_workers(self, records, *, timeout_seconds):
            assert tuple(records) == (ready_record,)
            assert timeout_seconds > 0
            return busy_sample

        async def wait_for_minimum_requests(self, minimum, *, timeout_seconds):
            assert minimum == 2
            assert timeout_seconds > 0
            return busy_sample

        async def sample(self):
            return busy_sample

        async def wait_for_zero_sessions(self, *, timeout_seconds):
            assert timeout_seconds > 0
            return zero_sample

    outcome = runner.ProcessOutcome(
        pid=90,
        returncode=0,
        stdout="",
        stderr="",
        output_truncated=False,
        descendant_pids=(101,),
        graceful_stop=True,
        forced_cleanup=False,
    )

    class FakeSupervisor:
        pid = 90

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            del args

        async def wait_for_worker_records(self, **kwargs):
            assert kwargs["expected_count"] == 1
            return (ready_record,)

        def read_worker_records(self, **kwargs):
            assert kwargs["phase"] == "shutdown"
            return (shutdown_record,)

        async def stop(self):
            events.append("server-stop")
            return outcome

    async def fake_launch(**kwargs):
        assert await kwargs["readiness_probe"](8124)
        return runner.ServerLaunch(
            supervisor=FakeSupervisor(),
            port=8124,
            attempts=1,
            sanitized_command=(sys.executable, "-m", "uvicorn"),
        )

    async def fake_get(port: int, path: str, **kwargs):
        assert port == 8124
        assert path == "/ready"
        assert kwargs["timeout_seconds"] > 0
        return {"pid": 101, "state": "ready"}

    async def fake_request(port: int, path: str, **kwargs):
        assert port == 8124
        assert kwargs["expected_statuses"] == (200,)
        if path.startswith("/wait/"):
            await asyncio.sleep(0.02)
            value = int(path.removeprefix("/wait/"))
            payload = {"delay_ms": 250, "session_id": 51, "value": value}
        elif path == "/ready":
            payload = {"pid": 101, "state": "ready"}
        else:
            assert path == "/pool"
            payload = {
                "pid": 101,
                "pool": {
                    "active_connections": 0,
                    "connections": 4,
                    "idle_connections": 4,
                    "max_size": 8,
                    "pending_gets": 0,
                },
            }
        return runner.LoopbackJsonResponse(
            status_code=200,
            payload=payload,
            elapsed_seconds=0.001,
        )

    async def fake_port_not_listening(port: int) -> bool:
        assert port == 8124
        return False

    monkeypatch.setattr(runner, "create_observer_connection", lambda *a, **k: FakeConnection())
    monkeypatch.setattr(runner, "SqlServerObserver", FakeObserver)
    monkeypatch.setattr(runner, "launch_with_port_retry", fake_launch)
    monkeypatch.setattr(runner, "http_get_json", fake_get)
    monkeypatch.setattr(runner, "http_request_json", fake_request)
    monkeypatch.setattr(runner, "loopback_port_is_listening", fake_port_not_listening)

    result = await runner.run_native_concurrency_scenario(
        config,
        isolated,
        profile,
        run_id="native-concurrency",
        policy=runner.SupervisorPolicy(),
        sql_auth_settings=settings,
        table_name="framework_items_native",
        values=(741_001, 741_002, 741_003, 741_004),
        sql_delay_ms=250,
    )

    record = result.to_record()
    assert record["status"] == "PASS"
    assert record["maximum_simultaneous_sql_requests"] == 4
    assert record["ready_pids"] == [101]
    assert record["shutdown_pids"] == [101]
    assert record["sessions_after"] == 0
    assert events == ["observer-connect", "server-stop", "observer-disconnect"]


def test_shared_listener_minimum_does_not_require_perfect_worker_dispatch(
) -> None:
    """Catch a shared-listener gate that assumes one request per worker."""

    runner = _load_production_framework_runner()
    cases = (
        ("flask-gunicorn-sync", 1, 2, 1),
        ("flask-gunicorn-gthread", 1, 5, 4),
        ("flask-gunicorn-sync", 8, 8, 1),
        ("flask-gunicorn-gthread", 8, 8, 1),
        ("flask-asgi-uvicorn-asyncio", 1, 4, 1),
        ("flask-asgi-uvicorn-asyncio", 8, 8, 1),
    )
    for family, workers, request_count, expected in cases:
        profile = _profile_by_family(runner, family, workers=workers)
        assert (
            runner.shared_listener_sql_request_minimum(
                profile,
                request_count=request_count,
            )
            == expected
        )


@pytest.mark.asyncio
async def test_observed_request_wave_surfaces_http_failure_and_settles(
) -> None:
    """Catch an observer timeout that hides the primary HTTP exception."""

    runner = _load_production_framework_runner()
    observer_cancelled = asyncio.Event()
    pending_request_cancelled = asyncio.Event()

    class WaitingObserver:
        async def wait_for_minimum_requests(
            self,
            minimum_requests: int,
            *,
            timeout_seconds: float,
        ) -> None:
            assert minimum_requests == 2
            assert timeout_seconds == 1.0
            try:
                await asyncio.Event().wait()
            finally:
                observer_cancelled.set()

    async def failing_request():
        raise runner.HttpProbeError("primary HTTP failure")

    async def pending_request():
        try:
            await asyncio.Event().wait()
        finally:
            pending_request_cancelled.set()

    tasks = (
        asyncio.create_task(failing_request()),
        asyncio.create_task(pending_request()),
    )
    try:
        with pytest.raises(
            runner.HttpProbeError,
            match="primary HTTP failure",
        ):
            await runner.await_observed_request_wave(
                WaitingObserver(),
                tasks,
                minimum_requests=2,
                timeout_seconds=1.0,
            )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    assert observer_cancelled.is_set()
    assert pending_request_cancelled.is_set()
    assert all(task.done() for task in tasks)


@pytest.mark.asyncio
async def test_observed_request_wave_normalizes_observation_timeout(
) -> None:
    """Catch a raw asyncio timeout escaping the runner error taxonomy."""

    runner = _load_production_framework_runner()
    observer_cancelled = asyncio.Event()

    class UnobservableWorker:
        async def wait_for_minimum_requests(
            self,
            minimum_requests: int,
            *,
            timeout_seconds: float,
        ) -> None:
            assert minimum_requests == 1
            assert timeout_seconds == 0.02
            try:
                await asyncio.Event().wait()
            finally:
                observer_cancelled.set()

    async def successful_request():
        return runner.LoopbackJsonResponse(
            status_code=200,
            payload={"value": 17},
            elapsed_seconds=0.001,
        )

    task = asyncio.create_task(successful_request())
    with pytest.raises(
        runner.ReadinessTimeoutError,
        match="SQL observation did not settle within the wave bound",
    ):
        await runner.await_observed_request_wave(
            UnobservableWorker(),
            (task,),
            minimum_requests=1,
            timeout_seconds=0.02,
        )

    assert task.done()
    assert observer_cancelled.is_set()


@pytest.mark.asyncio
async def test_observed_request_wave_normalizes_response_timeout() -> None:
    """Catch a raw asyncio timeout when SQL is visible but HTTP never settles."""

    runner = _load_production_framework_runner()
    request_cancelled = asyncio.Event()

    class ImmediateObserver:
        async def wait_for_minimum_requests(
            self,
            minimum_requests: int,
            *,
            timeout_seconds: float,
        ) -> None:
            assert minimum_requests == 1
            assert timeout_seconds == 0.02

    async def stalled_request():
        try:
            await asyncio.Event().wait()
        finally:
            request_cancelled.set()

    task = asyncio.create_task(stalled_request())
    with pytest.raises(
        runner.ReadinessTimeoutError,
        match="HTTP responses did not settle within the wave bound",
    ):
        await runner.await_observed_request_wave(
            ImmediateObserver(),
            (task,),
            minimum_requests=1,
            timeout_seconds=0.02,
        )

    assert task.done()
    assert request_cancelled.is_set()


def test_task8_results_refuse_pass_without_harness_controlled_shutdown(
) -> None:
    """Catch PASS evidence produced after a server exits before harness stop."""

    runner = _load_production_framework_runner()
    evidence = SimpleNamespace(
        to_record=lambda: {"status": "PASS"},
        worker_execution=(
            {"maximum_active_requests": 1, "pid": 101},
        ),
    )
    process_fields = {
        "descendant_pids": (101,),
        "forced_cleanup": False,
        "graceful_stop": False,
        "listening_sockets_after": (),
        "manager_pid": 90,
        "returncode": 0,
        "sessions_after": 0,
        "shutdown_pids": (101,),
    }
    profile_fields = {
        **process_fields,
        "launch_attempts": 1,
        "port": 8125,
        "sanitized_command": (sys.executable, "-m", "server"),
    }
    results = (
        runner.FlaskWsgiProfileResult(
            profile=_profile_by_family(
                runner,
                "flask-gunicorn-sync",
                workers=1,
            ),
            scaling_evidence=evidence,
            wsgi_evidence=evidence,
            **profile_fields,
        ),
        runner.AdaptedFlaskProfileResult(
            profile=_profile_by_family(
                runner,
                "flask-asgi-uvicorn-asyncio",
                workers=1,
            ),
            scaling_evidence=evidence,
            adapted_evidence=evidence,
            **profile_fields,
        ),
        runner.FlaskGatherScenarioResult(
            profile_id="flask-gunicorn-sync-w1",
            evidence=evidence,
            ready_pids=(101,),
            **process_fields,
        ),
        runner.AdaptedFlaskSerializationScenarioResult(
            profile_id="flask-asgi-uvicorn-asyncio-w1",
            evidence=evidence,
            ready_pids=(101,),
            **process_fields,
        ),
    )

    for result in results:
        with pytest.raises(
            runner.WorkerEvidenceError,
            match="harness-controlled graceful shutdown",
        ):
            result.to_record()


def test_flask_wsgi_profile_selection_is_posix_only_and_complete() -> None:
    """Catch a missing worker count, WSGI class, or false Windows claim."""

    runner = _load_production_framework_runner()
    for platform_name in ("linux", "darwin"):
        profiles = runner.flask_wsgi_profiles(platform_name)
        assert tuple(profile.id for profile in profiles) == tuple(
            sorted(
                f"flask-gunicorn-{worker_class}-w{workers}"
                for worker_class in ("gthread", "sync")
                for workers in (1, 2, 4, 8)
            )
        )
        assert {profile.worker_class for profile in profiles} == {
            "gthread",
            "sync",
        }
        assert all(profile.applicable for profile in profiles)
        assert all(profile.database_mode == "sql_auth" for profile in profiles)

    assert runner.flask_wsgi_profiles("win32") == ()


def _flask_execution_payload(
    *,
    value: int,
    sequence: int,
    loop_token: int,
    wsgi_thread_token: int,
    active: int,
    maximum_active: int,
    async_thread_token: int | None = None,
    pid: int = 101,
    delay_ms: int = 250,
    execution_model: str = (
        "Flask WSGI: async view, occupied WSGI worker/thread"
    ),
) -> dict[str, object]:
    return {
        "async_thread_token": (
            sequence if async_thread_token is None else async_thread_token
        ),
        "delay_ms": delay_ms,
        "execution_model": execution_model,
        "loop_token": loop_token,
        "pid": pid,
        "request_sequence": sequence,
        "session_id": 50 + sequence,
        "value": value,
        "wsgi_active_requests": active,
        "wsgi_maximum_active_requests": maximum_active,
        "wsgi_thread_token": wsgi_thread_token,
    }


def _flask_observer_sample(
    runner,
    *,
    maximum_requests: int,
) -> object:
    return runner.SqlObserverSample(
        applications=(
            runner.ObserverApplicationSample(
                application_name="fm-flask-wsgi-101",
                host_process_ids=(101,),
                sessions=maximum_requests,
                requests=0,
            ),
        ),
        current_sessions=maximum_requests,
        current_requests=0,
        maximum_sessions=maximum_requests,
        maximum_requests=maximum_requests,
        request_context_tokens=(),
    )


def test_sync_wsgi_wave_requires_one_request_slot_and_distinct_loops(
    tmp_path: Path,
) -> None:
    """Catch accidental inter-request concurrency claims for sync workers."""

    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=8,
    )
    profile = replace(
        _profile_by_family(runner, "flask-gunicorn-sync", workers=1),
        database_mode="sql_auth",
        platform_system=config.platform_system,
    )
    ready_records = (
        {
            "phase": "ready",
            "pid": 101,
            "worker_application_name": "fm-flask-wsgi-101",
        },
    )
    payloads = tuple(
        _flask_execution_payload(
            value=value,
            sequence=sequence,
            loop_token=sequence,
            wsgi_thread_token=1,
            active=1,
            maximum_active=1,
        )
        for sequence, value in enumerate((751_001, 751_002), start=1)
    )
    settled = (
        {
            "active_other_requests": 0,
            "completed_requests": 2,
            "execution_model": (
                "Flask WSGI: async view, occupied WSGI worker/thread"
            ),
            "maximum_active_requests": 1,
            "pid": 101,
            "wsgi_thread_count": 1,
        },
    )

    evidence = runner.validate_flask_wsgi_wave_evidence(
        config=config,
        profile=profile,
        ready_records=ready_records,
        response_payloads=payloads,
        settled_records=settled,
        expected_values=(751_001, 751_002),
        wave_seconds=0.53,
        sql_delay_ms=250,
        observer_sample=_flask_observer_sample(
            runner,
            maximum_requests=1,
        ),
    )

    assert evidence.to_record() == {
        "execution_model": (
            "Flask WSGI: async view, occupied WSGI worker/thread"
        ),
        "loop_tokens_distinct_per_worker": True,
        "maximum_aggregate_sql_sessions": 1,
        "maximum_simultaneous_sql_requests": 1,
        "profile_id": profile.id,
        "queued_request_proven": True,
        "request_count": 2,
        "sql_delay_seconds": 0.25,
        "status": "PASS",
        "thread_limit_per_worker": 1,
        "values": [751_001, 751_002],
        "wave_seconds": 0.53,
        "worker_execution": [
            {
                "maximum_active_requests": 1,
                "pid": 101,
                "wsgi_thread_count": 1,
            }
        ],
    }

    duplicated_loop = (payloads[0], {**payloads[1], "loop_token": 1})
    with pytest.raises(
        runner.WorkerEvidenceError,
        match="per-request event loops are not distinct",
    ):
        runner.validate_flask_wsgi_wave_evidence(
            config=config,
            profile=profile,
            ready_records=ready_records,
            response_payloads=duplicated_loop,
            settled_records=settled,
            expected_values=(751_001, 751_002),
            wave_seconds=0.53,
            sql_delay_ms=250,
            observer_sample=_flask_observer_sample(
                runner,
                maximum_requests=1,
            ),
        )


def test_gthread_wsgi_wave_requires_four_threads_and_queues_fifth(
    tmp_path: Path,
) -> None:
    """Catch a gthread wave that exceeds or never occupies its four slots."""

    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=8,
    )
    profile = replace(
        _profile_by_family(runner, "flask-gunicorn-gthread", workers=1),
        database_mode="sql_auth",
        platform_system=config.platform_system,
    )
    ready_records = (
        {
            "phase": "ready",
            "pid": 101,
            "worker_application_name": "fm-flask-wsgi-101",
        },
    )
    values = (752_001, 752_002, 752_003, 752_004, 752_005)
    payloads = tuple(
        _flask_execution_payload(
            value=value,
            sequence=sequence,
            loop_token=sequence,
            wsgi_thread_token=((sequence - 1) % 4) + 1,
            active=4,
            maximum_active=4,
        )
        for sequence, value in enumerate(values, start=1)
    )
    settled = (
        {
            "active_other_requests": 0,
            "completed_requests": 5,
            "execution_model": (
                "Flask WSGI: async view, occupied WSGI worker/thread"
            ),
            "maximum_active_requests": 4,
            "pid": 101,
            "wsgi_thread_count": 4,
        },
    )

    evidence = runner.validate_flask_wsgi_wave_evidence(
        config=config,
        profile=profile,
        ready_records=ready_records,
        response_payloads=payloads,
        settled_records=settled,
        expected_values=values,
        wave_seconds=0.54,
        sql_delay_ms=250,
        observer_sample=_flask_observer_sample(
            runner,
            maximum_requests=4,
        ),
    )

    record = evidence.to_record()
    assert record["thread_limit_per_worker"] == 4
    assert record["request_count"] == 5
    assert record["queued_request_proven"] is True
    assert record["maximum_simultaneous_sql_requests"] == 4
    assert record["worker_execution"] == [
        {
            "maximum_active_requests": 4,
            "pid": 101,
            "wsgi_thread_count": 4,
        }
    ]

    excessive = ({**settled[0], "maximum_active_requests": 5},)
    with pytest.raises(
        runner.WorkerEvidenceError,
        match="gthread WSGI concurrency exceeded four threads",
    ):
        runner.validate_flask_wsgi_wave_evidence(
            config=config,
            profile=profile,
            ready_records=ready_records,
            response_payloads=payloads,
            settled_records=excessive,
            expected_values=values,
            wave_seconds=0.54,
            sql_delay_ms=250,
            observer_sample=_flask_observer_sample(
                runner,
                maximum_requests=4,
            ),
        )


def test_multiworker_wsgi_evidence_preserves_truthful_idle_workers(
    tmp_path: Path,
) -> None:
    """Catch a validator that turns shared-listener idle workers into failure."""

    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=8,
    )
    profile = replace(
        _profile_by_family(runner, "flask-gunicorn-gthread", workers=8),
        database_mode="sql_auth",
        platform_system=config.platform_system,
    )
    pids = tuple(range(101, 109))
    ready_records = tuple(
        {
            "phase": "ready",
            "pid": pid,
            "worker_application_name": f"fm-flask-wsgi-{pid}",
        }
        for pid in pids
    )
    values = tuple(range(752_101, 752_109))
    response_pids = (101, 102, 103, 104, 104, 105, 105, 105)
    response_counts = {101: 1, 102: 1, 103: 1, 104: 2, 105: 3}
    payloads = tuple(
        _flask_execution_payload(
            value=value,
            sequence=sequence,
            loop_token=sequence,
            wsgi_thread_token=((sequence - 1) % 4) + 1,
            active=response_counts[pid],
            maximum_active=response_counts[pid],
            pid=pid,
        )
        for sequence, (value, pid) in enumerate(
            zip(values, response_pids, strict=True),
            start=1,
        )
    )
    settled = tuple(
        {
            "active_other_requests": 0,
            "completed_requests": response_counts.get(pid, 0),
            "execution_model": (
                "Flask WSGI: async view, occupied WSGI worker/thread"
            ),
            "maximum_active_requests": response_counts.get(pid, 0),
            "pid": pid,
            "wsgi_thread_count": response_counts.get(pid, 0),
        }
        for pid in pids
    )
    observer_sample = runner.SqlObserverSample(
        applications=tuple(
            runner.ObserverApplicationSample(
                application_name=f"fm-flask-wsgi-{pid}",
                host_process_ids=(pid,),
                sessions=1,
                requests=0,
            )
            for pid in pids
        ),
        current_sessions=8,
        current_requests=0,
        maximum_sessions=8,
        maximum_requests=5,
        request_context_tokens=(),
    )

    evidence = runner.validate_flask_wsgi_wave_evidence(
        config=config,
        profile=profile,
        ready_records=ready_records,
        response_payloads=payloads,
        settled_records=settled,
        expected_values=values,
        wave_seconds=0.31,
        sql_delay_ms=250,
        observer_sample=observer_sample,
    )

    assert evidence.to_record()["worker_execution"] == [
        {"maximum_active_requests": 1, "pid": 101, "wsgi_thread_count": 1},
        {"maximum_active_requests": 1, "pid": 102, "wsgi_thread_count": 1},
        {"maximum_active_requests": 1, "pid": 103, "wsgi_thread_count": 1},
        {"maximum_active_requests": 2, "pid": 104, "wsgi_thread_count": 2},
        {"maximum_active_requests": 3, "pid": 105, "wsgi_thread_count": 3},
        {"maximum_active_requests": 0, "pid": 106, "wsgi_thread_count": 0},
        {"maximum_active_requests": 0, "pid": 107, "wsgi_thread_count": 0},
        {"maximum_active_requests": 0, "pid": 108, "wsgi_thread_count": 0},
    ]


@pytest.mark.asyncio
async def test_flask_wsgi_profile_runs_every_layer_and_tears_down(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a WSGI runner that skips SQL evidence or worker teardown."""

    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=8,
    )
    profile = replace(
        _profile_by_family(runner, "flask-gunicorn-sync", workers=1),
        database_mode="sql_auth",
        platform_system=config.platform_system,
    )
    isolated = runner.prepare_isolated_application(
        config,
        source_directory=ROOT / "tests/production_framework",
        directory_name="flask-wsgi-profile-test",
    )
    settings = runner.SqlAuthObserverSettings(
        host="127.0.0.1",
        port=14334,
        database="fastmssql_validation",
        username="fastmssql_owner",
        password="private-owner-password",
    )
    ready_record = {
        "candidate_sha": config.candidate_sha,
        "phase": "ready",
        "pid": 101,
        "pool_connected_monotonic": 12.0,
        "pool_created_monotonic": 11.0,
        "pool_created_pid": 101,
        "pool_identity": "pool-101",
        "pool_max_per_worker": 8,
        "process_started_monotonic": 10.0,
        "run_id": "flask-sync",
        "worker_application_name": "fm-flask-wsgi-101",
        "wheel_filename": config.wheel.name,
        "wheel_sha256": config.wheel_sha256,
    }
    shutdown_record = {**ready_record, "phase": "shutdown"}
    busy_sample = _flask_observer_sample(runner, maximum_requests=1)
    zero_sample = replace(
        busy_sample,
        applications=(),
        current_sessions=0,
        current_requests=0,
    )
    events: list[str] = []

    class FakeObserverConnection:
        async def connect(self, *, validate: bool) -> None:
            assert validate is True
            events.append("observer-connect")

        async def disconnect(self) -> None:
            events.append("observer-disconnect")

    class FakeObserver:
        def __init__(self, **kwargs) -> None:
            assert kwargs["worker_prefix"].startswith("fm-")

        async def wait_for_ready_workers(self, records, *, timeout_seconds):
            assert tuple(records) == (ready_record,)
            assert timeout_seconds > 0
            return busy_sample

        async def wait_for_minimum_requests(self, minimum, *, timeout_seconds):
            assert minimum == 1
            assert timeout_seconds > 0
            events.append("wave-observed")
            return busy_sample

        async def sample(self):
            return busy_sample

        async def wait_for_zero_sessions(self, *, timeout_seconds):
            assert timeout_seconds > 0
            events.append("zero-sessions")
            return zero_sample

    outcome = runner.ProcessOutcome(
        pid=90,
        returncode=0,
        stdout="",
        stderr="",
        output_truncated=False,
        descendant_pids=(101,),
        graceful_stop=True,
        forced_cleanup=False,
    )

    class FakeSupervisor:
        pid = 90

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            del args

        async def wait_for_worker_records(self, **kwargs):
            assert kwargs["expected_count"] == 1
            assert kwargs["expected_run_id"] == "flask-sync"
            return (ready_record,)

        def read_worker_records(self, **kwargs):
            assert kwargs["phase"] == "shutdown"
            return (shutdown_record,)

        async def stop(self):
            events.append("server-stop")
            return outcome

    async def fake_launch(**kwargs):
        assert await kwargs["readiness_probe"](8125)
        return runner.ServerLaunch(
            supervisor=FakeSupervisor(),
            port=8125,
            attempts=1,
            sanitized_command=(sys.executable, "-m", "gunicorn"),
        )

    async def fake_get(port: int, path: str, **kwargs):
        assert port == 8125
        assert path == "/ready"
        assert kwargs["timeout_seconds"] > 0
        return {"pid": 101, "state": "ready"}

    async def fake_collect(port: int, path: str, **kwargs):
        assert port == 8125
        assert kwargs["expected_pids"] == (101,)
        if path == "/package":
            return (
                {
                    "candidate_sha": config.candidate_sha,
                    "fastmssql_import_path": "/site-packages/fastmssql.so",
                    "pid": 101,
                    "wheel_filename": config.wheel.name,
                    "wheel_sha256": config.wheel_sha256,
                },
            )
        if path == "/principal":
            return (
                {
                    "application_name": "fm-flask-wsgi-101",
                    "pid": 101,
                    "principal": "fastmssql_owner",
                    "session_id": 51,
                },
            )
        if path == "/execution/state":
            return (
                {
                    "active_other_requests": 0,
                    "completed_requests": 8,
                    "execution_model": (
                        "Flask WSGI: async view, occupied WSGI worker/thread"
                    ),
                    "maximum_active_requests": 1,
                    "pid": 101,
                    "wsgi_thread_count": 1,
                },
            )
        assert path == "/pool"
        return (
            {
                "admission": {"active": 0, "capacity": 16, "rejected": 0},
                "application_name": "fm-flask-wsgi-101",
                "operations": {},
                "pid": 101,
                "pool": {
                    "active_connections": 0,
                    "connections": 1,
                    "idle_connections": 1,
                    "max_size": 8,
                    "pending_gets": 0,
                },
            },
        )

    request_lock = asyncio.Lock()

    async def fake_request(port: int, path: str, **kwargs):
        assert port == 8125
        assert kwargs["expected_statuses"] == (200,)
        value = int(path.removeprefix("/execution/wait/"))
        sequence = value - 753_000
        async with request_lock:
            await asyncio.sleep(0.2)
            return runner.LoopbackJsonResponse(
                status_code=200,
                payload=_flask_execution_payload(
                    value=value,
                    sequence=sequence,
                    loop_token=sequence,
                    wsgi_thread_token=1,
                    active=1,
                    maximum_active=1,
                ),
                elapsed_seconds=0.2,
            )

    async def fake_port_not_listening(port: int) -> bool:
        assert port == 8125
        return False

    def fake_shared_listener_minimum(profile_arg, *, request_count):
        assert profile_arg is profile
        assert request_count == 2
        events.append("minimum-selected")
        return 1

    monkeypatch.setattr(
        runner,
        "create_observer_connection",
        lambda *args, **kwargs: FakeObserverConnection(),
    )
    monkeypatch.setattr(runner, "SqlServerObserver", FakeObserver)
    monkeypatch.setattr(runner, "launch_with_port_retry", fake_launch)
    monkeypatch.setattr(runner, "http_get_json", fake_get)
    monkeypatch.setattr(runner, "collect_worker_payloads", fake_collect)
    monkeypatch.setattr(runner, "http_request_json", fake_request)
    monkeypatch.setattr(
        runner,
        "shared_listener_sql_request_minimum",
        fake_shared_listener_minimum,
    )
    monkeypatch.setattr(
        runner,
        "validate_package_record",
        lambda *args, **kwargs: dict(args[2]),
    )
    monkeypatch.setattr(
        runner,
        "loopback_port_is_listening",
        fake_port_not_listening,
    )

    result = await runner.run_flask_wsgi_profile(
        config,
        isolated,
        profile,
        repository_root=ROOT,
        run_id="flask-sync",
        policy=runner.SupervisorPolicy(),
        sql_auth_settings=settings,
        table_name="framework_items_flask",
        wave_values=(753_001, 753_002),
        sql_delay_ms=250,
    )

    record = result.to_record()
    assert record["status"] == "PASS"
    assert record["profile_id"] == profile.id
    assert record["ready_pids"] == [101]
    assert record["shutdown_pids"] == [101]
    assert record["maximum_active_requests"] == 1
    assert record["queued_request_proven"] is True
    assert record["sessions_after"] == 0
    assert record["forced_cleanup"] is False
    assert events == [
        "observer-connect",
        "minimum-selected",
        "wave-observed",
        "server-stop",
        "zero-sessions",
        "observer-disconnect",
    ]


@pytest.mark.asyncio
async def test_flask_wsgi_matrix_sizes_each_real_worker_model_deterministically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a matrix that skips counts or overloads a WSGI model."""

    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=8,
    )
    profiles = tuple(
        replace(
            _profile_by_family(runner, family, workers=workers),
            database_mode="sql_auth",
            platform_system=config.platform_system,
        )
        for family, workers in (
            ("flask-gunicorn-gthread", 1),
            ("flask-gunicorn-gthread", 2),
            ("flask-gunicorn-sync", 1),
            ("flask-gunicorn-sync", 4),
        )
    )
    settings = runner.SqlAuthObserverSettings(
        host="127.0.0.1",
        port=14334,
        database="fastmssql_validation",
        username="fastmssql_owner",
        password="private-owner-password",
    )
    calls: list[dict[str, object]] = []

    async def fake_profile(config_arg, isolated, profile, **kwargs):
        assert config_arg is config
        assert isolated.root.is_relative_to(config.run_root)
        calls.append({"profile": profile, **kwargs})
        return profile.id

    monkeypatch.setattr(runner, "flask_wsgi_profiles", lambda platform: profiles)
    monkeypatch.setattr(runner, "run_flask_wsgi_profile", fake_profile)

    results = await runner.run_flask_wsgi_matrix(
        config,
        repository_root=ROOT,
        source_directory=ROOT / "tests/production_framework",
        sql_auth_settings=settings,
        table_name="framework_items_flask",
        policy=runner.SupervisorPolicy(),
    )

    assert results == tuple(profile.id for profile in profiles)
    assert [call["run_id"] for call in calls] == [
        "fwsgi-aaaaaaaa-1",
        "fwsgi-aaaaaaaa-2",
        "fwsgi-aaaaaaaa-3",
        "fwsgi-aaaaaaaa-4",
    ]
    assert [len(call["wave_values"]) for call in calls] == [5, 8, 2, 4]
    assert [call["wave_values"][0] for call in calls] == [
        753_101,
        753_201,
        753_301,
        753_401,
    ]
    assert len(
        {
            value
            for call in calls
            for value in call["wave_values"]
        }
    ) == sum(len(call["wave_values"]) for call in calls)


def test_flask_gather_evidence_proves_internal_overlap_in_one_wsgi_slot() -> None:
    """Catch an internal-concurrency claim without SQL overlap or slot proof."""

    runner = _load_production_framework_runner()
    response = runner.LoopbackJsonResponse(
        status_code=200,
        payload={
            "async_thread_token": 2,
            "concurrent": [0, 1, 2, 3],
            "concurrent_seconds": 0.28,
            "execution_model": (
                "Flask WSGI: async view, occupied WSGI worker/thread"
            ),
            "loop_token": 7,
            "pid": 101,
            "request_sequence": 9,
            "sequential": [0, 1, 2, 3],
            "sequential_seconds": 1.05,
            "wsgi_active_requests": 1,
            "wsgi_maximum_active_requests": 1,
            "wsgi_thread_token": 1,
        },
        elapsed_seconds=1.34,
    )
    state = {
        "active_other_requests": 0,
        "completed_requests": 9,
        "execution_model": (
            "Flask WSGI: async view, occupied WSGI worker/thread"
        ),
        "maximum_active_requests": 1,
        "pid": 101,
        "wsgi_thread_count": 1,
    }
    observer = _flask_observer_sample(runner, maximum_requests=4)
    pool = {
        "pid": 101,
        "pool": {
            "active_connections": 0,
            "connections": 4,
            "idle_connections": 4,
            "max_size": 8,
            "pending_gets": 0,
        },
    }

    evidence = runner.validate_flask_gather_evidence(
        response=response,
        state_record=state,
        observer_sample=observer,
        pool_record=pool,
        sql_delay_ms=250,
    )

    assert evidence.to_record() == {
        "concurrent_seconds": 0.28,
        "execution_model": (
            "Flask WSGI: async view, occupied WSGI worker/thread"
        ),
        "loop_token": 7,
        "maximum_simultaneous_sql_requests": 4,
        "outer_response_seconds": 1.34,
        "pool_active_after": 0,
        "pool_pending_after": 0,
        "sequential_seconds": 1.05,
        "sql_delay_seconds": 0.25,
        "status": "PASS",
        "values_exact": True,
        "wsgi_request_slots": 1,
    }

    slow_concurrent = replace(
        response,
        payload={**response.payload, "concurrent_seconds": 0.8},
    )
    with pytest.raises(
        runner.WorkerEvidenceError,
        match="same-view concurrent SQL did not beat",
    ):
        runner.validate_flask_gather_evidence(
            response=slow_concurrent,
            state_record=state,
            observer_sample=observer,
            pool_record=pool,
            sql_delay_ms=250,
        )


@pytest.mark.asyncio
async def test_flask_gather_scenario_uses_real_server_lifecycle_and_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a /gather scenario that omits supervision or zero-session proof."""

    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=8,
    )
    profile = replace(
        _profile_by_family(runner, "flask-gunicorn-sync", workers=1),
        database_mode="sql_auth",
        platform_system=config.platform_system,
    )
    isolated = runner.prepare_isolated_application(
        config,
        source_directory=ROOT / "tests/production_framework",
        directory_name="flask-gather-test",
    )
    settings = runner.SqlAuthObserverSettings(
        host="127.0.0.1",
        port=14334,
        database="fastmssql_validation",
        username="fastmssql_owner",
        password="private-owner-password",
    )
    ready_record = {
        "phase": "ready",
        "pid": 101,
        "run_id": "flask-gather",
        "worker_application_name": "fm-flask-gather-101",
    }
    shutdown_record = {**ready_record, "phase": "shutdown"}
    busy_sample = runner.SqlObserverSample(
        applications=(
            runner.ObserverApplicationSample(
                application_name="fm-flask-gather-101",
                host_process_ids=(101,),
                sessions=4,
                requests=4,
            ),
        ),
        current_sessions=4,
        current_requests=4,
        maximum_sessions=4,
        maximum_requests=4,
        request_context_tokens=(),
    )
    zero_sample = replace(
        busy_sample,
        applications=(),
        current_sessions=0,
        current_requests=0,
    )
    events: list[str] = []

    class FakeConnection:
        async def connect(self, *, validate: bool) -> None:
            assert validate is True
            events.append("observer-connect")

        async def disconnect(self) -> None:
            events.append("observer-disconnect")

    class FakeObserver:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def wait_for_ready_workers(self, records, *, timeout_seconds):
            assert tuple(records) == (ready_record,)
            assert timeout_seconds > 0
            return busy_sample

        async def wait_for_minimum_requests(self, minimum, *, timeout_seconds):
            assert minimum == 4
            assert timeout_seconds > 0
            events.append("gather-observed")
            return busy_sample

        async def wait_for_zero_sessions(self, *, timeout_seconds):
            assert timeout_seconds > 0
            events.append("zero-sessions")
            return zero_sample

    outcome = runner.ProcessOutcome(
        pid=90,
        returncode=0,
        stdout="",
        stderr="",
        output_truncated=False,
        descendant_pids=(101,),
        graceful_stop=True,
        forced_cleanup=False,
    )

    class FakeSupervisor:
        pid = 90

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            del args

        async def wait_for_worker_records(self, **kwargs):
            assert kwargs["expected_count"] == 1
            return (ready_record,)

        def read_worker_records(self, **kwargs):
            assert kwargs["phase"] == "shutdown"
            return (shutdown_record,)

        async def stop(self):
            events.append("server-stop")
            return outcome

    async def fake_launch(**kwargs):
        assert await kwargs["readiness_probe"](8126)
        return runner.ServerLaunch(
            supervisor=FakeSupervisor(),
            port=8126,
            attempts=1,
            sanitized_command=(sys.executable, "-m", "gunicorn"),
        )

    async def fake_get(port: int, path: str, **kwargs):
        assert port == 8126
        assert path == "/ready"
        assert kwargs["timeout_seconds"] > 0
        return {"pid": 101, "state": "ready"}

    async def fake_request(port: int, path: str, **kwargs):
        assert port == 8126
        assert kwargs["expected_statuses"] == (200,)
        if path == "/gather":
            await asyncio.sleep(0.01)
            payload = {
                "async_thread_token": 2,
                "concurrent": [0, 1, 2, 3],
                "concurrent_seconds": 0.28,
                "execution_model": (
                    "Flask WSGI: async view, occupied WSGI worker/thread"
                ),
                "loop_token": 7,
                "pid": 101,
                "request_sequence": 9,
                "sequential": [0, 1, 2, 3],
                "sequential_seconds": 1.05,
                "wsgi_active_requests": 1,
                "wsgi_maximum_active_requests": 1,
                "wsgi_thread_token": 1,
            }
            elapsed = 1.34
        elif path == "/execution/state":
            payload = {
                "active_other_requests": 0,
                "completed_requests": 9,
                "execution_model": (
                    "Flask WSGI: async view, occupied WSGI worker/thread"
                ),
                "maximum_active_requests": 1,
                "pid": 101,
                "wsgi_thread_count": 1,
            }
            elapsed = 0.01
        else:
            assert path == "/pool"
            payload = {
                "pid": 101,
                "pool": {
                    "active_connections": 0,
                    "connections": 4,
                    "idle_connections": 4,
                    "max_size": 8,
                    "pending_gets": 0,
                },
            }
            elapsed = 0.01
        return runner.LoopbackJsonResponse(
            status_code=200,
            payload=payload,
            elapsed_seconds=elapsed,
        )

    async def fake_port_not_listening(port: int) -> bool:
        assert port == 8126
        return False

    monkeypatch.setattr(
        runner,
        "create_observer_connection",
        lambda *args, **kwargs: FakeConnection(),
    )
    monkeypatch.setattr(runner, "SqlServerObserver", FakeObserver)
    monkeypatch.setattr(runner, "launch_with_port_retry", fake_launch)
    monkeypatch.setattr(runner, "http_get_json", fake_get)
    monkeypatch.setattr(runner, "http_request_json", fake_request)
    monkeypatch.setattr(
        runner,
        "loopback_port_is_listening",
        fake_port_not_listening,
    )

    result = await runner.run_flask_gather_scenario(
        config,
        isolated,
        profile,
        run_id="flask-gather",
        policy=runner.SupervisorPolicy(),
        sql_auth_settings=settings,
        table_name="framework_items_flask",
        sql_delay_ms=250,
    )

    record = result.to_record()
    assert record["status"] == "PASS"
    assert record["profile_id"] == profile.id
    assert record["wsgi_request_slots"] == 1
    assert record["maximum_simultaneous_sql_requests"] == 4
    assert record["ready_pids"] == [101]
    assert record["shutdown_pids"] == [101]
    assert record["sessions_after"] == 0
    assert events == [
        "observer-connect",
        "gather-observed",
        "server-stop",
        "zero-sessions",
        "observer-disconnect",
    ]


@pytest.mark.asyncio
async def test_flask_gather_scenario_preserves_pre_sql_http_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch SQL-observer timeout masking a failed /gather response."""

    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=8,
    )
    profile = replace(
        _profile_by_family(runner, "flask-gunicorn-sync", workers=1),
        database_mode="sql_auth",
        platform_system=config.platform_system,
    )
    isolated = runner.prepare_isolated_application(
        config,
        source_directory=ROOT / "tests/production_framework",
        directory_name="flask-gather-primary-error-test",
    )
    settings = runner.SqlAuthObserverSettings(
        host="127.0.0.1",
        port=14334,
        database="fastmssql_validation",
        username="fastmssql_owner",
        password="private-owner-password",
    )
    ready_record = {
        "phase": "ready",
        "pid": 101,
        "run_id": "flask-gather-error",
        "worker_application_name": "fm-flask-gather-error-101",
    }
    request_failed = asyncio.Event()
    events: list[str] = []

    class FakeConnection:
        async def connect(self, *, validate: bool) -> None:
            assert validate is True
            events.append("observer-connect")

        async def disconnect(self) -> None:
            events.append("observer-disconnect")

    class FakeObserver:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def wait_for_ready_workers(self, records, *, timeout_seconds):
            assert tuple(records) == (ready_record,)
            assert timeout_seconds > 0

        async def wait_for_minimum_requests(self, minimum, *, timeout_seconds):
            assert minimum == 4
            assert timeout_seconds > 0
            await request_failed.wait()
            raise runner.ReadinessTimeoutError(
                "observer would mask the primary HTTP failure"
            )

    class FakeSupervisor:
        pid = 90

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            del args

        async def wait_for_worker_records(self, **kwargs):
            assert kwargs["expected_count"] == 1
            return (ready_record,)

    async def fake_launch(**kwargs):
        assert await kwargs["readiness_probe"](8126)
        return runner.ServerLaunch(
            supervisor=FakeSupervisor(),
            port=8126,
            attempts=1,
            sanitized_command=(sys.executable, "-m", "gunicorn"),
        )

    async def fake_get(port: int, path: str, **kwargs):
        assert port == 8126
        assert path == "/ready"
        assert kwargs["timeout_seconds"] > 0
        return {"pid": 101, "state": "ready"}

    async def fake_request(port: int, path: str, **kwargs):
        assert port == 8126
        assert path == "/gather"
        assert kwargs["expected_statuses"] == (200,)
        request_failed.set()
        raise runner.HttpProbeError("primary /gather HTTP failure")

    monkeypatch.setattr(
        runner,
        "create_observer_connection",
        lambda *args, **kwargs: FakeConnection(),
    )
    monkeypatch.setattr(runner, "SqlServerObserver", FakeObserver)
    monkeypatch.setattr(runner, "launch_with_port_retry", fake_launch)
    monkeypatch.setattr(runner, "http_get_json", fake_get)
    monkeypatch.setattr(runner, "http_request_json", fake_request)

    with pytest.raises(
        runner.HttpProbeError,
        match="primary /gather HTTP failure",
    ):
        await runner.run_flask_gather_scenario(
            config,
            isolated,
            profile,
            run_id="flask-gather-error",
            policy=runner.SupervisorPolicy(),
            sql_auth_settings=settings,
            table_name="framework_items_flask",
            sql_delay_ms=250,
        )

    assert request_failed.is_set()
    assert events == ["observer-connect", "observer-disconnect"]


def test_adapted_flask_profile_selection_matches_platform_capabilities() -> None:
    """Catch a missing asyncio/uvloop count or an invalid Windows uvloop gate."""

    runner = _load_production_framework_runner()
    posix = runner.adapted_flask_profiles("darwin")
    assert tuple(profile.id for profile in posix) == tuple(
        sorted(
            f"flask-asgi-uvicorn-{loop}-w{workers}"
            for loop in ("asyncio", "uvloop")
            for workers in (1, 2, 4, 8)
        )
    )
    windows = runner.adapted_flask_profiles("win32")
    assert tuple(profile.id for profile in windows) == tuple(
        f"flask-asgi-uvicorn-asyncio-w{workers}"
        for workers in (1, 2, 4, 8)
    )
    assert all(profile.applicable for profile in (*posix, *windows))
    assert all(profile.database_mode == "sql_auth" for profile in (*posix, *windows))


def _adapted_loop_payload(
    *,
    sequence: int,
    loop_token: int = 7,
) -> dict[str, object]:
    return {
        "async_thread_token": 2,
        "execution_model": (
            "Flask via WsgiToAsgi: persistent ASGI loop, thread-sensitive "
            "WSGI serialization per process"
        ),
        "loop_id": 999,
        "loop_token": loop_token,
        "pid": 101,
        "request_sequence": sequence,
        "value": 17,
        "wsgi_active_requests": 1,
        "wsgi_maximum_active_requests": 1,
        "wsgi_thread_token": 1,
    }


def test_adapted_flask_scaling_evidence_requires_persistent_worker_loop(
    tmp_path: Path,
) -> None:
    """Catch per-request loop recreation or parallel WSGI calls in the adapter."""

    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=8,
    )
    profile = replace(
        _profile_by_family(
            runner,
            "flask-asgi-uvicorn-asyncio",
            workers=1,
        ),
        database_mode="sql_auth",
        platform_system=config.platform_system,
    )
    ready_records = (
        {
            "phase": "ready",
            "pid": 101,
            "worker_application_name": "fm-adapted-flask-101",
        },
    )
    execution_model = (
        "Flask via WsgiToAsgi: persistent ASGI loop, thread-sensitive WSGI "
        "serialization per process"
    )
    values = (761_001, 761_002)
    responses = tuple(
        _flask_execution_payload(
            value=value,
            sequence=sequence,
            loop_token=7,
            wsgi_thread_token=1,
            active=1,
            maximum_active=1,
            async_thread_token=2,
            execution_model=execution_model,
        )
        for sequence, value in enumerate(values, start=3)
    )
    settled = (
        {
            "active_other_requests": 0,
            "completed_requests": 4,
            "execution_model": execution_model,
            "maximum_active_requests": 1,
            "pid": 101,
            "wsgi_thread_count": 1,
        },
    )
    observer = runner.SqlObserverSample(
        applications=(
            runner.ObserverApplicationSample(
                application_name="fm-adapted-flask-101",
                host_process_ids=(101,),
                sessions=1,
                requests=0,
            ),
        ),
        current_sessions=1,
        current_requests=0,
        maximum_sessions=1,
        maximum_requests=1,
        request_context_tokens=(),
    )

    evidence = runner.validate_adapted_flask_scaling_evidence(
        config=config,
        profile=profile,
        ready_records=ready_records,
        first_loop_records=(_adapted_loop_payload(sequence=1),),
        second_loop_records=(_adapted_loop_payload(sequence=2),),
        response_payloads=responses,
        settled_records=settled,
        observer_sample=observer,
    )

    assert evidence.to_record() == {
        "execution_model": execution_model,
        "maximum_aggregate_sql_sessions": 1,
        "maximum_serialized_wsgi_calls_per_process": 1,
        "maximum_simultaneous_sql_requests": 1,
        "persistent_worker_loops": [
            {
                "async_thread_token": 2,
                "loop_token": 7,
                "pid": 101,
                "wsgi_thread_token": 1,
            }
        ],
        "profile_id": profile.id,
        "status": "PASS",
    }

    changed_loop = (_adapted_loop_payload(sequence=2, loop_token=8),)
    with pytest.raises(
        runner.WorkerEvidenceError,
        match="persistent ASGI loop changed",
    ):
        runner.validate_adapted_flask_scaling_evidence(
            config=config,
            profile=profile,
            ready_records=ready_records,
            first_loop_records=(_adapted_loop_payload(sequence=1),),
            second_loop_records=changed_loop,
            response_payloads=responses,
            settled_records=settled,
            observer_sample=observer,
        )


@pytest.mark.asyncio
async def test_adapted_flask_profile_reconciles_loops_pool_and_teardown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch an adapted profile that skips persistent-loop or cleanup proof."""

    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=8,
    )
    profile = replace(
        _profile_by_family(
            runner,
            "flask-asgi-uvicorn-asyncio",
            workers=1,
        ),
        database_mode="sql_auth",
        platform_system=config.platform_system,
    )
    isolated = runner.prepare_isolated_application(
        config,
        source_directory=ROOT / "tests/production_framework",
        directory_name="adapted-flask-profile-test",
    )
    settings = runner.SqlAuthObserverSettings(
        host="127.0.0.1",
        port=14334,
        database="fastmssql_validation",
        username="fastmssql_owner",
        password="private-owner-password",
    )
    ready_record = {
        "candidate_sha": config.candidate_sha,
        "phase": "ready",
        "pid": 101,
        "pool_connected_monotonic": 12.0,
        "pool_created_monotonic": 11.0,
        "pool_created_pid": 101,
        "pool_identity": "pool-101",
        "pool_max_per_worker": 8,
        "process_started_monotonic": 10.0,
        "run_id": "adapted-flask",
        "worker_application_name": "fm-adapted-flask-101",
        "wheel_filename": config.wheel.name,
        "wheel_sha256": config.wheel_sha256,
    }
    shutdown_record = {**ready_record, "phase": "shutdown"}
    busy_sample = runner.SqlObserverSample(
        applications=(
            runner.ObserverApplicationSample(
                application_name="fm-adapted-flask-101",
                host_process_ids=(101,),
                sessions=1,
                requests=0,
            ),
        ),
        current_sessions=1,
        current_requests=0,
        maximum_sessions=1,
        maximum_requests=1,
        request_context_tokens=(),
    )
    zero_sample = replace(
        busy_sample,
        applications=(),
        current_sessions=0,
        current_requests=0,
    )
    execution_model = (
        "Flask via WsgiToAsgi: persistent ASGI loop, thread-sensitive WSGI "
        "serialization per process"
    )
    events: list[str] = []

    class FakeConnection:
        async def connect(self, *, validate: bool) -> None:
            assert validate is True
            events.append("observer-connect")

        async def disconnect(self) -> None:
            events.append("observer-disconnect")

    class FakeObserver:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def wait_for_ready_workers(self, records, *, timeout_seconds):
            assert tuple(records) == (ready_record,)
            assert timeout_seconds > 0
            return busy_sample

        async def wait_for_minimum_requests(self, minimum, *, timeout_seconds):
            assert minimum == 1
            assert timeout_seconds > 0
            events.append("wave-observed")
            return busy_sample

        async def sample(self):
            return busy_sample

        async def wait_for_zero_sessions(self, *, timeout_seconds):
            assert timeout_seconds > 0
            events.append("zero-sessions")
            return zero_sample

    outcome = runner.ProcessOutcome(
        pid=90,
        returncode=0,
        stdout="",
        stderr="",
        output_truncated=False,
        descendant_pids=(101,),
        graceful_stop=True,
        forced_cleanup=False,
    )

    class FakeSupervisor:
        pid = 90

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            del args

        async def wait_for_worker_records(self, **kwargs):
            assert kwargs["expected_count"] == 1
            return (ready_record,)

        def read_worker_records(self, **kwargs):
            assert kwargs["phase"] == "shutdown"
            return (shutdown_record,)

        async def stop(self):
            events.append("server-stop")
            return outcome

    async def fake_launch(**kwargs):
        assert await kwargs["readiness_probe"](8127)
        return runner.ServerLaunch(
            supervisor=FakeSupervisor(),
            port=8127,
            attempts=1,
            sanitized_command=(sys.executable, "-m", "uvicorn"),
        )

    async def fake_get(port: int, path: str, **kwargs):
        assert port == 8127
        assert path == "/ready"
        assert kwargs["timeout_seconds"] > 0
        return {"pid": 101, "state": "ready"}

    loop_collection = 0

    async def fake_collect(port: int, path: str, **kwargs):
        nonlocal loop_collection
        assert port == 8127
        assert kwargs["expected_pids"] == (101,)
        if path == "/package":
            return (
                {
                    "candidate_sha": config.candidate_sha,
                    "fastmssql_import_path": "/site-packages/fastmssql.so",
                    "pid": 101,
                    "wheel_filename": config.wheel.name,
                    "wheel_sha256": config.wheel_sha256,
                },
            )
        if path == "/principal":
            return (
                {
                    "application_name": "fm-adapted-flask-101",
                    "pid": 101,
                    "principal": "fastmssql_owner",
                    "session_id": 51,
                },
            )
        if path == "/loop":
            loop_collection += 1
            return (_adapted_loop_payload(sequence=loop_collection),)
        if path == "/execution/state":
            return (
                {
                    "active_other_requests": 0,
                    "completed_requests": 4,
                    "execution_model": execution_model,
                    "maximum_active_requests": 1,
                    "pid": 101,
                    "wsgi_thread_count": 1,
                },
            )
        assert path == "/pool"
        return (
            {
                "admission": {"active": 0, "capacity": 16, "rejected": 0},
                "application_name": "fm-adapted-flask-101",
                "operations": {},
                "pid": 101,
                "pool": {
                    "active_connections": 0,
                    "connections": 1,
                    "idle_connections": 1,
                    "max_size": 8,
                    "pending_gets": 0,
                },
            },
        )

    async def fake_request(port: int, path: str, **kwargs):
        assert port == 8127
        assert kwargs["expected_statuses"] == (200,)
        value = int(path.removeprefix("/execution/wait/"))
        sequence = value - 762_000 + 2
        return runner.LoopbackJsonResponse(
            status_code=200,
            payload=_flask_execution_payload(
                value=value,
                sequence=sequence,
                loop_token=7,
                wsgi_thread_token=1,
                active=1,
                maximum_active=1,
                async_thread_token=2,
                execution_model=execution_model,
            ),
            elapsed_seconds=0.25,
        )

    async def fake_port_not_listening(port: int) -> bool:
        assert port == 8127
        return False

    monkeypatch.setattr(
        runner,
        "create_observer_connection",
        lambda *args, **kwargs: FakeConnection(),
    )
    monkeypatch.setattr(runner, "SqlServerObserver", FakeObserver)
    monkeypatch.setattr(runner, "launch_with_port_retry", fake_launch)
    monkeypatch.setattr(runner, "http_get_json", fake_get)
    monkeypatch.setattr(runner, "collect_worker_payloads", fake_collect)
    monkeypatch.setattr(runner, "http_request_json", fake_request)
    monkeypatch.setattr(
        runner,
        "validate_package_record",
        lambda *args, **kwargs: dict(args[2]),
    )
    monkeypatch.setattr(
        runner,
        "loopback_port_is_listening",
        fake_port_not_listening,
    )

    result = await runner.run_adapted_flask_profile(
        config,
        isolated,
        profile,
        repository_root=ROOT,
        run_id="adapted-flask",
        policy=runner.SupervisorPolicy(),
        sql_auth_settings=settings,
        table_name="framework_items_flask",
        wave_values=(762_001, 762_002),
        sql_delay_ms=250,
    )

    record = result.to_record()
    assert record["status"] == "PASS"
    assert record["profile_id"] == profile.id
    assert record["persistent_worker_loops"] == [
        {
            "async_thread_token": 2,
            "loop_token": 7,
            "pid": 101,
            "wsgi_thread_token": 1,
        }
    ]
    assert record["ready_pids"] == [101]
    assert record["shutdown_pids"] == [101]
    assert record["sessions_after"] == 0
    assert events == [
        "observer-connect",
        "wave-observed",
        "server-stop",
        "zero-sessions",
        "observer-disconnect",
    ]


@pytest.mark.asyncio
async def test_adapted_flask_matrix_runs_every_selected_loop_and_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a matrix that skips an adapted loop or worker-count profile."""

    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=8,
    )
    profiles = tuple(
        replace(
            _profile_by_family(runner, family, workers=workers),
            database_mode="sql_auth",
            platform_system=config.platform_system,
        )
        for family, workers in (
            ("flask-asgi-uvicorn-asyncio", 1),
            ("flask-asgi-uvicorn-asyncio", 4),
            ("flask-asgi-uvicorn-uvloop", 2),
            ("flask-asgi-uvicorn-uvloop", 8),
        )
    )
    settings = runner.SqlAuthObserverSettings(
        host="127.0.0.1",
        port=14334,
        database="fastmssql_validation",
        username="fastmssql_owner",
        password="private-owner-password",
    )
    calls: list[dict[str, object]] = []

    async def fake_profile(config_arg, isolated, profile, **kwargs):
        assert config_arg is config
        assert isolated.root.is_relative_to(config.run_root)
        calls.append({"profile": profile, **kwargs})
        return profile.id

    monkeypatch.setattr(
        runner,
        "adapted_flask_profiles",
        lambda platform: profiles,
    )
    monkeypatch.setattr(runner, "run_adapted_flask_profile", fake_profile)

    results = await runner.run_adapted_flask_matrix(
        config,
        repository_root=ROOT,
        source_directory=ROOT / "tests/production_framework",
        sql_auth_settings=settings,
        table_name="framework_items_flask",
        policy=runner.SupervisorPolicy(),
    )

    assert results == tuple(profile.id for profile in profiles)
    assert [call["run_id"] for call in calls] == [
        "fasgi-aaaaaaaa-1",
        "fasgi-aaaaaaaa-2",
        "fasgi-aaaaaaaa-3",
        "fasgi-aaaaaaaa-4",
    ]
    assert [len(call["wave_values"]) for call in calls] == [2, 4, 2, 8]
    assert [call["wave_values"][0] for call in calls] == [
        762_101,
        762_201,
        762_301,
        762_401,
    ]


def test_adapted_flask_serialization_evidence_rejects_parallel_claims() -> None:
    """Catch a WsgiToAsgi result mislabeled as concurrent per process."""

    runner = _load_production_framework_runner()
    execution_model = (
        "Flask via WsgiToAsgi: persistent ASGI loop, thread-sensitive WSGI "
        "serialization per process"
    )
    values = (763_001, 763_002, 763_003, 763_004)

    def payloads(start_sequence: int) -> tuple[dict[str, object], ...]:
        return tuple(
            _flask_execution_payload(
                value=value,
                sequence=start_sequence + offset,
                loop_token=7,
                wsgi_thread_token=1,
                active=1,
                maximum_active=1,
                async_thread_token=2,
                execution_model=execution_model,
            )
            for offset, value in enumerate(values)
        )

    state = {
        "active_other_requests": 0,
        "completed_requests": 8,
        "execution_model": execution_model,
        "maximum_active_requests": 1,
        "pid": 101,
        "wsgi_thread_count": 1,
    }
    observer = runner.SqlObserverSample(
        applications=(
            runner.ObserverApplicationSample(
                application_name="fm-adapted-serialization-101",
                host_process_ids=(101,),
                sessions=1,
                requests=0,
            ),
        ),
        current_sessions=1,
        current_requests=0,
        maximum_sessions=1,
        maximum_requests=1,
        request_context_tokens=(),
    )
    pool = {
        "pid": 101,
        "pool": {
            "active_connections": 0,
            "connections": 1,
            "idle_connections": 1,
            "max_size": 8,
            "pending_gets": 0,
        },
    }

    evidence = runner.validate_adapted_flask_serialization_evidence(
        sequential_payloads=payloads(1),
        concurrent_payloads=payloads(5),
        expected_values=values,
        sequential_seconds=1.04,
        concurrent_seconds=1.07,
        state_record=state,
        observer_sample=observer,
        pool_record=pool,
        sql_delay_ms=250,
    )

    assert evidence.to_record() == {
        "concurrent_seconds": 1.07,
        "execution_model": execution_model,
        "loop_token": 7,
        "maximum_simultaneous_sql_requests": 1,
        "multi_process_scaling": "additional worker processes only",
        "pool_active_after": 0,
        "pool_pending_after": 0,
        "sequential_seconds": 1.04,
        "serialization_ratio": pytest.approx(1.07 / 1.04),
        "sql_delay_seconds": 0.25,
        "status": "PASS",
        "values": [763_001, 763_002, 763_003, 763_004],
        "wsgi_calls_per_process": 1,
    }

    with pytest.raises(
        runner.WorkerEvidenceError,
        match="thread-sensitive serialization timing",
    ):
        runner.validate_adapted_flask_serialization_evidence(
            sequential_payloads=payloads(1),
            concurrent_payloads=payloads(5),
            expected_values=values,
            sequential_seconds=1.04,
            concurrent_seconds=0.5,
            state_record=state,
            observer_sample=observer,
            pool_record=pool,
            sql_delay_ms=250,
        )


@pytest.mark.asyncio
async def test_adapted_flask_serialization_scenario_is_supervised_and_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch serialization evidence detached from a real server lifecycle."""

    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=8,
    )
    profile = replace(
        _profile_by_family(
            runner,
            "flask-asgi-uvicorn-asyncio",
            workers=1,
        ),
        database_mode="sql_auth",
        platform_system=config.platform_system,
    )
    isolated = runner.prepare_isolated_application(
        config,
        source_directory=ROOT / "tests/production_framework",
        directory_name="adapted-serialization-test",
    )
    settings = runner.SqlAuthObserverSettings(
        host="127.0.0.1",
        port=14334,
        database="fastmssql_validation",
        username="fastmssql_owner",
        password="private-owner-password",
    )
    ready_record = {
        "phase": "ready",
        "pid": 101,
        "run_id": "adapted-serialization",
        "worker_application_name": "fm-adapted-serialization-101",
    }
    shutdown_record = {**ready_record, "phase": "shutdown"}
    busy_sample = runner.SqlObserverSample(
        applications=(
            runner.ObserverApplicationSample(
                application_name="fm-adapted-serialization-101",
                host_process_ids=(101,),
                sessions=1,
                requests=1,
            ),
        ),
        current_sessions=1,
        current_requests=1,
        maximum_sessions=1,
        maximum_requests=1,
        request_context_tokens=(),
    )
    zero_sample = replace(
        busy_sample,
        applications=(),
        current_sessions=0,
        current_requests=0,
    )
    execution_model = (
        "Flask via WsgiToAsgi: persistent ASGI loop, thread-sensitive WSGI "
        "serialization per process"
    )
    events: list[str] = []

    class FakeConnection:
        async def connect(self, *, validate: bool) -> None:
            assert validate is True
            events.append("observer-connect")

        async def disconnect(self) -> None:
            events.append("observer-disconnect")

    class FakeObserver:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def wait_for_ready_workers(self, records, *, timeout_seconds):
            assert tuple(records) == (ready_record,)
            assert timeout_seconds > 0
            return busy_sample

        async def wait_for_minimum_requests(self, minimum, *, timeout_seconds):
            assert minimum == 1
            assert timeout_seconds > 0
            events.append("concurrent-wave-observed")
            return busy_sample

        async def wait_for_zero_sessions(self, *, timeout_seconds):
            assert timeout_seconds > 0
            events.append("zero-sessions")
            return zero_sample

    outcome = runner.ProcessOutcome(
        pid=90,
        returncode=0,
        stdout="",
        stderr="",
        output_truncated=False,
        descendant_pids=(101,),
        graceful_stop=True,
        forced_cleanup=False,
    )

    class FakeSupervisor:
        pid = 90

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            del args

        async def wait_for_worker_records(self, **kwargs):
            assert kwargs["expected_count"] == 1
            return (ready_record,)

        def read_worker_records(self, **kwargs):
            assert kwargs["phase"] == "shutdown"
            return (shutdown_record,)

        async def stop(self):
            events.append("server-stop")
            return outcome

    async def fake_launch(**kwargs):
        assert await kwargs["readiness_probe"](8128)
        return runner.ServerLaunch(
            supervisor=FakeSupervisor(),
            port=8128,
            attempts=1,
            sanitized_command=(sys.executable, "-m", "uvicorn"),
        )

    async def fake_get(port: int, path: str, **kwargs):
        assert port == 8128
        assert path == "/ready"
        assert kwargs["timeout_seconds"] > 0
        return {"pid": 101, "state": "ready"}

    request_lock = asyncio.Lock()
    request_sequence = 0

    async def fake_request(port: int, path: str, **kwargs):
        nonlocal request_sequence
        assert port == 8128
        assert kwargs["expected_statuses"] == (200,)
        if path.startswith("/execution/wait/"):
            value = int(path.removeprefix("/execution/wait/"))
            async with request_lock:
                request_sequence += 1
                sequence = request_sequence
                await asyncio.sleep(0.04)
            payload = _flask_execution_payload(
                value=value,
                sequence=sequence,
                loop_token=7,
                wsgi_thread_token=1,
                active=1,
                maximum_active=1,
                async_thread_token=2,
                delay_ms=50,
                execution_model=execution_model,
            )
        elif path == "/execution/state":
            payload = {
                "active_other_requests": 0,
                "completed_requests": 8,
                "execution_model": execution_model,
                "maximum_active_requests": 1,
                "pid": 101,
                "wsgi_thread_count": 1,
            }
        else:
            assert path == "/pool"
            payload = {
                "pid": 101,
                "pool": {
                    "active_connections": 0,
                    "connections": 1,
                    "idle_connections": 1,
                    "max_size": 8,
                    "pending_gets": 0,
                },
            }
        return runner.LoopbackJsonResponse(
            status_code=200,
            payload=payload,
            elapsed_seconds=0.04,
        )

    async def fake_port_not_listening(port: int) -> bool:
        assert port == 8128
        return False

    monkeypatch.setattr(
        runner,
        "create_observer_connection",
        lambda *args, **kwargs: FakeConnection(),
    )
    monkeypatch.setattr(runner, "SqlServerObserver", FakeObserver)
    monkeypatch.setattr(runner, "launch_with_port_retry", fake_launch)
    monkeypatch.setattr(runner, "http_get_json", fake_get)
    monkeypatch.setattr(runner, "http_request_json", fake_request)
    monkeypatch.setattr(
        runner,
        "loopback_port_is_listening",
        fake_port_not_listening,
    )

    result = await runner.run_adapted_flask_serialization_scenario(
        config,
        isolated,
        profile,
        run_id="adapted-serialization",
        policy=runner.SupervisorPolicy(),
        sql_auth_settings=settings,
        table_name="framework_items_flask",
        values=(764_001, 764_002, 764_003, 764_004),
        sql_delay_ms=50,
    )

    record = result.to_record()
    assert record["status"] == "PASS"
    assert record["profile_id"] == profile.id
    assert record["wsgi_calls_per_process"] == 1
    assert 0.75 <= record["serialization_ratio"] <= 1.35
    assert record["maximum_simultaneous_sql_requests"] == 1
    assert record["ready_pids"] == [101]
    assert record["shutdown_pids"] == [101]
    assert record["sessions_after"] == 0
    assert events == [
        "observer-connect",
        "concurrent-wave-observed",
        "server-stop",
        "zero-sessions",
        "observer-disconnect",
    ]


@pytest.mark.asyncio
async def test_adapted_serialization_preserves_pre_sql_http_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch observer failure masking a failed adapted concurrent wave."""

    runner = _load_production_framework_runner()
    config = replace(
        _config_with_actual_wheel_hash(runner, tmp_path),
        database_mode="sql_auth",
        global_connection_budget=8,
    )
    profile = replace(
        _profile_by_family(
            runner,
            "flask-asgi-uvicorn-asyncio",
            workers=1,
        ),
        database_mode="sql_auth",
        platform_system=config.platform_system,
    )
    isolated = runner.prepare_isolated_application(
        config,
        source_directory=ROOT / "tests/production_framework",
        directory_name="adapted-serialization-primary-error-test",
    )
    settings = runner.SqlAuthObserverSettings(
        host="127.0.0.1",
        port=14334,
        database="fastmssql_validation",
        username="fastmssql_owner",
        password="private-owner-password",
    )
    ready_record = {
        "phase": "ready",
        "pid": 101,
        "run_id": "adapted-serialization-error",
        "worker_application_name": "fm-adapted-serialization-error-101",
    }
    request_failed = asyncio.Event()
    pending_cancellations: list[int] = []
    request_count = 0
    events: list[str] = []

    class FakeConnection:
        async def connect(self, *, validate: bool) -> None:
            assert validate is True
            events.append("observer-connect")

        async def disconnect(self) -> None:
            events.append("observer-disconnect")

    class FakeObserver:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def wait_for_ready_workers(self, records, *, timeout_seconds):
            assert tuple(records) == (ready_record,)
            assert timeout_seconds > 0

        async def wait_for_minimum_requests(self, minimum, *, timeout_seconds):
            assert minimum == 1
            assert timeout_seconds > 0
            await request_failed.wait()
            raise runner.ReadinessTimeoutError(
                "observer would mask the adapted HTTP failure"
            )

    class FakeSupervisor:
        pid = 90

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            del args

        async def wait_for_worker_records(self, **kwargs):
            assert kwargs["expected_count"] == 1
            return (ready_record,)

    async def fake_launch(**kwargs):
        assert await kwargs["readiness_probe"](8128)
        return runner.ServerLaunch(
            supervisor=FakeSupervisor(),
            port=8128,
            attempts=1,
            sanitized_command=(sys.executable, "-m", "uvicorn"),
        )

    async def fake_get(port: int, path: str, **kwargs):
        assert port == 8128
        assert path == "/ready"
        assert kwargs["timeout_seconds"] > 0
        return {"pid": 101, "state": "ready"}

    async def fake_request(port: int, path: str, **kwargs):
        nonlocal request_count
        assert port == 8128
        assert path.startswith("/execution/wait/")
        assert kwargs["expected_statuses"] == (200,)
        request_count += 1
        if request_count <= 4:
            return runner.LoopbackJsonResponse(
                status_code=200,
                payload={"value": request_count},
                elapsed_seconds=0.001,
            )
        if request_count == 5:
            request_failed.set()
            raise runner.HttpProbeError(
                "primary adapted serialization HTTP failure"
            )
        try:
            await asyncio.Event().wait()
        finally:
            pending_cancellations.append(request_count)

    monkeypatch.setattr(
        runner,
        "create_observer_connection",
        lambda *args, **kwargs: FakeConnection(),
    )
    monkeypatch.setattr(runner, "SqlServerObserver", FakeObserver)
    monkeypatch.setattr(runner, "launch_with_port_retry", fake_launch)
    monkeypatch.setattr(runner, "http_get_json", fake_get)
    monkeypatch.setattr(runner, "http_request_json", fake_request)

    with pytest.raises(
        runner.HttpProbeError,
        match="primary adapted serialization HTTP failure",
    ):
        await runner.run_adapted_flask_serialization_scenario(
            config,
            isolated,
            profile,
            run_id="adapted-serialization-error",
            policy=runner.SupervisorPolicy(),
            sql_auth_settings=settings,
            table_name="framework_items_flask",
            values=(764_001, 764_002, 764_003, 764_004),
            sql_delay_ms=50,
        )

    assert request_failed.is_set()
    assert len(pending_cancellations) == 3
    assert events == ["observer-connect", "observer-disconnect"]


def test_execution_model_language_is_exact_and_rejects_equivalence_claims() -> None:
    """Catch report language that erases the measured framework differences."""

    runner = _load_production_framework_runner()
    labels = runner.execution_model_labels()
    assert labels == (
        "native ASGI: concurrent requests on persistent event loop",
        "Flask WSGI: async view, occupied WSGI worker/thread",
        (
            "Flask via WsgiToAsgi: persistent ASGI loop, thread-sensitive "
            "WSGI serialization per process"
        ),
    )
    assert framework_app.FLASK_WSGI_EXECUTION_MODEL == labels[1]
    assert framework_app.ADAPTED_FLASK_EXECUTION_MODEL == labels[2]
    assert runner.validate_execution_model_language(labels) == labels

    for forbidden in (
        "All modes are equivalent",
        "They have the same throughput",
        "This is fully async Flask",
        "This makes true-async Flask",
        "Flask is now native ASGI Flask",
    ):
        with pytest.raises(
            runner.WorkerEvidenceError,
            match="forbidden execution-model claim",
        ):
            runner.validate_execution_model_language((*labels, forbidden))


def test_observer_connection_factory_lazily_uses_one_installed_wheel_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_production_framework_runner()
    imports: list[str] = []
    constructor: dict[str, object] = {}

    class FakeConfig:
        def __init__(self, **values: object) -> None:
            self.values = values

    class FakeSslConfig:
        @staticmethod
        def development() -> str:
            return "development-tls"

    def fake_connection(**values: object) -> object:
        constructor.update(values)
        return object()

    fake_driver = SimpleNamespace(
        Connection=fake_connection,
        LifecycleConfig=FakeConfig,
        PoolConfig=FakeConfig,
        SslConfig=FakeSslConfig,
        TimeoutConfig=FakeConfig,
    )

    def fake_import(name: str):
        imports.append(name)
        return fake_driver

    monkeypatch.setattr(runner.importlib, "import_module", fake_import)
    settings = runner.SqlAuthObserverSettings(
        host="127.0.0.1",
        port=14334,
        database="fastmssql_validation",
        username="fastmssql_observer",
        password="private-observer-password",
    )
    connection = runner.create_observer_connection(
        settings,
        application_name="fm-run-native-observer",
    )

    assert connection is not None
    assert imports == ["fastmssql"]
    assert constructor["application_name"] == "fm-run-native-observer"
    assert constructor["password"] == "private-observer-password"
    assert constructor["pool_config"].values == {
        "connection_timeout_secs": 5,
        "idle_timeout_secs": None,
        "max_lifetime_secs": None,
        "max_size": 1,
        "min_idle": 0,
        "retry_connection": False,
    }
    assert "private-observer-password" not in repr(settings)


def test_observer_configuration_and_ready_records_fail_closed() -> None:
    runner = _load_production_framework_runner()
    valid = {
        "host": "127.0.0.1",
        "port": 14334,
        "database": "fastmssql_validation",
        "username": "fastmssql_owner",
        "password": "private-observer-password",
    }
    invalid_values = (
        ("host", object()),
        ("host", "server;unsafe"),
        ("port", True),
        ("database", 7),
        ("username", "unsafe user"),
        ("password", 7),
        ("password", "contains\0nul"),
    )
    for field_name, value in invalid_values:
        with pytest.raises(runner.RunnerConfigurationError) as captured:
            runner.SqlAuthObserverSettings(**{**valid, field_name: value})
        assert valid["password"] not in str(captured.value)

    source = _FakeObserverSource([[]])
    with pytest.raises(runner.RunnerConfigurationError):
        runner.SqlServerObserver(
            source=source,
            worker_prefix=object(),
            observer_application_name="fm-run-observer",
        )
    with pytest.raises(runner.RunnerConfigurationError):
        runner.create_observer_connection(
            runner.SqlAuthObserverSettings(**valid),
            application_name=None,
        )

    empty = runner.SqlObserverSample(
        applications=(),
        current_sessions=0,
        current_requests=0,
        maximum_sessions=0,
        maximum_requests=0,
        request_context_tokens=(),
    )
    for malformed in (
        [],
        [
            {
                "phase": "ready",
                "pid": True,
                "worker_application_name": "fm-run-1",
            }
        ],
        [
            {
                "phase": "ready",
                "pid": 1,
                "worker_application_name": 1,
            }
        ],
    ):
        with pytest.raises(
            runner.WorkerEvidenceError,
            match="worker SQL sessions do not match ready records",
        ):
            runner.reconcile_worker_sessions(empty, malformed)


@pytest.mark.asyncio
async def test_worker_application_name_is_recorded_and_bounded(
    tmp_path: Path,
) -> None:
    environment = _valid_worker_environment(tmp_path)
    events: list[object] = []
    connection = _FakeConnection(events)
    application = framework_app.create_fastapi_app(
        environment=environment,
        connection_factory=_connection_factory(connection, events),
    )
    async with application.router.lifespan_context(application):
        ready = json.loads(
            _worker_record(environment, "ready").read_text(encoding="utf-8")
        )
    assert ready["worker_application_name"] == (
        f"{environment['FASTMSSQL_FRAMEWORK_APPLICATION_NAME']}-{os.getpid()}"
    )

    overlong = _valid_worker_environment(tmp_path / "overlong")
    overlong["FASTMSSQL_FRAMEWORK_APPLICATION_NAME"] = "a" * 128
    rejected_events: list[object] = []
    rejected = framework_app.create_fastapi_app(
        environment=overlong,
        connection_factory=_connection_factory(
            _FakeConnection(rejected_events),
            rejected_events,
        ),
    )
    with pytest.raises(
        framework_app.ConfigurationError,
        match="worker application name exceeds SQL Server's 128-character limit",
    ):
        async with rejected.router.lifespan_context(rejected):
            pytest.fail("overlong worker application name became ready")
    assert rejected_events == []


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
        assert ready_payload["pool_created_pid"] == os.getpid()
        assert (
            ready_payload["process_started_monotonic"]
            <= ready_payload["pool_created_monotonic"]
            <= ready_payload["pool_connected_monotonic"]
        )
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
        assert constructor_arguments["pool_config"].connection_timeout_secs is None
        assert constructor_arguments["timeout_config"].acquire_timeout_secs == 0.25
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
