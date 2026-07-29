from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
from typing import Any

import fastmssql
import pytest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "python/fastmssql/__init__.py"
WRAPPER_STUB = ROOT / "python/fastmssql/__init__.pyi"
RAW_STUB = ROOT / "python/fastmssql/fastmssql.pyi"
COORDINATOR = ROOT / "python/fastmssql/_execute_many.py"
CONNECTION_POSITIONAL = ("self", "sql", "parameter_sets")
CONNECTION_KEYWORD_ONLY = ("atomic", "chunk_size")
TRANSACTION_KEYWORD_ONLY = ("chunk_size",)


def _tree(path: Path) -> ast.Module:
    assert path.is_file(), f"missing execute-many artifact: {path}"
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


def _type_alias_value(tree: ast.Module, name: str) -> ast.expr:
    matches: list[ast.expr] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            matches.append(node.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            matches.append(node.value)
    assert len(matches) == 1, f"expected one type alias {name}"
    return matches[0]


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
        self.calls: list[tuple[str, list[object], bool | None, int]] = []
        self.sequence_constructions = 0

    async def execute_many(
        self,
        sql: str,
        parameter_sets: list[list[Any] | fastmssql.Parameters],
        *,
        atomic: bool | None = None,
        chunk_size: int,
    ) -> int:
        assert isinstance(parameter_sets, list)
        self.calls.append((sql, parameter_sets, atomic, chunk_size))
        return len(parameter_sets)

    def _execute_many_sequence(self, *args: object, **kwargs: object) -> None:
        self.sequence_constructions += 1
        raise AssertionError("a concrete list must not construct a producer sequence")


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", (fastmssql.Connection, fastmssql.Transaction))
async def test_concrete_list_uses_raw_bounded_fast_path(owner: type) -> None:
    raw = _ListRawSentinel()
    wrapper = _wrapper(owner, raw)
    parameter_sets = [[1, "one"], [2, "two"]]

    if owner is fastmssql.Connection:
        affected = await wrapper.execute_many(
            "INSERT INTO dbo.items VALUES (@P1, @P2)",
            parameter_sets,
            atomic=False,
            chunk_size=2,
        )
        expected_atomic: bool | None = False
    else:
        affected = await wrapper.execute_many(
            "INSERT INTO dbo.items VALUES (@P1, @P2)",
            parameter_sets,
            chunk_size=2,
        )
        expected_atomic = None

    assert affected == 2
    assert raw.calls == [
        (
            "INSERT INTO dbo.items VALUES (@P1, @P2)",
            parameter_sets,
            expected_atomic,
            2,
        )
    ]
    assert raw.calls[0][1] is parameter_sets
    assert raw.sequence_constructions == 0
    assert _raw_owner(wrapper) is raw


def test_public_stub_defines_parameter_set_source_aliases() -> None:
    tree = _tree(WRAPPER_STUB)
    imports = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "collections.abc"
        for alias in node.names
    }

    assert {"AsyncIterable", "Iterable"} <= imports
    assert ast.unparse(_type_alias_value(tree, "ParameterSet")) == (
        "list[Any] | Parameters"
    )
    assert ast.unparse(_type_alias_value(tree, "ParameterSetSource")) == (
        "list[ParameterSet] | Iterable[ParameterSet] | "
        "AsyncIterable[ParameterSet]"
    )


def test_wrapper_stubs_publish_exact_execute_many_signatures() -> None:
    tree = _tree(WRAPPER_STUB)
    for class_name, keyword_only in (
        ("Connection", CONNECTION_KEYWORD_ONLY),
        ("Transaction", TRANSACTION_KEYWORD_ONLY),
    ):
        method = _method(
            _class(tree, class_name),
            "execute_many",
            asynchronous=False,
        )
        assert tuple(argument.arg for argument in method.args.args) == (
            CONNECTION_POSITIONAL
        )
        assert tuple(argument.arg for argument in method.args.kwonlyargs) == (
            keyword_only
        )
        assert ast.unparse(method.args.args[1].annotation) == "str"
        assert ast.unparse(method.args.args[2].annotation) == "ParameterSetSource"
        defaults = {
            argument.arg: default.value
            for argument, default in zip(
                method.args.kwonlyargs,
                method.args.kw_defaults,
                strict=True,
            )
            if isinstance(default, ast.Constant)
        }
        assert defaults["chunk_size"] == 1000
        if class_name == "Connection":
            assert defaults["atomic"] is True
        else:
            assert "atomic" not in defaults
        assert ast.unparse(method.returns) == "Coroutine[Any, Any, int]"


