# Pooled Query Cancellation Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure cancelling a pooled FastMssql query discards the interrupted
TDS connection so the next operation can acquire a fresh connection
immediately.

**Architecture:** Wrap each bb8-managed Tiberius client with a reusable-state
flag. A cancellation guard owns the bb8 checkout while an operation is in
flight; normal completion arms the guard, while future cancellation drops it
unarmed, marks the client unusable, and lets bb8 discard it through
`has_broken()`.

**Tech Stack:** Rust 2024, PyO3 0.29, pyo3-async-runtimes 0.29, Tokio 1.52,
Tiberius 0.12, bb8 0.9, pytest/pytest-asyncio, SQL Server 2022 Developer.

## Global Constraints

- Work only on branch `fix/pool-query-cancellation` in the dedicated worktree.
- Preserve SQL username/password authentication; do not add Windows or Azure
  authentication paths.
- Do not change public Python method signatures or result types.
- Do not publish, push, fork, or create a pull request.
- The existing `POOL-012` strict test is the authoritative integration
  reproduction.
- A normal SQL error remains reusable as before; only cancellation while an
  operation is incomplete invalidates the physical connection.
- Keep the fix limited to pooled `query()`, `simple_query()`, and `execute()`;
  separately test other cancellation surfaces before extending the scope.

---

### Task 1: Preserve the deterministic regression

**Files:**
- Test: `tests/sql_auth_strict/test_pool.py`

**Interfaces:**
- Consumes: `Connection.query()`, `Connection.pool_stats()`, and a pool with
  `max_size=1`, `min_idle=0`, `connection_timeout_secs=2`,
  `retry_connection=False`.
- Produces: the `POOL-012` assertion that a query immediately succeeds after
  cancelling an in-flight `WAITFOR`.

- [ ] **Step 1: Confirm the committed test shape**

The test must contain this behavior:

```python
task = asyncio.create_task(
    scalar(
        connection,
        "WAITFOR DELAY '00:00:05'; SELECT 1",
    )
)
await _wait_for_active(connection, 1)
task.cancel()
with pytest.raises(asyncio.CancelledError):
    await task
await _wait_for_active(connection, 0)
assert await scalar(connection, "SELECT 2") == 2
```

- [ ] **Step 2: Verify RED against the unfixed build**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
.venv/bin/pytest \
  tests/sql_auth_strict/test_pool.py::test_resources_return_after_task_cancellation \
  -vv
```

Expected: FAIL after about two seconds with
`SqlConnectionError: Connection pool timeout - all connections are busy`.
This failure was reproduced twice on commit `00dfda3`.

---

### Task 2: Add cancellation-aware pooled connection ownership

**Files:**
- Modify: `src/pool_manager.rs`
- Modify: `src/connection.rs`

**Interfaces:**
- Produces:
  `ManagedConnection::mark_unusable(&mut self)`,
  `ManagedConnection::is_reusable(&self) -> bool`, and
  `PooledOperationGuard::complete(&mut self)`.
- `PooledOperationGuard` dereferences to the underlying Tiberius client so the
  existing query/execute calls keep their signatures.

- [ ] **Step 1: Wrap the managed Tiberius client**

In `src/pool_manager.rs`, replace the raw manager connection type with:

```rust
type TiberiusClient =
    tiberius::Client<tokio_util::compat::Compat<tokio::net::TcpStream>>;

pub struct ManagedConnection {
    client: TiberiusClient,
    reusable: bool,
}

impl ManagedConnection {
    fn new(client: TiberiusClient) -> Self {
        Self {
            client,
            reusable: true,
        }
    }

    pub(crate) fn mark_unusable(&mut self) {
        self.reusable = false;
    }

    pub(crate) fn is_reusable(&self) -> bool {
        self.reusable
    }
}

impl std::ops::Deref for ManagedConnection {
    type Target = TiberiusClient;

    fn deref(&self) -> &Self::Target {
        &self.client
    }
}

impl std::ops::DerefMut for ManagedConnection {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.client
    }
}
```

Set `AzureConnectionManager::Connection = ManagedConnection`, wrap every
successful client returned by `connect()` with `ManagedConnection::new`, and
change `has_broken()` to:

```rust
fn has_broken(&self, conn: &mut Self::Connection) -> bool {
    !conn.is_reusable()
}
```

- [ ] **Step 2: Add the cancellation guard**

In `src/pool_manager.rs`, add:

```rust
pub(crate) struct PooledOperationGuard<'a> {
    connection: bb8::PooledConnection<'a, AzureConnectionManager>,
    completed: bool,
}

impl<'a> PooledOperationGuard<'a> {
    pub(crate) fn new(
        connection: bb8::PooledConnection<'a, AzureConnectionManager>,
    ) -> Self {
        Self {
            connection,
            completed: false,
        }
    }

    pub(crate) fn complete(&mut self) {
        self.completed = true;
    }
}

impl std::ops::Deref for PooledOperationGuard<'_> {
    type Target = TiberiusClient;

    fn deref(&self) -> &Self::Target {
        &self.connection
    }
}

impl std::ops::DerefMut for PooledOperationGuard<'_> {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.connection
    }
}

