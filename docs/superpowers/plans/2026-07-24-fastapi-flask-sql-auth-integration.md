# FastAPI and Flask SQL-Auth Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 24 deterministic framework integration cases that prove FastMssql SQL-auth behavior through native FastAPI/ASGI, Flask async WSGI views, and Flask behind `WsgiToAsgi`.

**Architecture:** Keep framework packages development-only. Small app factories own a shared FastMssql `Connection`, while the strict tests drive them through real ASGI or WSGI request paths and execute real TDS operations against `fastmssql-sql-auth-dev`. A framework metrics artifact records timing, loop, pool, dependency, principal, and lifecycle evidence for the generated report.

**Tech Stack:** Python 3.11+, FastMssql/PyO3/Rust/Tokio/Tiberius, SQL Server 2022 Developer, FastAPI, Flask with its `async` extra, HTTPX, asgiref, asgi-lifespan, pytest 9, pytest-asyncio, uv.

## Global Constraints

- Use only SQL Server username/password authentication; Windows and Azure authentication remain excluded.
- Execute all database behavior against the dedicated `fastmssql-sql-auth-dev` container on host port `14334`; do not touch any other container.
- Use `fastmssql_owner` and `fastmssql_validation` for normal framework queries; use `sa` only for server-session observation.
- Do not use mocks, SQLite, ODBC, `pytest.skip`, `pytest.xfail`, bare `except`, or `except Exception` in the strict framework tests.
- Keep FastAPI, Flask, HTTPX, asgiref, and asgi-lifespan in the development dependency group only.
- Preserve exactly 250 unique strict case identifiers after adding `FRAME-001` through `FRAME-024`.
- Give every app instance a unique SQL Server application name and use that exact name for session cleanup assertions.
- Native FastAPI concurrency must finish below 65% of its measured sequential baseline and advance the event-loop ticker at least ten times.
- Flask/WSGI results are compatibility evidence and must remain labeled worker-bound.
- Flask through `WsgiToAsgi` is a persistent-loop compatibility lane, not a native-ASGI equivalence claim.
- Never print, persist, or return a password or complete connection string.
- `upstream` remains fetch-only with push disabled. `origin` is exactly `https://github.com/galeamarcel/FastMssql.git`; the user has explicitly directed that all project branches and commits be published only to this fork.

## File Structure

- Create `tests/sql_auth_strict/framework_apps.py`: framework-neutral state, connection construction, app factories, safe routes, and request helpers.
- Create `tests/sql_auth_strict/test_framework_integration.py`: all 24 `FRAME-*` cases and no unrelated behavior.
- Modify `tests/sql_auth_strict/conftest.py`: JSON-safe framework metric recorder and artifact writer.
- Modify `tests/sql_auth_strict/test_matrix_contract.py`: 250-case contract, framework dependency contract, runner lane contract, and report wording contract.
- Modify `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`: append the 24 approved `FRAME-*` requirements to the authoritative matrix.
- Modify `pyproject.toml` and `uv.lock`: development-only framework packages and the `framework` marker.
- Modify `scripts/sql_auth/run_all.sh`: isolated serial framework lane and metrics path.
- Modify `scripts/sql_auth/generate_report.py`: load framework metrics and render execution-model-specific conclusions.
- Regenerate `docs/SQL_AUTH_TEST_MATRIX.md` and `docs/SQL_AUTH_TEST_REPORT.md` only from captured artifacts.

---

### Task 1: Development dependencies and framework evidence recorder

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `tests/sql_auth_strict/conftest.py`
- Create: `tests/sql_auth_strict/test_framework_integration.py`

**Interfaces:**
- Consumes: the existing `case()` decorator and `SqlAuthConfig`.
- Produces: `record_framework_metric(case_id: str, **values: object) -> None` and `.artifacts/sql-auth/framework-metrics.json`.

- [ ] **Step 1: Write the failing dependency and import-isolation cases**

Create the framework test module with these first two cases:

```python
from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

from sql_auth_strict.cases import case


ROOT = Path(__file__).resolve().parents[2]
pytestmark = [
    pytest.mark.sql_auth_strict,
    pytest.mark.integration,
    pytest.mark.framework,
]
FRAMEWORK_DISTRIBUTIONS = (
    "fastapi",
    "flask",
    "httpx",
    "asgiref",
    "asgi-lifespan",
)


@case("FRAME-001")
def test_framework_dependencies_are_development_only_and_locked(
    record_framework_metric,
) -> None:
    pyproject = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    runtime = tuple(pyproject["project"].get("dependencies", ()))
    development = tuple(pyproject["dependency-groups"]["dev"])
    assert runtime == ()
    for expected in FRAMEWORK_DISTRIBUTIONS:
        assert any(
            item.lower().replace("_", "-").startswith(expected)
            for item in development
        )
    versions = {
        name: importlib.metadata.version(name)
        for name in FRAMEWORK_DISTRIBUTIONS
    }
    lock_text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert all(f'name = "{name}"' in lock_text for name in versions)
    record_framework_metric("FRAME-001", versions=versions)


@case("FRAME-002")
def test_importing_fastmssql_does_not_import_frameworks() -> None:
    probe = """
import json
import sys
import fastmssql

blocked = ("fastapi", "flask", "httpx", "asgiref", "asgi_lifespan")
print(json.dumps(sorted(name for name in blocked if name in sys.modules)))
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "python")
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == []
```

- [ ] **Step 2: Run the focused test to verify RED**

Run:

```bash
PYTHONPATH=python .venv/bin/pytest \
  tests/sql_auth_strict/test_framework_integration.py -vv
```

Expected: collection fails because marker `framework`, fixture
`record_framework_metric`, and framework development dependencies do not exist.

- [ ] **Step 3: Add and lock development-only dependencies**

Run:

```bash
uv add --dev fastapi 'flask[async]' httpx asgiref asgi-lifespan
```

Add this marker to `tool.pytest.ini_options.markers`:

```toml
"framework: end-to-end FastAPI and Flask SQL-auth integration",
```

- [ ] **Step 4: Implement the JSON-safe metrics fixture**

In `tests/sql_auth_strict/conftest.py`, add:

```python
DEFAULT_FRAMEWORK_METRICS_PATH = (
    ROOT / ".artifacts/sql-auth/framework-metrics.json"
)
_FRAMEWORK_METRICS: dict[str, dict[str, object]] = {}
```

Clear it in `pytest_configure`:

```python
_FRAMEWORK_METRICS.clear()
```

Add the fixture:

```python
@pytest.fixture
def record_framework_metric():
    def record(case_id: str, **values: object) -> None:
        if not case_id.startswith("FRAME-"):
            raise ValueError(f"not a framework case ID: {case_id}")
        json.dumps(values)
        existing = _FRAMEWORK_METRICS.setdefault(case_id, {})
        overlap = existing.keys() & values.keys()
        if overlap:
            raise ValueError(
                f"duplicate framework metric keys for {case_id}: "
                f"{sorted(overlap)}"
            )
        existing.update(values)

    return record
```

At the end of `pytest_sessionfinish`, write metrics only when recorded:

