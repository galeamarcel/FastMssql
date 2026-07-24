# FastMssql SQL Authentication Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and execute a reproducible strict SQL-authentication integration suite for every case in the approved 226-case FastMssql matrix against a dedicated SQL Server 2022 Developer container.

**Architecture:** A repository-local Docker Compose environment provisions isolated SQL-auth logins and databases. A strict pytest plugin records matrix IDs and outcomes, focused test modules exercise each API/behavior category, and report tooling merges the results with the approved specification. The upstream non-Azure suite remains a separate regression lane; every FastMssql defect receives a minimal reproduction and a separate fix branch before any publication.

**Tech Stack:** Docker Compose, `mcr.microsoft.com/mssql/server:2022-latest`, SQL Server SQL authentication, Bash, Python 3.13, pytest/pytest-asyncio, Maturin, PyO3, Rust 1.94, FastMssql 0.7.7, stdlib JSON/XML/AST tooling.

## Global Constraints

- Use branch `test/sql-auth-validation`; never commit directly to `master`.
- Container name is exactly `fastmssql-sql-auth-dev`.
- Image is exactly `mcr.microsoft.com/mssql/server:2022-latest`.
- Edition is exactly `MSSQL_PID=Developer`.
- Bind only `127.0.0.1:14334` to container port `1433`.
- Use `platform: linux/amd64` and record that AMD64 emulation on ARM64 is unsupported by Microsoft.
- Never stop, restart, pause, inspect secrets from, or otherwise mutate any Docker container except `fastmssql-sql-auth-dev`.
- Exercise SQL username/password authentication only; exclude Windows and all Azure authentication paths.
- Use `sa` only for provisioning and environment verification.
- Run ordinary tests as `fastmssql_owner`; use `fastmssql_readonly` and `fastmssql_denied` only for authorization cases.
- Never commit `.env.sql-auth.local`, passwords, complete connection strings, or generated test artifacts.
- The strict suite runs serially; concurrency is created deliberately inside individual tests.
- Strict tests contain no bare `except`, no `except Exception`, no runtime skip for supported behavior, and no assertion accepting contradictory outcomes.
- Every approved matrix identifier must be attached to at least one collected strict test and must have exactly one final status.
- A failing supported behavior is reported as a defect, not weakened, skipped, or converted into an accepted alternative.
- Design/test-only changes keep package version `0.7.7`; a library fix requires a dedicated fix plan and version decision.
- Do not create/push a fork or open a pull request without explicit user approval.

## File Structure

### Repository and container contracts

- Modify `.gitignore` to exclude `.env.sql-auth.local` and `.artifacts/sql-auth/`.
- Create `.env.sql-auth.example` with names and non-secret example values.
- Create `docker-compose.sql-auth.yml` for the one dedicated SQL Server service and volume.
- Create `scripts/sql_auth/create_env.py` to generate an ignored local env file
  with random test-only passwords.
- Create `scripts/sql_auth/wait_for_sql.sh` to poll only the named container.
- Create `scripts/sql_auth/provision.sql` to idempotently create databases, logins, users, and role memberships.
- Create `scripts/sql_auth/provision.sh` to validate the local env file and run `provision.sql`.
- Create `scripts/sql_auth/run_all.sh` to run build, strict, upstream, resilience/load, and reporting lanes with separate artifacts.

### Strict test harness

- Create `tests/sql_auth_strict/__init__.py` as the package boundary.
- Create `tests/sql_auth_strict/config.py` for `SqlAuthConfig` and redacted connection-string construction.
- Create `tests/sql_auth_strict/cases.py` for the `case(*ids)` marker, specification ID extraction, and AST marker extraction.
- Create `tests/sql_auth_strict/helpers.py` for SQL scalar helpers, safe identifiers, tracked cleanup, timings, session counting, and Docker target validation.
- Create `tests/sql_auth_strict/conftest.py` for fixtures and case-result JSON hooks.
- Create `tests/sql_auth_strict/test_matrix_contract.py` for repository safety and complete ID coverage.

### Focused test modules

- Create `tests/sql_auth_strict/test_environment_auth.py` for `ENV-*` and `AUTH-*`.
- Create `tests/sql_auth_strict/test_connection.py` for `CONN-*`.
- Create `tests/sql_auth_strict/test_pool.py` for `POOL-*`.
- Create `tests/sql_auth_strict/test_sql_features.py` for `SQL-*`.
- Create `tests/sql_auth_strict/test_parameters_strict.py` for `PARAM-*`.
- Create `tests/sql_auth_strict/test_type_mapping_strict.py` for `TYPE-*`.
- Create `tests/sql_auth_strict/test_results_strict.py` for `RESULT-*`.
- Create `tests/sql_auth_strict/test_batch_strict.py` for `BATCH-*`.
- Create `tests/sql_auth_strict/test_transactions_strict.py` for `TX-*`.
- Create `tests/sql_auth_strict/test_async_strict.py` for `ASYNC-*`.
- Create `tests/sql_auth_strict/test_errors_tls.py` for `ERR-*` and `TLS-*`.
- Create `tests/sql_auth_strict/test_resilience_load.py` for `RES-*` and `LOAD-*`.

### Reporting and project configuration

- Modify `pyproject.toml` to register `sql_auth_strict`, `resilience`, and `load` markers.
- Create `scripts/sql_auth/generate_report.py` to merge specification IDs, strict result JSON, JUnit XML, upstream results, and environment metadata.
- Create `docs/SQL_AUTH_TEST_MATRIX.md` as generated per-case evidence.
- Create `docs/SQL_AUTH_TEST_REPORT.md` as the human-readable result and risk report.

---

### Task 1: Repository safety contract and Compose definition

**Files:**
- Modify: `.gitignore`
- Create: `.env.sql-auth.example`
- Create: `docker-compose.sql-auth.yml`
- Create: `scripts/sql_auth/create_env.py`
- Create: `tests/sql_auth_strict/__init__.py`
- Create: `tests/sql_auth_strict/test_matrix_contract.py`

**Interfaces:**
- Consumes: approved container/database/login names from the specification.
- Produces: `docker-compose.sql-auth.yml` service `sqlserver`; ignored local secret/artifact paths; repository contract tests used by every later task.

- [ ] **Step 1: Add the failing repository contract test**

Create the initial portion of `tests/sql_auth_strict/test_matrix_contract.py`:

```python
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
```

- [ ] **Step 2: Run the contract test and verify RED**

Run:

```bash
uv run pytest tests/sql_auth_strict/test_matrix_contract.py -q
```

Expected: FAIL because `.env.sql-auth.example` and
`docker-compose.sql-auth.yml` do not exist and the new ignore entries are
absent.

- [ ] **Step 3: Add the local-only paths to `.gitignore`**

Append exactly:

```gitignore

# Local SQL-auth integration environment
.env.sql-auth.local
.artifacts/sql-auth/
```

- [ ] **Step 4: Add the sanitized environment example**

Create `.env.sql-auth.example`:

```dotenv
FASTMSSQL_SQL_AUTH_HOST=127.0.0.1
FASTMSSQL_SQL_AUTH_PORT=14334
FASTMSSQL_SQL_AUTH_DATABASE=fastmssql_validation
FASTMSSQL_SQL_AUTH_UPSTREAM_DATABASE=fastmssql_upstream_regression
FASTMSSQL_SQL_AUTH_SA_USER=sa
FASTMSSQL_SQL_AUTH_SA_PASSWORD=EXAMPLE_ONLY_NOT_A_CREDENTIAL
FASTMSSQL_SQL_AUTH_OWNER_USER=fastmssql_owner
FASTMSSQL_SQL_AUTH_OWNER_PASSWORD=EXAMPLE_ONLY_NOT_A_CREDENTIAL
FASTMSSQL_SQL_AUTH_READONLY_USER=fastmssql_readonly
FASTMSSQL_SQL_AUTH_READONLY_PASSWORD=EXAMPLE_ONLY_NOT_A_CREDENTIAL
FASTMSSQL_SQL_AUTH_DENIED_USER=fastmssql_denied
FASTMSSQL_SQL_AUTH_DENIED_PASSWORD=EXAMPLE_ONLY_NOT_A_CREDENTIAL
```

