import ast
import hashlib
import importlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

import pytest

from sql_auth_strict.cases import (
    source_case_ids,
    source_case_occurrences,
    spec_case_ids,
)
from sql_auth_strict import conftest as strict_conftest
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.conftest import redact_message
from sql_auth_strict.operation_metrics_assertions import (
    BUCKET_BOUNDS_SECONDS,
    OPERATION_NAMES,
)
from sql_auth_strict.test_production_framework_matrix import (
    production_framework_evidence as _production_framework_evidence,
)


ROOT = Path(__file__).resolve().parents[2]


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _write_fake_uv(path: Path) -> None:
    _write_executable(
        path,
        """#!/usr/bin/env bash
set -u
if [[ "${1:-}" == "run" && "${2:-}" == "python" && "${3:-}" == "-c" ]]; then
  case "${4:-}" in
    *"sys.executable"*)
      printf '%s\\n' "${FASTMSSQL_TEST_PYTHON_EXECUTABLE:?}"
      ;;
    *"sys.base_prefix"*)
      printf '%s\\n' "${FASTMSSQL_TEST_PYTHON_HOME:?}"
      ;;
    *)
      exit 64
      ;;
  esac
fi
exit 0
""",
    )


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


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
    assert branch_index is not None, "workflow has no push branch list"
    branches: set[str] = set()
    for line in on_lines[branch_index + 1 :]:
        if line.startswith("      - "):
            branches.add(line.removeprefix("      - ").strip("'\""))
            continue
        if line.strip() and len(line) - len(line.lstrip()) <= 4:
            break
    assert branches, "workflow push branch list is empty"
    return branches


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
    assert "record named-instance-load " in source
    assert "scripts/sql_auth/run_named_instance_stress.sh" in source
    assert "record result-stream-load " in source
    assert "scripts/sql_auth/run_result_stream_stress.sh" in source
    assert "record query-many-load " in source
    assert "scripts/sql_auth/run_query_many_stress.sh" in source
    assert "tests/sql_auth_strict/test_resultsets_streaming.py" in source
    assert "tests/sql_auth_strict/test_rpc_results.py" in source
    assert "tests/sql_auth_strict/test_query_many_strict.py" in source
    assert "tests/sql_auth_strict/test_named_instance_strict.py" in source
    assert source.index("record provision ") < source.index(
        "record named-instance-load "
    )
    assert source.index("record named-instance-load ") < source.index(
        "record result-stream-load "
    )
    assert source.index("record result-stream-load ") < source.index(
        "record query-many-load "
    )
    assert source.index("record query-many-load ") < source.index("record strict ")
    for ignored in (
        "tests/test_azure_auth_advanced.py",
        "tests/test_azure_authentication.py",
        "tests/test_azure_cli_path_validation.py",
        "tests/test_transaction_azure_auth.py",
        "tests/test_transaction_azure_auth_advanced.py",
        "tests/sql_auth_strict",
    ):
        assert f"--ignore={ignored}" in source


