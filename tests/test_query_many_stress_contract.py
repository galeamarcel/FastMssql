from __future__ import annotations

import argparse
import ast
import asyncio
import json
from pathlib import Path
import runpy
import subprocess
import sys
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/sql_auth/query_many_stress.py"


def _namespace() -> dict[str, object]:
    assert RUNNER.is_file(), "missing query-many stress harness"
    return runpy.run_path(str(RUNNER))


def _profile_values(profiles: tuple[object, ...]) -> list[tuple[object, ...]]:
    return [
        (
            profile.operations,
            profile.source,
            profile.ordered,
            profile.requested_concurrency,
            profile.pool_max,
        )
        for profile in profiles
    ]


def _baseline_metrics() -> dict[str, Any]:
    return {
        "operations": 3,
        "source": "sync",
        "ordered": True,
        "requested_concurrency": 5,
        "pool_max": 2,
        "effective_concurrency": 2,
        "producer_pulls": 3,
        "yielded_ids": [0, 1, 2],
        "maximum_accepted_window": 2,
        "maximum_active_queries": 2,
        "maximum_pool_connections": 2,
        "maximum_sql_sessions": 2,
        "operation_schema_version": 2,
        "query_metric_delta": {
            "started": 3,
            "completed": 3,
            "succeeded": 3,
            "errors": 0,
            "timed_out": 0,
            "cancelled": 0,
            "outcome_unknown": 0,
        },
        "operation_metric_names": ["query"],
        "rss_growth_bytes": 1_024,
        "maximum_event_loop_gap_seconds": 0.01,
        "post_load_smoke": True,
        "teardown_sessions": 0,
        "unhandled_task_exceptions": [],
        "errors": [],
    }


def test_query_many_stress_cli_profiles_are_strict_and_extended_is_explicit(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [sys.executable, str(RUNNER), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    for option in (
        "--profiles",
        "--allow-extended",
        "--metrics-output",
        "--rss-growth-limit-bytes",
        "--event-loop-gap-limit-seconds",
        "--operation-timeout-seconds",
    ):
        assert option in completed.stdout

    namespace = _namespace()
    parse_profiles = namespace["parse_profiles"]
    profiles = parse_profiles(
        "1_000:sync:true:10:10,"
        "1_000:async:false:25:8,"
        "10_000:sync:false:50:16,"
        "10_000:async:true:100:16"
    )
    assert _profile_values(profiles) == [
        (1_000, "sync", True, 10, 10),
        (1_000, "async", False, 25, 8),
        (10_000, "sync", False, 50, 16),
        (10_000, "async", True, 100, 16),
    ]

    for invalid in (
        "0:sync:true:1:1",
        "100_000:sync:true:1:1",
        "1_000:thread:true:1:1",
        "1_000:sync:yes:1:1",
        "1_000:sync:true:0:1",
        "1_000:sync:true:10_001:1",
        "1_000:sync:true:1:0",
        "1_000:sync:true:1",
        "1_000:sync:true:10:10,1_000:sync:true:10:10",
    ):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_profiles(invalid)

    parse_args = namespace["parse_args"]
    defaults = parse_args(
        ["--metrics-output", str(tmp_path / "defaults.json")]
    )
    assert _profile_values(defaults.profiles) == [
        (1_000, "sync", True, 10, 10),
        (1_000, "async", False, 25, 8),
        (10_000, "sync", False, 50, 16),
        (10_000, "async", True, 100, 16),
    ]
    baseline = parse_args(
        [
            "--profiles",
            "1_000:sync:true:10:10",
            "--metrics-output",
            str(tmp_path / "baseline.json"),
        ]
    )
    assert baseline.allow_extended is False
    assert baseline.metrics_output == (tmp_path / "baseline.json").resolve()
    with pytest.raises(SystemExit):
        parse_args(
            [
                "--profiles",
                "99_999:sync:true:200:32",
                "--metrics-output",
                str(tmp_path / "rejected.json"),
            ]
        )
    extended = parse_args(
        [
            "--profiles",
            "99_999:sync:true:200:32,99_999:async:false:500:32",
            "--allow-extended",
            "--metrics-output",
            str(tmp_path / "extended.json"),
        ]
    )
    assert extended.allow_extended is True


@pytest.mark.asyncio
async def test_query_many_stress_producers_are_lazy_and_track_one_window() -> None:
    namespace = _namespace()
    tracker = namespace["AdmissionTracker"]()
    sync_producer = namespace["SyncProducer"](3, tracker)

    sync_iterator = iter(sync_producer)
    assert sync_producer.pulls == 0
    assert next(sync_iterator) == [0]
    assert next(sync_iterator) == [1]
    assert sync_producer.pulls == 2
    assert tracker.current == 2
    assert tracker.maximum == 2
    tracker.mark_yielded()
    assert tracker.current == 1

    async_tracker = namespace["AdmissionTracker"]()
    async_producer = namespace["AsyncProducer"](2, async_tracker)
    async_iterator = aiter(async_producer)
    assert async_producer.pulls == 0
    assert await anext(async_iterator) == [0]
    assert async_producer.pulls == 1
    assert async_tracker.current == 1
    async_tracker.mark_yielded()
    assert async_tracker.current == 0


def test_query_many_stress_source_has_no_operation_sized_fanout() -> None:
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"), filename=str(RUNNER))

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "gather"
        ):
            assert not any(
                isinstance(
                    argument,
                    (ast.ListComp, ast.SetComp, ast.GeneratorExp),
                )
                or (
                    isinstance(argument, ast.Starred)
                    and isinstance(
                        argument.value,
                        (ast.ListComp, ast.SetComp, ast.GeneratorExp),
                    )
                )
                for argument in node.args
            )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "list"
            and node.args
            and isinstance(node.args[0], ast.Name)
        ):
            assert node.args[0].id not in {
                "parameter_sets",
                "producer",
                "results",
            }
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Queue"
        ):
            maxsize = (
                node.args[0]
                if node.args
                else next(
                    (
                        keyword.value
                        for keyword in node.keywords
                        if keyword.arg == "maxsize"
                    ),
                    None,
                )
            )
            assert maxsize is not None
            if isinstance(maxsize, ast.Constant):
                assert maxsize.value != 0
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"repr", "str"}
            and node.args
            and isinstance(node.args[0], ast.Name)
        ):
            assert node.args[0].id != "error"

    producer_classes = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name in {"SyncProducer", "AsyncProducer"}
    }
    assert set(producer_classes) == {"SyncProducer", "AsyncProducer"}
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_task"
        for producer in producer_classes.values()
        for node in ast.walk(producer)
    )


