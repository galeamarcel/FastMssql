from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import json
from pathlib import Path
import runpy
import subprocess
import sys

import fastmssql
import pytest


ROOT = Path(__file__).resolve().parents[1]
POSITIONAL = ("self", "table", "columns", "rows")
KEYWORD_ONLY = ("chunk_size",)


def _tree(path: Path) -> ast.Module:
    assert path.is_file(), f"missing native-bulk public artifact: {path}"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    ]
    assert len(matches) == 1, f"expected one class {name}"
    return matches[0]


def _method(
    class_node: ast.ClassDef,
    name: str,
    *,
    asynchronous: bool,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    expected = ast.AsyncFunctionDef if asynchronous else ast.FunctionDef
    matches = [
        node
        for node in class_node.body
        if isinstance(node, expected) and node.name == name
    ]
    assert len(matches) == 1, (
        f"expected one {'async ' if asynchronous else ''}{class_node.name}.{name}"
    )
    return matches[0]


def _assert_runtime_signature(owner: type) -> None:
    assert hasattr(owner, "native_bulk_insert"), (
        f"missing runtime method {owner.__name__}.native_bulk_insert"
    )
    signature = inspect.signature(owner.native_bulk_insert)
    assert tuple(signature.parameters) == POSITIONAL + KEYWORD_ONLY
    for name in POSITIONAL:
        assert signature.parameters[name].kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
    chunk_size = signature.parameters["chunk_size"]
    assert chunk_size.kind is inspect.Parameter.KEYWORD_ONLY
    assert chunk_size.default == 1000


def _assert_stub_signature(class_node: ast.ClassDef) -> None:
    method = _method(
        class_node,
        "native_bulk_insert",
        asynchronous=False,
    )
    assert tuple(argument.arg for argument in method.args.args) == POSITIONAL
    assert tuple(argument.arg for argument in method.args.kwonlyargs) == KEYWORD_ONLY
    assert len(method.args.kw_defaults) == 1
    assert ast.literal_eval(method.args.kw_defaults[0]) == 1000
    assert tuple(
        ast.unparse(argument.annotation) for argument in method.args.args[1:]
    ) == (
        "str",
        "list[str]",
        "list[list[Any]]",
    )
    assert ast.unparse(method.args.kwonlyargs[0].annotation) == "int"
    assert ast.unparse(method.returns) == "Coroutine[Any, Any, int]"


def _assert_wrapper_signature(class_node: ast.ClassDef) -> None:
    method = _method(
        class_node,
        "native_bulk_insert",
        asynchronous=True,
    )
    assert tuple(argument.arg for argument in method.args.args) == POSITIONAL
    assert tuple(argument.arg for argument in method.args.kwonlyargs) == KEYWORD_ONLY
    assert len(method.args.kw_defaults) == 1
    assert ast.literal_eval(method.args.kw_defaults[0]) == 1000

    calls = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "native_bulk_insert"
    ]
    assert len(calls) == 1


def test_native_bulk_runtime_has_exact_list_only_surface() -> None:
    core = importlib.import_module("fastmssql.fastmssql")

    for owner in (
        fastmssql.Connection,
        fastmssql.Transaction,
        core.Connection,
        core.Transaction,
    ):
        _assert_runtime_signature(owner)


def test_native_bulk_is_explicit_on_both_wrapper_classes() -> None:
    wrapper = _tree(ROOT / "python/fastmssql/__init__.py")
    for class_name in ("Connection", "Transaction"):
        _assert_wrapper_signature(_class(wrapper, class_name))


def test_native_bulk_is_exact_in_both_stub_layers() -> None:
    for path in (
        ROOT / "python/fastmssql/__init__.pyi",
        ROOT / "python/fastmssql/fastmssql.pyi",
    ):
        tree = _tree(path)
        for class_name in ("Connection", "Transaction"):
            _assert_stub_signature(_class(tree, class_name))


