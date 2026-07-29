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
QUERY_MANY_POSITIONAL = ("self", "sql", "parameter_sets")
QUERY_MANY_KEYWORD_ONLY = ("concurrency", "ordered")


def _tree(path: Path) -> ast.Module:
    assert path.is_file(), f"missing query-many artifact: {path}"
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
        f"expected one {'async ' if asynchronous else ''}"
        f"{class_node.name}.{name}"
    )
    return matches[0]


def _method_names(class_node: ast.ClassDef) -> set[str]:
    return {
        node.name
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _wrapper(raw: object) -> fastmssql.Connection:
    connection = object.__new__(fastmssql.Connection)
    connection._conn = raw
    return connection


class _NoIoRaw:
    def __init__(self) -> None:
        self.pool_stats_calls = 0
        self.query_calls: list[tuple[str, object]] = []

    async def pool_stats(self) -> dict[str, int | float | bool | None]:
        self.pool_stats_calls += 1
        return {
            "connected": False,
            "connections": 0,
            "idle_connections": 0,
            "active_connections": 0,
            "max_size": 4,
            "min_idle": 0,
            "get_started": 0,
            "get_direct": 0,
            "get_waited": 0,
            "get_timed_out": 0,
            "pending_gets": 0,
            "get_wait_time_seconds": 0.0,
            "connections_created": 0,
            "connections_closed_broken": 0,
            "connections_closed_invalid": 0,
            "connections_closed_max_lifetime": 0,
            "connections_closed_idle_timeout": 0,
        }

    async def query(self, sql: str, params: object) -> object:
        self.query_calls.append((sql, params))
        raise AssertionError("query_many performed unexpected SQL")


class _CountingSyncSource:
    def __init__(self) -> None:
        self.iter_calls = 0
        self.pull_calls = 0

    def __iter__(self) -> _CountingSyncSource:
        self.iter_calls += 1
        return self

    def __next__(self) -> list[int]:
        self.pull_calls += 1
        raise StopIteration


class _DualProtocolSource:
    def __init__(self) -> None:
        self.aiter_calls = 0
        self.anext_calls = 0
        self.iter_calls = 0

    def __aiter__(self) -> _DualProtocolSource:
        self.aiter_calls += 1
        return self

    async def __anext__(self) -> list[int]:
        self.anext_calls += 1
        raise StopAsyncIteration

    def __iter__(self) -> _DualProtocolSource:
        self.iter_calls += 1
        raise AssertionError("async producer protocol must take precedence")


class _ProducerAcquisitionFailure(BaseException):
    pass


class _FailingSyncSource:
    def __init__(self, failure: BaseException) -> None:
        self.failure = failure
        self.iter_calls = 0

    def __iter__(self) -> _FailingSyncSource:
        self.iter_calls += 1
        raise self.failure


def test_wrapper_and_stub_publish_exact_query_many_factory() -> None:
    wrapper_tree = _tree(WRAPPER)
    runtime = _method(
        _class(wrapper_tree, "Connection"),
        "query_many",
        asynchronous=False,
    )
    assert tuple(argument.arg for argument in runtime.args.args) == (
        QUERY_MANY_POSITIONAL
    )
    assert tuple(argument.arg for argument in runtime.args.kwonlyargs) == (
        QUERY_MANY_KEYWORD_ONLY
    )

    stub_tree = _tree(WRAPPER_STUB)
    stub = _method(
        _class(stub_tree, "Connection"),
        "query_many",
        asynchronous=False,
    )
    assert tuple(argument.arg for argument in stub.args.args) == (
        QUERY_MANY_POSITIONAL
    )
    assert tuple(argument.arg for argument in stub.args.kwonlyargs) == (
        QUERY_MANY_KEYWORD_ONLY
    )
    assert ast.unparse(stub.args.args[1].annotation) == "str"
    assert ast.unparse(stub.args.args[2].annotation) == "ParameterSetSource"
    assert [
        ast.literal_eval(default)
        for default in stub.args.kw_defaults
        if default is not None
    ] == [10, True]
    assert ast.unparse(stub.returns) == "QueryManyIterator"


def test_wrapper_stub_publishes_exact_query_many_iterator_protocol() -> None:
    tree = _tree(WRAPPER_STUB)
    imports = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "collections.abc"
        for alias in node.names
    }
    assert "AsyncIterator" in imports

    iterator = _class(tree, "QueryManyIterator")
    assert [ast.unparse(base) for base in iterator.bases] == [
        "AsyncIterator[QueryStream]"
    ]
    expected = {
        "__aiter__": (False, "QueryManyIterator"),
        "__anext__": (True, "QueryStream"),
        "aclose": (True, "None"),
        "__aenter__": (True, "QueryManyIterator"),
        "__aexit__": (True, "bool"),
    }
    for method_name, (asynchronous, return_type) in expected.items():
        method = _method(iterator, method_name, asynchronous=asynchronous)
        assert ast.unparse(method.returns) == return_type

    exit_method = _method(iterator, "__aexit__", asynchronous=True)
    assert tuple(argument.arg for argument in exit_method.args.args) == (
        "self",
        "exc_type",
        "exc",
        "traceback",
    )
    assert ast.unparse(exit_method.returns) == "bool"


