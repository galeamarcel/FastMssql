# FastMssql Connection Lifecycle and Graceful Shutdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. This session must use inline
> `superpowers:executing-plans`; delegated subagents are not permitted by the
> active repository instructions.

**Goal:** Add a generation-aware `Open -> Closing -> Closed` lifecycle that
drains admitted work, force-retires over-deadline sessions, preserves
transaction outcome safety and allows deterministic reconnection.

**Architecture:** A new connection-scoped coordinator serializes admission and
shutdown transitions with a short-held standard mutex, mirrors public state in
an atomic byte, counts ordinary operations with RAII permits, and registers
long-lived pooled transaction sessions for forced cleanup. One Tokio-owned
supervisor performs each shutdown round and publishes cloneable outcome data
to all Python waiters; SQL futures select a generation-specific force signal
and retire their transport when interrupted.

**Tech Stack:** Rust 2024, PyO3 0.29, pyo3-async-runtimes 0.29, Tokio 1.52,
bb8 0.9.1, vendored Tiberius, Python 3.11+, pytest/pytest-asyncio, Docker SQL
Server with SQL authentication, FastAPI/ASGI, Flask/WSGI and WsgiToAsgi.

## Global constraints

- Source baseline is `test/sql-auth-validation` at
  `0992f60a23db7e038cba484d9eadfbff6606d2ad`.
- Design authority is
  `docs/superpowers/specs/2026-07-26-fastmssql-lifecycle-state-design.md`.
- All branches, commits, pushes, workflow runs and artifacts target only
  `https://github.com/galeamarcel/FastMssql.git`.
- `upstream` must remain fetch-only with push URL exactly `DISABLED`.
- No upstream branch, pull request, package publication or dependency fork is
  authorized.
- Use project-local `.worktrees/`; preserve every existing worktree.
- Keep documentation, RED tests, GREEN implementation and final status on
  distinct branches.
- RED assertions may not be deleted, weakened, skipped or rewritten in GREEN.
- Use real Docker SQL Server with SQL authentication for SQL behavior; never
  substitute SQLite.
- Do not log credentials, connection strings, SQL parameter values or the
  contents of `.env.sql-auth.local`.
- `LifecycleConfig.shutdown_timeout_secs` defaults to `30.0`.
- `LifecycleConfig.force_timeout_secs` defaults to `5.0`.
- Both lifecycle values are required, finite, positive, at least one
  nanosecond and at most `3_153_600_000` seconds.
- The initial public state is `ConnectionLifecycleState.OPEN`.
- `Closing` rejects new SQL admission before pool initialization or network
  I/O, but allows commit, rollback and close for an already permitted pooled
  transaction.
- `Closed` may reopen only as a fresh generation after the previous supervisor
  has finished.
- Every forced query/simple-query/execute/batch/bulk interruption is
  conservatively `outcome_unknown=True`; FastMssql performs no retry.
- An unconfirmed forced COMMIT must remain
  `CommitOutcomeUnknown(ConnectionLifecycleError)`.
- Pool backpressure, metrics, tracing, TDS ATTENTION, streaming and context
  cleanup aggregation remain out of scope.
- Run code-review-graph graph-first before source editing and rebuild it after
  implementation.
- The repository has no `VERSION.md`; each handoff must explicitly report that
  no version ledger could be updated rather than creating an unrelated file.

---

## File map and ownership boundaries

### New files

- `src/lifecycle_config.rs` — public `LifecycleConfig` and
  `ConnectionLifecycleState`; portable validation and Python representation.
- `src/lifecycle.rs` — internal coordinator, generation state, operation and
  transaction permits, force signal, participant interface and shutdown
  supervisor.
- `tests/test_lifecycle_contract.py` — pure/runtime/stub API contract.
- `tests/sql_auth_strict/test_lifecycle.py` — real SQL-auth lifecycle cases
  `LIFE-002` through `LIFE-014`.

### Existing Rust files

- `src/types.rs` — Python exception classes and structured lifecycle error
  constructors.
- `src/lib.rs` — module declarations, public re-exports and PyO3 registration.
- `src/connection.rs` — coordinator ownership, public properties, operation
  admission, supervisor-backed disconnect and context exit.
- `src/batch.rs` — lifecycle permits around pooled query/bulk paths and the
  direct `execute_batch()` socket.
- `src/transaction.rs` — pooled-session permit lifetime, old-generation
  fail-closed state, settlement authorization and forced participant cleanup.

### Existing Python/docs/test files

- `python/fastmssql/__init__.py` — package imports, wrapper properties and
  `__all__`.
- `python/fastmssql/fastmssql.pyi` — core public types and constructor
  signature.
- `python/fastmssql/__init__.pyi` — wrapper public types and constructor
  signature.
- `README.md` — lifecycle semantics, framework usage and timeout handling.
- `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
  — authoritative `LIFE-001..015` registry.
- `tests/sql_auth_strict/test_matrix_contract.py` — strict registry count from
  295 to 310.
- `tests/sql_auth_strict/test_framework_integration.py` — `LIFE-015`.
- `tests/sql_auth_strict/framework_apps.py` — application factories that
  accept lifecycle configuration and expose lifecycle errors without secrets.
- `scripts/sql_auth/run_all.sh` — include the lifecycle strict file.
- `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md` — final measured evidence and
  remaining limitations only after GREEN verification.
- `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md` — future
  PR candidate summary only after GREEN verification; no PR creation.

## Stable internal interfaces

Implement these names consistently across tasks:

```rust
// src/lifecycle_config.rs
pub struct PyLifecycleConfig {
    pub(crate) shutdown_timeout: Duration,
    pub(crate) force_timeout: Duration,
}

pub enum ConnectionLifecycleState {
    Open,
    Closing,
    Closed,
}

// src/lifecycle.rs
pub(crate) struct ConnectionLifecycle;
pub(crate) struct OperationPermit;
pub(crate) struct TransactionPermit;
pub(crate) struct ForceRequested;
#[derive(Clone, Debug)]
pub(crate) struct ShutdownOutcome {
    pub(crate) had_resources: bool,
    pub(crate) generation: u64,
    pub(crate) forced: bool,
    pub(crate) shutdown_timeout: Duration,
    pub(crate) force_timeout: Duration,
    pub(crate) active_operations_at_timeout: usize,
    pub(crate) active_transactions_at_timeout: usize,
    pub(crate) force_completed: bool,
}
pub(crate) struct LifecycleFailure {
    pub(crate) operation: OperationName,
    pub(crate) state: ConnectionLifecycleState,
    pub(crate) generation: u64,
    pub(crate) retryable: bool,
    pub(crate) connection_discarded: bool,
    pub(crate) outcome_unknown: bool,
    pub(crate) forced: bool,
}

pub(crate) trait ForcedShutdownParticipant: Send + Sync {
    fn force_close(
        &self,
        generation: u64,
    ) -> Pin<Box<dyn Future<Output = ()> + Send + 'static>>;
}

pub(crate) async fn run_force_aware<T, F>(
    force_receiver: watch::Receiver<bool>,
    future: F,
) -> Result<T, ForceRequested>
where
    F: Future<Output = T>;

impl ConnectionLifecycle {
    pub(crate) fn new() -> Arc<Self>;
    pub(crate) fn state(&self) -> ConnectionLifecycleState;
    pub(crate) fn admit_operation(
        self: &Arc<Self>,
        operation: OperationName,
        outcome_unknown: bool,
    ) -> PyResult<OperationPermit>;
    pub(crate) fn admit_transaction(
        self: &Arc<Self>,
        participant: Arc<dyn ForcedShutdownParticipant>,
    ) -> PyResult<TransactionPermit>;
    pub(crate) async fn shutdown(
        self: Arc<Self>,
        pool: Arc<RwLock<Option<ConnectionPool>>>,
        config: PyLifecycleConfig,
    ) -> PyResult<bool>;
}

impl OperationPermit {
    pub(crate) async fn run<T, F>(mut self, future: F) -> PyResult<T>
    where
        T: Send + 'static,
        F: Future<Output = PyResult<T>> + Send;
}

impl TransactionPermit {
    pub(crate) fn generation(&self) -> u64;
    pub(crate) fn authorize_data(&self) -> PyResult<()>;
    pub(crate) fn authorize_settlement(&self) -> PyResult<()>;
    pub(crate) fn force_receiver(&self) -> watch::Receiver<bool>;
}
```

`ShutdownOutcome` is cloneable Rust data. It is never a cached `PyErr`.
Python errors are constructed independently for each waiter.

---

### Task 1: Preserve design history and create the RED worktree

**Files:**

- Verify:
  `docs/superpowers/specs/2026-07-26-fastmssql-lifecycle-state-design.md`
- Verify:
  `docs/superpowers/plans/2026-07-26-fastmssql-lifecycle-state.md`

**Interfaces:**

- Consumes: design commit `0184e8f`.
- Produces: pushed `docs/lifecycle-state-design` and isolated
  `test/lifecycle-state`.

- [ ] **Step 1: Verify branch, remotes and clean documentation tree**

Run:

```bash
git status --short --branch
git remote -v
git log -2 --oneline
git diff --check HEAD^ HEAD
```

Expected:

```text
branch docs/lifecycle-state-design
origin push https://github.com/galeamarcel/FastMssql.git
upstream push DISABLED
clean worktree
design and plan are the newest commits
```

- [ ] **Step 2: Push documentation only to the fork**

Run:

```bash
git push -u origin docs/lifecycle-state-design
```

Expected: branch is created on `galeamarcel/FastMssql`; no upstream ref
changes.

- [ ] **Step 3: Create the RED branch in a project-local worktree**

From the repository root:

```bash
git worktree add .worktrees/test-lifecycle-state \
  -b test/lifecycle-state docs/lifecycle-state-design
```

Expected: `.worktrees/test-lifecycle-state` is on
`test/lifecycle-state` and inherits both documentation commits.

- [ ] **Step 4: Link the ignored local SQL-auth environment**

Inside `.worktrees/test-lifecycle-state`:

```bash
test -f ../../.env.sql-auth.local
test ! -e .env.sql-auth.local
ln -s ../../.env.sql-auth.local .env.sql-auth.local
git check-ignore .env.sql-auth.local
```

Expected: the symlink resolves to the repository root's approved environment
and Git reports it ignored. Do not print, copy or add the file.

- [ ] **Step 5: Verify the exact baseline in the RED worktree**

Run:

```bash
git status --short --branch
cargo test --locked
```

Expected: clean branch and `23 passed; 0 failed` Rust tests before RED files
are added.

---

### Task 2: Add the RED public contract and strict registry

**Files:**

- Create: `tests/test_lifecycle_contract.py`
- Modify:
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- Modify: `tests/sql_auth_strict/test_matrix_contract.py`
- Modify: `scripts/sql_auth/run_all.sh`

**Interfaces:**

- Consumes: public API from the approved lifecycle design.
- Produces: exact `LIFE-001..015` registry and a focused RED contract for
  `LifecycleConfig`, state and errors.

- [ ] **Step 1: Add the fifteen approved case descriptions**

Append this section before the strict specification's error workflow:

```markdown
### LIFE — connection lifecycle and graceful shutdown