def test_query_many_stress_hard_gates_reject_every_enterprise_violation() -> None:
    namespace = _namespace()
    profile_violations = namespace["profile_violations"]
    limits = {
        "rss_growth_limit_bytes": 134_217_728,
        "event_loop_gap_limit_seconds": 0.100,
    }
    baseline = _baseline_metrics()
    assert profile_violations(baseline, **limits) == []

    mutations: tuple[tuple[str, object, str], ...] = (
        ("yielded_ids", [0, 0, 2], "missing_or_duplicate_ids"),
        ("yielded_ids", [1, 0, 2], "ordered_output_mismatch"),
        ("maximum_accepted_window", 3, "accepted_window_exceeded"),
        ("maximum_active_queries", 3, "active_query_bound_exceeded"),
        ("maximum_pool_connections", 3, "pool_bound_exceeded"),
        (
            "query_metric_delta",
            {
                **baseline["query_metric_delta"],
                "started": 2,
            },
            "query_metric_mismatch",
        ),
        (
            "operation_metric_names",
            ["query", "query_many"],
            "unexpected_query_many_metric",
        ),
        ("operation_schema_version", 3, "schema_version_mismatch"),
        ("rss_growth_bytes", 134_217_729, "rss_growth_exceeded"),
        (
            "maximum_event_loop_gap_seconds",
            0.101,
            "event_loop_gap_exceeded",
        ),
        ("post_load_smoke", False, "post_load_smoke_failed"),
        ("teardown_sessions", 1, "teardown_session_leak"),
        (
            "unhandled_task_exceptions",
            ["RuntimeError"],
            "unhandled_task_exception",
        ),
    )
    for key, value, expected in mutations:
        metrics = _baseline_metrics()
        metrics[key] = value
        assert expected in profile_violations(metrics, **limits)


def test_query_many_stress_artifact_is_atomic_and_privacy_safe(
    tmp_path: Path,
) -> None:
    namespace = _namespace()
    output = tmp_path / "nested" / "query-many.json"
    payload = {
        "schema_version": 1,
        "status": "contract",
        "source_sha": "0" * 40,
        "worktree_dirty": False,
        "profiles": [],
    }

    namespace["atomic_write"](output, payload)

    assert json.loads(output.read_text(encoding="utf-8")) == payload
    assert list(output.parent.glob(".*.tmp")) == []
    source = RUNNER.read_text(encoding="utf-8")
    assert "owner_password" not in json.dumps(payload)
    assert "sa_password" not in json.dumps(payload)
    assert "repr(error)" not in source
    assert "str(error)" not in source


def test_cumulative_vendor_build_artifacts_preserve_clean_source_evidence() -> None:
    for relative_path in (
        "vendor/tiberius/Cargo.lock",
        "vendor/tiberius/target/.query-many-cleanliness-probe",
    ):
        completed = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", relative_path],
            cwd=ROOT,
            check=False,
        )
        assert completed.returncode == 0, (
            f"cumulative Tiberius build artifact is not ignored: {relative_path}"
        )


@pytest.mark.asyncio
async def test_query_many_stress_cleanup_reports_monitor_failures() -> None:
    namespace = _namespace()
    settle_monitor_tasks = namespace["settle_monitor_tasks"]

    async def failed_monitor() -> None:
        raise RuntimeError("private sentinel")

    task = asyncio.create_task(failed_monitor())
    failures = await settle_monitor_tasks([task])

    assert failures == ["RuntimeError"]
