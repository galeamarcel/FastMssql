from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
from pathlib import Path

import fastmssql
import pytest


ROOT = Path(__file__).resolve().parents[1]
CORE_STUB = ROOT / "python/fastmssql/fastmssql.pyi"
WRAPPER_STUB = ROOT / "python/fastmssql/__init__.pyi"
README = ROOT / "README.md"
RUST_METRICS = ROOT / "src/operation_metrics.rs"
OPERATION_NAMES = (
    "connect",
    "ping",
    "query",
    "simple_query",
    "execute",
    "query_batch",
    "execute_batch",
    "bulk_insert",
    "begin",
    "commit",
    "rollback",
    "close",
    "disconnect",
)
BUCKET_BOUNDS_SECONDS = (
    0.0001,
    0.00025,
    0.0005,
    0.001,
    0.0025,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
)
ENTRY_KEYS = {
    "started",
    "completed",
    "in_flight",
    "succeeded",
    "errors",
    "timed_out",
    "cancelled",
    "outcome_unknown",
    "duration_seconds_sum",
    "duration_seconds_min",
    "duration_seconds_max",
    "duration_seconds_buckets",
    "saturated",
}


def public_type(name: str):
    assert hasattr(fastmssql, name), f"missing package export {name}"
    assert name in fastmssql.__all__
    return getattr(fastmssql, name)


def assert_zero_snapshot(snapshot: dict[str, object], *, enabled: bool) -> None:
    assert set(snapshot) == {
        "schema_version",
        "enabled",
        "bucket_bounds_seconds",
        "operations",
    }
    assert snapshot["schema_version"] == 1
    assert snapshot["enabled"] is enabled
    assert tuple(snapshot["bucket_bounds_seconds"]) == BUCKET_BOUNDS_SECONDS
    assert tuple(snapshot["operations"]) == OPERATION_NAMES
    for entry in snapshot["operations"].values():
        assert set(entry) == ENTRY_KEYS
        assert entry["started"] == entry["completed"] == 0
        assert entry["in_flight"] == 0
        assert all(
            entry[key] == 0
            for key in (
                "succeeded",
                "errors",
                "timed_out",
                "cancelled",
                "outcome_unknown",
            )
        )
        assert entry["duration_seconds_sum"] == 0.0
        assert entry["duration_seconds_min"] is None
        assert entry["duration_seconds_max"] is None
        assert entry["duration_seconds_buckets"] == [0] * 17
        assert entry["saturated"] is False


def _class_definition(tree: ast.Module, name: str) -> ast.ClassDef:
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    ]
    assert len(matches) == 1, f"expected one {name}, found {len(matches)}"
    return matches[0]


def _annotated_fields(class_node: ast.ClassDef) -> dict[str, str]:
    return {
        node.target.id: ast.unparse(node.annotation)
        for node in class_node.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }


