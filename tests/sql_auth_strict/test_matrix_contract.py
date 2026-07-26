import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from sql_auth_strict.cases import (
    source_case_ids,
    source_case_occurrences,
    spec_case_ids,
)
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.conftest import redact_message
from sql_auth_strict.operation_metrics_assertions import (
    BUCKET_BOUNDS_SECONDS,
    OPERATION_NAMES,
)


ROOT = Path(__file__).resolve().parents[2]


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def test_sql_auth_repository_contract_files_exist() -> None:
    required = {
        ROOT / ".env.sql-auth.example",
        ROOT / "docker-compose.sql-auth.yml",
    }
    assert {path for path in required if not path.is_file()} == set()


def test_local_secrets_and_artifacts_are_ignored() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env.sql-auth.local" in ignored
    assert ".artifacts/sql-auth/" in ignored


def test_sql_auth_orchestration_files_exist_and_are_executable() -> None:
    scripts = (
        ROOT / "scripts/sql_auth/wait_for_sql.sh",
        ROOT / "scripts/sql_auth/provision.sh",
        ROOT / "scripts/sql_auth/provision.sql",
    )
    assert all(path.is_file() for path in scripts)
    assert all(path.stat().st_mode & 0o111 for path in scripts[:2])


def test_full_runner_contract() -> None:
    runner = ROOT / "scripts/sql_auth/run_all.sh"
    assert runner.is_file()
    assert runner.stat().st_mode & 0o111
    source = runner.read_text(encoding="utf-8")
    assert "fastmssql-sql-auth-dev" in source
    assert "fastmssql_upstream_regression" in source
    assert "-n 1" in source
    for ignored in (
        "tests/test_azure_auth_advanced.py",
        "tests/test_azure_authentication.py",
        "tests/test_azure_cli_path_validation.py",
        "tests/test_transaction_azure_auth.py",
        "tests/test_transaction_azure_auth_advanced.py",
        "tests/sql_auth_strict",
    ):
        assert f"--ignore={ignored}" in source


