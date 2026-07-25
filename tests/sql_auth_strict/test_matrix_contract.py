import ast
import json
import os
from pathlib import Path
import subprocess
import sys

from sql_auth_strict.cases import (
    source_case_ids,
    source_case_occurrences,
    spec_case_ids,
)
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.conftest import redact_message


ROOT = Path(__file__).resolve().parents[2]


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
        sum(line.startswith("| `") for line in matrix.splitlines()) == 277
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
    assert "upstream" in report
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
    assert "missing evidence for 277 case(s)" in completed.stderr
    assert matrix_output.is_file()
    assert report_output.is_file()
    assert "| NOT RUN | 277 |" in report_output.read_text(encoding="utf-8")


def test_config_redacts_password(monkeypatch) -> None:
    monkeypatch.setenv(
        "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD", "NeverPrintMe_2026!"
    )
    config = SqlAuthConfig.from_env(require_all=False)
    assert "NeverPrintMe_2026!" not in repr(config)


def test_approved_spec_contains_277_unique_case_ids() -> None:
    spec = ROOT / (
        "docs/superpowers/specs/"
        "2026-07-24-fastmssql-sql-auth-validation-design.md"
    )
    ids = spec_case_ids(spec)
    assert len(ids) == 277


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
                if case_id.startswith("LOAD-")
                else ""
            )
            if expected_marker and expected_marker not in decorators:
                incorrectly_routed[case_id] = node.name

    assert incorrectly_routed == {}


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
