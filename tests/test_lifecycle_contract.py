from __future__ import annotations

import ast
import importlib
import inspect
import math
from pathlib import Path

import fastmssql
import pytest


ROOT = Path(__file__).resolve().parents[1]
CORE_STUB = ROOT / "python/fastmssql/fastmssql.pyi"
WRAPPER_STUB = ROOT / "python/fastmssql/__init__.pyi"
README = ROOT / "README.md"
DEFAULTS = {
    "shutdown_timeout_secs": 30.0,
    "force_timeout_secs": 5.0,
}
TEXT_SIGNATURE = "(shutdown_timeout_secs=30.0, force_timeout_secs=5.0)"
INVALID = (True, False, None, 0, -0.1, math.nan, math.inf, -math.inf)
UNREPRESENTABLE = (5e-324, 1e-12, 0.5e-9, 1e19)
PORTABLE_CEILING = 100 * 365 * 24 * 60 * 60


def public_type(name: str):
    assert hasattr(fastmssql, name), f"missing package export {name}"
    assert name in fastmssql.__all__
    return getattr(fastmssql, name)


def class_node(path: Path, name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def test_lifecycle_config_defaults_signature_repr_and_validation() -> None:
    lifecycle_type = public_type("LifecycleConfig")
    assert lifecycle_type.__text_signature__ == TEXT_SIGNATURE
    signature = inspect.signature(lifecycle_type)
    assert tuple(signature.parameters) == tuple(DEFAULTS)
    assert {
        name: parameter.default
        for name, parameter in signature.parameters.items()
    } == DEFAULTS

    config = lifecycle_type()
    assert {name: getattr(config, name) for name in DEFAULTS} == DEFAULTS
    assert repr(config) == (
        "LifecycleConfig(shutdown_timeout_secs=30.0, "
        "force_timeout_secs=5.0)"
    )

    for field in DEFAULTS:
        for value in (*INVALID, *UNREPRESENTABLE):
            with pytest.raises(ValueError):
                lifecycle_type(**{field: value})
            mutable = lifecycle_type()
            original = getattr(mutable, field)
            with pytest.raises(ValueError):
                setattr(mutable, field, value)
            assert getattr(mutable, field) == original

        mutable = lifecycle_type()
        setattr(mutable, field, 0.125)
        assert getattr(mutable, field) == 0.125

        boundary = lifecycle_type(**{field: PORTABLE_CEILING})
        assert getattr(boundary, field) == PORTABLE_CEILING
        with pytest.raises(ValueError, match="100 years"):
            lifecycle_type(**{field: PORTABLE_CEILING + 1})


def test_lifecycle_state_and_errors_are_public_and_typed() -> None:
    state = public_type("ConnectionLifecycleState")
    lifecycle_error = public_type("ConnectionLifecycleError")
    shutdown_error = public_type("ShutdownTimeoutError")
    assert str(state.OPEN) == "Open"
    assert str(state.CLOSING) == "Closing"
    assert str(state.CLOSED) == "Closed"
    assert issubclass(lifecycle_error, fastmssql.SqlConnectionError)
    assert issubclass(shutdown_error, lifecycle_error)


def test_compiled_connection_preserves_lifecycle_before_additive_config() -> None:
    core = importlib.import_module("fastmssql.fastmssql")
    parameters = tuple(inspect.signature(core.Connection).parameters.values())
    assert [
        (parameter.name, parameter.default)
        for parameter in parameters[-2:]
    ] == [
        ("lifecycle_config", None),
        ("operation_metrics_config", None),
    ]


def test_lifecycle_stubs_and_readme_match_runtime_contract() -> None:
    for name in (
        "LifecycleConfig",
        "ConnectionLifecycleState",
        "ConnectionLifecycleError",
        "ShutdownTimeoutError",
    ):
        class_node(CORE_STUB, name)
    for stub in (CORE_STUB, WRAPPER_STUB):
        text = stub.read_text(encoding="utf-8")
        assert "lifecycle_config: Optional[LifecycleConfig] = None" in text
        assert "def lifecycle_config(self) -> LifecycleConfig" in text
        assert (
            "def lifecycle_state(self) -> ConnectionLifecycleState" in text
        )
    readme = README.read_text(encoding="utf-8")
    for token in (
        "Open",
        "Closing",
        "Closed",
        "shutdown_timeout_secs",
        "force_timeout_secs",
        "ShutdownTimeoutError",
    ):
        assert token in readme


def test_connection_stores_an_isolated_lifecycle_config() -> None:
    external = fastmssql.LifecycleConfig(
        shutdown_timeout_secs=0.5,
        force_timeout_secs=0.25,
    )
    connection = fastmssql.Connection(
        server="localhost",
        database="master",
        username="test",
        password="secret",
        ssl_config=fastmssql.SslConfig.development(),
        lifecycle_config=external,
    )
    external.shutdown_timeout_secs = 9.0
    exposed = connection.lifecycle_config
    assert exposed.shutdown_timeout_secs == 0.5
    exposed.shutdown_timeout_secs = 8.0
    assert connection.lifecycle_config.shutdown_timeout_secs == 0.5
    with pytest.raises(AttributeError):
        connection.lifecycle_config = fastmssql.LifecycleConfig()


def test_new_connection_is_open_but_not_connected() -> None:
    connection = fastmssql.Connection(
        server="localhost",
        database="master",
        username="test",
        password="secret",
        ssl_config=fastmssql.SslConfig.development(),
    )
    assert (
        connection.lifecycle_state
        == fastmssql.ConnectionLifecycleState.OPEN
    )