- `LIFE-001`: public config/state/error exports, defaults, signatures, stubs,
  copy isolation and portable validation.
- `LIFE-002`: initial Open, pool-independent state and graceful transitions.
- `LIFE-003`: disconnect waits for admitted SQL and leaves zero sessions.
- `LIFE-004`: Closing rejects new SQL and Closed permits a new generation.
- `LIFE-005`: concurrent/cancelled shutdown waiters share one supervisor.
- `LIFE-006`: admitted pool waiters remain inside the drain barrier.
- `LIFE-007`: an active pooled transaction may commit while Closing.
- `LIFE-008`: rollback/close release the transaction permit exactly once.
- `LIFE-009`: grace expiry force-retires a query and allows recovery.
- `LIFE-010`: forced writes/batches are uncertain and never retried.
- `LIFE-011`: force rolls back and revokes an idle pooled transaction.
- `LIFE-012`: forced unconfirmed COMMIT remains CommitOutcomeUnknown.
- `LIFE-013`: direct execute_batch and nested contexts use one lifecycle.
- `LIFE-014`: 100 generations and 2,000 operations leave no stale work.
- `LIFE-015`: ASGI lifecycle is persistent; Flask/WSGI remains per-loop.
```

Do not change any existing ID.

- [ ] **Step 2: Change only the registry-count assertions**

In `tests/sql_auth_strict/test_matrix_contract.py`, rename
`test_approved_spec_contains_295_unique_case_ids` to
`test_approved_spec_contains_310_unique_case_ids` and replace every count
literal that describes the complete matrix:

```python
assert sum(line.startswith("| `") for line in matrix.splitlines()) == 310
assert "missing evidence for 310 case(s)" in completed.stderr
assert "| NOT RUN | 310 |" in report_output.read_text(encoding="utf-8")
assert len(spec_case_ids(spec)) == 310
```

Leave unrelated measured historical counts unchanged.

- [ ] **Step 3: Wire the lifecycle file into the strict lane**

Add this path immediately after `test_operation_timeouts.py`:

```bash
tests/sql_auth_strict/test_lifecycle.py
```

- [ ] **Step 4: Write the public RED contract**

Create `tests/test_lifecycle_contract.py` with these exact constants and
assertion groups:

```python
from __future__ import annotations

import ast
import importlib
import inspect
import math
from pathlib import Path

import fastmssql
import pytest


ROOT = Path(__file__).resolve().parents[1]
CORE_STUB = ROOT / "python/fastmssql/fastmssql.pyi"
WRAPPER_STUB = ROOT / "python/fastmssql/__init__.pyi"
README = ROOT / "README.md"
DEFAULTS = {
    "shutdown_timeout_secs": 30.0,
    "force_timeout_secs": 5.0,
}
TEXT_SIGNATURE = (
    "(shutdown_timeout_secs=30.0, force_timeout_secs=5.0)"
)
INVALID = (True, False, None, 0, -0.1, math.nan, math.inf, -math.inf)
UNREPRESENTABLE = (5e-324, 1e-12, 0.5e-9, 1e19)
PORTABLE_CEILING = 100 * 365 * 24 * 60 * 60


def public_type(name: str):
    assert hasattr(fastmssql, name), f"missing package export {name}"
    assert name in fastmssql.__all__
    return getattr(fastmssql, name)


def class_node(path: Path, name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def test_lifecycle_config_defaults_signature_repr_and_validation() -> None:
    lifecycle_type = public_type("LifecycleConfig")
    assert lifecycle_type.__text_signature__ == TEXT_SIGNATURE
    signature = inspect.signature(lifecycle_type)
    assert tuple(signature.parameters) == tuple(DEFAULTS)
    assert {
        name: parameter.default
        for name, parameter in signature.parameters.items()
    } == DEFAULTS
    config = lifecycle_type()
    assert {name: getattr(config, name) for name in DEFAULTS} == DEFAULTS
    assert repr(config) == (
        "LifecycleConfig(shutdown_timeout_secs=30.0, "
        "force_timeout_secs=5.0)"
    )

    for field in DEFAULTS:
        for value in (*INVALID, *UNREPRESENTABLE):
            with pytest.raises(ValueError):
                lifecycle_type(**{field: value})
            mutable = lifecycle_type()
            original = getattr(mutable, field)
            with pytest.raises(ValueError):
                setattr(mutable, field, value)
            assert getattr(mutable, field) == original
        mutable = lifecycle_type()
        setattr(mutable, field, 0.125)
        assert getattr(mutable, field) == 0.125
        boundary = lifecycle_type(**{field: PORTABLE_CEILING})
        assert getattr(boundary, field) == PORTABLE_CEILING
        with pytest.raises(ValueError, match="100 years"):
            lifecycle_type(**{field: PORTABLE_CEILING + 1})


def test_lifecycle_state_and_errors_are_public_and_typed() -> None:
    state = public_type("ConnectionLifecycleState")
    lifecycle_error = public_type("ConnectionLifecycleError")
    shutdown_error = public_type("ShutdownTimeoutError")
    assert str(state.OPEN) == "Open"
    assert str(state.CLOSING) == "Closing"
    assert str(state.CLOSED) == "Closed"
    assert issubclass(lifecycle_error, fastmssql.SqlConnectionError)
    assert issubclass(shutdown_error, lifecycle_error)


def test_compiled_connection_appends_lifecycle_config() -> None:
    core = importlib.import_module("fastmssql.fastmssql")
    parameters = tuple(inspect.signature(core.Connection).parameters.values())
    assert parameters[-1].name == "lifecycle_config"
    assert parameters[-1].default is None


def test_lifecycle_stubs_and_readme_match_runtime_contract() -> None:
    for name in (
        "LifecycleConfig",
        "ConnectionLifecycleState",
        "ConnectionLifecycleError",
        "ShutdownTimeoutError",
    ):
        class_node(CORE_STUB, name)
    for stub in (CORE_STUB, WRAPPER_STUB):
        text = stub.read_text(encoding="utf-8")
        assert "lifecycle_config: Optional[LifecycleConfig] = None" in text
        assert "def lifecycle_config(self) -> LifecycleConfig" in text
        assert "def lifecycle_state(self) -> ConnectionLifecycleState" in text
    readme = README.read_text(encoding="utf-8")
    for token in (
        "Open",
        "Closing",
        "Closed",
        "shutdown_timeout_secs",
        "force_timeout_secs",
        "ShutdownTimeoutError",
    ):
        assert token in readme
```

Add two further tests in the same file:

```python
def test_connection_stores_an_isolated_lifecycle_config() -> None:
    external = fastmssql.LifecycleConfig(
        shutdown_timeout_secs=0.5,
        force_timeout_secs=0.25,
    )
    connection = fastmssql.Connection(
        server="localhost",
        database="master",
        username="test",
        password="secret",
        ssl_config=fastmssql.SslConfig.development(),
        lifecycle_config=external,
    )
    external.shutdown_timeout_secs = 9.0
    exposed = connection.lifecycle_config
    assert exposed.shutdown_timeout_secs == 0.5
    exposed.shutdown_timeout_secs = 8.0
    assert connection.lifecycle_config.shutdown_timeout_secs == 0.5
    with pytest.raises(AttributeError):
        connection.lifecycle_config = fastmssql.LifecycleConfig()


def test_new_connection_is_open_but_not_connected() -> None:
    connection = fastmssql.Connection(
        server="localhost",
        database="master",
        username="test",
        password="secret",
        ssl_config=fastmssql.SslConfig.development(),
    )
    assert connection.lifecycle_state == fastmssql.ConnectionLifecycleState.OPEN
```

- [ ] **Step 5: Run the registry test GREEN and public contract RED**

Run:

```bash
uv run pytest \
  tests/sql_auth_strict/test_matrix_contract.py \
  tests/test_lifecycle_contract.py -q
```

Expected:

```text
the registry/report assertions pass with 310 unique IDs
the source-attachment assertion remains RED for LIFE-001..015 until Tasks 3-5
the lifecycle contract fails because LifecycleConfig is not exported
```

At this intermediate commit, the full matrix file is intentionally not green:
the specification IDs exist before their test-source markers. After Tasks 3-5
attach every LIFE marker, rerun the complete matrix and require it to pass.
If any other failure occurs, fix only the test fixture or registry wiring; do
not add implementation on the RED branch.

- [ ] **Step 6: Commit the public RED contract**

Run:

```bash
git add \
  docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  scripts/sql_auth/run_all.sh \
  tests/sql_auth_strict/test_matrix_contract.py \
  tests/test_lifecycle_contract.py
git commit -m "test: define connection lifecycle contract"
```

---

### Task 3: Add deterministic graceful-shutdown RED cases

**Files:**

- Create: `tests/sql_auth_strict/test_lifecycle.py`

**Interfaces:**

- Consumes: strict `SqlAuthConfig`, `case`, `wait_until`, SQL observer
  connection and public lifecycle API.
- Produces: `LIFE-001..006` real SQL-auth reproductions.

- [ ] **Step 1: Add connection and DMV helpers**

Use the same construction policy as `test_connection.py`:

```python
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
import time

from fastmssql import (
    CommitOutcomeUnknown,
    Connection,
    ConnectionLifecycleError,
    ConnectionLifecycleState,
    LifecycleConfig,
    PoolConfig,
    QueryStream,
    ShutdownTimeoutError,
    SqlConnectionError,
    SslConfig,
)
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import CleanupRegistry, scalar
from sql_auth_strict.tcp_fault_proxy import DownstreamGateProxy
from sql_auth_strict.timeout_fixtures import wait_until


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


def lifecycle_connection(
    config: SqlAuthConfig,
    *,
    application_name: str,
    max_size: int = 2,
    shutdown_timeout: float = 3.0,
    force_timeout: float = 1.0,
) -> Connection:
    return Connection(
        server=config.host,
        port=config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=max_size,
            min_idle=0,
            connection_timeout_secs=3,
            retry_connection=False,
        ),
        lifecycle_config=LifecycleConfig(
            shutdown_timeout_secs=shutdown_timeout,
            force_timeout_secs=force_timeout,
        ),
        application_name=application_name,
    )
```

Add `application_requests(observer, name)` using this SQL:

```sql
SELECT session_id, status, command
FROM sys.dm_exec_requests
WHERE session_id IN (
    SELECT session_id
    FROM sys.dm_exec_sessions
    WHERE program_name = @P1
)
  AND session_id <> @@SPID
