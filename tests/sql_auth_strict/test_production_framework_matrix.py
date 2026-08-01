from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
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
NATIVE_ASGI_LABEL = "native ASGI: concurrent requests on persistent event loop"
FLASK_WSGI_LABEL = "Flask WSGI: async view, occupied WSGI worker/thread"
ADAPTED_FLASK_LABEL = (
    "Flask via WsgiToAsgi: persistent ASGI loop, "
    "thread-sensitive WSGI serialization per process"
)
APPROVED_LABELS = {
    NATIVE_ASGI_LABEL,
    FLASK_WSGI_LABEL,
    ADAPTED_FLASK_LABEL,
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
    assert payload["harness"]["git_sha"] == _git_head()
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


def _assert_exact_int(
    value: object,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    assert type(value) is int
    if minimum is not None:
        assert value >= minimum
    if maximum is not None:
        assert value <= maximum
    return value


def _assert_finite_number(
    value: object,
    *,
    minimum: float | None = None,
) -> float:
    assert type(value) in {int, float}
    numeric = float(value)
    assert math.isfinite(numeric)
    if minimum is not None:
        assert numeric >= minimum
    return numeric


def _import_path_key(
    value: object,
    *,
    platform_system: str,
) -> tuple[str, ...]:
    assert isinstance(value, str)
    assert value
    assert len(value) <= 4_096
    assert all(character.isprintable() for character in value)
    if platform_system == "Windows":
        path = PureWindowsPath(value)
        assert path.is_absolute()
        key = tuple(part.casefold() for part in path.parts)
    else:
        assert platform_system in {"Darwin", "Linux"}
        assert "\\" not in value
        path = PurePosixPath(value)
        assert path.is_absolute()
        key = tuple(path.parts)
    assert not any(part in {".", ".."} for part in path.parts)
    folded_parts = tuple(part.casefold() for part in path.parts)
    assert "site-packages" in folded_parts
    terminal = folded_parts[-2:] if platform_system == "Windows" else key[-2:]
    assert terminal == ("fastmssql", "__init__.py")
    return key


def _assert_pid_list(
    value: object,
    *,
    allow_empty: bool,
) -> set[int]:
    assert isinstance(value, list)
    if not allow_empty:
        assert value
    pids = [_assert_exact_int(pid, minimum=1) for pid in value]
    assert len(pids) == len(set(pids))
    return set(pids)


def _assert_clean_scenario_lifecycle(
    scenario: dict[str, object],
) -> None:
    assert scenario["graceful_stop"] is True
    assert scenario["forced_cleanup"] is False
    assert scenario["listening_sockets_after"] == []
    _assert_exact_int(scenario["manager_pid"], minimum=1)
    _assert_pid_list(scenario["descendant_pids"], allow_empty=True)
    assert _assert_exact_int(scenario["returncode"]) == 0
    assert _assert_exact_int(scenario["sessions_after"], minimum=0) == 0
    ready_pids = _assert_pid_list(scenario["ready_pids"], allow_empty=False)
    shutdown_pids = _assert_pid_list(
        scenario["shutdown_pids"],
        allow_empty=False,
    )
    assert ready_pids == shutdown_pids


def _assert_wsgi_occupancy(
    evidence: dict[str, object],
    scenario: dict[str, object],
    *,
    worker_class: str,
    thread_limit: int,
) -> None:
    assert scenario["status"] == "PASS"
    assert scenario["worker_class"] == worker_class
    assert _assert_exact_int(scenario["workers"], minimum=1) == 1
    assert scenario["execution_model"] == FLASK_WSGI_LABEL
    assert _assert_exact_int(scenario["thread_limit_per_worker"], minimum=1) == (
        thread_limit
    )
    assert scenario["queued_request_proven"] is True
    assert scenario["loop_tokens_distinct_per_worker"] is True
    assert _assert_exact_int(scenario["maximum_active_requests"], minimum=1) == (
        thread_limit
    )
    assert _assert_exact_int(
        scenario["maximum_simultaneous_sql_requests"],
        minimum=1,
    ) == thread_limit
    budget = _assert_exact_int(
        evidence["configuration"]["global_connection_budget"],
        minimum=1,
    )
    assert _assert_exact_int(
        scenario["maximum_aggregate_sql_sessions"],
        minimum=0,
    ) <= budget
    values = scenario["values"]
    request_count = _assert_exact_int(scenario["request_count"], minimum=1)
    assert request_count == len(values) == thread_limit + 1
    assert all(type(value) is int for value in values)
    assert len(set(values)) == len(values)
    sql_delay_seconds = _assert_finite_number(
        scenario["sql_delay_seconds"],
        minimum=0.0,
    )
    assert sql_delay_seconds > 0
    wave_seconds = _assert_finite_number(scenario["wave_seconds"], minimum=0.0)
    assert wave_seconds >= sql_delay_seconds * 1.5
    _assert_clean_scenario_lifecycle(scenario)
    worker_execution = scenario["worker_execution"]
    assert len(worker_execution) == 1
    execution = worker_execution[0]
    assert set(execution) == {
        "maximum_active_requests",
        "pid",
        "wsgi_thread_count",
    }
    assert _assert_exact_int(execution["maximum_active_requests"], minimum=1) == (
        thread_limit
    )
    assert _assert_exact_int(execution["pid"], minimum=1) in set(
        scenario["ready_pids"]
    )
    assert _assert_exact_int(execution["wsgi_thread_count"], minimum=1) == (
        thread_limit
    )


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
    platform = production_framework_evidence["platform"]["system"]
    candidate_path = _import_path_key(
        wheel["import_path"],
        platform_system=platform,
    )
    worker_paths = {
        _import_path_key(
            worker["fastmssql_import_path"],
            platform_system=platform,
        )
        for profile in _applicable(production_framework_evidence)
        for worker in profile["worker_records"]
    }
    assert worker_paths
    assert worker_paths == {candidate_path}


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
    assert scenario["connection_replaced"] is True
    assert re.fullmatch(r"[0-9a-f]{64}", scenario["context_token_sha256"])
    assert scenario["sql_requests_after"] == 0
    assert scenario["pool_active_after"] == 0
    assert scenario["pool_pending_after"] == 0
    assert scenario["recovery_value"] == 36
    _assert_clean_scenario_lifecycle(scenario)


@case("FRAME-037")
def test_graceful_query_shutdown_finishes_without_forced_cleanup(
    production_framework_evidence,
) -> None:
    scenario = production_framework_evidence["scenarios"]["graceful_query_shutdown"]
    if production_framework_evidence["platform"]["system"] == "Windows":
        _assert_not_applicable(scenario, POSIX_SHUTDOWN_NA)
        return
    assert scenario["status"] == "PASS"
    assert scenario["sql_observed_before_signal"] is True
    assert scenario["response_completed_after_signal"] is True
    assert re.fullmatch(r"[0-9a-f]{64}", scenario["context_token_sha256"])
    assert type(scenario["response_session_id"]) is int
    assert scenario["response_session_id"] > 0
    assert type(scenario["response_value"]) is int
    assert scenario["sql_delay_seconds"] > 0
    assert scenario["forced_cleanup"] is False
    _assert_clean_scenario_lifecycle(scenario)


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
    for outcome, durable_result in {
        "commit": "row-present",
        "rollback": "row-absent",
    }.items():
        record = scenario[outcome]
        assert record["status"] == "PASS"
        assert record["outcome"] == outcome
        assert record["durable_result"] == durable_result
        assert record["sql_observed_before_signal"] is True
        assert record["transaction_holding_before_signal"] is True
        assert record["response_completed_after_signal"] is True
        assert record["transaction_settled"] is True
        assert isinstance(record["context_token_sha256"], str)
        assert re.fullmatch(r"[0-9a-f]{64}", record["context_token_sha256"])
        assert _assert_exact_int(record["item_id"], minimum=1) > 0
        assert _assert_exact_int(record["response_session_id"], minimum=1) > 0
        _assert_clean_scenario_lifecycle(record)


@case("FRAME-039")
def test_saturated_pool_has_bounded_admission_and_recovers(
    production_framework_evidence,
) -> None:
    scenario = _scenario(
        production_framework_evidence,
        "saturation",
    )
    pool_max = _assert_exact_int(scenario["pool_max_per_worker"], minimum=1)
    assert _assert_exact_int(scenario["admitted_holders"], minimum=1) == pool_max
    assert _assert_exact_int(scenario["admitted_waiters"], minimum=1) == pool_max
    assert _assert_exact_int(scenario["admission_capacity"], minimum=2) == (
        pool_max * 2
    )
    assert _assert_exact_int(scenario["maximum_sql_sessions"], minimum=1) == (
        pool_max
    )
    assert _assert_exact_int(scenario["maximum_sql_requests"], minimum=1) == (
        pool_max
    )
    assert _assert_exact_int(
        scenario["observed_active_connections"],
        minimum=1,
    ) == pool_max
    assert _assert_exact_int(scenario["observed_pending_gets"], minimum=1) == (
        pool_max
    )
    assert _assert_exact_int(scenario["rejected_requests"], minimum=1) > 0
    assert _assert_exact_int(scenario["acquire_timeouts"], minimum=1) == pool_max
    assert _assert_exact_int(scenario["pool_get_timed_out_delta"], minimum=1) == (
        pool_max
    )
    assert _assert_exact_int(scenario["pool_active_after"], minimum=0) == 0
    assert _assert_exact_int(scenario["pool_pending_after"], minimum=0) == 0
    assert _assert_exact_int(scenario["admission_active_after"], minimum=0) == 0
    assert _assert_exact_int(scenario["recovery_value"]) == 39
    _assert_clean_scenario_lifecycle(scenario)


@case("FRAME-040")
def test_result_stream_reaches_real_http_incrementally_and_cleans_up(
    production_framework_evidence,
) -> None:
    scenario = _scenario(
        production_framework_evidence,
        "large_streaming",
    )
    assert scenario["driver_buffer_rows"] == 8
    assert scenario["incremental_first_data"] is True
    assert 2 <= scenario["full_rows"] <= 10_000
    assert re.fullmatch(r"[0-9a-f]{64}", scenario["full_value_digest"])
    assert scenario["full_bytes_received"] > 0
    assert 0 <= scenario["full_first_data_seconds"] < scenario["full_elapsed_seconds"]
    assert scenario["full_pool_active_after"] == 0
    assert scenario["full_pool_pending_after"] == 0
    assert scenario["early_client_closed"] is True
    assert 2 <= scenario["early_requested_rows"] <= 10_000
    assert 1 <= scenario["early_prefix_rows"] < scenario["early_requested_rows"]
    assert re.fullmatch(r"[0-9a-f]{64}", scenario["early_prefix_digest"])
    assert scenario["early_bytes_received"] > 0
    assert 0 <= scenario["early_first_data_seconds"] < scenario["early_close_seconds"]
    assert scenario["early_sql_requests_after"] == 0
    assert scenario["early_pool_active_after"] == 0
    assert scenario["early_pool_pending_after"] == 0
    assert scenario["rss_peak_bytes"] >= max(
        scenario["rss_start_bytes"],
        scenario["rss_end_bytes"],
    )
    assert scenario["rss_growth_bytes"] == (
        scenario["rss_peak_bytes"] - scenario["rss_start_bytes"]
    )
    assert scenario["rss_growth_bytes"] <= scenario["rss_growth_limit_bytes"]
    assert scenario["recovery_value"] == 40
    _assert_clean_scenario_lifecycle(scenario)


@case("FRAME-041")
def test_flask_sync_worker_remains_occupied(
    production_framework_evidence,
) -> None:
    scenario = production_framework_evidence["scenarios"]["flask_sync_occupancy"]
    if production_framework_evidence["platform"]["system"] == "Windows":
        _assert_not_applicable(scenario, GUNICORN_NA)
        return
    _assert_wsgi_occupancy(
        production_framework_evidence,
        scenario,
        worker_class="sync",
        thread_limit=1,
    )


@case("FRAME-042")
def test_flask_gthread_has_explicit_thread_occupancy(
    production_framework_evidence,
) -> None:
    scenario = production_framework_evidence["scenarios"]["flask_gthread_occupancy"]
    if production_framework_evidence["platform"]["system"] == "Windows":
        _assert_not_applicable(scenario, GUNICORN_NA)
        return
    _assert_wsgi_occupancy(
        production_framework_evidence,
        scenario,
        worker_class="gthread",
        thread_limit=4,
    )


@case("FRAME-043")
def test_flask_one_request_can_gather_concurrent_sql(
    production_framework_evidence,
) -> None:
    scenario = production_framework_evidence["scenarios"]["flask_internal_concurrency"]
    if production_framework_evidence["platform"]["system"] == "Windows":
        _assert_not_applicable(scenario, GUNICORN_NA)
        return
    assert scenario["status"] == "PASS"
    assert scenario["execution_model"] == FLASK_WSGI_LABEL
    assert _assert_exact_int(scenario["wsgi_request_slots"], minimum=1) == 1
    assert scenario["values_exact"] is True
    assert _assert_exact_int(
        scenario["maximum_simultaneous_sql_requests"],
        minimum=1,
    ) == 4
    sequential_seconds = _assert_finite_number(
        scenario["sequential_seconds"],
        minimum=0.0,
    )
    concurrent_seconds = _assert_finite_number(
        scenario["concurrent_seconds"],
        minimum=0.0,
    )
    sql_delay_seconds = _assert_finite_number(
        scenario["sql_delay_seconds"],
        minimum=0.0,
    )
    outer_response_seconds = _assert_finite_number(
        scenario["outer_response_seconds"],
        minimum=0.0,
    )
    assert concurrent_seconds < sequential_seconds * 0.70
    assert sequential_seconds >= sql_delay_seconds * 3
    assert concurrent_seconds >= sql_delay_seconds * 0.75
    assert outer_response_seconds >= (sequential_seconds + concurrent_seconds) * 0.90
    assert _assert_exact_int(scenario["pool_active_after"], minimum=0) == 0
    assert _assert_exact_int(scenario["pool_pending_after"], minimum=0) == 0
    _assert_clean_scenario_lifecycle(scenario)


@case("FRAME-044")
def test_adapted_flask_uses_persistent_worker_event_loop(
    production_framework_evidence,
) -> None:
    scenario = _scenario(
        production_framework_evidence,
        "adapted_flask_persistent_loop",
    )
    assert scenario["server"] == "uvicorn"
    assert scenario["execution_model"] == ADAPTED_FLASK_LABEL
    assert _assert_exact_int(
        scenario["maximum_serialized_wsgi_calls_per_process"],
        minimum=1,
    ) == 1
    workers = scenario["workers"]
    loops = scenario["persistent_worker_loops"]
    worker_records = scenario["worker_records"]
    workers = _assert_exact_int(workers, minimum=1)
    assert len(loops) == len(worker_records) == workers
    assert {record["pid"] for record in loops} == {
        worker["pid"] for worker in worker_records
    }
    for record in loops:
        assert all(
            type(record[name]) is int and record[name] > 0
            for name in (
                "async_thread_token",
                "loop_token",
                "pid",
                "wsgi_thread_token",
            )
        )
    for worker in worker_records:
        worker_pid = _assert_exact_int(worker["pid"], minimum=1)
        assert _assert_exact_int(worker["pool_created_pid"], minimum=1) == worker_pid
        connected = _assert_finite_number(
            worker["pool_connected_monotonic"],
            minimum=0.0,
        )
        started = _assert_finite_number(
            worker["process_started_monotonic"],
            minimum=0.0,
        )
        assert connected >= started
    maximum_requests = _assert_exact_int(
        scenario["maximum_simultaneous_sql_requests"],
        minimum=1,
    )
    assert maximum_requests <= workers
    budget = _assert_exact_int(
        production_framework_evidence["configuration"][
            "global_connection_budget"
        ],
        minimum=1,
    )
    assert _assert_exact_int(
        scenario["maximum_aggregate_sql_sessions"],
        minimum=0,
    ) <= budget
    _assert_clean_scenario_lifecycle(scenario)


@case("FRAME-045")
def test_adapted_flask_is_thread_sensitive_serialized_per_process(
    production_framework_evidence,
) -> None:
    scenario = _scenario(
        production_framework_evidence,
        "adapted_flask_serialization",
    )
    assert scenario["execution_model"] == ADAPTED_FLASK_LABEL
    assert _assert_exact_int(scenario["wsgi_calls_per_process"], minimum=1) == 1
    assert _assert_exact_int(
        scenario["maximum_simultaneous_sql_requests"],
        minimum=1,
    ) == 1
    assert scenario["multi_process_scaling"] == "additional worker processes only"
    assert len(scenario["values"]) == 4
    assert all(type(value) is int for value in scenario["values"])
    sequential_seconds = _assert_finite_number(
        scenario["sequential_seconds"],
        minimum=0.0,
    )
    concurrent_seconds = _assert_finite_number(
        scenario["concurrent_seconds"],
        minimum=0.0,
    )
    assert sequential_seconds > 0
    assert concurrent_seconds > 0
    serialization_ratio = _assert_finite_number(
        scenario["serialization_ratio"],
        minimum=0.0,
    )
    assert 0.75 <= serialization_ratio <= 1.35
    assert serialization_ratio == pytest.approx(
        concurrent_seconds / sequential_seconds
    )
    assert _assert_exact_int(scenario["pool_active_after"], minimum=0) == 0
    assert _assert_exact_int(scenario["pool_pending_after"], minimum=0) == 0
    _assert_clean_scenario_lifecycle(scenario)


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


def _expected_fixed_worker_digest(
    operations: int,
    client_worker_count: int,
) -> str:
    combined = hashlib.sha256()
    for worker_id in range(client_worker_count):
        worker_digest = hashlib.sha256()
        worker_count = 0
        for operation_id in range(
            worker_id + 1,
            operations + 1,
            client_worker_count,
        ):
            worker_digest.update(
                f"{operation_id}:{operation_id}\n".encode("ascii")
            )
            worker_count += 1
        combined.update(
            f"{worker_id}:{worker_count}:{worker_digest.hexdigest()}\n".encode(
                "ascii"
            )
        )
    return combined.hexdigest()


@case("FRAME-048")
def test_fixed_worker_load_reaches_required_and_extended_profiles(
    production_framework_evidence,
) -> None:
    configuration = production_framework_evidence["configuration"]
    assert _assert_exact_int(configuration["required_operations"], minimum=1) == (
        1_000
    )
    assert configuration["extended_operations"] == [10_000, 99_999]
    assert configuration["extended_requires_opt_in"] is True
    assert type(configuration["allow_extended"]) is bool
    configured_budget = _assert_exact_int(
        configuration["global_connection_budget"],
        minimum=1,
    )
    load_profiles = production_framework_evidence["load_profiles"]
    assert isinstance(load_profiles, list)
    profiles: dict[int, dict[str, object]] = {}
    for profile in load_profiles:
        operations = _assert_exact_int(
            profile["operations"],
            minimum=1,
            maximum=99_999,
        )
        assert operations not in profiles
        profiles[operations] = profile
    assert len(profiles) == len(load_profiles)
    expected_operations = {1_000}
    if configuration["allow_extended"]:
        expected_operations |= {10_000, 99_999}
    assert set(profiles) == expected_operations
    for operations, profile in profiles.items():
        assert profile["status"] == "PASS"
        assert _assert_exact_int(profile["completed"], minimum=0) == operations
        assert _assert_exact_int(profile["errors"], minimum=0) == 0
        assert profile["fixed_client_workers"] is True
        assert profile["values_exact"] is True
        assert _assert_exact_int(profile["value_count"], minimum=0) == operations
        assert _assert_exact_int(profile["expected_value_count"], minimum=0) == (
            operations
        )
        expected_value_sum = operations * (operations + 1) // 2
        assert _assert_exact_int(profile["value_sum"], minimum=0) == (
            expected_value_sum
        )
        assert _assert_exact_int(profile["expected_value_sum"], minimum=0) == (
            expected_value_sum
        )
        client_workers = _assert_exact_int(
            profile["client_worker_count"],
            minimum=1,
            maximum=256,
        )
        expected_digest = _expected_fixed_worker_digest(
            operations,
            client_workers,
        )
        assert profile["value_digest"] == expected_digest
        assert profile["expected_value_digest"] == expected_digest
        assert profile["value_digest_algorithm"] == "sha256-worker-stride-v1"
        maximum_active_requests = _assert_exact_int(
            profile["maximum_active_requests"],
            minimum=1,
        )
        assert maximum_active_requests <= client_workers
        budget = _assert_exact_int(
            profile["global_connection_budget"],
            minimum=1,
        )
        assert budget == configured_budget
        assert _assert_exact_int(
            profile["maximum_sql_sessions"],
            minimum=0,
        ) <= budget
        assert _assert_exact_int(
            profile["maximum_sql_requests"],
            minimum=0,
        ) <= budget
        assert _assert_exact_int(
            profile["maximum_pool_active"],
            minimum=0,
        ) <= budget
        assert _assert_exact_int(
            profile["maximum_pool_pending"],
            minimum=0,
        ) <= client_workers
        server_workers = _assert_exact_int(
            profile["server_worker_count"],
            minimum=1,
        )
        assert server_workers == 4
        for field_name in (
            "server_child_count_start",
            "maximum_server_child_count",
            "server_child_count_end",
            "process_count_start",
            "process_count_end",
        ):
            assert _assert_exact_int(profile[field_name], minimum=0) == server_workers
        assert _assert_exact_int(profile["resource_samples"], minimum=2) >= 2
        rss_start = _assert_exact_int(profile["rss_start_bytes"], minimum=0)
        rss_end = _assert_exact_int(profile["rss_end_bytes"], minimum=0)
        rss_peak = _assert_exact_int(profile["rss_peak_bytes"], minimum=0)
        rss_growth = _assert_exact_int(profile["rss_growth_bytes"], minimum=0)
        assert rss_peak >= max(rss_start, rss_end)
        assert rss_growth == rss_peak - rss_start
        assert _assert_exact_int(profile["sql_requests_start"], minimum=0) == 0
        assert _assert_exact_int(profile["sql_requests_end"], minimum=0) == 0
        assert _assert_exact_int(profile["sql_sessions_start"], minimum=0) <= budget
        assert _assert_exact_int(profile["sql_sessions_end"], minimum=0) <= budget
        for field_name in (
            "pool_active_start",
            "pool_active_after",
            "pool_pending_start",
            "pool_pending_after",
            "pending_after",
        ):
            assert _assert_exact_int(profile[field_name], minimum=0) == 0
        histogram = profile["latency_histogram"]
        expected_upper_bounds = [
            1,
            2,
            5,
            10,
            25,
            50,
            100,
            250,
            500,
            1_000,
            2_500,
            5_000,
            10_000,
        ]
        buckets = histogram["buckets"]
        assert len(buckets) == len(expected_upper_bounds)
        bucket_total = 0
        for bucket, expected_upper_bound in zip(
            buckets,
            expected_upper_bounds,
            strict=True,
        ):
            assert _assert_exact_int(bucket["upper_bound_ms"], minimum=1) == (
                expected_upper_bound
            )
            bucket_total += _assert_exact_int(bucket["count"], minimum=0)
        assert _assert_exact_int(histogram["count"], minimum=0) == operations
        overflow_count = _assert_exact_int(
            histogram["overflow_count"],
            minimum=0,
        )
        assert bucket_total + overflow_count == operations
        minimum_ms = _assert_finite_number(histogram["minimum_ms"], minimum=0.0)
        maximum_ms = _assert_finite_number(histogram["maximum_ms"], minimum=0.0)
        sum_ms = _assert_finite_number(histogram["sum_ms"], minimum=0.0)
        assert minimum_ms <= maximum_ms
        minimum_total = minimum_ms * operations
        maximum_total = maximum_ms * operations
        rounding_tolerance = operations * max(
            math.ulp(minimum_total),
            math.ulp(maximum_total),
            math.ulp(sum_ms),
        )
        assert minimum_total - rounding_tolerance <= sum_ms
        assert sum_ms <= maximum_total + rounding_tolerance
        _assert_finite_number(profile["duration_seconds"], minimum=0.0)


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


def _runtime_shaped_case_evidence() -> dict[str, object]:
    """Return valid records using runtime-emitted names and invariants."""

    candidate_sha = "a" * 40
    wheel_sha256 = "b" * 64
    wheel_filename = "fastmssql-0.7.7-cp311-abi3.whl"
    import_path = "/opt/wheel/lib/site-packages/fastmssql/__init__.py"
    worker = {
        "application_name": "framework-worker-101",
        "fastmssql_import_path": import_path,
        "pid": 101,
        "pool_connected_monotonic": 2.0,
        "pool_created_monotonic": 1.5,
        "pool_created_pid": 101,
        "pool_identity": "pool-101",
        "principal": "framework_user",
        "process_started_monotonic": 1.0,
    }
    lifecycle_common = {
        "descendant_pids": [],
        "forced_cleanup": False,
        "graceful_stop": True,
        "listening_sockets_after": [],
        "manager_pid": 100,
        "profile_id": "representative-profile",
        "ready_pids": [101],
        "returncode": 0,
        "sessions_after": 0,
        "shutdown_pids": [101],
    }
    transaction_common = {
        **lifecycle_common,
        "response_completed_after_signal": True,
        "sql_observed_before_signal": True,
        "status": "PASS",
        "transaction_holding_before_signal": True,
        "transaction_settled": True,
    }
    latency_buckets = [
        {
            "count": 1_000 if index == 0 else 0,
            "upper_bound_ms": upper_bound,
        }
        for index, upper_bound in enumerate(
            (1, 2, 5, 10, 25, 50, 100, 250, 500, 1_000, 2_500, 5_000, 10_000)
        )
    ]
    value_digest = (
        "9f8d50c88a799974d40cb5af35f1f2cf"
        "cf1339551ca2c9c008ae6da400bdc5ff"
    )
    return {
        "candidate": {
            "git_sha": candidate_sha,
            "wheel": {
                "filename": wheel_filename,
                "import_path": import_path,
                "sha256": wheel_sha256,
            },
        },
        "configuration": {
            "allow_extended": False,
            "extended_operations": [10_000, 99_999],
            "extended_requires_opt_in": True,
            "global_connection_budget": 16,
            "required_operations": 1_000,
        },
        "load_profiles": [
            {
                "client_worker_count": 16,
                "completed": 1_000,
                "duration_seconds": 2.0,
                "errors": 0,
                "expected_value_count": 1_000,
                "expected_value_digest": value_digest,
                "expected_value_sum": 500_500,
                "fixed_client_workers": True,
                "global_connection_budget": 16,
                "latency_histogram": {
                    "buckets": latency_buckets,
                    "count": 1_000,
                    "maximum_ms": 1.0,
                    "minimum_ms": 1.0,
                    "overflow_count": 0,
                    "sum_ms": 1_000.0,
                },
                "maximum_active_requests": 16,
                "maximum_pool_active": 8,
                "maximum_pool_pending": 4,
                "maximum_server_child_count": 4,
                "maximum_sql_requests": 8,
                "maximum_sql_sessions": 8,
                "operations": 1_000,
                "pending_after": 0,
                "pool_active_after": 0,
                "pool_active_start": 0,
                "pool_pending_after": 0,
                "pool_pending_start": 0,
                "process_count_end": 4,
                "process_count_start": 4,
                "resource_samples": 3,
                "rss_end_bytes": 1_100,
                "rss_growth_bytes": 200,
                "rss_peak_bytes": 1_200,
                "rss_start_bytes": 1_000,
                "server_child_count_end": 4,
                "server_child_count_start": 4,
                "server_worker_count": 4,
                "sql_requests_end": 0,
                "sql_requests_start": 0,
                "sql_sessions_end": 4,
                "sql_sessions_start": 4,
                "status": "PASS",
                "value_count": 1_000,
                "value_digest": value_digest,
                "value_digest_algorithm": "sha256-worker-stride-v1",
                "value_sum": 500_500,
                "values_exact": True,
            }
        ],
        "overall": "PASS",
        "platform": {"system": "Linux"},
        "profiles": [
            {
                "applicable": True,
                "worker_records": [worker],
            }
        ],
        "scenarios": {
            "adapted_flask_persistent_loop": {
                **lifecycle_common,
                "execution_model": (
                    "Flask via WsgiToAsgi: persistent ASGI loop, "
                    "thread-sensitive WSGI serialization per process"
                ),
                "maximum_aggregate_sql_sessions": 1,
                "maximum_serialized_wsgi_calls_per_process": 1,
                "maximum_simultaneous_sql_requests": 1,
                "persistent_worker_loops": [
                    {
                        "async_thread_token": 11,
                        "loop_token": 7,
                        "pid": 101,
                        "wsgi_thread_token": 13,
                    }
                ],
                "server": "uvicorn",
                "status": "PASS",
                "worker_records": [worker],
                "workers": 1,
            },
            "adapted_flask_serialization": {
                **lifecycle_common,
                "concurrent_seconds": 1.07,
                "execution_model": (
                    "Flask via WsgiToAsgi: persistent ASGI loop, "
                    "thread-sensitive WSGI serialization per process"
                ),
                "loop_token": 7,
                "maximum_simultaneous_sql_requests": 1,
                "multi_process_scaling": "additional worker processes only",
                "pool_active_after": 0,
                "pool_pending_after": 0,
                "sequential_seconds": 1.04,
                "serialization_ratio": 1.07 / 1.04,
                "sql_delay_seconds": 0.25,
                "status": "PASS",
                "values": [1, 2, 3, 4],
                "wsgi_calls_per_process": 1,
            },
            "client_disconnect_cancellation": {
                **lifecycle_common,
                "connection_replaced": True,
                "context_token_sha256": "1" * 64,
                "pool_active_after": 0,
                "pool_pending_after": 0,
                "recovery_value": 36,
                "sql_observed_before_close": True,
                "sql_requests_after": 0,
                "status": "PASS",
                "transport": "raw-tcp-client-close",
            },
            "flask_gthread_occupancy": {
                **lifecycle_common,
                "execution_model": (
                    "Flask WSGI: async view, occupied WSGI worker/thread"
                ),
                "loop_tokens_distinct_per_worker": True,
                "maximum_active_requests": 4,
                "maximum_aggregate_sql_sessions": 4,
                "maximum_simultaneous_sql_requests": 4,
                "profile_id": "flask-gunicorn-gthread-w1",
                "queued_request_proven": True,
                "request_count": 5,
                "sql_delay_seconds": 0.25,
                "status": "PASS",
                "thread_limit_per_worker": 4,
                "values": [1, 2, 3, 4, 5],
                "wave_seconds": 0.5,
                "worker_class": "gthread",
                "worker_execution": [
                    {
                        "maximum_active_requests": 4,
                        "pid": 101,
                        "wsgi_thread_count": 4,
                    }
                ],
                "workers": 1,
            },
            "flask_internal_concurrency": {
                **lifecycle_common,
                "concurrent_seconds": 0.26,
                "execution_model": (
                    "Flask WSGI: async view, occupied WSGI worker/thread"
                ),
                "loop_token": 7,
                "maximum_simultaneous_sql_requests": 4,
                "outer_response_seconds": 1.3,
                "pool_active_after": 0,
                "pool_pending_after": 0,
                "sequential_seconds": 1.02,
                "sql_delay_seconds": 0.25,
                "status": "PASS",
                "values_exact": True,
                "wsgi_request_slots": 1,
            },
            "flask_sync_occupancy": {
                **lifecycle_common,
                "execution_model": (
                    "Flask WSGI: async view, occupied WSGI worker/thread"
                ),
                "loop_tokens_distinct_per_worker": True,
                "maximum_active_requests": 1,
                "maximum_aggregate_sql_sessions": 1,
                "maximum_simultaneous_sql_requests": 1,
                "profile_id": "flask-gunicorn-sync-w1",
                "queued_request_proven": True,
                "request_count": 2,
                "sql_delay_seconds": 0.25,
                "status": "PASS",
                "thread_limit_per_worker": 1,
                "values": [1, 2],
                "wave_seconds": 0.5,
                "worker_class": "sync",
                "worker_execution": [
                    {
                        "maximum_active_requests": 1,
                        "pid": 101,
                        "wsgi_thread_count": 1,
                    }
                ],
                "workers": 1,
            },
            "graceful_query_shutdown": {
                **lifecycle_common,
                "context_token_sha256": "2" * 64,
                "response_completed_after_signal": True,
                "response_session_id": 52,
                "response_value": 37,
                "sql_delay_seconds": 1.0,
                "sql_observed_before_signal": True,
                "status": "PASS",
            },
            "graceful_transaction_shutdown": {
                "commit": {
                    **transaction_common,
                    "context_token_sha256": "3" * 64,
                    "durable_result": "row-present",
                    "item_id": 37,
                    "outcome": "commit",
                    "response_session_id": 53,
                },
                "rollback": {
                    **transaction_common,
                    "context_token_sha256": "4" * 64,
                    "durable_result": "row-absent",
                    "item_id": 38,
                    "outcome": "rollback",
                    "response_session_id": 54,
                },
                "status": "PASS",
            },
            "large_streaming": {
                **lifecycle_common,
                "driver_buffer_rows": 8,
                "early_bytes_received": 256,
                "early_client_closed": True,
                "early_close_seconds": 0.2,
                "early_first_data_seconds": 0.05,
                "early_pool_active_after": 0,
                "early_pool_pending_after": 0,
                "early_prefix_digest": "d" * 64,
                "early_prefix_rows": 16,
                "early_requested_rows": 1_000,
                "early_sql_requests_after": 0,
                "full_bytes_received": 16_384,
                "full_elapsed_seconds": 0.5,
                "full_first_data_seconds": 0.05,
                "full_pool_active_after": 0,
                "full_pool_pending_after": 0,
                "full_rows": 1_000,
                "full_value_digest": "e" * 64,
                "incremental_first_data": True,
                "recovery_value": 40,
                "rss_end_bytes": 4_608,
                "rss_growth_bytes": 1_024,
                "rss_growth_limit_bytes": 2_048,
                "rss_peak_bytes": 5_120,
                "rss_start_bytes": 4_096,
                "status": "PASS",
            },
            "saturation": {
                **lifecycle_common,
                "acquire_timeouts": 4,
                "admission_active_after": 0,
                "admission_capacity": 8,
                "admitted_holders": 4,
                "admitted_waiters": 4,
                "maximum_sql_requests": 4,
                "maximum_sql_sessions": 4,
                "observed_active_connections": 4,
                "observed_pending_gets": 4,
                "pool_active_after": 0,
                "pool_get_timed_out_delta": 4,
                "pool_max_per_worker": 4,
                "pool_pending_after": 0,
                "recovery_value": 39,
                "rejected_requests": 1,
                "status": "PASS",
            },
        },
        "violations": [],
    }


def _replace_case_value(
    evidence: dict[str, object],
    path: tuple[object, ...],
    value: object,
) -> None:
    target: object = evidence
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value


_RUNTIME_CASE_VALIDATOR_CONTRACTS = (
    (
        "FRAME-028",
        test_every_worker_imports_the_exact_isolated_wheel,
        (
            "profiles",
            0,
            "worker_records",
            0,
            "fastmssql_import_path",
        ),
        "/srv/other/lib/site-packages/fastmssql/__init__.py",
    ),
    (
        "FRAME-036",
        test_real_client_disconnect_settles_sql_and_recovers,
        ("scenarios", "client_disconnect_cancellation", "connection_replaced"),
        False,
    ),
    (
        "FRAME-037",
        test_graceful_query_shutdown_finishes_without_forced_cleanup,
        ("scenarios", "graceful_query_shutdown", "response_completed_after_signal"),
        False,
    ),
    (
        "FRAME-038",
        test_graceful_transaction_shutdown_has_deterministic_outcomes,
        (
            "scenarios",
            "graceful_transaction_shutdown",
            "rollback",
            "durable_result",
        ),
        "row-present",
    ),
    (
        "FRAME-039",
        test_saturated_pool_has_bounded_admission_and_recovers,
        ("scenarios", "saturation", "admission_active_after"),
        1,
    ),
    (
        "FRAME-040",
        test_result_stream_reaches_real_http_incrementally_and_cleans_up,
        ("scenarios", "large_streaming", "incremental_first_data"),
        False,
    ),
    (
        "FRAME-041",
        test_flask_sync_worker_remains_occupied,
        ("scenarios", "flask_sync_occupancy", "loop_tokens_distinct_per_worker"),
        False,
    ),
    (
        "FRAME-042",
        test_flask_gthread_has_explicit_thread_occupancy,
        ("scenarios", "flask_gthread_occupancy", "queued_request_proven"),
        False,
    ),
    (
        "FRAME-043",
        test_flask_one_request_can_gather_concurrent_sql,
        (
            "scenarios",
            "flask_internal_concurrency",
            "maximum_simultaneous_sql_requests",
        ),
        1,
    ),
    (
        "FRAME-044",
        test_adapted_flask_uses_persistent_worker_event_loop,
        ("scenarios", "adapted_flask_persistent_loop", "persistent_worker_loops"),
        [],
    ),
    (
        "FRAME-045",
        test_adapted_flask_is_thread_sensitive_serialized_per_process,
        (
            "scenarios",
            "adapted_flask_serialization",
            "maximum_simultaneous_sql_requests",
        ),
        2,
    ),
    (
        "FRAME-048",
        test_fixed_worker_load_reaches_required_and_extended_profiles,
        ("load_profiles", 0, "value_digest"),
        "f" * 64,
    ),
)


@pytest.mark.parametrize(
    ("case_id", "validator", "mutation_path", "invalid_value"),
    _RUNTIME_CASE_VALIDATOR_CONTRACTS,
    ids=[contract[0] for contract in _RUNTIME_CASE_VALIDATOR_CONTRACTS],
)
def test_case_validator_accepts_runtime_shape_and_rejects_primary_mutation(
    case_id: str,
    validator,
    mutation_path: tuple[object, ...],
    invalid_value: object,
) -> None:
    """Prevent a global PASS check or stale field vocabulary from passing."""

    evidence = _runtime_shaped_case_evidence()
    validator(deepcopy(evidence))

    forged = deepcopy(evidence)
    _replace_case_value(forged, mutation_path, invalid_value)
    assert forged["overall"] == "PASS", case_id
    assert forged["violations"] == [], case_id
    with pytest.raises(AssertionError):
        validator(forged)


_CASE_VALIDATOR_REVIEW_DEFECTS = (
    (
        "FRAME-028-windows-path-on-posix",
        test_every_worker_imports_the_exact_isolated_wheel,
        (
            (
                ("candidate", "wheel", "import_path"),
                r"C:\wheel-venv\Lib\site-packages\fastmssql\__init__.py",
            ),
            (
                (
                    "profiles",
                    0,
                    "worker_records",
                    0,
                    "fastmssql_import_path",
                ),
                r"C:\wheel-venv\Lib\site-packages\fastmssql\__init__.py",
            ),
        ),
    ),
    (
        "FRAME-028-relative-posix-path",
        test_every_worker_imports_the_exact_isolated_wheel,
        (
            (
                ("candidate", "wheel", "import_path"),
                "opt/wheel-venv/lib/site-packages/fastmssql/__init__.py",
            ),
            (
                (
                    "profiles",
                    0,
                    "worker_records",
                    0,
                    "fastmssql_import_path",
                ),
                "opt/wheel-venv/lib/site-packages/fastmssql/__init__.py",
            ),
        ),
    ),
    (
        "FRAME-028-wrong-terminal-package",
        test_every_worker_imports_the_exact_isolated_wheel,
        (
            (
                ("candidate", "wheel", "import_path"),
                "/opt/wheel-venv/lib/site-packages/not-fastmssql/__init__.py",
            ),
            (
                (
                    "profiles",
                    0,
                    "worker_records",
                    0,
                    "fastmssql_import_path",
                ),
                "/opt/wheel-venv/lib/site-packages/not-fastmssql/__init__.py",
            ),
        ),
    ),
    (
        "FRAME-028-posix-path-on-windows",
        test_every_worker_imports_the_exact_isolated_wheel,
        (
            (("platform", "system"), "Windows"),
            (
                ("candidate", "wheel", "import_path"),
                "/opt/wheel-venv/lib/site-packages/fastmssql/__init__.py",
            ),
            (
                (
                    "profiles",
                    0,
                    "worker_records",
                    0,
                    "fastmssql_import_path",
                ),
                "/opt/wheel-venv/lib/site-packages/fastmssql/__init__.py",
            ),
        ),
    ),
    (
        "FRAME-028-parent-segment",
        test_every_worker_imports_the_exact_isolated_wheel,
        (
            (
                ("candidate", "wheel", "import_path"),
                (
                    "/opt/wheel-venv/lib/site-packages/other/../"
                    "fastmssql/__init__.py"
                ),
            ),
            (
                (
                    "profiles",
                    0,
                    "worker_records",
                    0,
                    "fastmssql_import_path",
                ),
                (
                    "/opt/wheel-venv/lib/site-packages/other/../"
                    "fastmssql/__init__.py"
                ),
            ),
        ),
    ),
    (
        "FRAME-028-control-character",
        test_every_worker_imports_the_exact_isolated_wheel,
        (
            (
                ("candidate", "wheel", "import_path"),
                "/opt/wheel-venv/lib/site-packages/fastmssql/\n__init__.py",
            ),
            (
                (
                    "profiles",
                    0,
                    "worker_records",
                    0,
                    "fastmssql_import_path",
                ),
                "/opt/wheel-venv/lib/site-packages/fastmssql/\n__init__.py",
            ),
        ),
    ),
    (
        "FRAME-028-posix-terminal-case",
        test_every_worker_imports_the_exact_isolated_wheel,
        (
            (
                ("candidate", "wheel", "import_path"),
                "/opt/wheel-venv/lib/site-packages/FastMssql/__INIT__.PY",
            ),
            (
                (
                    "profiles",
                    0,
                    "worker_records",
                    0,
                    "fastmssql_import_path",
                ),
                "/opt/wheel-venv/lib/site-packages/FastMssql/__INIT__.PY",
            ),
        ),
    ),
    (
        "FRAME-036-empty-worker-lifecycle",
        test_real_client_disconnect_settles_sql_and_recovers,
        (
            (("scenarios", "client_disconnect_cancellation", "ready_pids"), []),
            (
                ("scenarios", "client_disconnect_cancellation", "shutdown_pids"),
                [],
            ),
        ),
    ),
    (
        "FRAME-036-boolean-worker-pid",
        test_real_client_disconnect_settles_sql_and_recovers,
        (
            (
                ("scenarios", "client_disconnect_cancellation", "ready_pids"),
                [True],
            ),
            (
                ("scenarios", "client_disconnect_cancellation", "shutdown_pids"),
                [True],
            ),
        ),
    ),
    (
        "FRAME-036-duplicate-worker-pids",
        test_real_client_disconnect_settles_sql_and_recovers,
        (
            (
                ("scenarios", "client_disconnect_cancellation", "ready_pids"),
                [101, 101],
            ),
            (
                ("scenarios", "client_disconnect_cancellation", "shutdown_pids"),
                [101, 101],
            ),
        ),
    ),
    (
        "FRAME-036-zero-worker-pid",
        test_real_client_disconnect_settles_sql_and_recovers,
        (
            (
                ("scenarios", "client_disconnect_cancellation", "ready_pids"),
                [0],
            ),
            (
                ("scenarios", "client_disconnect_cancellation", "shutdown_pids"),
                [0],
            ),
        ),
    ),
    (
        "FRAME-036-negative-worker-pid",
        test_real_client_disconnect_settles_sql_and_recovers,
        (
            (
                ("scenarios", "client_disconnect_cancellation", "ready_pids"),
                [-1],
            ),
            (
                ("scenarios", "client_disconnect_cancellation", "shutdown_pids"),
                [-1],
            ),
        ),
    ),
    (
        "FRAME-036-boolean-manager-pid",
        test_real_client_disconnect_settles_sql_and_recovers,
        (
            (("scenarios", "client_disconnect_cancellation", "manager_pid"), True),
        ),
    ),
    (
        "FRAME-036-zero-manager-pid",
        test_real_client_disconnect_settles_sql_and_recovers,
        (
            (("scenarios", "client_disconnect_cancellation", "manager_pid"), 0),
        ),
    ),
    (
        "FRAME-036-negative-manager-pid",
        test_real_client_disconnect_settles_sql_and_recovers,
        (
            (("scenarios", "client_disconnect_cancellation", "manager_pid"), -1),
        ),
    ),
    (
        "FRAME-036-boolean-returncode",
        test_real_client_disconnect_settles_sql_and_recovers,
        (
            (("scenarios", "client_disconnect_cancellation", "returncode"), False),
        ),
    ),
    (
        "FRAME-036-boolean-session-count",
        test_real_client_disconnect_settles_sql_and_recovers,
        (
            (("scenarios", "client_disconnect_cancellation", "sessions_after"), False),
        ),
    ),
    (
        "FRAME-036-boolean-descendant-pid",
        test_real_client_disconnect_settles_sql_and_recovers,
        (
            (
                ("scenarios", "client_disconnect_cancellation", "descendant_pids"),
                [True],
            ),
        ),
    ),
    (
        "FRAME-036-negative-descendant-pid",
        test_real_client_disconnect_settles_sql_and_recovers,
        (
            (
                ("scenarios", "client_disconnect_cancellation", "descendant_pids"),
                [-1],
            ),
        ),
    ),
    (
        "FRAME-036-zero-descendant-pid",
        test_real_client_disconnect_settles_sql_and_recovers,
        (
            (
                ("scenarios", "client_disconnect_cancellation", "descendant_pids"),
                [0],
            ),
        ),
    ),
    (
        "FRAME-041-wrong-execution-model",
        test_flask_sync_worker_remains_occupied,
        (
            (
                ("scenarios", "flask_sync_occupancy", "execution_model"),
                (
                    "Flask via WsgiToAsgi: persistent ASGI loop, "
                    "thread-sensitive WSGI serialization per process"
                ),
            ),
        ),
    ),
    (
        "FRAME-041-boolean-thread-limit",
        test_flask_sync_worker_remains_occupied,
        (
            (("scenarios", "flask_sync_occupancy", "thread_limit_per_worker"), True),
        ),
    ),
    (
        "FRAME-041-boolean-active-requests",
        test_flask_sync_worker_remains_occupied,
        (
            (("scenarios", "flask_sync_occupancy", "maximum_active_requests"), True),
        ),
    ),
    (
        "FRAME-041-boolean-sql-requests",
        test_flask_sync_worker_remains_occupied,
        (
            (
                (
                    "scenarios",
                    "flask_sync_occupancy",
                    "maximum_simultaneous_sql_requests",
                ),
                True,
            ),
        ),
    ),
    (
        "FRAME-041-boolean-sql-sessions",
        test_flask_sync_worker_remains_occupied,
        (
            (
                (
                    "scenarios",
                    "flask_sync_occupancy",
                    "maximum_aggregate_sql_sessions",
                ),
                True,
            ),
        ),
    ),
    (
        "FRAME-041-boolean-worker-active-requests",
        test_flask_sync_worker_remains_occupied,
        (
            (
                (
                    "scenarios",
                    "flask_sync_occupancy",
                    "worker_execution",
                    0,
                    "maximum_active_requests",
                ),
                True,
            ),
        ),
    ),
    (
        "FRAME-041-boolean-worker-thread-count",
        test_flask_sync_worker_remains_occupied,
        (
            (
                (
                    "scenarios",
                    "flask_sync_occupancy",
                    "worker_execution",
                    0,
                    "wsgi_thread_count",
                ),
                True,
            ),
        ),
    ),
    (
        "FRAME-042-wrong-execution-model",
        test_flask_gthread_has_explicit_thread_occupancy,
        (
            (
                ("scenarios", "flask_gthread_occupancy", "execution_model"),
                "native ASGI: concurrent requests on persistent event loop",
            ),
        ),
    ),
    (
        "FRAME-043-wrong-execution-model",
        test_flask_one_request_can_gather_concurrent_sql,
        (
            (
                ("scenarios", "flask_internal_concurrency", "execution_model"),
                (
                    "Flask via WsgiToAsgi: persistent ASGI loop, "
                    "thread-sensitive WSGI serialization per process"
                ),
            ),
        ),
    ),
    (
        "FRAME-043-boolean-wsgi-slot",
        test_flask_one_request_can_gather_concurrent_sql,
        (
            (("scenarios", "flask_internal_concurrency", "wsgi_request_slots"), True),
        ),
    ),
    (
        "FRAME-044-wrong-execution-model",
        test_adapted_flask_uses_persistent_worker_event_loop,
        (
            (
                ("scenarios", "adapted_flask_persistent_loop", "execution_model"),
                "Flask WSGI: async view, occupied WSGI worker/thread",
            ),
        ),
    ),
    (
        "FRAME-044-boolean-worker-count",
        test_adapted_flask_uses_persistent_worker_event_loop,
        (
            (("scenarios", "adapted_flask_persistent_loop", "workers"), True),
        ),
    ),
    (
        "FRAME-044-boolean-serialized-call-count",
        test_adapted_flask_uses_persistent_worker_event_loop,
        (
            (
                (
                    "scenarios",
                    "adapted_flask_persistent_loop",
                    "maximum_serialized_wsgi_calls_per_process",
                ),
                True,
            ),
        ),
    ),
    (
        "FRAME-044-boolean-sql-request-count",
        test_adapted_flask_uses_persistent_worker_event_loop,
        (
            (
                (
                    "scenarios",
                    "adapted_flask_persistent_loop",
                    "maximum_simultaneous_sql_requests",
                ),
                True,
            ),
        ),
    ),
    (
        "FRAME-044-boolean-sql-session-count",
        test_adapted_flask_uses_persistent_worker_event_loop,
        (
            (
                (
                    "scenarios",
                    "adapted_flask_persistent_loop",
                    "maximum_aggregate_sql_sessions",
                ),
                True,
            ),
        ),
    ),
    (
        "FRAME-045-wrong-execution-model",
        test_adapted_flask_is_thread_sensitive_serialized_per_process,
        (
            (
                ("scenarios", "adapted_flask_serialization", "execution_model"),
                "native ASGI: concurrent requests on persistent event loop",
            ),
        ),
    ),
    (
        "FRAME-045-boolean-wsgi-call-count",
        test_adapted_flask_is_thread_sensitive_serialized_per_process,
        (
            (
                ("scenarios", "adapted_flask_serialization", "wsgi_calls_per_process"),
                True,
            ),
        ),
    ),
    (
        "FRAME-045-boolean-sql-request-count",
        test_adapted_flask_is_thread_sensitive_serialized_per_process,
        (
            (
                (
                    "scenarios",
                    "adapted_flask_serialization",
                    "maximum_simultaneous_sql_requests",
                ),
                True,
            ),
        ),
    ),
    (
        "FRAME-048-self-consistent-forged-digest",
        test_fixed_worker_load_reaches_required_and_extended_profiles,
        (
            (("load_profiles", 0, "value_digest"), "f" * 64),
            (("load_profiles", 0, "expected_value_digest"), "f" * 64),
        ),
    ),
    (
        "FRAME-048-boolean-active-request-count",
        test_fixed_worker_load_reaches_required_and_extended_profiles,
        ((("load_profiles", 0, "maximum_active_requests"), True),),
    ),
    (
        "FRAME-048-boolean-sql-session-count",
        test_fixed_worker_load_reaches_required_and_extended_profiles,
        ((("load_profiles", 0, "maximum_sql_sessions"), True),),
    ),
    (
        "FRAME-048-boolean-sql-request-count",
        test_fixed_worker_load_reaches_required_and_extended_profiles,
        ((("load_profiles", 0, "maximum_sql_requests"), True),),
    ),
    (
        "FRAME-048-boolean-pool-active-count",
        test_fixed_worker_load_reaches_required_and_extended_profiles,
        ((("load_profiles", 0, "maximum_pool_active"), True),),
    ),
    (
        "FRAME-048-boolean-pool-pending-count",
        test_fixed_worker_load_reaches_required_and_extended_profiles,
        ((("load_profiles", 0, "maximum_pool_pending"), True),),
    ),
    (
        "FRAME-048-boolean-duration",
        test_fixed_worker_load_reaches_required_and_extended_profiles,
        ((("load_profiles", 0, "duration_seconds"), True),),
    ),
)


@pytest.mark.parametrize(
    ("case_id", "validator", "mutations"),
    _CASE_VALIDATOR_REVIEW_DEFECTS,
    ids=[contract[0] for contract in _CASE_VALIDATOR_REVIEW_DEFECTS],
)
def test_case_validator_rejects_review_forgery(
    case_id: str,
    validator,
    mutations: tuple[tuple[tuple[object, ...], object], ...],
) -> None:
    """Reject self-consistent evidence forgeries hidden by Python equality."""

    evidence = _runtime_shaped_case_evidence()
    validator(deepcopy(evidence))

    forged = deepcopy(evidence)
    for mutation_path, invalid_value in mutations:
        _replace_case_value(forged, mutation_path, invalid_value)
    assert forged["overall"] == "PASS", case_id
    assert forged["violations"] == [], case_id
    with pytest.raises(AssertionError):
        validator(forged)


@pytest.mark.parametrize("outcome", ("commit", "rollback"))
@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("context_token_sha256", "not-a-sha256"),
        ("item_id", True),
        ("item_id", 0),
        ("item_id", -1),
        ("response_session_id", True),
        ("response_session_id", 0),
        ("response_session_id", -1),
    ),
)
def test_frame_038_rejects_invalid_transaction_provenance(
    outcome: str,
    field_name: str,
    invalid_value: object,
) -> None:
    """Require valid SHA provenance and positive exact IDs for both outcomes."""

    evidence = _runtime_shaped_case_evidence()
    test_graceful_transaction_shutdown_has_deterministic_outcomes(
        deepcopy(evidence)
    )

    forged = deepcopy(evidence)
    forged["scenarios"]["graceful_transaction_shutdown"][outcome][field_name] = (
        invalid_value
    )
    assert forged["overall"] == "PASS"
    assert forged["violations"] == []
    with pytest.raises(AssertionError):
        test_graceful_transaction_shutdown_has_deterministic_outcomes(forged)


