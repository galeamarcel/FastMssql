# FastMssql Connection Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make explicit connection startup prove a complete SQL Server
round-trip by default, preserve an explicit lazy opt-out, and expose a typed,
pool-aware `ping()` readiness API.

**Architecture:** One private Rust readiness primitive acquires from the shared
bb8 pool, executes fixed SQL `SELECT 1`, consumes the full TDS response, and
uses the existing pooled-operation guard to retire incomplete responses. One
outer Tokio timeout applies the pool's effective `connection_timeout` to
checkout plus SQL response consumption. `connect(validate=True)`, `ping()`,
and async context entry call the same primitive; `connect(validate=False)`
only initializes the pool handle.

**Tech Stack:** Rust, PyO3 0.29, Tokio, bb8 0.9.1, vendored Tiberius 0.12.3,
Python 3.13, pytest-asyncio, FastAPI, Flask through `WsgiToAsgi`, and SQL
Server Developer in Docker with SQL authentication.

## Global Constraints

- All branches, commits and pushes target
  `https://github.com/galeamarcel/FastMssql.git`.
- The implementation baseline is `test/sql-auth-validation` at `13c0925`.
- The `upstream` remote remains fetch-only with push URL `DISABLED`.
- No upstream PR is created without a separate explicit approval.
- No Tiberius fork is created or published.
- Keep design, RED test, production fix and status branches separate.
- `connect(validate=True)` is the default and performs a full `SELECT 1`
  round-trip on every call.
- `connect(validate=False)` initializes or reuses the pool without forcing a
  physical connection.
- `ping()` uses the shared pool and returns `True` or raises an existing typed
  FastMssql exception; it never converts a failure to `False`.
- Async context entry validates before yielding control to the body.
- `is_connected()` remains a pool-handle lifecycle check and performs no
  network I/O.
- Query-without-connect remains a supported lazy initialization path.
- The effective bb8 `connection_timeout`, including its 30-second default,
  bounds checkout plus complete readiness response consumption once.
- Cancellation and timeout preserve fail-closed lease retirement through
  `PooledOperationGuard`.
- No query, write, transaction or readiness operation is retried
  transparently.
- Secrets remain only in the ignored `.env.sql-auth.local`.
- Strict tests contain no skip, xfail, broad exception swallowing or accepted
  successful alternative.

---

## File Map

- `src/connection.rs`: owns the common readiness primitive and Rust-visible
  `connect`, `ping`, `is_connected`, and async context behavior.
- `src/pool_manager.rs`: supplies the existing shared checkout-error mapper
  and cancellation-safe `PooledOperationGuard`; its behavior is consumed, not
  duplicated.
- `python/fastmssql/__init__.py`: exposes runtime wrapper signatures and
  docstrings for readiness and lifecycle methods.
- `python/fastmssql/fastmssql.pyi`: describes the compiled Rust surface.
- `python/fastmssql/__init__.pyi`: describes the public Python wrapper surface.
- `tests/sql_auth_strict/test_connection.py`: contains CONN-020 through
  CONN-024 plus bounded checkout/response timeout probes.
- `tests/sql_auth_strict/framework_apps.py`: guarantees startup-failure cleanup
  for FastAPI and persistent-loop adapted Flask fixtures.
- `tests/sql_auth_strict/test_framework_integration.py`: contains FRAME-025
  and FRAME-026.
- `tests/sql_auth_strict/test_resilience_load.py`: contains LOAD-009 and its
  pool/session sampler.
- `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`:
  is the executable strict case registry and grows from 277 to 285 IDs.
- `tests/sql_auth_strict/test_matrix_contract.py`: enforces the exact 285-ID
  registry and generated-report totals.
- `README.md`: documents strict startup, explicit lazy allocation, real
  readiness, and the precise meaning of `is_connected()`.
- `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`: receives exact RED/GREEN,
  load, cleanup, and residual-risk evidence after the merged tree is verified.
- `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`: receives
  the isolated PR-20 candidate record without publishing upstream.

---

### Task 1: Commit and integrate the approved design artifacts

**Files:**

- Existing:
  `docs/superpowers/specs/2026-07-25-fastmssql-connection-readiness-design.md`
- Create:
  `docs/superpowers/plans/2026-07-25-fastmssql-connection-readiness.md`

**Interfaces:**

- Consumes: approved public signatures
  `connect(validate: bool = True) -> bool`, `ping() -> bool`, and unchanged
  `is_connected() -> bool`.
- Produces: a committed design baseline from which the RED branch is created.

- [ ] **Step 1: Verify the isolated design worktree**

Run:

```bash
git status --short --branch
git log -2 --oneline --decorate
git remote -v
git diff --check
```

Expected:

```text
branch                         docs/connection-readiness-design
specification commit           f06c844
origin                         galeamarcel/FastMssql
upstream push                  DISABLED
tracked modifications          only this implementation plan
diff whitespace errors         none
```

- [ ] **Step 2: Self-review the plan against the approved specification**

Run:

```bash
../../.venv/bin/python - <<'PY'
from pathlib import Path

text = Path(
    "docs/superpowers/plans/2026-07-25-fastmssql-connection-readiness.md"
).read_text(encoding="utf-8")
red_flags = (
    "TO" + "DO",
    "TB" + "D",
    "implement " + "later",
    "fill " + "in",
    "similar " + "to",
)
assert not {flag for flag in red_flags if flag in text}
PY
rg -n 'CONN-02[0-4]|FRAME-02[5-6]|LOAD-009|285' \
  docs/superpowers/plans/2026-07-25-fastmssql-connection-readiness.md
git diff --check
```

Expected:

```text
placeholder matches            none
all eight approved case IDs    present
exact target total             285
diff whitespace errors         none
```

- [ ] **Step 3: Commit and push only the design branch**

