from __future__ import annotations

import ast
import inspect
from pathlib import Path

from fastmssql import PoolConfig


ROOT = Path(__file__).resolve().parents[1]
STUB = ROOT / "python" / "fastmssql" / "fastmssql.pyi"
README = ROOT / "README.md"
CANONICAL_DEFAULTS = {
    "max_size": 15,
    "min_idle": 3,
    "max_lifetime_secs": 1800,
    "idle_timeout_secs": 300,
    "connection_timeout_secs": 30,
    "test_on_check_out": None,
    "retry_connection": None,
}
CANONICAL_TEXT_SIGNATURE = (
    "(max_size=15, min_idle=3, max_lifetime_secs=1800, "
    "idle_timeout_secs=300, connection_timeout_secs=30, "
    "test_on_check_out=None, retry_connection=None)"
)


def _pool_config_stub_init() -> ast.FunctionDef:
    tree = ast.parse(STUB.read_text(encoding="utf-8"), filename=str(STUB))
    pool_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PoolConfig"
    )
    return next(
        node
        for node in pool_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )


def test_runtime_constructor_signature_matches_canonical_profile() -> None:
    signature = inspect.signature(PoolConfig)
    assert PoolConfig.__text_signature__ == CANONICAL_TEXT_SIGNATURE
    assert tuple(signature.parameters) == tuple(CANONICAL_DEFAULTS)
    assert {
        name: parameter.default
        for name, parameter in signature.parameters.items()
    } == CANONICAL_DEFAULTS
    assert all(
        parameter.default is not Ellipsis
        for parameter in signature.parameters.values()
    )
    config = PoolConfig()
    assert {
        name: getattr(config, name)
        for name in CANONICAL_DEFAULTS
    } == CANONICAL_DEFAULTS


def test_pool_config_stub_matches_runtime_defaults_and_optional_types() -> None:
    init = _pool_config_stub_init()
    parameters = init.args.args[1:]
    assert tuple(parameter.arg for parameter in parameters) == tuple(
        CANONICAL_DEFAULTS
    )
    assert len(parameters) == len(init.args.defaults)
    assert {
        parameter.arg: ast.literal_eval(default)
        for parameter, default in zip(
            parameters, init.args.defaults, strict=True
        )
    } == CANONICAL_DEFAULTS
    annotations = {
        parameter.arg: ast.unparse(parameter.annotation)
        for parameter in parameters
    }
    assert annotations == {
        "max_size": "int",
        "min_idle": "Optional[int]",
        "max_lifetime_secs": "Optional[int]",
        "idle_timeout_secs": "Optional[int]",
        "connection_timeout_secs": "Optional[int]",
        "test_on_check_out": "Optional[bool]",
        "retry_connection": "Optional[bool]",
    }
    stub = STUB.read_text(encoding="utf-8")
    assert "default: None = unlimited" not in stub
    assert "default: None = no timeout" not in stub


def test_readme_states_one_complete_default_profile() -> None:
    readme = README.read_text(encoding="utf-8")
    normalized_readme = " ".join(readme.split())
    assert "smart defaults (default max_size=15, min_idle=3)" in readme
    assert (
        "Default pool (if omitted or constructed with `PoolConfig()`): "
        "`max_size=15`, `min_idle=3`, `max_lifetime_secs=1800`, "
        "`idle_timeout_secs=300`, `connection_timeout_secs=30`."
        in normalized_readme
    )
    assert "smart defaults (default max_size=20, min_idle=2)" not in readme
