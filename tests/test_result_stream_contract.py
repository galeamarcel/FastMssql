from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
from types import ModuleType

import fastmssql


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_TYPES = (
    "ResultStream",
    "ResultSet",
    "ResultSummary",
    "ColumnMetadata",
    "DoneResult",
    "SqlMessage",
)
STREAM_METHODS = {
    "stream": (
        ("self", "sql", "params"),
        ("buffer_size",),
        ("str", "list[Any] | Parameters | None"),
    ),
    "batch": (
        ("self", "sql"),
        ("buffer_size",),
        ("str",),
    ),
}
RESULT_PROPERTIES = {
    "ColumnMetadata": {
        "ordinal": "int",
        "name": "str",
        "type_name": "str",
        "nullable": "bool | None",
        "precision": "int | None",
        "scale": "int | None",
        "length": "int | Literal['MAX'] | None",
    },
    "DoneResult": {
        "kind": "str",
        "rows_affected": "int | None",
        "more_results": "bool",
        "in_transaction": "bool",
        "attention_acknowledged": "bool",
    },
    "SqlMessage": {
        "number": "int",
        "state": "int",
        "severity": "int",
        "message": "str",
        "server": "str",
        "procedure": "str",
        "line": "int",
    },
    "ResultSummary": {
        "result_set_count": "int",
        "done": "tuple[DoneResult, ...]",
        "messages": "tuple[SqlMessage, ...]",
        "return_status": "int | None",
        "output_parameters": "dict[str | int, object]",
    },
}


def _package_files() -> tuple[Path, Path, Path, Path]:
    package_file = Path(fastmssql.__file__).resolve()
    package_dir = package_file.parent
    wrapper_stub = (package_dir / "__init__.pyi").resolve()
    core_stub = (package_dir / "fastmssql.pyi").resolve()
    typed_marker = (package_dir / "py.typed").resolve()

    for path in (package_file, wrapper_stub, core_stub, typed_marker):
        assert path.is_file(), f"missing installed package artifact: {path}"
        assert path.parent == package_dir
    return package_file, wrapper_stub, core_stub, typed_marker


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    ]
    assert len(matches) == 1, f"expected one stub class {name}"
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


def _all_names(tree: ast.Module) -> tuple[str, ...]:
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        )
    )
    return tuple(ast.literal_eval(assignment.value))


def _imported_names(tree: ast.Module, module: str) -> set[str]:
    return {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
    }


def _runtime_method_signature(owner: type, name: str) -> inspect.Signature:
    assert hasattr(owner, name), f"missing runtime method {owner.__name__}.{name}"
    return inspect.signature(getattr(owner, name))


def _assert_runtime_stream_signature(owner: type, name: str) -> None:
    positional, keyword_only, _ = STREAM_METHODS[name]
    signature = _runtime_method_signature(owner, name)
    assert tuple(signature.parameters) == positional + keyword_only
    assert signature.parameters["self"].kind in {
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    }
    assert all(
        signature.parameters[item].kind
        is inspect.Parameter.POSITIONAL_OR_KEYWORD
        for item in positional[1:]
    )
    assert signature.parameters["buffer_size"].kind is (
        inspect.Parameter.KEYWORD_ONLY
    )
    assert signature.parameters["buffer_size"].default == 64


def _assert_stub_stream_signature(
    class_node: ast.ClassDef,
    name: str,
) -> None:
    positional, keyword_only, annotations = STREAM_METHODS[name]
    method = _method(class_node, name, asynchronous=True)
    assert tuple(argument.arg for argument in method.args.args) == positional
    assert tuple(
        argument.arg for argument in method.args.kwonlyargs
    ) == keyword_only
    assert len(method.args.kw_defaults) == 1
    assert ast.literal_eval(method.args.kw_defaults[0]) == 64
    assert tuple(
        ast.unparse(argument.annotation)
        for argument in method.args.args[1:]
    ) == annotations
    assert ast.unparse(method.args.kwonlyargs[0].annotation) == "int"
    assert ast.unparse(method.returns) == "ResultStream"