ORDER BY session_id
```

Add `application_sessions(observer, name)` using the existing
`sys.dm_exec_sessions` pattern. Poll with `wait_until`; never synchronize only
with a fixed sleep.

Define the helpers used by later test steps:

```python
async def wait_for_state(
    connection: Connection,
    expected: ConnectionLifecycleState,
    *,
    timeout: float = 3.0,
) -> None:
    async def state_matches() -> bool:
        return connection.lifecycle_state == expected

    await wait_until(state_matches, timeout=timeout)


async def wait_for_one_visible_request(
    observer: Connection,
    application_name: str,
) -> None:
    async def one_request() -> bool:
        return len(
            await application_requests(observer, application_name)
        ) == 1

    await wait_until(one_request, timeout=3.0)


async def wait_for_zero_sessions(
    observer: Connection,
    application_name: str,
) -> None:
    async def no_sessions() -> bool:
        return not await application_sessions(observer, application_name)

    await wait_until(no_sessions, timeout=4.0)


async def start_visible_waitfor(
    connection: Connection,
    observer: Connection,
    application_name: str,
    *,
    seconds: int = 1,
    slot: int = 1,
) -> asyncio.Task:
    task = asyncio.create_task(
        connection.query(
            f"WAITFOR DELAY '00:00:{seconds:02d}'; SELECT @P1 AS slot",
            [slot],
        )
    )
    await wait_for_one_visible_request(observer, application_name)
    return task
```

Every test owns its created `Connection` and task set in `try/finally`.
The `finally` block cancels and awaits unfinished tasks, then awaits
`connection.disconnect()` while suppressing only the expected
`ShutdownTimeoutError` from a force case. It never swallows an unexpected
cleanup exception.

- [ ] **Step 2: Add the strict public API and state test**

The pure contract from Task 2 is not collected by the strict case registry.
Add one real SQL-auth node for `LIFE-001`:

```python
@case("LIFE-001")
@pytest.mark.asyncio
async def test_public_lifecycle_api_on_real_sql_auth(
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
) -> None:
    config = LifecycleConfig(
        shutdown_timeout_secs=2.0,
        force_timeout_secs=1.0,
    )
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=unique_sql_name("strict_lifecycle_api"),
        shutdown_timeout=config.shutdown_timeout_secs,
        force_timeout=config.force_timeout_secs,
    )
    assert connection.lifecycle_state == ConnectionLifecycleState.OPEN
    assert issubclass(ConnectionLifecycleError, SqlConnectionError)
    assert issubclass(ShutdownTimeoutError, ConnectionLifecycleError)
    assert await connection.connect() is True
    assert await connection.disconnect() is True
```

- [ ] **Step 3: Add state and graceful-query tests**

Implement:

```python
@case("LIFE-002")
@pytest.mark.asyncio
async def test_lifecycle_state_is_independent_of_pool_and_reconnects(
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("strict_lifecycle_state")
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
    )
    assert connection.lifecycle_state == ConnectionLifecycleState.OPEN
    assert await connection.is_connected() is False
    assert await connection.disconnect() is False
    assert connection.lifecycle_state == ConnectionLifecycleState.CLOSED
    assert await scalar(connection, "SELECT 1") == 1
    assert connection.lifecycle_state == ConnectionLifecycleState.OPEN
    assert await connection.disconnect() is True
    assert connection.lifecycle_state == ConnectionLifecycleState.CLOSED


@case("LIFE-003")
@pytest.mark.asyncio
async def test_disconnect_waits_for_admitted_query(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("strict_lifecycle_grace")
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
    )
    await connection.connect()
    query = asyncio.create_task(
        connection.simple_query(
            "WAITFOR DELAY '00:00:02'; SELECT 42 AS answer"
        )
    )
    await wait_for_one_visible_request(sa_connection, application_name)
    started = time.monotonic()
    shutdown = asyncio.create_task(connection.disconnect())
    await wait_for_state(connection, ConnectionLifecycleState.CLOSING)
    await asyncio.sleep(0.1)
    assert shutdown.done() is False
    assert (await query).fetchone()["answer"] == 42
    assert await shutdown is True
    assert time.monotonic() - started >= 1.0
    await wait_for_zero_sessions(sa_connection, application_name)
```

The wide lower bound proves shutdown did not return immediately without
depending on exactly two seconds of scheduler timing.

- [ ] **Step 4: Add closing-barrier and coalescing tests**

Implement:

```python
@case("LIFE-004")
@pytest.mark.asyncio
async def test_closing_rejects_new_sql_then_closed_reopens(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    application_name = unique_sql_name("strict_lifecycle_barrier")
    raw_table = unique_sql_name("strict_lifecycle_barrier_rows")
    table = f"[dbo].[{raw_table}]"
    await cleanup_registry.connection.execute(
        f"CREATE TABLE {table} (id INT NOT NULL PRIMARY KEY)"
    )
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
    )
    holder = asyncio.create_task(
        connection.simple_query(
            "WAITFOR DELAY '00:00:01'; SELECT 1 AS value"
        )
    )
    await wait_for_one_visible_request(sa_connection, application_name)
    shutdown = asyncio.create_task(connection.disconnect())
    await wait_for_state(connection, ConnectionLifecycleState.CLOSING)
    with pytest.raises(ConnectionLifecycleError) as captured:
        await connection.execute(
            f"INSERT INTO {table} (id) VALUES (1)"
        )
    error = captured.value
    assert error.operation == "execute"
    assert error.state == "Closing"
    assert error.retryable is True
    assert error.connection_discarded is False
    assert error.outcome_unknown is False
    assert await scalar(sa_connection, f"SELECT COUNT(*) FROM {table}") == 0
    await holder
    assert await shutdown is True
    assert await scalar(connection, "SELECT 9") == 9
    assert connection.lifecycle_state == ConnectionLifecycleState.OPEN


@case("LIFE-005")
@pytest.mark.asyncio
async def test_concurrent_shutdown_is_coalesced_and_waiter_safe(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("strict_lifecycle_coalesced")
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
    )
    holder = await start_visible_waitfor(
        connection,
        sa_connection,
        application_name,
    )
    first = asyncio.create_task(connection.disconnect())
    second = asyncio.create_task(connection.disconnect())
    await wait_for_state(connection, ConnectionLifecycleState.CLOSING)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await holder
    assert await second is True
    assert connection.lifecycle_state == ConnectionLifecycleState.CLOSED
    await wait_for_zero_sessions(sa_connection, application_name)
```

- [ ] **Step 5: Add the admitted pool-waiter test**

With `max_size=1`, start one visible `WAITFOR` holder and then a second query.
Yield until the second wrapper future has entered its first await, start
shutdown, and assert:

```python
@case("LIFE-006")
@pytest.mark.asyncio
async def test_admitted_pool_waiter_is_inside_shutdown_barrier(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("strict_lifecycle_waiter")
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
        max_size=1,
    )
    holder = await start_visible_waitfor(
        connection,
        sa_connection,
        application_name,
        seconds=1,
        slot=1,
    )
    waiter_entered = asyncio.Event()

    async def wait_for_slot() -> QueryStream:
        waiter_entered.set()
        return await connection.query("SELECT 2 AS slot")

    waiting = asyncio.create_task(wait_for_slot())
    await waiter_entered.wait()
    await asyncio.sleep(0)
    assert waiting.done() is False
    shutdown = asyncio.create_task(connection.disconnect())
    await wait_for_state(connection, ConnectionLifecycleState.CLOSING)
    with pytest.raises(ConnectionLifecycleError):
        await connection.query("SELECT 3 AS late_slot")
    assert shutdown.done() is False
    assert (await holder).fetchone()["slot"] == 1
    assert (await waiting).fetchone()["slot"] == 2
    assert await shutdown is True
    assert connection.lifecycle_state == ConnectionLifecycleState.CLOSED
```

The test timeout is five seconds and every task is cancelled/awaited in
`finally`.

- [ ] **Step 6: Build the baseline extension and prove the cases are RED**

Load the root SQL-auth environment without printing it:

```bash
set -a
source ../../.env.sql-auth.local
set +a
uv run maturin develop --release
uv run pytest tests/sql_auth_strict/test_lifecycle.py \
  -k 'LIFE or lifecycle or disconnect or closing' -vv
```

Expected: collection or execution fails because lifecycle public types do not
exist. The pre-existing ad-hoc baseline also proves immediate disconnect:
`0.000` seconds versus a `2.012`-second query.

- [ ] **Step 7: Commit the graceful RED cases**

Run:

```bash
git add tests/sql_auth_strict/test_lifecycle.py
git commit -m "test: reproduce graceful shutdown races"
```

---

### Task 4: Add forced-shutdown and transaction RED cases

**Files:**

- Modify: `tests/sql_auth_strict/test_lifecycle.py`

**Interfaces:**

- Consumes: `DownstreamGateProxy`, lifecycle helpers from Task 3 and existing
  transaction outcome contracts.
- Produces: `LIFE-007..013`.

- [ ] **Step 1: Add graceful transaction settlement**

Use one pooled transaction and start shutdown only after `BEGIN` and the write
are visible:

```python
@case("LIFE-007")
@pytest.mark.asyncio
async def test_active_transaction_can_commit_while_closing(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    application_name = unique_sql_name("strict_lifecycle_tx_commit")
    raw_table = unique_sql_name("strict_lifecycle_tx_rows")
    table = f"[dbo].[{raw_table}]"
    await cleanup_registry.connection.execute(
        f"CREATE TABLE {table} (id INT NOT NULL PRIMARY KEY)"
    )
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
        max_size=1,
    )
    transaction = connection.transaction()
    await transaction.begin()
    await transaction.execute(f"INSERT INTO {table} (id) VALUES (7)")
    shutdown = asyncio.create_task(connection.disconnect())
    await wait_for_state(connection, ConnectionLifecycleState.CLOSING)
    assert shutdown.done() is False
    await transaction.commit()
    assert await shutdown is True
    assert await scalar(sa_connection, f"SELECT COUNT(*) FROM {table}") == 1
```

For `LIFE-008`, parameterize settlement as explicit `rollback()` and
`close()`. Both paths must let shutdown finish, persist zero rows, report
`transaction.is_connected() is False`, and leave zero candidate sessions.

- [ ] **Step 2: Add forced query and uncertain write tests**

For `LIFE-009`, configure `shutdown_timeout=0.2` and
`force_timeout=2.0`, start a visible five-second query and assert:

```python
with pytest.raises(ShutdownTimeoutError) as shutdown_error:
    await connection.disconnect()
assert shutdown_error.value.force_completed is True
assert shutdown_error.value.active_operations_at_timeout == 1
with pytest.raises(ConnectionLifecycleError) as operation_error:
    await query
