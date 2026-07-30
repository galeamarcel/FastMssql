from __future__ import annotations

import ast
from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[1]
CARGO_MANIFEST = ROOT / "Cargo.toml"
POOL_MANAGER = ROOT / "src/pool_manager.rs"
CORE_STUB = ROOT / "python/fastmssql/fastmssql.pyi"
README = ROOT / "README.md"
RUST_WORKFLOW = ROOT / ".github/workflows/rust-unit-tests.yml"
WINDOWS_WORKFLOW = ROOT / ".github/workflows/named-instance-windows.yml"
NAMED_TEST_SOURCES = (
    ROOT / "tests/test_named_instance_contract.py",
    ROOT / "tests/sql_auth_strict/test_named_instance_strict.py",
)


def _required_text(path: Path) -> str:
    assert path.is_file(), f"missing named-instance artifact: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^async fn {re.escape(name)}\b.*?(?=^async fn |^fn |^impl |^#\[cfg|\Z)",
        source,
    )
    assert match is not None, f"missing Rust function {name}"
    return match.group(0)


def test_root_build_enables_only_the_existing_tokio_browser_adapter() -> None:
    with CARGO_MANIFEST.open("rb") as source:
        manifest = tomllib.load(source)

    dependency = manifest["dependencies"]["tiberius"]
    assert dependency["path"] == "vendor/tiberius"
    assert dependency["default-features"] is False
    assert dependency["features"] == [
        "chrono",
        "tds73",
        "rustls",
        "sql-browser-tokio",
    ]


def test_one_pure_classifier_owns_the_initial_transport_matrix() -> None:
    source = _required_text(POOL_MANAGER)

    assert source.count("enum InitialTarget") == 1
    assert source.count("fn classify_initial_target") == 1
    assert "config.has_instance_name()" in source
    assert "config.has_explicit_port()" in source
    assert "InitialTarget::SqlBrowser" in source
    assert "InitialTarget::Direct(config.get_addr())" in source

    classifier = re.search(
        r"(?ms)^fn classify_initial_target\b.*?(?=^async fn |^fn |\Z)",
        source,
    )
    assert classifier is not None
    classifier_source = classifier.group(0)
    assert "TcpStream::connect" not in classifier_source
    assert "connect_named" not in classifier_source


def test_discovery_and_direct_stream_opening_are_explicitly_separated() -> None:
    source = _required_text(POOL_MANAGER)

    assert "use tiberius::SqlBrowser;" in source
    initial = _function_body(source, "open_initial_stream")
    direct = _function_body(source, "open_direct_stream")
    assert "classify_initial_target(config)" in initial
    assert "<TcpStream as SqlBrowser>::connect_named(config)" in initial
    assert "PoolConnectionError::Discovery" in initial
    assert "TcpStream::connect" in direct
    assert "connect_named" not in direct

    routing = source.split(
        "Err(tiberius::error::Error::Routing { host, port }) =>",
        maxsplit=1,
    )[1].split("Err(error) =>", maxsplit=1)[0]
    assert "open_direct_stream" in routing
    assert "classify_initial_target" not in routing
    assert "connect_named" not in routing


def test_discovery_error_has_the_stable_connection_stage_metadata() -> None:
    source = _required_text(POOL_MANAGER)

    assert "PoolConnectionError::Discovery" in source
    for token in (
        'setattr("stage", "sql_browser_discovery")',
        'setattr("retryable", true)',
        'setattr("connection_discarded", false)',
        'setattr("outcome_unknown", false)',
    ):
        assert token in source


def test_stub_and_readme_document_the_same_transport_precedence() -> None:
    stub = _required_text(CORE_STUB)
    readme = _required_text(README)

    assert (
        "Named SQL Server instance. Without an explicit port, FastMssql uses "
        "SQL Browser discovery over UDP 1434; an explicit port bypasses discovery."
        in " ".join(stub.split())
    )
    for token in (
        "Named instances and SQL Browser",
        'instance_name="SQLEXPRESS"',
        r"Server=tcp:db-host\SQLEXPRESS",
        r"Server=tcp:db-host\SQLEXPRESS,51433",
        "explicit port bypasses SQL Browser",
    ):
        assert token in readme


def test_installed_wheel_gate_runs_named_instance_contracts() -> None:
    workflow = _required_text(RUST_WORKFLOW)
    normalized = " ".join(workflow.replace("\\", " ").split())

    assert (
        "cargo test --manifest-path vendor/tiberius/Cargo.toml "
        "--no-default-features --features "
        "chrono,tds73,rustls,sql-browser-tokio --lib"
    ) in normalized
    assert "tests/test_named_instance_contract.py" in workflow
    assert workflow.index("uv pip install") < workflow.index(
        "tests/test_named_instance_contract.py"
    )


def test_windows_named_instance_gate_is_read_only_and_first_party() -> None:
    workflow = _required_text(WINDOWS_WORKFLOW)

    assert "permissions:\n  contents: read" in workflow
    assert "runs-on: windows-2022" in workflow
    assert "persist-credentials: false" in workflow
    assert "workflow_dispatch:" in workflow
    assert "continue-on-error" not in workflow
    assert "pytest.skip" not in workflow
    assert "|| true" not in workflow
    assert "download.microsoft.com" in workflow
    assert "Get-AuthenticodeSignature" in workflow
    assert 'Status -ne "Valid"' in workflow
    assert "/INSTANCENAME=SQLEXPRESS" in workflow
    assert "/SECURITYMODE=SQL" in workflow
    assert "/TCPENABLED=1" in workflow
    assert "/BROWSERSVCSTARTUPTYPE=Automatic" in workflow
    assert "Set-Service SQLBrowser -StartupType Automatic" in workflow
    assert "Start-Service SQLBrowser" in workflow
    assert "sqlcmd" in workflow
    assert r"localhost\SQLEXPRESS" in workflow
    assert "SERVERPROPERTY('InstanceName')" in workflow
    assert "fastmssql-0.7.7-" in workflow
    assert 'python-version: "3.13"' in workflow
    assert "PYTHONPATH" in workflow
    assert "actions/upload-artifact@" in workflow

    for forbidden in (
        "chocolatey",
        "choco ",
        "setup-sql-server",
        "install-sql-server",
        "sqlserver-actions",
        "contents: write",
        "id-token: write",
        "pull-requests: write",
    ):
        assert forbidden not in workflow.lower()

    smoke = workflow.split(
        "# BEGIN FASTMSSQL_NAMED_INSTANCE_SMOKE",
        maxsplit=1,
    )[1].split(
        "# END FASTMSSQL_NAMED_INSTANCE_SMOKE",
        maxsplit=1,
    )[0]
    assert 'instance_name="SQLEXPRESS"' in smoke
    assert "port=" not in smoke
    assert ",1433" not in smoke
    assert ",1434" not in smoke


def test_required_named_instance_tests_have_no_skip_or_swallowed_exception() -> None:
    for path in NAMED_TEST_SOURCES:
        tree = ast.parse(_required_text(path), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                rendered = ast.unparse(node.func)
                assert rendered not in {"pytest.skip", "pytest.xfail"}
            if isinstance(node, ast.ExceptHandler) and node.type is not None:
                caught = ast.unparse(node.type)
                assert caught not in {"Exception", "BaseException"}
