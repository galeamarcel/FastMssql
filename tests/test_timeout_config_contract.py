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
    "connect_timeout_secs": 30.0,
    "acquire_timeout_secs": 30.0,
    "operation_timeout_secs": None,
    "transaction_timeout_secs": None,
    "rollback_timeout_secs": 30.0,
}
TEXT_SIGNATURE = (
    "(connect_timeout_secs=30.0, acquire_timeout_secs=30.0, "
    "operation_timeout_secs=None, transaction_timeout_secs=None, "
    "rollback_timeout_secs=30.0)"
)
INVALID_TIMEOUT_VALUES = (
    True,
    False,
    0,
    0.0,
    -0.1,
    math.nan,
    math.inf,
    -math.inf,
)
UNREPRESENTABLE_TIMEOUT_VALUES = (
    5e-324,
    1e-12,
    0.5e-9,
    1e19,
)
PORTABLE_TIMEOUT_CEILING_SECS = 100 * 365 * 24 * 60 * 60


def _public_type(name: str):
    assert hasattr(fastmssql, name), f"missing package export {name}"
    assert name in fastmssql.__all__
    return getattr(fastmssql, name)


def _class_node(path: Path, name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    ]
    assert len(matches) == 1, f"missing or duplicate stub class {name}"
    return matches[0]


def test_timeout_config_exact_defaults_signature_and_repr() -> None:
    timeout_type = _public_type("TimeoutConfig")
    signature = inspect.signature(timeout_type)
    assert timeout_type.__text_signature__ == TEXT_SIGNATURE
    assert tuple(signature.parameters) == tuple(DEFAULTS)
    assert {
        name: parameter.default
        for name, parameter in signature.parameters.items()
    } == DEFAULTS

    config = timeout_type()
    assert {name: getattr(config, name) for name in DEFAULTS} == DEFAULTS
    assert repr(config) == (
        "TimeoutConfig(connect_timeout_secs=30.0, "
        "acquire_timeout_secs=30.0, operation_timeout_secs=None, "
        "transaction_timeout_secs=None, rollback_timeout_secs=30.0)"
    )


def test_timeout_config_rejects_nonpositive_nonfinite_and_boolean_values() -> None:
    timeout_type = _public_type("TimeoutConfig")
    for field in DEFAULTS:
        for invalid in INVALID_TIMEOUT_VALUES:
            with pytest.raises(ValueError):
                timeout_type(**{field: invalid})
            config = timeout_type()
            original = getattr(config, field)
            with pytest.raises(ValueError):
                setattr(config, field, invalid)
            assert getattr(config, field) == original
        integer_config = timeout_type(**{field: 1})
        assert getattr(integer_config, field) == 1.0


def test_timeout_config_mutation_preserves_validation_and_optionality() -> None:
    timeout_type = _public_type("TimeoutConfig")
    for field in DEFAULTS:
        optional = field != "acquire_timeout_secs"
        config = timeout_type()
        setattr(config, field, 0.125)
        assert getattr(config, field) == 0.125
        if optional:
            setattr(config, field, None)
            assert getattr(config, field) is None
        else:
            with pytest.raises(ValueError):
                setattr(config, field, None)

    unbounded = timeout_type(
        connect_timeout_secs=None,
        operation_timeout_secs=None,
        transaction_timeout_secs=None,
        rollback_timeout_secs=None,
    )
    assert unbounded.connect_timeout_secs is None
    assert unbounded.operation_timeout_secs is None
    assert unbounded.transaction_timeout_secs is None
    assert unbounded.rollback_timeout_secs is None


def test_timeout_config_rejects_values_that_cannot_form_runtime_deadlines() -> None:
    timeout_type = _public_type("TimeoutConfig")
    for field in DEFAULTS:
        for invalid in UNREPRESENTABLE_TIMEOUT_VALUES:
            with pytest.raises(ValueError):
                timeout_type(**{field: invalid})

            config = timeout_type()
            original = getattr(config, field)
            with pytest.raises(ValueError):
                setattr(config, field, invalid)
            assert getattr(config, field) == original


def test_timeout_config_enforces_portable_hundred_year_ceiling() -> None:
    timeout_type = _public_type("TimeoutConfig")
    for field in DEFAULTS:
        boundary = timeout_type(**{field: PORTABLE_TIMEOUT_CEILING_SECS})
        assert getattr(boundary, field) == PORTABLE_TIMEOUT_CEILING_SECS

        with pytest.raises(ValueError, match="100 years"):
            timeout_type(**{field: PORTABLE_TIMEOUT_CEILING_SECS + 1})

        config = timeout_type()
        original = getattr(config, field)
        with pytest.raises(ValueError, match="100 years"):
            setattr(config, field, PORTABLE_TIMEOUT_CEILING_SECS + 1)
        assert getattr(config, field) == original


def test_legacy_pool_timeout_cannot_bypass_deadline_representability() -> None:
    pool_config = fastmssql.PoolConfig(
        connection_timeout_secs=(2**64 - 1),
    )

    with pytest.raises(
        ValueError,
        match="PoolConfig.connection_timeout_secs is too large",
    ):
        fastmssql.Connection(
            server="localhost",
            database="master",
            username="test_user",
            password="test_password",
            ssl_config=fastmssql.SslConfig.development(),
            pool_config=pool_config,
        )


def test_timeout_error_is_structured_connection_error() -> None:
    timeout_error = _public_type("OperationTimeoutError")
    assert issubclass(timeout_error, fastmssql.SqlConnectionError)


def test_compiled_connection_types_append_timeout_config() -> None:
    core = importlib.import_module("fastmssql.fastmssql")
    for public_type in (core.Connection, core.Transaction):
        parameters = tuple(inspect.signature(public_type).parameters.values())
        assert parameters[-1].name == "timeout_config"
        assert parameters[-1].default is None


def test_timeout_stubs_and_readme_match_runtime_contract() -> None:
    timeout_class = _class_node(CORE_STUB, "TimeoutConfig")
    _class_node(CORE_STUB, "OperationTimeoutError")
    initializer = next(
        node
        for node in timeout_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    parameters = initializer.args.args[1:]
    assert tuple(parameter.arg for parameter in parameters) == tuple(DEFAULTS)
    assert {
        parameter.arg: ast.literal_eval(default)
        for parameter, default in zip(
            parameters, initializer.args.defaults, strict=True
        )
    } == DEFAULTS

    for stub in (CORE_STUB, WRAPPER_STUB):
        text = stub.read_text(encoding="utf-8")
        assert "timeout_config: Optional[TimeoutConfig] = None" in text
        assert "def timeout_config(self) -> TimeoutConfig" in text
    wrapper = WRAPPER_STUB.read_text(encoding="utf-8")
    assert "    OperationTimeoutError," in wrapper
    assert "    TimeoutConfig," in wrapper

    readme = README.read_text(encoding="utf-8")
    for token in (
        "connect_timeout_secs",
        "acquire_timeout_secs",
        "operation_timeout_secs",
        "transaction_timeout_secs",
        "rollback_timeout_secs",
        "CommitOutcomeUnknown",
    ):
        assert token in readme