def test_native_bulk_source_uses_tds_rows_and_never_values_sql() -> None:
    path = ROOT / "src/native_bulk.rs"
    assert path.is_file(), f"missing dedicated native-bulk engine: {path}"
    source = path.read_text(encoding="utf-8")

    for required in (
        "bulk_insert_columns",
        "column_declarations",
        "parse_sql_parameter_type",
        "TokenRow::with_capacity",
        ".push(",
        ".send(",
        ".finalize(",
    ):
        assert required in source

    upper = source.upper()
    assert "INSERT INTO" not in upper
    assert " VALUES " not in upper


def test_native_bulk_transaction_source_has_rollback_only_state() -> None:
    transaction = (ROOT / "src/transaction.rs").read_text(encoding="utf-8")
    assert "RollbackOnly" in transaction
    assert "rollback required" in transaction.lower()


def test_native_bulk_stress_harness_is_bounded_and_extended_is_explicit(
    tmp_path: Path,
) -> None:
    runner = ROOT / "scripts/sql_auth/native_bulk_stress.py"
    completed = subprocess.run(
        [sys.executable, str(runner), "--help"],
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
        "--event-loop-stall-limit-seconds",
        "--operation-timeout-seconds",
    ):
        assert option in completed.stdout

    namespace = runpy.run_path(str(runner))
    parse_profiles = namespace["parse_profiles"]
    profiles = parse_profiles("1_000:250,10_000:1_000,99_999:1_000")
    assert [(profile.rows, profile.chunk_size) for profile in profiles] == [
        (1_000, 250),
        (10_000, 1_000),
        (99_999, 1_000),
    ]
    for invalid in (
        "0:1",
        "100_000:1_000",
        "1_000:0",
        "1_000:10_001",
        "1_000",
        "1_000:250,1_000:250",
    ):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_profiles(invalid)

    parse_args = namespace["parse_args"]
    baseline = parse_args(
        [
            "--profiles",
            "1_000:250,10_000:1_000",
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

    nearest_rank = namespace["nearest_rank"]
    assert nearest_rank([50, 10, 40, 20, 30], 50) == 30
    assert nearest_rank([50, 10, 40, 20, 30], 95) == 50

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


def test_native_bulk_stress_builds_list_rows_for_the_list_only_api() -> None:
    runner = ROOT / "scripts/sql_auth/native_bulk_stress.py"
    namespace = runpy.run_path(str(runner))

    rows = namespace["build_rows"](3)

    assert rows == [
        [0, "native-bulk-0", 0],
        [1, "native-bulk-1", 1],
        [2, "native-bulk-2", 2],
    ]
    assert isinstance(rows, list)
    assert all(isinstance(row, list) for row in rows)


def test_native_bulk_stress_reports_unavailable_failure_metrics() -> None:
    runner = ROOT / "scripts/sql_auth/native_bulk_stress.py"
    namespace = runpy.run_path(str(runner))
    metrics = {
        "rows": 1_000,
        "chunk_size": 250,
        "errors": ["ConversionError"],
        "timed_out": 0,
        "primary_single_call": {
            "affected_rows": None,
            "persisted": None,
            "physical_identity_stable": False,
        },
        "chunk_call_probe": {
            "affected_rows": None,
            "persisted": None,
            "call_count": 0,
            "physical_identity_stable": False,
        },
        "resources": {
            "rss_growth_bytes": None,
            "rss_growth_limit_bytes": 1024,
            "event_loop_ticks": 0,
            "maximum_event_loop_stall_seconds": None,
            "event_loop_stall_limit_seconds": 0.1,
            "maximum_sql_sessions": 0,
        },
        "pool": {
            "maximum_size": 1,
            "connections": 0,
            "final_active_connections": 0,
        },
        "post_load_smoke": False,
        "teardown_sessions": 0,
    }

    violations = namespace["profile_violations"](
        metrics,
        rss_growth_limit_bytes=1024,
        event_loop_stall_limit_seconds=0.1,
    )

    assert "operation_error" in violations
    assert "resource_metrics_unavailable" in violations
