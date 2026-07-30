from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

import pytest

from sql_auth_strict.cases import case


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVIDENCE = ROOT / ".artifacts/sql-auth/production-framework-metrics.json"
WORKER_COUNTS = {1, 2, 4, 8}
LOCKED_VERSIONS = {
    "asgiref": "3.12.1",
    "fastapi": "0.139.2",
    "flask": "3.1.3",
    "gunicorn": "26.0.0",
    "httpx": "0.28.1",
    "uvicorn": "0.51.0",
    "uvicorn-worker": "0.4.0",
    "uvloop": "0.22.1",
}
POSIX_ONLY_DEPENDENCIES = {
    "gunicorn",
    "uvicorn-worker",
    "uvloop",
}
POSIX_SHUTDOWN_NA = "POSIX SIGTERM graceful shutdown is not supported on Windows"
GUNICORN_NA = "Gunicorn is not supported on Windows"
PROFILE_FAMILIES = {
    "fastapi-uvicorn-asyncio",
    "fastapi-uvicorn-uvloop",
    "fastapi-gunicorn-uvicorn-worker",
    "flask-gunicorn-sync",
    "flask-gunicorn-gthread",
    "flask-asgi-uvicorn-asyncio",
    "flask-asgi-uvicorn-uvloop",
}
APPROVED_LABELS = {
    "native ASGI: concurrent requests on persistent event loop",
    "Flask WSGI: async view, occupied WSGI worker/thread",
    (
        "Flask via WsgiToAsgi: persistent ASGI loop, "
        "thread-sensitive WSGI serialization per process"
    ),
}

pytestmark = [
    pytest.mark.sql_auth_strict,
    pytest.mark.integration,
    pytest.mark.framework,
]


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


