import ast
from pathlib import Path

from sql_auth_strict.cases import source_case_ids, spec_case_ids
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.conftest import redact_message


ROOT = Path(__file__).resolve().parents[2]


def test_sql_auth_repository_contract_files_exist() -> None:
    required = {
        ROOT / ".env.sql-auth.example",
        ROOT / "docker-compose.sql-auth.yml",
    }
    assert {path for path in required if not path.is_file()} == set()


def test_local_secrets_and_artifacts_are_ignored() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env.sql-auth.local" in ignored
    assert ".artifacts/sql-auth/" in ignored


def test_sql_auth_orchestration_files_exist_and_are_executable() -> None:
    scripts = (
        ROOT / "scripts/sql_auth/wait_for_sql.sh",
        ROOT / "scripts/sql_auth/provision.sh",
        ROOT / "scripts/sql_auth/provision.sql",
    )
    assert all(path.is_file() for path in scripts)
    assert all(path.stat().st_mode & 0o111 for path in scripts[:2])


def test_config_redacts_password(monkeypatch) -> None:
    monkeypatch.setenv(
        "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD", "NeverPrintMe_2026!"
    )
    config = SqlAuthConfig.from_env(require_all=False)
    assert "NeverPrintMe_2026!" not in repr(config)


def test_approved_spec_contains_226_unique_case_ids() -> None:
    spec = ROOT / (
        "docs/superpowers/specs/"
        "2026-07-24-fastmssql-sql-auth-validation-design.md"
    )
    ids = spec_case_ids(spec)
    assert len(ids) == 226


def test_result_messages_redact_every_nonempty_password() -> None:
    assert redact_message(
        "owner=OwnerSecret readonly=ReadonlySecret",
        ("OwnerSecret", "", "ReadonlySecret"),
    ) == "owner=<redacted> readonly=<redacted>"


def test_every_spec_case_is_attached_to_test_source() -> None:
    spec = ROOT / (
        "docs/superpowers/specs/"
        "2026-07-24-fastmssql-sql-auth-validation-design.md"
    )
    test_sources = sorted(
        path
        for path in (ROOT / "tests/sql_auth_strict").glob("test_*.py")
        if path.name != "test_matrix_contract.py"
    )
    assert source_case_ids(test_sources) == spec_case_ids(spec)


def test_strict_tests_do_not_swallow_failures() -> None:
    violations: list[str] = []
    for path in sorted((ROOT / "tests/sql_auth_strict").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        broad_handlers = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
            and (
                node.type is None
                or (
                    isinstance(node.type, ast.Name)
                    and node.type.id == "Exception"
                )
            )
        ]
        if broad_handlers:
            violations.append(str(path.relative_to(ROOT)))
    assert violations == []