The repeated value is intentionally unusable as an actual environment secret.
The generator in the next step creates distinct local-only passwords.

- [ ] **Step 5: Add the local secret generator**

Create `scripts/sql_auth/create_env.py`:

```python
from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / ".env.sql-auth.local"


def password(label: str) -> str:
    return f"{label}_{secrets.token_urlsafe(24)}!Aa1"


def render() -> str:
    values = {
        "FASTMSSQL_SQL_AUTH_HOST": "127.0.0.1",
        "FASTMSSQL_SQL_AUTH_PORT": "14334",
        "FASTMSSQL_SQL_AUTH_DATABASE": "fastmssql_validation",
        "FASTMSSQL_SQL_AUTH_UPSTREAM_DATABASE": (
            "fastmssql_upstream_regression"
        ),
        "FASTMSSQL_SQL_AUTH_SA_USER": "sa",
        "FASTMSSQL_SQL_AUTH_SA_PASSWORD": password("Sa"),
        "FASTMSSQL_SQL_AUTH_OWNER_USER": "fastmssql_owner",
        "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD": password("Owner"),
        "FASTMSSQL_SQL_AUTH_READONLY_USER": "fastmssql_readonly",
        "FASTMSSQL_SQL_AUTH_READONLY_PASSWORD": password("Readonly"),
        "FASTMSSQL_SQL_AUTH_DENIED_USER": "fastmssql_denied",
        "FASTMSSQL_SQL_AUTH_DENIED_PASSWORD": password("Denied"),
    }
    return "".join(f"{key}={value}\n" for key, value in values.items())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if OUTPUT.exists() and not args.force:
        print(f"kept existing {OUTPUT}")
        return 0
    OUTPUT.write_text(render(), encoding="utf-8")
    os.chmod(OUTPUT, 0o600)
    print(f"created {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Add the dedicated Compose service**

Create `docker-compose.sql-auth.yml`:

```yaml
name: fastmssql-sql-auth

services:
  sqlserver:
    image: mcr.microsoft.com/mssql/server:2022-latest
    platform: linux/amd64
    container_name: fastmssql-sql-auth-dev
    hostname: fastmssql-sql-auth-dev
    environment:
      ACCEPT_EULA: "Y"
      MSSQL_PID: "Developer"
      MSSQL_SA_PASSWORD: "${FASTMSSQL_SQL_AUTH_SA_PASSWORD:?set in .env.sql-auth.local}"
    ports:
      - "127.0.0.1:14334:1433"
    volumes:
      - fastmssql_sql_auth_data:/var/opt/mssql
    healthcheck:
      test:
        - CMD-SHELL
        - >-
          /opt/mssql-tools18/bin/sqlcmd
          -S localhost -U sa -P "$$MSSQL_SA_PASSWORD"
          -C -b -Q "SET NOCOUNT ON; SELECT 1" >/dev/null
          || exit 1
      interval: 5s
      timeout: 5s
      retries: 60
      start_period: 30s
    restart: unless-stopped

volumes:
  fastmssql_sql_auth_data:
    name: fastmssql_sql_auth_data
```

- [ ] **Step 7: Generate local secrets, validate Compose, and verify GREEN**

Generate the ignored file without printing any password, then run:

```bash
uv run python scripts/sql_auth/create_env.py
docker compose \
  --env-file .env.sql-auth.local \
  -f docker-compose.sql-auth.yml \
  config --quiet
uv run pytest tests/sql_auth_strict/test_matrix_contract.py -q
git check-ignore .env.sql-auth.local .artifacts/sql-auth/
```

Expected: Compose exits 0; contract tests PASS; both paths are reported by
`git check-ignore`.

- [ ] **Step 8: Commit the repository contract**

```bash
git add .gitignore .env.sql-auth.example docker-compose.sql-auth.yml \
  scripts/sql_auth/create_env.py \
  tests/sql_auth_strict/__init__.py \
  tests/sql_auth_strict/test_matrix_contract.py
git commit -m "test: define isolated SQL auth container contract"
```

### Task 2: Idempotent SQL Server provisioning

**Files:**
- Create: `scripts/sql_auth/wait_for_sql.sh`
- Create: `scripts/sql_auth/provision.sql`
- Create: `scripts/sql_auth/provision.sh`
- Modify: `tests/sql_auth_strict/test_matrix_contract.py`

**Interfaces:**
- Consumes: service `sqlserver`, `.env.sql-auth.local`, and fixed database/login names.
- Produces: executable `scripts/sql_auth/provision.sh`; databases `fastmssql_validation` and `fastmssql_upstream_regression`; owner/readonly/denied SQL logins.

- [ ] **Step 1: Extend the failing contract test**

Add:

```python
def test_sql_auth_orchestration_files_exist_and_are_executable() -> None:
    scripts = (
        ROOT / "scripts/sql_auth/wait_for_sql.sh",
        ROOT / "scripts/sql_auth/provision.sh",
        ROOT / "scripts/sql_auth/provision.sql",
    )
    assert all(path.is_file() for path in scripts)
    assert all(path.stat().st_mode & 0o111 for path in scripts[:2])
```

- [ ] **Step 2: Run the focused contract and verify RED**

Run:

```bash
uv run pytest \
  tests/sql_auth_strict/test_matrix_contract.py::test_sql_auth_orchestration_files_exist_and_are_executable \
  -q
```

Expected: FAIL because the three files do not exist.

- [ ] **Step 3: Add the readiness script**

Create `scripts/sql_auth/wait_for_sql.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

readonly compose_file="docker-compose.sql-auth.yml"
readonly env_file=".env.sql-auth.local"
readonly container_name="fastmssql-sql-auth-dev"
readonly max_attempts=90

if [[ ! -f "${env_file}" ]]; then
  echo "missing ${env_file}; create it from .env.sql-auth.example" >&2
  exit 2
fi

for ((attempt = 1; attempt <= max_attempts; attempt++)); do
  status="$(
    docker inspect \
      --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      "${container_name}" 2>/dev/null || true
  )"
  if [[ "${status}" == "healthy" ]]; then
    exit 0
  fi
  sleep 2
done

docker compose --env-file "${env_file}" -f "${compose_file}" ps
docker logs --tail 200 "${container_name}"
echo "${container_name} did not become healthy within 180 seconds" >&2
exit 1
```

- [ ] **Step 4: Add the idempotent SQL provisioning**

Create `scripts/sql_auth/provision.sql` using fixed identifiers and sqlcmd
variables for passwords:

```sql
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_ID(N'fastmssql_validation') IS NULL
    CREATE DATABASE [fastmssql_validation];
IF DB_ID(N'fastmssql_upstream_regression') IS NULL
    CREATE DATABASE [fastmssql_upstream_regression];

IF SUSER_ID(N'fastmssql_owner') IS NULL
    CREATE LOGIN [fastmssql_owner]
      WITH PASSWORD = N'$(OwnerPassword)', CHECK_POLICY = OFF;
ELSE
    ALTER LOGIN [fastmssql_owner]
      WITH PASSWORD = N'$(OwnerPassword)', CHECK_POLICY = OFF;

IF SUSER_ID(N'fastmssql_readonly') IS NULL
    CREATE LOGIN [fastmssql_readonly]
      WITH PASSWORD = N'$(ReadonlyPassword)', CHECK_POLICY = OFF;
ELSE
    ALTER LOGIN [fastmssql_readonly]
      WITH PASSWORD = N'$(ReadonlyPassword)', CHECK_POLICY = OFF;