def _assert_property(
    class_node: ast.ClassDef,
    name: str,
    annotation: str,
) -> None:
    method = _method(class_node, name, asynchronous=False)
    assert any(
        isinstance(decorator, ast.Name) and decorator.id == "property"
        for decorator in method.decorator_list
    )
    assert ast.unparse(method.returns) == annotation


def _assert_result_protocols(path: Path) -> None:
    tree = _tree(path)
    stream = _class(tree, "ResultStream")
    result_set = _class(tree, "ResultSet")

    for forbidden in ("__iter__", "__next__"):
        assert not any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == forbidden
            for class_node in (stream, result_set)
            for node in class_node.body
        )

    stream_aiter = _method(stream, "__aiter__", asynchronous=False)
    assert ast.unparse(stream_aiter.returns) == "ResultStream"
    stream_anext = _method(stream, "__anext__", asynchronous=True)
    assert ast.unparse(stream_anext.returns) == "ResultSet"
    stream_enter = _method(stream, "__aenter__", asynchronous=True)
    assert ast.unparse(stream_enter.returns) == "ResultStream"
    stream_exit = _method(stream, "__aexit__", asynchronous=True)
    assert ast.unparse(stream_exit.returns) == "None"
    stream_close = _method(stream, "aclose", asynchronous=True)
    assert ast.unparse(stream_close.returns) == "None"
    finish = _method(stream, "finish", asynchronous=True)
    assert ast.unparse(finish.returns) == "ResultSummary"
    _assert_property(stream, "closed", "bool")
    _assert_property(stream, "complete", "bool")
    _assert_property(stream, "summary", "ResultSummary")

    set_aiter = _method(result_set, "__aiter__", asynchronous=False)
    assert ast.unparse(set_aiter.returns) == "ResultSet"
    set_anext = _method(result_set, "__anext__", asynchronous=True)
    assert ast.unparse(set_anext.returns) == "FastRow"
    set_close = _method(result_set, "aclose", asynchronous=True)
    assert ast.unparse(set_close.returns) == "None"
    _assert_property(result_set, "index", "int")
    _assert_property(
        result_set,
        "columns",
        "tuple[ColumnMetadata, ...]",
    )
    _assert_property(result_set, "column_names", "tuple[str, ...]")
    _assert_property(result_set, "closed", "bool")


def _assert_result_value_properties(path: Path) -> None:
    tree = _tree(path)
    for class_name, properties in RESULT_PROPERTIES.items():
        class_node = _class(tree, class_name)
        for name, annotation in properties.items():
            _assert_property(class_node, name, annotation)


def _assert_legacy_query_stream_stub(path: Path) -> None:
    query_stream = _class(_tree(path), "QueryStream")
    documentation = (ast.get_docstring(query_stream) or "").lower()
    assert "synchronous" in documentation
    assert "buffered" in documentation
    assert "memory-efficient" not in documentation
    _method(query_stream, "__iter__", asynchronous=False)
    _method(query_stream, "__next__", asynchronous=False)
    assert not any(
        isinstance(node, ast.AsyncFunctionDef)
        and node.name in {"__aiter__", "__anext__"}
        for node in query_stream.body
    )


def _assert_legacy_connection_docs(path: Path) -> None:
    connection = _class(_tree(path), "Connection")
    for name in ("query", "simple_query"):
        method = _method(connection, name, asynchronous=False)
        documentation = (ast.get_docstring(method) or "").lower()
        assert "synchronous" in documentation
        assert "buffered" in documentation
        assert "memory-efficient" not in documentation
        assert "async stream" not in documentation


def test_installed_package_owns_stubs_and_typed_marker() -> None:
    package_file, wrapper_stub, core_stub, typed_marker = _package_files()
    core: ModuleType = importlib.import_module("fastmssql.fastmssql")
    core_file = Path(core.__file__).resolve()

    assert package_file.name == "__init__.py"
    assert core_file.parent == package_file.parent
    assert wrapper_stub.parent == package_file.parent
    assert core_stub.parent == package_file.parent
    assert typed_marker.parent == package_file.parent