```python
if _FRAMEWORK_METRICS:
    metrics_path = Path(
        os.getenv(
            "FASTMSSQL_FRAMEWORK_METRICS_PATH",
            str(DEFAULT_FRAMEWORK_METRICS_PATH),
        )
    )
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": dict(sorted(_FRAMEWORK_METRICS.items())),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
```

- [ ] **Step 5: Verify GREEN and the artifact**

Run:

```bash
PYTHONPATH=python .venv/bin/pytest \
  tests/sql_auth_strict/test_framework_integration.py -vv
jq -e '
  .schema_version == 1
  and (.cases["FRAME-001"].versions.fastapi | length > 0)
' .artifacts/sql-auth/framework-metrics.json
.venv/bin/ruff check \
  tests/sql_auth_strict/conftest.py \
  tests/sql_auth_strict/test_framework_integration.py
```

Expected: 2 tests pass, jq returns true, and Ruff exits 0.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock tests/sql_auth_strict/conftest.py \
  tests/sql_auth_strict/test_framework_integration.py
git commit -m "test: establish framework integration evidence"
```

---

### Task 2: Shared framework state and SQL-session helpers

**Files:**
- Create: `tests/sql_auth_strict/framework_apps.py`
- Modify: `tests/sql_auth_strict/test_framework_integration.py`

**Interfaces:**
- Consumes: `SqlAuthConfig`, `Connection`, `PoolConfig`, `SslConfig`, and safe table names produced by `quote_identifier`.
- Produces: `FrameworkState.create(...)`, `FrameworkState.transaction()`, `session_count(...)`, `wait_for_pool_active(...)`, `wait_for_session_count(...)`, and `wait_for_sql_request(...)`.

- [ ] **Step 1: Write failing state and session-helper tests**

Append unmarked infrastructure tests:

```python
from fastmssql import Connection

from sql_auth_strict.framework_apps import (
    FrameworkState,
    session_count,
    wait_for_pool_active,
)


def test_framework_state_uses_explicit_unique_application_name(
    sql_auth_config,
    unique_sql_name,
) -> None:
    application_name = unique_sql_name("strict_frame_state")
    state = FrameworkState.create(
        sql_auth_config,
        application_name=application_name,
    )
    assert isinstance(state.connection, Connection)
    assert state.application_name == application_name
    assert state.loops == []


@pytest.mark.asyncio
async def test_framework_session_helpers_observe_real_pool(
    sql_auth_config,
    sa_connection,
    unique_sql_name,
) -> None:
    application_name = unique_sql_name("strict_frame_session")
    state = FrameworkState.create(
        sql_auth_config,
        application_name=application_name,
    )
    try:
        await state.connection.connect()
        assert await session_count(sa_connection, application_name) >= 1
        assert (
            await wait_for_pool_active(state.connection, expected=0)
        )["active_connections"] == 0
    finally:
        await state.connection.disconnect()
```

- [ ] **Step 2: Verify RED**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
PYTHONPATH=python .venv/bin/pytest \
  tests/sql_auth_strict/test_framework_integration.py \
  -k 'framework_state or framework_session_helpers' -vv
```

Expected: import fails because `framework_apps.py` does not exist.

- [ ] **Step 3: Implement shared state and observation helpers**

Create `tests/sql_auth_strict/framework_apps.py`:

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time

from fastmssql import Connection, PoolConfig, SslConfig, Transaction

from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import IDENTIFIER, scalar


@dataclass
class FrameworkState:
    config: SqlAuthConfig
    application_name: str
    connection: Connection
    loops: list[asyncio.AbstractEventLoop] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        config: SqlAuthConfig,
        *,
        application_name: str,
        max_size: int = 4,
        min_idle: int = 0,
    ) -> "FrameworkState":
        if not IDENTIFIER.fullmatch(application_name):
            raise ValueError(
                f"unsafe framework application name {application_name!r}"
            )
        connection = Connection(
            server=config.host,
            port=config.port,
            database=config.database,
            username=config.owner_user,
            password=config.owner_password,
            application_name=application_name,
            ssl_config=SslConfig.development(),
            pool_config=PoolConfig(
                max_size=max_size,
                min_idle=min_idle,
                max_lifetime_secs=None,
                idle_timeout_secs=None,
                connection_timeout_secs=3,
                retry_connection=False,
            ),
        )
        return cls(config, application_name, connection)

    def transaction(self) -> Transaction:
        return Transaction(
            self.config.connection_string(
                self.config.owner_user,
                self.config.owner_password,
                extra=f"Application Name={self.application_name}",
            )
        )


async def session_count(observer: Connection, application_name: str) -> int:
    return await scalar(
        observer,
        """
        SELECT COUNT(*)
        FROM sys.dm_exec_sessions
        WHERE program_name = @P1
          AND session_id <> @@SPID
        """,
        [application_name],
    )


async def sql_request_count(observer: Connection, token: str) -> int:
    return await scalar(
        observer,
        """
        SELECT COUNT(*)
        FROM sys.dm_exec_requests AS request
        CROSS APPLY sys.dm_exec_sql_text(request.sql_handle) AS sql_text
        WHERE request.session_id <> @@SPID
          AND sql_text.text LIKE @P1
        """,
        [f"%{token}%"],
    )