```bash
git add \
  docs/superpowers/plans/2026-07-25-fastmssql-connection-readiness.md
git commit -m "docs: plan strict connection readiness"
git push origin docs/connection-readiness-design
```

- [ ] **Step 4: Merge the design branch into the cumulative fork branch**

Run from the main `test/sql-auth-validation` worktree:

```bash
git merge --no-ff docs/connection-readiness-design \
  -m "merge: plan strict connection readiness"
git push origin test/sql-auth-validation
```

Expected: the design and plan exist on the fork's cumulative branch, while no
technical test or production source has changed.

---

### Task 2: Add deterministic RED connection contracts

**Files:**

- Modify:
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- Modify: `tests/sql_auth_strict/test_connection.py`
- Modify: `tests/sql_auth_strict/test_matrix_contract.py`

**Interfaces:**

- Consumes: current `Connection`, `PoolConfig`, `SqlConnectionError`,
  `ProtocolError`, `DownstreamGateProxy`, and DMV observer fixtures.
- Produces: CONN-020 through CONN-024, plus unnumbered timeout safety probes,
  all failing on the unchanged production source for the intended reason.

- [ ] **Step 1: Create the isolated RED worktree**

Run from the repository root:

```bash
git worktree add \
  .worktrees/test-connection-readiness \
  -b test/connection-readiness \
  test/sql-auth-validation
ln -s ../../.env.sql-auth.local \
  .worktrees/test-connection-readiness/.env.sql-auth.local
```

Expected: a clean `test/connection-readiness` worktree whose ignored local
environment symlink resolves to the existing SQL-auth credentials.

- [ ] **Step 2: Extend the strict case registry to 285 IDs**

Append these exact entries to the corresponding CONN, LOAD and FRAME sections
of
`docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`:

```markdown
- `CONN-020`: default `connect()` rejects lazy pool allocation as readiness
  when the SQL Server endpoint is unreachable.
- `CONN-021`: `connect(validate=False)` preserves explicit lazy allocation and
  `ping()` still detects the unreachable endpoint.
- `CONN-022`: default `connect()` creates an authenticated SQL session before
  any application query when `min_idle=0`.
- `CONN-023`: async connection context entry validates SQL Server before
  entering the body.
- `CONN-024`: a failed `ping()` on a killed physical connection retires it and
  a second explicit `ping()` recovers on a different `connection_id`.
```

```markdown
- `LOAD-009`: 1,000 readiness probes at task concurrency 100 remain bounded by
  `pool.max_size=20`, preserve a post-load query, and leave zero application
  sessions after disconnect.
```

```markdown
- `FRAME-025`: FastAPI lifespan rejects an unreachable SQL Server before
  serving and cleans its failed-startup pool.
- `FRAME-026`: persistent-loop Flask-through-ASGI startup rejects an
  unreachable SQL Server before serving and remains safe to shut down.
```

Change the five exact count assertions in
`tests/sql_auth_strict/test_matrix_contract.py` to:

```python
assert sum(line.startswith("| `") for line in matrix.splitlines()) == 285
assert "missing evidence for 285 case(s)" in completed.stderr
assert "| NOT RUN | 285 |" in report_output.read_text(encoding="utf-8")


def test_approved_spec_contains_285_unique_case_ids() -> None:
    spec = ROOT / (
        "docs/superpowers/specs/"
        "2026-07-24-fastmssql-sql-auth-validation-design.md"
    )
    ids = spec_case_ids(spec)
    assert len(ids) == 285
```

- [ ] **Step 3: Add shared observer and closed-endpoint helpers**

Add these imports and helpers to
`tests/sql_auth_strict/test_connection.py`:

```python
import asyncio
import time

from fastmssql import ProtocolError
from sql_auth_strict.tcp_fault_proxy import DownstreamGateProxy


def _closed_endpoint_connection() -> Connection:
    return Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="fastmssql_readiness_closed",
        password="not-a-credential",
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=1,
            min_idle=0,
            connection_timeout_secs=1,
            retry_connection=False,
        ),
    )


async def _application_session_rows(
    observer: Connection,
    application_name: str,
) -> list:
    return (
        await observer.query(
            """
            SELECT
                session_id,
                login_name,
                DB_NAME(database_id) AS database_name,
                program_name
            FROM sys.dm_exec_sessions
            WHERE program_name = @P1
              AND session_id <> @@SPID
            ORDER BY session_id
            """,
            [application_name],
        )
    ).rows()


async def _wait_for_application_session_count(
    observer: Connection,
    application_name: str,
    *,
    expected: int,
    timeout: float = 3.0,
) -> list:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = await _application_session_rows(observer, application_name)
        if len(rows) == expected:
            return rows
        await asyncio.sleep(0.02)
    rows = await _application_session_rows(observer, application_name)
    raise AssertionError(
        f"expected {expected} session(s) for {application_name!r}, "
        f"observed {len(rows)}"
    )


async def _pool_identity(connection: Connection) -> tuple[int, str]:
    row = (
        await connection.query(
            """
            SELECT
                @@SPID AS session_id,
                CONVERT(NVARCHAR(36), connection_id) AS connection_id
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """
        )
    ).fetchone()
    assert row is not None
    return int(row["session_id"]), str(row["connection_id"])
```

- [ ] **Step 4: Add CONN-020 and CONN-021**

Append these complete tests:

```python
@case("CONN-020")
@pytest.mark.asyncio
async def test_default_connect_rejects_lazy_false_positive() -> None:
    connection = _closed_endpoint_connection()
    try:
        with pytest.raises(SqlConnectionError):
            await connection.connect()
    finally:
        await connection.disconnect()