def _method(class_node: ast.ClassDef, name: str) -> ast.FunctionDef:
    matches = [
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(matches) == 1, (
        f"expected one {class_node.name}.{name}, found {len(matches)}"
    )
    return matches[0]


def _assert_stub_contract(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    entry = _class_definition(tree, "_OperationStatsEntry")
    by_name = _class_definition(tree, "_OperationStatsByName")
    snapshot = _class_definition(tree, "_OperationStatsSnapshot")
    config = _class_definition(tree, "OperationMetricsConfig")
    connection = _class_definition(tree, "Connection")

    assert _annotated_fields(entry) == {
        "started": "int",
        "completed": "int",
        "in_flight": "int",
        "succeeded": "int",
        "errors": "int",
        "timed_out": "int",
        "cancelled": "int",
        "outcome_unknown": "int",
        "duration_seconds_sum": "float",
        "duration_seconds_min": "Optional[float]",
        "duration_seconds_max": "Optional[float]",
        "duration_seconds_buckets": "List[int]",
        "saturated": "bool",
    }
    assert _annotated_fields(by_name) == {
        operation: "_OperationStatsEntry" for operation in OPERATION_NAMES
    }
    assert _annotated_fields(snapshot) == {
        "schema_version": "Literal[1]",
        "enabled": "bool",
        "bucket_bounds_seconds": "List[float]",
        "operations": "_OperationStatsByName",
    }
    assert _annotated_fields(config) == {"enabled": "bool"}

    config_init = _method(config, "__init__")
    assert [argument.arg for argument in config_init.args.args] == [
        "self",
        "enabled",
    ]
    assert len(config_init.args.defaults) == 1
    assert (
        ast.unparse(config_init.args.defaults[0]) == "False"
        and ast.unparse(config_init.args.args[-1].annotation) == "bool"
    )

    connection_init = _method(connection, "__init__")
    assert connection_init.args.args[-1].arg == "operation_metrics_config"
    assert ast.unparse(connection_init.args.args[-1].annotation) == (
        "Optional[OperationMetricsConfig]"
    )
    assert ast.unparse(connection_init.args.defaults[-1]) == "None"

    config_property = _method(connection, "operation_metrics_config")
    assert any(
        isinstance(decorator, ast.Name) and decorator.id == "property"
        for decorator in config_property.decorator_list
    )
    assert ast.unparse(config_property.returns) == "OperationMetricsConfig"

    stats_method = _method(connection, "operation_stats")
    assert ast.unparse(stats_method.returns) == (
        "Coroutine[Any, Any, _OperationStatsSnapshot]"
    )


def test_operation_metrics_config_exact_runtime_contract() -> None:
    config_type = public_type("OperationMetricsConfig")
    assert config_type.__text_signature__ == "(enabled=False)"
    signature = inspect.signature(config_type)
    assert tuple(signature.parameters) == ("enabled",)
    assert signature.parameters["enabled"].default is False
    assert repr(config_type()) == "OperationMetricsConfig(enabled=False)"
    assert repr(config_type(enabled=True)) == ("OperationMetricsConfig(enabled=True)")
    for invalid in (0, 1, "true", None, object()):
        with pytest.raises(TypeError):
            config_type(enabled=invalid)
        mutable = config_type()
        with pytest.raises(TypeError):
            mutable.enabled = invalid
        assert mutable.enabled is False


def test_connection_appends_and_copies_operation_metrics_config() -> None:
    config_type = public_type("OperationMetricsConfig")
    core = importlib.import_module("fastmssql.fastmssql")
    parameters = tuple(inspect.signature(core.Connection).parameters.values())
    assert parameters[-1].name == "operation_metrics_config"
    assert parameters[-1].default is None

    external = config_type(enabled=True)
    connection = fastmssql.Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="contract",
        password="not-used",
        ssl_config=fastmssql.SslConfig.development(),
        operation_metrics_config=external,
    )
    external.enabled = False
    assert connection.operation_metrics_config.enabled is True
    exposed = connection.operation_metrics_config
    exposed.enabled = False
    assert connection.operation_metrics_config.enabled is True
    with pytest.raises(AttributeError):
        connection.operation_metrics_config = config_type(enabled=False)


async def disabled_snapshots() -> tuple[
    dict[str, object],
    dict[str, object],
]:
    connection = fastmssql.Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="contract",
        password="not-used",
        ssl_config=fastmssql.SslConfig.development(),
    )
    first = await connection.operation_stats()
    first["bucket_bounds_seconds"].append(99.0)
    first["operations"]["query"]["started"] = 99
    first["operations"]["query"]["duration_seconds_buckets"].append(99)
    second = await connection.operation_stats()
    return first, second


def test_disabled_snapshot_is_exact_zero_and_mutation_isolated() -> None:
    first, second = asyncio.run(disabled_snapshots())
    assert first["bucket_bounds_seconds"][-1] == 99.0
    assert first["operations"]["query"]["started"] == 99
    assert_zero_snapshot(second, enabled=False)


def test_core_stub_publishes_exact_operation_metrics_contract() -> None:
    _assert_stub_contract(CORE_STUB)


def test_wrapper_stub_publishes_exact_operation_metrics_contract() -> None:
    _assert_stub_contract(WRAPPER_STUB)


def test_disabled_branch_precedes_clock_and_atomic_recording() -> None:
    assert RUST_METRICS.is_file(), "missing src/operation_metrics.rs"
    source = RUST_METRICS.read_text(encoding="utf-8")
    observer_impl = source.index("impl OperationObserver")
    observer_start = source.index("pub(crate) fn start(", observer_impl)
    disabled_gate = source.index(
        "let guard = metrics.and_then(|registry| {",
        observer_start,
    )
    clock = source.index("Instant::now()", disabled_gate)
    observer_finish = source.index("    fn finish(", observer_start)
    assert disabled_gate < clock < observer_finish

    observe_operation = source.index(
        "pub(crate) async fn observe_operation"
    )
    assert (
        "OperationObserver::start(metrics, operation)"
        in source[observe_operation:]
    )
    assert "Python callback" not in source
    assert "opentelemetry" not in source.lower()


def test_wrapper_stubs_and_readme_publish_operation_metrics() -> None:
    wrapper = (ROOT / "python/fastmssql/__init__.py").read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    for token in (
        "OperationMetricsConfig",
        "operation_metrics_config",
        "operation_stats",
        "outcome_unknown",
        "duration_seconds_buckets",
        "connection lifetime",
        "weakly consistent",
    ):
        assert token in wrapper or token in readme
