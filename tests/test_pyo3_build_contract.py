from __future__ import annotations

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]
CARGO_MANIFEST = ROOT / "Cargo.toml"
PYPROJECT = ROOT / "pyproject.toml"
WORKFLOW = ROOT / ".github" / "workflows" / "rust-unit-tests.yml"
SQL_AUTH_RUNNER = ROOT / "scripts" / "sql_auth" / "run_all.sh"


def _load_toml(path: Path) -> dict[str, object]:
    assert path.is_file(), f"missing TOML contract input: {path.relative_to(ROOT)}"
    with path.open("rb") as source:
        return tomllib.load(source)


def _read_required(path: Path) -> str:
    assert path.is_file(), f"missing build gate: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def test_default_pyo3_features_keep_generic_cargo_linkable() -> None:
    cargo = _load_toml(CARGO_MANIFEST)
    pyo3 = cargo["dependencies"]["pyo3"]

    assert pyo3["version"] == "0.29.0"
    assert pyo3["features"] == ["abi3-py311", "chrono"]
    assert cargo["lib"]["crate-type"] == ["cdylib"]


def test_maturin_owns_extension_build_mode() -> None:
    pyproject = _load_toml(PYPROJECT)

    assert pyproject["build-system"]["requires"] == ["maturin>=1.9.4,<2.0"]
    assert pyproject["build-system"]["build-backend"] == "maturin"
    assert pyproject["tool"]["maturin"]["features"] == ["pyo3/abi3-py311"]


def test_hosted_gate_covers_all_supported_desktop_platforms() -> None:
    workflow = _read_required(WORKFLOW)

    assert "push:" in workflow
    assert "pull_request:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "runs-on: ${{ matrix.os }}" in workflow
    assert "fail-fast: false" in workflow
    for runner in ("ubuntu-latest", "macos-latest", "windows-latest"):
        assert workflow.count(f"- {runner}") == 1
    assert 'RUST_TOOLCHAIN: "1.94.0"' in workflow
    assert 'PYTHON_VERSION: "3.13"' in workflow
    assert "timeout-minutes: 30" in workflow
    assert (
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
        in workflow
    )
    assert "astral-sh/setup-uv@v8.3.2" in workflow
    assert "python-version: ${{ env.PYTHON_VERSION }}" in workflow
    assert "activate-environment: true" in workflow
    assert "no-project: true" in workflow
    assert "PYO3_PYTHON: python" in workflow
    assert "cargo build --locked" in workflow
    assert "cargo test --locked" in workflow


def test_hosted_gate_has_no_linker_or_failure_bypass() -> None:
    workflow = _read_required(WORKFLOW)

    forbidden = (
        "continue-on-error: true",
        "|| true",
        "RUSTFLAGS",
        "PYO3_BUILD_EXTENSION_MODULE",
        "Python.framework",
        "libpython",
        "/opt/homebrew",
    )
    for token in forbidden:
        assert token not in workflow


def test_linux_runner_exposes_managed_python_library_to_runtime_loader() -> None:
    """PyO3 Rust tests embed Python, so Linux must resolve its shared library."""
    workflow = _read_required(WORKFLOW)

    assert "if: runner.os == 'Linux'" in workflow
    assert 'sysconfig.get_config_var("LIBDIR")' in workflow
    assert 'sysconfig.get_config_var("LDLIBRARY")' in workflow
    assert "LD_LIBRARY_PATH=" in workflow
    assert "${GITHUB_ENV}" in workflow
    assert "/home/runner" not in workflow
    assert workflow.index("LD_LIBRARY_PATH=") < workflow.index(
        "cargo test --locked"
    )


def test_sql_auth_runner_uses_locked_cargo_tests() -> None:
    runner = _read_required(SQL_AUTH_RUNNER)

    assert "record cargo-test cargo test --locked" in runner
    assert "record cargo-test cargo test\n" not in runner


def test_hosted_gate_builds_extension_and_checks_configuration_contracts() -> None:
    workflow = _read_required(WORKFLOW)

    assert "uvx --from maturin==1.14.1 maturin build" in workflow
    assert "uv pip install" in workflow
    assert '--python "${contract_python}"' in workflow
    normalized_workflow = " ".join(workflow.replace("\\", " ").split())
    assert (
        "-m pytest --noconftest "
        "tests/test_pool_config_default_contract.py "
        "tests/test_timeout_config_contract.py "
        "tests/test_lifecycle_contract.py -q"
        in normalized_workflow
    )
    assert (
        "-m pytest tests/test_timeout_config_contract.py"
        not in workflow
    )
    assert workflow.index("cargo test --locked") < workflow.index(
        "uvx --from maturin==1.14.1 maturin build"
    )
    assert workflow.index("uv pip install") < workflow.index(
        "tests/test_pool_config_default_contract.py"
    )