async def wait_for_pool_active(
    connection: Connection,
    *,
    expected: int,
    timeout: float = 3.0,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stats = await connection.pool_stats()
        if stats["active_connections"] == expected:
            return stats
        await asyncio.sleep(0.02)
    stats = await connection.pool_stats()
    raise AssertionError(
        f"expected {expected} active connection(s), observed {stats}"
    )


async def wait_for_session_count(
    observer: Connection,
    application_name: str,
    *,
    expected: int,
    timeout: float = 4.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await session_count(observer, application_name) == expected:
            return
        await asyncio.sleep(0.05)
    observed = await session_count(observer, application_name)
    raise AssertionError(
        f"expected {expected} SQL session(s) for "
        f"{application_name!r}, observed {observed}"
    )


async def wait_for_sql_request(
    observer: Connection,
    token: str,
    *,
    present: bool,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        observed = await sql_request_count(observer, token)
        if (observed > 0) is present:
            return
        await asyncio.sleep(0.02)
    observed = await sql_request_count(observer, token)
    raise AssertionError(
        f"expected SQL request {token!r} present={present}, "
        f"observed count={observed}"
    )
```

- [ ] **Step 4: Verify GREEN**

Run the focused command from Step 2 again.

Expected: 2 tests pass and the dedicated application-name session becomes
observable and is cleaned up.

- [ ] **Step 5: Commit**

```bash
git add tests/sql_auth_strict/framework_apps.py \
  tests/sql_auth_strict/test_framework_integration.py
git commit -m "test: add framework SQL lifecycle helpers"
```

---

### Task 3: FastAPI lifecycle, data, transaction, and error routes

**Files:**
- Modify: `tests/sql_auth_strict/framework_apps.py`
- Modify: `tests/sql_auth_strict/test_framework_integration.py`

**Interfaces:**
- Consumes: `FrameworkState`.
- Produces: `create_fastapi_app(state, table_sql, *, safe_errors=False) -> FastAPI` with `/principal`, `/items/{item_id}`, `/transaction/{item_id}`, and `/sql-error`.

- [ ] **Step 1: Write the FastAPI failing cases**

Add imports:

```python
from asgi_lifespan import LifespanManager
from fastmssql import SqlError
from httpx import ASGITransport, AsyncClient
import pytest_asyncio

from sql_auth_strict.framework_apps import (
    IntentionalRollback,
    create_fastapi_app,
    wait_for_sql_request,
    wait_for_session_count,
)
from sql_auth_strict.helpers import quote_identifier, scalar
```

Add a small client helper:

```python
def asgi_client(app, *, raise_app_exceptions: bool = True) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(
            app=app,
            raise_app_exceptions=raise_app_exceptions,
        ),
        base_url="http://framework.test",
    )
```

Add these cases:

```python
@case("FRAME-005")
@pytest.mark.asyncio
async def test_fastapi_lifespan_connects_and_disconnects_shared_pool(
    sql_auth_config,
    sa_connection,
    unique_sql_name,
) -> None:
    state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_fastapi_lifecycle"),
    )
    app = create_fastapi_app(state, "[unused_framework_table]")
    assert await state.connection.is_connected() is False
    async with LifespanManager(app):
        assert await state.connection.is_connected() is True
        assert await scalar(state.connection, "SELECT 1") == 1
        assert await session_count(sa_connection, state.application_name) >= 1
    assert await state.connection.is_connected() is False
    await wait_for_session_count(
        sa_connection,
        state.application_name,
        expected=0,
    )


@case("FRAME-006")
@pytest.mark.asyncio
async def test_fastapi_parameterized_read_write_routes(
    sql_auth_config,
    owner_connection,
    unique_sql_name,
    cleanup_registry,
) -> None:
    raw_table = unique_sql_name("strict_fastapi_items")
    table = quote_identifier(raw_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value NVARCHAR(100))"
    )
    state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_fastapi_data"),
    )
    app = create_fastapi_app(state, table)
    async with LifespanManager(app), asgi_client(app) as client:
        written = await client.post(
            "/items/7",
            json={"value": "Română 🧪"},
        )
        read = await client.get("/items/7")
    assert written.status_code == 201
    assert written.json() == {"affected": 1}
    assert read.status_code == 200
    assert read.json() == {"id": 7, "value": "Română 🧪"}
    assert await scalar(
        owner_connection,
        f"SELECT value FROM {table} WHERE id = 7",
    ) == "Română 🧪"


@case("FRAME-007")
@pytest.mark.asyncio
async def test_fastapi_request_transaction_commits(
    framework_fastapi_app,
    owner_connection,
    framework_table,
) -> None:
    app, _state = framework_fastapi_app
    async with LifespanManager(app), asgi_client(app) as client:
        response = await client.post("/transaction/17")
    assert response.status_code == 201
    assert await scalar(
        owner_connection,
        f"SELECT COUNT(*) FROM {framework_table} WHERE id = 17",
    ) == 1


@case("FRAME-008")
@pytest.mark.asyncio
async def test_fastapi_request_transaction_rolls_back(
    framework_fastapi_app,
    owner_connection,
    framework_table,
) -> None:
    app, _state = framework_fastapi_app
    async with LifespanManager(app), asgi_client(app) as client:
        with pytest.raises(IntentionalRollback):
            await client.post("/transaction/18?fail=true")
    assert await scalar(
        owner_connection,
        f"SELECT COUNT(*) FROM {framework_table} WHERE id = 18",
    ) == 0


@case("FRAME-012")
@pytest.mark.asyncio
async def test_fastapi_sql_error_preserves_type_and_code(
    framework_fastapi_app,
) -> None:
    app, state = framework_fastapi_app
    async with LifespanManager(app), asgi_client(app) as client:
        with pytest.raises(SqlError) as captured:
            await client.get("/sql-error")
        assert captured.value.code == 208
        assert await scalar(state.connection, "SELECT 12") == 12


@case("FRAME-013")
@pytest.mark.asyncio
async def test_fastapi_500_response_and_logs_redact_credentials(
    sql_auth_config,
    unique_sql_name,
    caplog,
) -> None:
    state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_fastapi_safe_error"),
    )
    app = create_fastapi_app(
        state,
        "[unused_framework_table]",
        safe_errors=True,
    )
    async with LifespanManager(app), asgi_client(
        app,
        raise_app_exceptions=False,
    ) as client:
        response = await client.get("/sql-error")
    combined = response.text + "\n" + caplog.text
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    for secret in (
        sql_auth_config.owner_password,
        sql_auth_config.sa_password,
        sql_auth_config.readonly_password,
        sql_auth_config.denied_password,
    ):
        assert secret not in combined
```

Create `framework_table` and `framework_fastapi_app` fixtures in the same test
module using the existing cleanup registry:

```python
@pytest_asyncio.fixture
async def framework_table(
    owner_connection,
    unique_sql_name,
    cleanup_registry,
):
    table = quote_identifier(unique_sql_name("strict_framework_items"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value NVARCHAR(100) NULL)"
    )
    return table


@pytest_asyncio.fixture
async def framework_fastapi_app(
    sql_auth_config,
    unique_sql_name,
    framework_table,
):
    state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_fastapi_app"),
    )
    return create_fastapi_app(state, framework_table), state
```

- [ ] **Step 2: Verify RED**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
PYTHONPATH=python .venv/bin/pytest \
  tests/sql_auth_strict/test_framework_integration.py \
  -k 'fastapi and not concurrent and not ticker and not cancellation' -vv
```

Expected: imports fail because `IntentionalRollback` and
`create_fastapi_app` do not exist.

- [ ] **Step 3: Implement the FastAPI app factory**

Append to `framework_apps.py`:

```python
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from pydantic import BaseModel
from starlette.responses import PlainTextResponse


LOGGER = logging.getLogger("fastmssql.framework")


class ItemPayload(BaseModel):
    value: str


class IntentionalRollback(RuntimeError):
    pass


def create_fastapi_app(
    state: FrameworkState,
    table_sql: str,
    *,
    safe_errors: bool = False,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await state.connection.connect()
        app.state.fastmssql = state
        try:
            yield
        finally:
            await state.connection.disconnect()

    app = FastAPI(lifespan=lifespan)

    @app.get("/principal")
    async def principal():
        return {
            "principal": await scalar(
                state.connection,
                "SELECT CAST(SUSER_SNAME() AS NVARCHAR(128))",
            )
        }

    @app.post("/items/{item_id}", status_code=201)
    async def write_item(item_id: int, payload: ItemPayload):
        affected = await state.connection.execute(
            f"INSERT INTO {table_sql} (id, value) VALUES (@P1, @P2)",
            [item_id, payload.value],
        )
        return {"affected": affected}

    @app.get("/items/{item_id}")
    async def read_item(item_id: int):
        row = (
            await state.connection.query(
                f"SELECT id, value FROM {table_sql} WHERE id = @P1",
                [item_id],
            )
        ).fetchone()
        return {"id": row["id"], "value": row["value"]}

    @app.post("/transaction/{item_id}", status_code=201)
    async def transaction_item(item_id: int, fail: bool = False):
        async with state.transaction() as transaction:
            await transaction.execute(
                f"INSERT INTO {table_sql} (id, value) VALUES (@P1, @P2)",
                [item_id, "transaction"],
            )
            if fail:
                raise IntentionalRollback("intentional rollback")
        return {"committed": True}

    @app.get("/sql-error")
    async def sql_error():
        await state.connection.query(
            "SELECT * FROM dbo.strict_framework_missing_table"
        )
        raise AssertionError("unreachable after missing-table query")

    if safe_errors:
        @app.exception_handler(Exception)
        async def safe_500(request, error):
            del request
            if hasattr(error, "code") and hasattr(error, "state"):
                LOGGER.error(
                    "FastMssql request failed: type=%s code=%s state=%s",
                    type(error).__name__,
                    error.code,
                    error.state,
                )
            return PlainTextResponse(
                "Internal Server Error",
                status_code=500,
            )

    return app
```

