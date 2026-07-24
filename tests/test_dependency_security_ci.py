from pathlib import Path
import stat


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "dependency-security.yml"
BUILD_WORKFLOW = ROOT / ".github" / "workflows" / "build-wheels.yml"
AUDIT_SCRIPT = ROOT / "scripts" / "security" / "audit_dependencies.sh"


def _read_required(path: Path) -> str:
    assert path.is_file(), f"missing dependency-security gate: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def test_dependency_security_workflow_is_triggered_with_least_privilege() -> None:
    workflow = _read_required(WORKFLOW)

    assert "push:" in workflow
    assert "pull_request:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "workflow_call:" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "continue-on-error: true" not in workflow
    assert "|| true" not in workflow


def test_dependency_security_toolchain_and_actions_are_pinned() -> None:
    workflow = _read_required(WORKFLOW)

    assert "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683" in workflow
    assert 'RUST_TOOLCHAIN: "1.94.0"' in workflow
    assert 'CARGO_AUDIT_VERSION: "0.22.2"' in workflow
    assert "cargo install cargo-audit" in workflow
    assert '--version "${CARGO_AUDIT_VERSION}" --locked' in workflow


def test_dependency_security_script_is_strict_and_shared_with_ci() -> None:
    workflow = _read_required(WORKFLOW)
    audit_script = _read_required(AUDIT_SCRIPT)

    assert AUDIT_SCRIPT.stat().st_mode & stat.S_IXUSR
    assert "set -euo pipefail" in audit_script
    assert 'cargo audit --deny warnings "$@"' in audit_script
    assert "scripts/security/audit_dependencies.sh" in workflow


def test_release_build_and_publish_depend_on_dependency_security() -> None:
    build_workflow = _read_required(BUILD_WORKFLOW)

    assert "dependency-security:" in build_workflow
    assert "uses: ./.github/workflows/dependency-security.yml" in build_workflow
    assert build_workflow.count("needs: dependency-security") >= 2
    assert "needs: [dependency-security, build-wheels, build-sdist]" in build_workflow
