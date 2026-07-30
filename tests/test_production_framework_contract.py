from __future__ import annotations

import ast
from pathlib import Path
import re
import tomllib


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