IF SUSER_ID(N'fastmssql_denied') IS NULL
    CREATE LOGIN [fastmssql_denied]
      WITH PASSWORD = N'$(DeniedPassword)', CHECK_POLICY = OFF;
ELSE
    ALTER LOGIN [fastmssql_denied]
      WITH PASSWORD = N'$(DeniedPassword)', CHECK_POLICY = OFF;

USE [fastmssql_validation];

IF USER_ID(N'fastmssql_owner') IS NULL
    CREATE USER [fastmssql_owner] FOR LOGIN [fastmssql_owner];
IF USER_ID(N'fastmssql_readonly') IS NULL
    CREATE USER [fastmssql_readonly] FOR LOGIN [fastmssql_readonly];
IF USER_ID(N'fastmssql_denied') IS NULL
    CREATE USER [fastmssql_denied] FOR LOGIN [fastmssql_denied];

IF IS_ROLEMEMBER(N'db_owner', N'fastmssql_owner') <> 1
    ALTER ROLE [db_owner] ADD MEMBER [fastmssql_owner];
IF IS_ROLEMEMBER(N'db_datareader', N'fastmssql_readonly') <> 1
    ALTER ROLE [db_datareader] ADD MEMBER [fastmssql_readonly];
DENY INSERT, UPDATE, DELETE, CREATE TABLE TO [fastmssql_readonly];
DENY SELECT, INSERT, UPDATE, DELETE, CREATE TABLE TO [fastmssql_denied];

USE [fastmssql_upstream_regression];

IF USER_ID(N'fastmssql_owner') IS NULL
    CREATE USER [fastmssql_owner] FOR LOGIN [fastmssql_owner];
IF IS_ROLEMEMBER(N'db_owner', N'fastmssql_owner') <> 1
    ALTER ROLE [db_owner] ADD MEMBER [fastmssql_owner];

SELECT
  SERVERPROPERTY('Edition') AS edition,
  SERVERPROPERTY('ProductVersion') AS product_version,
  DB_ID(N'fastmssql_validation') AS validation_database_id,
  DB_ID(N'fastmssql_upstream_regression') AS upstream_database_id;
```

- [ ] **Step 5: Add the provisioning wrapper**

Create `scripts/sql_auth/provision.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

readonly root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly env_file="${root_dir}/.env.sql-auth.local"
readonly container_name="fastmssql-sql-auth-dev"

cd "${root_dir}"
if [[ ! -f "${env_file}" ]]; then
  echo "missing ${env_file}; create it from .env.sql-auth.example" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a

required=(
  FASTMSSQL_SQL_AUTH_SA_PASSWORD
  FASTMSSQL_SQL_AUTH_OWNER_PASSWORD
  FASTMSSQL_SQL_AUTH_READONLY_PASSWORD
  FASTMSSQL_SQL_AUTH_DENIED_PASSWORD
)
for variable in "${required[@]}"; do
  if [[ -z "${!variable:-}" ]]; then
    echo "missing ${variable} in ${env_file}" >&2
    exit 2
  fi
done

scripts/sql_auth/wait_for_sql.sh
docker exec -i "${container_name}" /opt/mssql-tools18/bin/sqlcmd \
  -S localhost -U sa -P "${FASTMSSQL_SQL_AUTH_SA_PASSWORD}" \
  -C -b \
  -v OwnerPassword="${FASTMSSQL_SQL_AUTH_OWNER_PASSWORD}" \
     ReadonlyPassword="${FASTMSSQL_SQL_AUTH_READONLY_PASSWORD}" \
     DeniedPassword="${FASTMSSQL_SQL_AUTH_DENIED_PASSWORD}" \
  < scripts/sql_auth/provision.sql
```

- [ ] **Step 6: Start and provision the dedicated container**

Run:

```bash
chmod +x scripts/sql_auth/wait_for_sql.sh scripts/sql_auth/provision.sh
bash -n scripts/sql_auth/wait_for_sql.sh scripts/sql_auth/provision.sh
docker compose \
  --env-file .env.sql-auth.local \
  -f docker-compose.sql-auth.yml \
  up -d
scripts/sql_auth/provision.sh
scripts/sql_auth/provision.sh
```

Expected: the container becomes healthy; both provisioning runs exit 0; the
result reports non-null database IDs and Developer Edition.

- [ ] **Step 7: Prove container identity and edition**

Run:

```bash
docker inspect fastmssql-sql-auth-dev \
  --format 'name={{.Name}} image={{.Config.Image}} status={{.State.Status}} health={{.State.Health.Status}}'
docker port fastmssql-sql-auth-dev 1433/tcp
```

Expected: only `fastmssql-sql-auth-dev`, image `2022-latest`, healthy state,
and `127.0.0.1:14334`.

- [ ] **Step 8: Verify GREEN and commit**

```bash
uv run pytest tests/sql_auth_strict/test_matrix_contract.py -q
git add scripts/sql_auth tests/sql_auth_strict/test_matrix_contract.py
git commit -m "test: provision SQL Server Developer for SQL auth"
```

### Task 3: Strict harness, fixtures, and matrix result plugin

**Files:**
- Create: `tests/sql_auth_strict/config.py`
- Create: `tests/sql_auth_strict/cases.py`
- Create: `tests/sql_auth_strict/helpers.py`
- Create: `tests/sql_auth_strict/conftest.py`
- Modify: `tests/sql_auth_strict/test_matrix_contract.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces `SqlAuthConfig.from_env() -> SqlAuthConfig`.
- Produces `SqlAuthConfig.connection_string(user, password, database=None, **options) -> str`.
- Produces `case(*case_ids: str) -> pytest.MarkDecorator`.
- Produces `spec_case_ids(path: Path) -> frozenset[str]`.
- Produces `source_case_ids(paths: Iterable[Path]) -> frozenset[str]`.
- Produces fixtures `sql_auth_config`, `owner_connection`, `readonly_connection`, `denied_connection`, `transaction_factory`, `unique_sql_name`, and `cleanup_registry`.
- Writes `.artifacts/sql-auth/strict-results.json` without secrets.

- [ ] **Step 1: Add RED tests for configuration and matrix parsing**

Add to `test_matrix_contract.py`:

```python
from tests.sql_auth_strict.cases import spec_case_ids
from tests.sql_auth_strict.config import SqlAuthConfig


def test_config_redacts_password(monkeypatch) -> None:
    monkeypatch.setenv("FASTMSSQL_SQL_AUTH_OWNER_PASSWORD", "NeverPrintMe_2026!")
    config = SqlAuthConfig.from_env(require_all=False)
    assert "NeverPrintMe_2026!" not in repr(config)


def test_approved_spec_contains_226_unique_case_ids() -> None:
    spec = ROOT / (
        "docs/superpowers/specs/"
        "2026-07-24-fastmssql-sql-auth-validation-design.md"
    )
    ids = spec_case_ids(spec)
    assert len(ids) == 226
```

- [ ] **Step 2: Run and verify RED**

```bash
uv run pytest tests/sql_auth_strict/test_matrix_contract.py -q
```

Expected: collection ERROR because `cases.py` and `config.py` are absent.

- [ ] **Step 3: Implement the environment configuration**

Create `config.py` with this public shape:

```python
from __future__ import annotations

from dataclasses import dataclass, field
import os


def _required(name: str, *, required: bool) -> str:
    value = os.getenv(name, "")
    if required and not value:
        raise RuntimeError(f"missing required environment variable {name}")
    return value


@dataclass(frozen=True, repr=False)
class SqlAuthConfig:
    host: str
    port: int
    database: str
    upstream_database: str
    sa_user: str
    sa_password: str = field(repr=False)
    owner_user: str
    owner_password: str = field(repr=False)
    readonly_user: str
    readonly_password: str = field(repr=False)
    denied_user: str
    denied_password: str = field(repr=False)

    @classmethod
    def from_env(cls, *, require_all: bool = True) -> "SqlAuthConfig":
        return cls(
            host=os.getenv("FASTMSSQL_SQL_AUTH_HOST", "127.0.0.1"),
            port=int(os.getenv("FASTMSSQL_SQL_AUTH_PORT", "14334")),
            database=os.getenv(
                "FASTMSSQL_SQL_AUTH_DATABASE", "fastmssql_validation"
            ),
            upstream_database=os.getenv(
                "FASTMSSQL_SQL_AUTH_UPSTREAM_DATABASE",
                "fastmssql_upstream_regression",
            ),
            sa_user=os.getenv("FASTMSSQL_SQL_AUTH_SA_USER", "sa"),
            sa_password=_required(
                "FASTMSSQL_SQL_AUTH_SA_PASSWORD", required=require_all
            ),
            owner_user=os.getenv(
                "FASTMSSQL_SQL_AUTH_OWNER_USER", "fastmssql_owner"
            ),
            owner_password=_required(
                "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD", required=require_all
            ),
            readonly_user=os.getenv(
                "FASTMSSQL_SQL_AUTH_READONLY_USER", "fastmssql_readonly"
            ),
            readonly_password=_required(
                "FASTMSSQL_SQL_AUTH_READONLY_PASSWORD", required=require_all
            ),
            denied_user=os.getenv(
                "FASTMSSQL_SQL_AUTH_DENIED_USER", "fastmssql_denied"
            ),
            denied_password=_required(
                "FASTMSSQL_SQL_AUTH_DENIED_PASSWORD", required=require_all
            ),
        )

    def connection_string(
        self,
        user: str,
        password: str,
        *,
        database: str | None = None,
        extra: str = "",
    ) -> str:
        suffix = f";{extra.strip(';')}" if extra else ""
        return (
            f"Server={self.host},{self.port};"
            f"Database={database or self.database};"
            f"User Id={user};Password={password};"
            f"Encrypt=True;TrustServerCertificate=True{suffix}"
        )

    def __repr__(self) -> str:
        return (
            "SqlAuthConfig("
            f"host={self.host!r}, port={self.port!r}, "
            f"database={self.database!r}, "
            f"upstream_database={self.upstream_database!r}, "
            f"owner_user={self.owner_user!r}, "
            f"readonly_user={self.readonly_user!r}, "
            f"denied_user={self.denied_user!r})"
        )
```

- [ ] **Step 4: Implement case markers and AST extraction**

Create `cases.py`:

```python
from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
import re

import pytest


CASE_PATTERN = re.compile(r"`([A-Z]+-\d{3})`")
CASE_MARKER = "sql_auth_case"


def case(*case_ids: str) -> pytest.MarkDecorator:
    if not case_ids or any(not re.fullmatch(r"[A-Z]+-\d{3}", item) for item in case_ids):
        raise ValueError(f"invalid SQL-auth case IDs: {case_ids!r}")
    return getattr(pytest.mark, CASE_MARKER)(*case_ids)


def spec_case_ids(path: Path) -> frozenset[str]:
    return frozenset(CASE_PATTERN.findall(path.read_text(encoding="utf-8")))


def source_case_ids(paths: Iterable[Path]) -> frozenset[str]:
    found: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "case":
                continue
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    found.add(argument.value)
    return frozenset(found)
```

- [ ] **Step 5: Implement focused helpers**

Create `helpers.py` with:

```python
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import re
import time
from uuid import uuid4

from fastmssql import Connection


IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,120}$")


def unique_sql_name(prefix: str) -> str:
    candidate = f"{prefix}_{uuid4().hex[:12]}"
    if not IDENTIFIER.fullmatch(candidate):
        raise ValueError(f"unsafe generated identifier {candidate!r}")
    return candidate


def quote_identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(f"unsafe SQL identifier {value!r}")
    return f"[{value}]"


async def scalar(connection: Connection, sql: str, params=None):
    result = await connection.query(sql, params)
    row = result.fetchone()
    if row is None or len(row) != 1:
        raise AssertionError(f"expected one scalar row, got {result.len()} rows")
    return row[0]


@dataclass
class CleanupRegistry:
    connection: Connection
    statements: list[str] = field(default_factory=list)

    def add(self, statement: str) -> None:
        self.statements.append(statement)

    async def cleanup(self) -> None:
        failures: list[str] = []
        for statement in reversed(self.statements):
            try:
                await self.connection.execute(statement)
            except BaseException as error:
                failures.append(f"{type(error).__name__}: {error}")
        if failures:
            raise AssertionError("cleanup failed: " + " | ".join(failures))


async def timed(coro) -> tuple[float, object]:
    started = time.monotonic()
    result = await coro
    return time.monotonic() - started, result


async def event_loop_ticks(stop: asyncio.Event, interval: float = 0.02) -> list[float]:
    ticks: list[float] = []
    while not stop.is_set():
        ticks.append(time.monotonic())
        await asyncio.sleep(interval)
    return ticks


def assert_dedicated_container(name: str) -> None:
    if name != "fastmssql-sql-auth-dev":
        raise RuntimeError(f"refusing disruptive Docker action for {name!r}")
```

The cleanup helper catches `BaseException` only to preserve and report cleanup
failures; the matrix contract must allow this one exact line and reject broad
handlers in test functions.

- [ ] **Step 6: Implement fixtures and result recording**

Create `conftest.py` with session config loading, connection factories, unique
name and cleanup fixtures. Implement `pytest_runtest_makereport` to collect the
`sql_auth_case` marker arguments for call/setup/teardown outcomes, redact the
configured passwords from messages, and write:

```json
{
  "schema_version": 1,
  "cases": {
    "ENV-001": {
      "outcome": "passed",
      "nodeid": "tests/sql_auth_strict/test_environment_auth.py::test_container_contract",
      "duration_seconds": 0.123,
      "message": ""
    }
  }
}
```

Use these exact hook signatures: `pytest_configure(config: pytest.Config) ->
None`, the hook wrapper `pytest_runtest_makereport(item: pytest.Item,
call: pytest.CallInfo)`, and `pytest_sessionfinish(session: pytest.Session,
exitstatus: int | pytest.ExitCode) -> None`.

- [ ] **Step 7: Register markers**

Add to `pyproject.toml` markers:

```toml
    "sql_auth_strict: strict SQL-authentication validation matrix",
    "resilience: disruptive tests targeting only fastmssql-sql-auth-dev",
    "load: bounded load and resource tests",
```

- [ ] **Step 8: Add complete matrix guardrails**

Extend `test_matrix_contract.py` to assert:

```python
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
        text = path.read_text(encoding="utf-8")
        if "except Exception" in text or re.search(r"(?m)^\s*except\s*:", text):
            violations.append(str(path.relative_to(ROOT)))
    assert violations == []
```

This coverage test remains RED until Tasks 4-10 attach all 226 IDs.

- [ ] **Step 9: Run focused harness tests and commit**

```bash
set -a
source .env.sql-auth.local
set +a
uv run pytest \
  tests/sql_auth_strict/test_matrix_contract.py \
  -k 'not every_spec_case_is_attached_to_test_source' -q
git add pyproject.toml tests/sql_auth_strict
git commit -m "test: add strict SQL auth matrix harness"
```

Expected: all selected harness tests PASS; only the intentionally deferred full
coverage test remains unexecuted until category files exist.

### Task 4: Environment and SQL-authentication cases

**Files:**
- Create: `tests/sql_auth_strict/test_environment_auth.py`

**Interfaces:**
- Consumes: `sql_auth_config`, connection factories, `case`, `scalar`.
- Produces: executable evidence for `ENV-001..007` and `AUTH-001..013`.

- [ ] **Step 1: Implement environment assertions**

Add tests decorated with:

```python
@case("ENV-001")
def test_dedicated_container_identity() -> None:
    completed = subprocess.run(
        [
            "docker",
            "inspect",
            "fastmssql-sql-auth-dev",
            "--format",
            "{{.Name}}|{{.Config.Image}}|{{.State.Status}}|"
            "{{.State.Health.Status}}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == (
        "/fastmssql-sql-auth-dev|"
        "mcr.microsoft.com/mssql/server:2022-latest|running|healthy"
    )

@case("ENV-002", "ENV-003")
async def test_server_reports_developer_edition_and_version(owner_connection) -> None:
    result = await owner_connection.query(
        """
        SELECT
          CAST(SERVERPROPERTY('Edition') AS NVARCHAR(128)) AS edition,
          CAST(SERVERPROPERTY('ProductVersion') AS NVARCHAR(128)) AS version,
          compatibility_level
        FROM sys.databases
        WHERE name = DB_NAME()
        """
    )
    row = result.fetchone()
    assert row is not None
    assert "Developer" in row["edition"]
    assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+", row["version"])
    assert row["compatibility_level"] >= 160
```

Cover the remaining environment IDs with exact database/principal queries,
idempotent second provisioning, source/config credential scans, and exercised
authentication-path assertions.

- [ ] **Step 2: Implement owner authentication paths**

Cover `AUTH-001..003` with connection string, individual parameters, and a
temporary SQL login whose password includes punctuation/delimiters. Create and
drop the temporary login through the `sa` provisioning connection; assert
`SUSER_SNAME()`, `ORIGINAL_LOGIN()`, and `USER_NAME()`.

- [ ] **Step 3: Implement invalid authentication and database cases**

Cover `AUTH-004..008` using exact failure types:

```python
@case("AUTH-005")
async def test_invalid_password_is_rejected(sql_auth_config) -> None:
    connection = Connection(
        sql_auth_config.connection_string(
            sql_auth_config.owner_user,
            sql_auth_config.owner_password + "-wrong",
        )
    )
    with pytest.raises((SqlError, SqlConnectionError)) as captured:
        await connection.connect()
    assert sql_auth_config.owner_password not in str(captured.value)
```

- [ ] **Step 4: Implement permission boundaries and redaction**

Cover `AUTH-009..013`. Create one owner-owned table, prove readonly SELECT,
assert denied DML/DDL SQL error code 229 or 262, prove denied user failure, and
scan every captured exception/repr for all configured passwords.

- [ ] **Step 5: Run category and commit**

```bash
set -a; source .env.sql-auth.local; set +a
uv run pytest tests/sql_auth_strict/test_environment_auth.py -vv
git add tests/sql_auth_strict/test_environment_auth.py
git commit -m "test: validate SQL authentication and environment"
```

Expected: all 20 case IDs report PASS or a concrete FastMssql defect; no skip.

### Task 5: Connection lifecycle and pool behavior

**Files:**
- Create: `tests/sql_auth_strict/test_connection.py`
- Create: `tests/sql_auth_strict/test_pool.py`

**Interfaces:**
- Produces evidence for `CONN-001..019` and `POOL-001..017`.

- [ ] **Step 1: Implement connection construction tests**

In `test_connection.py`, use one focused test or parameterized row per behavior:

```python
@case("CONN-001", "CONN-002")
@pytest.mark.parametrize("construction", ["connection_string", "individual"])
async def test_supported_connection_construction(
    construction, sql_auth_config
) -> None:
    if construction == "connection_string":
        connection = Connection(
            sql_auth_config.connection_string(
                sql_auth_config.owner_user,
                sql_auth_config.owner_password,
            )
        )
    else:
        connection = Connection(
            server=sql_auth_config.host,
            port=sql_auth_config.port,
            database=sql_auth_config.database,
            username=sql_auth_config.owner_user,
            password=sql_auth_config.owner_password,
            ssl_config=SslConfig.development(),
        )
    async with connection:
        assert await scalar(connection, "SELECT 1") == 1
```

Attach every `CONN-*` ID exactly once. Use `APP_NAME()`, invalid intent, malformed
ADO strings, a closed localhost port, lifecycle state checks, nested context
behavior, and exact `pool_stats()` invariants.

- [ ] **Step 2: Implement pool configuration and warmup**

In `test_pool.py`, cover presets, invalid boundaries, adaptive math, min-idle
warmup, and concurrent lazy initialization. Assert:

```python
stats = await connection.pool_stats()
assert stats["connected"] is True
assert 0 <= stats["idle_connections"] <= stats["connections"]
assert stats["active_connections"] == (
    stats["connections"] - stats["idle_connections"]
)
assert stats["connections"] <= stats["max_size"]
```

- [ ] **Step 3: Implement reuse, saturation, timeout, and retirement**

Use `WAITFOR DELAY '00:00:02'` with a small pool to hold checked-out
connections. Measure saturation timeout, release after success/error/cancel,
session IDs, idle timeout, max lifetime, checkout validation, and rapid
lifecycle session counts. Attach `POOL-007..017`.

- [ ] **Step 4: Run both modules and commit**

```bash
set -a; source .env.sql-auth.local; set +a
uv run pytest \
  tests/sql_auth_strict/test_connection.py \
  tests/sql_auth_strict/test_pool.py -vv
git add tests/sql_auth_strict/test_connection.py \
  tests/sql_auth_strict/test_pool.py
git commit -m "test: validate connection lifecycle and pooling"
```

Expected: all 36 case IDs have outcomes; no skip or swallowed error.

### Task 6: SQL execution and Python parameter conversion

**Files:**
- Create: `tests/sql_auth_strict/test_sql_features.py`
- Create: `tests/sql_auth_strict/test_parameters_strict.py`

**Interfaces:**
- Produces evidence for `SQL-001..025` and `PARAM-001..024`.

- [ ] **Step 1: Implement core query/DML/DDL cases**

Create unique permanent objects through the cleanup registry. For every DML
case, assert both affected row count and persisted rows. Cover SELECT shapes,
large results, raw `simple_query`, DDL, CTEs, joins/windows, OUTPUT, MERGE,
views, functions, procedures, triggers, identity/sequence/default/computed
columns.

Use exact SQL such as:

```python
@case("SQL-006", "SQL-007", "SQL-008", "SQL-009")
async def test_dml_row_counts_and_persisted_state(
    owner_connection, unique_sql_name, cleanup_registry
) -> None:
    table = unique_sql_name("dml")
    quoted = quote_identifier(table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {quoted}")
    await owner_connection.execute(
        f"CREATE TABLE {quoted} (id INT PRIMARY KEY, value NVARCHAR(100))"
    )
    assert await owner_connection.execute(
        f"INSERT INTO {quoted} (id, value) VALUES (@P1, @P2)", [1, "one"]
    ) == 1
    assert await owner_connection.execute(
        f"UPDATE {quoted} SET value=@P1 WHERE id=@P2", ["updated", 1]
    ) == 1
    assert await owner_connection.execute(
        f"UPDATE {quoted} SET value=@P1 WHERE id=@P2", ["missing", 99]
    ) == 0
    assert await owner_connection.execute(
        f"DELETE FROM {quoted} WHERE id=@P1", [1]
    ) == 1
    assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {quoted}") == 0
```

- [ ] **Step 2: Assert pooled session semantics**

Use deterministic repeated observations to document local temp table and SET
state behavior on `Connection`, then compare with `Transaction`. Do not demand
session persistence from a pool unless the implementation contract guarantees
it. Assert current runtime behavior explicitly for `SQL-021..024`.

- [ ] **Step 3: Implement parameterized Python-to-SQL coverage**

Use parameterized tables for `None`, every `TypedNull`, bool, integer
boundaries/overflow, floats including infinity/NaN behavior, Decimal
precision/scale, Unicode, binary-like inputs, temporal objects, UUID,
`Parameter`, `Parameters`, iterable expansion, malformed/nested values,
placeholder mismatches, repeated placeholders, and injection payloads.

For the SQL Server parameter limit, assert success at the highest form the
driver can encode without exceeding 2,100 and a stable failure above it. Record
the exact observed limit.