def test_raw_stubs_are_list_only_and_publish_private_sequence() -> None:
    tree = _tree(RAW_STUB)
    progress = _class(tree, "_ExecuteManyAbortProgress")
    progress_fields = {
        node.target.id: ast.unparse(node.annotation)
        for node in progress.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
    }
    assert progress_fields == {
        "active_parameter_set_index": "int | None",
        "confirmed_committed_parameter_sets": "int",
        "partial_commit_possible": "bool",
    }
    sequence = _class(tree, "_ExecuteManySequence")
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
    abort = _method(sequence, "abort", asynchronous=False)
    assert ast.unparse(abort.returns) == (
        "Coroutine[Any, Any, _ExecuteManyAbortProgress]"
    )

    for class_name, keyword_only in (
        ("Connection", CONNECTION_KEYWORD_ONLY),
        ("Transaction", TRANSACTION_KEYWORD_ONLY),
    ):
        owner = _class(tree, class_name)
        method = _method(owner, "execute_many", asynchronous=False)
        assert tuple(argument.arg for argument in method.args.args) == (
            CONNECTION_POSITIONAL
        )
        assert tuple(argument.arg for argument in method.args.kwonlyargs) == (
            keyword_only
        )
        annotation = ast.unparse(method.args.args[2].annotation)
        assert annotation == "list[list[Any] | Parameters]"

        constructor = _method(
            owner,
            "_execute_many_sequence",
            asynchronous=False,
        )
        assert tuple(argument.arg for argument in constructor.args.args) == (
            "self",
            "sql",
        )
        assert tuple(
            argument.arg for argument in constructor.args.kwonlyargs
        ) == keyword_only
        assert ast.unparse(constructor.returns) == "_ExecuteManySequence"


def test_wrapper_dispatch_is_explicit_and_transaction_has_no_atomic() -> None:
    tree = _tree(WRAPPER)

    for class_name, keyword_only in (
        ("Connection", CONNECTION_KEYWORD_ONLY),
        ("Transaction", TRANSACTION_KEYWORD_ONLY),
    ):
        method = _method(
            _class(tree, class_name),
            "execute_many",
            asynchronous=True,
        )
        assert tuple(argument.arg for argument in method.args.args) == (
            CONNECTION_POSITIONAL
        )
        assert tuple(argument.arg for argument in method.args.kwonlyargs) == (
            keyword_only
        )
        calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)]
        assert any(
            isinstance(call.func, ast.Name)
            and call.func.id == "execute_many_iterable"
            for call in calls
        )
        direct = [
            call
            for call in calls
            if isinstance(call.func, ast.Attribute)
            and call.func.attr == "execute_many"
        ]
        assert len(direct) == 1
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
    tree = _tree(COORDINATOR)

    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        assert not (
            isinstance(call.func, ast.Name)
            and call.func.id == "list"
            and call.args
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id == "parameter_sets"
        )
        assert not (
            isinstance(call.func, ast.Attribute)
            and call.func.attr in {"Queue", "PriorityQueue", "LifoQueue"}
        )
        assert not (
            isinstance(call.func, ast.Attribute)
            and call.func.attr in {"create_task", "ensure_future"}
            and any(
                isinstance(argument, ast.Name)
                and argument.id in {"parameter_set", "item"}
                for argument in call.args
            )
        )


def test_runtime_surface_has_exact_execute_many_call_shape() -> None:
    core = importlib.import_module("fastmssql.fastmssql")
    expected = {
        fastmssql.Connection: CONNECTION_KEYWORD_ONLY,
        fastmssql.Transaction: TRANSACTION_KEYWORD_ONLY,
        core.Connection: CONNECTION_KEYWORD_ONLY,
        core.Transaction: TRANSACTION_KEYWORD_ONLY,
    }
    for owner, keyword_only in expected.items():
        signature = inspect.signature(owner.execute_many)
        assert tuple(signature.parameters) == CONNECTION_POSITIONAL + keyword_only
        assert signature.parameters["chunk_size"].default == 1000
        if owner in (fastmssql.Connection, core.Connection):
            assert signature.parameters["atomic"].default is True
        else:
            assert "atomic" not in signature.parameters