def test_fake_uv_emulates_runner_python_discovery(tmp_path: Path) -> None:
    fake_uv = tmp_path / "uv"
    _write_fake_uv(fake_uv)
    environment = os.environ.copy()
    environment["FASTMSSQL_TEST_PYTHON_EXECUTABLE"] = sys.executable
    environment["FASTMSSQL_TEST_PYTHON_HOME"] = sys.base_prefix

    for expression, expected in (
        ("import sys; print(sys.executable)", sys.executable),
        ("import sys; print(sys.base_prefix)", sys.base_prefix),
    ):
        completed = subprocess.run(
            [str(fake_uv), "run", "python", "-c", expression],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout == f"{expected}\n"

    unsupported = subprocess.run(
        [str(fake_uv), "run", "python", "-c", "print('unsupported')"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert unsupported.returncode == 64

    unrelated = subprocess.run(
        [str(fake_uv), "sync", "--locked"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert unrelated.returncode == 0
    assert unrelated.stdout == ""


def _run_full_runner_sandbox(
    tmp_path: Path,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    sandbox = tmp_path / "repo"
    scripts = sandbox / "scripts" / "sql_auth"
    scripts.mkdir(parents=True)
    runner = scripts / "run_all.sh"
    shutil.copy2(ROOT / "scripts/sql_auth/run_all.sh", runner)
    sequence_path = sandbox / "runner-sequence.tsv"

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
        """#!/usr/bin/env bash
set -eu
printf 'provision\\n' >>"${FASTMSSQL_TEST_SEQUENCE_PATH:?}"
""",
    )
    for name in (
        "run_named_instance_stress.sh",
        "run_result_stream_stress.sh",
        "run_query_many_stress.sh",
    ):
        _write_executable(
            scripts / name,
            "#!/usr/bin/env bash\nexit 0\n",
        )

    fake_bin = sandbox / "fake-bin"
    fake_bin.mkdir()
    _write_fake_uv(fake_bin / "uv")
    _write_executable(
        fake_bin / "cargo",
        "#!/usr/bin/env bash\nexit 0\n",
    )
    _write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env bash
set -eu
printf 'docker' >>"${FASTMSSQL_TEST_SEQUENCE_PATH:?}"
printf '\\t%s' "$@" >>"${FASTMSSQL_TEST_SEQUENCE_PATH:?}"
printf '\\n' >>"${FASTMSSQL_TEST_SEQUENCE_PATH:?}"
""",
    )

    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment.get('PATH', '')}"
    environment["FASTMSSQL_TEST_PYTHON_EXECUTABLE"] = sys.executable
    environment["FASTMSSQL_TEST_PYTHON_HOME"] = sys.base_prefix
    environment["FASTMSSQL_TEST_SEQUENCE_PATH"] = str(sequence_path)
    completed = subprocess.run(
        [str(runner)],
        cwd=sandbox,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    return completed, sandbox, sequence_path


def test_full_runner_uses_original_local_regression_display_name(
    tmp_path: Path,
) -> None:
    completed, sandbox, _ = _run_full_runner_sandbox(tmp_path)

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


def test_full_runner_recreates_sqlserver_before_provision(
    tmp_path: Path,
) -> None:
    completed, sandbox, sequence_path = _run_full_runner_sandbox(tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert sequence_path.read_text(encoding="utf-8").splitlines() == [
        "\t".join(
            (
                "docker",
                "compose",
                "--env-file",
                str(sandbox / ".env.sql-auth.local"),
                "-f",
                "docker-compose.sql-auth.yml",
                "up",
                "-d",
                "--force-recreate",
                "sqlserver",
            )
        ),
        "provision",
    ]


def test_report_generator_preserves_not_run_zero_values_and_redacts(
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
    source_sha = _git_head()
    (artifact_dir / "result-stream-load-results.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_sha": source_sha,
                "cases": {
                    "RESULT-029": {
                        "outcome": "passed",
                        "nodeid": ("external::result_stream_stress[1000:64]"),
                        "duration_seconds": 2.0,
                        "message": "metrics=result-stream-stress-metrics.json",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "result-stream-stress-metrics.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_sha": source_sha,
                "status": "passed",
                "configuration": {
                    "percentile_method": "nearest_rank",
                    "redaction_probe": secret,
                },
                "profiles": [
                    {
                        "status": "passed",
                        "operations": 1_000,
                        "concurrency": 64,
                        "operations_per_second": 500.0,
                        "redaction_probe": secret,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "query-many-stress.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_sha": source_sha,
                "status": "passed",
                "profiles": [
                    {
                        "status": "passed",
                        "operations": 1_000,
                        "source": "sync",
                        "ordered": True,
                        "requested_concurrency": 10,
                        "pool_max": 10,
                        "maximum_active_queries": 10,
                        "maximum_sql_sessions": 10,
                        "maximum_accepted_window": 10,
                        "rss_growth_bytes": 1_024,
                        "maximum_event_loop_gap_seconds": 0.01,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "named-instance-stress.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_sha": source_sha,
                "status": "passed",
                "profile": {
                    "operations": 1_000,
                    "sessions_after_disconnect": 0,
                    "transactions_after_disconnect": 0,
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
                ROOT / "docs/superpowers/specs/"
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
    assert sum(line.startswith("| `") for line in matrix.splitlines()) == 442
    assert "| `ENV-001` | PASS |" in matrix
    assert "| `AUTH-001` | FAIL |" in matrix
    assert "| `CONN-001` | ERROR |" in matrix
    assert "| `POOL-001` | NOT RUN |" in matrix
    assert "| `RESULT-029` | PASS |" in matrix
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
    assert "Result-stream stress metrics" in report
    assert "nearest_rank" in report
    assert "| 1 | passed | 1000 | 64 |" in report
    assert "Query-many stress metrics" in report
    assert "| 1 | passed | 1000 | sync | yes | 10 | 10 | 10 | 10 | 10 |" in report
    assert "Named-instance stress metrics" in report
    assert "- Sessions after disconnect: `0`" in report
    assert "- Transactions after disconnect: `0`" in report


def test_stale_result_stream_evidence_cannot_be_reported_as_pass(
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
    stale_sha = "0" * 40
    (artifact_dir / "result-stream-load-results.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_sha": stale_sha,
                "cases": {
                    "RESULT-029": {
                        "outcome": "passed",
                        "nodeid": "external::stale-result-stream",
                        "duration_seconds": 1.0,
                        "message": "must not be accepted",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "result-stream-stress-metrics.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_sha": stale_sha,
                "status": "passed",
                "profiles": [
                    {
                        "status": "passed",
                        "operations": 1_000,
                        "concurrency": 64,
                    }
                ],
            }
        ),
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
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    matrix = matrix_output.read_text(encoding="utf-8")
    report = report_output.read_text(encoding="utf-8")
    assert "| `RESULT-029` | NOT RUN |" in matrix
    assert "external::stale-result-stream" not in matrix
    assert "Evidence status: `STALE`" in report
    assert "| none | STALE | 0 | 0 | |" in report


def test_fresh_failed_result_stream_evidence_is_redacted(
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
    secret = "ResultStreamSecret_MustNotLeak_2026!"
    source_sha = _git_head()
    (artifact_dir / "result-stream-load-results.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_sha": source_sha,
                "cases": {
                    "RESULT-029": {
                        "outcome": "failed",
                        "nodeid": (
                            "external::result_stream_stress[1000:64]"
                        ),
                        "duration_seconds": 1.5,
                        "message": f"password={secret}",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "result-stream-stress-metrics.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_sha": source_sha,
                "status": "failed",
                "profiles": [
                    {
                        "status": "failed",
                        "operations": 1_000,
                        "concurrency": 64,
                        "failure_probe": secret,
                    }
                ],
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
    assert "| `RESULT-029` | FAIL |" in matrix
    assert "Evidence status: `failed`" in report
    assert "<redacted>" in matrix
    assert "<redacted>" in report
    assert secret not in matrix
    assert secret not in report


def test_result_stream_artifact_validator_accepts_exact_required_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_sha = _git_head()
    digest = hashlib.sha256()
    for operation_id in range(1_000):
        digest.update(str(operation_id).encode("ascii"))
        digest.update(b"\n")
    wall_duration = 2.0
    metrics = {
        "schema_version": 1,
        "source_sha": source_sha,
        "status": "passed",
        "configuration": {
            "profiles": [{"operations": 1_000, "concurrency": 64}],
            "pool_size": 8,
            "buffer_size": 8,
            "rss_growth_limit_bytes": 134_217_728,
            "queue_maxsize_factor": 2,
            "percentile_method": "nearest_rank",
            "worker_model": "long_lived",
        },
        "profiles": [
            {
                "status": "passed",
                "operations": 1_000,
                "concurrency": 64,
                "application_name": (
                    "fastmssql_result_stress_contract"
                ),
                "worker_count": 64,
                "queue_maxsize": 128,
                "total": 1_000,
                "succeeded": 1_000,
                "failed": 0,
                "timed_out": 0,
                "completed_id_count": 1_000,
                "completed_id_min": 0,
                "completed_id_max": 999,
                "completed_id_sum": 499_500,
                "completed_ids_sha256": digest.hexdigest(),
                "missing_ids": [],
                "duplicate_ids": [],
                "failure_types": {},
                "violations": [],
                "wall_duration_seconds": wall_duration,
                "operations_per_second": 500.0,
                "admitted_driver_latency_ns": {
                    "p50_ns": 10,
                    "p95_ns": 20,
                    "p99_ns": 30,
                    "max_ns": 40,
                },
                "scheduled_end_to_end_latency_ns": {
                    "p50_ns": 20,
                    "p95_ns": 30,
                    "p99_ns": 40,
                    "max_ns": 50,
                },
                "pool": {
                    "deltas": {
                        "get_started": 1_000,
                        "get_direct": 8,
                        "get_waited": 992,
                        "get_timed_out": 0,
                        "get_wait_time_seconds": 1.0,
                    },
                    "peak_pending_gets": 56,
                    "peak_active_connections": 8,
                    "final_active_connections": 0,
                },
                "unique_sql_spids": list(range(101, 109)),
                "unique_sql_spid_count": 8,
                "max_concurrent_sql_spids": 8,
                "rss": {
                    "baseline_bytes": 100_000_000,
                    "peak_bytes": 110_000_000,
                    "final_bytes": 105_000_000,
                    "growth_bytes": 10_000_000,
                    "limit_bytes": 134_217_728,
                },
                "python_process_cpu_seconds": 1.5,
                "sql_session_cpu_time_ms_delta": 25,
                "event_loop_ticker": {
                    "count": 10,
                    "max_scheduling_gap_seconds": 0.01,
                },
                "sampler_iterations": 5,
                "post_load_smoke": True,
            }
        ],
    }
    results = {
        "schema_version": 1,
        "source_sha": source_sha,
        "cases": {
            "RESULT-029": {
                "outcome": "passed",
                "nodeid": (
                    "external::result_stream_stress[1000:64]"
                ),
                "duration_seconds": wall_duration,
                "message": "metrics=result-stream-stress-metrics.json",
            }
        },
    }
    metrics_path = tmp_path / "metrics.json"
    results_path = tmp_path / "results.json"
    metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
    results_path.write_text(json.dumps(results), encoding="utf-8")
    monkeypatch.setenv(
        "FASTMSSQL_RESULT_STREAM_STRESS_METRICS_PATH",
        str(metrics_path),
    )
    monkeypatch.setenv(
        "FASTMSSQL_RESULT_STREAM_STRESS_RESULTS_PATH",
        str(results_path),
    )

    result_stream_tests = importlib.import_module(
        "sql_auth_strict.test_resultsets_streaming"
    )
    validator = (
        result_stream_tests
        .test_required_result_stream_stress_artifact_is_fresh_and_complete
    )
    validator()


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
                ROOT / "docs/superpowers/specs/"
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
    assert "missing evidence for 442 case(s)" in completed.stderr
    assert matrix_output.is_file()
    assert report_output.is_file()
    assert "| NOT RUN | 442 |" in report_output.read_text(encoding="utf-8")


def test_config_redacts_password(monkeypatch) -> None:
    monkeypatch.setenv(
        "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD", "NeverPrintMe_2026!"
    )
    config = SqlAuthConfig.from_env(require_all=False)
    assert "NeverPrintMe_2026!" not in repr(config)


def test_approved_spec_contains_442_unique_case_ids() -> None:
    spec = ROOT / (
        "docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md"
    )
    ids = spec_case_ids(spec)
    assert len(ids) == 442


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


def test_result_stream_stress_harness_is_bounded_and_required(
    tmp_path: Path,
) -> None:
    python_runner = ROOT / "scripts/sql_auth/result_stream_stress.py"
    shell_runner = ROOT / "scripts/sql_auth/run_result_stream_stress.sh"
    full_runner = ROOT / "scripts/sql_auth/run_all.sh"
    assert python_runner.is_file()
    assert shell_runner.is_file()
    assert shell_runner.stat().st_mode & 0o111

    source = python_runner.read_text(encoding="utf-8")
    for token in (
        "MAX_OPERATIONS = 99_999",
        "MAX_CONCURRENCY = 500",
        "MAX_BUFFER_SIZE = 1_024",
        'DEFAULT_PROFILE = "1000:64"',
        "DEFAULT_POOL_SIZE = 8",
        "DEFAULT_BUFFER_SIZE = 8",
        "DEFAULT_RSS_GROWTH_LIMIT_BYTES = 134_217_728",
        "asyncio.Queue(",
        "maxsize=2 * profile.concurrency",
        "for _ in range(profile.concurrency):",
        "group.create_task(worker())",
        "asyncio.Semaphore(profile.concurrency)",
        "item.scheduled_ns = time.perf_counter_ns()",
        "admitted_ns = time.perf_counter_ns()",
        "nearest_rank",
        "sql_session_cpu_time_ms_delta",
        "python_process_cpu_seconds",
        "os.replace(temporary, path)",
        '"worker_model": "long_lived"',
    ):
        assert token in source
    assert "create_task(execute_item" not in source
    assert "operations must be between 1 and 99,999" in source
    assert "concurrency must be between 1 and 500" in source
    assert "--buffer-size must be between 1 and 1,024" in source

    sandbox_root = tmp_path / "repo"
    sandbox_runner = sandbox_root / "scripts/sql_auth/run_result_stream_stress.sh"
    sandbox_runner.parent.mkdir(parents=True)
    shutil.copy2(shell_runner, sandbox_runner)
    (sandbox_root / ".env.sql-auth.local").write_text("", encoding="utf-8")
    fake_python = sandbox_root / ".venv/bin/python"
    fake_python.parent.mkdir(parents=True)
    _write_executable(
        fake_python,
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "${CAPTURE_PATH}"\n',
    )
    captured_arguments = tmp_path / "result-stream-stress-arguments.txt"
    runner_env = os.environ.copy()
    for name in tuple(runner_env):
        if name.startswith("FASTMSSQL_RESULT_STREAM_STRESS_"):
            del runner_env[name]
    runner_env.update(
        {
            "CAPTURE_PATH": str(captured_arguments),
            "FASTMSSQL_RESULT_STREAM_STRESS_PROFILES": "3:2",
            "FASTMSSQL_RESULT_STREAM_STRESS_POOL_SIZE": "17",
            "FASTMSSQL_RESULT_STREAM_STRESS_BUFFER_SIZE": "23",
        }
    )
    completed = subprocess.run(
        [str(sandbox_runner)],
        cwd=sandbox_root,
        env=runner_env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    arguments = captured_arguments.read_text(encoding="utf-8").splitlines()
    assert arguments[arguments.index("--pool-size") + 1] == "17"
    assert arguments[arguments.index("--buffer-size") + 1] == "23"

    shell_source = shell_runner.read_text(encoding="utf-8")
    for token in (
        "FASTMSSQL_RESULT_STREAM_STRESS_METRICS_PATH",
        "FASTMSSQL_RESULT_STREAM_STRESS_RESULTS_PATH",
        "FASTMSSQL_RESULT_STREAM_STRESS_PROFILES",
        "FASTMSSQL_RESULT_STREAM_STRESS_POOL_SIZE",
        "FASTMSSQL_RESULT_STREAM_STRESS_BUFFER_SIZE",
        "FASTMSSQL_RESULT_STREAM_STRESS_RSS_GROWTH_LIMIT_BYTES",
        "result-stream-stress-metrics.json",
        "result-stream-stress-metrics-extended.json",
        "result-stream-load-results.json",
        "--profiles",
        'readonly pool_size="${FASTMSSQL_RESULT_STREAM_STRESS_POOL_SIZE:-8}"',
        'readonly buffer_size="${FASTMSSQL_RESULT_STREAM_STRESS_BUFFER_SIZE:-8}"',
        '--pool-size "${pool_size}"',
        '--buffer-size "${buffer_size}"',
        "--rss-growth-limit-bytes",
        ".env.sql-auth.local",
    ):
        assert token in shell_source
    assert "--pool-size 8" not in shell_source
    assert "--buffer-size 8" not in shell_source

    full_source = full_runner.read_text(encoding="utf-8")
    assert "record result-stream-load " in full_source
    assert full_source.index("record provision ") < full_source.index(
        "record result-stream-load "
    )
    assert full_source.index("record result-stream-load ") < full_source.index(
        "record strict "
    )
    assert "tests/sql_auth_strict/test_resultsets_streaming.py" in full_source

    completed = subprocess.run(
        [sys.executable, str(python_runner), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    for option in (
        "--profiles",
        "--pool-size",
        "--buffer-size",
        "--rss-growth-limit-bytes",
        "--metrics-output",
        "--results-output",
    ):
        assert option in completed.stdout

    namespace = runpy.run_path(str(python_runner))
    nearest_rank = namespace["nearest_rank"]
    assert nearest_rank([50, 10, 40, 20, 30], 50) == 30
    assert nearest_rank([50, 10, 40, 20, 30], 95) == 50
    profiles = namespace["parse_profiles"]("1_000:64,99_999:200")
    assert [
        (profile.operations, profile.concurrency)
        for profile in profiles
    ] == [(1_000, 64), (99_999, 200)]
    evidence_path = tmp_path / "nested" / "metrics.json"
    namespace["atomic_write"](
        evidence_path,
        {"schema_version": 1, "status": "contract"},
    )
    assert json.loads(evidence_path.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "status": "contract",
    }
    assert list(evidence_path.parent.glob(".*.tmp")) == []


def test_query_many_stress_runner_is_required_and_privacy_safe(
    tmp_path: Path,
) -> None:
    python_runner = ROOT / "scripts/sql_auth/query_many_stress.py"
    shell_runner = ROOT / "scripts/sql_auth/run_query_many_stress.sh"
    full_runner = ROOT / "scripts/sql_auth/run_all.sh"
    report = ROOT / "scripts/sql_auth/generate_report.py"
    assert python_runner.is_file()
    assert shell_runner.is_file()
    assert shell_runner.stat().st_mode & 0o111

    sandbox_root = tmp_path / "repo"
    sandbox_runner = sandbox_root / "scripts/sql_auth/run_query_many_stress.sh"
    sandbox_runner.parent.mkdir(parents=True)
    shutil.copy2(shell_runner, sandbox_runner)
    (sandbox_root / ".env.sql-auth.local").write_text("", encoding="utf-8")
    fake_python = sandbox_root / ".venv/bin/python"
    fake_python.parent.mkdir(parents=True)
    _write_executable(
        fake_python,
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "${CAPTURE_PATH}"\n',
    )
    captured_arguments = tmp_path / "query-many-stress-arguments.txt"
    runner_env = os.environ.copy()
    for name in tuple(runner_env):
        if name.startswith("FASTMSSQL_QUERY_MANY_"):
            del runner_env[name]
    runner_env.update(
        {
            "CAPTURE_PATH": str(captured_arguments),
            "FASTMSSQL_QUERY_MANY_PROFILES": (
                "3:async:false:5:2"
            ),
            "FASTMSSQL_QUERY_MANY_ALLOW_EXTENDED": "1",
        }
    )
    completed = subprocess.run(
        [str(sandbox_runner)],
        cwd=sandbox_root,
        env=runner_env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    arguments = captured_arguments.read_text(encoding="utf-8").splitlines()
    assert arguments[arguments.index("--profiles") + 1] == (
        "3:async:false:5:2"
    )
    assert "--allow-extended" in arguments
    output = Path(arguments[arguments.index("--metrics-output") + 1])
    assert output == (
        sandbox_root / ".artifacts/sql-auth/query-many-stress.json"
    )

    shell_source = shell_runner.read_text(encoding="utf-8")
    for token in (
        "FASTMSSQL_QUERY_MANY_PROFILES",
        "FASTMSSQL_QUERY_MANY_ALLOW_EXTENDED",
        "FASTMSSQL_QUERY_MANY_METRICS_PATH",
        "FASTMSSQL_QUERY_MANY_RSS_GROWTH_LIMIT_BYTES",
        "FASTMSSQL_QUERY_MANY_EVENT_LOOP_GAP_LIMIT_SECONDS",
        "FASTMSSQL_QUERY_MANY_OPERATION_TIMEOUT_SECONDS",
        "query-many-stress.json",
        "--profiles",
        "--metrics-output",
        ".env.sql-auth.local",
    ):
        assert token in shell_source
    assert "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD" not in completed.stdout
    assert "FASTMSSQL_SQL_AUTH_SA_PASSWORD" not in completed.stdout

    full_source = full_runner.read_text(encoding="utf-8")
    assert "record query-many-load " in full_source
    assert full_source.index("record provision ") < full_source.index(
        "record query-many-load "
    )
    assert full_source.index("record query-many-load ") < full_source.index(
        "record strict "
    )
    assert "tests/sql_auth_strict/test_query_many_strict.py" in full_source

    report_source = report.read_text(encoding="utf-8")
    assert "query-many-stress.json" in report_source
    assert "Query-many stress metrics" in report_source


def test_named_instance_stress_harness_is_bounded_opt_in_and_required(
    tmp_path: Path,
    monkeypatch,
) -> None:
    python_runner = ROOT / "scripts/sql_auth/named_instance_stress.py"
    shell_runner = ROOT / "scripts/sql_auth/run_named_instance_stress.sh"
    full_runner = ROOT / "scripts/sql_auth/run_all.sh"
    report = ROOT / "scripts/sql_auth/generate_report.py"
    assert python_runner.is_file()
    assert shell_runner.is_file()
    assert shell_runner.stat().st_mode & 0o111

    source = python_runner.read_text(encoding="utf-8")
    for token in (
        "MAX_OPERATIONS = 99_999",
        "EXTENDED_OPERATIONS = 99_999",
        "DEFAULT_OPERATIONS = 1_000",
        "DEFAULT_WORKERS = 32",
        "DEFAULT_POOL_SIZE = 8",
        "asyncio.Queue(",
        "maxsize=max(1, worker_count * 2)",
        "asyncio.TaskGroup()",
        "for _ in range(worker_count):",
        "instance_name=INSTANCE_NAME",
        '"SELECT @P1 AS operation_id, @@SPID AS spid"',
        "browser_requests",
        "successful_physical_connections",
        "maximum_pool_connections",
        "maximum_sql_sessions",
        "rss_growth_bytes",
        "maximum_event_loop_gap_seconds",
        "sessions_after_disconnect",
        "transactions_after_disconnect",
        "os.replace(temporary, path)",
    ):
        assert token in source
    assert "create_task(connection.query" not in source
    assert "operations must be between 1 and 99,999" in source
    assert "99,999 operations require --allow-extended" in source

    sandbox_root = tmp_path / "repo"
    sandbox_runner = sandbox_root / "scripts/sql_auth/run_named_instance_stress.sh"
    sandbox_runner.parent.mkdir(parents=True)
    shutil.copy2(shell_runner, sandbox_runner)
    (sandbox_root / ".env.sql-auth.local").write_text("", encoding="utf-8")
    fake_python = sandbox_root / ".venv/bin/python"
    fake_python.parent.mkdir(parents=True)
    _write_executable(
        fake_python,
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "${CAPTURE_PATH}"\n',
    )
    fake_bin = sandbox_root / "fake-bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "git",
        "#!/usr/bin/env bash\nprintf '%040d\\n' 0\n",
    )
    captured_arguments = tmp_path / "named-instance-stress-arguments.txt"
    runner_env = os.environ.copy()
    for name in tuple(runner_env):
        if name.startswith("FASTMSSQL_NAMED_INSTANCE_"):
            del runner_env[name]
    runner_env.update(
        {
            "PATH": (f"{fake_bin}{os.pathsep}{runner_env.get('PATH', '')}"),
            "CAPTURE_PATH": str(captured_arguments),
            "FASTMSSQL_NAMED_INSTANCE_OPERATIONS": "3",
            "FASTMSSQL_NAMED_INSTANCE_WORKERS": "2",
            "FASTMSSQL_NAMED_INSTANCE_POOL_SIZE": "1",
            "FASTMSSQL_NAMED_INSTANCE_ALLOW_EXTENDED": "1",
        }
    )
    completed = subprocess.run(
        [str(sandbox_runner)],
        cwd=sandbox_root,
        env=runner_env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    arguments = captured_arguments.read_text(encoding="utf-8").splitlines()
    assert arguments[arguments.index("--operations") + 1] == "3"
    assert arguments[arguments.index("--workers") + 1] == "2"
    assert arguments[arguments.index("--pool-size") + 1] == "1"
    assert "--allow-extended" in arguments
    assert arguments[arguments.index("--source-sha") + 1] == "0" * 40
    metrics_output = Path(arguments[arguments.index("--metrics-output") + 1])
    assert metrics_output == (
        sandbox_root / ".artifacts/sql-auth/named-instance-stress-extended.json"
    )
    assert "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD" not in completed.stdout
    assert "FASTMSSQL_SQL_AUTH_SA_PASSWORD" not in completed.stdout

    rejected = subprocess.run(
        [
            sys.executable,
            str(python_runner),
            "--operations",
            "99999",
            "--metrics-output",
            str(tmp_path / "must-not-exist.json"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    assert "99,999 operations require --allow-extended" in rejected.stderr

    full_source = full_runner.read_text(encoding="utf-8")
    assert "record named-instance-load " in full_source
    assert full_source.index("record provision ") < full_source.index(
        "record named-instance-load "
    )
    assert full_source.index("record named-instance-load ") < (
        full_source.index("record strict ")
    )
    assert "tests/sql_auth_strict/test_named_instance_strict.py" in full_source

    report_source = report.read_text(encoding="utf-8")
    assert "named-instance-stress.json" in report_source
    assert "Named-instance stress metrics" in report_source

    strict_path = ROOT / "tests/sql_auth_strict/test_named_instance_strict.py"
    strict_tree = ast.parse(
        strict_path.read_text(encoding="utf-8"),
        filename=str(strict_path),
    )
    load_test = next(
        node
        for node in strict_tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        == "test_required_named_instance_stress_artifact_is_fresh_and_bounded"
    )
    assert {ast.unparse(item) for item in load_test.decorator_list} == {
        "case('NINST-018')",
        "pytest.mark.load",
    }
    assert tuple(argument.arg for argument in load_test.args.args) == (
        "record_load_metric",
    )

    monkeypatch.setattr(strict_conftest, "_LOAD_METRICS", {})
    recorder = strict_conftest.record_load_metric.__wrapped__()
    recorder("NINST-018", contract_probe=True)
    with pytest.raises(ValueError):
        recorder("NINST-017", contract_probe=True)


def test_query_many_case_ownership_and_load_routing_are_exact(
    monkeypatch,
) -> None:
    strict_path = ROOT / "tests/sql_auth_strict/test_query_many_strict.py"
    strict_tree = ast.parse(
        strict_path.read_text(encoding="utf-8"),
        filename=str(strict_path),
    )
    expected = {
        "test_query_many_surface_validation_and_empty": (
            ("QMANY-001", "QMANY-002", "QMANY-003"),
            ("sql_auth_config", "sa_connection", "unique_sql_name"),
        ),
        "test_query_many_parameter_sources_and_typed_results": (
            ("QMANY-004",),
            ("owner_connection", "unique_sql_name", "cleanup_registry"),
        ),
        "test_query_many_pool_window_and_ordering": (
            ("QMANY-005", "QMANY-006", "QMANY-007"),
            (
                "sql_auth_config",
                "sa_connection",
                "transaction_factory",
                "unique_sql_name",
                "cleanup_registry",
            ),
        ),
        "test_query_many_failure_and_consumer_cleanup": (
            ("QMANY-008", "QMANY-009"),
            (
                "sql_auth_config",
                "sa_connection",
                "transaction_factory",
                "unique_sql_name",
                "cleanup_registry",
            ),
        ),
        "test_query_many_faults_and_lifecycle": (
            ("QMANY-010", "QMANY-011"),
            (
                "sql_auth_config",
                "sa_connection",
                "transaction_factory",
                "unique_sql_name",
                "cleanup_registry",
            ),
        ),
        "test_query_many_metrics_and_security_retirement": (
            ("QMANY-012",),
            ("sql_auth_config", "sa_connection", "unique_sql_name"),
        ),
    }
    actual: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for node in strict_tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in expected:
            continue
        case_ids = tuple(
            argument.value
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "case"
            for argument in decorator.args
            if isinstance(argument, ast.Constant)
            and isinstance(argument.value, str)
        )
        fixtures = tuple(argument.arg for argument in node.args.args)
        actual[node.name] = (case_ids, fixtures)
    assert actual == expected

    load_path = ROOT / "tests/sql_auth_strict/test_resilience_load.py"
    load_tree = ast.parse(
        load_path.read_text(encoding="utf-8"),
        filename=str(load_path),
    )
    load_function = next(
        node
        for node in load_tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "test_query_many_thousand_operation_required_profile"
    )
    assert tuple(argument.arg for argument in load_function.args.args) == (
        "sql_auth_config",
        "record_load_metric",
    )
    assert {ast.unparse(item) for item in load_function.decorator_list} == {
        "case('QMANY-013')",
        "pytest.mark.load",
        "pytest.mark.asyncio",
    }

    monkeypatch.setattr(strict_conftest, "_LOAD_METRICS", {})
    recorder = strict_conftest.record_load_metric.__wrapped__()
    recorder("QMANY-013", contract_probe=True)
    for functional_id in ("QMANY-001", "QMANY-012"):
        with pytest.raises(ValueError):
            recorder(functional_id, contract_probe=True)


def test_hosted_wheel_gate_runs_all_query_many_offline_contracts() -> None:
    workflow = (
        ROOT / ".github/workflows/rust-unit-tests.yml"
    ).read_text(encoding="utf-8")
    start = workflow.index(
        "- name: Verify installed Python configuration contracts"
    )
    wheel_gate = workflow[start:]
    for test_path in (
        "tests/test_query_many_contract.py",
        "tests/test_query_many_coordinator.py",
        "tests/test_query_many_stress_contract.py",
    ):
        assert test_path in wheel_gate


def test_result_stream_isolated_wheel_gate_installs_test_dependencies() -> None:
    plan = ROOT / (
        "docs/superpowers/plans/"
        "2026-07-27-fastmssql-resultsets-streaming.md"
    )
    source = plan.read_text(encoding="utf-8")
    start = source.index("- [ ] **Step 5: Build and test an isolated installed wheel**")
    end = source.index("- [ ] **Step 6: Run privacy, secret and artifact checks**")
    wheel_gate = source[start:end]

    for requirement in (
        "python-dotenv==1.2.2",
        "pytest-timeout==2.4.0",
        "pytest==9.1.1",
        "pytest-asyncio==1.4.0",
        "psutil==7.2.2",
    ):
        assert requirement in wheel_gate, (
            f"isolated wheel SQL-auth gate is missing {requirement}"
        )
    assert "env -u PYTHONPATH" in wheel_gate


def test_hosted_installed_wheel_gate_installs_async_test_plugin() -> None:
    workflow = (
        ROOT / ".github/workflows/rust-unit-tests.yml"
    ).read_text(encoding="utf-8")
    start = workflow.index("- name: Install extension contract environment")
    end = workflow.index(
        "- name: Verify installed Python configuration contracts"
    )
    install_gate = workflow[start:end]

    assert "pytest-asyncio==1.4.0" in install_gate, (
        "hosted installed-wheel gate executes async result-stream contracts "
        "and must install the pinned pytest plugin that runs them"
    )


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
                if (
                    case_id.startswith("LOAD-")
                    or case_id in {"OPMET-011", "PARAM-033", "QMANY-013"}
                )
                else ""
            )
            if expected_marker and expected_marker not in decorators:
                incorrectly_routed[case_id] = node.name

    assert incorrectly_routed == {}


def test_load_metric_recorder_accepts_every_case_that_records_metrics(
    monkeypatch,
) -> None:
    path = ROOT / "tests/sql_auth_strict/test_resilience_load.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    metric_case_ids = {
        argument.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "record_load_metric"
        for argument in node.args[:1]
        if isinstance(argument, ast.Constant)
        and isinstance(argument.value, str)
    }
    monkeypatch.setattr(strict_conftest, "_LOAD_METRICS", {})
    recorder = strict_conftest.record_load_metric.__wrapped__()
    rejected: dict[str, str] = {}

    for case_id in sorted(metric_case_ids):
        try:
            recorder(case_id, contract_probe=True)
        except ValueError as error:
            rejected[case_id] = str(error)

    assert metric_case_ids
    assert rejected == {}


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


def test_production_framework_evidence_requires_exact_current_sha(
    tmp_path,
    monkeypatch,
) -> None:
    evidence_path = tmp_path / "production-framework-metrics.json"
    payload = {
        "schema_version": 1,
        "candidate": {"git_sha": _git_head()},
        "harness": {"git_sha": _git_head()},
        "overall": "PASS",
        "violations": [],
    }
    evidence_path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "FASTMSSQL_PRODUCTION_FRAMEWORK_METRICS_PATH",
        str(evidence_path),
    )

    assert _production_framework_evidence.__wrapped__() == payload

    payload["harness"]["git_sha"] = "0" * 40
    evidence_path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError):
        _production_framework_evidence.__wrapped__()

    payload["harness"]["git_sha"] = _git_head()
    payload["candidate"]["git_sha"] = "0" * 40
    evidence_path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError):
        _production_framework_evidence.__wrapped__()


def test_production_framework_runner_precedes_evidence_validation() -> None:
    source = (ROOT / "scripts/sql_auth/run_all.sh").read_text(encoding="utf-8")
    process_lane = "record production-framework-process "
    evidence_lane = "record production-framework-evidence "
    assert process_lane in source
    assert evidence_lane in source
    assert source.index("record provision ") < source.index(process_lane)
    assert source.index(process_lane) < source.index(evidence_lane)
    assert source.index(evidence_lane) < source.index("record report ")
    assert "tests/sql_auth_strict/test_production_framework_matrix.py" in source


def test_production_framework_report_receives_exact_metrics_path() -> None:
    runner = (ROOT / "scripts/sql_auth/run_all.sh").read_text(encoding="utf-8")
    report = (ROOT / "scripts/sql_auth/generate_report.py").read_text(encoding="utf-8")
    expected = "${artifact_dir}/production-framework-metrics.json"
    assert expected in runner
    assert "--production-framework-metrics" in runner
    assert "production-framework-metrics.json" in report
    assert "Production framework process matrix" in report


def test_production_framework_candidate_branches_trigger_hosted_gates() -> None:
    expected = {
        "test/production-framework-matrix",
        "feat/production-framework-matrix",
        "verify/production-framework-matrix",
        "docs/production-framework-matrix-status",
    }
    for path in (
        ROOT / ".github/workflows/production-framework-matrix.yml",
        ROOT / ".github/workflows/rust-unit-tests.yml",
        ROOT / ".github/workflows/dependency-security.yml",
    ):
        assert path.is_file(), path
        source = path.read_text(encoding="utf-8")
        assert expected <= _workflow_push_branches(source), path.name


def test_production_framework_hosted_claims_are_platform_exact() -> None:
    workflow_path = ROOT / ".github/workflows/production-framework-matrix.yml"
    assert workflow_path.is_file(), workflow_path
    workflow = workflow_path.read_text(encoding="utf-8")
    for token in (
        "ubuntu-latest",
        "macos-latest",
        "windows-2022",
        "hosted_sql_auth",
        "Microsoft SQL Server 2022 Docker",
        "Microsoft SQL Server 2022 Express",
        "Gunicorn is not supported on Windows",
        "uvloop is not supported on Windows",
    ):
        assert token in workflow
    assert "macos-hosted-sql-auth: true" not in workflow
    assert "continue-on-error" not in workflow
    assert "pytest.skip" not in workflow
    assert "FastMssql/python" not in workflow
    assert "PYTHONPATH=${{ github.workspace }}/python" not in workflow