- [ ] **Step 4: Run modules and commit**

```bash
set -a; source .env.sql-auth.local; set +a
uv run pytest \
  tests/sql_auth_strict/test_sql_features.py \
  tests/sql_auth_strict/test_parameters_strict.py -vv
git add tests/sql_auth_strict/test_sql_features.py \
  tests/sql_auth_strict/test_parameters_strict.py
git commit -m "test: validate SQL execution and parameters"
```

Expected: all 49 case IDs have evidence with exact database assertions.

### Task 7: SQL-to-Python type mapping and result objects

**Files:**
- Create: `tests/sql_auth_strict/test_type_mapping_strict.py`
- Create: `tests/sql_auth_strict/test_results_strict.py`

**Interfaces:**
- Produces evidence for `TYPE-001..017` and `RESULT-001..015`.

- [ ] **Step 1: Implement a strict SQL type table**

Parameterize SQL expressions, expected Python classes, exact values, and NULL
values. Include every numeric, character, binary, temporal, GUID, XML, legacy
LOB, rowversion, and unsupported complex type named in the specification.

Example:

```python
@case("TYPE-001", "TYPE-002", "TYPE-003", "TYPE-004", "TYPE-005")
@pytest.mark.parametrize(
    ("expression", "expected_type", "expected_value"),
    [
        ("CAST(255 AS TINYINT)", int, 255),
        ("CAST(-32768 AS SMALLINT)", int, -32768),
        ("CAST(1 AS BIT)", bool, True),
        ("CAST(1.25 AS REAL)", float, pytest.approx(1.25)),
        ("CAST(-0.0001 AS DECIMAL(10,4))", Decimal, Decimal("-0.0001")),
        ("CAST(12.3400 AS MONEY)", Decimal, Decimal("12.3400")),
    ],
)
async def test_numeric_type_mapping(
    owner_connection, expression, expected_type, expected_value
) -> None:
    value = await scalar(owner_connection, f"SELECT {expression}")
    assert type(value) is expected_type
    assert value == expected_value
```

- [ ] **Step 2: Implement result access and state-machine tests**

Test `FastRow` name/index access, missing/out-of-range behavior, columns,
values, dict, repr/str, and `QueryStream` length, iteration, indexing, slices,
fetch methods, mixed fetch position, reset, empty sets, iterator protocol, and
conversion memory behavior.

Assert the actual sync/async iterator protocol by checking `iter(result)` and
`hasattr(result, "__aiter__")`; a documentation/runtime mismatch becomes a
defect instead of an accepted ambiguity.

- [ ] **Step 3: Run modules and commit**

```bash
set -a; source .env.sql-auth.local; set +a
uv run pytest \
  tests/sql_auth_strict/test_type_mapping_strict.py \
  tests/sql_auth_strict/test_results_strict.py -vv
git add tests/sql_auth_strict/test_type_mapping_strict.py \
  tests/sql_auth_strict/test_results_strict.py
git commit -m "test: validate SQL type and result mapping"
```

Expected: all 32 IDs have direct class/value/state evidence.

### Task 8: Batch, bulk, and dedicated transactions

**Files:**
- Create: `tests/sql_auth_strict/test_batch_strict.py`
- Create: `tests/sql_auth_strict/test_transactions_strict.py`

**Interfaces:**
- Produces evidence for `BATCH-001..020` and `TX-001..019`.

- [ ] **Step 1: Implement query/execute batch contracts**

Test empty, one, many, mixed parameterized queries; ordering; independent
result objects; errors; row counts; malformed item shapes; and full atomic
rollback. Prove rollback with a separate post-error SELECT.

- [ ] **Step 2: Implement bulk insert contracts**

Test basic/empty/mixed/null data, exact parameter chunk boundary, multiple
chunks, wide rows, quoted identifiers, malicious identifiers, row-width
mismatch, constraints, identity/default/computed/trigger behavior, and
cancellation cleanup.

- [ ] **Step 3: Implement dedicated transaction state and isolation**

Use one physical `Transaction` session and two independent owner connections.
Cover begin/commit/rollback/context management, manual termination, invalid
repeated transitions, close/reuse, forwarding methods, local temp/session
state, DDL rollback, savepoints, isolation visibility, blocking, deterministic
deadlock, cancellation, and concurrent call serialization.

The persistence assertion for rollback uses a separate connection:

```python
@case("TX-002", "TX-003")
async def test_explicit_commit_and_rollback(
    sql_auth_config, owner_connection, unique_sql_name
) -> None:
    table = unique_sql_name("tx")
    await owner_connection.execute(
        f"CREATE TABLE [{table}] (id INT PRIMARY KEY)"
    )
    try:
        transaction = Transaction(
            sql_auth_config.connection_string(
                sql_auth_config.owner_user,
                sql_auth_config.owner_password,
            )
        )
        await transaction.begin()
        await transaction.execute(f"INSERT INTO [{table}] VALUES (1)")
        await transaction.commit()
        assert await scalar(
            owner_connection, f"SELECT COUNT(*) FROM [{table}]"
        ) == 1

        await transaction.begin()
        await transaction.execute(f"INSERT INTO [{table}] VALUES (2)")
        await transaction.rollback()
        assert await scalar(
            owner_connection, f"SELECT COUNT(*) FROM [{table}]"
        ) == 1
    finally:
        await owner_connection.execute(f"DROP TABLE IF EXISTS [{table}]")
```

- [ ] **Step 4: Run modules and commit**

```bash
set -a; source .env.sql-auth.local; set +a
uv run pytest \
  tests/sql_auth_strict/test_batch_strict.py \
  tests/sql_auth_strict/test_transactions_strict.py -vv
git add tests/sql_auth_strict/test_batch_strict.py \
  tests/sql_auth_strict/test_transactions_strict.py
git commit -m "test: validate batch bulk and transactions"
```

Expected: all 39 case IDs have persistence/rollback/state evidence.

### Task 9: True async, errors, and TLS over SQL authentication

**Files:**
- Create: `tests/sql_auth_strict/test_async_strict.py`
- Create: `tests/sql_auth_strict/test_errors_tls.py`

**Interfaces:**
- Produces evidence for `ASYNC-001..013`, `ERR-001..015`, and `TLS-001..008`.

- [ ] **Step 1: Implement the sequential/concurrent timing gate**

Measure the sequential baseline first, then five concurrent one-second
`WAITFOR` calls with `PoolConfig(max_size=5, min_idle=5)`. Assert concurrent
elapsed time is below 2.5 seconds and
`sequential_elapsed / concurrent_elapsed >= 2.0`.

Run an event-loop ticker and a Python thread counter throughout database I/O.
Assert ticks span the wait and the Python thread counter increases.

- [ ] **Step 2: Implement cancellation and isolation cases**

Cover successful/failing task isolation, one shared wrapper, multiple wrappers,
`asyncio.wait_for`, post-cancel reuse, cancellation storms, cancellation while
waiting for pool capacity, dedicated transaction serialization, and conversion
event-loop responsiveness. Inspect `pool_stats()` before and after.

- [ ] **Step 3: Implement exact error taxonomy**

Generate syntax, missing object, duplicate, constraint, truncation/conversion,
arithmetic, deadlock, permission, connection, TLS, protocol/conversion, pool
reuse, and batch failures. For `SqlError`, assert `code`, `message`, and `state`.
Scan every error string for all configured passwords.

- [ ] **Step 4: Implement TLS cases with SQL credentials**

Exercise ADO connection-string TLS settings and individual
`SslConfig.development()`, disabled/login-only modes, untrusted certificate,
invalid CA path/content/extension, mutually exclusive trust settings, and SQL
principal identity after TLS negotiation.

- [ ] **Step 5: Run modules and commit**