@case("CONN-021")
@pytest.mark.asyncio
async def test_explicit_lazy_connect_requires_ping_for_readiness() -> None:
    connection = _closed_endpoint_connection()
    try:
        assert await connection.connect(validate=False) is True
        assert await connection.is_connected() is True
        stats = await connection.pool_stats()
        assert stats["connections"] == 0
        assert stats["idle_connections"] == 0
        with pytest.raises(SqlConnectionError):
            await connection.ping()
    finally:
        assert await connection.disconnect() is True
    assert await connection.is_connected() is False
```

Baseline expectations:

```text
CONN-020   FAIL because connect() returns True without network access
CONN-021   FAIL because validate and ping do not exist
```

- [ ] **Step 5: Add CONN-022 and CONN-023**

Append:

```python
@case("CONN-022")
@pytest.mark.asyncio
async def test_strict_connect_creates_authenticated_session_with_min_idle_zero(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("strict_connect_readiness")
    connection = _individual_connection(
        sql_auth_config,
        application_name=application_name,
        pool_config=PoolConfig(
            max_size=1,
            min_idle=0,
            connection_timeout_secs=2,
            retry_connection=False,
        ),
    )
    assert await _application_session_rows(sa_connection, application_name) == []
    try:
        assert await connection.connect() is True
        stats = await connection.pool_stats()
        assert stats["connections"] == 1
        assert stats["idle_connections"] == 1
        rows = await _wait_for_application_session_count(
            sa_connection,
            application_name,
            expected=1,
        )
        row = rows[0]
        assert row["login_name"] == sql_auth_config.owner_user
        assert row["database_name"] == sql_auth_config.database
        assert row["program_name"] == application_name
    finally:
        await connection.disconnect()
    await _wait_for_application_session_count(
        sa_connection,
        application_name,
        expected=0,
    )


@case("CONN-023")
@pytest.mark.asyncio
async def test_async_context_validates_before_body_entry() -> None:
    connection = _closed_endpoint_connection()
    body_entered = False
    try:
        with pytest.raises(SqlConnectionError):
            async with connection:
                body_entered = True
    finally:
        await connection.disconnect()
    assert body_entered is False
```

Baseline expectations:

```text
CONN-022   FAIL because physical connection count remains zero
CONN-023   FAIL because the context body is entered
```

- [ ] **Step 6: Add CONN-024 physical retirement**

Append:

```python
@case("CONN-024")
@pytest.mark.asyncio
async def test_ping_retires_killed_connection_then_recovers_explicitly(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("strict_ping_recovery")
    connection = _individual_connection(
        sql_auth_config,
        application_name=application_name,
        pool_config=PoolConfig(
            max_size=1,
            min_idle=0,
            connection_timeout_secs=2,
            test_on_check_out=False,
            retry_connection=False,
        ),
    )
    try:
        assert await connection.connect() is True
        first_session_id, first_connection_id = await _pool_identity(connection)
        await sa_connection.execute(f"KILL {first_session_id}")
        await _wait_for_application_session_count(
            sa_connection,
            application_name,
            expected=0,
        )

        with pytest.raises((SqlConnectionError, ProtocolError)):
            await connection.ping()
        failed_stats = await connection.pool_stats()
        assert failed_stats["active_connections"] == 0
        assert failed_stats["connections"] == 0

        assert await connection.ping() is True
        second_session_id, second_connection_id = await _pool_identity(connection)
        assert second_connection_id != first_connection_id
        assert second_session_id > 0
    finally:
        await connection.disconnect()
    await _wait_for_application_session_count(
        sa_connection,
        application_name,
        expected=0,
    )
```

The first failed ping is asserted and never treated as successful recovery.
Only the second explicit call may succeed.

- [ ] **Step 7: Add bounded checkout and partial-response timeout probes**

Append these unnumbered strict support tests:

```python
@pytest.mark.asyncio
async def test_ping_timeout_includes_saturated_pool_checkout(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(
        sql_auth_config,
        pool_config=PoolConfig(
            max_size=1,
            min_idle=0,
            connection_timeout_secs=1,
            retry_connection=False,
        ),
    )
    blocker = asyncio.create_task(
        scalar(connection, "WAITFOR DELAY '00:00:05'; SELECT 1")
    )
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if (await connection.pool_stats())["active_connections"] == 1:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("blocking query never acquired the only lease")

        started = time.monotonic()
        with pytest.raises(SqlConnectionError):
            await connection.ping()
        elapsed = time.monotonic() - started
        assert 0.8 <= elapsed < 2.0
    finally:
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        await connection.disconnect()


@pytest.mark.asyncio
async def test_ping_timeout_retires_partial_tds_response(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("strict_ping_timeout")
    proxy = DownstreamGateProxy(sql_auth_config.host, sql_auth_config.port)
    await proxy.start()
    connection = Connection(
        server=proxy.host,
        port=proxy.port,
        database=sql_auth_config.database,
        username=sql_auth_config.owner_user,
        password=sql_auth_config.owner_password,
        application_name=application_name,
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=1,
            min_idle=0,
            connection_timeout_secs=1,
            test_on_check_out=False,
            retry_connection=False,
        ),
    )
    try:
        assert await connection.connect() is True
        _, first_connection_id = await _pool_identity(connection)
        proxy.pause_downstream()
        started = time.monotonic()
        with pytest.raises(
            SqlConnectionError,
            match="Connection readiness timed out",
        ):
            await connection.ping()
        elapsed = time.monotonic() - started
        assert 0.8 <= elapsed < 2.0
        await proxy.wait_until_downstream_held()
        proxy.resume_downstream()
        await _wait_for_application_session_count(
            sa_connection,
            application_name,
            expected=0,
        )

        assert await connection.ping() is True
        _, replacement_connection_id = await _pool_identity(connection)
        assert replacement_connection_id != first_connection_id
    finally:
        proxy.resume_downstream()
        await connection.disconnect()
        await proxy.close()
    await _wait_for_application_session_count(
        sa_connection,
        application_name,
        expected=0,
    )
```

- [ ] **Step 8: Build the unchanged source and run the intended RED tests**

Run:

```bash
CARGO_TARGET_DIR=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/target \
  ../../.venv/bin/maturin develop --release
set -a
source .env.sql-auth.local
set +a
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_connection.py \
  -k 'lazy_false_positive or requires_ping or authenticated_session_with_min_idle_zero or validates_before_body_entry or ping_retires or ping_timeout' \
  -q --tb=short
```

Expected: every selected new behavior test fails because the unchanged public
API does not validate readiness or expose `ping()`. Cleanup still leaves zero
test-owned SQL sessions.

- [ ] **Step 9: Verify and commit the first RED slice**

Run:

```bash
../../.venv/bin/ruff check \
  tests/sql_auth_strict/test_connection.py \
  tests/sql_auth_strict/test_matrix_contract.py
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_matrix_contract.py -q --tb=short
git diff --check
```

Expected: lint and matrix-contract checks pass while behavior remains RED.

Commit:

```bash
git add \
  docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  tests/sql_auth_strict/test_connection.py \
  tests/sql_auth_strict/test_matrix_contract.py
git commit -m "test: reproduce false connection readiness"
```

---

### Task 3: Add deterministic RED framework startup contracts

**Files:**

- Modify: `tests/sql_auth_strict/framework_apps.py`
- Modify: `tests/sql_auth_strict/test_framework_integration.py`

**Interfaces:**

- Consumes: strict default `Connection.connect()`, FastAPI lifespan,
  `WsgiToAsgi`, `SqlConnectionError`, and `wait_for_session_count`.
- Produces: FRAME-025 and FRAME-026 with unconditional failed-startup cleanup.

- [ ] **Step 1: Make framework fixture startup cleanup unconditional**

Replace the FastAPI lifespan in `create_fastapi_app` with:

```python
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await state.connection.connect()
            app.state.fastmssql = state
            yield
        finally:
            await state.connection.disconnect()
```

Add this persistent-loop adapted Flask helper:

```python
@asynccontextmanager
async def adapted_flask_lifespan(state: FrameworkState):
    try:
        await state.connection.connect()
        yield create_adapted_flask_app(state)
    finally:
        await state.connection.disconnect()
```

This cleanup does not make `connect()` destructively drop an existing shared
pool when one readiness call fails. The framework owner closes its own startup
resource explicitly.

- [ ] **Step 2: Add FRAME-025 and FRAME-026**

Add imports:

```python
from dataclasses import replace

from fastmssql import Connection, SqlConnectionError, SqlError
from sql_auth_strict.framework_apps import adapted_flask_lifespan
```

Append:

```python
@case("FRAME-025")
@pytest.mark.asyncio
async def test_fastapi_lifespan_rejects_unreachable_sql_before_serving(
    sql_auth_config,
    sa_connection,
    unique_sql_name,
) -> None:
    unreachable = replace(sql_auth_config, host="127.0.0.1", port=1)
    state = FrameworkState.create(
        unreachable,
        application_name=unique_sql_name("strict_fastapi_unreachable"),
        max_size=1,
        min_idle=0,
    )
    app = create_fastapi_app(state, "[unused_framework_table]")
    serving_started = False

    with pytest.raises(SqlConnectionError):
        async with LifespanManager(app):
            serving_started = True

    assert serving_started is False
    assert await state.connection.is_connected() is False
    await wait_for_session_count(
        sa_connection,
        state.application_name,
        expected=0,
    )


@case("FRAME-026")
@pytest.mark.asyncio
async def test_adapted_flask_startup_rejects_unreachable_sql_before_serving(
    sql_auth_config,
    sa_connection,
    unique_sql_name,
) -> None:
    unreachable = replace(sql_auth_config, host="127.0.0.1", port=1)
    state = FrameworkState.create(
        unreachable,
        application_name=unique_sql_name("strict_flask_asgi_unreachable"),
        max_size=1,
        min_idle=0,
    )
    serving_started = False

    with pytest.raises(SqlConnectionError):
        async with adapted_flask_lifespan(state):
            serving_started = True

    assert serving_started is False
    assert await state.connection.is_connected() is False
    assert await state.connection.disconnect() is False
    await wait_for_session_count(
        sa_connection,
        state.application_name,
        expected=0,
    )
```

- [ ] **Step 3: Run and commit the framework RED slice**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_framework_integration.py \
  -k 'unreachable_sql_before_serving' -q --tb=short
../../.venv/bin/ruff check \
  tests/sql_auth_strict/framework_apps.py \
  tests/sql_auth_strict/test_framework_integration.py
git diff --check
```

Expected: FRAME-025 and FRAME-026 fail because baseline `connect()` reports
success and enters the serving body; lint and whitespace checks pass.

Commit:

```bash
git add \
  tests/sql_auth_strict/framework_apps.py \
  tests/sql_auth_strict/test_framework_integration.py
git commit -m "test: require strict framework database startup"
```

---

### Task 4: Add deterministic RED readiness load contract

**Files:**

- Modify: `tests/sql_auth_strict/test_resilience_load.py`

**Interfaces:**

- Consumes: `_connection`, `_application_session_count`, `scalar`, and the
  `record_load_metric` fixture.
- Produces: LOAD-009 with 1,000 exact results, task concurrency 100,
  `pool.max_size=20`, post-load recovery, and zero-session cleanup.

- [ ] **Step 1: Append LOAD-009**

```python
@case("LOAD-009")
@pytest.mark.load
@pytest.mark.asyncio
async def test_thousand_readiness_probes_remain_pool_bounded(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    record_load_metric,
) -> None:
    probe_count = 1_000
    concurrency = 100
    max_size = 20
    application_name = unique_sql_name("strict_load_readiness")
    connection = _connection(
        sql_auth_config,
        max_size=max_size,
        min_idle=0,
        application_name=application_name,
    )
    semaphore = asyncio.Semaphore(concurrency)
    sampling_done = asyncio.Event()
    sampled_session_counts: list[int] = []

    async def probe() -> bool:
        async with semaphore:
            return await connection.ping()

    async def sample_sessions() -> None:
        while not sampling_done.is_set():
            sampled_session_counts.append(
                await _application_session_count(
                    sa_connection,
                    application_name,
                )
            )
            await asyncio.sleep(0.005)

    sampler = asyncio.create_task(sample_sessions())
    try:
        started = time.monotonic()
        outcomes = await asyncio.wait_for(
            asyncio.gather(*(probe() for _ in range(probe_count))),
            timeout=30.0,
        )
        elapsed = time.monotonic() - started
        sampling_done.set()
        await sampler

        assert outcomes == [True] * probe_count
        assert sampled_session_counts
        assert max(sampled_session_counts) <= max_size
        stats = await connection.pool_stats()
        assert stats["active_connections"] == 0
        assert stats["connections"] <= max_size
        assert await scalar(connection, "SELECT 9009") == 9009
        record_load_metric(
            "LOAD-009",
            probe_count=probe_count,
            task_concurrency=concurrency,
            pool_max_size=max_size,
            peak_observed_sessions=max(sampled_session_counts),
            elapsed_seconds=elapsed,
            probes_per_second=probe_count / elapsed,
        )
    finally:
        sampling_done.set()
        if not sampler.done():
            await sampler
        await connection.disconnect()

    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        remaining = await _application_session_count(
            sa_connection,
            application_name,
        )
        if remaining == 0:
            break
        await asyncio.sleep(0.02)
    else:
        raise AssertionError(
            f"readiness load left {remaining} SQL application session(s)"
        )
```

- [ ] **Step 2: Run, commit and push the complete RED branch**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_resilience_load.py \
  -k 'thousand_readiness_probes' -q --tb=short
../../.venv/bin/ruff check tests/sql_auth_strict/test_resilience_load.py
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_matrix_contract.py -q --tb=short
git diff --check
```

Expected: LOAD-009 fails because `Connection.ping` is absent; static and
matrix checks pass.

Commit and push:

```bash
git add tests/sql_auth_strict/test_resilience_load.py
git commit -m "test: add bounded connection readiness load"
git push -u origin test/connection-readiness
```

Before production changes, record:

```bash
git log --oneline --decorate test/sql-auth-validation..HEAD
git status --short
```

Expected: three test-only commits and a clean RED worktree.

---

### Task 5: Implement the single bounded readiness primitive

**Files:**

- Modify: `src/connection.rs`

**Interfaces:**

- Consumes:
  `map_pool_checkout_error(error) -> PyErr`,
  `PooledOperationGuard::new(connection)`,
  `PooledOperationGuard::complete_with_result_and_retirement`,
  `catch_driver_panic`, and
  `pool.config().connection_timeout`.
- Produces:
  `connect(validate: bool = true)`,
  `ping()`,
  strict `__aenter__`, and private
  `validate_pool_readiness(pool: &ConnectionPool) -> PyResult<()>`.

- [ ] **Step 1: Create the fix worktree from the committed RED branch**

Run:

```bash
git worktree add \
  .worktrees/fix-connection-readiness \
  -b fix/connection-readiness \
  test/connection-readiness
ln -s ../../.env.sql-auth.local \
  .worktrees/fix-connection-readiness/.env.sql-auth.local
```

- [ ] **Step 2: Use the common pool checkout mapper**

Change the pool-manager import to:

```rust
use crate::pool_manager::{
    ConnectionPool, PooledOperationGuard, ensure_pool_initialized_with_auth,
    map_pool_checkout_error,
};
```

Replace `get_pool_connection` with:

```rust
    async fn get_pool_connection(pool: &ConnectionPool) -> PyResult<PooledOperationGuard<'_>> {
        let connection = pool.get().await.map_err(map_pool_checkout_error)?;
        Ok(PooledOperationGuard::new(connection))
    }
```

- [ ] **Step 3: Add the fixed statement and common readiness primitive**

Add beside `ConnectionHandles`:

```rust
const READINESS_QUERY: &str = "SELECT 1";
```

Add inside `impl PyConnection`:

```rust
    async fn validate_pool_readiness(pool: &ConnectionPool) -> PyResult<()> {
        let readiness_timeout = pool.config().connection_timeout;
        let readiness = async {
            let mut connection = Self::get_pool_connection(pool).await?;
            let operation = catch_driver_panic(async {
                connection
                    .simple_query(READINESS_QUERY)
                    .await
                    .map_err(|error| {
                        create_sql_error(error, "Connection readiness query failed")
                    })?
                    .into_results()
                    .await
                    .map_err(|error| {
                        create_sql_error(
                            error,
                            "Failed to consume connection readiness response",
                        )
                    })?;
                Ok::<(), PyErr>(())
            })
            .await;

            match operation {
                Ok(result) => {
                    connection.complete_with_result_and_retirement(&result, false);
                    result
                }
                Err(driver_panic) => Err(driver_panic),
            }
        };

        match tokio::time::timeout(readiness_timeout, readiness).await {
            Ok(result) => result,
            Err(_) => Err(create_connection_error(format!(
                "Connection readiness timed out after {:.3} seconds",
                readiness_timeout.as_secs_f64(),
            ))),
        }
    }
```

The guard exists before the first TDS await. An outer timeout drops the
incomplete guard, which marks the physical connection unusable before bb8
receives the lease back.

- [ ] **Step 4: Make connect strict by default and add ping**

Replace the Rust public methods with:

```rust
    /// Initialize the shared pool and, by default, verify SQL Server readiness.
    ///
    /// Set `validate=False` only for intentional lazy pool allocation. A lazy
    /// pool is not proof that SQL Server is reachable.
    #[pyo3(signature = (validate = true))]
    pub fn connect<'p>(
        &self,
        py: Python<'p>,
        validate: bool,
    ) -> PyResult<Bound<'p, PyAny>> {
        let handles = self.clone_handles();
        future_into_py(py, async move {
            let pool = handles.ensure_connected().await?;
            if validate {
                Self::validate_pool_readiness(&pool).await?;
            }
            Ok(true)
        })
    }

    /// Execute a complete `SELECT 1` round-trip through the shared pool.
    ///
    /// Returns `True` on success and raises a typed FastMssql exception on
    /// checkout, authentication, TLS, SQL, protocol, I/O, panic, or timeout
    /// failure.
    pub fn ping<'p>(&self, py: Python<'p>) -> PyResult<Bound<'p, PyAny>> {
        let handles = self.clone_handles();
        future_into_py(py, async move {
            let pool = handles.ensure_connected().await?;
            Self::validate_pool_readiness(&pool).await?;
            Ok(true)
        })
    }