The generic FastAPI handler is application test-harness code, not a strict-test
exception handler. It never marks a test as passed and never logs an exception
message or credentials.

- [ ] **Step 4: Verify GREEN**

Run the focused command from Step 2 again.

Expected: 6 tests pass with no skip or xfail.

- [ ] **Step 5: Commit**

```bash
git add tests/sql_auth_strict/framework_apps.py \
  tests/sql_auth_strict/test_framework_integration.py
git commit -m "test: validate FastAPI SQL auth lifecycle"
```

---

### Task 4: FastAPI true-async concurrency and cancellation

**Files:**
- Modify: `tests/sql_auth_strict/framework_apps.py`
- Modify: `tests/sql_auth_strict/test_framework_integration.py`

**Interfaces:**
- Produces: `/wait/{value}?profile=short|long` with a fixed delay lookup; no user-provided SQL interpolation.

- [ ] **Step 1: Write failing concurrency, ticker, and cancellation cases**

Add:

```python
import asyncio
import time

from sql_auth_strict.helpers import event_loop_ticks


@case("FRAME-009")
@pytest.mark.asyncio
async def test_fastapi_concurrent_requests_beat_sequential_baseline(
    framework_fastapi_app,
    record_framework_metric,
) -> None:
    app, _state = framework_fastapi_app
    async with LifespanManager(app), asgi_client(app) as client:
        sequential_started = time.monotonic()
        sequential = [
            (await client.get(f"/wait/{value}?profile=short")).json()["value"]
            for value in range(4)
        ]
        sequential_elapsed = time.monotonic() - sequential_started

        concurrent_started = time.monotonic()
        responses = await asyncio.gather(
            *(client.get(f"/wait/{value}?profile=short") for value in range(4))
        )
        concurrent_elapsed = time.monotonic() - concurrent_started
    assert sequential == list(range(4))
    assert [response.json()["value"] for response in responses] == list(
        range(4)
    )
    assert concurrent_elapsed < sequential_elapsed * 0.65
    record_framework_metric(
        "FRAME-009",
        sequential_seconds=sequential_elapsed,
        concurrent_seconds=concurrent_elapsed,
        ratio=concurrent_elapsed / sequential_elapsed,
    )


@case("FRAME-010")
@pytest.mark.asyncio
async def test_fastapi_event_loop_ticks_during_sql_wait(
    framework_fastapi_app,
    record_framework_metric,
) -> None:
    app, _state = framework_fastapi_app
    async with LifespanManager(app), asgi_client(app) as client:
        stop = asyncio.Event()
        ticker = asyncio.create_task(event_loop_ticks(stop, interval=0.02))
        try:
            response = await client.get("/wait/10?profile=short")
            assert response.json() == {"value": 10}
        finally:
            stop.set()
            ticks = await ticker
    assert len(ticks) >= 10
    record_framework_metric("FRAME-010", ticker_count=len(ticks))


@case("FRAME-011")
@pytest.mark.asyncio
async def test_fastapi_request_cancellation_recovers_immediately(
    framework_fastapi_app,
    sa_connection,
    record_framework_metric,
) -> None:
    app, state = framework_fastapi_app
    async with LifespanManager(app), asgi_client(app) as client:
        request = asyncio.create_task(
            client.get("/wait/11?profile=long")
        )
        await wait_for_pool_active(state.connection, expected=1)
        await wait_for_sql_request(
            sa_connection,
            state.application_name,
            present=True,
            timeout=1.0,
        )
        started = time.monotonic()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(request, timeout=0.5)
        cancellation_elapsed = time.monotonic() - started
        await wait_for_pool_active(
            state.connection,
            expected=0,
            timeout=1.0,
        )
        await wait_for_sql_request(
            sa_connection,
            state.application_name,
            present=False,
            timeout=1.0,
        )
        recovered = await asyncio.wait_for(
            client.get("/wait/111?profile=none"),
            timeout=2.0,
        )
        pool_after = await state.connection.pool_stats()
    assert recovered.json() == {"value": 111}
    record_framework_metric(
        "FRAME-011",
        cancellation_seconds=cancellation_elapsed,
        pool_after=pool_after,
    )
```

- [ ] **Step 2: Verify RED**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
PYTHONPATH=python .venv/bin/pytest \
  tests/sql_auth_strict/test_framework_integration.py \
  -k 'fastapi and (concurrent or ticker or cancellation)' -vv
```

Expected: requests return 404 because `/wait/{value}` does not exist.

- [ ] **Step 3: Implement the fixed-delay route**

Inside `create_fastapi_app`, add:

```python
    delays = {
        "none": "",
        "short": "WAITFOR DELAY '00:00:01';",
        "long": "WAITFOR DELAY '00:00:05';",
    }

    @app.get("/wait/{value}")
    async def wait_value(value: int, profile: str = "short"):
        prefix = delays.get(profile)
        if prefix is None:
            raise ValueError(f"invalid delay profile {profile!r}")
        returned = await scalar(
            state.connection,
            f"/* {state.application_name} */ {prefix} SELECT @P1",
            [value],
        )
        return {"value": returned}
```

- [ ] **Step 4: Verify GREEN twice**

Run the focused command from Step 2 twice.

Expected on both runs: 3 tests pass; the four-request concurrent time is below
65% of sequential; ticker count is at least 10; cancellation completes within
500 ms and the next query succeeds.

- [ ] **Step 5: Commit**

```bash
git add tests/sql_auth_strict/framework_apps.py \
  tests/sql_auth_strict/test_framework_integration.py
git commit -m "test: prove FastAPI true async SQL requests"
```

---

### Task 5: Flask async views under WSGI

**Files:**
- Modify: `tests/sql_auth_strict/framework_apps.py`
- Modify: `tests/sql_auth_strict/test_framework_integration.py`

**Interfaces:**
- Produces: `create_flask_app(state) -> Flask` with `/principal`, `/value/{value}`, `/loop`, `/gather`, and `/sql-error`.

- [ ] **Step 1: Write failing Flask/WSGI cases**

Add:

```python
from flask import Flask