assert operation_error.value.forced is True
assert operation_error.value.connection_discarded is True
assert operation_error.value.outcome_unknown is True
assert connection.lifecycle_state == ConnectionLifecycleState.CLOSED
await wait_for_zero_sessions(sa_connection, application_name)
assert await scalar(connection, "SELECT 1") == 1
```

For `LIFE-010`, route a parameterized insert through
`DownstreamGateProxy`, pause downstream only after connection setup, wait until
the observer sees the unique business key, start shutdown and assert:

```python
assert operation_error.value.outcome_unknown is True
assert operation_error.value.retryable is False
count_by_key_sql = f"SELECT COUNT(*) FROM {table} WHERE business_key = @P1"
assert await scalar(
    sa_connection,
    count_by_key_sql,
    [business_key],
) == 1
assert proxy.accepted_connections == 1
```

Resume/close the proxy and await every task in `finally`.

- [ ] **Step 3: Add forced idle-transaction retirement**

For `LIFE-011`:

1. begin a pooled transaction;
2. insert one row without committing;
3. start disconnect with a 0.2-second grace budget;
4. assert `ShutdownTimeoutError(force_completed=True)`;
5. assert the uncommitted row is absent from the observer;
6. assert the transaction no longer owns a connection;
7. assert any later operation on that old transaction raises
   `ConnectionLifecycleError` and cannot use a reopened pool;
8. prove a newly created transaction succeeds on the new generation.

- [ ] **Step 4: Preserve unknown COMMIT outcome**

Reuse the exact proxy sequence from
`test_commit_unknown_and_rollback_close_timeouts_preserve_precedence`:

```python
commit_proxy.pause_downstream()
commit_proxy.expect_client_disconnect()
commit_task = asyncio.create_task(transaction.commit())
await wait_until(committed_row_is_visible)
await commit_proxy.wait_until_downstream_held()
shutdown_task = asyncio.create_task(connection.disconnect())

with pytest.raises(CommitOutcomeUnknown) as captured:
    await commit_task
assert isinstance(captured.value.__cause__, ConnectionLifecycleError)
assert captured.value.__cause__.operation == "commit"
assert captured.value.__cause__.forced is True
assert captured.value.__cause__.outcome_unknown is True
with pytest.raises(ShutdownTimeoutError):
    await shutdown_task
```

Decorate with `@case("LIFE-012")`. Assert the business key exists exactly once
and no rollback/retry was submitted.

- [ ] **Step 5: Cover direct batch and nested context lifecycle**

For `LIFE-013`, run two subcases:

1. `Connection.execute_batch()` through a proxy with a held downstream
   response; force shutdown, assert the direct socket closes and the operation
   reports uncertainty.
2. Enter nested connection contexts, let inner exit close one generation,
   lazily query in the outer body, then let outer exit close the new generation.

Assert final state `Closed`, `is_connected() is False` and zero candidate
sessions.

- [ ] **Step 6: Prove the new cases fail on the baseline**

Run:

```bash
uv run pytest tests/sql_auth_strict/test_lifecycle.py \
  -k 'transaction or force or batch or context' -vv
```

Expected: RED due to missing API and immediate disconnect. Do not accept a
failure caused by leaked fixture tasks, proxy bookkeeping or TLS setup.

- [ ] **Step 7: Commit and push the transaction/force RED state**

Run:

```bash
git add tests/sql_auth_strict/test_lifecycle.py
git commit -m "test: require bounded lifecycle force cleanup"
git push -u origin test/lifecycle-state
```

Expected: only `origin/test/lifecycle-state` is updated.

---

### Task 5: Add lifecycle stress and framework RED cases

**Files:**

- Modify: `tests/sql_auth_strict/test_lifecycle.py`
- Modify: `tests/sql_auth_strict/test_framework_integration.py`
- Modify: `tests/sql_auth_strict/framework_apps.py`

**Interfaces:**

- Consumes: lifecycle test connection factory and existing FastAPI/Flask app
  factories.
- Produces: `LIFE-014` and `LIFE-015`.

- [ ] **Step 1: Add the 100-generation stress case**

Decorate one test with `@case("LIFE-014")`. Use 100 rounds and 20 operations
per round:

```python
@case("LIFE-014")
@pytest.mark.asyncio
async def test_generation_stress(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("strict_lifecycle_generations")
    connection = lifecycle_connection(
        sql_auth_config,
        application_name=application_name,
        max_size=20,
    )
    completed = 0
    try:
        for generation in range(100):
            results = await asyncio.gather(
                *[
                    connection.query(
                        "SELECT @P1 AS generation, @P2 AS operation_id",
                        [generation, operation_id],
                    )
                    for operation_id in range(20)
                ]
            )
            rows = [stream.fetchone() for stream in results]
            assert all(row is not None for row in rows)
            observed = {
                (row["generation"], row["operation_id"])
                for row in rows
                if row is not None
            }
            assert observed == {
                (generation, operation_id)
                for operation_id in range(20)
            }
            completed += len(rows)
            assert await connection.disconnect() is True
            assert (
                connection.lifecycle_state
                == ConnectionLifecycleState.CLOSED
            )
        assert completed == 2_000
    finally:
        await connection.disconnect()
    await wait_for_zero_sessions(sa_connection, application_name)
```

After the loop, assert exactly 2,000 correct operation identities, no task
failures and zero sessions for the unique application name.

- [ ] **Step 2: Make framework factories accept lifecycle config**

Add an optional `lifecycle_config` argument to `FrameworkState.create`:

```python
@classmethod
def create(
    cls,
    config: SqlAuthConfig,
    *,
    application_name: str,
    max_size: int = 4,
    min_idle: int = 0,
    pool_config: PoolConfig | None = None,
    timeout_config=None,
    lifecycle_config=None,
) -> FrameworkState:
    connection_kwargs = {}
    if timeout_config is not None:
        connection_kwargs["timeout_config"] = timeout_config
    if lifecycle_config is not None:
        connection_kwargs["lifecycle_config"] = lifecycle_config
```

Keep the existing `Connection` construction and pass
`**connection_kwargs`. Export lifecycle errors through a renamed
`_connection_error_payload` helper. The lifecycle branch returns only these
safe fields:

```python
{
    "type": type(error).__name__,
    "operation": getattr(error, "operation", None),
    "state": getattr(error, "state", None),
    "forced": getattr(error, "forced", None),
}
```

Do not expose error messages, SQL or credentials.

Add the bounded state helper used by the framework test:

```python
async def wait_for_lifecycle_state(
    connection: Connection,
    expected,
    *,
    timeout: float = 3.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if connection.lifecycle_state == expected:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"expected lifecycle state {expected}, "
        f"observed {connection.lifecycle_state}"
    )
```

- [ ] **Step 3: Add persistent-loop and WSGI distinction assertions**

Decorate the framework test with `@case("LIFE-015")`. It must:

- start one long FastAPI request, begin lifespan shutdown and prove shutdown
  waits;
- reject a request that reaches the driver during `Closing`;
- repeat through Flask wrapped by `WsgiToAsgi`;
- run plain Flask/WSGI with explicit close/reopen generations between
  transient request loops and verify functional cleanup only;
- assert heartbeat progress for both persistent ASGI loops;
- assert zero candidate sessions at every final teardown;
- record no claim that Flask/WSGI is true async.

Use this control flow for each persistent ASGI app:

```python
@case("LIFE-015")
@pytest.mark.asyncio
async def test_framework_lifecycle_shutdown_modes(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
) -> None:
    lifecycle_config = LifecycleConfig(
        shutdown_timeout_secs=3.0,
        force_timeout_secs=1.0,
    )
    state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_lifecycle_fastapi"),
        lifecycle_config=lifecycle_config,
    )
    fastapi_app = create_fastapi_app(
        state,
        "[unused_framework_table]",
    )
    manager = LifespanManager(fastapi_app)
    await manager.__aenter__()
    client_context = asgi_client(
        fastapi_app,
        raise_app_exceptions=False,
    )
    client = await client_context.__aenter__()
    try:
        request_task = asyncio.create_task(
            client.get("/wait/15?profile=short")
        )
        await wait_for_sql_request(
            sa_connection,
            state.application_name,
            present=True,
            timeout=3.0,
        )
        shutdown_task = asyncio.create_task(
            manager.__aexit__(None, None, None)
        )
        await wait_for_lifecycle_state(
            state.connection,
            ConnectionLifecycleState.CLOSING,
        )
        late_response = await client.get(
            "/timeout/99?profile=immediate"
        )
        assert late_response.status_code == 504
        assert late_response.json() == {
            "type": "ConnectionLifecycleError",
            "operation": "query",
            "state": "Closing",
            "forced": False,
        }
        assert shutdown_task.done() is False
        assert (await request_task).json() == {"value": 15}
        assert await shutdown_task is None
    finally:
        await client_context.__aexit__(None, None, None)
```

For `adapted_flask_lifespan(state)`, account for the verified asgiref execution
model: `WsgiToAsgi` dispatches WSGI calls through a thread-sensitive serialized
lane. Two simultaneous HTTP requests therefore cannot prove that a late
request reaches FastMssql while the first WSGI request is active. Instead,
start one visible admitted SQL operation on the same application-scoped
connection, begin lifespan shutdown, then issue `/timeout/99` through the
returned WsgiToAsgi app. This keeps the WSGI thread available and proves that
the late HTTP request reaches the driver and receives the typed `Closing`
payload. Record this serialization limitation; do not claim native-ASGI
inter-request parallelism for Flask.

For plain WSGI, use two calls to `flask_request(app, "/loop")`, assert their
`loop_id` values differ, explicitly await `state.connection.disconnect()`
after each request so the shared wrapper lazily opens a fresh generation on
the next transient loop, and assert only functional SQL correctness plus zero
final sessions.

- [ ] **Step 4: Run and record RED**

Run:

```bash
uv run pytest \
  tests/sql_auth_strict/test_lifecycle.py::test_generation_stress \
  tests/sql_auth_strict/test_framework_integration.py \
  -k 'lifecycle' -vv
```

Expected: lifecycle API failures on the baseline; existing non-lifecycle
framework tests remain unaffected.

- [ ] **Step 5: Commit and push the final RED test state**

Run:

```bash
git add \
  tests/sql_auth_strict/test_lifecycle.py \
  tests/sql_auth_strict/test_framework_integration.py \
  tests/sql_auth_strict/framework_apps.py
git commit -m "test: cover lifecycle framework and generation stress"
git push origin test/lifecycle-state
```

Record the exact RED SHA and failure summary in the implementation log.

---

### Task 6: Create the GREEN worktree and implement public lifecycle types

**Files:**

- Create: `src/lifecycle_config.rs`
- Create: `src/lifecycle.rs`
- Modify: `src/types.rs`
- Modify: `src/lib.rs`
- Test: Rust unit tests inside the two new modules

**Interfaces:**

- Consumes: RED branch head and the stable interfaces above.
- Produces: validated public configuration/state/errors and an independently
  unit-tested coordinator.

- [ ] **Step 1: Create the feature worktree from the exact RED SHA**

From the repository root:

```bash
git worktree add .worktrees/feat-lifecycle-state \
  -b feat/lifecycle-state test/lifecycle-state