```

- [ ] **Step 5: Make async context entry use the same primitive**

Replace its future body with:

```rust
        future_into_py(py, async move {
            let pool = handles.ensure_connected().await?;
            Self::validate_pool_readiness(&pool).await?;
            Python::try_attach(|py| Ok(slf_clone.clone_ref(py))).ok_or_else(|| {
                pyo3::exceptions::PyRuntimeError::new_err(
                    "Failed to attach Python runtime thread",
                )
            })?
        })
```

Add this doc comment above `is_connected` without changing its code:

```rust
    /// Return whether this object currently owns a pool handle.
    ///
    /// This method performs no network I/O and does not prove SQL Server
    /// readiness. Use `ping()` for a live readiness check.
```

- [ ] **Step 6: Build and run the focused GREEN connection slice**

Run:

```bash
cargo fmt --check
CARGO_TARGET_DIR=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/target \
  ../../.venv/bin/maturin develop --release
set -a
source .env.sql-auth.local
set +a
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_connection.py \
  -k 'lazy_false_positive or requires_ping or authenticated_session_with_min_idle_zero or validates_before_body_entry or ping_retires or ping_timeout' \
  -q --tb=short
```

Expected: CONN-020 through CONN-024 and both timeout support tests pass, with
zero remaining application sessions.

- [ ] **Step 7: Commit the minimal Rust fix**

```bash
git add src/connection.rs
git commit -m "fix: validate SQL Server readiness on connect"
```

---

### Task 6: Expose and document the public readiness contract

**Files:**

- Modify: `python/fastmssql/__init__.py`
- Modify: `python/fastmssql/fastmssql.pyi`
- Modify: `python/fastmssql/__init__.pyi`
- Modify: `README.md`

**Interfaces:**

- Consumes: Rust `connect(validate=True)`, `ping()`, `disconnect()`, and
  `is_connected()`.
- Produces: explicit runtime and static public signatures with unambiguous
  readiness documentation.

- [ ] **Step 1: Add explicit wrapper methods and runtime docstrings**

Insert into public `Connection` before `__aenter__`:

```python
    async def connect(self, validate: bool = True) -> bool:
        """Initialize the pool and validate SQL Server by default.

        Pass ``validate=False`` only to allocate the pool lazily without
        claiming readiness.
        """
        return await self._conn.connect(validate)

    async def ping(self) -> bool:
        """Run a complete ``SELECT 1`` through the shared pool.

        Returns ``True`` on success and raises a typed FastMssql exception on
        failure.
        """
        return await self._conn.ping()

    async def disconnect(self) -> bool:
        """Drop this connection object's pool handle."""
        return await self._conn.disconnect()

    async def is_connected(self) -> bool:
        """Return whether this object owns a pool handle.

        This performs no network I/O. Use ``ping()`` for SQL Server readiness.
        """
        return await self._conn.is_connected()