from sql_auth_strict.framework_apps import (
    create_flask_app,
    wait_for_session_count,
)


async def flask_request(app: Flask, path: str):
    def request_once():
        with app.test_client() as client:
            return client.get(path)

    return await asyncio.to_thread(request_once)


@pytest.fixture
def framework_flask_app(sql_auth_config, unique_sql_name):
    state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_flask_wsgi"),
    )
    return create_flask_app(state), state


@case("FRAME-014")
@pytest.mark.asyncio
async def test_flask_async_wsgi_view_executes_real_query(
    framework_flask_app,
) -> None:
    app, state = framework_flask_app
    try:
        response = await flask_request(app, "/value/14")
        assert response.status_code == 200
        assert response.get_json() == {"value": 14}
    finally:
        await state.connection.disconnect()


@case("FRAME-015")
@pytest.mark.asyncio
async def test_flask_shared_pool_crosses_distinct_request_loops(
    framework_flask_app,
    record_framework_metric,
) -> None:
    app, state = framework_flask_app
    try:
        first = await flask_request(app, "/loop")
        second = await flask_request(app, "/loop")
        assert first.status_code == second.status_code == 200
        assert len(state.loops) == 2
        assert state.loops[0] is not state.loops[1]
        assert await scalar(state.connection, "SELECT 15") == 15
        record_framework_metric(
            "FRAME-015",
            distinct_request_loops=True,
            loop_ids=[id(loop) for loop in state.loops],
        )
    finally:
        await state.connection.disconnect()


@case("FRAME-016")
@pytest.mark.asyncio
async def test_flask_one_async_view_overlaps_database_operations(
    framework_flask_app,
    record_framework_metric,
) -> None:
    app, state = framework_flask_app
    try:
        response = await flask_request(app, "/gather")
        payload = response.get_json()
        assert payload["sequential"] == [0, 1, 2, 3]
        assert payload["concurrent"] == [0, 1, 2, 3]
        assert payload["concurrent_seconds"] < (
            payload["sequential_seconds"] * 0.65
        )
        record_framework_metric("FRAME-016", **payload)
    finally:
        await state.connection.disconnect()


@case("FRAME-017")
@pytest.mark.asyncio
async def test_flask_wsgi_worker_requests_are_correct_and_measured(
    framework_flask_app,
    record_framework_metric,
) -> None:
    app, state = framework_flask_app
    try:
        started = time.monotonic()
        responses = await asyncio.gather(
            *(flask_request(app, f"/wait/{value}") for value in range(4))
        )
        elapsed = time.monotonic() - started
        assert [response.get_json()["value"] for response in responses] == list(
            range(4)
        )
        record_framework_metric(
            "FRAME-017",
            execution_model="WSGI worker-bound",
            requests=4,
            elapsed_seconds=elapsed,
        )
    finally:
        await state.connection.disconnect()


@case("FRAME-018")
@pytest.mark.asyncio
async def test_flask_wsgi_error_is_typed_redacted_and_pool_recovers(
    framework_flask_app,
    sql_auth_config,
) -> None:
    app, state = framework_flask_app
    app.testing = True
    try:
        with pytest.raises(SqlError) as captured:
            await flask_request(app, "/sql-error")
        assert captured.value.code == 208
        assert sql_auth_config.owner_password not in str(captured.value)
        assert await scalar(state.connection, "SELECT 18") == 18
    finally:
        await state.connection.disconnect()


@case("FRAME-019")
@pytest.mark.asyncio
async def test_flask_wsgi_explicit_shutdown_removes_app_sessions(
    framework_flask_app,
    sa_connection,
) -> None:
    app, state = framework_flask_app
    response = await flask_request(app, "/value/19")
    assert response.get_json() == {"value": 19}
    assert await session_count(sa_connection, state.application_name) >= 1
    await state.connection.disconnect()
    assert await state.connection.is_connected() is False
    await wait_for_session_count(
        sa_connection,
        state.application_name,
        expected=0,
    )
```

- [ ] **Step 2: Verify RED**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
PYTHONPATH=python .venv/bin/pytest \
  tests/sql_auth_strict/test_framework_integration.py \
  -k 'flask and wsgi and not adapted' -vv
```

Expected: import fails because `create_flask_app` does not exist.

- [ ] **Step 3: Implement the Flask app factory**

Append:

```python
from flask import Flask, jsonify


def create_flask_app(state: FrameworkState) -> Flask:
    app = Flask(__name__)
    delays = {
        "none": "",
        "short": "WAITFOR DELAY '00:00:01';",
        "long": "WAITFOR DELAY '00:00:02';",
    }

    async def delayed(value: int, profile: str = "short") -> int:
        prefix = delays[profile]
        return await scalar(
            state.connection,
            f"/* {state.application_name} */ {prefix} SELECT @P1",
            [value],
        )

    @app.get("/principal")
    async def principal():
        value = await scalar(
            state.connection,
            "SELECT CAST(SUSER_SNAME() AS NVARCHAR(128))",
        )
        return jsonify(principal=value)

    @app.get("/value/<int:value>")
    async def value(value: int):
        return jsonify(value=await delayed(value, "none"))

    @app.get("/loop")
    async def loop():
        running = asyncio.get_running_loop()
        state.loops.append(running)
        return jsonify(loop_id=id(running))

    @app.get("/gather")
    async def gather():
        sequential_started = time.monotonic()
        sequential = [await delayed(value) for value in range(4)]
        sequential_elapsed = time.monotonic() - sequential_started
        concurrent_started = time.monotonic()
        concurrent = await asyncio.gather(
            *(delayed(value) for value in range(4))
        )
        concurrent_elapsed = time.monotonic() - concurrent_started
        return jsonify(
            sequential=sequential,
            concurrent=concurrent,
            sequential_seconds=sequential_elapsed,
            concurrent_seconds=concurrent_elapsed,
        )

    @app.get("/wait/<int:value>")
    async def wait(value: int):
        return jsonify(value=await delayed(value, "long"))

    @app.get("/sql-error")
    async def sql_error():
        await state.connection.query(
            "SELECT * FROM dbo.strict_framework_missing_table"
        )
        raise AssertionError("unreachable after missing-table query")

    return app
```

- [ ] **Step 4: Verify GREEN twice**

Run the focused command from Step 2 twice.

Expected: 6 tests pass twice; two sequential WSGI requests retain two distinct
event-loop objects; intra-view concurrency meets the 65% threshold; shutdown
removes application-name sessions.

- [ ] **Step 5: Commit**

```bash
git add tests/sql_auth_strict/framework_apps.py \
  tests/sql_auth_strict/test_framework_integration.py
git commit -m "test: validate Flask async WSGI compatibility"
```

---

### Task 6: Flask behind WsgiToAsgi

**Files:**
- Modify: `tests/sql_auth_strict/framework_apps.py`
- Modify: `tests/sql_auth_strict/test_framework_integration.py`

**Interfaces:**
- Consumes: the exact Flask app from Task 5.
- Produces: `create_adapted_flask_app(state) -> WsgiToAsgi`.