```

Verify:

```bash
git merge-base --is-ancestor test/lifecycle-state HEAD
git status --short --branch
test -f ../../.env.sql-auth.local
test ! -e .env.sql-auth.local
ln -s ../../.env.sql-auth.local .env.sql-auth.local
git check-ignore .env.sql-auth.local
```

The symlink remains ignored and local to the worktree.

- [ ] **Step 2: Implement `PyLifecycleConfig` and public state**

Use `RequiredLifecycleSeconds` to reject booleans and `None`, then validate
with the same numerical boundaries as `TimeoutConfig`:

```rust
const DEFAULT_SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(30);
const DEFAULT_FORCE_TIMEOUT: Duration = Duration::from_secs(5);
const MIN_TIMEOUT_SECONDS: f64 = 1e-9;
const MAX_PORTABLE_TIMEOUT: Duration =
    Duration::from_secs(100 * 365 * 24 * 60 * 60);

#[pyclass(name = "LifecycleConfig", from_py_object)]
#[derive(Clone, Debug)]
pub struct PyLifecycleConfig {
    pub(crate) shutdown_timeout: Duration,
    pub(crate) force_timeout: Duration,
}

#[pyclass(name = "ConnectionLifecycleState", eq, eq_int, from_py_object)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConnectionLifecycleState {
    Open,
    Closing,
    Closed,
}
```

The constructor text signature is exactly:

```rust
"(shutdown_timeout_secs=30.0, force_timeout_secs=5.0)"
```

Setters validate before assigning so a rejected mutation leaves the previous
value unchanged. Add `OPEN`, `CLOSING` and `CLOSED` class attributes plus
`__str__`/`__repr__` methods:

```rust
#[pymethods]
impl ConnectionLifecycleState {
    #[classattr]
    const OPEN: Self = Self::Open;
    #[classattr]
    const CLOSING: Self = Self::Closing;
    #[classattr]
    const CLOSED: Self = Self::Closed;

    fn __str__(&self) -> &'static str {
        match self {
            Self::Open => "Open",
            Self::Closing => "Closing",
            Self::Closed => "Closed",
        }
    }

    fn __repr__(&self) -> String {
        format!("ConnectionLifecycleState.{}", self.__str__())
    }
}
```

- [ ] **Step 3: Add structured lifecycle exceptions**

In `src/types.rs`:

```rust
create_exception!(
    crate::fastmssql,
    ConnectionLifecycleError,
    SqlConnectionError
);
create_exception!(
    crate::fastmssql,
    ShutdownTimeoutError,
    ConnectionLifecycleError
);

pub(crate) struct LifecycleErrorMetadata {
    pub(crate) operation: OperationName,
    pub(crate) state: ConnectionLifecycleState,
    pub(crate) generation: u64,
    pub(crate) retryable: bool,
    pub(crate) connection_discarded: bool,
    pub(crate) outcome_unknown: bool,
    pub(crate) forced: bool,
}
```

Implement `create_lifecycle_error` and `create_shutdown_timeout_error` so
every attribute from the design is set with `setattr`. Return `PyResult<PyErr>`
when metadata attachment can fail; never silently ignore an attribute error.

- [ ] **Step 4: Implement coordinator state and RAII permits**

Use:

```rust
struct LifecycleInner {
    state: ConnectionLifecycleState,
    generation: u64,
    active_operations: usize,
    active_transactions: usize,
    next_participant_id: u64,
    participants: HashMap<u64, Arc<dyn ForcedShutdownParticipant>>,
    force_sender: watch::Sender<bool>,
    shutdown_sender: Option<watch::Sender<Option<ShutdownOutcome>>>,
}

pub(crate) struct ConnectionLifecycle {
    public_state: AtomicU8,
    inner: Mutex<LifecycleInner>,
    activity_changed: Notify,
}
```

Never `unwrap()` a poisoned mutex. Recover its inner state with
`PoisonError::into_inner`, because lifecycle cleanup must remain fail-closed.
Mirror a mutex-protected transition with `AtomicU8::store(Ordering::Release)`
and read the public property with `load(Ordering::Acquire)`. The atomic is
introspection only; admission decisions always use the mutex state.

`admit_operation` rules:

```text
Open    increment active_operations and return a permit
Closing return admission ConnectionLifecycleError
Closed  increment generation, create a fresh force channel, set Open,
        increment active_operations and return a permit
```

Use `checked_add` for generation, active counters and participant IDs. Return
a typed internal `PyRuntimeError` before mutation on overflow; never wrap an
identifier into a generation that a stale permit could match.

`Drop` on a current-generation permit decrements exactly once and calls
`activity_changed.notify_one()`. There is exactly one supervisor per
connection; `notify_one` stores a permit if the supervisor has not yet polled
its `Notified` future, avoiding the lost-wakeup window of `notify_waiters`.
A stale-generation drop does not mutate new-generation counts.

- [ ] **Step 5: Implement force-aware operation execution**

The current Tokio feature set does not include its macro feature. Do not add
`tokio::select!` or change dependency features. Implement one force-first race
with `std::future::poll_fn`:

```rust
pub(crate) async fn run_force_aware<T, F>(
    mut force_receiver: watch::Receiver<bool>,
    future: F,
) -> Result<T, ForceRequested>
where
    F: Future<Output = T>,
{
    let force = async move {
        loop {
            if *force_receiver.borrow() {
                return;
            }
            if force_receiver.changed().await.is_err() {
                return;
            }
        }
    };
    let mut force = Box::pin(force);
    let mut operation = Box::pin(future);

    std::future::poll_fn(|context| {
        if force.as_mut().poll(context).is_ready() {
            return Poll::Ready(Err(ForceRequested));
        }
        match operation.as_mut().poll(context) {
            Poll::Ready(value) => Poll::Ready(Ok(value)),
            Poll::Pending => Poll::Pending,
        }
    })
    .await
}
```

Import `std::task::Poll`. Polling force first makes a simultaneously ready
force signal deterministic. The losing SQL future is dropped when
`run_force_aware` returns.

`OperationPermit::run` calls `run_force_aware(self.force_receiver.clone(),
future)` and maps `ForceRequested` to its structured forced error. A permit
created just before force cannot miss the signal because the current watch
value is checked before `changed()`.

- [ ] **Step 6: Implement cancellation-safe shutdown**

The first caller:

1. locks the coordinator;
2. changes `Open` to `Closing`;
3. stores a `watch::Sender<Option<ShutdownOutcome>>`;
4. spawns `run_shutdown_supervisor`;
5. subscribes to the outcome.

Later `Closing` callers subscribe to the same sender. A `Closed` caller with
no active round returns `false`.

After the transition, the supervisor reads the pool without holding the
coordinator mutex. It computes:

```rust
let had_resources = active_operations > 0
    || active_transactions > 0
    || pool.read().await.is_some();
```

An admitted future that has not initialized the pool is covered by its active
count; an unused connection remains `false`.

Use this lost-wakeup-safe drain loop:

```rust
loop {
    let notified = lifecycle.activity_changed.notified();
    if lifecycle.current_counts(generation).is_zero() {
        break DrainResult::Complete;
    }
    if tokio::time::timeout_at(deadline, notified).await.is_err() {
        break DrainResult::TimedOut;
    }
}
```

The corresponding permit drops use `notify_one`, so a decrement between the
count check and the first poll leaves a stored notification for this loop.

On force:

- snapshot counts;
- send `true` on the generation's force channel;
- clone participant handles under the mutex and invoke them after releasing it;
- wait at most `force_timeout`;
- set the pool `Option` to `None`;
- transition to `Closed`;
- publish one cloneable `ShutdownOutcome` with
  `shutdown_sender.send_replace(Some(outcome))`, which remains valid even
  when the initiating Python receiver was cancelled.

Wrap each participant future and the top-level supervisor future with
`AssertUnwindSafe(...).catch_unwind()` from the already present
`futures_util::FutureExt`. A participant panic sets `force_completed=false`
and cannot prevent other participant futures from running; poll the
participant cleanup futures concurrently with
`futures_util::future::join_all`. A supervisor panic uses an emergency
fail-closed path that broadcasts force, detaches the owner pool, transitions
to `Closed` and publishes a forced outcome with `force_completed=false`; it
never leaves waiters blocked in `Closing`.

The waiter maps a forced outcome to `ShutdownTimeoutError` after cleanup.

- [ ] **Step 7: Add Rust coordinator unit tests**

Test without SQL:

```text
new coordinator is Open
Closing rejects admission before count mutation
dropped operation permit wakes graceful drain
concurrent shutdown receivers observe one outcome
dropped first receiver does not stop the supervisor
force signal reaches an old-generation permit
simultaneously ready force and SQL futures choose force
stale permit drop cannot decrement a reopened generation
participant callback runs once
participant panic still closes the generation with force_completed=false
config rejects boolean/nonfinite/subnanosecond/over-100-year values
```

Use a manually constructed Tokio runtime in `#[test]` functions; do not add
the Tokio `macros` feature solely for tests.

- [ ] **Step 8: Register modules and run the focused Rust tests**

Add module declarations, public re-exports, `m.add_class` registrations and
exception registration in `src/lib.rs`.

Run:

```bash
cargo fmt
cargo test --locked lifecycle -- --nocapture
cargo clippy --all-targets -- -D warnings
```

Expected: coordinator/config tests pass; Python lifecycle contract still fails
until connection and package exports are wired.

- [ ] **Step 9: Commit the coordinator**

Run:

```bash
git add src/lifecycle.rs src/lifecycle_config.rs src/types.rs src/lib.rs
git commit -m "feat: add connection lifecycle coordinator"
```

---

### Task 7: Admit every connection and batch operation

**Files:**

- Modify: `src/connection.rs`
- Modify: `src/batch.rs`
- Test: `tests/sql_auth_strict/test_lifecycle.py`

**Interfaces:**

- Consumes: `ConnectionLifecycle`, `OperationPermit`, `PyLifecycleConfig`.
- Produces: barrier/force behavior for all non-transaction connection paths.

- [ ] **Step 1: Store and clone lifecycle handles**

Add to `PyConnection`:

```rust
lifecycle: Arc<ConnectionLifecycle>,
lifecycle_config: PyLifecycleConfig,
```

Add `lifecycle` to `ConnectionHandles`. Append
`lifecycle_config: Option<PyLifecycleConfig>` to the PyO3 constructor and
initialize:

```rust
lifecycle: ConnectionLifecycle::new(),
lifecycle_config: lifecycle_config.unwrap_or_default(),
```

Add getters:

```rust
#[getter]
fn lifecycle_state(&self) -> ConnectionLifecycleState;

#[getter]
fn lifecycle_config(&self) -> PyLifecycleConfig;
```

- [ ] **Step 2: Wrap query, simple-query, execute, connect and ping**

The pattern is:

```rust
future_into_py(py, async move {
    let permit = handles.lifecycle.admit_operation(
        OperationName::Query,
        true,
    )?;
    permit
        .run(async move {
            let pool = handles.ensure_connected(OperationName::Query).await?;
            let rows = Self::execute_query_async_gil_free(
                &pool,
                &handles.timeout_config,
                OperationName::Query,
                &query,
                &fast_parameters,
            )
            .await?;
            wrap_query_stream(rows)
        })
        .await
})
```

Use `outcome_unknown=true` for query, simple-query and execute. Use `false`
for connect, ping and context entry.

- [ ] **Step 3: Replace raw context exit and disconnect**

`__aexit__` and `disconnect` both call:

```rust
Arc::clone(&lifecycle)
    .shutdown(
        Arc::clone(&pool),
        lifecycle_config.clone(),
    )
    .await
```

`__aexit__` maps a graceful Boolean to `()` but propagates
`ShutdownTimeoutError`.

- [ ] **Step 4: Pass lifecycle through all batch functions**

Append `Arc<ConnectionLifecycle>` to internal `query_batch`, `bulk_insert` and
`execute_batch` signatures. Admit inside their `future_into_py` block before
pool initialization or direct connect:

```rust
async fn execute_batch_transaction(
    connection: &mut tiberius::Client<
        tokio_util::compat::Compat<tokio::net::TcpStream>,
    >,
    batch_commands: Vec<(String, SmallVec<[FastParameter; 16]>)>,
    timeout_config: &PyTimeoutConfig,
) -> PyResult<Vec<u64>> {
    let deadline = deadline_from(
        TimeoutPhase::Operation,
        timeout_config.operation_timeout,
    );
    let mut transaction_started = false;
    let operation = run_until(
        deadline,
        catch_driver_panic(async {
            consume_simple_command(
                connection,
                "BEGIN TRANSACTION",
                "Failed to start transaction",
            )
            .await?;
            transaction_started = true;
            let results =
                execute_batch_on_connection(connection, batch_commands).await?;
            consume_simple_command(
                connection,
                "COMMIT TRANSACTION",
                "Failed to commit batch transaction",
            )
            .await?;
            transaction_started = false;
            Ok::<Vec<u64>, PyErr>(results)
        }),
    )
    .await;

    match operation {
        Err(elapsed) => Err(operation_timeout_error(
            elapsed,
            OperationName::ExecuteBatch,
            true,
        )),
        Ok(Err(driver_panic)) => Err(driver_panic),
        Ok(Ok(Ok(results))) => Ok(results),
        Ok(Ok(Err(primary))) => {
            if transaction_started
                && let Err(cleanup) = rollback_after_failure(
                    connection,
                    timeout_config,
                    OperationName::ExecuteBatch,
                    "Failed to roll back batch transaction",
                )
                .await
            {
                return Err(attach_cleanup_cause(primary, cleanup));
            }
            Err(primary)
        }
    }
}

let permit = lifecycle.admit_operation(
    OperationName::ExecuteBatch,
    true,
)?;
let all_results = permit
    .run(async move {
        let mut connection = connect_client_with_timeout(
            &config,
            azure_credential.as_ref(),
            timeout_config.connect_timeout,
            OperationName::ExecuteBatch,
        )
        .await?;
        execute_batch_transaction(
            &mut connection,
            batch_commands,
            &timeout_config,
        )
        .await
    })
    .await?;
Python::attach(|py| {
    let result = PyList::new(py, all_results)?;
    Ok(result.into_any().unbind())
})
```

The helper body is the current `execute_batch` async block from the
`transaction_started` flag through the final result match, including
`run_until`, `catch_driver_panic`, rollback cleanup and Python-independent
`Vec<u64>` return. Extract it mechanically; do not change its SQL or timeout
semantics. Do not acquire one permit per item or bulk chunk; the public call
owns one absolute lifecycle admission.

- [ ] **Step 5: Build and run graceful/force connection tests**

Run:

```bash
uv run maturin develop --release
uv run pytest tests/test_lifecycle_contract.py -q
uv run pytest tests/sql_auth_strict/test_lifecycle.py \
  -k 'state or admitted_query or closing or concurrent or waiter or query or write or batch or context' \
  -vv
```

Expected: public contract and non-transaction lifecycle cases pass. Diagnose
any server-session leak before continuing.

- [ ] **Step 6: Run existing connection/batch regression tests**

Run:

```bash
uv run pytest \
  tests/sql_auth_strict/test_connection.py \
  tests/sql_auth_strict/test_batch_strict.py \
  tests/sql_auth_strict/test_operation_timeouts.py \
  -m 'not resilience and not load' -q
```

Expected: all selected existing cases pass, including reconnect, nested
contexts, cancellation retirement and operation timeouts.

- [ ] **Step 7: Commit connection integration**

Run:

```bash
git add src/connection.rs src/batch.rs
git commit -m "feat: drain connection work during shutdown"
```

---

### Task 8: Track and force-retire pooled transaction sessions

**Files:**

- Modify: `src/transaction.rs`
- Modify: `src/lifecycle.rs`
- Test: Rust transaction unit tests and strict `LIFE-007..012`

**Interfaces:**

- Consumes: transaction participant trait and permit authorization API.
- Produces: lease-lifetime accounting, graceful settlement and forced
  old-generation retirement.

- [ ] **Step 1: Extend pooled transaction source and session state**

Add:

```rust
#[derive(Clone)]
struct SharedPoolSource {
    pool: Arc<RwLock<Option<ConnectionPool>>>,
    pool_config: PyPoolConfig,
    timeout_config: PyTimeoutConfig,
    lifecycle: Arc<ConnectionLifecycle>,
}

struct TransactionSession {
    // existing fields
    lifecycle_permit: Option<TransactionPermit>,
    lifecycle_failure: Option<LifecycleFailure>,
}
```

`LifecycleFailure` is cloneable metadata sufficient to reconstruct a
`ConnectionLifecycleError`; it contains no `PyErr`.

Change `Transaction::from_pool` to accept
`lifecycle: Arc<ConnectionLifecycle>` after `timeout_config`, store it in
`SharedPoolSource`, and change `Connection.transaction()` to pass
`Arc::clone(&self.lifecycle)`.

- [ ] **Step 2: Centralize lease retirement**

Add one helper and replace every raw transaction `session.conn.take()` terminal
path:

```rust
fn retire_connection(
    &mut self,
    failure: Option<LifecycleFailure>,
) -> Option<TransactionConnection> {
    if let Some(connection) = self.conn.as_mut() {
        connection.mark_unusable();
    }
    let connection = self.conn.take();
    self.lifecycle_failure = failure;
    self.lifecycle_permit.take();
    connection
}
```

For confirmed commit/rollback, release the pooled connection without marking
it unusable, then take `lifecycle_permit`. Keep those success paths separate
from forced/error retirement.

- [ ] **Step 3: Register a participant before owned checkout**

Define:

```rust
struct TransactionShutdownParticipant {
    session: Weak<AsyncMutex<TransactionSession>>,
}
```

Its `force_close` future upgrades the weak pointer, locks the session, confirms
the permit generation, marks/takes the connection, sets state `Failed`, stores
forced lifecycle metadata and drops the permit.

In `ensure_connected_inner`, for a pooled source with no current connection:

1. create the participant from `Arc::downgrade(session)`;
2. call `lifecycle.admit_transaction`;
3. store the permit before awaiting pool initialization;
4. select pool initialization/acquisition against the permit's force receiver;
5. on failure, clear the permit exactly once.

- [ ] **Step 4: Enforce Closing authorization**

Before a transaction data operation:

```rust
if let Some(permit) = session.lifecycle_permit.as_ref() {
    permit.authorize_data()?;
}
```

Before commit, rollback or close:

```rust
if let Some(permit) = session.lifecycle_permit.as_ref() {
    permit.authorize_settlement()?;
}
```

`authorize_settlement` accepts the same generation in `Open` or `Closing`.
`authorize_data` accepts only `Open`. A stored lifecycle failure takes
precedence over generic transaction-state errors.

- [ ] **Step 5: Select every pooled transaction I/O against force**

Clone the permit receiver before awaiting BEGIN, query, execute, batch, COMMIT
or ROLLBACK and pass the driver future through `run_force_aware`. A
`ForceRequested` branch:

- marks/takes the connection;
- transitions to `Failed`;
- stores metadata;
- drops the session permit;
- returns `ConnectionLifecycleError`.

For COMMIT, pass that error through `create_commit_outcome_unknown` so it
becomes the cause. Disarm the existing cancellation guard only after the
forced path owns retirement.

- [ ] **Step 6: Add unit tests for exact-once permit release**

Extend transaction unit tests:

```text
confirmed commit drops one session permit
confirmed rollback drops one session permit
close drops one session permit
forced idle participant marks Failed and drops the lease
forced in-flight epoch cannot retire a newer transaction
stored lifecycle failure precedes generic Failed-state RuntimeError
direct Transaction has no lifecycle permit and keeps existing behavior
```

- [ ] **Step 7: Run transaction lifecycle and regressions**

Run:

```bash
cargo test --locked transaction -- --nocapture
uv run maturin develop --release
uv run pytest tests/sql_auth_strict/test_lifecycle.py \
  -k 'transaction or commit or rollback or force' -vv
uv run pytest \
  tests/sql_auth_strict/test_transactions_strict.py \
  tests/sql_auth_strict/test_operation_timeouts.py \
  -m 'not resilience and not load' -q
```

Expected: `LIFE-007..012`, cancellation retirement, transaction lifetime and
unknown COMMIT precedence all pass.

- [ ] **Step 8: Commit transaction lifecycle**

Run:

```bash
git add src/lifecycle.rs src/transaction.rs
git commit -m "feat: retire pooled transactions on shutdown"
```

---

### Task 9: Complete Python exports, stubs and lifecycle documentation

**Files:**

- Modify: `python/fastmssql/__init__.py`
- Modify: `python/fastmssql/fastmssql.pyi`
- Modify: `python/fastmssql/__init__.pyi`
- Modify: `README.md`
- Test: `tests/test_lifecycle_contract.py`

**Interfaces:**

- Consumes: compiled public Rust classes and getters.
- Produces: one coherent runtime, typing and user-documentation contract.

- [ ] **Step 1: Export all four public lifecycle types**

Import and add to `__all__`:

```python
ConnectionLifecycleError
ConnectionLifecycleState
LifecycleConfig
ShutdownTimeoutError
```

Add wrapper properties:

```python
@property
def lifecycle_config(self):
    """Return an isolated copy of the effective lifecycle policy."""
    return self._conn.lifecycle_config

@property
def lifecycle_state(self):
    """Return Open, Closing or Closed without network I/O."""
    return self._conn.lifecycle_state
```

Update `disconnect()` and context-exit docstrings to state that they drain and
may raise `ShutdownTimeoutError`.

- [ ] **Step 2: Update both stubs exactly**

Define:

```python
class LifecycleConfig:
    shutdown_timeout_secs: float
    force_timeout_secs: float
    def __init__(
        self,
        shutdown_timeout_secs: float = 30.0,
        force_timeout_secs: float = 5.0,
    ) -> None: ...

class ConnectionLifecycleState(StrEnum):
    OPEN: str
    CLOSING: str
    CLOSED: str

class ConnectionLifecycleError(SqlConnectionError):
    message: str
    operation: str
    phase: str
    state: str
    generation: int
    retryable: bool
    connection_discarded: bool
    outcome_unknown: bool
    forced: bool

class ShutdownTimeoutError(ConnectionLifecycleError):
    shutdown_timeout_seconds: float
    force_timeout_seconds: float
    active_operations_at_timeout: int
    active_transactions_at_timeout: int
    force_completed: bool
```

Append `lifecycle_config: Optional[LifecycleConfig] = None` to both Connection
constructor declarations. Add read-only property declarations.

- [ ] **Step 3: Document production framework usage**

Add a README section containing:

```python
import os
from contextlib import asynccontextmanager

from fastmssql import Connection, LifecycleConfig
from fastapi import FastAPI


lifecycle = LifecycleConfig(
    shutdown_timeout_secs=30.0,
    force_timeout_secs=5.0,
)
connection = Connection(
    server="sql.example.internal",
    database="application",
    username=os.environ["MSSQL_USER"],
    password=os.environ["MSSQL_PASSWORD"],
    lifecycle_config=lifecycle,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    await connection.connect()
    try:
        yield
    finally:
        await connection.disconnect()


app = FastAPI(lifespan=lifespan)
```

Explain:

- `Open` is admission, not readiness;
- `Closing` rejects new SQL;
- `Closed` can reopen as a new generation;
- graceful timeout always raises after force cleanup;
- forced write outcomes are uncertain and never retried;
- FastAPI/ASGI and Flask-through-ASGI have persistent loops;
- Flask/WSGI is functional per-loop compatibility only.

- [ ] **Step 4: Run API/stub tests and import smoke**

Run:

```bash
uv run maturin develop --release
uv run pytest tests/test_lifecycle_contract.py -q
uv run python -c \
  "import fastmssql; print(fastmssql.ConnectionLifecycleState.OPEN); print(fastmssql.LifecycleConfig())"
```

Expected: contract passes and output contains `Open` plus the exact config
repr; no connection is attempted.

- [ ] **Step 5: Commit Python/docs integration**

Run:

```bash
git add \
  python/fastmssql/__init__.py \
  python/fastmssql/fastmssql.pyi \
  python/fastmssql/__init__.pyi \
  README.md
git commit -m "docs: expose graceful lifecycle policy"
```

---

### Task 10: Make all fifteen strict lifecycle cases GREEN

**Files:**

- Modify only when diagnosis proves necessary:
  `src/lifecycle.rs`, `src/connection.rs`, `src/batch.rs`,
  `src/transaction.rs`, lifecycle tests/fixtures.

**Interfaces:**

- Consumes: complete implementation and unchanged RED assertions.
- Produces: deterministic local evidence for `LIFE-001..015`.

- [ ] **Step 1: Run the pure and real lifecycle gates**

Run:

```bash
uv run maturin develop --release
uv run pytest tests/test_lifecycle_contract.py -q
set -a
source ../../.env.sql-auth.local
set +a
uv run pytest \
  tests/sql_auth_strict/test_lifecycle.py \
  tests/sql_auth_strict/test_framework_integration.py \
  -m 'not resilience and not load' -vv
```

Expected: all fifteen `LIFE-*` IDs record PASS.

- [ ] **Step 2: Diagnose every failure systematically**

For each failure, record:

```text
case ID
client state/generation
active operation/transaction snapshot
server session/request/transaction rows
whether SQL may have started
whether the physical session ID disappeared
whether the next generation used a new connection_id
```

Form one hypothesis, add the smallest diagnostic/assertion, and change one
root cause at a time. If three fixes fail for one symptom, stop and re-evaluate
the coordinator architecture before a fourth change.

- [ ] **Step 3: Re-run RED/GREEN evidence for any corrected defect**

For a test or implementation correction:

1. verify the test fails at the pre-fix implementation SHA;
2. restore the minimal fix;
3. verify the same test passes;
4. run its category regression file.

Do not weaken timing, exception metadata, no-retry or zero-session assertions.

- [ ] **Step 4: Run the lifecycle stress case separately**

Run:

```bash
uv run pytest \
  tests/sql_auth_strict/test_lifecycle.py \
  -k 'generation_stress' -vv
```

Expected:

```text
100 generations
2,000 correct operations
0 stale-generation completions
0 leaked candidate sessions
```

- [ ] **Step 5: Commit focused fixes, if any**

Each independent root cause receives its own commit. For a coordinator or
integration correction, first confirm the changed file set and then commit
only the lifecycle source files:

```bash
git diff --name-only
git add \
  src/lifecycle.rs \
  src/connection.rs \
  src/batch.rs \
  src/transaction.rs
git diff --cached --check
git commit -m "fix: preserve lifecycle shutdown invariants"
```

If the root cause is confined to fewer files, omit the unchanged paths from
`git add`. Never include unrelated formatting or refactoring.

---

### Task 11: Run complete local production gates

**Files:**

- Generated evidence only after every command succeeds:
  `docs/SQL_AUTH_TEST_MATRIX.md`
  `docs/SQL_AUTH_TEST_REPORT.md`
  `.artifacts/sql-auth/*`

**Interfaces:**

- Consumes: feature branch candidate SHA.
- Produces: full local evidence and a reviewable exact candidate SHA.

- [ ] **Step 1: Run source-quality gates**

Run:

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --locked
uv run ruff check .
uv run python -m compileall -q python tests
scripts/security/audit_dependencies.sh
```

Expected: every command exits zero; Rust test output reports zero failures and
dependency audit reports zero denied findings.

- [ ] **Step 2: Run the complete SQL-auth harness**

From the feature worktree with the root environment available:

```bash
set -a
source ../../.env.sql-auth.local
set +a
scripts/sql_auth/run_all.sh
```

Expected all required lanes pass:

```text
strict
async true-async
framework
resilience
load
upstream
report completeness
```

The report must contain 310/310 strict IDs and zero missing evidence.

- [ ] **Step 3: Re-run the 99,999 transaction stress gate**

Run with the existing approved strategy and pool size:

```bash
FASTMSSQL_TRANSACTION_STRESS_PROFILES="10_000:100,99_999:100,99_999:200" \
FASTMSSQL_TRANSACTION_STRESS_STRATEGY="persistent" \
FASTMSSQL_TRANSACTION_STRESS_POOL_SIZE="100" \
scripts/sql_auth/run_transaction_stress.sh
```

Expected: all three profiles complete, including both 99,999-transaction
profiles at concurrency 100 and 200, with bounded pool concurrency, zero
failed transactions and zero leaked sessions. Record throughput as evidence,
not a universal MSSQL benchmark.

- [ ] **Step 4: Build and install an isolated ABI3 wheel**

Run:

```bash
uv run maturin build --release --out dist
wheel_path="$(find dist -maxdepth 1 -name 'fastmssql-*.whl' -print -quit)"
test -n "${wheel_path}"
wheel_venv="$(mktemp -d)/venv"
python3 -m venv "${wheel_venv}"
"${wheel_venv}/bin/pip" install "${wheel_path}"
"${wheel_venv}/bin/python" -c \
  "import fastmssql; assert str(fastmssql.ConnectionLifecycleState.OPEN) == 'Open'; print(fastmssql.version())"
```

Do not delete the temporary directory with a broad recursive command; the OS
temporary-area policy may clean it later.

- [ ] **Step 5: Rebuild code-review-graph and inspect impact**

Run:

```bash
uvx code-review-graph build
uvx code-review-graph status
```

Then query callers/tests for `ConnectionLifecycle`, `disconnect`,
`Transaction::from_pool`, `query_batch`, `bulk_insert` and `execute_batch`.
Confirm every public SQL entry path reaches an admission permit and no old raw
pool assignment remains in context exit.

- [ ] **Step 6: Perform implementation self-review**

Check the diff against every design acceptance criterion:

```text
no lifecycle lock held across await
no missed SQL entry path
no method-only transaction accounting
no force signal lost before receiver wait
no PyErr stored across threads/waiters
no stale generation mutates new counts
no forced transport returned reusable
no COMMIT error precedence regression
no test weakening
no secret-bearing metadata
no upstream target
```

Run:

```bash
git diff --check docs/lifecycle-state-design...HEAD
git status --short --branch
git log --oneline --decorate docs/lifecycle-state-design..HEAD
```

- [ ] **Step 7: Commit generated lifecycle evidence**

Add only stable reports; do not commit secrets or raw environment files:

```bash
git add docs/SQL_AUTH_TEST_MATRIX.md docs/SQL_AUTH_TEST_REPORT.md
git commit -m "test: record verified lifecycle evidence"
```

- [ ] **Step 8: Push the feature only to origin**

Run:

```bash
git push -u origin feat/lifecycle-state
```

Verify:

```bash
git ls-remote --heads origin feat/lifecycle-state
git remote get-url --push upstream
```

Expected: feature ref exists on the user fork; upstream prints `DISABLED`.

---

### Task 12: Integrate the exact technical candidate and update live status

**Files:**

- Modify: `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- Modify:
  `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`

**Interfaces:**

- Consumes: fully verified feature SHA and exact local counts.
- Produces: cumulative technical merge, documentation-only status branch and
  no upstream PR.

- [ ] **Step 1: Merge the feature into the cumulative branch**

In the root worktree:

```bash
git status --short --branch
git switch test/sql-auth-validation
git merge --no-ff feat/lifecycle-state \
  -m "merge: add graceful connection lifecycle"
```

Do not resolve conflicts by discarding user changes. If the cumulative branch
advanced, inspect every overlapping hunk and rerun the full affected gates.

- [ ] **Step 2: Verify the exact cumulative technical SHA**

Run from a clean detached verification worktree:

```bash
git worktree add --detach \
  .worktrees/verify-lifecycle-state test/sql-auth-validation
```

Inside the detached worktree, verify no local env path exists, then link the
ignored root environment:

```bash
test ! -e .env.sql-auth.local
ln -s ../../.env.sql-auth.local .env.sql-auth.local
git check-ignore .env.sql-auth.local
```