```

- [ ] **Step 2: Update both stub surfaces**

Use this exact block in both `_RustConnection` and public `Connection`:

```python
    def connect(
        self,
        validate: bool = True,
    ) -> Coroutine[Any, Any, bool]:
        """Initialize the pool and validate SQL Server by default.

        Set validate=False for intentional lazy pool allocation without a
        readiness claim.
        """
        ...

    def ping(self) -> Coroutine[Any, Any, bool]:
        """Execute SELECT 1 through the shared pool.

        Return True on success and raise a typed FastMssql exception on failure.
        """
        ...

    def disconnect(self) -> Coroutine[Any, Any, bool]:
        """Drop this connection object's pool handle."""
        ...

    def is_connected(self) -> Coroutine[Any, Any, bool]:
        """Return whether this object currently owns a pool handle.

        This method performs no network I/O. Use ping() for readiness.
        """
        ...
```

Change both context-entry docstrings to:

```python
"""Validate SQL Server readiness before entering the async context."""
```

- [ ] **Step 3: Correct README connection and readiness examples**

Replace the quick-start pool tuple example with:

```python
        stats = await conn.pool_stats()
        print(
            "Pool: "
            f"connected={stats['connected']}, "
            f"size={stats['connections']}/{stats['max_size']}, "
            f"idle={stats['idle_connections']}, "
            f"min_idle={stats['min_idle']}"
        )
