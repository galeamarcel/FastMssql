from __future__ import annotations

import argparse
import ast
import gc
import json
from pathlib import Path
import runpy
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/sql_auth/native_bulk_iterable_stress.py"


def _namespace() -> dict[str, object]:
    return runpy.run_path(str(RUNNER))


def test_iterable_stress_cli_has_bounded_profiles_and_explicit_extended(
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
        "--modes",
        "--allow-extended",
        "--metrics-output",
        "--rss-growth-limit-bytes",
        "--event-loop-gap-limit-seconds",
        "--operation-timeout-seconds",
        "--payload-bytes",
    ):
        assert option in completed.stdout

    namespace = _namespace()
    parse_profiles = namespace["parse_profiles"]
    profiles = parse_profiles("1_000:100,10_000:1_000,99_999:1_000")
    assert [(profile.rows, profile.chunk_size) for profile in profiles] == [
        (1_000, 100),
        (10_000, 1_000),
        (99_999, 1_000),
    ]
    for invalid in (
        "0:1",
        "100_000:1_000",
        "1_000:0",
        "1_000:10_001",
        "1_000",
        "1_000:100,1_000:100",
    ):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_profiles(invalid)

    parse_modes = namespace["parse_modes"]
    assert parse_modes("sync,async") == ("sync", "async")
    for invalid in ("", "sync,sync", "threaded"):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_modes(invalid)

    parse_args = namespace["parse_args"]
    baseline = parse_args(
        [
            "--profiles",
            "1_000:100,10_000:1_000",
            "--modes",
            "sync,async",
            "--metrics-output",
            str(tmp_path / "baseline.json"),
        ]
    )
    assert baseline.allow_extended is False
    with pytest.raises(SystemExit):
        parse_args(
            [
                "--profiles",
                "99_999:1_000",
                "--metrics-output",
                str(tmp_path / "rejected.json"),
            ]
        )
    extended = parse_args(
        [
            "--profiles",
            "99_999:1_000",
            "--allow-extended",
            "--metrics-output",
            str(tmp_path / "extended.json"),
        ]
    )
    assert extended.allow_extended is True


def test_iterable_stress_producers_are_lazy_and_track_live_chunk_cells() -> None:
    namespace = _namespace()
    tracker = namespace["BufferTracker"]()
    producer = namespace["SyncProducer"](3, "payload", tracker)

    iterator = iter(producer)
    assert producer.pulls == 0
    first = next(iterator)
    second = next(iterator)
    assert producer.pulls == 2
    assert tracker.current == 2
    assert tracker.maximum == 2
    assert first[0] == 0
    assert second[0] == 1
    del first
    del second
    gc.collect()
    assert tracker.current == 0

    tree = ast.parse(RUNNER.read_text(encoding="utf-8"), filename=str(RUNNER))
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "list"
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id in {"rows", "producer"}
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.FunctionDef) and node.name == "build_rows"
        for node in ast.walk(tree)
    )


def test_iterable_stress_hard_gates_are_not_advisory() -> None:
    namespace = _namespace()
    expected_summary = namespace["expected_summary"]
    profile_violations = namespace["profile_violations"]
    metrics = {
        "row_count": 1_000,
        "chunk_size": 100,
        "affected_rows": 1_000,
        "persisted": expected_summary(1_000),
        "producer_pulls": 1_000,
        "maximum_buffered_rows": 100,
        "buffered_rows_after_gc": 0,
        "rss_growth_bytes": 1_024,
        "maximum_event_loop_gap_seconds": 0.01,
        "event_loop_ticks": 10,
        "maximum_sql_sessions": 1,
        "physical_identity_stable": True,
        "operation_metric_delta": {
            "started": 1,
            "completed": 1,
            "succeeded": 1,
            "errors": 0,
            "timed_out": 0,
            "cancelled": 0,
            "outcome_unknown": 0,
        },
        "pool": {
            "max_size": 1,
            "connections": 1,
            "active_connections": 0,
        },
        "post_load_smoke": True,
        "teardown_sessions": 0,
        "errors": [],
        "timed_out": 0,
    }
    assert (
        profile_violations(
            metrics,
            rss_growth_limit_bytes=67_108_864,
            event_loop_gap_limit_seconds=0.100,
        )
        == []
    )

    metrics["maximum_buffered_rows"] = 101
    metrics["rss_growth_bytes"] = 67_108_865
    metrics["maximum_event_loop_gap_seconds"] = 0.101
    metrics["teardown_sessions"] = 1
    assert set(
        profile_violations(
            metrics,
            rss_growth_limit_bytes=67_108_864,
            event_loop_gap_limit_seconds=0.100,
        )
    ) >= {
        "buffer_bound_exceeded",
        "rss_growth_exceeded",
        "event_loop_gap_exceeded",
        "teardown_session_leak",
    }


def test_iterable_stress_artifact_is_atomic_and_privacy_safe(
    tmp_path: Path,
) -> None:
    namespace = _namespace()
    output = tmp_path / "nested" / "metrics.json"
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
    assert "observer_password" not in json.dumps(payload)
    assert "repr(error)" not in source
    assert "str(error)" not in source


def test_iterable_stress_timeout_and_cleanup_contract_is_unmasking() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(RUNNER))
    handlers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
    ]

    assert any(
        isinstance(handler.type, ast.Name)
        and handler.type.id == "OperationTimeoutError"
        for handler in handlers
    )
    assert not any(
        isinstance(handler.type, ast.Name)
        and handler.type.id == "BaseException"
        for handler in handlers
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "gather"
        and any(
            keyword.arg == "return_exceptions"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in node.keywords
        )
        for node in ast.walk(tree)
    )

    run_profile = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "run_profile"
    )
    run_profile_source = ast.get_source_segment(source, run_profile)
    assert run_profile_source is not None
    assert run_profile_source.index("await connection.disconnect()") < (
        run_profile_source.index("DROP TABLE IF EXISTS")
    )