- [ ] **Step 1: Write failing adapted-Flask cases**

Add:

```python
from sql_auth_strict.framework_apps import create_adapted_flask_app


@pytest.fixture
def framework_adapted_flask_app(sql_auth_config, unique_sql_name):
    state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_flask_asgi"),
    )
    return create_adapted_flask_app(state), state


@case("FRAME-020")
@pytest.mark.asyncio
async def test_adapted_flask_executes_real_sql_auth_query(
    framework_adapted_flask_app,
) -> None:
    app, state = framework_adapted_flask_app
    await state.connection.connect()
    try:
        async with asgi_client(app) as client:
            response = await client.get("/value/20")
        assert response.status_code == 200
        assert response.json() == {"value": 20}
    finally:
        await state.connection.disconnect()


@case("FRAME-021")
@pytest.mark.asyncio
async def test_adapted_flask_reuses_persistent_asgi_loop(
    framework_adapted_flask_app,
    record_framework_metric,
) -> None:
    app, state = framework_adapted_flask_app
    await state.connection.connect()
    try:
        async with asgi_client(app) as client:
            first = await client.get("/loop")
            second = await client.get("/loop")
        assert first.status_code == second.status_code == 200
        assert len(state.loops) == 2
        assert state.loops[0] is state.loops[1]
        record_framework_metric(
            "FRAME-021",
            persistent_loop=True,
            loop_id=first.json()["loop_id"],
        )
    finally:
        await state.connection.disconnect()


@case("FRAME-022")
@pytest.mark.asyncio
async def test_adapted_flask_concurrent_requests_are_correct_and_measured(
    framework_adapted_flask_app,
    record_framework_metric,
) -> None:
    app, state = framework_adapted_flask_app
    await state.connection.connect()
    try:
        async with asgi_client(app) as client:
            started = time.monotonic()
            responses = await asyncio.gather(
                *(client.get(f"/wait/{value}") for value in range(4))
            )
            elapsed = time.monotonic() - started
        assert [response.json()["value"] for response in responses] == list(
            range(4)
        )
        record_framework_metric(
            "FRAME-022",
            execution_model="persistent ASGI loop around Flask/WSGI",
            requests=4,
            elapsed_seconds=elapsed,
        )
    finally:
        await state.connection.disconnect()


@case("FRAME-023")
@pytest.mark.asyncio
async def test_adapted_flask_cancellation_has_bounded_recovery(
    framework_adapted_flask_app,
    sa_connection,
    record_framework_metric,
) -> None:
    app, state = framework_adapted_flask_app
    await state.connection.connect()
    try:
        async with asgi_client(app) as client:
            request = asyncio.create_task(client.get("/wait/23"))
            await wait_for_pool_active(state.connection, expected=1)
            await wait_for_sql_request(
                sa_connection,
                state.application_name,
                present=True,
                timeout=1.0,
            )
            started = time.monotonic()
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(request, timeout=0.5)
            cancellation_elapsed = time.monotonic() - started
            await wait_for_pool_active(
                state.connection,
                expected=0,
                timeout=3.5,
            )
            await wait_for_sql_request(
                sa_connection,
                state.application_name,
                present=False,
                timeout=3.5,
            )
            recovered = await client.get("/value/223")
            pool_after = await state.connection.pool_stats()
        assert recovered.json() == {"value": 223}
        record_framework_metric(
            "FRAME-023",
            cancellation_seconds=cancellation_elapsed,
            recovery_bound_seconds=3.5,
            pool_after=pool_after,
        )
    finally:
        await state.connection.disconnect()


@case("FRAME-024")
@pytest.mark.asyncio
async def test_adapted_flask_shutdown_removes_app_sessions(
    framework_adapted_flask_app,
    sa_connection,
) -> None:
    app, state = framework_adapted_flask_app
    await state.connection.connect()
    async with asgi_client(app) as client:
        assert (await client.get("/value/24")).json() == {"value": 24}
    assert await session_count(sa_connection, state.application_name) >= 1
    await state.connection.disconnect()
    assert await state.connection.is_connected() is False
    await wait_for_session_count(
        sa_connection,
        state.application_name,
        expected=0,
    )
```

- [ ] **Step 2: Verify RED**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
PYTHONPATH=python .venv/bin/pytest \
  tests/sql_auth_strict/test_framework_integration.py \
  -k 'adapted_flask' -vv
```

Expected: import fails because `create_adapted_flask_app` does not exist.

- [ ] **Step 3: Implement the WSGI-to-ASGI wrapper**

Append:

```python
from asgiref.wsgi import WsgiToAsgi


def create_adapted_flask_app(state: FrameworkState) -> WsgiToAsgi:
    return WsgiToAsgi(create_flask_app(state))
```

- [ ] **Step 4: Verify GREEN twice**

Run the focused command from Step 2 twice.

Expected: 5 tests pass twice; loop object identity is reused; concurrent
results are correct; cancellation surfaces within 500 ms, capacity returns
within 3.5 seconds, and the pool recovers.

- [ ] **Step 5: Commit**

```bash
git add tests/sql_auth_strict/framework_apps.py \
  tests/sql_auth_strict/test_framework_integration.py
git commit -m "test: validate Flask through WsgiToAsgi"
```

---

### Task 7: Cross-framework SQL-auth identity and redaction

**Files:**
- Modify: `tests/sql_auth_strict/test_framework_integration.py`

**Interfaces:**
- Consumes: all three app factories.
- Produces: single-source parameterized `FRAME-003` and `FRAME-004` evidence across all modes.

- [ ] **Step 1: Write the cross-framework cases**

Add a fixture returning callables that create each mode with a fresh state:

```python
@pytest.fixture
def framework_modes(sql_auth_config, unique_sql_name):
    def fastapi_mode():
        state = FrameworkState.create(
            sql_auth_config,
            application_name=unique_sql_name("strict_mode_fastapi"),
        )
        return "FastAPI/native ASGI", create_fastapi_app(
            state, "[unused_framework_table]"
        ), state, True

    def flask_wsgi_mode():
        state = FrameworkState.create(
            sql_auth_config,
            application_name=unique_sql_name("strict_mode_flask_wsgi"),
        )
        return "Flask/WSGI", create_flask_app(state), state, False

    def flask_asgi_mode():
        state = FrameworkState.create(
            sql_auth_config,
            application_name=unique_sql_name("strict_mode_flask_asgi"),
        )
        return (
            "Flask/WsgiToAsgi",
            create_adapted_flask_app(state),
            state,
            True,
        )

    return fastapi_mode, flask_wsgi_mode, flask_asgi_mode
```

Add:

```python
@case("FRAME-003")
@pytest.mark.asyncio
async def test_every_framework_mode_uses_owner_sql_auth(
    framework_modes,
    sql_auth_config,
    record_framework_metric,
) -> None:
    observed = {}
    for factory in framework_modes:
        name, app, state, is_asgi = factory()
        try:
            if name == "FastAPI/native ASGI":
                async with LifespanManager(app):
                    async with asgi_client(app) as client:
                        response = await client.get("/principal")
                        principal = response.json()["principal"]
            elif is_asgi:
                await state.connection.connect()
                async with asgi_client(app) as client:
                    response = await client.get("/principal")
                    principal = response.json()["principal"]
            else:
                await state.connection.connect()
                response = await flask_request(app, "/principal")
                principal = response.get_json()["principal"]
            assert principal == sql_auth_config.owner_user
            observed[name] = principal
        finally:
            if await state.connection.is_connected():
                await state.connection.disconnect()
    record_framework_metric("FRAME-003", principals=observed)