```

Replace the explicit-management explanation and example with:

```markdown
`query()`, `execute()`, and the other data methods still initialize the pool
lazily when needed. Explicit `connect()` is strict by default: it returns only
after a complete `SELECT 1` round-trip through the shared pool.

Use `connect(validate=False)` only when allocating the pool lazily is
intentional. `is_connected()` reports whether the object owns a pool handle;
it performs no network I/O. Use `ping()` for current SQL Server readiness.
```

```python
async def main():
    conn_str = "Server=localhost;Database=master;User Id=myuser;Password=mypass"
    conn = Connection(conn_str)

    await conn.connect()
    assert await conn.is_connected()  # pool handle exists
    assert await conn.ping()          # live SQL Server round-trip

    result = await conn.query("SELECT 42 as answer")
    print(result.rows()[0]["answer"])

    await conn.disconnect()
    assert not await conn.is_connected()
```

Add the intentional lazy path:

```python
await conn.connect(validate=False)
assert await conn.is_connected()
await conn.ping()
```

State explicitly that both `async with Connection(...)` and FastAPI lifespan
startup validate readiness before entering their body.

- [ ] **Step 4: Verify wrapper behavior and documentation**

Run:

```bash
../../.venv/bin/ruff check python
../../.venv/bin/python -m compileall -q python
../../.venv/bin/python - <<'PY'
import inspect
from fastmssql import Connection