def test_result_types_are_exported_by_runtime_and_both_stubs() -> None:
    _, wrapper_stub, core_stub, _ = _package_files()
    core = importlib.import_module("fastmssql.fastmssql")
    core_tree = _tree(core_stub)
    wrapper_tree = _tree(wrapper_stub)
    wrapper_imports = _imported_names(wrapper_tree, "fastmssql")
    wrapper_all = set(_all_names(wrapper_tree))

    for name in PUBLIC_TYPES:
        assert hasattr(core, name), f"compiled module does not export {name}"
        assert hasattr(fastmssql, name), f"wrapper package does not export {name}"
        assert name in fastmssql.__all__
        _class(core_tree, name)
        assert name in wrapper_imports
        assert name in wrapper_all
    _assert_result_value_properties(core_stub)
    for class_name, properties in RESULT_PROPERTIES.items():
        result_type = getattr(core, class_name)
        for name in properties:
            assert hasattr(result_type, name)


def test_connection_publishes_exact_stream_and_batch_signatures() -> None:
    _, wrapper_stub, core_stub, _ = _package_files()
    core = importlib.import_module("fastmssql.fastmssql")

    for owner in (
        fastmssql.Connection,
        core.Connection,
    ):
        for name in STREAM_METHODS:
            _assert_runtime_stream_signature(owner, name)

    for path in (wrapper_stub, core_stub):
        tree = _tree(path)
        connection = _class(tree, "Connection")
        for name in STREAM_METHODS:
            _assert_stub_stream_signature(connection, name)


def test_result_stream_and_result_set_are_async_only_protocols() -> None:
    _, wrapper_stub, core_stub, _ = _package_files()
    wrapper_tree = _tree(wrapper_stub)
    wrapper_imports = _imported_names(wrapper_tree, "fastmssql")
    assert {"ResultStream", "ResultSet"} <= wrapper_imports
    _assert_result_protocols(core_stub)

    for name in ("ResultStream", "ResultSet"):
        result_type = getattr(fastmssql, name)
        assert hasattr(result_type, "__aiter__")
        assert hasattr(result_type, "__anext__")
        assert not hasattr(result_type, "__iter__")
        assert not hasattr(result_type, "__next__")


def test_query_stream_is_honestly_documented_as_sync_and_buffered() -> None:
    _, wrapper_stub, core_stub, _ = _package_files()
    query_stream = fastmssql.QueryStream
    runtime_documentation = (query_stream.__doc__ or "").lower()
    assert "synchronous" in runtime_documentation
    assert "buffered" in runtime_documentation
    assert "memory-efficient" not in runtime_documentation
    assert hasattr(query_stream, "__iter__")
    assert hasattr(query_stream, "__next__")
    assert not hasattr(query_stream, "__aiter__")
    assert not hasattr(query_stream, "__anext__")
    wrapper_tree = _tree(wrapper_stub)
    assert "QueryStream" in _imported_names(wrapper_tree, "fastmssql")
    _assert_legacy_query_stream_stub(core_stub)
    for path in (wrapper_stub, core_stub):
        _assert_legacy_connection_docs(path)


def test_result_stream_source_uses_bounded_event_and_ack_credit() -> None:
    source_path = ROOT / "src/result_stream.rs"
    assert source_path.is_file()
    source = source_path.read_text(encoding="utf-8")
    normalized = " ".join(source.split())
    assert "mpsc::channel::<ResponseEventEnvelope>(buffer_size)" in normalized
    assert "mpsc::channel::<ConsumerAck>(buffer_size)" in normalized
    assert "outstanding" in source
    assert "buffer_size" in source
    assert "ConsumerAckKind::Converted" in source
    assert "ConsumerAckKind::Discarded" in source
    assert "ConsumerAckKind::ConversionFailed" in source
    assert "Vec<Row>" not in source