@case("FRAME-004")
@pytest.mark.asyncio
async def test_framework_outputs_never_disclose_credentials(
    framework_modes,
    sql_auth_config,
    caplog,
) -> None:
    visible = []
    for factory in framework_modes:
        name, app, state, is_asgi = factory()
        await state.connection.connect()
        try:
            if is_asgi:
                async with asgi_client(
                    app,
                    raise_app_exceptions=False,
                ) as client:
                    visible.append((await client.get("/sql-error")).text)
            else:
                app.testing = False
                visible.append(
                    (await flask_request(app, "/sql-error")).get_data(
                        as_text=True
                    )
                )
        finally:
            await state.connection.disconnect()
    combined = "\n".join(visible) + "\n" + caplog.text
    for secret in (
        sql_auth_config.owner_password,
        sql_auth_config.sa_password,
        sql_auth_config.readonly_password,
        sql_auth_config.denied_password,
    ):
        assert secret not in combined
```

- [ ] **Step 2: Run and verify GREEN**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
PYTHONPATH=python .venv/bin/pytest \
  tests/sql_auth_strict/test_framework_integration.py \
  -k 'every_framework_mode or framework_outputs' -vv
```

Expected: 2 tests pass; every principal equals `fastmssql_owner`; no configured
password appears in response or captured logs.

- [ ] **Step 3: Commit**

```bash
git add tests/sql_auth_strict/test_framework_integration.py
git commit -m "test: prove framework SQL auth identity"
```

---

### Task 8: Authoritative 250-case contract and dedicated runner lane

**Files:**
- Modify: `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- Modify: `tests/sql_auth_strict/test_matrix_contract.py`
- Modify: `scripts/sql_auth/run_all.sh`
- Modify: `scripts/sql_auth/generate_report.py`
- Modify: `docs/SQL_AUTH_TEST_MATRIX.md`
- Modify: `docs/SQL_AUTH_TEST_REPORT.md`

**Interfaces:**
- Consumes: 24 framework case results and framework metrics JSON.
- Produces: a strict 250-case matrix, a `framework` execution lane, and three explicit framework conclusions.

- [ ] **Step 1: Write failing contract assertions**

Rename `test_approved_spec_contains_226_unique_case_ids` to
`test_approved_spec_contains_250_unique_case_ids`, change its expected count
from 226 to 250, and add:

```python
def test_framework_contract_is_wired_into_runner_and_report() -> None:
    runner = (ROOT / "scripts/sql_auth/run_all.sh").read_text(
        encoding="utf-8"
    )
    report_source = (
        ROOT / "scripts/sql_auth/generate_report.py"
    ).read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "tests/sql_auth_strict/test_framework_integration.py" in runner
    assert 'record framework \\' in runner
    assert "FASTMSSQL_FRAMEWORK_METRICS_PATH" in runner
    assert "framework-results.json" in runner
    assert "framework.xml" in runner
    assert "FastAPI/native ASGI" in report_source
    assert "Flask/WSGI" in report_source
    assert "Flask via WsgiToAsgi" in report_source
    assert "framework-metrics.json" in report_source
    assert "framework:" in pyproject
```

- [ ] **Step 2: Verify RED**

Run:

```bash
PYTHONPATH=python .venv/bin/pytest \
  tests/sql_auth_strict/test_matrix_contract.py -vv
```

Expected: case count remains 226, `FRAME-*` IDs are extra, and the framework
runner/report assertions fail.

- [ ] **Step 3: Append the 24 approved requirements to the authoritative spec**

Place this exact section after `LOAD-007`:

```markdown
### FRAME — framework integration

- `FRAME-001`: framework dependencies are development-only and their locked
  versions are recorded.
- `FRAME-002`: importing FastMssql does not import FastAPI, Flask, HTTPX, or
  asgiref.
- `FRAME-003`: every framework lane authenticates as `fastmssql_owner` using
  SQL authentication.
- `FRAME-004`: credentials are absent from HTTP responses, exception strings,
  and captured framework logs.
- `FRAME-005`: FastAPI lifespan connects one shared pool and disconnects it on
  shutdown.
- `FRAME-006`: FastAPI parameterized read/write routes return correct HTTP and
  persisted database results.
- `FRAME-007`: FastAPI request-scoped transaction commits on success.
- `FRAME-008`: FastAPI request-scoped transaction rolls back on failure.
- `FRAME-009`: FastAPI concurrent `WAITFOR` requests beat a measured sequential
  baseline with the configured pool size.
- `FRAME-010`: the Python event loop continues ticking during FastAPI SQL waits.
- `FRAME-011`: cancelling a FastAPI request does not leak pool capacity or
  poison the next query.
- `FRAME-012`: a FastMssql SQL error propagates through FastAPI with its class
  and code intact when application exceptions are enabled.
- `FRAME-013`: the normal FastAPI 500 response and logs do not disclose SQL
  credentials.
- `FRAME-014`: Flask executes a real parameterized FastMssql query from an
  `async def` WSGI view.
- `FRAME-015`: one shared FastMssql connection remains correct across
  sequential Flask requests with different per-request event loops.
- `FRAME-016`: concurrent FastMssql operations inside one Flask async view
  overlap and return independent results.
- `FRAME-017`: concurrent Flask WSGI worker requests return correct independent
  SQL results; worker-bound timing is recorded without an ASGI claim.
- `FRAME-018`: Flask WSGI SQL failures remain typed, redact credentials, and
  leave the shared pool usable.
- `FRAME-019`: explicit Flask WSGI test shutdown disconnects the shared pool
  and leaves no test-owned active SQL sessions.
- `FRAME-020`: Flask wrapped by `WsgiToAsgi` executes real FastMssql SQL-auth
  queries.
- `FRAME-021`: sequential adapted Flask requests reuse the persistent ASGI
  event loop.
- `FRAME-022`: concurrent adapted Flask requests all complete correctly and
  their measured timing is reported without native-ASGI equivalence.
- `FRAME-023`: cancelling an adapted Flask request has a bounded outcome and
  the FastMssql pool remains usable afterward.
- `FRAME-024`: adapted Flask startup and shutdown leave the FastMssql pool
  disconnected and no test-owned active SQL sessions.
```

The authoritative parent spec must contain each ID once.

- [ ] **Step 4: Add the dedicated framework runner lane**

In `scripts/sql_auth/run_all.sh`, after the async lane and before resilience:

```bash
record framework \
  env FASTMSSQL_SQL_AUTH_RESULTS_PATH="${artifact_dir}/framework-results.json" \
  FASTMSSQL_FRAMEWORK_METRICS_PATH="${artifact_dir}/framework-metrics.json" \
  uv run pytest tests/sql_auth_strict/test_framework_integration.py \
  --junitxml="${artifact_dir}/framework.xml" -vv