```bash
set -a; source .env.sql-auth.local; set +a
uv run pytest \
  tests/sql_auth_strict/test_async_strict.py \
  tests/sql_auth_strict/test_errors_tls.py -vv
git add tests/sql_auth_strict/test_async_strict.py \
  tests/sql_auth_strict/test_errors_tls.py
git commit -m "test: prove async behavior errors and TLS"
```

Expected: all 36 case IDs have timing/state/error evidence.

### Task 10: Safe resilience and bounded load

**Files:**
- Create: `tests/sql_auth_strict/test_resilience_load.py`

**Interfaces:**
- Produces evidence for `RES-001..007` and `LOAD-001..007`.
- All disruptive subprocess calls pass through
  `assert_dedicated_container("fastmssql-sql-auth-dev")`.

- [ ] **Step 1: Implement the Docker target safety contract**

Add a non-disruptive test proving the helper rejects every other name and that
all subprocess command arrays contain the exact dedicated container.

- [ ] **Step 2: Implement pause/unpause and restart recovery**

Mark resilience tests:

```python
pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.resilience]
```

Use concrete command arrays such as
`subprocess.run(["docker", "pause", "fastmssql-sql-auth-dev"], check=True)`
and
`subprocess.run(["docker", "unpause", "fastmssql-sql-auth-dev"], check=True)`
without `shell=True`. Always register an `atexit`/fixture finalizer that
unpauses/starts the dedicated container and waits for health. Assert bounded
failure, new connection recovery, old pool behavior, explicit in-flight
transaction outcome, and target safety.

- [ ] **Step 3: Implement bounded load/resource cases**

Mark load tests with `pytest.mark.load`. Use fixed workload bounds:

- 1,000 short SELECTs at concurrency 20;
- result sizes 1,000, 10,000, and 50,000 rows;
- 100 repeated result-conversion cycles;
- bulk sizes 1, 100, 1,000, and 10,000 rows;
- 250 rapid lifecycle cycles;
- 500 mixed operations;
- one post-load smoke query.

Record elapsed time, RSS before/after, SQL session counts, and correctness.
Correctness/resource leaks fail; raw throughput remains diagnostic.

- [ ] **Step 4: Run resilience then load and commit**

```bash
set -a; source .env.sql-auth.local; set +a
uv run pytest \
  tests/sql_auth_strict/test_resilience_load.py \
  -m resilience -vv
uv run pytest \
  tests/sql_auth_strict/test_resilience_load.py \
  -m load -vv
git add tests/sql_auth_strict/test_resilience_load.py
git commit -m "test: validate SQL Server recovery and bounded load"
```

Expected: all 14 IDs have evidence; the dedicated container ends healthy.

### Task 11: Enforce full 226-case coverage

**Files:**
- Modify: `tests/sql_auth_strict/test_matrix_contract.py`
- Test: all `tests/sql_auth_strict/test_*.py`

**Interfaces:**
- Consumes all category test markers.
- Produces a hard gate proving spec/source/result one-to-one coverage.

- [ ] **Step 1: Run the previously deferred source coverage test**

```bash
uv run pytest \
  tests/sql_auth_strict/test_matrix_contract.py::test_every_spec_case_is_attached_to_test_source \
  -vv
```

Expected: PASS with exactly 226 unique spec IDs and exactly the same 226 source
IDs. A failure lists missing/extra IDs; fix the relevant test decorator rather
than weakening the contract.

- [ ] **Step 2: Add duplicate and collected-marker guards**

Parse every test source and assert each ID appears exactly once. During
`pytest_collection_finish`, compare collected marker IDs with spec IDs and
raise `pytest.UsageError` on missing, extra, or duplicate IDs.

- [ ] **Step 3: Run strict static and collection gates**

```bash
uv run pytest tests/sql_auth_strict/test_matrix_contract.py -vv
uv run pytest tests/sql_auth_strict --collect-only -q
rg -n 'except Exception|^[[:space:]]*except[[:space:]]*:' \
  tests/sql_auth_strict/test_*.py
rg -n 'pytest\\.skip|pytest\\.xfail' tests/sql_auth_strict/test_*.py
```

Expected: contract PASS; collection succeeds; both `rg` commands return no
strict-test violations.

- [ ] **Step 4: Commit the coverage gate**

```bash
git add tests/sql_auth_strict/test_matrix_contract.py \
  tests/sql_auth_strict/conftest.py
git commit -m "test: enforce complete SQL auth matrix coverage"
```

### Task 12: Upstream SQL-auth regression runner

**Files:**
- Create: `scripts/sql_auth/run_all.sh`
- Modify: `tests/sql_auth_strict/test_matrix_contract.py`

**Interfaces:**
- Produces `.artifacts/sql-auth/strict.xml`,
  `.artifacts/sql-auth/upstream.xml`, `.artifacts/sql-auth/load.xml`,
  `.artifacts/sql-auth/resilience.xml`, command logs, and exit-code files.

- [ ] **Step 1: Add a RED runner contract**

Assert `scripts/sql_auth/run_all.sh` exists, is executable, contains every
explicit Azure test exclusion, uses `-n 1`, and targets
`fastmssql_upstream_regression`.

- [ ] **Step 2: Implement the orchestration runner**

The script must:

1. source `.env.sql-auth.local`;
2. refuse to run if Docker target differs;
3. create `.artifacts/sql-auth`;
4. run `uv sync --locked --all-extras --dev`;
5. run `uv run maturin develop --release`;
6. run `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, and
   `cargo test`;
7. provision the container;
8. export the upstream `FASTMSSQL_TEST_*` variables pointing at port 14334 and
   `fastmssql_upstream_regression`;
9. run strict functional, async, resilience, and load lanes separately;
10. run the upstream suite serially with these exact ignores:

```text
tests/test_azure_auth_advanced.py
tests/test_azure_authentication.py
tests/test_azure_cli_path_validation.py
tests/test_transaction_azure_auth.py
tests/test_transaction_azure_auth_advanced.py
tests/sql_auth_strict
```

11. preserve every exit code instead of stopping before report generation;
12. run `generate_report.py`;
13. exit nonzero when any required strict lane or build check failed.

- [ ] **Step 3: Verify runner syntax and contract**

```bash
chmod +x scripts/sql_auth/run_all.sh
bash -n scripts/sql_auth/run_all.sh
uv run pytest tests/sql_auth_strict/test_matrix_contract.py -vv
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add scripts/sql_auth/run_all.sh \
  tests/sql_auth_strict/test_matrix_contract.py
git commit -m "test: orchestrate strict and upstream SQL auth lanes"
```

### Task 13: Machine-readable matrix and human report generation

**Files:**
- Create: `scripts/sql_auth/generate_report.py`
- Create: `docs/SQL_AUTH_TEST_MATRIX.md`
- Create: `docs/SQL_AUTH_TEST_REPORT.md`
- Test: `tests/sql_auth_strict/test_matrix_contract.py`

**Interfaces:**
- CLI:

```text
python scripts/sql_auth/generate_report.py
  --spec PATH
  --strict-results PATH
  --artifact-dir PATH
  --matrix-output PATH
  --report-output PATH
```

- Outputs deterministic Markdown without credentials.

- [ ] **Step 1: Add RED report generator tests**

Use temporary JSON/JUnit fixtures and assert:

- all 226 spec IDs appear;
- pass/fail/error/not-run are distinct;
- failures include node IDs and redacted messages;
- missing results become `NOT RUN`, never `PASS`;
- environment exclusions and ARM64 emulation risk appear;
- generated output contains no configured password.

- [ ] **Step 2: Implement `generate_report.py` with stdlib only**

Use `argparse`, `json`, `re`, `xml.etree.ElementTree`, `platform`,
`subprocess`, and `pathlib`. Do not add a new dependency. Sort by category and
numeric ID. Summarize each lane separately and include exact commands/exit
codes read from the artifact directory.

- [ ] **Step 3: Generate initial not-run documents**

Before the full run, generate documents in which every ID is `NOT RUN`. This
proves the generator never fabricates a pass.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/sql_auth_strict/test_matrix_contract.py -vv
uv run python scripts/sql_auth/generate_report.py \
  --spec docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  --strict-results .artifacts/sql-auth/strict-results.json \
  --artifact-dir .artifacts/sql-auth \
  --matrix-output docs/SQL_AUTH_TEST_MATRIX.md \
  --report-output docs/SQL_AUTH_TEST_REPORT.md
git add scripts/sql_auth/generate_report.py \
  docs/SQL_AUTH_TEST_MATRIX.md docs/SQL_AUTH_TEST_REPORT.md \
  tests/sql_auth_strict/test_matrix_contract.py
git commit -m "test: generate SQL auth evidence reports"
```

