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
        sum(line.startswith("| `") for line in matrix.splitlines()) == 226
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


def test_config_redacts_password(monkeypatch) -> None:
    monkeypatch.setenv(
        "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD", "NeverPrintMe_2026!"
    )
    config = SqlAuthConfig.from_env(require_all=False)
    assert "NeverPrintMe_2026!" not in repr(config)


def test_approved_spec_contains_226_unique_case_ids() -> None:
    spec = ROOT / (
        "docs/superpowers/specs/"
        "2026-07-24-fastmssql-sql-auth-validation-design.md"
    )
    ids = spec_case_ids(spec)
    assert len(ids) == 226


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