impl Drop for PooledOperationGuard<'_> {
    fn drop(&mut self) {
        if !self.completed {
            self.connection.mark_unusable();
        }
    }
}
```

- [ ] **Step 3: Guard each core pooled operation**

Import `PooledOperationGuard` in `src/connection.rs`. Change
`get_pool_connection()` to return the guard:

```rust
async fn get_pool_connection(
    pool: &ConnectionPool,
) -> PyResult<PooledOperationGuard<'_>> {
    let connection = pool.get().await.map_err(|e| match e {
        bb8::RunError::TimedOut => create_connection_error(
            "Connection pool timeout - all connections are busy. \
             Try reducing concurrent requests or increasing pool size.",
        ),
        bb8::RunError::User(e) => create_connection_error(format!(
            "Failed to get connection from pool: {e}"
        )),
    })?;
    Ok(PooledOperationGuard::new(connection))
}
```

For `execute_query_async_gil_free()`, run the complete Tiberius interaction
inside one result value, then arm the guard before propagating that result:

```rust
let mut conn = Self::get_pool_connection(pool).await?;
let operation = async {
    let stream = conn
        .query(query, &tiberius_params)
        .await
        .map_err(|e| create_sql_error(e, "Query execution failed"))?;
    stream
        .into_first_result()
        .await
        .map_err(|e| create_sql_error(e, "Failed to get results"))
}
.await;
conn.complete();
operation
```

Apply the same ordering to `execute_simple_query_async_gil_free()` and
`execute_command_async_gil_free()`: await the whole protocol operation, call
`conn.complete()`, then return or propagate its `PyResult`.

- [ ] **Step 4: Verify Rust compilation and formatting**

Run:

```bash
CARGO_TARGET_DIR=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/target \
  cargo test
cargo fmt --check -- src/pool_manager.rs src/connection.rs
```

Expected: Rust compilation succeeds. If repository-wide `cargo fmt --check`
still reports the pre-existing upstream formatting drift, verify the two
modified files independently with `rustfmt --check`.

---

### Task 3: Prove GREEN and guard against regressions

**Files:**
- Test: `tests/sql_auth_strict/test_pool.py`
- Modify only if the focused test exposes a root-cause mismatch:
  `src/pool_manager.rs`, `src/connection.rs`

**Interfaces:**
- Consumes: the worktree build installed into `.venv`.
- Produces: a passing `POOL-012` and unchanged behavior for SQL errors,
  saturation, checkout validation, and broken-connection replacement.

- [ ] **Step 1: Build the worktree extension**

Run:

```bash
.venv/bin/maturin develop --release
```

Expected: FastMssql 0.7.7 is rebuilt from
`fix/pool-query-cancellation`.

- [ ] **Step 2: Verify the original reproduction is GREEN**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
.venv/bin/pytest \
  tests/sql_auth_strict/test_pool.py::test_resources_return_after_task_cancellation \
  -vv
```

Expected: PASS; the post-cancellation `SELECT 2` completes rather than timing
out.

- [ ] **Step 3: Reprove the regression test with the fix removed**

Temporarily restore only `src/pool_manager.rs` and `src/connection.rs` to
`00dfda3`, rebuild the extension, and rerun the focused test.

Expected: FAIL with the original pool timeout. Restore the fix immediately,
rebuild, and rerun the focused test.

Expected after restoration: PASS.

- [ ] **Step 4: Run the affected strict subset**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
.venv/bin/pytest \
  tests/sql_auth_strict/test_connection.py \
  tests/sql_auth_strict/test_pool.py \
  -k 'not idle_timeout_retires_connection' \
  -vv
```

Expected: 35 selected tests PASS with no skip or xfail.

- [ ] **Step 5: Run the full affected category**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
.venv/bin/pytest \
  tests/sql_auth_strict/test_connection.py \
  tests/sql_auth_strict/test_pool.py \
  -vv
```

Expected: all 36 tests PASS, including the real 30-second idle reaper case.

- [ ] **Step 6: Run static and baseline gates**

Run:

```bash
.venv/bin/ruff check tests/sql_auth_strict/test_connection.py \
  tests/sql_auth_strict/test_pool.py
git diff --check
CARGO_TARGET_DIR=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/target \
  cargo test
```

Expected: all commands exit 0. Record the repository-wide pre-existing
`cargo fmt --check` result separately; do not reformat unrelated upstream
files.

---

### Task 4: Commit locally and stop at the publication gate

**Files:**
- Add: `docs/superpowers/plans/2026-07-24-fix-pooled-query-cancellation.md`
- Modify: `src/pool_manager.rs`
- Modify: `src/connection.rs`

**Interfaces:**
- Produces: a local fix commit on `fix/pool-query-cancellation`.
- Does not produce: a fork, remote branch, pull request, release, or package
  publication.

- [ ] **Step 1: Review the exact diff**

Run:

```bash
git status --short --branch
git diff -- src/pool_manager.rs src/connection.rs \
  docs/superpowers/plans/2026-07-24-fix-pooled-query-cancellation.md
git diff --check
```

Expected: only the plan and two Rust implementation files are changed.

- [ ] **Step 2: Commit the verified fix**

Run:

```bash
git add src/pool_manager.rs src/connection.rs \
  docs/superpowers/plans/2026-07-24-fix-pooled-query-cancellation.md
git commit -m "fix: discard pooled connection after query cancellation"
```

- [ ] **Step 3: Stop before external publication**

Report the local branch name, commit, RED/GREEN evidence, and affected test
counts. Ask for explicit approval before creating a fork, pushing, or opening
a pull request.