### Task 14: Execute the full matrix and classify evidence

**Files:**
- Modify: `docs/SQL_AUTH_TEST_MATRIX.md`
- Modify: `docs/SQL_AUTH_TEST_REPORT.md`
- Generated, ignored: `.artifacts/sql-auth/**`

**Interfaces:**
- Consumes all prior scripts/tests.
- Produces fresh authoritative results and a defect list.

- [ ] **Step 1: Capture the pre-run Docker inventory**

```bash
docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker inspect fastmssql-sql-auth-dev \
  --format '{{.Name}} {{.Config.Image}} {{.State.Status}} {{.State.Health.Status}}'
```

Save the non-secret output under `.artifacts/sql-auth/environment-before.txt`.

- [ ] **Step 2: Run the full orchestrator**

```bash
scripts/sql_auth/run_all.sh
```

Expected: all commands and exit codes are captured even if a lane finds a
defect. The runner itself exits nonzero until every required strict/build gate
passes.

- [ ] **Step 3: Classify every non-pass**

For each failing/error/not-run case, record exactly one classification:

- `FASTMSSQL_DEFECT`;
- `TEST_DEFECT`;
- `DOCUMENTATION_MISMATCH`;
- `UNSUPPORTED_SQL_TYPE`;
- `ENVIRONMENT_LIMITATION`;
- `UPSTREAM_TEST_CONFIDENCE_LIMITATION`.

No strict supported behavior remains unclassified.

- [ ] **Step 4: Verify the dedicated container is restored**

```bash
docker inspect fastmssql-sql-auth-dev \
  --format 'status={{.State.Status}} health={{.State.Health.Status}}'
```

Expected: `status=running health=healthy`.

- [ ] **Step 5: Regenerate reports with final pre-fix evidence**

Run the report CLI from Task 13 and verify:

```bash
rg -n 'NOT RUN|FAIL|ERROR' docs/SQL_AUTH_TEST_MATRIX.md
git diff --check
```

Every matching result must be explained in `docs/SQL_AUTH_TEST_REPORT.md`.

- [ ] **Step 6: Commit evidence, not ignored artifacts**

```bash
git add docs/SQL_AUTH_TEST_MATRIX.md docs/SQL_AUTH_TEST_REPORT.md
git commit -m "test: record SQL auth validation evidence"
```

### Task 15: Create exact plans for discovered library defects

**Files:**
- Create: `docs/superpowers/plans/2026-07-24-fastmssql-discovered-defects.md`
- Modify: `docs/SQL_AUTH_TEST_REPORT.md`

**Interfaces:**
- Consumes the concrete `FASTMSSQL_DEFECT` classifications from Task 14.
- Produces a follow-on plan containing real failing node IDs, real root-cause
  source paths, real branch names, and exact verification commands. If Task 14
  finds no library defect, the report records that result and no follow-on plan
  is created.

- [ ] **Step 1: Enumerate concrete library defects**

Run:

```bash
rg -n 'FASTMSSQL_DEFECT' docs/SQL_AUTH_TEST_REPORT.md
```

For every match, copy the case ID, pytest node ID, error class/code, and
artifact path into a defect inventory. Do not include test or environment
failures.

- [ ] **Step 2: Freeze each minimal reproduction**

Run each concrete failing node ID twice and save both outputs. Confirm the same
FastMssql behavior fails for the same reason. Diagnose unstable behavior before
planning code changes.

- [ ] **Step 3: Invoke systematic debugging for each reproduction**

Trace the failing public method through code-review-graph callers/callees,
inspect the Rust/Python source, test a single hypothesis at a time, and record
the evidence-backed root cause.

- [ ] **Step 4: Write the exact follow-on defect plan**

Create
`docs/superpowers/plans/2026-07-24-fastmssql-discovered-defects.md`. Give every
confirmed defect its own task containing:

- the real branch name beginning with `fix/sql-auth-`;
- the exact failing node ID and expected RED output;
- the exact Rust/Python files and functions to modify;
- the minimum implementation code;
- focused, category, strict, upstream, Rust, and full-orchestrator commands;
- version/release documentation decisions;
- the local commit command;
- an explicit publication-approval gate.

Do not write or modify library code in Task 15.

- [ ] **Step 5: Commit the concrete defect plan**

If the plan was created:

```bash
git add \
  docs/superpowers/plans/2026-07-24-fastmssql-discovered-defects.md \
  docs/SQL_AUTH_TEST_REPORT.md
git commit -m "docs: plan fixes for SQL auth validation defects"
```

Execute that reviewed follow-on plan before Task 16. If no library defect was
found, commit only the report statement and continue to Task 16.

### Task 16: Final completion audit

**Files:**
- Modify: `docs/SQL_AUTH_TEST_MATRIX.md`
- Modify: `docs/SQL_AUTH_TEST_REPORT.md`

**Interfaces:**
- Produces the requirement-by-requirement completion evidence for the original
  objective.

- [ ] **Step 1: Re-run every required verification from a clean state**

```bash
git status --short
scripts/sql_auth/run_all.sh
git diff --check
uv run pytest tests/sql_auth_strict/test_matrix_contract.py -vv
```

- [ ] **Step 2: Audit all 226 matrix IDs**

Assert programmatically:

```python
assert len(results) == 226
assert set(results) == spec_case_ids
assert all(result.status in {"PASS", "FAIL", "UNSUPPORTED", "EXCLUDED"} for result in results.values())
assert all(result.evidence for result in results.values())
```

`FAIL`, `UNSUPPORTED`, and `EXCLUDED` entries require explicit rationale and
must not be represented as completion of supported behavior.

- [ ] **Step 3: Audit the original user requirements**

Confirm with authoritative evidence:

- repository clone and commit baseline;
- dedicated Developer Edition container;
- SQL-auth only;
- strict case list and executed results;
- upstream regression run;
- true async evidence;
- all defects reproduced;
- fixes on separate local branches;
- fork/push/PR state;
- remaining platform risk.

- [ ] **Step 4: Update the final reports**

Add the exact verification commands, exit codes, pass/fail/skip counts,
container edition/build, Python/Rust/FastMssql versions, branch commits, defect
branches, and residual risks.

- [ ] **Step 5: Rebuild the code-review graph and inspect changes**

```bash
uvx code-review-graph build
uvx code-review-graph detect-changes --base master
```

Use the MCP `detect_changes`/`get_affected_flows` results if the CLI subcommand
is unavailable.

- [ ] **Step 6: Commit the audited reports**

```bash
git add docs/SQL_AUTH_TEST_MATRIX.md docs/SQL_AUTH_TEST_REPORT.md
git commit -m "docs: finalize FastMssql SQL auth validation"
```

- [ ] **Step 7: Request explicit fork/publication approval**

Present:

- local branch names and commits;
- every discovered/fixed defect;
- final test counts and failures;
- proposed fork name;
- exact proposed push commands copied from the final report;
- proposed upstream PRs.

Only after approval:

```bash
gh repo fork Rivendael/FastMssql --remote --remote-name fork
git push -u fork test/sql-auth-validation
```

Push each approved fix branch using its concrete branch name from the reviewed
defect plan. Never push an unapproved defect branch.