@pytest.fixture(scope="module")
def production_framework_evidence() -> dict[str, object]:
    configured = os.environ.get("FASTMSSQL_PRODUCTION_FRAMEWORK_METRICS_PATH")
    path = Path(configured) if configured else DEFAULT_EVIDENCE
    assert path.is_file(), (
        f"required exact-SHA production framework evidence is missing: {path}"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["candidate"]["git_sha"] == _git_head()
    assert payload["overall"] == "PASS"
    assert payload["violations"] == []
    return payload


def _profiles(evidence: dict[str, object]) -> list[dict[str, object]]:
    profiles = evidence["profiles"]
    assert isinstance(profiles, list)
    return profiles


def _applicable(
    evidence: dict[str, object],
    *,
    family: str | None = None,
) -> list[dict[str, object]]:
    return [
        profile
        for profile in _profiles(evidence)
        if profile["applicable"] and (family is None or profile["family"] == family)
    ]


def _database_profiles(
    evidence: dict[str, object],
) -> list[dict[str, object]]:
    return [
        profile
        for profile in _applicable(evidence)
        if profile["database_mode"] == "sql_auth"
    ]


def _scenario(
    evidence: dict[str, object],
    name: str,
) -> dict[str, object]:
    scenario = evidence["scenarios"][name]
    assert scenario["status"] == "PASS"
    return scenario


def _assert_not_applicable(
    scenario: dict[str, object],
    reason: str,
) -> None:
    assert scenario["status"] == "N/A"
    assert scenario["not_applicable_reason"] == reason


@case("FRAME-027")
def test_production_dependencies_are_locked_and_platform_scoped(
    production_framework_evidence,
) -> None:
    dependencies = production_framework_evidence["dependencies"]
    platform = production_framework_evidence["platform"]["system"]
    assert dependencies["runtime"] == []
    assert dependencies["locked_versions"] == LOCKED_VERSIONS
    assert dependencies["posix_only"] == [
        "gunicorn",
        "uvicorn-worker",
        "uvloop",
    ]
    expected_installed = {
        name: version
        for name, version in LOCKED_VERSIONS.items()
        if platform != "Windows" or name not in POSIX_ONLY_DEPENDENCIES
    }
    assert dependencies["installed_versions"] == expected_installed


@case("FRAME-028")
def test_every_worker_imports_the_exact_isolated_wheel(
    production_framework_evidence,
) -> None:
    candidate = production_framework_evidence["candidate"]
    wheel = candidate["wheel"]
    assert re.fullmatch(r"[0-9a-f]{64}", wheel["sha256"])
    assert wheel["filename"].startswith("fastmssql-0.7.7-")
    assert "site-packages" in wheel["import_path"]
    assert "FastMssql/python" not in wheel["import_path"]
    worker_paths = {
        worker["fastmssql_import_path"]
        for profile in _applicable(production_framework_evidence)
        for worker in profile["worker_records"]
    }
    assert worker_paths
    assert all("site-packages" in path for path in worker_paths)
    assert all("FastMssql/python" not in path for path in worker_paths)


@case("FRAME-029")
def test_standalone_uvicorn_asyncio_profiles_are_real(
    production_framework_evidence,
) -> None:
    profiles = [
        profile
        for profile in _applicable(production_framework_evidence)
        if profile["server"] == "uvicorn" and profile["loop"] == "asyncio"
    ]
    assert profiles
    assert {profile["family"] for profile in profiles} == {
        "fastapi-uvicorn-asyncio",
        "flask-asgi-uvicorn-asyncio",
    }
    assert {profile["workers"] for profile in profiles} == WORKER_COUNTS
    assert all(profile["transport"] == "tcp-http-1.1" for profile in profiles)
    assert all(profile["status"] == "PASS" for profile in profiles)


@case("FRAME-030")
def test_uvloop_profiles_are_posix_or_explicitly_not_applicable(
    production_framework_evidence,
) -> None:
    platform = production_framework_evidence["platform"]["system"]
    uvloop = [
        profile
        for profile in _profiles(production_framework_evidence)
        if profile["loop"] == "uvloop"
    ]
    assert uvloop
    assert {profile["family"] for profile in uvloop} == {
        "fastapi-uvicorn-uvloop",
        "flask-asgi-uvicorn-uvloop",
    }
    assert {profile["workers"] for profile in uvloop} == WORKER_COUNTS
    if platform == "Windows":
        assert all(not profile["applicable"] for profile in uvloop)
        assert {profile["not_applicable_reason"] for profile in uvloop} == {
            "uvloop is not supported on Windows"
        }
    else:
        assert all(profile["applicable"] for profile in uvloop)
        assert all(profile["status"] == "PASS" for profile in uvloop)


@case("FRAME-031")
def test_native_fastapi_uvicorn_scales_through_eight_workers(
    production_framework_evidence,
) -> None:
    profiles = _applicable(
        production_framework_evidence,
        family="fastapi-uvicorn-asyncio",
    )
    assert {profile["workers"] for profile in profiles} == WORKER_COUNTS
    assert all(profile["status"] == "PASS" for profile in profiles)


@case("FRAME-032")
def test_gunicorn_uses_current_uvicorn_worker_at_all_counts(
    production_framework_evidence,
) -> None:
    platform = production_framework_evidence["platform"]["system"]
    profiles = [
        profile
        for profile in _profiles(production_framework_evidence)
        if profile["family"] == "fastapi-gunicorn-uvicorn-worker"
    ]
    assert profiles
    assert {profile["workers"] for profile in profiles} == WORKER_COUNTS
    if platform == "Windows":
        assert all(not profile["applicable"] for profile in profiles)
        assert {profile["not_applicable_reason"] for profile in profiles} == {
            "Gunicorn is not supported on Windows"
        }
    else:
        assert {profile["workers"] for profile in profiles} == WORKER_COUNTS
        assert all(profile["status"] == "PASS" for profile in profiles)
        assert all(
            profile["worker_class"] == "uvicorn_worker.UvicornWorker"
            for profile in profiles
        )
        assert all(not profile["preload_app"] for profile in profiles)


@case("FRAME-033")
def test_every_pool_is_worker_local_and_created_after_process_start(
    production_framework_evidence,
) -> None:
    profiles = _database_profiles(production_framework_evidence)
    assert profiles
    for profile in profiles:
        workers = profile["worker_records"]
        assert len(workers) == profile["workers"]
        assert len(profile["ready_pids"]) == len(set(profile["ready_pids"]))
        assert len(profile["shutdown_pids"]) == len(set(profile["shutdown_pids"]))
        assert set(profile["ready_pids"]) == set(profile["shutdown_pids"])
        assert {worker["pid"] for worker in workers} == set(profile["ready_pids"])
        assert all(worker["pool_created_pid"] == worker["pid"] for worker in workers)
        assert all(
            worker["pool_connected_monotonic"] >= worker["process_started_monotonic"]
            for worker in workers
        )
        assert len({worker["pool_identity"] for worker in workers}) == len(workers)


@case("FRAME-034")
def test_connection_budget_holds_across_worker_processes(
    production_framework_evidence,
) -> None:
    budget = production_framework_evidence["configuration"]["global_connection_budget"]
    assert budget > 0
    profiles = _database_profiles(production_framework_evidence)
    assert profiles
    for profile in profiles:
        assert profile["workers"] * profile["pool_max_per_worker"] <= budget
        assert profile["maximum_aggregate_sql_sessions"] <= budget


@case("FRAME-035")
def test_native_asgi_remains_concurrent_and_responsive(
    production_framework_evidence,
) -> None:
    scenario = _scenario(
        production_framework_evidence,
        "native_concurrency",
    )
    assert scenario["values_exact"]
    assert scenario["maximum_simultaneous_sql_requests"] > 1
    assert scenario["concurrent_seconds"] < scenario["sequential_seconds"] * 0.70
    assert scenario["health_seconds"] < scenario["sql_delay_seconds"]
    assert scenario["pool_pending_after"] == 0


@case("FRAME-036")
def test_real_client_disconnect_settles_sql_and_recovers(
    production_framework_evidence,
) -> None:
    scenario = _scenario(
        production_framework_evidence,
        "client_disconnect_cancellation",
    )
    assert scenario["transport"] == "raw-tcp-client-close"
    assert scenario["sql_observed_before_close"]
    assert scenario["sql_requests_after"] == 0
    assert scenario["pool_active_after"] == 0
    assert scenario["pool_pending_after"] == 0
    assert scenario["recovery_value"] == 36


@case("FRAME-037")
def test_graceful_query_shutdown_finishes_without_forced_cleanup(
    production_framework_evidence,
) -> None:
    scenario = production_framework_evidence["scenarios"]["graceful_query_shutdown"]
    if production_framework_evidence["platform"]["system"] == "Windows":
        _assert_not_applicable(scenario, POSIX_SHUTDOWN_NA)
        return
    assert scenario["status"] == "PASS"
    assert scenario["signal"] == "SIGTERM"
    assert scenario["query_observed"]
    assert scenario["response_completed"]
    assert scenario["forced_cleanup"] is False
    assert scenario["sessions_after"] == 0


@case("FRAME-038")
def test_graceful_transaction_shutdown_has_deterministic_outcomes(
    production_framework_evidence,
) -> None:
    scenario = production_framework_evidence["scenarios"][
        "graceful_transaction_shutdown"
    ]
    if production_framework_evidence["platform"]["system"] == "Windows":
        _assert_not_applicable(scenario, POSIX_SHUTDOWN_NA)
        return
    assert scenario["status"] == "PASS"
    assert scenario["commit"]["durable_rows"] == 1
    assert scenario["rollback"]["durable_rows"] == 0
    assert scenario["commit"]["forced_cleanup"] is False
    assert scenario["rollback"]["forced_cleanup"] is False
    assert scenario["transactions_after"] == 0
    assert scenario["sessions_after"] == 0


@case("FRAME-039")
def test_saturated_pool_has_bounded_admission_and_recovers(
    production_framework_evidence,
) -> None:
    scenario = _scenario(
        production_framework_evidence,
        "saturation",
    )
    assert scenario["maximum_pool_connections"] == scenario["pool_max"]
    assert scenario["maximum_admitted_waiters"] <= scenario["queue_max"]
    assert scenario["rejected_503"] > 0
    assert scenario["typed_acquire_timeouts"] > 0
    assert scenario["pool_active_after"] == 0
    assert scenario["pool_pending_after"] == 0
    assert scenario["recovery_value"] == 39


@case("FRAME-040")
def test_result_stream_reaches_real_http_incrementally_and_cleans_up(
    production_framework_evidence,
) -> None:
    scenario = _scenario(
        production_framework_evidence,
        "large_streaming",
    )
    full = scenario["full_consumption"]
    early = scenario["early_close"]
    assert full["transport"] == "chunked-http-1.1"
    assert full["first_data_before_completion"]
    assert full["row_count"] == full["expected_row_count"]
    assert full["digest"] == full["expected_digest"]
    assert full["maximum_buffered_rows"] <= full["buffer_size"]
    assert early["validated_prefix_rows"] > 0
    assert early["sql_requests_after"] == 0
    assert early["pool_pending_after"] == 0
    assert early["recovery_value"] == 40


@case("FRAME-041")
def test_flask_sync_worker_remains_occupied(
    production_framework_evidence,
) -> None:
    scenario = production_framework_evidence["scenarios"]["flask_sync_occupancy"]
    if production_framework_evidence["platform"]["system"] == "Windows":
        _assert_not_applicable(scenario, GUNICORN_NA)
        return
    assert scenario["status"] == "PASS"
    assert scenario["worker_class"] == "sync"
    assert scenario["request_slots_per_worker"] == 1
    assert scenario["second_request_waited"]
    assert scenario["request_loop_ids_distinct"]
    assert scenario["values_exact"]


@case("FRAME-042")
def test_flask_gthread_has_explicit_thread_occupancy(
    production_framework_evidence,
) -> None:
    scenario = production_framework_evidence["scenarios"]["flask_gthread_occupancy"]
    if production_framework_evidence["platform"]["system"] == "Windows":
        _assert_not_applicable(scenario, GUNICORN_NA)
        return
    assert scenario["status"] == "PASS"
    assert scenario["worker_class"] == "gthread"
    assert scenario["threads_per_worker"] == 4
    assert scenario["maximum_active_requests_per_process"] <= 4
    assert scenario["fifth_request_waited"]
    assert scenario["values_exact"]


@case("FRAME-043")
def test_flask_one_request_can_gather_concurrent_sql(
    production_framework_evidence,
) -> None:
    scenario = production_framework_evidence["scenarios"]["flask_internal_concurrency"]
    if production_framework_evidence["platform"]["system"] == "Windows":
        _assert_not_applicable(scenario, GUNICORN_NA)
        return
    assert scenario["status"] == "PASS"
    assert scenario["wsgi_request_slots_consumed"] == 1
    assert scenario["sequential_values"] == scenario["concurrent_values"]
    assert scenario["concurrent_seconds"] < scenario["sequential_seconds"] * 0.70
    assert scenario["inter_request_asgi_claim"] is False


@case("FRAME-044")
def test_adapted_flask_uses_persistent_worker_event_loop(
    production_framework_evidence,
) -> None:
    scenario = _scenario(
        production_framework_evidence,
        "adapted_flask_persistent_loop",
    )
    assert scenario["server"] == "uvicorn"
    assert scenario["sequential_loop_ids_equal"]
    assert scenario["pool_created_in_lifespan"]
    assert scenario["sessions_after"] == 0


@case("FRAME-045")
def test_adapted_flask_is_thread_sensitive_serialized_per_process(
    production_framework_evidence,
) -> None:
    scenario = _scenario(
        production_framework_evidence,
        "adapted_flask_serialization",
    )
    assert scenario["thread_sensitive"] is True
    assert scenario["maximum_wsgi_calls_per_process"] == 1
    assert scenario["concurrent_seconds"] >= scenario["sequential_seconds"] * 0.85
    assert scenario["scaling_unit"] == "process"


@case("FRAME-046")
def test_comparison_uses_only_approved_execution_model_claims(
    production_framework_evidence,
) -> None:
    comparison = production_framework_evidence["comparison"]
    assert set(comparison["labels"]) == APPROVED_LABELS
    text = comparison["rendered"].lower()
    for forbidden in (
        "equivalent throughput",
        "same throughput",
        "fully async flask",
    ):
        assert forbidden not in text


@case("FRAME-047")
def test_every_applicable_profile_executed_without_skip_or_swallow(
    production_framework_evidence,
) -> None:
    inventory = production_framework_evidence["profile_inventory"]
    profiles = _profiles(production_framework_evidence)
    assert {profile["family"] for profile in profiles} == PROFILE_FAMILIES
    assert len(profiles) == len(PROFILE_FAMILIES) * len(WORKER_COUNTS)
    assert len({profile["id"] for profile in profiles}) == len(profiles)
    assert all(
        sum(profile["family"] == family for profile in profiles) == len(WORKER_COUNTS)
        for family in PROFILE_FAMILIES
    )
    assert inventory["expected"] == len(profiles)
    assert inventory["executed"] + inventory["not_applicable"] == len(profiles)
    assert inventory["failed"] == 0
    assert inventory["skipped"] == 0
    assert all(
        profile["status"] == ("PASS" if profile["applicable"] else "N/A")
        for profile in profiles
    )


@case("FRAME-048")
def test_fixed_worker_load_reaches_required_and_extended_profiles(
    production_framework_evidence,
) -> None:
    configuration = production_framework_evidence["configuration"]
    assert configuration["required_operations"] == 1_000
    assert configuration["extended_operations"] == [10_000, 99_999]
    assert configuration["extended_requires_opt_in"] is True
    profiles = {
        profile["operations"]: profile
        for profile in production_framework_evidence["load_profiles"]
    }
    expected_operations = {1_000}
    if configuration["allow_extended"]:
        expected_operations |= {10_000, 99_999}
    assert set(profiles) == expected_operations
    for operations, profile in profiles.items():
        assert profile["status"] == "PASS"
        assert profile["completed"] == operations
        assert profile["errors"] == 0
        assert profile["fixed_client_workers"]
        assert profile["values_exact"]
        assert profile["maximum_sql_sessions"] <= profile["global_connection_budget"]
        assert profile["process_count_end"] == profile["process_count_start"]
        assert profile["pending_after"] == 0


@case("FRAME-049")
def test_all_process_socket_sql_and_pool_resources_are_gone(
    production_framework_evidence,
) -> None:
    teardown = production_framework_evidence["teardown"]
    assert teardown == {
        "child_processes_after": 0,
        "listening_sockets_after": 0,
        "sql_requests_after": 0,
        "sql_sessions_after": 0,
        "active_pool_leases_after": 0,
        "pending_pool_waiters_after": 0,
        "forced_cleanup_count": 0,
    }


@case("FRAME-050")
def test_commands_responses_logs_and_artifacts_are_private(
    production_framework_evidence,
) -> None:
    privacy = production_framework_evidence["privacy"]
    assert privacy["credential_values_expected"] >= 1
    assert privacy["credential_values_checked"] == privacy["credential_values_expected"]
    assert privacy["connection_string_patterns_checked"] > 0
    assert privacy["tracked_matches"] == []
    assert privacy["artifact_matches"] == []
    assert privacy["command_matches"] == []
    assert privacy["http_matches"] == []
    assert privacy["log_matches"] == []


@case("FRAME-051")
def test_hosted_linux_owns_exact_wheel_real_sql_auth_gate(
    production_framework_evidence,
) -> None:
    linux = production_framework_evidence["hosted_gate_contracts"]["Linux"]
    assert linux["workflow_owned"]
    assert linux["installed_wheel"]
    assert linux["real_sql_auth"]
    assert linux["database"] == "Microsoft SQL Server 2022 Docker"
    assert linux["required_profiles"] == "complete-posix"


@case("FRAME-052")
def test_hosted_windows_owns_sql_express_uvicorn_gate(
    production_framework_evidence,
) -> None:
    windows = production_framework_evidence["hosted_gate_contracts"]["Windows"]
    assert windows["workflow_owned"]
    assert windows["installed_wheel"]
    assert windows["real_sql_auth"]
    assert windows["database"] == "Microsoft SQL Server 2022 Express"
    assert windows["uvicorn_worker_counts"] == [1, 2, 4, 8]
    assert windows["gunicorn"] == "N/A"
    assert windows["uvloop"] == "N/A"


@case("FRAME-053")
def test_macos_claims_separate_hosted_structure_from_local_sql_auth(
    production_framework_evidence,
) -> None:
    macos = production_framework_evidence["hosted_gate_contracts"]["macOS"]
    assert macos["workflow_owned"]
    assert macos["installed_wheel"]
    assert macos["real_server_processes"]
    assert macos["hosted_sql_auth"] is False
    assert macos["local_docker_sql_auth"] is True
    assert macos["claim_boundary_explicit"]


@case("FRAME-054")
def test_cumulative_exact_sha_gates_are_mandatory(
    production_framework_evidence,
) -> None:
    cumulative = production_framework_evidence["cumulative_gate_contracts"]
    assert cumulative == {
        "sql_auth_runner": True,
        "original_local_regression": True,
        "root_rust": True,
        "vendored_tiberius": True,
        "installed_wheel": True,
        "dependency_security": True,
        "code_review_graph": True,
        "generated_reports": True,
        "exact_sha": True,
    }