_SATURATION_EQUAL_COUNTER_FIELDS = (
    "pool_max_per_worker",
    "admitted_holders",
    "admitted_waiters",
    "maximum_sql_sessions",
    "maximum_sql_requests",
    "observed_active_connections",
    "observed_pending_gets",
    "acquire_timeouts",
    "pool_get_timed_out_delta",
)


@pytest.mark.parametrize(
    "boolean_field",
    _SATURATION_EQUAL_COUNTER_FIELDS,
)
def test_frame_039_rejects_boolean_equality_counter(
    boolean_field: str,
) -> None:
    """Reject each boolean independently even when all relationships agree."""

    evidence = _runtime_shaped_case_evidence()
    test_saturated_pool_has_bounded_admission_and_recovers(deepcopy(evidence))

    forged = deepcopy(evidence)
    scenario = forged["scenarios"]["saturation"]
    for field_name in _SATURATION_EQUAL_COUNTER_FIELDS:
        scenario[field_name] = 1
    scenario["admission_capacity"] = 2
    scenario[boolean_field] = True
    assert forged["overall"] == "PASS"
    assert forged["violations"] == []
    with pytest.raises(AssertionError):
        test_saturated_pool_has_bounded_admission_and_recovers(forged)


@pytest.mark.parametrize(
    ("boolean_field", "invalid_value"),
    (
        ("rejected_requests", True),
        ("pool_active_after", False),
        ("pool_pending_after", False),
        ("admission_active_after", False),
    ),
)
def test_frame_039_rejects_boolean_boundary_counter(
    boolean_field: str,
    invalid_value: bool,
) -> None:
    """Reject booleans accepted by positive and zero integer comparisons."""

    evidence = _runtime_shaped_case_evidence()
    test_saturated_pool_has_bounded_admission_and_recovers(deepcopy(evidence))

    forged = deepcopy(evidence)
    forged["scenarios"]["saturation"][boolean_field] = invalid_value
    assert forged["overall"] == "PASS"
    assert forged["violations"] == []
    with pytest.raises(AssertionError):
        test_saturated_pool_has_bounded_admission_and_recovers(forged)