def test_transaction_and_raw_surfaces_do_not_publish_query_many() -> None:
    wrapper_tree = _tree(WRAPPER_STUB)
    raw_tree = _tree(RAW_STUB)
    assert "query_many" not in _method_names(_class(wrapper_tree, "Transaction"))
    assert "query_many" not in _method_names(_class(raw_tree, "Transaction"))
    assert "query_many" not in _method_names(_class(raw_tree, "Connection"))

    core = importlib.import_module("fastmssql.fastmssql")
    assert "query_many" not in vars(fastmssql.Transaction)
    assert "query_many" not in vars(core.Transaction)
    assert "query_many" not in vars(core.Connection)


def test_runtime_factory_is_regular_and_returns_the_public_iterator() -> None:
    raw = _NoIoRaw()
    connection = _wrapper(raw)

    assert not inspect.iscoroutinefunction(fastmssql.Connection.query_many)
    signature = inspect.signature(fastmssql.Connection.query_many)
    assert tuple(signature.parameters) == (
        QUERY_MANY_POSITIONAL + QUERY_MANY_KEYWORD_ONLY
    )
    assert signature.parameters["concurrency"].default == 10
    assert signature.parameters["ordered"].default is True

    iterator = connection.query_many("SELECT @P1", [[1]])

    assert isinstance(iterator, fastmssql.QueryManyIterator)
    assert iterator.__aiter__() is iterator
    assert raw.pool_stats_calls == 0
    assert raw.query_calls == []


@pytest.mark.parametrize("sql", (None, 1, b"SELECT 1", object()))
def test_invalid_sql_performs_zero_producer_pool_or_query_work(sql: object) -> None:
    raw = _NoIoRaw()
    source = _CountingSyncSource()
    connection = _wrapper(raw)

    with pytest.raises(TypeError, match="sql must be a string"):
        connection.query_many(sql, source)

    assert source.iter_calls == 0
    assert source.pull_calls == 0
    assert raw.pool_stats_calls == 0
    assert raw.query_calls == []


@pytest.mark.parametrize(
    ("concurrency", "error_type"),
    (
        (True, TypeError),
        (False, TypeError),
        (1.5, TypeError),
        ("2", TypeError),
        (None, TypeError),
        (0, ValueError),
        (-1, ValueError),
        (10_001, ValueError),
    ),
)
def test_invalid_concurrency_performs_zero_forbidden_work(
    concurrency: object,
    error_type: type[Exception],
) -> None:
    raw = _NoIoRaw()
    source = _CountingSyncSource()
    connection = _wrapper(raw)

    with pytest.raises(error_type, match="concurrency"):
        connection.query_many(
            "SELECT @P1",
            source,
            concurrency=concurrency,
        )

    assert source.iter_calls == 0
    assert source.pull_calls == 0
    assert raw.pool_stats_calls == 0
    assert raw.query_calls == []


@pytest.mark.parametrize("ordered", (0, 1, "yes", None, object()))
def test_invalid_ordered_performs_zero_forbidden_work(ordered: object) -> None:
    raw = _NoIoRaw()
    source = _CountingSyncSource()
    connection = _wrapper(raw)

    with pytest.raises(TypeError, match="ordered must be exactly bool"):
        connection.query_many("SELECT @P1", source, ordered=ordered)

    assert source.iter_calls == 0
    assert source.pull_calls == 0
    assert raw.pool_stats_calls == 0
    assert raw.query_calls == []


@pytest.mark.parametrize(
    "source",
    (
        "rows",
        b"rows",
        bytearray(b"rows"),
        memoryview(b"rows"),
        {"value": 1},
        object(),
    ),
)
def test_invalid_top_level_source_performs_zero_pool_or_query_work(
    source: object,
) -> None:
    raw = _NoIoRaw()
    connection = _wrapper(raw)

    with pytest.raises(TypeError, match="parameter_sets must be an iterable"):
        connection.query_many("SELECT @P1", source)

    assert raw.pool_stats_calls == 0
    assert raw.query_calls == []


def test_dual_protocol_source_is_acquired_once_with_async_precedence() -> None:
    raw = _NoIoRaw()
    source = _DualProtocolSource()
    connection = _wrapper(raw)

    iterator = connection.query_many("SELECT @P1", source)

    assert isinstance(iterator, fastmssql.QueryManyIterator)
    assert source.aiter_calls == 1
    assert source.iter_calls == 0
    assert source.anext_calls == 0
    assert raw.pool_stats_calls == 0
    assert raw.query_calls == []


def test_sync_producer_acquisition_preserves_the_original_baseexception() -> None:
    raw = _NoIoRaw()
    failure = _ProducerAcquisitionFailure("producer acquisition sentinel")
    source = _FailingSyncSource(failure)
    connection = _wrapper(raw)

    with pytest.raises(_ProducerAcquisitionFailure) as caught:
        connection.query_many("SELECT @P1", source)

    assert caught.value is failure
    assert source.iter_calls == 1
    assert raw.pool_stats_calls == 0
    assert raw.query_calls == []


@pytest.mark.asyncio
async def test_concrete_source_resize_fails_at_the_next_pull_boundary() -> None:
    raw = _NoIoRaw()
    source = [[1]]
    connection = _wrapper(raw)
    iterator = connection.query_many("SELECT @P1", source)
    source.append([2])

    with pytest.raises(ValueError) as caught:
        await iterator.__anext__()

    assert caught.value.query_index == 0
    assert raw.pool_stats_calls == 1
    assert raw.query_calls == []
