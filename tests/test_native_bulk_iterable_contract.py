from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import fastmssql
import pytest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "python/fastmssql/__init__.py"
WRAPPER_STUB = ROOT / "python/fastmssql/__init__.pyi"
RAW_STUB = ROOT / "python/fastmssql/fastmssql.pyi"
COORDINATOR = ROOT / "python/fastmssql/_bulk_iterable.py"
SHARED_COORDINATOR = ROOT / "python/fastmssql/_bounded_sequence.py"
OWNER_NAMES = ("Connection", "Transaction")
POSITIONAL = ("self", "table", "columns", "rows")
KEYWORD_ONLY = ("chunk_size",)


def _tree(path: Path) -> ast.Module:
    assert path.is_file(), f"missing native-bulk iterable artifact: {path}"
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


def _assignment(tree: ast.Module, name: str) -> ast.Assign:
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        )
    ]
    assert len(matches) == 1, f"expected one assignment for {name}"
    return matches[0]


def _stub_rows_annotation(tree: ast.Module, class_name: str) -> str:
    method = _method(
        _class(tree, class_name),
        "native_bulk_insert",
        asynchronous=False,
    )
    assert tuple(argument.arg for argument in method.args.args) == POSITIONAL
    assert tuple(argument.arg for argument in method.args.kwonlyargs) == KEYWORD_ONLY
    return ast.unparse(method.args.args[3].annotation)


def _raw_owner(wrapper: object) -> object:
    if isinstance(wrapper, fastmssql.Connection):
        return wrapper._conn
    return wrapper._rust_conn


def _wrapper(owner: type, raw: object) -> object:
    wrapper = object.__new__(owner)
    if owner is fastmssql.Connection:
        wrapper._conn = raw
    else:
        wrapper._rust_conn = raw
    return wrapper


class _ListRawSentinel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], list[list[object]], int]] = []
        self.sequence_constructions = 0

    async def native_bulk_insert(
        self,
        table: str,
        columns: list[str],
        rows: list[list[object]],
        *,
        chunk_size: int,
    ) -> int:
        assert isinstance(rows, list)
        self.calls.append((table, columns, rows, chunk_size))
        return len(rows)

    def _native_bulk_sequence(self, *args: object, **kwargs: object) -> None:
        self.sequence_constructions += 1
        raise AssertionError("a concrete list must not construct an iterable sequence")


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", (fastmssql.Connection, fastmssql.Transaction))
async def test_concrete_list_uses_unchanged_raw_fast_path(owner: type) -> None:
    raw = _ListRawSentinel()
    wrapper = _wrapper(owner, raw)
    rows = [[1, "one"], [2, "two"]]

    affected = await wrapper.native_bulk_insert(
        "dbo.items",
        ["id", "payload"],
        rows,
        chunk_size=2,
    )

    assert affected == 2
    assert raw.calls == [("dbo.items", ["id", "payload"], rows, 2)]
    assert raw.calls[0][2] is rows
    assert raw.sequence_constructions == 0
    assert _raw_owner(wrapper) is raw


def test_public_stub_defines_bounded_bulk_row_aliases() -> None:
    tree = _tree(WRAPPER_STUB)
    imports = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "collections.abc"
        for alias in node.names
    }

    assert {"AsyncIterable", "Iterable", "Sequence"} <= imports
    assert ast.unparse(_assignment(tree, "BulkRow").value) == "Sequence[Any]"
    assert ast.unparse(_assignment(tree, "BulkRows").value) == (
        "list[list[Any]] | Iterable[BulkRow] | AsyncIterable[BulkRow]"
    )

    for class_name in OWNER_NAMES:
        assert _stub_rows_annotation(tree, class_name) == "BulkRows"


def test_raw_public_stubs_remain_concrete_list_primitives() -> None:
    tree = _tree(RAW_STUB)
    for class_name in OWNER_NAMES:
        assert _stub_rows_annotation(tree, class_name) == "list[list[Any]]"


def test_raw_stub_exposes_only_private_sequence_coordination_surface() -> None:
    tree = _tree(RAW_STUB)
    sequence = _class(tree, "_NativeBulkSequence")
    methods = {
        node.name
        for node in sequence.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {
        "reserve",
        "activate",
        "push",
        "finish",
        "abort",
        "expire",
        "remaining_timeout",
    } <= methods

    for class_name in OWNER_NAMES:
        constructor = _method(
            _class(tree, class_name),
            "_native_bulk_sequence",
            asynchronous=False,
        )
        assert tuple(argument.arg for argument in constructor.args.args) == (
            "self",
            "table",
            "columns",
        )
        assert tuple(argument.arg for argument in constructor.args.kwonlyargs) == (
            "chunk_size",
        )
        assert ast.unparse(constructor.returns) == "_NativeBulkSequence"


def test_wrapper_dispatch_is_explicit_and_list_path_is_not_coordinated() -> None:
    tree = _tree(WRAPPER)

    for class_name in OWNER_NAMES:
        method = _method(
            _class(tree, class_name),
            "native_bulk_insert",
            asynchronous=True,
        )
        calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)]
        assert any(
            isinstance(call.func, ast.Name)
            and call.func.id == "native_bulk_insert_iterable"
            for call in calls
        )
        direct_calls = [
            call
            for call in calls
            if isinstance(call.func, ast.Attribute)
            and call.func.attr == "native_bulk_insert"
        ]
        assert len(direct_calls) == 1
        assert any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and any(
                isinstance(argument, ast.Name) and argument.id == "list"
                for argument in node.args
            )
            for node in ast.walk(method)
        )


def test_coordinator_has_no_eager_or_unbounded_producer_constructs() -> None:
    trees = (_tree(COORDINATOR), _tree(SHARED_COORDINATOR))

    for tree in trees:
        for call in (
            node for node in ast.walk(tree) if isinstance(node, ast.Call)
        ):
            assert not (
                isinstance(call.func, ast.Name)
                and call.func.id == "list"
                and call.args
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id
                in {"items", "parameter_sets", "producer", "rows", "source"}
            )
            assert not (
                isinstance(call.func, ast.Attribute)
                and call.func.attr in {"Queue", "PriorityQueue", "LifoQueue"}
            )

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "shield"
                and node.args
            ):
                continue
            assert isinstance(node.args[0], ast.Name), (
                "asyncio.shield must receive the strong-referenced cleanup task"
            )


def test_runtime_surface_keeps_the_same_call_shape() -> None:
    core = importlib.import_module("fastmssql.fastmssql")

    for owner in (
        fastmssql.Connection,
        fastmssql.Transaction,
        core.Connection,
        core.Transaction,
    ):
        signature = inspect.signature(owner.native_bulk_insert)
        assert tuple(signature.parameters) == POSITIONAL + KEYWORD_ONLY
        assert signature.parameters["chunk_size"].default == 1000
