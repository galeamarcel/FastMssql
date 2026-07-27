from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import fastmssql


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