def test_full_runner_uses_original_local_regression_display_name(
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / "repo"
    scripts = sandbox / "scripts" / "sql_auth"
    scripts.mkdir(parents=True)
    runner = scripts / "run_all.sh"
    shutil.copy2(ROOT / "scripts/sql_auth/run_all.sh", runner)

    (sandbox / ".env.sql-auth.local").write_text(
        "\n".join(
            (
                "FASTMSSQL_SQL_AUTH_CONTAINER=fastmssql-sql-auth-dev",
                "FASTMSSQL_SQL_AUTH_HOST=127.0.0.1",
                "FASTMSSQL_SQL_AUTH_PORT=14333",
                "FASTMSSQL_SQL_AUTH_OWNER_USER=test_owner",
                "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD=test_only_password",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _write_executable(
        scripts / "provision.sh",
        "#!/usr/bin/env bash\nexit 0\n",
    )

    fake_bin = sandbox / "fake-bin"
    fake_bin.mkdir()
    for name in ("uv", "cargo", "docker"):
        _write_executable(
            fake_bin / name,
            "#!/usr/bin/env bash\nexit 0\n",
        )

    environment = os.environ.copy()
    environment["PATH"] = (
        f"{fake_bin}{os.pathsep}{environment.get('PATH', '')}"
    )
    completed = subprocess.run(
        [str(runner)],
        cwd=sandbox,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert (
        "[sql-auth] original-local-regression: passed" in completed.stdout
    )
    assert "[sql-auth] upstream:" not in completed.stdout

    artifacts = sandbox / ".artifacts" / "sql-auth"
    assert (artifacts / "upstream.exitcode").read_text(
        encoding="utf-8"
    ) == "0\n"
    assert (artifacts / "upstream.log").is_file()
    command = (artifacts / "upstream.command").read_text(
        encoding="utf-8"
    )
    assert "uv run pytest -n 1 tests" in command
    assert "--ignore=tests/sql_auth_strict" in command
    for forbidden in (
        "git ",
        "gh ",
        "curl ",
        "http://",
        "https://",
        "Rivendael/FastMssql",
    ):
        assert forbidden not in command
    assert not (
        artifacts / "original-local-regression.exitcode"
    ).exists()


def test_report_generator_preserves_not_run_and_redacts(
    tmp_path: Path,
) -> None:
    generator = ROOT / "scripts/sql_auth/generate_report.py"
    assert generator.is_file()
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    secret = "ReportSecret_MustNotLeak_2026!"
    strict_results = artifact_dir / "strict-results.json"
    strict_results.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": {
                    "ENV-001": {
                        "outcome": "passed",
                        "nodeid": "test_environment.py::test_pass",
                        "duration_seconds": 0.1,
                        "message": "",
                    },
                    "AUTH-001": {
                        "outcome": "failed",
                        "nodeid": "test_auth.py::test_fail",
                        "duration_seconds": 0.2,
                        "message": f"credential={secret}",
                    },
                    "CONN-001": {
                        "outcome": "error",
                        "nodeid": "test_connection.py::test_error",
                        "duration_seconds": 0.3,
                        "message": "setup error",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "strict.exitcode").write_text("1\n", encoding="utf-8")
    (artifact_dir / "upstream.exitcode").write_text("0\n", encoding="utf-8")
    (artifact_dir / "upstream.xml").write_text(
        '<testsuite tests="3" failures="0" errors="0" skipped="1"/>',
        encoding="utf-8",
    )
    (artifact_dir / "framework-metrics.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": {
                    "FRAME-009": {
                        "ratio": 0.25,
                        "redaction_probe": secret,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "load-metrics.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": {
                    "LOAD-008": {
                        "transaction_count": 1_000,
                        "transactions_per_second": 500.0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    matrix_output = tmp_path / "matrix.md"
    report_output = tmp_path / "report.md"
    environment = os.environ.copy()
    environment["FASTMSSQL_SQL_AUTH_OWNER_PASSWORD"] = secret
    completed = subprocess.run(
        [
            sys.executable,
            str(generator),
            "--spec",
            str(
                ROOT
                / "docs/superpowers/specs/"
                "2026-07-24-fastmssql-sql-auth-validation-design.md"
            ),
            "--strict-results",
            str(strict_results),
            "--artifact-dir",
            str(artifact_dir),
            "--matrix-output",
            str(matrix_output),
            "--report-output",
            str(report_output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr
    matrix = matrix_output.read_text(encoding="utf-8")
    report = report_output.read_text(encoding="utf-8")
    assert (
        sum(line.startswith("| `") for line in matrix.splitlines()) == 337
    )
    assert "| `ENV-001` | PASS |" in matrix
    assert "| `AUTH-001` | FAIL |" in matrix
    assert "| `CONN-001` | ERROR |" in matrix
    assert "| `POOL-001` | NOT RUN |" in matrix
    assert "test_auth.py::test_fail" in matrix
    assert "<redacted>" in matrix
    assert secret not in matrix
    assert secret not in report
    assert "ARM64" in report
    assert "Azure" in report
    assert "Windows authentication" in report
    assert "strict" in report
    assert "| original-local-regression | 0 | 3 | 0 | 0 | 1 |" in report
    assert "| upstream |" not in report
    assert "FastAPI/native ASGI" in report
    assert "Flask/WSGI" in report
    assert "Flask via WsgiToAsgi" in report
    assert "Fixed exclusions" in report
    assert "FRAME-009" in report
    assert "0.25" in report
    assert "Load metrics" in report
    assert "LOAD-008" in report
    assert "transactions_per_second" in report
    assert "500.0" in report


def test_report_generator_can_require_complete_evidence(
    tmp_path: Path,
) -> None:
    generator = ROOT / "scripts/sql_auth/generate_report.py"
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    strict_results = artifact_dir / "strict-results.json"
    strict_results.write_text(
        json.dumps({"schema_version": 1, "cases": {}}),
        encoding="utf-8",
    )
    matrix_output = tmp_path / "matrix.md"
    report_output = tmp_path / "report.md"

    completed = subprocess.run(
        [
            sys.executable,
            str(generator),
            "--spec",
            str(
                ROOT
                / "docs/superpowers/specs/"
                "2026-07-24-fastmssql-sql-auth-validation-design.md"
            ),
            "--strict-results",
            str(strict_results),
            "--artifact-dir",
            str(artifact_dir),
            "--matrix-output",
            str(matrix_output),
            "--report-output",
            str(report_output),
            "--require-complete",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "missing evidence for 337 case(s)" in completed.stderr
    assert matrix_output.is_file()
    assert report_output.is_file()
    assert "| NOT RUN | 337 |" in report_output.read_text(encoding="utf-8")


def test_config_redacts_password(monkeypatch) -> None:
    monkeypatch.setenv(
        "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD", "NeverPrintMe_2026!"
    )
    config = SqlAuthConfig.from_env(require_all=False)
    assert "NeverPrintMe_2026!" not in repr(config)


def test_approved_spec_contains_337_unique_case_ids() -> None:
    spec = ROOT / (
        "docs/superpowers/specs/"
        "2026-07-24-fastmssql-sql-auth-validation-design.md"
    )
    ids = spec_case_ids(spec)
    assert len(ids) == 337


def test_framework_contract_is_wired_into_runner_and_report() -> None:
    runner = (ROOT / "scripts/sql_auth/run_all.sh").read_text(
        encoding="utf-8"
    )
    report_source = (
        ROOT / "scripts/sql_auth/generate_report.py"
    ).read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "tests/sql_auth_strict/test_framework_integration.py" in runner
    assert "record framework \\" in runner
    assert "FASTMSSQL_FRAMEWORK_METRICS_PATH" in runner
    assert "framework-results.json" in runner
    assert "framework.xml" in runner
    assert "FastAPI/native ASGI" in report_source
    assert "Flask/WSGI" in report_source
    assert "Flask via WsgiToAsgi" in report_source
    assert "framework-metrics.json" in report_source
    assert "framework:" in pyproject


def test_load_metrics_are_wired_into_runner_and_report() -> None:
    runner = (ROOT / "scripts/sql_auth/run_all.sh").read_text(
        encoding="utf-8"
    )
    report_source = (
        ROOT / "scripts/sql_auth/generate_report.py"
    ).read_text(encoding="utf-8")
    assert "FASTMSSQL_LOAD_METRICS_PATH" in runner
    assert "load-metrics.json" in runner
    assert "load-metrics.json" in report_source
    assert "Load metrics" in report_source


def test_extended_transaction_stress_harness_is_bounded_and_opt_in() -> None:
    python_runner = ROOT / "scripts/sql_auth/transaction_stress.py"
    shell_runner = ROOT / "scripts/sql_auth/run_transaction_stress.sh"
    assert python_runner.is_file()
    assert shell_runner.is_file()
    assert shell_runner.stat().st_mode & 0o111
    source = python_runner.read_text(encoding="utf-8")
    assert "99_999" in source
    assert "--profiles" in source
    assert "--connection-strategy" in source
    assert "per-transaction" in source
    assert '"pooled"' in source
    assert "--pool-size" in source
    assert "shared_pool.transaction()" in source
    assert "distinct sampled SQL sessions exceeded pool size" in source
    assert "transactions must be between 1 and 99,999" in source
    assert "concurrency must be between 1 and 500" in source
    shell_source = shell_runner.read_text(encoding="utf-8")
    assert ".env.sql-auth.local" in shell_source
    assert "10_000:100,99_999:100" in shell_source
    assert "99_999:200" in shell_source
    assert "FASTMSSQL_TRANSACTION_STRESS_STRATEGY" in shell_source
    assert "FASTMSSQL_TRANSACTION_STRESS_POOL_SIZE" in shell_source
    assert '--connection-strategy "${strategy}"' in shell_source
    assert '--pool-size "${pool_size}"' in shell_source
    assert "transaction_stress.py" in shell_source
    assert "run_transaction_stress.sh" not in (
        ROOT / "scripts/sql_auth/run_all.sh"
    ).read_text(encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(python_runner), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--profiles" in completed.stdout


def test_operation_metrics_stress_harness_is_bounded_and_opt_in() -> None:
    python_runner = ROOT / "scripts/sql_auth/operation_metrics_stress.py"
    shell_runner = (
        ROOT / "scripts/sql_auth/run_operation_metrics_stress.sh"
    )
    assert python_runner.is_file()
    assert shell_runner.is_file()
    assert shell_runner.stat().st_mode & 0o111

    source = python_runner.read_text(encoding="utf-8")
    assert "99_999" in source
    assert "DEFAULT_WORKERS = 200" in source
    assert "DEFAULT_POOL_SIZE = 100" in source
    assert "REQUIRED_PAIRS = 3" in source
    assert "DEFAULT_MAXIMUM_MEDIAN_DEGRADATION = 0.15" in source
    assert "asyncio.TaskGroup" in source
    assert "from statistics import median" in source
    assert "operation_stats_scrapes_during_timing" in source
    assert "operations must be between 1 and 99,999" in source
    assert "workers must be between 1 and 500" in source
    assert "source SHA must be exactly 40 lowercase hexadecimal" in source

    tree = ast.parse(source, filename=str(python_runner))
    literals = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id
        in {"OPERATION_NAMES", "BUCKET_BOUNDS_SECONDS", "PAIR_MODE_ORDER"}
    }
    assert tuple(literals["OPERATION_NAMES"]) == OPERATION_NAMES
    assert tuple(literals["BUCKET_BOUNDS_SECONDS"]) == BUCKET_BOUNDS_SECONDS
    assert tuple(
        tuple(pair) for pair in literals["PAIR_MODE_ORDER"]
    ) == (
        (False, True),
        (True, False),
        (False, True),
    )

    shell_source = shell_runner.read_text(encoding="utf-8")
    for token in (
        ".env.sql-auth.local",
        "--operations 99999",
        "--workers 200",
        "--pool-size 100",
        "--pairs 3",
        "--maximum-median-degradation 0.15",
        "--source-sha",
        "operation-metrics-stress.json",
    ):
        assert token in shell_source
    runner = (ROOT / "scripts/sql_auth/run_all.sh").read_text(
        encoding="utf-8"
    )
    assert "run_operation_metrics_stress.sh" not in runner

    completed = subprocess.run(
        [sys.executable, str(python_runner), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    for option in (
        "--operations",
        "--workers",
        "--pool-size",
        "--pairs",
        "--maximum-median-degradation",
        "--source-sha",
        "--metrics-output",
    ):
        assert option in completed.stdout


def test_result_messages_redact_every_nonempty_password() -> None:
    assert redact_message(
        "owner=OwnerSecret readonly=ReadonlySecret",
        ("OwnerSecret", "", "ReadonlySecret"),
    ) == "owner=<redacted> readonly=<redacted>"


def test_every_spec_case_is_attached_to_test_source() -> None:
    spec = ROOT / (
        "docs/superpowers/specs/"
        "2026-07-24-fastmssql-sql-auth-validation-design.md"
    )
    test_sources = sorted(
        path
        for path in (ROOT / "tests/sql_auth_strict").glob("test_*.py")
        if path.name != "test_matrix_contract.py"
    )
    assert source_case_ids(test_sources) == spec_case_ids(spec)


def test_every_case_id_occurs_in_exactly_one_test_source() -> None:
    test_sources = sorted(
        path
        for path in (ROOT / "tests/sql_auth_strict").glob("test_*.py")
        if path.name != "test_matrix_contract.py"
    )
    occurrences = source_case_occurrences(test_sources)
    assert {
        case_id: count for case_id, count in occurrences.items() if count != 1
    } == {}


def test_resilience_and_load_cases_are_routed_to_their_runner_lanes() -> None:
    path = ROOT / "tests/sql_auth_strict/test_resilience_load.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    incorrectly_routed: dict[str, str] = {}

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = {ast.unparse(decorator) for decorator in node.decorator_list}
        case_ids = {
            argument.value
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "case"
            for argument in decorator.args
            if isinstance(argument, ast.Constant)
            and isinstance(argument.value, str)
        }
        for case_id in case_ids:
            expected_marker = (
                "pytest.mark.resilience"
                if case_id.startswith("RES-")
                else "pytest.mark.load"
                if case_id.startswith("LOAD-") or case_id == "OPMET-011"
                else ""
            )
            if expected_marker and expected_marker not in decorators:
                incorrectly_routed[case_id] = node.name

    assert incorrectly_routed == {}


def test_operation_metric_cases_are_routed_to_exact_runner_lanes() -> None:
    framework_path = (
        ROOT / "tests/sql_auth_strict/test_framework_integration.py"
    )
    framework_source = framework_path.read_text(encoding="utf-8")
    framework_tree = ast.parse(
        framework_source,
        filename=str(framework_path),
    )
    assert "pytest.mark.framework" in framework_source
    framework_cases = {
        argument.value
        for node in framework_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id == "case"
        for argument in decorator.args
        if isinstance(argument, ast.Constant)
        and isinstance(argument.value, str)
        and argument.value.startswith("OPMET-")
    }
    assert framework_cases == {"OPMET-014", "OPMET-015", "OPMET-016"}

    load_path = ROOT / "tests/sql_auth_strict/test_resilience_load.py"
    load_tree = ast.parse(
        load_path.read_text(encoding="utf-8"),
        filename=str(load_path),
    )
    load_functions = {
        argument.value: {
            ast.unparse(item)
            for item in node.decorator_list
        }
        for node in load_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id == "case"
        for argument in decorator.args
        if isinstance(argument, ast.Constant)
        and argument.value == "OPMET-011"
    }
    assert load_functions == {
        "OPMET-011": {
            "case('OPMET-011')",
            "pytest.mark.load",
            "pytest.mark.asyncio",
            "pytest.mark.timeout(120)",
        }
    }


def test_strict_tests_do_not_swallow_failures() -> None:
    violations: list[str] = []
    for path in sorted((ROOT / "tests/sql_auth_strict").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        broad_handlers = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
            and (
                node.type is None
                or (
                    isinstance(node.type, ast.Name)
                    and node.type.id == "Exception"
                )
            )
        ]
        if broad_handlers:
            violations.append(str(path.relative_to(ROOT)))
    assert violations == []