connect_signature = inspect.signature(Connection.connect)
ping_signature = inspect.signature(Connection.ping)
assert tuple(connect_signature.parameters) == ("self", "validate")
assert connect_signature.parameters["validate"].default is True
assert tuple(ping_signature.parameters) == ("self",)
assert "no network I/O" in Connection.is_connected.__doc__
PY
git diff --check
```

Expected: all checks pass.

- [ ] **Step 5: Run framework and load GREEN slices**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_framework_integration.py \
  -k 'unreachable_sql_before_serving' -q --tb=short
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_resilience_load.py \
  -k 'thousand_readiness_probes' -q --tb=short
```

Expected:

```text
FRAME-025–FRAME-026       2/2 PASS
LOAD-009                  PASS
readiness results         1,000/1,000 True
peak SQL sessions         at most 20
post-load query           PASS
remaining app sessions    0
```

- [ ] **Step 6: Commit and push the complete fix branch**

```bash
git add \
  python/fastmssql/__init__.py \
  python/fastmssql/fastmssql.pyi \
  python/fastmssql/__init__.pyi \
  README.md \
  tests/sql_auth_strict/framework_apps.py
git commit -m "docs: expose strict connection readiness contract"
git push -u origin fix/connection-readiness
```

The test files inherited from the RED branch remain unchanged by the
production implementation.

---

### Task 7: Run complete verification on the fix branch

**Files:** no source change is allowed unless a failure is first reduced to a
new deterministic reproduction and committed separately.

**Interfaces:**

- Consumes: the complete fix branch.
- Produces: fresh exact evidence for Rust, strict SQL-auth, framework, load,
  applicable upstream tests, static analysis, security and repository safety.

- [ ] **Step 1: Run Rust and static gates**

Run:

```bash
cargo fmt --check
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
/private/tmp/fastmssql-cargo-audit-0.22.2/bin/cargo-audit \
  audit --deny warnings
../../.venv/bin/ruff check python tests/sql_auth_strict
../../.venv/bin/python -m compileall -q python tests/sql_auth_strict
git diff --check
```

Expected:

```text
FastMssql Rust tests       all PASS
fmt                        PASS
Clippy -D warnings         PASS
RustSec                    zero findings
Ruff                       PASS
compileall                 PASS
diff whitespace errors     none
```

- [ ] **Step 2: Run focused readiness, framework and pool regressions**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_connection.py \
  tests/sql_auth_strict/test_framework_integration.py \
  tests/sql_auth_strict/test_pool.py \
  tests/test_explicit_connection_advanced.py \
  tests/test_context_manager_edge_cases.py \
  -q --tb=short
```

Expected: zero failures, including repeated strict `connect()`, lazy first
query, nested contexts, pool saturation and post-failure recovery.

- [ ] **Step 3: Run the complete strict SQL-auth suite**

Run:

```bash
FASTMSSQL_SQL_AUTH_RESULTS_PATH=/private/tmp/readiness-strict.json \
FASTMSSQL_FRAMEWORK_METRICS_PATH=/private/tmp/readiness-framework.json \
FASTMSSQL_LOAD_METRICS_PATH=/private/tmp/readiness-load.json \
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict -q --tb=no
```

Then verify exact evidence:

```bash
../../.venv/bin/python - <<'PY'
import json
from pathlib import Path

payload = json.loads(
    Path("/private/tmp/readiness-strict.json").read_text(encoding="utf-8")
)
assert len(payload["cases"]) == 285
assert all(case["outcome"] == "passed" for case in payload["cases"].values())
PY
```

Expected: 285/285 strict IDs reported, no skipped or failed ID, all unnumbered
support tests green, and no test-owned SQL sessions after teardown.

- [ ] **Step 4: Run the complete applicable upstream suite**

Export the six upstream SQL-auth fixture variables against
`fastmssql_upstream_regression`, then run:

```bash
../../.venv/bin/python -m pytest -n 1 tests \
  --ignore=tests/test_azure_auth_advanced.py \
  --ignore=tests/test_azure_authentication.py \
  --ignore=tests/test_azure_cli_path_validation.py \
  --ignore=tests/test_transaction_azure_auth.py \
  --ignore=tests/test_transaction_azure_auth_advanced.py \
  --ignore=tests/sql_auth_strict \
  -q --tb=no