Repeat:

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --locked
uv run maturin develop --release
uv run pytest tests/test_lifecycle_contract.py -q
scripts/sql_auth/run_all.sh
```

All results must belong to the exact merge SHA, not only the pre-merge feature
SHA.

- [ ] **Step 3: Create the status branch from the verified technical SHA**

From the repository root:

```bash
git worktree add .worktrees/docs-lifecycle-state-status \
  -b docs/lifecycle-state-status test/sql-auth-validation
```

- [ ] **Step 4: Update the production-readiness audit with measured evidence**

Change `Lifecycle și graceful shutdown — deschis` to
`Lifecycle și graceful shutdown — VERIFIED_FORK` only if all gates above are
green.

Record:

```text
design SHA
plan SHA
RED branch SHA and observed immediate-disconnect reproduction
GREEN feature SHA
cumulative technical merge SHA
public API and defaults
exact strict test/ID counts
100-generation/2,000-operation result
99,999-transaction result
Rust/upstream/framework/load/wheel/security results
zero final candidate sessions
remaining pool-backpressure/metrics/tracing/TDS-attention limitations
origin-only publication boundary
```

Do not copy anticipated counts from this plan; use command output.

- [ ] **Step 5: Update the future upstream roadmap without creating a PR**

Add one isolated candidate entry:

```text
future title       feat: add graceful connection lifecycle
fork evidence      exact verified SHA
public API         LifecycleConfig, ConnectionLifecycleState,
                   ConnectionLifecycleError, ShutdownTimeoutError
scope exclusions   backpressure, observability, TDS ATTENTION
upstream PR        not created
```

State that a future upstream proposal needs fresh explicit approval and a
branch based on then-current upstream.

- [ ] **Step 6: Self-review and commit status documentation**

Run:

```bash
git diff --check
rg -n 'TODO|TBD|FIXME|PLACEHOLDER' \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md
git diff --stat
```

Review every recorded SHA/count against actual artifacts, then:

```bash
git add \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md
git commit -m "docs: record verified graceful lifecycle"
git push -u origin docs/lifecycle-state-status
```

- [ ] **Step 7: Merge status docs and push cumulative branch**

In the root worktree:

```bash
git merge --no-ff docs/lifecycle-state-status \
  -m "merge: record graceful lifecycle verification"
git push origin test/sql-auth-validation
```

Verify fork parity:

```bash
git rev-list --left-right --count \
  test/sql-auth-validation...origin/test/sql-auth-validation
```

Expected: `0 0`.

---

### Task 13: Hosted Linux/macOS/Windows gates and final evidence correction

**Files:**

- Modify status documents only if hosted evidence changes a claim.
- Add a focused RED test commit before any hosted-only implementation fix.

**Interfaces:**

- Consumes: pushed exact candidate/cumulative SHAs.
- Produces: hosted cross-platform evidence or an explicit infrastructure
  limitation, never an assumed pass.

- [ ] **Step 1: Inspect workflows at the candidate SHA**

Run:

```bash
gh run list --repo galeamarcel/FastMssql \
  --branch feat/lifecycle-state --limit 20
```

Identify raw Rust/unit and wheel jobs for Ubuntu, macOS and Windows, plus
dependency security. If workflow triggers exclude the branch, use an
authorized `workflow_dispatch` only for existing workflows; do not publish a
release.

- [ ] **Step 2: Wait for terminal hosted results**

Collect the candidate run IDs and inspect each terminal result:

```bash
run_ids="$(
  gh run list --repo galeamarcel/FastMssql \
    --branch feat/lifecycle-state --limit 20 \
    --json databaseId --jq '.[].databaseId'
)"
for run_id in ${run_ids}; do
  gh run watch "${run_id}" \
    --repo galeamarcel/FastMssql --exit-status
  gh run view "${run_id}" \
    --repo galeamarcel/FastMssql \
    --json databaseId,headSha,status,conclusion,jobs,url
done
```

Confirm `headSha` is the intended feature or cumulative SHA and every required
OS job concludes `success`.

- [ ] **Step 3: Handle a hosted-only failure with RED/GREEN discipline**

If one OS fails:

1. preserve the job URL, command and full error;
2. reproduce with the smallest static/unit contract possible;
3. derive a lowercase platform slug from the failing runner (`ubuntu`,
   `macos` or `windows`) and create the branch with:

   ```bash
   platform_slug="windows"
   test_branch="test/lifecycle-state-${platform_slug}"
   worktree_path=".worktrees/test-lifecycle-state-${platform_slug}"
   git worktree add "${worktree_path}" \
     -b "${test_branch}" feat/lifecycle-state
   ```

   Set `platform_slug` to the actual failing runner; do not reuse `windows`
   when a different job failed.
4. commit the RED reproduction;
5. merge it into `feat/lifecycle-state`;
6. implement one root-cause fix;
7. rerun all local and hosted gates;
8. update every evidence SHA.

Never edit the existing RED test to match the implementation.

- [ ] **Step 4: Record final hosted evidence**

On `docs/lifecycle-state-status`, record run/job URLs and conclusions for:

```text
Ubuntu Rust/wheel
macOS Rust/wheel
Windows Rust/wheel
RustSec/dependency security
SQL-auth lifecycle runner, if available
```

If SQL-auth is local-only, state that limitation precisely while retaining the
complete local Docker SQL-auth gate.

- [ ] **Step 5: Final verification and fork-only handoff**

Freshly run:

```bash
git status --short --branch
git rev-list --left-right --count \
  test/sql-auth-validation...origin/test/sql-auth-validation
git remote -v
gh pr list --repo Rivendael/FastMssql \
  --head galeamarcel:feat/lifecycle-state \
  --state all --json number,url,state
```

Expected:

```text
clean cumulative worktree
origin parity 0 0
origin push is galeamarcel/FastMssql
upstream push is DISABLED
no upstream lifecycle PR
```

Report that `VERSION.md` is absent, so no version-ledger entry changed.

---

### Task 14: Make transaction close cancellation-safe

**Files:**

- Modify:
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- Modify: `tests/sql_auth_strict/test_matrix_contract.py`
- Modify: `tests/sql_auth_strict/test_lifecycle.py`
- Modify: `src/transaction.rs`
- Regenerate: `docs/SQL_AUTH_TEST_MATRIX.md`
- Regenerate: `docs/SQL_AUTH_TEST_REPORT.md`

**Interfaces:**

- Consumes: a pooled transaction whose `close()` rollback request has reached
  SQL Server but whose response is withheld.
- Produces: Python `CancelledError`, fail-closed transport retirement,
  epoch-checked `Failed` state, one lifecycle-permit release and a subsequent
  graceful `Connection.disconnect()`.

- [ ] **Step 1: Preserve the second-audit finding on a focused RED branch**

Create `test/lifecycle-close-cancellation` from the current
`feat/lifecycle-state` candidate. Add `LIFE-016` to the strict registry and a
real SQL-auth test using `DownstreamGateProxy`; update the exact matrix count
from 310 to 311. Pause the rollback response, cancel `Transaction.close()`,
confirm the client socket is retired, and call `Connection.disconnect()`
without a compensating second `close()`.

Expected before the fix: `disconnect()` reaches its graceful deadline because
the cancelled close left `TransactionPermit` in `TransactionSession`, then
raises `ShutdownTimeoutError`.

- [ ] **Step 2: Commit and merge the unchanged RED reproduction**

Run the focused test and preserve the exact failure. Commit only the registry
and test, push only to `origin`, then merge that commit into
`feat/lifecycle-state`. Do not weaken the assertion in GREEN.

- [ ] **Step 3: Make close an epoch-checked in-flight phase**

Treat `TransactionState::Closing` as in-flight for cancellation cleanup.
`Transaction.close()` must arm `TransactionCancellationGuard` immediately
after taking the connection and entering `Closing`, then disarm only after the
connection/lease and lifecycle permit have reached their terminal state.
Cancellation cleanup must be idempotent and generation-aware through the
existing `TransactionPermit` RAII drop.

- [ ] **Step 4: Verify focused, complete and hosted gates again**

Run:

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --locked
uv run maturin develop --release
uv run pytest tests/test_lifecycle_contract.py -q
uv run pytest \
  tests/sql_auth_strict/test_lifecycle.py \
  -k cancelled_transaction_close -vv
scripts/sql_auth/run_all.sh
```

Repeat the exact isolated-wheel check, the 10,000 and 99,999 transaction
profiles, and the Linux/macOS/Windows plus RustSec hosted workflows on the new
feature SHA. Evidence from a superseded SHA is retained only as history and is
not cited as the final verdict.

## Plan self-review record

The plan was checked against every section of the approved design.

### Spec coverage

| Design requirement | Plan task |
| --- | --- |
| Public config/state/errors | Tasks 2, 6, 9 |
| Atomic Open/Closing/Closed admission | Tasks 6, 7 |
| Graceful operation drain | Tasks 3, 6, 7 |
| Long-lived pooled transaction accounting | Tasks 4, 8 |
| Concurrent/cancelled disconnect waiters | Tasks 3, 6 |
| Bounded force cleanup | Tasks 4, 6, 7, 8 |
| Uncertain writes/no retry | Tasks 4, 7, 10 |
| CommitOutcomeUnknown precedence | Tasks 4, 8 |
| Direct execute_batch coverage | Tasks 4, 7 |
| Explicit/lazy generation recovery | Tasks 3, 7 |
| FastAPI/Flask lifecycle distinction | Tasks 5, 10 |
| 100-generation stress | Tasks 5, 10 |
| Existing 99,999 load gate | Task 11 |
| Cross-platform hosted gates | Task 13 |
| Cancellation-safe transaction close | Task 14 |
| Live audit/roadmap evidence | Task 12 |
| Fork-only publication | Tasks 1, 4, 11, 12, 13, 14 |

### Type consistency

- Public class is always `LifecycleConfig`.
- Public enum is always `ConnectionLifecycleState`.
- Base lifecycle error is always `ConnectionLifecycleError`.
- Shutdown waiter error is always `ShutdownTimeoutError`.
- Public config fields are always `shutdown_timeout_secs` and
  `force_timeout_secs`.
- Rust duration fields are always `shutdown_timeout` and `force_timeout`.
- Ordinary work uses `OperationPermit`; pooled lease lifetime uses
  `TransactionPermit`.
- All old-generation failures are structured data until attached to one Python
  exception.

### Scope and placeholder review

The plan adds no waiter limit, pool metric, trace span, TDS ATTENTION, retry,
streaming API, direct-transaction lifecycle ownership or package publication.
Every implementation step names exact files, interfaces, assertions and
commands. There are no unresolved design choices, generic error-handling
instructions or deferred code markers.

The plan is approved for inline execution by Marcel Galea's standing
authorization. Execution proceeds with `superpowers:executing-plans` and a
self-review checkpoint after each independently testable commit.