@pytest.mark.parametrize(
    ("platform_system", "candidate_path", "worker_path"),
    (
        (
            "Linux",
            "/opt/wheel-venv/lib/site-packages/fastmssql/__init__.py",
            "/opt/wheel-venv/lib/site-packages/fastmssql/__init__.py",
        ),
        (
            "Darwin",
            "/private/var/wheel-venv/lib/site-packages/fastmssql/__init__.py",
            "/private/var/wheel-venv/lib/site-packages/fastmssql/__init__.py",
        ),
        (
            "Windows",
            r"C:\Wheel-Venv\Lib\site-packages\fastmssql\__init__.py",
            r"c:\wheel-venv\lib\SITE-PACKAGES\FastMssql\__INIT__.PY",
        ),
    ),
    ids=("linux", "darwin", "windows-case-insensitive"),
)
def test_frame_028_accepts_platform_native_isolated_wheel_paths(
    platform_system: str,
    candidate_path: str,
    worker_path: str,
) -> None:
    """Keep native POSIX and case-insensitive Windows wheel paths portable."""

    evidence = _runtime_shaped_case_evidence()
    _replace_case_value(evidence, ("platform", "system"), platform_system)
    _replace_case_value(
        evidence,
        ("candidate", "wheel", "import_path"),
        candidate_path,
    )
    _replace_case_value(
        evidence,
        ("profiles", 0, "worker_records", 0, "fastmssql_import_path"),
        worker_path,
    )
    test_every_worker_imports_the_exact_isolated_wheel(evidence)


def test_frame_048_accepts_finite_histogram_rounding_noise() -> None:
    """Do not reject a valid accumulated histogram for one floating-point ULP."""

    evidence = _runtime_shaped_case_evidence()
    histogram = evidence["load_profiles"][0]["latency_histogram"]
    histogram["minimum_ms"] = 0.1
    histogram["maximum_ms"] = 0.1
    histogram["sum_ms"] = 100.00000000000001
    test_fixed_worker_load_reaches_required_and_extended_profiles(evidence)


def test_frame_048_rejects_histogram_sum_beyond_ulp_bound() -> None:
    """Reject a positive sum when every recorded latency bound is exactly zero."""

    evidence = _runtime_shaped_case_evidence()
    histogram = evidence["load_profiles"][0]["latency_histogram"]
    histogram["minimum_ms"] = 0.0
    histogram["maximum_ms"] = 0.0
    histogram["sum_ms"] = 1e-10
    with pytest.raises(AssertionError):
        test_fixed_worker_load_reaches_required_and_extended_profiles(evidence)
