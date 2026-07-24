from pathlib import Path


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