```

- [ ] **Step 5: Load and render framework metrics**

In `generate_report.py`, add:

```python
def load_framework_metrics(artifact_dir: Path) -> dict[str, dict]:
    path = artifact_dir / "framework-metrics.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported framework metrics schema")
    return dict(payload.get("cases", {}))
```

Change the signature to:

```python
def render_report(
    root: Path,
    cases: list[tuple[str, str]],
    results: dict[str, dict[str, object]],
    lanes: list[dict],
    framework_metrics: dict[str, dict],
    secrets: tuple[str, ...],
) -> str:
```

Append this section before the function returns:

```python
    lines.extend(
        [
            "",
            "## Framework execution models",
            "",
            "- **FastAPI/native ASGI:** true-async end-to-end only when "
            "`FRAME-005` through `FRAME-013` pass.",
            "- **Flask/WSGI:** functional async-view compatibility; each "
            "request remains worker-bound.",
            "- **Flask via WsgiToAsgi:** persistent event-loop compatibility; "
            "the application remains adapted WSGI, not native ASGI.",
            "",
            "### Fixed exclusions",
            "",
            "- Production deployment tuning for Gunicorn, uWSGI, Hypercorn, "
            "or Uvicorn.",
            "- WebSockets.",
            "- Framework authentication, authorization, serialization, or "
            "ORM behavior.",
            "- Quart, gevent, eventlet, or non-`asyncio` event loops.",
            "- Multi-process pool sharing; each process must own its own pool.",
            "- Windows or Azure SQL authentication.",
            "",
            "### Framework metrics",
            "",
            "| Case | Metrics |",
            "|---|---|",
        ]
    )
    if not framework_metrics:
        lines.append("| none | NOT RUN |")
    for case_id, metrics in sorted(framework_metrics.items()):
        lines.append(
            f"| `{case_id}` | "
            f"{markdown_cell(json.dumps(metrics, sort_keys=True), secrets)} |"
        )
```

In `main`, load and pass:

```python
framework_metrics = load_framework_metrics(args.artifact_dir)
report = render_report(
    root,
    cases,
    results,
    lanes,
    framework_metrics,
    secrets,
)
```

- [ ] **Step 6: Verify the 250-case contract**

Run:

```bash
PYTHONPATH=python .venv/bin/pytest \
  tests/sql_auth_strict/test_matrix_contract.py -vv
PYTHONPATH=python .venv/bin/pytest \
  tests/sql_auth_strict --collect-only -q
.venv/bin/ruff check tests/sql_auth_strict \
  scripts/sql_auth/generate_report.py
bash -n scripts/sql_auth/run_all.sh
```

Expected: all contract tests pass; collection reports 250 unique case IDs with
no missing, extra, or duplicate source occurrences; Ruff and shell syntax pass.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock \
  docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  tests/sql_auth_strict/test_matrix_contract.py \
  scripts/sql_auth/run_all.sh scripts/sql_auth/generate_report.py
git commit -m "test: add framework SQL auth matrix lane"
```

---

### Task 9: Framework lane verification and generated evidence

**Files:**
- Modify generated: `docs/SQL_AUTH_TEST_MATRIX.md`
- Modify generated: `docs/SQL_AUTH_TEST_REPORT.md`

**Interfaces:**
- Consumes: dedicated real SQL Server, built local extension, 24 framework cases.
- Produces: repeatable framework results and final report evidence.

- [ ] **Step 1: Build the exact branch extension**

Run:

```bash
.venv/bin/maturin develop --release --skip-install --offline \
  --target-dir /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/target
```

Expected: wheel build succeeds from the current worktree.

- [ ] **Step 2: Run the framework lane twice against real SQL Server**

Run twice:

```bash
set -a
source .env.sql-auth.local
set +a
FASTMSSQL_SQL_AUTH_RESULTS_PATH=.artifacts/sql-auth/framework-results.json \
FASTMSSQL_FRAMEWORK_METRICS_PATH=.artifacts/sql-auth/framework-metrics.json \
PYTHONPATH=python .venv/bin/pytest \
  tests/sql_auth_strict/test_framework_integration.py \
  --junitxml=.artifacts/sql-auth/framework.xml -vv
```

Expected on each run: all framework tests pass, no skip or xfail, and every
`FRAME-*` case has a result.

- [ ] **Step 3: Validate artifacts and secrets**

Run:

```bash
jq -e '
  (.cases | keys | length) == 24
  and ([.cases[].outcome] | all(. == "passed"))
' .artifacts/sql-auth/framework-results.json
jq -e '
  .schema_version == 1
  and (.cases["FRAME-009"].ratio < 0.65)
  and (.cases["FRAME-010"].ticker_count >= 10)
  and (.cases["FRAME-021"].persistent_loop == true)
' .artifacts/sql-auth/framework-metrics.json
set -a
source .env.sql-auth.local
set +a
for secret in \
  "$FASTMSSQL_SQL_AUTH_SA_PASSWORD" \
  "$FASTMSSQL_SQL_AUTH_OWNER_PASSWORD" \
  "$FASTMSSQL_SQL_AUTH_READONLY_PASSWORD" \
  "$FASTMSSQL_SQL_AUTH_DENIED_PASSWORD"
do
  if rg -F -- "$secret" .artifacts/sql-auth docs/SQL_AUTH_TEST_REPORT.md; then
    exit 1
  fi
done
```

Expected: jq succeeds and no secret search produces output.

- [ ] **Step 4: Generate the 250-case matrix and report**

Run:

```bash
PYTHONPATH=python .venv/bin/python scripts/sql_auth/generate_report.py \
  --spec docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  --strict-results .artifacts/sql-auth/strict-results.json \
  --artifact-dir .artifacts/sql-auth \
  --matrix-output docs/SQL_AUTH_TEST_MATRIX.md \
  --report-output docs/SQL_AUTH_TEST_REPORT.md
```

Expected: the matrix contains 250 rows; framework conclusions remain separated
by execution model; missing non-framework evidence stays `NOT RUN`.

- [ ] **Step 5: Run static and Rust gates**

Run:

```bash
.venv/bin/ruff check tests/sql_auth_strict \
  scripts/sql_auth/generate_report.py
bash -n scripts/sql_auth/run_all.sh
git diff --check
cargo test
```

Expected: all commands exit 0. Record repository-wide pre-existing
`cargo fmt --check` or Clippy failures separately; do not reformat unrelated
upstream files.

- [ ] **Step 6: Commit generated evidence**

```bash
git add docs/SQL_AUTH_TEST_MATRIX.md docs/SQL_AUTH_TEST_REPORT.md
git commit -m "docs: record framework SQL auth evidence"
```

- [ ] **Step 7: Stop at the publication gate**

Report:

```text
local branch: test/sql-auth-validation
upstream push: DISABLED
publication target: user's fork as origin, only after explicit approval
```

Do not create a fork, push a branch, open a pull request, or publish a package
as part of this task.