```

Expected: zero failures and no new exclusion.

- [ ] **Step 5: Run vendored Tiberius tests**

```bash
cargo test --manifest-path vendor/tiberius/Cargo.toml --locked
```

Expected: all vendored Tiberius unit tests pass; no vendored source changes are
present in this candidate.

- [ ] **Step 6: Verify repository identity and safety**

Run:

```bash
git status --short --branch
git log --format=fuller -8
git remote -v
git ls-files .env.sql-auth.local .artifacts
```

Expected:

```text
worktree              clean
author                Marcel Galea <galea.marcel@gmail.com>
origin                galeamarcel/FastMssql
upstream push          DISABLED
tracked secrets        none
upstream PR            none
```

---

### Task 8: Integrate and verify the exact cumulative fork tree

**Files:** the cumulative merge only; no squash and no upstream operation.

**Interfaces:**

- Consumes: verified `fix/connection-readiness`.
- Produces: exact cumulative fork head containing design, RED evidence and
  production fix history.

- [ ] **Step 1: Merge the fix branch into `test/sql-auth-validation`**

Run from the main worktree:

```bash
git merge --no-ff fix/connection-readiness \
  -m "merge: add strict connection readiness"
```

- [ ] **Step 2: Rebuild from the exact merged source**

```bash
CARGO_TARGET_DIR=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/target \
  .venv/bin/maturin develop --release
set -a
source .env.sql-auth.local
set +a
.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_connection.py \
  tests/sql_auth_strict/test_framework_integration.py \
  tests/sql_auth_strict/test_resilience_load.py \
  -k 'lazy_false_positive or requires_ping or authenticated_session_with_min_idle_zero or validates_before_body_entry or ping_retires or ping_timeout or unreachable_sql_before_serving or thousand_readiness_probes' \
  -q --tb=short
cargo test --locked
```

Expected: the merged installation resolves to the main worktree source and all
readiness gates remain green.

- [ ] **Step 3: Push only the cumulative fork branch**

```bash
git push origin test/sql-auth-validation
```

Verify:

```bash
git status --short --branch
git rev-list --left-right --count \
  origin/test/sql-auth-validation...test/sql-auth-validation
git remote -v
```

Expected: clean, `0 0`, fork origin, and upstream push `DISABLED`.

---

### Task 9: Update the live audit and PR-20 candidate record

**Files:**

- Modify: `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- Modify:
  `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`

**Interfaces:**

- Consumes: exact merged commit, RED commits, all verification outputs and
  readiness load metrics.
- Produces: a fork-only `VERIFIED_FORK` PR-20 candidate with explicit residual
  risks and no publication.

- [ ] **Step 1: Create the status branch from the verified cumulative head**

```bash
git worktree add \
  .worktrees/docs-connection-readiness-status \
  -b docs/connection-readiness-status \
  test/sql-auth-validation
```

- [ ] **Step 2: Record exact audit evidence**

Add a dated checkpoint containing:

```text
baseline false positive             connect=True, pool connections=0
CONN-020–CONN-024                   5/5 PASS
FRAME-025–FRAME-026                 2/2 PASS
LOAD-009                            PASS
readiness probe results             1,000/1,000 True
peak observed readiness sessions    value from readiness-load.json, <=20
post-load application query         PASS
strict SQL-auth IDs                 285/285 PASS
complete strict suite               exact pytest count, zero failures
applicable upstream suite           exact pytest count, zero failures
FastMssql Rust tests                exact count, zero failures
vendored Tiberius tests             exact count, zero failures
fmt / Clippy / Ruff / compileall    PASS
RustSec                             zero findings
remaining application sessions      0
origin                              galeamarcel/FastMssql
upstream push                       DISABLED
upstream PR                         not created
```

State the remaining boundaries precisely:

- `is_connected()` reports only pool-handle lifecycle;
- a readiness failure does not destructively remove a shared pool;
- framework owners perform failed-startup cleanup in `finally`;
- concurrent `disconnect()` serialization belongs to the lifecycle-state
  candidate;
- general per-operation timeout taxonomy and telemetry are excluded;
- TDS `ATTENTION` and same-socket cancellation reuse are excluded.

- [ ] **Step 3: Add PR-20 to the upstream roadmap**

Use:

```markdown
### Task 19: PR-20 — Strict SQL Server connection readiness

**State:** `VERIFIED_FORK`

**Scope:** one shared-pool `SELECT 1` primitive; strict
`connect(validate=True)` default; explicit `connect(validate=False)` lazy
allocation; public `ping()`; strict async context entry; precise
`is_connected()` lifecycle semantics.

**Excluded:** lifecycle state machine, general timeout taxonomy, metrics,
automatic retry, TDS `ATTENTION`, Tiberius publication, and any upstream
operation.
```

Include the exact RED/GREEN commits, cumulative merge commit, test totals,
load metrics, physical `connection_id` replacement proof, and the requirement
for a clean rebase onto the current upstream before publication is proposed.

- [ ] **Step 4: Commit, push and merge the status branch only on the fork**

```bash
git add \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md
git commit -m "docs: record strict connection readiness candidate"
git push -u origin docs/connection-readiness-status
```

Merge into `test/sql-auth-validation`, push that branch to `origin`, and
verify remote parity. Do not modify the intentionally separate executive
registry branch unless the user requests it.

---

## Completion Gate

This candidate is complete only when fresh evidence on the exact cumulative
fork head shows:

```text
CONN-020–CONN-024                    PASS
FRAME-025–FRAME-026                  PASS
LOAD-009                             PASS
strict specification IDs            285/285
complete strict SQL-auth suite       zero failures
applicable upstream suite            zero failures
FastMssql Rust unit tests            zero failures
vendored Tiberius unit tests         zero failures
cargo fmt                            PASS
Clippy -D warnings                   PASS
Ruff / compileall                    PASS
RustSec                              zero findings
readiness storm                      bounded at pool.max_size=20
post-load query                      PASS
remaining application sessions      0
origin                               galeamarcel/FastMssql
upstream push                        DISABLED
upstream PR                          not created
```

The next independent audit candidate begins only after the status branch is
committed and the cumulative fork is clean and synchronized.
